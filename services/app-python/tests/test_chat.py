import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from trpg_app.chat import (
    CitationRegistry,
    EvidenceBudget,
    ModelDecision,
    OpenAIModelGateway,
    ToolLoopController,
    ToolInvocation,
    _answer_quality_issue,
    _missing_citation_suffix,
    _stream_final_answer,
    _visible_content,
    run_rule_turn,
)
from trpg_app.config import ModelConfig
from trpg_app.query_decomposition import decompose_query, route_query


class EvidenceBudgetTest(unittest.TestCase):
    def test_admits_small_documents_when_a_later_document_exceeds_budget(self) -> None:
        budget = EvidenceBudget(max_documents=10, max_evidence_characters=20)
        accepted = budget.consume_documents([{"content": "短规则"}, {"content": "另一条"}])
        self.assertEqual(len(accepted), 2)
        self.assertEqual(budget.documents, 2)

        accepted = budget.consume_documents([{"content": "x" * 20}, {"content": "小条"}])
        self.assertEqual([item["content"] for item in accepted], ["小条"])
        self.assertEqual(budget.documents, 3)
        self.assertEqual(budget.skipped_documents, 1)
        self.assertIn("evidence_budget", budget.last_skipped_reasons)

    def test_enforces_evidence_token_budget_without_registering_oversized_document(self) -> None:
        budget = EvidenceBudget(max_documents=10, max_evidence_characters=1_000, max_evidence_tokens=2)

        accepted = budget.consume_documents([{"content": "规则正文" * 10}])

        self.assertEqual(accepted, [])
        self.assertEqual(budget.documents, 0)
        self.assertEqual(budget.evidence_tokens, 0)
        self.assertIn("evidence_token_budget", budget.last_skipped_reasons)

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

    def test_topic_budget_reserves_capacity_for_unread_topics(self) -> None:
        budget = EvidenceBudget(
            max_documents=8,
            max_evidence_characters=10_000,
            max_evidence_tokens=1_000,
            topic_allocations={"a": 0.45, "b": 0.45, "shared": 0.10},
        )
        documents = [
            {"id": f"a-{index}", "content": "短证据"}
            for index in range(8)
        ]
        topics = {document["id"]: "a" for document in documents}

        accepted = budget.consume_documents(documents, topics)

        self.assertEqual(len(accepted), 6)
        self.assertIn("topic_document_limit", budget.last_skipped_reasons)
        self.assertEqual(budget.topic_documents, {"a": 6})

        follow_up = [
            {"id": "b-1", "content": "另一侧证据"},
            {"id": "shared-1", "content": "共同证据"},
        ]
        accepted = budget.consume_documents(
            follow_up,
            {"b-1": "b", "shared-1": "shared"},
        )
        self.assertEqual(len(accepted), 2)
        self.assertEqual(budget.documents, 8)

    def test_strict_subquestion_allocations_do_not_recycle_capacity(self) -> None:
        budget = EvidenceBudget(
            max_documents=4,
            max_evidence_characters=10_000,
            max_evidence_tokens=100,
            topic_allocations={"q1": 0.5, "q2": 0.5},
            strict_topic_allocations=True,
        )
        documents = [
            {"id": f"q1-{index}", "content": "短证据"}
            for index in range(3)
        ]

        accepted = budget.consume_documents(
            documents,
            {document["id"]: "q1" for document in documents},
        )

        self.assertEqual(len(accepted), 2)
        self.assertIn("topic_document_limit", budget.last_skipped_reasons)

    def test_unread_candidates_round_robin_across_search_batches(self) -> None:
        controller = ToolLoopController()
        controller.result_batches = [
            ["class-1", "class-2"],
            ["feat-1", "feat-2"],
            ["multiclass-1", "multiclass-2"],
        ]

        candidates = controller.unread_candidates({"feat-1"})

        self.assertEqual(
            candidates,
            ["class-1", "multiclass-1", "class-2", "feat-2", "multiclass-2"],
        )

    def test_decomposition_does_not_credit_an_unrelated_search(self) -> None:
        decision = route_query("借机攻击何时触发？准备动作如何使用？")
        controller = ToolLoopController(
            decomposition=decompose_query(
                "借机攻击何时触发？准备动作如何使用？",
                decision,
            )
        )

        self.assertEqual(controller._subquestion_for_query("完全无关的规则"), "")

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
    decision_messages = []

    def __init__(self, _model):
        self.step = 0
        type(self).decision_messages = []

    async def decide(self, messages, tools):
        self.step += 1
        type(self).decision_messages.append(messages)
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


