import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from trpg_app.answer_evaluation import (
    AnswerCase,
    AnswerTurn,
    _normalize,
    evaluate_model,
    grade_turn,
    load_cases,
)


class _ScriptedRunner:
    """Replaces trpg_app.chat.run_rule_turn with scripted SSE events."""

    def __init__(self, event_batches):
        self.event_batches = list(event_batches)
        self.calls = 0

    async def __call__(self, *, model, library, messages, request_id=None):
        batch = self.event_batches[self.calls]
        self.calls += 1
        for event in batch:
            yield event


class _FakeModel:
    id = "test-model"
    request_timeout_seconds = 30.0


class _FakeLibrary:
    pass


class AnswerEvaluationTest(unittest.TestCase):
    def test_loads_multi_turn_cases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cases.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "id": "combat",
                        "turns": [
                            {
                                "query": "减值是多少？",
                                "relevantIds": ["parent"],
                                "requiredAny": [["-2", "减2"]],
                            }
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            cases = load_cases(path)

        self.assertEqual(cases[0].turns[0].required_any, (("-2", "减2"),))

    def test_grades_facts_and_legacy_parent_source(self) -> None:
        turn = AnswerTurn("问题", ("parent",), (("-2", "减2"), ("副手",)))

        grade = grade_turn(
            "副手承受–2减值。",
            [
                {
                    "documentId": "parent:section:one",
                    "metadata": {"legacyParentId": "parent"},
                }
            ],
            turn,
        )

        self.assertTrue(grade["passed"])

    def test_rejects_missing_fact_or_wrong_source(self) -> None:
        turn = AnswerTurn("问题", ("parent",), (("60",),))

        grade = grade_turn("硬度为10。", [{"documentId": "other"}], turn)

        self.assertFalse(grade["passed"])
        self.assertEqual(grade["missingRequiredAny"], [["60"]])
        self.assertFalse(grade["sourceMatch"])

    def test_normalizes_fact_matching_across_dash_variants(self) -> None:
        turn = AnswerTurn("问题", ("parent",), (("-2", "减2"),))
        grade = grade_turn(
            "承受 –2 减值。",
            [{"documentId": "parent:1", "metadata": {"legacyParentId": "parent"}}],
            turn,
        )
        self.assertTrue(grade["passed"])
        self.assertEqual(_normalize("–2 减值"), _normalize("-2减值"))

    def _run(self, cases, event_batches):
        runner = _ScriptedRunner(event_batches)
        with patch("trpg_app.answer_evaluation.run_rule_turn", new=runner):
            return asyncio.run(evaluate_model(_FakeModel(), _FakeLibrary(), cases))

    def test_evaluate_counts_tool_calls_and_budget(self) -> None:
        cases = [
            AnswerCase(
                "c1",
                (AnswerTurn("减值多少？", ("p",), (("-2", "减2"),)),),
            )
        ]
        events = [
            {"type": "status", "status": "thinking"},
            {"type": "status", "status": "searching"},
            {"type": "status", "status": "reading"},
            {"type": "text_delta", "delta": "减值是 -2。"},
            {
                "type": "sources",
                "sources": [{"documentId": "p:1", "metadata": {"legacyParentId": "p"}}],
            },
            {"type": "done"},
        ]
        report = self._run(cases, [events])

        self.assertEqual(report["passedTurns"], 1)
        turn = report["cases"][0]["turns"][0]
        self.assertEqual(turn["toolCalls"], 2)
        self.assertTrue(turn["withinBudget"])
        self.assertEqual(report["toolCallTotal"], 2)

    def test_evaluate_flags_budget_overrun(self) -> None:
        cases = [AnswerCase("c1", (AnswerTurn("q", ("p",), (("x",),)),))]
        runaway = [{"type": "status", "status": "searching"} for _ in range(20)]
        runaway += [
            {"type": "text_delta", "delta": "x"},
            {"type": "sources", "sources": []},
            {"type": "done"},
        ]
        report = self._run(cases, [runaway])

        turn = report["cases"][0]["turns"][0]
        self.assertEqual(turn["toolCalls"], 20)
        self.assertFalse(turn["withinBudget"])

    def test_evaluate_reports_unsupported_conclusion(self) -> None:
        cases = [AnswerCase("c1", (AnswerTurn("硬度？", ("p",), (("60",),)),))]
        events = [
            {"type": "text_delta", "delta": "硬度为10。"},
            {"type": "sources", "sources": [{"documentId": "other"}]},
            {"type": "done"},
        ]
        report = self._run(cases, [events])

        turn = report["cases"][0]["turns"][0]
        self.assertFalse(turn["sourceMatch"])
        self.assertEqual(report["unsupportedTurns"], 1)
        self.assertEqual(report["unsupportedRate"], 1.0)

    def test_evaluate_handles_timeout_as_error(self) -> None:
        class _TimeoutRunner:
            async def __call__(self, *, model, library, messages, request_id=None):
                raise TimeoutError("boom")
                yield  # unreachable; keeps this an async generator

        cases = [AnswerCase("c1", (AnswerTurn("q", ("p",), (("x",),)),))]
        with patch(
            "trpg_app.answer_evaluation.run_rule_turn", new=_TimeoutRunner()
        ):
            report = asyncio.run(
                evaluate_model(_FakeModel(), _FakeLibrary(), cases)
            )

        turn = report["cases"][0]["turns"][0]
        self.assertEqual(turn["error"]["type"], "model_timeout")
        self.assertFalse(turn["passed"])
        self.assertEqual(report["passedTurns"], 0)

    def test_evaluate_multi_turn_passes_both(self) -> None:
        cases = [
            AnswerCase(
                "c1",
                (
                    AnswerTurn("减值多少？", ("p",), (("-2",),)),
                    AnswerTurn("那攻击呢？", ("a",), (("+1",),)),
                ),
            )
        ]
        batch1 = [
            {"type": "text_delta", "delta": "-2。"},
            {
                "type": "sources",
                "sources": [{"documentId": "p:1", "metadata": {"legacyParentId": "p"}}],
            },
            {"type": "done"},
        ]
        batch2 = [
            {"type": "text_delta", "delta": "+1。"},
            {
                "type": "sources",
                "sources": [{"documentId": "a:1", "metadata": {"legacyParentId": "a"}}],
            },
            {"type": "done"},
        ]
        report = self._run(cases, [batch1, batch2])

        self.assertEqual(report["turnCount"], 2)
        self.assertEqual(report["passedTurns"], 2)


if __name__ == "__main__":
    unittest.main()
