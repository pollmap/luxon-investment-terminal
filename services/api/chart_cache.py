from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from data.blob_queue import BlobQueueItem, BlobUploadQueue

TICKER_SEGMENT_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-")
TICKER_SEGMENT_SEPARATORS = frozenset(".-")
MAX_TICKER_SEGMENT_LENGTH = 32


@dataclass(frozen=True)
class ChartRenderResult:
    content: bytes
    content_type: str
    cache_key: str
    cached: bool
    local_path: str
    blob_key: str
    queue_manifest: str | None = None


def render_cached_valuation_chart(
    ticker: str,
    payload: dict,
    chart_format: str,
    renderer: Callable[[list[dict], dict | None], str | bytes],
) -> ChartRenderResult:
    normalized_format = chart_format.lower()
    if normalized_format not in {"svg", "png"}:
        raise ValueError(f"unsupported chart format: {chart_format}")

    ticker_segment = _canonical_ticker_segment(ticker)
    cache_key = valuation_chart_cache_key(ticker_segment, payload, normalized_format)
    cache_path = _valuation_chart_cache_path(ticker_segment, cache_key, normalized_format)
    content_type = "image/svg+xml" if normalized_format == "svg" else "image/png"
    cached = cache_path.exists()
    if cached:
        content = cache_path.read_bytes()
    else:
        rendered = renderer(payload["data"], _chart_visibility(payload))
        content = rendered.encode("utf-8") if isinstance(rendered, str) else rendered
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(content)

    blob_key = (
        f"rendered/charts/valuation-map/{ticker_segment}/{cache_key}.{normalized_format}"
    )
    queue_manifest = (
        _queue_chart_blob(cache_path, blob_key, content_type, ticker_segment, payload, cache_key)
        if _env_truthy("CHART_BLOB_QUEUE_ENABLED") and not cached
        else None
    )
    return ChartRenderResult(
        content=content,
        content_type=content_type,
        cache_key=cache_key,
        cached=cached,
        local_path=str(cache_path),
        blob_key=blob_key,
        queue_manifest=queue_manifest,
    )


def valuation_chart_cache_key(ticker: str, payload: dict, chart_format: str) -> str:
    meta = payload.get("meta", {})
    stable = {
        "ticker": ticker.upper(),
        "format": chart_format,
        "data": payload.get("data", []),
        "metric": meta.get("metric"),
        "metric_label": meta.get("metric_label"),
        "forecast": meta.get("forecast"),
        "line_visibility": meta.get("line_visibility"),
        "data_mode": meta.get("data_mode"),
        "data_backend": meta.get("data_backend"),
        "price_points": meta.get("price_points"),
        "price_points_meta": meta.get("price_points_meta"),
    }
    encoded = json.dumps(stable, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def chart_visibility_from_payload(payload: dict) -> dict:
    meta = payload.get("meta", {})
    visibility = dict(meta.get("line_visibility") or {})
    visibility["calculation_lines"] = (meta.get("forecast") or {}).get("calculation_lines", [])
    visibility["recession_bands_data"] = meta.get("recession_bands") or []
    visibility["price_points"] = meta.get("price_points") or []
    visibility["price_points_meta"] = meta.get("price_points_meta") or {}
    visibility["ticker"] = meta.get("ticker")
    visibility["metric"] = meta.get("metric")
    visibility["metric_label"] = meta.get("metric_label")
    visibility["data_mode"] = meta.get("data_mode")
    visibility["data_backend"] = meta.get("data_backend")
    return visibility


def _chart_visibility(payload: dict) -> dict:
    return chart_visibility_from_payload(payload)


def _chart_cache_root() -> Path:
    configured = os.getenv("CHART_CACHE_DIR")
    if configured:
        return Path(configured)
    if _env_truthy("VERCEL"):
        return Path(tempfile.gettempdir()) / "personal-fastgraphs" / "rendered_charts"
    return Path("storage/rendered_charts")


def _canonical_ticker_segment(ticker: str) -> str:
    if not isinstance(ticker, str):
        raise ValueError("ticker must be a string")
    normalized = ticker.strip().upper()
    if (
        not normalized
        or len(normalized) > MAX_TICKER_SEGMENT_LENGTH
        or any(char not in TICKER_SEGMENT_CHARS for char in normalized)
        or normalized[0] in TICKER_SEGMENT_SEPARATORS
        or normalized[-1] in TICKER_SEGMENT_SEPARATORS
        or any(pair in normalized for pair in ("..", "--", ".-", "-."))
    ):
        raise ValueError(
            "ticker must use letters and numbers with optional single '.' or '-' separators"
        )
    return normalized


def _valuation_chart_cache_path(ticker_segment: str, cache_key: str, chart_format: str) -> Path:
    root = _chart_cache_root().resolve()
    path = (
        root
        / "valuation-map"
        / ticker_segment
        / f"{cache_key}.{chart_format}"
    ).resolve()
    if root not in path.parents:
        raise ValueError("chart cache path resolves outside the configured cache directory")
    return path


def _queue_chart_blob(
    local_path: Path,
    blob_key: str,
    content_type: str,
    ticker: str,
    payload: dict,
    cache_key: str,
) -> str:
    queue = BlobUploadQueue()
    manifest = queue.enqueue(
        BlobQueueItem(
            local_path=str(local_path),
            blob_key=blob_key,
            content_type=content_type,
            metadata={
                "ticker": ticker.upper(),
                "chart_type": "valuation_map",
                "cache_key": cache_key,
                "metric": payload.get("meta", {}).get("metric"),
                "data_mode": payload.get("meta", {}).get("data_mode"),
                "data_backend": payload.get("meta", {}).get("data_backend"),
            },
        )
    )
    return str(manifest)


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").lower() in {"1", "true", "yes", "on"}
