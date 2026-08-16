from __future__ import annotations

import os
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool


def postgres_enabled() -> bool:
    return os.getenv("DATA_BACKEND", "").lower() == "postgres" and bool(os.getenv("DATABASE_URL"))


def fixture_fallback_allowed() -> bool:
    if _env_truthy("DISABLE_FIXTURE_FALLBACK"):
        return False
    if os.getenv("ALLOW_FIXTURE_FALLBACK") is not None:
        return _env_truthy("ALLOW_FIXTURE_FALLBACK")
    return os.getenv("VERCEL_ENV", "").lower() != "production"


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is required for DATA_BACKEND=postgres")
    return create_engine(url, poolclass=NullPool, pool_pre_ping=True, future=True)


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").lower() in {"1", "true", "yes", "on"}
