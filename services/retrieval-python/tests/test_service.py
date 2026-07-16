import unittest

from trpg_retrieval.domain import RuleDocument
from trpg_retrieval.repository import RuleRepository
from trpg_retrieval.retriever import InMemoryRetriever
from trpg_retrieval.service import RetrievalService


def document(document_id: str, title: str, content: str) -> RuleDocument:
    return RuleDocument(
        id=document_id,
        ruleset_id="pathfinder-1e",
        source_id="crb",
        source_title="核心规则书",
        title=title,
        full_path="核心规则书 > %s" % title,
        content=content,
        version="1e",
        priority=100,
    )


class RetrievalServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        repository = RuleRepository([
            document("aoo", "借机攻击", "借机攻击和威胁范围的测试文本"),
            document("spell", "法术", "法术位和施法的测试文本"),
        ])
        self.service = RetrievalService(repository, InMemoryRetriever())

    def test_search_returns_relevant_document_first(self) -> None:
        hits = self.service.search("借机攻击", "pathfinder-1e")
        self.assertEqual("aoo", hits[0]["id"])

    def test_read_preserves_requested_order(self) -> None:
        documents = self.service.read("pathfinder-1e", ["spell", "aoo"])
        self.assertEqual(["spell", "aoo"], [item["id"] for item in documents])

    def test_read_rejects_excessive_document_count(self) -> None:
        with self.assertRaises(ValueError):
            self.service.read("pathfinder-1e", [str(index) for index in range(9)])


if __name__ == "__main__":
    unittest.main()
