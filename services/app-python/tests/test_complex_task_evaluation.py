import json
import tempfile
import unittest
from pathlib import Path

from trpg_app.complex_task_evaluation import evaluate, load_cases
from trpg_app.answer_evaluation import load_cases as load_answer_cases


class ComplexTaskEvaluationTest(unittest.TestCase):
    def test_stage_three_probe_separates_independent_and_dependent_complex_cases(self) -> None:
        cases = load_cases(Path("rulepacks/pathfinder-1e/evals/complex-task-cases.jsonl"))

        report = evaluate(cases)

        self.assertTrue(report["passed"])
        self.assertEqual(report["passedCases"], 10)
        self.assertEqual(report["stage2SufficientCases"], 4)
        self.assertEqual(report["plannerCandidateCases"], 6)
        self.assertEqual(report["falsePlannerTriggers"], 0)
        self.assertEqual(report["missedPlannerNeeds"], 0)
        self.assertEqual(report["plannerReadyCases"], 6)
        self.assertEqual(report["synthesisReadyCases"], 6)
        self.assertTrue(
            all(
                not row["plannerRecommended"]
                for row in report["cases"]
                if row["expectedStage2Sufficient"]
            )
        )
        self.assertTrue(
            all(
                row["plannerValid"] and not row["plannerMissingDomains"]
                for row in report["cases"]
                if not row["expectedStage2Sufficient"]
            )
        )
        self.assertEqual(
            report["nextAction"],
            "run_local_synthesis_regression",
        )
        self.assertTrue(
            all(
                row["synthesisContractValid"] and row["synthesisCheckKinds"]
                for row in report["cases"]
                if not row["expectedStage2Sufficient"]
            )
        )

    def test_rejects_cyclic_case_dependencies(self) -> None:
        case = {
            "id": "cycle",
            "query": "我想规划1到5级法师，必须比较职业和法术。",
            "tasks": [
                {"id": "a", "domain": "class", "dependsOn": ["b"]},
                {"id": "b", "domain": "spell", "dependsOn": ["a"]},
            ],
            "conditional": True,
            "expectedStage2Sufficient": False,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.jsonl"
            path.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "cyclic dependencies"):
                load_cases(path)

    def test_complex_answer_gold_set_contains_only_planner_candidates(self) -> None:
        structural = {
            case["id"]: case
            for case in load_cases(
                Path("rulepacks/pathfinder-1e/evals/complex-task-cases.jsonl")
            )
        }
        answer_cases = load_answer_cases(
            Path("rulepacks/pathfinder-1e/evals/complex-task-answer-cases.jsonl")
        )

        self.assertEqual(len(answer_cases), 6)
        for case in answer_cases:
            self.assertFalse(structural[case.id]["expectedStage2Sufficient"])


if __name__ == "__main__":
    unittest.main()
