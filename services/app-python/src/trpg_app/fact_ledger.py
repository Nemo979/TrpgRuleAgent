"""Rule-system-neutral facts, provenance, and validation results.

This module intentionally knows nothing about a particular game system. Rule
parsing, public prompt shapes, and answer validation live behind adapters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


FACT_LEDGER_CORE_SCHEMA_VERSION = 1


class FactStatus(str, Enum):
    """Confidence state for a fact recorded from the current evidence set."""

    KNOWN = "known"
    UNKNOWN = "unknown"
    CONFLICTING = "conflicting"


class FactDerivation(str, Enum):
    """Whether a fact was read directly or deterministically derived."""

    OBSERVED = "observed"
    DERIVED = "derived"


class ValidationSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class EvidenceDocument:
    """A labeled document made available to exactly one ledger build."""

    label: str
    title: str
    content: str
    library_id: str = ""
    document_id: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("evidence label must not be empty")
        if not isinstance(self.metadata, Mapping):
            raise ValueError("evidence metadata must be a mapping")


@dataclass(frozen=True)
class FactRecord:
    """One adapter-owned fact with explicit provenance and lifecycle state."""

    adapter_id: str
    adapter_version: int
    subject: str
    predicate: str
    value: Any = None
    value_type: str = "value"
    status: FactStatus = FactStatus.KNOWN
    derivation: FactDerivation = FactDerivation.OBSERVED
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.adapter_id.strip():
            raise ValueError("fact adapter_id must not be empty")
        if self.adapter_version < 1:
            raise ValueError("fact adapter_version must be positive")
        if not self.subject.strip() or not self.predicate.strip():
            raise ValueError("fact subject and predicate must not be empty")
        if not self.value_type.strip():
            raise ValueError("fact value_type must not be empty")
        if not isinstance(self.status, FactStatus):
            raise ValueError("fact status is invalid")
        if not isinstance(self.derivation, FactDerivation):
            raise ValueError("fact derivation is invalid")
        if not isinstance(self.evidence_refs, tuple):
            raise ValueError("fact evidence refs must be a tuple")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("fact evidence refs must be unique")
        if any(not value.strip() for value in self.evidence_refs):
            raise ValueError("fact evidence refs must not be empty")

    def public(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "subject": self.subject,
            "predicate": self.predicate,
            "value": self.value,
            "value_type": self.value_type,
            "status": self.status.value,
            "derivation": self.derivation.value,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class ValidationIssue:
    """A deterministic answer validation failure returned by an adapter."""

    code: str
    message: str
    path: str = ""
    severity: ValidationSeverity = ValidationSeverity.ERROR
    expected: Any = None
    actual: Any = None
    evidence_refs: tuple[str, ...] = ()
    repairable: bool = True

    def __post_init__(self) -> None:
        if not self.code.strip() or not self.message.strip():
            raise ValueError("validation issue code and message must not be empty")
        if not isinstance(self.severity, ValidationSeverity):
            raise ValueError("validation issue severity is invalid")
        if not isinstance(self.evidence_refs, tuple):
            raise ValueError("validation issue evidence refs must be a tuple")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("validation issue evidence refs must be unique")
        if any(not value.strip() for value in self.evidence_refs):
            raise ValueError("validation issue evidence refs must not be empty")


@dataclass(frozen=True)
class FactLedger:
    """A generic ledger whose records and opaque data belong to one adapter."""

    adapter_id: str
    adapter_version: int
    goal: str
    registered_evidence_refs: tuple[str, ...]
    records: tuple[FactRecord, ...] = ()
    adapter_data: Any = field(default=None, repr=False, compare=False)
    schema_version: int = FACT_LEDGER_CORE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.adapter_id.strip():
            raise ValueError("adapter_id must not be empty")
        if self.adapter_version < 1:
            raise ValueError("adapter_version must be positive")
        if self.schema_version < 1:
            raise ValueError("schema_version must be positive")
        if not isinstance(self.registered_evidence_refs, tuple):
            raise ValueError("registered evidence refs must be a tuple")
        if not isinstance(self.records, tuple):
            raise ValueError("ledger records must be a tuple")
        registered = set(self.registered_evidence_refs)
        if len(registered) != len(self.registered_evidence_refs):
            raise ValueError("registered evidence refs must be unique")
        if any(not value.strip() for value in self.registered_evidence_refs):
            raise ValueError("registered evidence refs must not be empty")
        for record in self.records:
            if not isinstance(record, FactRecord):
                raise ValueError("ledger records must contain FactRecord values")
            if record.adapter_id != self.adapter_id:
                raise ValueError("record adapter_id does not match ledger adapter")
            if record.adapter_version != self.adapter_version:
                raise ValueError("record adapter_version does not match ledger adapter")
            missing = set(record.evidence_refs) - registered
            if missing:
                raise ValueError(
                    "record references unregistered evidence: "
                    + ", ".join(sorted(missing))
                )
            if (
                record.status in {FactStatus.KNOWN, FactStatus.CONFLICTING}
                and not record.evidence_refs
            ):
                raise ValueError("known or conflicting facts require evidence refs")

    @property
    def record_count(self) -> int:
        return len(self.records)

    def public(self) -> dict[str, Any]:
        """Generic representation used only when an adapter has no custom view."""

        return {
            "schema_version": self.schema_version,
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "records": [record.public() for record in self.records],
        }
