from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Sequence

from .chm_structure_audit import plausible_anchor_label
from .domain import RuleDocument
from .importers.chm import decode_document


_SPACE = re.compile(r"[ \t\f\v]+")
_SPLIT_STRATEGIES = {"split_headings", "split_headings_and_tables"}
_TABLE_STRATEGIES = {"keep_table_aware", "split_headings_and_tables"}
_ENTRY_STRATEGIES = {"split_catalog_entries", "split_tables_with_context"}
_EVIDENCE_CHARACTER_LIMIT = 80_000
_BLOCK_TAGS = {"p", "li", "dt", "dd", "pre", "blockquote"}
_SKIP_TAGS = {"script", "style", "noscript"}

_ENTRY_FIELD_LABELS = {
    "spell": (
        "school", "level", "casting time", "components", "range", "target",
        "effect", "area", "duration", "saving throw", "spell resistance",
        "学派", "法术等级", "施法时间", "成分", "距离", "目标", "效果", "范围",
        "持续时间", "豁免", "法术抗力",
    ),
    "feat": (
        "prerequisites", "benefit", "normal", "special", "先决条件", "好处", "正常", "特殊",
    ),
    "magic_item": (
        "aura", "caster level", "slot", "price", "weight", "requirements", "construction",
        "cost", "灵光", "施法者等级", "部位", "价格", "重量", "制作要求", "制作", "成本",
    ),
    "class_ability": (
        "level", "class skill", "class skills", "class features", "requirements", "special",
        "description", "weapon and armor proficiency", "等级", "职业技能", "职业能力", "需求",
        "特殊", "效果", "描述",
    ),
    "archetype": (
        "replaces", "replaced", "modified", "altered", "requirements", "替代", "替换", "修改",
        "改变", "需求",
    ),
}

_ENTRY_CATEGORY_MARKERS = {
    "spell": ("法术", "spell", "spells"),
    "feat": ("专长", "feat", "feats"),
    "magic_item": ("魔法物品", "magic item", "magic items"),
    "archetype": ("职业变体", "archetype", "variant"),
    "class_ability": ("职业能力", "职业特性", "class feature", "class ability", "class features"),
}


@dataclass(frozen=True)
class HtmlBlock:
    kind: str
    text: str
    level: int = 0
    rows: tuple[tuple[str, ...], ...] = ()


class StructuredHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[HtmlBlock] = []
        self._skip_depth = 0
        self._block_tag: str | None = None
        self._block_level = 0
        self._parts: list[str] = []
        self._loose_parts: list[str] = []
        self._table_depth = 0
        self._table_rows: list[tuple[str, ...]] = []
        self._row: list[str] | None = None
        self._cell_parts: list[str] | None = None
        self._anchor_name: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if lowered == "table":
            self._flush_block()
            self._flush_loose()
            self._table_depth += 1
            if self._table_depth == 1:
                self._table_rows = []
            return
        if lowered == "a" and not self._table_depth:
            attributes = {key.lower(): value or "" for key, value in attrs}
            anchor_name = attributes.get("name", "")
            if anchor_name and plausible_anchor_label(anchor_name):
                self._anchor_name = anchor_name
            return
        if self._table_depth:
            if lowered == "tr" and self._table_depth == 1:
                self._row = []
            elif lowered in {"td", "th"} and self._row is not None:
                self._cell_parts = []
            elif lowered == "br" and self._cell_parts is not None:
                self._cell_parts.append(" ")
            return
        if lowered in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._flush_block()
            self._flush_loose()
            self._block_tag = "heading"
            self._block_level = int(lowered[1])
            self._parts = []
        elif lowered in _BLOCK_TAGS:
            self._flush_block()
            self._flush_loose()
            self._block_tag = "paragraph"
            self._block_level = 0
            self._parts = []
        elif lowered == "br":
            if self._block_tag:
                self._parts.append("\n")
            else:
                self._flush_loose()

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in _SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if lowered == "a" and self._anchor_name:
            name = self._anchor_name
            self._anchor_name = None
            if self._block_tag in (None, "paragraph"):
                value = _normalize("".join([*self._parts, *self._loose_parts]))
                if _anchor_text_matches(value, name):
                    self._parts = []
                    self._loose_parts = []
                    self._block_tag = None
                    self._block_level = 0
                    self.blocks.append(HtmlBlock("heading", name, level=2))
            return
        if self._table_depth:
            if lowered in {"td", "th"} and self._cell_parts is not None:
                if self._row is not None:
                    self._row.append(_normalize("".join(self._cell_parts)))
                self._cell_parts = None
            elif lowered == "tr" and self._row is not None:
                if any(self._row):
                    self._table_rows.append(tuple(self._row))
                self._row = None
            elif lowered == "table":
                self._table_depth -= 1
                if self._table_depth == 0 and self._table_rows:
                    rows = tuple(self._table_rows)
                    self.blocks.append(
                        HtmlBlock("table", _render_table(rows), rows=rows)
                    )
                    self._table_rows = []
            return
        if self._block_tag == "heading" and lowered == f"h{self._block_level}":
            self._flush_block()
        elif self._block_tag == "paragraph" and lowered in _BLOCK_TAGS:
            self._flush_block()
        elif lowered in {"div", "section", "article", "body"}:
            self._flush_loose()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._cell_parts is not None:
            self._cell_parts.append(data)
        elif self._table_depth:
            return
        elif self._block_tag:
            self._parts.append(data)
        else:
            self._loose_parts.append(data)

    def close(self) -> None:
        super().close()
        self._flush_block()
        self._flush_loose()

    def _flush_block(self) -> None:
        if not self._block_tag:
            return
        value = _normalize("".join(self._parts))
        if value:
            self.blocks.append(
                HtmlBlock(self._block_tag, value, level=self._block_level)
            )
        self._block_tag = None
        self._block_level = 0
        self._parts = []

    def _flush_loose(self) -> None:
        value = _normalize("".join(self._loose_parts))
        if value:
            self.blocks.append(HtmlBlock("paragraph", value))
        self._loose_parts = []


