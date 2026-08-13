from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ModelConfig:
    id: str
    label: str
    base_url: str
    model: str
    api_key: str
    context_window: int = 128_000
    max_output_tokens: int = 8_192
    request_timeout_seconds: float = 120.0
    max_retries: int = 1
    disable_thinking: bool = False
    strip_thinking: bool = False

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "contextWindow": self.context_window,
        }


@dataclass(frozen=True)
class AppConfig:
    shared_password: str
    session_secret: str
    library_root: Path
    models: tuple[ModelConfig, ...]
    cookie_secure: bool = True
    session_ttl_seconds: int = 7 * 24 * 60 * 60
    max_concurrent_turns: int = 5
    enable_dynamic_evidence_budget: bool = False
    enable_query_decomposition: bool = False
    enable_complex_planner: bool = False
    enable_fact_ledger: bool = False


def load_config(path: Path | None = None) -> AppConfig:
    config_path = path or Path(os.environ.get("TRPG_CONFIG", "config/app.yaml"))
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ValueError("application config must be a mapping")

    shared_password = _required_env(str(raw.get("shared_password_env", "TRPG_SHARED_PASSWORD")))
    session_secret = _required_env(str(raw.get("session_secret_env", "TRPG_SESSION_SECRET")))
    model_values = raw.get("models")
    if not isinstance(model_values, list) or not model_values:
        raise ValueError("config must define at least one model")

    models: list[ModelConfig] = []
    seen_ids: set[str] = set()
    for index, value in enumerate(model_values):
        if not isinstance(value, dict):
            raise ValueError(f"models[{index}] must be a mapping")
        model_id = _required_string(value, "id", index)
        if model_id in seen_ids:
            raise ValueError(f"duplicate model id: {model_id}")
        seen_ids.add(model_id)
        context_window = int(value.get("context_window", 128_000))
        max_output_tokens = int(value.get("max_output_tokens", 8_192))
        request_timeout_seconds = float(value.get("request_timeout_seconds", 120))
        max_retries = int(value.get("max_retries", 1))
        if context_window <= 0:
            raise ValueError(f"models[{index}].context_window must be positive")
        if max_output_tokens <= 0:
            raise ValueError(f"models[{index}].max_output_tokens must be positive")
        if request_timeout_seconds <= 0:
            raise ValueError(f"models[{index}].request_timeout_seconds must be positive")
        if not 0 <= max_retries <= 3:
            raise ValueError(f"models[{index}].max_retries must be between 0 and 3")
        models.append(
            ModelConfig(
                id=model_id,
                label=_required_string(value, "label", index),
                base_url=_required_string(value, "base_url", index).rstrip("/"),
                model=_required_string(value, "model", index),
                api_key=_required_env(_required_string(value, "api_key_env", index)),
                context_window=context_window,
                max_output_tokens=max_output_tokens,
                request_timeout_seconds=request_timeout_seconds,
                max_retries=max_retries,
                disable_thinking=bool(value.get("disable_thinking", False)),
                strip_thinking=bool(value.get("strip_thinking", False)),
            )
        )

    library_root = Path(str(raw.get("library_root", "data/libraries"))).resolve()
    return AppConfig(
        shared_password=shared_password,
        session_secret=session_secret,
        library_root=library_root,
        models=tuple(models),
        cookie_secure=bool(raw.get("cookie_secure", True)),
        session_ttl_seconds=int(raw.get("session_ttl_seconds", 7 * 24 * 60 * 60)),
        max_concurrent_turns=int(raw.get("max_concurrent_turns", 5)),
        enable_dynamic_evidence_budget=_optional_bool(
            raw, "enable_dynamic_evidence_budget", False
        ),
        enable_query_decomposition=_optional_bool(
            raw, "enable_query_decomposition", False
        ),
        enable_complex_planner=_optional_bool(raw, "enable_complex_planner", False),
        enable_fact_ledger=_optional_bool(raw, "enable_fact_ledger", False),
    )


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"required environment variable is missing: {name}")
    return value


def _optional_bool(value: dict[str, Any], key: str, default: bool) -> bool:
    result = value.get(key, default)
    if not isinstance(result, bool):
        raise ValueError(f"{key} must be a boolean")
    return result


def _required_string(value: dict[str, Any], key: str, index: int) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise ValueError(f"models[{index}].{key} must be a non-empty string")
    return result.strip()
