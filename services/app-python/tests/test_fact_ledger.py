from __future__ import annotations

import unittest

from trpg_app.fact_ledger import build_fact_ledger, validate_fact_answer


WIZARD = """法师（Wizard）
| 表：法师 |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |
| 等级 | 基本攻击加值 | 强韧 | 反射 | 意志 | 特殊能力 | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
| 5 | +2 | +1 | +1 | +4 | 奖励专长 | 4 | 3 | 2 | 1 | - | - | - | - | - | - |
| 7 | +3 | +2 | +2 | +5 | | 4 | 4 | 3 | 2 | 1 | - | - | - | - | - |
| 9 | +4 | +3 | +3 | +6 | | 4 | 4 | 4 | 3 | 2 | 1 | - | - | - | - |
| 10 | +5 | +3 | +3 | +7 | 奖励专长 | 4 | 4 | 4 | 3 | 3 | 2 | - | - | - | - |
奖励专长：在5，10，15，20级时，法师都可以获得一个奖励专长。在每次获得这些专长时他可以挑选超魔专长、物品制造专长或者“法术掌握”专长。
"""

ELDRITCH_KNIGHT = """奥法骑士
进阶要求
擅长武器：角色必须擅长所有军用武器。
施法：角色必须能够施展3级奥术。
本职技能
"""

FEATS = """全专长列表
| 施法专长 | 先决条件 | 专长效果 | 类型 | 出处 |
| --- | --- | --- | --- | --- |
| Spell Focus 法术专攻 | | DC+1 | | CRB |
| Persistent Spell 持久法术 | | 重投豁免 | 超魔 | APG |
"""


def ledger():
    return build_fact_ledger(
        "规划法师与奥法骑士",
        [
            ("S1", {"title": "法师（Wizard）", "content": WIZARD}),
            ("S2", {"title": "奥法骑士", "content": ELDRITCH_KNIGHT}),
            ("S3", {"title": "全专长列表", "content": FEATS}),
        ],
    )


class FactLedgerTest(unittest.TestCase):
    def test_parses_spell_progression_bonus_scope_and_requirements(self) -> None:
        value = ledger()

        self.assertEqual(value.class_levels[("法师", 7)].max_spell_level, 4)
        self.assertEqual(value.bonus_feat_scopes[0].allowed_categories, (
            "超魔专长",
            "物品制造专长",
            "法术掌握",
        ))
        self.assertEqual(len(value.prestige_requirements), 1)
        self.assertGreaterEqual(value.record_count, 7)

    def test_rejects_level_sum_prestige_bab_bonus_scope_and_spell_node(self) -> None:
        answer = """
角色达到12级时，可采用法师5/战士4。[S1]
奥法骑士的进阶要求包括BAB+1。[S2]
法师10级奖励专长建议选择法术专攻。[S1][S3]
7级：获得3环法术位。[S1]
5级法师每天可施放2个1环法术。[S1]
"""
        codes = {issue.code for issue in validate_fact_answer(answer, ledger())}

        self.assertEqual(
            codes,
            {
                "class_level_sum",
                "invented_prestige_requirement",
                "bonus_feat_scope",
                "spell_progression",
                "spell_slot_count",
            },
        )

    def test_accepts_ledger_consistent_claims(self) -> None:
        answer = """
角色达到12级时，可采用法师9/战士3。[S1]
奥法骑士进阶要求为所有军用武器擅长与3级奥术施法。[S2]
法师10级奖励专长可选择持久法术这一超魔专长。[S1][S3]
7级法师可以获得4环法术位。[S1]
"""
        self.assertEqual(validate_fact_answer(answer, ledger()), ())

    def test_rejects_named_feat_missing_from_read_evidence(self) -> None:
        value = build_fact_ledger(
            "规划专长链",
            [("S1", {"title": "全专长列表", "content": FEATS})],
        )

        issues = validate_fact_answer("建议选择：**高等猛力攻击**。[S1]", value)

        self.assertEqual({issue.code for issue in issues}, {"unsupported_named_option"})


if __name__ == "__main__":
    unittest.main()