@dataclass
class Section:
    heading_path: list[str]
    blocks: list[HtmlBlock] = field(default_factory=list)

    def content(self) -> str:
        return "\n\n".join(block.text for block in self.blocks if block.text).strip()


@dataclass(frozen=True)
class EntryCandidate:
    heading_path: tuple[str, ...]
    blocks: tuple[HtmlBlock, ...]
    block_start: int
    block_end: int
    entry_type: str

    @property
    def content(self) -> str:
        return "\n\n".join(block.text for block in self.blocks if block.text).strip()


def extract_blocks(path: Path) -> list[HtmlBlock]:
    text, _encoding = decode_document(path.read_bytes())
    parser = StructuredHtmlParser()
    parser.feed(text)
    parser.close()
    return parser.blocks


def partition_sections(blocks: Sequence[HtmlBlock]) -> list[Section]:
    sections: list[Section] = []
    heading_stack: list[tuple[int, str]] = []
    current = Section([])
    for block in blocks:
        if block.kind != "heading":
            current.blocks.append(block)
            continue
        if current.content():
            sections.append(current)
        while heading_stack and heading_stack[-1][0] >= block.level:
            heading_stack.pop()
        heading_stack.append((block.level, block.text))
        current = Section([label for _level, label in heading_stack], [block])
    if current.content():
        sections.append(current)
    return [
        section
        for section in sections
        if any(block.kind != "heading" and block.text for block in section.blocks)
    ]


