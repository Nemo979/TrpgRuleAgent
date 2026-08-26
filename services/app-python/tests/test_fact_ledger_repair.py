from __future__ import annotations

import json
import unittest

from trpg_app.fact_ledger import (
    DraftClaimContract,
    DraftPathSpec,
    DraftSelectionOption,
    DraftSelectionGroup,
    RepairPathMapping,
    ValidationIssue,
)
from trpg_app.fact_ledger_repair import (
    AnswerClaim,
    AnswerDraft,
    AnswerSection,
    StructuredAnswerError,
    RepairTargetError,
    apply_repair_patch,
    build_repair_targets,
    classify_draft_parse_failure,
    classify_repair_patch_failure,
    parse_answer_draft,
    parse_repair_patch,
    render_answer_draft,
    render_answer_draft_for_validation,
    repair_instruction,
    structured_draft_instruction,
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
    def test_server_text_contract_is_exclusive_and_uses_no_wire_slot(self) -> None:
        path = "answer.levels[5].spells"
        contract = DraftClaimContract(
            path,
            evidence_refs=("S1",),
            server_text="5级法师最高可施放3环法术。",
        )
        instruction = structured_draft_instruction(
            (DraftPathSpec("answer.levels[{level}].spells"),),
            (path,),
            ("S1",),
            (contract,),
        )
        self.assertIn('"values":[]', instruction)
        self.assertNotIn('"kind":"server"', instruction)
        draft = parse_answer_draft(
            '{"values":[]}',
            ("S1",),
            (DraftPathSpec("answer.levels[{level}].spells"),),
            (path,),
            (contract,),
        )
        self.assertEqual(draft.claims[0].text, contract.server_text)

        wrapped = parse_answer_draft(
            '{"required_output":{"values":[]}}',
            ("S1",),
            (DraftPathSpec("answer.levels[{level}].spells"),),
            (path,),
            (contract,),
        )
        self.assertEqual(wrapped.claims[0].text, contract.server_text)

        with self.assertRaises(ValueError):
            DraftClaimContract(
                path,
                selection_count=1,
                selection_options=(DraftSelectionOption("选项", ("S1",)),),
                text_template="选择：{values}",
                server_text="服务器正文",
            )

    def test_free_text_contract_extracts_semantic_scope(self) -> None:
        path = "answer.build.levels"
        contract = DraftClaimContract(
            path,
            evidence_refs=("S1",),
            value_description="只写职业等级。",
            required_term_groups=(("级",), ("法师", "战士")),
            forbidden_terms=("护甲", "法术位"),
        )
        kwargs = (
            ("S1",),
            (DraftPathSpec(path),),
            (path,),
            (contract,),
        )
        draft = parse_answer_draft('{"values":["角色5级选择法师职业。"]}', *kwargs)
        self.assertEqual(draft.claims[0].text, "角色5级选择法师职业。")
        scoped = parse_answer_draft(
            '{"values":["角色5级选择法师职业，购买护甲。"]}', *kwargs
        )
        self.assertEqual(scoped.claims[0].text, "角色5级选择法师职业")
        with self.assertRaises(StructuredAnswerError) as caught:
            parse_answer_draft('{"values":["购买护甲。"]}', *kwargs)
        self.assertEqual(classify_draft_parse_failure(caught.exception), "semantic_scope")

    def test_free_text_contract_can_publish_server_semantic_fallback(self) -> None:
        path = "answer.equipment.stats"
        fallback = "已读装备证据不足，不臆测购买结论。"
        contract = DraftClaimContract(
            path,
            evidence_refs=("S1",),
            semantic_fallback_text=fallback,
            value_description="只写装备比较。",
            required_term_groups=(("装备", "护甲"),),
            forbidden_terms=("法术位",),
        )
        draft = parse_answer_draft(
            '{"values":["只写每日法术位。"]}',
            ("S1",),
            (DraftPathSpec(path),),
            (path,),
            (contract,),
        )
        self.assertEqual(draft.claims[0].text, fallback)

    def test_server_owned_claim_cannot_become_a_repair_target(self) -> None:
        path = "answer.levels[5].spells"
        draft = AnswerDraft(
            (AnswerSection("rules", "规则", (AnswerClaim("spell", path, "服务器正文"),)),)
        )
        issue = ValidationIssue("spell_progression", "错误", path=path)
        contract = DraftClaimContract(path, server_text="服务器正文")
        with self.assertRaises(RepairTargetError) as caught:
            build_repair_targets(draft, (issue,), claim_contracts=(contract,))
        self.assertEqual(caught.exception.reason, "server_owned")

    def test_ordered_selection_groups_enforce_each_slot_domain(self) -> None:
        path = "answer.levels[1].feats"
        contract = DraftClaimContract(
            path,
            evidence_refs=("S1",),
            selection_count=2,
            selection_options=(
                DraftSelectionOption("钢铁意志", ("S2",)),
                DraftSelectionOption("猛力攻击", ("S2",)),
            ),
            selection_groups=(
                DraftSelectionGroup("普通专长", 1, ("钢铁意志", "猛力攻击")),
                DraftSelectionGroup("战士奖励专长", 1, ("猛力攻击",)),
            ),
            text_template="1级选择专长：{values}。",
        )
        args = (
            ("S1", "S2"),
            (DraftPathSpec("answer.levels[{level}].feats"),),
            (path,),
            (contract,),
        )

        draft = parse_answer_draft(
            '{"values":[["钢铁意志","猛力攻击"]]}',
            *args,
        )
        self.assertEqual(
            draft.claims[0].text,
            "1级选择专长：普通专长：钢铁意志；战士奖励专长：猛力攻击。",
        )
        with self.assertRaisesRegex(StructuredAnswerError, "ordered group"):
            parse_answer_draft(
                '{"values":[["猛力攻击","钢铁意志"]]}',
                *args,
            )

    def test_adapter_opt_in_selection_fallback_normalizes_each_group(self) -> None:
        path = "answer.levels[1].feats"
        contract = DraftClaimContract(
            path,
            evidence_refs=("S1",),
            selection_count=2,
            selection_options=(
                DraftSelectionOption("钢铁意志", ("S2",)),
                DraftSelectionOption("猛力攻击", ("S2",)),
            ),
            selection_groups=(
                DraftSelectionGroup("普通专长", 1, ("钢铁意志", "猛力攻击")),
                DraftSelectionGroup("战士奖励专长", 1, ("猛力攻击",)),
            ),
            allow_selection_fallback=True,
            text_template="1级选择专长：{values}。",
        )
        draft = parse_answer_draft(
            '{"values":[["越界专长"]]}',
            ("S1", "S2"),
            (DraftPathSpec(path),),
            (path,),
            (contract,),
        )
        self.assertEqual(draft.selection_fallback_count, 1)
        self.assertEqual(
            draft.claims[0].text,
            "1级选择专长：普通专长：钢铁意志；战士奖励专长：猛力攻击。",
        )

    def test_rejects_malformed_adapter_path_contract(self) -> None:
        for canonical, aliases in (
            ("levels[{level}]", ()),
            ("answer.levels[{level}].{field}", ()),
            ("answer.levels[{level}]", ("answer.levels[{other}]",)),
        ):
            with self.subTest(canonical=canonical), self.assertRaises(ValueError):
                DraftPathSpec(canonical, aliases)

    def test_rejects_issue_code_that_cannot_be_safely_aggregated(self) -> None:
        with self.assertRaisesRegex(ValueError, "code or message"):
            ValidationIssue("claim text: secret", "invalid metric dimension")

    def test_parses_and_deterministically_renders_registered_claims(self) -> None:
        payload = draft_payload()
        payload["sections"][0]["claims"][0]["text"] += "[S1]"

        draft = parse_answer_draft(json.dumps(payload, ensure_ascii=False), ("S1", "S2"))

        self.assertEqual(
            render_answer_draft(draft),
            "## 升级路线\n\n5级没有新的专长选择。 [S1]\n\n6级选择战斗反射。 [S2]",
        )

        validation_text = render_answer_draft_for_validation(draft)
        self.assertIn(
            "[[TRPGCLAIMPATH:answer.levels[5].feats]]",
            validation_text,
        )
        self.assertNotIn("TRPGCLAIMPATH", render_answer_draft(draft))

    def test_recovers_one_prose_wrapped_json_object_but_rejects_ambiguity(self) -> None:
        raw = "这是结果：\n" + json.dumps(draft_payload(), ensure_ascii=False) + "\n结束。"

        draft = parse_answer_draft(raw, ("S1", "S2"))

        self.assertEqual(len(draft.claims), 2)
        with self.assertRaisesRegex(StructuredAnswerError, "not valid JSON"):
            parse_answer_draft(
                raw + json.dumps(draft_payload(), ensure_ascii=False),
                ("S1", "S2"),
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

    def test_rejects_duplicate_paths_and_unknown_fields(self) -> None:
        for mutation in ("path", "field"):
            payload = draft_payload()
            if mutation == "path":
                payload["sections"][0]["claims"][1]["path"] = "answer.levels[5].feats"
            else:
                payload["unexpected"] = True
            with self.subTest(mutation=mutation), self.assertRaises(StructuredAnswerError):
                parse_answer_draft(json.dumps(payload, ensure_ascii=False), ("S1", "S2"))

    def test_duplicate_path_has_a_distinct_bounded_failure_class(self) -> None:
        payload = draft_payload()
        payload["sections"][0]["claims"][1]["path"] = "answer.levels[5].feats"

        with self.assertRaises(StructuredAnswerError) as caught:
            parse_answer_draft(json.dumps(payload, ensure_ascii=False), ("S1", "S2"))

        self.assertEqual(classify_draft_parse_failure(caught.exception), "duplicate_path")

    def test_server_deterministically_disambiguates_duplicate_internal_ids(self) -> None:
        payload = draft_payload()
        payload["sections"][0]["claims"][1]["id"] = "level_5_feat"

        draft = parse_answer_draft(
            json.dumps(payload, ensure_ascii=False),
            ("S1", "S2"),
        )

        self.assertEqual(
            [claim.id for claim in draft.claims],
            ["level_5_feat", "level_5_feat_2"],
        )

    def test_adapter_contract_normalizes_alias_and_rejects_freeform_path(self) -> None:
        specs = (
            DraftPathSpec(
                "answer.levels[{level}].feats",
                ("answer.progression[{level}].feats",),
            ),
        )
        payload = draft_payload()
        payload["sections"][0]["claims"] = [payload["sections"][0]["claims"][0]]
        payload["sections"][0]["claims"][0]["path"] = "answer.progression[5].feats"

        draft = parse_answer_draft(
            json.dumps(payload, ensure_ascii=False),
            ("S1", "S2"),
            specs,
        )

        self.assertEqual(draft.claims[0].path, "answer.levels[5].feats")
        payload["sections"][0]["claims"][0]["path"] = "answer.route.feats"
        with self.assertRaisesRegex(StructuredAnswerError, "adapter contract"):
            parse_answer_draft(
                json.dumps(payload, ensure_ascii=False),
                ("S1", "S2"),
                specs,
            )

    def test_required_paths_are_checked_after_alias_normalization(self) -> None:
        specs = (
            DraftPathSpec(
                "answer.levels[{level}].feats",
                ("answer.progression[{level}].feats",),
            ),
        )
        payload = draft_payload()
        payload["sections"][0]["claims"] = [payload["sections"][0]["claims"][0]]
        payload["sections"][0]["claims"][0]["path"] = "answer.progression[5].feats"

        parsed = parse_answer_draft(
            json.dumps(payload, ensure_ascii=False),
            ("S1", "S2"),
            specs,
            ("answer.levels[5].feats",),
        )
        self.assertEqual(parsed.claims[0].path, "answer.levels[5].feats")

        with self.assertRaisesRegex(StructuredAnswerError, "required path") as caught:
            parse_answer_draft(
                json.dumps(payload, ensure_ascii=False),
                ("S1", "S2"),
                specs,
                ("answer.levels[6].feats",),
            )
        self.assertEqual(
            classify_draft_parse_failure(caught.exception),
            "missing_required_path",
        )

    def test_server_required_ids_override_invalid_or_duplicate_model_paths(self) -> None:
        payload = draft_payload()
        claims = payload["sections"][0]["claims"]
        claims[0]["id"] = "required_1"
        claims[0]["path"] = "answer.not_in_contract[value]"
        claims[1]["id"] = "required_2"
        claims[1]["path"] = "answer.not_in_contract[value]"
        specs = (
            DraftPathSpec("answer.levels[{level}].feats"),
            DraftPathSpec("answer.summary[{key}]"),
        )

        draft = parse_answer_draft(
            json.dumps(payload, ensure_ascii=False),
            ("S1", "S2"),
            specs,
            ("answer.levels[5].feats", "answer.summary[feat_eligibility]"),
        )

        self.assertEqual(
            [claim.path for claim in draft.claims],
            ["answer.levels[5].feats", "answer.summary[feat_eligibility]"],
        )

    def test_instruction_contains_real_json_and_server_required_paths(self) -> None:
        instruction = structured_draft_instruction(
            (DraftPathSpec("answer.summary[{key}]"),),
            ("answer.summary[conditional_branch]",),
            ("S1",),
        )

        self.assertIn('"schema_version":1', instruction)
        self.assertIn('"sections":[', instruction)
        self.assertIn('"answer.summary[conditional_branch]"', instruction)
        self.assertIn('"id":"required_1"', instruction)
        self.assertIn('"selection_contracts":{}', instruction)
        self.assertIn("证据由服务器绑定", instruction)
        self.assertIn('"required_output":', instruction)
        self.assertNotIn("Schema 为 {schema_version", instruction)

    def test_server_renders_typed_selection_and_owns_its_evidence(self) -> None:
        path = "answer.levels[1].feats"
        contract = DraftClaimContract(
            path,
            evidence_refs=("S1",),
            selection_count=2,
            selection_options=(
                DraftSelectionOption("猛力攻击", ("S2",)),
                DraftSelectionOption("顺势斩", ("S2",)),
                DraftSelectionOption("闪避", ("S3",)),
            ),
            text_template="1级选择专长：{values}。",
        )
        payload = {"values": [["猛力攻击", "闪避"]]}

        draft = parse_answer_draft(
            json.dumps(payload, ensure_ascii=False),
            ("S1", "S2", "S3"),
            (DraftPathSpec("answer.levels[{level}].feats"),),
            (path,),
            (contract,),
        )

        self.assertEqual(draft.claims[0].path, path)
        self.assertEqual(draft.sections[0].heading, "规则结论")
        self.assertEqual(draft.claims[0].text, "1级选择专长：猛力攻击、闪避。")
        self.assertEqual(draft.claims[0].evidence_refs, ("S1", "S2", "S3"))

    def test_typed_selection_rejects_wrong_count_duplicates_and_unknown_values(self) -> None:
        path = "answer.levels[1].feats"
        contract = DraftClaimContract(
            path,
            selection_count=2,
            selection_options=(
                DraftSelectionOption("猛力攻击", ("S1",)),
                DraftSelectionOption("顺势斩", ("S1",)),
            ),
            text_template="1级选择专长：{values}。",
        )
        for values in (["猛力攻击"], ["猛力攻击", "猛力攻击"], ["猛力攻击", "虚构专长"]):
            payload = {"values": [values]}
            with self.subTest(values=values), self.assertRaises(
                StructuredAnswerError
            ) as caught:
                parse_answer_draft(
                    json.dumps(payload, ensure_ascii=False),
                    ("S1",),
                    (DraftPathSpec("answer.levels[{level}].feats"),),
                    (path,),
                    (contract,),
                )
            self.assertEqual(
                classify_draft_parse_failure(caught.exception),
                "invalid_selection",
            )

    def test_free_text_required_claim_uses_server_evidence(self) -> None:
        path = "answer.summary[conditional_branch]"
        payload = {"values": ["满足条件后进入进阶职业。[S9]"]}

        draft = parse_answer_draft(
            json.dumps(payload, ensure_ascii=False),
            ("S1",),
            (DraftPathSpec("answer.summary[{key}]"),),
            (path,),
            (DraftClaimContract(path, evidence_refs=("S1",)),),
        )

        self.assertEqual(draft.claims[0].evidence_refs, ("S1",))
        self.assertEqual(draft.claims[0].text, "满足条件后进入进阶职业。")

    def test_minimal_values_instruction_and_outer_shape_are_server_owned(self) -> None:
        path = "answer.levels[1].feats"
        contract = DraftClaimContract(
            path,
            selection_count=1,
            selection_options=(DraftSelectionOption("猛力攻击", ("S1",)),),
            text_template="1级选择专长：{values}。",
        )

        instruction = structured_draft_instruction(
            (DraftPathSpec("answer.levels[{level}].feats"),),
            (path,),
            ("S1",),
            (contract,),
        )

        self.assertIn('输出形状：{"values":[[]]}', instruction)
        self.assertIn('"kind":"selection"', instruction)
        self.assertIn('"catalog_1":["猛力攻击"]', instruction)
        self.assertNotIn('"required_output"', instruction)
        self.assertNotIn('"sections"', instruction)
        self.assertNotIn('"evidence_refs":', instruction)

        with self.assertRaises(StructuredAnswerError) as caught:
            parse_answer_draft(
                '{"values":[["猛力攻击"]],"heading":"forged"}',
                ("S1",),
                (DraftPathSpec("answer.levels[{level}].feats"),),
                (path,),
                (contract,),
            )
        self.assertEqual(
            classify_draft_parse_failure(caught.exception),
            "values_fields",
        )

    def test_minimal_values_count_has_bounded_failure_class(self) -> None:
        path = "answer.summary[value]"
        contract = DraftClaimContract(path, evidence_refs=("S1",))

        with self.assertRaises(StructuredAnswerError) as caught:
            parse_answer_draft(
                '{"values":[]}',
                ("S1",),
                (DraftPathSpec("answer.summary[{key}]"),),
                (path,),
                (contract,),
            )
        self.assertEqual(
            classify_draft_parse_failure(caught.exception),
            "values_count",
        )

        with self.assertRaises(StructuredAnswerError) as caught:
            parse_answer_draft(
                '{"values":{}}',
                ("S1",),
                (DraftPathSpec("answer.summary[{key}]"),),
                (path,),
                (contract,),
            )
        self.assertEqual(
            classify_draft_parse_failure(caught.exception),
            "values_shape",
        )

    def test_rejects_unreplaced_required_output_placeholders(self) -> None:
        payload = {
            "schema_version": 1,
            "sections": [
                {
                    "id": "required_section",
                    "heading": "REPLACE_WITH_SECTION_HEADING",
                    "claims": [
                        {
                            "id": "required_1",
                            "path": "answer.summary[conditional_branch]",
                            "text": "REPLACE_WITH_CLAIM_TEXT",
                            "evidence_refs": ["S1"],
                        }
                    ],
                }
            ],
        }

        with self.assertRaisesRegex(StructuredAnswerError, "placeholder"):
            parse_answer_draft(
                json.dumps(payload, ensure_ascii=False),
                ("S1",),
                (DraftPathSpec("answer.summary[{key}]"),),
                ("answer.summary[conditional_branch]",),
            )

    def test_rejects_model_injection_of_reserved_validation_marker(self) -> None:
        payload = draft_payload()
        payload["sections"][0]["claims"][0]["text"] = (
            "[[TRPGCLAIMPATH:answer.levels[9].feats]]\n选择专长：虚构专长。"
        )

        with self.assertRaisesRegex(StructuredAnswerError, "reserved validation marker"):
            parse_answer_draft(
                json.dumps(payload, ensure_ascii=False),
                ("S1", "S2"),
            )

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
                    "values": ["5级选择钢铁意志。"],
                },
                ensure_ascii=False,
            ),
            ("S1", "S2"),
            targets,
        )

        repaired = apply_repair_patch(draft, patch, targets)

        self.assertEqual(repaired.claims[0].text, "5级选择钢铁意志。")
        self.assertIs(repaired.claims[1], draft.claims[1])

    def test_server_reconstructs_fixed_patch_fields_and_rejects_wrong_count(self) -> None:
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
        patch = parse_repair_patch(
            json.dumps(
                {"values": ["5级选择钢铁意志。"]},
                ensure_ascii=False,
            ),
            ("S1", "S2"),
            targets,
        )

        self.assertEqual(patch.operations[0].claim_id, "level_5_feat")
        self.assertEqual(patch.operations[0].path, "answer.levels[5].feats")
        self.assertEqual(patch.operations[0].evidence_refs, ("S1",))
        self.assertEqual(patch.operations[0].issue_codes, ("missing",))
        repaired = apply_repair_patch(draft, patch, targets)
        self.assertEqual(repaired.claims[0].text, "5级选择钢铁意志。")
        self.assertIs(repaired.claims[1], draft.claims[1])

        with self.assertRaisesRegex(StructuredAnswerError, "exactly cover"):
            parse_repair_patch(
                json.dumps({"values": []}),
                ("S1", "S2"),
                targets,
            )

        with self.assertRaisesRegex(StructuredAnswerError, "reserved validation marker"):
            parse_repair_patch(
                json.dumps(
                    {
                        "values": [
                            "[[TRPGCLAIMPATH:answer.levels[6].feats]]\n篡改目标"
                        ],
                    }
                ),
                ("S1", "S2"),
                targets,
            )

    def test_typed_selection_repair_uses_minimal_values_and_server_template(self) -> None:
        path = "answer.levels[5].feats"
        draft = AnswerDraft(
            (
                AnswerSection(
                    "levels",
                    "等级",
                    (AnswerClaim("level_5", path, "5级没有选择专长。", ("S1",)),),
                ),
            )
        )
        issues = (
            ValidationIssue(
                "feat_timeline_slot_count",
                "需要两个专长",
                path=path,
                expected=2,
                actual=0,
                evidence_refs=("S1",),
            ),
        )
        targets = build_repair_targets(draft, issues)
        contract = DraftClaimContract(
            path,
            evidence_refs=("S1",),
            selection_count=2,
            selection_options=(
                DraftSelectionOption("猛力攻击", ("S2",)),
                DraftSelectionOption("顺势斩", ("S2",)),
            ),
            text_template="5级选择专长：{values}。",
        )

        instruction = repair_instruction(draft, issues, targets, (contract,))
        self.assertIn('"kind":"selection"', instruction)
        self.assertIn('"required_output":{"values":[[]]}', instruction)
        patch = parse_repair_patch(
            '{"values":[["猛力攻击","顺势斩"]]}',
            ("S1", "S2"),
            targets,
            (contract,),
        )
        repaired = apply_repair_patch(draft, patch, targets, (contract,))
        self.assertEqual(repaired.claims[0].text, "5级选择专长：猛力攻击、顺势斩。")
        self.assertEqual(repaired.claims[0].evidence_refs, ("S1", "S2"))

        wrapped_patch = parse_repair_patch(
            '{"required_output":{"values":[["猛力攻击","顺势斩"]]}}',
            ("S1", "S2"),
            targets,
            (contract,),
        )
        self.assertEqual(
            wrapped_patch.operations[0].replacement_text,
            "5级选择专长：猛力攻击、顺势斩。",
        )

        with self.assertRaisesRegex(StructuredAnswerError, "outside"):
            parse_repair_patch(
                '{"values":[["猛力攻击","虚构专长"]]}',
                ("S1", "S2"),
                targets,
                (contract,),
            )

        with self.assertRaisesRegex(StructuredAnswerError, "placeholder"):
            parse_repair_patch(
                json.dumps(
                    {
                        "values": ["REPLACE_WITH_CORRECTED_CLAIM_VALUE"],
                    }
                ),
                ("S1", "S2"),
                targets,
            )

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
            expected_reason = {
                "blocked": "nonrepairable",
                "missing": "path_not_found",
                "legacy": "missing_path",
            }[issue.code]
            with self.subTest(code=issue.code), self.assertRaises(RepairTargetError) as caught:
                build_repair_targets(draft, (issue,))
            self.assertEqual(caught.exception.reason, expected_reason)

    def test_maps_changed_issue_path_only_when_the_claim_is_unique(self) -> None:
        draft = AnswerDraft(
            (
                AnswerSection(
                    "summary",
                    "摘要",
                    (
                        AnswerClaim(
                            "recommendation",
                            "answer.summary[feat_eligibility]",
                            "建议选择虚构专长。",
                        ),
                    ),
                ),
            )
        )
        targets = build_repair_targets(
            draft,
            (
                ValidationIssue(
                    "unsupported_named_option",
                    "unsupported",
                    path="answer.feats[虚构专长]",
                    actual="虚构专长",
                ),
            ),
        )
        self.assertEqual(targets[0].claim_id, "recommendation")

        ambiguous = AnswerDraft(
            (
                AnswerSection(
                    "summary",
                    "摘要",
                    (
                        *draft.claims,
                        AnswerClaim(
                            "alternative",
                            "answer.other[alternative]",
                            "另一处也建议虚构专长。",
                        ),
                    ),
                ),
            )
        )
        with self.assertRaises(RepairTargetError) as caught:
            build_repair_targets(
                ambiguous,
                (
                    ValidationIssue(
                        "unsupported_named_option",
                        "unsupported",
                        path="answer.feats[虚构专长]",
                        actual="虚构专长",
                    ),
                ),
            )
        self.assertEqual(caught.exception.reason, "path_ambiguous")

    def test_bonus_feat_scope_uses_unique_actual_text_when_level_path_is_absent(self) -> None:
        draft = AnswerDraft(
            (
                AnswerSection(
                    "summary",
                    "摘要",
                    (
                        AnswerClaim(
                            "feat_summary",
                            "answer.summary[feat_eligibility]",
                            "法师5级奖励专长建议选择武器专攻。",
                        ),
                    ),
                ),
            )
        )
        targets = build_repair_targets(
            draft,
            (
                ValidationIssue(
                    "bonus_feat_scope",
                    "武器专攻不在奖励专长范围内",
                    path="answer.levels[5].feats",
                    actual="武器专攻",
                ),
            ),
        )

        self.assertEqual(targets[0].claim_id, "feat_summary")
        self.assertEqual(targets[0].path, "answer.summary[feat_eligibility]")

    def test_adapter_mapping_targets_only_the_declared_unique_sibling(self) -> None:
        draft = AnswerDraft(
            (
                AnswerSection(
                    "levels",
                    "等级",
                    (
                        AnswerClaim(
                            "level_5_spells",
                            "answer.levels[5].spells",
                            "法师5级法术位为错误值。",
                            ("S1",),
                        ),
                    ),
                ),
            )
        )
        mapping = RepairPathMapping(
            "answer.levels[{level}].spell_slots[{spell_level}]",
            "answer.levels[{level}].spells",
            ("spell_slot_count",),
        )

        targets = build_repair_targets(
            draft,
            (
                ValidationIssue(
                    "spell_slot_count",
                    "wrong count",
                    path="answer.levels[5].spell_slots[2]",
                    expected=2,
                    actual=3,
                    evidence_refs=("S1",),
                ),
            ),
            (mapping,),
        )

        self.assertEqual(targets[0].claim_id, "level_5_spells")
        self.assertEqual(targets[0].path, "answer.levels[5].spells")
        with self.assertRaises(RepairTargetError) as caught:
            build_repair_targets(
                draft,
                (
                    ValidationIssue(
                        "other_issue",
                        "wrong count",
                        path="answer.levels[5].spell_slots[2]",
                    ),
                ),
                (mapping,),
            )
        self.assertEqual(caught.exception.reason, "path_not_found")

    def test_minimal_repair_instruction_has_complete_fixed_operation_skeletons(self) -> None:
        draft = parse_answer_draft(
            json.dumps(draft_payload(), ensure_ascii=False),
            ("S1", "S2"),
        )
        issues = (
            ValidationIssue(
                "feat_timeline_slot_count",
                "missing feat",
                path="answer.levels[5].feats",
                expected=1,
                actual=0,
                evidence_refs=("S1",),
            ),
        )
        targets = build_repair_targets(draft, issues)

        instruction = repair_instruction(draft, issues, targets)

        self.assertIn('"required_output":{"values":[', instruction)
        self.assertIn('"claim_id":"level_5_feat"', instruction)
        self.assertIn('"path":"answer.levels[5].feats"', instruction)
        self.assertIn('"values":["REPLACE_WITH_CORRECTED_CLAIM_VALUE"]', instruction)
        self.assertNotIn('"draft":', instruction)
        self.assertNotIn("Schema 为 {schema_version", instruction)

    def test_repair_contexts_bind_each_same_code_issue_to_its_exact_target(self) -> None:
        draft = parse_answer_draft(
            json.dumps(draft_payload(), ensure_ascii=False),
            ("S1", "S2"),
        )
        issues = (
            ValidationIssue(
                "feat_timeline_slot_count",
                "level 5",
                path="answer.levels[5].feats",
                expected=1,
                actual=0,
                evidence_refs=("S1",),
            ),
            ValidationIssue(
                "feat_timeline_slot_count",
                "level 6",
                path="answer.levels[6].feats",
                expected=2,
                actual=1,
                evidence_refs=("S2",),
            ),
        )

        targets = build_repair_targets(draft, issues)
        payload = json.loads(repair_instruction(draft, issues, targets).split("\n", 1)[1])

        contexts = payload["target_contexts"]
        self.assertEqual(
            [item["issues"][0]["message"] for item in contexts],
            ["level 5", "level 6"],
        )
        self.assertTrue(all(len(item["issues"]) == 1 for item in contexts))

    def test_repair_patch_failures_have_bounded_shape_classifications(self) -> None:
        failures = (
            ("not json", "invalid_json"),
            (json.dumps({"schema_version": 3}), "invalid_fields"),
            (
                json.dumps({"values": {}}),
                "invalid_shape",
            ),
        )
        for raw, expected in failures:
            with self.subTest(expected=expected), self.assertRaises(
                StructuredAnswerError
            ) as caught:
                parse_repair_patch(raw, ("S1",), ())
            self.assertEqual(classify_repair_patch_failure(caught.exception), expected)

        self.assertEqual(
            classify_repair_patch_failure(
                StructuredAnswerError(
                    "repair patch does not exactly cover failed targets"
                )
            ),
            "target_mismatch",
        )

    def test_mixed_repairability_is_classified_without_partial_patch(self) -> None:
        draft = parse_answer_draft(
            json.dumps(draft_payload(), ensure_ascii=False),
            ("S1", "S2"),
        )
        issues = (
            ValidationIssue(
                "repairable",
                "repairable",
                path="answer.levels[5].feats",
            ),
            ValidationIssue(
                "manual_review",
                "manual review",
                path="answer.levels[6].feats",
                repairable=False,
            ),
        )

        with self.assertRaises(RepairTargetError) as caught:
            build_repair_targets(draft, issues)

        self.assertEqual(caught.exception.reason, "nonrepairable")


if __name__ == "__main__":
    unittest.main()
