"""PF1E evidence parsing and deterministic validation adapter.

The ledger only parses facts present in registered rule documents. It is
deliberately narrow: class spell tables, prestige requirements, class bonus
feat scopes, feat rows, and cited equipment numbers. Unknown formats stay
unresolved instead of being completed from model knowledge.
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


PF1E_FACT_SCHEMA_VERSION = 1


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


FactValidationIssue = ValidationIssue


@dataclass
class PF1EFactData:
    goal: str
    evidence: dict[str, str]
    class_levels: dict[tuple[str, int], ClassLevelFact] = field(default_factory=dict)
    bonus_feat_scopes: list[BonusFeatScope] = field(default_factory=list)
    prestige_requirements: list[PrestigeRequirements] = field(default_factory=list)
    feats: dict[str, FeatFact] = field(default_factory=dict)
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
        }

    @property
    def record_count(self) -> int:
        return (
            len(self.class_levels)
            + len(self.bonus_feat_scopes)
            + len(self.prestige_requirements)
            + len(self.feats)
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


def _build_pf1e_data(
    goal: str,
    labeled_documents: Iterable[tuple[str, dict[str, Any]]],
) -> PF1EFactData:
    documents = tuple(labeled_documents)
    evidence = {
        label: str(document.get("content", ""))
        for label, document in documents
        if label and str(document.get("content", "")).strip()
    }
    ledger = PF1EFactData(goal=goal, evidence=evidence)
    for label, document in documents:
        title = str(document.get("title", ""))
        content = str(document.get("content", ""))
        if "法师" in title and "表：法师" in content:
            _parse_wizard_table(ledger, label, content)
            _parse_wizard_bonus_scope(ledger, label, content)
        if "进阶要求" in content:
            _parse_prestige_requirements(ledger, label, title, content)
        if "专长" in title or "专长列表" in content:
            _parse_feat_rows(ledger, label, content)
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
    issues.extend(_validate_spell_progression(plain, ledger))
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
PF1E_ADAPTER_VERSION = 1
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
                value={
                    "name": fact.name,
                    "prerequisites": fact.prerequisites,
                    "category": fact.category,
                    "source": fact.source_label,
                },
                value_type="option",
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
        if str(label).strip() and str(document.get("content", "")).strip()
    )
    return PF1E_FACT_LEDGER_ADAPTER.build(goal, evidence)


def validate_pf1e_fact_answer(
    content: str,
    ledger: GenericFactLedger,
) -> tuple[ValidationIssue, ...]:
    """Compatibility helper used by PF1E-specific offline audits."""

    return PF1E_FACT_LEDGER_ADAPTER.validate(content, ledger)
