from __future__ import annotations

import unittest

from trpg_app.fact_ledger import (
    FactDerivation,
    FactLedger,
    FactRecord,
    FactStatus,
    ValidationIssue,
)


class FactLedgerCoreTest(unittest.TestCase):
    def test_preserves_generic_fact_lifecycle_and_provenance(self) -> None:
        ledger = FactLedger(
            adapter_id="catalog-v1",
            adapter_version=2,
            goal="compare two catalog entries",
            registered_evidence_refs=("D1", "D2"),
            records=(
                FactRecord(
                    adapter_id="catalog-v1",
                    adapter_version=2,
                    subject="entry-a",
                    predicate="capacity",
                    value=8,
                    value_type="integer",
                    status=FactStatus.KNOWN,
                    derivation=FactDerivation.OBSERVED,
                    evidence_refs=("D1",),
                ),
                FactRecord(
                    adapter_id="catalog-v1",
                    adapter_version=2,
                    subject="entry-b",
                    predicate="availability",
                    status=FactStatus.UNKNOWN,
                ),
            ),
        )

        self.assertEqual(ledger.record_count, 2)
        self.assertEqual(ledger.public()["records"][0]["status"], "known")
        self.assertEqual(ledger.public()["records"][1]["status"], "unknown")

    def test_rejects_known_fact_without_registered_provenance(self) -> None:
        with self.assertRaisesRegex(ValueError, "require evidence refs"):
            FactLedger(
                adapter_id="catalog-v1",
                adapter_version=1,
                goal="inspect inventory",
                registered_evidence_refs=(),
                records=(
                    FactRecord(
                        adapter_id="catalog-v1",
                        adapter_version=1,
                        subject="entry-a",
                        predicate="stock",
                        value=3,
                    ),
                ),
            )

    def test_rejects_cross_adapter_and_unregistered_evidence(self) -> None:
        for record, message in (
            (
                FactRecord(
                    adapter_id="other-v1",
                    adapter_version=1,
                    subject="entry-a",
                    predicate="stock",
                    value=3,
                    evidence_refs=("D1",),
                ),
                "adapter_id",
            ),
            (
                FactRecord(
                    adapter_id="catalog-v1",
                    adapter_version=1,
                    subject="entry-a",
                    predicate="stock",
                    value=3,
                    evidence_refs=("D2",),
                ),
                "unregistered evidence",
            ),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                FactLedger(
                    adapter_id="catalog-v1",
                    adapter_version=1,
                    goal="inspect inventory",
                    registered_evidence_refs=("D1",),
                    records=(record,),
                )

    def test_rejects_mutable_or_non_core_collections(self) -> None:
        with self.assertRaisesRegex(ValueError, "records must be a tuple"):
            FactLedger(
                adapter_id="catalog-v1",
                adapter_version=1,
                goal="inspect inventory",
                registered_evidence_refs=(),
                records=[],  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(ValueError, "contain FactRecord"):
            FactLedger(
                adapter_id="catalog-v1",
                adapter_version=1,
                goal="inspect inventory",
                registered_evidence_refs=(),
                records=(object(),),  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(ValueError, "must be a tuple"):
            FactRecord(
                adapter_id="catalog-v1",
                adapter_version=1,
                subject="entry-a",
                predicate="availability",
                status=FactStatus.UNKNOWN,
                evidence_refs=[],  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(ValueError, "must be a tuple"):
            ValidationIssue(
                "invalid",
                "invalid value",
                evidence_refs=[],  # type: ignore[arg-type]
            )


if __name__ == "__main__":
    unittest.main()
