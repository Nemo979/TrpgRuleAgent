from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Protocol

from openai import AsyncOpenAI

from .config import ModelConfig
from .complex_planner import (
    PLANNER_VERSION,
    PLAN_EXECUTOR_DEADLINE_SECONDS,
    PLAN_TASK_TIMEOUT_SECONDS,
    ComplexPlan,
    PlanTaskType,
    PlanValidationError,
    TaskResult,
    build_complex_plan,
    planner_result_guidance,
)
from .context_budget import ContextBudget
from .conversation_state import ConversationState
from .evidence_policy import EvidenceBudgetPolicy, EvidenceBudgetProfile
from .fact_ledger import (
    FACT_LEDGER_CORE_SCHEMA_VERSION,
    ValidationIssue,
    ValidationSeverity,
)
from .fact_ledger_adapter import AdapterStatus, FactLedgerRuntime
from .fact_ledger_defaults import build_default_fact_ledger_runtime
from .libraries import Library
from .observability import (
    TurnPhaseTimer,
    UsageTotals,
    estimate_message_tokens,
    estimate_tokens,
    log_turn_metrics,
    summarize_messages,
)
from .query_intent import QueryIntent, QueryPlan, build_query_plan, classify_intent
from .query_decomposition import (
    QueryDecomposition,
    RouteDecision,
    decomposition_search_query,
    decompose_query,
    detect_domains,
    route_query,
)
from .synthesis_contract import (
    SYNTHESIS_CONTRACT_VERSION,
    SynthesisContract,
    assess_synthesis_contract,
    build_synthesis_contract,
)


logger = logging.getLogger("uvicorn.error")


# Hold only the provider preamble long enough to catch the known refusal and
# transient-service envelopes. Once the preamble is safe, later provider
# deltas are forwarded immediately instead of waiting for the full answer.
_ANSWER_STREAM_GUARD_CHARACTERS = 96


_PROVIDER_REFUSAL_PATTERNS = (
    re.compile(r"the request was rejected", re.IGNORECASE),
    re.compile(r"considered high risk", re.IGNORECASE),
    re.compile(r"request (?:has been |was )?blocked", re.IGNORECASE),
    re.compile(r"模型服务拒绝了本次请求"),
    re.compile(r"请求(?:被|已被).{0,12}(?:拒绝|拦截)"),
)
_UNFINISHED_PROCESS_PATTERNS = (
    re.compile(r"^\s*(?:让我|我会|我将|接下来(?:我会)?).{0,12}(?:继续)?(?:搜索|检索|读取|查找)"),
    re.compile(r"^\s*(?:let me|i(?:'ll| will)).{0,20}(?:search|retrieve|read)", re.IGNORECASE),
)
_TRANSIENT_OUTPUT_PATTERNS = (
    re.compile(r"模型服务当前繁忙"),
    re.compile(r"模型响应超时"),
    re.compile(r"暂时无法连接模型服务"),
    re.compile(r"service (?:is )?(?:busy|unavailable)", re.IGNORECASE),
)


SYSTEM_PROMPT = """你是一个基于证据的 TRPG 规则助手。

要求：
1. 规则结论必须先使用 search_rules 检索当前规则库。
2. 搜索摘要不能作为最终依据；使用 read_rules 读取相关章节。
3. 复杂问题应拆分检索，直到关键子问题都有证据，或继续搜索不再产生新证据。
4. 只能引用 read_rules 返回的 [S1]、[S2] 等标签。
5. 当前规则库没有充分依据时，明确说明证据不足；禁止用模型记忆补全规则。
6. 来源冲突时并列说明，不自动裁决。
7. 用中文回答，英文专有名词首次出现时附英文原名。
8. 使用 Markdown 排版。
9. 证据充分、可以生成最终回答时调用 finish_answer。
10. 每次响应最多调用一个工具，禁止并行调用多个 search_rules 或 read_rules。
11. search_rules 返回候选后，应优先 read_rules 核对候选；连续两次搜索仍没有可读
    证据时必须停止，不得继续换词穷举。
12. 检索计划含多个候选词时，先使用最短的规则条目名检索，并优先读取标题与条目名
    精确匹配的候选；只有该候选不足以覆盖问题字段时，才使用完整自然语言问题补查。

规则库边界：
1. 当前对话只绑定下方列出的一个规则库；id、name、system、edition、revision
   共同定义本轮规则范围，不能混用其他游戏系统、版本或规则库。
2. 对属于当前规则库或系统归属不明确的问题，必须遵循
   search_rules → read_rules → finish_answer 的证据流程。
3. 如果问题明显属于另一个游戏系统或另一个版本，不要盲目反复检索当前库，也不要
   调用 search_rules 或 read_rules；直接调用 finish_answer，随后只说明当前绑定规则库
   不覆盖该问题，并建议用户切换到对应规则库。
4. 异系统问题不得使用模型记忆回答其规则内容，也不得编造来源。"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_rules",
            "description": "搜索当前对话绑定的规则库，返回候选章节摘要。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_rules",
            "description": "读取搜索结果中的完整规则章节并取得引用标签。",
            "parameters": {
                "type": "object",
                "properties": {
                    "ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 20,
                    }
                },
                "required": ["ids"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish_answer",
            "description": "关键子问题均有来源或已确认证据不足后，结束检索并生成最终回答。",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
]


@dataclass(frozen=True)
class ToolInvocation:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ModelDecision:
    content: str
    tool_calls: tuple[ToolInvocation, ...]
    assistant_message: dict[str, Any]
    usage: TokenUsage | None = None


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int
    completion_tokens: int


class ModelGateway(Protocol):
    async def decide(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ModelDecision:
        ...

    def stream_answer(
        self,
        messages: list[dict[str, Any]],
    ) -> AsyncIterator[str]:
        ...


class OpenAIModelGateway:
    def __init__(self, model: ModelConfig) -> None:
        self.model = model
        self.client = AsyncOpenAI(
            api_key=model.api_key,
            base_url=model.base_url,
            timeout=model.request_timeout_seconds,
            max_retries=model.max_retries,
        )
        self._last_stream_usage: TokenUsage | None = None

    async def decide(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ModelDecision:
        response = await self.client.chat.completions.create(
            model=self.model.model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            max_tokens=self.model.max_output_tokens,
            extra_body=self._extra_body(),
        )
        message = response.choices[0].message
        calls = tuple(
            ToolInvocation(
                id=call.id,
                name=call.function.name,
                arguments=call.function.arguments,
            )
            for call in (message.tool_calls or ())
        )
        return ModelDecision(
            content=_visible_content(message.content or "", self.model.strip_thinking),
            tool_calls=calls,
            assistant_message=message.model_dump(exclude_none=True),
            usage=_token_usage(response.usage),
        )

    async def stream_answer(
        self,
        messages: list[dict[str, Any]],
    ) -> AsyncIterator[str]:
        self._last_stream_usage = None
        stream = await self.client.chat.completions.create(
            model=self.model.model,
            messages=messages,
            stream=True,
            max_tokens=self.model.max_output_tokens,
            extra_body=self._extra_body(),
        )
        buffered: list[str] = []
        async for chunk in stream:
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage is not None:
                self._last_stream_usage = _token_usage(chunk_usage)
            if not chunk.choices:
                continue
            content = chunk.choices[0].delta.content
            if content:
                if self.model.strip_thinking:
                    buffered.append(content)
                else:
                    yield content
        if buffered:
            visible = _visible_content("".join(buffered), True)
            if visible:
                yield visible

    def take_stream_usage(self) -> TokenUsage | None:
        usage = self._last_stream_usage
        self._last_stream_usage = None
        return usage

    def _extra_body(self) -> dict[str, Any] | None:
        if not self.model.disable_thinking:
            return None
        return {"chat_template_kwargs": {"enable_thinking": False}}


def _token_usage(value: Any) -> TokenUsage | None:
    if value is None:
        return None
    prompt_tokens = getattr(value, "prompt_tokens", None)
    completion_tokens = getattr(value, "completion_tokens", None)
    if prompt_tokens is None or completion_tokens is None:
        return None
    return TokenUsage(int(prompt_tokens), int(completion_tokens))


def _visible_content(content: str, strip_thinking: bool) -> str:
    if not strip_thinking:
        return content
    closing_tag = "</think>"
    if closing_tag in content:
        content = content.rsplit(closing_tag, 1)[1]
    opening_tag = "<think>"
    while opening_tag in content and closing_tag in content:
        before, remainder = content.split(opening_tag, 1)
        _, after = remainder.split(closing_tag, 1)
        content = before + after
    return content.lstrip()


@dataclass
class EvidenceBudget:
    max_searches: int = 6
    max_documents: int = 24
    max_evidence_characters: int = 80_000
    max_evidence_tokens: int = 40_000
    searches: int = 0
    documents: int = 0
    evidence_characters: int = 0
    evidence_tokens: int = 0
    skipped_documents: int = 0
    last_skipped_reasons: list[str] = field(default_factory=list)
    topic_allocations: dict[str, float] = field(default_factory=dict)
    topic_documents: dict[str, int] = field(default_factory=dict)
    topic_tokens: dict[str, int] = field(default_factory=dict)
    strict_topic_allocations: bool = False

    def consume_search(self) -> None:
        if self.searches >= self.max_searches:
            raise ValueError("检索已达到本轮安全上限")
        self.searches += 1

    def consume_documents(
        self,
        documents: list[dict[str, Any]],
        document_topics: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Admit readable evidence one document at a time.

        A single oversized document must not discard smaller documents in the
        same read request.  The caller can surface ``last_skipped_reasons`` and
        decide whether an entirely rejected batch should stop the loop.
        """
        accepted: list[dict[str, Any]] = []
        accepted_topics: dict[str, int] = {}
        accepted_topic_tokens: dict[str, int] = {}
        self.last_skipped_reasons = []
        for document in documents:
            if self.documents + len(accepted) >= self.max_documents:
                self.last_skipped_reasons.append("document_limit")
                continue
            characters = len(str(document.get("content", "")))
            tokens = estimate_tokens(str(document.get("content", "")))
            topic = (document_topics or {}).get(str(document.get("id", "")), "")
            if self.strict_topic_allocations and topic not in self.topic_allocations:
                self.last_skipped_reasons.append("unassigned_topic")
                continue
            if topic in self.topic_allocations:
                if self.strict_topic_allocations:
                    topic_document_limit = max(
                        1,
                        math.ceil(self.max_documents * self.topic_allocations[topic]),
                    )
                    topic_token_limit = max(
                        1,
                        int(self.max_evidence_tokens * self.topic_allocations[topic]),
                    )
                    if (
                        self.topic_documents.get(topic, 0)
                        + accepted_topics.get(topic, 0)
                        >= topic_document_limit
                    ):
                        self.last_skipped_reasons.append("topic_document_limit")
                        continue
                    if (
                        self.topic_tokens.get(topic, 0)
                        + accepted_topic_tokens.get(topic, 0)
                        + tokens
                        > topic_token_limit
                    ):
                        self.last_skipped_reasons.append("topic_token_budget")
                        continue
                else:
                    other_unfilled = sum(
                        1
                        for other in self.topic_allocations
                        if other != topic
                        and self.topic_documents.get(other, 0)
                        + accepted_topics.get(other, 0)
                        == 0
                    )
                    topic_document_limit = max(1, self.max_documents - other_unfilled)
                    if (
                        self.topic_documents.get(topic, 0)
                        + accepted_topics.get(topic, 0)
                        >= topic_document_limit
                    ):
                        self.last_skipped_reasons.append("topic_document_limit")
                        continue
                    recyclable_tokens = sum(
                        int(self.max_evidence_tokens * share)
                        for other, share in self.topic_allocations.items()
                        if other != topic
                        and self.topic_documents.get(other, 0)
                        + accepted_topics.get(other, 0)
                        > 0
                    )
                    topic_token_limit = max(
                        1,
                        int(self.max_evidence_tokens * self.topic_allocations[topic])
                        + recyclable_tokens,
                    )
                    if (
                        self.topic_tokens.get(topic, 0)
                        + accepted_topic_tokens.get(topic, 0)
                        + tokens
                        > topic_token_limit
                    ):
                        self.last_skipped_reasons.append("topic_token_budget")
                        continue
            if self.evidence_characters + sum(
                len(str(item.get("content", ""))) for item in accepted
            ) + characters > self.max_evidence_characters:
                self.last_skipped_reasons.append("evidence_budget")
                continue
            if self.evidence_tokens + sum(
                estimate_tokens(str(item.get("content", ""))) for item in accepted
            ) + tokens > self.max_evidence_tokens:
                self.last_skipped_reasons.append("evidence_token_budget")
                continue
            accepted.append(document)
            if topic in self.topic_allocations:
                accepted_topics[topic] = accepted_topics.get(topic, 0) + 1
                accepted_topic_tokens[topic] = (
                    accepted_topic_tokens.get(topic, 0) + tokens
                )
        self.documents += len(accepted)
        self.evidence_characters += sum(
            len(str(item.get("content", ""))) for item in accepted
        )
        self.evidence_tokens += sum(
            estimate_tokens(str(item.get("content", ""))) for item in accepted
        )
        self.skipped_documents += len(documents) - len(accepted)
        for topic, count in accepted_topics.items():
            self.topic_documents[topic] = self.topic_documents.get(topic, 0) + count
        for topic, tokens in accepted_topic_tokens.items():
            self.topic_tokens[topic] = self.topic_tokens.get(topic, 0) + tokens
        return accepted


