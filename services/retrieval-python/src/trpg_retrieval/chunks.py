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

    step = chunk_size - chunk_overlap
    prefix = "%s\n%s\n" % (document.title, document.full_path)
    for index, start in enumerate(range(0, max(len(document.content), 1), step)):
        content = document.content[start:start + chunk_size]
        if not content:
            break
        yield RuleChunk(
            id="%s:%05d" % (document.id, index),
            parent_id=document.id,
            content=content,
            search_text=prefix + content,
            index=index,
        )
        if start + chunk_size >= len(document.content):
            break


def iter_chunks(
    documents: Iterable[RuleDocument],
    chunk_size: int,
    chunk_overlap: int,
) -> Iterator[RuleChunk]:
    for document in documents:
        yield from iter_document_chunks(document, chunk_size, chunk_overlap)
