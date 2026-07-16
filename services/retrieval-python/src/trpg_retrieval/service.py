from typing import Any, Dict, Iterable, List, Optional, Protocol, Sequence

from .domain import RuleDocument, SearchHit
from .repository import RuleRepository


class RuleRetriever(Protocol):
    def search(
        self,
        query: str,
        documents: Iterable[RuleDocument],
        limit: int,
        source_ids: Optional[Sequence[str]] = None,
    ) -> List[SearchHit]:
        ...


class RetrievalService:
    def __init__(self, repository: RuleRepository, retriever: RuleRetriever) -> None:
        self.repository = repository
        self.retriever = retriever

    def health(self) -> Dict[str, Any]:
        return {
            "status": "ok",
            "documentCount": self.repository.count(),
            "rulesets": self.repository.rulesets(),
        }

    def search(
        self,
        query: str,
        ruleset_id: str,
        limit: int = 8,
        source_ids: Optional[Sequence[str]] = None,
    ) -> List[Dict[str, Any]]:
        if not query.strip():
            raise ValueError("query must not be empty")
        safe_limit = min(max(limit, 1), 10)
        hits = self.retriever.search(
            query,
            self.repository.all(ruleset_id),
            safe_limit,
            source_ids,
        )
        return [hit.to_json() for hit in hits]

    def read(self, ruleset_id: str, ids: Sequence[str]) -> List[Dict[str, Any]]:
        if len(ids) > 8:
            raise ValueError("at most 8 documents can be read at once")
        return [document.to_json() for document in self.repository.read(ruleset_id, ids)]
