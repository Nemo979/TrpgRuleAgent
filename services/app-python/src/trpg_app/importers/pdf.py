from __future__ import annotations

import hashlib
import json
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
    with pdfplumber.open(pdf_path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            text = (page.extract_text(layout=True) or "").strip()
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
            document_id = _page_id(library_id, pdf_path.name, page_number)
            documents.append(
                RuleDocument(
                    id=document_id,
                    ruleset_id=library_id,
                    source_id=_source_id(source_title),
                    source_title=source_title,
                    title=f"{source_title} · 第 {page_number} 页",
                    full_path=f"{source_title} > 第 {page_number} 页",
                    content=content,
                    version=edition,
                    priority=0,
                    metadata={
                        "format": "pdf",
                        "sourceFile": pdf_path.name,
                        "page": page_number,
                        "tableCount": len(rendered_tables),
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
