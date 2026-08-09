from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class QueryIntent(str, Enum):
    RULE_FACT = "RULE_FACT"
    PROCEDURE = "PROCEDURE"
    COMPARE_OPTIONS = "COMPARE_OPTIONS"
    BUILD_ADVICE = "BUILD_ADVICE"


@dataclass(frozen=True)
class QueryPlan:
    intent: QueryIntent
    queries: tuple[str, ...]


def classify_intent(query: str) -> QueryIntent:
    value = query.strip()
    if re.search(r"(?:怎么BD|怎么构筑|构筑|build|推荐.+职业|生存能力)", value, re.I):
        return QueryIntent.BUILD_ADVICE
    if re.search(r"(?:兼职|多职业)", value) and re.search(
        r"(?:相比|收益|损失|推荐|提高|继续升|怎么)", value
    ):
        return QueryIntent.BUILD_ADVICE
    if re.search(r"(?:区别|比较|哪个更好|优劣|相比|和.+有什么不同)", value):
        return QueryIntent.COMPARE_OPTIONS
    if re.search(r"(?:如何创建|怎么创建|创建.+步骤|流程|步骤|如何进行)", value):
        return QueryIntent.PROCEDURE
    return QueryIntent.RULE_FACT


def build_query_plan(query: str, state: object | None = None) -> QueryPlan:
    intent = classify_intent(query)
    if intent != QueryIntent.BUILD_ADVICE:
        original = query.strip()
        focus = _rule_entry_focus(original) if intent == QueryIntent.RULE_FACT else ""
        queries = [value for value in (focus, original) if value]
        return QueryPlan(intent, tuple(dict.fromkeys(queries)))

    # Keep these as independent searches so each claim in a build answer can
    # be traced to a short rule entry. State is intentionally read through
    # public attributes only; no model output is parsed here.
    class_levels = getattr(state, "classLevels", {}) if state else {}
    planned_dips = getattr(state, "plannedDipLevels", {}) if state else {}
    role = getattr(state, "rolePreference", None) if state else None
    current = " ".join(f"{name}{level}" for name, level in class_levels.items())
    dip = " ".join(f"{name}{level}" for name, level in planned_dips.items())
    suffix = " ".join(value for value in (current, dip, str(role or "")) if value)
    # Keep the user goal compact and present in every sub-query. This is a
    # planning hint only; it is not treated as a rule fact.
    goal = "生存" if role and "生存" in str(role) else str(role or "")
    queries = [
        f"{query} 职业等级 职业能力",
        f"{query} 属性依赖 专长 法术",
        f"{query} 多职业 兼职收益 损失",
    ]
    if suffix:
        queries = [f"{item} {suffix} {goal}" for item in queries]
    return QueryPlan(intent, tuple(dict.fromkeys(queries)))


def _rule_entry_focus(query: str) -> str:
    """Extract a conservative title-like lookup hint from a fact question.

    The hint is shown to the model as a search plan; it never bypasses the
    normal retriever or becomes evidence.  Chinese rule questions commonly
    lead with an entry name and then append conditions ("选择…后", "把…时").
    Keeping that leading name makes exact-heading candidates visible before a
    broader natural-language search.
    """
    value = re.sub(r"^(?:(?:请问|想知道|关于)\s*)+", "", query.strip())
    value = re.sub(r"^(?:有|没有)", "", value)
    match = re.match(
        r"(?P<focus>[\u4e00-\u9fffA-Za-z0-9·（）()／/+-]{2,24}?)"
        r"(?:选择|获得|使用|把|将|且|时|后|在)",
        value,
    )
    if not match:
        return ""
    focus = re.sub(r"(?:专长|规则|效果)$", "", match.group("focus").strip())
    return focus if len(focus) >= 2 else ""