def transform_documents(
    documents: Iterable[RuleDocument],
    extracted: Path,
    audit: dict[str, Any],
) -> tuple[list[RuleDocument], dict[str, Any]]:
    strategy_by_id = {
        str(row["id"]): str(row["strategy"])
        for row in audit.get("documents", [])
    }
    transformed: list[RuleDocument] = []
    mappings: dict[str, list[str]] = {}
    counts: dict[str, int] = {
        "splitParents": 0,
        "tableEnhancedParents": 0,
        "fallbackParents": 0,
    }
    for document in documents:
        strategy = strategy_by_id.get(document.id, "keep")
        source_file = str(document.metadata.get("sourceFile", ""))
        html_path = extracted / source_file
        if strategy not in _SPLIT_STRATEGIES | _TABLE_STRATEGIES | _ENTRY_STRATEGIES or not html_path.is_file():
            transformed.append(document)
            mappings[document.id] = [document.id]
            continue
        blocks = extract_blocks(html_path)
        if strategy in _ENTRY_STRATEGIES:
            children = _entry_documents(document, blocks, include_tables=True)
            if children:
                transformed.extend(children)
                mappings[document.id] = [child.id for child in children]
                counts["splitParents"] += 1
                continue
        if strategy in _SPLIT_STRATEGIES:
            sections = partition_sections(blocks)
            children = _section_documents(document, sections)
            extracted_characters = sum(len(child.content) for child in children)
            coverage = extracted_characters / max(len(document.content), 1)
            if len(children) >= 2 and coverage >= 0.65:
                transformed.extend(children)
                mappings[document.id] = [child.id for child in children]
                counts["splitParents"] += 1
                continue
            counts["fallbackParents"] += 1
        enhanced = _table_enhanced_document(document, blocks)
        transformed.append(enhanced)
        mappings[document.id] = [enhanced.id]
        if enhanced.metadata.get("structureVersion") == 2:
            counts["tableEnhancedParents"] += 1

    lengths = [len(document.content) for document in transformed]
    duplicate_groups: dict[str, list[str]] = {}
    entry_type_counts: dict[str, int] = {}
    navigation_shell_count = 0
    for value in transformed:
        key = _content_key(value.content)
        if key:
            duplicate_groups.setdefault(key, []).append(value.id)
        entry_type = value.metadata.get("entryType")
        if entry_type:
            entry_type_counts[str(entry_type)] = entry_type_counts.get(str(entry_type), 0) + 1
        heading_path = value.metadata.get("headingPath", [])
        if isinstance(heading_path, list) and _is_heading_shell(value.content, heading_path):
            navigation_shell_count += 1
    duplicate_groups = {
        key: ids for key, ids in duplicate_groups.items() if len(ids) > 1
    }
    documents_by_id = {value.id: value for value in transformed}
    duplicate_entry_groups = {
        key: ids
        for key, ids in duplicate_groups.items()
        if any(
            documents_by_id[item].metadata.get("entryType") not in (None, "rule_table")
            for item in ids
        )
        and len({documents_by_id[item].metadata.get("legacyParentId") for item in ids}) <= 1
    }
    duplicate_table_groups = {
        key: ids
        for key, ids in duplicate_groups.items()
        if all(documents_by_id[item].metadata.get("entryType") == "rule_table" for item in ids)
    }
    duplicate_cross_parent_groups = {
        key: ids
        for key, ids in duplicate_groups.items()
        if key not in duplicate_entry_groups and key not in duplicate_table_groups
    }
    report = {
        "inputDocumentCount": len(mappings),
        "outputDocumentCount": len(transformed),
        **counts,
        "maximumParentLength": max(lengths, default=0),
        "over10000": sum(length > 10_000 for length in lengths),
        "overEvidenceBudget": sum(length > _EVIDENCE_CHARACTER_LIMIT for length in lengths),
        "duplicateContentGroupCount": len(duplicate_groups),
        "duplicateContentGroups": list(duplicate_groups.values()),
        "duplicateEntryContentGroupCount": len(duplicate_entry_groups),
        "duplicateEntryContentGroups": list(duplicate_entry_groups.values()),
        "duplicateRuleTableGroupCount": len(duplicate_table_groups),
        "duplicateRuleTableGroups": list(duplicate_table_groups.values()),
        "duplicateCrossParentGroupCount": len(duplicate_cross_parent_groups),
        "duplicateCrossParentGroups": list(duplicate_cross_parent_groups.values()),
        "navigationShellCount": navigation_shell_count,
        "entryTypeCounts": entry_type_counts,
        "qualityGate": {
            "passed": not duplicate_entry_groups
            and navigation_shell_count == 0
            and not any(length > _EVIDENCE_CHARACTER_LIMIT for length in lengths),
            "evidenceCharacterLimit": _EVIDENCE_CHARACTER_LIMIT,
        },
        "mappings": mappings,
    }
    return transformed, report


def _entry_documents(
    document: RuleDocument,
    blocks: Sequence[HtmlBlock],
    *,
    include_tables: bool,
) -> list[RuleDocument]:
    """Build semantic parents from PF1e catalog entries and standalone tables.

    CHM catalog pages use several different labels, but their stable shape is
    usually a heading followed by a small set of labelled fields.  The field
    check is deliberately independent of any particular spell name: this is
    what keeps an entry such as Grease working while leaving spell-list
    headings and navigation indexes in their original parent document.
    """
    candidates = _entry_candidates(document, blocks)
    consumed = {
        index
        for candidate in candidates
        for index in range(candidate.block_start, candidate.block_end)
    }
    values: list[RuleDocument] = []
    seen: set[str] = set()
    for index, candidate in enumerate(candidates):
        child = _entry_document(document, candidate, index)
        key = _content_key(child.content)
        if len(child.content) > _EVIDENCE_CHARACTER_LIMIT:
            splits = _field_split_entry(document, candidate)
            if splits:
                for split in splits:
                    split_key = _content_key(split.content)
                    if split_key and split_key not in seen:
                        seen.add(split_key)
                        values.append(split)
                continue
        if not key or key in seen:
            continue
        seen.add(key)
        values.append(child)

    if include_tables:
        table_index = 0
        for block_index, block in enumerate(blocks):
            if block.kind != "table" or block_index in consumed or not _standalone_table(block):
                continue
            table_index += 1
            heading_path = _heading_path_at(blocks, block_index)
            child = _table_document(document, block, heading_path, block_index, table_index)
            key = _content_key(child.content)
            if key and key not in seen:
                seen.add(key)
                values.append(child)

    overview = _overview_document(document, blocks, consumed)
    for child in overview:
        key = _content_key(child.content)
        if key and key not in seen:
            seen.add(key)
            values.append(child)
    return values


