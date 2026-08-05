import re
import unicodedata
from typing import Dict, Iterable, List, Optional, Sequence

from .domain import RuleDocument, SearchHit
from .retriever import InMemoryRetriever
from .retriever import (
    _FAVORED_CLASS_MARKERS,
    _MULTICLASS_MARKERS,
    _is_favored_class_document,
    _is_multiclass_document,
)
from .vector_retriever import ChromaVectorRetriever


class HybridRetriever:
    """Fuse semantic and lexical parent rankings with weighted RRF."""

    def __init__(
        self,
        vector: ChromaVectorRetriever,
        lexical: Optional[InMemoryRetriever] = None,
        candidate_limit: int = 50,
        rrf_k: int = 60,
        vector_weight: float = 1.0,
        lexical_weight: float = 1.2,
        priority_scale: float = 3333.0,
    ) -> None:
        self.vector = vector
        self.lexical = lexical or InMemoryRetriever()
        self.candidate_limit = candidate_limit
        self.rrf_k = rrf_k
        self.vector_weight = vector_weight
        self.lexical_weight = lexical_weight
        self.priority_scale = priority_scale

    def search(
        self,
        query: str,
        documents: Iterable[RuleDocument],
        limit: int,
        source_ids: Optional[Sequence[str]] = None,
    ) -> List[SearchHit]:
        document_list = list(documents)
        candidate_limit = max(limit, self.candidate_limit)
        vector_hits = self.vector.search(
            query,
            document_list,
            candidate_limit,
            source_ids,
        )
        lexical_hits = self.lexical.search(
            query,
            document_list,
            candidate_limit,
            source_ids,
        )

        scores: Dict[str, float] = {}
        hits_by_id: Dict[str, SearchHit] = {}
        self._add_ranking(vector_hits, self.vector_weight, scores, hits_by_id)
        self._add_ranking(lexical_hits, self.lexical_weight, scores, hits_by_id)
        self._preserve_unilateral_leader(
            vector_hits,
            lexical_hits,
            self.lexical_weight,
            scores,
        )
        self._preserve_unilateral_leader(
            lexical_hits,
            vector_hits,
            self.vector_weight,
            scores,
        )

        for document_id, hit in hits_by_id.items():
            scores[document_id] += (
                max(0, hit.document.priority) / self.priority_scale
            )
            scores[document_id] += intent_rerank_bonus(query, hit.document)

        ranked_ids = sorted(scores, key=scores.get, reverse=True)
        return [
            SearchHit(
                hits_by_id[document_id].document,
                hits_by_id[document_id].excerpt,
                scores[document_id],
                chunk_id=hits_by_id[document_id].chunk_id,
                chunk_index=hits_by_id[document_id].chunk_index,
                match_score=hits_by_id[document_id].match_score,
                parent_score=scores[document_id],
            )
            for document_id in ranked_ids[:limit]
        ]

    def _add_ranking(
        self,
        hits: Sequence[SearchHit],
        weight: float,
        scores: Dict[str, float],
        hits_by_id: Dict[str, SearchHit],
    ) -> None:
        for rank, hit in enumerate(hits, start=1):
            document_id = hit.document.id
            scores[document_id] = scores.get(document_id, 0.0) + (
                weight / (self.rrf_k + rank)
            )
            hits_by_id.setdefault(document_id, hit)

    def _preserve_unilateral_leader(
        self,
        primary_hits: Sequence[SearchHit],
        other_hits: Sequence[SearchHit],
        missing_channel_weight: float,
        scores: Dict[str, float],
    ) -> None:
        """Prevent either engine's top result from vanishing for lack of consensus."""
        if not primary_hits:
            return
        leader_id = primary_hits[0].document.id
        if any(hit.document.id == leader_id for hit in other_hits):
            return
        scores[leader_id] += missing_channel_weight / (self.rrf_k + 1)


def intent_rerank_bonus(query: str, document: RuleDocument) -> float:
    """Apply small, backend-independent boosts after RRF fusion.

    RRF intentionally discards absolute BM25/vector scores.  Without a final
    intent signal, an exact semantic entry can lose to several broad class
    tables simply because they appear in both channels.  These bonuses are
    generic title/category signals and do not name any individual rule.
    """
    query_value = _compact(query)
    title_value = _compact(document.title)
    metadata = document.metadata
    bonus = 0.0
    if title_value and title_value in query_value:
        bonus += 0.12
    for key in ("entryNameZh", "entryNameEn", "nameZh", "nameEn"):
        value = _compact(str(metadata.get(key, "")))
        if value and value in query_value:
            bonus += 0.10
            break

    multiclass_intent = _has_marker(query, _MULTICLASS_MARKERS)
    favored_class_intent = _has_marker(query, _FAVORED_CLASS_MARKERS)
    if multiclass_intent:
        if _is_multiclass_document(document):
            bonus += 0.08
        if _is_favored_class_document(document):
            bonus -= 0.08
    if favored_class_intent:
        if _is_favored_class_document(document):
            bonus += 0.08
        if _is_multiclass_document(document):
            bonus -= 0.05
    return bonus


def _compact(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(re.findall(r"[a-z0-9\u4e00-\u9fff]+", normalized))


def _has_marker(value: str, markers: Sequence[str]) -> bool:
    compact = _compact(value)
    return any(_compact(marker) in compact for marker in markers)
