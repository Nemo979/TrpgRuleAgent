import unittest

from trpg_app.observability import (
    TurnPhaseTimer,
    estimate_tokens,
    summarize_messages,
)


class TokenEstimateTest(unittest.TestCase):
    def test_cjk_text_estimates_four_characters_per_token(self) -> None:
        self.assertEqual(estimate_tokens("油腻术" * 4), 3)
        self.assertEqual(estimate_tokens(""), 0)

    def test_short_text_has_at_least_one_token(self) -> None:
        self.assertEqual(estimate_tokens("借机"), 1)

    def test_summarize_messages_counts_roles_and_tokens(self) -> None:
        summary = summarize_messages(
            [
                {"role": "user", "content": "油腻术是什么"},
                {"role": "assistant", "content": "油腻术" * 12},
            ]
        )
        self.assertEqual(summary["messageCounts"], {"user": 1, "assistant": 1})
        self.assertEqual(summary["tokens"], estimate_tokens("油腻术是什么") + estimate_tokens("油腻术" * 12))


class TurnPhaseTimerTest(unittest.TestCase):
    def test_accumulates_phases(self) -> None:
        timer = TurnPhaseTimer()
        timer.decision_seconds += 0.5
        timer.retrieval_seconds += 0.2
        timer.read_seconds += 0.1
        timer.final_generation_seconds += 1.2

        rounded = timer.round_all()

        self.assertEqual(rounded["decision"], 0.5)
        self.assertEqual(rounded["retrieval"], 0.2)
        self.assertEqual(rounded["read"], 0.1)
        self.assertEqual(rounded["finalGeneration"], 1.2)
        self.assertGreaterEqual(rounded["total"], 0.0)
        self.assertLess(rounded["total"], 60.0)


if __name__ == "__main__":
    unittest.main()