def _overview_document(
    document: RuleDocument,
    blocks: Sequence[HtmlBlock],
    consumed: set[int],
) -> list[RuleDocument]:
    """Keep prose that no entry candidate covered.

    Table-heavy pages (strategy ``split_tables_with_context``) used to drop
    every paragraph that was not part of an extracted entry, losing most of
    the parent's prose.  The uncovered paragraphs become overview sections
    so the chapter text stays retrievable.
    """
    heading_path = document.full_path.split(" > ")
    paragraphs = [
        block.text
        for index, block in enumerate(blocks)
        if index not in consumed
        and block.kind == "paragraph"
        and block.text
        and not _is_heading_shell(block.text, [*heading_path, document.title])
    ]
    if not paragraphs:
        return []
    values: list[RuleDocument] = []
    for part in _chunk_paragraphs(paragraphs):
        content = "\n\n".join(part).strip()
        if not content:
            continue
        digest = hashlib.sha1(f"overview\0{content[:200]}".encode("utf-8")).hexdigest()[:10]
        metadata = {
            **document.metadata,
            "structureVersion": 2,
            "legacyParentId": document.id,
            "entryType": "section",
            "headingPath": heading_path,
            "structuralBlocks": [
                {"headingPath": heading_path, "content": content},
            ],
        }
        values.append(
            RuleDocument(
                id=f"{document.id}:section:{digest}",
                ruleset_id=document.ruleset_id,
                source_id=document.source_id,
                source_title=document.source_title,
                title=document.title,
                full_path=document.full_path,
                content=content,
                version=document.version,
                priority=document.priority,
                metadata=metadata,
            )
        )
    return values


def _chunk_paragraphs(paragraphs: Sequence[str], maximum: int = 8_000) -> list[list[str]]:
    """Group paragraphs so each chunk stays under the evidence limit."""
    chunks: list[list[str]] = []
    current: list[str] = []
    current_length = 0
    step = max(maximum - 50, 1)
    for paragraph in paragraphs:
        if len(paragraph) > maximum:
            if current:
                chunks.append(current)
                current = []
                current_length = 0
            for start in range(0, len(paragraph), step):
                chunks.append([paragraph[start:start + maximum]])
            continue
        if current and current_length + len(paragraph) > maximum:
            chunks.append(current)
            current = []
            current_length = 0
        current.append(paragraph)
        current_length += len(paragraph)
    if current:
        chunks.append(current)
    return chunks


_FIELD_START_RE = re.compile(r"^\s*等级\s*[：:]")


def _looks_like_field_start(value: str) -> bool:
    return bool(_FIELD_START_RE.match(value))


def _field_split_entry(
    document: RuleDocument,
    candidate: EntryCandidate,
) -> list[RuleDocument]:
    """Re-split an oversized catalog entry on ``等级：`` field paragraphs.

    Some catalog pages end with spells whose titles carry no parenthesised
    English name; the whole page tail collapses into one oversized entry.
    The stable ``等级：`` field opener marks each spell's start, with the
    preceding paragraph as its title.
    """
    blocks = list(candidate.blocks)
    field_starts = [
        index
        for index, block in enumerate(blocks)
        if block.kind == "paragraph" and _looks_like_field_start(block.text)
    ]
    if not field_starts:
        return []
    values: list[RuleDocument] = []
    for offset, field_index in enumerate(field_starts):
        title_index = (
            field_index - 1
            if field_index > 0 and blocks[field_index - 1].kind == "paragraph"
            else field_index
        )
        end = field_starts[offset + 1] if offset + 1 < len(field_starts) else len(blocks)
        title_block = blocks[title_index]
        title = title_block.text.split("\n", 1)[0].strip()
        split_heading_path = [*candidate.heading_path, title]
        body = blocks[title_index:end]
        content = "\n\n".join(block.text for block in body if block.text).strip()
        if not content:
            continue
        suffix_path = _deduplicated_suffix(document, split_heading_path)
        full_path = " > ".join([document.full_path, *suffix_path])
        heading_path = [*document.full_path.split(" > "), *suffix_path]
        name_zh, name_en = _entry_names(title)
        metadata = _entry_metadata(
            document,
            candidate.entry_type,
            title,
            name_zh,
            name_en,
            heading_path,
            title_index,
            end,
            body,
        )
        digest = _entry_digest(10_000 + offset, candidate.entry_type, split_heading_path, content)
        values.append(
            RuleDocument(
                id=f"{document.id}:entry:{digest}",
                ruleset_id=document.ruleset_id,
                source_id=document.source_id,
                source_title=document.source_title,
                title=title,
                full_path=full_path,
                content=content,
                version=document.version,
                priority=document.priority,
                metadata=metadata,
            )
        )
    return values


