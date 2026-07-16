import unittest

from trpg_retrieval.domain import RuleDocument, SearchHit
from trpg_retrieval.hybrid_retriever import HybridRetriever


def document(identifier: str, priority: int = 0) -> RuleDocument:
    return RuleDocument(
        id=identifier,
        ruleset_id="pathfinder-1e",
        source_id="crb",
        source_title="Core Rulebook",
        title=identifier,
        full_path=identifier,
        content=identifier,
        version="test",
        priority=priority,
    )


class StubRetriever:
    def __init__(self, hits):
        self.hits = hits

    def search(self, query, documents, limit, source_ids=None):
        del query, documents, source_ids
        return self.hits[:limit]


class HybridRetrieverTest(unittest.TestCase):
    def test_default_fusion_slightly_favors_lexical_ranking(self) -> None:
        first = document("first")
        second = document("second")
        vector = StubRetriever([
            SearchHit(first, "vector-first", 0.9),
            SearchHit(second, "vector-second", 0.8),
        ])
        lexical = StubRetriever([
            SearchHit(second, "lexical-second", 0.9),
            SearchHit(first, "lexical-first", 0.8),
        ])
        retriever = HybridRetriever(vector, lexical, candidate_limit=2)

        hits = retriever.search("query", [first, second], 2)

        self.assertEqual([hit.document.id for hit in hits], ["second", "first"])
        self.assertEqual(hits[0].excerpt, "vector-second")

    def test_fuses_vector_and_lexical_rankings_with_explicit_equal_weights(self) -> None:
        first = document("first")
        second = document("second")
        vector = StubRetriever([
            SearchHit(first, "vector-first", 0.9),
            SearchHit(second, "vector-second", 0.8),
        ])
        lexical = StubRetriever([
            SearchHit(second, "lexical-second", 0.9),
            SearchHit(first, "lexical-first", 0.8),
        ])
        retriever = HybridRetriever(
            vector,
            lexical,
            candidate_limit=2,
            vector_weight=1.0,
            lexical_weight=1.0,
        )

        hits = retriever.search("query", [first, second], 2)

        self.assertEqual([hit.document.id for hit in hits], ["first", "second"])

    def test_source_priority_breaks_near_ties(self) -> None:
        lower_priority = document("lower", priority=10)
        higher_priority = document("higher", priority=100)
        vector = StubRetriever([
            SearchHit(lower_priority, "lower", 0.9),
            SearchHit(higher_priority, "higher", 0.8),
        ])
        lexical = StubRetriever([
            SearchHit(lower_priority, "lower", 0.9),
            SearchHit(higher_priority, "higher", 0.8),
        ])
        retriever = HybridRetriever(vector, lexical, candidate_limit=2)

        hits = retriever.search("query", [lower_priority, higher_priority], 2)

        self.assertEqual(hits[0].document.id, "higher")


if __name__ == "__main__":
    unittest.main()
