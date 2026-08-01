import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence

from .repository import RuleRepository
from .retriever import InMemoryRetriever
from .service import RuleRetriever
from .vector_retriever import ChromaVectorRetriever


@dataclass(frozen=True)
class RetrievalCase:
    id: str
    query: str
    relevant_ids: Sequence[str]


def load_cases(path: Path) -> List[RetrievalCase]:
    cases: List[RetrievalCase] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            relevant_ids = value.get("relevantIds", [])
            if not value.get("id") or not value.get("query") or not relevant_ids:
                raise ValueError("invalid retrieval case at line %d" % line_number)
            cases.append(RetrievalCase(
                id=str(value["id"]),
                query=str(value["query"]),
                relevant_ids=[str(item) for item in relevant_ids],
            ))
    if not cases:
        raise ValueError("retrieval evaluation set is empty")
    return cases


def evaluate(
    repository: RuleRepository,
    retriever: RuleRetriever,
    cases: Sequence[RetrievalCase],
    limit: int,
) -> Dict[str, Any]:
    rulesets = repository.rulesets()
    if len(rulesets) != 1:
        raise ValueError("evaluation requires exactly one rule library")
    ruleset_id = rulesets[0]
    rows: List[Dict[str, Any]] = []
    reciprocal_rank_sum = 0.0
    hits = 0
    for case in cases:
        results = retriever.search(
            case.query,
            repository.all(ruleset_id),
            limit,
        )
        result_ids = [result.document.id for result in results]
        rank = next(
            (
                index + 1
                for index, result in enumerate(results)
                if _is_relevant(result.document, case.relevant_ids)
            ),
            None,
        )
        if rank is not None:
            hits += 1
            reciprocal_rank_sum += 1.0 / rank
        rows.append({
            "id": case.id,
            "query": case.query,
            "rank": rank,
            "topIds": result_ids,
            "topPaths": [result.document.full_path for result in results],
        })
        status = "hit@%d" % rank if rank is not None else "miss"
        top_path = results[0].document.full_path if results else "<none>"
        print("%-22s %-8s %s" % (case.id, status, top_path), flush=True)

    case_count = len(cases)
    report = {
        "caseCount": case_count,
        "limit": limit,
        "hitRate": round(hits / case_count, 4),
        "mrr": round(reciprocal_rank_sum / case_count, 4),
        "cases": rows,
    }
    print(
        "hit@%d=%.1f%% mrr=%.4f" %
        (limit, report["hitRate"] * 100, report["mrr"]),
        flush=True,
    )
    return report


def _is_relevant(document: Any, relevant_ids: Sequence[str]) -> bool:
    return bool(
        document.id in relevant_ids
        or str(document.metadata.get("legacyParentId", "")) in relevant_ids
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate PF1E vector retrieval")
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--index-dir", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--backend", choices=["chroma", "hybrid"], default="hybrid")
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repository = RuleRepository.from_jsonl(args.documents)
    vector_retriever = ChromaVectorRetriever(repository, args.index_dir)
    if args.backend == "hybrid":
        from .coordinator import RetrievalCoordinator
        from .hybrid_retriever import HybridRetriever
        retriever: RuleRetriever = RetrievalCoordinator(
            HybridRetriever(
                vector_retriever,
                InMemoryRetriever(),
            )
        )
    else:
        retriever = vector_retriever
    report = evaluate(repository, retriever, load_cases(args.cases), args.limit)
    report["backend"] = args.backend
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
