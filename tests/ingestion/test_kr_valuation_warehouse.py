from __future__ import annotations

import json
from pathlib import Path

import duckdb

from services.ingestion_worker.kr_valuation_warehouse import (
    KR_NORMALIZED_FACTS_VIEW,
    KR_VALUATION_POINTS_VIEW,
    load_kr_valuation_cache_to_warehouse,
)
from services.api.kr_warehouse_provider import (
    source_coverage_rows_from_kr_warehouse,
    valuation_points_from_kr_warehouse,
)
from services.ingestion_worker.cli import source_coverage_report


def test_load_kr_valuation_cache_to_warehouse_creates_duckdb_views(tmp_path):
    cache_dir = tmp_path / "storage" / "cache" / "kr-valuation-inputs"
    cache_dir.mkdir(parents=True)
    cache_path = cache_dir / "005930_KS-2020-2025-valuation-inputs.json"
    cache_path.write_text(
        json.dumps(_cache_payload("005930.KS", _source_trace()), ensure_ascii=False),
        encoding="utf-8",
    )

    warehouse_root = tmp_path / "data" / "warehouse" / "kr_valuation"
    db_path = tmp_path / "data" / "warehouse" / "warehouse.duckdb"
    summary = load_kr_valuation_cache_to_warehouse(
        "005930.KS",
        cache_dir=cache_dir,
        warehouse_root=warehouse_root,
        db_path=db_path,
        strict=True,
    )

    assert summary["status"] == "ok"
    assert summary["fact_rows_written"] == 1
    assert summary["valuation_points_written"] == 1
    assert Path(summary["output_paths"]["normalized_facts"]).exists()
    assert Path(summary["output_paths"]["valuation_points"]).exists()
    assert summary["views"]["normalized_facts"] == KR_NORMALIZED_FACTS_VIEW
    assert summary["views"]["valuation_points"] == KR_VALUATION_POINTS_VIEW

    with duckdb.connect(str(db_path)) as connection:
        fact_rows = connection.execute(
            f"SELECT ticker, metric, source_document_id FROM {KR_NORMALIZED_FACTS_VIEW}"
        ).fetchall()
        valuation_rows = connection.execute(
            f"SELECT ticker, fiscal_year, metric, source_document_id FROM {KR_VALUATION_POINTS_VIEW}"
        ).fetchall()

    assert fact_rows == [("005930.KS", "adjusted_operating_eps", "storage/raw/kr/opendart/005930/2024.json")]
    assert valuation_rows == [
        ("005930.KS", 2024, "adjusted_operating_eps", "storage/raw/kr/opendart/005930/2024.json")
    ]


def test_load_kr_valuation_cache_to_warehouse_rejects_missing_source_trace(tmp_path):
    cache_dir = tmp_path / "storage" / "cache" / "kr-valuation-inputs"
    cache_dir.mkdir(parents=True)
    bad_trace = _source_trace()
    bad_trace.pop("source_document_id")
    cache_path = cache_dir / "005930_KS-2020-2025-valuation-inputs.json"
    cache_path.write_text(
        json.dumps(_cache_payload("005930.KS", bad_trace), ensure_ascii=False),
        encoding="utf-8",
    )

    summary = load_kr_valuation_cache_to_warehouse(
        "005930",
        cache_dir=cache_dir,
        warehouse_root=tmp_path / "warehouse",
        db_path=tmp_path / "warehouse.duckdb",
        strict=True,
    )

    assert summary["status"] == "failed"
    assert summary["fact_rows_written"] == 0
    assert summary["valuation_points_written"] == 0
    assert summary["rejected_fact_rows"] == 1
    assert summary["rejected_valuation_points"] == 1
    assert "rejected_kr_fact_rows_missing_source_trace" in summary["quality_flags"]
    assert "rejected_kr_valuation_points_missing_source_trace" in summary["quality_flags"]

    with duckdb.connect(str(tmp_path / "warehouse.duckdb")) as connection:
        assert connection.execute(f"SELECT COUNT(*) FROM {KR_NORMALIZED_FACTS_VIEW}").fetchone()[0] == 0
        assert connection.execute(f"SELECT COUNT(*) FROM {KR_VALUATION_POINTS_VIEW}").fetchone()[0] == 0


