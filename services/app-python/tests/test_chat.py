import unittest
from types import SimpleNamespace

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
    manifest = SimpleNamespace(
        id="pf1e",
        name="Pathfinder 1E",
        system="Pathfinder",
        edition="1E",
        revision="test-revision",
    )

    def __init__(self):
        self.search_queries: list[str] = []
        self.read_ids: list[list[str]] = []

    def search(self, query: str, limit: int):
        self.search_queries.append(query)
        return [{"id": "pf1e:combat", "title": "借机攻击", "excerpt": "离开威胁方格"}]

    def read(self, ids):
        self.read_ids.append(list(ids))
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
    first_system_prompt = ""

    def __init__(self, _model):
        self.step = 0

    async def decide(self, messages, tools):
        self.step += 1
        if self.step == 1:
            type(self).first_system_prompt = messages[0]["content"]
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


class OtherSystemGateway:
    def __init__(self, _model):
        self.decisions = 0

    async def decide(self, messages, tools):
        self.decisions += 1
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
        yield "当前绑定的 Pathfinder 1E 规则库不覆盖《夕妖晚谣》，请切换规则库。"


def tool_decision(*calls: ToolInvocation) -> ModelDecision:
    return ModelDecision(
        content="",
        tool_calls=tuple(calls),
        assistant_message={
            "role": "assistant",
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in calls
            ],
        },
    )


class RepeatedSearchGateway:
    def __init__(self, _model):
        self.step = 0

    async def decide(self, messages, tools):
        self.step += 1
        return tool_decision(
            ToolInvocation(
                f"search-{self.step}",
                "search_rules",
                '{"query":"魔战士"}',
            )
        )

    async def stream_answer(self, messages):
        raise AssertionError("no-evidence early stop must not call the model again")
        yield ""


class SearchThenStopsGateway:
    def __init__(self, _model):
        self.step = 0

    async def decide(self, messages, tools):
        self.step += 1
        if self.step == 1:
            return tool_decision(
                ToolInvocation("search-1", "search_rules", '{"query":"魔战士"}')
            )
        return ModelDecision(
            content="我不知道",
            tool_calls=(),
            assistant_message={"role": "assistant", "content": "我不知道"},
        )

    async def stream_answer(self, messages):
        raise AssertionError("no-evidence early stop must not call the model again")
        yield ""


class SearchThenFinishesGateway:
    def __init__(self, _model):
        self.step = 0

    async def decide(self, messages, tools):
        self.step += 1
        call = (
            ToolInvocation("search-1", "search_rules", '{"query":"魔战士"}')
            if self.step == 1
            else ToolInvocation("finish-1", "finish_answer", "{}")
        )
        return tool_decision(call)

    async def stream_answer(self, messages):
        raise AssertionError("finish without evidence must not generate a model answer")
        yield ""


class ParallelSearchGateway:
    second_decision_messages: list[dict] = []

    def __init__(self, _model):
        self.step = 0

    async def decide(self, messages, tools):
        self.step += 1
        if self.step == 1:
            return tool_decision(
                ToolInvocation("search-1", "search_rules", '{"query":"魔战士"}'),
                ToolInvocation("search-2", "search_rules", '{"query":"战斗职业"}'),
                ToolInvocation("search-3", "search_rules", '{"query":"魔法战士"}'),
            )
        type(self).second_decision_messages = list(messages)
        return tool_decision(ToolInvocation("finish-1", "finish_answer", "{}"))

    async def stream_answer(self, messages):
        yield "当前证据不足。"


class RepeatedReadGateway:
    def __init__(self, _model):
        self.step = 0

    async def decide(self, messages, tools):
        self.step += 1
        if self.step == 1:
            return tool_decision(
                ToolInvocation("search-1", "search_rules", '{"query":"借机攻击"}')
            )
        return tool_decision(
            ToolInvocation(
                f"read-{self.step}",
                "read_rules",
                '{"ids":["pf1e:combat"]}',
            )
        )

    async def stream_answer(self, messages):
        yield "只能确认已经读取的借机攻击规则。"


class ChangingSearchLibrary(FakeLibrary):
    def search(self, query: str, limit: int):
        self.search_queries.append(query)
        return [{"id": f"pf1e:{query}", "title": query, "excerpt": query}]


class EndlessDifferentSearchGateway:
    def __init__(self, _model):
        self.step = 0

    async def decide(self, messages, tools):
        self.step += 1
        return tool_decision(
            ToolInvocation(
                f"search-{self.step}",
                "search_rules",
                f'{{"query":"查询{self.step}"}}',
            )
        )

    async def stream_answer(self, messages):
        raise AssertionError("no-evidence early stop must not call the model again")
        yield ""


