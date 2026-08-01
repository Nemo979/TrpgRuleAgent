from dataclasses import dataclass
from typing import Iterable, Iterator

from .domain import RuleDocument


@dataclass(frozen=True)
class RuleChunk:
    id: str
    parent_id: str
    content: str
    search_text: str
    index: int


def iter_document_chunks(
    document: RuleDocument,
    chunk_size: int,
    chunk_overlap: int,
) -> Iterator[RuleChunk]:
    if chunk_size <= chunk_overlap:
        raise ValueError("chunk_size must be larger than chunk_overlap")

    blocks = document.metadata.get("structuralBlocks")
    if not isinstance(blocks, list) or not blocks:
        blocks = [{"content": document.content, "headingPath": []}]

    index = 0
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_content = str(block.get("content", "")).strip()
        if not block_content:
            continue
        heading_path = block.get("headingPath")
        path = (
            " > ".join(str(value) for value in heading_path if value)
            if isinstance(heading_path, list)
            else document.full_path
        )
        prefix = "%s\n%s\n" % (document.title, path or document.full_path)
        step = chunk_size - chunk_overlap
        for start in range(0, max(len(block_content), 1), step):
            content = block_content[start:start + chunk_size]
            if not content:
                break
            yield RuleChunk(
                id="%s:%05d" % (document.id, index),
                parent_id=document.id,
                content=content,
                search_text=prefix + content,
                index=index,
            )
            index += 1
            if start + chunk_size >= len(block_content):
                break


def iter_chunks(
    documents: Iterable[RuleDocument],
    chunk_size: int,
    chunk_overlap: int,
) -> Iterator[RuleChunk]:
    for document in documents:
        yield from iter_document_chunks(document, chunk_size, chunk_overlap)
