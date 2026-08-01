from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Protocol

from openai import AsyncOpenAI

from .config import ModelConfig
from .libraries import Library


logger = logging.getLogger("uvicorn.error")


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

    def consume_search(self) -> None:
        if self.searches >= self.max_searches:
            raise ValueError("检索已达到本轮安全上限")
        self.searches += 1

    def consume_documents(self, documents: list[dict[str, Any]]) -> None:
        new_characters = sum(len(str(item.get("content", ""))) for item in documents)
        if self.documents + len(documents) > self.max_documents:
            raise ValueError("读取章节数量已达到本轮安全上限")
        if self.evidence_characters + new_characters > self.max_evidence_characters:
            raise ValueError("证据文本已达到本轮上下文预算")
        self.documents += len(documents)
        self.evidence_characters += new_characters


@dataclass
class ToolLoopController:
    max_searches_without_read: int = 2
    seen_queries: set[str] = field(default_factory=set)
    seen_result_ids: set[str] = field(default_factory=set)
    searches_without_read: int = 0
    invalid_tool_calls: int = 0

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

    def after_search(self, hits: list[dict[str, Any]]) -> str | None:
        self.invalid_tool_calls = 0
        self.searches_without_read += 1
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
    conversation: list[dict[str, Any]] = [
        {"role": "system", "content": _system_prompt(library)}
    ]
    trimmed, dropped_count = _trim_messages(messages, model.context_window)
    conversation.extend(trimmed)
    budget = EvidenceBudget()
    citations = CitationRegistry()
    controller = ToolLoopController()

    if dropped_count:
        yield {"type": "context_truncated", "droppedMessages": dropped_count}
    yield {"type": "status", "status": "thinking"}
    for decision_index in range(1, 11):
        decision = await gateway.decide(conversation, TOOLS)
        conversation.append(decision.assistant_message)
        if not decision.tool_calls:
            # Compatibility fallback for models that do not follow finish_answer.
            if not citations.by_document_id:
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
                    ):
                        yield event
                    return
                raise RuntimeError("模型未读取规则证据")
            if decision.content:
                yield {"type": "text_delta", "delta": decision.content}
                suffix = _missing_citation_suffix(decision.content, citations.labels())
                if suffix:
                    yield {"type": "text_delta", "delta": suffix}
            yield {"type": "sources", "sources": citations.public()}
            yield {"type": "done"}
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
                ):
                    yield event
                return
            async for event in _stream_final_answer(gateway, conversation, citations):
                yield event
            return

        tool_call = requested_calls[0]
        execution = _execute_tool(
            name=tool_call.name,
            arguments=tool_call.arguments,
            library=library,
            budget=budget,
            citations=citations,
            controller=controller,
        )
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
            ):
                yield event
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
    ):
        yield event


async def _stream_final_answer(
    gateway: ModelGateway,
    conversation: list[dict[str, Any]],
    citations: CitationRegistry,
) -> AsyncIterator[dict[str, Any]]:
    yield {"type": "status", "status": "answering"}
    answer_parts: list[str] = []
    async for delta in gateway.stream_answer(conversation):
        answer_parts.append(delta)
        yield {"type": "text_delta", "delta": delta}
    suffix = _missing_citation_suffix("".join(answer_parts), citations.labels())
    if suffix:
        yield {"type": "text_delta", "delta": suffix}
    yield {"type": "sources", "sources": citations.public()}
    yield {"type": "done"}


async def _finish_after_controller_stop(
    *,
    gateway: ModelGateway,
    conversation: list[dict[str, Any]],
    citations: CitationRegistry,
    library: Library,
    stop_reason: str,
) -> AsyncIterator[dict[str, Any]]:
    if not citations.by_document_id:
        yield {"type": "status", "status": "answering"}
        yield {
            "type": "text_delta",
            "delta": (
                f"在当前绑定的「{library.manifest.name}」规则库中，"
                "本轮检索没有找到足够可靠的可引用依据。"
                "这个问题可能属于其他规则库，或需要更具体的规则术语。"
            ),
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


def _system_prompt(library: Library) -> str:
    manifest = library.manifest
    identity = {
        "id": manifest.id,
        "name": manifest.name,
        "system": manifest.system,
        "edition": manifest.edition,
        "revision": manifest.revision,
    }
    return (
        f"{SYSTEM_PROMPT}\n\n"
        "当前绑定规则库（这些字段仅用于标识规则范围）：\n"
        f"{json.dumps(identity, ensure_ascii=False, indent=2)}"
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
        query = str(params.get("query", ""))
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
        ids = [str(value) for value in raw_ids]
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
        try:
            budget.consume_documents(new_documents)
        except ValueError:
            return ToolExecution(
                content='{"status":"stopped","reason":"evidence_budget"}',
                status="reading",
                stop_reason="evidence_budget",
            )
        result = []
        for document in new_documents:
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
        return ToolExecution(
            content=json.dumps(result, ensure_ascii=False),
            status="reading",
            new_documents=len(new_documents),
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
    if not labels:
        return ""
    used = set(re.findall(r"\[(S\d+)]", content))
    if any(label in used for label in labels):
        return ""
    return "\n\n依据：" + "".join(f"[{label}]" for label in labels)