def _entry_candidates(
    document: RuleDocument,
    blocks: Sequence[HtmlBlock],
) -> list[EntryCandidate]:
    headings = [
        (index, block)
        for index, block in enumerate(blocks)
        if block.kind == "heading"
    ]
    candidates: list[EntryCandidate] = []
    for heading_offset, (start, heading) in enumerate(headings):
        end = len(blocks)
        for next_start, next_heading in headings[heading_offset + 1:]:
            if next_heading.level <= heading.level:
                end = next_start
                break
        body = blocks[start + 1:end]
        heading_path = tuple(_heading_path_at(blocks, start))
        # A catalog wrapper such as "CRB 核心规则手册" can itself look like a
        # spell because all of its descendants contribute spell fields.  If
        # it contains paragraph-style entry anchors, keep the children and
        # do not emit the wrapper as a duplicate oversized entry.
        if any(
            block.kind == "paragraph"
            and _looks_like_entry_title(block.text)
            and _looks_like_entry_start(document, blocks, position)
            for position, block in enumerate(blocks[start + 1:end], start + 1)
        ):
            continue
        if (
            any(block.kind == "heading" and block.level > heading.level for block in body)
            and _is_catalog_heading(heading.text)
        ):
            continue
        entry_type = _classify_entry(document, heading_path, heading.text, body)
        if not entry_type or not _substantive_entry_body(body, heading_path):
            continue
        candidate = EntryCandidate(
            heading_path=heading_path,
            blocks=tuple(blocks[start:end]),
            block_start=start,
            block_end=end,
            entry_type=entry_type,
        )
        if _content_key(candidate.content) in {
            _content_key(value.content) for value in candidates
        }:
            continue
        candidates.append(candidate)

    # A large portion of the source CHM uses plain paragraphs as entry titles
    # rather than h1-h6 elements.  Spell catalogs, for example, have the
    # stable shape "中文名 (English Name)" followed by a field table and
    # prose.  Treat those titles as anchors too; relying on HTML headings
    # alone leaves the entire catalog as one parent document.
    paragraph_starts = [
        index
        for index, block in enumerate(blocks)
        if block.kind == "paragraph"
        and _looks_like_entry_title(block.text)
        and _looks_like_entry_start(document, blocks, index)
    ]
    for index, start in enumerate(paragraph_starts):
        end = paragraph_starts[index + 1] if index + 1 < len(paragraph_starts) else len(blocks)
        title = blocks[start].text
        parent_path = _heading_path_at(blocks, start)
        heading_path = tuple([*parent_path, title])
        body = blocks[start + 1:end]
        entry_type = _classify_entry(document, heading_path, title, body)
        if not entry_type or not _substantive_entry_body(body, heading_path):
            continue
        candidate = EntryCandidate(
            heading_path=heading_path,
            blocks=tuple(blocks[start:end]),
            block_start=start,
            block_end=end,
            entry_type=entry_type,
        )
        if _content_key(candidate.content) in {
            _content_key(value.content) for value in candidates
        }:
            continue
        candidates.append(candidate)
    return candidates


_ENTRY_TITLE_PATTERN = re.compile(r"^.{1,160}\s*[\(（][^()（）\n]{2,160}[\)）]$")


def _looks_like_entry_title(value: str) -> bool:
    normalized = re.sub(r"\s+", " ", value).strip()
    if not _ENTRY_TITLE_PATTERN.match(normalized):
        return False
    name_zh, name_en = _entry_names(normalized)
    return bool(name_zh and name_en)


def _looks_like_entry_start(
    document: RuleDocument,
    blocks: Sequence[HtmlBlock],
    start: int,
) -> bool:
    context = " ".join([document.full_path, blocks[start].text]).casefold()
    following = blocks[start + 1 : min(len(blocks), start + 4)]
    following_text = "\n".join(block.text for block in following)
    if following and following[0].kind == "table":
        if any(
            _field_count(following_text, labels) >= 1
            for labels in (
                ("学派", "school"),
                ("先决条件", "prerequisites"),
                ("灵光", "aura"),
                ("替代", "replaces"),
            )
        ):
            return True
    if _contains_marker(context, _ENTRY_CATEGORY_MARKERS["feat"]):
        return _field_count(following_text, ("先决条件", "prerequisites", "好处", "benefit")) >= 1
    if _contains_marker(context, _ENTRY_CATEGORY_MARKERS["magic_item"]):
        return _field_count(following_text, ("灵光", "aura", "价格", "price")) >= 1
    if _contains_marker(context, _ENTRY_CATEGORY_MARKERS["archetype"]):
        return _field_count(following_text, ("替代", "replaces", "修改", "modified")) >= 1
    if _contains_marker(context, ("职业", "class")):
        if re.search(r"\b(?:ex|su|sp)\b|（[^）]*(?:ex|su|sp)[^）]*）", following_text, re.IGNORECASE):
            return True
        first = next((block for block in following if block.text.strip()), None)
        return bool(first and first.kind == "paragraph" and len(_compact(first.text)) >= 20)
    return False


