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
_ABILITY_NAMES = ("力量", "敏捷", "体质", "智力", "感知", "魅力")
_ABILITY_PATTERN = "|".join(_ABILITY_NAMES)
_CLASS_HINTS = (
    "吟游诗人",
    "圣武士",
    "野蛮人",
    "德鲁伊",
    "游侠",
    "术士",
    "牧师",
    "法师",
    "战士",
    "盗贼",
    "武僧",
)


@dataclass
class ConversationState:
    task: str | None = None
    facts: dict[str, str] = field(default_factory=dict)
    characterLevel: int | None = None
    classLevels: dict[str, int] = field(default_factory=dict)
    plannedClassLevels: dict[str, int | None] = field(default_factory=dict)
    plannedDipLevels: dict[str, int] = field(default_factory=dict)
    rolePreference: str | None = None
    race: str | None = None
    abilityScores: dict[str, int] = field(default_factory=dict)
    feats: list[str] = field(default_factory=list)
    spells: list[str] = field(default_factory=list)
    _character_level_explicit: bool = field(
        default=False, init=False, repr=False, compare=False
    )

    @classmethod
    def from_messages(cls, messages: list[dict[str, str]]) -> "ConversationState":
        state = cls()
        for message in messages:
            if message.get("role") != "user":
                continue
            state.observe(str(message.get("content", "")))
        return state

    def observe(self, content: str) -> None:
        if re.search(r"(?:创建|建立|做|构筑).{0,6}(?:角色|人物)", content):
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

        self._observe_structured_state(content)

    def _observe_structured_state(self, content: str) -> None:
        """Apply only state explicitly stated in one user message.

        This method deliberately does not look at assistant text or try to fill
        in missing values from other fields.  A later explicit assignment wins
        for scalar fields and per-key mappings, matching the legacy ``facts``
        behavior.
        """
        explicit_level = _extract_character_level(content)
        if explicit_level is not None:
            self.characterLevel = explicit_level
            self._character_level_explicit = True

        dip_levels, dip_spans = _extract_planned_dips(content)
        self.plannedDipLevels.update(dip_levels)

        current_levels = _extract_class_levels(content, excluded_spans=dip_spans)
        if current_levels:
            # A newly stated level composition is a replacement choice.  This
            # prevents a later "法师5" from retaining an earlier fighter dip,
            # while a phrase containing both classes ("法师5/战士1") replaces
            # the old composition in one operation.
            self.classLevels = current_levels
            self.characterLevel = sum(self.classLevels.values())

        planned_levels = _extract_planned_class_levels(content)
        self.plannedClassLevels.update(planned_levels)

        for field_name, value in self.facts.items():
            if field_name == "种族":
                self.race = value

        preference = _extract_role_preference(content)
        if preference is not None:
            self.rolePreference = preference

        self.abilityScores.update(_extract_ability_scores(content))

        feats = _extract_named_choices(content, ("专长", "特技"))
        if feats:
            self.feats = feats

        spells = _extract_named_choices(content, ("法术", "咒语"))
        if spells:
            self.spells = spells

    def prompt_context(self) -> str:
        if not self._has_state():
            return ""
        return json.dumps(
            {
                "task": self.task,
                "characterLevel": self.characterLevel,
                "classLevels": self.classLevels,
                "plannedClassLevels": self.plannedClassLevels,
                "plannedDipLevels": self.plannedDipLevels,
                "rolePreference": self.rolePreference,
                "race": self.race,
                "abilityScores": self.abilityScores,
                "feats": self.feats,
                "spells": self.spells,
                "facts": self.facts,
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    def _has_state(self) -> bool:
        return bool(
            self.task
            or self.facts
            or self.characterLevel is not None
            or self.classLevels
            or self.plannedClassLevels
            or self.plannedDipLevels
            or self.rolePreference
            or self.race
            or self.abilityScores
            or self.feats
            or self.spells
        )

    def field_count(self) -> int:
        """Return a privacy-safe count of populated state values."""
        return sum(
            [
                int(bool(self.task)),
                int(self.characterLevel is not None),
                len(self.classLevels),
                len(self.plannedClassLevels),
                len(self.plannedDipLevels),
                int(bool(self.rolePreference)),
                int(bool(self.race)),
                len(self.abilityScores),
                len(self.feats),
                len(self.spells),
                len(self.facts),
            ]
        )

    def answer_guidance(self, question: str) -> str:
        mentioned = [
            (question.rfind(field), _FIELD_ALIASES.get(field, field))
            for field in _FIELDS
            if field in question
        ]
        if not mentioned:
            return ""
        target = max(mentioned)[1]
        entity_constraints = {
            field: self.facts[field]
            for field in _ENTITY_FIELDS
            if self.facts.get(field)
        }
        parallel_state = {
            field: value
            for field, value in self.facts.items()
            if field != target and field not in entity_constraints
        }
        return (
            f"本题目标字段：{target}。"
            f"已知实体约束：{json.dumps(entity_constraints, ensure_ascii=False)}。"
            f"其他并列状态：{json.dumps(parallel_state, ensure_ascii=False)}。"
            "应依据实体约束回答目标字段；除非规则原文明示，其他并列状态不缩小"
            "目标字段范围，也不与目标字段建立对应关系。"
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


def _extract_character_level(content: str) -> int | None:
    patterns = (
        r"(?:角色|人物|总)?(?:等级|级别)\s*(?:为|是|=|：|:)\s*(?P<level>\d+)",
        rf"(?P<level>\d+)\s*级?\s*(?:{'|'.join(_CLASS_HINTS)})",
        r"(?P<level>\d+)\s*级(?:角色|人物)",
    )
    for pattern in patterns:
        match = re.search(pattern, content)
        if match:
            return int(match.group("level"))
    return None


def _extract_planned_dips(content: str) -> tuple[dict[str, int], list[tuple[int, int]]]:
    levels: dict[str, int] = {}
    spans: list[tuple[int, int]] = []
    pattern = re.compile(
        r"兼职\s*(?P<level>\d+)\s*级?\s*(?P<class>[^/／，,。！？!?\s]+)"
    )
    for match in pattern.finditer(content):
        class_name = _clean_class_name(match.group("class"))
        if class_name:
            levels[class_name] = int(match.group("level"))
            spans.append(match.span())
    return levels, spans


def _extract_class_levels(
    content: str,
    *,
    excluded_spans: list[tuple[int, int]] | None = None,
) -> dict[str, int]:
    levels: dict[str, int] = {}
    excluded_spans = excluded_spans or []
    for segment_match in re.finditer(r"[^/／，,。！？!?]+", content):
        if any(
            segment_match.start() < end and segment_match.end() > start
            for start, end in excluded_spans
        ):
            continue
        segment = segment_match.group(0).strip()
        level_match = re.search(
            r"(?P<level>\d+)\s*级?\s*(?P<class>[^\s\d][^\s]*)", segment
        )
        if level_match:
            class_name = _clean_class_name(level_match.group("class"))
            if class_name:
                levels[class_name] = int(level_match.group("level"))
                continue

        for class_name in sorted(_CLASS_HINTS, key=len, reverse=True):
            level_match = re.search(
                rf"{re.escape(class_name)}\s*"
                r"(?P<level>\d+)\s*级?(?=$|[/／，,、。！？!?\s])",
                segment,
            )
            if level_match:
                levels[class_name] = int(level_match.group("level"))
                break
    return levels


def _extract_planned_class_levels(content: str) -> dict[str, int | None]:
    levels: dict[str, int | None] = {}
    pattern = re.compile(
        r"(?:继续|计划|打算|准备|想要)\s*(?:升|提升|玩|练|走)?\s*"
        r"(?P<class>法师|战士|盗贼|牧师|游侠|术士|武僧|圣武士|德鲁伊|吟游诗人)"
        r"(?:到\s*(?P<level>\d+)\s*级?)?"
    )
    for match in pattern.finditer(content):
        class_name = _clean_class_name(match.group("class"))
        if class_name:
            level = match.group("level")
            levels[class_name] = int(level) if level is not None else None
    return levels


def _extract_role_preference(content: str) -> str | None:
    matches: list[tuple[int, str]] = []
    for pattern in (
        r"(?:我(?:主要)?想|希望|倾向于|偏好|更注重|目标是)\s*(?P<value>[^，。！？!?\n]+)",
        r"(?P<value>不考虑[^，。！？!?\n]+)",
        r"(?P<value>避免[^，。！？!?\n]+)",
    ):
        matches.extend(
            (match.start("value"), match.group("value").strip())
            for match in re.finditer(pattern, content)
        )
    for _, value in sorted(matches, reverse=True):
        if not re.match(r"^(?:创建|建立|做(?:一个|个)?|构筑)", value):
            return value
    return None


def _extract_ability_scores(content: str) -> dict[str, int]:
    pattern = re.compile(
        rf"(?P<ability>{_ABILITY_PATTERN})\s*(?:为|是|=|：|:)\s*(?P<score>\d+)"
        rf"|(?P<ability_b>{_ABILITY_PATTERN})\s*(?P<score_b>\d+)"
    )
    scores: dict[str, int] = {}
    for match in pattern.finditer(content):
        ability = match.group("ability") or match.group("ability_b")
        score = match.group("score") or match.group("score_b")
        scores[ability] = int(score)
    return scores


def _extract_named_choices(content: str, field_names: tuple[str, ...]) -> list[str]:
    field_pattern = "|".join(field_names)
    patterns = (
        rf"(?:选择(?:了)?\s*)?(?P<value>[^，。！？!?\n]+?)\s*(?:作为|为|是)\s*(?:{field_pattern})",
        rf"(?:{field_pattern})\s*(?:为|是|：|:)\s*(?P<value>[^，。！？!?\n]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, content)
        if match:
            values = re.split(r"[、,，和]\s*", _clean_value(match.group("value")))
            return [value.strip() for value in values if value.strip()]
    return []


def _clean_class_name(value: str) -> str:
    cleaned = value.strip(" \t\r\n，,、。；;：:为是作为")
    for hint in sorted(_CLASS_HINTS, key=len, reverse=True):
        if hint in cleaned:
            return hint
    cleaned = re.sub(r"^(?:我|现在|选择|职业|角色|想|继续|升|提升|计划|打算|准备)+", "", cleaned)
    return cleaned.strip(" \t\r\n")[:12]


def _clean_value(value: str) -> str:
    cleaned = re.sub(r"^(?:了|一个|一种)", "", value.strip())
    cleaned = re.sub(r"(?:作为)?$", "", cleaned).strip()
    return cleaned[:40]


def _is_task_scoped_entity_lookup(value: str) -> bool:
    return bool(
        any(field in value for field in _ENTITY_FIELDS)
        and re.search(r"(?:哪|什么|可选|选择|列出|种类)", value)
    )
