import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from trpg_app.api import create_app
from trpg_app.chat import ModelDecision, ToolInvocation
from trpg_app.config import AppConfig, ModelConfig


class ApiFakeGateway:
    def __init__(self, _model):
        self.step = 0
        self.document_id = "pf1e:combat"

    async def decide(self, messages, tools):
        self.step += 1
        user_message = next(
            message["content"]
            for message in reversed(messages)
            if message["role"] == "user"
        )
        if "golden-sky-stories-zh-1-2" in messages[0]["content"]:
            self.document_id = "golden-sky-stories-zh-1-2:basic"
        calls = {
            1: ToolInvocation("search", "search_rules", json.dumps({"query": user_message})),
            2: ToolInvocation(
                "read",
                "read_rules",
                json.dumps({"ids": [self.document_id]}),
            ),
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
        if self.document_id == "pf1e:combat":
            yield "离开威胁方格可能触发借机攻击。[S1]"
        else:
            yield "化形在幕间恢复梦的力量。[S1]"


class TimeoutGateway:
    def __init__(self, _model):
        pass

    async def decide(self, messages, tools):
        raise TimeoutError("provider timed out")

    async def stream_answer(self, messages):
        if False:
            yield ""


class CountingGateway:
    created = 0

    def __init__(self, _model):
        type(self).created += 1

    async def decide(self, messages, tools):
        raise AssertionError("library boundary must not call the model gateway")

    async def stream_answer(self, messages):
        raise AssertionError("library boundary must not call the model gateway")
        yield ""


class EmptySearchGateway:
    def __init__(self, _model):
        self.step = 0

    async def decide(self, messages, tools):
        self.step += 1
        call = ToolInvocation(
            f"search-{self.step}",
            "search_rules",
            '{"query":"魔战士"}',
        )
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
        raise AssertionError("empty search should end without another model request")
        yield ""


class AppApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        current = root / "pathfinder-1e" / "current"
        current.mkdir(parents=True)
        document = {
            "id": "pf1e:combat",
            "rulesetId": "pathfinder-1e",
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
                    "id": "pathfinder-1e",
                    "name": "Pathfinder 1E 中文规则库",
                    "system": "Pathfinder",
                    "edition": "1E",
                    "revision": "test-revision",
                    "documents": "documents.jsonl",
                    "aliases": ["PF1E", "Pathfinder 1E"],
                }
            ),
            encoding="utf-8",
        )
        other_current = root / "golden-sky-stories-zh-1-2" / "current"
        other_current.mkdir(parents=True)
        other_document = {
            **document,
            "id": "golden-sky-stories-zh-1-2:basic",
            "rulesetId": "golden-sky-stories-zh-1-2",
            "sourceId": "rulebook",
            "sourceTitle": "夕妖晚谣 1.2",
            "title": "幕间",
            "fullPath": "夕妖晚谣 1.2 > 游戏流程 > 幕间",
            "content": "化形在幕间恢复梦的力量。",
            "version": "1.2",
            "metadata": {"page": 99},
        }
        (other_current / "documents.jsonl").write_text(
            json.dumps(other_document, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (other_current / "manifest.json").write_text(
            json.dumps(
                {
                    "id": "golden-sky-stories-zh-1-2",
                    "name": "夕妖晚谣 1.2",
                    "system": "夕妖晚谣（Golden Sky Stories）",
                    "edition": "中文 1.2",
                    "revision": "test-revision-gss",
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
        libraries = {item["id"]: item for item in body["libraries"]}
        self.assertEqual(
            libraries,
            {
                "pathfinder-1e": {
                    "id": "pathfinder-1e",
                    "name": "Pathfinder 1E 中文规则库",
                    "system": "Pathfinder",
                    "edition": "1E",
                    "revision": "test-revision",
                },
                "golden-sky-stories-zh-1-2": {
                    "id": "golden-sky-stories-zh-1-2",
                    "name": "夕妖晚谣 1.2",
                    "system": "夕妖晚谣（Golden Sky Stories）",
                    "edition": "中文 1.2",
                    "revision": "test-revision-gss",
                },
            },
        )

    def test_dynamic_evidence_flag_reaches_chat_metrics(self) -> None:
        metrics_path = Path(self.temporary.name) / "metrics.jsonl"
        config = replace(self.config, enable_dynamic_evidence_budget=True)
        with patch.dict(
            "os.environ",
            {"TRPG_TURN_METRICS_PATH": str(metrics_path)},
            clear=False,
        ):
            with TestClient(
                create_app(config, gateway_factory=ApiFakeGateway)
            ) as client:
                client.post("/api/auth/login", json={"password": "shared"})
                response = client.post(
                    "/api/chat",
                    json={
                        "model_id": "test",
                        "library_id": "pathfinder-1e",
                        "messages": [
                            {"role": "user", "content": "借机攻击是什么"}
                        ],
                    },
                )

        self.assertEqual(response.status_code, 200)
        metrics = json.loads(metrics_path.read_text(encoding="utf-8").splitlines()[-1])
        self.assertTrue(metrics["context"]["dynamicEvidenceBudgetEnabled"])
        self.assertEqual(metrics["context"]["evidencePolicyVersion"], 2)

    def test_query_decomposition_flag_reaches_chat_metrics(self) -> None:
        metrics_path = Path(self.temporary.name) / "route-metrics.jsonl"
        config = replace(self.config, enable_query_decomposition=True)
        with patch.dict(
            "os.environ",
            {"TRPG_TURN_METRICS_PATH": str(metrics_path)},
            clear=False,
        ):
            with TestClient(
                create_app(config, gateway_factory=ApiFakeGateway)
            ) as client:
                client.post("/api/auth/login", json={"password": "shared"})
                response = client.post(
                    "/api/chat",
                    json={
                        "model_id": "test",
                        "library_id": "pathfinder-1e",
                        "messages": [
                            {"role": "user", "content": "借机攻击是什么"}
                        ],
                    },
                )

        self.assertEqual(response.status_code, 200)
        metrics = json.loads(metrics_path.read_text(encoding="utf-8").splitlines()[-1])
        self.assertTrue(metrics["context"]["queryDecompositionEnabled"])
        self.assertEqual(metrics["context"]["routerVersion"], 1)
        self.assertEqual(metrics["context"]["routeComplexity"], "simple")

    def test_complex_planner_flag_reaches_chat_metrics(self) -> None:
        metrics_path = Path(self.temporary.name) / "planner-metrics.jsonl"
        config = replace(self.config, enable_complex_planner=True)
        with patch.dict(
            "os.environ",
            {"TRPG_TURN_METRICS_PATH": str(metrics_path)},
            clear=False,
        ):
            with TestClient(
                create_app(config, gateway_factory=ApiFakeGateway)
            ) as client:
                client.post("/api/auth/login", json={"password": "shared"})
                response = client.post(
                    "/api/chat",
                    json={
                        "model_id": "test",
                        "library_id": "pathfinder-1e",
                        "messages": [
                            {"role": "user", "content": "借机攻击是什么"}
                        ],
                    },
                )

        self.assertEqual(response.status_code, 200)
        metrics = json.loads(metrics_path.read_text(encoding="utf-8").splitlines()[-1])
        self.assertTrue(metrics["context"]["complexPlannerEnabled"])
        self.assertFalse(metrics["context"]["complexPlannerUsed"])
        self.assertEqual(metrics["context"]["plannerTaskCount"], 0)

    def test_fact_ledger_flag_is_inert_without_complex_planner(self) -> None:
        metrics_path = Path(self.temporary.name) / "fact-ledger-metrics.jsonl"
        config = replace(self.config, enable_fact_ledger=True)
        with patch.dict(
            "os.environ",
            {"TRPG_TURN_METRICS_PATH": str(metrics_path)},
            clear=False,
        ):
            with TestClient(
                create_app(config, gateway_factory=ApiFakeGateway)
            ) as client:
                client.post("/api/auth/login", json={"password": "shared"})
                response = client.post(
                    "/api/chat",
                    json={
                        "model_id": "test",
                        "library_id": "pathfinder-1e",
                        "messages": [
                            {"role": "user", "content": "借机攻击是什么"}
                        ],
                    },
                )

        self.assertEqual(response.status_code, 200)
        metrics = json.loads(metrics_path.read_text(encoding="utf-8").splitlines()[-1])
        self.assertTrue(metrics["context"]["factLedgerEnabled"])
        self.assertEqual(metrics["context"]["factLedgerStatus"], "planner_disabled")
        self.assertEqual(metrics["context"]["factLedgerVersion"], 0)

    def test_source_is_scoped_to_selected_library(self) -> None:
        self.client.post("/api/auth/login", json={"password": "shared"})
        pf_source = self.client.get(
            "/api/libraries/pathfinder-1e/documents/pf1e%3Acombat"
        )
        gss_source = self.client.get(
            "/api/libraries/golden-sky-stories-zh-1-2/documents/"
            "golden-sky-stories-zh-1-2%3Abasic"
        )
        self.assertEqual(pf_source.status_code, 200)
        self.assertEqual(pf_source.json()["metadata"]["page"], 42)
        self.assertEqual(gss_source.status_code, 200)
        self.assertEqual(gss_source.json()["metadata"]["page"], 99)

        cross_library_paths = (
            "/api/libraries/golden-sky-stories-zh-1-2/documents/pf1e%3Acombat",
            "/api/libraries/pathfinder-1e/documents/"
            "golden-sky-stories-zh-1-2%3Abasic",
            "/api/libraries/missing/documents/pf1e%3Acombat",
        )
        for path in cross_library_paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json(), {"detail": "source not found"})

    def test_chat_stream_is_grounded_in_each_bound_library(self) -> None:
        self.client.post("/api/auth/login", json={"password": "shared"})
        pf_response = self.client.post(
            "/api/chat",
            json={
                "model_id": "test",
                "library_id": "pathfinder-1e",
                "messages": [{"role": "user", "content": "何时触发借机攻击？"}],
            },
        )
        gss_response = self.client.post(
            "/api/chat",
            json={
                "model_id": "test",
                "library_id": "golden-sky-stories-zh-1-2",
                "messages": [{"role": "user", "content": "化形何时恢复梦？"}],
            },
        )

        self.assertEqual(pf_response.status_code, 200)
        self.assertEqual(len(pf_response.headers["x-request-id"]), 32)
        self.assertEqual(pf_response.headers["x-accel-buffering"], "no")
        self.assertEqual(
            pf_response.headers["cache-control"],
            "no-cache, no-transform",
        )
        self.assertIn('"type": "text_delta"', pf_response.text)
        self.assertIn("pf1e:combat", pf_response.text)
        self.assertNotIn("golden-sky-stories-zh-1-2:basic", pf_response.text)
        self.assertIn('"type": "done"', pf_response.text)

        self.assertEqual(gss_response.status_code, 200)
        self.assertEqual(len(gss_response.headers["x-request-id"]), 32)
        self.assertIn('"type": "text_delta"', gss_response.text)
        self.assertIn("golden-sky-stories-zh-1-2:basic", gss_response.text)
        self.assertNotIn("pf1e:combat", gss_response.text)
        self.assertIn('"type": "done"', gss_response.text)

    def test_chat_redirects_explicit_other_library_without_calling_model(self) -> None:
        CountingGateway.created = 0
        with TestClient(
            create_app(self.config, gateway_factory=CountingGateway)
        ) as client:
            client.post("/api/auth/login", json={"password": "shared"})
            cases = (
                (
                    "pathfinder-1e",
                    "夕妖晚谣的化形在幕间做什么？",
                    "Pathfinder 1E 中文规则库",
                    "夕妖晚谣 1.2",
                ),
                (
                    "pathfinder-1e",
                    "How does a scene work in Golden Sky Stories?",
                    "Pathfinder 1E 中文规则库",
                    "夕妖晚谣 1.2",
                ),
                (
                    "golden-sky-stories-zh-1-2",
                    "PF1E 的借机攻击如何判定？",
                    "夕妖晚谣 1.2",
                    "Pathfinder 1E 中文规则库",
                ),
            )

            for library_id, message, current_name, other_name in cases:
                with self.subTest(message=message):
                    response = client.post(
                        "/api/chat",
                        json={
                            "model_id": "test",
                            "library_id": library_id,
                            "messages": [{"role": "user", "content": message}],
                        },
                    )
                    events = [
                        json.loads(line.removeprefix("data: "))
                        for line in response.text.splitlines()
                        if line.startswith("data: ")
                    ]

                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(
                        [event["type"] for event in events],
                        ["text_delta", "sources", "done"],
                    )
                    self.assertIn(current_name, events[0]["delta"])
                    self.assertIn(other_name, events[0]["delta"])
                    self.assertIn("新建对话", events[0]["delta"])
                    self.assertEqual(events[1]["sources"], [])

        self.assertEqual(CountingGateway.created, 0)

    def test_empty_search_returns_no_evidence_instead_of_budget_error(self) -> None:
        with TestClient(
            create_app(self.config, gateway_factory=EmptySearchGateway)
        ) as client:
            client.post("/api/auth/login", json={"password": "shared"})
            response = client.post(
                "/api/chat",
                json={
                    "model_id": "test",
                    "library_id": "golden-sky-stories-zh-1-2",
                    "messages": [{"role": "user", "content": "你知道魔战士吗"}],
                },
            )

        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [event["type"] for event in events],
            ["status", "status", "status", "text_delta", "sources", "done"],
        )
        self.assertIn("当前绑定的规则库没有找到匹配结果", events[3]["delta"])
        self.assertEqual(events[4]["sources"], [])
        self.assertFalse(any(event["type"] == "error" for event in events))

    def test_chat_boundary_checks_only_latest_user_message_without_false_match(
        self,
    ) -> None:
        self.client.post("/api/auth/login", json={"password": "shared"})
        cases = (
            (
                "pathfinder-1e",
                [
                    {"role": "user", "content": "夕妖晚谣怎么玩？"},
                    {"role": "assistant", "content": "请继续。"},
                    {"role": "user", "content": "借机攻击何时触发？"},
                ],
                "pf1e:combat",
            ),
            (
                "pathfinder-1e",
                [{"role": "user", "content": "Pathfinder 的借机攻击何时触发？"}],
                "pf1e:combat",
            ),
            (
                "pathfinder-1e",
                [
                    {
                        "role": "user",
                        "content": "PF1E 里有没有类似夕妖晚谣化形的能力？",
                    }
                ],
                "pf1e:combat",
            ),
            (
                "golden-sky-stories-zh-1-2",
                [
                    {
                        "role": "user",
                        "content": "mypf1ehelper 只是名称，化形何时恢复梦？",
                    }
                ],
                "golden-sky-stories-zh-1-2:basic",
            ),
        )

        for library_id, messages, expected_document_id in cases:
            with self.subTest(library_id=library_id, messages=messages):
                response = self.client.post(
                    "/api/chat",
                    json={
                        "model_id": "test",
                        "library_id": library_id,
                        "messages": messages,
                    },
                )

                self.assertEqual(response.status_code, 200)
                self.assertIn(expected_document_id, response.text)
                self.assertIn('"type": "status"', response.text)

    def test_chat_rejects_unknown_library_before_streaming(self) -> None:
        self.client.post("/api/auth/login", json={"password": "shared"})

        response = self.client.post(
            "/api/chat",
            json={
                "model_id": "test",
                "library_id": "missing",
                "messages": [{"role": "user", "content": "这个库不存在"}],
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "library not found"})
        self.assertNotIn("x-request-id", response.headers)

    def test_chat_error_is_classified_and_correlated_without_details(self) -> None:
        with TestClient(create_app(self.config, gateway_factory=TimeoutGateway)) as client:
            client.post("/api/auth/login", json={"password": "shared"})
            response = client.post(
                "/api/chat",
                json={
                    "model_id": "test",
                    "library_id": "pathfinder-1e",
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