def test_kr_warehouse_provider_promotes_price_and_dividend_source_traces(tmp_path, monkeypatch):
    cache_dir = tmp_path / "storage" / "cache" / "kr-valuation-inputs"
    cache_dir.mkdir(parents=True)
    cache_path = cache_dir / "005930_KS-2020-2025-valuation-inputs.json"
    cache_path.write_text(
        json.dumps(_cache_payload_with_joined_traces("005930.KS"), ensure_ascii=False),
        encoding="utf-8",
    )
    db_path = tmp_path / "data" / "warehouse" / "warehouse.duckdb"
    summary = load_kr_valuation_cache_to_warehouse(
        "005930.KS",
        cache_dir=cache_dir,
        warehouse_root=tmp_path / "data" / "warehouse" / "kr_valuation",
        db_path=db_path,
        strict=True,
    )
    assert summary["status"] == "ok"
    assert summary["fact_rows_written"] == 3
    assert summary["valuation_points_written"] == 1

    monkeypatch.setenv("KR_VALUATION_WAREHOUSE_DB", str(db_path))
    payload = valuation_points_from_kr_warehouse("005930.KS", "adjusted_operating")

    assert payload is not None
    assert payload.meta["data_backend"] == "kr_valuation_warehouse"
    point_trace = payload.points[0].source_trace
    assert point_trace["price_source_trace"]["source_type"] == "pykrx_ohlcv"
    assert point_trace["price_source_trace"]["cache_path"].endswith("005930_KS-2020-2025-valuation-inputs.json")
    assert point_trace["price_source_trace"]["warehouse_view"] == KR_NORMALIZED_FACTS_VIEW
    assert point_trace["metric_source_trace"]["source_type"] == "opendart_xbrl"
    assert point_trace["dividend_source_trace"]["source_type"] == "opendart_dividend"
    assert point_trace["dividend_source_trace"]["cache_path"].endswith("005930_KS-2020-2025-valuation-inputs.json")
    assert payload.price_points[0]["source_trace"]["cache_path"].endswith("005930_KS-2020-2025-valuation-inputs.json")
    assert payload.price_points[0]["source_trace"]["loaded_at"]