@dataclass
class ToolLoopController:
    max_searches_without_read: int = 2
    max_searches_with_evidence: int = 3
    max_answer_documents: int = 8
    seen_queries: set[str] = field(default_factory=set)
    seen_result_ids: set[str] = field(default_factory=set)
    searches_without_read: int = 0
    invalid_tool_calls: int = 0
    latest_result_ids: list[str] = field(default_factory=list)
    result_batches: list[list[str]] = field(default_factory=list)
    conversation_state: ConversationState = field(default_factory=ConversationState)
    latest_user_message: str = ""
    intent: QueryIntent = QueryIntent.RULE_FACT
    evidence_profile: EvidenceBudgetProfile | None = None
    document_topics: dict[str, str] = field(default_factory=dict)
    deferred_finishes: int = 0
    deferred_decomposition_finishes: int = 0
    planned_queries: tuple[str, ...] = ()
    is_follow_up: bool = False
    decomposition: QueryDecomposition = field(default_factory=QueryDecomposition)
    active_subquestion_id: str = ""
    searched_subquestions: set[str] = field(default_factory=set)
    subquestion_document_ids: dict[str, set[str]] = field(default_factory=dict)

    def before_search(self, query: str) -> str | None:
        normalized = _normalize_query(query)
        if not normalized:
            return "empty_query"
        if normalized in self.seen_queries:
            return "repeated_query"
        if self.searches_without_read >= self.max_searches_without_read:
            return "searches_without_read"
        self.seen_queries.add(normalized)
        self.active_subquestion_id = self._subquestion_for_query(query)
        return None

    def enrich_query(self, query: str) -> str:
        return self.conversation_state.enrich_search_query(
            query,
            self.latest_user_message,
        )

    def after_search(self, query: str, hits: list[dict[str, Any]]) -> str | None:
        self.invalid_tool_calls = 0
        self.searches_without_read += 1
        self.latest_result_ids = [
            str(hit["id"]) for hit in hits if hit.get("id")
        ]
        if self.latest_result_ids:
            self.result_batches.append(list(self.latest_result_ids))
        if self.active_subquestion_id:
            self.searched_subquestions.add(self.active_subquestion_id)
            for hit in hits:
                document_id = str(hit.get("id", ""))
                if document_id:
                    self.document_topics[document_id] = self.active_subquestion_id
        elif self.evidence_profile is not None:
            for hit in hits:
                document_id = str(hit.get("id", ""))
                if document_id:
                    self.document_topics[document_id] = EvidenceBudgetPolicy.classify_topic(
                        self.intent,
                        self.latest_user_message,
                        query,
                        hit,
                        self.evidence_profile,
                    )
        result_ids = {str(hit.get("id", "")) for hit in hits if hit.get("id")}
        if not result_ids:
            return "no_results"
        new_ids = result_ids - self.seen_result_ids
        self.seen_result_ids.update(result_ids)
        if not new_ids:
            return "repeated_results"
        return None

    def _subquestion_for_query(self, query: str) -> str:
        if not self.decomposition.questions:
            return ""
        normalized = _normalize_query(query)
        # Prefer an exact containment match across every task before applying
        # fuzzy matching. Otherwise an earlier task sharing terms such as
        # “战士专长” can steal evidence from the later exact equipment task.
        for question in self.decomposition.questions:
            question_normalized = _normalize_query(question.question)
            if normalized in question_normalized or question_normalized in normalized:
                return question.id
        for question in self.decomposition.questions:
            question_normalized = _normalize_query(question.question)
            if _longest_common_substring_length(normalized, question_normalized) >= 4:
                return question.id
        query_domains = set(detect_domains(query))
        domain_matches = [
            question
            for question in self.decomposition.questions
            if question.id not in self.searched_subquestions
            and question.domain != "rule"
            and question.domain in query_domains
        ]
        return domain_matches[0].id if len(domain_matches) == 1 else ""

    def missing_subquestions(self) -> tuple[Any, ...]:
        return tuple(
            question
            for question in self.decomposition.questions
            if question.id not in self.searched_subquestions
        )

    def unsourced_subquestions(self) -> tuple[Any, ...]:
        return tuple(
            question
            for question in self.decomposition.questions
            if question.id in self.searched_subquestions
            and not self.subquestion_document_ids.get(question.id)
        )

    def unread_subquestion_candidates(
        self,
        question_id: str,
        registered_ids: set[str],
    ) -> list[str]:
        return [
            document_id
            for document_id, topic in self.document_topics.items()
            if topic == question_id and document_id not in registered_ids
        ]

    def record_admitted_documents(self, documents: list[dict[str, Any]]) -> None:
        for document in documents:
            document_id = str(document.get("id", ""))
            question_id = self.document_topics.get(document_id, "")
            if question_id:
                self.subquestion_document_ids.setdefault(question_id, set()).add(document_id)

    def record_existing_document_ids(self, document_ids: list[str]) -> None:
        for document_id in document_ids:
            if not document_id:
                continue
            question_id = self.document_topics.get(document_id, "")
            if question_id:
                self.subquestion_document_ids.setdefault(question_id, set()).add(document_id)

    def unread_candidates(self, registered_ids: set[str]) -> list[str]:
        """Return unseen candidates round-robin across all search batches."""
        ordered: list[str] = []
        seen = set(registered_ids)
        depth = max((len(batch) for batch in self.result_batches), default=0)
        for index in range(depth):
            for batch in self.result_batches:
                if index >= len(batch):
                    continue
                document_id = batch[index]
                if document_id in seen:
                    continue
                seen.add(document_id)
                ordered.append(document_id)
        return ordered

    def after_read(self) -> None:
        self.searches_without_read = 0
        self.invalid_tool_calls = 0

    def after_invalid_tool_call(self) -> str | None:
        self.invalid_tool_calls += 1
        if self.invalid_tool_calls >= 2:
            return "invalid_tool_calls"
        return None


@dataclass(frozen=True)
class ToolExecution:
    content: str
    status: str
    stop_reason: str | None = None
    query_hash: str | None = None
    new_documents: int = 0