class CompareLibrary(FakeLibrary):
    def search(self, query: str, limit: int):
        self.search_queries.append(query)
        if "防御式战斗" in query:
            return [
                {
                    "id": "pf1e:defensive-fighting",
                    "title": "防御式战斗",
                    "excerpt": "攻击-4，AC+2",
                }
            ]
        return [
            {
                "id": "pf1e:total-defense",
                "title": "全防御",
                "excerpt": "AC+4",
            }
        ]

    def read(self, ids):
        self.read_ids.append(list(ids))
        documents = {
            "pf1e:total-defense": {
                "id": "pf1e:total-defense",
                "title": "全防御",
                "fullPath": "核心规则 > 战斗 > 全防御",
                "content": "以标准动作令AC获得+4闪避加值。",
                "metadata": {},
            },
            "pf1e:defensive-fighting": {
                "id": "pf1e:defensive-fighting",
                "title": "防御式战斗",
                "fullPath": "核心规则 > 战斗 > 攻击",
                "content": "攻击检定-4，AC获得+2闪避加值。",
                "metadata": {},
            },
        }
        return [documents[document_id] for document_id in ids if document_id in documents]


class CompareCoverageGateway:
    def __init__(self, _model):
        self.step = 0

    async def decide(self, messages, tools):
        self.step += 1
        calls = {
            1: ToolInvocation("search-left", "search_rules", '{"query":"全防御"}'),
            2: ToolInvocation(
                "read-left", "read_rules", '{"ids":["pf1e:total-defense"]}'
            ),
            3: ToolInvocation("finish-early", "finish_answer", "{}"),
            4: ToolInvocation(
                "search-right", "search_rules", '{"query":"防御式战斗"}'
            ),
            5: ToolInvocation(
                "read-right",
                "read_rules",
                '{"ids":["pf1e:defensive-fighting"]}',
            ),
        }
        call = calls.get(
            self.step,
            ToolInvocation("finish-complete", "finish_answer", "{}"),
        )
        return tool_decision(call)

    async def stream_answer(self, messages):
        yield "全防御AC+4；防御式战斗攻击-4、AC+2。[S1][S2]"


class DecompositionLibrary(FakeLibrary):
    def search(self, query: str, limit: int):
        self.search_queries.append(query)
        if "准备动作" in query:
            return [{"id": "pf1e:ready", "title": "准备", "excerpt": "准备动作"}]
        return [{"id": "pf1e:aoo", "title": "借机攻击", "excerpt": "触发条件"}]

    def read(self, ids):
        self.read_ids.append(list(ids))
        documents = {
            "pf1e:aoo": {
                "id": "pf1e:aoo",
                "title": "借机攻击",
                "fullPath": "核心规则 > 战斗 > 借机攻击",
                "content": "离开受威胁方格可能触发借机攻击。",
                "metadata": {},
            },
            "pf1e:ready": {
                "id": "pf1e:ready",
                "title": "准备",
                "fullPath": "核心规则 > 战斗 > 特殊先攻动作 > 准备",
                "content": "准备允许声明触发条件和将要执行的动作。",
                "metadata": {},
            },
        }
        return [documents[document_id] for document_id in ids if document_id in documents]


class PlannerLibrary(FakeLibrary):
    def search(self, query: str, limit: int):
        self.search_queries.append(query)
        document_id = f"pf1e:planner-{len(self.search_queries)}"
        return [{"id": document_id, "title": query, "excerpt": "规则摘要"}]

    def read(self, ids):
        self.read_ids.append(list(ids))
        return [
            {
                "id": document_id,
                "title": f"规则 {document_id}",
                "fullPath": "PF1E > 规则",
                "content": "这是当前任务已读取的规则正文。",
                "metadata": {},
            }
            for document_id in ids
        ]


