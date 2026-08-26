from __future__ import annotations

import json
import unittest

from trpg_app.fact_ledger_repair import (
    AnswerClaim,
    AnswerDraft,
    AnswerSection,
    apply_repair_patch,
    build_repair_targets,
    parse_answer_draft,
    parse_repair_patch,
    render_answer_draft_for_validation,
)
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

        self.assertEqual(ledger.adapter_version, 16)
        self.assertEqual(
            PF1E_FACT_LEDGER_ADAPTER.required_draft_paths(ledger),
            (
                "answer.build.levels",
                "answer.summary[goal_facts]",
                "answer.summary[feat_eligibility]",
                "answer.summary[spell_progression]",
            ),
        )
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
        self.assertEqual(data.feats["大顺势斩"].effect, "连续额外攻击")
        self.assertEqual(dict(data.spells["镜影术"].class_levels)["法师"], 2)
        self.assertEqual(data.spells["法师护甲"].school, "咒法系")

    def test_parses_bounded_narrative_feat_entries(self) -> None:
        ledger = build_pf1e_fact_ledger(
            "选择稳健步伐专长",
            [
                (
                    "S1",
                    {
                        "title": "专长",
                        "content": (
                            "专长\n稳健步伐（Steady Step）〔战斗〕\n"
                            "你经过了稳固的训练。\n"
                            "先决条件：力量13。\n"
                            "专长效果：你站得更稳。"
                        ),
                    },
                )
            ],
        )

        fact = ledger.adapter_data.feats["稳健步伐"]
        self.assertEqual(fact.prerequisites, "力量13。")
        self.assertEqual(fact.effect, "")
        self.assertEqual(fact.category, "战斗")
        self.assertEqual(fact.source_label, "S1")

    def test_goal_named_facts_are_server_owned_from_registered_evidence(self) -> None:
        fighter = FIGHTER + (
            "\n武器和防具擅长：战士擅长使用所有的简易武器和军用武器，"
            "同时擅长所有类型盔甲（重甲，中甲，轻甲）和盾牌（包括塔盾）。\n"
        )
        mage_armor = MAGE_ARMOR + (
            "\n法师护甲对AC提供+4护甲加值。法师护甲没有防具检定减值、"
            "没有奥术失败率、也不会降低速度。\n"
        )
        ledger = build_pf1e_fact_ledger(
            "创建1到5级人类战士，确认职业擅长并用法师护甲比较装备",
            [
                ("S1", {"title": "战士", "content": fighter}),
                ("S2", {"title": "人类", "content": HUMAN}),
                ("S3", {"title": "法师护甲 (Mage Armor)", "content": mage_armor}),
            ],
        )
        contracts = {
            item.path: item
            for item in PF1E_FACT_LEDGER_ADAPTER.draft_claim_contracts(ledger)
        }
        goal_facts = contracts["answer.summary[goal_facts]"]

        self.assertIn("人类角色在1级获得一个额外专长", goal_facts.server_text)
        self.assertIn("所有简易武器、军用武器", goal_facts.server_text)
        self.assertIn("+4护甲加值", goal_facts.server_text)
        self.assertIn("没有奥术失败率", goal_facts.server_text)
        self.assertEqual(goal_facts.evidence_refs, ("S2", "S1", "S3"))

    def test_inline_spell_table_publishes_grease_and_spell_focus_facts(self) -> None:
        grease = (
            "油腻术 (Grease)\n\n"
            "| 学派 咒法系 (创造) 环位 术士/法师 1 施法时间 标准动作 "
            "持续时间 1分钟/等级 (可解消) 豁免 见后文 法术抗力 否 |\n"
            "油腻术区域内的生物必须进行一次反射豁免，否则倒地。"
        )
        spell_focus = (
            "全专长列表\n"
            "| Spell Focus 法术专攻 | | 选定学派法术豁免DC+1 | | CRB |"
        )
        ledger = build_pf1e_fact_ledger(
            "核对油腻术的豁免与持续规则并选择配套专长",
            [
                ("S1", {"title": "油腻术 (Grease)", "content": grease}),
                ("S2", {"title": "全专长列表", "content": spell_focus}),
            ],
        )
        contracts = {
            item.path: item
            for item in PF1E_FACT_LEDGER_ADAPTER.draft_claim_contracts(ledger)
        }
        goal_facts = contracts["answer.summary[goal_facts]"]

        self.assertIn("油腻术属于咒法系", goal_facts.server_text)
        self.assertIn("1分钟/等级", goal_facts.server_text)
        self.assertIn("反射豁免", goal_facts.server_text)
        self.assertIn("法术专攻（咒法）", goal_facts.server_text)
        self.assertIn("豁免DC+1", goal_facts.server_text)
        self.assertEqual(goal_facts.evidence_refs, ("S1", "S2"))

    def test_feat_comparison_does_not_require_a_full_level_timeline(self) -> None:
        ledger = build("规划1到5级人类战士并比较专长与装备收益")

        self.assertFalse(
            any(
                path.startswith("answer.levels[") and path.endswith("].feats")
                for path in PF1E_FACT_LEDGER_ADAPTER.required_draft_paths(ledger)
            )
        )
        self.assertEqual(
            validate_pf1e_fact_answer("比较战士专长与装备收益。", ledger),
            (),
        )

    def test_negated_prestige_bab_claim_is_allowed_and_model_slots_filter_bab(self) -> None:
        prestige = (
            "奥法骑士\n进阶要求\n"
            "擅长武器：角色必须擅长所有军用武器。\n"
            "施法：角色必须能够施展3级奥术。\n本职技能\n"
        )
        ledger = build_pf1e_fact_ledger(
            "规划5到10级法师进入奥法骑士，必须先核对基础攻击加值与进阶条件",
            [("S1", {"title": "奥法骑士", "content": prestige})],
        )
        contracts = {
            item.path: item
            for item in PF1E_FACT_LEDGER_ADAPTER.draft_claim_contracts(ledger)
        }

        self.assertIn("基础攻击", contracts["answer.build.levels"].forbidden_terms)
        self.assertIn(
            "已读奥法骑士进阶要求不包含BAB",
            contracts["answer.summary[goal_facts]"].server_text,
        )
        self.assertEqual(
            validate_pf1e_fact_answer(
                "已读奥法骑士进阶要求不包含BAB或基础攻击加值门槛。",
                ledger,
            ),
            (),
        )

    def test_feat_slot_with_insufficient_catalog_is_server_owned(self) -> None:
        ledger = build("创建1到5级人类战士专长升级路线")
        data = ledger.adapter_data
        data.feats = dict(tuple(data.feats.items())[:2])

        by_path = {
            item.path: item
            for item in PF1E_FACT_LEDGER_ADAPTER.draft_claim_contracts(ledger)
        }

        level_one = by_path["answer.levels[1].feats"]
        self.assertEqual(level_one.selection_count, 0)
        self.assertIn("不能安全生成具体选择", level_one.server_text)
        rendered = render_answer_draft_for_validation(
            AnswerDraft(
                (
                    AnswerSection(
                        "rules",
                        "规则",
                        (
                            AnswerClaim(
                                "feat",
                                "answer.levels[1].feats",
                                level_one.server_text,
                                level_one.evidence_refs,
                            ),
                        ),
                    ),
                )
            )
        )
        self.assertFalse(
            any(
                issue.code == "feat_timeline_slot_count"
                and issue.path == "answer.levels[1].feats"
                for issue in validate_pf1e_fact_answer(rendered, ledger)
            )
        )

    def test_publishes_exact_typed_feat_claim_contracts(self) -> None:
        ledger = build("力量13，创建1到5级人类战士专长升级路线")

        contracts = PF1E_FACT_LEDGER_ADAPTER.draft_claim_contracts(ledger)

        self.assertEqual(
            tuple(item.path for item in contracts),
            PF1E_FACT_LEDGER_ADAPTER.required_draft_paths(ledger),
        )
        by_path = {item.path: item for item in contracts}
        level_one = by_path["answer.levels[1].feats"]
        self.assertEqual(level_one.selection_count, 3)
        self.assertEqual(level_one.evidence_refs, ("S1", "S2", "S3"))
        self.assertIn("猛力攻击", {item.value for item in level_one.selection_options})
        self.assertTrue(
            all(len(group.option_values) <= 40 for group in level_one.selection_groups)
        )
        self.assertEqual(
            by_path["answer.levels[2].feats"].selection_count,
            1,
        )
        self.assertEqual(
            by_path["answer.summary[feat_eligibility]"].selection_count,
            0,
        )
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

    def test_typed_feat_draft_deterministically_passes_full_validation(self) -> None:
        ledger = build("力量13，创建1到5级人类战士专长升级路线")
        paths = PF1E_FACT_LEDGER_ADAPTER.required_draft_paths(ledger)
        contracts = PF1E_FACT_LEDGER_ADAPTER.draft_claim_contracts(ledger)
        texts = {
            "answer.build.levels": "角色1到5级均选择战士职业。",
            "answer.summary[feat_eligibility]": "力量为13，按先决条件依次选择专长。",
            "answer.levels[1].feats": ["猛力攻击", "武器专攻", "闪避"],
            "answer.levels[2].feats": ["战斗反射"],
            "answer.levels[3].feats": ["钢铁意志"],
            "answer.levels[4].feats": ["顺势斩"],
            "answer.levels[5].feats": ["大顺势斩"],
        }
        payload = {
            "values": [
                texts[path]
                for path, contract in zip(paths, contracts)
                if not contract.server_text
            ]
        }

        draft = parse_answer_draft(
            json.dumps(payload, ensure_ascii=False),
            ledger.registered_evidence_refs,
            PF1E_FACT_LEDGER_ADAPTER.draft_path_specs(ledger),
            paths,
            contracts,
        )
        rendered = render_answer_draft_for_validation(draft)

        self.assertIn(
            "1级选择专长：普通专长：猛力攻击；战士奖励专长：武器专攻；"
            "人类奖励专长：闪避。",
            rendered,
        )
        self.assertEqual(validate_pf1e_fact_answer(rendered, ledger), ())

    def test_wizard_progression_claim_is_server_owned_and_validates(self) -> None:
        wizard = """表：法师
| 等级 | BAB | 强韧 | 反射 | 意志 | 特殊 | 0环 | 1环 | 2环 | 3环 | 4环 | 5环 | 6环 | 7环 | 8环 | 9环 |
| 5 | +2 | +1 | +1 | +4 | 奖励专长 | 4 | 3 | 2 | 1 | - | - | - | - | - | - |
"""
        ledger = build_pf1e_fact_ledger(
            "规划5到5级法师法术进度",
            [("S1", {"title": "法师", "content": wizard})],
        )
        paths = PF1E_FACT_LEDGER_ADAPTER.required_draft_paths(ledger)
        contracts = PF1E_FACT_LEDGER_ADAPTER.draft_claim_contracts(ledger)
        by_path = {item.path: item for item in contracts}
        spell_path = "answer.levels[5].spells"

        self.assertEqual(
            by_path[spell_path].server_text,
            "5级法师最高可施放3环法术，每日法术位为4/3/2/1（0/1/2/3环）。",
        )
        model_paths = tuple(
            path for path, contract in zip(paths, contracts) if not contract.server_text
        )
        values = [
            "角色5级选择法师职业。" if path == "answer.build.levels" else "规则摘要。"
            for path in model_paths
        ]
        draft = parse_answer_draft(
            json.dumps({"values": values}, ensure_ascii=False),
            ledger.registered_evidence_refs,
            PF1E_FACT_LEDGER_ADAPTER.draft_path_specs(ledger),
            paths,
            contracts,
        )
        rendered = render_answer_draft_for_validation(draft)
        self.assertEqual(validate_pf1e_fact_answer(rendered, ledger), ())

        with self.assertRaisesRegex(ValueError, "count"):
            parse_answer_draft(
                json.dumps({"values": [*values, "伪造服务器法术进度"]}, ensure_ascii=False),
                ledger.registered_evidence_refs,
                PF1E_FACT_LEDGER_ADAPTER.draft_path_specs(ledger),
                paths,
                contracts,
            )

    def test_prestige_spell_advancement_is_server_owned_in_summary(self) -> None:
        ledger = build_pf1e_fact_ledger(
            "规划5到10级法师进入奥法骑士并核对施法进度",
            [
                (
                    "S1",
                    {
                        "title": "奥法骑士",
                        "content": (
                            "进阶要求\n施法：角色必须能够施展3级奥术。\n本职技能\n"
                            "每日法术：从2级开始，奥法骑士每次升级，每日法术数量都会增加，"
                            "就像之前奥术施法职业获得提升一样。"
                        ),
                    },
                )
            ],
        )

        summary = next(
            contract
            for contract in PF1E_FACT_LEDGER_ADAPTER.draft_claim_contracts(ledger)
            if contract.path == "answer.summary[spell_progression]"
        )

        self.assertIn("奥法骑士1级不增加现有奥术施法职业等级", summary.server_text)
        self.assertIn("从2级开始", summary.server_text)
        self.assertEqual(summary.evidence_refs, ("S1",))

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

    def test_claim_path_markers_assign_replacements_to_exact_feat_levels(self) -> None:
        ledger = build("创建1到5级人类战士专长升级路线")
        answer = """
力量13。
[[TRPGCLAIMPATH:answer.levels[1].feats]]
选择专长：猛力攻击，武器专攻，闪避。
[[TRPGCLAIMPATH:answer.levels[2].feats]]
选择专长：战斗反射。
[[TRPGCLAIMPATH:answer.levels[3].feats]]
选择专长：钢铁意志。
[[TRPGCLAIMPATH:answer.levels[4].feats]]
选择专长：顺势斩。
[[TRPGCLAIMPATH:answer.levels[5].feats]]
选择专长：大顺势斩。
"""

        issues = validate_pf1e_fact_answer(answer, ledger)

        self.assertNotIn("feat_timeline_slot_count", [issue.code for issue in issues])

    def test_conditional_attribute_gate_supports_a_conditional_feat_plan(self) -> None:
        ledger = build("规划1到5级战士的猛力攻击专长链")
        answer = """
如果力量达到13，则采用下列专长分支；否则先不选择猛力攻击。
[[TRPGCLAIMPATH:answer.levels[1].feats]]
1级选择专长：猛力攻击、武器专攻。
"""

        issues = validate_pf1e_fact_answer(answer, ledger)

        self.assertNotIn(
            "feat_attribute_prerequisite",
            [issue.code for issue in issues],
        )

    def test_claim_path_marker_targets_unsupported_option_to_original_claim(self) -> None:
        ledger = build("规划1到5级战士专长")

        issues = validate_pf1e_fact_answer(
            "[[TRPGCLAIMPATH:answer.levels[3].feats]]\n"
            "建议选择：虚构专长。",
            ledger,
        )

        unsupported = [
            issue for issue in issues if issue.code == "unsupported_named_option"
        ]
        self.assertEqual(len(unsupported), 1)
        self.assertEqual(unsupported[0].path, "answer.levels[3].feats")

    def test_path_anchored_feat_repair_passes_without_level_text_in_replacement(self) -> None:
        ledger = build("创建1到5级人类战士专长升级路线")
        draft = AnswerDraft(
            (
                AnswerSection(
                    "levels",
                    "等级路线",
                    (
                        AnswerClaim(
                            "level_1",
                            "answer.levels[1].feats",
                            "力量13。选择专长：猛力攻击，武器专攻，闪避。",
                            ("S1", "S2", "S3", "S4"),
                        ),
                        AnswerClaim(
                            "level_2",
                            "answer.levels[2].feats",
                            "选择专长：战斗反射。",
                            ("S2", "S4"),
                        ),
                        AnswerClaim(
                            "level_3",
                            "answer.levels[3].feats",
                            "获得盔甲训练1。",
                            ("S1", "S2"),
                        ),
                        AnswerClaim(
                            "level_4",
                            "answer.levels[4].feats",
                            "选择专长：顺势斩。",
                            ("S2", "S4"),
                        ),
                        AnswerClaim(
                            "level_5",
                            "answer.levels[5].feats",
                            "选择专长：大顺势斩。",
                            ("S1", "S4"),
                        ),
                    ),
                ),
            )
        )
        issues = tuple(
            issue
            for issue in validate_pf1e_fact_answer(
                render_answer_draft_for_validation(draft),
                ledger,
            )
            if issue.code == "feat_timeline_slot_count"
        )
        self.assertEqual([issue.path for issue in issues], ["answer.levels[3].feats"])

        targets = build_repair_targets(draft, issues)
        patch = parse_repair_patch(
            '{"values":["选择专长：钢铁意志。"]}',
            ledger.registered_evidence_refs,
            targets,
        )
        repaired = apply_repair_patch(draft, patch, targets)
        remaining = validate_pf1e_fact_answer(
            render_answer_draft_for_validation(repaired),
            ledger,
        )

        self.assertNotIn(
            "feat_timeline_slot_count",
            [issue.code for issue in remaining],
        )

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
