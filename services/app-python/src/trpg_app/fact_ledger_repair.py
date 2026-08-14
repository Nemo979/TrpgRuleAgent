"""Rule-system-neutral structured answer repair and deterministic rendering."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import re
import time
from typing import Any, Iterable

from .fact_ledger import DraftPathSpec, ValidationIssue, ValidationSeverity


ANSWER_DRAFT_SCHEMA_VERSION = 1
REPAIR_PATCH_SCHEMA_VERSION = 1
MAX_DRAFT_SECTIONS = 16
MAX_DRAFT_CLAIMS = 96
MAX_CLAIM_CHARACTERS = 4_000
MAX_STRUCTURED_OUTPUT_CHARACTERS = 256_000
_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_PATH = re.compile(r"^answer(?:\.[A-Za-z_][A-Za-z0-9_]*|\[[^\]\n]{1,80}\])+$")
_CITATION = re.compile(r"\[(?:\^)?(S\d+)]")
_TEMPLATE_FIELD = re.compile(r"\{([a-z][a-z0-9_]*)\}")


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

    def __post_init__(self) -> None:
        if self.schema_version != ANSWER_DRAFT_SCHEMA_VERSION:
            raise StructuredAnswerError("answer draft schema version is incompatible")
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
        if not self.replacement_text.strip() or len(self.replacement_text) > MAX_CLAIM_CHARACTERS:
            raise StructuredAnswerError("replacement text is empty or too large")
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

    def public(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "path": self.path,
            "issue_codes": list(self.issue_codes),
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
    issue_codes: tuple[str, ...] = ()


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


def parse_answer_draft(
    raw: str,
    registered_refs: Iterable[str],
    path_specs: Iterable[DraftPathSpec] = (),
) -> AnswerDraft:
    allowed = set(registered_refs)
    specs = tuple(path_specs)
    payload = _strict_object(
        _json_payload(raw),
        {"schema_version", "sections"},
        "answer draft",
    )
    if type(payload["schema_version"]) is not int:
        raise StructuredAnswerError("answer draft schema version must be an integer")
    if not isinstance(payload["sections"], list):
        raise StructuredAnswerError("answer draft sections must be a list")
    sections: list[AnswerSection] = []
    for raw_section in payload["sections"]:
        section = _strict_object(raw_section, {"id", "heading", "claims"}, "section")
        if not isinstance(section["id"], str) or not isinstance(section["heading"], str):
            raise StructuredAnswerError("section scalar fields are invalid")
        if not isinstance(section["claims"], list):
            raise StructuredAnswerError("section claims must be a list")
        claims: list[AnswerClaim] = []
        for raw_claim in section["claims"]:
            claim = _strict_object(
                raw_claim,
                {"id", "path", "text", "evidence_refs"},
                "claim",
            )
            if not isinstance(claim["evidence_refs"], list):
                raise StructuredAnswerError("claim evidence refs must be a list")
            if not all(isinstance(value, str) for value in claim["evidence_refs"]):
                raise StructuredAnswerError("claim evidence refs must contain strings")
            if not all(
                isinstance(claim[field], str) for field in ("id", "path", "text")
            ):
                raise StructuredAnswerError("claim scalar fields are invalid")
            refs = tuple(claim["evidence_refs"])
            if set(refs) - allowed:
                raise StructuredAnswerError("claim references unregistered evidence")
            embedded = set(_CITATION.findall(str(claim["text"])))
            if embedded - set(refs):
                raise StructuredAnswerError("claim text contains an undeclared citation")
            claims.append(
                AnswerClaim(
                    id=claim["id"],
                    path=canonicalize_draft_path(claim["path"], specs),
                    text=claim["text"],
                    evidence_refs=refs,
                )
            )
        sections.append(
            AnswerSection(
                id=section["id"],
                heading=section["heading"],
                claims=tuple(claims),
            )
        )
    return AnswerDraft(tuple(sections), schema_version=int(payload["schema_version"]))


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


def build_repair_targets(
    draft: AnswerDraft,
    issues: Iterable[ValidationIssue],
) -> tuple[RepairTarget, ...]:
    claims_by_path = {claim.path: claim for claim in draft.claims}
    grouped: dict[tuple[str, str], list[str]] = {}
    for issue in issues:
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
        if claim is None:
            raise RepairTargetError(
                "path_not_found", "validation issue path has no exact draft claim"
            )
        grouped.setdefault((claim.id, claim.path), []).append(issue.code)
    return tuple(
        RepairTarget(claim_id, path, tuple(dict.fromkeys(codes)))
        for (claim_id, path), codes in grouped.items()
    )


def parse_repair_patch(raw: str, registered_refs: Iterable[str]) -> RepairPatch:
    allowed = set(registered_refs)
    payload = _strict_object(
        _json_payload(raw),
        {"schema_version", "operations"},
        "repair patch",
    )
    if type(payload["schema_version"]) is not int:
        raise StructuredAnswerError("repair schema version must be an integer")
    if not isinstance(payload["operations"], list):
        raise StructuredAnswerError("repair operations must be a list")
    operations: list[RepairOperation] = []
    for raw_operation in payload["operations"]:
        operation = _strict_object(
            raw_operation,
            {"claim_id", "path", "replacement_text", "evidence_refs", "issue_codes"},
            "repair operation",
        )
        if not isinstance(operation["evidence_refs"], list) or not isinstance(
            operation["issue_codes"], list
        ):
            raise StructuredAnswerError("repair operation arrays are invalid")
        if not all(
            isinstance(operation[field], str)
            for field in ("claim_id", "path", "replacement_text")
        ) or not all(
            isinstance(value, str)
            for value in [*operation["evidence_refs"], *operation["issue_codes"]]
        ):
            raise StructuredAnswerError("repair operation values are invalid")
        refs = tuple(operation["evidence_refs"])
        if set(refs) - allowed:
            raise StructuredAnswerError("repair references unregistered evidence")
        embedded = set(_CITATION.findall(str(operation["replacement_text"])))
        if embedded - set(refs):
            raise StructuredAnswerError("repair text contains an undeclared citation")
        operations.append(
            RepairOperation(
                claim_id=operation["claim_id"],
                path=operation["path"],
                replacement_text=operation["replacement_text"],
                evidence_refs=refs,
                issue_codes=tuple(operation["issue_codes"]),
            )
        )
    return RepairPatch(tuple(operations), schema_version=int(payload["schema_version"]))


def apply_repair_patch(
    draft: AnswerDraft,
    patch: RepairPatch,
    targets: tuple[RepairTarget, ...],
) -> AnswerDraft:
    expected = {(target.claim_id, target.path): set(target.issue_codes) for target in targets}
    actual = {(item.claim_id, item.path): set(item.issue_codes) for item in patch.operations}
    if actual != expected:
        raise StructuredAnswerError("repair patch does not exactly cover failed targets")
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


def structured_draft_instruction(path_specs: Iterable[DraftPathSpec] = ()) -> str:
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
    return (
        "最终输出必须是严格 JSON，不得使用代码围栏或输出 JSON 之外的文字。"
        "Schema 为 {schema_version:1,sections:[{id,heading,claims:[{id,path,text,evidence_refs}]}]}。"
        "section/claim id 使用小写字母数字与下划线；每个 claim path 必须是唯一稳定语义路径，"
        "格式为 answer.<domain>[<key>].<field>。每条规则结论单独一个 claim；text 不要手写引用，"
        "引用只放 evidence_refs，且只能使用已注册的 S 标签。"
        + suffix
    )


def repair_instruction(
    draft: AnswerDraft,
    issues: tuple[ValidationIssue, ...],
    targets: tuple[RepairTarget, ...],
) -> str:
    issue_payload = [
        {
            "code": issue.code,
            "message": issue.message,
            "path": issue.path,
            "expected": issue.expected,
            "actual": issue.actual,
            "evidence_refs": list(issue.evidence_refs),
        }
        for issue in issues
    ]
    payload = {
        "draft": draft.public(),
        "issues": issue_payload,
        "allowed_targets": [target.public() for target in targets],
    }
    return (
        "只返回一次局部修补 JSON，不得重写整篇答案，不得修改 allowed_targets 之外的 claim。"
        "Schema 为 {schema_version:1,operations:[{claim_id,path,replacement_text,"
        "evidence_refs,issue_codes}]}。operations 必须精确覆盖全部 allowed_targets，claim_id/path/"
        "issue_codes 必须原样复制；只能引用已注册证据。\n"
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