class PlannerGateway:
    final_messages = []

    def __init__(self, _model):
        type(self).final_messages = []

    async def decide(self, messages, tools):
        raise AssertionError("bounded planner executor must not delegate tools to the model")

    async def stream_answer(self, messages):
        type(self).final_messages = messages
        yield "按依赖顺序完成规则核对与条件分支。[S1]"


class FactRetryPlannerGateway(PlannerGateway):
    attempts = 0

    def __init__(self, _model):
        super().__init__(_model)
        type(self).attempts = 0

    async def stream_answer(self, messages):
        type(self).attempts += 1
        type(self).final_messages = messages
        if type(self).attempts == 1:
            yield "角色达到12级时，可采用法师5/战士4。[S1]"
            return
        yield "角色达到12级时，可采用法师9/战士3。[S1]"


class AlwaysInvalidFactPlannerGateway(PlannerGateway):
    attempts = 0

    def __init__(self, _model):
        super().__init__(_model)
        type(self).attempts = 0

    async def stream_answer(self, messages):
        type(self).attempts += 1
        type(self).final_messages = messages
        yield "角色达到12级时，可采用法师5/战士4。[S1]"


class DecompositionCoverageGateway:
    def __init__(self, _model):
        self.step = 0

    async def decide(self, messages, tools):
        self.step += 1
        calls = {
            1: ToolInvocation("search-q1", "search_rules", '{"query":"借机攻击何时触发？"}'),
            2: ToolInvocation("read-q1", "read_rules", '{"ids":["pf1e:aoo"]}'),
            3: ToolInvocation("finish-early", "finish_answer", "{}"),
            4: ToolInvocation("search-q2", "search_rules", '{"query":"准备动作如何使用？"}'),
            5: ToolInvocation("read-q2", "read_rules", '{"ids":["pf1e:ready"]}'),
        }
        return tool_decision(
            calls.get(
                self.step,
                ToolInvocation("finish-complete", "finish_answer", "{}"),
            )
        )

    async def stream_answer(self, messages):
        yield "借机攻击与准备动作分别依据对应规则处理。[S1][S2]"


class DecompositionSkipsSecondReadGateway(DecompositionCoverageGateway):
    async def decide(self, messages, tools):
        self.step += 1
        calls = {
            1: ToolInvocation("search-q1", "search_rules", '{"query":"借机攻击何时触发？"}'),
            2: ToolInvocation("read-q1", "read_rules", '{"ids":["pf1e:aoo"]}'),
            3: ToolInvocation("finish-early", "finish_answer", "{}"),
            4: ToolInvocation("search-q2", "search_rules", '{"query":"准备动作如何使用？"}'),
        }
        return tool_decision(
            calls.get(
                self.step,
                ToolInvocation("finish-without-second-read", "finish_answer", "{}"),
            )
        )


class ImmediateFinishGateway:
    def __init__(self, _model):
        pass

    async def decide(self, messages, tools):
        return tool_decision(ToolInvocation("finish-now", "finish_answer", "{}"))

    async def stream_answer(self, messages):
        yield "追问答案基于服务端恢复的规则证据。[S1]"


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
        yield "服务端读取搜索候选后回答。[S1]"


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
        yield "服务端读取搜索候选后回答。[S1]"


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


class PausedStreamingGateway:
    def __init__(self) -> None:
        self.resume = asyncio.Event()

    async def stream_answer(self, _messages):
        yield "这是已经通过首段安全检查的规则回答内容。" * 8
        await self.resume.wait()
        yield "这是后续增量内容。[S1]"


