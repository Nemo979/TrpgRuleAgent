import argparse
import hashlib
import json
import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import unquote

from trpg_retrieval.domain import RuleDocument


HTML_SUFFIXES = {".htm", ".html"}
SPACE_PATTERN = re.compile(r"[ \t\f\v]+")
NEWLINE_PATTERN = re.compile(r"\n{3,}")
CHARSET_PATTERN = re.compile(br"charset\s*=\s*['\"]?([a-zA-Z0-9_-]+)", re.IGNORECASE)


def decode_document(raw: bytes) -> Tuple[str, str]:
    declared = CHARSET_PATTERN.search(raw[:4096])
    candidates = []
    if declared:
        candidates.append(declared.group(1).decode("ascii", errors="ignore"))
    candidates.extend(["utf-8", "gb18030", "big5"])

    tried = set()
    for encoding in candidates:
        normalized = encoding.lower().replace("gb2312", "gb18030")
        if normalized in tried:
            continue
        tried.add(normalized)
        try:
            return raw.decode(normalized), normalized
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("gb18030", errors="replace"), "gb18030-replace"


class SitemapParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = -1
        self.in_object = False
        self.params: Dict[str, str] = {}
        self.last_path_at_depth: Dict[int, List[str]] = {}
        self.entries: List[Dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        attributes = {key.lower(): value or "" for key, value in attrs}
        lowered = tag.lower()
        if lowered == "ul":
            self.depth += 1
        elif lowered == "object" and attributes.get("type", "").lower() == "text/sitemap":
            self.in_object = True
            self.params = {}
        elif lowered == "param" and self.in_object:
            name = attributes.get("name", "").lower()
            self.params[name] = attributes.get("value", "")

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "object" and self.in_object:
            self._finish_object()
            self.in_object = False
        elif lowered == "ul":
            self.last_path_at_depth.pop(self.depth, None)
            self.depth -= 1

    def _finish_object(self) -> None:
        name = self.params.get("name", "").strip()
        local = normalize_local(self.params.get("local", ""))
        parent_path = self.last_path_at_depth.get(self.depth - 1, [])
        path = parent_path + ([name] if name else [])
        self.last_path_at_depth[self.depth] = path
        if local:
            self.entries.append({"name": name, "local": local, "path": path})


class TextExtractor(HTMLParser):
    BREAK_TAGS = {
        "address", "article", "aside", "blockquote", "br", "caption", "div",
        "dl", "dt", "dd", "figcaption", "footer", "h1", "h2", "h3", "h4",
        "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre",
        "section", "table", "td", "th", "tr", "ul",
    }
    SKIP_TAGS = {"script", "style", "noscript"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self.skip_depth = 0
        self.title_parts: List[str] = []
        self.in_title = False

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        lowered = tag.lower()
        if lowered in self.SKIP_TAGS:
            self.skip_depth += 1
        elif self.skip_depth == 0 and lowered in self.BREAK_TAGS:
            self.parts.append("\n")
        if lowered == "title":
            self.in_title = True

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in self.SKIP_TAGS and self.skip_depth > 0:
            self.skip_depth -= 1
        elif self.skip_depth == 0 and lowered in self.BREAK_TAGS:
            self.parts.append("\n")
        if lowered == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.skip_depth > 0:
            return
        self.parts.append(data)
        if self.in_title:
            self.title_parts.append(data)

    def text(self) -> str:
        joined = "".join(self.parts).replace("\r", "\n")
        lines = [SPACE_PATTERN.sub(" ", line).strip() for line in joined.split("\n")]
        return NEWLINE_PATTERN.sub("\n\n", "\n".join(line for line in lines if line)).strip()

    def title(self) -> str:
        return SPACE_PATTERN.sub(" ", "".join(self.title_parts)).strip()


def normalize_local(value: str) -> str:
    local = unquote(value.strip()).replace("\\", "/")
    return local.lstrip("/").split("#", 1)[0]


def parse_sitemap(path: Path) -> List[Dict[str, Any]]:
    text, _encoding = decode_document(path.read_bytes())
    parser = SitemapParser()
    parser.feed(text)
    return parser.entries


def extract_html(path: Path) -> Tuple[str, str, str]:
    text, encoding = decode_document(path.read_bytes())
    parser = TextExtractor()
    parser.feed(text)
    return parser.title(), parser.text(), encoding


def stable_id(local: str) -> str:
    digest = hashlib.sha1(local.encode("utf-8")).hexdigest()[:12]
    return "pf1e-%s" % digest


def source_id(source_title: str) -> str:
    upper = source_title.upper()
    known = ["CRB", "APG", "ACG", "ARG", "UM", "UC", "OA", "UI", "FAQ"]
    for abbreviation in known:
        if abbreviation in upper:
            return abbreviation.lower()
    return "source-%s" % hashlib.sha1(source_title.encode("utf-8")).hexdigest()[:8]


def source_priority(title: str) -> int:
    upper = title.upper()
    if "CRB" in upper or "核心规则" in title:
        return 100
    if "FAQ" in upper or "常用速查" in title:
        return 90
    if "未整理" in title or "出处未明" in title:
        return 20
    return 60


def choose_entries(entries: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    selected: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        local = entry["local"]
        existing = selected.get(local.lower())
        if existing is None or len(entry["path"]) > len(existing["path"]):
            selected[local.lower()] = entry
    return selected


def import_documents(extracted: Path) -> Tuple[List[RuleDocument], Dict[str, Any]]:
    hhc_files = sorted(extracted.glob("*.hhc"))
    if len(hhc_files) != 1:
        raise ValueError("expected exactly one .hhc file, found %d" % len(hhc_files))

    sitemap_entries = parse_sitemap(hhc_files[0])
    selected = choose_entries(sitemap_entries)
    documents = []
    missing_files = []
    empty_files = []
    encoding_counts: Counter = Counter()

    for entry in selected.values():
        local = entry["local"]
        html_path = extracted / local
        if not html_path.exists() or html_path.suffix.lower() not in HTML_SUFFIXES:
            missing_files.append(local)
            continue
        html_title, content, encoding = extract_html(html_path)
        encoding_counts[encoding] += 1
        if not content:
            empty_files.append(local)
            continue

        path_parts = [part for part in entry["path"] if part]
        title = entry["name"] or html_title or html_path.stem
        top_level = path_parts[0] if path_parts else title
        documents.append(RuleDocument(
            id=stable_id(local.lower()),
            ruleset_id="pathfinder-1e",
            source_id=source_id(top_level),
            source_title=top_level,
            title=title,
            full_path=" > ".join(path_parts) if path_parts else title,
            content=content,
            version="1e-v2.24-sc",
            priority=source_priority(top_level),
            metadata={
                "sourceFile": local,
                "encoding": encoding,
                "demo": False,
            },
        ))

    report = {
        "rulesetId": "pathfinder-1e",
        "sitemap": hhc_files[0].name,
        "sitemapEntryCount": len(sitemap_entries),
        "uniqueLinkedFileCount": len(selected),
        "documentCount": len(documents),
        "missingFileCount": len(missing_files),
        "missingFiles": missing_files[:100],
        "emptyFileCount": len(empty_files),
        "emptyFiles": empty_files[:100],
        "encodings": dict(encoding_counts),
    }
    return documents, report


def write_jsonl(path: Path, documents: Sequence[RuleDocument]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for document in documents:
            handle.write(json.dumps(document.to_json(), ensure_ascii=False) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import an extracted Pathfinder CHM")
    parser.add_argument("--extracted", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    documents, report = import_documents(args.extracted)
    write_jsonl(args.output, documents)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Imported %d parent documents" % len(documents))
    print("Report: %s" % args.report)
    print("Documents: %s" % args.output)


if __name__ == "__main__":
    main()
