import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .domain import RuleDocument, SearchHit
from .repository import RuleRepository
from .vector_index import LEGACY_COLLECTION_NAME, embed_query, prepare_fastembed_model


class ChromaVectorRetriever:
    def __init__(
        self,
        repository: RuleRepository,
        index_dir: Path,
        search_k: int = 100,
    ) -> None:
        try:
            import chromadb
            from fastembed import TextEmbedding
        except ImportError as error:
            raise RuntimeError(
                "Vector dependencies are missing. Install requirements-vector.txt"
            ) from error

        manifest_path = index_dir / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError("Vector index manifest not found: %s" % manifest_path)
        self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.repository = repository
        self.search_k = search_k
        if self.manifest.get("embeddingEngine") != "fastembed":
            raise RuntimeError("Unsupported vector index embedding engine")
        self.ruleset_id = str(self.manifest.get("rulesetId", "pathfinder-1e"))
        self.model_name = str(self.manifest["model"])
        prepare_fastembed_model(TextEmbedding, self.model_name)
        self.model = TextEmbedding(
            model_name=self.model_name,
            cache_dir=self.manifest.get("cacheDir"),
        )
        client = chromadb.PersistentClient(path=str(index_dir / "chroma"))
        collection_name = str(
            self.manifest.get("collectionName", LEGACY_COLLECTION_NAME)
        )
        self.collection = client.get_collection(collection_name)

    def search(
        self,
        query: str,
        documents: Iterable[RuleDocument],
        limit: int,
        source_ids: Optional[Sequence[str]] = None,
    ) -> List[SearchHit]:
        del documents
        query_embedding = next(iter(embed_query(self.model, self.model_name, query)))
        where = self._where(source_ids)
        result = self.collection.query(
            query_embeddings=[query_embedding.tolist()],
            n_results=self.search_k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        child_documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        child_ids = (result.get("ids") or [[]])[0]

        best_by_parent: Dict[str, Tuple[float, str, Optional[str], Optional[int]]] = {}
        for child, metadata, distance, child_id in zip(
            child_documents,
            metadatas,
            distances,
            child_ids or [None] * len(child_documents),
        ):
            if metadata is None or child is None or distance is None:
                continue
            parent_id = str(metadata["parentId"])
            priority = float(metadata.get("priority", 0))
            score = (1.0 - float(distance)) + max(0.0, priority) / 10000.0
            existing = best_by_parent.get(parent_id)
            if existing is None or score > existing[0]:
                best_by_parent[parent_id] = (
                    score,
                    str(child),
                    str(child_id) if child_id is not None else None,
                    int(metadata["chunkIndex"]) if metadata.get("chunkIndex") is not None else None,
                )

        ranked = sorted(best_by_parent.items(), key=lambda item: item[1][0], reverse=True)
        parent_ids = [parent_id for parent_id, _value in ranked[:limit]]
        parents = self.repository.read(self.ruleset_id, parent_ids)
        parent_by_id = {document.id: document for document in parents}
        return [
            SearchHit(
                parent_by_id[parent_id],
                excerpt,
                score,
                chunk_id=chunk_id,
                chunk_index=chunk_index,
                match_score=score,
                parent_score=score,
            )
            for parent_id, (score, excerpt, chunk_id, chunk_index) in ranked[:limit]
            if parent_id in parent_by_id
        ]

    def _where(self, source_ids: Optional[Sequence[str]]) -> Dict[str, Any]:
        filters: List[Dict[str, Any]] = [{"rulesetId": {"$eq": self.ruleset_id}}]
        if source_ids:
            filters.append({"sourceId": {"$in": list(source_ids)}})
        return filters[0] if len(filters) == 1 else {"$and": filters}
