from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, AsyncIterator, Callable, Literal

from fastapi import Cookie, Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .auth import COOKIE_NAME, SessionSigner, password_matches
from .chat import ModelGateway, OpenAIModelGateway, run_rule_turn
from .config import AppConfig, ModelConfig
from .errors import classify_chat_error, safe_status
from .library_boundary import find_explicit_other_library
from .libraries import LibraryCatalog


logger = logging.getLogger("uvicorn.error")


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=100_000)


class ChatRequest(BaseModel):
    model_id: str
    library_id: str
    messages: list[ChatMessage] = Field(min_length=1, max_length=200)


def create_app(
    config: AppConfig,
    static_dir: Path | None = None,
    gateway_factory: Callable[[ModelConfig], ModelGateway] = OpenAIModelGateway,
) -> FastAPI:
    signer = SessionSigner(config.session_secret, config.session_ttl_seconds)
    turn_slots = asyncio.Semaphore(config.max_concurrent_turns)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.catalog = LibraryCatalog(config.library_root)
        yield

    app = FastAPI(title="TRPG Rule App", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready() -> Response:
        catalog: LibraryCatalog = app.state.catalog
        libraries = catalog.list()
        if not libraries or not config.models:
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready"},
            )
        return JSONResponse(
            content={
                "status": "ready",
                "libraryCount": len(libraries),
                "modelCount": len(config.models),
            }
        )

    def require_auth(session: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None) -> None:
        if not signer.verify(session):
            raise HTTPException(status_code=401, detail="authentication required")

    @app.post("/api/auth/login")
    async def login(payload: LoginRequest, response: Response) -> dict[str, bool]:
        if not password_matches(payload.password, config.shared_password):
            raise HTTPException(status_code=401, detail="invalid password")
        response.set_cookie(
            COOKIE_NAME,
            signer.issue(),
            httponly=True,
            secure=config.cookie_secure,
            samesite="strict",
            max_age=config.session_ttl_seconds,
            path="/",
        )
        return {"authenticated": True}

    @app.post("/api/auth/logout")
    async def logout(response: Response) -> dict[str, bool]:
        response.delete_cookie(COOKIE_NAME, path="/")
        return {"authenticated": False}

    @app.get("/api/bootstrap", dependencies=[Depends(require_auth)])
    async def bootstrap() -> dict[str, object]:
        catalog: LibraryCatalog = app.state.catalog
        return {
            "models": [model.public() for model in config.models],
            "libraries": catalog.list(),
        }

    @app.post("/api/chat", dependencies=[Depends(require_auth)])
    async def chat(payload: ChatRequest) -> StreamingResponse:
        request_id = uuid.uuid4().hex
        model = next((item for item in config.models if item.id == payload.model_id), None)
        if model is None:
            raise HTTPException(status_code=404, detail="model not found")
        catalog: LibraryCatalog = app.state.catalog
        try:
            library = catalog.get(payload.library_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="library not found") from None
        latest_user_message = next(
            (
                message.content
                for message in reversed(payload.messages)
                if message.role == "user"
            ),
            "",
        )
        other_library = find_explicit_other_library(
            latest_user_message,
            current_library_id=library.manifest.id,
            libraries=catalog.descriptors(),
        )

        async def events() -> AsyncIterator[str]:
            if other_library is not None:
                current_name = library.manifest.name
                other_name = other_library["name"]
                yield _sse(
                    {
                        "type": "text_delta",
                        "delta": (
                            f"当前对话绑定「{current_name}」，不覆盖"
                            f"「{other_name}」规则。请新建对话并选择"
                            f"「{other_name}」规则库。"
                        ),
                    }
                )
                yield _sse({"type": "sources", "sources": []})
                yield _sse({"type": "done"})
                return
            async with turn_slots:
                try:
                    async for event in run_rule_turn(
                        model=model,
                        library=library,
                        messages=[item.model_dump() for item in payload.messages],
                        gateway_factory=gateway_factory,
                        request_id=request_id,
                        enable_dynamic_evidence_budget=(
                            config.enable_dynamic_evidence_budget
                        ),
                    ):
                        yield _sse(event)
                except Exception as error:
                    public_error = classify_chat_error(error)
                    logger.error(
                        "chat_failed request_id=%s model_id=%s library_id=%s "
                        "error_type=%s status=%s code=%s retryable=%s",
                        request_id,
                        model.id,
                        library.manifest.id,
                        type(error).__name__,
                        safe_status(error),
                        public_error.code,
                        public_error.retryable,
                    )
                    yield _sse(
                        {
                            "type": "error",
                            "message": public_error.message,
                            "code": public_error.code,
                            "requestId": request_id,
                            "retryable": public_error.retryable,
                        }
                    )
                    yield _sse({"type": "done"})

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"X-Request-ID": request_id},
        )

    @app.get(
        "/api/libraries/{library_id}/documents/{document_id}",
        dependencies=[Depends(require_auth)],
    )
    async def source(library_id: str, document_id: str) -> dict[str, object]:
        catalog: LibraryCatalog = app.state.catalog
        try:
            document = catalog.get(library_id).document(document_id)
        except KeyError:
            document = None
        if document is None:
            raise HTTPException(status_code=404, detail="source not found")
        return document

    if static_dir and static_dir.is_dir():
        assets = static_dir / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def frontend(path: str) -> FileResponse:
            requested = (static_dir / path).resolve()
            if path and requested.is_file() and static_dir.resolve() in requested.parents:
                return FileResponse(requested)
            return FileResponse(static_dir / "index.html")

    return app


def _sse(event: dict[str, object]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
