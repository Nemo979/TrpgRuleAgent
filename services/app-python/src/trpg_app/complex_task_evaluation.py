"""Deterministic Stage 3 launch-condition and bounded-plan probe."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .complex_planner import PlanValidationError, build_complex_plan, validate_plan
from .query_decomposition import QueryComplexity, decompose_query, route_query
from .synthesis_contract import build_synthesis_contract


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"invalid complex task case at line {line_number}")
            _validate_case(value, line_number)
            cases.append(value)
    return cases


def evaluate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    false_planner_triggers = 0
    missed_planner_needs = 0
    planner_ready_cases = 0
    synthesis_ready_cases = 0
    for case in cases:
        query = str(case["query"])
        decision = route_query(query)
        decomposition = decompose_query(query, decision)
        tasks = list(case["tasks"])
        required_domains = {str(task["domain"]) for task in tasks}
        actual_domains = {question.domain for question in decomposition.questions}
        missing_domains = sorted(required_domains - actual_domains)
        dependency_edges = sum(len(task["dependsOn"]) for task in tasks)
        reasons: list[str] = []
        if decision.complexity != QueryComplexity.COMPLEX:
            reasons.append("routing_gap")
        if missing_domains:
            reasons.append("domain_capacity_gap")
        if len(tasks) > len(decomposition.questions):
            reasons.append("task_capacity_gap")
        if dependency_edges:
            reasons.append("dependency_graph_gap")
        if bool(case.get("conditional")):
            reasons.append("conditional_branch_gap")
        predicted_sufficient = not reasons
        expected_sufficient = bool(case["expectedStage2Sufficient"])
        planner_valid = False
        planner_task_count = 0
        planner_domains: set[str] = set()
        synthesis_check_kinds: list[str] = []
        synthesis_unresolved_fields: list[str] = []
        synthesis_contract_valid = False
        if decision.need_planner:
            try:
                plan = build_complex_plan(query, decision, decomposition)
                if plan is not None:
                    validate_plan(plan)
                    planner_valid = True
                    planner_task_count = len(plan.tasks)
                    planner_domains = {
                        task.domain.value for task in plan.tasks if task.domain.value != "rule"
                    }
                    contract = build_synthesis_contract(query, plan)
                    synthesis_check_kinds = [
                        check.kind.value for check in contract.checks
                    ]
                    synthesis_unresolved_fields = list(contract.unresolved_fields)
                    synthesis_contract_valid = bool(contract.checks)
            except (PlanValidationError, ValueError):
                planner_valid = False
        if planner_valid:
            planner_ready_cases += 1
        if synthesis_contract_valid:
            synthesis_ready_cases += 1
        if not predicted_sufficient and expected_sufficient:
            false_planner_triggers += 1
        if predicted_sufficient and not expected_sufficient:
            missed_planner_needs += 1
        reason_counts.update(reasons)
        rows.append(
            {
                "id": str(case["id"]),
                "passed": predicted_sufficient == expected_sufficient,
                "routeComplexity": decision.complexity.value,
                "stage2QuestionCount": len(decomposition.questions),
                "requiredTaskCount": len(tasks),
                "dependencyEdges": dependency_edges,
                "conditional": bool(case.get("conditional")),
                "missingDomains": missing_domains,
                "gapReasons": reasons,
                "stage2Sufficient": predicted_sufficient,
                "expectedStage2Sufficient": expected_sufficient,
                "plannerRecommended": decision.need_planner,
                "plannerValid": planner_valid,
                "plannerTaskCount": planner_task_count,
                "plannerMissingDomains": sorted(required_domains - planner_domains),
                "synthesisContractValid": synthesis_contract_valid,
                "synthesisCheckKinds": synthesis_check_kinds,
                "synthesisUnresolvedFields": synthesis_unresolved_fields,
            }
        )
    planner_candidates = sum(1 for row in rows if not row["stage2Sufficient"])
    all_classified = bool(rows) and all(row["passed"] for row in rows)
    return {
        "schemaVersion": 2,
        "caseCount": len(rows),
        "passedCases": sum(1 for row in rows if row["passed"]),
        "stage2SufficientCases": len(rows) - planner_candidates,
        "plannerCandidateCases": planner_candidates,
        "falsePlannerTriggers": false_planner_triggers,
        "missedPlannerNeeds": missed_planner_needs,
        "plannerReadyCases": planner_ready_cases,
        "synthesisReadyCases": synthesis_ready_cases,
        "gapReasonCounts": dict(sorted(reason_counts.items())),
        "passed": all_classified
        and false_planner_triggers == 0
        and missed_planner_needs == 0
        and planner_ready_cases == planner_candidates
        and synthesis_ready_cases == planner_candidates,
        "nextAction": (
            "run_local_synthesis_regression"
            if all_classified
            and planner_ready_cases == planner_candidates
            and synthesis_ready_cases == planner_candidates
            else "refine_complex_probe"
        ),
        "cases": rows,
    }


def _validate_case(case: dict[str, Any], line_number: int) -> None:
    if not isinstance(case.get("id"), str) or not isinstance(case.get("query"), str):
        raise ValueError(f"complex task case {line_number} requires id and query")
    tasks = case.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError(f"complex task case {line_number} requires tasks")
    task_ids = [str(task.get("id", "")) for task in tasks if isinstance(task, dict)]
    if len(task_ids) != len(tasks) or not all(task_ids) or len(set(task_ids)) != len(task_ids):
        raise ValueError(f"complex task case {line_number} has invalid task ids")
    known = set(task_ids)
    graph: dict[str, tuple[str, ...]] = {}
    for task in tasks:
        domain = task.get("domain")
        dependencies = task.get("dependsOn")
        if not isinstance(domain, str) or not isinstance(dependencies, list):
            raise ValueError(f"complex task case {line_number} has invalid task schema")
        values = tuple(str(item) for item in dependencies)
        if any(value not in known or value == str(task["id"]) for value in values):
            raise ValueError(f"complex task case {line_number} has invalid dependency")
        graph[str(task["id"])] = values
    _assert_acyclic(graph, line_number)
    if not isinstance(case.get("expectedStage2Sufficient"), bool):
        raise ValueError(f"complex task case {line_number} requires expectedStage2Sufficient")


def _assert_acyclic(graph: dict[str, tuple[str, ...]], line_number: int) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise ValueError(f"complex task case {line_number} has cyclic dependencies")
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in graph[task_id]:
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in graph:
        visit(task_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Stage 3 launch conditions")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate(load_cases(args.cases))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"passed={report['passedCases']}/{report['caseCount']} "
        f"stage2Sufficient={report['stage2SufficientCases']} "
        f"plannerCandidates={report['plannerCandidateCases']} "
        f"next={report['nextAction']}"
    )
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
