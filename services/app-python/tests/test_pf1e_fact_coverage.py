from __future__ import annotations

import unittest

from trpg_app.pf1e_fact_adapter import (
    PF1E_FACT_LEDGER_ADAPTER,
    build_pf1e_fact_ledger,
    validate_pf1e_fact_answer,
)


ADVANCEMENT = """角色升级（Character Advancement）
关于专长和属性的对应获得等级，请参见下表。
| 角色等级 | 专长获得 | 属性增长 |
| --- | --- | --- |
| 1 | 第1项 | |
| 2 | | |
| 3 | 第2项 | |
| 4 | | 第1次 |
| 5 | 第3项 | |
"""

FIGHTER = """战士（Fighter）
表：战士
| 等级 | 基本攻击加值 | 强韧 | 反射 | 意志 | 特殊能力 |
| --- | --- | --- | --- | --- | --- |
| 1 | +1 | +2 | +0 | +0 | 奖励专长 |
| 2 | +2 | +3 | +0 | +0 | 奖励专长 |
| 3 | +3 | +3 | +1 | +1 | 盔甲训练1 |
| 4 | +4 | +4 | +1 | +1 | 奖励专长 |
| 5 | +5 | +4 | +1 | +1 | 武器训练1 |
奖励专长：1级以及之后的每个偶数等级，战士获得一项奖励专长，
该额外专长独立于角色升级时获得的（意味着战士每级都可获得专长）。
这些专长必须在战斗专长列表中。
"""

HUMAN = """人类
奖励专长：人类角色在1级时获得一个额外专长。
"""

FEATS = """全专长列表
| 专长名称 | 先决条件 | 专长效果 | 类型 | 出处 |
| --- | --- | --- | --- | --- |
| Power Attack 猛力攻击 | 力量13，BAB+1 | 以攻击换伤害 | 战斗 | CRB |
| Cleave 顺势斩 | 猛力攻击 | 额外攻击 | 战斗 | CRB |
| Great Cleave 大顺势斩 | 顺势斩，BAB+4 | 连续额外攻击 | 战斗 | CRB |
| Weapon Focus 武器专攻 | | 命中+1 | 战斗 | CRB |
| Dodge 闪避 | | AC+1 | 战斗 | CRB |
| Iron Will 钢铁意志 | | 意志+2 | | CRB |
| Combat Reflexes 战斗反射 | | 更多借机攻击 | 战斗 | CRB |
"""

MAGE_ARMOR = """法师护甲 (Mage Armor)
学派
咒法系 (创造) [力场]
环位
术士/法师 1, 召唤师 1, 女巫 1
施法时间
标准动作
持续时间
1小时/等级 (可解消)
豁免
意志，通过则无效 (无害)
法术抗力
否
"""

MIRROR_IMAGE = """镜影术 (Mirror Image)
学派
幻术系 (虚幻)
环位
吟游诗人 2, 术士/法师 2
施法时间
标准动作
持续时间
1分钟/等级
豁免
无
法术抗力
否
"""


def build(goal: str):
    return build_pf1e_fact_ledger(
        goal,
        [
            ("S1", {"title": "角色升级", "content": ADVANCEMENT}),
            ("S2", {"title": "战士", "content": FIGHTER}),
            ("S3", {"title": "人类", "content": HUMAN}),
            ("S4", {"title": "全专长列表", "content": FEATS}),
            ("S5", {"title": "法师护甲 (Mage Armor)", "content": MAGE_ARMOR}),
            ("S6", {"title": "镜影术 (Mirror Image)", "content": MIRROR_IMAGE}),
        ],
    )


