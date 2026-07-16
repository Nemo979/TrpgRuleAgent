import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict
from urllib.parse import parse_qs, urlparse

from .repository import RuleRepository
from .retriever import InMemoryRetriever
from .service import RetrievalService


class RetrievalRequestHandler(BaseHTTPRequestHandler):
    service: RetrievalService

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send(HTTPStatus.OK, {"data": self.service.health()})
            return
        if parsed.path == "/sources":
            ruleset_id = parse_qs(parsed.query).get("rulesetId", [""])[0]
            self._send(HTTPStatus.OK, {"data": self.service.repository.sources(ruleset_id)})
            return
        self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        try:
            payload = self._read_json()
            if self.path == "/search":
                data = self.service.search(
                    query=str(payload.get("query", "")),
                    ruleset_id=str(payload.get("rulesetId", "")),
                    limit=int(payload.get("limit", 8)),
                    source_ids=payload.get("sourceIds"),
                )
            elif self.path == "/documents/read":
                data = self.service.read(
                    ruleset_id=str(payload.get("rulesetId", "")),
                    ids=payload.get("ids", []),
                )
            else:
                self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            self._send(HTTPStatus.OK, {"data": data})
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            self._send(HTTPStatus.BAD_REQUEST, {"error": str(error)})

    def log_message(self, format_string: str, *args: object) -> None:
        print("[retrieval] %s" % (format_string % args))

    def _read_json(self) -> Dict[str, Any]:
        content_length = int(self.headers.get("content-length", "0"))
        if content_length > 1_000_000:
            raise ValueError("request body too large")
        raw = self.rfile.read(content_length)
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("request body must be an object")
        return value

    def _send(self, status: HTTPStatus, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status.value)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def create_server(
    service: RetrievalService,
    host: str,
    port: int,
) -> ThreadingHTTPServer:
    handler = type("ConfiguredRetrievalHandler", (RetrievalRequestHandler,), {"service": service})
    return ThreadingHTTPServer((host, port), handler)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TrpgRuleAgent retrieval service")
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument(
        "--backend",
        choices=["lexical", "chroma", "hybrid"],
        default="lexical",
    )
    parser.add_argument("--index-dir", type=Path)
    parser.add_argument("--search-k", default=100, type=int)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repository = RuleRepository.from_jsonl(args.documents)
    if args.backend in ("chroma", "hybrid"):
        if args.index_dir is None:
            raise ValueError("--index-dir is required for vector-backed retrieval")
        from .vector_retriever import ChromaVectorRetriever
        retriever = ChromaVectorRetriever(repository, args.index_dir, args.search_k)
        if args.backend == "hybrid":
            from .hybrid_retriever import HybridRetriever
            retriever = HybridRetriever(retriever)
    else:
        retriever = InMemoryRetriever()
    service = RetrievalService(repository, retriever)
    server = create_server(service, args.host, args.port)
    print("Retrieval service listening on http://%s:%d" % (args.host, args.port))
    print("Loaded %d documents: %s" % (repository.count(), ", ".join(repository.rulesets())))
    print("Retrieval backend: %s" % args.backend)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
