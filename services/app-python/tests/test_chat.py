import unittest
from types import SimpleNamespace

from trpg_app.chat import (
    EvidenceBudget,
    ModelDecision,
    OpenAIModelGateway,
    ToolInvocation,
    _answer_quality_issue,
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

    def test_never_appends_citations_to_provider_refusal(self) -> None:
        refusal = "The request was rejected because it was considered high risk"
        self.assertEqual(_answer_quality_issue(refusal), "provider_refusal")
        self.assertEqual(_missing_citation_suffix(refusal, ["S1", "S2"]), "")

    def test_recognizes_unfinished_search_plan(self) -> None:
        self.assertEqual(
            _answer_quality_issue("让我继续搜索后续的创建步骤内容。"),
            "unfinished_process",
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


class StrictFakeLibrary(FakeLibrary):
    def read(self, ids):
        self.read_ids.append(list(ids))
        if "pf1e:combat" not in ids:
            return []
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
        yield "服务端已补充读取规则证据后回答。[S1]"


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


class DifferentSearchSameResultsGateway:
    def __init__(self, _model):
        self.step = 0

    async def decide(self, messages, tools):
        self.step += 1
        return tool_decision(
            ToolInvocation(
                f"search-{self.step}",
                "search_rules",
                f'{{"query":"角色创建{self.step}"}}',
            )
        )

    async def stream_answer(self, messages):
        yield "根据候选规则章节，下面说明角色创建步骤。[S1]"


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


class MissingDocumentReadGateway:
    def __init__(self, _model):
        self.step = 0

    async def decide(self, messages, tools):
        self.step += 1
        call = (
            ToolInvocation("search-1", "search_rules", '{"query":"角色创建"}')
            if self.step == 1
            else ToolInvocation(
                "read-1",
                "read_rules",
                '{"ids":["model:invented-document-id"]}',
            )
        )
        return tool_decision(call)

    async def stream_answer(self, messages):
        yield "服务端改读真实搜索候选后回答。[S1]"


class DirectMissingDocumentReadGateway:
    def __init__(self, _model):
        pass

    async def decide(self, messages, tools):
        return tool_decision(
            ToolInvocation(
                "read-previous-source",
                "read_rules",
                '{"ids":["previous-turn:S1"]}',
            )
        )

    async def stream_answer(self, messages):
        yield "追问已重新搜索并读取本轮证据。[S1]"


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
    final_messages: list[dict] = []

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
        type(self).final_messages = list(messages)
        yield "只能确认已经读取的借机攻击规则。"


class PartialRepeatedReadGateway:
    def __init__(self, _model):
        self.step = 0

    async def decide(self, messages, tools):
        self.step += 1
        if self.step == 1:
            return tool_decision(
                ToolInvocation("search-1", "search_rules", '{"query":"真身种类"}')
            )
        return tool_decision(
            ToolInvocation(
                f"read-{self.step}",
                "read_rules",
                '{"ids":["pf1e:evidence-1"]}',
            )
        )

    async def stream_answer(self, messages):
        yield "已补读尚未读取的候选章节。[S1][S2][S3]"


class ChangingSearchLibrary(FakeLibrary):
    def search(self, query: str, limit: int):
        self.search_queries.append(query)
        return [{"id": f"pf1e:{query}", "title": query, "excerpt": query}]


class ExpandingLibrary(ChangingSearchLibrary):
    def read(self, ids):
        self.read_ids.append(list(ids))
        return [
            {
                "id": document_id,
                "title": document_id,
                "fullPath": f"测试 > {document_id}",
                "content": f"{document_id} 的规则证据。",
                "metadata": {},
            }
            for document_id in ids
        ]


class MultiCandidateLibrary(ExpandingLibrary):
    def search(self, query: str, limit: int):
        self.search_queries.append(query)
        return [
            {
                "id": f"pf1e:evidence-{number}",
                "title": f"证据 {number}",
                "excerpt": query,
            }
            for number in range(1, 4)
        ]


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
        yield "已读取候选章节，现在直接回答用户问题。"


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


class ProductiveEndlessGateway:
    def __init__(self, _model):
        self.step = 0

    async def decide(self, messages, tools):
        self.step += 1
        call = (
            ToolInvocation(
                "search-1",
                "search_rules",
                '{"query":"复杂问题"}',
            )
            if self.step == 1
            else ToolInvocation(
                f"read-{self.step}",
                "read_rules",
                f'{{"ids":["pf1e:证据{self.step}"]}}',
            )
        )
        return tool_decision(call)

    async def stream_answer(self, messages):
        yield "已达到本轮取证上限，只根据已读取的规则回答。"


class EvidenceSaturationGateway:
    def __init__(self, _model):
        self.step = 0

    async def decide(self, messages, tools):
        self.step += 1
        number = (self.step + 1) // 2
        call = (
            ToolInvocation(
                f"search-{number}",
                "search_rules",
                f'{{"query":"查询{number}"}}',
            )
            if self.step % 2
            else ToolInvocation(
                f"read-{number}",
                "read_rules",
                f'{{"ids":["pf1e:查询{number}"]}}',
            )
        )
        return tool_decision(call)

    async def stream_answer(self, messages):
        yield "三轮检索后直接根据已读取证据回答。"


class StateAwareGateway:
    final_messages: list[dict] = []

    def __init__(self, _model):
        self.step = 0

    async def decide(self, messages, tools):
        self.step += 1
        if self.step == 1:
            return tool_decision(
                ToolInvocation("search-1", "search_rules", '{"query":"弱点"}')
            )
        if self.step == 2:
            return tool_decision(
                ToolInvocation("read-1", "read_rules", '{"ids":["pf1e:combat"]}')
            )
        return tool_decision(ToolInvocation("finish-1", "finish_answer", "{}"))

    async def stream_answer(self, messages):
        type(self).final_messages = list(messages)
        yield "根据当前猫角色状态回答弱点问题。[S1]"


class RefusalThenValidGateway(FakeGateway):
    answer_attempt = 0

    async def stream_answer(self, messages):
        type(self).answer_attempt += 1
        if type(self).answer_attempt == 1:
            yield "The request was rejected because it was considered high risk"
            return
        yield "这是重新生成的规则回答。[S1]"


class AlwaysRefusesGateway(FakeGateway):
    async def stream_answer(self, messages):
        yield "The request was rejected because it was considered high risk"


class RuleTurnTest(unittest.IsolatedAsyncioTestCase):
    async def test_state_enriches_follow_up_search_and_final_prompt(self) -> None:
        library = FakeLibrary()
        events = [
            event
            async for event in run_rule_turn(
                model=self.model(),
                library=library,
                messages=[
                    {"role": "user", "content": "我想创建一个角色"},
                    {"role": "assistant", "content": "请选择真身"},
                    {"role": "user", "content": "我选择猫作为真身"},
                    {"role": "assistant", "content": "已选择猫"},
                    {"role": "user", "content": "我现在可以选择什么弱点"},
                ],
                gateway_factory=StateAwareGateway,
            )
        ]

        self.assertEqual(
            library.search_queries,
            ["我现在可以选择什么弱点 猫"],
        )
        final_prompt = StateAwareGateway.final_messages[1]["content"]
        self.assertIn('"task": "创建角色"', final_prompt)
        self.assertIn('"真身": "猫"', final_prompt)
        self.assertIn("状态字段之间互不构成约束", final_prompt)
        self.assertIn(
            "不得根据表格位置",
            StateAwareGateway.final_messages[0]["content"],
        )
        self.assertEqual(events[-1]["type"], "done")

    async def test_retries_provider_refusal_before_exposing_answer(self) -> None:
        RefusalThenValidGateway.answer_attempt = 0
        events = [
            event
            async for event in run_rule_turn(
                model=self.model(),
                library=FakeLibrary(),
                messages=[{"role": "user", "content": "创建一个扮演的角色"}],
                gateway_factory=RefusalThenValidGateway,
            )
        ]

        text = "".join(event.get("delta", "") for event in events)
        self.assertNotIn("high risk", text)
        self.assertEqual(text, "这是重新生成的规则回答。[S1]")
        self.assertEqual(
            len(next(event["sources"] for event in events if event["type"] == "sources")),
            1,
        )

    async def test_repeated_provider_refusal_has_no_sources(self) -> None:
        events = [
            event
            async for event in run_rule_turn(
                model=self.model(),
                library=FakeLibrary(),
                messages=[{"role": "user", "content": "创建一个扮演的角色"}],
                gateway_factory=AlwaysRefusesGateway,
            )
        ]

        text = "".join(event.get("delta", "") for event in events)
        self.assertIn("没有将拒绝信息作为规则结论", text)
        self.assertNotIn("依据：", text)
        self.assertEqual(
            next(event["sources"] for event in events if event["type"] == "sources"),
            [],
        )

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

    async def test_recovers_when_model_skips_tools_on_first_decision(self) -> None:
        model = ModelConfig(
            id="fake",
            label="Fake",
            base_url="https://example.invalid/v1",
            model="fake",
            api_key="secret",
        )

        library = FakeLibrary()
        events = [
            event
            async for event in run_rule_turn(
                model=model,
                library=library,
                messages=[{"role": "user", "content": "有哪几种真身？"}],
                gateway_factory=NoEvidenceGateway,
            )
        ]

        self.assertEqual(
            library.search_queries,
            ["有哪几种真身？"],
        )
        self.assertEqual(library.read_ids, [["pf1e:combat"]])
        self.assertIn(
            "补充读取规则证据后回答",
            "".join(event.get("delta", "") for event in events),
        )
        self.assertEqual(events[-1]["type"], "done")

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

    async def test_repeated_results_are_read_before_final_answer(self) -> None:
        library = FakeLibrary()
        events = [
            event
            async for event in run_rule_turn(
                model=self.model(),
                library=library,
                messages=[{"role": "user", "content": "我想创建一个角色"}],
                gateway_factory=DifferentSearchSameResultsGateway,
            )
        ]

        self.assertEqual(
            library.search_queries,
            [
                "我想创建一个角色 角色创建1",
                "我想创建一个角色 角色创建2",
                "我想创建一个角色",
            ],
        )
        self.assertEqual(library.read_ids, [["pf1e:combat"]])
        self.assertIn(
            "下面说明角色创建步骤",
            "".join(event.get("delta", "") for event in events),
        )
        self.assertEqual(
            len(next(event["sources"] for event in events if event["type"] == "sources")),
            1,
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

    async def test_missing_model_document_ids_fall_back_to_search_results(self) -> None:
        library = StrictFakeLibrary()
        events = [
            event
            async for event in run_rule_turn(
                model=self.model(),
                library=library,
                messages=[{"role": "user", "content": "我想创建一个角色"}],
                gateway_factory=MissingDocumentReadGateway,
            )
        ]

        self.assertEqual(
            library.read_ids,
            [["model:invented-document-id"], ["pf1e:combat"]],
        )
        self.assertIn(
            "改读真实搜索候选后回答",
            "".join(event.get("delta", "") for event in events),
        )
        self.assertEqual(events[-1]["type"], "done")

    async def test_follow_up_reading_old_source_ids_researches_latest_question(self) -> None:
        library = StrictFakeLibrary()
        events = [
            event
            async for event in run_rule_turn(
                model=self.model(),
                library=library,
                messages=[
                    {"role": "user", "content": "我想创建一个角色"},
                    {"role": "assistant", "content": "可以选择真身。[S1]"},
                    {"role": "user", "content": "有哪几种真身？"},
                ],
                gateway_factory=DirectMissingDocumentReadGateway,
            )
        ]

        self.assertEqual(
            library.search_queries,
            ["有哪几种真身？ 创建角色"],
        )
        self.assertEqual(
            library.read_ids,
            [["previous-turn:S1"], ["pf1e:combat"]],
        )
        self.assertIn(
            "追问已重新搜索并读取本轮证据",
            "".join(event.get("delta", "") for event in events),
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
        self.assertEqual(
            [message["role"] for message in RepeatedReadGateway.final_messages],
            ["system", "user"],
        )
        self.assertIn(
            "不要说‘让我继续搜索’",
            RepeatedReadGateway.final_messages[0]["content"],
        )
        self.assertIn(
            "[S1] 借机攻击",
            RepeatedReadGateway.final_messages[1]["content"],
        )
        sources = next(event["sources"] for event in events if event["type"] == "sources")
        self.assertEqual([source["documentId"] for source in sources], ["pf1e:combat"])
        self.assertEqual(events[-1]["type"], "done")

    async def test_repeated_read_adds_unread_search_candidates_before_answer(self) -> None:
        library = MultiCandidateLibrary()
        events = [
            event
            async for event in run_rule_turn(
                model=self.model(),
                library=library,
                messages=[{"role": "user", "content": "有哪几种真身？"}],
                gateway_factory=PartialRepeatedReadGateway,
            )
        ]

        self.assertEqual(
            library.read_ids,
            [
                ["pf1e:evidence-1"],
                ["pf1e:evidence-1"],
                ["pf1e:evidence-2", "pf1e:evidence-3"],
            ],
        )
        sources = next(event["sources"] for event in events if event["type"] == "sources")
        self.assertEqual(len(sources), 3)
        self.assertIn(
            "已补读尚未读取的候选章节",
            "".join(event.get("delta", "") for event in events),
        )
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

        self.assertEqual(library.search_queries, ["查询1", "查询2", "宽泛问题"])
        self.assertIn(
            "已读取候选章节，现在直接回答用户问题。",
            "".join(event.get("delta", "") for event in events),
        )
        self.assertEqual(library.read_ids, [["pf1e:宽泛问题"]])
        self.assertEqual(
            len(next(event["sources"] for event in events if event["type"] == "sources")),
            1,
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

    async def test_decision_limit_finishes_from_collected_evidence(self) -> None:
        library = ExpandingLibrary()
        events = [
            event
            async for event in run_rule_turn(
                model=self.model(),
                library=library,
                messages=[{"role": "user", "content": "复杂规则问题"}],
                gateway_factory=ProductiveEndlessGateway,
            )
        ]

        self.assertEqual(len(library.search_queries), 1)
        self.assertEqual(len(library.read_ids), 8)
        self.assertIn(
            "只根据已读取的规则回答",
            "".join(event.get("delta", "") for event in events),
        )
        self.assertEqual(
            len(next(event["sources"] for event in events if event["type"] == "sources")),
            8,
        )
        self.assertEqual(events[-1]["type"], "done")

    async def test_third_search_with_evidence_reads_candidates_then_finishes(self) -> None:
        library = ExpandingLibrary()
        events = [
            event
            async for event in run_rule_turn(
                model=self.model(),
                library=library,
                messages=[{"role": "user", "content": "复杂规则问题"}],
                gateway_factory=EvidenceSaturationGateway,
            )
        ]

        self.assertEqual(library.search_queries, ["查询1", "查询2", "查询3"])
        self.assertEqual(len(library.read_ids), 3)
        self.assertEqual(library.read_ids[-1], ["pf1e:查询3"])
        self.assertIn(
            "三轮检索后直接根据已读取证据回答",
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
