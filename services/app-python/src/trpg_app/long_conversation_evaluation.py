"""Deterministic Stage 0 evaluation for long-history and revision contracts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from .answer_evaluation import expand_history
from .context_budget import ContextBudget
from .chat import _select_prior_assistant_artifact
from .conversation_state import ConversationState
from .observability import estimate_tokens, summarize_messages


_MISSING = object()


def load_long_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if (
                not isinstance(value, dict)
                or not value.get("id")
                or not isinstance(value.get("expectedState"), dict)
                or not isinstance(value.get("forbiddenState", []), list)
            ):
                raise ValueError(f"invalid long conversation case at line {line_number}")
            history_value = value.get("history")
            history = expand_history(history_value, line_number)
            expected_turns = (
                history_value.get("turnCount")
                if isinstance(history_value, dict)
                else len(history) // 2
            )
            if (
                not isinstance(expected_turns, int)
                or len(history) % 2
                or len(history) != expected_turns * 2
            ):
                raise ValueError(f"history turn count mismatch at line {line_number}")
            cases.append({**value, "expandedHistory": history})
    if not cases:
        raise ValueError("long conversation evaluation set is empty")
    return cases


def evaluate_long_cases(cases: Sequence[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    expected_total = 0
    expected_correct = 0
    overwrite_total = 0
    overwrite_correct = 0
    forbidden_total = 0
    contamination_count = 0
    revision_correct = 0
    probe_total = 0
    probe_correct = 0
    artifact_probe_total = 0
    artifact_probe_correct = 0
    for case in cases:
        history = list(case["expandedHistory"])
        state = ConversationState.from_messages(history)
        state_value = json.loads(state.prompt_context() or "{}")
        expected = dict(case["expectedState"])
        correct_paths = {
            path
            for path, expected_value in expected.items()
            if _path_value(state_value, path) == expected_value
        }
        expected_total += len(expected)
        expected_correct += len(correct_paths)

        overwrite_paths = [str(path) for path in case.get("overwritePaths", [])]
        overwrite_total += len(overwrite_paths)
        overwrite_correct += sum(path in correct_paths for path in overwrite_paths)

        violations = 0
        for forbidden in case.get("forbiddenState", []):
            if not isinstance(forbidden, dict) or not isinstance(forbidden.get("path"), str):
                raise ValueError(f"invalid forbiddenState in {case['id']}")
            forbidden_total += 1
            actual = _path_value(state_value, forbidden["path"])
            if "value" in forbidden and actual == forbidden["value"]:
                violations += 1
            elif "value" not in forbidden and actual is not _MISSING:
                violations += 1
        contamination_count += violations

        state_tokens = estimate_tokens(state.prompt_context())
        budget = ContextBudget.allocate(
            context_window_tokens=int(case.get("contextWindow", 4_096)),
            output_reserve_tokens=int(case.get("outputReserveTokens", 1_024)),
            system_tokens=int(case.get("systemTokens", 500)),
            state_tokens=state_tokens,
        )
        kept, dropped = budget.trim_recent_messages(history)
        expect_truncation = bool(case.get("expectTruncation", False))
        truncation_correct = (dropped > 0) == expect_truncation
        latest_preserved = bool(kept) and kept[-1] == history[-1]

        expected_revision_mismatch = bool(case.get("expectedRevisionMismatch", False))
        actual_revision_mismatch = (
            str(case.get("conversationRevision", ""))
            != str(case.get("currentRevision", ""))
        )
        revision_matches_expectation = actual_revision_mismatch == expected_revision_mismatch
        revision_correct += int(revision_matches_expectation)

        probe = case.get("probe")
        probe_passed = True
        if probe is not None:
            if not isinstance(probe, dict):
                raise ValueError(f"invalid probe in {case['id']}")
            probe_total += 1
            enriched = state.enrich_search_query(
                str(probe.get("query", "")),
                str(probe.get("latestUserMessage", "")),
            )
            probe_passed = all(
                str(value) in enriched for value in probe.get("mustContain", [])
            )
            probe_correct += int(probe_passed)

        artifact_probe = case.get("artifactProbe")
        artifact_probe_passed = True
        if artifact_probe is not None:
            if not isinstance(artifact_probe, dict):
                raise ValueError(f"invalid artifactProbe in {case['id']}")
            artifact_probe_total += 1
            latest_user_message = str(
                artifact_probe.get("latestUserMessage", "")
            )
            artifact = _select_prior_assistant_artifact(
                [
                    *history,
                    {"role": "user", "content": latest_user_message},
                ],
                latest_user_message,
            )
            artifact_probe_passed = all(
                str(value) in artifact
                for value in artifact_probe.get("mustContain", [])
            ) and all(
                str(value) not in artifact
                for value in artifact_probe.get("mustNotContain", [])
            )
            artifact_probe_correct += int(artifact_probe_passed)

        passed = bool(
            len(correct_paths) == len(expected)
            and violations == 0
            and truncation_correct
            and latest_preserved
            and revision_matches_expectation
            and probe_passed
            and artifact_probe_passed
        )
        original_summary = summarize_messages(history)
        kept_summary = summarize_messages(kept)
        rows.append(
            {
                "id": str(case["id"]),
                "historyTurns": len(history) // 2,
                "historyMessages": len(history),
                "originalHistoryTokens": original_summary["tokens"],
                "keptHistoryTokens": kept_summary["tokens"],
                "droppedMessages": dropped,
                "truncated": dropped > 0,
                "latestMessagePreserved": latest_preserved,
                "stateFieldCount": state.field_count(),
                "expectedStateFields": len(expected),
                "correctStateFields": len(correct_paths),
                "overwriteFields": len(overwrite_paths),
                "correctOverwriteFields": sum(path in correct_paths for path in overwrite_paths),
                "contaminationViolations": violations,
                "revisionMismatch": actual_revision_mismatch,
                "revisionContractPassed": revision_matches_expectation,
                "probePassed": probe_passed,
                "artifactProbePassed": artifact_probe_passed,
                "passed": passed,
            }
        )
    case_count = len(rows)
    return {
        "schemaVersion": 1,
        "caseCount": case_count,
        "passedCases": sum(row["passed"] for row in rows),
        "passRate": round(sum(row["passed"] for row in rows) / case_count, 4),
        "historyTurnBuckets": {
            str(row["historyTurns"]): sum(
                1 for candidate in rows if candidate["historyTurns"] == row["historyTurns"]
            )
            for row in rows
        },
        "contextTruncationRate": round(
            sum(row["truncated"] for row in rows) / case_count, 4
        ),
        "stateRetentionAccuracy": round(expected_correct / max(expected_total, 1), 4),
        "stateOverwriteAccuracy": round(overwrite_correct / max(overwrite_total, 1), 4),
        "unconfirmedStateContaminationRate": round(
            contamination_count / max(forbidden_total, 1), 4
        ),
        "revisionMismatchAccuracy": round(revision_correct / case_count, 4),
        "queryProbeAccuracy": round(probe_correct / max(probe_total, 1), 4),
        "artifactReferenceAccuracy": round(
            artifact_probe_correct / max(artifact_probe_total, 1), 4
        ),
        "cases": rows,
    }


def _path_value(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate long conversation state contracts")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate_long_cases(load_long_cases(args.cases))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"cases={report['caseCount']} passed={report['passedCases']}")


if __name__ == "__main__":
    main()
