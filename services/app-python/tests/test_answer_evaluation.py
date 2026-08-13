import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from trpg_app.answer_evaluation import (
    AnswerCase,
    AnswerTurn,
    JudgeInput,
    _normalize,
    _parse_judge_response,
    build_reference,
    evaluate_model,
    expand_history,
    grade_turn,
    load_cases,
    load_document_texts,
)


class _ScriptedRunner:
    """Replaces trpg_app.chat.run_rule_turn with scripted SSE events."""

    def __init__(self, event_batches):
        self.event_batches = list(event_batches)
        self.calls = 0
        self.messages = []
        self.dynamic_flags = []
        self.query_decomposition_flags = []
        self.complex_planner_flags = []
        self.fact_ledger_flags = []

    async def __call__(
        self,
        *,
        model,
        library,
        messages,
        request_id=None,
        enable_dynamic_evidence_budget=False,
        enable_query_decomposition=False,
        enable_complex_planner=False,
        enable_fact_ledger=False,
    ):
        self.messages.append([dict(message) for message in messages])
        self.dynamic_flags.append(enable_dynamic_evidence_budget)
        self.query_decomposition_flags.append(enable_query_decomposition)
        self.complex_planner_flags.append(enable_complex_planner)
        self.fact_ledger_flags.append(enable_fact_ledger)
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
                                "requiredSourceGroups": [["parent"], ["second"]],
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
        self.assertEqual(
            cases[0].turns[0].required_source_groups,
            (("parent",), ("second",)),
        )

    def test_expands_compact_history_template(self) -> None:
        history = expand_history(
            {
                "turnCount": 3,
                "fillerUser": "普通问题",
                "fillerAssistant": "普通回答",
                "events": [{"turn": 1, "user": "5级法师"}],
            }
        )

        self.assertEqual(len(history), 6)
        self.assertEqual(history[0], {"role": "user", "content": "5级法师"})
        self.assertEqual(history[-1], {"role": "assistant", "content": "普通回答"})

    def test_rejects_invalid_history_template(self) -> None:
        with self.assertRaises(ValueError):
            expand_history(
                {
                    "turnCount": 2,
                    "fillerUser": "u",
                    "fillerAssistant": "a",
                    "events": [{"turn": 3, "user": "outside"}],
                }
            )

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

    def test_requires_each_source_group_for_multi_topic_cases(self) -> None:
        turn = AnswerTurn(
            "比较",
            ("left", "right"),
            (("区别",),),
            (("left",), ("right", "right-parent")),
        )

        incomplete = grade_turn(
            "区别如下。",
            [{"documentId": "left", "metadata": {}}],
            turn,
        )
        complete = grade_turn(
            "区别如下。",
            [
                {"documentId": "left", "metadata": {}},
                {
                    "documentId": "right:section",
                    "metadata": {"legacyParentId": "right-parent"},
                },
            ],
            turn,
        )

        self.assertFalse(incomplete["passed"])
        self.assertEqual(
            incomplete["missingSourceGroups"],
            [["right", "right-parent"]],
        )
        self.assertTrue(complete["passed"])

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

    def _run_with_judge(self, cases, event_batches, judge):
        runner = _ScriptedRunner(event_batches)
        with patch("trpg_app.answer_evaluation.run_rule_turn", new=runner):
            return asyncio.run(
                evaluate_model(
                    _FakeModel(), _FakeLibrary(), cases, judge=judge
                )
            )

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

    def test_evaluate_forwards_dynamic_evidence_flag(self) -> None:
        cases = [AnswerCase("c1", (AnswerTurn("q", ("p",), (("x",),)),))]
        runner = _ScriptedRunner(
            [
                [
                    {"type": "text_delta", "delta": "x"},
                    {"type": "sources", "sources": [{"documentId": "p"}]},
                    {"type": "done"},
                ]
            ]
        )
        with patch("trpg_app.answer_evaluation.run_rule_turn", new=runner):
            report = asyncio.run(
                evaluate_model(
                    _FakeModel(),
                    _FakeLibrary(),
                    cases,
                    enable_dynamic_evidence_budget=True,
                )
            )

        self.assertEqual(runner.dynamic_flags, [True])
        self.assertTrue(report["dynamicEvidenceBudget"])

    def test_evaluate_forwards_query_decomposition_flag(self) -> None:
        cases = [AnswerCase("c1", (AnswerTurn("q", ("p",), (("x",),)),))]
        runner = _ScriptedRunner(
            [
                [
                    {"type": "text_delta", "delta": "x"},
                    {"type": "sources", "sources": [{"documentId": "p"}]},
                    {"type": "done"},
                ]
            ]
        )
        with patch("trpg_app.answer_evaluation.run_rule_turn", new=runner):
            report = asyncio.run(
                evaluate_model(
                    _FakeModel(),
                    _FakeLibrary(),
                    cases,
                    enable_query_decomposition=True,
                )
            )

        self.assertEqual(runner.query_decomposition_flags, [True])
        self.assertTrue(report["queryDecomposition"])

    def test_evaluate_forwards_complex_planner_flag(self) -> None:
        cases = [AnswerCase("c1", (AnswerTurn("q", ("p",), (("x",),)),))]
        runner = _ScriptedRunner(
            [
                [
                    {"type": "text_delta", "delta": "x"},
                    {"type": "sources", "sources": [{"documentId": "p"}]},
                    {"type": "done"},
                ]
            ]
        )
        with patch("trpg_app.answer_evaluation.run_rule_turn", new=runner):
            report = asyncio.run(
                evaluate_model(
                    _FakeModel(),
                    _FakeLibrary(),
                    cases,
                    enable_complex_planner=True,
                )
            )

        self.assertEqual(runner.complex_planner_flags, [True])
        self.assertTrue(report["complexPlanner"])

    def test_evaluate_forwards_fact_ledger_flag(self) -> None:
        cases = [AnswerCase("c1", (AnswerTurn("q", ("p",), (("x",),)),))]
        runner = _ScriptedRunner(
            [[
                {"type": "text_delta", "delta": "x"},
                {"type": "sources", "sources": [{"documentId": "p"}]},
                {"type": "done"},
            ]]
        )
        with patch("trpg_app.answer_evaluation.run_rule_turn", new=runner):
            report = asyncio.run(
                evaluate_model(
                    _FakeModel(),
                    _FakeLibrary(),
                    cases,
                    enable_fact_ledger=True,
                )
            )

        self.assertEqual(runner.fact_ledger_flags, [True])
        self.assertTrue(report["factLedger"])

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

    def test_evaluate_counts_safe_refusal_separately(self) -> None:
        class _Judge:
            calls = 0

            async def __call__(self, _input):
                type(self).calls += 1
                return {"factual_correct": True, "hallucination_free": True}

        cases = [AnswerCase("c1", (AnswerTurn("硬度？", ("p",), (("60",),)),))]
        events = [
            {"type": "safe_refusal", "reason": "repair_validation_failed"},
            {"type": "text_delta", "delta": "候选答案未通过服务器事实校验。"},
            {"type": "sources", "sources": []},
            {"type": "done"},
        ]

        report = self._run_with_judge(cases, [events], _Judge())

        turn = report["cases"][0]["turns"][0]
        self.assertTrue(turn["safeRefusal"])
        self.assertEqual(turn["safeRefusalReason"], "repair_validation_failed")
        self.assertEqual(report["safeRefusalTurns"], 1)
        self.assertEqual(report["safeRefusalRate"], 1.0)
        self.assertEqual(report["unsupportedTurns"], 0)
        self.assertEqual(report["unsupportedRate"], 0.0)
        self.assertEqual(_Judge.calls, 0)
        self.assertEqual(report["judgedTurns"], 0)

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

    def test_evaluate_prepends_case_history_without_reporting_content(self) -> None:
        case = AnswerCase(
            "history-case",
            (AnswerTurn("现在呢？", ("p",), (("-2",),)),),
            ({"role": "user", "content": "我选择混血术士作为职业"},),
        )
        events = [
            {"type": "text_delta", "delta": "-2"},
            {"type": "sources", "sources": [{"documentId": "p"}]},
            {"type": "done"},
        ]
        runner = _ScriptedRunner([events])
        with patch("trpg_app.answer_evaluation.run_rule_turn", new=runner):
            report = asyncio.run(evaluate_model(_FakeModel(), _FakeLibrary(), [case]))

        self.assertEqual(runner.messages[0][0]["content"], "我选择混血术士作为职业")
        self.assertEqual(report["cases"][0]["historyMessages"], 1)
        self.assertNotIn("我选择混血术士作为职业", json.dumps(report, ensure_ascii=False))

    def test_evaluate_skips_judge_when_none(self) -> None:
        cases = [AnswerCase("c1", (AnswerTurn("q", ("p",), (("x",),)),))]
        events = [
            {"type": "text_delta", "delta": "x"},
            {"type": "sources", "sources": [{"documentId": "p"}]},
            {"type": "done"},
        ]
        report = self._run(cases, [events])

        turn = report["cases"][0]["turns"][0]
        self.assertIsNone(turn.get("factualCorrect"))
        self.assertIsNone(turn.get("hallucinationFree"))
        self.assertIsNone(report["factualPassRate"])
        self.assertIsNone(report["hallucinationRate"])
        self.assertEqual(report["judgedTurns"], 0)

    def test_evaluate_records_judge_verdict_and_aggregates(self) -> None:
        class _RecordingJudge:
            def __init__(self):
                self.calls = []

            async def __call__(self, inp: JudgeInput):
                self.calls.append(inp)
                return {
                    "factual_correct": True,
                    "hallucination_free": False,
                    "reason": "answer adds an unsupported claim",
                }

        judge = _RecordingJudge()
        cases = [AnswerCase("c1", (AnswerTurn("硬度？", ("p",), (("60",),)),))]
        events = [
            {"type": "text_delta", "delta": "硬度为60。"},
            {"type": "sources", "sources": [{"documentId": "p"}]},
            {"type": "done"},
        ]
        report = self._run_with_judge(cases, [events], judge)

        turn = report["cases"][0]["turns"][0]
        self.assertTrue(turn["factualCorrect"])
        self.assertFalse(turn["hallucinationFree"])
        self.assertEqual(turn["judgeReason"], "answer adds an unsupported claim")
        self.assertIsNone(turn.get("judgeError"))
        self.assertEqual(report["judgedTurns"], 1)
        self.assertEqual(report["factualPassRate"], 1.0)
        self.assertEqual(report["hallucinationRate"], 1.0)
        # The judge received the flattened gold facts and the question.
        self.assertEqual(judge.calls[0].query, "硬度？")
        self.assertIn("60", judge.calls[0].facts)

    def test_evaluate_runs_judge_with_reference_map(self) -> None:
        captured = {}

        class _RefJudge:
            async def __call__(self, inp: JudgeInput):
                captured["reference"] = inp.reference
                return {"factual_correct": True, "hallucination_free": True, "reason": "ok"}

        reference_map = {"doc-1": "原文：硬度为60。"}
        cases = [AnswerCase("c1", (AnswerTurn("硬度？", ("doc-1",), (("60",),)),))]
        events = [
            {"type": "text_delta", "delta": "硬度为60。"},
            {"type": "sources", "sources": [{"documentId": "doc-1"}]},
            {"type": "done"},
        ]
        runner = _ScriptedRunner([events])
        with patch("trpg_app.answer_evaluation.run_rule_turn", new=runner):
            asyncio.run(
                evaluate_model(
                    _FakeModel(),
                    _FakeLibrary(),
                    cases,
                    judge=_RefJudge(),
                    reference_map=reference_map,
                )
            )

        self.assertIn("原文：硬度为60。", captured["reference"])

    def test_parse_judge_response_handles_json_and_prose(self) -> None:
        clean = _parse_judge_response(
            '{"factual_correct": true, "hallucination_free": false, "reason": "x"}'
        )
        self.assertTrue(clean["factual_correct"])
        self.assertFalse(clean["hallucination_free"])

        wrapped = _parse_judge_response(
            'Sure.\n```json\n{"factual_correct": "yes", "hallucination_free": "no", "reason": "y"}\n```'
        )
        self.assertTrue(wrapped["factual_correct"])
        self.assertFalse(wrapped["hallucination_free"])

        unparseable = _parse_judge_response("I cannot grade this.")
        self.assertIsNone(unparseable["factual_correct"])
        self.assertIn("unparseable", unparseable["reason"])

    def test_load_document_texts_and_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "documents.jsonl"
            path.write_text(
                json.dumps({"id": "doc-1", "content": "硬度为60。"}) + "\n"
                + json.dumps({"id": "doc-2", "content": "其他。"}) + "\n",
                encoding="utf-8",
            )
            texts = load_document_texts(path)

        self.assertEqual(texts["doc-1"], "硬度为60。")
        self.assertEqual(
            build_reference(["doc-1", "missing"], texts), "[doc-1]\n硬度为60。"
        )
        self.assertEqual(build_reference(["missing"], texts), "")


if __name__ == "__main__":
    unittest.main()
