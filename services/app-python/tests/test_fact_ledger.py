from __future__ import annotations

import unittest

from trpg_app.pf1e_fact_adapter import (
    PF1E_FACT_LEDGER_ADAPTER,
    PF1EFactData,
    build_pf1e_fact_ledger,
    validate_pf1e_fact_answer,
)


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
    return build_pf1e_fact_ledger(
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
        data = value.adapter_data

        self.assertIsInstance(data, PF1EFactData)

        self.assertEqual(data.class_levels[("法师", 7)].max_spell_level, 4)
        self.assertEqual(data.bonus_feat_scopes[0].allowed_categories, (
            "超魔专长",
            "物品制造专长",
            "法术掌握",
        ))
        self.assertEqual(len(data.prestige_requirements), 1)
        self.assertEqual(value.record_count, 8)
        self.assertEqual(
            PF1E_FACT_LEDGER_ADAPTER.public(value),
            {
                "version": 2,
                "class_levels": [
                    {
                        "class": "法师",
                        "level": level,
                        "max_spell_level": maximum,
                        "spell_slots": slots,
                        "source": "S1",
                    }
                    for level, maximum, slots in (
                        (5, 3, [4, 3, 2, 1, None, None, None, None, None, None]),
                        (7, 4, [4, 4, 3, 2, 1, None, None, None, None, None]),
                        (9, 5, [4, 4, 4, 3, 2, 1, None, None, None, None]),
                        (10, 5, [4, 4, 4, 3, 3, 2, None, None, None, None]),
                    )
                ],
                "bonus_feat_scopes": [
                    {
                        "class": "法师",
                        "levels": [5, 10, 15, 20],
                        "allowed_categories": ["超魔专长", "物品制造专长", "法术掌握"],
                        "source": "S1",
                    }
                ],
                "prestige_requirements": [
                    {
                        "class": "奥法骑士",
                        "requirements": [
                            "擅长武器：角色必须擅长所有军用武器。",
                            "施法：角色必须能够施展3级奥术。",
                        ],
                        "source": "S2",
                    }
                ],
                "feat_count": 2,
                "feats": [],
                "feat_slots": [],
                "base_attack": [],
                "spells": [],
                "draft_path_templates": {
                    "class_allocation": "answer.build.levels",
                    "prestige_requirements": "answer.classes[<class>].requirements",
                    "level_feats": "answer.levels[<level>].feats",
                    "level_spell_progression": "answer.levels[<level>].spells",
                    "level_spell_slots": "answer.levels[<level>].spell_slots[<spell_level>]",
                    "feat": "answer.feats[<name>]",
                    "named_spell": "answer.spells[<name>]",
                    "spell_level": "answer.spells[<name>].level",
                    "spell_school": "answer.spells[<name>].school",
                    "spell_duration": "answer.spells[<name>].duration",
                    "spell_saving_throw": "answer.spells[<name>].saving_throw",
                    "equipment_stats": "answer.equipment.stats",
                },
            },
        )

    def test_rejects_level_sum_prestige_bab_bonus_scope_and_spell_node(self) -> None:
        answer = """
角色达到12级时，可采用法师5/战士4。[S1]
奥法骑士的进阶要求包括BAB+1。[S2]
法师10级奖励专长建议选择法术专攻。[S1][S3]
7级：获得3环法术位。[S1]
5级法师每天可施放2个1环法术。[S1]
"""
        issues = validate_pf1e_fact_answer(answer, ledger())

        self.assertEqual(
            [(issue.code, issue.message) for issue in issues],
            [
                ("class_level_sum", "职业等级分配 法师5/战士4 合计 9，不等于声明的角色等级 12"),
                ("invented_prestige_requirement", "奥法骑士 的已读进阶要求不包含 BAB/基本攻击条件"),
                ("bonus_feat_scope", "法术专攻 不在已读的 法师10级奖励专长范围内"),
                ("spell_progression", "法师7级最高法术环级应为 4，候选答案写为 3"),
                ("spell_slot_count", "法师5级的1环基础每日法术位应为 3，候选答案写为 2"),
            ],
        )

    def test_accepts_ledger_consistent_claims(self) -> None:
        answer = """
角色达到12级时，可采用法师9/战士3。[S1]
奥法骑士进阶要求为所有军用武器擅长与3级奥术施法。[S2]
法师10级奖励专长可选择持久法术这一超魔专长。[S1][S3]
7级法师可以获得4环法术位。[S1]
"""
        self.assertEqual(validate_pf1e_fact_answer(answer, ledger()), ())

    def test_rejects_named_feat_missing_from_read_evidence(self) -> None:
        value = build_pf1e_fact_ledger(
            "规划专长链",
            [("S1", {"title": "全专长列表", "content": FEATS})],
        )

        issues = validate_pf1e_fact_answer("建议选择：**高等猛力攻击**。[S1]", value)

        self.assertEqual(
            [(issue.code, issue.message) for issue in issues],
            [
                (
                    "unsupported_named_option",
                    "推荐的专长 高等猛力攻击 未出现在本轮已读专长条目中",
                )
            ],
        )

    def test_freezes_unknown_citation_and_cited_numeric_checks(self) -> None:
        issues = validate_pf1e_fact_answer(
            "该装备价格为50 gp[S1]。另见[S9]。",
            ledger(),
        )

        self.assertEqual(
            [(issue.code, issue.message) for issue in issues],
            [
                ("unknown_citation", "答案引用了未注册来源 S9"),
                ("unsupported_numeric_stat", "数值 50 gp 未出现在该句引用的来源中"),
            ],
        )

    def test_public_projection_filters_class_rows_to_goal_range(self) -> None:
        value = build_pf1e_fact_ledger(
            "规划6到9级法师",
            [("S1", {"title": "法师（Wizard）", "content": WIZARD})],
        )

        public = PF1E_FACT_LEDGER_ADAPTER.public(value)
        self.assertEqual(
            [item["level"] for item in public["class_levels"]],
            [7, 9],
        )

    def test_offline_helper_rejects_explicit_cross_library_evidence(self) -> None:
        with self.assertRaisesRegex(ValueError, "another library"):
            build_pf1e_fact_ledger(
                "检查来源",
                [
                    (
                        "S1",
                        {
                            "id": "gss:rule",
                            "rulesetId": "golden-sky-stories-zh-1-2",
                            "title": "规则",
                            "content": "规则正文",
                        },
                    )
                ],
            )

if __name__ == "__main__":
    unittest.main()
