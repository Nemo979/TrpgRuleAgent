from __future__ import annotations

import argparse
import asyncio
import json
import re
import unicodedata
from dataclasses import dataclass
from openai import AsyncOpenAI
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from .chat import run_rule_turn
from .config import ModelConfig, load_config
from .libraries import Library, LibraryManifest

# Status values emitted by trpg_app.chat during tool execution. Counting these
# events gives an observational proxy for how many retrieval/read tool calls a
# turn triggered (the server enforces its own EvidenceBudget separately).
TOOL_STATUS_EVENTS = ("searching", "reading")
# Soft observation threshold for the evaluation harness: the chat loop allows at
# most 10 decisions and one tool per decision, plus recovery searches, so a well
# behaved turn stays well under this. Used only to flag runaway tool usage.
TOOL_CALL_BUDGET = 12


@dataclass(frozen=True)
class JudgeInput:
    query: str
    answer: str
    facts: tuple[str, ...]
    reference: str


AnswerJudge = Callable[["JudgeInput"], Awaitable[dict[str, Any]]]


def load_document_texts(documents_path: Path | None) -> dict[str, str]:
    """Map document id -> content text, used to feed gold sources to the judge."""
    if documents_path is None or not documents_path.exists():
        return {}
    texts: dict[str, str] = {}
    with documents_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                document = json.loads(line)
            except json.JSONDecodeError:
                continue
            document_id = document.get("id")
            if isinstance(document_id, str) and document_id:
                texts[document_id] = str(document.get("content", ""))
    return texts


def build_reference(relevant_ids: Sequence[str], document_texts: dict[str, str]) -> str:
    parts = []
    for relevant_id in relevant_ids:
        text = document_texts.get(relevant_id)
        if text:
            parts.append(f"[{relevant_id}]\n{text}")
    return "\n\n".join(parts)


