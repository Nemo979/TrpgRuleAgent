import re
from collections import Counter
from math import log
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .chunks import iter_document_chunks
from .domain import RuleDocument, SearchHit


def _tokens(text: str) -> Counter:
    lowered = text.lower()
    english = re.findall(r"[a-z0-9]+", lowered)
    chinese_segments = re.findall(r"[\u4e00-\u9fff]+", lowered)
    chinese = []
    for segment in chinese_segments:
        if len(segment) <= 8:
            chinese.append(segment)
        chinese.extend(segment)
        chinese.extend(segment[index:index + 2] for index in range(max(0, len(segment) - 1)))
    return Counter(english + chinese)


class InMemoryRetriever:
    """Parent-document retrieval with deterministic BM25 child chunks."""

    def __init__(
        self,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        if chunk_size <= chunk_overlap:
            raise ValueError("chunk_size must be larger than chunk_overlap")
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.k1 = k1
        self.b = b
        self._chunk_cache: Dict[str, List[Tuple[str, Counter, int]]] = {}
        self._document_fingerprint: Optional[Tuple[str, ...]] = None
        self._idf: Dict[str, float] = {}
        self._average_chunk_length = 1.0

    def _chunks(self, document: RuleDocument) -> List[Tuple[str, Counter, int]]:
        cached = self._chunk_cache.get(document.id)
        if cached is not None:
            return cached

        chunks = []
        for chunk in iter_document_chunks(document, self.chunk_size, self.chunk_overlap):
            chunk_tokens = _tokens(chunk.search_text)
            chunks.append((chunk.content, chunk_tokens, sum(chunk_tokens.values())))
        self._chunk_cache[document.id] = chunks
        return chunks

    def _ensure_index(self, documents: Sequence[RuleDocument]) -> None:
        fingerprint = tuple(document.id for document in documents)
        if fingerprint == self._document_fingerprint:
            return

        document_frequency: Counter = Counter()
        total_chunk_length = 0
        chunk_count = 0
        for document in documents:
            for _content, chunk_tokens, chunk_length in self._chunks(document):
                document_frequency.update(chunk_tokens.keys())
                total_chunk_length += chunk_length
                chunk_count += 1

        safe_chunk_count = max(chunk_count, 1)
        self._idf = {
            token: log(1.0 + (safe_chunk_count - frequency + 0.5) / (frequency + 0.5))
            for token, frequency in document_frequency.items()
        }
        self._average_chunk_length = max(
            total_chunk_length / safe_chunk_count,
            1.0,
        )
        self._document_fingerprint = fingerprint

    def _bm25(self, query_tokens: Counter, chunk_tokens: Counter, length: int) -> float:
        score = 0.0
        length_normalization = self.k1 * (
            1.0 - self.b + self.b * length / self._average_chunk_length
        )
        for token, query_frequency in query_tokens.items():
            term_frequency = chunk_tokens.get(token, 0)
            if term_frequency == 0:
                continue
            term_score = self._idf.get(token, 0.0) * (
                term_frequency * (self.k1 + 1.0)
                / (term_frequency + length_normalization)
            )
            score += term_score * min(query_frequency, 2)
        return score

    def search(
        self,
        query: str,
        documents: Iterable[RuleDocument],
        limit: int,
        source_ids: Optional[Sequence[str]] = None,
    ) -> List[SearchHit]:
        document_list = list(documents)
        self._ensure_index(document_list)
        query_tokens = _tokens(query)
        allowed_sources: Optional[Set[str]] = set(source_ids) if source_ids else None
        hits = []
        for document in document_list:
            if allowed_sources is not None and document.source_id not in allowed_sources:
                continue
            best_chunk = ""
            lexical_score = 0.0
            for chunk, chunk_tokens, chunk_length in self._chunks(document):
                chunk_score = self._bm25(query_tokens, chunk_tokens, chunk_length)
                if chunk_score > lexical_score:
                    lexical_score = chunk_score
                    best_chunk = chunk
            priority_boost = max(0, document.priority) / 10000.0
            score = lexical_score + priority_boost
            if lexical_score > 0:
                hits.append(SearchHit(document, best_chunk[:240], score))
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:limit]