def test_source_coverage_uses_local_kr_warehouse_when_postgres_is_missing(tmp_path, monkeypatch):
    cache_dir = tmp_path / "storage" / "cache" / "kr-valuation-inputs"
    cache_dir.mkdir(parents=True)
    cache_payload = _cache_payload_with_joined_traces("005930.KS")
    cache_payload["normalized_facts"].extend(
        [
            _fact_payload(
                "005930.KS",
                "market_cap",
                2200000000000000.0,
                {
                    **_source_trace(),
                    "source": "marcap",
                    "source_type": "marcap_market_cap",
                    "source_document_id": "raw:marcap:005930.KS:2024:market-cap",
                    "filing_id": "marcap-005930-2024-market-cap",
                    "unit": "KRW",
                    "method": "MARCAP_YEAR_END_MARKET_CAP",
                    "formula": "market_cap = source-backed year-end marcap value",
                },
            ),
            _fact_payload(
                "005930.KS",
                "listed_shares",
                5969782550.0,
                {
                    **_source_trace(),
                    "source": "marcap",
                    "source_type": "marcap_listed_shares",
                    "source_document_id": "raw:marcap:005930.KS:2024:listed-shares",
                    "filing_id": "marcap-005930-2024-listed-shares",
                    "unit": "shares",
                    "method": "MARCAP_YEAR_END_LISTED_SHARES",
                    "formula": "listed_shares = source-backed year-end listed shares",
                },
            ),
        ]
    )
    cache_path = cache_dir / "005930_KS-2020-2025-valuation-inputs.json"
    cache_path.write_text(json.dumps(cache_payload, ensure_ascii=False), encoding="utf-8")
    db_path = tmp_path / "data" / "warehouse" / "warehouse.duckdb"
    summary = load_kr_valuation_cache_to_warehouse(
        "005930.KS",
        cache_dir=cache_dir,
        warehouse_root=tmp_path / "data" / "warehouse" / "kr_valuation",
        db_path=db_path,
        strict=True,
    )
    assert summary["status"] == "ok"

    monkeypatch.setenv("KR_VALUATION_WAREHOUSE_DB", str(db_path))
    monkeypatch.delenv("DATA_BACKEND", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    rows = source_coverage_rows_from_kr_warehouse(["005930.KS"])
    assert rows is not None
    assert rows[0]["adjusted_years"] == 1
    assert rows[0]["price_years"] == 1
    assert rows[0]["market_cap_years"] == 1
    assert rows[0]["listed_shares_years"] == 1

    report = source_coverage_report(
        "005930.KS",
        market="KR",
        min_historical_years=1,
    )
    assert report["status"] == "ready"
    assert report["data_backend"] == "kr_valuation_warehouse"
    assert report["data_mode"] == "local_source_backed_warehouse"
    assert report["local_warehouse"]["enabled"] is True
    assert report["tickers"][0]["core_ready"] is True


def _cache_payload(ticker: str, source_trace: dict[str, object]) -> dict[str, object]:
    return {
        "ticker": ticker,
        "entity_id": f"krx:{ticker}",
        "market": "KR",
        "status": "ok",
        "coverage_status": "complete",
        "normalized_facts": [
            {
                "fact_id": f"fact:kr:{ticker}:2024:adjusted_operating_eps",
                "entity_id": f"krx:{ticker}",
                "ticker": ticker,
                "metric": "adjusted_operating_eps",
                "period": "FY2024",
                "fiscal_year": 2024,
                "fiscal_period": "FY",
                "value": 44800.0,
                "unit": "KRW/share",
                "currency": "KRW",
                "version": 1,
                "source_trace": source_trace,
            }
        ],
        "valuation_points": [
            {
                "valuation_point_id": f"valuation-point:kr:{ticker}:2024:adjusted_operating_eps",
                "entity_id": f"krx:{ticker}",
                "ticker": ticker,
                "fiscal_year": 2024,
                "period": "FY2024",
                "metric": "adjusted_operating_eps",
                "metric_value": 44800.0,
                "price": 339500.0,
                "currency": "KRW",
                "quality_flags": ["source_trace_passed"],
                "source_trace": source_trace,
            }
        ],
        "quality_flags": ["source_trace_passed"],
    }


def _cache_payload_with_joined_traces(ticker: str) -> dict[str, object]:
    metric_trace = {
        **_source_trace(),
        "source": "opendart",
        "source_type": "opendart_xbrl",
        "source_document_id": "raw:opendart:005930.KS:2024:eps",
        "filing_id": "opendart-005930-2024-eps",
        "unit": "KRW/share",
        "method": "S3_MARKET_STANDARD_KR",
        "formula": "adjusted_operating_eps = KR market-standard reported EPS source row",
    }
    price_trace = {
        **_source_trace(),
        "source": "pykrx",
        "source_type": "pykrx_ohlcv",
        "source_document_id": "raw:pykrx:005930.KS:2024:ohlcv",
        "filing_id": "pykrx-005930-2024-ohlcv",
        "unit": "KRW/share",
        "method": "PYKRX_YEAR_END_CLOSE",
        "formula": "price_close = source-backed year-end close from pykrx OHLCV",
    }
    dividend_trace = {
        **_source_trace(),
        "source": "opendart",
        "source_type": "opendart_dividend",
        "source_document_id": "raw:opendart_dividends:005930.KS:2024",
        "filing_id": "opendart-dividend-005930-2024",
        "unit": "KRW/share",
        "method": "OPENDART_DIVIDEND_PER_SHARE",
        "formula": "dividend_per_share = source-backed cash dividend per share",
    }
    valuation_trace = {
        **_source_trace(),
        "source": "derived",
        "source_type": "derived_valuation_input",
        "source_document_id": "derived:kr:005930.KS:2024:valuation-input",
        "filing_id": "KR_VALUATION_INPUT_005930.KS_2024",
        "method": "KR_SOURCE_BACKED_PRICE_EPS_JOIN",
        "formula": "valuation_input = source-backed price joined to source-backed EPS and dividend",
        "input_fact_ids": [
            "fact:kr:005930.KS:2024:price_close",
            "fact:kr:005930.KS:2024:adjusted_operating_eps",
            "fact:kr:005930.KS:2024:dividend_per_share",
        ],
        "quality_status": "source_backed",
        "quality_flags": ["source_backed", "source_backed_valuation_input", "source_backed_dividend"],
        "metadata": {
            "price_source_trace": price_trace,
            "metric_source_trace": metric_trace,
            "dividend_source_trace": dividend_trace,
        },
    }
    return {
        "ticker": ticker,
        "entity_id": f"krx:{ticker}",
        "market": "KR",
        "status": "ok",
        "coverage_status": "complete",
        "normalized_facts": [
            _fact_payload(ticker, "adjusted_operating_eps", 44800.0, metric_trace),
            _fact_payload(ticker, "price_close", 339500.0, price_trace),
            _fact_payload(ticker, "dividend_per_share", 1444.0, dividend_trace),
        ],
        "valuation_points": [
            {
                "valuation_point_id": f"valuation-point:kr:{ticker}:2024:adjusted_operating_eps",
                "entity_id": f"krx:{ticker}",
                "ticker": ticker,
                "fiscal_year": 2024,
                "period": "FY2024",
                "metric": "adjusted_operating_eps",
                "metric_value": 44800.0,
                "price": 339500.0,
                "currency": "KRW",
                "quality_flags": ["source_backed_valuation_input", "source_backed_dividend"],
                "source_trace": valuation_trace,
            }
        ],
        "quality_flags": ["source_trace_passed"],
    }


def _fact_payload(
    ticker: str,
    metric: str,
    value: float,
    source_trace: dict[str, object],
) -> dict[str, object]:
    return {
        "fact_id": f"fact:kr:{ticker}:2024:{metric}",
        "entity_id": f"krx:{ticker}",
        "ticker": ticker,
        "metric": metric,
        "period": "FY2024",
        "fiscal_year": 2024,
        "fiscal_period": "FY",
        "value": value,
        "unit": source_trace["unit"],
        "currency": "KRW",
        "version": 1,
        "source_trace": source_trace,
    }


def _source_trace() -> dict[str, object]:
    return {
        "source": "opendart",
        "source_type": "opendart_xbrl",
        "source_document_id": "storage/raw/kr/opendart/005930/2024.json",
        "filing_id": "opendart-005930-2024",
        "accession_number": "opendart-005930-2024",
        "form": "annual_report",
        "form_type": "annual_report",
        "period": "FY2024",
        "fiscal_year": 2024,
        "fiscal_period": "FY",
        "available_at": "2025-03-31T00:00:00+00:00",
        "unit": "KRW/share",
        "currency": "KRW",
        "method": "S3_MARKET_STANDARD_KR",
        "formula": "adjusted_operating_eps = KR market-standard reported EPS source row",
        "confidence": "0.92",
        "quality_status": "passed",
        "quality_flags": ["source_trace_passed"],
        "version": 1,
    }
