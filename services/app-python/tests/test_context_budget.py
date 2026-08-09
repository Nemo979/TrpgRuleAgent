from __future__ import annotations

import json
import unittest

from trpg_app.context_budget import ContextBudget


class ContextBudgetTest(unittest.TestCase):
    def test_allocates_non_overlapping_input_reserves(self) -> None:
        budget = ContextBudget.allocate(
            context_window_tokens=128_000,
            output_reserve_tokens=8_192,
            system_tokens=500,
            state_tokens=100,
        )

        self.assertEqual(budget.evidence_tokens, 39_936)
        self.assertEqual(budget.decision_tool_tokens, 47_936)
        self.assertEqual(budget.recent_history_tokens, 71_272)
        self.assertLessEqual(
            budget.output_reserve_tokens
            + budget.system_tokens
            + budget.state_tokens
            + budget.decision_tool_tokens
            + budget.recent_history_tokens,
            budget.context_window_tokens,
        )

    def test_trims_old_history_by_token_estimate_and_keeps_latest(self) -> None:
        budget = ContextBudget.allocate(
            context_window_tokens=4_096,
            output_reserve_tokens=1_024,
            system_tokens=100,
            state_tokens=0,
        )
        budget.recent_history_tokens = 20
        messages = [
            {"role": "user", "content": "旧消息" * 30},
            {"role": "assistant", "content": "旧回答" * 30},
            {"role": "user", "content": "最新问题"},
        ]

        kept, dropped = budget.trim_recent_messages(messages)

        self.assertEqual(kept, [{"role": "user", "content": "最新问题"}])
        self.assertEqual(dropped, 2)

    def test_small_context_allocation_never_exceeds_the_window(self) -> None:
        budget = ContextBudget.allocate(
            context_window_tokens=4_096,
            output_reserve_tokens=1_024,
            system_tokens=100,
            state_tokens=0,
        )

        self.assertLessEqual(budget.evidence_tokens, budget.decision_tool_tokens)
        self.assertLessEqual(
            budget.output_reserve_tokens
            + budget.system_tokens
            + budget.state_tokens
            + budget.decision_tool_tokens
            + budget.recent_history_tokens,
            budget.context_window_tokens,
        )

    def test_compacts_old_tool_results_but_keeps_latest_batch_full(self) -> None:
        budget = ContextBudget.allocate(
            context_window_tokens=128_000,
            output_reserve_tokens=8_192,
            system_tokens=500,
            state_tokens=0,
        )
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "question"},
            {"role": "assistant", "tool_calls": [{"id": "search-1"}]},
            {
                "role": "tool",
                "tool_call_id": "search-1",
                "content": json.dumps([{"id": "doc-1", "excerpt": "very long excerpt" * 200}]),
            },
            {"role": "assistant", "tool_calls": [{"id": "read-1"}]},
            {
                "role": "tool",
                "tool_call_id": "read-1",
                "content": json.dumps([{"id": "doc-1", "citation": "S1", "content": "full evidence"}]),
            },
        ]

        prepared = budget.prepare_decision_messages(messages)

        old_result = json.loads(prepared[3]["content"])
        self.assertEqual(old_result["status"], "compacted_search_results")
        self.assertEqual(old_result["resultIds"], ["doc-1"])
        self.assertNotIn("very long excerpt", prepared[3]["content"])
        self.assertEqual(prepared[5]["content"], messages[5]["content"])
        self.assertEqual(budget.compacted_tool_messages, 1)
        self.assertLess(
            budget.decision_tool_tokens_after_max,
            budget.decision_tool_tokens_before_max,
        )

    def test_keeps_all_results_in_latest_parallel_tool_batch(self) -> None:
        budget = ContextBudget.allocate(
            context_window_tokens=32_000,
            output_reserve_tokens=2_048,
            system_tokens=500,
            state_tokens=0,
        )
        messages = [
            {"role": "assistant", "tool_calls": [{"id": "a"}, {"id": "b"}]},
            {"role": "tool", "tool_call_id": "a", "content": '[{"id":"doc","excerpt":"full"}]'},
            {"role": "tool", "tool_call_id": "b", "content": "skipped"},
        ]

        prepared = budget.prepare_decision_messages(messages)

        self.assertEqual(prepared[1]["content"], messages[1]["content"])
        self.assertEqual(prepared[2]["content"], "skipped")
        self.assertEqual(budget.compacted_tool_messages, 0)


if __name__ == "__main__":
    unittest.main()
