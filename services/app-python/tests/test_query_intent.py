import unittest

from trpg_app.conversation_state import ConversationState
from trpg_app.query_intent import QueryIntent, build_query_plan, classify_intent


class QueryIntentTest(unittest.TestCase):
    def test_classifies_the_four_supported_intents(self) -> None:
        self.assertEqual(classify_intent("油腻术解释一下"), QueryIntent.RULE_FACT)
        self.assertEqual(classify_intent("如何创建角色"), QueryIntent.PROCEDURE)
        self.assertEqual(classify_intent("法师和术士有什么区别"), QueryIntent.COMPARE_OPTIONS)
        self.assertEqual(classify_intent("兼职规则是什么"), QueryIntent.RULE_FACT)
        self.assertEqual(classify_intent("5级法师怎么BD"), QueryIntent.BUILD_ADVICE)

    def test_build_plan_decomposes_independent_rule_questions(self) -> None:
        state = ConversationState.from_messages([
            {"role": "user", "content": "5级法师怎么BD"},
            {"role": "user", "content": "兼职1级战士，我主要想提高生存能力"},
        ])
        plan = build_query_plan("继续升法师和兼职相比损失什么", state)
        self.assertEqual(plan.intent, QueryIntent.BUILD_ADVICE)
        self.assertEqual(len(plan.queries), 3)
        self.assertTrue(all("法师5" in query for query in plan.queries))
        self.assertTrue(all("战士1" in query for query in plan.queries))
        self.assertTrue(all("生存" in query for query in plan.queries))

    def test_rule_fact_plan_leads_with_compact_entry_name(self) -> None:
        plan = build_query_plan(
            "混血术士选择两种血统后，在可知法术和意志豁免上有什么缺陷？"
        )

        self.assertEqual(plan.intent, QueryIntent.RULE_FACT)
        self.assertEqual(plan.queries[0], "混血术士")
        self.assertEqual(
            plan.queries[1],
            "混血术士选择两种血统后，在可知法术和意志豁免上有什么缺陷？",
        )

    def test_rule_fact_focus_handles_common_leading_conditions(self) -> None:
        self.assertEqual(
            build_query_plan("有双武器格斗专长且副手是轻型武器时，减值多少？").queries[0],
            "双武器格斗",
        )
        self.assertEqual(
            build_query_plan("操纸师把卷轴作为卷轴刃时，硬度如何计算？").queries[0],
            "操纸师",
        )

    def test_attribute_table_question_keeps_the_full_query(self) -> None:
        query = "两寸厚铁门的硬度、生命值和破坏DC分别是多少？"

        self.assertEqual(build_query_plan(query).queries, (query,))


if __name__ == "__main__":
    unittest.main()
