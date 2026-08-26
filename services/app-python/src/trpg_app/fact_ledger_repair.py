"""Rule-system-neutral structured answer repair and deterministic rendering."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import re
import time
from typing import Any, Iterable

from .fact_ledger import (
    DraftClaimContract,
    DraftPathSpec,
    RepairPathMapping,
    ValidationIssue,
    ValidationSeverity,
)


ANSWER_DRAFT_SCHEMA_VERSION = 1
REPAIR_PATCH_SCHEMA_VERSION = 3
MAX_DRAFT_SECTIONS = 16
MAX_DRAFT_CLAIMS = 96
MAX_CLAIM_CHARACTERS = 4_000
MAX_STRUCTURED_OUTPUT_CHARACTERS = 256_000
_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_PATH = re.compile(r"^answer(?:\.[A-Za-z_][A-Za-z0-9_]*|\[[^\]\n]{1,80}\])+$")
_CITATION = re.compile(r"\[(?:\^)?(S\d+)]")
_TEMPLATE_FIELD = re.compile(r"\{([a-z][a-z0-9_]*)\}")
_ACTUAL_TEXT_TARGET_CODES = frozenset(
    {"bonus_feat_scope", "unsupported_named_option"}
)
_REPLACEMENT_PLACEHOLDER = "REPLACE_WITH_CORRECTED_CLAIM_VALUE"
_DRAFT_TEXT_PLACEHOLDER = "REPLACE_WITH_CLAIM_TEXT"
_DRAFT_HEADING_PLACEHOLDER = "REPLACE_WITH_SECTION_HEADING"
_VALIDATION_MARKER_TOKEN = "[[TRPGCLAIMPATH:"
_SERVER_REQUIRED_SECTION_HEADING = "规则结论"


class StructuredAnswerError(ValueError):
    """Raised when a draft or patch crosses the server-owned schema boundary."""


class RepairTargetError(StructuredAnswerError):
    """A privacy-safe classification of why validation cannot be patched."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class AnswerClaim:
    id: str
    path: str
    text: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _ID.fullmatch(self.id):
            raise StructuredAnswerError("claim id is invalid")
        if not _PATH.fullmatch(self.path):
            raise StructuredAnswerError("claim path is invalid")
        if not self.text.strip() or len(self.text) > MAX_CLAIM_CHARACTERS:
            raise StructuredAnswerError("claim text is empty or too large")
        if self.text == _DRAFT_TEXT_PLACEHOLDER:
            raise StructuredAnswerError("claim text still contains the template placeholder")
        if _VALIDATION_MARKER_TOKEN in self.text:
            raise StructuredAnswerError("claim text contains a reserved validation marker")
        _validate_refs(self.evidence_refs, "claim")

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "text": self.text,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class AnswerSection:
    id: str
    heading: str
    claims: tuple[AnswerClaim, ...]

    def __post_init__(self) -> None:
        if not _ID.fullmatch(self.id):
            raise StructuredAnswerError("section id is invalid")
        if not self.heading.strip() or "\n" in self.heading:
            raise StructuredAnswerError("section heading is invalid")
        if self.heading == _DRAFT_HEADING_PLACEHOLDER:
            raise StructuredAnswerError("section heading still contains the template placeholder")
        if _VALIDATION_MARKER_TOKEN in self.heading:
            raise StructuredAnswerError("section heading contains a reserved validation marker")
        if not isinstance(self.claims, tuple) or not self.claims:
            raise StructuredAnswerError("section claims must be a non-empty tuple")
        if any(not isinstance(claim, AnswerClaim) for claim in self.claims):
            raise StructuredAnswerError("section contains an invalid claim")

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "heading": self.heading,
            "claims": [claim.public() for claim in self.claims],
        }


@dataclass(frozen=True)
class AnswerDraft:
    sections: tuple[AnswerSection, ...]
    schema_version: int = ANSWER_DRAFT_SCHEMA_VERSION
    selection_fallback_count: int = 0

    def __post_init__(self) -> None:
        if self.schema_version != ANSWER_DRAFT_SCHEMA_VERSION:
            raise StructuredAnswerError("answer draft schema version is incompatible")
        if type(self.selection_fallback_count) is not int or self.selection_fallback_count < 0:
            raise StructuredAnswerError("answer draft selection fallback count is invalid")
        if not isinstance(self.sections, tuple) or not self.sections:
            raise StructuredAnswerError("answer draft sections must be a non-empty tuple")
        if len(self.sections) > MAX_DRAFT_SECTIONS:
            raise StructuredAnswerError("answer draft has too many sections")
        claims = self.claims
        if len(claims) > MAX_DRAFT_CLAIMS:
            raise StructuredAnswerError("answer draft has too many claims")
        section_ids = [section.id for section in self.sections]
        claim_ids = [claim.id for claim in claims]
        claim_paths = [claim.path for claim in claims]
        if len(set(section_ids)) != len(section_ids):
            raise StructuredAnswerError("section ids must be unique")
        if len(set(claim_ids)) != len(claim_ids):
            raise StructuredAnswerError("claim ids must be unique")
        if len(set(claim_paths)) != len(claim_paths):
            raise StructuredAnswerError("claim paths must be unique")

    @property
    def claims(self) -> tuple[AnswerClaim, ...]:
        return tuple(claim for section in self.sections for claim in section.claims)

    def public(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "sections": [section.public() for section in self.sections],
        }


