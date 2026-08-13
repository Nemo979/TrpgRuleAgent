"""Application composition root for built-in Fact Ledger adapters."""

from __future__ import annotations

from typing import Any, Iterable

from .fact_ledger_adapter import (
    FactLedgerRegistry,
    FactLedgerRuntime,
    LibraryIdentity,
)
from .pf1e_fact_adapter import PF1E_FACT_LEDGER_ADAPTER


DEFAULT_FACT_LEDGER_REGISTRY = FactLedgerRegistry((PF1E_FACT_LEDGER_ADAPTER,))


def build_default_fact_ledger_runtime(
    manifest: Any,
    goal: str,
    labeled_documents: Iterable[tuple[str, dict[str, Any]]],
) -> FactLedgerRuntime:
    return DEFAULT_FACT_LEDGER_REGISTRY.build(
        LibraryIdentity.from_manifest(manifest),
        goal,
        labeled_documents,
    )
