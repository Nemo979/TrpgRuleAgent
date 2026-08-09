"""V2.2 Stage 1A: read-only near-duplicate audit (Phase0).

Performs the two Phase0 scans from ``docs/near-duplicate-retrieval.md``
without changing any ranking:

- bucket scan: candidates sharing a ``derivedSemanticKey`` are compared
  pairwise with trigram shingle Jaccard;
- cross-bucket scan: high-similarity pairs with different semantic keys
  inside the same rule library, which signals key gaps or cross-parent
  duplication.

Only a report is produced.  No deduplication, vetoing or ranking changes
happen here; thresholds are experiments, starting at 0.90.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence

from .domain import RuleDocument
from .repository import RuleRepository
from .service import RuleRetriever
from .vector_retriever import ChromaVectorRetriever

_DEFAULT_THRESHOLD = 0.90
_TOP_KEYS = 8
_PUNCTUATION = re.compile(r"[\s\W_]+", re.UNICODE)


def normalize_text(value: str) -> str:
    """NFKC, lowercase, collapse whitespace/punctuation."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return _PUNCTUATION.sub(" ", normalized).strip()


def _is_cjk(value: str) -> bool:
    return bool(value) and any("㐀" <= character <= "鿿" for character in value)


def shingles(value: str, size: int = 3) -> set[str]:
    normalized = normalize_text(value)
    if _is_cjk(normalized):
        return {
            normalized[index:index + size]
            for index in range(max(len(normalized) - size + 1, 1))
        }
    words = normalized.split()
    return {
        " ".join(words[index:index + size])
        for index in range(max(len(words) - size + 1, 1))
    }


def shingle_jaccard(left: str, right: str) -> float:
    left_shingles = shingles(left)
    right_shingles = shingles(right)
    if not left_shingles or not right_shingles:
        return 0.0
    union = left_shingles | right_shingles
    if not union:
        return 0.0
    return len(left_shingles & right_shingles) / len(union)


def semantic_key(document: RuleDocument) -> str | None:
    """Derived semantic key: entry identity first, heading path as fallback.

    Phase1 only uses reliable automatic keys; candidates without one are
    always kept and never merged.
    """
    metadata = document.metadata or {}
    entry_type = metadata.get("entryType")
    if entry_type and entry_type != "rule_table":
        name_zh = str(metadata.get("entryNameZh") or "").strip()
        name_en = str(metadata.get("entryNameEn") or "").strip()
        if name_zh or name_en:
            return f"{entry_type}:{name_zh}:{name_en}".strip(":")
    heading_path = metadata.get("headingPath")
    if isinstance(heading_path, list) and len(heading_path) >= 2:
        leaf = [str(value) for value in heading_path[-2:] if str(value).strip()]
        if leaf:
            return "path:" + ":".join(leaf)
    title = (document.title or "").strip()
    if title:
        return "title:" + title
    return None


@dataclass(frozen=True)
class AuditPair:
    left: str
    right: str
    jaccard: float
    same_key: bool


@dataclass
class CaseAudit:
    case_id: str
    query: str
    top_ids: List[str]
    top_keys: List[str | None]
    top8_duplicate_slots: int
    top8_unique_keys: int
    in_bucket_pairs: List[AuditPair]
    cross_bucket_pairs: List[AuditPair]
    cluster_sizes: Dict[str, int]

    def to_json(self) -> Dict[str, Any]:
        return {
            "id": self.case_id,
            "query": self.query,
            "topIds": self.top_ids,
            "topKeys": self.top_keys,
            "top8DuplicateSlots": self.top8_duplicate_slots,
            "top8UniqueKeys": self.top8_unique_keys,
            "inBucketPairs": [
                {"left": p.left, "right": p.right, "jaccard": round(p.jaccard, 4)}
                for p in self.in_bucket_pairs
            ],
            "crossBucketPairs": [
                {"left": p.left, "right": p.right, "jaccard": round(p.jaccard, 4)}
                for p in self.cross_bucket_pairs
            ],
            "clusterSizes": self.cluster_sizes,
        }


