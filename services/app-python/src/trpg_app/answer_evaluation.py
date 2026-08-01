from __future__ import annotations

import argparse
import asyncio
import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .chat import run_rule_turn
from .config import ModelConfig, load_config
from .libraries import Library, LibraryManifest


@dataclass(frozen=True)
class AnswerTurn:
    query: str
    relevant_ids: tuple[str, ...]
    required_any: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class AnswerCase:
    id: str
    turns: tuple[AnswerTurn, ...]


def load_cases(path: Path) -> list[AnswerCase]:
    cases: list[AnswerCase] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            turns_value = value.get("turns")
            if not value.get("id") or not isinstance(turns_value, list) or not turns_value:
                raise ValueError(f"invalid answer case at line {line_number}")
            turns: list[AnswerTurn] = []
            for turn in turns_value:
                required = turn.get("requiredAny", [])
                relevant = turn.get("relevantIds", [])
                if (
                    not isinstance(turn, dict)
                    or not turn.get("query")
                    or not isinstance(required, list)
                    or not required
                    or not isinstance(relevant, list)
                    or not relevant
                ):
                    raise ValueError(f"invalid answer turn at line {line_number}")
                turns.append(
                    AnswerTurn(
                        query=str(turn["query"]),
                        relevant_ids=tuple(str(item) for item in relevant),
                        required_any=tuple(
                            tuple(str(option) for option in group)
                            for group in required
                            if isinstance(group, list) and group
                        ),
                    )
                )
            if any(not turn.required_any for turn in turns):
                raise ValueError(f"empty requiredAny group at line {line_number}")
            cases.append(AnswerCase(str(value["id"]), tuple(turns)))
    if not cases:
        raise ValueError("answer evaluation set is empty")
    return cases


def grade_turn(
    answer: str,
    sources: Sequence[dict[str, Any]],
    turn: AnswerTurn,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_answer = _normalize(answer)
    missing = [
        list(group)
        for group in turn.required_any
        if not any(_normalize(option) in normalized_answer for option in group)
    ]
    relevant = set(turn.relevant_ids)
    source_match = any(
        str(source.get("documentId", "")) in relevant
        or str(source.get("metadata", {}).get("legacyParentId", "")) in relevant
        for source in sources
        if isinstance(source, dict)
    )
    passed = error is None and bool(answer.strip()) and not missing and source_match
    return {
        "passed": passed,
        "missingRequiredAny": missing,
        "sourceMatch": source_match,
        "error": error,
    }


async def evaluate_model(
    model: ModelConfig,
    library: Library,
    cases: Sequence[AnswerCase],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    passed_turns = 0
    turn_count = 0
    for case in cases:
        messages: list[dict[str, str]] = []
        case_rows: list[dict[str, Any]] = []
        for turn_index, turn in enumerate(case.turns, start=1):
            turn_count += 1
            messages.append({"role": "user", "content": turn.query})
            answer_parts: list[str] = []
            sources: list[dict[str, Any]] = []
            error: dict[str, Any] | None = None
            statuses: list[str] = []
            try:
                async for event in run_rule_turn(
                    model=model,
                    library=library,
                    messages=messages,
                    request_id=f"eval-{model.id}-{case.id}-{turn_index}",
                ):
                    event_type = event.get("type")
                    if event_type == "text_delta":
                        answer_parts.append(str(event.get("delta", "")))
                    elif event_type == "sources":
                        value = event.get("sources", [])
                        if isinstance(value, list):
                            sources = value
                    elif event_type == "status":
                        statuses.append(str(event.get("status", "")))
                    elif event_type == "error":
                        error = dict(event)
            except Exception as exception:
                error = {"type": type(exception).__name__, "message": str(exception)}
            answer = "".join(answer_parts).strip()
            grade = grade_turn(answer, sources, turn, error)
            if grade["passed"]:
                passed_turns += 1
            case_rows.append(
                {
                    "turn": turn_index,
                    "query": turn.query,
                    "answer": answer,
                    "sources": sources,
                    "statuses": statuses,
                    **grade,
                }
            )
            if answer:
                messages.append({"role": "assistant", "content": answer})
            if error is not None:
                break
        rows.append(
            {
                "id": case.id,
                "passed": len(case_rows) == len(case.turns)
                and all(row["passed"] for row in case_rows),
                "turns": case_rows,
            }
        )
    return {
        "modelId": model.id,
        "caseCount": len(cases),
        "turnCount": turn_count,
        "passedTurns": passed_turns,
        "passRate": round(passed_turns / max(turn_count, 1), 4),
        "cases": rows,
    }


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = normalized.replace("–", "-").replace("—", "-").replace("−", "-")
    return "".join(character for character in normalized if not character.isspace())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate grounded PF1e answers")
    parser.add_argument("--config", type=Path, default=Path("config/app.yaml"))
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--index-dir", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--models", required=True, help="Comma-separated configured model IDs")
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


async def async_main() -> None:
    args = parse_args()
    config = load_config(args.config)
    requested = {value.strip() for value in args.models.split(",") if value.strip()}
    models = [model for model in config.models if model.id in requested]
    if {model.id for model in models} != requested:
        missing = sorted(requested - {model.id for model in models})
        raise ValueError(f"unknown configured models: {', '.join(missing)}")
    manifest = LibraryManifest(
        id="pathfinder-1e",
        name="Pathfinder 1E 中文规则库（结构化候选）",
        system="Pathfinder",
        edition="1E",
        revision="structured-candidate",
        documents=args.documents.resolve(),
        index_dir=args.index_dir.resolve(),
        aliases=("PF1E", "Pathfinder 1E"),
    )
    library = Library(manifest)
    cases = load_cases(args.cases)
    reports = []
    for model in models:
        print(f"Evaluating {model.id}...", flush=True)
        report = await evaluate_model(model, library, cases)
        reports.append(report)
        print(
            f"{model.id}: {report['passedTurns']}/{report['turnCount']} turns passed",
            flush=True,
        )
    result = {"libraryRevision": manifest.revision, "models": reports}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
