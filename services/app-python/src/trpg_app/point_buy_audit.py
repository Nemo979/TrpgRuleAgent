from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable


ABILITY_NAMES = ("力量", "敏捷", "体质", "智力", "感知", "魅力")


@dataclass(frozen=True)
class PointBuyAudit:
    budget: int
    pre_racial_scores: dict[str, int]
    costs: dict[str, int]
    total_cost: int
    racial_adjustments: dict[str, int]
    expected_final_scores: dict[str, int]
    proposed_final_scores: dict[str, int]

    def public(self) -> dict[str, Any]:
        discrepancies = {
            ability: {
                "proposed": proposed,
                "expected": self.expected_final_scores[ability],
            }
            for ability, proposed in self.proposed_final_scores.items()
            if ability in self.expected_final_scores
            and proposed != self.expected_final_scores[ability]
        }
        return {
            "kind": "deterministic_point_buy_audit",
            "budget": self.budget,
            "preRacialScores": self.pre_racial_scores,
            "costs": self.costs,
            "totalCost": self.total_cost,
            "budgetDelta": self.budget - self.total_cost,
            "racialAdjustments": self.racial_adjustments,
            "expectedFinalScores": self.expected_final_scores,
            "proposedFinalScores": self.proposed_final_scores,
            "finalScoreDiscrepancies": discrepancies,
        }

    def prompt_guidance(self) -> str:
        return (
            "服务器已根据本轮注册的购点表和种族调整证据完成确定性核算。"
            "以下 JSON 是算术结果，不是额外规则来源；解释规则时仍须引用对应的 S 来源：\n"
            + json.dumps(self.public(), ensure_ascii=False, sort_keys=True)
        )

    def render_answer(self, *, point_buy_label: str, race_label: str) -> str:
        score_order = list(ABILITY_NAMES)
        cost_expression = " + ".join(
            str(self.costs[ability]) for ability in score_order
        )
        rows = [
            "| 属性 | 购点后 | 购点消耗 | 种族调整 | 正确最终值 | 旧方案最终值 |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        for ability in score_order:
            adjustment = self.racial_adjustments.get(ability, 0)
            proposed = self.proposed_final_scores.get(ability)
            rows.append(
                f"| {ability} | {self.pre_racial_scores[ability]} | "
                f"{self.costs[ability]:+d} | {adjustment:+d} | "
                f"{self.expected_final_scores[ability]} | "
                f"{proposed if proposed is not None else '未提供'} |"
            )
        budget_statement = (
            f"六项合计为 `{cost_expression} = {self.total_cost}` 点；"
            f"预算是 {self.budget} 点，因此还剩 "
            f"{self.budget - self.total_cost} 点未使用。"
        )
        discrepancies = self.public()["finalScoreDiscrepancies"]
        discrepancy_text = "、".join(
            f"{ability}应为{values['expected']}，旧方案写成{values['proposed']}"
            for ability, values in discrepancies.items()
        ) or "旧方案的种族后最终值没有差异"
        return (
            f"结论：**上一版方案不正确，也不是一个用满 {self.budget} 点的购点方案。**\n\n"
            f"- 购点核算：{budget_statement} [{point_buy_label}]\n"
            f"- 种族调整核算：{discrepancy_text}。[{race_label}]\n\n"
            + "\n".join(rows)
            + "\n\n购点必须先完成，再应用种族调整；表中的正确最终值已经按这个顺序计算。"
            f" [{point_buy_label}][{race_label}]"
        )


def build_point_buy_audit(
    *,
    artifact: str,
    budget: int | None,
    race: str | None,
    evidence_documents: Iterable[dict[str, Any]],
) -> PointBuyAudit | None:
    """Audit a six-ability proposal only when registered evidence is sufficient."""
    if budget is None or not artifact:
        return None
    pre_racial, proposed_final = _parse_proposed_scores(artifact)
    if set(pre_racial) != set(ABILITY_NAMES):
        return None

    documents = tuple(evidence_documents)
    cost_table = _parse_point_buy_cost_table(documents)
    if any(score not in cost_table for score in pre_racial.values()):
        return None

    racial_adjustments = _parse_racial_adjustments(documents, race)
    if race and not racial_adjustments:
        return None
    costs = {ability: cost_table[score] for ability, score in pre_racial.items()}
    expected_final = {
        ability: score + racial_adjustments.get(ability, 0)
        for ability, score in pre_racial.items()
    }
    return PointBuyAudit(
        budget=budget,
        pre_racial_scores=pre_racial,
        costs=costs,
        total_cost=sum(costs.values()),
        racial_adjustments=racial_adjustments,
        expected_final_scores=expected_final,
        proposed_final_scores=proposed_final,
    )


def _parse_proposed_scores(artifact: str) -> tuple[dict[str, int], dict[str, int]]:
    pre_racial: dict[str, int] = {}
    proposed_final: dict[str, int] = {}
    for raw_line in artifact.splitlines():
        ability = next((name for name in ABILITY_NAMES if name in raw_line), None)
        if ability is None or ability in pre_racial:
            continue
        target = re.search(r"(?:升至|降至)\s*(?P<score>\d{1,2})", raw_line)
        if target is None:
            continue
        pre_racial[ability] = int(target.group("score"))

        cells = [cell.strip().replace("**", "") for cell in raw_line.split("|")]
        exact_numbers = [
            int(cell)
            for cell in cells
            if re.fullmatch(r"[+-]?\d{1,2}", cell)
        ]
        if exact_numbers:
            proposed_final[ability] = exact_numbers[-1]
    return pre_racial, proposed_final


def _parse_point_buy_cost_table(
    documents: Iterable[dict[str, Any]],
) -> dict[int, int]:
    parsed: dict[int, int] = {}
    for document in documents:
        content = str(document.get("content", "")).replace("–", "-").replace("−", "-")
        if "购点" not in content:
            continue
        for score, cost in re.findall(
            r"(?m)^\s*\|?\s*(7|8|9|10|11|12|13|14|15|16|17|18)\s*"
            r"\|\s*([+-]?\d{1,2})(?:\s*\||\s*$)",
            content,
        ):
            parsed[int(score)] = int(cost)
    return parsed


def _parse_racial_adjustments(
    documents: Iterable[dict[str, Any]], race: str | None
) -> dict[str, int]:
    if not race:
        return {}
    candidates = [
        document
        for document in documents
        if race in str(document.get("title", ""))
        or race in str(document.get("fullPath", ""))
    ]
    for document in candidates:
        content = str(document.get("content", "")).replace("–", "-").replace("−", "-")
        for line in content.splitlines():
            matches = re.findall(
                r"([+-]\s*\d+)\s*(力量|敏捷|体质|智力|感知|魅力)", line
            )
            if len(matches) < 2:
                continue
            return {
                ability: int(value.replace(" ", ""))
                for value, ability in matches
            }
    return {}
