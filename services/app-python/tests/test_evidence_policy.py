from __future__ import annotations

import unittest

from trpg_app.context_budget import ContextBudget
from trpg_app.conversation_state import ConversationState
from trpg_app.evidence_policy import EvidenceBudgetPolicy
from trpg_app.query_intent import QueryIntent, QueryPlan


class EvidenceBudgetPolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.context = ContextBudget.allocate(
            context_window_tokens=128_000,
            output_reserve_tokens=8_192,
            system_tokens=600,
            state_tokens=100,
        )
        self.state = ConversationState()

    def profile(self, intent: QueryIntent):
        return EvidenceBudgetPolicy.allocate(
            intent,
            self.context,
            QueryPlan(intent, ("query",)),
            self.state,
        )

    def test_fact_profile_is_smaller_than_build_without_exceeding_context(self) -> None:
        fact = self.profile(QueryIntent.RULE_FACT)
        build = self.profile(QueryIntent.BUILD_ADVICE)

        self.assertLess(fact.max_searches, build.max_searches)
        self.assertLess(fact.max_answer_documents, build.max_answer_documents)
        self.assertLess(fact.max_evidence_tokens, build.max_evidence_tokens)
        self.assertLessEqual(build.max_evidence_tokens, self.context.evidence_tokens)

    def test_anaphoric_fact_follow_up_gets_intermediate_budget(self) -> None:
        profile = EvidenceBudgetPolicy.allocate(
            QueryIntent.RULE_FACT,
            self.context,
            QueryPlan(QueryIntent.RULE_FACT, ("那它可以同时获得两个血统奥秘吗？",)),
            self.state,
        )

        self.assertEqual(profile.max_searches, 5)
        self.assertEqual(profile.max_answer_documents, 6)
        self.assertEqual(profile.max_evidence_tokens, 24_000)

    def test_compare_and_build_reserve_multiple_topics(self) -> None:
        for intent in (QueryIntent.COMPARE_OPTIONS, QueryIntent.BUILD_ADVICE):
            with self.subTest(intent=intent):
                profile = self.profile(intent)
                self.assertGreaterEqual(len(profile.topic_allocations), 3)
                self.assertAlmostEqual(sum(profile.topic_allocations.values()), 1.0)
                self.assertLess(max(profile.topic_allocations.values()), 0.5)

    def test_small_context_caps_dynamic_profile(self) -> None:
        context = ContextBudget.allocate(
            context_window_tokens=2_048,
            output_reserve_tokens=512,
            system_tokens=500,
            state_tokens=200,
        )
        profile = EvidenceBudgetPolicy.allocate(
            QueryIntent.BUILD_ADVICE,
            context,
            QueryPlan(QueryIntent.BUILD_ADVICE, ("query",)),
            self.state,
        )
        self.assertLessEqual(profile.max_evidence_tokens, context.evidence_tokens)

    def test_fixed_profile_preserves_existing_limits(self) -> None:
        profile = EvidenceBudgetPolicy.fixed(self.context)

        self.assertEqual(profile.max_searches, 6)
        self.assertEqual(profile.max_answer_documents, 8)
        self.assertEqual(profile.max_evidence_tokens, self.context.evidence_tokens)
        self.assertEqual(profile.policy_version, 0)

    def test_compare_documents_are_attributed_to_both_options(self) -> None:
        profile = self.profile(QueryIntent.COMPARE_OPTIONS)
        original = "全防御和以标准动作进行防御式战斗有什么区别？"

        left = EvidenceBudgetPolicy.classify_topic(
            QueryIntent.COMPARE_OPTIONS,
            original,
            "全防御",
            {"title": "全防御", "content": "AC获得+4闪避加值"},
            profile,
        )
        right = EvidenceBudgetPolicy.classify_topic(
            QueryIntent.COMPARE_OPTIONS,
            original,
            "防御式战斗",
            {"title": "攻击（Attack）", "content": "以标准动作进行防御式战斗"},
            profile,
        )

        self.assertEqual(left, "option_a")
        self.assertEqual(right, "option_b")

    def test_compare_missing_topic_prompt_names_unread_side(self) -> None:
        prompt = EvidenceBudgetPolicy.missing_topic_prompt(
            QueryIntent.COMPARE_OPTIONS,
            "全防御和以标准动作进行防御式战斗有什么区别？",
            {"option_a"},
        )

        self.assertIsNotNone(prompt)
        self.assertIn("防御式战斗", prompt)
        self.assertNotIn("全防御、", prompt)
        self.assertIsNone(
            EvidenceBudgetPolicy.missing_topic_prompt(
                QueryIntent.COMPARE_OPTIONS,
                "全防御和以标准动作进行防御式战斗有什么区别？",
                {"option_a", "option_b"},
            )
        )
        self.assertEqual(
            EvidenceBudgetPolicy.missing_topic_queries(
                QueryIntent.COMPARE_OPTIONS,
                "全防御和以标准动作进行防御式战斗有什么区别？",
                {"option_a"},
            ),
            ("攻击（Attack）",),
        )

    def test_compare_canonical_attack_title_maps_to_defensive_fighting(self) -> None:
        profile = self.profile(QueryIntent.COMPARE_OPTIONS)

        topic = EvidenceBudgetPolicy.classify_topic(
            QueryIntent.COMPARE_OPTIONS,
            "全防御和以标准动作进行防御式战斗有什么区别？",
            "攻击（Attack）",
            {
                "title": "攻击（Attack）",
                "content": "防御式战斗时攻击检定承受减值。",
            },
            profile,
        )

        self.assertEqual(topic, "option_b")


if __name__ == "__main__":
    unittest.main()
