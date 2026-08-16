from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


@dataclass(frozen=True)
class ApiSession:
    email: str
    exp: int


def owner_key_from_email(email: str) -> str:
    digest = hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:32]
    return f"user_{digest}"


def request_owner_key(request: Request, fallback: str = "default") -> str:
    email = getattr(request.state, "owner_email", None)
    return owner_key_from_email(str(email)) if email else fallback


def api_auth_required() -> bool:
    if os.getenv("API_AUTH_DISABLED", "").lower() == "true":
        return False
    return os.getenv("API_AUTH_REQUIRED", os.getenv("AUTH_REQUIRED", "")).lower() == "true"


def sign_pf_session(
    email: str,
    secret: str,
    now: int | None = None,
    ttl_seconds: int = 8 * 60 * 60,
) -> str:
    issued_at = int(now if now is not None else time.time())
    payload = {
        "email": email.lower(),
        "iat": issued_at,
        "exp": issued_at + ttl_seconds,
    }
    payload_part = _base64_url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = _base64_url_encode(
        hmac.new(
            secret.encode("utf-8"),
            payload_part.encode("utf-8"),
            hashlib.sha256,
        ).digest()
    )
    return f"v1.{payload_part}.{signature}"


def verify_pf_session(
    value: str | None,
    secret: str | None = None,
    now: int | None = None,
) -> ApiSession | None:
    if not value:
        return None
    active_secret = secret or os.getenv("PF_COOKIE_SECRET") or os.getenv("AUTH_SECRET")
    if not active_secret:
        return None
    try:
        version, payload_part, signature = value.split(".", 2)
    except ValueError:
        return None
    if version != "v1":
        return None
    expected = _base64_url_encode(
        hmac.new(
            active_secret.encode("utf-8"),
            payload_part.encode("utf-8"),
            hashlib.sha256,
        ).digest()
    )
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(_base64_url_decode(payload_part).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None
    exp = int(payload.get("exp") or 0)
    if exp <= int(now if now is not None else time.time()):
        return None
    email = str(payload.get("email") or "").lower()
    if not _email_is_allowed(email):
        return None
    return ApiSession(email=email, exp=exp)


class ApiAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if (
            not api_auth_required()
            or request.method.upper() == "OPTIONS"
            or request.url.path == "/api/health"
        ):
            return await call_next(request)
        session = verify_pf_session(request.cookies.get("pf_session"))
        if session is None:
            return JSONResponse(
                status_code=401,
                content={"error": {"code": "unauthorized", "message": "Authentication required"}},
            )
        request.state.owner_email = session.email
        return await call_next(request)


def _email_is_allowed(email: str) -> bool:
    allowed = {
        item.strip().lower()
        for item in os.getenv("AUTH_ALLOWED_EMAILS", "").split(",")
        if item.strip()
    }
    return bool(email) and (not allowed or email in allowed)


def _base64_url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64_url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