@dataclass
class CitationRegistry:
    by_document_id: dict[str, tuple[str, dict[str, Any]]] = field(default_factory=dict)

    def register(self, document: dict[str, Any]) -> str:
        document_id = str(document["id"])
        existing = self.by_document_id.get(document_id)
        if existing:
            return existing[0]
        label = f"S{len(self.by_document_id) + 1}"
        self.by_document_id[document_id] = (label, document)
        return label

    def public(self) -> list[dict[str, Any]]:
        return [
            {
                "label": label,
                "documentId": document_id,
                "title": document["title"],
                "fullPath": document["fullPath"],
                "metadata": document.get("metadata", {}),
            }
            for document_id, (label, document) in self.by_document_id.items()
        ]

    def labels(self) -> list[str]:
        return [label for label, _document in self.by_document_id.values()]


async def run_rule_turn(
    *,
    model: ModelConfig,
    library: Library,
    messages: list[dict[str, str]],
    gateway_factory: Callable[[ModelConfig], ModelGateway] = OpenAIModelGateway,
    request_id: str | None = None,
    enable_dynamic_evidence_budget: bool = False,
    enable_query_decomposition: bool = False,
    enable_complex_planner: bool = False,
    enable_fact_ledger: bool = False,
) -> AsyncIterator[dict[str, Any]]:
    gateway = gateway_factory(model)
    state = ConversationState.from_messages(messages)
    timer = TurnPhaseTimer()
    latest_user_message = next(
        (
            str(message.get("content", ""))
            for message in reversed(messages)
            if message.get("role") == "user"
        ),
        "",
    )
    route_decision: RouteDecision | None = None
    decomposition = QueryDecomposition()
    complex_plan: ComplexPlan | None = None
    synthesis_contract: SynthesisContract | None = None
    plan_results: list[TaskResult] = []
    fact_ledger = FactLedgerRuntime(
        status=(
            AdapterStatus.DISABLED
            if not enable_fact_ledger
            else AdapterStatus.PLANNER_DISABLED
            if not enable_complex_planner
            else AdapterStatus.NOT_APPLICABLE
        )
    )
    fact_validation_issue_codes: list[str] = []
    planner_seconds = 0.0
    executor_seconds = 0.0
    planner_fallback = False
    if enable_query_decomposition or enable_complex_planner:
        route_decision = route_query(latest_user_message, state)
        decomposition = decompose_query(latest_user_message, route_decision)
        timer.routing_seconds += route_decision.routing_seconds
    if enable_complex_planner and route_decision is not None:
        planner_started = time.monotonic()
        try:
            complex_plan = build_complex_plan(
                latest_user_message,
                route_decision,
                decomposition,
            )
            if complex_plan is not None:
                synthesis_contract = build_synthesis_contract(
                    latest_user_message,
                    complex_plan,
                )
                decomposition = complex_plan.evidence_decomposition()
        except (PlanValidationError, ValueError):
            planner_fallback = True
            complex_plan = None
        planner_seconds = time.monotonic() - planner_started
    system_prompt = _system_prompt(
        library,
        state,
        latest_user_message,
        # Keep the enabled-but-simple path byte-for-byte compatible with the
        # existing agent prompt. Routing remains observable in metrics, while
        # only an actual decomposition is exposed to the model.
        route_decision=route_decision if decomposition.questions else None,
        decomposition=decomposition,
    )
    state_context = state.prompt_context()
    system_total_tokens = estimate_tokens(system_prompt)
    state_tokens = estimate_tokens(state_context)
    system_tokens = max(0, system_total_tokens - state_tokens)
    context_budget = ContextBudget.allocate(
        context_window_tokens=model.context_window,
        output_reserve_tokens=model.max_output_tokens,
        system_tokens=system_tokens,
        state_tokens=state_tokens,
    )
    conversation: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": system_prompt,
        }
    ]
    trimmed, dropped_count = context_budget.trim_recent_messages(messages)
    conversation.extend(trimmed)
    intent = route_decision.intent if route_decision is not None else classify_intent(latest_user_message)
    query_plan = build_query_plan(latest_user_message, state)
    previous_user_message = next(
        (
            str(message.get("content", ""))
            for message in reversed(messages[:-1])
            if message.get("role") == "user"
        ),
        "",
    )
    is_follow_up = bool(previous_user_message) and EvidenceBudgetPolicy.is_follow_up(
        latest_user_message
    )
    if is_follow_up:
        previous_plan = build_query_plan(previous_user_message, state)
        query_plan = QueryPlan(
            query_plan.intent,
            tuple(
                dict.fromkeys(
                    (
                        *previous_plan.queries,
                        f"{previous_user_message} {latest_user_message}",
                        *query_plan.queries,
                    )
                )
            ),
        )
    evidence_profile = (
        EvidenceBudgetPolicy.allocate(intent, context_budget, query_plan, state)
        if enable_dynamic_evidence_budget
        else EvidenceBudgetPolicy.fixed(context_budget)
    )
    decomposition_allocations = (
        {
            question.id: 1 / len(decomposition.questions)
            for question in decomposition.questions
        }
        if decomposition.questions
        else {}
    )
    budget = EvidenceBudget(
        max_searches=(evidence_profile.max_searches if enable_dynamic_evidence_budget else 6),
        max_documents=(
            evidence_profile.max_answer_documents
            if enable_dynamic_evidence_budget
            else 24
        ),
        max_evidence_tokens=evidence_profile.max_evidence_tokens,
        topic_allocations=(
            decomposition_allocations
            if decomposition_allocations
            else dict(evidence_profile.topic_allocations)
            if enable_dynamic_evidence_budget
            else {}
        ),
        strict_topic_allocations=bool(decomposition_allocations),
    )
    planned_queries = tuple(
        dict.fromkeys(
            (
                *(decomposition_search_query(question) for question in decomposition.questions),
                *query_plan.queries,
            )
        )
    )
    citations = CitationRegistry()
    controller = ToolLoopController(
        max_searches_with_evidence=(
            len(decomposition.questions)
            if complex_plan is not None
            else (
                max(
                    1,
                    evidence_profile.max_searches
                    - (
                        1
                        if len(evidence_profile.topic_allocations) > 1 or is_follow_up
                        else 0
                    ),
                )
                if enable_dynamic_evidence_budget
                else 3
            )
        ),
        max_answer_documents=(
            min(
                budget.max_documents,
                max(
                    evidence_profile.max_answer_documents,
                    len(decomposition.questions) * 4,
                ),
            )
            if decomposition.questions
            else evidence_profile.max_answer_documents
        ),
        conversation_state=state,
        latest_user_message=latest_user_message,
        intent=intent,
        evidence_profile=evidence_profile,
        planned_queries=planned_queries,
        is_follow_up=is_follow_up,
        decomposition=decomposition,
    )
    history = summarize_messages(trimmed)
    original_history = summarize_messages(messages)
    final_answer_tokens: list[int] = [0]

    def _emit_turn_metrics(stop_reason: str, final_tokens: int = 0) -> None:
        _log_turn_metrics(
            request_id=request_id,
            model=model,
            library=library,
            timer=timer,
            context={
                "historyTokens": history["tokens"],
                "originalHistoryTokens": original_history["tokens"],
                "systemTokens": system_tokens,
                "stateTokens": state_tokens,
                "stateFieldCount": state.field_count(),
                "dynamicEvidenceBudgetEnabled": enable_dynamic_evidence_budget,
                "evidencePolicyVersion": evidence_profile.policy_version,
                "evidencePolicyMaxSearches": evidence_profile.max_searches,
                "evidencePolicyMaxAnswerDocuments": (
                    evidence_profile.max_answer_documents
                ),
                "evidencePolicyMaxTokens": evidence_profile.max_evidence_tokens,
                "evidencePolicyUsedTopics": len(budget.topic_documents),
                "queryDecompositionEnabled": enable_query_decomposition,
                "complexPlannerEnabled": enable_complex_planner,
                "complexPlannerUsed": complex_plan is not None,
                "complexPlannerFallback": planner_fallback,
                "plannerVersion": PLANNER_VERSION if complex_plan is not None else 0,
                "plannerTaskCount": len(complex_plan.tasks) if complex_plan else 0,
                "plannerCompletedTaskCount": sum(
                    result.status == "completed" for result in plan_results
                ),
                "plannerFailedTaskCount": sum(
                    result.status != "completed" for result in plan_results
                ),
                "synthesisContractVersion": (
                    SYNTHESIS_CONTRACT_VERSION if synthesis_contract else 0
                ),
                "synthesisCheckCount": (
                    len(synthesis_contract.checks) if synthesis_contract else 0
                ),
                "synthesisUnresolvedFieldCount": (
                    len(synthesis_contract.unresolved_fields)
                    if synthesis_contract
                    else 0
                ),
                "synthesisMissingEvidenceCheckCount": (
                    sum(
                        item.status == "missing_evidence"
                        for item in assess_synthesis_contract(
                            synthesis_contract,
                            complex_plan,
                            plan_results,
                        )
                    )
                    if synthesis_contract and complex_plan
                    else 0
                ),
                "factLedgerEnabled": enable_fact_ledger,
                "factLedgerStatus": fact_ledger.status.value,
                "factLedgerAdapterId": fact_ledger.adapter_id,
                "factLedgerAdapterVersion": fact_ledger.adapter_version,
                "factLedgerVersion": (
                    FACT_LEDGER_CORE_SCHEMA_VERSION
                    if fact_ledger.ledger is not None
                    else 0
                ),
                "factLedgerRecordCount": fact_ledger.record_count,
                "factLedgerKnownCount": (
                    sum(
                        record.status.value == "known"
                        for record in fact_ledger.ledger.records
                    )
                    if fact_ledger.ledger is not None
                    else 0
                ),
                "factLedgerUnknownCount": (
                    sum(
                        record.status.value == "unknown"
                        for record in fact_ledger.ledger.records
                    )
                    if fact_ledger.ledger is not None
                    else 0
                ),
                "factLedgerConflictingCount": (
                    sum(
                        record.status.value == "conflicting"
                        for record in fact_ledger.ledger.records
                    )
                    if fact_ledger.ledger is not None
                    else 0
                ),
                "factValidationIssueCount": len(fact_validation_issue_codes),
                "factLedgerBuildSeconds": round(fact_ledger.build_seconds, 6),
                "factLedgerValidationSeconds": round(
                    fact_ledger.validation_seconds,
                    6,
                ),
                "plannerSeconds": round(planner_seconds, 6),
                "executorSeconds": round(executor_seconds, 6),
                "routerVersion": route_decision.router_version if route_decision else 0,
                "routeComplexity": (
                    route_decision.complexity.value if route_decision else "disabled"
                ),
                "routeReasonCode": (
                    route_decision.reason_code if route_decision else "disabled"
                ),
                "routeDomainCount": len(route_decision.domains) if route_decision else 0,
                "routeNeedDecomposition": bool(
                    route_decision and route_decision.need_decomposition
                ),
                "decompositionQuestionCount": len(decomposition.questions),
                "decompositionCoveredQuestionCount": len(
                    controller.searched_subquestions
                ),
                "decompositionSourcedQuestionCount": len(
                    controller.subquestion_document_ids
                ),
                **context_budget.metrics(),
            },
            budget=budget,
            citations=citations,
            stop_reason=stop_reason,
            dropped_messages=dropped_count,
            final_answer_tokens=final_tokens,
            intent=intent.value,
            query_hashes=[_query_hash(query) for query in controller.seen_queries],
            usage=timer.usage,
        )

    if dropped_count:
        yield {"type": "context_truncated", "droppedMessages": dropped_count}
    yield {"type": "status", "status": "thinking"}
    if complex_plan is not None:
        executor_started = time.monotonic()
        executor_deadline = executor_started + min(
            model.request_timeout_seconds,
            PLAN_EXECUTOR_DEADLINE_SECONDS,
        )
        results_by_id: dict[str, TaskResult] = {}
        for task_index, task in enumerate(complex_plan.tasks, start=1):
            failed_dependencies = [
                dependency
                for dependency in task.depends_on
                if results_by_id.get(dependency) is None
                or results_by_id[dependency].status != "completed"
            ]
            if failed_dependencies:
                result = TaskResult(
                    task_id=task.id,
                    status="blocked_dependency",
                    missing_fields=tuple(failed_dependencies),
                    visible_summary="前置任务未取得完整证据，未执行本任务。",
                )
                plan_results.append(result)
                results_by_id[task.id] = result
                continue
            if time.monotonic() >= executor_deadline:
                result = TaskResult(
                    task_id=task.id,
                    status="deadline_exceeded",
                    missing_fields=("global_deadline",),
                    visible_summary="全局执行期限已到，未执行本任务。",
                )
                plan_results.append(result)
                results_by_id[task.id] = result
                continue
            if task.type == PlanTaskType.SELECTION:
                dependency_sources = tuple(
                    dict.fromkeys(
                        source_id
                        for dependency in task.depends_on
                        for source_id in results_by_id[dependency].source_ids
                    )
                )
                result = TaskResult(
                    task_id=task.id,
                    status="completed" if dependency_sources else "missing_evidence",
                    source_ids=dependency_sources,
                    missing_fields=() if dependency_sources else ("rule_evidence",),
                    visible_summary=(
                        "前置规则任务已完成，可执行条件分支与选择。"
                        if dependency_sources
                        else "没有可用于条件分支的已注册来源。"
                    ),
                )
                plan_results.append(result)
                results_by_id[task.id] = result
                continue

            task_started = time.monotonic()
            if budget.searches >= budget.max_searches:
                result = TaskResult(
                    task_id=task.id,
                    status="budget_exhausted",
                    missing_fields=("search_budget",),
                    visible_summary="全局搜索预算已耗尽。",
                )
                plan_results.append(result)
                results_by_id[task.id] = result
                continue
            controller.searches_without_read = 0
            registered_before_task = set(citations.by_document_id)
            search_started = time.monotonic()
            execution = _execute_tool(
                name="search_rules",
                arguments=json.dumps(
                    {"query": task.query, "limit": 10},
                    ensure_ascii=False,
                ),
                library=library,
                budget=budget,
                citations=citations,
                controller=controller,
            )
            timer.retrieval_seconds += time.monotonic() - search_started
            yield {"type": "status", "status": execution.status}
            _log_tool_step(
                request_id=request_id,
                model=model,
                library=library,
                decision_index=task_index,
                requested_calls=0,
                executed_tool="search_rules_planner",
                budget=budget,
                stop_reason=execution.stop_reason,
                query_hash=execution.query_hash,
            )
            remaining_documents = max(
                0,
                controller.max_answer_documents - budget.documents,
            )
            candidate_ids = [
                document_id
                for document_id in controller.latest_result_ids
                if document_id not in citations.by_document_id
            ]
            if candidate_ids and remaining_documents:
                read_started = time.monotonic()
                read_execution = _execute_tool(
                    name="read_rules",
                    arguments=json.dumps(
                        {"ids": candidate_ids[: min(4, remaining_documents)]},
                        ensure_ascii=False,
                    ),
                    library=library,
                    budget=budget,
                    citations=citations,
                    controller=controller,
                )
                timer.read_seconds += time.monotonic() - read_started
                yield {"type": "status", "status": read_execution.status}
                _log_tool_step(
                    request_id=request_id,
                    model=model,
                    library=library,
                    decision_index=task_index,
                    requested_calls=0,
                    executed_tool="read_rules_planner",
                    budget=budget,
                    stop_reason=read_execution.stop_reason,
                    new_documents=read_execution.new_documents,
                )
            source_ids = tuple(
                sorted(
                    set(citations.by_document_id).difference(registered_before_task)
                    | {
                        document_id
                        for document_id in controller.latest_result_ids
                        if document_id in citations.by_document_id
                    }
                    | controller.subquestion_document_ids.get(task.id, set())
                )
            )
            elapsed = time.monotonic() - task_started
            status = (
                "task_timeout"
                if elapsed > PLAN_TASK_TIMEOUT_SECONDS
                else "completed"
                if source_ids
                else "missing_evidence"
            )
            result = TaskResult(
                task_id=task.id,
                status=status,
                source_ids=source_ids,
                missing_fields=() if status == "completed" else ("rule_evidence",),
                visible_summary=(
                    f"已注册 {len(source_ids)} 个规则来源。"
                    if source_ids
                    else "未取得可注册的规则来源。"
                ),
            )
            plan_results.append(result)
            results_by_id[task.id] = result

        executor_seconds = time.monotonic() - executor_started
        if citations.by_document_id:
            fact_ledger_payload: dict[str, Any] | None = None
            answer_validator: Callable[[str], tuple[ValidationIssue, ...]] | None = None
            if enable_fact_ledger:
                fact_ledger = build_default_fact_ledger_runtime(
                    library.manifest,
                    complex_plan.goal,
                    citations.by_document_id.values(),
                )
                candidate_payload: dict[str, Any] | None = None
                if fact_ledger.active:
                    candidate_payload = fact_ledger.public()
                if fact_ledger.active:
                    # Verify the optional validation boundary before it changes
                    # final-answer streaming behavior.
                    fact_ledger.validate("")
                if fact_ledger.active:
                    fact_ledger_payload = candidate_payload
                    answer_validator = fact_ledger.validate
            async for event in _stream_final_answer(
                gateway,
                conversation,
                citations,
                timer,
                final_answer_tokens,
                final_guidance=planner_result_guidance(
                    complex_plan,
                    plan_results,
                    synthesis_contract=synthesis_contract,
                    fact_ledger=fact_ledger_payload,
                ),
                answer_validator=answer_validator,
                validation_issue_codes=fact_validation_issue_codes,
            ):
                yield event
            _emit_turn_metrics("planner_finish", final_answer_tokens[0])
            return
        async for event in _finish_after_controller_stop(
            gateway=gateway,
            conversation=conversation,
            citations=citations,
            library=library,
            stop_reason="planner_no_evidence",
            model=model,
            budget=budget,
            controller=controller,
            request_id=request_id,
            decision_index=len(complex_plan.tasks),
        ):
            yield event
        _emit_turn_metrics("planner_no_evidence", final_answer_tokens[0])
        return
    if decomposition.questions:
        for question_index, question in enumerate(decomposition.questions, start=1):
            if budget.searches >= budget.max_searches:
                break
            controller.searches_without_read = 0
            search_started = time.monotonic()
            execution = _execute_tool(
                name="search_rules",
                arguments=json.dumps(
                    {
                        "query": decomposition_search_query(question),
                        "limit": 10,
                    },
                    ensure_ascii=False,
                ),
                library=library,
                budget=budget,
                citations=citations,
                controller=controller,
            )
            timer.retrieval_seconds += time.monotonic() - search_started
            yield {"type": "status", "status": execution.status}
            _log_tool_step(
                request_id=request_id,
                model=model,
                library=library,
                decision_index=question_index,
                requested_calls=0,
                executed_tool="search_rules_decomposition",
                budget=budget,
                stop_reason=execution.stop_reason,
                query_hash=execution.query_hash,
            )
            remaining_documents = max(
                0,
                controller.max_answer_documents - budget.documents,
            )
            candidate_ids = [
                document_id
                for document_id in controller.latest_result_ids
                if document_id not in citations.by_document_id
            ]
            if not candidate_ids or not remaining_documents:
                continue
            read_started = time.monotonic()
            read_execution = _execute_tool(
                name="read_rules",
                arguments=json.dumps(
                    {"ids": candidate_ids[: min(4, remaining_documents)]},
                    ensure_ascii=False,
                ),
                library=library,
                budget=budget,
                citations=citations,
                controller=controller,
            )
            timer.read_seconds += time.monotonic() - read_started
            yield {"type": "status", "status": read_execution.status}
            _log_tool_step(
                request_id=request_id,
                model=model,
                library=library,
                decision_index=question_index,
                requested_calls=0,
                executed_tool="read_rules_decomposition",
                budget=budget,
                stop_reason=read_execution.stop_reason,
                new_documents=read_execution.new_documents,
            )
        if citations.by_document_id:
            async for event in _stream_final_answer(
                gateway,
                conversation,
                citations,
                timer,
                final_answer_tokens,
            ):
                yield event
            _emit_turn_metrics("decomposition_finish", final_answer_tokens[0])
            return
    for decision_index in range(1, 11):
        decision_conversation = context_budget.prepare_decision_messages(conversation)
        decision_prompt_tokens = estimate_message_tokens(decision_conversation)
        decision_started = time.monotonic()
        decision = await gateway.decide(decision_conversation, TOOLS)
        timer.decision_seconds += time.monotonic() - decision_started
        timer.usage.add(
            prompt_tokens=decision.usage.prompt_tokens if decision.usage else None,
            completion_tokens=decision.usage.completion_tokens if decision.usage else None,
            estimated_prompt_tokens=decision_prompt_tokens,
            estimated_completion_tokens=estimate_tokens(
                json.dumps(decision.assistant_message, ensure_ascii=False)
            ),
        )
        conversation.append(decision.assistant_message)
        if not decision.tool_calls:
            # Compatibility fallback for models that do not follow finish_answer.
            if not citations.by_document_id:
                if not budget.searches:
                    fallback_search = _execute_tool(
                        name="search_rules",
                        arguments=json.dumps(
                            {"query": _latest_user_content(conversation), "limit": 10},
                            ensure_ascii=False,
                        ),
                        library=library,
                        budget=budget,
                        citations=citations,
                        controller=controller,
                    )
                    yield {"type": "status", "status": fallback_search.status}
                    _log_tool_step(
                        request_id=request_id,
                        model=model,
                        library=library,
                        decision_index=decision_index,
                        requested_calls=0,
                        executed_tool="search_rules_fallback",
                        budget=budget,
                        stop_reason=fallback_search.stop_reason,
                        query_hash=fallback_search.query_hash,
                    )
                    async for event in _finish_after_controller_stop(
                        gateway=gateway,
                        conversation=conversation,
                        citations=citations,
                        library=library,
                        stop_reason="model_skipped_tools",
                        model=model,
                        budget=budget,
                        controller=controller,
                        request_id=request_id,
                        decision_index=decision_index,
                    ):
                        yield event
                    _emit_turn_metrics("model_skipped_tools", final_answer_tokens[0])
                    return
                if budget.searches:
                    _log_tool_step(
                        request_id=request_id,
                        model=model,
                        library=library,
                        decision_index=decision_index,
                        requested_calls=0,
                        executed_tool="none",
                        budget=budget,
                        stop_reason="model_stopped_without_evidence",
                    )
                    async for event in _finish_after_controller_stop(
                        gateway=gateway,
                        conversation=conversation,
                        citations=citations,
                        library=library,
                        stop_reason="model_stopped_without_evidence",
                        model=model,
                        budget=budget,
                        controller=controller,
                        request_id=request_id,
                        decision_index=decision_index,
                    ):
                        yield event
                    _emit_turn_metrics("model_stopped_without_evidence", final_answer_tokens[0])
                    return
                raise RuntimeError("模型未读取规则证据")
            async for event in _stream_final_answer(gateway, conversation, citations, timer, final_answer_tokens):
                yield event
            _emit_turn_metrics("model_finish", final_answer_tokens[0])
            return

        requested_calls = list(decision.tool_calls)
        should_finish = any(call.name == "finish_answer" for call in requested_calls)
        if should_finish:
            if (
                enable_dynamic_evidence_budget
                and controller.is_follow_up
                and not citations.by_document_id
                and controller.planned_queries
                and budget.searches < budget.max_searches
            ):
                controller.searches_without_read = 0
                follow_up_search = _execute_tool(
                    name="search_rules",
                    arguments=json.dumps(
                        {"query": controller.planned_queries[0], "limit": 10},
                        ensure_ascii=False,
                    ),
                    library=library,
                    budget=budget,
                    citations=citations,
                    controller=controller,
                )
                yield {"type": "status", "status": follow_up_search.status}
                _log_tool_step(
                    request_id=request_id,
                    model=model,
                    library=library,
                    decision_index=decision_index,
                    requested_calls=len(requested_calls),
                    executed_tool="search_rules_followup_fallback",
                    budget=budget,
                    stop_reason=follow_up_search.stop_reason,
                    query_hash=follow_up_search.query_hash,
                )
                remaining_documents = max(
                    0,
                    controller.max_answer_documents - budget.documents,
                )
                if controller.latest_result_ids and remaining_documents:
                    follow_up_read = _execute_tool(
                        name="read_rules",
                        arguments=json.dumps(
                            {
                                "ids": controller.latest_result_ids[
                                    : min(4, remaining_documents)
                                ]
                            },
                            ensure_ascii=False,
                        ),
                        library=library,
                        budget=budget,
                        citations=citations,
                        controller=controller,
                    )
                    yield {"type": "status", "status": follow_up_read.status}
                    _log_tool_step(
                        request_id=request_id,
                        model=model,
                        library=library,
                        decision_index=decision_index,
                        requested_calls=0,
                        executed_tool="read_rules_followup_fallback",
                        budget=budget,
                        stop_reason=follow_up_read.stop_reason,
                        new_documents=follow_up_read.new_documents,
                    )
            for question in controller.unsourced_subquestions():
                remaining_documents = max(
                    0,
                    controller.max_answer_documents - budget.documents,
                )
                candidate_ids = controller.unread_subquestion_candidates(
                    question.id,
                    set(citations.by_document_id),
                )
                if not candidate_ids or not remaining_documents:
                    continue
                decomposition_read = _execute_tool(
                    name="read_rules",
                    arguments=json.dumps(
                        {"ids": candidate_ids[: min(4, remaining_documents)]},
                        ensure_ascii=False,
                    ),
                    library=library,
                    budget=budget,
                    citations=citations,
                    controller=controller,
                )
                yield {"type": "status", "status": decomposition_read.status}
                _log_tool_step(
                    request_id=request_id,
                    model=model,
                    library=library,
                    decision_index=decision_index,
                    requested_calls=len(requested_calls),
                    executed_tool="read_rules_decomposition_source_recovery",
                    budget=budget,
                    stop_reason=decomposition_read.stop_reason,
                    new_documents=decomposition_read.new_documents,
                )
            missing_subquestions = controller.missing_subquestions()
            if (
                missing_subquestions
                and budget.searches < budget.max_searches
                and controller.deferred_decomposition_finishes
                < len(decomposition.questions)
            ):
                next_question = missing_subquestions[0]
                controller.deferred_decomposition_finishes += 1
                prompt = (
                    "多问题证据尚未覆盖完毕。请只针对下一个子问题调用一次 "
                    f"search_rules：[{next_question.id}] "
                    f"{decomposition_search_query(next_question)}。"
                    "不要回答，也不要并行调用工具。"
                )
                for tool_call in requested_calls:
                    conversation.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": prompt,
                        }
                    )
                _log_tool_step(
                    request_id=request_id,
                    model=model,
                    library=library,
                    decision_index=decision_index,
                    requested_calls=len(requested_calls),
                    executed_tool="finish_answer_deferred_for_decomposition",
                    budget=budget,
                    stop_reason=None,
                )
                continue
            missing_topic_prompt = (
                EvidenceBudgetPolicy.missing_topic_prompt(
                    intent,
                    latest_user_message,
                    set(budget.topic_documents),
                )
                if enable_dynamic_evidence_budget
                and budget.searches < budget.max_searches
                and controller.deferred_finishes < 2
                else None
            )
            if missing_topic_prompt:
                controller.deferred_finishes += 1
                for tool_call in requested_calls:
                    conversation.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": missing_topic_prompt,
                        }
                    )
                _log_tool_step(
                    request_id=request_id,
                    model=model,
                    library=library,
                    decision_index=decision_index,
                    requested_calls=len(requested_calls),
                    executed_tool="finish_answer_deferred",
                    budget=budget,
                    stop_reason=None,
                )
                continue
            for tool_call in requested_calls:
                conversation.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": (
                            "证据收集结束。现在生成带引用的最终回答。"
                            if tool_call.name == "finish_answer"
                            else "本轮已请求结束回答，因此未执行这个额外工具调用。"
                        ),
                    }
                )
            _log_tool_step(
                request_id=request_id,
                model=model,
                library=library,
                decision_index=decision_index,
                requested_calls=len(requested_calls),
                executed_tool="finish_answer",
                budget=budget,
                stop_reason="model_finish",
            )
            if not citations.by_document_id and budget.searches:
                async for event in _finish_after_controller_stop(
                    gateway=gateway,
                    conversation=conversation,
                    citations=citations,
                    library=library,
                    stop_reason="model_finished_without_evidence",
                    model=model,
                    budget=budget,
                    controller=controller,
                    request_id=request_id,
                    decision_index=decision_index,
                ):
                    yield event
                _emit_turn_metrics("model_finished_without_evidence", final_answer_tokens[0])
                return
            async for event in _stream_final_answer(gateway, conversation, citations, timer, final_answer_tokens):
                yield event
            _emit_turn_metrics("model_finish", final_answer_tokens[0])
            return

        tool_call = requested_calls[0]
        tool_started = time.monotonic()
        execution = _execute_tool(
            name=tool_call.name,
            arguments=tool_call.arguments,
            library=library,
            budget=budget,
            citations=citations,
            controller=controller,
        )
        if tool_call.name == "search_rules":
            timer.retrieval_seconds += time.monotonic() - tool_started
        elif tool_call.name == "read_rules":
            timer.read_seconds += time.monotonic() - tool_started
        yield {"type": "status", "status": execution.status}
        conversation.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": execution.content,
            }
        )
        for skipped_call in requested_calls[1:]:
            conversation.append(
                {
                    "role": "tool",
                    "tool_call_id": skipped_call.id,
                    "content": (
                        "服务端每轮只执行一个工具调用；本调用已跳过。"
                        "请根据已返回的结果决定下一步，不要并行调用工具。"
                    ),
                }
            )

        _log_tool_step(
            request_id=request_id,
            model=model,
            library=library,
            decision_index=decision_index,
            requested_calls=len(requested_calls),
            executed_tool=tool_call.name,
            budget=budget,
            stop_reason=execution.stop_reason,
            query_hash=execution.query_hash,
            new_documents=execution.new_documents,
        )
        if execution.stop_reason:
            async for event in _finish_after_controller_stop(
                gateway=gateway,
                conversation=conversation,
                citations=citations,
                library=library,
                stop_reason=execution.stop_reason,
                model=model,
                budget=budget,
                controller=controller,
                request_id=request_id,
                decision_index=decision_index,
            ):
                yield event
            _emit_turn_metrics(execution.stop_reason, final_answer_tokens[0])
            return
    _log_tool_step(
        request_id=request_id,
        model=model,
        library=library,
        decision_index=10,
        requested_calls=0,
        executed_tool="none",
        budget=budget,
        stop_reason="decision_limit",
    )
    async for event in _finish_after_controller_stop(
        gateway=gateway,
        conversation=conversation,
        citations=citations,
        library=library,
        stop_reason="decision_limit",
        model=model,
        budget=budget,
        controller=controller,
        request_id=request_id,
        decision_index=10,
    ):
        yield event
    _emit_turn_metrics("decision_limit", final_answer_tokens[0])


