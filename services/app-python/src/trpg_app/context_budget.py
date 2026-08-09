"""Stage 3 context allocation without summaries or extra model calls."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Sequence

from .observability import estimate_message_tokens, estimate_tokens


@dataclass
class ContextBudget:
    context_window_tokens: int
    output_reserve_tokens: int
    system_tokens: int
    state_tokens: int
    recent_history_tokens: int
    decision_tool_tokens: int
    evidence_tokens: int
    compacted_tool_messages: int = 0
    decision_tool_tokens_before_max: int = 0
    decision_tool_tokens_after_max: int = 0

    @classmethod
    def allocate(
        cls,
        *,
        context_window_tokens: int,
        output_reserve_tokens: int,
        system_tokens: int,
        state_tokens: int,
    ) -> "ContextBudget":
        output = min(max(1, output_reserve_tokens), max(1, context_window_tokens // 2))
        input_capacity = max(0, context_window_tokens - output)
        allocatable = max(0, input_capacity - system_tokens - state_tokens)
        evidence_target = min(40_000, input_capacity // 3)
        decision_tools = min(48_000, evidence_target + 8_000, allocatable // 2)
        evidence = min(evidence_target, decision_tools)
        recent = allocatable - decision_tools
        return cls(
            context_window_tokens=context_window_tokens,
            output_reserve_tokens=output,
            system_tokens=system_tokens,
            state_tokens=state_tokens,
            recent_history_tokens=recent,
            decision_tool_tokens=decision_tools,
            evidence_tokens=evidence,
        )

    def trim_recent_messages(
        self,
        messages: Sequence[dict[str, str]],
    ) -> tuple[list[dict[str, str]], int]:
        kept: list[dict[str, str]] = []
        used = 0
        for message in reversed(messages):
            normalized = {"role": message["role"], "content": message.get("content", "")}
            tokens = estimate_message_tokens([normalized])
            if kept and used + tokens > self.recent_history_tokens:
                break
            kept.append(normalized)
            used += tokens
        values = list(reversed(kept))
        return values, len(messages) - len(values)

    def prepare_decision_messages(
        self,
        messages: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Show each tool-result batch in full once, then retain a compact receipt."""
        last_assistant = max(
            (index for index, message in enumerate(messages) if message.get("role") == "assistant"),
            default=-1,
        )
        prepared: list[dict[str, Any]] = []
        before = 0
        after = 0
        compacted = 0
        for index, message in enumerate(messages):
            if message.get("role") != "tool":
                prepared.append(dict(message))
                continue
            content = str(message.get("content", ""))
            before += estimate_tokens(content)
            if index <= last_assistant:
                compact_content = _compact_tool_content(content)
                compacted += 1
                prepared.append({**message, "content": compact_content})
                after += estimate_tokens(compact_content)
            else:
                prepared.append(dict(message))
                after += estimate_tokens(content)
        self.compacted_tool_messages = max(self.compacted_tool_messages, compacted)
        self.decision_tool_tokens_before_max = max(self.decision_tool_tokens_before_max, before)
        self.decision_tool_tokens_after_max = max(self.decision_tool_tokens_after_max, after)
        return prepared

    def metrics(self) -> dict[str, int]:
        return {
            "contextWindowTokens": self.context_window_tokens,
            "outputReserveTokens": self.output_reserve_tokens,
            "recentHistoryBudgetTokens": self.recent_history_tokens,
            "decisionToolBudgetTokens": self.decision_tool_tokens,
            "evidenceBudgetTokens": self.evidence_tokens,
            "compactedToolMessages": self.compacted_tool_messages,
            "decisionToolTokensBeforeMax": self.decision_tool_tokens_before_max,
            "decisionToolTokensAfterMax": self.decision_tool_tokens_after_max,
        }


def _compact_tool_content(content: str) -> str:
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        return json.dumps(
            {"status": "compacted_tool_result", "instruction": "完整结果已在上一决策中展示。"},
            ensure_ascii=False,
        )
    items = value if isinstance(value, list) else [value]
    ids: list[str] = []
    citations: list[str] = []
    is_read = False
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("id"):
            ids.append(str(item["id"]))
        if item.get("citation"):
            citations.append(str(item["citation"]))
            is_read = True
        if "content" in item:
            is_read = True
    return json.dumps(
        {
            "status": "compacted_read_evidence" if is_read else "compacted_search_results",
            "resultIds": list(dict.fromkeys(ids)),
            "citations": list(dict.fromkeys(citations)),
            "instruction": (
                "正文已注册，最终回答会重新获得完整已读证据。"
                if is_read
                else "完整候选已在上一决策中展示；仍可按 ID 读取。"
            ),
        },
        ensure_ascii=False,
    )
