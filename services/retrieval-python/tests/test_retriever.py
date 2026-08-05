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
    def test_hit_keeps_child_evidence_and_parent_score_in_json(self) -> None:
        entry = document(
            "entry",
            "目标条目",
            "前置说明。" * 30 + "目标条目正文命中。",
        )
        retriever = InMemoryRetriever(chunk_size=40, chunk_overlap=10)

        hit = retriever.search("前置说明", [entry], 1)[0]
        payload = hit.to_json()

        self.assertEqual(hit.chunk_id, "entry:00000")
        self.assertEqual(hit.chunk_index, 0)
        self.assertGreater(hit.match_score, 0.0)
        self.assertEqual(hit.parent_score, hit.score)
        self.assertEqual(payload["chunkId"], hit.chunk_id)
        self.assertEqual(payload["chunkIndex"], hit.chunk_index)
        self.assertEqual(payload["matchScore"], round(hit.match_score, 6))
        self.assertEqual(payload["parentScore"], round(hit.parent_score, 6))
        self.assertEqual(payload["score"], round(hit.score, 6))

    def test_exact_title_prefers_entry_over_reference_documents(self) -> None:
        entry = document(
            "wizard",
            "法师",
            "法师的独立条目正文，描述施法、法术和职业特性。",
        )
        spell_list = document(
            "wizard-spells",
            "法师职业法术列表",
            "法师可以从职业法术列表中选择法术。",
        )
        source_reference = document(
            "wizard-source",
            "法师出处速查",
            "法师出处速查和书目索引。",
        )
        contents = document("contents", "目录", "目录列出法师所在章节。")
        update_log = document("updates", "更新记录", "更新记录提到法师条目。")
        retriever = InMemoryRetriever()

        hits = retriever.search(
            "法师",
            [spell_list, source_reference, contents, update_log, entry],
            5,
        )

        self.assertEqual(hits[0].document.id, "wizard")

    def test_favored_class_is_penalized_only_for_multiclass_intent(self) -> None:
        multiclass = document(
            "multiclass",
            "多职业",
            "多职业规则说明兼职时如何组合职业等级。",
        )
        favored = document(
            "favored",
            "天赋职业",
            "Favored Class 规则说明每次升级可选择升级奖励。",
        )
        retriever = InMemoryRetriever()

        multiclass_hits = retriever.search(
            "兼职时如何组合多职业的职业等级",
            [favored, multiclass],
            2,
        )
        favored_hits = retriever.search(
            "天赋职业的升级奖励是什么 Favored Class",
            [multiclass, favored],
            2,
        )

        self.assertEqual(multiclass_hits[0].document.id, "multiclass")
        self.assertEqual(favored_hits[0].document.id, "favored")

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
