from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text

from packages.valuation.chart import chart_source_summary
from services.api.chart_cache import chart_visibility_from_payload, valuation_chart_cache_key
from services.api.database import get_engine, postgres_enabled


def create_chart_run(request_params: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    ticker = str(payload["meta"]["ticker"]).upper()
    chart_run_id = _chart_run_id(ticker, request_params, payload)
    created_at = datetime.now(UTC).isoformat()
    svg_cache_key = valuation_chart_cache_key(ticker, payload, "svg")
    png_cache_key = valuation_chart_cache_key(ticker, payload, "png")
    record = {
        "id": chart_run_id,
        "ticker": ticker,
        "metric": payload["meta"].get("metric"),
        "request_params": _json_safe(request_params),
        "payload": payload,
        "line_visibility": payload["meta"].get("line_visibility", {}),
        "data_mode": payload["meta"].get("data_mode"),
        "data_backend": payload["meta"].get("data_backend"),
        "svg_cache_key": svg_cache_key,
        "png_cache_key": png_cache_key,
        "svg_blob_key": f"rendered/charts/valuation-map/{ticker}/{svg_cache_key}.svg",
        "png_blob_key": f"rendered/charts/valuation-map/{ticker}/{png_cache_key}.png",
        "created_at": created_at,
    }
    record = _with_evidence_summary(record)
    if postgres_enabled():
        _store_chart_run_postgres(record)
    else:
        _store_chart_run_manifest(record)
    return record


def load_chart_run(chart_run_id: str) -> dict[str, Any] | None:
    if postgres_enabled():
        record = _load_chart_run_postgres(chart_run_id)
        if record is not None:
            return _with_evidence_summary(record)
    record = _load_chart_run_manifest(chart_run_id)
    return _with_evidence_summary(record) if record is not None else None


def _chart_run_id(ticker: str, request_params: dict[str, Any], payload: dict[str, Any]) -> str:
    stable = {
        "ticker": ticker,
        "request_params": _json_safe(request_params),
        "payload": payload,
    }
    encoded = json.dumps(stable, sort_keys=True, separators=(",", ":"), default=str)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"personal-fastgraphs:chart-run:{encoded}"))


def _store_chart_run_postgres(record: dict[str, Any]) -> None:
    with get_engine().begin() as connection:
        security = connection.execute(
            text("SELECT id FROM securities WHERE ticker = :ticker"),
            {"ticker": record["ticker"]},
        ).mappings().first()
        connection.execute(
            text(
                """
                INSERT INTO chart_runs (
                  id, security_id, ticker, metric, request_params, payload, line_visibility,
                  data_mode, data_backend, svg_cache_key, png_cache_key, svg_blob_key,
                  png_blob_key, created_at
                )
                VALUES (
                  :id, :security_id, :ticker, :metric, CAST(:request_params AS jsonb),
                  CAST(:payload AS jsonb), CAST(:line_visibility AS jsonb), :data_mode,
                  :data_backend, :svg_cache_key, :png_cache_key, :svg_blob_key,
                  :png_blob_key, :created_at
                )
                ON CONFLICT (id) DO UPDATE SET
                  request_params = EXCLUDED.request_params,
                  payload = EXCLUDED.payload,
                  line_visibility = EXCLUDED.line_visibility,
                  data_mode = EXCLUDED.data_mode,
                  data_backend = EXCLUDED.data_backend,
                  svg_cache_key = EXCLUDED.svg_cache_key,
                  png_cache_key = EXCLUDED.png_cache_key,
                  svg_blob_key = EXCLUDED.svg_blob_key,
                  png_blob_key = EXCLUDED.png_blob_key
                """
            ),
            {
                "id": uuid.UUID(record["id"]),
                "security_id": security["id"] if security else None,
                "ticker": record["ticker"],
                "metric": record["metric"],
                "request_params": json.dumps(record["request_params"]),
                "payload": json.dumps(record["payload"]),
                "line_visibility": json.dumps(record["line_visibility"]),
                "data_mode": record["data_mode"],
                "data_backend": record["data_backend"],
                "svg_cache_key": record["svg_cache_key"],
                "png_cache_key": record["png_cache_key"],
                "svg_blob_key": record["svg_blob_key"],
                "png_blob_key": record["png_blob_key"],
                "created_at": datetime.fromisoformat(record["created_at"]),
            },
        )


def _load_chart_run_postgres(chart_run_id: str) -> dict[str, Any] | None:
    with get_engine().connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT id, ticker, metric, request_params, payload, line_visibility,
                       data_mode, data_backend, svg_cache_key, png_cache_key,
                       svg_blob_key, png_blob_key, created_at
                FROM chart_runs
                WHERE id = :id
                """
            ),
            {"id": uuid.UUID(chart_run_id)},
        ).mappings().first()
    if row is None:
        return None
    return {
        "id": str(row["id"]),
        "ticker": row["ticker"],
        "metric": row["metric"],
        "request_params": row["request_params"] or {},
        "payload": row["payload"],
        "line_visibility": row["line_visibility"] or {},
        "data_mode": row["data_mode"],
        "data_backend": row["data_backend"],
        "svg_cache_key": row["svg_cache_key"],
        "png_cache_key": row["png_cache_key"],
        "svg_blob_key": row["svg_blob_key"],
        "png_blob_key": row["png_blob_key"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


def _store_chart_run_manifest(record: dict[str, Any]) -> None:
    path = _manifest_path(record["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_chart_run_manifest(chart_run_id: str) -> dict[str, Any] | None:
    path = _manifest_path(chart_run_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _manifest_path(chart_run_id: str) -> Path:
    return _chart_run_dir() / f"{chart_run_id}.json"


def _chart_run_dir() -> Path:
    return Path(os.getenv("CHART_RUN_DIR") or "storage/chart_runs")


def _json_safe(value):
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value) if not isinstance(value, str | int | float | bool | type(None)) else value


def _with_evidence_summary(record: dict[str, Any]) -> dict[str, Any]:
    payload = record.get("payload") or {}
    enriched = dict(record)
    if enriched.get("evidence_summary"):
        return enriched
    try:
        enriched["evidence_summary"] = chart_source_summary(
            payload.get("data") or [],
            chart_visibility_from_payload(payload),
        )
    except Exception as exc:
        enriched["evidence_summary"] = {
            "error": "chart_source_summary_failed",
            "detail": str(exc),
        }
    return enriched
