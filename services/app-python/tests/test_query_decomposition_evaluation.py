import unittest

from trpg_app.query_decomposition_evaluation import evaluate


class QueryDecompositionEvaluationTest(unittest.TestCase):
    def test_reports_release_gate_metrics_without_raw_queries(self) -> None:
        report = evaluate(
            [
                {
                    "id": "simple",
                    "query": "借机攻击是什么？",
                    "expectedComplexity": "simple",
                    "expectedDomains": ["rule"],
                    "expectedQuestionCount": 0,
                },
                {
                    "id": "compound",
                    "query": "借机攻击何时触发？准备动作如何使用？",
                    "expectedComplexity": "compound",
                    "expectedDomains": ["rule"],
                    "expectedQuestionCount": 2,
                },
            ]
        )

        self.assertTrue(report["passed"])
        self.assertEqual(report["simpleMisSplitRate"], 0.0)
        self.assertEqual(report["targetCoverageRate"], 1.0)
        self.assertEqual(report["maxSubquestions"], 2)
        self.assertNotIn("query", report["cases"][0])


if __name__ == "__main__":
    unittest.main()
