from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Protocol

from openai import AsyncOpenAI

from .config import ModelConfig
from .conversation_state import ConversationState
from .libraries import Library
from .observability import (
    TurnPhaseTimer,
    estimate_tokens,
    log_turn_metrics,
    summarize_messages,
)
from .query_intent import QueryIntent, build_query_plan, classify_intent


logger = logging.getLogger("uvicorn.error")


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
        )

    async def stream_answer(
        self,
        messages: list[dict[str, Any]],
    ) -> AsyncIterator[str]:
        stream = await self.client.chat.completions.create(
            model=self.model.model,
            messages=messages,
            stream=True,
            max_tokens=self.model.max_output_tokens,
            extra_body=self._extra_body(),
        )
        buffered: list[str] = []
        async for chunk in stream:
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

    def _extra_body(self) -> dict[str, Any] | None:
        if not self.model.disable_thinking:
            return None
        return {"chat_template_kwargs": {"enable_thinking": False}}


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
    searches: int = 0
    documents: int = 0
    evidence_characters: int = 0
    skipped_documents: int = 0
    last_skipped_reasons: list[str] = field(default_factory=list)

    def consume_search(self) -> None:
        if self.searches >= self.max_searches:
            raise ValueError("检索已达到本轮安全上限")
        self.searches += 1

    def consume_documents(self, documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Admit readable evidence one document at a time.

        A single oversized document must not discard smaller documents in the
        same read request.  The caller can surface ``last_skipped_reasons`` and
        decide whether an entirely rejected batch should stop the loop.
        """
        accepted: list[dict[str, Any]] = []
        self.last_skipped_reasons = []
        for document in documents:
            if self.documents + len(accepted) >= self.max_documents:
                self.last_skipped_reasons.append("document_limit")
                continue
            characters = len(str(document.get("content", "")))
            if self.evidence_characters + sum(
                len(str(item.get("content", ""))) for item in accepted
            ) + characters > self.max_evidence_characters:
                self.last_skipped_reasons.append("evidence_budget")
                continue
            accepted.append(document)
        self.documents += len(accepted)
        self.evidence_characters += sum(
            len(str(item.get("content", ""))) for item in accepted
        )
        self.skipped_documents += len(documents) - len(accepted)
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
    conversation_state: ConversationState = field(default_factory=ConversationState)
    latest_user_message: str = ""

    def before_search(self, query: str) -> str | None:
        normalized = _normalize_query(query)
        if not normalized:
            return "empty_query"
        if normalized in self.seen_queries:
            return "repeated_query"
        if self.searches_without_read >= self.max_searches_without_read:
            return "searches_without_read"
        self.seen_queries.add(normalized)
        return None

    def enrich_query(self, query: str) -> str:
        return self.conversation_state.enrich_search_query(
            query,
            self.latest_user_message,
        )

    def after_search(self, hits: list[dict[str, Any]]) -> str | None:
        self.invalid_tool_calls = 0
        self.searches_without_read += 1
        self.latest_result_ids = [
            str(hit["id"]) for hit in hits if hit.get("id")
        ]
        result_ids = {str(hit.get("id", "")) for hit in hits if hit.get("id")}
        if not result_ids:
            return "no_results"
        new_ids = result_ids - self.seen_result_ids
        self.seen_result_ids.update(result_ids)
        if not new_ids:
            return "repeated_results"
        return None

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
    conversation: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": _system_prompt(
                library,
                state,
                latest_user_message,
            ),
        }
    ]
    trimmed, dropped_count = _trim_messages(messages, model.context_window)
    conversation.extend(trimmed)
    budget = EvidenceBudget()
    citations = CitationRegistry()
    controller = ToolLoopController(
        conversation_state=state,
        latest_user_message=latest_user_message,
    )
    system_tokens = estimate_tokens(_system_prompt(library, state, latest_user_message))
    history = summarize_messages(messages)
    final_answer_tokens: list[int] = [0]

    if dropped_count:
        yield {"type": "context_truncated", "droppedMessages": dropped_count}
    yield {"type": "status", "status": "thinking"}
    for decision_index in range(1, 11):
        decision_started = time.monotonic()
        decision = await gateway.decide(conversation, TOOLS)
        timer.decision_seconds += time.monotonic() - decision_started
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
                    _log_turn_metrics(
                        request_id=request_id,
                        model=model,
                        library=library,
                        timer=timer,
                        context={"historyTokens": history["tokens"], "systemTokens": system_tokens},
                        budget=budget,
                        citations=citations,
                        stop_reason="model_skipped_tools",
                        dropped_messages=dropped_count,
                        final_answer_tokens=final_answer_tokens[0],
                    )
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
                    _log_turn_metrics(
                        request_id=request_id,
                        model=model,
                        library=library,
                        timer=timer,
                        context={"historyTokens": history["tokens"], "systemTokens": system_tokens},
                        budget=budget,
                        citations=citations,
                        stop_reason="model_stopped_without_evidence",
                        dropped_messages=dropped_count,
                        final_answer_tokens=final_answer_tokens[0],
                    )
                    return
                raise RuntimeError("模型未读取规则证据")
            async for event in _stream_final_answer(gateway, conversation, citations, timer, final_answer_tokens):
                yield event
            _log_turn_metrics(
                request_id=request_id,
                model=model,
                library=library,
                timer=timer,
                context={"historyTokens": history["tokens"], "systemTokens": system_tokens},
                budget=budget,
                citations=citations,
                stop_reason="model_finish",
                dropped_messages=dropped_count,
                final_answer_tokens=final_answer_tokens[0],
            )
            return

        requested_calls = list(decision.tool_calls)
        should_finish = any(call.name == "finish_answer" for call in requested_calls)
        if should_finish:
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
                _log_turn_metrics(
                    request_id=request_id,
                    model=model,
                    library=library,
                    timer=timer,
                    context={"historyTokens": history["tokens"], "systemTokens": system_tokens},
                    budget=budget,
                    citations=citations,
                    stop_reason="model_finished_without_evidence",
                    dropped_messages=dropped_count,
                    final_answer_tokens=final_answer_tokens[0],
                )
                return
            async for event in _stream_final_answer(gateway, conversation, citations, timer, final_answer_tokens):
                yield event
            _log_turn_metrics(
                request_id=request_id,
                model=model,
                library=library,
                timer=timer,
                context={"historyTokens": history["tokens"], "systemTokens": system_tokens},
                budget=budget,
                citations=citations,
                stop_reason="model_finish",
                dropped_messages=dropped_count,
                final_answer_tokens=final_answer_tokens[0],
            )
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
            _log_turn_metrics(
                request_id=request_id,
                model=model,
                library=library,
                timer=timer,
                context={"historyTokens": history["tokens"], "systemTokens": system_tokens},
                budget=budget,
                citations=citations,
                stop_reason=execution.stop_reason,
                dropped_messages=dropped_count,
                final_answer_tokens=final_answer_tokens[0],
            )
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
    _log_turn_metrics(
        request_id=request_id,
        model=model,
        library=library,
        timer=timer,
        context={"historyTokens": history["tokens"], "systemTokens": system_tokens},
        budget=budget,
        citations=citations,
        stop_reason="decision_limit",
        dropped_messages=dropped_count,
        final_answer_tokens=final_answer_tokens[0],
    )


async def _stream_final_answer(
    gateway: ModelGateway,
    conversation: list[dict[str, Any]],
    citations: CitationRegistry,
    timer: TurnPhaseTimer | None = None,
    final_answer_tokens: list[int] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    answer_conversation = _final_answer_conversation(conversation, citations)
    yield {"type": "status", "status": "answering"}
    last_issue = "empty"
    final_answer_tokens_estimate = 0
    for attempt in range(2):
        answer_started = time.monotonic()
        answer_parts = [
            delta async for delta in gateway.stream_answer(answer_conversation)
        ]
        if timer is not None:
            timer.final_generation_seconds += time.monotonic() - answer_started
        content = "".join(answer_parts)
        final_answer_tokens_estimate = max(final_answer_tokens_estimate, estimate_tokens(content))
        last_issue = _answer_quality_issue(content) or ""
        if not last_issue:
            for delta in answer_parts:
                yield {"type": "text_delta", "delta": delta}
            suffix = _missing_citation_suffix(content, citations.labels())
            if suffix:
                yield {"type": "text_delta", "delta": suffix}
            yield {"type": "sources", "sources": citations.public()}
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
            answer_conversation = [
                *answer_conversation,
                {
                    "role": "system",
                    "content": _answer_recovery_instruction(last_issue),
                },
            ]

    yield {"type": "text_delta", "delta": _answer_quality_failure(last_issue)}
    # Rejected or incomplete provider output is not a rule conclusion. Never
    # attach the evidence registry to it, even if retrieval itself succeeded.
    yield {"type": "sources", "sources": []}
    yield {"type": "done"}


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
    unread_candidate_ids = [
        document_id
        for document_id in controller.latest_result_ids
        if document_id not in citations.by_document_id
    ]
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
                f"已读取证据：\n{evidence}"
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
    return (
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
        stop_reason = controller.after_search(hits)
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
        admitted_documents = budget.consume_documents(new_documents)
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
        evidence_tokens=evidence_tokens,
        stop_reason=stop_reason,
        dropped_messages=dropped_messages,
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


def _trim_messages(
    messages: list[dict[str, str]],
    context_window: int,
) -> tuple[list[dict[str, str]], int]:
    # A transparent conservative approximation; the API reports truncation separately.
    character_budget = max(8_000, int(context_window * 2.2))
    kept: list[dict[str, str]] = []
    used = 0
    for message in reversed(messages):
        content = message.get("content", "")
        if used + len(content) > character_budget:
            break
        kept.append({"role": message["role"], "content": content})
        used += len(content)
    values = list(reversed(kept))
    return values, len(messages) - len(values)


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
    return "上一份候选输出不可用；请根据已提供证据重新生成完整的中文最终答案。"


def _answer_quality_failure(issue: str) -> str:
    if issue == "provider_refusal":
        return "所选模型连续拒绝生成本次规则回答，系统没有将拒绝信息作为规则结论或添加虚假引用。"
    if issue == "transient_provider_output":
        return "所选模型服务连续返回临时异常信息，本次没有生成可验证的规则回答，请稍后重试。"
    if issue == "unfinished_process":
        return "所选模型连续返回未完成的检索过程，本次没有生成可验证的最终回答。"
    return "所选模型没有生成可验证的规则回答，请重试或更换模型。"
