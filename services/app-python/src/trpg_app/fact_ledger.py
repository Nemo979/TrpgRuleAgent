"""Rule-system-neutral facts, provenance, and validation results.

This module intentionally knows nothing about a particular game system. Rule
parsing, public prompt shapes, and answer validation live behind adapters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any, Mapping


FACT_LEDGER_CORE_SCHEMA_VERSION = 1
_DRAFT_PATH_TEMPLATE = re.compile(
    r"^answer(?:\.[A-Za-z_][A-Za-z0-9_]*|\[(?:[^\]\n{}]{1,80}|\{[a-z][a-z0-9_]*\})\])+$"
)
_DRAFT_PATH_FIELD = re.compile(r"\{([a-z][a-z0-9_]*)\}")
_ISSUE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


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
        if not _ISSUE_CODE.fullmatch(self.code) or not self.message.strip():
            raise ValueError("validation issue code or message is invalid")
        if not isinstance(self.severity, ValidationSeverity):
            raise ValueError("validation issue severity is invalid")
        if not isinstance(self.evidence_refs, tuple):
            raise ValueError("validation issue evidence refs must be a tuple")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("validation issue evidence refs must be unique")
        if any(not value.strip() for value in self.evidence_refs):
            raise ValueError("validation issue evidence refs must not be empty")


@dataclass(frozen=True)
class DraftPathSpec:
    """Adapter-owned canonical claim path and accepted model aliases."""

    canonical_template: str
    alias_templates: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.alias_templates, tuple):
            raise ValueError("draft path aliases must be a tuple")
        if len(set(self.alias_templates)) != len(self.alias_templates):
            raise ValueError("draft path aliases must be unique")
        templates = (self.canonical_template, *self.alias_templates)
        if any(not _DRAFT_PATH_TEMPLATE.fullmatch(value) for value in templates):
            raise ValueError("draft path template is invalid")
        canonical_fields = _DRAFT_PATH_FIELD.findall(self.canonical_template)
        if len(canonical_fields) != len(set(canonical_fields)):
            raise ValueError("canonical draft path fields must be unique")
        if any(
            set(_DRAFT_PATH_FIELD.findall(value)) != set(canonical_fields)
            for value in self.alias_templates
        ):
            raise ValueError("draft path alias fields are incompatible")


@dataclass(frozen=True)
class DraftSelectionOption:
    """One adapter-published value and its server-owned provenance."""

    value: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.value.strip() or len(self.value) > 160:
            raise ValueError("draft selection option is invalid")
        if not isinstance(self.evidence_refs, tuple) or not self.evidence_refs:
            raise ValueError("draft selection option requires evidence refs")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("draft selection option evidence refs must be unique")
        if any(not isinstance(value, str) or not value.strip() for value in self.evidence_refs):
            raise ValueError("draft selection option evidence ref is invalid")


@dataclass(frozen=True)
class DraftSelectionGroup:
    """One ordered selection sub-slot with its own finite option domain."""

    label: str
    count: int
    option_values: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.label.strip() or len(self.label) > 80:
            raise ValueError("draft selection group label is invalid")
        if type(self.count) is not int or self.count < 1:
            raise ValueError("draft selection group count is invalid")
        if not isinstance(self.option_values, tuple) or self.count > len(
            self.option_values
        ):
            raise ValueError("draft selection group options are invalid")
        if len(set(self.option_values)) != len(self.option_values) or any(
            not isinstance(value, str) or not value.strip()
            for value in self.option_values
        ):
            raise ValueError("draft selection group option value is invalid")


@dataclass(frozen=True)
class DraftClaimContract:
    """Server-owned shape, provenance, and optional finite selection domain."""

    path: str
    evidence_refs: tuple[str, ...] = ()
    selection_count: int = 0
    selection_options: tuple[DraftSelectionOption, ...] = ()
    selection_groups: tuple[DraftSelectionGroup, ...] = ()
    allow_selection_fallback: bool = False
    text_template: str = ""
    server_text: str = ""
    semantic_fallback_text: str = ""
    value_description: str = ""
    required_term_groups: tuple[tuple[str, ...], ...] = ()
    forbidden_terms: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _DRAFT_PATH_TEMPLATE.fullmatch(self.path) or "{" in self.path:
            raise ValueError("draft claim contract path is invalid")
        if not isinstance(self.evidence_refs, tuple):
            raise ValueError("draft claim contract evidence refs must be a tuple")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("draft claim contract evidence refs must be unique")
        if any(not isinstance(value, str) or not value.strip() for value in self.evidence_refs):
            raise ValueError("draft claim contract evidence ref is invalid")
        if type(self.selection_count) is not int or self.selection_count < 0:
            raise ValueError("draft claim contract selection count is invalid")
        if not isinstance(self.selection_options, tuple):
            raise ValueError("draft claim contract options must be a tuple")
        if any(not isinstance(value, DraftSelectionOption) for value in self.selection_options):
            raise ValueError("draft claim contract option is invalid")
        option_values = [item.value for item in self.selection_options]
        if len(set(option_values)) != len(option_values):
            raise ValueError("draft claim contract option values must be unique")
        if self.selection_count:
            if self.server_text:
                raise ValueError("selection contract cannot publish server text")
            if self.selection_count > len(self.selection_options):
                raise ValueError("draft claim contract has too few selection options")
            if self.text_template.count("{values}") != 1:
                raise ValueError("selection contract requires one values placeholder")
            if not isinstance(self.selection_groups, tuple) or any(
                not isinstance(group, DraftSelectionGroup)
                for group in self.selection_groups
            ):
                raise ValueError("draft selection groups are invalid")
            if self.selection_groups:
                if sum(group.count for group in self.selection_groups) != self.selection_count:
                    raise ValueError("draft selection group counts do not match")
                if any(
                    set(group.option_values) - set(option_values)
                    for group in self.selection_groups
                ):
                    raise ValueError("draft selection group is outside the option domain")
            if self.allow_selection_fallback and not self.selection_groups:
                raise ValueError("selection fallback requires ordered selection groups")
        elif self.selection_options or self.selection_groups or self.text_template:
            raise ValueError("free-text contract cannot publish selection fields")
        elif self.allow_selection_fallback:
            raise ValueError("free-text contract cannot enable selection fallback")
        if self.server_text:
            if not self.server_text.strip() or len(self.server_text) > 4_000:
                raise ValueError("draft claim contract server text is invalid")
            if "[[TRPGCLAIMPATH:" in self.server_text:
                raise ValueError("draft claim contract server text contains a reserved marker")
        if self.semantic_fallback_text:
            if not self.semantic_fallback_text.strip() or len(self.semantic_fallback_text) > 1_000:
                raise ValueError("draft claim contract semantic fallback is invalid")
            if "[[TRPGCLAIMPATH:" in self.semantic_fallback_text:
                raise ValueError("draft claim contract semantic fallback contains a reserved marker")
            if self.server_text or self.selection_count:
                raise ValueError("only free-text contracts may publish a semantic fallback")
        if not isinstance(self.value_description, str) or len(self.value_description) > 500:
            raise ValueError("draft claim contract value description is invalid")
        if self.server_text and self.value_description:
            raise ValueError("server claim contract cannot request a model value")
        if not isinstance(self.required_term_groups, tuple) or any(
            not isinstance(group, tuple)
            or not group
            or any(not isinstance(term, str) or not term for term in group)
            for group in self.required_term_groups
        ):
            raise ValueError("draft claim contract required term groups are invalid")
        if not isinstance(self.forbidden_terms, tuple) or any(
            not isinstance(term, str) or not term for term in self.forbidden_terms
        ):
            raise ValueError("draft claim contract forbidden terms are invalid")
        if self.server_text and (self.required_term_groups or self.forbidden_terms):
            raise ValueError("server claim contract cannot validate a model value")
        if self.semantic_fallback_text and not self.required_term_groups:
            raise ValueError("semantic fallback requires a bounded semantic scope")


@dataclass(frozen=True)
class RepairPathMapping:
    """Adapter-owned mapping from a validator path to one draft claim path."""

    issue_path_template: str
    target_path_template: str
    issue_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.issue_codes, tuple) or not self.issue_codes:
            raise ValueError("repair path mapping issue codes must be a non-empty tuple")
        if len(set(self.issue_codes)) != len(self.issue_codes):
            raise ValueError("repair path mapping issue codes must be unique")
        if any(not _ISSUE_CODE.fullmatch(value) for value in self.issue_codes):
            raise ValueError("repair path mapping issue code is invalid")
        if not _DRAFT_PATH_TEMPLATE.fullmatch(self.issue_path_template):
            raise ValueError("repair issue path template is invalid")
        if not _DRAFT_PATH_TEMPLATE.fullmatch(self.target_path_template):
            raise ValueError("repair target path template is invalid")
        issue_fields = _DRAFT_PATH_FIELD.findall(self.issue_path_template)
        target_fields = _DRAFT_PATH_FIELD.findall(self.target_path_template)
        if len(issue_fields) != len(set(issue_fields)):
            raise ValueError("repair issue path fields must be unique")
        if len(target_fields) != len(set(target_fields)):
            raise ValueError("repair target path fields must be unique")
        if not set(target_fields).issubset(issue_fields):
            raise ValueError("repair target path fields must come from the issue path")


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
