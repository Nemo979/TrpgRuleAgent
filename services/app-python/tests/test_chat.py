import unittest

from trpg_app.chat import (
    EvidenceBudget,
    ModelDecision,
    OpenAIModelGateway,
    ToolInvocation,
    _missing_citation_suffix,
    _visible_content,
    run_rule_turn,
)
from trpg_app.config import ModelConfig


class EvidenceBudgetTest(unittest.TestCase):
    def test_allows_adaptive_reads_until_character_budget(self) -> None:
        budget = EvidenceBudget(max_documents=10, max_evidence_characters=20)
        budget.consume_documents([{"content": "短规则"}, {"content": "另一条"}])
        self.assertEqual(budget.documents, 2)

        with self.assertRaisesRegex(ValueError, "上下文预算"):
            budget.consume_documents([{"content": "x" * 20}])

    def test_strips_provider_thinking_before_visible_answer(self) -> None:
        content = "内部分析内容\n</think>\n\n最终回答。[S1]"
        self.assertEqual(_visible_content(content, True), "最终回答。[S1]")
        self.assertEqual(_visible_content(content, False), content)

    def test_disables_thinking_with_provider_request_parameter(self) -> None:
        model = ModelConfig(
            id="agnes",
            label="Agnes",
            base_url="https://example.invalid/v1",
            model="agnes-2.5-flash",
            api_key="secret",
            disable_thinking=True,
        )
        gateway = OpenAIModelGateway(model)
        self.assertEqual(
            gateway._extra_body(),
            {"chat_template_kwargs": {"enable_thinking": False}},
        )

    def test_enforces_search_safety_ceiling(self) -> None:
        budget = EvidenceBudget(max_searches=1)
        budget.consume_search()
        with self.assertRaisesRegex(ValueError, "安全上限"):
            budget.consume_search()

    def test_appends_registered_citations_when_model_omits_them(self) -> None:
        self.assertEqual(
            _missing_citation_suffix("规则回答", ["S1", "S2"]),
            "\n\n依据：[S1][S2]",
        )
        self.assertEqual(
            _missing_citation_suffix("规则回答。[S2]", ["S1", "S2"]),
            "",
        )


class FakeLibrary:
    def search(self, query: str, limit: int):
        return [{"id": "pf1e:combat", "title": "借机攻击", "excerpt": "离开威胁方格"}]

    def read(self, ids):
        return [
            {
                "id": "pf1e:combat",
                "title": "借机攻击",
                "fullPath": "核心规则 > 战斗 > 借机攻击",
                "content": "离开受威胁方格可能引发借机攻击。",
                "metadata": {"page": 42},
            }
        ]


class FakeGateway:
    def __init__(self, _model):
        self.step = 0

    async def decide(self, messages, tools):
        self.step += 1
        if self.step == 1:
            call = ToolInvocation("search-1", "search_rules", '{"query":"借机攻击"}')
        elif self.step == 2:
            call = ToolInvocation("read-1", "read_rules", '{"ids":["pf1e:combat"]}')
        else:
            call = ToolInvocation("finish-1", "finish_answer", "{}")
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
        yield "离开威胁方格"
        yield "可能触发借机攻击。[S1]"


class NoEvidenceGateway:
    def __init__(self, _model):
        pass

    async def decide(self, messages, tools):
        return ModelDecision(
            content="未经检索直接回答",
            tool_calls=(),
            assistant_message={"role": "assistant", "content": "未经检索直接回答"},
        )

    async def stream_answer(self, messages):
        if False:
            yield ""


class RuleTurnTest(unittest.IsolatedAsyncioTestCase):
    async def test_searches_reads_then_streams_grounded_answer(self) -> None:
        model = ModelConfig(
            id="fake",
            label="Fake",
            base_url="https://example.invalid/v1",
            model="fake",
            api_key="secret",
        )
        events = [
            event
            async for event in run_rule_turn(
                model=model,
                library=FakeLibrary(),
                messages=[{"role": "user", "content": "何时触发借机攻击？"}],
                gateway_factory=FakeGateway,
            )
        ]

        self.assertEqual(
            [event["delta"] for event in events if event["type"] == "text_delta"],
            ["离开威胁方格", "可能触发借机攻击。[S1]"],
        )
        sources = next(event["sources"] for event in events if event["type"] == "sources")
        self.assertEqual(sources[0]["documentId"], "pf1e:combat")
        self.assertEqual(events[-1]["type"], "done")

    async def test_rejects_answer_that_never_reads_rule_evidence(self) -> None:
        model = ModelConfig(
            id="fake",
            label="Fake",
            base_url="https://example.invalid/v1",
            model="fake",
            api_key="secret",
        )

        with self.assertRaisesRegex(RuntimeError, "未读取规则证据"):
            _ = [
                event
                async for event in run_rule_turn(
                    model=model,
                    library=FakeLibrary(),
                    messages=[{"role": "user", "content": "直接回答"}],
                    gateway_factory=NoEvidenceGateway,
                )
            ]


if __name__ == "__main__":
    unittest.main()
