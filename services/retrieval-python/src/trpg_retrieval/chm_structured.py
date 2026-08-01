from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Sequence

from .domain import RuleDocument
from .importers.chm import decode_document


_SPACE = re.compile(r"[ \t\f\v]+")
_SPLIT_STRATEGIES = {"split_headings", "split_headings_and_tables"}
_TABLE_STRATEGIES = {"keep_table_aware", "split_headings_and_tables"}
_BLOCK_TAGS = {"p", "li", "dt", "dd", "pre", "blockquote"}
_SKIP_TAGS = {"script", "style", "noscript"}


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
        if strategy not in _SPLIT_STRATEGIES | _TABLE_STRATEGIES or not html_path.is_file():
            transformed.append(document)
            mappings[document.id] = [document.id]
            continue
        blocks = extract_blocks(html_path)
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
    report = {
        "inputDocumentCount": len(mappings),
        "outputDocumentCount": len(transformed),
        **counts,
        "maximumParentLength": max(lengths, default=0),
        "over10000": sum(length > 10_000 for length in lengths),
        "mappings": mappings,
    }
    return transformed, report


def _section_documents(document: RuleDocument, sections: Sequence[Section]) -> list[RuleDocument]:
    values: list[RuleDocument] = []
    for index, section in enumerate(sections):
        content = section.content()
        if not content:
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
