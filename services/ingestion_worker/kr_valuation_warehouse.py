from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import pyarrow as pa
import pyarrow.parquet as pq

from data.local_store import DuckDBWarehouse
from packages.quality import validate_source_trace

DEFAULT_KR_VALUATION_CACHE_DIR = Path("storage/cache/kr-valuation-inputs")
DEFAULT_KR_VALUATION_WAREHOUSE_ROOT = Path("data/warehouse/kr_valuation")
DEFAULT_WAREHOUSE_DB_PATH = Path("data/warehouse/warehouse.duckdb")

KR_NORMALIZED_FACTS_VIEW = "kr_normalized_facts"
KR_VALUATION_POINTS_VIEW = "kr_valuation_points"

_NON_PRODUCTION_MARKERS = (
    "fixture",
    "mock",
    "dummy",
    "sample",
    "synthetic",
    "non-production",
    "non_production",
)

_FACT_SCHEMA = pa.schema(
    [
        ("fact_id", pa.string()),
        ("entity_id", pa.string()),
        ("ticker", pa.string()),
        ("metric", pa.string()),
        ("fiscal_year", pa.int64()),
        ("fiscal_period", pa.string()),
        ("period", pa.string()),
        ("value", pa.float64()),
        ("unit", pa.string()),
        ("currency", pa.string()),
        ("version", pa.int64()),
        ("source_document_id", pa.string()),
        ("filing_id", pa.string()),
        ("method", pa.string()),
        ("formula", pa.string()),
        ("quality_status", pa.string()),
        ("source_trace_json", pa.string()),
        ("cache_path", pa.string()),
        ("loaded_at", pa.string()),
    ]
)

_VALUATION_POINT_SCHEMA = pa.schema(
    [
        ("valuation_point_id", pa.string()),
        ("entity_id", pa.string()),
        ("ticker", pa.string()),
        ("fiscal_year", pa.int64()),
        ("period", pa.string()),
        ("metric", pa.string()),
        ("metric_value", pa.float64()),
        ("price", pa.float64()),
        ("currency", pa.string()),
        ("source_document_id", pa.string()),
        ("filing_id", pa.string()),
        ("method", pa.string()),
        ("formula", pa.string()),
        ("quality_status", pa.string()),
        ("quality_flags_json", pa.string()),
        ("source_trace_json", pa.string()),
        ("cache_path", pa.string()),
        ("loaded_at", pa.string()),
    ]
)


