from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping


_PARENTHETICAL_ALIAS = re.compile(r"\(([^()]*)\)")
_ALIAS_CHARACTERS = re.compile(r"[0-9a-z\u3400-\u9fff]")
_ASCII_ALPHANUMERIC = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
)
_GENERIC_ALIASES = {
    "basicrules",
    "corerules",
    "game",
    "gamerules",
    "rpg",
    "rules",
    "ruleset",
    "system",
    "trpg",
    "中文版",
    "中文规则",
    "中文规则库",
    "中文",
    "基础规则",
    "核心规则",
    "游戏规则",
    "游戏系统",
    "游戏",
    "规则库",
    "规则",
}


def find_explicit_other_library(
    message: str,
    *,
    current_library_id: str,
    libraries: Iterable[Mapping[str, object]],
) -> dict[str, object] | None:
    """Return a uniquely and explicitly named non-current published library."""

    normalized_message = _normalize(message)
    values = [dict(library) for library in libraries]
    alias_owners: dict[str, set[str]] = {}
    aliases_by_library: dict[str, set[str]] = {}

    for library in values:
        library_id = str(library.get("id", ""))
        aliases = _library_aliases(library)
        aliases_by_library[library_id] = aliases
        for alias in aliases:
            alias_owners.setdefault(alias, set()).add(library_id)

    current_aliases = aliases_by_library.get(current_library_id, set())
    if any(
        alias_owners[alias] == {current_library_id}
        and _contains_alias(normalized_message, alias)
        for alias in current_aliases
    ):
        return None

    matches: list[tuple[int, int, str, dict[str, object]]] = []
    for catalog_index, library in enumerate(values):
        library_id = str(library.get("id", ""))
        if library_id == current_library_id:
            continue
        for alias in aliases_by_library.get(library_id, set()):
            # A system label shared by multiple editions does not identify one
            # published rule library well enough to redirect the conversation.
            if alias_owners[alias] != {library_id}:
                continue
            if _contains_alias(normalized_message, alias):
                matches.append(
                    (_alias_length(alias), -catalog_index, alias, library)
                )

    if not matches:
        return None
    return max(matches, key=lambda value: (value[0], value[1], value[2]))[3]


def _library_aliases(library: Mapping[str, object]) -> set[str]:
    aliases: set[str] = set()
    for field in ("id", "name", "system"):
        value = _normalize(str(library.get(field, "")))
        if not value:
            continue
        candidates = {value}
        parenthetical_values = _PARENTHETICAL_ALIAS.findall(value)
        if parenthetical_values:
            candidates.update(parenthetical_values)
            candidates.add(_PARENTHETICAL_ALIAS.sub(" ", value))
        for candidate in candidates:
            alias = _normalize(candidate).strip(" -–—_/|,，:：;；")
            if _is_specific_alias(alias):
                aliases.add(alias)
    configured_aliases = library.get("aliases", [])
    if isinstance(configured_aliases, list):
        for value in configured_aliases:
            if not isinstance(value, str):
                continue
            alias = _normalize(value).strip(" -–—_/|,，:：;；")
            if _is_specific_alias(alias, minimum_length=3):
                aliases.add(alias)
    return aliases


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(normalized.split())


def _is_specific_alias(alias: str, minimum_length: int = 4) -> bool:
    compact = "".join(_ALIAS_CHARACTERS.findall(alias))
    if len(compact) < minimum_length:
        return False
    if compact in _GENERIC_ALIASES:
        return False
    without_version = re.sub(r"\d+(?:\d+)*$", "", compact)
    return without_version not in _GENERIC_ALIASES


def _alias_length(alias: str) -> int:
    return len(_ALIAS_CHARACTERS.findall(alias))


def _contains_alias(message: str, alias: str) -> bool:
    start = message.find(alias)
    while start >= 0:
        end = start + len(alias)
        left_ok = (
            not alias[0].isascii()
            or not alias[0].isalnum()
            or start == 0
            or message[start - 1] not in _ASCII_ALPHANUMERIC
        )
        right_ok = (
            not alias[-1].isascii()
            or not alias[-1].isalnum()
            or end == len(message)
            or message[end] not in _ASCII_ALPHANUMERIC
        )
        if left_ok and right_ok:
            return True
        start = message.find(alias, start + 1)
    return False
