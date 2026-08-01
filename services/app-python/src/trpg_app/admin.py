from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trpg_retrieval.repository import RuleRepository


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage staged TRPG rule libraries")
    parser.add_argument("--root", type=Path, default=Path("data/libraries"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build-jsonl", help="stage an existing normalized JSONL library")
    build.add_argument("--id", required=True)
    build.add_argument("--name", required=True)
    build.add_argument("--system", required=True)
    build.add_argument("--alias", action="append", default=[])
    build.add_argument("--edition", required=True)
    build.add_argument("--documents", type=Path, required=True)
    build.add_argument("--import-report", type=Path)
    build.add_argument("--index-dir", type=Path)

    pdf = subparsers.add_parser("extract-pdf", help="extract a text PDF into normalized JSONL")
    pdf.add_argument("--id", required=True)
    pdf.add_argument("--edition", required=True)
    pdf.add_argument("--source-title", required=True)
    pdf.add_argument("--pdf", type=Path, required=True)
    pdf.add_argument("--output", type=Path, required=True)

    chm = subparsers.add_parser("extract-chm", help="extract a CHM into normalized JSONL")
    chm.add_argument("--id", required=True)
    chm.add_argument("--edition", required=True)
    chm.add_argument("--chm", type=Path, required=True)
    chm.add_argument("--output", type=Path, required=True)

    publish = subparsers.add_parser("publish", help="atomically activate a staged revision")
    publish.add_argument("--id", required=True)
    publish.add_argument("--revision", required=True)

    args = parser.parse_args()
    if args.command == "build-jsonl":
        revision = build_jsonl(
            root=args.root,
            library_id=args.id,
            name=args.name,
            system=args.system,
            aliases=args.alias,
            edition=args.edition,
            documents=args.documents,
            import_report=args.import_report,
            index_dir=args.index_dir,
        )
        print(revision)
    elif args.command == "publish":
        publish_revision(args.root, args.id, args.revision)
    elif args.command == "extract-pdf":
        from .importers.pdf import import_pdf

        print(
            json.dumps(
                import_pdf(
                    pdf_path=args.pdf,
                    output_dir=args.output,
                    library_id=args.id,
                    source_title=args.source_title,
                    edition=args.edition,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "extract-chm":
        from .importers.chm import import_chm

        print(
            json.dumps(
                import_chm(
                    chm_path=args.chm,
                    output_dir=args.output,
                    library_id=args.id,
                    edition=args.edition,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )


def build_jsonl(
    *,
    root: Path,
    library_id: str,
    name: str,
    system: str,
    edition: str,
    documents: Path,
    index_dir: Path | None,
    aliases: list[str] | tuple[str, ...] = (),
    import_report: Path | None = None,
) -> str:
    normalized_aliases = _normalize_aliases(aliases)
    repository = RuleRepository.from_jsonl(documents)
    values = repository.all()
    if not values:
        raise ValueError("library contains no documents")
    wrong_library = [item.id for item in values if item.ruleset_id != library_id]
    if wrong_library:
        raise ValueError(f"documents use a different library id: {wrong_library[0]}")

    duplicates = _exact_content_duplicates(values)
    if duplicates:
        first = duplicates[0]
        raise ValueError(f"exact duplicate content: {first[0]} and {first[1]}")

    extraction_report = (
        _load_import_report(import_report, library_id)
        if import_report is not None
        else {}
    )
    revision = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stage = root / library_id / "builds" / revision
    if stage.exists():
        raise ValueError(f"revision already exists: {revision}")
    stage.mkdir(parents=True)
    shutil.copy2(documents, stage / "documents.jsonl")
    if index_dir is not None:
        shutil.copytree(index_dir, stage / "vector-index")

    report = {
        **extraction_report,
        "libraryId": library_id,
        "revision": revision,
        "documentCount": len(values),
        "exactDuplicates": [],
    }
    report.setdefault("warnings", [])
    (stage / "import-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest: dict[str, Any] = {
        "id": library_id,
        "name": name,
        "system": system,
        "edition": edition,
        "revision": revision,
        "documents": "documents.jsonl",
    }
    if normalized_aliases:
        manifest["aliases"] = normalized_aliases
    if index_dir is not None:
        manifest["indexDir"] = "vector-index"
    (stage / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return revision


def publish_revision(root: Path, library_id: str, revision: str) -> None:
    library_root = (root / library_id).resolve()
    target = (library_root / "builds" / revision).resolve()
    if not target.is_dir() or library_root not in target.parents:
        raise ValueError(f"unknown revision: {revision}")
    current = library_root / "current"
    replacement = library_root / f".current-{os.getpid()}"
    replacement.symlink_to(Path("builds") / revision, target_is_directory=True)
    os.replace(replacement, current)


def _exact_content_duplicates(documents: list[Any]) -> list[tuple[str, str]]:
    seen: dict[str, str] = {}
    duplicates: list[tuple[str, str]] = []
    for document in documents:
        normalized = " ".join(document.content.split())
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        existing = seen.get(digest)
        if existing:
            duplicates.append((existing, document.id))
        else:
            seen[digest] = document.id
    return duplicates


def _normalize_aliases(aliases: list[str] | tuple[str, ...]) -> list[str]:
    if any(not isinstance(alias, str) or not alias.strip() for alias in aliases):
        raise ValueError("aliases must be non-empty strings")
    return list(dict.fromkeys(alias.strip() for alias in aliases))


def _load_import_report(path: Path, library_id: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid import report JSON: {path}") from error
    if not isinstance(value, dict):
        raise ValueError("import report must be a JSON object")
    report_library_id = value.get("libraryId")
    if report_library_id != library_id:
        raise ValueError(
            "import report uses a different library id: "
            f"{report_library_id!r} (expected {library_id!r})"
        )
    return value


if __name__ == "__main__":
    main()