@dataclass(frozen=True)
class RepairOperation:
    claim_id: str
    path: str
    replacement_text: str
    evidence_refs: tuple[str, ...]
    issue_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not _ID.fullmatch(self.claim_id) or not _PATH.fullmatch(self.path):
            raise StructuredAnswerError("repair target is invalid")
        if (
            not self.replacement_text.strip()
            or len(self.replacement_text) > MAX_CLAIM_CHARACTERS
        ):
            raise StructuredAnswerError("replacement text is empty or too large")
        if self.replacement_text == _REPLACEMENT_PLACEHOLDER:
            raise StructuredAnswerError(
                "replacement text still contains the template placeholder"
            )
        if _VALIDATION_MARKER_TOKEN in self.replacement_text:
            raise StructuredAnswerError(
                "replacement text contains a reserved validation marker"
            )
        _validate_refs(self.evidence_refs, "repair")
        if not isinstance(self.issue_codes, tuple) or not self.issue_codes:
            raise StructuredAnswerError("repair issue codes must be a non-empty tuple")
        if len(set(self.issue_codes)) != len(self.issue_codes):
            raise StructuredAnswerError("repair issue codes must be unique")
        if any(not _ID.fullmatch(value) for value in self.issue_codes):
            raise StructuredAnswerError("repair issue code is invalid")


@dataclass(frozen=True)
class RepairPatch:
    operations: tuple[RepairOperation, ...]
    schema_version: int = REPAIR_PATCH_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != REPAIR_PATCH_SCHEMA_VERSION:
            raise StructuredAnswerError("repair patch schema version is incompatible")
        if not isinstance(self.operations, tuple) or not self.operations:
            raise StructuredAnswerError("repair patch operations must be a non-empty tuple")
        targets = [(item.claim_id, item.path) for item in self.operations]
        if len(set(targets)) != len(targets):
            raise StructuredAnswerError("repair patch targets must be unique")


