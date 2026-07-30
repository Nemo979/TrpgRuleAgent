import argparse
import hashlib
import itertools
import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .chunks import RuleChunk, iter_chunks
from .repository import RuleRepository


DEFAULT_MODEL = "BAAI/bge-small-zh-v1.5"
EMBEDDING_ENGINE = "fastembed"
COLLECTION_NAME = "rule_child_chunks"
LEGACY_COLLECTION_NAME = "pathfinder_1e_child_chunks"
CUSTOM_FASTEMBED_MODELS = {
    "intfloat/multilingual-e5-small": {
        "dim": 384,
        "hf": "intfloat/multilingual-e5-small",
        "model_file": "onnx/model.onnx",
    },
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def batched(values: Iterable[RuleChunk], batch_size: int) -> Iterable[List[RuleChunk]]:
    batch: List[RuleChunk] = []
    for value in values:
        batch.append(value)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def _write_json(path: Path, value: Dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _matches(value: Dict[str, Any], expected: Dict[str, Any]) -> bool:
    return all(value.get(key) == expected_value for key, expected_value in expected.items())


def prepare_fastembed_model(TextEmbedding: Any, model_name: str) -> Dict[str, Any]:
    supported = {
        str(item["model"]): item for item in TextEmbedding.list_supported_models()
    }
    if model_name not in supported and model_name in CUSTOM_FASTEMBED_MODELS:
        from fastembed.common.model_description import ModelSource, PoolingType

        value = CUSTOM_FASTEMBED_MODELS[model_name]
        TextEmbedding.add_custom_model(
            model=model_name,
            pooling=PoolingType.MEAN,
            normalization=True,
            sources=ModelSource(hf=str(value["hf"])),
            dim=int(value["dim"]),
            model_file=str(value["model_file"]),
        )
        supported = {
            str(item["model"]): item for item in TextEmbedding.list_supported_models()
        }
    if model_name not in supported:
        raise ValueError("unsupported FastEmbed model: %s" % model_name)
    return supported[model_name]


def embed_passages(model: Any, model_name: str, texts: List[str], **kwargs: Any) -> Any:
    if model_name.startswith("intfloat/multilingual-e5-"):
        return model.embed(["passage: " + text for text in texts], **kwargs)
    return model.passage_embed(texts, **kwargs)


def embed_query(model: Any, model_name: str, query: str) -> Any:
    if model_name.startswith("intfloat/multilingual-e5-"):
        return model.embed(["query: " + query])
    return model.query_embed(query)


def build_index(
    documents_path: Path,
    index_dir: Path,
    cache_dir: Optional[Path] = None,
    model_name: str = DEFAULT_MODEL,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
    batch_size: int = 32,
    write_batch_size: int = 2048,
    threads: Optional[int] = None,
    parallel: Optional[int] = None,
    force: bool = False,
) -> Dict[str, Any]:
    try:
        import chromadb
        from fastembed import TextEmbedding
    except ImportError as error:
        raise RuntimeError(
            "Vector dependencies are missing. Install requirements-vector.txt"
        ) from error

    repository = RuleRepository.from_jsonl(documents_path)
    rulesets = repository.rulesets()
    if len(rulesets) != 1:
        raise ValueError(
            "a vector index must contain exactly one rule library; found %d" % len(rulesets)
        )
    ruleset_id = rulesets[0]
    collection_name = COLLECTION_NAME
    source_hash = file_sha256(documents_path)
    index_dir.mkdir(parents=True, exist_ok=True)
    resolved_cache_dir = cache_dir or (index_dir / "model-cache")
    resolved_cache_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = index_dir / "manifest.json"
    build_state_path = index_dir / "build-state.json"
    expected = {
        "sourceSha256": source_hash,
        "model": model_name,
        "embeddingEngine": EMBEDDING_ENGINE,
        "rulesetId": ruleset_id,
        "collectionName": collection_name,
        "chunkSize": chunk_size,
        "chunkOverlap": chunk_overlap,
    }

    if manifest_path.exists() and not force:
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if _matches(existing, expected):
            print("Vector index is already current: %s" % manifest_path)
            return existing
        raise RuntimeError("Vector index configuration changed; rebuild with --force")

    if build_state_path.exists() and not force:
        build_state = json.loads(build_state_path.read_text(encoding="utf-8"))
        if not _matches(build_state, expected):
            raise RuntimeError(
                "Partial vector index configuration changed; rebuild with --force"
            )
    else:
        build_state = dict(expected)
        build_state.update({"status": "building", "indexedChunks": 0})
        _write_json(build_state_path, build_state)

    documents = repository.all()
    client = chromadb.PersistentClient(path=str(index_dir / "chroma"))
    if force:
        try:
            client.delete_collection(collection_name)
        except Exception:
            pass
        build_state = dict(expected)
        build_state.update({"status": "building", "indexedChunks": 0})
        _write_json(build_state_path, build_state)

    collection = client.get_or_create_collection(
        collection_name,
        metadata={"hnsw:space": "cosine"},
    )
    existing_chunk_count = collection.count()
    if existing_chunk_count > 0:
        print("Resuming vector index after %d existing chunks" % existing_chunk_count)

    print("Loading FastEmbed model: %s" % model_name, flush=True)
    model_metadata = prepare_fastembed_model(TextEmbedding, model_name)
    model = TextEmbedding(
        model_name=model_name,
        cache_dir=str(resolved_cache_dir),
        threads=threads,
    )
    started_at = time.time()
    chunk_count = existing_chunk_count
    indexed_this_run = 0
    embedding_dimension = int(model_metadata["dim"])

    chunks = itertools.islice(
        iter_chunks(documents, chunk_size, chunk_overlap),
        existing_chunk_count,
        None,
    )
    for batch_number, batch in enumerate(batched(chunks, write_batch_size), start=1):
        embedding_started_at = time.time()
        embeddings = list(embed_passages(
            model,
            model_name,
            [chunk.search_text for chunk in batch],
            batch_size=batch_size,
            parallel=parallel,
        ))
        embedding_seconds = time.time() - embedding_started_at
        parent_documents = repository.read(
            ruleset_id,
            [chunk.parent_id for chunk in batch],
        )
        parent_by_id = {document.id: document for document in parent_documents}
        metadatas = []
        for chunk in batch:
            parent = parent_by_id[chunk.parent_id]
            metadatas.append({
                "parentId": parent.id,
                "rulesetId": parent.ruleset_id,
                "sourceId": parent.source_id,
                "sourceTitle": parent.source_title,
                "title": parent.title,
                "fullPath": parent.full_path,
                "version": parent.version,
                "priority": parent.priority,
                "chunkIndex": chunk.index,
            })
        write_started_at = time.time()
        collection.add(
            ids=[chunk.id for chunk in batch],
            documents=[chunk.content for chunk in batch],
            metadatas=metadatas,
            embeddings=[embedding.tolist() for embedding in embeddings],
        )
        write_seconds = time.time() - write_started_at
        chunk_count += len(batch)
        indexed_this_run += len(batch)
        if batch_number == 1 or batch_number % 2 == 0:
            elapsed = max(time.time() - started_at, 0.001)
            build_state.update({
                "status": "building",
                "indexedChunks": chunk_count,
                "updatedAtEpochSeconds": round(time.time(), 3),
            })
            _write_json(build_state_path, build_state)
            print(
                "Indexed %d chunks (+%d this run, %.1f chunks/s; "
                "last embed %.1fs, write %.1fs)" %
                (
                    chunk_count,
                    indexed_this_run,
                    indexed_this_run / elapsed,
                    embedding_seconds,
                    write_seconds,
                ),
                flush=True,
            )

    manifest = {
        "rulesetId": ruleset_id,
        "collectionName": collection_name,
        "sourceDocuments": str(documents_path),
        "sourceSha256": source_hash,
        "documentCount": len(documents),
        "chunkCount": chunk_count,
        "model": model_name,
        "embeddingEngine": EMBEDDING_ENGINE,
        "cacheDir": str(resolved_cache_dir),
        "chunkSize": chunk_size,
        "chunkOverlap": chunk_overlap,
        "embeddingDimension": embedding_dimension,
        "lastRunBuildSeconds": round(time.time() - started_at, 3),
    }
    _write_json(manifest_path, manifest)
    completed_state = dict(expected)
    completed_state.update({"status": "complete", "indexedChunks": chunk_count})
    _write_json(build_state_path, completed_state)
    print("Vector index built: %s" % manifest_path)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the PF1E BGE + Chroma index")
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--index-dir", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--chunk-size", type=int, default=500)
    parser.add_argument("--chunk-overlap", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--write-batch-size", type=int, default=2048)
    parser.add_argument("--threads", type=int)
    parser.add_argument("--parallel", type=int)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    build_index(
        documents_path=args.documents,
        index_dir=args.index_dir,
        cache_dir=args.cache_dir,
        model_name=args.model,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        batch_size=args.batch_size,
        write_batch_size=args.write_batch_size,
        threads=args.threads,
        parallel=args.parallel,
        force=args.force,
    )


if __name__ == "__main__":
    main()
