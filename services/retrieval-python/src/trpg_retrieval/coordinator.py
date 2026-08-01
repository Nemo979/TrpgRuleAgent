import re
import unicodedata
from typing import Iterable, List, Optional, Sequence

from .domain import RuleDocument, SearchHit
from .service import RuleRetriever


_QUERY_ALIASES = {
    "创建": ("建立",),
    "建立": ("创建",),
    "基本特性": ("基本特技",),
    "基本特技": ("基本特性",),
    "人物": ("角色",),
}
_RULE_CATEGORIES = (
    "弱点",
    "特技",
    "技能",
    "能力",
    "属性",
    "职业",
    "动作",
    "法术",
    "装备",
    "武器",
    "防具",
    "检定",
    "费用",
    "消耗",
    "条件",
    "效果",
)
_CHARACTER_CREATION_SUBTOPICS = (
    "属性",
    "能力值",
    "技能",
    "专长",
    "装备",
    "法术",
    "弱点",
    "特技",
    "生命值",
    "购点",
)


class RetrievalCoordinator:
    """Deterministic query expansion and structure-aware candidate reranking."""

    def __init__(
        self,
        base: RuleRetriever,
        candidate_limit: int = 50,
        heading_bonus: float = 0.012,
    ) -> None:
        self.base = base
        self.candidate_limit = candidate_limit
        self.heading_bonus = heading_bonus

    def search(
        self,
        query: str,
        documents: Iterable[RuleDocument],
        limit: int,
        source_ids: Optional[Sequence[str]] = None,
    ) -> List[SearchHit]:
        expanded = expand_query(query)
        retrieval_query = expanded if _is_character_creation_query(query) else query
        hits = self.base.search(
            retrieval_query,
            documents,
            max(limit, self.candidate_limit),
            source_ids,
        )
        scored = [
            (
                SearchHit(
                hit.document,
                hit.excerpt,
                    hit.score
                    + self.heading_bonus
                    * (
                        heading_match_score(expanded, hit.document)
                        + character_creation_heading_score(query, hit.document)
                    ),
                ),
                heading_match_score(expanded, hit.document),
            )
            for hit in hits
        ]
        scored.sort(key=lambda item: item[0].score, reverse=True)
        if _is_direct_lookup(query) and any(structure > 0 for _hit, structure in scored):
            reranked = [hit for hit, structure in scored if structure > 0]
        else:
            reranked = [hit for hit, _structure in scored]
        return reranked[:limit]


def expand_query(query: str) -> str:
    normalized = unicodedata.normalize("NFKC", query).strip()
    additions: list[str] = []
    for source, aliases in _QUERY_ALIASES.items():
        if source in normalized:
            additions.extend(alias for alias in aliases if alias not in normalized)
    return " ".join([normalized, *dict.fromkeys(additions)])


def heading_match_score(query: str, document: RuleDocument) -> float:
    compact_query = _compact(query)
    if not compact_query:
        return 0.0
    blocks = document.metadata.get("structuralBlocks")
    best = 0.0
    if isinstance(blocks, list):
        for block in blocks:
            if not isinstance(block, dict):
                continue
            path = block.get("headingPath")
            if not isinstance(path, list):
                continue
            matches = _matched_heading_units(compact_query, path)
            if not _has_entity_category_pair(matches):
                continue
            substance = min(1.0, len(str(block.get("content", ""))) / 160.0)
            best = max(best, len(matches) * substance)
        return min(best, 5.0)

    matches = _matched_heading_units(compact_query, _heading_components(document))
    return min(float(len(matches)), 5.0) if _has_entity_category_pair(matches) else 0.0


def character_creation_heading_score(query: str, document: RuleDocument) -> float:
    if not _is_character_creation_query(query):
        return 0.0
    return 2.0 if any(
        marker in _compact(component)
        for component in _heading_components(document)
        for marker in ("创建", "建立")
    ) else 0.0


def _matched_heading_units(compact_query: str, components: list[object]) -> set[str]:
    matches: set[str] = set()
    for component in components:
        compact_component = _compact(str(component))
        if not compact_component:
            continue
        if compact_component in compact_query:
            matches.add(compact_component)
            continue
        for size in (4, 3, 2):
            units = {
                compact_component[index:index + size]
                for index in range(max(0, len(compact_component) - size + 1))
                if compact_component[index:index + size] in compact_query
            }
            if units:
                matches.add(sorted(units)[0])
                break
    return matches


def _has_entity_category_pair(matches: set[str]) -> bool:
    categories = {
        match
        for match in matches
        if any(match in category or category in match for category in _RULE_CATEGORIES)
    }
    return bool(categories and matches - categories)


def _heading_components(document: RuleDocument) -> list[str]:
    values: list[str] = []
    blocks = document.metadata.get("structuralBlocks")
    if isinstance(blocks, list):
        for block in blocks:
            if not isinstance(block, dict):
                continue
            path = block.get("headingPath")
            if isinstance(path, list):
                values.extend(str(value) for value in path if value)
    path = document.metadata.get("headingPath")
    if isinstance(path, list):
        values.extend(str(value) for value in path if value)
    values.extend([document.title, *document.full_path.split(" > ")])
    return list(dict.fromkeys(values))


def _compact(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(re.findall(r"[a-z0-9\u4e00-\u9fff]+", normalized))


def _is_direct_lookup(query: str) -> bool:
    if re.search(r"(?:是否|能否|可否|为什么|为何|如何).*(?:与|和|同时|影响)", query):
        return False
    return bool(re.search(r"(?:有哪些|有什么|是什么|列出|多少|效果)", query))


def _is_character_creation_query(query: str) -> bool:
    return bool(
        re.search(r"(?:创建|建立)", query)
        and re.search(r"(?:角色|人物)", query)
        and not any(topic in query for topic in _CHARACTER_CREATION_SUBTOPICS)
    )
