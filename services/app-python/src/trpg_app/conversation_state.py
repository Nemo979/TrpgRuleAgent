from __future__ import annotations

import json
import re
from dataclasses import dataclass, field


_FIELDS = (
    "基本特性",
    "基本特技",
    "追加特技",
    "角色类型",
    "职业",
    "种族",
    "真身",
    "正体",
    "弱点",
    "专长",
    "技能",
)
_FIELD_PATTERN = "|".join(_FIELDS)
_FIELD_ALIASES = {
    "基本特性": "基本特技",
    "正体": "真身",
}
_ENTITY_FIELDS = ("真身", "职业", "种族", "角色类型")
_CATEGORY_TERMS = ("弱点", "特技", "技能", "专长", "能力", "属性")


@dataclass
class ConversationState:
    task: str | None = None
    facts: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_messages(cls, messages: list[dict[str, str]]) -> "ConversationState":
        state = cls()
        for message in messages:
            if message.get("role") != "user":
                continue
            state.observe(str(message.get("content", "")))
        return state

    def observe(self, content: str) -> None:
        if re.search(r"(?:创建|建立).{0,6}(?:角色|人物)", content):
            self.task = "创建角色"
        patterns = (
            rf"(?:我)?(?:现在)?选择(?:了)?(?P<field>{_FIELD_PATTERN})为(?P<value>[^，。！？,.!?\n]+)",
            rf"(?:我)?(?:现在)?选择(?:了)?(?P<value>[^，。！？,.!?\n]+?)(?:作为|为)(?P<field>{_FIELD_PATTERN})",
            rf"(?P<field>{_FIELD_PATTERN})为(?P<value>[^，。！？,.!?\n]+)",
        )
        for pattern in patterns:
            for match in re.finditer(pattern, content):
                field_name = _FIELD_ALIASES.get(match.group("field"), match.group("field"))
                value = _clean_value(match.group("value"))
                if value:
                    self.facts[field_name] = value

    def prompt_context(self) -> str:
        if not self.task and not self.facts:
            return ""
        return json.dumps(
            {"task": self.task, "facts": self.facts},
            ensure_ascii=False,
            sort_keys=True,
        )

    def enrich_search_query(self, query: str, latest_user_message: str) -> str:
        additions: list[str] = []
        entity = next(
            (self.facts[field] for field in _ENTITY_FIELDS if self.facts.get(field)),
            "",
        )
        combined = f"{latest_user_message} {query}"
        is_selection = bool(
            re.search(r"选择(?:了)?.+(?:作为|为).+", latest_user_message)
        )
        if is_selection:
            additions.extend(self.facts.values())
            additions.extend(self.facts.keys())
        elif entity and (
            any(term in combined for term in _CATEGORY_TERMS)
            or re.search(r"(?:它|这个|该角色)", combined)
        ):
            additions.append(entity)
        elif self.task and re.search(r"(?:下一步|接下来)", combined):
            additions.append(self.task)
        if self.task and _is_task_scoped_entity_lookup(combined):
            additions.append(self.task)
        base = (
            latest_user_message.strip()
            if (self.task or self.facts) and latest_user_message.strip()
            else query.strip()
        )
        if query.strip() and query.strip() not in base:
            additions.insert(0, query.strip())
        return " ".join(
            [base, *[value for value in dict.fromkeys(additions) if value not in base]]
        ).strip()


def _clean_value(value: str) -> str:
    cleaned = re.sub(r"^(?:了|一个|一种)", "", value.strip())
    cleaned = re.sub(r"(?:作为)?$", "", cleaned).strip()
    return cleaned[:40]


def _is_task_scoped_entity_lookup(value: str) -> bool:
    return bool(
        any(field in value for field in _ENTITY_FIELDS)
        and re.search(r"(?:哪|什么|可选|选择|列出|种类)", value)
    )