@dataclass(frozen=True)
class RepairTarget:
    claim_id: str
    path: str
    issue_codes: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    issue_indexes: tuple[int, ...]

    def public(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "path": self.path,
            "issue_codes": list(self.issue_codes),
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass
class RepairMetrics:
    draft_parse_seconds: float = 0.0
    repair_seconds: float = 0.0
    render_seconds: float = 0.0
    attempted: bool = False
    applied: bool = False
    patch_count: int = 0
    safe_refusal: bool = False
    failure_reason: str = ""
    target_failure_reason: str = ""
    draft_parse_failure_reason: str = ""
    repair_patch_failure_reason: str = ""
    draft_json_recovery_attempted: bool = False
    draft_json_recovery_applied: bool = False
    draft_contract_recovery_attempted: bool = False
    draft_contract_recovery_applied: bool = False
    repair_json_recovery_attempted: bool = False
    repair_json_recovery_applied: bool = False
    semantic_fallback_count: int = 0
    selection_fallback_count: int = 0
    issue_codes: tuple[str, ...] = ()
    residual_issue_codes: tuple[str, ...] = ()
    residual_path_templates: tuple[str, ...] = ()


def json_only_recovery_instruction(owner: str) -> str:
    """Request one serialization-only retry without changing semantic targets."""

    if owner not in {"draft", "repair"}:
        raise ValueError("JSON recovery owner is invalid")
    return (
        "上一条输出不是可解析的 JSON。只修正 JSON 序列化，不得改变任何值、选择或语义目标。"
        "重新输出上一条要求的唯一 JSON 对象；不得使用代码围栏、解释文字或多个对象。"
    )


def draft_contract_recovery_instruction(value_count: int) -> str:
    """Request one bounded retry of only the minimal required-values wire."""

    if type(value_count) is not int or value_count < 0:
        raise ValueError("draft recovery value count is invalid")
    return (
        "上一条输出不符合服务器的 required values 契约。重新执行同一契约，"
        f"只返回一个严格 JSON 对象 {{\"values\":[...]}}，values 必须恰好有{value_count}项，"
        "并保持原契约给出的顺序、文本/数组类型、exact_count 与 catalog 限制。"
        "不得输出 required_output 包装、代码围栏、解释、标题、claim、path、引用或额外字段。"
    )


def _validate_refs(values: tuple[str, ...], owner: str) -> None:
    if not isinstance(values, tuple):
        raise StructuredAnswerError(f"{owner} evidence refs must be a tuple")
    if len(set(values)) != len(values):
        raise StructuredAnswerError(f"{owner} evidence refs must be unique")
    if any(not re.fullmatch(r"S\d+", value) for value in values):
        raise StructuredAnswerError(f"{owner} evidence ref is invalid")


def _strict_object(value: Any, keys: set[str], owner: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise StructuredAnswerError(f"{owner} fields are invalid")
    return value


def _minimal_values_object(value: Any, owner: str) -> dict[str, Any]:
    """Accept the exact wire object or one provider-echoed output wrapper."""

    if isinstance(value, dict) and set(value) == {"required_output"}:
        value = value["required_output"]
    return _strict_object(value, {"values"}, owner)


def _json_payload(raw: str) -> Any:
    value = raw.strip()
    if len(value) > MAX_STRUCTURED_OUTPUT_CHARACTERS:
        raise StructuredAnswerError("structured answer is too large")
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", value, re.DOTALL | re.IGNORECASE)
    if fenced:
        value = fenced.group(1)
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError) as error:
        # Some providers wrap an otherwise valid single JSON object in a short
        # prose prefix/suffix. Recover only one top-level object whose exterior
        # contains no competing JSON container; multiple/ambiguous values remain
        # a hard failure.
        decoder = json.JSONDecoder()
        for start, character in enumerate(value):
            if character != "{":
                continue
            try:
                candidate, length = decoder.raw_decode(value[start:])
            except json.JSONDecodeError:
                continue
            end = start + length
            exterior = value[:start] + value[end:]
            if isinstance(candidate, dict) and not re.search(r"[{}\[\]]", exterior):
                return candidate
        raise StructuredAnswerError("structured answer is not valid JSON") from error


def _path_template_pattern(template: str) -> re.Pattern[str]:
    fields = _TEMPLATE_FIELD.findall(template)
    if len(fields) != len(set(fields)):
        raise StructuredAnswerError("draft path template fields must be unique")
    cursor = 0
    parts: list[str] = []
    for match in _TEMPLATE_FIELD.finditer(template):
        parts.append(re.escape(template[cursor : match.start()]))
        parts.append(f"(?P<{match.group(1)}>[^\\]\\n]{{1,80}})")
        cursor = match.end()
    parts.append(re.escape(template[cursor:]))
    return re.compile("^" + "".join(parts) + "$")


def canonicalize_draft_path(path: str, specs: Iterable[DraftPathSpec]) -> str:
    available = tuple(specs)
    if not available:
        return path
    for spec in available:
        canonical_fields = set(_TEMPLATE_FIELD.findall(spec.canonical_template))
        for template in (spec.canonical_template, *spec.alias_templates):
            if set(_TEMPLATE_FIELD.findall(template)) != canonical_fields:
                raise StructuredAnswerError("draft path alias fields are incompatible")
            match = _path_template_pattern(template).fullmatch(path)
            if match:
                canonical = spec.canonical_template.format(**match.groupdict())
                if not _PATH.fullmatch(canonical):
                    raise StructuredAnswerError("canonical draft path is invalid")
                return canonical
    raise StructuredAnswerError("claim path is outside the adapter contract")


def canonicalize_draft_path_template(
    template: str,
    specs: Iterable[DraftPathSpec],
) -> str:
    """Canonicalize an adapter template without inventing parameter values."""

    fields = tuple(_TEMPLATE_FIELD.findall(template))
    sentinels = {field: f"value_{index}" for index, field in enumerate(fields, 1)}
    concrete = template.format(**sentinels)
    canonical = canonicalize_draft_path(concrete, specs)
    for field, sentinel in sentinels.items():
        canonical = canonical.replace(sentinel, "{" + field + "}")
    return canonical


def classify_draft_path_template(
    path: str,
    specs: Iterable[DraftPathSpec],
) -> str:
    """Return a bounded canonical template without exposing path parameters."""

    if not path:
        return "missing"
    for spec in specs:
        for template in (spec.canonical_template, *spec.alias_templates):
            if _path_template_pattern(template).fullmatch(path):
                return spec.canonical_template
    return "unmatched"


def _deduplicated_id(value: str, used: set[str]) -> str:
    if not _ID.fullmatch(value):
        raise StructuredAnswerError("structured answer id is invalid")
    if value not in used:
        used.add(value)
        return value
    for index in range(2, MAX_DRAFT_CLAIMS + MAX_DRAFT_SECTIONS + 1):
        suffix = f"_{index}"
        candidate = value[: 64 - len(suffix)] + suffix
        if candidate not in used:
            used.add(candidate)
            return candidate
    raise StructuredAnswerError("structured answer has too many duplicate ids")


def _server_required_draft(
    payload: Any,
    required: tuple[str, ...],
    contracts: dict[str, DraftClaimContract],
) -> AnswerDraft:
    """Build the complete draft from one model-owned ordered values array."""

    value_object = _minimal_values_object(payload, "required values")
    raw_values = value_object["values"]
    if not isinstance(raw_values, list):
        raise StructuredAnswerError("required values must be a list")
    model_paths = tuple(path for path in required if not contracts[path].server_text)
    if len(raw_values) != len(model_paths):
        raise StructuredAnswerError("required values count is invalid")
    model_values = dict(zip(model_paths, raw_values))
    claims: list[AnswerClaim] = []
    selection_fallback_count = 0
    for index, path in enumerate(required, 1):
        contract = contracts[path]
        text_value, refs, selection_fallback = _contract_text_and_refs(
            contract,
            None if contract.server_text else model_values[path],
            allow_selection_fallback=True,
        )
        selection_fallback_count += int(selection_fallback)
        claims.append(
            AnswerClaim(
                id=f"required_{index}",
                path=path,
                text=text_value,
                evidence_refs=refs,
            )
        )
    return AnswerDraft(
        (
            AnswerSection(
                id="required_section",
                heading=_SERVER_REQUIRED_SECTION_HEADING,
                claims=tuple(claims),
            ),
        ),
        selection_fallback_count=selection_fallback_count,
    )


def _contract_text_and_refs(
    contract: DraftClaimContract,
    raw_value: Any,
    extra_refs: Iterable[str] = (),
    *,
    allow_selection_fallback: bool = False,
) -> tuple[str, tuple[str, ...], bool]:
    """Render one model value through a server-owned claim contract."""

    refs = [*contract.evidence_refs, *extra_refs]
    selection_fallback = False
    if contract.server_text:
        if raw_value is not None:
            raise StructuredAnswerError("server claim value must be null")
        text_value = contract.server_text
    elif contract.selection_count:
        raw_strings = (
            tuple(raw_value)
            if isinstance(raw_value, list)
            and all(isinstance(value, str) for value in raw_value)
            else ()
        )
        options = {item.value: item for item in contract.selection_options}
        selection_is_valid = (
            len(raw_strings) == contract.selection_count
            and len(set(raw_strings)) == len(raw_strings)
            and not (set(raw_strings) - set(options))
        )
        ordered_group_invalid = False
        if selection_is_valid and contract.selection_groups:
            offset = 0
            for group in contract.selection_groups:
                selected = raw_strings[offset : offset + group.count]
                if any(value not in group.option_values for value in selected):
                    selection_is_valid = False
                    ordered_group_invalid = True
                    break
                offset += group.count
        if not selection_is_valid:
            if not (
                allow_selection_fallback
                and contract.allow_selection_fallback
                and contract.selection_groups
            ):
                if not raw_strings:
                    raise StructuredAnswerError("claim selection must be a list of strings")
                if len(raw_strings) != contract.selection_count:
                    raise StructuredAnswerError("claim selection count is invalid")
                if len(set(raw_strings)) != len(raw_strings):
                    raise StructuredAnswerError("claim selections must be unique")
                if ordered_group_invalid:
                    raise StructuredAnswerError(
                        "claim selection is outside its ordered group contract"
                    )
                raise StructuredAnswerError("claim selection is outside the adapter contract")
            normalized: list[str] = []
            for group in contract.selection_groups:
                selected = [
                    value
                    for value in raw_strings
                    if value in group.option_values and value not in normalized
                ][: group.count]
                selected.extend(
                    value
                    for value in group.option_values
                    if value not in normalized and value not in selected
                )
                selected = selected[: group.count]
                if len(selected) != group.count:
                    raise StructuredAnswerError("selection fallback domain is insufficient")
                normalized.extend(selected)
            raw_strings = tuple(normalized)
            selection_fallback = True
        values = raw_strings
        if contract.selection_groups:
            offset = 0
            rendered_groups: list[str] = []
            for group in contract.selection_groups:
                selected = values[offset : offset + group.count]
                rendered_groups.append(f"{group.label}：{'、'.join(selected)}")
                offset += group.count
            rendered_values = "；".join(rendered_groups)
        else:
            rendered_values = "、".join(values)
        for value in values:
            refs.extend(options[value].evidence_refs)
        text_value = contract.text_template.format(values=rendered_values)
    else:
        if not isinstance(raw_value, str):
            raise StructuredAnswerError("claim text must be a string")
        # Citations are a server-owned rendering concern. Removing only the
        # bounded S-label syntax prevents model citation mistakes from
        # changing provenance or rejecting an otherwise valid value.
        unscoped = _CITATION.sub("", raw_value).strip()
        clauses = tuple(
            clause.strip()
            for clause in re.split(r"(?<=[。！？!?；;，,])|\n+", unscoped)
            if clause.strip()
        )
        if contract.forbidden_terms:
            clauses = tuple(
                clause
                for clause in clauses
                if not any(term in clause for term in contract.forbidden_terms)
            )
        text_value = "".join(clauses).strip(" \t\r\n，,；;")
        semantic_invalid = not text_value or any(
            not any(term in text_value for term in group)
            for group in contract.required_term_groups
        )
        if semantic_invalid:
            if contract.semantic_fallback_text:
                text_value = contract.semantic_fallback_text
            else:
                raise StructuredAnswerError("claim text violates its semantic scope")
    return text_value, tuple(dict.fromkeys(refs)), selection_fallback


def parse_answer_draft(
    raw: str,
    registered_refs: Iterable[str],
    path_specs: Iterable[DraftPathSpec] = (),
    required_paths: Iterable[str] = (),
    claim_contracts: Iterable[DraftClaimContract] = (),
) -> AnswerDraft:
    allowed = set(registered_refs)
    specs = tuple(path_specs)
    required = tuple(required_paths)
    required_by_id = {
        f"required_{index}": path for index, path in enumerate(required, 1)
    }
    contracts = {item.path: item for item in claim_contracts}
    if contracts and tuple(contracts) != required:
        raise StructuredAnswerError("claim contracts do not cover required paths")
    payload = _json_payload(raw)
    if required and contracts:
        return _server_required_draft(payload, required, contracts)
    payload = _strict_object(
        payload,
        {"schema_version", "sections"},
        "answer draft",
    )
    if type(payload["schema_version"]) is not int:
        raise StructuredAnswerError("answer draft schema version must be an integer")
    if not isinstance(payload["sections"], list):
        raise StructuredAnswerError("answer draft sections must be a list")
    sections: list[AnswerSection] = []
    section_ids: set[str] = set()
    claim_ids: set[str] = set()
    for raw_section in payload["sections"]:
        section = _strict_object(raw_section, {"id", "heading", "claims"}, "section")
        if not isinstance(section["id"], str) or not isinstance(section["heading"], str):
            raise StructuredAnswerError("section scalar fields are invalid")
        if not isinstance(section["claims"], list):
            raise StructuredAnswerError("section claims must be a list")
        section_id = _deduplicated_id(section["id"], section_ids)
        claims: list[AnswerClaim] = []
        for raw_claim in section["claims"]:
            claim = _strict_object(
                raw_claim,
                {"id", "path", "text", "evidence_refs"},
                "claim",
            )
            if not isinstance(claim["evidence_refs"], list):
                raise StructuredAnswerError("claim evidence refs must be a list")
            if not all(isinstance(claim[field], str) for field in ("id", "path")):
                raise StructuredAnswerError("claim scalar fields are invalid")
            required_path = required_by_id.get(claim["id"])
            contract = contracts.get(required_path or "")
            if contract is not None:
                refs = list(contract.evidence_refs)
                if contract.selection_count:
                    if not isinstance(claim["text"], list) or not all(
                        isinstance(value, str) for value in claim["text"]
                    ):
                        raise StructuredAnswerError("claim selection must be a list of strings")
                    values = tuple(claim["text"])
                    if len(values) != contract.selection_count:
                        raise StructuredAnswerError("claim selection count is invalid")
                    if len(set(values)) != len(values):
                        raise StructuredAnswerError("claim selections must be unique")
                    options = {item.value: item for item in contract.selection_options}
                    if set(values) - set(options):
                        raise StructuredAnswerError("claim selection is outside the adapter contract")
                    for value in values:
                        refs.extend(options[value].evidence_refs)
                    text_value = contract.text_template.format(values="、".join(values))
                else:
                    if not isinstance(claim["text"], str):
                        raise StructuredAnswerError("claim text must be a string")
                    text_value = claim["text"]
                refs = list(dict.fromkeys(refs))
            else:
                if not isinstance(claim["text"], str):
                    raise StructuredAnswerError("claim text must be a string")
                if not all(isinstance(value, str) for value in claim["evidence_refs"]):
                    raise StructuredAnswerError("claim evidence refs must contain strings")
                refs = list(claim["evidence_refs"])
                if set(refs) - allowed:
                    raise StructuredAnswerError("claim references unregistered evidence")
                if required_path and not refs:
                    raise StructuredAnswerError("required claim evidence is missing")
                text_value = claim["text"]
            embedded = set(_CITATION.findall(text_value))
            if embedded - set(refs):
                raise StructuredAnswerError("claim text contains an undeclared citation")
            claims.append(
                AnswerClaim(
                    id=_deduplicated_id(claim["id"], claim_ids),
                    path=(
                        required_path
                        if required_path is not None
                        else canonicalize_draft_path(claim["path"], specs)
                    ),
                    text=text_value,
                    evidence_refs=tuple(refs),
                )
            )
        sections.append(
            AnswerSection(
                id=section_id,
                heading=section["heading"],
                claims=tuple(claims),
            )
        )
    draft = AnswerDraft(tuple(sections), schema_version=int(payload["schema_version"]))
    missing = set(required) - {claim.path for claim in draft.claims}
    if missing:
        raise StructuredAnswerError("answer draft is missing a required path")
    return draft


def classify_draft_parse_failure(error: StructuredAnswerError) -> str:
    """Map parser errors to a bounded, privacy-safe metric dimension."""

    message = str(error)
    if "not valid JSON" in message:
        return "invalid_json"
    if "too large" in message or "too many" in message:
        return "size_limit"
    if "schema version" in message:
        return "schema_version"
    if "required values fields" in message:
        return "values_fields"
    if "required values count" in message:
        return "values_count"
    if "required values must be a list" in message:
        return "values_shape"
    if "claim selection" in message or "adapter contract" in message:
        return "invalid_selection"
    if "semantic scope" in message:
        return "semantic_scope"
    if "fields are invalid" in message or "must be a list" in message:
        return "schema_fields"
    if "id is invalid" in message or "scalar fields are invalid" in message:
        return "invalid_identifier"
    if "evidence" in message or "citation" in message:
        return "invalid_evidence_ref"
    if "semantic scope" in message:
        return "semantic_scope"
    if "reserved validation marker" in message:
        return "invalid_value"
    if "claim paths must be unique" in message:
        return "duplicate_path"
    if "ids must be unique" in message or "duplicate ids" in message:
        return "duplicate_identifier"
    if "path" in message:
        return "missing_required_path" if "missing a required" in message else "invalid_path"
    return "other"


def render_answer_draft(draft: AnswerDraft) -> str:
    rendered: list[str] = []
    for section in draft.sections:
        heading = re.sub(r"^#+\s*", "", section.heading).strip()
        rendered.append(f"## {heading}")
        for claim in section.claims:
            text = _CITATION.sub("", claim.text).strip()
            suffix = "".join(f"[{label}]" for label in claim.evidence_refs)
            rendered.append(text + (f" {suffix}" if suffix else ""))
    return "\n\n".join(rendered).strip()


def render_answer_draft_for_validation(draft: AnswerDraft) -> str:
    """Expose claim paths only to validators, never to the published answer."""

    rendered: list[str] = []
    for section in draft.sections:
        for claim in section.claims:
            text = _CITATION.sub("", claim.text).strip()
            suffix = "".join(f"[{label}]" for label in claim.evidence_refs)
            rendered.append(
                f"[[TRPGCLAIMPATH:{claim.path}]]\n"
                + text
                + (f" {suffix}" if suffix else "")
            )
    return "\n\n".join(rendered).strip()


def build_repair_targets(
    draft: AnswerDraft,
    issues: Iterable[ValidationIssue],
    path_mappings: Iterable[RepairPathMapping] = (),
    claim_contracts: Iterable[DraftClaimContract] = (),
) -> tuple[RepairTarget, ...]:
    issue_values = tuple(issues)
    claims_by_path = {claim.path: claim for claim in draft.claims}
    mappings = tuple(path_mappings)
    contracts = {item.path: item for item in claim_contracts}
    grouped_codes: dict[tuple[str, str], list[str]] = {}
    grouped_refs: dict[tuple[str, str], list[str]] = {}
    grouped_indexes: dict[tuple[str, str], list[int]] = {}
    for issue_index, issue in enumerate(issue_values):
        if (
            issue.severity is not ValidationSeverity.ERROR
        ):
            raise RepairTargetError("non_error", "validation issue is not an error")
        if not issue.repairable:
            raise RepairTargetError(
                "nonrepairable", "validation issue is explicitly nonrepairable"
            )
        if not issue.path:
            raise RepairTargetError("missing_path", "validation issue has no path")
        claim = claims_by_path.get(issue.path)
        if (
            claim is None
            and issue.code in _ACTUAL_TEXT_TARGET_CODES
            and isinstance(issue.actual, str)
            and issue.actual.strip()
        ):
            actual = issue.actual.strip()
            text_matches = tuple(
                candidate for candidate in draft.claims if actual in candidate.text
            )
            if len(text_matches) == 1:
                claim = text_matches[0]
            elif len(text_matches) > 1:
                raise RepairTargetError(
                    "path_ambiguous",
                    "validation issue matches more than one draft claim",
                )
        if claim is None:
            mapped_claims: dict[tuple[str, str], AnswerClaim] = {}
            for mapping in mappings:
                if issue.code not in mapping.issue_codes:
                    continue
                match = _path_template_pattern(mapping.issue_path_template).fullmatch(
                    issue.path
                )
                if match is None:
                    continue
                target_path = mapping.target_path_template.format(**match.groupdict())
                candidate = claims_by_path.get(target_path)
                if candidate is not None:
                    mapped_claims[(candidate.id, candidate.path)] = candidate
            if len(mapped_claims) == 1:
                claim = next(iter(mapped_claims.values()))
            elif len(mapped_claims) > 1:
                raise RepairTargetError(
                    "path_ambiguous",
                    "validation issue maps to more than one draft claim",
                )
        if claim is None:
            structural_matches = tuple(
                candidate
                for candidate in draft.claims
                if candidate.path.startswith(issue.path + ".")
                or candidate.path.startswith(issue.path + "[")
                or issue.path.startswith(candidate.path + ".")
                or issue.path.startswith(candidate.path + "[")
            )
            if len(structural_matches) == 1:
                claim = structural_matches[0]
            elif len(structural_matches) > 1:
                raise RepairTargetError(
                    "path_ambiguous",
                    "validation issue path matches more than one draft claim",
                )
        if claim is None:
            raise RepairTargetError(
                "path_not_found", "validation issue path has no exact draft claim"
            )
        if contracts.get(claim.path) is not None and contracts[claim.path].server_text:
            raise RepairTargetError(
                "server_owned", "validation issue targets a server-owned claim"
            )
        key = (claim.id, claim.path)
        grouped_codes.setdefault(key, []).append(issue.code)
        grouped_refs.setdefault(key, []).extend(
            [*claim.evidence_refs, *issue.evidence_refs]
        )
        grouped_indexes.setdefault(key, []).append(issue_index)
    return tuple(
        RepairTarget(
            claim_id,
            path,
            tuple(dict.fromkeys(codes)),
            tuple(dict.fromkeys(grouped_refs[(claim_id, path)])),
            tuple(grouped_indexes[(claim_id, path)]),
        )
        for (claim_id, path), codes in grouped_codes.items()
    )


def parse_repair_patch(
    raw: str,
    registered_refs: Iterable[str],
    targets: tuple[RepairTarget, ...],
    claim_contracts: Iterable[DraftClaimContract] = (),
) -> RepairPatch:
    allowed = set(registered_refs)
    payload = _minimal_values_object(_json_payload(raw), "repair patch")
    raw_values = payload["values"]
    if not isinstance(raw_values, list):
        raise StructuredAnswerError("repair values must be a list")
    if len(raw_values) != len(targets):
        raise StructuredAnswerError("repair values do not exactly cover targets")
    contracts = {item.path: item for item in claim_contracts}
    operations: list[RepairOperation] = []
    for target, raw_value in zip(targets, raw_values):
        contract = contracts.get(target.path)
        if contract is None:
            if not isinstance(raw_value, str):
                raise StructuredAnswerError("repair text value must be a string")
            replacement_text = _CITATION.sub("", raw_value).strip()
            refs = target.evidence_refs
        elif contract.server_text:
            raise StructuredAnswerError("server-owned repair target is invalid")
        else:
            replacement_text, refs, _selection_fallback = _contract_text_and_refs(
                contract,
                raw_value,
                target.evidence_refs,
            )
        if set(refs) - allowed:
            raise StructuredAnswerError("repair references unregistered evidence")
        embedded = set(_CITATION.findall(replacement_text))
        if embedded - set(refs):
            raise StructuredAnswerError("repair text contains an undeclared citation")
        operations.append(
            RepairOperation(
                claim_id=target.claim_id,
                path=target.path,
                replacement_text=replacement_text,
                evidence_refs=refs,
                issue_codes=target.issue_codes,
            )
        )
    return RepairPatch(tuple(operations), schema_version=REPAIR_PATCH_SCHEMA_VERSION)


def classify_repair_patch_failure(error: StructuredAnswerError) -> str:
    """Map invalid patch shapes to bounded dimensions without recording content."""

    message = str(error)
    if "not valid JSON" in message:
        return "invalid_json"
    if "fields are invalid" in message:
        return "invalid_fields"
    if "must be a list" in message or "arrays are invalid" in message:
        return "invalid_shape"
    if "exactly cover" in message:
        return "target_mismatch"
    if "targets must be unique" in message:
        return "duplicate_target"
    if "evidence" in message or "citation" in message:
        return "invalid_evidence_ref"
    if (
        "invalid" in message
        or "empty" in message
        or "too large" in message
        or "reserved validation marker" in message
    ):
        return "invalid_value"
    return "other"


def apply_repair_patch(
    draft: AnswerDraft,
    patch: RepairPatch,
    targets: tuple[RepairTarget, ...],
    claim_contracts: Iterable[DraftClaimContract] = (),
) -> AnswerDraft:
    expected = {
        (target.claim_id, target.path): target for target in targets
    }
    actual = {(item.claim_id, item.path): item for item in patch.operations}
    if set(actual) != set(expected):
        raise StructuredAnswerError("repair patch does not exactly cover failed targets")
    contracts = {item.path: item for item in claim_contracts}
    for key, operation in actual.items():
        target = expected[key]
        if set(operation.issue_codes) != set(target.issue_codes):
            raise StructuredAnswerError("repair patch does not exactly cover failed targets")
        contract = contracts.get(target.path)
        if contract is None:
            allowed_refs = set(target.evidence_refs)
        else:
            allowed_refs = {
                *target.evidence_refs,
                *contract.evidence_refs,
                *(
                    ref
                    for option in contract.selection_options
                    for ref in option.evidence_refs
                ),
            }
        if not set(target.evidence_refs).issubset(operation.evidence_refs) or not set(
            operation.evidence_refs
        ).issubset(allowed_refs):
            raise StructuredAnswerError("repair patch references are outside its contract")
    replacements = {(item.claim_id, item.path): item for item in patch.operations}
    sections: list[AnswerSection] = []
    for section in draft.sections:
        claims: list[AnswerClaim] = []
        for claim in section.claims:
            operation = replacements.get((claim.id, claim.path))
            claims.append(
                claim
                if operation is None
                else replace(
                    claim,
                    text=operation.replacement_text,
                    evidence_refs=operation.evidence_refs,
                )
            )
        sections.append(replace(section, claims=tuple(claims)))
    return AnswerDraft(tuple(sections))


def structured_draft_instruction(
    path_specs: Iterable[DraftPathSpec] = (),
    required_paths: Iterable[str] = (),
    registered_refs: Iterable[str] = (),
    claim_contracts: Iterable[DraftClaimContract] = (),
) -> str:
    specs = tuple(path_specs)
    contract = [
        {
            "canonical": spec.canonical_template,
            "aliases": list(spec.alias_templates),
        }
        for spec in specs
    ]
    suffix = (
        " claim path 只能从以下服务器路径模板中选择；花括号参数替换为答案中的实际键，"
        "别名会由服务器规范化，优先使用 canonical："
        + json.dumps(contract, ensure_ascii=False, separators=(",", ":"))
        if contract
        else ""
    )
    required = tuple(required_paths)
    refs = tuple(registered_refs)
    contracts = {item.path: item for item in claim_contracts}
    if required and contracts:
        selection_catalogs: dict[tuple[str, ...], str] = {}
        value_contracts: list[dict[str, Any]] = []
        output_values: list[Any] = []
        model_paths = tuple(
            path for path in required if not contracts[path].server_text
        )
        for index, path in enumerate(model_paths):
            claim_contract = contracts[path]
            if claim_contract.selection_count:
                values = tuple(
                    item.value for item in claim_contract.selection_options
                )
                catalog = selection_catalogs.setdefault(
                    values,
                    f"catalog_{len(selection_catalogs) + 1}",
                )
                group_rules = []
                for group in claim_contract.selection_groups:
                    group_catalog = selection_catalogs.setdefault(
                        group.option_values,
                        f"catalog_{len(selection_catalogs) + 1}",
                    )
                    group_rules.append(
                        {
                            "label": group.label,
                            "exact_count": group.count,
                            "catalog": group_catalog,
                        }
                    )
                value_contracts.append(
                    {
                        "index": index,
                        "topic": path,
                        "kind": "selection",
                        "exact_count": claim_contract.selection_count,
                        "catalog": catalog,
                        "instruction": claim_contract.value_description,
                        "groups": group_rules,
                    }
                )
                output_values.append([])
            else:
                value_contracts.append(
                    {
                        "index": index,
                        "topic": path,
                        "kind": "text",
                        "instruction": claim_contract.value_description,
                    }
                )
                output_values.append("WRITE_TEXT")
        output = {"values": output_values}
        catalogs = {
            catalog: list(values) for values, catalog in selection_catalogs.items()
        }
        return (
            "只返回一个严格 JSON 对象，不得使用代码围栏或输出其他文字。对象只能有 values 一个字段；"
            "values 必须保持给定长度和顺序。服务器拥有的 claim 已从 values 中完全移除，不得补写；"
            "text 项必须严格遵守 instruction，只写该 topic 的不含引用标签正文；"
            "selection 项写成恰好 exact_count 个不同 catalog 值组成的数组。不要返回标题、section、claim、id、path、"
            "evidence_refs、Schema 或契约。服务器会构造全部结构、正文模板和证据。\n"
            "输出形状："
            + json.dumps(output, ensure_ascii=False, separators=(",", ":"))
            + "\n值规则："
            + json.dumps(value_contracts, ensure_ascii=False, separators=(",", ":"))
            + "\n选项目录："
            + json.dumps(catalogs, ensure_ascii=False, separators=(",", ":"))
        )
    if required:
        selection_catalogs: dict[tuple[str, ...], str] = {}
        for path in required:
            contract = contracts.get(path)
            if contract is None or not contract.selection_count:
                continue
            values = tuple(item.value for item in contract.selection_options)
            selection_catalogs.setdefault(values, f"catalog_{len(selection_catalogs) + 1}")
        required_output = {
            "schema_version": ANSWER_DRAFT_SCHEMA_VERSION,
            "sections": [
                {
                    "id": "required_section",
                    "heading": _DRAFT_HEADING_PLACEHOLDER,
                    "claims": [
                        {
                            "id": f"required_{index}",
                            "path": path,
                            "text": (
                                []
                                if contracts.get(path) is not None
                                and contracts[path].selection_count
                                else _DRAFT_TEXT_PLACEHOLDER
                            ),
                            "evidence_refs": [],
                        }
                        for index, path in enumerate(required, 1)
                    ],
                }
            ],
        }
        payload = {
            "required_output": required_output,
            "selection_contracts": {
                f"required_{index}": {
                    "exact_count": contracts[path].selection_count,
                    "allowed_values_catalog": selection_catalogs[
                        tuple(item.value for item in contracts[path].selection_options)
                    ],
                }
                for index, path in enumerate(required, 1)
                if path in contracts and contracts[path].selection_count
            },
            "selection_catalogs": {
                catalog: list(values)
                for values, catalog in selection_catalogs.items()
            },
        }
        return (
            "只返回 required_output 对象本身，不得使用代码围栏或输出其他文字。"
            "完整复制其中的 section、claim、id、path、字段和顺序；不得增加、删除或合并 claim。"
            "只替换 REPLACE_WITH_SECTION_HEADING 和每个文本占位符；selection_contracts 中列出的 "
            "claim 必须把 text 填为恰好 exact_count 个对应 allowed_values_catalog 值组成的 JSON 数组。"
            "所有 evidence_refs 保持空数组，证据由服务器绑定。服务器会按 required_N id 强制绑定 "
            "canonical path，仍会拒绝缺失 claim、越界选择或不合 Schema 的输出。\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )
    example = {
        "schema_version": ANSWER_DRAFT_SCHEMA_VERSION,
        "sections": [
            {
                "id": "section_id",
                "heading": "章节标题",
                "claims": [
                    {
                        "id": "claim_id",
                        "path": "answer.summary[replace_me]",
                        "text": "规则结论",
                        "evidence_refs": ["S1"],
                    }
                ],
            }
        ],
    }
    return (
        "最终输出必须是严格 JSON，不得使用代码围栏或输出 JSON 之外的文字。"
        "以下仅为合法 JSON 形状示例，必须替换占位值："
        + json.dumps(example, ensure_ascii=False, separators=(",", ":"))
        + "。"
        "section/claim id 使用小写字母数字与下划线；每个 claim path 必须是唯一稳定语义路径，"
        "格式为 answer.<domain>[<key>].<field>。每条规则结论单独一个 claim；text 不要手写引用，"
        "引用只放 evidence_refs，且只能使用已注册的 S 标签。"
        + suffix
    )


def repair_instruction(
    draft: AnswerDraft,
    issues: tuple[ValidationIssue, ...],
    targets: tuple[RepairTarget, ...],
    claim_contracts: Iterable[DraftClaimContract] = (),
) -> str:
    claims_by_target = {(claim.id, claim.path): claim for claim in draft.claims}
    contracts = {item.path: item for item in claim_contracts}
    selection_catalogs: dict[tuple[str, ...], str] = {}
    target_payload = []
    value_contracts = []
    values = []
    for index, target in enumerate(targets):
        key = (target.claim_id, target.path)
        target_issues = [issues[index] for index in target.issue_indexes]
        claim = claims_by_target[key]
        contract = contracts.get(target.path)
        if contract is not None and contract.server_text:
            raise RepairTargetError(
                "server_owned", "server-owned claim cannot enter model repair"
            )
        if contract is not None and contract.selection_count:
            option_values = tuple(item.value for item in contract.selection_options)
            catalog = selection_catalogs.setdefault(
                option_values,
                f"catalog_{len(selection_catalogs) + 1}",
            )
            group_rules = []
            for group in contract.selection_groups:
                group_catalog = selection_catalogs.setdefault(
                    group.option_values,
                    f"catalog_{len(selection_catalogs) + 1}",
                )
                group_rules.append(
                    {
                        "label": group.label,
                        "exact_count": group.count,
                        "catalog": group_catalog,
                    }
                )
            kind = "selection"
            output_value = []
            value_rule = {
                "index": index,
                "kind": kind,
                "exact_count": contract.selection_count,
                "catalog": catalog,
                "instruction": contract.value_description,
                "groups": group_rules,
            }
        else:
            kind = "text"
            output_value = _REPLACEMENT_PLACEHOLDER
            value_rule = {
                "index": index,
                "kind": kind,
                "instruction": contract.value_description if contract else "",
            }
        target_payload.append(
            {
                "claim_id": target.claim_id,
                "path": target.path,
                "current_text": claim.text,
                "issues": [
                    {
                        "code": issue.code,
                        "message": issue.message,
                        "expected": issue.expected,
                        "actual": issue.actual,
                        "evidence_refs": list(issue.evidence_refs),
                    }
                    for issue in target_issues
                ],
            }
        )
        value_contracts.append(value_rule)
        values.append(output_value)
    payload = {
        "target_contexts": target_payload,
        "value_contracts": value_contracts,
        "selection_catalogs": {
            catalog: list(option_values)
            for option_values, catalog in selection_catalogs.items()
        },
        "required_output": {"values": values},
    }
    return (
        "只返回 required_output 对象本身，不得重写整篇答案，不得使用代码围栏或输出其他文字。"
        "对象只能有 values 一个字段，且不得增加、删除或重排 values。text 项把占位符替换为修正后的"
        "单条正文并严格遵守 instruction；selection 项填写恰好 exact_count 个不同 catalog 值。"
        "claim_id、path、evidence_refs、issue_codes、正文模板和服务器值全部由服务器恢复，输出中不得"
        "包含这些固定字段。每项必须独立满足同位置 target_context 的全部 issues，不得改写相邻目标。\n"
        + json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            default=lambda _value: "<non-serializable>",
        )
    )


def timed_render(draft: AnswerDraft, metrics: RepairMetrics) -> str:
    started = time.monotonic()
    try:
        return render_answer_draft(draft)
    finally:
        metrics.render_seconds += time.monotonic() - started
