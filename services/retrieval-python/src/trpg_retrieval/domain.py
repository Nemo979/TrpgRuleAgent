from dataclasses import asdict, dataclass, field
from typing import Any, Dict


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

    def to_json(self) -> Dict[str, Any]:
        document = self.document.to_json()
        document.pop("content")
        document.pop("priority")
        document["excerpt"] = self.excerpt
        document["score"] = round(self.score, 6)
        return document
