from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


DEFAULT_LOCAL_ORIGINS = ("http://localhost:3100", "http://127.0.0.1:3100")


def cors_allow_origins() -> list[str]:
    raw = os.getenv("API_CORS_ORIGINS") or os.getenv("CORS_ALLOW_ORIGINS")
    origins = _split_csv(raw)
    site_url = _normalize_origin(os.getenv("NEXT_PUBLIC_SITE_URL") or "")
    if site_url:
        origins.append(site_url)
    if not origins:
        origins.extend(DEFAULT_LOCAL_ORIGINS)
    return _dedupe([origin for origin in (_normalize_origin(item) for item in origins) if origin])


def cors_allow_credentials() -> bool:
    return "*" not in cors_allow_origins()


def cors_is_restricted() -> bool:
    origins = cors_allow_origins()
    return bool(origins) and "*" not in origins


def rate_limit_enabled() -> bool:
    explicit = os.getenv("API_RATE_LIMIT_ENABLED")
    if explicit is not None:
        return _env_truthy("API_RATE_LIMIT_ENABLED")
    if os.getenv("API_AUTH_DISABLED", "").lower() == "true":
        return False
    return _env_truthy("API_AUTH_REQUIRED") or os.getenv("VERCEL_ENV") == "production"


def rate_limit_config() -> dict[str, int]:
    return {
        "requests": _positive_int(os.getenv("API_RATE_LIMIT_REQUESTS"), 600),
        "window_seconds": _positive_int(os.getenv("API_RATE_LIMIT_WINDOW_SECONDS"), 60),
    }


def reset_rate_limit_state() -> None:
    _RATE_LIMIT_BUCKETS.clear()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        _apply_security_headers(request, response)
        return response


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after: int


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if _rate_limit_skip(request):
            return await call_next(request)
        decision = _rate_limit_decision(request)
        if not decision.allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "rate_limited",
                        "message": "Too many API requests; retry after the current window.",
                    }
                },
                headers={
                    "Retry-After": str(decision.retry_after),
                    "X-RateLimit-Limit": str(rate_limit_config()["requests"]),
                },
            )
        response = await call_next(request)
        response.headers.setdefault("X-RateLimit-Limit", str(rate_limit_config()["requests"]))
        return response


_RATE_LIMIT_BUCKETS: dict[str, deque[float]] = defaultdict(deque)


def _apply_security_headers(request: Request, response: Response) -> None:
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "private, no-store")
    if _env_truthy("API_ENABLE_HSTS") or os.getenv("VERCEL_ENV") == "production":
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains; preload",
        )


def _rate_limit_skip(request: Request) -> bool:
    if not rate_limit_enabled():
        return True
    if request.method.upper() == "OPTIONS":
        return True
    if not request.url.path.startswith("/api/"):
        return True
    exempt_paths = set(_split_csv(os.getenv("API_RATE_LIMIT_EXEMPT_PATHS"))) or {"/api/health"}
    return request.url.path in exempt_paths


def _rate_limit_decision(request: Request) -> RateLimitDecision:
    config = rate_limit_config()
    now = time.monotonic()
    key = _rate_limit_key(request)
    bucket = _RATE_LIMIT_BUCKETS[key]
    window = config["window_seconds"]
    while bucket and now - bucket[0] >= window:
        bucket.popleft()
    if len(bucket) >= config["requests"]:
        retry_after = max(1, int(window - (now - bucket[0])))
        return RateLimitDecision(allowed=False, retry_after=retry_after)
    bucket.append(now)
    return RateLimitDecision(allowed=True, retry_after=0)


def _rate_limit_key(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    client_ip = forwarded_for.split(",", 1)[0].strip()
    if not client_ip and request.client:
        client_ip = request.client.host
    return f"{client_ip or 'unknown'}:{request.url.path}"


def _split_csv(raw: str | None) -> list[str]:
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


def _normalize_origin(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value or value == "*":
        return value
    return value


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _env_truthy(key: str) -> bool:
    return os.getenv(key, "").strip().lower() in {"1", "true", "yes", "on"}


def _positive_int(raw: str | None, default: int) -> int:
    try:
        value = int(raw or default)
    except ValueError:
        return default
    return value if value > 0 else default