def load_kr_valuation_cache_to_warehouse(
    tickers: str | Iterable[str],
    *,
    cache_dir: Path = DEFAULT_KR_VALUATION_CACHE_DIR,
    warehouse_root: Path = DEFAULT_KR_VALUATION_WAREHOUSE_ROOT,
    db_path: Path = DEFAULT_WAREHOUSE_DB_PATH,
    strict: bool = False,
) -> dict[str, Any]:
    """Promote source-backed KR valuation cache rows into Parquet/DuckDB views."""

    requested_tickers = [_canonical_kr_ticker(ticker) for ticker in _parse_tickers(tickers)]
    loaded_at = datetime.now(UTC).isoformat()

    fact_rows: list[dict[str, Any]] = []
    valuation_rows: list[dict[str, Any]] = []
    ticker_summaries: list[dict[str, Any]] = []
    rejected_fact_rows = 0
    rejected_valuation_points = 0
    missing_tickers: list[str] = []

    for ticker in requested_tickers:
        payload, cache_path = _latest_cache_payload(ticker, cache_dir)
        if payload is None or cache_path is None:
            missing_tickers.append(ticker)
            ticker_summaries.append(
                {
                    "ticker": ticker,
                    "cache_found": False,
                    "fact_rows_loaded": 0,
                    "valuation_points_loaded": 0,
                    "rejected_fact_rows": 0,
                    "rejected_valuation_points": 0,
                }
            )
            continue

        ticker_fact_count = 0
        ticker_point_count = 0
        ticker_rejected_facts = 0
        ticker_rejected_points = 0

        for fact in payload.get("normalized_facts") or []:
            if not isinstance(fact, dict):
                rejected_fact_rows += 1
                ticker_rejected_facts += 1
                continue
            trace = fact.get("source_trace")
            if not _is_warehouse_eligible_trace(trace):
                rejected_fact_rows += 1
                ticker_rejected_facts += 1
                continue
            fact_rows.append(_flatten_fact_row(fact, trace, cache_path, loaded_at))
            ticker_fact_count += 1

        for point in payload.get("valuation_points") or []:
            if not isinstance(point, dict):
                rejected_valuation_points += 1
                ticker_rejected_points += 1
                continue
            trace = point.get("source_trace")
            if not _is_warehouse_eligible_trace(trace):
                rejected_valuation_points += 1
                ticker_rejected_points += 1
                continue
            valuation_rows.append(_flatten_valuation_point(point, trace, cache_path, loaded_at))
            ticker_point_count += 1

        ticker_summaries.append(
            {
                "ticker": ticker,
                "cache_found": True,
                "cache_path": str(cache_path),
                "fact_rows_loaded": ticker_fact_count,
                "valuation_points_loaded": ticker_point_count,
                "rejected_fact_rows": ticker_rejected_facts,
                "rejected_valuation_points": ticker_rejected_points,
                "coverage_status": payload.get("coverage_status"),
                "valuation_ready": bool(ticker_point_count),
            }
        )

    warehouse_root.mkdir(parents=True, exist_ok=True)
    facts_path = warehouse_root / "normalized_facts.parquet"
    valuation_points_path = warehouse_root / "valuation_points.parquet"
    _write_parquet(fact_rows, facts_path, _FACT_SCHEMA)
    _write_parquet(valuation_rows, valuation_points_path, _VALUATION_POINT_SCHEMA)
    _create_duckdb_views(db_path, facts_path, valuation_points_path)

    status = _summary_status(
        requested_tickers=requested_tickers,
        missing_tickers=missing_tickers,
        valuation_rows=valuation_rows,
        rejected_fact_rows=rejected_fact_rows,
        rejected_valuation_points=rejected_valuation_points,
        strict=strict,
    )
    quality_flags = _quality_flags(
        missing_tickers=missing_tickers,
        rejected_fact_rows=rejected_fact_rows,
        rejected_valuation_points=rejected_valuation_points,
        valuation_rows=valuation_rows,
    )
    quality_status = "passed" if status == "ok" else "warning"

    return {
        "status": status,
        "market": "KR",
        "data_backend": "duckdb_parquet",
        "tickers_expected": len(requested_tickers),
        "cache_files_found": len(requested_tickers) - len(missing_tickers),
        "missing_tickers": missing_tickers,
        "fact_rows_written": len(fact_rows),
        "valuation_points_written": len(valuation_rows),
        "rejected_fact_rows": rejected_fact_rows,
        "rejected_valuation_points": rejected_valuation_points,
        "strict": strict,
        "output_paths": {
            "normalized_facts": str(facts_path),
            "valuation_points": str(valuation_points_path),
            "duckdb": str(db_path),
        },
        "views": {
            "normalized_facts": KR_NORMALIZED_FACTS_VIEW,
            "valuation_points": KR_VALUATION_POINTS_VIEW,
        },
        "rows": ticker_summaries,
        "quality_status": quality_status,
        "quality_flags": quality_flags,
        "source_trace": {
            "source": "kr_valuation_warehouse_loader",
            "source_type": "duckdb_parquet_loader",
            "source_document_id": str(valuation_points_path),
            "filing_id": "KR-VALUATION-WAREHOUSE-LOAD",
            "period": "latest_source_backed_cache",
            "available_at": loaded_at,
            "unit": "row_count",
            "currency": "KRW",
            "method": "KR_CACHE_TO_DUCKDB_PARQUET",
            "formula": (
                "Validate source_trace for KR valuation input cache rows, reject non-production "
                "or incomplete traces, write normalized facts and valuation points to Parquet, "
                "and expose DuckDB views."
            ),
            "quality_status": quality_status,
            "quality_flags": quality_flags,
        },
    }


def _parse_tickers(tickers: str | Iterable[str]) -> list[str]:
    if isinstance(tickers, str):
        raw = tickers.split(",")
    else:
        raw = list(tickers)
    return [str(ticker).strip() for ticker in raw if str(ticker).strip()]


def _canonical_kr_ticker(ticker: str) -> str:
    normalized = ticker.strip().upper()
    if "." not in normalized and normalized.isdigit() and len(normalized) == 6:
        return f"{normalized}.KS"
    return normalized


