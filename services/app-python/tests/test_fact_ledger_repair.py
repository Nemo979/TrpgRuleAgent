from __future__ import annotations

import json
import unittest

from trpg_app.fact_ledger import ValidationIssue
from trpg_app.fact_ledger_repair import (
    AnswerClaim,
    AnswerDraft,
    AnswerSection,
    StructuredAnswerError,
    apply_repair_patch,
    build_repair_targets,
    parse_answer_draft,
    parse_repair_patch,
    render_answer_draft,
)


def draft_payload() -> dict:
    return {
        "schema_version": 1,
        "sections": [
            {
                "id": "route",
                "heading": "升级路线",
                "claims": [
                    {
                        "id": "level_5_feat",
                        "path": "answer.levels[5].feats",
                        "text": "5级没有新的专长选择。",
                        "evidence_refs": ["S1"],
                    },
                    {
                        "id": "level_6_feat",
                        "path": "answer.levels[6].feats",
                        "text": "6级选择战斗反射。",
                        "evidence_refs": ["S2"],
                    },
                ],
            }
        ],
    }


class FactLedgerRepairTest(unittest.TestCase):
    def test_parses_and_deterministically_renders_registered_claims(self) -> None:
        payload = draft_payload()
        payload["sections"][0]["claims"][0]["text"] += "[S1]"

        draft = parse_answer_draft(json.dumps(payload, ensure_ascii=False), ("S1", "S2"))

        self.assertEqual(
            render_answer_draft(draft),
            "## 升级路线\n\n5级没有新的专长选择。 [S1]\n\n6级选择战斗反射。 [S2]",
        )

    def test_rejects_unregistered_or_undeclared_citation(self) -> None:
        for refs, text in ((["S9"], "结论"), (["S1"], "结论[S2]")):
            payload = draft_payload()
            payload["sections"][0]["claims"][0]["evidence_refs"] = refs
            payload["sections"][0]["claims"][0]["text"] = text
            with self.subTest(refs=refs, text=text), self.assertRaises(
                StructuredAnswerError
            ):
                parse_answer_draft(json.dumps(payload, ensure_ascii=False), ("S1", "S2"))

    def test_rejects_duplicate_ids_paths_and_unknown_fields(self) -> None:
        for mutation in ("id", "path", "field"):
            payload = draft_payload()
            if mutation == "id":
                payload["sections"][0]["claims"][1]["id"] = "level_5_feat"
            elif mutation == "path":
                payload["sections"][0]["claims"][1]["path"] = "answer.levels[5].feats"
            else:
                payload["unexpected"] = True
            with self.subTest(mutation=mutation), self.assertRaises(StructuredAnswerError):
                parse_answer_draft(json.dumps(payload, ensure_ascii=False), ("S1", "S2"))

    def test_applies_only_exact_failed_claim_and_preserves_verified_claim(self) -> None:
        draft = parse_answer_draft(
            json.dumps(draft_payload(), ensure_ascii=False),
            ("S1", "S2"),
        )
        issues = (
            ValidationIssue(
                "feat_timeline_slot_count",
                "5级缺少一个专长",
                path="answer.levels[5].feats",
                expected=1,
                actual=0,
                evidence_refs=("S1",),
            ),
        )
        targets = build_repair_targets(draft, issues)
        patch = parse_repair_patch(
            json.dumps(
                {
                    "schema_version": 1,
                    "operations": [
                        {
                            "claim_id": "level_5_feat",
                            "path": "answer.levels[5].feats",
                            "replacement_text": "5级选择钢铁意志。",
                            "evidence_refs": ["S1"],
                            "issue_codes": ["feat_timeline_slot_count"],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            ("S1", "S2"),
        )

        repaired = apply_repair_patch(draft, patch, targets)

        self.assertEqual(repaired.claims[0].text, "5级选择钢铁意志。")
        self.assertIs(repaired.claims[1], draft.claims[1])

    def test_rejects_patch_that_changes_verified_or_omits_failed_claim(self) -> None:
        draft = parse_answer_draft(
            json.dumps(draft_payload(), ensure_ascii=False),
            ("S1", "S2"),
        )
        targets = build_repair_targets(
            draft,
            (
                ValidationIssue(
                    "missing",
                    "missing",
                    path="answer.levels[5].feats",
                ),
            ),
        )
        wrong_target = {
            "schema_version": 1,
            "operations": [
                {
                    "claim_id": "level_6_feat",
                    "path": "answer.levels[6].feats",
                    "replacement_text": "篡改已验证内容",
                    "evidence_refs": ["S2"],
                    "issue_codes": ["missing"],
                }
            ],
        }
        patch = parse_repair_patch(
            json.dumps(wrong_target, ensure_ascii=False),
            ("S1", "S2"),
        )

        with self.assertRaisesRegex(StructuredAnswerError, "exactly cover"):
            apply_repair_patch(draft, patch, targets)

    def test_rejects_nonrepairable_or_unmapped_issue(self) -> None:
        draft = AnswerDraft(
            (
                AnswerSection(
                    "summary",
                    "摘要",
                    (
                        AnswerClaim(
                            "summary_claim",
                            "answer.summary.claim",
                            "结论",
                        ),
                    ),
                ),
            )
        )
        for issue in (
            ValidationIssue("blocked", "blocked", path="answer.summary.claim", repairable=False),
            ValidationIssue("missing", "missing", path="answer.other.claim"),
            ValidationIssue("legacy", "legacy"),
        ):
            with self.subTest(code=issue.code), self.assertRaises(StructuredAnswerError):
                build_repair_targets(draft, (issue,))


if __name__ == "__main__":
    unittest.main()