def _classify_entry(
    document: RuleDocument,
    heading_path: Sequence[str],
    title: str,
    body: Sequence[HtmlBlock],
) -> str | None:
    body_text = "\n".join(block.text for block in body)
    if not body_text.strip():
        return None
    context = " ".join([document.full_path, *heading_path]).casefold()
    field_counts = {
        entry_type: _field_count(body_text, labels)
        for entry_type, labels in _ENTRY_FIELD_LABELS.items()
    }

    # Category context wins over an ambiguous field such as Level or Special.
    strong_fields = {
        "spell": ("school", "casting time", "components", "range", "target", "duration", "学派", "施法时间", "成分", "距离", "目标", "持续时间"),
        "feat": ("prerequisites", "benefit", "先决条件", "好处"),
        "magic_item": ("aura", "caster level", "slot", "price", "灵光", "施法者等级", "部位", "价格"),
        "archetype": ("replaces", "replaced", "modified", "altered", "替代", "替换", "修改", "改变"),
        "class_ability": ("class skill", "class skills", "class features", "description", "职业技能", "职业能力", "描述"),
    }
    for entry_type in ("spell", "feat", "magic_item", "archetype", "class_ability"):
        if not _contains_marker(context, _ENTRY_CATEGORY_MARKERS[entry_type]):
            continue
        if entry_type == "archetype":
            if not _looks_like_catalog_label(title):
                return entry_type
        if any(_field_count(body_text, (label,)) for label in strong_fields[entry_type]):
            return entry_type
        if field_counts[entry_type] >= 2 and entry_type not in {"spell", "class_ability"}:
            return entry_type

    if field_counts["spell"] >= 2:
        return "spell"
    if field_counts["magic_item"] >= 2:
        return "magic_item"
    if field_counts["archetype"] >= 1 and field_counts["class_ability"] >= 1:
        return "archetype"
    if _contains_marker(context, ("职业", "class")) and re.search(
        r"\b(?:ex|su|sp)\b|（[^）]*(?:ex|su|sp)[^）]*）",
        body_text,
        re.IGNORECASE,
    ):
        return "class_ability"
    if _contains_marker(context, ("职业", "class")):
        return "class_ability"
    if field_counts["feat"] >= 1:
        return "feat"
    if field_counts["class_ability"] >= 2:
        return "class_ability"
    return None


def _looks_like_catalog_label(value: str) -> bool:
    folded = value.casefold()
    return any(
        marker in folded
        for marker in ("列表", "目录", "表格", "list", "catalog", "table", "index")
    )


def _is_catalog_heading(value: str) -> bool:
    folded = value.casefold()
    return any(
        marker in folded
        for markers in _ENTRY_CATEGORY_MARKERS.values()
        for marker in markers
    ) or _looks_like_catalog_label(value)


def _field_count(text: str, labels: Sequence[str]) -> int:
    folded = text.casefold()
    count = 0
    for label in labels:
        pattern = re.compile(
            r"(?<![\w-])" + re.escape(label.casefold()) + r"(?=\s*(?::|：|[-—]|\s|$))"
        )
        if pattern.search(folded):
            count += 1
    return count


def _contains_marker(value: str, markers: Sequence[str]) -> bool:
    return any(marker.casefold() in value for marker in markers)


def _substantive_entry_body(body: Sequence[HtmlBlock], heading_path: Sequence[str]) -> bool:
    prose = "\n".join(block.text for block in body if block.kind != "table").strip()
    if not prose:
        return False
    if _is_heading_shell(prose, heading_path):
        return False
    return len(_compact(prose)) >= 12


def _heading_path_at(blocks: Sequence[HtmlBlock], position: int) -> list[str]:
    stack: list[tuple[int, str]] = []
    for block in blocks[:position + 1]:
        if block.kind != "heading":
            continue
        while stack and stack[-1][0] >= block.level:
            stack.pop()
        stack.append((block.level, block.text))
    return [value for _level, value in stack]


def _entry_document(
    document: RuleDocument,
    candidate: EntryCandidate,
    index: int,
) -> RuleDocument:
    content = candidate.content
    suffix_path = _deduplicated_suffix(document, candidate.heading_path)
    full_path = " > ".join([document.full_path, *suffix_path])
    title = suffix_path[-1] if suffix_path else candidate.heading_path[-1]
    name_zh, name_en = _entry_names(title)
    heading_path = [*document.full_path.split(" > "), *suffix_path]
    metadata = _entry_metadata(
        document,
        candidate.entry_type,
        title,
        name_zh,
        name_en,
        heading_path,
        candidate.block_start,
        candidate.block_end,
        candidate.blocks,
    )
    digest = _entry_digest(index, candidate.entry_type, candidate.heading_path, content)
    return RuleDocument(
        id=f"{document.id}:entry:{digest}",
        ruleset_id=document.ruleset_id,
        source_id=document.source_id,
        source_title=document.source_title,
        title=title,
        full_path=full_path,
        content=content,
        version=document.version,
        priority=document.priority,
        metadata=metadata,
    )


