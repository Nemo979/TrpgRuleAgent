import json
import unittest

from trpg_app.conversation_state import ConversationState


class ConversationStateTest(unittest.TestCase):
    def test_tracks_structured_character_state_across_four_user_rounds(self) -> None:
        state = ConversationState.from_messages(
            [
                {"role": "user", "content": "我想创建一个角色，5级法师，种族为精灵"},
                {
                    "role": "assistant",
                    "content": "你可以考虑法师5/战士1，并兼职1级战士。",
                },
                {"role": "user", "content": "我改成法师5/战士1，兼职1级战士"},
                {"role": "user", "content": "我想提高生存能力，不考虑近战"},
                {"role": "user", "content": "继续升法师"},
            ]
        )

        self.assertEqual(state.task, "创建角色")
        self.assertEqual(state.characterLevel, 6)
        self.assertEqual(state.classLevels, {"法师": 5, "战士": 1})
        self.assertEqual(state.plannedDipLevels, {"战士": 1})
        self.assertEqual(state.plannedClassLevels, {"法师": None})
        self.assertEqual(state.rolePreference, "不考虑近战")
        self.assertEqual(state.race, "精灵")
        self.assertEqual(state.facts["种族"], "精灵")

    def test_recognizes_level_notations_and_replaces_previous_class_choice(self) -> None:
        state = ConversationState.from_messages(
            [
                {"role": "user", "content": "5级法师"},
                {"role": "user", "content": "法师3"},
                {"role": "user", "content": "法师5"},
                {"role": "user", "content": "法师5/战士1"},
            ]
        )

        self.assertEqual(state.classLevels, {"法师": 5, "战士": 1})
        self.assertEqual(state.characterLevel, 6)

        state.observe("法师5")
        self.assertEqual(state.classLevels, {"法师": 5})
        self.assertEqual(state.characterLevel, 5)

    def test_extracts_remaining_structured_fields(self) -> None:
        state = ConversationState.from_messages(
            [
                {
                    "role": "user",
                    "content": (
                        "种族为精灵，力量18，敏捷14，"
                        "专长为警觉，法术为火球术、护盾术"
                    ),
                }
            ]
        )

        self.assertEqual(state.race, "精灵")
        self.assertEqual(state.abilityScores, {"力量": 18, "敏捷": 14})
        self.assertEqual(state.feats, ["警觉"])
        self.assertEqual(state.spells, ["火球术", "护盾术"])

    def test_prompt_context_contains_structured_state_and_legacy_facts(self) -> None:
        state = ConversationState.from_messages(
            [{"role": "user", "content": "5级法师，种族为精灵"}]
        )

        context = json.loads(state.prompt_context())
        self.assertEqual(context["characterLevel"], 5)
        self.assertEqual(context["classLevels"], {"法师": 5})
        self.assertEqual(context["race"], "精灵")
        self.assertEqual(context["facts"]["种族"], "精灵")

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

    def test_answer_guidance_separates_target_from_parallel_state(self) -> None:
        state = ConversationState(
            task="创建角色",
            facts={"真身": "猫", "基本特技": "一团毛球"},
        )

        guidance = state.answer_guidance(
            "我选择基本特性为一团毛球，我现在可以选择什么弱点？"
        )

        self.assertIn("本题目标字段：弱点", guidance)
        self.assertIn('"真身": "猫"', guidance)
        self.assertIn('"基本特技": "一团毛球"', guidance)
        self.assertIn("其他并列状态不缩小目标字段范围", guidance)

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
            [
                {
                    "role": "assistant",
                    "content": (
                        "你可以选择猫作为真身，5级法师，兼职1级战士；"
                        "我想提高生存能力，不考虑近战，继续升法师。"
                    ),
                }
            ]
        )
        self.assertEqual(state.facts, {})
        self.assertIsNone(state.characterLevel)
        self.assertEqual(state.classLevels, {})
        self.assertEqual(state.plannedClassLevels, {})
        self.assertEqual(state.plannedDipLevels, {})
        self.assertIsNone(state.rolePreference)
        self.assertIsNone(state.race)
        self.assertEqual(state.abilityScores, {})
        self.assertEqual(state.feats, [])
        self.assertEqual(state.spells, [])


if __name__ == "__main__":
    unittest.main()
