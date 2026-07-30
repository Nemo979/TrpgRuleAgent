from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

from trpg_retrieval.importers.chm import import_documents, write_jsonl


def import_chm(
    *,
    chm_path: Path,
    output_dir: Path,
    library_id: str,
    edition: str,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    originals = output_dir / "originals"
    originals.mkdir(exist_ok=True)
    shutil.copy2(chm_path, originals / chm_path.name)
    extracted = output_dir / "extracted"
    extracted.mkdir(exist_ok=True)
    subprocess.run(
        ["extract_chmLib", str(chm_path), str(extracted)],
        check=True,
        capture_output=True,
        text=True,
    )
    imported, report = import_documents(extracted)
    documents = [
        replace(
            document,
            id=_document_id(library_id, str(document.metadata.get("sourceFile", document.id))),
            ruleset_id=library_id,
            source_id=_source_id(document.source_title),
            version=edition,
            priority=0,
            metadata={**document.metadata, "format": "chm", "originalFile": chm_path.name},
        )
        for document in imported
    ]
    write_jsonl(output_dir / "documents.jsonl", documents)
    result = {
        **report,
        "libraryId": library_id,
        "sourceFile": chm_path.name,
        "documentCount": len(documents),
    }
    (output_dir / "import-report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def _document_id(library_id: str, source_file: str) -> str:
    digest = hashlib.sha256(source_file.lower().encode()).hexdigest()[:16]
    return f"{library_id}:chm:{digest}"


def _source_id(source_title: str) -> str:
    return "source-" + hashlib.sha256(source_title.encode()).hexdigest()[:12]
