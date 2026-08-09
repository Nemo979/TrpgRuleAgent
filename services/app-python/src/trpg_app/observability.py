"""V2.2 Stage 1B: read-only per-turn aggregate metrics.

Records aggregated token/context/latency numbers for each chat turn
without logging any raw question, answer, rule text, summary or key.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

logger = logging.getLogger("uvicorn.error")

_CJK_BOUNDARY = 0x2E80
_TOKEN_PER_CHARACTER = 2


def estimate_tokens(value: str) -> int:
    """Rough token estimate for CJK-heavy rule text.

    CJK text is typically ~1-2 tokens per character in modern tokenizers,
    so 2 characters per token is a reasonable mid-range approximation for
    observability logs.  It is an estimate only and must not gate context
    budgets on its own.
    """
    if not value:
        return 0
    characters = sum(
        1
        for character in value
        if ord(character) >= _CJK_BOUNDARY or not character.isspace()
    )
    return max(1, characters // _TOKEN_PER_CHARACTER)


def summarize_messages(messages: Sequence[dict[str, Any]]) -> dict[str, int]:
    """Aggregate token estimate and counts by role (no content retained)."""
    tokens = 0
    counts: dict[str, int] = {}
    for message in messages:
        role = str(message.get("role", "unknown"))
        counts[role] = counts.get(role, 0) + 1
        tokens += estimate_tokens(str(message.get("content", "")))
    return {"tokens": tokens, "messageCounts": counts}


@dataclass
class TurnPhaseTimer:
    """Elapsed seconds for each observable phase of a turn."""

    started_at: float = field(default_factory=time.monotonic)
    decision_seconds: float = 0.0
    retrieval_seconds: float = 0.0
    read_seconds: float = 0.0
    final_generation_seconds: float = 0.0

    @property
    def total_seconds(self) -> float:
        return round(time.monotonic() - self.started_at, 4)

    def round_all(self) -> dict[str, float]:
        return {
            "decision": round(self.decision_seconds, 4),
            "retrieval": round(self.retrieval_seconds, 4),
            "read": round(self.read_seconds, 4),
            "finalGeneration": round(self.final_generation_seconds, 4),
            "total": self.total_seconds,
        }


def log_turn_metrics(
    *,
    request_id: str | None,
    model_id: str,
    library_id: str,
    timer: TurnPhaseTimer,
    context: dict[str, Any],
    search_count: int,
    read_documents: int,
    evidence_characters: int,
    evidence_tokens: int,
    stop_reason: str | None,
    dropped_messages: int,
) -> None:
    """One JSON line per turn, aggregating counts and timings only."""
    logger.info(
        "turn_metrics %s",
        json.dumps(
            {
                "request_id": request_id or "-",
                "model_id": model_id,
                "library_id": library_id,
                "phases_seconds": timer.round_all(),
                "context": context,
                "search_count": search_count,
                "read_documents": read_documents,
                "evidence_characters": evidence_characters,
                "evidence_tokens": evidence_tokens,
                "stop_reason": stop_reason or "-",
                "dropped_messages": dropped_messages,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
