import unittest

from trpg_retrieval.coordinator import (
    RetrievalCoordinator,
    character_creation_heading_score,
    content_match_score,
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
    def test_uses_expanded_query_only_for_character_creation_intent(self) -> None:
        creation = document("creation", ["规则", "建立角色"])
        base = StubRetriever([SearchHit(creation, "creation", 0.04)])
        coordinator = RetrievalCoordinator(base, candidate_limit=1)

        coordinator.search("我想创建一个扮演的角色", [creation], 1)

        self.assertEqual(base.query, "我想创建一个扮演的角色 建立")
        self.assertGreater(
            character_creation_heading_score("我想创建一个角色", creation),
            0,
        )

    def test_does_not_broaden_specific_character_creation_subtopic(self) -> None:
        ability = document("ability", ["规则", "属性购点"])
        base = StubRetriever([SearchHit(ability, "ability", 0.04)])
        coordinator = RetrievalCoordinator(base, candidate_limit=1)

        coordinator.search("创建角色时属性购点怎么分配", [ability], 1)

        self.assertEqual(base.query, "创建角色时属性购点怎么分配")
        self.assertEqual(
            character_creation_heading_score("创建角色时属性购点怎么分配", ability),
            0,
        )

    def test_expands_common_rule_query_synonyms_deterministically(self) -> None:
        self.assertEqual(expand_query("怎么创建角色"), "怎么创建角色 建立")
        self.assertEqual(
            expand_query("猫有什么基本特性"),
            "猫有什么基本特性 基本特技",
        )
        self.assertEqual(
            expand_query("火把能照亮多大范围，可以燃烧多久？"),
            "火把能照亮多大范围,可以燃烧多久? 照明半径 持续时间",
        )

    def test_uses_safe_rule_term_expansions_for_retrieval(self) -> None:
        light = document("light", ["核心规则", "视力和光源"])
        base = StubRetriever([SearchHit(light, "light", 0.04)])

        RetrievalCoordinator(base, candidate_limit=1).search(
            "火把能照亮多大范围，可以燃烧多久？", [light], 1
        )

        self.assertEqual(
            base.query,
            "火把能照亮多大范围,可以燃烧多久? 照明半径 持续时间",
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

    def test_direct_lookup_keeps_a_strong_exact_evidence_leader(self) -> None:
        exact = document("exact", ["核心规则", "特殊攻击"])
        structural = document("structural", ["其他规则", "武器", "减值"])
        base = StubRetriever([
            SearchHit(
                exact,
                "拥有双武器格斗专长且副手为轻型武器时，主手和副手各承受-2减值。",
                0.04,
            ),
            SearchHit(structural, "无关规则", 0.035),
        ])
        query = "有双武器格斗专长且副手是轻型武器时，主手和副手各受多少减值？"

        hits = RetrievalCoordinator(base, candidate_limit=2).search(
            query, [exact, structural], 10
        )

        self.assertIn("exact", [hit.document.id for hit in hits])
        self.assertGreater(content_match_score(query, base.hits[0].excerpt), 0.3)

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
