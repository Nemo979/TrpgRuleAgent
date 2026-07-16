import unittest

from trpg_retrieval.domain import RuleDocument
from trpg_retrieval.retriever import InMemoryRetriever


def document(identifier: str, title: str, content: str, source_id: str = "crb") -> RuleDocument:
    return RuleDocument(
        id=identifier,
        ruleset_id="pathfinder-1e",
        source_id=source_id,
        source_title=source_id,
        title=title,
        full_path="规则 > %s" % title,
        content=content,
        version="test",
        priority=100,
    )


class Bm25RetrieverTest(unittest.TestCase):
    def test_specific_terms_win_inside_long_document(self) -> None:
        relevant = document(
            "ability",
            "属性",
            ("其他规则内容。" * 80) + "体质修正改变时，角色生命值也会相应增加或减少。",
        )
        distractor = document("class", "职业", "体质强健的角色可以选择这个职业。")
        retriever = InMemoryRetriever(chunk_size=120, chunk_overlap=20)

        hits = retriever.search("体质修正改变以后生命值怎么调整", [distractor, relevant], 2)

        self.assertEqual(hits[0].document.id, "ability")

    def test_filters_sources(self) -> None:
        core = document("core", "借机攻击", "借机攻击触发规则", "crb")
        optional = document("optional", "借机攻击", "借机攻击触发规则", "unchained")
        retriever = InMemoryRetriever()

        hits = retriever.search(
            "借机攻击",
            [core, optional],
            5,
            source_ids=["unchained"],
        )

        self.assertEqual([hit.document.id for hit in hits], ["optional"])


if __name__ == "__main__":
    unittest.main()
