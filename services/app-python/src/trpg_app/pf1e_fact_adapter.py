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
    DraftClaimContract,
    DraftPathSpec,
    DraftSelectionOption,
    DraftSelectionGroup,
    EvidenceDocument,
    FactDerivation,
    FactLedger as GenericFactLedger,
    FactRecord,
    FactStatus,
    RepairPathMapping,
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
class PrestigeSpellAdvancement:
    class_name: str
    starts_at_level: int
    progression_text: str
    source_label: str

    def public(self) -> dict[str, Any]:
        return {
            "class": self.class_name,
            "starts_at_level": self.starts_at_level,
            "progression": self.progression_text,
            "source": self.source_label,
        }


@dataclass(frozen=True)
class FeatFact:
    name: str
    prerequisites: str
    effect: str
    category: str
    source_label: str

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "prerequisites": self.prerequisites,
            "effect": self.effect,
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
    prestige_spell_advancements: list[PrestigeSpellAdvancement] = field(
        default_factory=list
    )
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
            "prestige_spell_advancements": [
                item.public() for item in self.prestige_spell_advancements
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
            "draft_path_templates": {
                "class_allocation": "answer.build.levels",
                "prestige_requirements": "answer.classes[<class>].requirements",
                "level_feats": "answer.levels[<level>].feats",
                "level_spell_progression": "answer.levels[<level>].spells",
                "level_spell_slots": "answer.levels[<level>].spell_slots[<spell_level>]",
                "feat": "answer.feats[<name>]",
                "named_spell": "answer.spells[<name>]",
                "spell_level": "answer.spells[<name>].level",
                "spell_school": "answer.spells[<name>].school",
                "spell_duration": "answer.spells[<name>].duration",
                "spell_saving_throw": "answer.spells[<name>].saving_throw",
                "equipment_stats": "answer.equipment.stats",
            },
        }

    @property
    def record_count(self) -> int:
        return (
            len(self.class_levels)
            + len(self.bonus_feat_scopes)
            + len(self.prestige_requirements)
            + len(self.prestige_spell_advancements)
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
_WIZARD_LEVEL_CLAUSE = re.compile(
    r"(?<!\d)(\d+)\s*级(?:法师)?(?:时)?([^。；;\n]{0,220})",
    re.MULTILINE,
)
_SPELL_LEVEL_FIRST_SLOT = re.compile(
    r"(\d+)\s*环(?:基础)?(?:每日)?(?:法术位)?\s*(?:为|有|[:：])?\s*"
    r"(\d+)\s*(?:个|位)"
)
_SPELL_COUNT_FIRST_SLOT = re.compile(
    r"(\d+)\s*(?:个|位)\s*(\d+)\s*环(?:法术位)?"
)
_SPELL_SLOT_VECTOR = re.compile(
    r"(?:每日)?法术位(?:为|提升|[:：])?\s*"
    r"((?:\d+|[-—])(?:\s*/\s*(?:\d+|[-—])){1,9})\s*"
    r"[（(]((?:\d+\s*/\s*)*\d+)\s*环[）)]"
)
_VALIDATION_CLAIM_PATH = re.compile(
    r"^\[\[TRPGCLAIMPATH:(answer(?:\.[A-Za-z_][A-Za-z0-9_]*|\[[^\]\n]{1,80}\])+)]\]$"
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
_MAX_SELECTION_OPTIONS_PER_GROUP = 40


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
            _parse_prestige_spell_advancement(ledger, label, title, content)
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
    has_standalone_fields = bool(
        re.search(r"(?m)^\s*学派\s*$", content)
        and re.search(r"(?m)^\s*环位\s*$", content)
    )
    has_inline_fields = bool(
        re.search(r"学派\s*.+?\s+环位\s*.+?\s+施法时间", content)
    )
    if not has_standalone_fields and not has_inline_fields:
        return
    raw_name = re.split(r"[（(\n]", title.strip(), maxsplit=1)[0].strip()
    name_match = re.search(r"[\u4e00-\u9fff]+", raw_name)
    name = name_match.group(0) if name_match else raw_name
    if not name:
        return
    school_text = _standalone_field(content, "学派", "环位")
    if not school_text:
        match = re.search(r"学派\s*(.+?)\s+环位\s*", content)
        school_text = match.group(1).strip() if match else ""
    school = next((item for item in _SCHOOL_NAMES if item in school_text), "")
    level_text = _standalone_field(
        content,
        "环位",
        "施法时间|成分|距离|目标|区域|效果|持续时间|豁免|法术抗力",
    )
    if not level_text:
        match = re.search(r"环位\s*(.+?)\s+施法时间\s*", content)
        level_text = match.group(1).strip() if match else ""
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
    if not duration:
        match = re.search(r"持续时间\s*(.+?)\s+豁免\s*", content)
        duration = match.group(1).strip() if match else ""
    saving_throw = _standalone_field(content, "豁免", "法术抗力")
    if not saving_throw:
        match = re.search(r"豁免\s*(.+?)\s+法术抗力\s*", content)
        saving_throw = match.group(1).strip() if match else ""
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


def _parse_prestige_spell_advancement(
    ledger: PF1EFactData,
    label: str,
    title: str,
    content: str,
) -> None:
    match = re.search(
        r"每日法术：从(\d+)级开始(?=[^。]*奥术施法职业)[^。]*",
        content,
    )
    if match is None:
        return
    name = next((item for item in _CLASS_NAMES if item in title), title.strip())
    ledger.prestige_spell_advancements.append(
        PrestigeSpellAdvancement(
            class_name=name,
            starts_at_level=int(match.group(1)),
            progression_text="现有奥术施法职业等级+1",
            source_label=label,
        )
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
                FeatFact(name, cells[1], cells[2], cells[3], label),
            )
    _parse_narrative_feat_entries(ledger, label, content)


_NARRATIVE_FEAT_HEADING = re.compile(
    r"^\s*(?P<name>[\u4e00-\u9fff·]{2,20})"
    r"(?=\s*(?:[（(〔]|[A-Z][A-Za-z]))"
)
_NON_FEAT_HEADINGS = {
    "先决条件",
    "专长效果",
    "通常情况",
    "通常状况",
    "特殊说明",
    "特别说明",
    "表现描述符",
    "表现学派",
    "表现魔法来源",
}


def _parse_narrative_feat_entries(
    ledger: PF1EFactData,
    label: str,
    content: str,
) -> None:
    """Parse bounded prose feat entries that are not published as table rows."""

    lines = content.splitlines()
    for marker_index, line in enumerate(lines):
        marker = line.strip()
        if not marker.startswith(("先决条件：", "先决条件:", "专长效果：", "专长效果:")):
            continue
        heading_index: int | None = None
        heading_match: re.Match[str] | None = None
        for candidate_index in range(marker_index - 1, max(-1, marker_index - 11), -1):
            candidate = lines[candidate_index].strip()
            match = _NARRATIVE_FEAT_HEADING.match(candidate)
            if match is None or match.group("name") in _NON_FEAT_HEADINGS:
                continue
            heading_index = candidate_index
            heading_match = match
            break
        if heading_index is None or heading_match is None:
            continue
        name = heading_match.group("name")
        heading_blob = " ".join(lines[heading_index:marker_index])
        category_match = re.search(r"〔([^〕]{1,40})〕", heading_blob)
        category = category_match.group(1).strip() if category_match else ""
        prerequisites = ""
        if marker.startswith(("先决条件：", "先决条件:")):
            prerequisites = re.split(r"[：:]", marker, maxsplit=1)[1].strip()
        ledger.feats.setdefault(
            name,
            FeatFact(name, prerequisites, "", category, label),
        )


def _validate_citations(content: str, ledger: PF1EFactData) -> list[FactValidationIssue]:
    return [
        FactValidationIssue(
            "unknown_citation",
            f"答案引用了未注册来源 {label}",
            path=f"answer.citations[{label}]",
            actual=label,
            repairable=False,
        )
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
                        path="answer.build.levels",
                        expected=target,
                        actual=total,
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
                    and not re.search(
                        r"(?:不包含|并不包含|没有|无需)[^。；\n]{0,24}(?:BAB|基本攻击)|"
                        r"(?:BAB|基本攻击)[^。；\n]{0,24}(?:不是|不在|不属于)",
                        line,
                        re.I,
                    )
                ):
                    issues.append(
                        FactValidationIssue(
                            "invented_prestige_requirement",
                            f"{fact.class_name} 的已读进阶要求不包含 BAB/基本攻击条件",
                            path=f"answer.classes[{fact.class_name}].requirements",
                            expected=list(fact.requirements),
                            actual="BAB/基本攻击",
                            evidence_refs=(fact.source_label,),
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
                            path=f"answer.levels[{level}].feats",
                            expected=list(scope.allowed_categories),
                            actual="战斗专长",
                            evidence_refs=(scope.source_label,),
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
                                path=f"answer.levels[{level}].feats",
                                expected=list(scope.allowed_categories),
                                actual=feat_name,
                                evidence_refs=tuple(
                                    dict.fromkeys(
                                        (scope.source_label, feat.source_label)
                                    )
                                ),
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
        path_match = _VALIDATION_CLAIM_PATH.fullmatch(line)
        if path_match:
            level_match = re.match(r"answer\.levels\[(\d+)]", path_match.group(1))
            current_level = int(level_match.group(1)) if level_match else None
            continue
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
    if not _requires_level_feat_choices(ledger.goal):
        return []
    slots = _relevant_feat_slots(ledger)
    if not slots:
        return []
    sections = _level_sections(content)
    by_level: dict[int, list[FeatSlotFact]] = {}
    for fact in slots:
        by_level.setdefault(fact.level, []).append(fact)
    issues: list[FactValidationIssue] = []
    for level, facts in sorted(by_level.items()):
        section = sections.get(level, "")
        if "不能安全生成具体选择" in section:
            continue
        expected = sum(item.count for item in facts)
        actual = _selection_count(section, ledger)
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
    values: list[int] = []
    for clause in re.split(r"[。；;\n]", content):
        if "先决条件" in clause or "需要" in clause:
            continue
        values.extend(
            int(value)
            for value in re.findall(
                rf"{re.escape(attribute)}(?:属性)?\s*(?:为|达到|=|：|:)?\s*(\d+)",
                clause,
            )
        )
    return max(values) if values else None


def _conditioned_attribute(content: str, attribute: str, required: int) -> bool:
    for clause in re.split(r"[。；;\n]", content):
        if not re.search(r"如果|若|条件|满足", clause):
            continue
        values = [
            int(value)
            for value in re.findall(
                rf"{re.escape(attribute)}(?:属性)?\s*(?:为|达到|>=|≥|=|：|:)?\s*(\d+)",
                clause,
            )
        ]
        if values and max(values) >= required:
            return True
    return False


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
                if (actual is None or actual < required) and not _conditioned_attribute(
                    content,
                    attribute,
                    required,
                ):
                    actual_text = actual if actual is not None else "未知"
                    issues.append(
                        FactValidationIssue(
                            "feat_attribute_prerequisite",
                            (
                                f"{name} 需要 {attribute}{required}，"
                                f"候选状态为 {actual_text}"
                            ),
                            path=f"answer.levels[{level}].feats[{name}].attributes[{attribute}]",
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
                    path=f"answer.levels[{level}].spells",
                    expected=fact.max_spell_level,
                    actual=claimed,
                    evidence_refs=(fact.source_label,),
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
                    path=f"answer.levels[{level}].spell_slots[{spell_level}]",
                    expected=expected_count,
                    actual=claimed_count,
                    evidence_refs=(fact.source_label,),
                )
            )
    for level_text, body in _WIZARD_LEVEL_CLAUSE.findall(content):
        level = int(level_text)
        fact = ledger.class_levels.get(("法师", level))
        if fact is None:
            continue
        compact_claims = [
            (int(spell_level), int(count))
            for spell_level, count in _SPELL_LEVEL_FIRST_SLOT.findall(body)
        ]
        compact_claims.extend(
            (int(spell_level), int(count))
            for count, spell_level in _SPELL_COUNT_FIRST_SLOT.findall(body)
        )
        vector = _SPELL_SLOT_VECTOR.search(body)
        if vector:
            counts = [
                None if value in {"-", "—"} else int(value)
                for value in re.split(r"\s*/\s*", vector.group(1))
            ]
            spell_levels = [
                int(value) for value in re.split(r"\s*/\s*", vector.group(2))
            ]
            if len(counts) == len(spell_levels):
                compact_claims.extend(
                    (spell_level, count)
                    for spell_level, count in zip(spell_levels, counts)
                    if count is not None
                )
        for spell_level, claimed_count in compact_claims:
            if spell_level >= len(fact.spell_slots):
                continue
            expected_count = fact.spell_slots[spell_level]
            if expected_count is not None and claimed_count != expected_count:
                issues.append(
                    FactValidationIssue(
                        "spell_slot_count",
                        (
                            f"法师{level}级的{spell_level}环基础每日法术位应为 "
                            f"{expected_count}，候选答案写为 {claimed_count}"
                        ),
                        path=f"answer.levels[{level}].spell_slots[{spell_level}]",
                        expected=expected_count,
                        actual=claimed_count,
                        evidence_refs=(fact.source_label,),
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
                        path="answer.equipment.stats",
                        actual=stat,
                        evidence_refs=tuple(dict.fromkeys(labels)),
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
    claim_path = ""
    for line in content.splitlines():
        marker = _VALIDATION_CLAIM_PATH.fullmatch(line.strip())
        if marker:
            claim_path = marker.group(1)
            continue
        for match in re.finditer(
            r"(?:建议选择|可选择|选择[：:]|替代专长[：:为])\s*"
            r"(?:\*\*|[‘’“”])?([\u4e00-\u9fffA-Za-z]+)",
            line,
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
                        path=claim_path or f"answer.feats[{name}]",
                        actual=name,
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
PF1E_ADAPTER_VERSION = 16
PF1E_ADAPTER_KEY = AdapterKey(
    library_id="pathfinder-1e",
    system="Pathfinder",
    edition="1E",
)

PF1E_DRAFT_PATH_SPECS = (
    DraftPathSpec("answer.build.levels", ("answer.build.class_levels",)),
    DraftPathSpec("answer.citations[{label}]"),
    DraftPathSpec("answer.classes[{class_name}].requirements"),
    DraftPathSpec(
        "answer.levels[{level}].feats",
        ("answer.progression[{level}].feats", "answer.level[{level}].feats"),
    ),
    DraftPathSpec("answer.levels[{level}].feats[{feat_name}].prerequisites"),
    DraftPathSpec("answer.levels[{level}].feats[{feat_name}].bab"),
    DraftPathSpec("answer.levels[{level}].feats[{feat_name}].attributes[{attribute}]"),
    DraftPathSpec("answer.levels[{level}].spells"),
    DraftPathSpec("answer.levels[{level}].spell_slots[{spell_level}]"),
    DraftPathSpec("answer.spells[{spell_name}].level"),
    DraftPathSpec("answer.spells[{spell_name}].school"),
    DraftPathSpec("answer.spells[{spell_name}].duration"),
    DraftPathSpec("answer.spells[{spell_name}].saving_throw"),
    DraftPathSpec("answer.spells[{spell_name}]"),
    DraftPathSpec("answer.equipment.stats"),
    DraftPathSpec("answer.feats[{feat_name}]"),
    DraftPathSpec("answer.summary[{key}]"),
    DraftPathSpec("answer.other[{key}]"),
    DraftPathSpec("answer.sections[{section}].claims[{claim}]"),
)

PF1E_REPAIR_PATH_MAPPINGS = (
    RepairPathMapping(
        "answer.levels[{level}].spell_slots[{spell_level}]",
        "answer.levels[{level}].spells",
        ("spell_slot_count",),
    ),
)


def _required_draft_paths(data: PF1EFactData) -> tuple[str, ...]:
    """Publish exact claim coverage derived only from the server-owned goal."""

    goal = data.goal
    paths: list[str] = []
    if re.search(r"\d+\s*(?:到|至|[-—~])\s*\d+\s*级|升级|成长|兼职|进阶", goal):
        paths.append("answer.build.levels")
    if re.search(r"必须先|先核对|只有|否则|如果|未达到|满足.*才", goal):
        paths.append("answer.summary[conditional_branch]")
    if _goal_fact_summary(data)[0]:
        paths.append("answer.summary[goal_facts]")
    if re.search(r"专长|feat", goal, re.IGNORECASE):
        paths.append("answer.summary[feat_eligibility]")
        if _requires_level_feat_choices(goal):
            paths.extend(
                f"answer.levels[{level}].feats"
                for level in sorted({item.level for item in _relevant_feat_slots(data)})
            )
    if re.search(r"法术|施法|奥术|神术|\d+\s*环|spell", goal, re.IGNORECASE):
        paths.append("answer.summary[spell_progression]")
        level_range = _goal_level_range(goal)
        if "法师" in goal and level_range is not None:
            paths.extend(
                f"answer.levels[{level}].spells"
                for level in range(level_range[0], level_range[1] + 1)
                if ("法师", level) in data.class_levels
            )
    if re.search(r"装备|武器|盔甲|护甲|盾牌|价格|伤害", goal):
        paths.append("answer.equipment.stats")
    if re.search(r"指定(?:的)?法术环级|指定环级", goal):
        paths.append("answer.summary[missing_input]")
    return tuple(dict.fromkeys(paths))


def _requires_level_feat_choices(goal: str) -> bool:
    """Distinguish a full feat timeline from eligibility/comparison requests."""

    return bool(
        re.search(
            r"逐级[^。；]{0,20}专长|专长[^。；]{0,20}逐级|"
            r"每级[^。；]{0,20}专长|专长升级(?:路线|方案)|专长时间线",
            goal,
        )
    )


def _claim_evidence_refs(data: PF1EFactData, path: str) -> tuple[str, ...]:
    """Select bounded provenance from parsed facts for one required claim."""

    refs: list[str] = []
    if path == "answer.build.levels":
        refs.extend(item.source_label for item in data.class_levels.values())
        refs.extend(item.source_label for item in data.base_attack.values())
        refs.extend(item.source_label for item in data.prestige_requirements)
    elif path == "answer.summary[conditional_branch]":
        refs.extend(item.source_label for item in data.prestige_requirements)
        refs.extend(item.source_label for item in data.class_levels.values())
        refs.extend(item.source_label for item in data.base_attack.values())
    elif path == "answer.summary[feat_eligibility]":
        refs.extend(item.source_label for item in _relevant_feat_slots(data))
        refs.extend(item.source_label for item in data.feats.values())
        refs.extend(item.source_label for item in data.bonus_feat_scopes)
        refs.extend(item.source_label for item in data.base_attack.values())
    elif path == "answer.summary[spell_progression]":
        refs.extend(item.source_label for item in data.class_levels.values())
        refs.extend(item.source_label for item in data.spells.values())
        refs.extend(item.source_label for item in data.prestige_spell_advancements)
    elif path == "answer.summary[goal_facts]":
        refs.extend(_goal_fact_summary(data)[1])
    elif match := re.fullmatch(r"answer\.levels\[(\d+)]\.spells", path):
        level = int(match.group(1))
        refs.extend(
            item.source_label
            for item in data.class_levels.values()
            if item.level == level
        )
    elif path == "answer.equipment.stats":
        refs.extend(
            label for label, content in data.evidence.items() if _NUMERIC_STAT.search(content)
        )
    if not refs:
        refs.extend(ref for record in _records(data) for ref in record.evidence_refs)
    return tuple(dict.fromkeys(refs))


def _wizard_progression_text(fact: ClassLevelFact) -> str:
    """Render class-table progression without delegating numeric facts to a model."""

    if fact.max_spell_level is None:
        raise ValueError("wizard progression requires an available spell level")
    spell_levels = tuple(range(fact.max_spell_level + 1))
    slot_values = tuple(fact.spell_slots[level] for level in spell_levels)
    if any(value is None for value in slot_values):
        raise ValueError("wizard progression has an incomplete slot vector")
    slots = "/".join(str(value) for value in slot_values)
    levels = "/".join(str(level) for level in spell_levels)
    return (
        f"{fact.level}级法师最高可施放{fact.max_spell_level}环法术，"
        f"每日法术位为{slots}（{levels}环）。"
    )


def _spell_progression_summary_text(data: PF1EFactData) -> str:
    parts = ["法师逐级最高法术环级与每日法术位由已读职业表确定，见逐级规则结论。"]
    parts.extend(
        (
            f"{fact.class_name}1级不增加现有奥术施法职业等级；从{fact.starts_at_level}级开始，"
            f"每次升级按{fact.progression_text}推进每日法术。"
        )
        for fact in data.prestige_spell_advancements
    )
    return "".join(parts)


def _feat_eligibility_text(data: PF1EFactData) -> str:
    slots_by_level: dict[int, int] = {}
    for slot in _relevant_feat_slots(data):
        slots_by_level[slot.level] = slots_by_level.get(slot.level, 0) + slot.count
    slot_text = "、".join(
        f"{level}级{count}个" for level, count in sorted(slots_by_level.items())
    )
    names = _goal_relevant_feat_names(data.goal, data.feats)
    feat_text = "、".join(
        f"{name}（先决条件：{data.feats[name].prerequisites or '无'}）"
        for name in names
    )
    parts = []
    declared = [
        f"{attribute}为{value}"
        for attribute in ("力量", "敏捷", "体质", "智力", "感知", "魅力")
        if (value := _declared_attribute(data.goal, attribute)) is not None
    ]
    if declared:
        parts.append(f"题目声明的角色属性为：{'、'.join(declared)}。")
    if slot_text:
        parts.append(f"已读规则给出的专长选择槽为：{slot_text}。")
    if feat_text:
        parts.append(f"目标专长的已读先决条件为：{feat_text}。")
    return "".join(parts) or "专长选择必须满足已读槽位数量和先决条件。"


def _goal_fact_summary(data: PF1EFactData) -> tuple[str, tuple[str, ...]]:
    """Render goal-named facts only when their registered evidence was parsed.

    This is intentionally a small PF1E release corpus bridge.  It prevents a
    model from dropping the exact facts that caused retrieval for the current
    goal, while refusing to complete facts that are absent from this turn's
    registered evidence.
    """

    goal = data.goal
    parts: list[str] = []
    refs: list[str] = []

    if "奥法骑士" in goal:
        for fact in data.prestige_requirements:
            if fact.class_name != "奥法骑士":
                continue
            parts.append(
                f"奥法骑士的已读进阶要求为：{'；'.join(fact.requirements)}。"
            )
            if not re.search(r"BAB|基本攻击", "\n".join(fact.requirements), re.I):
                parts.append("已读奥法骑士进阶要求不包含BAB或基础攻击加值门槛。")
            refs.append(fact.source_label)

    if "人类" in goal:
        human_slots = [
            item
            for item in data.feat_slots
            if item.source_type == "ancestry_bonus"
            and item.source_name == "人类"
            and item.level == 1
        ]
        if human_slots:
            parts.append("人类角色在1级获得一个额外专长。")
            refs.extend(item.source_label for item in human_slots)

    fighter_sources = [
        (label, content)
        for label, content in data.evidence.items()
        if "武器和防具擅长：战士擅长使用所有的简易武器和军用武器" in content
        and "盾牌（包括塔盾）" in content
    ]
    if "战士" in goal and re.search(r"擅长|装备|武器|盔甲|护甲|盾牌", goal):
        if fighter_sources:
            parts.append(
                "战士擅长所有简易武器、军用武器、所有类型盔甲和盾牌（包括塔盾）。"
            )
            refs.extend(label for label, _content in fighter_sources)

    fighter_bonus = [
        item
        for item in data.feat_slots
        if item.source_type == "class_bonus" and item.source_name == "战士"
    ]
    if "战士" in goal and "专长" in goal and fighter_bonus:
        parts.append("战士在1级以及之后的每个偶数战士等级获得一项战士奖励专长。")
        refs.extend(item.source_label for item in fighter_bonus)

    if "法师护甲" in goal:
        mage_armor = data.spells.get("法师护甲")
        if mage_armor is not None:
            evidence = data.evidence.get(mage_armor.source_label, "")
            if "AC提供+4护甲加值" in evidence and "没有奥术失败率" in evidence:
                parts.append(
                    "法师护甲为受术者提供+4护甲加值，没有防具检定减值、没有奥术失败率，也不会降低速度。"
                )
                refs.append(mage_armor.source_label)

    if "油腻术" in goal:
        grease = data.spells.get("油腻术")
        if grease is not None:
            saving = grease.saving_throw or "见法术正文的反射豁免规则"
            parts.append(
                f"油腻术属于{grease.school}，持续时间为{grease.duration}，豁免为{saving}；"
                "区域内生物需通过反射豁免，否则倒地。"
            )
            refs.append(grease.source_label)
            spell_focus = data.feats.get("法术专攻")
            if spell_focus is not None and spell_focus.effect:
                school = grease.school.removesuffix("系")
                parts.append(
                    f"若围绕油腻术配置专长，可选择法术专攻（{school}）；"
                    f"其已读效果为{spell_focus.effect}。"
                )
                refs.append(spell_focus.source_label)

    return "".join(parts), tuple(dict.fromkeys(refs))


def _claim_value_description(path: str) -> str:
    if path == "answer.build.levels":
        return "只写职业等级分配和每个分支的适用条件；不得写装备、专长名称或法术位数值。"
    if path == "answer.summary[conditional_branch]":
        return "只写题目要求的 if/else 条件判断及采用哪个分支；不得重复逐级法术位、专长表或装备数值。"
    if path == "answer.equipment.stats":
        return "只比较题目要求的装备、擅长、价格或防御数据并给出购买结论；不得写职业等级或专长时间线。"
    return "只写该 canonical topic 对应的结论，不得重复其他 topic 的事实。"


def _claim_semantic_terms(path: str) -> tuple[tuple[tuple[str, ...], ...], tuple[str, ...]]:
    if path == "answer.build.levels":
        return (("级",), ("法师", "战士", "奥法骑士")), (
            "AC", "护甲", "盾牌", "每日法术位", "选择专长",
        )
    if path == "answer.summary[conditional_branch]":
        return (("如果", "否则", "满足", "不满足", "条件", "分支"),), ()
    if path == "answer.equipment.stats":
        return (("装备", "武器", "盔甲", "护甲", "盾牌", "价格", "gp", "AC"),), (
            "每日法术位", "最高可施放", "选择专长", "职业构成",
        )
    return (), ()


def _semantic_fallback_text(data: PF1EFactData, path: str) -> str:
    """Return a bounded server sentence when a model supplies no legal topic clause."""

    if path == "answer.build.levels":
        classes = tuple(
            name for name in ("法师", "战士", "奥法骑士") if name in data.goal
        )
        class_text = "、".join(classes) or "相关职业"
        level_range = _goal_level_range(data.goal)
        range_text = (
            f"{level_range[0]}到{level_range[1]}级"
            if level_range is not None
            else "题目所述等级"
        )
        return f"无法从合法的职业子句安全确定{range_text}{class_text}等级分配，不臆测具体路线。"
    if path == "answer.summary[conditional_branch]":
        return "按题目条件执行：条件满足时采用前一分支；条件不满足时采用否则分支。"
    if path == "answer.equipment.stats":
        return "已读装备证据不足以安全完成武器、盔甲、护甲或盾牌的具体比较，不臆测购买结论。"
    return ""


def _feat_matches_slot(fact: FeatFact, slot: FeatSlotFact) -> bool:
    if not slot.allowed_categories:
        return True
    for category in slot.allowed_categories:
        if "战斗" in category and "战斗" in fact.category:
            return True
        if "超魔" in category and "超魔" in fact.category:
            return True
        if "物品制造" in category and (
            "造物" in fact.category or "物品制造" in fact.category
        ):
            return True
        if "法术掌握" in category and fact.name in {"法术掌握", "法术熟稔"}:
            return True
    return False


def _feat_slot_label(slot: FeatSlotFact) -> str:
    if slot.source_type == "general":
        return "普通专长"
    if slot.source_type == "class_bonus":
        return f"{slot.source_name}奖励专长"
    if slot.source_type == "ancestry_bonus":
        return f"{slot.source_name}奖励专长"
    return f"{slot.source_name}专长"


def _ranked_feat_option_names(
    data: PF1EFactData,
    slot: FeatSlotFact,
) -> tuple[str, ...]:
    relevant = set(_goal_relevant_feat_names(data.goal, data.feats))
    spell_goal = bool(re.search(r"法术|施法|控制|奥术", data.goal))

    def rank(fact: FeatFact) -> tuple[int, int, int, str]:
        semantic_match = spell_goal and bool(
            re.search(r"法术|施法|奥术|超魔", fact.name + fact.category + fact.prerequisites)
        )
        return (
            0 if fact.name in relevant else 1,
            0 if semantic_match else 1,
            0 if not fact.prerequisites else 1,
            fact.name,
        )

    candidates = sorted(
        (fact for fact in data.feats.values() if _feat_matches_slot(fact, slot)),
        key=rank,
    )
    return tuple(
        fact.name for fact in candidates[:_MAX_SELECTION_OPTIONS_PER_GROUP]
    )


def _draft_claim_contracts(data: PF1EFactData) -> tuple[DraftClaimContract, ...]:
    contracts: list[DraftClaimContract] = []
    slots_by_level: dict[int, list[FeatSlotFact]] = {}
    for slot in _relevant_feat_slots(data):
        slots_by_level.setdefault(slot.level, []).append(slot)
    for path in _required_draft_paths(data):
        if path == "answer.summary[goal_facts]":
            goal_text, goal_refs = _goal_fact_summary(data)
            contracts.append(
                DraftClaimContract(
                    path=path,
                    evidence_refs=goal_refs,
                    server_text=goal_text,
                )
            )
            continue
        if path == "answer.build.levels" and re.search(
            r"指定(?:的)?法术环级|指定环级", data.goal
        ) and data.class_levels:
            contracts.append(
                DraftClaimContract(
                    path=path,
                    evidence_refs=_claim_evidence_refs(data, path),
                    server_text=(
                        "题目未给出要保持的具体法术环级，因此暂不生成具体法师/战士等级分配；"
                        "先明确目标环级后，再按法师职业表核对可用的兼职等级。"
                    ),
                )
            )
            continue
        if path == "answer.summary[feat_eligibility]":
            contracts.append(
                DraftClaimContract(
                    path=path,
                    evidence_refs=_claim_evidence_refs(data, path),
                    server_text=_feat_eligibility_text(data),
                )
            )
            continue
        if path == "answer.summary[spell_progression]":
            contracts.append(
                DraftClaimContract(
                    path=path,
                    evidence_refs=_claim_evidence_refs(data, path),
                    server_text=_spell_progression_summary_text(data),
                )
            )
            continue
        if path == "answer.summary[missing_input]":
            contracts.append(
                DraftClaimContract(
                    path=path,
                    evidence_refs=_claim_evidence_refs(data, path),
                    server_text="题目没有给出要保持的具体法术环级；必须先明确目标环级，再选择对应兼职分支。",
                )
            )
            continue
        spell_match = re.fullmatch(r"answer\.levels\[(\d+)]\.spells", path)
        if spell_match:
            level = int(spell_match.group(1))
            fact = data.class_levels.get(("法师", level))
            if fact is not None and fact.max_spell_level is not None:
                contracts.append(
                    DraftClaimContract(
                        path=path,
                        evidence_refs=(fact.source_label,),
                        server_text=_wizard_progression_text(fact),
                    )
                )
                continue
        match = re.fullmatch(r"answer\.levels\[(\d+)]\.feats", path)
        if match:
            level = int(match.group(1))
            slots = slots_by_level.get(level, [])
            count = sum(item.count for item in slots)
            selection_groups = tuple(
                DraftSelectionGroup(
                    label=_feat_slot_label(slot),
                    count=slot.count,
                    option_values=_ranked_feat_option_names(data, slot),
                )
                for slot in slots
                if len(_ranked_feat_option_names(data, slot)) >= slot.count
            )
            groups_cover_slots = len(selection_groups) == len(slots)
            option_names = tuple(
                dict.fromkeys(
                    value
                    for group in selection_groups
                    for value in group.option_values
                )
            )
            options = tuple(
                DraftSelectionOption(name, (data.feats[name].source_label,))
                for name in option_names
            )
            if count and groups_cover_slots and len(options) >= count:
                contracts.append(
                    DraftClaimContract(
                        path=path,
                        evidence_refs=tuple(
                            dict.fromkeys(item.source_label for item in slots)
                        ),
                        selection_count=count,
                        selection_options=options,
                        selection_groups=selection_groups,
                        allow_selection_fallback=True,
                        text_template=f"{level}级选择专长：{{values}}。",
                        value_description="按该等级的全部专长槽选择不同的已读专长；每个选择必须满足先决条件和奖励专长范围。",
                    )
                )
                continue
            if count:
                contracts.append(
                    DraftClaimContract(
                        path=path,
                        evidence_refs=tuple(
                            dict.fromkeys(item.source_label for item in slots)
                        ),
                        server_text=(
                            f"{level}级规则要求选择{count}个专长，但已读证据只提供"
                            f"{len(options)}个且未覆盖全部槽位的可验证候选，不能安全生成具体选择。"
                        ),
                    )
                )
                continue
        required_term_groups, forbidden_terms = _claim_semantic_terms(path)
        if path in {"answer.build.levels", "answer.summary[conditional_branch]"}:
            prestige_requirements = "\n".join(
                requirement
                for fact in data.prestige_requirements
                for requirement in fact.requirements
            )
            if data.prestige_requirements and not re.search(
                r"BAB|基本攻击", prestige_requirements, re.I
            ):
                forbidden_terms = (*forbidden_terms, "BAB", "基础攻击")
        contracts.append(
            DraftClaimContract(
                path=path,
                evidence_refs=_claim_evidence_refs(data, path),
                value_description=_claim_value_description(path),
                semantic_fallback_text=_semantic_fallback_text(data, path),
                required_term_groups=required_term_groups,
                forbidden_terms=forbidden_terms,
            )
        )
    return tuple(contracts)


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
    for fact in data.prestige_spell_advancements:
        records.append(
            FactRecord(
                adapter_id=PF1E_ADAPTER_ID,
                adapter_version=PF1E_ADAPTER_VERSION,
                subject=fact.class_name,
                predicate="prestige_spell_advancement",
                value=fact.public(),
                value_type="progression",
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

    def draft_path_specs(
        self,
        ledger: GenericFactLedger,
    ) -> tuple[DraftPathSpec, ...]:
        _adapter_data(ledger)
        return PF1E_DRAFT_PATH_SPECS

    def required_draft_paths(
        self,
        ledger: GenericFactLedger,
    ) -> tuple[str, ...]:
        return _required_draft_paths(_adapter_data(ledger))

    def draft_claim_contracts(
        self,
        ledger: GenericFactLedger,
    ) -> tuple[DraftClaimContract, ...]:
        return _draft_claim_contracts(_adapter_data(ledger))

    def repair_path_mappings(
        self,
        ledger: GenericFactLedger,
    ) -> tuple[RepairPathMapping, ...]:
        _adapter_data(ledger)
        return PF1E_REPAIR_PATH_MAPPINGS


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
