"""Rule-system-neutral Fact Ledger adapter protocol and registry."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Protocol, runtime_checkable

from .fact_ledger import (
    FACT_LEDGER_CORE_SCHEMA_VERSION,
    EvidenceDocument,
    FactLedger,
    ValidationIssue,
    DraftPathSpec,
)


FACT_LEDGER_ADAPTER_PROTOCOL_VERSION = 2


def _identity_part(value: str) -> str:
    return value.strip()


@dataclass(frozen=True)
class LibraryIdentity:
    id: str
    system: str
    edition: str

    @classmethod
    def from_manifest(cls, manifest: Any) -> "LibraryIdentity":
        return cls(
            id=str(manifest.id),
            system=str(manifest.system),
            edition=str(manifest.edition),
        )


@dataclass(frozen=True)
class AdapterKey:
    library_id: str
    system: str
    edition: str

    @classmethod
    def from_identity(cls, identity: LibraryIdentity) -> "AdapterKey":
        return cls(
            library_id=_identity_part(identity.id),
            system=_identity_part(identity.system),
            edition=_identity_part(identity.edition),
        )


@runtime_checkable
class FactLedgerAdapter(Protocol):
    adapter_id: str
    adapter_version: int
    protocol_version: int
    keys: tuple[AdapterKey, ...]
    supports_evidence_only_validation: bool

    def build(
        self,
        goal: str,
        evidence: tuple[EvidenceDocument, ...],
    ) -> FactLedger:
        ...

    def validate(
        self,
        content: str,
        ledger: FactLedger,
    ) -> tuple[ValidationIssue, ...]:
        ...

    def public(self, ledger: FactLedger) -> dict[str, Any]:
        ...

    def draft_path_specs(self, ledger: FactLedger) -> tuple[DraftPathSpec, ...]:
        ...


class AdapterStatus(str, Enum):
    DISABLED = "disabled"
    PLANNER_DISABLED = "planner_disabled"
    NOT_APPLICABLE = "not_applicable"
    MATCHED = "matched"
    UNREGISTERED = "unregistered"
    INCOMPATIBLE = "incompatible"
    BUILD_FAILED = "build_failed"
    NO_FACTS = "no_facts"
    PUBLICATION_FAILED = "publication_failed"
    VALIDATION_FAILED = "validation_failed"


@dataclass
class FactLedgerRuntime:
    """One fail-open adapter execution bound to a single rule turn."""

    status: AdapterStatus
    reason: str = ""
    adapter: FactLedgerAdapter | None = None
    ledger: FactLedger | None = None
    build_seconds: float = 0.0
    validation_seconds: float = 0.0

    @property
    def active(self) -> bool:
        return (
            self.status is AdapterStatus.MATCHED
            and self.adapter is not None
            and self.ledger is not None
        )

    @property
    def adapter_id(self) -> str:
        return self.adapter.adapter_id if self.adapter is not None else ""

    @property
    def adapter_version(self) -> int:
        return self.adapter.adapter_version if self.adapter is not None else 0

    @property
    def record_count(self) -> int:
        return self.ledger.record_count if self.ledger is not None else 0

    def public(self) -> dict[str, Any]:
        if not self.active:
            return {}
        assert self.adapter is not None and self.ledger is not None
        try:
            value = self.adapter.public(self.ledger)
            if not isinstance(value, dict):
                raise TypeError("adapter public view must be a mapping")
            json.dumps(value, ensure_ascii=False)
            return value
        except Exception as error:  # adapters are an optional, fail-open boundary
            self.status = AdapterStatus.PUBLICATION_FAILED
            self.reason = f"adapter public view failed: {type(error).__name__}"
            return {}

    def validate(self, content: str) -> tuple[ValidationIssue, ...]:
        if not self.active:
            return ()
        assert self.adapter is not None and self.ledger is not None
        started = time.monotonic()
        try:
            issues = tuple(self.adapter.validate(content, self.ledger))
            registered = set(self.ledger.registered_evidence_refs)
            for issue in issues:
                if not isinstance(issue, ValidationIssue):
                    raise TypeError("adapter validation result is invalid")
                if set(issue.evidence_refs) - registered:
                    raise ValueError("validation issue references unregistered evidence")
            return issues
        except Exception as error:  # adapters must never block the normal answer path
            self.status = AdapterStatus.VALIDATION_FAILED
            self.reason = f"adapter validation failed: {type(error).__name__}"
            return ()
        finally:
            self.validation_seconds += time.monotonic() - started

    def draft_path_specs(self) -> tuple[DraftPathSpec, ...]:
        if not self.active:
            return ()
        assert self.adapter is not None and self.ledger is not None
        try:
            specs = tuple(self.adapter.draft_path_specs(self.ledger))
            if not specs:
                raise ValueError("adapter must publish draft path specs")
            if any(not isinstance(spec, DraftPathSpec) for spec in specs):
                raise TypeError("adapter draft path spec is invalid")
            return specs
        except Exception as error:
            self.status = AdapterStatus.PUBLICATION_FAILED
            self.reason = f"adapter draft path publication failed: {type(error).__name__}"
            return ()


class FactLedgerRegistry:
    """Exact `(library id, system, edition)` adapter selection."""

    def __init__(self, adapters: Iterable[FactLedgerAdapter] = ()) -> None:
        self._adapters: dict[AdapterKey, FactLedgerAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: FactLedgerAdapter) -> None:
        if not adapter.adapter_id.strip():
            raise ValueError("adapter_id must not be empty")
        if adapter.adapter_version < 1:
            raise ValueError("adapter_version must be positive")
        if not adapter.keys:
            raise ValueError("adapter must register at least one library identity")
        if not isinstance(adapter.supports_evidence_only_validation, bool):
            raise ValueError("adapter validation capability must be a boolean")
        for key in adapter.keys:
            if not all((key.library_id, key.system, key.edition)):
                raise ValueError("adapter key fields must not be empty")
            normalized = AdapterKey.from_identity(
                LibraryIdentity(key.library_id, key.system, key.edition)
            )
            if key != normalized:
                raise ValueError("adapter key fields must already be stripped")
            if key in self._adapters:
                raise ValueError(f"duplicate Fact Ledger adapter key: {key}")
        for key in adapter.keys:
            self._adapters[key] = adapter

    def resolve(self, identity: LibraryIdentity) -> FactLedgerRuntime:
        adapter = self._adapters.get(AdapterKey.from_identity(identity))
        if adapter is None:
            return FactLedgerRuntime(
                status=AdapterStatus.UNREGISTERED,
                reason="no adapter registered for the exact library identity",
            )
        if adapter.protocol_version != FACT_LEDGER_ADAPTER_PROTOCOL_VERSION:
            return FactLedgerRuntime(
                status=AdapterStatus.INCOMPATIBLE,
                reason=(
                    "adapter protocol version "
                    f"{adapter.protocol_version} is incompatible with "
                    f"{FACT_LEDGER_ADAPTER_PROTOCOL_VERSION}"
                ),
                adapter=adapter,
            )
        return FactLedgerRuntime(status=AdapterStatus.MATCHED, adapter=adapter)

    def build(
        self,
        identity: LibraryIdentity,
        goal: str,
        labeled_documents: Iterable[tuple[str, dict[str, Any]]],
    ) -> FactLedgerRuntime:
        runtime = self.resolve(identity)
        if runtime.status is not AdapterStatus.MATCHED:
            return runtime
        assert runtime.adapter is not None
        started = time.monotonic()
        try:
            evidence_items: list[EvidenceDocument] = []
            for label, document in labeled_documents:
                if not isinstance(document, dict):
                    raise TypeError("evidence document must be a mapping")
                evidence_items.append(
                    EvidenceDocument(
                        label=str(label),
                        title=str(document.get("title", "")),
                        content=str(document.get("content", "")),
                        library_id=str(document.get("rulesetId", "")),
                        document_id=str(document.get("id", "")),
                        metadata=(
                            document.get("metadata", {})
                            if isinstance(document.get("metadata", {}), dict)
                            else {}
                        ),
                    )
                )
            evidence = tuple(evidence_items)
            if len({item.label for item in evidence}) != len(evidence):
                raise ValueError("evidence labels must be unique")
            if any(not item.document_id.strip() for item in evidence):
                raise ValueError("evidence document id is missing")
            if len({item.document_id for item in evidence}) != len(evidence):
                raise ValueError("evidence document ids must be unique")
            if any(item.library_id != identity.id for item in evidence):
                raise ValueError("evidence belongs to another library")
            if not evidence:
                runtime.status = AdapterStatus.NO_FACTS
                runtime.reason = "no evidence was registered for this ledger build"
                return runtime
            ledger = runtime.adapter.build(goal, evidence)
            if not isinstance(ledger, FactLedger):
                raise TypeError("adapter returned an invalid ledger type")
            if ledger.adapter_id != runtime.adapter.adapter_id:
                raise ValueError("adapter returned a ledger owned by another adapter")
            if ledger.adapter_version != runtime.adapter.adapter_version:
                raise ValueError("adapter returned a ledger with another version")
            if ledger.schema_version != FACT_LEDGER_CORE_SCHEMA_VERSION:
                raise ValueError("adapter returned an incompatible core ledger schema")
            if ledger.goal != goal:
                raise ValueError("adapter changed the ledger goal")
            if ledger.registered_evidence_refs != tuple(
                item.label for item in evidence
            ):
                raise ValueError("adapter changed the registered evidence snapshot")
        except Exception as error:
            runtime.status = AdapterStatus.BUILD_FAILED
            runtime.reason = f"adapter build failed: {type(error).__name__}"
            return runtime
        finally:
            runtime.build_seconds += time.monotonic() - started
        runtime.ledger = ledger
        if (
            ledger.record_count == 0
            and not runtime.adapter.supports_evidence_only_validation
        ):
            runtime.status = AdapterStatus.NO_FACTS
            runtime.reason = "adapter found no supported facts in the current evidence"
        return runtime
