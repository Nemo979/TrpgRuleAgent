from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass


COOKIE_NAME = "trpg_session"


@dataclass(frozen=True)
class SessionSigner:
    secret: str
    ttl_seconds: int

    def issue(self, now: int | None = None) -> str:
        issued_at = int(time.time() if now is None else now)
        payload = str(issued_at).encode("ascii")
        signature = hmac.new(self.secret.encode(), payload, hashlib.sha256).digest()
        return _encode(payload + b"." + signature)

    def verify(self, token: str | None, now: int | None = None) -> bool:
        if not token:
            return False
        try:
            decoded = _decode(token)
            payload, signature = decoded.split(b".", 1)
            issued_at = int(payload.decode("ascii"))
        except (ValueError, UnicodeDecodeError):
            return False
        expected = hmac.new(self.secret.encode(), payload, hashlib.sha256).digest()
        current = int(time.time() if now is None else now)
        return (
            hmac.compare_digest(signature, expected)
            and 0 <= current - issued_at <= self.ttl_seconds
        )


def password_matches(candidate: str, expected: str) -> bool:
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