def _table_document(
    document: RuleDocument,
    block: HtmlBlock,
    heading_path: Sequence[str],
    block_index: int,
    table_index: int,
) -> RuleDocument:
    suffix_path = _deduplicated_suffix(document, heading_path)
    table_label = f"表格 {table_index}"
    title = heading_path[-1] if heading_path else document.title
    full_path = " > ".join([document.full_path, *suffix_path, table_label])
    content = "\n\n".join([value for value in [*heading_path, block.text] if value]).strip()
    name_zh, name_en = _entry_names(title)
    metadata = _entry_metadata(
        document,
        "rule_table",
        title,
        name_zh,
        name_en,
        [*document.full_path.split(" > "), *suffix_path, table_label],
        block_index,
        block_index + 1,
        [block],
    )
    metadata["tableIndex"] = table_index
    digest = _entry_digest(table_index, "rule_table", heading_path, content)
    return RuleDocument(
        id=f"{document.id}:table:{digest}",
        ruleset_id=document.ruleset_id,
        source_id=document.source_id,
        source_title=document.source_title,
        title=title,
        full_path=full_path,
        content=content,
        version=document.version,
        priority=document.priority,
        metadata=metadata,
    )


def _entry_metadata(
    document: RuleDocument,
    entry_type: str,
    title: str,
    name_zh: str | None,
    name_en: str | None,
    heading_path: Sequence[str],
    block_start: int,
    block_end: int,
    blocks: Sequence[HtmlBlock],
) -> dict[str, Any]:
    position = {
        "blockStart": block_start,
        "blockEnd": max(block_start, block_end - 1),
        "headingLevel": blocks[0].level if blocks and blocks[0].kind == "heading" else None,
    }
    metadata = {
        **document.metadata,
        "structureVersion": 2,
        "legacyParentId": document.id,
        "entryType": entry_type,
        "entryName": title,
        "nameZh": name_zh,
        "nameEn": name_en,
        "entryNameZh": name_zh,
        "entryNameEn": name_en,
        "headingPath": list(heading_path),
        "sourcePosition": position,
        "originalPosition": {**position, "headingPath": list(heading_path)},
        "structuralBlocks": _structural_blocks(blocks, list(heading_path)),
    }
    return metadata


def _entry_names(title: str) -> tuple[str | None, str | None]:
    value = re.sub(r"\s+", " ", title).strip()
    match = re.match(r"^(.+?)\s*[\(（]([^\)）]+)[\)）]$", value)
    if match:
        left, right = match.group(1).strip(), match.group(2).strip()
        if re.search(r"[\u3400-\u9fff]", left) and re.search(r"[A-Za-z]", right):
            return left, right
        if re.search(r"[A-Za-z]", left) and re.search(r"[\u3400-\u9fff]", right):
            return right, left
    match = re.match(r"^(.+?)\s*/\s*(.+)$", value)
    if match:
        left, right = match.group(1).strip(), match.group(2).strip()
        if re.search(r"[\u3400-\u9fff]", left) and re.search(r"[A-Za-z]", right):
            return left, right
        if re.search(r"[A-Za-z]", left) and re.search(r"[\u3400-\u9fff]", right):
            return right, left
    match = re.match(r"^([A-Za-z][A-Za-z0-9 +'’./-]*)\s*[—–:]\s*(.+)$", value)
    if match and re.search(r"[\u3400-\u9fff]", match.group(2)):
        return match.group(2).strip(), match.group(1).strip()
    if re.search(r"[\u3400-\u9fff]", value):
        return value, None
    if re.search(r"[A-Za-z]", value):
        return None, value
    return None, None


def _entry_digest(index: int, entry_type: str, heading_path: Sequence[str], content: str) -> str:
    digest_source = f"{index}\0{entry_type}\0{' > '.join(heading_path)}\0{content[:240]}"
    return hashlib.sha1(digest_source.encode("utf-8")).hexdigest()[:10]


def _standalone_table(block: HtmlBlock) -> bool:
    if block.kind != "table" or len(block.rows) < 2:
        return False
    data_rows = block.rows[1:]
    return any(any(_compact(cell) for cell in row) for row in data_rows)


def _content_key(value: str) -> str:
    return _compact(value)


def _section_documents(document: RuleDocument, sections: Sequence[Section]) -> list[RuleDocument]:
    values: list[RuleDocument] = []
    for index, section in enumerate(sections):
        content = section.content()
        known_headings = [
            document.title,
            *document.full_path.split(" > "),
            *section.heading_path,
        ]
        if not content or _is_heading_shell(content, known_headings):
            continue
        suffix_path = _deduplicated_suffix(document, section.heading_path)
        full_path = " > ".join([document.full_path, *suffix_path])
        title = suffix_path[-1] if suffix_path else document.title
        digest_source = f"{index}\0{' > '.join(section.heading_path)}\0{content[:200]}"
        digest = hashlib.sha1(digest_source.encode("utf-8")).hexdigest()[:10]
        heading_path = [*document.full_path.split(" > "), *suffix_path]
        metadata = {
            **document.metadata,
            "structureVersion": 2,
            "legacyParentId": document.id,
            "headingPath": heading_path,
            "structuralBlocks": _structural_blocks(section.blocks, heading_path),
        }
        values.append(
            RuleDocument(
                id=f"{document.id}:section:{digest}",
                ruleset_id=document.ruleset_id,
                source_id=document.source_id,
                source_title=document.source_title,
                title=title,
                full_path=full_path,
                content=content,
                version=document.version,
                priority=document.priority,
                metadata=metadata,
            )
        )
    return values