class InvalidToolGateway:
    def __init__(self, _model):
        self.step = 0

    async def decide(self, messages, tools):
        self.step += 1
        return tool_decision(
            ToolInvocation(f"invalid-{self.step}", "", "not-json")
        )

    async def stream_answer(self, messages):
        raise AssertionError("invalid-tool early stop must not call the model again")
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
        prompt = FakeGateway.first_system_prompt
        self.assertIn('"id": "pf1e"', prompt)
        self.assertIn('"name": "Pathfinder 1E"', prompt)
        self.assertIn('"system": "Pathfinder"', prompt)
        self.assertIn('"edition": "1E"', prompt)
        self.assertIn('"revision": "test-revision"', prompt)
        self.assertIn("search_rules → read_rules → finish_answer", prompt)

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

    async def test_allows_other_system_to_finish_without_rule_evidence(self) -> None:
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
                messages=[{"role": "user", "content": "夕妖晚谣的化形如何恢复梦？"}],
                gateway_factory=OtherSystemGateway,
            )
        ]

        self.assertEqual(
            [event["delta"] for event in events if event["type"] == "text_delta"],
            ["当前绑定的 Pathfinder 1E 规则库不覆盖《夕妖晚谣》，请切换规则库。"],
        )
        self.assertFalse(
            any(
                event.get("status") in {"searching", "reading"}
                for event in events
                if event["type"] == "status"
            )
        )
        self.assertEqual(
            next(event["sources"] for event in events if event["type"] == "sources"),
            [],
        )
        self.assertEqual(events[-1]["type"], "done")

    async def test_stops_repeated_search_without_returning_budget_error(self) -> None:
        library = FakeLibrary()
        events = [
            event
            async for event in run_rule_turn(
                model=self.model(),
                library=library,
                messages=[{"role": "user", "content": "你知道魔战士吗"}],
                gateway_factory=RepeatedSearchGateway,
            )
        ]

        self.assertEqual(library.search_queries, ["魔战士"])
        self.assertIn(
            "没有找到足够可靠的可引用依据",
            "".join(event.get("delta", "") for event in events),
        )
        self.assertEqual(
            next(event["sources"] for event in events if event["type"] == "sources"),
            [],
        )
        self.assertEqual(events[-1]["type"], "done")

    async def test_model_stopping_after_search_returns_no_evidence_not_error(self) -> None:
        events = [
            event
            async for event in run_rule_turn(
                model=self.model(),
                library=FakeLibrary(),
                messages=[{"role": "user", "content": "你知道魔战士吗"}],
                gateway_factory=SearchThenStopsGateway,
            )
        ]

        self.assertIn(
            "没有找到足够可靠的可引用依据",
            "".join(event.get("delta", "") for event in events),
        )
        self.assertEqual(events[-1]["type"], "done")

    async def test_model_finishing_after_search_cannot_answer_without_evidence(self) -> None:
        events = [
            event
            async for event in run_rule_turn(
                model=self.model(),
                library=FakeLibrary(),
                messages=[{"role": "user", "content": "你知道魔战士吗"}],
                gateway_factory=SearchThenFinishesGateway,
            )
        ]

        self.assertIn(
            "没有找到足够可靠的可引用依据",
            "".join(event.get("delta", "") for event in events),
        )
        self.assertEqual(
            next(event["sources"] for event in events if event["type"] == "sources"),
            [],
        )
        self.assertEqual(events[-1]["type"], "done")

    async def test_executes_only_one_tool_from_parallel_model_calls(self) -> None:
        library = FakeLibrary()
        events = [
            event
            async for event in run_rule_turn(
                model=self.model(),
                library=library,
                messages=[{"role": "user", "content": "你知道魔战士吗"}],
                gateway_factory=ParallelSearchGateway,
            )
        ]

        self.assertEqual(library.search_queries, ["魔战士"])
        tool_messages = [
            message
            for message in ParallelSearchGateway.second_decision_messages
            if message["role"] == "tool"
        ]
        self.assertEqual(len(tool_messages), 3)
        self.assertEqual(
            sum("本调用已跳过" in message["content"] for message in tool_messages),
            2,
        )
        self.assertEqual(events[-1]["type"], "done")

    async def test_repeated_read_forces_limited_answer_from_existing_evidence(self) -> None:
        library = FakeLibrary()
        events = [
            event
            async for event in run_rule_turn(
                model=self.model(),
                library=library,
                messages=[{"role": "user", "content": "借机攻击是什么"}],
                gateway_factory=RepeatedReadGateway,
            )
        ]

        self.assertEqual(library.read_ids, [["pf1e:combat"], ["pf1e:combat"]])
        self.assertIn(
            "只能确认已经读取的借机攻击规则。",
            "".join(event.get("delta", "") for event in events),
        )
        sources = next(event["sources"] for event in events if event["type"] == "sources")
        self.assertEqual([source["documentId"] for source in sources], ["pf1e:combat"])
        self.assertEqual(events[-1]["type"], "done")

    async def test_stops_after_two_searches_without_a_read(self) -> None:
        library = ChangingSearchLibrary()
        events = [
            event
            async for event in run_rule_turn(
                model=self.model(),
                library=library,
                messages=[{"role": "user", "content": "宽泛问题"}],
                gateway_factory=EndlessDifferentSearchGateway,
            )
        ]

        self.assertEqual(library.search_queries, ["查询1", "查询2"])
        self.assertIn(
            "没有找到足够可靠的可引用依据",
            "".join(event.get("delta", "") for event in events),
        )
        self.assertEqual(events[-1]["type"], "done")

    async def test_stops_after_two_invalid_tool_calls(self) -> None:
        events = [
            event
            async for event in run_rule_turn(
                model=self.model(),
                library=FakeLibrary(),
                messages=[{"role": "user", "content": "你知道魔战士吗"}],
                gateway_factory=InvalidToolGateway,
            )
        ]

        self.assertIn(
            "没有找到足够可靠的可引用依据",
            "".join(event.get("delta", "") for event in events),
        )
        self.assertEqual(events[-1]["type"], "done")

    @staticmethod
    def model() -> ModelConfig:
        return ModelConfig(
            id="fake",
            label="Fake",
            base_url="https://example.invalid/v1",
            model="fake",
            api_key="secret",
        )


if __name__ == "__main__":
    unittest.main()