class PF1EFactCoverageTest(unittest.TestCase):
    def test_derives_odd_general_slots_only_when_fighter_evidence_states_them(self) -> None:
        ledger = build_pf1e_fact_ledger(
            "规划1到10级战士专长",
            [("S1", {"title": "战士", "content": FIGHTER})],
        )

        general_levels = [
            item.level
            for item in ledger.adapter_data.feat_slots
            if item.source_type == "general"
        ]

        self.assertEqual(general_levels, list(range(1, 20, 2)))

    def test_does_not_invent_feat_slots_without_matching_evidence(self) -> None:
        ledger = build_pf1e_fact_ledger(
            "规划1到5级专长",
            [("S1", {"title": "规则摘要", "content": "角色可以选择专长。"})],
        )

        self.assertEqual(ledger.adapter_data.feat_slots, [])
        self.assertEqual(
            validate_pf1e_fact_answer("1级：没有安排专长。", ledger),
            (),
        )

    def test_uses_structural_blocks_only_when_direct_body_is_empty(self) -> None:
        ledger = build_pf1e_fact_ledger(
            "核对法术",
            [
                (
                    "S1",
                    {
                        "title": "镜影术 (Mirror Image)",
                        "content": "",
                        "metadata": {
                            "structuralBlocks": [{"content": MIRROR_IMAGE}]
                        },
                    },
                )
            ],
        )

        self.assertEqual(ledger.adapter_data.spells["镜影术"].source_label, "S1")

    def test_parses_timeline_prerequisite_support_and_spell_metadata(self) -> None:
        ledger = build("创建1到5级人类战士专长与法术方案")
        data = ledger.adapter_data

        self.assertEqual(ledger.adapter_version, 3)
        self.assertEqual(
            sorted(
                (item.level, item.source_type)
                for item in data.feat_slots
                if item.level <= 5
            ),
            sorted([
                (1, "general"),
                (3, "general"),
                (5, "general"),
                (1, "class_bonus"),
                (2, "class_bonus"),
                (4, "class_bonus"),
                (1, "ancestry_bonus"),
            ]),
        )
        self.assertEqual(data.base_attack[("战士", 4)].bonus, 4)
        self.assertEqual(data.feats["大顺势斩"].prerequisites, "顺势斩，BAB+4")
        self.assertEqual(dict(data.spells["镜影术"].class_levels)["法师"], 2)
        self.assertEqual(data.spells["法师护甲"].school, "咒法系")
        public = PF1E_FACT_LEDGER_ADAPTER.public(ledger)
        self.assertEqual(public["version"], 2)
        self.assertEqual(public["feats"], [])
        self.assertEqual(
            {item["name"] for item in public["spells"]},
            {"法师护甲", "镜影术"},
        )
        self.assertTrue(
            {"feat_slot", "base_attack_bonus", "spell_metadata"}
            <= {record.predicate for record in ledger.records}
        )

    def test_public_projection_includes_only_goal_feat_prerequisite_graph(self) -> None:
        ledger = build("规划1到5级战士的大顺势斩专长链")

        public = PF1E_FACT_LEDGER_ADAPTER.public(ledger)

        self.assertEqual(
            [item["name"] for item in public["feats"]],
            ["大顺势斩", "猛力攻击", "顺势斩"],
        )
        self.assertNotIn("武器专攻", [item["name"] for item in public["feats"]])

    def test_rejects_missing_general_feat_slots_from_known_failure_shape(self) -> None:
        ledger = build("创建1到5级人类战士专长升级路线")
        answer = """
力量13。
1级：选择专长：猛力攻击，武器专攻。
2级：选择专长：顺势斩。
3级：获得盔甲训练1。
4级：选择专长：大顺势斩。
5级：获得武器训练1。
"""

        issues = validate_pf1e_fact_answer(answer, ledger)
        timeline = [issue for issue in issues if issue.code == "feat_timeline_slot_count"]

        self.assertEqual(
            [(issue.path, issue.expected, issue.actual) for issue in timeline],
            [
                ("answer.levels[1].feats", 3, 2),
                ("answer.levels[3].feats", 1, 0),
                ("answer.levels[5].feats", 1, 0),
            ],
        )
        self.assertTrue(all(issue.evidence_refs for issue in timeline))

    def test_rejects_feat_selected_before_dependency_and_bab(self) -> None:
        ledger = build("核对战士专长前提")

        issues = validate_pf1e_fact_answer(
            "力量13。\n1级：选择专长：大顺势斩，当前BAB+1。",
            ledger,
        )

        self.assertEqual(
            [issue.code for issue in issues],
            ["feat_prerequisite_missing", "feat_bab_prerequisite"],
        )
        self.assertEqual(issues[0].expected, "顺势斩")
        self.assertEqual(issues[1].expected, 4)
        self.assertEqual(issues[1].actual, 1)

    def test_accepts_complete_timeline_and_satisfied_feat_chain(self) -> None:
        ledger = build("创建1到5级人类战士专长升级路线")
        answer = """
力量13。
1级：选择专长：猛力攻击，武器专攻，闪避。
2级：选择专长：顺势斩。
3级：选择专长：钢铁意志。
4级：选择专长：大顺势斩。
5级：选择专长：战斗反射。
"""

        self.assertEqual(validate_pf1e_fact_answer(answer, ledger), ())

    def test_rejects_wrong_spell_level_and_school(self) -> None:
        ledger = build("规划法师防御法术")

        issues = validate_pf1e_fact_answer(
            "法师护甲属于防护学派；建议准备镜影术（3环）。",
            ledger,
        )

        self.assertEqual([issue.code for issue in issues], ["spell_school", "spell_level"])
        self.assertEqual(issues[0].expected, "咒法系")
        self.assertEqual(issues[1].expected, 2)

    def test_accepts_evidence_consistent_spell_metadata(self) -> None:
        ledger = build("规划法师防御法术")

        issues = validate_pf1e_fact_answer(
            "法师护甲是1环咒法系法术；建议准备镜影术（2环，幻术系）。",
            ledger,
        )

        self.assertEqual(issues, ())

    def test_accepts_explicitly_negated_wrong_spell_metadata(self) -> None:
        ledger = build("规划法师防御法术")

        issues = validate_pf1e_fact_answer(
            "法师护甲不是防护学派而是咒法系；镜影术不是3环而是2环幻术系。",
            ledger,
        )

        self.assertEqual(issues, ())

    def test_rejects_wrong_spell_duration_and_saving_throw(self) -> None:
        ledger = build("规划法师防御法术")

        issues = validate_pf1e_fact_answer(
            "镜影术持续10分钟/等级，豁免为意志通过则无效。",
            ledger,
        )

        self.assertEqual(
            [issue.code for issue in issues],
            ["spell_duration", "spell_saving_throw"],
        )
        self.assertEqual(issues[0].expected, "1分钟/等级")
        self.assertEqual(issues[1].expected, "无")

    def test_accepts_spell_duration_and_saving_throw(self) -> None:
        ledger = build("规划法师防御法术")

        issues = validate_pf1e_fact_answer(
            "镜影术持续1分钟/等级，豁免为无。",
            ledger,
        )

        self.assertEqual(issues, ())

    def test_rejects_recommended_spell_without_registered_entry(self) -> None:
        ledger = build("规划法师防御法术")

        issues = validate_pf1e_fact_answer("建议准备石肤术（4环）。", ledger)

        self.assertEqual([issue.code for issue in issues], ["unsupported_named_spell"])
        self.assertEqual(issues[0].actual, "石肤术")


if __name__ == "__main__":
    unittest.main()
