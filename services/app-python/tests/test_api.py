import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from trpg_app.api import create_app
from trpg_app.chat import ModelDecision, ToolInvocation
from trpg_app.config import AppConfig, ModelConfig


class ApiFakeGateway:
    def __init__(self, _model):
        self.step = 0

    async def decide(self, messages, tools):
        self.step += 1
        calls = {
            1: ToolInvocation("search", "search_rules", '{"query":"借机攻击"}'),
            2: ToolInvocation("read", "read_rules", '{"ids":["pf1e:combat"]}'),
            3: ToolInvocation("finish", "finish_answer", "{}"),
        }
        call = calls[self.step]
        return ModelDecision(
            content="",
            tool_calls=(call,),
            assistant_message={
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": call.arguments},
                    }
                ],
            },
        )

    async def stream_answer(self, messages):
        yield "离开威胁方格可能触发借机攻击。[S1]"


class TimeoutGateway:
    def __init__(self, _model):
        pass

    async def decide(self, messages, tools):
        raise TimeoutError("provider timed out")

    async def stream_answer(self, messages):
        if False:
            yield ""


class AppApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        current = root / "pf1e" / "current"
        current.mkdir(parents=True)
        document = {
            "id": "pf1e:combat",
            "rulesetId": "pf1e",
            "sourceId": "core",
            "sourceTitle": "核心规则",
            "title": "借机攻击",
            "fullPath": "核心规则 > 战斗 > 借机攻击",
            "content": "离开受威胁方格可能引发借机攻击。",
            "version": "1",
            "priority": 0,
            "metadata": {"page": 42},
        }
        (current / "documents.jsonl").write_text(
            json.dumps(document, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (current / "manifest.json").write_text(
            json.dumps(
                {
                    "id": "pf1e",
                    "name": "Pathfinder 1E",
                    "system": "Pathfinder",
                    "edition": "1E",
                    "revision": "test-revision",
                    "documents": "documents.jsonl",
                }
            ),
            encoding="utf-8",
        )
        other_current = root / "coc7" / "current"
        other_current.mkdir(parents=True)
        other_document = {
            **document,
            "id": "coc7:combat",
            "rulesetId": "coc7",
            "title": "战斗轮",
            "fullPath": "守秘人规则 > 战斗轮",
            "content": "战斗轮按敏捷顺序行动。",
            "metadata": {"page": 99},
        }
        (other_current / "documents.jsonl").write_text(
            json.dumps(other_document, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (other_current / "manifest.json").write_text(
            json.dumps(
                {
                    "id": "coc7",
                    "name": "Call of Cthulhu 7E",
                    "system": "Call of Cthulhu",
                    "edition": "7E",
                    "revision": "test-revision",
                    "documents": "documents.jsonl",
                }
            ),
            encoding="utf-8",
        )
        config = AppConfig(
            shared_password="shared",
            session_secret="test-secret",
            library_root=root,
            models=(
                ModelConfig(
                    id="test",
                    label="Test Model",
                    base_url="https://example.invalid/v1",
                    model="test-model",
                    api_key="secret",
                ),
            ),
            cookie_secure=False,
        )
        self.config = config
        self.client_context = TestClient(create_app(config, gateway_factory=ApiFakeGateway))
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temporary.cleanup()

    def test_protects_bootstrap_and_exposes_only_public_model_config(self) -> None:
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})
        ready = self.client.get("/ready")
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.json()["libraryCount"], 2)
        self.assertEqual(ready.json()["modelCount"], 1)
        self.assertEqual(self.client.get("/api/bootstrap").status_code, 401)
        response = self.client.post("/api/auth/login", json={"password": "shared"})
        self.assertEqual(response.status_code, 200)

        bootstrap = self.client.get("/api/bootstrap")
        self.assertEqual(bootstrap.status_code, 200)
        body = bootstrap.json()
        self.assertEqual(body["models"][0]["id"], "test")
        self.assertNotIn("apiKey", body["models"][0])
        self.assertEqual({item["id"] for item in body["libraries"]}, {"pf1e", "coc7"})

    def test_source_is_scoped_to_selected_library(self) -> None:
        self.client.post("/api/auth/login", json={"password": "shared"})
        source = self.client.get("/api/libraries/pf1e/documents/pf1e%3Acombat")
        self.assertEqual(source.status_code, 200)
        self.assertEqual(source.json()["metadata"]["page"], 42)
        self.assertEqual(
            self.client.get("/api/libraries/other/documents/pf1e%3Acombat").status_code,
            404,
        )
        self.assertEqual(
            self.client.get("/api/libraries/coc7/documents/pf1e%3Acombat").status_code,
            404,
        )

    def test_chat_stream_is_grounded_in_bound_library(self) -> None:
        self.client.post("/api/auth/login", json={"password": "shared"})
        response = self.client.post(
            "/api/chat",
            json={
                "model_id": "test",
                "library_id": "pf1e",
                "messages": [{"role": "user", "content": "何时触发借机攻击？"}],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.headers["x-request-id"]), 32)
        self.assertIn('"type": "text_delta"', response.text)
        self.assertIn("pf1e:combat", response.text)
        self.assertNotIn("coc7:combat", response.text)
        self.assertIn('"type": "done"', response.text)

    def test_chat_error_is_classified_and_correlated_without_details(self) -> None:
        with TestClient(create_app(self.config, gateway_factory=TimeoutGateway)) as client:
            client.post("/api/auth/login", json={"password": "shared"})
            response = client.post(
                "/api/chat",
                json={
                    "model_id": "test",
                    "library_id": "pf1e",
                    "messages": [{"role": "user", "content": "问题正文不应进入日志"}],
                },
            )

        request_id = response.headers["x-request-id"]
        self.assertEqual(response.status_code, 200)
        self.assertIn('"code": "model_timeout"', response.text)
        self.assertIn(f'"requestId": "{request_id}"', response.text)
        self.assertNotIn("provider timed out", response.text)

    def test_ready_reports_503_without_a_published_library(self) -> None:
        with tempfile.TemporaryDirectory() as empty_root:
            config = AppConfig(
                shared_password="shared",
                session_secret="test-secret",
                library_root=Path(empty_root),
                models=self.config.models,
                cookie_secure=False,
            )
            with TestClient(create_app(config, gateway_factory=ApiFakeGateway)) as client:
                self.assertEqual(client.get("/health").status_code, 200)
                response = client.get("/ready")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "not_ready"})


if __name__ == "__main__":
    unittest.main()
