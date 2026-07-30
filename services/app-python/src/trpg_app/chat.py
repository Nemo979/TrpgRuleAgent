from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Protocol

from openai import AsyncOpenAI

from .config import ModelConfig
from .libraries import Library


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
9. 证据充分、可以生成最终回答时调用 finish_answer。"""

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
) -> AsyncIterator[dict[str, Any]]:
    gateway = gateway_factory(model)
    conversation: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    trimmed, dropped_count = _trim_messages(messages, model.context_window)
    conversation.extend(trimmed)
    budget = EvidenceBudget()
    citations = CitationRegistry()

    if dropped_count:
        yield {"type": "context_truncated", "droppedMessages": dropped_count}
    yield {"type": "status", "status": "thinking"}
    for _ in range(10):
        decision = await gateway.decide(conversation, TOOLS)
        conversation.append(decision.assistant_message)
        if not decision.tool_calls:
            # Compatibility fallback for models that do not follow finish_answer.
            if not citations.by_document_id:
                raise RuntimeError("模型未读取规则证据")
            if decision.content:
                yield {"type": "text_delta", "delta": decision.content}
                suffix = _missing_citation_suffix(decision.content, citations.labels())
                if suffix:
                    yield {"type": "text_delta", "delta": suffix}
            yield {"type": "sources", "sources": citations.public()}
            yield {"type": "done"}
            return

        should_finish = any(call.name == "finish_answer" for call in decision.tool_calls)
        for tool_call in decision.tool_calls:
            if tool_call.name == "finish_answer":
                conversation.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": "证据收集结束。现在生成带引用的最终回答。",
                    }
                )
                continue
            result = _execute_tool(
                name=tool_call.name,
                arguments=tool_call.arguments,
                library=library,
                budget=budget,
                citations=citations,
            )
            yield {
                "type": "status",
                "status": "searching" if tool_call.name == "search_rules" else "reading",
            }
            conversation.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                }
            )
        if should_finish:
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
            return
    raise RuntimeError("模型工具循环超过安全上限")


def _execute_tool(
    *,
    name: str,
    arguments: str,
    library: Library,
    budget: EvidenceBudget,
    citations: CitationRegistry,
) -> str:
    try:
        params = json.loads(arguments)
    except json.JSONDecodeError as error:
        return f"工具参数不是有效 JSON：{error}"
    if name == "search_rules":
        budget.consume_search()
        hits = library.search(str(params.get("query", "")), int(params.get("limit", 10)))
        return json.dumps(hits, ensure_ascii=False)
    if name == "read_rules":
        ids = [str(value) for value in params.get("ids", [])]
        documents = library.read(list(dict.fromkeys(ids)))
        budget.consume_documents(documents)
        result = []
        for document in documents:
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
        return json.dumps(result, ensure_ascii=False)
    return f"未知工具：{name}"


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
