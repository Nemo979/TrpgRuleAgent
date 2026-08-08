from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

from .importers.chm import decode_document


_SPACE = re.compile(r"\s+")
_CATALOG_MARKERS = ("法术", "专长", "装备", "魔法物品", "职业")
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_WORD_ANCHOR_PREFIXES = ("ole_link", "toc", "ref", "bookmark", "msocom")
_LABEL_NUMBER_RE = re.compile(r"^[A-Za-z]+\d+$|^\d+[A-Za-z]+$")


@dataclass(frozen=True)
class HtmlSignals:
    headings: tuple[str, ...]
    table_count: int
    table_row_count: int
    anchor_count: int
    bold_labels: tuple[str, ...]


class HtmlStructureParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._capture: str | None = None
        self._capture_depth = 0
        self._parts: list[str] = []
        self._table_depth = 0
        self.headings: list[str] = []
        self.bold_labels: list[str] = []
        self.table_count = 0
        self.table_row_count = 0
        self.anchor_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        attributes = {key.lower(): value or "" for key, value in attrs}
        if lowered == "table":
            self.table_count += 1
            self._table_depth += 1
        elif lowered == "tr" and self._table_depth:
            self.table_row_count += 1
        elif attributes.get("id") or (lowered == "a" and attributes.get("name")):
            self.anchor_count += 1
            if (
                lowered == "a"
                and not self._table_depth
                and plausible_anchor_label(attributes.get("name", ""))
            ):
                self.headings.append(attributes["name"])
        if self._capture is not None and lowered not in _VOID_TAGS:
            self._capture_depth += 1
        elif lowered in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._capture = "heading"
            self._capture_depth = 1
            self._parts = []
        elif lowered in {"b", "strong"} and not self._table_depth:
            self._capture = "bold"
            self._capture_depth = 1
            self._parts = []

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if self._capture is not None:
            self._capture_depth -= 1
            if self._capture_depth == 0:
                value = _normalize("".join(self._parts))
                if self._capture == "heading" and _plausible_label(value, 160):
                    self.headings.append(value)
                elif self._capture == "bold" and _plausible_label(value, 100):
                    self.bold_labels.append(value)
                self._capture = None
                self._parts = []
        if lowered == "table" and self._table_depth:
            self._table_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._parts.append(data)

    def signals(self) -> HtmlSignals:
        return HtmlSignals(
            headings=tuple(dict.fromkeys(self.headings)),
            table_count=self.table_count,
            table_row_count=self.table_row_count,
            anchor_count=self.anchor_count,
            bold_labels=tuple(dict.fromkeys(self.bold_labels)),
        )


def inspect_html(path: Path) -> HtmlSignals:
    text, _encoding = decode_document(path.read_bytes())
    parser = HtmlStructureParser()
    parser.feed(text)
    return parser.signals()


def recommend_strategy(
    *,
    content_length: int,
    full_path: str,
    signals: HtmlSignals,
) -> str:
    is_catalog = any(marker in full_path for marker in _CATALOG_MARKERS)
    heading_count = len(signals.headings)
    bold_count = len(signals.bold_labels)
    if content_length <= 4_000:
        return "keep_table_aware" if signals.table_count else "keep"
    if is_catalog and content_length >= 20_000 and bold_count >= 20:
        return "split_catalog_entries"
    if heading_count >= 2 and signals.table_count:
        return "split_headings_and_tables"
    if heading_count >= 2:
        return "split_headings"
    if signals.table_count:
        return "split_tables_with_context"
    if content_length >= 10_000:
        return "manual_pattern_review"
    return "keep"


