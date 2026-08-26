from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace

from trpg_app.fact_ledger import (
    DraftClaimContract,
    DraftPathSpec,
    FactLedger,
    FactRecord,
    RepairPathMapping,
    ValidationIssue,
)
from trpg_app.fact_ledger_adapter import (
    FACT_LEDGER_ADAPTER_PROTOCOL_VERSION,
    AdapterKey,
    AdapterStatus,
    FactLedgerRegistry,
    LibraryIdentity,
)


IDENTITY = LibraryIdentity("catalog", "Catalog", "2026")
KEY = AdapterKey("catalog", "Catalog", "2026")


class StubAdapter:
    adapter_id = "catalog"
    adapter_version = 3
    protocol_version = FACT_LEDGER_ADAPTER_PROTOCOL_VERSION
    keys = (KEY,)
    supports_evidence_only_validation = False

    def __init__(self) -> None:
        self.build_calls = 0
        self.public_calls = 0
        self.validate_calls = 0

    def build(self, goal, evidence):
        self.build_calls += 1
        return FactLedger(
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            goal=goal,
            registered_evidence_refs=tuple(item.label for item in evidence),
            records=(
                FactRecord(
                    adapter_id=self.adapter_id,
                    adapter_version=self.adapter_version,
                    subject="entry-a",
                    predicate="capacity",
                    value=8,
                    evidence_refs=(evidence[0].label,),
                ),
            ),
        )

    def public(self, ledger):
        self.public_calls += 1
        return {"count": ledger.record_count}

    def validate(self, content, ledger):
        self.validate_calls += 1
        return (ValidationIssue("capacity", "capacity is inconsistent"),) if "9" in content else ()

    def draft_path_specs(self, ledger):
        return (DraftPathSpec("answer.entries[{key}].capacity"),)

    def required_draft_paths(self, ledger):
        return ("answer.entries[entry-a].capacity",)

    def draft_claim_contracts(self, ledger):
        return (
            DraftClaimContract(
                "answer.entries[entry-a].capacity",
                evidence_refs=("D1",),
            ),
        )

    def repair_path_mappings(self, ledger):
        return (
            RepairPathMapping(
                "answer.entries[{key}].raw_capacity",
                "answer.entries[{key}].capacity",
                ("capacity",),
            ),
        )


def evidence(*, ruleset_id="catalog", document_id="entry-a"):
    return [("D1", {"id": document_id, "rulesetId": ruleset_id, "title": "A", "content": "capacity 8"})]


