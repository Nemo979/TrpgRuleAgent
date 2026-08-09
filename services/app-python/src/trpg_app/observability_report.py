"""Aggregate and compare privacy-safe V2.2 turn metric JSONL files."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Iterable


_METRICS = {
    "latency.totalSeconds": ("phases_seconds", "total"),
    "latency.decisionSeconds": ("phases_seconds", "decision"),
    "latency.retrievalSeconds": ("phases_seconds", "retrieval"),
    "latency.readSeconds": ("phases_seconds", "read"),
    "latency.finalGenerationSeconds": ("phases_seconds", "finalGeneration"),
    "context.historyTokens": ("context", "historyTokens"),
    "context.originalHistoryTokens": ("context", "originalHistoryTokens"),
    "context.systemTokens": ("context", "systemTokens"),
    "context.stateTokens": ("context", "stateTokens"),
    "context.stateFieldCount": ("context", "stateFieldCount"),
    "context.recentHistoryBudgetTokens": ("context", "recentHistoryBudgetTokens"),
    "context.decisionToolBudgetTokens": ("context", "decisionToolBudgetTokens"),
    "context.evidenceBudgetTokens": ("context", "evidenceBudgetTokens"),
    "context.compactedToolMessages": ("context", "compactedToolMessages"),
    "context.decisionToolTokensBeforeMax": ("context", "decisionToolTokensBeforeMax"),
    "context.decisionToolTokensAfterMax": ("context", "decisionToolTokensAfterMax"),
    "context.outputReserveTokens": ("context", "outputReserveTokens"),
    "context.finalAnswerTokens": ("context", "finalAnswerTokens"),
    "usage.promptTokens": ("usage", "promptTokens"),
    "usage.completionTokens": ("usage", "completionTokens"),
    "usage.totalTokens": ("usage", "totalTokens"),
    "evidence.characters": ("evidence_characters",),
    "evidence.tokens": ("evidence_tokens",),
    "tools.searchCount": ("search_count",),
    "tools.readDocuments": ("read_documents",),
    "context.droppedMessages": ("dropped_messages",),
}


def _value(record: dict[str, Any], path: tuple[str, ...]) -> float | None:
    current: Any = record
    for part in path:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    if isinstance(current, (int, float)) and math.isfinite(float(current)):
        return float(current)
    return None


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _summary(values: Iterable[float]) -> dict[str, float | int]:
    collected = list(values)
    if not collected:
        return {"count": 0, "mean": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0}
    return {
        "count": len(collected),
        "mean": round(sum(collected) / len(collected), 4),
        "p50": round(_percentile(collected, 0.50), 4),
        "p95": round(_percentile(collected, 0.95), 4),
        "max": round(max(collected), 4),
    }


def aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    dimensions: dict[str, dict[str, int]] = {}
    for field in ("model_id", "library_id", "intent", "stop_reason"):
        counts: dict[str, int] = {}
        for record in records:
            key = str(record.get(field, "-"))
            counts[key] = counts.get(key, 0) + 1
        dimensions[field] = dict(sorted(counts.items()))
    reported_calls = sum(int(record.get("usage", {}).get("reportedCalls", 0)) for record in records)
    estimated_calls = sum(int(record.get("usage", {}).get("estimatedCalls", 0)) for record in records)
    truncated_turns = sum(
        1 for record in records if int(record.get("dropped_messages", 0)) > 0
    )
    return {
        "schemaVersion": 1,
        "turnCount": len(records),
        "dimensions": dimensions,
        "usageCoverage": {
            "reportedCalls": reported_calls,
            "estimatedCalls": estimated_calls,
            "reportedRate": round(reported_calls / max(reported_calls + estimated_calls, 1), 4),
        },
        "derivedRates": {
            "contextTruncationRate": round(truncated_turns / max(len(records), 1), 4),
        },
        "metrics": {
            name: _summary(
                value for record in records if (value := _value(record, path)) is not None
            )
            for name, path in _METRICS.items()
        },
    }


def compare(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    comparisons: dict[str, Any] = {}
    for name, current_summary in current.get("metrics", {}).items():
        baseline_summary = baseline.get("metrics", {}).get(name)
        if not isinstance(baseline_summary, dict):
            continue
        comparisons[name] = {
            statistic: round(float(current_summary[statistic]) - float(baseline_summary[statistic]), 4)
            for statistic in ("mean", "p50", "p95", "max")
        }
    return {
        "baselineTurnCount": baseline.get("turnCount", 0),
        "currentTurnCount": current.get("turnCount", 0),
        "metricDeltas": comparisons,
    }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid JSONL at line {line_number}: {error}") from error
            if not isinstance(record, dict):
                raise ValueError(f"metric line {line_number} must be an object")
            records.append(record)
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregate V2.2 turn metrics")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    report = aggregate(load_jsonl(args.input))
    if args.baseline:
        report["comparison"] = compare(report, json.loads(args.baseline.read_text(encoding="utf-8")))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"turns={report['turnCount']} reportedUsageRate={report['usageCoverage']['reportedRate']:.1%}")


if __name__ == "__main__":
    main()
