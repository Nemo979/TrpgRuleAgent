from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trpg_retrieval.repository import RuleRepository
from trpg_retrieval.retriever import InMemoryRetriever
from trpg_retrieval.service import RetrievalService


@dataclass(frozen=True)
class LibraryManifest:
    id: str
    name: str
    system: str
    edition: str
    revision: str
    documents: Path
    index_dir: Path | None
    aliases: tuple[str, ...] = ()

    def public(self) -> dict[str, str]:
        return {
            "id": self.id,
            "name": self.name,
            "system": self.system,
            "edition": self.edition,
            "revision": self.revision,
        }


class Library:
    def __init__(self, manifest: LibraryManifest) -> None:
        self.manifest = manifest
        repository = RuleRepository.from_jsonl(manifest.documents)
        retriever = InMemoryRetriever()
        if manifest.index_dir is not None:
            from trpg_retrieval.hybrid_retriever import HybridRetriever
            from trpg_retrieval.vector_retriever import ChromaVectorRetriever

            retriever = HybridRetriever(ChromaVectorRetriever(repository, manifest.index_dir))
        self.service = RetrievalService(repository, retriever)

    def search(self, query: str, limit: int) -> list[dict[str, Any]]:
        if not query.strip():
            raise ValueError("query must not be empty")
        hits = self.service.retriever.search(
            query,
            self.service.repository.all(self.manifest.id),
            min(max(limit, 1), 20),
        )
        return [hit.to_json() for hit in hits]

    def read(self, ids: list[str]) -> list[dict[str, Any]]:
        return [
            document.to_json()
            for document in self.service.repository.read(self.manifest.id, ids)
        ]

    def document(self, document_id: str) -> dict[str, Any] | None:
        values = self.service.read(self.manifest.id, [document_id])
        return values[0] if values else None


class LibraryCatalog:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._libraries = self._load()

    def list(self) -> list[dict[str, str]]:
        return [item.manifest.public() for item in self._libraries.values()]

    def descriptors(self) -> list[dict[str, object]]:
        return [
            {
                **item.manifest.public(),
                "aliases": list(item.manifest.aliases),
            }
            for item in self._libraries.values()
        ]

    def get(self, library_id: str) -> Library:
        library = self._libraries.get(library_id)
        if library is None:
            raise KeyError(library_id)
        return library

    def _load(self) -> dict[str, Library]:
        if not self.root.exists():
            return {}
        result: dict[str, Library] = {}
        for manifest_path in sorted(self.root.glob("*/current/manifest.json")):
            manifest = _read_manifest(manifest_path)
            if manifest.id in result:
                raise ValueError(f"duplicate library id: {manifest.id}")
            result[manifest.id] = Library(manifest)
        return result


def _read_manifest(path: Path) -> LibraryManifest:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    aliases_value = value.get("aliases", [])
    if not isinstance(aliases_value, list) or any(
        not isinstance(alias, str) or not alias.strip()
        for alias in aliases_value
    ):
        raise ValueError("manifest aliases must be a list of non-empty strings")
    base = path.parent
    documents = (base / str(value["documents"])).resolve()
    index_value = value.get("indexDir")
    return LibraryManifest(
        id=str(value["id"]),
        name=str(value["name"]),
        system=str(value["system"]),
        edition=str(value["edition"]),
        revision=str(value["revision"]),
        documents=documents,
        index_dir=(base / str(index_value)).resolve() if index_value else None,
        aliases=tuple(alias.strip() for alias in aliases_value),
    )
