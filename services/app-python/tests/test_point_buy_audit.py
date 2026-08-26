import unittest

from trpg_app.point_buy_audit import build_point_buy_audit


POINT_BUY_EVIDENCE = {
    "title": "生成属性值（Generating Ability Scores）",
    "fullPath": "核心规则 > 属性 > 生成属性值",
    "content": """购点（Purchase）：种族调整在所有购点使用完毕后生效。
| 原始属性 | 消耗购点 |
| --- | --- |
| 7 | -4 |
| 8 | -2 |
| 9 | -1 |
| 10 | 0 |
| 11 | 1 |
| 12 | 2 |
| 13 | 3 |
| 14 | 5 |
| 15 | 7 |
| 16 | 10 |
| 17 | 13 |
| 18 | 17 |""",
}
CATFOLK_EVIDENCE = {
    "title": "猫族",
    "fullPath": "种族 > 猫族",
    "content": "猫族种族特性\n+2敏捷，+2魅力，-2感知：猫族随和而又敏捷。",
}
BAD_PROPOSAL = """| 属性 | 基础值 | 购点消耗 | 最终值（含种族） |
| **力量** | 10 | -2 (降至8) | 8 |
| **敏捷** | 10 | +2 (升至12) | 14 |
| **体质** | 10 | +2 (升至12) | 12 |
| **智力** | 10 | +5 (升至14) | 14 |
| **感知** | 10 | +5 (升至14) | 16 |
| **魅力** | 10 | +2 (升至12) | 12 |"""


class PointBuyAuditTest(unittest.TestCase):
    def test_audits_cost_and_catfolk_adjustments_deterministically(self) -> None:
        audit = build_point_buy_audit(
            artifact=BAD_PROPOSAL,
            budget=20,
            race="猫族",
            evidence_documents=[POINT_BUY_EVIDENCE, CATFOLK_EVIDENCE],
        )

        self.assertIsNotNone(audit)
        result = audit.public()  # type: ignore[union-attr]
        self.assertEqual(result["totalCost"], 14)
        self.assertEqual(result["budgetDelta"], 6)
        self.assertEqual(result["expectedFinalScores"]["感知"], 12)
        self.assertEqual(result["expectedFinalScores"]["魅力"], 14)
        self.assertEqual(
            result["finalScoreDiscrepancies"]["感知"],
            {"proposed": 16, "expected": 12},
        )
        self.assertEqual(
            result["finalScoreDiscrepancies"]["魅力"],
            {"proposed": 12, "expected": 14},
        )
        answer = audit.render_answer(point_buy_label="S7", race_label="S1")  # type: ignore[union-attr]
        self.assertIn("不是一个用满 20 点", answer)
        self.assertIn("= 14", answer)
        self.assertIn("还剩 6 点未使用", answer)
        self.assertIn("感知应为12", answer)
        self.assertIn("魅力应为14", answer)
        self.assertIn("[S7][S1]", answer)

    def test_refuses_audit_without_registered_cost_table(self) -> None:
        self.assertIsNone(
            build_point_buy_audit(
                artifact=BAD_PROPOSAL,
                budget=20,
                race="猫族",
                evidence_documents=[CATFOLK_EVIDENCE],
            )
        )

    def test_refuses_racial_result_without_registered_race_adjustments(self) -> None:
        self.assertIsNone(
            build_point_buy_audit(
                artifact=BAD_PROPOSAL,
                budget=20,
                race="猫族",
                evidence_documents=[POINT_BUY_EVIDENCE],
            )
        )

    def test_refuses_incomplete_six_ability_proposal(self) -> None:
        self.assertIsNone(
            build_point_buy_audit(
                artifact="力量 -2（降至8）",
                budget=20,
                race="猫族",
                evidence_documents=[POINT_BUY_EVIDENCE, CATFOLK_EVIDENCE],
            )
        )


if __name__ == "__main__":
    unittest.main()