def audit_case(
    case_id: str,
    query: str,
    results: Sequence[RuleDocument],
    threshold: float,
) -> CaseAudit:
    documents = [result if isinstance(result, RuleDocument) else result.document for result in results]
    top_ids = [document.id for document in documents]
    keys = [semantic_key(document) for document in documents]
    top_keys = keys[:_TOP_KEYS]
    key_counts = Counter(key for key in top_keys if key)
    top8_duplicate_slots = sum(count - 1 for count in key_counts.values() if count > 1)
    top8_unique_keys = len(key_counts)

    in_bucket: List[AuditPair] = []
    cross_bucket: List[AuditPair] = []
    buckets: Dict[str, List[int]] = {}
    for index, key in enumerate(keys):
        if key:
            buckets.setdefault(key, []).append(index)

    for indexes in buckets.values():
        for offset, left_index in enumerate(indexes):
            for right_index in indexes[offset + 1:]:
                jaccard = shingle_jaccard(
                    documents[left_index].content or "",
                    documents[right_index].content or "",
                )
                if jaccard >= threshold:
                    in_bucket.append(
                        AuditPair(documents[left_index].id, documents[right_index].id, jaccard, True)
                    )

    for left_index in range(len(keys)):
        for right_index in range(left_index + 1, len(keys)):
            if keys[left_index] == keys[right_index]:
                continue
            if keys[left_index] is None or keys[right_index] is None:
                continue
            jaccard = shingle_jaccard(
                documents[left_index].content or "",
                documents[right_index].content or "",
            )
            if jaccard >= threshold:
                cross_bucket.append(
                    AuditPair(documents[left_index].id, documents[right_index].id, jaccard, False)
                )

    return CaseAudit(
        case_id=case_id,
        query=query,
        top_ids=top_ids,
        top_keys=top_keys,
        top8_duplicate_slots=top8_duplicate_slots,
        top8_unique_keys=top8_unique_keys,
        in_bucket_pairs=in_bucket,
        cross_bucket_pairs=cross_bucket,
        cluster_sizes={key: count for key, count in key_counts.items() if count > 1},
    )


def audit_set(
    retriever: RuleRetriever,
    repository: RuleRepository,
    cases: Sequence[Dict[str, Any]],
    *,
    top_n: int = 50,
    thresholds: Sequence[float] = (_DEFAULT_THRESHOLD, 0.95),
) -> Dict[str, Any]:
    rulesets = repository.rulesets()
    if len(rulesets) != 1:
        raise ValueError("audit requires exactly one rule library")
    ruleset_id = rulesets[0]
    documents = repository.all(ruleset_id)

    per_case: Dict[str, CaseAudit] = {}
    for case in cases:
        results = retriever.search(case["query"], documents, top_n)
        per_case[str(case["id"])] = audit_case(str(case["id"]), str(case["query"]), results, thresholds[0])

    duplicate_slots = [case.top8_duplicate_slots for case in per_case.values()]
    unique_keys = [case.top8_unique_keys for case in per_case.values()]
    all_in_bucket = sum(len(case.in_bucket_pairs) for case in per_case.values())
    all_cross_bucket = sum(len(case.cross_bucket_pairs) for case in per_case.values())
    cases_with_duplicates = sum(1 for slots in duplicate_slots if slots > 0)
    cross_bucket_keys = Counter()
    for case in per_case.values():
        for pair in case.cross_bucket_pairs:
            cross_bucket_keys[pair.left] += 1

    return {
        "caseCount": len(per_case),
        "topN": top_n,
        "thresholds": [float(value) for value in thresholds],
        "top8DuplicateSlots": {
            "total": sum(duplicate_slots),
            "average": round(sum(duplicate_slots) / max(len(duplicate_slots), 1), 4),
            "casesWithDuplicates": cases_with_duplicates,
        },
        "top8UniqueKeys": {
            "average": round(sum(unique_keys) / max(len(unique_keys), 1), 4),
        },
        "nearDuplicatePairsInBucket": all_in_bucket,
        "nearDuplicatePairsCrossBucket": all_cross_bucket,
        "crossBucketPairIds": sorted(cross_bucket_keys)[:100],
        "cases": [case.to_json() for case in per_case.values()],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase0 read-only near-duplicate audit")
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--index-dir", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--top-n", type=int, default=50)
    parser.add_argument("--threshold", type=float, default=_DEFAULT_THRESHOLD)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repository = RuleRepository.from_jsonl(args.documents)
    retriever = ChromaVectorRetriever(repository, args.index_dir)
    cases: List[Dict[str, Any]] = []
    with args.cases.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                cases.append(json.loads(line))
    report = audit_set(
        retriever,
        repository,
        cases,
        top_n=args.top_n,
        thresholds=(args.threshold,),
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        "top8Duplicates=%d avgUniqueKeys=%.3f inBucket=%d crossBucket=%d" % (
            report["top8DuplicateSlots"]["total"],
            report["top8UniqueKeys"]["average"],
            report["nearDuplicatePairsInBucket"],
            report["nearDuplicatePairsCrossBucket"],
        )
    )


if __name__ == "__main__":
    main()
