"""Deterministic final-synthesis constraints for bounded complex plans.

The contract is not a source of PF1E facts.  It describes which kinds of
claims need exact evidence and which user inputs are unresolved before the
final model may turn retrieved rules into build advice.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Iterable

from .complex_planner import ComplexPlan, PlanDomain, TaskResult


SYNTHESIS_CONTRACT_VERSION = 1
MAX_SYNTHESIS_CHECKS = 6


class SynthesisCheckKind(str, Enum):
    CONDITIONAL_BRANCH = "conditional_branch"
    LEVEL_ARITHMETIC = "level_arithmetic"
    FEAT_ELIGIBILITY = "feat_eligibility"
    SPELL_PROGRESSION = "spell_progression"
    EQUIPMENT_STAT = "equipment_stat"
    MISSING_INPUT = "missing_input"


@dataclass(frozen=True)
class SynthesisCheck:
    id: str
    kind: SynthesisCheckKind
    instruction: str
    required_domains: tuple[PlanDomain, ...] = ()
    unresolved_field: str = ""

    def public(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "instruction": self.instruction,
            "required_domains": [domain.value for domain in self.required_domains],
            "unresolved_field": self.unresolved_field,
        }


@dataclass(frozen=True)
class SynthesisContract:
    checks: tuple[SynthesisCheck, ...]
    contract_version: int = SYNTHESIS_CONTRACT_VERSION

    def public(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "checks": [check.public() for check in self.checks],
        }

    @property
    def unresolved_fields(self) -> tuple[str, ...]:
        return tuple(
            check.unresolved_field
            for check in self.checks
            if check.kind == SynthesisCheckKind.MISSING_INPUT
            and check.unresolved_field
        )


@dataclass(frozen=True)
class SynthesisCheckReadiness:
    check_id: str
    status: str
    source_ids: tuple[str, ...] = ()

    def public(self) -> dict[str, object]:
        return {
            "check_id": self.check_id,
            "status": self.status,
            "source_ids": list(self.source_ids),
        }


_CONDITIONAL = re.compile(r"必须先|先核对|只有|否则|如果|未达到|满足.*才")
_LEVEL = re.compile(r"\d+\s*(?:到|至|[-—~])\s*\d+\s*级|升级|成长|兼职|进阶")
_FEAT = re.compile(r"专长|feat", re.IGNORECASE)
_SPELL = re.compile(r"法术|施法|奥术|神术|\d+\s*环|spell", re.IGNORECASE)
_EQUIPMENT = re.compile(r"装备|武器|盔甲|护甲|盾牌|价格|伤害")
_UNRESOLVED_SPELL_TARGET = re.compile(r"指定(?:的)?法术环级|指定环级")


def build_synthesis_contract(goal: str, plan: ComplexPlan) -> SynthesisContract:
    """Build claim-safety checks from the goal without inventing rule facts."""
    checks: list[SynthesisCheck] = []

    def add(
        kind: SynthesisCheckKind,
        instruction: str,
        *domains: PlanDomain,
        unresolved_field: str = "",
    ) -> None:
        checks.append(
            SynthesisCheck(
                id=f"c{len(checks) + 1}",
                kind=kind,
                instruction=instruction,
                required_domains=tuple(domains),
                unresolved_field=unresolved_field,
            )
        )

    if _UNRESOLVED_SPELL_TARGET.search(goal):
        add(
            SynthesisCheckKind.MISSING_INPUT,
            "用户没有给出要保持的具体法术环级；不得擅自假设一个环级并输出单一路线，"
            "必须列出按目标环级区分的条件分支，并把该字段列入缺失信息。",
            unresolved_field="目标法术环级",
        )
    if _CONDITIONAL.search(goal):
        add(
            SynthesisCheckKind.CONDITIONAL_BRANCH,
            "先逐项列出条件及满足状态，再执行对应分支；未知条件不得当作已满足或未满足。",
        )
    if _LEVEL.search(goal):
        add(
            SynthesisCheckKind.LEVEL_ARITHMETIC,
            "逐级列出角色等级、各职业等级和关键能力取得等级；每条路线必须复算职业等级总和，"
            "并区分角色等级、职业等级、施法者等级与法术环级。",
            PlanDomain.CLASS,
            PlanDomain.PROGRESSION,
        )
    if _FEAT.search(goal):
        add(
            SynthesisCheckKind.FEAT_ELIGIBILITY,
            "分别核对通用专长、种族奖励专长和职业奖励专长的取得时点与可选范围；"
            "每个先决条件、替代专长和数量都必须有同项证据，不得因名称含‘专长’就推定可选。",
            PlanDomain.FEAT,
            PlanDomain.CLASS,
            PlanDomain.RACE,
        )
    if _SPELL.search(goal):
        add(
            SynthesisCheckKind.SPELL_PROGRESSION,
            "法术名称、所属职业列表、法术环级、效果和成长节点必须由精确法术或职业表证据支持；"
            "不得引入证据中未出现的法术，也不得混淆施法者等级、职业等级和法术环级。",
            PlanDomain.SPELL,
            PlanDomain.CLASS,
            PlanDomain.PROGRESSION,
        )
    if _EQUIPMENT.search(goal):
        add(
            SynthesisCheckKind.EQUIPMENT_STAT,
            "武器、盔甲和盾牌的价格、伤害、加值、失败率及叠加关系必须逐项引用精确装备或规则条目；"
            "没有精确证据时只说明缺口，不得凭记忆填写购买表。",
            PlanDomain.EQUIPMENT,
            PlanDomain.RULE,
        )

    contract = SynthesisContract(tuple(checks[:MAX_SYNTHESIS_CHECKS]))
    validate_synthesis_contract(contract, plan)
    return contract


def validate_synthesis_contract(
    contract: SynthesisContract,
    plan: ComplexPlan,
) -> None:
    if contract.contract_version != SYNTHESIS_CONTRACT_VERSION:
        raise ValueError("unsupported synthesis contract version")
    if len(contract.checks) > MAX_SYNTHESIS_CHECKS:
        raise ValueError("synthesis check count exceeds limit")
    ids = [check.id for check in contract.checks]
    if len(ids) != len(set(ids)) or any(not re.fullmatch(r"c[1-9]\d*", item) for item in ids):
        raise ValueError("invalid synthesis check ids")
    plan_domains = {task.domain for task in plan.tasks}
    for check in contract.checks:
        if not check.instruction.strip():
            raise ValueError("synthesis instruction must not be empty")
        if check.kind == SynthesisCheckKind.MISSING_INPUT:
            if not check.unresolved_field:
                raise ValueError("missing-input check requires a field")
            continue
        if check.unresolved_field:
            raise ValueError("only missing-input checks may declare unresolved fields")
        # A check may list several alternative evidence domains. At least one
        # must be represented by the bounded plan when domains are required.
        if check.required_domains and not plan_domains.intersection(
            check.required_domains
        ):
            raise ValueError("synthesis check is not represented by plan domains")


def assess_synthesis_contract(
    contract: SynthesisContract,
    plan: ComplexPlan,
    results: Iterable[TaskResult],
) -> tuple[SynthesisCheckReadiness, ...]:
    """Report evidence readiness; this never claims that a rule fact is true."""
    results_by_id = {result.task_id: result for result in results}
    readiness: list[SynthesisCheckReadiness] = []
    for check in contract.checks:
        if check.kind == SynthesisCheckKind.MISSING_INPUT:
            readiness.append(
                SynthesisCheckReadiness(check.id, "requires_user_branching")
            )
            continue
        matching_tasks = [
            task
            for task in plan.tasks
            if not check.required_domains or task.domain in check.required_domains
        ]
        matching_results = [
            results_by_id[task.id]
            for task in matching_tasks
            if task.id in results_by_id
        ]
        source_ids = tuple(
            dict.fromkeys(
                source_id
                for result in matching_results
                if result.status == "completed"
                for source_id in result.source_ids
            )
        )
        readiness.append(
            SynthesisCheckReadiness(
                check.id,
                "evidence_available" if source_ids else "missing_evidence",
                source_ids,
            )
        )
    return tuple(readiness)