def _table_enhanced_document(document: RuleDocument, blocks: Sequence[HtmlBlock]) -> RuleDocument:
    table_blocks = _table_row_blocks(blocks, document.full_path.split(" > "))
    if not table_blocks:
        return document
    metadata = {
        **document.metadata,
        "structureVersion": 2,
        "legacyParentId": document.id,
        "headingPath": document.full_path.split(" > "),
        "structuralBlocks": [
            {
                "headingPath": document.full_path.split(" > "),
                "content": document.content,
            },
            *table_blocks,
        ],
    }
    return RuleDocument(
        id=document.id,
        ruleset_id=document.ruleset_id,
        source_id=document.source_id,
        source_title=document.source_title,
        title=document.title,
        full_path=document.full_path,
        content=document.content,
        version=document.version,
        priority=document.priority,
        metadata=metadata,
    )


def _structural_blocks(blocks: Sequence[HtmlBlock], heading_path: list[str]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    prose: list[str] = []
    for block in blocks:
        if block.kind == "table":
            if prose:
                values.append({"headingPath": heading_path, "content": "\n\n".join(prose)})
                prose = []
            values.extend(_table_row_blocks([block], heading_path))
        else:
            prose.append(block.text)
    if prose:
        values.append({"headingPath": heading_path, "content": "\n\n".join(prose)})
    return values or [{"headingPath": heading_path, "content": ""}]


def _table_row_blocks(
    blocks: Iterable[HtmlBlock],
    heading_path: list[str],
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    table_index = 0
    for block in blocks:
        if block.kind != "table" or not block.rows:
            continue
        table_index += 1
        header = " | ".join(block.rows[0])
        rows = block.rows[1:] or block.rows
        for row_index, row in enumerate(rows, start=1):
            row_text = " | ".join(row)
            if not row_text:
                continue
            values.append(
                {
                    "headingPath": [*heading_path, f"表格 {table_index}"],
                    "content": f"{header}\n{row_text}" if header and row != block.rows[0] else row_text,
                    "tableIndex": table_index,
                    "rowIndex": row_index,
                }
            )
    return values


def _deduplicated_suffix(document: RuleDocument, path: Sequence[str]) -> list[str]:
    existing = {_compact(value) for value in [document.title, *document.full_path.split(" > ")]}
    result: list[str] = []
    for value in path:
        if _compact(value) in existing and not result:
            continue
        if not result or _compact(result[-1]) != _compact(value):
            result.append(value)
    return result


def _is_heading_shell(content: str, heading_path: Sequence[str]) -> bool:
    """Reject tiny navigation labels repeated as both a heading and loose text."""
    if len(content.strip()) > 80:
        return False
    content_lines = {
        _compact(line) for line in content.splitlines() if _compact(line)
    }
    headings = {_compact(value) for value in heading_path if _compact(value)}
    return bool(content_lines and content_lines <= headings)


def _render_table(rows: Sequence[Sequence[str]]) -> str:
    width = max((len(row) for row in rows), default=0)
    if not width:
        return ""
    normalized = [list(row) + [""] * (width - len(row)) for row in rows]
    header = normalized[0]
    values = [
        "| " + " | ".join(_escape_table_cell(cell) for cell in row) + " |"
        for row in normalized
    ]
    return "\n".join([values[0], "| " + " | ".join("---" for _ in header) + " |", *values[1:]])


def _escape_table_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _normalize(value: str) -> str:
    lines = [_SPACE.sub(" ", line).strip() for line in value.replace("\r", "\n").split("\n")]
    return "\n".join(line for line in lines if line).strip()


def _compact(value: str) -> str:
    return "".join(character.casefold() for character in value if character.isalnum())


def _anchor_text_matches(text: str, name: str) -> bool:
    compact_text = _compact(text)
    return bool(compact_text) and compact_text.startswith(_compact(name))


def load_documents(path: Path) -> list[RuleDocument]:
    with path.open("r", encoding="utf-8") as handle:
        return [RuleDocument.from_json(json.loads(line)) for line in handle if line.strip()]


def write_documents(path: Path, documents: Iterable[RuleDocument]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for document in documents:
            handle.write(json.dumps(document.to_json(), ensure_ascii=False) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build PF1e heading/table structured candidate documents")
    parser.add_argument("--documents", required=True, type=Path)
    parser.add_argument("--extracted", required=True, type=Path)
    parser.add_argument("--audit", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    audit = json.loads(args.audit.read_text(encoding="utf-8"))
    documents, report = transform_documents(
        load_documents(args.documents),
        args.extracted,
        audit,
    )
    write_documents(args.output, documents)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "mappings"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
