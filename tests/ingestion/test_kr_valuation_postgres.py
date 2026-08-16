from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace

from services.ingestion_worker import kr_valuation_postgres
from services.ingestion_worker.kr_valuation_postgres import (
    DEFAULT_POLICY_KEY,
    load_kr_valuation_cache_to_postgres,
)


def test_load_kr_valuation_cache_to_postgres_dry_run_counts_source_backed_rows(tmp_path):
    cache_dir = _write_cache(tmp_path, _cache_payload("005930.KS"))

    summary = load_kr_valuation_cache_to_postgres(
        "005930.KS",
        cache_dir=cache_dir,
        dry_run=True,
        strict=True,
    )

    assert summary["status"] == "ok"
    assert summary["dry_run"] is True
    assert summary["policy"] == DEFAULT_POLICY_KEY
    assert summary["source_documents"] == 1
    assert summary["raw_objects"] == 1
    assert summary["adjusted_earnings"] == 1
    assert summary["metric_values"] == 5
    assert summary["financial_facts"] == 2
    assert summary["price_bars"] == 1
    assert summary["dividends"] == 1
    assert summary["rows"][0]["valuation_ready"] is True
    assert summary["quality_flags"] == ["source_trace_passed"]


def test_load_kr_valuation_cache_to_postgres_rejects_incomplete_trace(tmp_path):
    payload = _cache_payload("005930.KS")
    payload["normalized_facts"][0]["source_trace"].pop("source_document_id")
    cache_dir = _write_cache(tmp_path, payload)

    summary = load_kr_valuation_cache_to_postgres(
        "005930.KS",
        cache_dir=cache_dir,
        dry_run=True,
        strict=True,
    )

    assert summary["status"] == "failed"
    assert summary["adjusted_earnings"] == 0
    assert summary["rejected_fact_rows"] == 1
    assert "rejected_kr_fact_rows_missing_source_trace" in summary["quality_flags"]


def test_load_kr_valuation_cache_to_postgres_persists_expected_tables(tmp_path, monkeypatch):
    cache_dir = _write_cache(tmp_path, _cache_payload("005930.KS"))
    fake_repo = FakeIngestionRepository()
    monkeypatch.setattr(
        kr_valuation_postgres,
        "IngestionRepository",
        lambda: fake_repo,
    )

    summary = load_kr_valuation_cache_to_postgres(
        "005930.KS",
        cache_dir=cache_dir,
        dry_run=False,
        strict=True,
    )

    assert summary["status"] == "ok"
    assert fake_repo.calls["ensure_security"] == 1
    assert fake_repo.calls["start_run"] == 1
    assert fake_repo.calls["finish_run"] == 1
    assert fake_repo.calls["store_source_document"] == 1
    assert fake_repo.calls["store_raw_object"] == 1
    assert fake_repo.calls["store_adjusted_earnings"] == 1
    assert fake_repo.calls["store_metric_value"] == 5
    assert fake_repo.calls["store_financial_fact"] == 2
    assert fake_repo.calls["store_price_bar"] == 1
    assert fake_repo.calls["store_dividend"] == 1
    price_trace = fake_repo.price_traces[0]
    assert price_trace["market_cap"] == 2200000000000000
    assert price_trace["listed_shares"] == 5969782550


