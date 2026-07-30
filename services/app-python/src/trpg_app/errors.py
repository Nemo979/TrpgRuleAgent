from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    PermissionDeniedError,
    RateLimitError,
)


@dataclass(frozen=True)
class PublicChatError:
    code: str
    message: str
    retryable: bool


def classify_chat_error(error: Exception) -> PublicChatError:
    if isinstance(error, (APITimeoutError, asyncio.TimeoutError)):
        return PublicChatError(
            code="model_timeout",
            message="模型响应超时，请稍后重试。",
            retryable=True,
        )
    if isinstance(error, RateLimitError):
        return PublicChatError(
            code="model_rate_limited",
            message="模型服务当前繁忙，请稍后重试。",
            retryable=True,
        )
    if isinstance(error, APIConnectionError):
        return PublicChatError(
            code="model_unavailable",
            message="暂时无法连接模型服务，请稍后重试。",
            retryable=True,
        )
    if isinstance(error, (AuthenticationError, PermissionDeniedError)):
        return PublicChatError(
            code="model_configuration_error",
            message="模型服务配置无效，请联系管理员。",
            retryable=False,
        )
    if isinstance(error, BadRequestError):
        return PublicChatError(
            code="model_request_rejected",
            message="模型服务拒绝了本次请求，请更换模型或联系管理员。",
            retryable=False,
        )
    if isinstance(error, APIStatusError):
        retryable = error.status_code >= 500
        return PublicChatError(
            code="model_unavailable" if retryable else "model_request_rejected",
            message=(
                "模型服务暂时不可用，请稍后重试。"
                if retryable
                else "模型服务拒绝了本次请求，请更换模型或联系管理员。"
            ),
            retryable=retryable,
        )
    if isinstance(error, ValueError) and any(
        marker in str(error) for marker in ("安全上限", "上下文预算", "数量已达到")
    ):
        return PublicChatError(
            code="evidence_budget_exceeded",
            message="本次问题涉及的规则范围过大，请缩小问题后重试。",
            retryable=False,
        )
    if isinstance(error, RuntimeError) and "工具循环超过安全上限" in str(error):
        return PublicChatError(
            code="agent_loop_limit",
            message="规则检索未能在安全范围内完成，请缩小问题或更换模型后重试。",
            retryable=False,
        )
    if isinstance(error, RuntimeError) and "未读取规则证据" in str(error):
        return PublicChatError(
            code="evidence_missing",
            message="模型未能读取规则依据，请重试或更换模型。",
            retryable=True,
        )
    return PublicChatError(
        code="internal_error",
        message="回答生成失败，请稍后重试。",
        retryable=False,
    )


def safe_status(error: Exception) -> int | None:
    value: Any = getattr(error, "status_code", None)
    return value if isinstance(value, int) else None