async def _stream_final_answer(
    gateway: ModelGateway,
    conversation: list[dict[str, Any]],
    citations: CitationRegistry,
    timer: TurnPhaseTimer | None = None,
    final_answer_tokens: list[int] | None = None,
    final_guidance: str = "",
    answer_validator: Callable[[str], tuple[ValidationIssue, ...]] | None = None,
    validation_issue_codes: list[str] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    answer_conversation = _final_answer_conversation(
        conversation,
        citations,
        final_guidance=final_guidance,
    )
    yield {"type": "status", "status": "answering"}
    last_issue = "empty"
    final_answer_tokens_estimate = 0
    defer_until_validated = answer_validator is not None
    for attempt in range(2):
        answer_prompt_tokens = estimate_message_tokens(answer_conversation)
        answer_started = time.monotonic()
        answer_parts: list[str] = []
        guarded_parts: list[str] = []
        answer_exposed = False
        detected_issue = ""
        fact_issues: tuple[ValidationIssue, ...] = ()
        async for delta in gateway.stream_answer(answer_conversation):
            answer_parts.append(delta)
            if defer_until_validated:
                continue
            if answer_exposed:
                yield {"type": "text_delta", "delta": delta}
                continue
            guarded_parts.append(delta)
            guarded_content = "".join(guarded_parts)
            detected_issue = _answer_quality_issue(guarded_content) or ""
            if detected_issue:
                break
            if _answer_stream_guard_ready(guarded_content):
                answer_exposed = True
                for guarded_delta in guarded_parts:
                    yield {"type": "text_delta", "delta": guarded_delta}
                guarded_parts.clear()
        if timer is not None:
            timer.final_generation_seconds += time.monotonic() - answer_started
        content = "".join(answer_parts)
        if timer is not None:
            take_usage = getattr(gateway, "take_stream_usage", None)
            provider_usage = take_usage() if callable(take_usage) else None
            timer.usage.add(
                prompt_tokens=provider_usage.prompt_tokens if provider_usage else None,
                completion_tokens=provider_usage.completion_tokens if provider_usage else None,
                estimated_prompt_tokens=answer_prompt_tokens,
                estimated_completion_tokens=estimate_tokens(content),
            )
        final_answer_tokens_estimate = max(final_answer_tokens_estimate, estimate_tokens(content))
        last_issue = detected_issue or _answer_quality_issue(content) or ""
        if not last_issue and answer_validator is not None:
            fact_issues = tuple(
                issue
                for issue in answer_validator(content)
                if issue.severity is ValidationSeverity.ERROR
            )
            if fact_issues:
                last_issue = "fact_validation"
                if validation_issue_codes is not None:
                    validation_issue_codes[:] = list(
                        dict.fromkeys(issue.code for issue in fact_issues)
                    )
        if not last_issue:
            if defer_until_validated:
                for index in range(0, len(content), 24):
                    yield {"type": "text_delta", "delta": content[index : index + 24]}
            elif not answer_exposed:
                for delta in guarded_parts:
                    yield {"type": "text_delta", "delta": delta}
            suffix = _missing_citation_suffix(content, citations.labels())
            if suffix:
                yield {"type": "text_delta", "delta": suffix}
            yield {"type": "sources", "sources": citations.public()}
            yield {"type": "done", "finalAnswerTokens": final_answer_tokens_estimate}
            if final_answer_tokens is not None:
                final_answer_tokens[0] = final_answer_tokens_estimate
            return
        if answer_exposed:
            # A provider envelope detected after the guarded preamble cannot be
            # retracted from an SSE stream. Do not attach rule sources to it;
            # end with an explicit safe failure instead of silently retrying.
            logger.warning("answer_quality_late_issue issue=%s", last_issue)
            yield {
                "type": "text_delta",
                "delta": "\n\n" + _answer_quality_failure(last_issue),
            }
            yield {"type": "sources", "sources": []}
            yield {"type": "done", "finalAnswerTokens": final_answer_tokens_estimate}
            if final_answer_tokens is not None:
                final_answer_tokens[0] = final_answer_tokens_estimate
            return
        logger.warning(
            "answer_quality_rejected issue=%s attempt=%s",
            last_issue,
            attempt + 1,
        )
        if attempt == 0:
            recovery = _answer_recovery_instruction(last_issue)
            if fact_issues:
                recovery += "\n服务器事实校验失败：\n- " + "\n- ".join(
                    issue.message for issue in fact_issues
                )
            answer_conversation = [
                *answer_conversation,
                {
                    "role": "system",
                    "content": recovery,
                },
            ]

    yield {"type": "text_delta", "delta": _answer_quality_failure(last_issue)}
    # Rejected or incomplete provider output is not a rule conclusion. Never
    # attach the evidence registry to it, even if retrieval itself succeeded.
    yield {"type": "sources", "sources": []}
    yield {"type": "done"}


def _answer_stream_guard_ready(content: str) -> bool:
    normalized = content.strip()
    return len(normalized) >= _ANSWER_STREAM_GUARD_CHARACTERS or (
        len(normalized) >= 32 and "\n\n" in normalized
    )


async def _finish_after_controller_stop(
    *,
    gateway: ModelGateway,
    conversation: list[dict[str, Any]],
    citations: CitationRegistry,
    library: Library,
    stop_reason: str,
    model: ModelConfig,
    budget: EvidenceBudget,
    controller: ToolLoopController,
    request_id: str | None,
    decision_index: int,
) -> AsyncIterator[dict[str, Any]]:
    priority_candidate_ids: list[str] = []
    # A compound route is completed deterministically and serially when the
    # model stops early.  Each search/read pair stays inside the shared hard
    # limits and is attributed to its own subquestion allocation.
    for question in tuple(controller.missing_subquestions()):
        if budget.searches >= budget.max_searches:
            break
        controller.searches_without_read = 0
        decomposition_search = _execute_tool(
            name="search_rules",
            arguments=json.dumps(
                {"query": decomposition_search_query(question), "limit": 10},
                ensure_ascii=False,
            ),
            library=library,
            budget=budget,
            citations=citations,
            controller=controller,
        )
        yield {"type": "status", "status": decomposition_search.status}
        _log_tool_step(
            request_id=request_id,
            model=model,
            library=library,
            decision_index=decision_index,
            requested_calls=0,
            executed_tool="search_rules_decomposition_recovery",
            budget=budget,
            stop_reason=decomposition_search.stop_reason,
            query_hash=decomposition_search.query_hash,
        )
        remaining_documents = max(
            0,
            controller.max_answer_documents - budget.documents,
        )
        candidate_ids = [
            document_id
            for document_id in controller.latest_result_ids
            if document_id not in citations.by_document_id
        ]
        if not candidate_ids or not remaining_documents:
            continue
        decomposition_read = _execute_tool(
            name="read_rules",
            arguments=json.dumps(
                {"ids": candidate_ids[: min(4, remaining_documents)]},
                ensure_ascii=False,
            ),
            library=library,
            budget=budget,
            citations=citations,
            controller=controller,
        )
        yield {"type": "status", "status": decomposition_read.status}
        _log_tool_step(
            request_id=request_id,
            model=model,
            library=library,
            decision_index=decision_index,
            requested_calls=0,
            executed_tool="read_rules_decomposition_recovery",
            budget=budget,
            stop_reason=decomposition_read.stop_reason,
            new_documents=decomposition_read.new_documents,
        )
    if (
        not citations.by_document_id
        and stop_reason
        in {"repeated_documents", "searches_without_read", "repeated_results"}
    ):
        # This is a server-controlled recovery query, so it is allowed after the
        # model's two-search-without-read ceiling.
        controller.searches_without_read = 0
        fallback_search = _execute_tool(
            name="search_rules",
            arguments=json.dumps(
                {"query": _latest_user_content(conversation), "limit": 10},
                ensure_ascii=False,
            ),
            library=library,
            budget=budget,
            citations=citations,
            controller=controller,
        )
        yield {"type": "status", "status": fallback_search.status}
        _log_tool_step(
            request_id=request_id,
            model=model,
            library=library,
            decision_index=decision_index,
            requested_calls=0,
            executed_tool="search_rules_fallback",
            budget=budget,
            stop_reason=fallback_search.stop_reason,
            query_hash=fallback_search.query_hash,
        )
    missing_topic_queries = (
        EvidenceBudgetPolicy.missing_topic_queries(
            controller.intent,
            controller.latest_user_message,
            set(budget.topic_documents),
        )
        if controller.evidence_profile is not None
        and controller.evidence_profile.policy_version > 0
        else ()
    )
    recovery_query = (
        missing_topic_queries[0]
        if missing_topic_queries
        else (
            controller.planned_queries[0]
            if (
                controller.intent == QueryIntent.BUILD_ADVICE
                or controller.is_follow_up
            )
            and stop_reason
            in {
                "searches_without_read",
                "repeated_documents",
                "repeated_results",
                "evidence_saturation",
            }
            and controller.planned_queries
            else None
        )
    )
    if recovery_query and budget.searches < budget.max_searches:
        controller.searches_without_read = 0
        topic_search = _execute_tool(
            name="search_rules",
            arguments=json.dumps(
                {"query": recovery_query, "limit": 10},
                ensure_ascii=False,
            ),
            library=library,
            budget=budget,
            citations=citations,
            controller=controller,
        )
        yield {"type": "status", "status": topic_search.status}
        _log_tool_step(
            request_id=request_id,
            model=model,
            library=library,
            decision_index=decision_index,
            requested_calls=0,
            executed_tool="search_rules_topic_fallback",
            budget=budget,
            stop_reason=topic_search.stop_reason,
            query_hash=topic_search.query_hash,
        )
        priority_candidate_ids = list(controller.latest_result_ids)
    dynamic_multi_topic_recovery = (
        (
            bool(controller.decomposition.questions)
            or (
                controller.evidence_profile is not None
                and controller.evidence_profile.policy_version > 0
                and (
                    len(controller.evidence_profile.topic_allocations) > 1
                    or controller.is_follow_up
                )
            )
        )
        and stop_reason
        in {
            "searches_without_read",
            "repeated_documents",
            "repeated_results",
            "evidence_saturation",
        }
    )
    unread_candidate_ids = (
        controller.unread_candidates(set(citations.by_document_id))
        if dynamic_multi_topic_recovery
        else [
            document_id
            for document_id in controller.latest_result_ids
            if document_id not in citations.by_document_id
        ]
    )
    if priority_candidate_ids:
        unread_candidate_ids = list(
            dict.fromkeys(
                [
                    document_id
                    for document_id in priority_candidate_ids
                    if document_id not in citations.by_document_id
                ]
                + unread_candidate_ids
            )
        )
    should_read_candidates = unread_candidate_ids and (
        (
            not citations.by_document_id
            and stop_reason
            in {
                "searches_without_read",
                "repeated_results",
                "model_skipped_tools",
                "model_finished_without_evidence",
                "model_stopped_without_evidence",
            }
        )
        or stop_reason in {"evidence_saturation", "repeated_documents"}
        or dynamic_multi_topic_recovery
    )
    remaining_documents = max(
        0,
        controller.max_answer_documents - budget.documents,
    )
    if should_read_candidates and remaining_documents:
        fallback = _execute_tool(
            name="read_rules",
            arguments=json.dumps(
                {"ids": unread_candidate_ids[: min(4, remaining_documents)]}
            ),
            library=library,
            budget=budget,
            citations=citations,
            controller=controller,
        )
        yield {"type": "status", "status": fallback.status}
        _log_tool_step(
            request_id=request_id,
            model=model,
            library=library,
            decision_index=decision_index,
            requested_calls=0,
            executed_tool="read_rules_fallback",
            budget=budget,
            stop_reason=fallback.stop_reason,
            new_documents=fallback.new_documents,
        )
    if not citations.by_document_id:
        yield {"type": "status", "status": "answering"}
        reason_message = {
            "no_results": "当前绑定的规则库没有找到匹配结果。请尝试使用更具体的规则术语。",
            "evidence_budget": "已命中候选规则，但相关文档超过本轮证据预算，未能注册可引用证据。请缩小问题范围。",
            "read_failed": "已命中候选规则，但当前候选无法读取为完整证据。请尝试更具体的条目或规则术语。",
        }.get(
            stop_reason,
            "本轮检索没有找到足够可靠的可引用依据。",
        )
        yield {
            "type": "text_delta",
            "delta": f"在当前绑定的「{library.manifest.name}」规则库中，{reason_message}",
        }
        yield {"type": "sources", "sources": []}
        yield {"type": "done"}
        return

    conversation.append(
        {
            "role": "system",
            "content": (
                "服务端已停止继续检索，原因是工具调用没有继续产生可靠的新证据"
                f"（内部原因：{stop_reason}）。只能根据已经读取并注册的来源回答；"
                "未覆盖的部分必须明确说明证据不足，不得继续调用工具或使用模型记忆补全。"
            ),
        }
    )
    async for event in _stream_final_answer(gateway, conversation, citations):
        yield event


def _final_answer_conversation(
    conversation: list[dict[str, Any]],
    citations: CitationRegistry,
    final_guidance: str = "",
) -> list[dict[str, str]]:
    if not citations.by_document_id:
        return conversation
    question = _latest_user_content(conversation)
    state = ConversationState.from_messages(
        [
            {"role": str(message.get("role", "")), "content": str(message.get("content", ""))}
            for message in conversation
            if message.get("role") in {"user", "assistant"}
        ]
    )
    evidence = "\n\n".join(
        (
            f"[{label}] {document['title']}\n"
            f"路径：{document['fullPath']}\n"
            f"正文：{document['content']}"
        )
        for label, document in citations.by_document_id.values()
    )
    build_guidance = ""
    if classify_intent(question) == QueryIntent.BUILD_ADVICE:
        build_guidance = (
            "本题是构筑问题，必须按以下顺序回答：\n"
            "1. 当前已知构筑\n"
            "2. 规则事实（每条只写证据支持的规则原文）\n"
            "3. 构筑建议（明确标注这是基于规则的推导）\n"
            "4. 收益与损失\n"
            "5. 适用条件\n"
            "6. 缺失信息\n"
            "7. 来源\n"
            "不得把模型经验或主观推荐写成规则事实；证据未覆盖的内容必须标为缺失信息。\n"
        )
    return [
        {
            "role": "system",
            "content": (
                "你是最终答案生成器，检索阶段已经结束，工具不可用。"
                "现在直接回答用户最后的问题，不要描述搜索、读取、工具调用或后续计划，"
                "也不要说‘让我继续搜索’。只能使用下方证据，规则结论必须标注对应的"
                "[S1]、[S2] 等脚注；证据没有覆盖的部分明确说证据不足。"
                "用户已选择的不同字段默认只是并列状态；除非规则原文明示约束或对应"
                "关系，不得声称一个字段会限定另一个字段。尤其不得根据表格位置、"
                "出现顺序或名称相似自行推断对应关系。\n"
                f"{build_guidance}"
            ),
        },
        {
            "role": "user",
            "content": (
                f"问题：{question}\n\n"
                f"用户已明确声明的会话状态：{state.prompt_context() or '无'}\n"
                "该状态需要用规则证据验证，不能覆盖规则原文；状态字段之间互不构成"
                "约束，除非证据明确说明。\n"
                f"本题字段解释：{state.answer_guidance(question) or '无'}\n\n"
                + (f"受限任务执行结果：\n{final_guidance}\n\n" if final_guidance else "")
                + f"已读取证据：\n{evidence}"
            ),
        },
    ]


def _latest_user_content(conversation: list[dict[str, Any]]) -> str:
    return next(
        (
            str(message.get("content", ""))
            for message in reversed(conversation)
            if message.get("role") == "user"
        ),
        "",
    )


def _system_prompt(
    library: Library,
    state: ConversationState | None = None,
    latest_user_message: str = "",
    route_decision: RouteDecision | None = None,
    decomposition: QueryDecomposition | None = None,
) -> str:
    manifest = library.manifest
    identity = {
        "id": manifest.id,
        "name": manifest.name,
        "system": manifest.system,
        "edition": manifest.edition,
        "revision": manifest.revision,
    }
    state_context = (state or ConversationState()).prompt_context()
    query_plan = build_query_plan(latest_user_message, state)
    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        "当前绑定规则库（这些字段仅用于标识规则范围）：\n"
        f"{json.dumps(identity, ensure_ascii=False, indent=2)}"
        + (
            "\n\n当前用户明确声明的会话状态（不是规则事实，必须用当前规则库验证）：\n"
            f"{state_context}"
            if state_context
            else ""
        )
        + "\n\n本轮问题意图和检索计划（仅用于拆分搜索，不是规则证据）：\n"
        + json.dumps(
            {
                "intent": query_plan.intent.value,
                "queries": list(query_plan.queries),
            },
            ensure_ascii=False,
        )
    )
    if route_decision is not None:
        prompt += (
            "\n\n受限路由与多问题拆解（纯代码生成，不是规则证据）：\n"
            + json.dumps(
                {
                    "routeDecision": route_decision.public(),
                    "decomposition": (decomposition or QueryDecomposition()).public(),
                },
                ensure_ascii=False,
            )
            + "\n若存在子问题，必须按列表顺序串行检索；每次只处理一个子问题，"
            "所有规则结论仍须来自 read_rules 返回的证据。"
        )
    return prompt


