import re
import unicodedata
from collections import Counter
from math import log
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .chunks import RuleChunk, iter_document_chunks
from .domain import RuleDocument, SearchHit


_MULTICLASS_MARKERS = (
    "兼职",
    "多职业",
    "职业等级组合",
    "混合职业",
    "混职",
    "兼职系统",
    "次要职业",
    "跨职业",
    "multiclass",
    "multiclassing",
    "multipleclasses",
    "classlevelcombination",
    "takinglevels",
)
_FAVORED_CLASS_MARKERS = (
    "天赋职业",
    "favoredclass",
    "favoredclassbonus",
    "preferredclass",
    "升级奖励",
)
_REFERENCE_MARKERS = (
    "职业法术",
    "法术列表",
    "spelllist",
    "classspell",
    "出处速查",
    "速查",
    "sourcequickreference",
    "quickreference",
    "目录",
    "tableofcontents",
    "contents",
    "索引",
    "index",
    "更新记录",
    "更新日志",
    "变更记录",
    "changelog",
    "updatelog",
)


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
        self._chunk_cache: Dict[
            str, List[Tuple[RuleChunk, Counter, int]]
        ] = {}
        self._document_fingerprint: Optional[Tuple[str, ...]] = None
        self._idf: Dict[str, float] = {}
        self._average_chunk_length = 1.0

    def _chunks(self, document: RuleDocument) -> List[Tuple[RuleChunk, Counter, int]]:
        cached = self._chunk_cache.get(document.id)
        if cached is not None:
            return cached

        chunks = []
        for chunk in iter_document_chunks(document, self.chunk_size, self.chunk_overlap):
            chunk_tokens = _tokens(chunk.search_text)
            chunks.append((chunk, chunk_tokens, sum(chunk_tokens.values())))
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
            for _chunk, chunk_tokens, chunk_length in self._chunks(document):
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
        multiclass_intent = _has_marker(query, _MULTICLASS_MARKERS)
        favored_class_intent = _has_marker(query, _FAVORED_CLASS_MARKERS)
        hits = []
        for document in document_list:
            if allowed_sources is not None and document.source_id not in allowed_sources:
                continue
            best_chunk: Optional[RuleChunk] = None
            lexical_score = 0.0
            for chunk, chunk_tokens, chunk_length in self._chunks(document):
                chunk_score = self._bm25(query_tokens, chunk_tokens, chunk_length)
                if chunk_score > lexical_score:
                    lexical_score = chunk_score
                    best_chunk = chunk
            priority_boost = max(0, document.priority) / 10000.0
            if best_chunk is None:
                continue

            score = lexical_score + priority_boost
            score += _title_intent_adjustment(query, document)
            score += _class_semantic_adjustment(
                document,
                multiclass_intent,
                favored_class_intent,
            )
            hits.append(
                SearchHit(
                    document=document,
                    excerpt=best_chunk.content[:240],
                    score=score,
                    chunk_id=best_chunk.id,
                    chunk_index=best_chunk.index,
                    match_score=lexical_score,
                    parent_score=score,
                )
            )
        hits.sort(key=lambda hit: (-hit.score, hit.document.id))
        return hits[:limit]


def _compact(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(re.findall(r"[a-z0-9\u4e00-\u9fff]+", normalized))


def _has_marker(value: str, markers: Sequence[str]) -> bool:
    compact = _compact(value)
    return any(_compact(marker) in compact for marker in markers)


def _document_labels(document: RuleDocument) -> str:
    values = [document.title, document.full_path]
    metadata = document.metadata
    for key in ("headingPath", "documentType", "kind", "category", "type"):
        value = metadata.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value)
        elif value:
            values.append(str(value))
    return " ".join(values)


def _is_reference_document(document: RuleDocument) -> bool:
    labels = _document_labels(document)
    if _has_marker(labels, _REFERENCE_MARKERS):
        return True
    metadata = document.metadata
    return bool(
        metadata.get("isIndex")
        or metadata.get("isNavigation")
        or metadata.get("isUpdateLog")
        or str(metadata.get("documentType", "")).casefold()
        in {"index", "contents", "changelog", "reference"}
    )


def _title_intent_adjustment(query: str, document: RuleDocument) -> float:
    """Favor a real entry when the query is exactly its complete title."""
    if _compact(query) != _compact(document.title):
        return 0.0
    if _is_reference_document(document):
        return -4.0
    return 5.0


def _is_favored_class_document(document: RuleDocument) -> bool:
    labels = _document_labels(document)
    if _has_marker(labels, _FAVORED_CLASS_MARKERS):
        return True
    # A generic class page may mention the concept in its prose. Only use a
    # short excerpt as a fallback so a multiclass page is not misclassified
    # merely because it cross-references favored class rules.
    content = document.content[:800]
    return _has_marker(content, _FAVORED_CLASS_MARKERS) and not _has_marker(
        labels, _MULTICLASS_MARKERS
    )


def _is_multiclass_document(document: RuleDocument) -> bool:
    labels = _document_labels(document)
    return _has_marker(labels, _MULTICLASS_MARKERS) or (
        _has_marker(document.content[:800], _MULTICLASS_MARKERS)
        and not _has_marker(labels, _FAVORED_CLASS_MARKERS)
    )


def _class_semantic_adjustment(
    document: RuleDocument,
    multiclass_intent: bool,
    favored_class_intent: bool,
) -> float:
    """Separate multiclass rules from favored-class rewards only when needed."""
    if favored_class_intent and _is_favored_class_document(document):
        return 1.0
    if _is_favored_class_document(document):
        return -2.5 if multiclass_intent else 0.0
    if _is_multiclass_document(document):
        return 1.0
    return 0.0
