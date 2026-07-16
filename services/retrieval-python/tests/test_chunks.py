import unittest

from trpg_retrieval.chunks import iter_document_chunks
from trpg_retrieval.domain import RuleDocument


class ChunkTest(unittest.TestCase):
    def test_chunks_overlap_and_keep_parent_id(self) -> None:
        document = RuleDocument(
            id="parent",
            ruleset_id="pathfinder-1e",
            source_id="crb",
            source_title="CRB",
            title="战斗",
            full_path="CRB > 战斗",
            content="abcdefghij",
            version="1e",
            priority=100,
        )
        chunks = list(iter_document_chunks(document, chunk_size=6, chunk_overlap=2))
        self.assertEqual(["abcdef", "efghij"], [chunk.content for chunk in chunks])
        self.assertEqual(["parent", "parent"], [chunk.parent_id for chunk in chunks])
        self.assertEqual(["parent:00000", "parent:00001"], [chunk.id for chunk in chunks])


if __name__ == "__main__":
    unittest.main()
