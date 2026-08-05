from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class RuleDocument:
    id: str
    ruleset_id: str
    source_id: str
    source_title: str
    title: str
    full_path: str
    content: str
    version: str
    priority: int
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, value: Dict[str, Any]) -> "RuleDocument":
        return cls(
            id=value["id"],
            ruleset_id=value["rulesetId"],
            source_id=value["sourceId"],
            source_title=value["sourceTitle"],
            title=value["title"],
            full_path=value["fullPath"],
            content=value["content"],
            version=value["version"],
            priority=int(value.get("priority", 0)),
            metadata=dict(value.get("metadata", {})),
        )

    def to_json(self) -> Dict[str, Any]:
        value = asdict(self)
        return {
            "id": value["id"],
            "rulesetId": value["ruleset_id"],
            "sourceId": value["source_id"],
            "sourceTitle": value["source_title"],
            "title": value["title"],
            "fullPath": value["full_path"],
            "content": value["content"],
            "version": value["version"],
            "priority": value["priority"],
            "metadata": value["metadata"],
        }


@dataclass(frozen=True)
class SearchHit:
    document: RuleDocument
    excerpt: str
    score: float
    chunk_id: Optional[str] = None
    chunk_index: Optional[int] = None
    match_score: Optional[float] = None
    parent_score: Optional[float] = None

    def __post_init__(self) -> None:
        # Keep the historical SearchHit(document, excerpt, score) constructor
        # meaningful for vector, hybrid, and test doubles.
        if self.match_score is None:
            object.__setattr__(self, "match_score", self.score)
        if self.parent_score is None:
            object.__setattr__(self, "parent_score", self.score)

    @property
    def child_id(self) -> Optional[str]:
        """Compatibility alias for callers that call the hit chunk a child."""
        return self.chunk_id

    @property
    def chunk_position(self) -> Optional[int]:
        """Compatibility alias for the zero-based child chunk position."""
        return self.chunk_index

    def to_json(self) -> Dict[str, Any]:
        document = self.document.to_json()
        document.pop("content")
        document.pop("priority")
        document["excerpt"] = self.excerpt
        document["chunkId"] = self.chunk_id
        document["chunkIndex"] = self.chunk_index
        document["matchScore"] = round(self.match_score or 0.0, 6)
        document["score"] = round(self.score, 6)
        document["parentScore"] = round(self.parent_score or self.score, 6)
        return document
