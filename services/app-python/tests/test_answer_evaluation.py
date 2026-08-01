import json
import tempfile
import unittest
from pathlib import Path

from trpg_app.answer_evaluation import AnswerTurn, grade_turn, load_cases


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


if __name__ == "__main__":
    unittest.main()
