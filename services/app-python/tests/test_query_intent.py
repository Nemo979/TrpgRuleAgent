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


if __name__ == "__main__":
    unittest.main()
