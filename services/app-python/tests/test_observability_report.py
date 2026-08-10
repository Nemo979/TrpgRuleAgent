from __future__ import annotations

import unittest

from trpg_app.observability_report import aggregate, compare


def record(total_seconds: float, total_tokens: int, *, intent: str = "rule_fact") -> dict:
    return {
        "model_id": "mimo-v2.5",
        "library_id": "pathfinder-1e",
        "intent": intent,
        "stop_reason": "model_finish",
        "phases_seconds": {
            "decision": 1.0,
            "retrieval": 0.1,
            "read": 0.1,
            "finalGeneration": 2.0,
            "total": total_seconds,
        },
        "context": {
            "historyTokens": 10,
            "systemTokens": 20,
            "outputReserveTokens": 1000,
            "finalAnswerTokens": 40,
            "evidencePolicyMaxSearches": 3,
            "evidencePolicyMaxAnswerDocuments": 4,
            "evidencePolicyMaxTokens": 12000,
            "evidencePolicyUsedTopics": 2,
        },
        "usage": {
            "promptTokens": total_tokens - 20,
            "completionTokens": 20,
            "totalTokens": total_tokens,
            "reportedCalls": 1,
            "estimatedCalls": 0,
        },
        "search_count": 1,
        "read_documents": 2,
        "evidence_characters": 300,
        "evidence_tokens": 150,
        "dropped_messages": 0,
    }


class ObservabilityReportTest(unittest.TestCase):
    def test_aggregates_dimensions_percentiles_and_usage_coverage(self) -> None:
        truncated = record(5.0, 140, intent="procedure")
        truncated["dropped_messages"] = 2
        report = aggregate([record(3.0, 100), truncated])

        self.assertEqual(report["turnCount"], 2)
        self.assertEqual(report["dimensions"]["intent"], {"procedure": 1, "rule_fact": 1})
        self.assertEqual(report["metrics"]["latency.totalSeconds"]["mean"], 4.0)
        self.assertEqual(report["metrics"]["usage.totalTokens"]["p95"], 140.0)
        self.assertEqual(report["usageCoverage"]["reportedRate"], 1.0)
        self.assertEqual(report["derivedRates"]["contextTruncationRate"], 0.5)
        self.assertEqual(report["metrics"]["policy.maxSearches"]["mean"], 3.0)
        self.assertEqual(
            report["metrics"]["policy.maxAnswerDocuments"]["mean"], 4.0
        )

    def test_compares_current_report_to_baseline(self) -> None:
        baseline = aggregate([record(3.0, 100)])
        current = aggregate([record(4.5, 125)])

        comparison = compare(current, baseline)

        self.assertEqual(comparison["metricDeltas"]["latency.totalSeconds"]["mean"], 1.5)
        self.assertEqual(comparison["metricDeltas"]["usage.totalTokens"]["mean"], 25.0)


if __name__ == "__main__":
    unittest.main()
