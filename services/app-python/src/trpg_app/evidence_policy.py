"""Deterministic evidence budgets layered on the existing tool loop."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .context_budget import ContextBudget
from .conversation_state import ConversationState
from .query_intent import QueryIntent, QueryPlan


@dataclass(frozen=True)
class EvidenceBudgetProfile:
    max_searches: int
    max_answer_documents: int
    max_evidence_tokens: int
    topic_allocations: dict[str, float]
    policy_version: int


class EvidenceBudgetPolicy:
    """Allocate bounded evidence capacity without another model call."""

    POLICY_VERSION = 2

    @classmethod
    def allocate(
        cls,
        intent: QueryIntent,
        context_budget: ContextBudget,
        query_plan: QueryPlan,
        conversation_state: ConversationState,
    ) -> EvidenceBudgetProfile:
        del conversation_state
        available = max(0, context_budget.evidence_tokens)
        if intent == QueryIntent.RULE_FACT:
            if query_plan.queries and cls.is_follow_up(query_plan.queries[-1]):
                return cls._profile(5, 6, min(24_000, available), {"rule": 1.0})
            return cls._profile(3, 4, min(12_000, available), {"rule": 1.0})
        if intent == QueryIntent.PROCEDURE:
            return cls._profile(4, 6, min(24_000, available), {"steps": 1.0})
        if intent == QueryIntent.COMPARE_OPTIONS:
            return cls._profile(
                5,
                8,
                min(32_000, available),
                {"option_a": 0.45, "option_b": 0.45, "shared": 0.10},
            )
        if intent == QueryIntent.BUILD_ADVICE:
            return cls._profile(
                6,
                8,
                min(40_000, available),
                {"class": 0.34, "feat_spell": 0.33, "multiclass": 0.33},
            )
        return cls.fixed(context_budget)

    @classmethod
    def fixed(cls, context_budget: ContextBudget) -> EvidenceBudgetProfile:
        """Describe the pre-Stage-1 limits used when the feature is disabled."""
        return EvidenceBudgetProfile(
            max_searches=6,
            max_answer_documents=8,
            max_evidence_tokens=max(0, context_budget.evidence_tokens),
            topic_allocations={},
            policy_version=0,
        )

    @classmethod
    def classify_topic(
        cls,
        intent: QueryIntent,
        original_query: str,
        search_query: str,
        document: dict[str, Any],
        profile: EvidenceBudgetProfile,
    ) -> str:
        """Map evidence to a bounded profile topic without storing query text."""
        if not profile.topic_allocations:
            return ""
        if intent == QueryIntent.RULE_FACT:
            return "rule"
        if intent == QueryIntent.PROCEDURE:
            return "steps"
        searchable = " ".join(
            str(value)
            for value in (
                search_query,
                document.get("title", ""),
                document.get("fullPath", ""),
                document.get("content", ""),
            )
        )
        if intent == QueryIntent.BUILD_ADVICE:
            if re.search(r"兼职|多职业|收益|损失", searchable):
                return "multiclass"
            if re.search(r"属性|专长|法术", searchable):
                return "feat_spell"
            return "class"
        if intent == QueryIntent.COMPARE_OPTIONS:
            options = cls._comparison_options(original_query)
            if options is not None:
                left, right = options
                document_text = " ".join(
                    str(value)
                    for value in (
                        document.get("title", ""),
                        document.get("fullPath", ""),
                        document.get("content", ""),
                    )
                )
                left_match = cls._comparison_option_matches(left, document, document_text)
                right_match = cls._comparison_option_matches(right, document, document_text)
                if left_match and not right_match:
                    return "option_a"
                if right_match and not left_match:
                    return "option_b"
            return "shared"
        return next(iter(profile.topic_allocations))

    @classmethod
    def missing_topic_prompt(
        cls,
        intent: QueryIntent,
        original_query: str,
        covered_topics: set[str],
    ) -> str | None:
        """Describe a comparison side that still needs readable evidence."""
        if intent != QueryIntent.COMPARE_OPTIONS:
            return None
        options = cls._comparison_options(original_query)
        if options is None:
            return None
        left, right = options
        missing_labels = [
            label
            for topic, label in (("option_a", left), ("option_b", right))
            if topic not in covered_topics
        ]
        if not missing_labels:
            return None
        return (
            "比较题仍缺少以下一侧的已读规则证据："
            + "、".join(missing_labels)
            + "。请先用缺失侧的最短规则条目名调用 search_rules，"
            "再用 read_rules 读取对应章节；不要结束回答。"
        )

    @classmethod
    def missing_topic_queries(
        cls,
        intent: QueryIntent,
        original_query: str,
        covered_topics: set[str],
    ) -> tuple[str, ...]:
        if intent != QueryIntent.COMPARE_OPTIONS:
            return ()
        options = cls._comparison_options(original_query)
        if options is None:
            return ()
        left, right = options
        return tuple(
            cls._comparison_option_terms(label)[-1]
            for topic, label in (("option_a", left), ("option_b", right))
            if topic not in covered_topics
        )

    @staticmethod
    def is_follow_up(query: str) -> bool:
        return bool(
            re.match(
                r"^(?:如果|那么|那|它|这个|这种|前者|后者|上述|刚才)",
                query.strip(),
            )
        )

    @staticmethod
    def _comparison_option_terms(option: str) -> tuple[str, ...]:
        if option in {"防御式战斗", "防御式攻击"}:
            return (option, "攻击（Attack）")
        return (option,)

    @classmethod
    def _comparison_option_matches(
        cls,
        option: str,
        document: dict[str, Any],
        document_text: str,
    ) -> bool:
        terms = cls._comparison_option_terms(option)
        if len(terms) > 1:
            return terms[-1] in str(document.get("title", ""))
        return terms[0] in document_text

    @staticmethod
    def _comparison_options(query: str) -> tuple[str, str] | None:
        value = query.strip().rstrip("？?")
        match = re.search(
            r"(?P<left>[^，。；]{1,30}?)(?:和|与|相比)"
            r"(?P<right>[^，。；]{1,30}?)(?:有什么)?(?:区别|不同|优劣|哪个更好)",
            value,
        )
        if not match:
            return None
        left = match.group("left").strip()
        right = re.sub(r"^(?:以)?(?:标准动作|整轮动作)?进行", "", match.group("right")).strip()
        return (left, right) if left and right else None

    @classmethod
    def _profile(
        cls,
        searches: int,
        documents: int,
        tokens: int,
        allocations: dict[str, float],
    ) -> EvidenceBudgetProfile:
        return EvidenceBudgetProfile(
            max_searches=searches,
            max_answer_documents=documents,
            max_evidence_tokens=tokens,
            topic_allocations=allocations,
            policy_version=cls.POLICY_VERSION,
        )
