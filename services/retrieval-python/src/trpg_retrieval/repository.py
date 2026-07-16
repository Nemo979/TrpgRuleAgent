import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .domain import RuleDocument


class RuleRepository:
    def __init__(self, documents: Iterable[RuleDocument]) -> None:
        self._documents = {document.id: document for document in documents}

    @classmethod
    def from_jsonl(cls, path: Path) -> "RuleRepository":
        documents = []
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    documents.append(RuleDocument.from_json(json.loads(line)))
                except (KeyError, TypeError, ValueError) as error:
                    raise ValueError("Invalid rule document at line %d: %s" % (line_number, error))
        return cls(documents)

    def all(self, ruleset_id: Optional[str] = None) -> List[RuleDocument]:
        documents = list(self._documents.values())
        if ruleset_id is not None:
            documents = [document for document in documents if document.ruleset_id == ruleset_id]
        return documents

    def read(self, ruleset_id: str, ids: Iterable[str]) -> List[RuleDocument]:
        result = []
        for document_id in ids:
            document = self._documents.get(document_id)
            if document is not None and document.ruleset_id == ruleset_id:
                result.append(document)
        return result

    def count(self) -> int:
        return len(self._documents)

    def rulesets(self) -> List[str]:
        return sorted({document.ruleset_id for document in self._documents.values()})

    def sources(self, ruleset_id: str) -> List[Dict[str, object]]:
        groups: Dict[str, List[RuleDocument]] = {}
        for document in self.all(ruleset_id):
            groups.setdefault(document.source_id, []).append(document)
        return [
            {
                "id": source_id,
                "title": documents[0].source_title,
                "rulesetId": ruleset_id,
                "version": documents[0].version,
                "priority": documents[0].priority,
                "documentCount": len(documents),
            }
            for source_id, documents in sorted(groups.items())
        ]
