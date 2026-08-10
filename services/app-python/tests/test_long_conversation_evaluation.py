from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from trpg_app.long_conversation_evaluation import evaluate_long_cases, load_long_cases


class LongConversationEvaluationTest(unittest.TestCase):
    def test_evaluates_state_truncation_probe_and_revision_without_raw_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cases.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "id": "long",
                        "history": {
                            "turnCount": 12,
                            "fillerUser": "一般问题",
                            "fillerAssistant": "一般回答",
                            "events": [
                                {
                                    "turn": 1,
                                    "user": "我选择猫作为真身",
                                    "assistant": "我猜是真身为狐",
                                }
                            ],
                        },
                        "contextWindow": 256,
                        "outputReserveTokens": 64,
                        "systemTokens": 64,
                        "expectTruncation": True,
                        "conversationRevision": "old",
                        "currentRevision": "new",
                        "expectedRevisionMismatch": True,
                        "expectedState": {"facts.真身": "猫"},
                        "forbiddenState": [{"path": "facts.真身", "value": "狐"}],
                        "probe": {
                            "query": "弱点",
                            "latestUserMessage": "它有什么弱点？",
                            "mustContain": ["猫"],
                        },
                        "turns": [
                            {
                                "query": "占位问题",
                                "relevantIds": ["doc"],
                                "requiredAny": [["占位"]],
                            }
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            report = evaluate_long_cases(load_long_cases(path))

        self.assertEqual(report["passedCases"], 1)
        self.assertEqual(report["stateRetentionAccuracy"], 1.0)
        self.assertEqual(report["unconfirmedStateContaminationRate"], 0.0)
        self.assertEqual(report["revisionMismatchAccuracy"], 1.0)
        self.assertEqual(report["queryProbeAccuracy"], 1.0)
        self.assertEqual(report["contextTruncationRate"], 1.0)
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("一般问题", serialized)
        self.assertNotIn("我选择猫作为真身", serialized)

    def test_load_rejects_mismatched_history_turn_count(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cases.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "id": "broken",
                        "history": [{"role": "user", "content": "x"}],
                        "expectedState": {},
                        "turns": [{"query": "q", "relevantIds": ["d"], "requiredAny": [["x"]]}],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                load_long_cases(path)


if __name__ == "__main__":
    unittest.main()