class FactLedgerAdapterRegistryTest(unittest.TestCase):
    def test_selects_only_the_exact_identity(self) -> None:
        adapter = StubAdapter()
        registry = FactLedgerRegistry((adapter,))

        runtime = registry.build(IDENTITY, "compare", evidence())

        self.assertTrue(runtime.active)
        self.assertEqual(runtime.status, AdapterStatus.MATCHED)
        self.assertEqual(runtime.public(), {"count": 1})
        self.assertEqual([item.code for item in runtime.validate("capacity 9")], ["capacity"])
        self.assertEqual(
            runtime.draft_path_specs(),
            (DraftPathSpec("answer.entries[{key}].capacity"),),
        )
        self.assertEqual(
            runtime.required_draft_paths(),
            ("answer.entries[entry-a].capacity",),
        )
        self.assertEqual(
            runtime.draft_claim_contracts(),
            (
                DraftClaimContract(
                    "answer.entries[entry-a].capacity",
                    evidence_refs=("D1",),
                ),
            ),
        )
        self.assertEqual(
            runtime.repair_path_mappings(),
            (
                RepairPathMapping(
                    "answer.entries[{key}].raw_capacity",
                    "answer.entries[{key}].capacity",
                    ("capacity",),
                ),
            ),
        )
        self.assertEqual((adapter.build_calls, adapter.public_calls, adapter.validate_calls), (1, 1, 1))

        for identity in (
            replace(IDENTITY, id="Catalog"),
            replace(IDENTITY, system="catalog"),
            replace(IDENTITY, edition="2026.1"),
        ):
            with self.subTest(identity=identity):
                result = registry.build(identity, "compare", evidence())
                self.assertEqual(result.status, AdapterStatus.UNREGISTERED)
        self.assertEqual(adapter.build_calls, 1)

    def test_ignores_manifest_fields_outside_the_identity(self) -> None:
        adapter = StubAdapter()
        registry = FactLedgerRegistry((adapter,))
        manifest_like = type(
            "Manifest",
            (),
            {
                "id": "catalog",
                "system": "Catalog",
                "edition": "2026",
                "name": "Renamed catalog",
                "revision": "new-revision",
                "aliases": ("C",),
            },
        )()

        result = registry.build(
            LibraryIdentity.from_manifest(manifest_like),
            "compare",
            evidence(),
        )

        self.assertTrue(result.active)

    def test_incompatible_adapter_never_builds(self) -> None:
        adapter = StubAdapter()
        adapter.protocol_version += 1

        result = FactLedgerRegistry((adapter,)).build(IDENTITY, "compare", evidence())

        self.assertEqual(result.status, AdapterStatus.INCOMPATIBLE)
        self.assertEqual(adapter.build_calls, 0)

    def test_bad_evidence_scope_fails_open_before_build(self) -> None:
        for values in (
            evidence(ruleset_id="other"),
            evidence(ruleset_id=""),
            evidence(document_id=""),
            [*evidence(), *evidence(document_id="entry-b")],
            [
                *evidence(),
                (
                    "D2",
                    {
                        "id": "entry-a",
                        "rulesetId": "catalog",
                        "title": "duplicate",
                        "content": "capacity 8",
                    },
                ),
            ],
        ):
            with self.subTest(values=values):
                adapter = StubAdapter()
                result = FactLedgerRegistry((adapter,)).build(IDENTITY, "compare", values)
                self.assertEqual(result.status, AdapterStatus.BUILD_FAILED)
                self.assertEqual(adapter.build_calls, 0)

    def test_build_publication_and_validation_failures_are_safe(self) -> None:
        class FailingAdapter(StubAdapter):
            def build(self, goal, evidence):
                raise RuntimeError("parser failed")

        result = FactLedgerRegistry((FailingAdapter(),)).build(IDENTITY, "compare", evidence())
        self.assertEqual(result.status, AdapterStatus.BUILD_FAILED)

        class BoundaryAdapter(StubAdapter):
            def public(self, ledger):
                raise RuntimeError("view failed")

        result = FactLedgerRegistry((BoundaryAdapter(),)).build(IDENTITY, "compare", evidence())
        self.assertEqual(result.public(), {})
        self.assertEqual(result.status, AdapterStatus.PUBLICATION_FAILED)

        class NonSerializableAdapter(StubAdapter):
            def public(self, ledger):
                return {"invalid": {"set-value"}}

        result = FactLedgerRegistry((NonSerializableAdapter(),)).build(
            IDENTITY,
            "compare",
            evidence(),
        )
        self.assertEqual(result.public(), {})
        self.assertEqual(result.status, AdapterStatus.PUBLICATION_FAILED)

        class ValidationAdapter(StubAdapter):
            def validate(self, content, ledger):
                raise RuntimeError("validator failed")

        result = FactLedgerRegistry((ValidationAdapter(),)).build(IDENTITY, "compare", evidence())
        self.assertEqual(result.validate("answer"), ())
        self.assertEqual(result.status, AdapterStatus.VALIDATION_FAILED)

        class NonCanonicalRequiredPathAdapter(StubAdapter):
            def required_draft_paths(self, ledger):
                return ("answer.other[value]",)

        result = FactLedgerRegistry((NonCanonicalRequiredPathAdapter(),)).build(
            IDENTITY,
            "compare",
            evidence(),
        )
        self.assertEqual(result.required_draft_paths(), ())
        self.assertEqual(result.status, AdapterStatus.PUBLICATION_FAILED)

        class ForgedIssueAdapter(StubAdapter):
            def validate(self, content, ledger):
                return (
                    ValidationIssue(
                        "forged",
                        "forged provenance",
                        evidence_refs=("FORGED",),
                    ),
                )

        result = FactLedgerRegistry((ForgedIssueAdapter(),)).build(
            IDENTITY,
            "compare",
            evidence(),
        )
        self.assertEqual(result.validate("answer"), ())
        self.assertEqual(result.status, AdapterStatus.VALIDATION_FAILED)

        class NonCanonicalRepairTargetAdapter(StubAdapter):
            def repair_path_mappings(self, ledger):
                return (
                    RepairPathMapping(
                        "answer.entries[{key}].raw_capacity",
                        "answer.other[{key}]",
                        ("capacity",),
                    ),
                )

        result = FactLedgerRegistry((NonCanonicalRepairTargetAdapter(),)).build(
            IDENTITY,
            "compare",
            evidence(),
        )
        self.assertEqual(result.repair_path_mappings(), ())
        self.assertEqual(result.status, AdapterStatus.PUBLICATION_FAILED)

        class IncompleteClaimContractAdapter(StubAdapter):
            def draft_claim_contracts(self, ledger):
                return ()

        result = FactLedgerRegistry((IncompleteClaimContractAdapter(),)).build(
            IDENTITY,
            "compare",
            evidence(),
        )
        self.assertEqual(result.draft_claim_contracts(), ())
        self.assertEqual(result.status, AdapterStatus.PUBLICATION_FAILED)

        class ForgedClaimContractAdapter(StubAdapter):
            def draft_claim_contracts(self, ledger):
                return (
                    DraftClaimContract(
                        "answer.entries[entry-a].capacity",
                        evidence_refs=("FORGED",),
                    ),
                )

        result = FactLedgerRegistry((ForgedClaimContractAdapter(),)).build(
            IDENTITY,
            "compare",
            evidence(),
        )
        self.assertEqual(result.draft_claim_contracts(), ())
        self.assertEqual(result.status, AdapterStatus.PUBLICATION_FAILED)

    def test_rejects_forged_or_non_core_ledgers(self) -> None:
        class ForgedEvidenceAdapter(StubAdapter):
            def build(self, goal, evidence):
                return FactLedger(
                    adapter_id=self.adapter_id,
                    adapter_version=self.adapter_version,
                    goal=goal,
                    registered_evidence_refs=("FORGED",),
                    records=(
                        FactRecord(
                            adapter_id=self.adapter_id,
                            adapter_version=self.adapter_version,
                            subject="entry-a",
                            predicate="capacity",
                            value=8,
                            evidence_refs=("FORGED",),
                        ),
                    ),
                )

        class ChangedGoalAdapter(StubAdapter):
            def build(self, goal, evidence):
                value = super().build(goal, evidence)
                return replace(value, goal="different goal")

        class ChangedSchemaAdapter(StubAdapter):
            def build(self, goal, evidence):
                value = super().build(goal, evidence)
                return replace(value, schema_version=2)

        class FakeLedgerAdapter(StubAdapter):
            def build(self, goal, evidence):
                return SimpleNamespace(
                    adapter_id=self.adapter_id,
                    adapter_version=self.adapter_version,
                    goal=goal,
                    registered_evidence_refs=("D1",),
                    records=(),
                    record_count=0,
                    schema_version=1,
                )

        for adapter in (
            ForgedEvidenceAdapter(),
            ChangedGoalAdapter(),
            ChangedSchemaAdapter(),
            FakeLedgerAdapter(),
        ):
            with self.subTest(adapter=type(adapter).__name__):
                result = FactLedgerRegistry((adapter,)).build(
                    IDENTITY,
                    "compare",
                    evidence(),
                )
                self.assertEqual(result.status, AdapterStatus.BUILD_FAILED)
                self.assertIsNone(result.ledger)

    def test_evidence_normalization_errors_fail_open(self) -> None:
        def broken_evidence():
            yield from evidence()
            raise RuntimeError("broken iterator")

        for values in (
            [("D1", object())],
            broken_evidence(),
        ):
            with self.subTest(values=values):
                result = FactLedgerRegistry((StubAdapter(),)).build(
                    IDENTITY,
                    "compare",
                    values,
                )
                self.assertEqual(result.status, AdapterStatus.BUILD_FAILED)

    def test_empty_ledger_requires_explicit_validation_capability(self) -> None:
        class EmptyAdapter(StubAdapter):
            def build(self, goal, evidence):
                self.build_calls += 1
                return FactLedger(
                    adapter_id=self.adapter_id,
                    adapter_version=self.adapter_version,
                    goal=goal,
                    registered_evidence_refs=tuple(item.label for item in evidence),
                )

        result = FactLedgerRegistry((EmptyAdapter(),)).build(IDENTITY, "compare", evidence())
        self.assertEqual(result.status, AdapterStatus.NO_FACTS)

        validating = EmptyAdapter()
        validating.supports_evidence_only_validation = True
        result = FactLedgerRegistry((validating,)).build(IDENTITY, "compare", evidence())
        self.assertTrue(result.active)

        no_evidence = EmptyAdapter()
        result = FactLedgerRegistry((no_evidence,)).build(IDENTITY, "compare", [])
        self.assertEqual(result.status, AdapterStatus.NO_FACTS)
        self.assertEqual(no_evidence.build_calls, 0)


if __name__ == "__main__":
    unittest.main()
