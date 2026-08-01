from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any, Sequence

import pdfplumber

from trpg_retrieval.domain import RuleDocument
from trpg_retrieval.importers.chm import write_jsonl


def import_pdf(
    *,
    pdf_path: Path,
    output_dir: Path,
    library_id: str,
    source_title: str,
    edition: str,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    originals = output_dir / "originals"
    originals.mkdir(exist_ok=True)
    copied_pdf = originals / pdf_path.name
    shutil.copy2(pdf_path, copied_pdf)

    documents: list[RuleDocument] = []
    warnings: list[dict[str, Any]] = []
    table_count = 0
    heading_context = HeadingContext(source_title)
    with pdfplumber.open(pdf_path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            raw_text = page.extract_text(layout=True) or ""
            text = normalize_layout_text(raw_text)
            detected_headings = detect_page_headings(raw_text)
            local_headings = detect_bulleted_page_headings(raw_text)
            raw_tables = page.extract_tables() or []
            rendered_tables: list[str] = []
            for table_index, table in enumerate(raw_tables, start=1):
                markdown, reliable = table_to_markdown(table)
                if markdown:
                    rendered_tables.append(
                        f"### 表格 {table_index}\n\n{markdown}"
                    )
                    table_count += 1
                if not reliable:
                    warnings.append(
                        {
                            "type": "unreliable_table",
                            "page": page_number,
                            "table": table_index,
                        }
                    )
            if page.images and len(text) < 80:
                warnings.append(
                    {
                        "type": "image_dominant_page",
                        "page": page_number,
                        "imageCount": len(page.images),
                    }
                )
            content_parts = [part for part in [text, *rendered_tables] if part]
            if not content_parts:
                warnings.append({"type": "empty_page", "page": page_number})
                continue
            content = "\n\n".join(content_parts)
            heading_path, inherited_headings, structural_blocks = (
                heading_context.partition(content, detected_headings, local_headings)
            )
            document_id = _page_id(library_id, pdf_path.name, page_number)
            path_parts = [*heading_path, f"第 {page_number} 页"]
            documents.append(
                RuleDocument(
                    id=document_id,
                    ruleset_id=library_id,
                    source_id=_source_id(source_title),
                    source_title=source_title,
                    title=f"{source_title} · 第 {page_number} 页",
                    full_path=" > ".join(path_parts),
                    content=content,
                    version=edition,
                    priority=0,
                    metadata={
                        "format": "pdf",
                        "sourceFile": pdf_path.name,
                        "page": page_number,
                        "tableCount": len(rendered_tables),
                        "structureVersion": 2,
                        "headingPath": heading_path,
                        "detectedHeadings": detected_headings,
                        "localHeadings": local_headings,
                        "inheritedHeadings": inherited_headings,
                        "structuralBlocks": structural_blocks,
                    },
                )
            )

    write_jsonl(output_dir / "documents.jsonl", documents)
    report = {
        "libraryId": library_id,
        "sourceFile": pdf_path.name,
        "pageCount": page_number if "page_number" in locals() else 0,
        "documentCount": len(documents),
        "tableCount": table_count,
        "warnings": warnings,
    }
    (output_dir / "import-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


class HeadingContext:
    """Conservative cross-page heading context for layout-extracted PDFs."""

    def __init__(self, source_title: str) -> None:
        self.source_title = source_title
        self.primary: str | None = None
        self.subsection: str | None = None

    def partition(
        self,
        content: str,
        headings: list[str],
        local_headings: list[str] | None = None,
    ) -> tuple[list[str], list[str], list[dict[str, Any]]]:
        before = self._values()
        heading_set = set(headings)
        local_heading_set = set(local_headings or [])
        blocks: list[dict[str, Any]] = []
        lines: list[str] = []
        block_path = [self.source_title, *before]
        for line in content.splitlines():
            heading_label = _heading_label(line)
            if heading_label in heading_set:
                self._append_block(blocks, block_path, lines)
                lines = []
                if heading_label in local_heading_set:
                    block_path = [self.source_title, *self._values(), heading_label]
                else:
                    self._apply(heading_label)
                    block_path = [self.source_title, *self._values()]
            lines.append(line)
        self._append_block(blocks, block_path, lines)
        after = self._values()
        inherited = [value for value in before if value in after and value not in headings]
        return [self.source_title, *after], inherited, blocks

    def _apply(self, heading: str) -> None:
        if is_primary_heading(heading):
            self.primary = heading
            self.subsection = None
        else:
            self.subsection = heading

    @staticmethod
    def _append_block(
        blocks: list[dict[str, Any]],
        heading_path: list[str],
        lines: list[str],
    ) -> None:
        content = "\n".join(lines).strip()
        if content:
            blocks.append({"headingPath": heading_path, "content": content})

    def _values(self) -> list[str]:
        return [value for value in (self.primary, self.subsection) if value]


def normalize_layout_text(value: str) -> str:
    lines: list[str] = []
    blank = False
    for raw_line in value.splitlines():
        line = " ".join(raw_line.split())
        if not line:
            blank = bool(lines)
            continue
        if blank and lines and lines[-1] != "":
            lines.append("")
        lines.append(line)
        blank = False
    return "\n".join(lines).strip()


def detect_page_headings(value: str) -> list[str]:
    raw_lines = value.splitlines()
    normalized = [" ".join(line.split()) for line in raw_lines]
    headings: list[str] = []
    for index, line in enumerate(normalized):
        if not line or len(line) > 32:
            continue
        previous_blank = index == 0 or not normalized[index - 1]
        next_blank = index == len(normalized) - 1 or not normalized[index + 1]
        bulleted = line.startswith(("⚫", "•"))
        if not previous_blank or (not next_blank and not bulleted):
            continue
        if _looks_like_heading(line):
            headings.append(_heading_label(line))
    return list(dict.fromkeys(headings))


def detect_bulleted_page_headings(value: str) -> list[str]:
    return [
        heading
        for heading in detect_page_headings(value)
        if any(
            (
                _heading_label(normalized) == heading
                and _heading_label(normalized) != normalized
            )
            for line in value.splitlines()
            if (normalized := " ".join(line.split()))
        )
    ]


def is_primary_heading(value: str) -> bool:
    return (
        len(value) <= 8
        and not re.search(r"[《》【】（）()：:]", value)
        and not re.search(r"弱点|特技|技能|能力|表格|步骤|说明", value)
    )


def _looks_like_heading(value: str) -> bool:
    if value.startswith(("-", "|", "※")):
        return False
    value = _heading_label(value)
    if value.endswith(("。", "！", "？", ".", "!", "?", "；", ";")):
        return False
    if re.fullmatch(r"[\d\s/＋+－—-]+", value):
        return False
    if value[0].isdigit() and not re.match(r"^\d+[.、]", value):
        return False
    return bool(
        len(value) <= 12
        or re.match(r"^(?:第.+[章节篇部]|\d+(?:\.\d+)*[、.]?)", value)
        or re.search(r"[《》【】]", value)
    )


def _heading_label(value: str) -> str:
    return re.sub(r"^[⚫•]\s*", "", value).strip()


def table_to_markdown(table: Sequence[Sequence[Any]]) -> tuple[str, bool]:
    rows = [
        [normalize_cell(cell) for cell in row]
        for row in table
        if row and any(normalize_cell(cell) for cell in row)
    ]
    if not rows:
        return "", False
    width = max(len(row) for row in rows)
    reliable = width > 1 and all(len(row) == width for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    header = padded[0]
    if not any(header):
        header = [f"列 {index + 1}" for index in range(width)]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in range(width)) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in padded[1:])
    return "\n".join(lines), reliable


def normalize_cell(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("|", "\\|").split())


def _page_id(library_id: str, filename: str, page_number: int) -> str:
    digest = hashlib.sha256(f"{filename}:{page_number}".encode()).hexdigest()[:16]
    return f"{library_id}:pdf:{digest}"


def _source_id(source_title: str) -> str:
    return "source-" + hashlib.sha256(source_title.encode()).hexdigest()[:12]
