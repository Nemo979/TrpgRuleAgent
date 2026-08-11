import unittest
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from trpg_app.observability import (
    TurnPhaseTimer,
    UsageTotals,
    estimate_message_tokens,
    estimate_tokens,
    log_turn_metrics,
    summarize_messages,
)


class TokenEstimateTest(unittest.TestCase):
    def test_cjk_text_estimates_two_characters_per_token(self) -> None:
        self.assertEqual(estimate_tokens("油腻术" * 4), 6)
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

    def test_estimates_full_provider_message_payload(self) -> None:
        estimate = estimate_message_tokens([{"role": "user", "content": "油腻术是什么"}])
        self.assertGreater(estimate, estimate_tokens("油腻术是什么"))


class UsageTotalsTest(unittest.TestCase):
    def test_prefers_reported_usage_and_falls_back_per_call(self) -> None:
        usage = UsageTotals()
        usage.add(
            prompt_tokens=100,
            completion_tokens=20,
            estimated_prompt_tokens=999,
            estimated_completion_tokens=999,
        )
        usage.add(
            prompt_tokens=None,
            completion_tokens=None,
            estimated_prompt_tokens=30,
            estimated_completion_tokens=5,
        )

        self.assertEqual(
            usage.to_json(),
            {
                "promptTokens": 130,
                "completionTokens": 25,
                "totalTokens": 155,
                "calls": 2,
                "reportedCalls": 1,
                "estimatedCalls": 1,
            },
        )


class TurnPhaseTimerTest(unittest.TestCase):
    def test_accumulates_phases(self) -> None:
        timer = TurnPhaseTimer()
        timer.routing_seconds += 0.01
        timer.decision_seconds += 0.5
        timer.retrieval_seconds += 0.2
        timer.read_seconds += 0.1
        timer.final_generation_seconds += 1.2

        rounded = timer.round_all()

        self.assertEqual(rounded["routing"], 0.01)
        self.assertEqual(rounded["decision"], 0.5)
        self.assertEqual(rounded["retrieval"], 0.2)
        self.assertEqual(rounded["read"], 0.1)
        self.assertEqual(rounded["finalGeneration"], 1.2)
        self.assertGreaterEqual(rounded["total"], 0.0)
        self.assertLess(rounded["total"], 60.0)

    def test_writes_privacy_safe_jsonl_when_metrics_path_is_configured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "turns.jsonl"
            with patch.dict(os.environ, {"TRPG_TURN_METRICS_PATH": str(path)}):
                log_turn_metrics(
                    request_id="request-1",
                    model_id="mimo-v2.5",
                    library_id="pathfinder-1e",
                    timer=TurnPhaseTimer(),
                    context={"historyTokens": 10},
                    search_count=1,
                    read_documents=2,
                    evidence_characters=300,
                    evidence_tokens=150,
                    stop_reason="model_finish",
                    dropped_messages=0,
                    intent="rule_fact",
                    query_hashes=["abc123", "abc123"],
                    usage=UsageTotals(prompt_tokens=100, completion_tokens=20, calls=1, reported_calls=1),
                )

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["query_hashes"], ["abc123"])
            self.assertEqual(payload["usage"]["totalTokens"], 120)
            self.assertNotIn("question", payload)
            self.assertNotIn("answer", payload)


if __name__ == "__main__":
    unittest.main()