def _execute_tool(
    *,
    name: str,
    arguments: str,
    library: Library,
    budget: EvidenceBudget,
    citations: CitationRegistry,
    controller: ToolLoopController,
) -> ToolExecution:
    try:
        params = json.loads(arguments)
        if not isinstance(params, dict):
            raise ValueError("工具参数必须是 JSON 对象")
    except (json.JSONDecodeError, ValueError) as error:
        stop_reason = controller.after_invalid_tool_call()
        return ToolExecution(
            content=f"工具参数不是有效 JSON：{error}",
            status="thinking",
            stop_reason=stop_reason,
        )
    if name == "search_rules":
        query = controller.enrich_query(str(params.get("query", "")))
        query_hash = _query_hash(query)
        try:
            limit = max(1, min(int(params.get("limit", 10)), 20))
        except (TypeError, ValueError):
            stop_reason = controller.after_invalid_tool_call()
            return ToolExecution(
                content="search_rules 的 limit 必须是整数。",
                status="thinking",
                stop_reason=stop_reason,
                query_hash=query_hash,
            )
        stop_reason = controller.before_search(query)
        if stop_reason:
            return ToolExecution(
                content=json.dumps(
                    {
                        "status": "stopped",
                        "reason": stop_reason,
                        "instruction": "不得继续搜索；请根据已有证据回答或说明当前库无依据。",
                    },
                    ensure_ascii=False,
                ),
                status="searching",
                stop_reason=stop_reason,
                query_hash=query_hash,
            )
        try:
            budget.consume_search()
        except ValueError:
            return ToolExecution(
                content='{"status":"stopped","reason":"search_budget"}',
                status="searching",
                stop_reason="search_budget",
                query_hash=query_hash,
            )
        hits = library.search(query, limit)
        stop_reason = controller.after_search(query, hits)
        controller.record_existing_document_ids(
            [
                str(hit.get("id", ""))
                for hit in hits
                if str(hit.get("id", "")) in citations.by_document_id
            ]
        )
        if (
            stop_reason is None
            and citations.by_document_id
            and budget.searches >= controller.max_searches_with_evidence
        ):
            stop_reason = "evidence_saturation"
        return ToolExecution(
            content=json.dumps(hits, ensure_ascii=False),
            status="searching",
            stop_reason=stop_reason,
            query_hash=query_hash,
        )
    if name == "read_rules":
        raw_ids = params.get("ids")
        if not isinstance(raw_ids, list) or not raw_ids:
            stop_reason = controller.after_invalid_tool_call()
            return ToolExecution(
                content="read_rules 的 ids 必须是非空数组。",
                status="thinking",
                stop_reason=stop_reason,
            )
        remaining_documents = max(
            0,
            controller.max_answer_documents - budget.documents,
        )
        if not remaining_documents:
            return ToolExecution(
                content='{"status":"stopped","reason":"answer_document_limit"}',
                status="reading",
                stop_reason="answer_document_limit",
            )
        ids = [str(value) for value in raw_ids[:remaining_documents]]
        documents = library.read(list(dict.fromkeys(ids)))
        controller.record_existing_document_ids(
            [
                document_id
                for document_id in ids
                if document_id in citations.by_document_id
            ]
        )
        new_documents = [
            document
            for document in documents
            if str(document.get("id", "")) not in citations.by_document_id
        ]
        if not new_documents:
            return ToolExecution(
                content=json.dumps(
                    {
                        "status": "stopped",
                        "reason": "repeated_documents",
                        "instruction": "请求的章节均已读取；不得重复读取。",
                    },
                    ensure_ascii=False,
                ),
                status="reading",
                stop_reason="repeated_documents",
            )
        admitted_documents = budget.consume_documents(
            new_documents,
            controller.document_topics,
        )
        if not admitted_documents:
            reason = "evidence_budget" if budget.last_skipped_reasons else "read_failed"
            return ToolExecution(
                content=json.dumps(
                    {
                        "status": "stopped",
                        "reason": reason,
                        "skippedDocuments": len(new_documents),
                    },
                    ensure_ascii=False,
                ),
                status="reading",
                stop_reason=reason,
            )
        result = []
        for document in admitted_documents:
            label = citations.register(document)
            result.append(
                {
                    "citation": label,
                    "id": document["id"],
                    "title": document["title"],
                    "fullPath": document["fullPath"],
                    "content": document["content"],
                }
            )
        controller.record_admitted_documents(admitted_documents)
        controller.after_read()
        if budget.last_skipped_reasons:
            result.insert(
                0,
                {
                    "status": "partial",
                    "skippedDocuments": len(new_documents) - len(admitted_documents),
                    "skippedReasons": budget.last_skipped_reasons,
                    "instruction": "只能根据已注册的文档回答；被跳过文档不是证据。",
                },
            )
        return ToolExecution(
            content=json.dumps(result, ensure_ascii=False),
            status="reading",
            new_documents=len(admitted_documents),
        )
    stop_reason = controller.after_invalid_tool_call()
    return ToolExecution(
        content=f"未知工具：{name}",
        status="thinking",
        stop_reason=stop_reason,
    )


