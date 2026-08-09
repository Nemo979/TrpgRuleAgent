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
_DICE = re.compile(r"\b\d+d\d+(?:[+-]\d+)?\b", re.IGNORECASE)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_DISTANCE = re.compile(r"\d+(?:\.\d+)?\s*(?:尺|英尺|米|公里|格)")
_DURATION = re.compile(r"(?:持续|维持|每)\s*\d+(?:\.\d+)?\s*(?:轮|分钟|小时|天)")
_ACTION_TYPES = (
    "标准动作", "移动动作", "迅捷动作", "即时动作", "整轮动作", "自由动作", "反应动作",
)


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


def safety_signals(left: str, right: str) -> tuple[str, ...]:
    """Return critical fields that cannot be safely aligned for auto-dedup.

    Phase0 only measures these vetoes. It does not claim that the documents
    truly conflict and never changes their rank.
    """
    left_normalized = unicodedata.normalize("NFKC", left).casefold()
    right_normalized = unicodedata.normalize("NFKC", right).casefold()

    def polarity(value: str) -> set[str]:
        found: set[str] = set()
        if any(token in value for token in ("不能", "不可", "不得", "禁止")):
            found.add("forbidden")
        if any(token in value for token in ("可以", "能够", "允许")):
            found.add("allowed")
        return found

    def obligation(value: str) -> set[str]:
        found: set[str] = set()
        if any(token in value for token in ("必须", "应当", "需要")):
            found.add("required")
        if any(token in value for token in ("可以", "可选", "无需")):
            found.add("optional")
        return found

    extractors = {
        "polarity": polarity,
        "obligation": obligation,
        "dice": lambda value: set(_DICE.findall(value)),
        "numbers": lambda value: set(_NUMBER.findall(_DICE.sub("", value))),
        "distance": lambda value: set(_DISTANCE.findall(value)),
        "actionType": lambda value: {token for token in _ACTION_TYPES if token in value},
        "duration": lambda value: set(_DURATION.findall(value)),
    }
    mismatches: list[str] = []
    for name, extractor in extractors.items():
        left_values = extractor(left_normalized)
        right_values = extractor(right_normalized)
        if left_values != right_values and (left_values or right_values):
            mismatches.append(name)
    return tuple(mismatches)


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
    safety_signals: tuple[str, ...] = ()


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
                {
                    "left": p.left,
                    "right": p.right,
                    "jaccard": round(p.jaccard, 4),
                    "safetyVeto": bool(p.safety_signals),
                    "safetySignals": list(p.safety_signals),
                }
                for p in self.in_bucket_pairs
            ],
            "crossBucketPairs": [
                {
                    "left": p.left,
                    "right": p.right,
                    "jaccard": round(p.jaccard, 4),
                    "safetyVeto": bool(p.safety_signals),
                    "safetySignals": list(p.safety_signals),
                }
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
    shingle_sets = [
        shingles(document.content or "")
        for document in documents
    ]
    top_keys = keys[:_TOP_KEYS]
    key_counts = Counter(key for key in top_keys if key)
    top8_duplicate_slots = sum(count - 1 for count in key_counts.values() if count > 1)
    top8_unique_keys = len(key_counts)

    def _pair_jaccard(left_index: int, right_index: int) -> float:
        left = shingle_sets[left_index]
        right = shingle_sets[right_index]
        if not left or not right:
            return 0.0
        union = left | right
        if not union:
            return 0.0
        return len(left & right) / len(union)

    in_bucket: List[AuditPair] = []
    cross_bucket: List[AuditPair] = []
    buckets: Dict[str, List[int]] = {}
    for index, key in enumerate(keys):
        if key:
            buckets.setdefault(key, []).append(index)

    for indexes in buckets.values():
        for offset, left_index in enumerate(indexes):
            for right_index in indexes[offset + 1:]:
                jaccard = _pair_jaccard(left_index, right_index)
                if jaccard >= threshold:
                    in_bucket.append(
                        AuditPair(
                            documents[left_index].id,
                            documents[right_index].id,
                            jaccard,
                            True,
                            safety_signals(
                                documents[left_index].content or "",
                                documents[right_index].content or "",
                            ),
                        )
                    )

    for left_index in range(len(keys)):
        for right_index in range(left_index + 1, len(keys)):
            if keys[left_index] == keys[right_index]:
                continue
            if keys[left_index] is None or keys[right_index] is None:
                continue
            jaccard = _pair_jaccard(left_index, right_index)
            if jaccard >= threshold:
                cross_bucket.append(
                    AuditPair(
                        documents[left_index].id,
                        documents[right_index].id,
                        jaccard,
                        False,
                        safety_signals(
                            documents[left_index].content or "",
                            documents[right_index].content or "",
                        ),
                    )
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

    retrieved_cases: list[tuple[Dict[str, Any], Sequence[Any]]] = []
    for case in cases:
        results = retriever.search(case["query"], documents, top_n)
        retrieved_cases.append((case, results))

    def audits_at(threshold: float) -> Dict[str, CaseAudit]:
        return {
            str(case["id"]): audit_case(
                str(case["id"]), str(case["query"]), results, threshold
            )
            for case, results in retrieved_cases
        }

    per_case = audits_at(thresholds[0])

    duplicate_slots = [case.top8_duplicate_slots for case in per_case.values()]
    unique_keys = [case.top8_unique_keys for case in per_case.values()]
    all_in_bucket = sum(len(case.in_bucket_pairs) for case in per_case.values())
    all_cross_bucket = sum(len(case.cross_bucket_pairs) for case in per_case.values())
    all_pairs = [
        pair
        for case in per_case.values()
        for pair in (*case.in_bucket_pairs, *case.cross_bucket_pairs)
    ]
    veto_pairs = [pair for pair in all_pairs if pair.safety_signals]
    veto_signal_counts = Counter(signal for pair in veto_pairs for signal in pair.safety_signals)
    cases_with_duplicates = sum(1 for slots in duplicate_slots if slots > 0)
    cross_bucket_keys = Counter()
    for case in per_case.values():
        for pair in case.cross_bucket_pairs:
            cross_bucket_keys[pair.left] += 1

    threshold_experiments: list[dict[str, Any]] = []
    for threshold in thresholds:
        experiment_cases = per_case if threshold == thresholds[0] else audits_at(threshold)
        experiment_pairs = [
            pair
            for case in experiment_cases.values()
            for pair in (*case.in_bucket_pairs, *case.cross_bucket_pairs)
        ]
        threshold_experiments.append(
            {
                "threshold": float(threshold),
                "nearDuplicatePairsInBucket": sum(
                    len(case.in_bucket_pairs) for case in experiment_cases.values()
                ),
                "nearDuplicatePairsCrossBucket": sum(
                    len(case.cross_bucket_pairs) for case in experiment_cases.values()
                ),
                "safetyVetoPairs": sum(1 for pair in experiment_pairs if pair.safety_signals),
            }
        )

    return {
        "caseCount": len(per_case),
        "topN": top_n,
        "thresholds": [float(value) for value in thresholds],
        "thresholdExperiments": threshold_experiments,
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
        "safetyVetoPairs": len(veto_pairs),
        "potentialConflictSignalPairs": len(veto_pairs),
        "safetySignalCounts": dict(sorted(veto_signal_counts.items())),
        "crossBucketPairIds": sorted(cross_bucket_keys)[:100],
        "cases": [case.to_json() for case in per_case.values()],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase0 read-only near-duplicate audit")
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--index-dir", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--top-n", type=int, default=50)
    parser.add_argument(
        "--threshold",
        type=float,
        action="append",
        dest="thresholds",
        help="repeat for a threshold sweep (default: 0.90 and 0.95)",
    )
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
        thresholds=tuple(args.thresholds or (_DEFAULT_THRESHOLD, 0.95)),
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        "top8Duplicates=%d avgUniqueKeys=%.3f inBucket=%d crossBucket=%d safetyVeto=%d" % (
            report["top8DuplicateSlots"]["total"],
            report["top8UniqueKeys"]["average"],
            report["nearDuplicatePairsInBucket"],
            report["nearDuplicatePairsCrossBucket"],
            report["safetyVetoPairs"],
        )
    )


if __name__ == "__main__":
    main()