def _latest_cache_payload(ticker: str, cache_dir: Path) -> tuple[dict[str, Any] | None, Path | None]:
    normalized = ticker.upper().replace(".", "_")
    paths = sorted(
        Path(cache_dir).glob(f"{normalized}-*-valuation-inputs.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if _canonical_kr_ticker(str(payload.get("ticker") or "")).upper() == ticker.upper():
            return payload, path
    return None, None


def _is_warehouse_eligible_trace(trace: Any) -> bool:
    if not isinstance(trace, dict):
        return False
    if validate_source_trace(trace).status != "passed":
        return False
    return not _contains_non_production_marker(trace)


def _contains_non_production_marker(value: Any) -> bool:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True).lower()
    except TypeError:
        text = str(value).lower()
    return any(marker in text for marker in _NON_PRODUCTION_MARKERS)


def _flatten_fact_row(
    fact: dict[str, Any],
    trace: dict[str, Any],
    cache_path: Path,
    loaded_at: str,
) -> dict[str, Any]:
    return {
        "fact_id": str(fact.get("fact_id") or ""),
        "entity_id": str(fact.get("entity_id") or ""),
        "ticker": _canonical_kr_ticker(str(fact.get("ticker") or "")),
        "metric": str(fact.get("metric") or ""),
        "fiscal_year": _int_or_none(fact.get("fiscal_year")),
        "fiscal_period": str(fact.get("fiscal_period") or trace.get("fiscal_period") or ""),
        "period": str(fact.get("period") or trace.get("period") or ""),
        "value": _float_or_none(fact.get("value")),
        "unit": str(fact.get("unit") or trace.get("unit") or ""),
        "currency": str(fact.get("currency") or trace.get("currency") or ""),
        "version": _int_or_none(fact.get("version")) or 1,
        "source_document_id": str(trace.get("source_document_id") or ""),
        "filing_id": str(trace.get("filing_id") or trace.get("accession_number") or ""),
        "method": str(trace.get("method") or ""),
        "formula": str(trace.get("formula") or ""),
        "quality_status": str(trace.get("quality_status") or ""),
        "source_trace_json": _json_dumps(trace),
        "cache_path": str(cache_path),
        "loaded_at": loaded_at,
    }


def _flatten_valuation_point(
    point: dict[str, Any],
    trace: dict[str, Any],
    cache_path: Path,
    loaded_at: str,
) -> dict[str, Any]:
    return {
        "valuation_point_id": str(point.get("valuation_point_id") or ""),
        "entity_id": str(point.get("entity_id") or ""),
        "ticker": _canonical_kr_ticker(str(point.get("ticker") or "")),
        "fiscal_year": _int_or_none(point.get("fiscal_year")),
        "period": str(point.get("period") or trace.get("period") or ""),
        "metric": str(point.get("metric") or ""),
        "metric_value": _float_or_none(point.get("metric_value")),
        "price": _float_or_none(point.get("price")),
        "currency": str(point.get("currency") or trace.get("currency") or ""),
        "source_document_id": str(trace.get("source_document_id") or ""),
        "filing_id": str(trace.get("filing_id") or trace.get("accession_number") or ""),
        "method": str(trace.get("method") or ""),
        "formula": str(trace.get("formula") or ""),
        "quality_status": str(trace.get("quality_status") or ""),
        "quality_flags_json": _json_dumps(point.get("quality_flags") or trace.get("quality_flags") or []),
        "source_trace_json": _json_dumps(trace),
        "cache_path": str(cache_path),
        "loaded_at": loaded_at,
    }


def _int_or_none(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    return int(value)


def _float_or_none(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    return float(value)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _write_parquet(rows: list[dict[str, Any]], path: Path, schema: pa.Schema) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows, schema=schema)
    pq.write_table(table, path)


def _create_duckdb_views(db_path: Path, facts_path: Path, valuation_points_path: Path) -> None:
    warehouse = DuckDBWarehouse(db_path)
    with warehouse.connect() as connection:
        connection.execute(
            (
                f"CREATE OR REPLACE VIEW {KR_NORMALIZED_FACTS_VIEW} AS "
                f"SELECT * FROM parquet_scan('{_duckdb_path(facts_path)}')"
            )
        )
        connection.execute(
            (
                f"CREATE OR REPLACE VIEW {KR_VALUATION_POINTS_VIEW} AS "
                f"SELECT * FROM parquet_scan('{_duckdb_path(valuation_points_path)}')"
            )
        )


def _duckdb_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace("'", "''")


def _summary_status(
    *,
    requested_tickers: list[str],
    missing_tickers: list[str],
    valuation_rows: list[dict[str, Any]],
    rejected_fact_rows: int,
    rejected_valuation_points: int,
    strict: bool,
) -> str:
    if not requested_tickers or not valuation_rows:
        return "failed"
    if missing_tickers or rejected_fact_rows or rejected_valuation_points:
        return "failed" if strict else "warning"
    return "ok"


def _quality_flags(
    *,
    missing_tickers: list[str],
    rejected_fact_rows: int,
    rejected_valuation_points: int,
    valuation_rows: list[dict[str, Any]],
) -> list[str]:
    flags: list[str] = []
    if missing_tickers:
        flags.append("missing_kr_valuation_cache")
    if rejected_fact_rows:
        flags.append("rejected_kr_fact_rows_missing_source_trace")
    if rejected_valuation_points:
        flags.append("rejected_kr_valuation_points_missing_source_trace")
    if not valuation_rows:
        flags.append("missing_source_traced_valuation_points")
    return flags or ["source_trace_passed"]
