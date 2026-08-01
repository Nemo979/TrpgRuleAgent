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

    def test_structural_blocks_keep_their_own_heading_path(self) -> None:
        document = RuleDocument(
            id="page-51",
            ruleset_id="gss",
            source_id="core",
            source_title="夕妖晚谣",
            title="第 51 页",
            full_path="夕妖晚谣 > 猫 > 第 51 页",
            content="猫的介绍\n一团毛球",
            version="1.2",
            priority=0,
            metadata={
                "structuralBlocks": [
                    {"headingPath": ["夕妖晚谣", "猫"], "content": "猫的介绍"},
                    {
                        "headingPath": ["夕妖晚谣", "猫", "基本特技"],
                        "content": "一团毛球",
                    },
                ]
            },
        )

        chunks = list(iter_document_chunks(document, chunk_size=20, chunk_overlap=2))
        self.assertEqual(["猫的介绍", "一团毛球"], [chunk.content for chunk in chunks])
        self.assertIn("夕妖晚谣 > 猫\n", chunks[0].search_text)
        self.assertIn("夕妖晚谣 > 猫 > 基本特技\n", chunks[1].search_text)


if __name__ == "__main__":
    unittest.main()
