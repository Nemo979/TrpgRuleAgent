from __future__ import annotations

import unittest

from trpg_app.fact_ledger_evaluation import evaluate_report


class FactLedgerEvaluationTest(unittest.TestCase):
    def test_audits_report_without_copying_source_bodies_to_output(self) -> None:
        report = {
            "models": [
                {
                    "cases": [
                        {
                            "id": "bad-level-sum",
                            "turns": [
                                {
                                    "query": "规划到12级",
                                    "answer": "角色达到12级时采用法师5/战士4。[S1]",
                                    "sources": [
                                        {
                                            "label": "S1",
                                            "title": "规则",
                                            "metadata": {
                                                "structuralBlocks": [
                                                    {"content": "这是完整规则正文 secret-body"}
                                                ]
                                            },
                                        }
                                    ],
                                }
                            ],
                        }
                    ]
                }
            ]
        }

        result = evaluate_report(report)

        self.assertEqual(result["rejectedCases"], 1)
        self.assertEqual(result["cases"][0]["issueCodes"], ["class_level_sum"])
        self.assertNotIn("secret-body", str(result))

        negative_result = evaluate_report(report, expect_rejected=True)
        self.assertTrue(negative_result["passed"])
        self.assertEqual(negative_result["expectationPassedCases"], 1)
        self.assertEqual(
            negative_result["nextAction"],
            "fact_ledger_migration_gate_complete",
        )


if __name__ == "__main__":
    unittest.main()
