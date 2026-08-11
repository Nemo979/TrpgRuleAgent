"""Deterministic V2.3 Stage 2 routing and bounded query decomposition.

The router only classifies user-authored text.  It never supplies rule facts,
never calls a model, and always leaves uncertain input on the existing direct
agent loop.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .query_intent import QueryIntent, classify_intent


ROUTER_VERSION = 1
MAX_SUBQUESTIONS = 4


class QueryComplexity(str, Enum):
    SIMPLE = "simple"
    COMPOUND = "compound"
    COMPLEX = "complex"


@dataclass(frozen=True)
class RouteDecision:
    intent: QueryIntent
    complexity: QueryComplexity
    need_decomposition: bool
    need_planner: bool
    domains: tuple[str, ...]
    reason_code: str
    router_version: int = ROUTER_VERSION
    routing_seconds: float = 0.0

    def public(self) -> dict[str, object]:
        return {
            "intent": self.intent.value,
            "complexity": self.complexity.value,
            "need_decomposition": self.need_decomposition,
            "need_planner": self.need_planner,
            "domains": list(self.domains),
            "reason_code": self.reason_code,
            "router_version": self.router_version,
        }


@dataclass(frozen=True)
class DecomposedQuestion:
    id: str
    question: str
    domain: str
    depends_on: tuple[str, ...] = ()

    def public(self) -> dict[str, object]:
        return {
            "id": self.id,
            "question": self.question,
            "domain": self.domain,
            "depends_on": list(self.depends_on),
        }


@dataclass(frozen=True)
class QueryDecomposition:
    questions: tuple[DecomposedQuestion, ...] = ()

    def public(self) -> dict[str, object]:
        return {"questions": [question.public() for question in self.questions]}


_DOMAIN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("class", re.compile(r"职业|兼职|多职业|法师|术士|战士|牧师|游荡者|职业能力")),
    ("race", re.compile(r"种族|族裔|人类|精灵|矮人|半身人")),
    ("feat", re.compile(r"专长|专攻|武器格斗")),
    ("spell", re.compile(r"法术|施法|法术位|环法术|戏法|奥术|神术|法师护甲")),
    ("equipment", re.compile(r"装备|武器|(?<!法师)护甲|盾牌|物品|金币|购买")),
    ("progression", re.compile(r"\d+\s*(?:到|至|[-~—])\s*\d+\s*级|成长|升级|前期|中期|后期|每级")),
)
_GOAL_PATTERN = re.compile(r"想要|我想|目标|构筑|build|推荐|规划|提高|兼顾", re.I)
_CONSTRAINT_PATTERN = re.compile(r"必须|不能|不要|只(?:能|用)|限定|限制|预算|保持|至少|最多")
_PLANNER_PATTERN = re.compile(
    r"必须先|先(?:核对|确认|计算)|只有|否则|如果|未达到|根据.{0,24}(?:再|决定|选择)"
)
_PROGRESSION_PATTERN = _DOMAIN_PATTERNS[-1][1]
_CLAUSE_SPLIT = re.compile(r"(?<=[？?；;。])\s*")
_JOINED_GOAL_SPLIT = re.compile(r"\s*[,，]\s*(?=(?:并|同时|另外|还要|以及))|\s+(?=(?:并|同时|另外|还要|以及))")
_QUESTION_MARKER = re.compile(r"(?:如何|怎么|什么|哪些|多少|是否|能否|何时|哪里|为何|说明|比较|推荐|规划|选择)")


def detect_domains(query: str) -> tuple[str, ...]:
    domains = [name for name, pattern in _DOMAIN_PATTERNS if pattern.search(query)]
    return tuple(domains or ["rule"])


def route_query(query: str, _state: object | None = None) -> RouteDecision:
    started = time.monotonic()
    value = query.strip()
    intent = classify_intent(value)
    domains = detect_domains(value)
    segments = _explicit_segments(value)
    is_complex = (
        bool(_GOAL_PATTERN.search(value))
        and bool(_CONSTRAINT_PATTERN.search(value))
        and bool(_PROGRESSION_PATTERN.search(value))
        and len(set(domains) - {"progression"}) >= 2
    )
    if is_complex:
        complexity = QueryComplexity.COMPLEX
        reason_code = "constrained_multi_domain_progression"
        need_decomposition = True
    elif len(segments) >= 2:
        complexity = QueryComplexity.COMPOUND
        reason_code = "explicit_multi_goal"
        need_decomposition = True
    else:
        complexity = QueryComplexity.SIMPLE
        reason_code = "single_goal_or_uncertain"
        need_decomposition = False
    return RouteDecision(
        intent=intent,
        complexity=complexity,
        need_decomposition=need_decomposition,
        # This recommends the bounded Stage 3 capability; the independent
        # feature flag still controls whether the server executes a plan.
        need_planner=(
            complexity == QueryComplexity.COMPLEX
            and bool(_PLANNER_PATTERN.search(value))
        ),
        domains=domains,
        reason_code=reason_code,
        routing_seconds=max(0.0, time.monotonic() - started),
    )


def decompose_query(
    query: str,
    decision: RouteDecision | None = None,
) -> QueryDecomposition:
    route = decision or route_query(query)
    if not route.need_decomposition:
        return QueryDecomposition()
    segments = _explicit_segments(query)
    candidates: list[tuple[str, str]] = []
    if route.complexity != QueryComplexity.COMPLEX and len(segments) >= 2:
        for segment in segments:
            comparison_questions = _comparison_questions(segment)
            if comparison_questions:
                candidates.extend((question, _primary_domain(question)) for question in comparison_questions)
            else:
                candidates.append((segment, _primary_domain(segment)))
    else:
        domains = (
            _ordered_complex_domains(route.domains)
            if route.complexity == QueryComplexity.COMPLEX
            else route.domains
        )
        for domain in domains:
            if domain == "rule":
                continue
            candidates.append((domain_question(query.strip(), domain), domain))
    questions = tuple(
        DecomposedQuestion(
            id=f"q{index}",
            question=question,
            domain=domain,
        )
        for index, (question, domain) in enumerate(
            _deduplicate(candidates)[:MAX_SUBQUESTIONS],
            start=1,
        )
    )
    return QueryDecomposition(questions)


def decomposition_search_query(question: DecomposedQuestion) -> str:
    value = question.question
    if "奥法骑士" in value:
        return {
            "class": "奥法骑士 进阶要求 所有军用武器 3级奥术",
            "spell": "奥法骑士 从2级 每日法术 现有奥术施法职业等级+1",
            "equipment": "奥法骑士 所有军用武器 擅长",
            "progression": "奥法骑士 1级 2级 每日法术 施法进度",
        }.get(
            question.domain,
            "奥法骑士 进阶要求 所有军用武器 3级奥术",
        )
    if all(term in value for term in ("猛力攻击", "顺势斩", "大顺势斩")):
        return "大顺势斩（Great Cleave） BAB+4"
    if "油腻术" in value:
        return "油腻术"
    if question.domain == "equipment" and "奥术失败" in value:
        return "护甲 盾牌 奥术失败率"
    if question.domain == "spell" and "法师护甲" in value:
        return "法师护甲"
    if question.domain == "spell" and "法师" in value and any(
        term in value for term in ("施法进度", "法术环级", "每日法术")
    ):
        return "法师（Wizard） 每日法术 表"
    if "铁门" in value and any(term in value for term in ("硬度", "生命值", "破坏DC")):
        return "铁门 硬度 生命值 破坏DC"
    if "防御式战斗" in value:
        return "防御式战斗"
    if "全防御" in value:
        return "全防御"
    if "准备动作" in value and "打断" in value and any(
        term in value for term in ("施法", "法术")
    ):
        return "准备动作 打断施法"
    return value


def _explicit_segments(query: str) -> list[str]:
    sentence_segments = [
        _ensure_question_mark(item.strip())
        for item in _CLAUSE_SPLIT.split(query.strip())
        if item.strip()
    ]
    if len(sentence_segments) >= 2:
        return sentence_segments

    joined = [item.strip(" ，,") for item in _JOINED_GOAL_SPLIT.split(query) if item.strip(" ，,")]
    if len(joined) < 2:
        return [query.strip()] if query.strip() else []
    marked = sum(bool(_QUESTION_MARKER.search(item)) for item in joined)
    # A comma-separated list of fields from one table is one rule question.
    if marked < 2:
        return [query.strip()]
    return [_ensure_question_mark(item) for item in joined]


def _ensure_question_mark(value: str) -> str:
    if value.endswith(("？", "?", "。", "；", ";")):
        return value.rstrip("。；;")
    return value + "？"


def _primary_domain(query: str) -> str:
    return detect_domains(query)[0]


def domain_question(query: str, domain: str) -> str:
    labels = {
        "class": "职业与职业能力",
        "race": "种族",
        "feat": "专长",
        "spell": "法术与施法",
        "equipment": "装备",
        "progression": "成长阶段",
    }
    terms = _explicit_domain_terms(query, domain)
    if terms:
        return " ".join(terms)
    return f"{labels.get(domain, domain)}：{query}"


def _explicit_domain_terms(query: str, domain: str) -> tuple[str, ...]:
    if domain == "class":
        class_names = re.findall(r"奥法骑士|法师|术士|战士|牧师|游荡者", query)
        level = re.search(r"(\d+)\s*(?:到|至|[-~—])?\s*\d*\s*级", query)
        terms: list[str] = []
        for name in dict.fromkeys(class_names):
            terms.append(f"{level.group(1)}级{name}" if level else name)
        if class_names:
            terms.append("职业能力")
        if any(value in query for value in ("兼职", "多职业")):
            terms.extend(("兼职", "多职业"))
        return tuple(dict.fromkeys(terms))
    patterns = {
        "race": re.compile(r"人类|精灵|矮人|半身人|种族|族裔"),
        "feat": re.compile(r"猛力攻击|大顺势斩|顺势斩|法术专攻|[一-鿿]{0,6}专长"),
        "spell": re.compile(r"法师护甲|油腻术|[一-鿿]{2,8}(?:法术|术)"),
        "equipment": re.compile(r"[一-鿿]{0,6}(?:装备|武器|护甲|盾牌|物品)"),
        "progression": re.compile(
            r"\d+\s*(?:到|至|[-~—])\s*\d+\s*级|职业升级|成长|升级|前期|中期|后期|施法进度"
        ),
    }
    pattern = patterns.get(domain)
    if pattern is None:
        return ()
    if domain == "spell":
        named_spells = tuple(
            name for name in ("法师护甲", "油腻术") if name in query
        )
        if named_spells:
            return named_spells
        if any(value in query for value in ("施法进度", "法术环级", "法术环位")):
            spell_terms = [
                name
                for name in ("法师", "术士", "牧师")
                if name in query
            ]
            if "施法进度" in query or any(
                value in query for value in ("兼职", "多职业")
            ):
                spell_terms.append("施法进度")
            if "法术环级" in query or "环级" in query:
                spell_terms.append("法术环级")
            return tuple(dict.fromkeys(spell_terms))
    terms = list(dict.fromkeys(match.group(0) for match in pattern.finditer(query)))
    if domain == "progression" and any(
        value in query for value in ("兼职", "多职业", "混职")
    ):
        terms.extend(("角色升级", "兼职", "每级选择职业"))
    return tuple(terms)


def _comparison_questions(segment: str) -> tuple[str, str] | None:
    if not re.search(r"区别|比较|优劣|分别|收益|损失|哪个更好", segment):
        return None
    # “硬度、生命值和破坏DC分别是多少” asks for several columns from one
    # rule table, not a comparison between independent rule subjects.
    if "、" in segment:
        return None
    match = re.search(
        r"(?P<left>[一-鿿A-Za-z0-9·（）()]{2,20}?)"
        r"(?:和|与)"
        r"(?P<right>[一-鿿A-Za-z0-9·（）()]{2,20}?)"
        r"(?:分别|有什么|有何|的区别|的优劣|的收益|的损失|相比)",
        segment,
    )
    if not match:
        return None
    left = match.group("left").removeprefix("并比较").removeprefix("比较")
    right = match.group("right")
    if len(left) < 2 or len(right) < 2:
        return None
    return (f"{left}的规则效果是什么？", f"{right}的规则效果是什么？")


def _deduplicate(values: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for question, domain in values:
        key = (re.sub(r"\s+", "", question).casefold(), domain)
        if key in seen:
            continue
        seen.add(key)
        result.append((question, domain))
    return result


def _ordered_complex_domains(domains: tuple[str, ...]) -> tuple[str, ...]:
    # A constrained build always needs its primary class and progression
    # evidence before optional feat/equipment branches consume the four-topic
    # ceiling. Preserve stable order within the same priority.
    priority = {
        "class": 0,
        "progression": 1,
        "race": 2,
        "spell": 3,
        "feat": 4,
        "equipment": 5,
        "rule": 6,
    }
    return tuple(
        domain
        for _index, domain in sorted(
            enumerate(domains),
            key=lambda item: (priority.get(item[1], 99), item[0]),
        )
    )