class FakeIngestionRepository:
    def __init__(self) -> None:
        self.calls = {
            "ensure_security": 0,
            "start_run": 0,
            "finish_run": 0,
            "store_source_document": 0,
            "store_raw_object": 0,
            "store_adjusted_earnings": 0,
            "store_metric_value": 0,
            "store_financial_fact": 0,
            "store_price_bar": 0,
            "store_dividend": 0,
        }
        self.security_id = uuid.uuid4()
        self.source_document_id = uuid.uuid4()
        self.run_id = uuid.uuid4()
        self.price_traces: list[dict[str, object]] = []

    def ensure_security(self, **kwargs):
        self.calls["ensure_security"] += 1
        assert kwargs["country"] == "KR"
        assert kwargs["currency"] == "KRW"
        return SimpleNamespace(id=self.security_id)

    def start_run(self, *args, **kwargs):
        self.calls["start_run"] += 1
        return self.run_id

    def finish_run(self, *args, **kwargs):
        self.calls["finish_run"] += 1

    def store_source_document(self, *args, **kwargs):
        self.calls["store_source_document"] += 1
        return self.source_document_id

    def store_raw_object(self, *args, **kwargs):
        self.calls["store_raw_object"] += 1

    def store_adjusted_earnings(self, *args, **kwargs):
        self.calls["store_adjusted_earnings"] += 1
        record = args[2]
        assert record.policy == DEFAULT_POLICY_KEY
        assert record.method == "S3_MARKET_STANDARD_KR"

    def store_metric_value(self, *args, **kwargs):
        self.calls["store_metric_value"] += 1

    def store_financial_fact(self, *args, **kwargs):
        self.calls["store_financial_fact"] += 1

    def store_price_bar(self, *args, **kwargs):
        self.calls["store_price_bar"] += 1
        self.price_traces.append(args[6])

    def store_dividend(self, *args, **kwargs):
        self.calls["store_dividend"] += 1


def _write_cache(tmp_path: Path, payload: dict[str, object]) -> Path:
    cache_dir = tmp_path / "storage" / "cache" / "kr-valuation-inputs"
    cache_dir.mkdir(parents=True)
    cache_path = cache_dir / "005930_KS-2020-2025-valuation-inputs.json"
    cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return cache_dir


def _cache_payload(ticker: str) -> dict[str, object]:
    return {
        "ticker": ticker,
        "entity_id": f"krx:{ticker}",
        "market": "KR",
        "status": "ok",
        "valuation_ready": True,
        "coverage_status": "complete",
        "years": [2020, 2021, 2022, 2023, 2024],
        "normalized_facts": [
            _fact_payload(ticker, "adjusted_operating_eps", 44800.0, "opendart", "KRW/share"),
            _fact_payload(ticker, "price_close", 339500.0, "pykrx", "KRW/share"),
            _fact_payload(ticker, "dividend_per_share", 1444.0, "opendart_dividend", "KRW/share"),
            _fact_payload(ticker, "market_cap", 2200000000000000, "marcap", "KRW"),
            _fact_payload(ticker, "listed_shares", 5969782550, "marcap", "shares"),
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
                "quality_flags": ["source_backed_valuation_input"],
                "source_trace": _source_trace("derived", "derived_valuation_input", "KRW/share"),
            }
        ],
        "quality_flags": ["source_trace_passed"],
    }


def _fact_payload(
    ticker: str,
    metric: str,
    value: float | int,
    source: str,
    unit: str,
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
        "unit": unit,
        "currency": "KRW",
        "version": 1,
        "source_trace": _source_trace(source, source, unit),
    }


def _source_trace(source: str, source_type: str, unit: str) -> dict[str, object]:
    return {
        "source": source,
        "source_type": source_type,
        "source_document_id": f"raw:{source}:005930.KS:2024",
        "filing_id": f"{source}-005930-2024",
        "form": "KR_SOURCE",
        "form_type": "KR_SOURCE",
        "period": "FY2024",
        "fiscal_year": 2024,
        "fiscal_period": "FY",
        "period_end": "2024-12-31",
        "available_at": "2025-03-31T00:00:00+09:00",
        "unit": unit,
        "currency": "KRW",
        "method": f"{source.upper()}_SOURCE_BACKED",
        "formula": f"{source} source-backed fact",
        "confidence": "0.95",
        "quality_status": "source_backed",
        "quality_flags": ["source_backed"],
        "source_url": f"https://example.invalid/{source}/005930/2024",
        "version": 1,
    }
