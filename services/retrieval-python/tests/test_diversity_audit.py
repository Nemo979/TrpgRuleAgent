from __future__ import annotations

import unittest

from trpg_retrieval.diversity_audit import (
    audit_case,
    normalize_text,
    semantic_key,
    shingle_jaccard,
    shingles,
)
from trpg_retrieval.domain import RuleDocument


def document(
    document_id: str,
    content: str,
    *,
    entry_type: str | None = None,
    heading_path: list[str] | None = None,
    title: str = "",
) -> RuleDocument:
    metadata: dict = {}
    if entry_type:
        metadata["entryType"] = entry_type
        metadata["entryNameZh"] = title
        metadata["entryNameEn"] = title
    if heading_path:
        metadata["headingPath"] = heading_path
    return RuleDocument(
        id=document_id,
        ruleset_id="pathfinder-1e",
        source_id="crb",
        source_title="核心规则",
        title=title,
        full_path=" > ".join(heading_path or [title or document_id]),
        content=content,
        version="test",
        priority=100,
        metadata=metadata,
    )


class SemanticKeyTest(unittest.TestCase):
    def test_entry_key_uses_type_and_names(self) -> None:
        value = document("a", "x", entry_type="spell", title="油腻术")
        self.assertEqual(semantic_key(value), "spell:油腻术:油腻术")

    def test_heading_path_key_for_chapter_documents(self) -> None:
        value = document("a", "x", heading_path=["战斗规则", "借机攻击"])
        self.assertEqual(semantic_key(value), "path:战斗规则:借机攻击")

    def test_title_fallback_and_none(self) -> None:
        self.assertEqual(semantic_key(document("a", "x", title="借机攻击")), "title:借机攻击")
        self.assertIsNone(semantic_key(document("a", "")))

    def test_rule_table_entries_fall_back_to_path(self) -> None:
        value = document(
            "a",
            "x",
            entry_type="rule_table",
            heading_path=["武器", "表格 1"],
        )
        self.assertEqual(semantic_key(value), "path:武器:表格 1")


class ShingleJaccardTest(unittest.TestCase):
    def test_normalize_unifies_punctuation_and_case(self) -> None:
        self.assertEqual(normalize_text("BAB+6（攻击加值）"), "bab 6 攻击加值")

    def test_identical_texts_are_one(self) -> None:
        self.assertEqual(shingle_jaccard("借机攻击规则", "借机攻击规则"), 1.0)

    def test_near_duplicates_score_high(self) -> None:
        left = "你可以进行一次借机攻击，使用你的最高攻击加值。"
        right = "你可以进行一次借机攻击。使用你的最高攻击加值。"
        self.assertGreaterEqual(shingle_jaccard(left, right), 0.90)

    def test_synonym_variant_scores_below_threshold(self) -> None:
        # 借机/藉机 是 PF1e 常见同义字；Phase0 从 0.90 起实验，
        # 低于阈值的轻微用字差异正是审计报告要呈现的发现。
        left = "你可以进行一次借机攻击，使用你的最高攻击加值。"
        right = "你可以进行一次藉机攻击，使用你的最高攻击加值。"
        self.assertGreaterEqual(shingle_jaccard(left, right), 0.7)
        self.assertLess(shingle_jaccard(left, right), 0.9)

    def test_distinct_rules_score_low(self) -> None:
        left = "借机攻击由动作引发。"
        right = "法术抗力由等级决定。"
        self.assertLess(shingle_jaccard(left, right), 0.6)

    def test_cjk_and_english_shingles(self) -> None:
        self.assertTrue(all(len(shingle) == 3 for shingle in shingles("借机攻击")))
        english = shingles("Attack of Opportunity")
        self.assertEqual(english, {"attack of opportunity"})


class CaseAuditTest(unittest.TestCase):
    def _results(self) -> list:
        return [
            document("a1", "借机攻击由动作引发，使用最高攻击加值。", heading_path=["战斗规则", "借机攻击"]),
            document("a2", "借机攻击由动作引发，使用最高攻击加值。", heading_path=["战斗规则", "借机攻击"]),
            document("b1", "法术抗力由等级决定，与豁免无关。", entry_type="spell", title="法术抗力"),
            document("b2", "法术抗力由等级决定，与豁免无关。", entry_type="spell", title="法术抗力"),
            document("c1", "不同体型生物的战斗有不同规则。", heading_path=["战斗规则", "不同体型生物的战斗"]),
            document("c2", "不同体型生物的战斗有不同规则。", heading_path=["战斗规则", "不同体型生物的战斗"]),
        ]

    def test_audit_case_counts_bucket_pairs_and_top8_duplicates(self) -> None:
        case = audit_case("combat", "什么时候触发借机攻击？", self._results(), threshold=0.90)

        self.assertEqual(case.top8_duplicate_slots, 3)
        self.assertEqual(case.top8_unique_keys, 3)
        self.assertEqual(len(case.in_bucket_pairs), 3)
        self.assertTrue(all(pair.same_key for pair in case.in_bucket_pairs))
        self.assertEqual(
            case.cluster_sizes,
            {
                "path:战斗规则:借机攻击": 2,
                "spell:法术抗力:法术抗力": 2,
                "path:战斗规则:不同体型生物的战斗": 2,
            },
        )

    def test_audit_case_detects_cross_bucket_pairs(self) -> None:
        results = self._results()[:2] + [
            document("d1", "借机攻击由动作引发，使用最高攻击加值。", heading_path=["怪物", "攻击"]),
            document("d2", "借机攻击由动作引发，使用最高攻击加值。", heading_path=["怪物", "攻击"]),
        ]
        case = audit_case("cross", "借机攻击", results, threshold=0.90)

        self.assertTrue(any(not pair.same_key for pair in case.cross_bucket_pairs))


if __name__ == "__main__":
    unittest.main()
