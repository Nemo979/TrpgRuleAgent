import unittest
from pathlib import Path

from trpg_app.answer_evaluation import load_cases
from trpg_app.query_decomposition import (
    QueryComplexity,
    decomposition_search_query,
    decompose_query,
    route_query,
)


class QueryDecompositionTest(unittest.TestCase):
    def test_keeps_single_rule_fact_on_direct_loop(self) -> None:
        decision = route_query("借机攻击在什么情况下触发？")
        decomposition = decompose_query("借机攻击在什么情况下触发？", decision)

        self.assertEqual(decision.complexity, QueryComplexity.SIMPLE)
        self.assertFalse(decision.need_decomposition)
        self.assertFalse(decision.need_planner)
        self.assertEqual(decomposition.questions, ())

    def test_does_not_split_fields_from_one_rule_table(self) -> None:
        decision = route_query("两寸厚铁门的硬度、生命值和破坏DC分别是多少？")

        self.assertEqual(decision.complexity, QueryComplexity.SIMPLE)
        self.assertFalse(decision.need_decomposition)

    def test_splits_explicit_independent_questions(self) -> None:
        query = "借机攻击何时触发？准备动作如何使用？油腻术允许哪些豁免？"
        decision = route_query(query)
        decomposition = decompose_query(query, decision)

        self.assertEqual(decision.complexity, QueryComplexity.COMPOUND)
        self.assertTrue(decision.need_decomposition)
        self.assertEqual([item.id for item in decomposition.questions], ["q1", "q2", "q3"])
        self.assertEqual(
            [item.question for item in decomposition.questions],
            ["借机攻击何时触发？", "准备动作如何使用？", "油腻术允许哪些豁免？"],
        )

    def test_compacts_ready_action_recovery_query(self) -> None:
        query = "借机攻击何时触发？怎样准备动作攻击施法者并打断他的法术？"
        decomposition = decompose_query(query, route_query(query))

        self.assertEqual(
            decomposition_search_query(decomposition.questions[1]),
            "准备动作 打断施法",
        )

    def test_classifies_independent_constrained_build_without_planner(self) -> None:
        query = (
            "我想构筑1到10级的近战法师，必须保持施法能力，"
            "请规划职业、专长和法术，并兼顾前期生存。"
        )
        decision = route_query(query)
        decomposition = decompose_query(query, decision)

        self.assertEqual(decision.complexity, QueryComplexity.COMPLEX)
        self.assertTrue(decision.need_decomposition)
        self.assertFalse(decision.need_planner)
        self.assertIn("class", decision.domains)
        self.assertIn("feat", decision.domains)
        self.assertIn("spell", decision.domains)
        self.assertIn("progression", decision.domains)
        self.assertLessEqual(len(decomposition.questions), 4)

    def test_complex_query_prefers_domains_over_semicolon_clauses(self) -> None:
        query = (
            "我想规划5到9级法师的防御装备，必须先确认护甲擅长与奥术失败率；"
            "如果装备不会破坏施法再比较盾牌和护甲，否则改用法师护甲并安排升级。"
        )
        decomposition = decompose_query(query, route_query(query))

        self.assertEqual(
            [question.domain for question in decomposition.questions],
            ["class", "progression", "spell", "equipment"],
        )
        self.assertEqual(
            decomposition_search_query(decomposition.questions[2]),
            "法师护甲",
        )

    def test_compacts_named_complex_prerequisite_queries(self) -> None:
        prestige = (
            "我想规划5到10级法师进入奥法骑士，必须先核对武器擅长和施法条件，"
            "并安排专长与升级。"
        )
        prestige_questions = decompose_query(prestige, route_query(prestige)).questions
        self.assertEqual(
            decomposition_search_query(prestige_questions[0]),
            "奥法骑士 进阶要求 所有军用武器 3级奥术",
        )

        feat_chain = (
            "我想规划1到10级战士，必须核对猛力攻击、顺势斩和大顺势斩专长，"
            "并安排升级。"
        )
        feat_question = next(
            question
            for question in decompose_query(feat_chain, route_query(feat_chain)).questions
            if question.domain == "feat"
        )
        self.assertEqual(
            decomposition_search_query(feat_question),
            "大顺势斩（Great Cleave） BAB+4",
        )

    def test_multiclass_progression_query_keeps_advancement_terms(self) -> None:
        query = (
            "我想规划6到12级法师兼职战士，必须保持施法进度，"
            "并比较专长和装备。"
        )
        progression = next(
            question
            for question in decompose_query(query, route_query(query)).questions
            if question.domain == "progression"
        )

        self.assertIn("角色升级", progression.question)
        self.assertIn("兼职", progression.question)
        self.assertIn("每级选择职业", progression.question)

    def test_multiclass_spell_query_keeps_casting_progression_terms(self) -> None:
        query = (
            "我想规划6到12级法师兼职战士，必须保持指定法术环级；"
            "先计算兼职造成的施法进度变化，再根据是否满足环级决定兼职等级。"
        )
        decision = route_query(query)
        decomposition = decompose_query(query, decision)

        spell = next(question for question in decomposition.questions if question.domain == "spell")
        self.assertIn("法师", spell.question)
        self.assertIn("施法进度", spell.question)
        self.assertIn("法术环级", spell.question)

    def test_caps_explicit_subquestions_at_four(self) -> None:
        query = "职业怎么选？种族怎么选？专长怎么选？法术怎么选？装备怎么选？"
        decision = route_query(query)
        decomposition = decompose_query(query, decision)

        self.assertTrue(decision.need_decomposition)
        self.assertEqual(len(decomposition.questions), 4)

    def test_splits_comparison_sides_inside_a_compound_question(self) -> None:
        query = "全防御和防御式战斗分别提供什么加值与减值？铁门的硬度是多少？"
        decision = route_query(query)
        decomposition = decompose_query(query, decision)

        self.assertEqual(
            [item.question for item in decomposition.questions],
            [
                "全防御的规则效果是什么？",
                "防御式战斗的规则效果是什么？",
                "铁门的硬度是多少？",
            ],
        )

    def test_keeps_object_table_fields_together_inside_compound_question(self) -> None:
        query = "全防御和防御式战斗分别提供什么加值与减值？两寸厚铁门的硬度、生命值和破坏DC分别是多少？"
        decomposition = decompose_query(query, route_query(query))

        self.assertEqual(
            [item.question for item in decomposition.questions],
            [
                "全防御的规则效果是什么？",
                "防御式战斗的规则效果是什么？",
                "两寸厚铁门的硬度、生命值和破坏DC分别是多少？",
            ],
        )
        self.assertEqual(
            decomposition_search_query(decomposition.questions[2]),
            "铁门 硬度 生命值 破坏DC",
        )

    def test_uses_exact_named_spell_queries(self) -> None:
        compound = "借机攻击何时触发？油腻术对区域内生物要求什么豁免？"
        compound_questions = decompose_query(compound, route_query(compound)).questions
        self.assertEqual(decomposition_search_query(compound_questions[1]), "油腻术")

        build = (
            "我想规划5到10级法师并提高生存能力，必须保持施法进度："
            "请说明职业升级与兼职取舍、适合的防御专长方向和法师护甲法术的规则依据。"
        )
        spell_question = next(
            item
            for item in decompose_query(build, route_query(build)).questions
            if item.domain == "spell"
        )
        self.assertEqual(decomposition_search_query(spell_question), "法师护甲")

    def test_stage_two_answer_cases_all_enter_bounded_decomposition(self) -> None:
        cases = load_cases(
            Path("rulepacks/pathfinder-1e/evals/query-decomposition-answer-cases.jsonl")
        )

        for case in cases:
            with self.subTest(case=case.id):
                query = case.turns[0].query
                decision = route_query(query)
                decomposition = decompose_query(query, decision)
                self.assertTrue(decision.need_decomposition)
                self.assertGreaterEqual(len(decomposition.questions), 2)
                self.assertLessEqual(len(decomposition.questions), 4)
                if case.id == "complex-wizard-progression":
                    spell_question = next(
                        item.question
                        for item in decomposition.questions
                        if item.domain == "spell"
                    )
                    self.assertIn("法师护甲", spell_question)
                    self.assertLess(len(spell_question), len(query))


if __name__ == "__main__":
    unittest.main()
