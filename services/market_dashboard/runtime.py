"""Explicit worker-only credentials loading; never imported by public request handlers."""

import os
from pathlib import Path

from packages.core.env import parse_env_line

KEYS = {
    "TOSS_CLIENT_ID",
    "TOSS_CLIENT_SECRET",
    "TOSS_TOKEN_FILE",
    "TOSS_CREDENTIAL_FILE",
    "DART_API_KEY",
    "MARKET_KRX_API_KEY",
    "MARKET_KRX_DAILY_BUDGET",
    "MARKET_NAVER_CLIENT_ID",
    "MARKET_NAVER_CLIENT_SECRET",
}


def load_worker_credentials(path=None):
    default = Path(__file__).resolve().parents[2] / "storage/public-market/runtime.env"
    path = Path(path or os.getenv("MARKET_ENV_FILE", default))
    if not path.is_file():
        return []
    loaded = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        item = parse_env_line(line)
        if item and item[0] in KEYS and not os.environ.get(item[0], "").strip() and item[1]:
            os.environ[item[0]] = item[1]
            loaded.append(item[0])
    return loaded