def _build_judge_messages(inp: JudgeInput) -> list[dict[str, str]]:
    facts_block = "\n".join(f"- {fact}" for fact in inp.facts) or "(none provided)"
    reference_block = inp.reference or "(no reference text provided)"
    system = (
        "You are a strict factual grader for tabletop RPG rules answers. "
        "Respond with ONLY a JSON object (no prose, no markdown) with exactly these keys: "
        '"factual_correct" (boolean), "hallucination_free" (boolean), "reason" (string). '
        "factual_correct: does the answer correctly state the provided ground-truth facts "
        "without contradicting any of them? hallucination_free: does the answer contain any "
        "claim that is not supported by the question, the ground-truth facts, or the reference "
        "text? reason: one concise sentence explaining the verdict."
    )
    user = (
        f"Question:\n{inp.query}\n\n"
        f"Ground-truth facts (at least one option per group must be true):\n{facts_block}\n\n"
        f"Reference rule text:\n{reference_block}\n\n"
        f"Model answer:\n{inp.answer}\n\n"
        "Grade the answer and return only the JSON object."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _to_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")
    if isinstance(value, (int, float)):
        return value == 1
    return None


def _parse_judge_response(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {
            "factual_correct": None,
            "hallucination_free": None,
            "reason": f"unparseable judge output: {raw[:200]}",
        }
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {
            "factual_correct": None,
            "hallucination_free": None,
            "reason": f"invalid json: {raw[:200]}",
        }
    if not isinstance(data, dict):
        return {
            "factual_correct": None,
            "hallucination_free": None,
            "reason": f"judge output not an object: {raw[:200]}",
        }
    return {
        "factual_correct": _to_bool(data.get("factual_correct")),
        "hallucination_free": _to_bool(data.get("hallucination_free")),
        "reason": str(data.get("reason", ""))[:500],
    }


class LLMJudge:
    """LLM-as-judge backed by an OpenAI-compatible chat completion endpoint."""

    def __init__(self, model: ModelConfig) -> None:
        self.model = model
        self._client = AsyncOpenAI(
            api_key=model.api_key,
            base_url=model.base_url,
            timeout=model.request_timeout_seconds,
            max_retries=model.max_retries,
        )

    async def __call__(self, inp: JudgeInput) -> dict[str, Any]:
        messages = _build_judge_messages(inp)
        try:
            response = await self._client.chat.completions.create(
                model=self.model.model,
                messages=messages,
                max_tokens=min(self.model.max_output_tokens, 1024),
                temperature=0,
            )
            raw = response.choices[0].message.content or "{}"
            return _parse_judge_response(raw)
        except Exception as exc:  # provider/network errors must not abort the whole eval
            return {
                "factual_correct": None,
                "hallucination_free": None,
                "reason": f"judge_error: {type(exc).__name__}: {exc}",
            }


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
    judge: AnswerJudge | None = None,
    reference_map: dict[str, str] | None = None,
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
            tool_calls = 0
            try:
                async with asyncio.timeout(model.request_timeout_seconds):
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
                            status_value = str(event.get("status", ""))
                            statuses.append(status_value)
                            if status_value in TOOL_STATUS_EVENTS:
                                tool_calls += 1
                        elif event_type == "error":
                            error = dict(event)
            except TimeoutError:
                error = {
                    "type": "model_timeout",
                    "message": (
                        "model response exceeded the configured evaluation timeout "
                        f"({model.request_timeout_seconds:g}s)"
                    ),
                }
            except Exception as exception:
                error = {"type": type(exception).__name__, "message": str(exception)}
            answer = "".join(answer_parts).strip()
            grade = grade_turn(answer, sources, turn, error)
            if grade["passed"]:
                passed_turns += 1
            factual_correct: bool | None = None
            hallucination_free: bool | None = None
            judge_reason: str | None = None
            judge_error: str | None = None
            if judge is not None and answer and error is None:
                facts = tuple(option for group in turn.required_any for option in group)
                reference = build_reference(turn.relevant_ids, reference_map or {})
                try:
                    verdict = await judge(
                        JudgeInput(
                            query=turn.query,
                            answer=answer,
                            facts=facts,
                            reference=reference,
                        )
                    )
                    factual_correct = verdict.get("factual_correct")
                    hallucination_free = verdict.get("hallucination_free")
                    judge_reason = verdict.get("reason")
                    if isinstance(judge_reason, str) and judge_reason.startswith("judge_error"):
                        judge_error = judge_reason
                except Exception as exc:
                    judge_error = f"judge_error: {type(exc).__name__}: {exc}"
            case_rows.append(
                {
                    "turn": turn_index,
                    "query": turn.query,
                    "answer": answer,
                    "sources": sources,
                    "statuses": statuses,
                    "toolCalls": tool_calls,
                    "withinBudget": tool_calls <= TOOL_CALL_BUDGET,
                    "factualCorrect": factual_correct,
                    "hallucinationFree": hallucination_free,
                    "judgeReason": judge_reason,
                    "judgeError": judge_error,
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
    all_turn_rows = [row for case_row in rows for row in case_row["turns"]]
    answered_turns = [row for row in all_turn_rows if row.get("answer")]
    unsupported_turns = [
        row
        for row in answered_turns
        if not row["sourceMatch"] and row.get("error") is None
    ]
    judged_rows = [row for row in all_turn_rows if row.get("factualCorrect") is not None]
    factual_passed = sum(1 for row in judged_rows if row["factualCorrect"])
    hallucinated = sum(1 for row in judged_rows if row.get("hallucinationFree") is False)
    return {
        "modelId": model.id,
        "caseCount": len(cases),
        "turnCount": turn_count,
        "passedTurns": passed_turns,
        "passRate": round(passed_turns / max(turn_count, 1), 4),
        "toolCallBudget": TOOL_CALL_BUDGET,
        "toolCallTotal": sum(row["toolCalls"] for row in all_turn_rows),
        "toolCallMax": max((row["toolCalls"] for row in all_turn_rows), default=0),
        "unsupportedTurns": len(unsupported_turns),
        "unsupportedRate": round(
            len(unsupported_turns) / max(len(answered_turns), 1), 4
        ),
        "judgedTurns": len(judged_rows),
        "factualPassRate": round(factual_passed / len(judged_rows), 4) if judged_rows else None,
        "hallucinationRate": round(hallucinated / len(judged_rows), 4) if judged_rows else None,
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
    parser.add_argument(
        "--judge-model",
        default=None,
        help="Configured model ID to use as an LLM judge (optional; factual/consistency check)",
    )
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
    judge: AnswerJudge | None = None
    reference_map = load_document_texts(args.documents) if args.judge_model else {}
    if args.judge_model:
        judge_model = next((m for m in config.models if m.id == args.judge_model), None)
        if judge_model is None:
            raise ValueError(f"unknown judge model: {args.judge_model}")
        judge = LLMJudge(judge_model)
    reports = []
    for model in models:
        print(f"Evaluating {model.id}...", flush=True)
        report = await evaluate_model(
            model, library, cases, judge=judge, reference_map=reference_map
        )
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
