"""Deterministic Stage 2 routing/decomposition evaluation CLI."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from .query_decomposition import decompose_query, route_query


def load_cases(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict) or not isinstance(value.get("query"), str):
                raise ValueError(f"invalid query decomposition case at line {line_number}")
            rows.append(value)
    return rows


def evaluate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    simple_count = 0
    simple_mis_split = 0
    covered_targets = 0
    total_targets = 0
    latencies: list[float] = []
    for case in cases:
        decision = route_query(str(case["query"]))
        decomposition = decompose_query(str(case["query"]), decision)
        expected_complexity = str(case.get("expectedComplexity", "simple"))
        expected_domains = {str(item) for item in case.get("expectedDomains", [])}
        expected_count = int(case.get("expectedQuestionCount", 0))
        actual_domains = set(decision.domains)
        covered = len(expected_domains & actual_domains)
        total_targets += len(expected_domains)
        covered_targets += covered
        if expected_complexity == "simple":
            simple_count += 1
            if decision.need_decomposition:
                simple_mis_split += 1
        latencies.append(decision.routing_seconds)
        passed = (
            decision.complexity.value == expected_complexity
            and len(decomposition.questions) == expected_count
            and covered == len(expected_domains)
            and len(decomposition.questions) <= 4
        )
        rows.append(
            {
                "id": str(case.get("id", "-")),
                "passed": passed,
                "complexity": decision.complexity.value,
                "reasonCode": decision.reason_code,
                "domains": list(decision.domains),
                "questionCount": len(decomposition.questions),
                "routingMilliseconds": round(decision.routing_seconds * 1000, 4),
            }
        )
    sorted_latencies = sorted(latencies)
    p95_index = max(0, math.ceil(0.95 * len(sorted_latencies)) - 1)
    question_counts = [int(row["questionCount"]) for row in rows]
    return {
        "schemaVersion": 1,
        "caseCount": len(rows),
        "passedCases": sum(1 for row in rows if row["passed"]),
        "simpleMisSplitRate": round(simple_mis_split / max(simple_count, 1), 4),
        "targetCoverageRate": round(covered_targets / max(total_targets, 1), 4),
        "averageSubquestions": round(sum(question_counts) / max(len(question_counts), 1), 4),
        "maxSubquestions": max(question_counts, default=0),
        "routingLatencyMilliseconds": {
            "mean": round(sum(latencies) * 1000 / max(len(latencies), 1), 4),
            "p95": round((sorted_latencies[p95_index] if sorted_latencies else 0.0) * 1000, 4),
        },
        "passed": bool(rows)
        and all(row["passed"] for row in rows)
        and simple_mis_split == 0
        and covered_targets == total_targets,
        "cases": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate deterministic query decomposition")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate(load_cases(args.cases))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"passed={report['passedCases']}/{report['caseCount']} "
        f"simpleMisSplitRate={report['simpleMisSplitRate']:.1%} "
        f"targetCoverageRate={report['targetCoverageRate']:.1%}"
    )
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
