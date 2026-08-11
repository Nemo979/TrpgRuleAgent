"""Bounded deterministic plan schema for Stage 3 complex PF1E tasks.

The planner describes research dependencies only.  It cannot select tools,
models, URLs, libraries, or commands; the server-owned executor maps validated
research tasks to the existing search/read path.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

from .query_decomposition import (
    QueryComplexity,
    QueryDecomposition,
    RouteDecision,
    decomposition_search_query,
    domain_question,
)


PLANNER_VERSION = 1
MAX_PLAN_TASKS = 6
MAX_PLAN_QUERY_CHARACTERS = 500
PLAN_TASK_TIMEOUT_SECONDS = 30.0
PLAN_EXECUTOR_DEADLINE_SECONDS = 90.0
_TASK_ID = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_FORBIDDEN_QUERY = re.compile(
    r"https?://|www\.|系统命令|执行命令|工具名|模型|规则库|"
    r"\b(?:search_rules|read_rules|finish_answer|system|shell|bash|python|curl)\b",
    re.IGNORECASE,
)
_CONDITIONAL = re.compile(r"必须先|先核对|只有|否则|如果|根据|未达到|满足.*才")


class PlanTaskType(str, Enum):
    RULE_RESEARCH = "rule_research"
    COMPARE = "compare"
    SELECTION = "selection"
    PROGRESSION = "progression"


class PlanDomain(str, Enum):
    CLASS = "class"
    RACE = "race"
    FEAT = "feat"
    SPELL = "spell"
    EQUIPMENT = "equipment"
    PROGRESSION = "progression"
    RULE = "rule"


@dataclass(frozen=True)
class PlanTask:
    id: str
    type: PlanTaskType
    query: str
    domain: PlanDomain
    depends_on: tuple[str, ...] = ()

    @property
    def requires_evidence(self) -> bool:
        return self.type != PlanTaskType.SELECTION

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "query": self.query,
            "domain": self.domain.value,
            "depends_on": list(self.depends_on),
        }


@dataclass(frozen=True)
class ComplexPlan:
    goal: str
    tasks: tuple[PlanTask, ...]
    planner_version: int = PLANNER_VERSION

    def public(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "tasks": [task.public() for task in self.tasks],
            "planner_version": self.planner_version,
        }

    def evidence_decomposition(self) -> QueryDecomposition:
        from .query_decomposition import DecomposedQuestion

        return QueryDecomposition(
            tuple(
                DecomposedQuestion(
                    id=task.id,
                    question=task.query,
                    domain=task.domain.value,
                    depends_on=task.depends_on,
                )
                for task in self.tasks
                if task.requires_evidence
            )
        )


@dataclass(frozen=True)
class TaskResult:
    task_id: str
    status: str
    source_ids: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()
    visible_summary: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "source_ids": list(self.source_ids),
            "missing_fields": list(self.missing_fields),
            "visible_summary": self.visible_summary,
        }


class PlanValidationError(ValueError):
    pass


def build_complex_plan(
    goal: str,
    route: RouteDecision,
    decomposition: QueryDecomposition,
) -> ComplexPlan | None:
    """Build a bounded plan from the deterministic Stage 2 decomposition."""
    if route.complexity != QueryComplexity.COMPLEX or not route.need_planner:
        return None

    evidence_questions = list(decomposition.questions)
    represented_domains = {question.domain for question in evidence_questions}
    evidence_limit = MAX_PLAN_TASKS - (1 if _CONDITIONAL.search(goal) else 0)
    if len(evidence_questions) < evidence_limit:
        from .query_decomposition import DecomposedQuestion

        for domain in route.domains:
            if domain == "rule" or domain in represented_domains:
                continue
            evidence_questions.append(
                DecomposedQuestion(
                    id=f"planner-{domain}",
                    question=domain_question(goal, domain),
                    domain=domain,
                )
            )
            represented_domains.add(domain)
            if len(evidence_questions) >= evidence_limit:
                break
    foundations = [
        question for question in evidence_questions if question.domain != "progression"
    ]
    progressions = [
        question for question in evidence_questions if question.domain == "progression"
    ]
    tasks: list[PlanTask] = []
    foundation_ids: list[str] = []
    for question in foundations:
        task_id = f"t{len(tasks) + 1}"
        task_type = (
            PlanTaskType.COMPARE
            if re.search(r"比较|收益|损失|优劣|区别", question.question)
            else PlanTaskType.RULE_RESEARCH
        )
        tasks.append(
            PlanTask(
                id=task_id,
                type=task_type,
                query=_planned_search_query(goal, question),
                domain=PlanDomain(question.domain),
            )
        )
        foundation_ids.append(task_id)

    for question in progressions:
        task_id = f"t{len(tasks) + 1}"
        tasks.append(
            PlanTask(
                id=task_id,
                type=PlanTaskType.PROGRESSION,
                query=_planned_search_query(goal, question),
                domain=PlanDomain.PROGRESSION,
                depends_on=tuple(foundation_ids),
            )
        )

    if _CONDITIONAL.search(goal) and len(tasks) < MAX_PLAN_TASKS:
        tasks.append(
            PlanTask(
                id=f"t{len(tasks) + 1}",
                type=PlanTaskType.SELECTION,
                query="根据已核对规则完成条件分支、选择与缺失信息",
                domain=PlanDomain.RULE,
                depends_on=tuple(task.id for task in tasks),
            )
        )

    plan = ComplexPlan(goal=goal.strip(), tasks=tuple(tasks))
    validate_plan(plan)
    return plan


def parse_plan(value: dict[str, Any]) -> ComplexPlan:
    """Parse a JSON-like plan for validator and fallback contract tests."""
    if not isinstance(value, dict):
        raise PlanValidationError("plan must be an object")
    if set(value) - {"goal", "tasks", "planner_version"}:
        raise PlanValidationError("plan contains unknown fields")
    tasks_value = value.get("tasks")
    if not isinstance(tasks_value, list):
        raise PlanValidationError("tasks must be an array")
    if any(
        not isinstance(task, dict)
        or set(task) - {"id", "type", "query", "domain", "depends_on"}
        for task in tasks_value
    ):
        raise PlanValidationError("task contains unknown fields")
    try:
        tasks = tuple(
            PlanTask(
                id=str(task["id"]),
                type=PlanTaskType(task["type"]),
                query=str(task["query"]),
                domain=PlanDomain(task["domain"]),
                depends_on=tuple(str(item) for item in task.get("depends_on", [])),
            )
            for task in tasks_value
        )
    except (KeyError, TypeError, ValueError) as error:
        raise PlanValidationError(f"invalid task: {error}") from error
    plan = ComplexPlan(
        goal=str(value.get("goal", "")),
        tasks=tasks,
        planner_version=int(value.get("planner_version", 0)),
    )
    validate_plan(plan)
    return plan


def validate_plan(plan: ComplexPlan) -> None:
    if plan.planner_version != PLANNER_VERSION:
        raise PlanValidationError("unsupported planner version")
    if not plan.goal.strip():
        raise PlanValidationError("goal must not be empty")
    if not 1 <= len(plan.tasks) <= MAX_PLAN_TASKS:
        raise PlanValidationError("task count exceeds bounded plan limit")
    ids = [task.id for task in plan.tasks]
    if len(ids) != len(set(ids)):
        raise PlanValidationError("task ids must be unique")
    known: set[str] = set()
    for task in plan.tasks:
        if not _TASK_ID.fullmatch(task.id):
            raise PlanValidationError("invalid task id")
        query = task.query.strip()
        if not query or len(query) > MAX_PLAN_QUERY_CHARACTERS:
            raise PlanValidationError("invalid task query length")
        if _FORBIDDEN_QUERY.search(query):
            raise PlanValidationError("task query contains forbidden capability")
        if task.id in task.depends_on or any(dep not in ids for dep in task.depends_on):
            raise PlanValidationError("task dependency is invalid")
        if any(dep not in known for dep in task.depends_on):
            raise PlanValidationError("tasks must be topologically ordered")
        known.add(task.id)
    _assert_acyclic(plan.tasks)


def planner_result_guidance(
    plan: ComplexPlan,
    results: Iterable[TaskResult],
    *,
    synthesis_contract: Any | None = None,
    fact_ledger: Any | None = None,
) -> str:
    result_items = tuple(results)
    payload = {
        "plan": plan.public(),
        "task_results": [result.public() for result in result_items],
    }
    if synthesis_contract is not None:
        from .synthesis_contract import assess_synthesis_contract

        payload["synthesis_contract"] = synthesis_contract.public()
        payload["synthesis_check_readiness"] = [
            item.public()
            for item in assess_synthesis_contract(
                synthesis_contract,
                plan,
                result_items,
            )
        ]
    if fact_ledger is not None:
        payload["fact_ledger"] = fact_ledger.public()
    return (
        "受限 Planner 已按依赖顺序完成。下面结构只描述目标覆盖和来源注册状态，"
        "不是规则事实；规则结论仍必须逐条引用已读取证据。回答必须明确区分规则事实、"
        "基于规则的推导、选择建议、收益与损失、适用条件、缺失信息和来源。条件未满足时"
        "必须执行题目要求的替代分支；不得把未完成任务写成已验证结论。合成契约也不是"
        "规则事实：它只规定哪些主张必须精确取证。数值、等级、资格、法术和装备明细若没有"
        "同项证据，必须删除或标为证据不足，不能用模型记忆补全。Fact Ledger 是服务器从"
        "已读证据确定性提取的唯一结构化事实表；最终数值和资格不得与其冲突。\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def _assert_acyclic(tasks: Iterable[PlanTask]) -> None:
    dependencies = {task.id: set(task.depends_on) for task in tasks}
    remaining = set(dependencies)
    while remaining:
        ready = {task_id for task_id in remaining if not dependencies[task_id] & remaining}
        if not ready:
            raise PlanValidationError("task dependencies must form a DAG")
        remaining -= ready


def _planned_search_query(goal: str, question: Any) -> str:
    from .query_decomposition import DecomposedQuestion

    use_full_goal = (
        "奥法骑士" in goal
        and question.domain in {"class", "spell", "equipment", "progression"}
    ) or (
        "奥术失败" in goal and question.domain == "equipment"
    ) or (
        all(term in goal for term in ("猛力攻击", "顺势斩", "大顺势斩"))
        and question.domain == "feat"
    )
    if use_full_goal:
        return decomposition_search_query(
            DecomposedQuestion(
                id=question.id,
                question=goal,
                domain=question.domain,
                depends_on=question.depends_on,
            )
        )
    return decomposition_search_query(question)
