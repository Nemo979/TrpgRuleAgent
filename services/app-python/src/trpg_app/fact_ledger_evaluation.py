"""Offline Fact Ledger audit for previously generated answer reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .pf1e_fact_adapter import (
    build_pf1e_fact_ledger,
    validate_pf1e_fact_answer,
)


def evaluate_report(
    report: dict[str, Any],
    *,
    expect_rejected: bool = False,
) -> dict[str, Any]:
    models = report.get("models", [])
    if not isinstance(models, list) or not models:
        raise ValueError("answer report requires at least one model")
    rows: list[dict[str, Any]] = []
    for case in models[0].get("cases", []):
        turns = case.get("turns", [])
        if not turns:
            continue
        turn = turns[0]
        sources = []
        for source in turn.get("sources", []):
            label = str(source.get("label", ""))
            metadata = source.get("metadata", {})
            blocks = metadata.get("structuralBlocks", []) if isinstance(metadata, dict) else []
            content = "\n\n".join(
                str(block.get("content", ""))
                for block in blocks
                if isinstance(block, dict) and block.get("content")
            )
            sources.append(
                (
                    label,
                    {
                        "title": str(source.get("title", "")),
                        "content": content,
                    },
                )
            )
        query = str(turn.get("query", ""))
        ledger = build_pf1e_fact_ledger(query, sources)
        issues = validate_pf1e_fact_answer(str(turn.get("answer", "")), ledger)
        rows.append(
            {
                "id": str(case.get("id", "")),
                "ledgerRecordCount": ledger.record_count,
                "passed": not issues,
                "issueCodes": list(dict.fromkeys(issue.code for issue in issues)),
                "issues": [issue.message for issue in issues],
            }
        )
    expectation_passed = sum(
        (not row["passed"]) if expect_rejected else row["passed"]
        for row in rows
    )
    return {
        "schemaVersion": 1,
        "caseCount": len(rows),
        "passedCases": sum(row["passed"] for row in rows),
        "rejectedCases": sum(not row["passed"] for row in rows),
        "ledgerRecordCount": sum(row["ledgerRecordCount"] for row in rows),
        "expectRejected": expect_rejected,
        "expectationPassedCases": expectation_passed,
        "passed": bool(rows) and expectation_passed == len(rows),
        "nextAction": (
            "fact_ledger_migration_gate_complete"
            if rows and expectation_passed == len(rows)
            else "expand_fact_ledger_validation"
        ),
        "cases": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit answers with Fact Ledger")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expect-rejected", action="store_true")
    args = parser.parse_args()
    result = evaluate_report(
        json.loads(args.input.read_text(encoding="utf-8")),
        expect_rejected=args.expect_rejected,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"passed={result['passedCases']}/{result['caseCount']} "
        f"rejected={result['rejectedCases']} "
        f"expected={result['expectationPassedCases']}/{result['caseCount']} "
        f"next={result['nextAction']}"
    )


if __name__ == "__main__":
    main()
