from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import httpx

from backend.normalize.edgar.rate_limit import RateLimiter


class EdgarConfigError(RuntimeError):
    pass


class EdgarClient:
    def __init__(
        self,
        user_agent: str | None = None,
        cache_dir: Path | str = "storage/cache/sec",
        raw_dir: Path | str = "storage/raw/sec",
    ) -> None:
        self.user_agent = user_agent or os.getenv("SEC_USER_AGENT")
        if not self.user_agent:
            raise EdgarConfigError("SEC_USER_AGENT is required for live SEC requests")
        if os.getenv("VERCEL"):
            scratch = Path(tempfile.gettempdir()) / "personal-fastgraphs"
            cache_dir = scratch / "cache" / "sec"
            raw_dir = scratch / "raw" / "sec"
        self.cache_dir = Path(cache_dir)
        self.raw_dir = Path(raw_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.rate_limiter = RateLimiter()
        self.client = httpx.Client(
            headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=30,
        )

    def get_json(self, url: str, *, force_refresh: bool = False) -> dict[str, Any]:
        path = self._cache_path(url, "json")
        if path.exists() and not force_refresh:
            return json.loads(path.read_text(encoding="utf-8"))
        text = self.get_text(url, force_refresh=force_refresh)
        data = json.loads(text)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return data

    def get_text(self, url: str, *, force_refresh: bool = False) -> str:
        path = self._cache_path(url, "txt")
        if path.exists() and not force_refresh:
            return path.read_text(encoding="utf-8", errors="ignore")
        self.rate_limiter.wait()
        response = self.client.get(url)
        response.raise_for_status()
        text = response.text
        path.write_text(text, encoding="utf-8")
        self._write_manifest(url, path, response.headers.get("content-type"))
        return text

    def _cache_path(self, url: str, suffix: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.{suffix}"

    def _write_manifest(self, url: str, path: Path, content_type: str | None) -> None:
        manifest = {
            "url": url,
            "path": str(path),
            "content_type": content_type,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        path.with_suffix(path.suffix + ".manifest.json").write_text(
            json.dumps(manifest, indent=2),
            encoding="utf-8",
        )
