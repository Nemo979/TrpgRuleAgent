"""PF1E evidence parsing and deterministic validation adapter.

The ledger only parses facts present in registered rule documents. It is
deliberately narrow: class spell tables, feat-slot timelines, feat
prerequisites, spell metadata, prestige requirements, class bonus feat scopes,
and cited equipment numbers. Unknown formats stay unresolved instead of being
completed from model knowledge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Iterable

from .fact_ledger import (
    EvidenceDocument,
    FactDerivation,
    FactLedger as GenericFactLedger,
    FactRecord,
    FactStatus,
    ValidationIssue,
)
from .fact_ledger_adapter import (
    FACT_LEDGER_ADAPTER_PROTOCOL_VERSION,
    AdapterKey,
)


PF1E_FACT_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class ClassLevelFact:
    class_name: str
    level: int
    max_spell_level: int | None
    spell_slots: tuple[int | None, ...]
    source_label: str

    def public(self) -> dict[str, Any]:
        return {
            "class": self.class_name,
            "level": self.level,
            "max_spell_level": self.max_spell_level,
            "spell_slots": list(self.spell_slots),
            "source": self.source_label,
        }


@dataclass(frozen=True)
class BonusFeatScope:
    class_name: str
    levels: tuple[int, ...]
    allowed_categories: tuple[str, ...]
    source_label: str

    def public(self) -> dict[str, Any]:
        return {
            "class": self.class_name,
            "levels": list(self.levels),
            "allowed_categories": list(self.allowed_categories),
            "source": self.source_label,
        }


@dataclass(frozen=True)
class PrestigeRequirements:
    class_name: str
    requirements: tuple[str, ...]
    source_label: str

    def public(self) -> dict[str, Any]:
        return {
            "class": self.class_name,
            "requirements": list(self.requirements),
            "source": self.source_label,
        }


@dataclass(frozen=True)
class FeatFact:
    name: str
    prerequisites: str
    category: str
    source_label: str

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "prerequisites": self.prerequisites,
            "category": self.category,
            "source": self.source_label,
        }


@dataclass(frozen=True)
class FeatSlotFact:
    level: int
    source_type: str
    source_name: str
    count: int
    allowed_categories: tuple[str, ...]
    source_label: str

    def public(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "source_type": self.source_type,
            "source_name": self.source_name,
            "count": self.count,
            "allowed_categories": list(self.allowed_categories),
            "source": self.source_label,
        }


@dataclass(frozen=True)
class BaseAttackFact:
    class_name: str
    level: int
    bonus: int
    source_label: str

    def public(self) -> dict[str, Any]:
        return {
            "class": self.class_name,
            "level": self.level,
            "bonus": self.bonus,
            "source": self.source_label,
        }


@dataclass(frozen=True)
class SpellFact:
    name: str
    school: str
    class_levels: tuple[tuple[str, int], ...]
    duration: str
    saving_throw: str
    source_label: str

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "school": self.school,
            "class_levels": {
                class_name: level for class_name, level in self.class_levels
            },
            "duration": self.duration,
            "saving_throw": self.saving_throw,
            "source": self.source_label,
        }


FactValidationIssue = ValidationIssue


@dataclass
class PF1EFactData:
    goal: str
    evidence: dict[str, str]
    class_levels: dict[tuple[str, int], ClassLevelFact] = field(default_factory=dict)
    bonus_feat_scopes: list[BonusFeatScope] = field(default_factory=list)
    prestige_requirements: list[PrestigeRequirements] = field(default_factory=list)
    feats: dict[str, FeatFact] = field(default_factory=dict)
    feat_slots: list[FeatSlotFact] = field(default_factory=list)
    base_attack: dict[tuple[str, int], BaseAttackFact] = field(default_factory=dict)
    spells: dict[str, SpellFact] = field(default_factory=dict)
    version: int = PF1E_FACT_SCHEMA_VERSION

    def public(self) -> dict[str, Any]:
        level_range = _goal_level_range(self.goal)
        class_facts = [
            fact
            for fact in self.class_levels.values()
            if level_range is None or level_range[0] <= fact.level <= level_range[1]
        ]
        return {
            "version": self.version,
            "class_levels": [
                fact.public()
                for fact in sorted(
                    class_facts,
                    key=lambda item: (item.class_name, item.level),
                )
            ],
            "bonus_feat_scopes": [item.public() for item in self.bonus_feat_scopes],
            "prestige_requirements": [
                item.public() for item in self.prestige_requirements
            ],
            "feat_count": len(self.feats),
            "feats": [
                self.feats[name].public()
                for name in _goal_relevant_feat_names(self.goal, self.feats)
            ],
            "feat_slots": [
                item.public()
                for item in sorted(
                    self.feat_slots,
                    key=lambda item: (item.level, item.source_type, item.source_name),
                )
                if level_range is None
                or level_range[0] <= item.level <= level_range[1]
            ],
            "base_attack": [
                item.public()
                for item in sorted(
                    self.base_attack.values(),
                    key=lambda item: (item.class_name, item.level),
                )
                if level_range is None
                or level_range[0] <= item.level <= level_range[1]
            ],
            "spells": [
                item.public()
                for item in sorted(
                    self.spells.values(),
                    key=lambda item: item.name,
                )
            ],
        }

    @property
    def record_count(self) -> int:
        return (
            len(self.class_levels)
            + len(self.bonus_feat_scopes)
            + len(self.prestige_requirements)
            + len(self.feats)
            + len(self.feat_slots)
            + len(self.base_attack)
            + len(self.spells)
        )


_CLASS_NAMES = ("法师", "战士", "奥法骑士")
_CITATION = re.compile(r"\[(?:\^)?(S\d+)]")
_ALLOC_NAME = r"(?:法师|战士|奥法骑士)"
_ALLOCATION = re.compile(
    rf"({_ALLOC_NAME})\s*(\d+)(?:\s*[/／]\s*({_ALLOC_NAME})\s*(\d+))+"
)
_ALLOCATION_PART = re.compile(rf"({_ALLOC_NAME})\s*(\d+)")
_LEVEL_TARGET = re.compile(
    r"(?:角色\s*)?(?:达到|等级|至)\s*(\d+)\s*级|(?<![到至\-—~])(\d+)\s*级时"
)
_WIZARD_SPELL_LEVEL = re.compile(
    r"(\d+)\s*级法师[^\n。；]{0,45}?"
    r"(?:最高(?:可)?|获得|解锁|可以施展|可施展|能施展)[^\n。；]{0,20}?(\d+)\s*环",
    re.MULTILINE,
)
_WIZARD_LEVEL_ROW = re.compile(
    r"^\s*(\d+)\s*级[：:][^\n。；]{0,45}?"
    r"(?:获得|解锁|可以|可|能)[^\n。；]{0,20}?(\d+)\s*环",
    re.MULTILINE,
)
_WIZARD_SLOT_COUNT = re.compile(
    r"(\d+)\s*级法师[^\n。；]{0,45}?(?:每天|每日)[^\n。；]{0,20}?"
    r"(\d+)\s*个\s*(\d+)\s*环",
    re.MULTILINE,
)
_NUMERIC_STAT = re.compile(r"\d+\s*gp|\d+d\d+|[+-]\d+%|[+-]\d+\s*(?:AC|攻击|伤害)", re.I)
_SCHOOL_NAMES = (
    "防护系",
    "咒法系",
    "预言系",
    "惑控系",
    "塑能系",
    "幻术系",
    "死灵系",
    "变化系",
)


def _goal_relevant_feat_names(
    goal: str,
    feats: dict[str, FeatFact],
) -> tuple[str, ...]:
    selected = {name for name in feats if name in goal}
    changed = True
    while changed:
        changed = False
        prerequisites = "\n".join(feats[name].prerequisites for name in selected)
        for name in feats:
            if name not in selected and name in prerequisites:
                selected.add(name)
                changed = True
    return tuple(sorted(selected))


def _document_content(document: dict[str, Any]) -> str:
    """Return direct evidence text, with blocks only as an empty-body fallback."""

    direct = str(document.get("content", "")).strip()
    if direct:
        return direct
    metadata = document.get("metadata", {})
    values: list[str] = []
    if isinstance(metadata, dict):
        blocks = metadata.get("structuralBlocks", ())
        if isinstance(blocks, list):
            values.extend(
                str(block.get("content", ""))
                for block in blocks
                if isinstance(block, dict)
            )
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique.append(normalized)
    return "\n".join(unique)


def _build_pf1e_data(
    goal: str,
    labeled_documents: Iterable[tuple[str, dict[str, Any]]],
) -> PF1EFactData:
    documents = tuple(labeled_documents)
    evidence = {
        label: _document_content(document)
        for label, document in documents
        if label and _document_content(document)
    }
    ledger = PF1EFactData(goal=goal, evidence=evidence)
    for label, document in documents:
        title = str(document.get("title", ""))
        content = _document_content(document)
        if "法师" in title and "表：法师" in content:
            _parse_wizard_table(ledger, label, content)
            _parse_wizard_bonus_scope(ledger, label, content)
        if "战士" in title:
            _parse_fighter_progression(ledger, label, content)
        if "角色升级" in title or "Character Advancement" in content:
            _parse_general_feat_slots(ledger, label, content)
        if "人类" in title:
            _parse_human_feat_slot(ledger, label, content)
        if "进阶要求" in content:
            _parse_prestige_requirements(ledger, label, title, content)
        if "专长" in title or "专长列表" in content:
            _parse_feat_rows(ledger, label, content)
        if "学派" in content and "环位" in content:
            _parse_spell(ledger, label, title, content)
    return ledger


def _validate_pf1e_answer(
    content: str,
    ledger: PF1EFactData,
) -> tuple[FactValidationIssue, ...]:
    plain = _plain(content)
    issues: list[FactValidationIssue] = []
    issues.extend(_validate_citations(content, ledger))
    issues.extend(_validate_level_sums(plain))
    issues.extend(_validate_prestige_requirements(plain, ledger))
    issues.extend(_validate_bonus_feat_scope(plain, ledger))
    issues.extend(_validate_feat_timeline(plain, ledger))
    issues.extend(_validate_feat_prerequisites(plain, ledger))
    issues.extend(_validate_spell_progression(plain, ledger))
    issues.extend(_validate_spell_metadata(plain, ledger))
    issues.extend(_validate_cited_numeric_stats(content, ledger))
    issues.extend(_validate_named_recommendations(content, ledger))
    unique: list[FactValidationIssue] = []
    seen: set[tuple[str, str]] = set()
    for issue in issues:
        key = (issue.code, issue.message)
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    return tuple(unique)


def _parse_wizard_table(ledger: PF1EFactData, label: str, content: str) -> None:
    for line in content.splitlines():
        if not re.match(r"^\s*\|?\s*\d+\s*\|", line):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 16 or not cells[0].isdigit():
            continue
        level = int(cells[0])
        slot_cells = cells[6:16]
        slots = tuple(int(value) if value.isdigit() else None for value in slot_cells)
        available = [index for index, value in enumerate(slots) if value is not None]
        ledger.class_levels[("法师", level)] = ClassLevelFact(
            class_name="法师",
            level=level,
            max_spell_level=max(available) if available else None,
            spell_slots=slots,
            source_label=label,
        )


def _parse_wizard_bonus_scope(ledger: PF1EFactData, label: str, content: str) -> None:
    match = re.search(
        r"奖励专长：在5，10，15，20级时.*?挑选([^。]+)",
        content,
        re.DOTALL,
    )
    if not match:
        return
    allowed = tuple(
        value
        for value in ("超魔专长", "物品制造专长", "法术掌握")
        if value in match.group(1)
    )
    ledger.bonus_feat_scopes.append(
        BonusFeatScope("法师", (5, 10, 15, 20), allowed, label)
    )


def _append_feat_slot(ledger: PF1EFactData, fact: FeatSlotFact) -> None:
    key = (fact.level, fact.source_type, fact.source_name)
    if not any(
        (item.level, item.source_type, item.source_name) == key
        for item in ledger.feat_slots
    ):
        ledger.feat_slots.append(fact)


def _parse_fighter_progression(
    ledger: PF1EFactData,
    label: str,
    content: str,
) -> None:
    if "表：战士" not in content:
        return
    for line in content.splitlines():
        if "|" not in line:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[0].isdigit():
            match = re.match(r"\+(\d+)", cells[1])
            if match:
                level = int(cells[0])
                ledger.base_attack[("战士", level)] = BaseAttackFact(
                    "战士", level, int(match.group(1)), label
                )
    for match in re.finditer(
        r"表：战士\s*\n\s*(\d+)\s*\|\s*\+(\d+)",
        content,
    ):
        level, bonus = (int(value) for value in match.groups())
        ledger.base_attack[("战士", level)] = BaseAttackFact(
            "战士", level, bonus, label
        )
    if re.search(r"1级以及之后的每个偶数等级[^。]{0,80}奖励专长", content):
        for level in (1, *range(2, 21, 2)):
            _append_feat_slot(
                ledger,
                FeatSlotFact(
                    level=level,
                    source_type="class_bonus",
                    source_name="战士",
                    count=1,
                    allowed_categories=("战斗",),
                    source_label=label,
                ),
            )
        if "意味着战士每级都可获得专长" in content:
            for level in range(1, 21, 2):
                _append_feat_slot(
                    ledger,
                    FeatSlotFact(
                        level=level,
                        source_type="general",
                        source_name="角色升级",
                        count=1,
                        allowed_categories=(),
                        source_label=label,
                    ),
                )


def _parse_general_feat_slots(
    ledger: PF1EFactData,
    label: str,
    content: str,
) -> None:
    if "专长获得" not in content:
        return
    levels = {
        int(match.group(1))
        for match in re.finditer(
            r"(?m)^\s*\|?\s*(\d+)\s*\|\s*第\d+项(?:\s*\||\s*$)",
            content,
        )
    }
    levels.update(
        int(match.group(1))
        for match in re.finditer(r"(?m)^\s*(\d+)\s*\n\s*第\d+项\s*$", content)
    )
    for level in sorted(levels):
        _append_feat_slot(
            ledger,
            FeatSlotFact(
                level=level,
                source_type="general",
                source_name="角色升级",
                count=1,
                allowed_categories=(),
                source_label=label,
            ),
        )


def _parse_human_feat_slot(
    ledger: PF1EFactData,
    label: str,
    content: str,
) -> None:
    if not re.search(
        r"奖励专长[：:]\s*人类角色在1级时获得一个额外专长",
        content,
    ):
        return
    _append_feat_slot(
        ledger,
        FeatSlotFact(
            level=1,
            source_type="ancestry_bonus",
            source_name="人类",
            count=1,
            allowed_categories=(),
            source_label=label,
        ),
    )


def _standalone_field(content: str, name: str, following: str) -> str:
    match = re.search(
        rf"(?ms)^\s*{re.escape(name)}\s*$\s*(.+?)(?=^\s*(?:{following})\s*$)",
        content,
    )
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""


def _parse_spell(
    ledger: PF1EFactData,
    label: str,
    title: str,
    content: str,
) -> None:
    if not re.search(r"(?m)^\s*学派\s*$", content) or not re.search(
        r"(?m)^\s*环位\s*$", content
    ):
        return
    raw_name = re.split(r"[（(\n]", title.strip(), maxsplit=1)[0].strip()
    name_match = re.search(r"[\u4e00-\u9fff]+", raw_name)
    name = name_match.group(0) if name_match else raw_name
    if not name:
        return
    school_text = _standalone_field(content, "学派", "环位")
    school = next((item for item in _SCHOOL_NAMES if item in school_text), "")
    level_text = _standalone_field(
        content,
        "环位",
        "施法时间|成分|距离|目标|区域|效果|持续时间|豁免|法术抗力",
    )
    class_levels: dict[str, int] = {}
    for class_names, level_text_value in re.findall(
        r"([\u4e00-\u9fff]+(?:/[\u4e00-\u9fff]+)*)\s*(\d+)",
        level_text,
    ):
        for class_name in class_names.split("/"):
            class_levels.setdefault(class_name, int(level_text_value))
    if not school or not class_levels:
        return
    duration = _standalone_field(content, "持续时间", "豁免|法术抗力")
    saving_throw = _standalone_field(content, "豁免", "法术抗力")
    ledger.spells.setdefault(
        name,
        SpellFact(
            name=name,
            school=school,
            class_levels=tuple(sorted(class_levels.items())),
            duration=duration,
            saving_throw=saving_throw,
            source_label=label,
        ),
    )


def _parse_prestige_requirements(
    ledger: PF1EFactData,
    label: str,
    title: str,
    content: str,
) -> None:
    section = content.split("进阶要求", 1)[1].split("本职技能", 1)[0]
    requirements = tuple(line.strip() for line in section.splitlines() if line.strip())
    if requirements:
        name = next((item for item in _CLASS_NAMES if item in title), title.strip())
        ledger.prestige_requirements.append(
            PrestigeRequirements(name, requirements, label)
        )


def _parse_feat_rows(ledger: PF1EFactData, label: str, content: str) -> None:
    for line in content.splitlines():
        if "|" not in line or re.match(r"^\s*\|?\s*---", line):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if (
            len(cells) < 4
            or cells[0] in {"施法专长", "造物专长", "专长"}
            or re.fullmatch(r"\d+", cells[0])
            or re.search(r"等级|基本攻击|豁免|特殊能力|每日法术", cells[0])
        ):
            continue
        raw_name = cells[0]
        chinese = re.sub(r"[A-Za-z][A-Za-z '\-’]+", "", raw_name).strip()
        name = chinese or raw_name
        if name:
            ledger.feats.setdefault(
                name,
                FeatFact(name, cells[1], cells[3], label),
            )


def _validate_citations(content: str, ledger: PF1EFactData) -> list[FactValidationIssue]:
    return [
        FactValidationIssue("unknown_citation", f"答案引用了未注册来源 {label}")
        for label in sorted(set(_CITATION.findall(content)) - set(ledger.evidence))
    ]


def _validate_level_sums(content: str) -> list[FactValidationIssue]:
    issues: list[FactValidationIssue] = []
    for line in content.splitlines():
        targets = [int(left or right) for left, right in _LEVEL_TARGET.findall(line)]
        if not targets:
            continue
        for allocation_text in re.findall(
            rf"{_ALLOC_NAME}\s*\d+(?:\s*[/／]\s*{_ALLOC_NAME}\s*\d+)+",
            line,
        ):
            total = sum(int(level) for _name, level in _ALLOCATION_PART.findall(allocation_text))
            target = targets[-1]
            if total != target:
                issues.append(
                    FactValidationIssue(
                        "class_level_sum",
                        f"职业等级分配 {allocation_text} 合计 {total}，不等于声明的角色等级 {target}",
                    )
                )
    return issues


def _validate_prestige_requirements(
    content: str,
    ledger: PF1EFactData,
) -> list[FactValidationIssue]:
    issues: list[FactValidationIssue] = []
    for fact in ledger.prestige_requirements:
        if fact.class_name not in content:
            continue
        requirements = "\n".join(fact.requirements)
        if not re.search(r"BAB|基本攻击", requirements, re.I):
            for line in content.splitlines():
                if (
                    fact.class_name in line
                    and re.search(r"BAB|基本攻击", line, re.I)
                    and re.search(r"要求|进阶条件|条件满足|满足.*条件", line)
                ):
                    issues.append(
                        FactValidationIssue(
                            "invented_prestige_requirement",
                            f"{fact.class_name} 的已读进阶要求不包含 BAB/基本攻击条件",
                        )
                    )
                    break
    return issues


def _validate_bonus_feat_scope(
    content: str,
    ledger: PF1EFactData,
) -> list[FactValidationIssue]:
    issues: list[FactValidationIssue] = []
    for scope in ledger.bonus_feat_scopes:
        if scope.class_name not in content:
            continue
        for level in scope.levels:
            windows = re.finditer(
                rf"{level}\s*级[^\n]{{0,30}}奖励专长|奖励专长[^\n]{{0,30}}{level}\s*级",
                content,
            )
            for window in windows:
                excerpt = content[max(0, window.start() - 40) : window.end() + 180]
                if "战斗专长" in excerpt:
                    issues.append(
                        FactValidationIssue(
                            "bonus_feat_scope",
                            f"{scope.class_name}{level}级奖励专长被错误扩展到战斗专长",
                        )
                    )
                for feat_name, feat in ledger.feats.items():
                    if feat_name not in excerpt or not re.search(r"选择|建议|可选", excerpt):
                        continue
                    allowed = (
                        "超魔" in feat.category
                        or "造物" in feat.category
                        or feat_name in {"法术掌握", "法术熟稔"}
                    )
                    if not allowed:
                        issues.append(
                            FactValidationIssue(
                                "bonus_feat_scope",
                                f"{feat_name} 不在已读的 {scope.class_name}{level}级奖励专长范围内",
                            )
                        )
    return issues


def _level_sections(content: str) -> dict[int, str]:
    sections: dict[int, list[str]] = {}
    current_level: int | None = None
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        level: int | None = None
        if "|" in line:
            first_cell = re.sub(r"[^\d级]", "", line.strip("|").split("|", 1)[0])
            match = re.fullmatch(r"(\d+)级?", first_cell)
            if match:
                level = int(match.group(1))
        if level is None:
            normalized = re.sub(r"^[\s>*#-]+", "", line)
            normalized = re.sub(r"[*_`]", "", normalized)
            match = re.match(r"(\d+)\s*级(?:\s*[：:]|\b)", normalized)
            if match:
                level = int(match.group(1))
        if level is not None:
            current_level = level
        if current_level is not None:
            sections.setdefault(current_level, []).append(line)
    return {level: "\n".join(lines) for level, lines in sections.items()}


def _mentioned_feat_names(section: str, ledger: PF1EFactData) -> set[str]:
    names: set[str] = set()
    occupied: list[tuple[int, int]] = []
    for name in sorted(ledger.feats, key=len, reverse=True):
        for match in re.finditer(re.escape(name), section):
            span = match.span()
            if any(span[0] < end and start < span[1] for start, end in occupied):
                continue
            occupied.append(span)
            names.add(name)
    return names


def _selection_count(section: str, ledger: PF1EFactData) -> int:
    known = _mentioned_feat_names(section, ledger)
    counts: list[int] = [len(known)] if known else []
    for match in re.finditer(
        r"(?:选择专长|获得[^：:\n]{0,12}专长)[：:]\s*([^。；\n]+)",
        section,
    ):
        value = re.sub(r"[`*_]", "", match.group(1))
        choices = [
            item.strip()
            for item in re.split(r"[，,、]", value)
            if item.strip() and "职业特性" not in item
        ]
        counts.append(len(choices))
    numbered = {int(value) for value in re.findall(r"专长\s*(\d+)", section)}
    if numbered:
        counts.append(max(numbered))
    return max(counts, default=0)


def _relevant_feat_slots(ledger: PF1EFactData) -> tuple[FeatSlotFact, ...]:
    level_range = _goal_level_range(ledger.goal)
    if level_range is None or "专长" not in ledger.goal:
        return ()
    relevant: list[FeatSlotFact] = []
    for fact in ledger.feat_slots:
        if not level_range[0] <= fact.level <= level_range[1]:
            continue
        if fact.source_type == "class_bonus" and fact.source_name not in ledger.goal:
            continue
        if fact.source_type == "ancestry_bonus" and fact.source_name not in ledger.goal:
            continue
        relevant.append(fact)
    return tuple(relevant)


def _validate_feat_timeline(
    content: str,
    ledger: PF1EFactData,
) -> list[FactValidationIssue]:
    slots = _relevant_feat_slots(ledger)
    if not slots:
        return []
    sections = _level_sections(content)
    by_level: dict[int, list[FeatSlotFact]] = {}
    for fact in slots:
        by_level.setdefault(fact.level, []).append(fact)
    issues: list[FactValidationIssue] = []
    for level, facts in sorted(by_level.items()):
        expected = sum(item.count for item in facts)
        actual = _selection_count(sections.get(level, ""), ledger)
        if actual >= expected:
            continue
        sources = tuple(dict.fromkeys(item.source_label for item in facts))
        issues.append(
            FactValidationIssue(
                "feat_timeline_slot_count",
                (
                    f"角色{level}级应安排 {expected} 个专长选择，"
                    f"候选答案只安排了 {actual} 个"
                ),
                path=f"answer.levels[{level}].feats",
                expected=expected,
                actual=actual,
                evidence_refs=sources,
            )
        )
    return issues


def _selected_feats_by_level(
    content: str,
    ledger: PF1EFactData,
) -> dict[int, set[str]]:
    return {
        level: _mentioned_feat_names(section, ledger)
        for level, section in _level_sections(content).items()
    }


def _declared_attribute(content: str, attribute: str) -> int | None:
    values = [
        int(value)
        for value in re.findall(
            rf"{re.escape(attribute)}(?:属性)?\s*(?:为|达到|=|：|:)?\s*(\d+)",
            content,
        )
    ]
    return max(values) if values else None


def _validate_feat_prerequisites(
    content: str,
    ledger: PF1EFactData,
) -> list[FactValidationIssue]:
    selections = _selected_feats_by_level(content, ledger)
    issues: list[FactValidationIssue] = []
    for level, selected_names in sorted(selections.items()):
        earlier = {
            name
            for selected_level, names in selections.items()
            if selected_level < level
            for name in names
        }
        section = _level_sections(content).get(level, "")
        for name in sorted(selected_names):
            fact = ledger.feats[name]
            prerequisites = fact.prerequisites
            for required_name in sorted(
                other
                for other in ledger.feats
                if other != name and other in prerequisites
            ):
                if required_name not in earlier:
                    issues.append(
                        FactValidationIssue(
                            "feat_prerequisite_missing",
                            (
                                f"{name} 在 {level} 级选择时尚未获得前提专长 "
                                f"{required_name}"
                            ),
                            path=f"answer.levels[{level}].feats[{name}].prerequisites",
                            expected=required_name,
                            actual=None,
                            evidence_refs=(fact.source_label,),
                        )
                    )
            bab_match = re.search(r"(?:BAB|基本攻击加值)\s*\+?(\d+)", prerequisites, re.I)
            if bab_match:
                required_bab = int(bab_match.group(1))
                declared = re.search(r"(?:BAB|基本攻击加值)\s*\+?(\d+)", section, re.I)
                fighter_fact = ledger.base_attack.get(("战士", level))
                actual_bab: int | None = None
                if declared:
                    actual_bab = int(declared.group(1))
                elif (
                    fighter_fact
                    and "战士" in ledger.goal
                    and "兼职" not in ledger.goal
                ):
                    actual_bab = fighter_fact.bonus
                if actual_bab is None or actual_bab < required_bab:
                    refs = [fact.source_label]
                    if fighter_fact is not None:
                        refs.append(fighter_fact.source_label)
                    actual_text = actual_bab if actual_bab is not None else "未知"
                    issues.append(
                        FactValidationIssue(
                            "feat_bab_prerequisite",
                            (
                                f"{name} 在 {level} 级需要 BAB +{required_bab}，"
                                f"候选状态为 {actual_text}"
                            ),
                            path=f"answer.levels[{level}].feats[{name}].bab",
                            expected=required_bab,
                            actual=actual_bab,
                            evidence_refs=tuple(dict.fromkeys(refs)),
                        )
                    )
            for attribute, required_text in re.findall(
                r"(力量|敏捷|体质|智力|感知|魅力)\s*(\d+)", prerequisites
            ):
                required = int(required_text)
                actual = _declared_attribute(content, attribute)
                if actual is None or actual < required:
                    actual_text = actual if actual is not None else "未知"
                    issues.append(
                        FactValidationIssue(
                            "feat_attribute_prerequisite",
                            (
                                f"{name} 需要 {attribute}{required}，"
                                f"候选状态为 {actual_text}"
                            ),
                            path=f"answer.levels[{level}].feats[{name}].attributes.{attribute}",
                            expected=required,
                            actual=actual,
                            evidence_refs=(fact.source_label,),
                        )
                    )
    return issues


def _validate_spell_progression(
    content: str,
    ledger: PF1EFactData,
) -> list[FactValidationIssue]:
    issues: list[FactValidationIssue] = []
    if "法师" not in content:
        return issues
    spell_claims = [
        *_WIZARD_SPELL_LEVEL.findall(content),
        *_WIZARD_LEVEL_ROW.findall(content),
    ]
    for level_text, spell_text in spell_claims:
        level = int(level_text)
        claimed = int(spell_text)
        fact = ledger.class_levels.get(("法师", level))
        if fact is not None and fact.max_spell_level is not None and claimed != fact.max_spell_level:
            issues.append(
                FactValidationIssue(
                    "spell_progression",
                    f"法师{level}级最高法术环级应为 {fact.max_spell_level}，候选答案写为 {claimed}",
                )
            )
    for level_text, count_text, spell_text in _WIZARD_SLOT_COUNT.findall(content):
        level = int(level_text)
        claimed_count = int(count_text)
        spell_level = int(spell_text)
        fact = ledger.class_levels.get(("法师", level))
        if fact is None or spell_level >= len(fact.spell_slots):
            continue
        expected_count = fact.spell_slots[spell_level]
        if expected_count is not None and claimed_count != expected_count:
            issues.append(
                FactValidationIssue(
                    "spell_slot_count",
                    f"法师{level}级的{spell_level}环基础每日法术位应为 {expected_count}，候选答案写为 {claimed_count}",
                )
            )
    return issues


def _canonical_school_claims(value: str) -> tuple[str, ...]:
    claims: list[str] = []
    for match in re.finditer(
        r"(防护|咒法|预言|惑控|塑能|幻术|死灵|变化)(?:系|学派)",
        value,
    ):
        prefix = value[max(0, match.start() - 5) : match.start()]
        if re.search(r"(?:不是|并非|而非|不属于|并不属于)\s*$", prefix):
            continue
        claims.append(f"{match.group(1)}系")
    return tuple(dict.fromkeys(claims))


def _spell_windows(content: str, name: str) -> tuple[str, ...]:
    windows: list[str] = []
    for match in re.finditer(re.escape(name), content):
        starts = [content.rfind(token, 0, match.start()) for token in ("\n", "。", "；")]
        start = max(starts) + 1
        stops = [
            position
            for token in ("\n", "。", "；")
            if (position := content.find(token, match.end())) >= 0
        ]
        stop = min(stops) if stops else min(len(content), match.end() + 80)
        windows.append(content[start:stop])
    return tuple(windows)


def _normalized_rule_text(value: str) -> str:
    return re.sub(r"[\s，,。；;：（）()]", "", value).lower()


def _validate_spell_metadata(
    content: str,
    ledger: PF1EFactData,
) -> list[FactValidationIssue]:
    if not ledger.spells:
        return []
    issues: list[FactValidationIssue] = []
    for name, fact in sorted(ledger.spells.items()):
        if name not in content:
            continue
        levels = dict(fact.class_levels)
        expected_level = levels.get("法师") or levels.get("术士/法师")
        for window in _spell_windows(content, name):
            tail = window.split(name, 1)[1][:40]
            level_claims: set[int] = set()
            for level_match in re.finditer(r"(\d+)\s*环", tail):
                prefix = tail[max(0, level_match.start() - 4) : level_match.start()]
                if re.search(r"(?:不是|并非)\s*$", prefix):
                    continue
                level_claims.add(int(level_match.group(1)))
            if expected_level is not None:
                for claimed in sorted(level_claims):
                    if claimed != expected_level:
                        issues.append(
                            FactValidationIssue(
                                "spell_level",
                                (
                                    f"{name} 的法师法术环级应为 {expected_level}，"
                                    f"候选答案写为 {claimed}"
                                ),
                                path=f"answer.spells[{name}].level",
                                expected=expected_level,
                                actual=claimed,
                                evidence_refs=(fact.source_label,),
                            )
                        )
            for claimed_school in _canonical_school_claims(window):
                if claimed_school != fact.school:
                    issues.append(
                        FactValidationIssue(
                            "spell_school",
                            (
                                f"{name} 的学派应为 {fact.school}，"
                                f"候选答案写为 {claimed_school}"
                            ),
                            path=f"answer.spells[{name}].school",
                            expected=fact.school,
                            actual=claimed_school,
                            evidence_refs=(fact.source_label,),
                        )
                    )
            duration_match = re.search(
                r"(?:持续时间\s*(?:为|[：:])?|持续)\s*([^，。；\n]{1,30})",
                window,
            )
            if duration_match and fact.duration:
                claimed_duration = duration_match.group(1).strip()
                expected_duration = _normalized_rule_text(fact.duration)
                normalized_claim = _normalized_rule_text(claimed_duration)
                if normalized_claim and normalized_claim not in expected_duration:
                    issues.append(
                        FactValidationIssue(
                            "spell_duration",
                            (
                                f"{name} 的持续时间应为 {fact.duration}，"
                                f"候选答案写为 {claimed_duration}"
                            ),
                            path=f"answer.spells[{name}].duration",
                            expected=fact.duration,
                            actual=claimed_duration,
                            evidence_refs=(fact.source_label,),
                        )
                    )
            saving_match = re.search(
                r"豁免\s*(?:为|[：:])\s*([^，。；\n]{1,30})",
                window,
            )
            if saving_match and fact.saving_throw:
                claimed_saving = saving_match.group(1).strip()
                expected_saving = _normalized_rule_text(fact.saving_throw)
                normalized_claim = _normalized_rule_text(claimed_saving)
                if normalized_claim and normalized_claim not in expected_saving:
                    issues.append(
                        FactValidationIssue(
                            "spell_saving_throw",
                            (
                                f"{name} 的豁免应为 {fact.saving_throw}，"
                                f"候选答案写为 {claimed_saving}"
                            ),
                            path=f"answer.spells[{name}].saving_throw",
                            expected=fact.saving_throw,
                            actual=claimed_saving,
                            evidence_refs=(fact.source_label,),
                        )
                    )
    known_names = set(ledger.spells)
    candidates = {
        name
        for name, _level in re.findall(
            r"(?:准备|施放|学习|推荐|选择|获得)(?:并施放)?(?:[`*“”‘’]*)"
            r"([\u4e00-\u9fff]{2,6}术)(?:[`*“”‘’]*)\s*[（(](\d+)\s*环",
            content,
        )
    }
    for name in sorted(candidates - known_names):
        issues.append(
            FactValidationIssue(
                "unsupported_named_spell",
                f"推荐的法术 {name} 未出现在本轮已读法术条目中",
                path=f"answer.spells[{name}]",
                expected="registered spell evidence",
                actual=name,
                evidence_refs=(),
            )
        )
    return issues


def _validate_cited_numeric_stats(
    content: str,
    ledger: PF1EFactData,
) -> list[FactValidationIssue]:
    issues: list[FactValidationIssue] = []
    for sentence in re.split(r"(?<=[。；\n])", content):
        labels = _CITATION.findall(sentence)
        stats = _NUMERIC_STAT.findall(sentence)
        if not labels or not stats:
            continue
        evidence = "\n".join(ledger.evidence.get(label, "") for label in labels)
        normalized_evidence = re.sub(r"\s+", "", evidence).lower()
        for stat in stats:
            if re.sub(r"\s+", "", stat).lower() not in normalized_evidence:
                issues.append(
                    FactValidationIssue(
                        "unsupported_numeric_stat",
                        f"数值 {stat} 未出现在该句引用的来源中",
                    )
                )
    return issues


def _validate_named_recommendations(
    content: str,
    ledger: PF1EFactData,
) -> list[FactValidationIssue]:
    if "专长" not in ledger.goal:
        return []
    issues: list[FactValidationIssue] = []
    for match in re.finditer(
        r"(?:建议选择|可选择|选择[：:]|替代专长[：:为])\s*"
        r"(?:\*\*|[‘’“”])?([\u4e00-\u9fffA-Za-z]+)",
        content,
    ):
        name = match.group(1).strip()
        if name in {"一个", "其他", "任意", "符合", "新的"}:
            continue
        if name in {"法术掌握", "法术熟稔"} and any(
            name in scope.allowed_categories
            or "法术掌握" in scope.allowed_categories
            for scope in ledger.bonus_feat_scopes
        ):
            continue
        if name not in ledger.feats:
            issues.append(
                FactValidationIssue(
                    "unsupported_named_option",
                    f"推荐的专长 {name} 未出现在本轮已读专长条目中",
                )
            )
    return issues


def _plain(content: str) -> str:
    value = re.sub(r"\[(?:\^)?S\d+]", "", content)
    return re.sub(r"[*_`#]", "", value)


def _goal_level_range(goal: str) -> tuple[int, int] | None:
    match = re.search(r"(\d+)\s*(?:到|至|[-—~])\s*(\d+)\s*级", goal)
    if not match:
        return None
    lower, upper = (int(match.group(1)), int(match.group(2)))
    return (min(lower, upper), max(lower, upper))


PF1E_ADAPTER_ID = "pathfinder-1e"
PF1E_ADAPTER_VERSION = 2
PF1E_ADAPTER_KEY = AdapterKey(
    library_id="pathfinder-1e",
    system="Pathfinder",
    edition="1E",
)


def _records(data: PF1EFactData) -> tuple[FactRecord, ...]:
    records: list[FactRecord] = []
    for fact in sorted(
        data.class_levels.values(),
        key=lambda item: (item.class_name, item.level),
    ):
        records.append(
            FactRecord(
                adapter_id=PF1E_ADAPTER_ID,
                adapter_version=PF1E_ADAPTER_VERSION,
                subject=f"{fact.class_name}:{fact.level}",
                predicate="class_level",
                value=fact.public(),
                value_type="class_level",
                status=FactStatus.KNOWN,
                derivation=FactDerivation.OBSERVED,
                evidence_refs=(fact.source_label,),
            )
        )
    for fact in data.bonus_feat_scopes:
        records.append(
            FactRecord(
                adapter_id=PF1E_ADAPTER_ID,
                adapter_version=PF1E_ADAPTER_VERSION,
                subject=fact.class_name,
                predicate="bonus_feat_scope",
                value=fact.public(),
                value_type="scope",
                evidence_refs=(fact.source_label,),
            )
        )
    for fact in data.prestige_requirements:
        records.append(
            FactRecord(
                adapter_id=PF1E_ADAPTER_ID,
                adapter_version=PF1E_ADAPTER_VERSION,
                subject=fact.class_name,
                predicate="prestige_requirements",
                value=fact.public(),
                value_type="requirements",
                evidence_refs=(fact.source_label,),
            )
        )
    for fact in sorted(data.feats.values(), key=lambda item: item.name):
        records.append(
            FactRecord(
                adapter_id=PF1E_ADAPTER_ID,
                adapter_version=PF1E_ADAPTER_VERSION,
                subject=fact.name,
                predicate="feat",
                value=fact.public(),
                value_type="option",
                evidence_refs=(fact.source_label,),
            )
        )
    for fact in sorted(
        data.feat_slots,
        key=lambda item: (item.level, item.source_type, item.source_name),
    ):
        records.append(
            FactRecord(
                adapter_id=PF1E_ADAPTER_ID,
                adapter_version=PF1E_ADAPTER_VERSION,
                subject=f"character:{fact.level}",
                predicate="feat_slot",
                value=fact.public(),
                value_type="slot",
                evidence_refs=(fact.source_label,),
            )
        )
    for fact in sorted(
        data.base_attack.values(),
        key=lambda item: (item.class_name, item.level),
    ):
        records.append(
            FactRecord(
                adapter_id=PF1E_ADAPTER_ID,
                adapter_version=PF1E_ADAPTER_VERSION,
                subject=f"{fact.class_name}:{fact.level}",
                predicate="base_attack_bonus",
                value=fact.public(),
                value_type="integer",
                evidence_refs=(fact.source_label,),
            )
        )
    for fact in sorted(data.spells.values(), key=lambda item: item.name):
        records.append(
            FactRecord(
                adapter_id=PF1E_ADAPTER_ID,
                adapter_version=PF1E_ADAPTER_VERSION,
                subject=fact.name,
                predicate="spell_metadata",
                value=fact.public(),
                value_type="spell",
                evidence_refs=(fact.source_label,),
            )
        )
    return tuple(records)


def _adapter_data(ledger: GenericFactLedger) -> PF1EFactData:
    if ledger.adapter_id != PF1E_ADAPTER_ID or not isinstance(
        ledger.adapter_data,
        PF1EFactData,
    ):
        raise ValueError("ledger was not built by the PF1E adapter")
    return ledger.adapter_data


class PF1EFactLedgerAdapter:
    """Adapter preserving the original PF1E parser and validator contract."""

    adapter_id = PF1E_ADAPTER_ID
    adapter_version = PF1E_ADAPTER_VERSION
    protocol_version = FACT_LEDGER_ADAPTER_PROTOCOL_VERSION
    keys = (PF1E_ADAPTER_KEY,)
    supports_evidence_only_validation = True

    def build(
        self,
        goal: str,
        evidence: tuple[EvidenceDocument, ...],
    ) -> GenericFactLedger:
        data = _build_pf1e_data(
            goal,
            (
                (
                    item.label,
                    {
                        "title": item.title,
                        "content": item.content,
                        "metadata": dict(item.metadata),
                    },
                )
                for item in evidence
            ),
        )
        return GenericFactLedger(
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            goal=goal,
            registered_evidence_refs=tuple(item.label for item in evidence),
            records=_records(data),
            adapter_data=data,
        )

    def validate(
        self,
        content: str,
        ledger: GenericFactLedger,
    ) -> tuple[ValidationIssue, ...]:
        return _validate_pf1e_answer(content, _adapter_data(ledger))

    def public(self, ledger: GenericFactLedger) -> dict[str, Any]:
        return _adapter_data(ledger).public()


PF1E_FACT_LEDGER_ADAPTER = PF1EFactLedgerAdapter()


def build_pf1e_fact_ledger(
    goal: str,
    labeled_documents: Iterable[tuple[str, dict[str, Any]]],
) -> GenericFactLedger:
    """Build known-PF1E offline fixtures without application registry lookup."""

    documents = tuple(labeled_documents)
    if any(
        str(document.get("rulesetId", PF1E_ADAPTER_KEY.library_id))
        != PF1E_ADAPTER_KEY.library_id
        for _label, document in documents
    ):
        raise ValueError("PF1E offline helper received evidence from another library")
    evidence = tuple(
        EvidenceDocument(
            label=str(label),
            title=str(document.get("title", "")),
            content=str(document.get("content", "")),
            library_id=PF1E_ADAPTER_KEY.library_id,
            document_id=str(document.get("id", label)),
            metadata=(
                document.get("metadata", {})
                if isinstance(document.get("metadata", {}), dict)
                else {}
            ),
        )
        for label, document in documents
        if str(label).strip() and _document_content(document)
    )
    return PF1E_FACT_LEDGER_ADAPTER.build(goal, evidence)


def validate_pf1e_fact_answer(
    content: str,
    ledger: GenericFactLedger,
) -> tuple[ValidationIssue, ...]:
    """Compatibility helper used by PF1E-specific offline audits."""

    return PF1E_FACT_LEDGER_ADAPTER.validate(content, ledger)