def _normalize_query(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _longest_common_substring_length(left: str, right: str) -> int:
    if not left or not right:
        return 0
    previous = [0] * (len(right) + 1)
    longest = 0
    for left_character in left:
        current = [0]
        for index, right_character in enumerate(right, start=1):
            length = previous[index - 1] + 1 if left_character == right_character else 0
            current.append(length)
            longest = max(longest, length)
        previous = current
    return longest


def _query_hash(value: str) -> str:
    normalized = _normalize_query(value)
    if not normalized:
        return "empty"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def _log_turn_metrics(
    *,
    request_id: str | None,
    model: ModelConfig,
    library: Library,
    timer: TurnPhaseTimer,
    context: dict[str, Any],
    budget: EvidenceBudget,
    citations: CitationRegistry,
    stop_reason: str | None,
    dropped_messages: int,
    final_answer_tokens: int,
    intent: str,
    query_hashes: list[str],
    usage: UsageTotals,
) -> None:
    evidence_tokens = sum(
        estimate_tokens(str(document.get("content", "")))
        for _label, document in citations.by_document_id.values()
    )
    log_turn_metrics(
        request_id=request_id,
        model_id=model.id,
        library_id=library.manifest.id,
        timer=timer,
        context={
            **context,
            "outputReserveTokens": model.max_output_tokens,
            "finalAnswerTokens": final_answer_tokens,
        },
        search_count=budget.searches,
        read_documents=budget.documents,
        evidence_characters=budget.evidence_characters,
        evidence_tokens=budget.evidence_tokens or evidence_tokens,
        stop_reason=stop_reason,
        dropped_messages=dropped_messages,
        intent=intent,
        query_hashes=query_hashes,
        usage=usage,
    )


def _log_tool_step(
    *,
    request_id: str | None,
    model: ModelConfig,
    library: Library,
    decision_index: int,
    requested_calls: int,
    executed_tool: str,
    budget: EvidenceBudget,
    stop_reason: str | None,
    query_hash: str | None = None,
    new_documents: int = 0,
) -> None:
    logger.info(
        "tool_step request_id=%s model_id=%s library_id=%s decision=%s "
        "requested_calls=%s executed_tool=%s searches=%s documents=%s "
        "evidence_characters=%s new_documents=%s stop_reason=%s query_hash=%s",
        request_id or "-",
        model.id,
        library.manifest.id,
        decision_index,
        requested_calls,
        executed_tool,
        budget.searches,
        budget.documents,
        budget.evidence_characters,
        new_documents,
        stop_reason or "-",
        query_hash or "-",
    )


def _missing_citation_suffix(content: str, labels: list[str]) -> str:
    if not labels or _answer_quality_issue(content):
        return ""
    used = set(re.findall(r"\[(S\d+)]", content))
    if any(label in used for label in labels):
        return ""
    return "\n\n依据：" + "".join(f"[{label}]" for label in labels)


def _answer_quality_issue(content: str) -> str | None:
    normalized = content.strip()
    if not normalized:
        return "empty"
    if any(pattern.search(normalized) for pattern in _PROVIDER_REFUSAL_PATTERNS):
        return "provider_refusal"
    if any(pattern.search(normalized) for pattern in _TRANSIENT_OUTPUT_PATTERNS):
        return "transient_provider_output"
    if any(pattern.search(normalized) for pattern in _UNFINISHED_PROCESS_PATTERNS):
        return "unfinished_process"
    return None


def _answer_recovery_instruction(issue: str) -> str:
    if issue == "provider_refusal":
        return (
            "上一份候选输出是供应商拒绝信息，不能作为回答。当前任务只是根据已提供的"
            "TRPG 规则证据回答普通游戏规则问题；请重新生成简洁、完整的中文答案。"
        )
    if issue == "unfinished_process":
        return (
            "上一份候选输出只是搜索或读取计划。检索已经结束，工具不可用；请立即根据"
            "已提供证据生成完整最终答案，不要描述下一步计划。"
        )
    if issue == "fact_validation":
        return (
            "上一份候选输出与服务器从已读证据提取的 Fact Ledger 冲突。必须修正列出的"
            "等级求和、资格、法术节点或数值问题；不得删除缺失信息或改用模型记忆。"
        )
    return "上一份候选输出不可用；请根据已提供证据重新生成完整的中文最终答案。"


def _answer_quality_failure(issue: str) -> str:
    if issue == "provider_refusal":
        return "所选模型连续拒绝生成本次规则回答，系统没有将拒绝信息作为规则结论或添加虚假引用。"
    if issue == "transient_provider_output":
        return "所选模型服务连续返回临时异常信息，本次没有生成可验证的规则回答，请稍后重试。"
    if issue == "unfinished_process":
        return "所选模型连续返回未完成的检索过程，本次没有生成可验证的最终回答。"
    if issue == "fact_validation":
        return "候选答案连续未通过服务器事实校验，本次没有输出可能错误的构筑结论或附加规则来源。"
    return "所选模型没有生成可验证的规则回答，请重试或更换模型。"