def audit_documents(
    documents: Iterable[dict[str, Any]],
    extracted: Path,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    missing_source_files: list[str] = []
    for document in documents:
        metadata = document.get("metadata") or {}
        source_file = str(metadata.get("sourceFile", ""))
        html_path = extracted / source_file
        if not source_file or not html_path.is_file():
            missing_source_files.append(source_file or str(document.get("id", "")))
            signals = HtmlSignals((), 0, 0, 0, ())
        else:
            signals = inspect_html(html_path)
        content_length = len(str(document.get("content", "")))
        strategy = recommend_strategy(
            content_length=content_length,
            full_path=str(document.get("fullPath", "")),
            signals=signals,
        )
        rows.append(
            {
                "id": str(document.get("id", "")),
                "fullPath": str(document.get("fullPath", "")),
                "sourceFile": source_file,
                "contentLength": content_length,
                "currentChunkEstimate": max(1, math.ceil(max(content_length - 50, 1) / 450)),
                "minimumRecommendedParents": max(1, math.ceil(content_length / 8_000)),
                "strategy": strategy,
                "signals": {
                    **asdict(signals),
                    "headingCount": len(signals.headings),
                    "boldLabelCount": len(signals.bold_labels),
                    "headingSample": list(signals.headings[:8]),
                    "boldLabelSample": list(signals.bold_labels[:8]),
                },
            }
        )

    strategy_counts = Counter(row["strategy"] for row in rows)
    lengths = [row["contentLength"] for row in rows]
    return {
        "documentCount": len(rows),
        "missingSourceFileCount": len(missing_source_files),
        "missingSourceFiles": missing_source_files[:100],
        "lengths": {
            "minimum": min(lengths, default=0),
            "maximum": max(lengths, default=0),
            "average": round(sum(lengths) / len(lengths), 2) if lengths else 0,
            "over4000": sum(value > 4_000 for value in lengths),
            "over10000": sum(value > 10_000 for value in lengths),
            "over50000": sum(value > 50_000 for value in lengths),
        },
        "strategyCounts": dict(sorted(strategy_counts.items())),
        "documents": sorted(rows, key=lambda row: row["contentLength"], reverse=True),
    }


def attach_evaluation_targets(
    report: dict[str, Any],
    cases: Iterable[dict[str, Any]],
    evaluation: dict[str, Any] | None = None,
) -> None:
    by_id = {row["id"]: row for row in report["documents"]}
    ranks = {
        str(item.get("id", "")): item.get("rank")
        for item in (evaluation or {}).get("cases", [])
    }
    targets = []
    for case in cases:
        case_id = str(case.get("id", ""))
        relevant_ids = [str(value) for value in case.get("relevantIds", [])]
        targets.append(
            {
                "id": case_id,
                "query": str(case.get("query", "")),
                "rank": ranks.get(case_id),
                "documents": [
                    {
                        "id": document_id,
                        "fullPath": by_id.get(document_id, {}).get("fullPath", ""),
                        "contentLength": by_id.get(document_id, {}).get("contentLength"),
                        "strategy": by_id.get(document_id, {}).get("strategy", "missing"),
                    }
                    for document_id in relevant_ids
                ],
            }
        )
    report["evaluationTargets"] = targets


def render_markdown(report: dict[str, Any], top: int = 50) -> str:
    lengths = report["lengths"]
    lines = [
        "# PF1e V2 CHM 结构审计报告",
        "",
        "本报告只分析候选切分策略，不修改当前发布规则库或索引。",
        "",
        "## 汇总",
        "",
        f"- 父文档：{report['documentCount']}",
        f"- 缺失原始 HTML：{report['missingSourceFileCount']}",
        f"- 平均正文长度：{lengths['average']}",
        f"- 最大正文长度：{lengths['maximum']}",
        f"- 超过 4,000 字：{lengths['over4000']}",
        f"- 超过 10,000 字：{lengths['over10000']}",
        f"- 超过 50,000 字：{lengths['over50000']}",
        "",
        "## 推荐策略分布",
        "",
        "| 策略 | 文档数 |",
        "| --- | ---: |",
    ]
    for strategy, count in report["strategyCounts"].items():
        lines.append(f"| `{strategy}` | {count} |")
    if report.get("evaluationTargets"):
        lines.extend(
            [
                "",
                "## 评测目标对应的切分策略",
                "",
                "| 题目 | 当前排名 | 目标文档字数 | 推荐策略 | 目标路径 |",
                "| --- | ---: | ---: | --- | --- |",
            ]
        )
        for target in report["evaluationTargets"]:
            for document in target["documents"]:
                rank = target["rank"] if target["rank"] is not None else "-"
                length = document["contentLength"] if document["contentLength"] is not None else "-"
                path = str(document["fullPath"]).replace("|", "\\|")
                lines.append(
                    f"| {target['id']}：{target['query']} | {rank} | {length} | "
                    f"`{document['strategy']}` | {path} |"
                )
    lines.extend(
        [
            "",
            f"## 最大 {top} 个父文档",
            "",
            "| 字数 | 当前块估算 | 最少父条目 | HTML 标题 | 表格 | 粗体候选 | 推荐策略 | 路径 |",
            "| ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for row in report["documents"][:top]:
        signals = row["signals"]
        path = row["fullPath"].replace("|", "\\|")
        lines.append(
            f"| {row['contentLength']} | {row['currentChunkEstimate']} | "
            f"{row['minimumRecommendedParents']} | {signals['headingCount']} | "
            f"{signals['table_count']} | {signals['boldLabelCount']} | "
            f"`{row['strategy']}` | {path} |"
        )
    lines.extend(
        [
            "",
            "## 策略说明",
            "",
            "- `keep`：短且结构简单，保持现有父文档。",
            "- `keep_table_aware`：父文档保持不变，但表格行应作为额外检索单元。",
            "- `split_headings`：按 HTML 标题拆分父文档。",
            "- `split_headings_and_tables`：按标题拆分，并为表格保留列名和上下文。",
            "- `split_catalog_entries`：法术、专长、物品或职业集合页，需要识别独立条目。",
            "- `split_tables_with_context`：标题不足，但存在大表格，应按表格区域和解释文字拆分。",
            "- `manual_pattern_review`：长文档缺少稳定 HTML 信号，需要为该页面类型增加模式。",
            "",
            "`minimumRecommendedParents` 只是按 8,000 字上限计算的审计下界，不是最终语义切分数量。",
        ]
    )
    return "\n".join(lines) + "\n"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit PF1e CHM structure for V2 splitting")
    parser.add_argument("--documents", required=True, type=Path)
    parser.add_argument("--extracted", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--evaluation-cases", type=Path)
    parser.add_argument("--evaluation-report", type=Path)
    parser.add_argument("--top", type=int, default=50)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = audit_documents(load_jsonl(args.documents), args.extracted)
    if args.evaluation_cases:
        evaluation = (
            json.loads(args.evaluation_report.read_text(encoding="utf-8"))
            if args.evaluation_report
            else None
        )
        attach_evaluation_targets(report, load_jsonl(args.evaluation_cases), evaluation)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(render_markdown(report, args.top), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "documents"}, ensure_ascii=False))


def _normalize(value: str) -> str:
    return _SPACE.sub(" ", value).strip()


def _plausible_label(value: str, maximum: int) -> bool:
    if not 1 < len(value) <= maximum:
        return False
    if re.fullmatch(r"[\d\W_]+", value):
        return False
    return True


def plausible_anchor_label(value: str) -> bool:
    """Word-style section anchors use ``<A name="章节名">`` as headings.

    Keep real section titles while rejecting tooling anchors: OLE link
    bookmarks, URL-encoded names, spreadsheet ranges, and label-number
    identifiers such as ``G1210``.
    """
    if not _plausible_label(value, 160):
        return False
    normalized = value.strip("_")
    if not normalized or any(marker in normalized for marker in ("%", "!", ":")):
        return False
    lowered = normalized.casefold()
    if any(lowered.startswith(prefix) for prefix in _WORD_ANCHOR_PREFIXES):
        return False
    if _LABEL_NUMBER_RE.match(normalized):
        return False
    return True


if __name__ == "__main__":
    main()
