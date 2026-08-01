import unittest

from trpg_app.conversation_state import ConversationState


class ConversationStateTest(unittest.TestCase):
    def test_extracts_explicit_character_creation_choices(self) -> None:
        state = ConversationState.from_messages(
            [
                {"role": "user", "content": "我想创建一个角色"},
                {"role": "assistant", "content": "请选择真身"},
                {"role": "user", "content": "我现在选择猫作为真身"},
                {"role": "user", "content": "我选择基本特性为一团毛球"},
            ]
        )

        self.assertEqual(state.task, "创建角色")
        self.assertEqual(state.facts, {"真身": "猫", "基本特技": "一团毛球"})

    def test_enriches_related_follow_up_but_not_general_next_step(self) -> None:
        state = ConversationState(task="创建角色", facts={"真身": "猫"})

        self.assertEqual(
            state.enrich_search_query("弱点", "我现在可以选择什么弱点"),
            "我现在可以选择什么弱点 猫",
        )
        self.assertEqual(
            state.enrich_search_query("下一步", "创建角色的下一步是什么"),
            "创建角色的下一步是什么",
        )

    def test_selection_query_includes_existing_entity_and_new_choice(self) -> None:
        state = ConversationState(
            task="创建角色",
            facts={"真身": "猫", "基本特技": "一团毛球"},
        )
        query = state.enrich_search_query(
            "一团毛球",
            "我选择基本特性为一团毛球",
        )
        self.assertIn("猫", query)
        self.assertIn("基本特技", query)

    def test_entity_lookup_keeps_the_active_task_scope(self) -> None:
        state = ConversationState(task="创建角色")

        self.assertEqual(
            state.enrich_search_query("真身种类", "有哪几种真身？"),
            "有哪几种真身？ 真身种类 创建角色",
        )

    def test_unscoped_entity_lookup_does_not_invent_a_task(self) -> None:
        state = ConversationState()

        self.assertEqual(
            state.enrich_search_query("真身种类", "有哪几种真身？"),
            "真身种类",
        )

    def test_extracts_multiple_assignments_from_one_statement(self) -> None:
        state = ConversationState.from_messages(
            [
                {
                    "role": "user",
                    "content": "我选择猫为真身，基本特性为一团毛球",
                }
            ]
        )
        self.assertEqual(state.facts, {"真身": "猫", "基本特技": "一团毛球"})

    def test_does_not_infer_facts_from_assistant_messages(self) -> None:
        state = ConversationState.from_messages(
            [{"role": "assistant", "content": "你可以选择猫作为真身"}]
        )
        self.assertEqual(state.facts, {})


if __name__ == "__main__":
    unittest.main()