class RuleTurnTest(unittest.IsolatedAsyncioTestCase):
    async def test_complex_planner_executes_topologically_and_reports_metrics(self) -> None:
        library = PlannerLibrary()
        query = (
            "我想规划6到12级法师兼职战士，必须保持指定法术环级；"
            "先核对兼职造成的施法进度变化，再根据是否满足环级决定兼职等级，"
            "并比较战士专长与装备收益。"
        )
        with patch("trpg_app.chat._log_turn_metrics") as log_metrics:
            events = [
                event
                async for event in run_rule_turn(
                    model=self.model(),
                    library=library,
                    messages=[{"role": "user", "content": query}],
                    gateway_factory=PlannerGateway,
                    enable_complex_planner=True,
                )
            ]

        context = log_metrics.call_args.kwargs["context"]
        self.assertTrue(context["complexPlannerUsed"])
        self.assertGreaterEqual(context["plannerTaskCount"], 2)
        self.assertEqual(
            context["plannerCompletedTaskCount"],
            context["plannerTaskCount"],
        )
        self.assertEqual(context["plannerFailedTaskCount"], 0)
        self.assertEqual(len(library.search_queries), context["plannerTaskCount"] - 1)
        final_prompt = PlannerGateway.final_messages[-1]["content"]
        self.assertIn("受限任务执行结果", final_prompt)
        self.assertIn('"depends_on"', final_prompt)
        self.assertIn('"synthesis_contract"', final_prompt)
        self.assertIn('"fact_ledger"', final_prompt)
        self.assertIn('"missing_input"', final_prompt)
        self.assertIn("不得擅自假设一个环级", final_prompt)
        self.assertEqual(context["synthesisContractVersion"], 1)
        self.assertGreaterEqual(context["synthesisCheckCount"], 4)
        self.assertEqual(context["synthesisUnresolvedFieldCount"], 1)
        self.assertEqual(context["synthesisMissingEvidenceCheckCount"], 0)
        self.assertEqual(context["factLedgerVersion"], 1)
        self.assertEqual(context["factValidationIssueCount"], 0)
        self.assertEqual(events[-1]["type"], "done")

    async def test_fact_ledger_retries_before_exposing_invalid_planner_answer(self) -> None:
        query = (
            "我想规划6到12级法师兼职战士，必须保持指定法术环级；"
            "先核对兼职造成的施法进度变化，再决定兼职等级。"
        )
        with patch("trpg_app.chat._log_turn_metrics") as log_metrics:
            events = [
                event
                async for event in run_rule_turn(
                    model=self.model(),
                    library=PlannerLibrary(),
                    messages=[{"role": "user", "content": query}],
                    gateway_factory=FactRetryPlannerGateway,
                    enable_complex_planner=True,
                )
            ]

        output = "".join(event.get("delta", "") for event in events)
        self.assertEqual(FactRetryPlannerGateway.attempts, 2)
        self.assertNotIn("法师5/战士4", output)
        self.assertIn("法师9/战士3", output)
        self.assertLessEqual(
            max(len(event.get("delta", "")) for event in events),
            24,
        )
        self.assertTrue(any(event["type"] == "sources" and event["sources"] for event in events))
        context = log_metrics.call_args.kwargs["context"]
        self.assertEqual(context["factValidationIssueCount"], 1)

    async def test_fact_ledger_rejects_repeated_invalid_answer_without_sources(self) -> None:
        query = (
            "我想规划6到12级法师兼职战士，必须保持指定法术环级；"
            "先核对兼职造成的施法进度变化，再决定兼职等级。"
        )
        events = [
            event
            async for event in run_rule_turn(
                model=self.model(),
                library=PlannerLibrary(),
                messages=[{"role": "user", "content": query}],
                gateway_factory=AlwaysInvalidFactPlannerGateway,
                enable_complex_planner=True,
            )
        ]

        output = "".join(event.get("delta", "") for event in events)
        self.assertEqual(AlwaysInvalidFactPlannerGateway.attempts, 2)
        self.assertNotIn("法师5/战士4", output)
        self.assertIn("连续未通过服务器事实校验", output)
        self.assertIn({"type": "sources", "sources": []}, events)

    async def test_final_answer_forwards_safe_deltas_before_provider_finishes(self) -> None:
        gateway = PausedStreamingGateway()
        stream = _stream_final_answer(
            gateway,
            [{"role": "user", "content": "规则问题"}],
            CitationRegistry(),
        )

        self.assertEqual(await anext(stream), {"type": "status", "status": "answering"})
        first_delta = await asyncio.wait_for(anext(stream), timeout=0.2)
        self.assertEqual(first_delta["type"], "text_delta")
        self.assertIn("规则回答内容", first_delta["delta"])

        gateway.resume.set()
        remaining = [event async for event in stream]
        self.assertIn(
            "这是后续增量内容。[S1]",
            "".join(event.get("delta", "") for event in remaining),
        )
        self.assertEqual(remaining[-1]["type"], "done")

    async def test_query_decomposition_defers_finish_until_each_question_has_sources(self) -> None:
        library = DecompositionLibrary()
        with patch("trpg_app.chat._log_turn_metrics") as log_metrics:
            events = [
                event
                async for event in run_rule_turn(
                    model=self.model(),
                    library=library,
                    messages=[
                        {
                            "role": "user",
                            "content": "借机攻击何时触发？准备动作如何使用？",
                        }
                    ],
                    gateway_factory=DecompositionCoverageGateway,
                    enable_query_decomposition=True,
                )
            ]

        context = log_metrics.call_args.kwargs["context"]
        self.assertEqual(library.search_queries, ["借机攻击何时触发？", "准备动作如何使用？"])
        self.assertEqual(context["routeComplexity"], "compound")
        self.assertEqual(context["decompositionQuestionCount"], 2)
        self.assertEqual(context["decompositionCoveredQuestionCount"], 2)
        self.assertEqual(context["decompositionSourcedQuestionCount"], 2)
        sources = next(event["sources"] for event in events if event["type"] == "sources")
        self.assertEqual(len(sources), 2)
        self.assertEqual(events[-1]["type"], "done")

    async def test_query_decomposition_keeps_simple_question_on_existing_loop(self) -> None:
        with patch("trpg_app.chat._log_turn_metrics") as log_metrics:
            events = [
                event
                async for event in run_rule_turn(
                    model=self.model(),
                    library=FakeLibrary(),
                    messages=[{"role": "user", "content": "借机攻击是什么？"}],
                    gateway_factory=FakeGateway,
                    enable_query_decomposition=True,
                )
            ]

        context = log_metrics.call_args.kwargs["context"]
        self.assertEqual(context["routeComplexity"], "simple")
        self.assertEqual(context["decompositionQuestionCount"], 0)
        self.assertFalse(context["routeNeedDecomposition"])
        self.assertNotIn("受限路由与多问题拆解", FakeGateway.first_system_prompt)
        self.assertEqual(events[-1]["type"], "done")

    async def test_query_decomposition_reads_unread_subquestion_before_finish(self) -> None:
        library = DecompositionLibrary()
        with patch("trpg_app.chat._log_turn_metrics") as log_metrics:
            events = [
                event
                async for event in run_rule_turn(
                    model=self.model(),
                    library=library,
                    messages=[
                        {
                            "role": "user",
                            "content": "借机攻击何时触发？准备动作如何使用？",
                        }
                    ],
                    gateway_factory=DecompositionSkipsSecondReadGateway,
                    enable_query_decomposition=True,
                )
            ]

        context = log_metrics.call_args.kwargs["context"]
        self.assertEqual(context["decompositionSourcedQuestionCount"], 2)
        self.assertIn(["pf1e:ready"], library.read_ids)
        sources = next(event["sources"] for event in events if event["type"] == "sources")
        self.assertEqual(len(sources), 2)

    async def test_dynamic_follow_up_recovers_evidence_before_immediate_finish(self) -> None:
        library = FakeLibrary()
        events = [
            event
            async for event in run_rule_turn(
                model=self.model(),
                library=library,
                messages=[
                    {
                        "role": "user",
                        "content": "有双武器格斗专长且副手是轻型武器时，主手和副手各受多少减值？",
                    },
                    {"role": "assistant", "content": "有专长时主副手都是-2。"},
                    {"role": "user", "content": "如果没有这个专长呢？"},
                ],
                gateway_factory=ImmediateFinishGateway,
                enable_dynamic_evidence_budget=True,
            )
        ]

        sources = next(event["sources"] for event in events if event["type"] == "sources")
        self.assertEqual(len(sources), 1)
        self.assertEqual(library.search_queries[0], "双武器格斗")
        self.assertTrue(library.read_ids)

    async def test_dynamic_follow_up_uses_intermediate_profile(self) -> None:
        with patch("trpg_app.chat._log_turn_metrics") as log_metrics:
            events = [
                event
                async for event in run_rule_turn(
                    model=self.model(),
                    library=FakeLibrary(),
                    messages=[
                        {"role": "user", "content": "双武器格斗的减值是多少？"},
                        {"role": "assistant", "content": "有专长时主副手都是-2。"},
                        {"role": "user", "content": "如果没有这个专长呢？"},
                    ],
                    gateway_factory=FakeGateway,
                    enable_dynamic_evidence_budget=True,
                )
            ]

        context = log_metrics.call_args.kwargs["context"]
        self.assertEqual(context["evidencePolicyMaxSearches"], 5)
        self.assertEqual(context["evidencePolicyMaxAnswerDocuments"], 6)
        self.assertEqual(events[-1]["type"], "done")

    async def test_dynamic_compare_defers_finish_until_both_sides_are_read(self) -> None:
        library = CompareLibrary()
        events = [
            event
            async for event in run_rule_turn(
                model=self.model(),
                library=library,
                messages=[
                    {
                        "role": "user",
                        "content": "全防御和以标准动作进行防御式战斗有什么区别？",
                    }
                ],
                gateway_factory=CompareCoverageGateway,
                enable_dynamic_evidence_budget=True,
            )
        ]

        self.assertEqual(library.search_queries, ["全防御", "防御式战斗"])
        sources = next(event["sources"] for event in events if event["type"] == "sources")
        self.assertEqual(len(sources), 2)
        self.assertEqual(events[-1]["type"], "done")

    async def test_dynamic_evidence_profile_is_applied_and_observable(self) -> None:
        with patch("trpg_app.chat._log_turn_metrics") as log_metrics:
            events = [
                event
                async for event in run_rule_turn(
                    model=self.model(),
                    library=FakeLibrary(),
                    messages=[{"role": "user", "content": "借机攻击是什么"}],
                    gateway_factory=FakeGateway,
                    enable_dynamic_evidence_budget=True,
                )
            ]

        context = log_metrics.call_args.kwargs["context"]
        self.assertTrue(context["dynamicEvidenceBudgetEnabled"])
        self.assertEqual(context["evidencePolicyVersion"], 2)
        self.assertEqual(context["evidencePolicyMaxSearches"], 3)
        self.assertEqual(context["evidencePolicyMaxAnswerDocuments"], 4)
        self.assertEqual(context["evidencePolicyMaxTokens"], 12_000)
        self.assertEqual(events[-1]["type"], "done")

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
        self.assertIn("本题目标字段：弱点", final_prompt)
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
        third_decision_tools = [
            message for message in FakeGateway.decision_messages[2] if message["role"] == "tool"
        ]
        self.assertEqual(len(third_decision_tools), 2)
        self.assertIn("compacted_search_results", third_decision_tools[0]["content"])
        self.assertIn("离开受威胁方格", third_decision_tools[1]["content"])

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

    async def test_model_stopping_after_search_reads_candidates(self) -> None:
        library = FakeLibrary()
        events = [
            event
            async for event in run_rule_turn(
                model=self.model(),
                library=library,
                messages=[{"role": "user", "content": "你知道魔战士吗"}],
                gateway_factory=SearchThenStopsGateway,
            )
        ]

        self.assertEqual(library.read_ids, [["pf1e:combat"]])
        self.assertIn("读取搜索候选后回答", "".join(
            event.get("delta", "") for event in events
        ))
        self.assertEqual(len(next(
            event["sources"] for event in events if event["type"] == "sources"
        )), 1)
        self.assertEqual(events[-1]["type"], "done")

    async def test_model_finishing_after_search_reads_candidates(self) -> None:
        library = FakeLibrary()
        events = [
            event
            async for event in run_rule_turn(
                model=self.model(),
                library=library,
                messages=[{"role": "user", "content": "你知道魔战士吗"}],
                gateway_factory=SearchThenFinishesGateway,
            )
        ]

        self.assertEqual(library.read_ids, [["pf1e:combat"]])
        self.assertIn("读取搜索候选后回答", "".join(
            event.get("delta", "") for event in events
        ))
        self.assertEqual(len(next(
            event["sources"] for event in events if event["type"] == "sources"
        )), 1)
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
