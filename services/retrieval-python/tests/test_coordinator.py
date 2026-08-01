import unittest

from trpg_retrieval.coordinator import (
    RetrievalCoordinator,
    expand_query,
    heading_match_score,
)
from trpg_retrieval.domain import RuleDocument, SearchHit


def document(identifier: str, path: list[str]) -> RuleDocument:
    return RuleDocument(
        id=identifier,
        ruleset_id="gss",
        source_id="core",
        source_title="夕妖晚谣",
        title=f"第 {identifier} 页",
        full_path=" > ".join(path),
        content=identifier,
        version="1.2",
        priority=0,
        metadata={
            "headingPath": path,
            "structuralBlocks": [
                {"headingPath": path, "content": (identifier + "规则正文") * 40}
            ],
        },
    )


class StubRetriever:
    def __init__(self, hits):
        self.hits = hits
        self.query = ""

    def search(self, query, documents, limit, source_ids=None):
        del documents, source_ids
        self.query = query
        return self.hits[:limit]


class RetrievalCoordinatorTest(unittest.TestCase):
    def test_expands_common_rule_query_synonyms_deterministically(self) -> None:
        self.assertEqual(expand_query("怎么创建角色"), "怎么创建角色 建立")
        self.assertEqual(
            expand_query("猫有什么基本特性"),
            "猫有什么基本特性 基本特技",
        )

    def test_entity_and_subsection_path_outrank_generic_weakness_page(self) -> None:
        generic = document("generic", ["夕妖晚谣", "其他", "弱点"])
        cat = document("cat", ["夕妖晚谣", "猫", "弱点和追加特技"])
        base = StubRetriever([
            SearchHit(generic, "generic", 0.04),
            SearchHit(cat, "cat", 0.035),
        ])
        coordinator = RetrievalCoordinator(base, candidate_limit=2)

        hits = coordinator.search("猫有哪些弱点？", [generic, cat], 2)

        self.assertEqual([hit.document.id for hit in hits], ["cat"])

    def test_heading_score_requires_the_explicit_entity(self) -> None:
        cat = document("cat", ["夕妖晚谣", "猫", "弱点和追加特技"])
        dog = document("dog", ["夕妖晚谣", "狗", "弱点和追加特技"])
        self.assertGreater(
            heading_match_score("猫有哪些弱点", cat),
            heading_match_score("猫有哪些弱点", dog),
        )

    def test_generic_flow_terms_do_not_trigger_structure_boost(self) -> None:
        flow = document("flow", ["夕妖晚谣", "故事", "幕间"])
        self.assertEqual(heading_match_score("故事从开始到最后幕间的流程", flow), 0.0)

    def test_direct_entity_lookup_prunes_unrelated_candidates(self) -> None:
        cat = document("cat", ["夕妖晚谣", "猫", "弱点和追加特技"])
        dog = document("dog", ["夕妖晚谣", "狗", "弱点和追加特技"])
        base = StubRetriever([
            SearchHit(dog, "dog", 0.04),
            SearchHit(cat, "cat", 0.035),
        ])

        hits = RetrievalCoordinator(base, candidate_limit=2).search(
            "猫有哪些弱点？", [cat, dog], 10
        )

        self.assertEqual([hit.document.id for hit in hits], ["cat"])

    def test_interaction_question_keeps_unboosted_candidates(self) -> None:
        cat = document("cat", ["夕妖晚谣", "猫", "基本特技"])
        general = document("general", ["夕妖晚谣", "场景"])
        base = StubRetriever([
            SearchHit(general, "general", 0.04),
            SearchHit(cat, "cat", 0.035),
        ])

        hits = RetrievalCoordinator(base, candidate_limit=2).search(
            "猫的基本特技是否会与场景规则同时生效？", [cat, general], 10
        )

        self.assertEqual(len(hits), 2)


if __name__ == "__main__":
    unittest.main()
