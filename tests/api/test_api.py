from datetime import date
from decimal import Decimal
import hashlib
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from packages.quality import validate_valuation_row
from packages.valuation.engine import ValuationPoint
from services.api.local_consensus_provider import local_consensus_projection_from_csv
from services.api.main import app
from services.api.postgres_provider import (
    _forecast_trace_from_row,
    _market_cap_usd_from_snapshot,
    _market_structure_from_price_trace,
    source_coverage_from_postgres,
    valuation_points_from_postgres,
)
from services.ingestion_worker.kr_valuation_warehouse import (
    load_kr_valuation_cache_to_warehouse,
)

client = TestClient(app)

REQUIRED_VALUATION_KEYS = {
    "metric",
    "price",
    "normal_multiple",
    "fair_multiple",
    "fair_value_price",
    "forecast_flag",
    "source_trace",
}

REQUIRED_SOURCE_TRACE_KEYS = {
    "source_document_id",
    "source_type",
    "filing_id",
    "period",
    "available_at",
    "unit",
    "currency",
    "formula",
    "quality_status",
}

SOURCE_TRACE_AUDIT_KEYS = [
    "source_document_id",
    "filing_id",
    "period",
    "unit",
    "currency",
    "formula",
    "quality_status",
]

KR_TOP_TEST_TICKERS = [
    "005930.KS",
    "000660.KS",
    "402340.KS",
    "005380.KS",
    "028260.KS",
    "032830.KS",
    "373220.KS",
    "207940.KS",
    "329180.KS",
    "009155.KS",
]


def _manual_consensus_csv_text(
    tickers: list[str],
    *,
    values_by_ticker: dict[str, list[str]] | None = None,
    start_year: int = 2026,
    years: int = 5,
) -> str:
    lines = [
        "ticker,fiscal_year,snapshot_date,estimate_case,estimate_eps,growth_rate_pct,"
        "analyst_count,currency,source,source_url,metric_key,period_end,quality_status,"
        "source_document_id,filing_id,notes"
    ]
    values_by_ticker = values_by_ticker or {}
    for ticker_index, ticker in enumerate(tickers):
        values = values_by_ticker.get(ticker)
        for offset, fiscal_year in enumerate(range(start_year, start_year + years)):
            estimate_eps = (
                values[offset]
                if values is not None
                else str(7000 + ticker_index * 100 + offset * 25)
            )
            lines.append(
                f"{ticker},{fiscal_year},2026-07-02,median,{estimate_eps},5.00,0,KRW,"
                "manual_forecast_assumption,,adjusted_operating_eps,"
                f"{fiscal_year}-12-31,manual_forecast_assumption,"
                f"manual-doc-{ticker}-{fiscal_year},manual-filing-{ticker}-{fiscal_year},"
                "Manual forecast assumption from source-backed KR valuation cache."
            )
    return "\n".join(lines) + "\n"


def _kr_warehouse_coverage_row(ticker: str) -> dict:
    return {
        "ticker": ticker,
        "security_count": 1,
        "name": ticker,
        "market": "KR",
        "country": "KR",
        "currency": "KRW",
        "adjusted_years": 6,
        "price_years": 6,
        "market_cap_years": 6,
        "listed_shares_years": 6,
        "financial_fact_years": 6,
        "financial_fact_tags": 7,
        "financial_metric_years": 6,
        "financial_metric_keys": 7,
        "available_metric_keys": ["adjusted_operating_eps"],
        "source_documents": 50,
        "raw_objects": 50,
        "s4_periods": 6,
    }


def _mock_kr_warehouse_valuation_points(
    monkeypatch,
    ticker: str,
    *,
    latest_metric: str = "6603",
) -> None:
    historical_trace = {
        "source_document_id": f"kr-warehouse-doc-{ticker}-2025",
        "source_type": "kr_valuation_warehouse",
        "filing_id": f"KR_VALUATION_INPUT_{ticker}_2025",
        "period": "FY2025",
        "available_at": "2026-04-01T00:00:00+00:00",
        "unit": "KRW/share",
        "currency": "KRW",
        "method": "S3_MARKET_STANDARD_KR",
        "formula": "source-backed adjusted operating EPS",
        "quality_status": "source_backed",
    }
    monkeypatch.setattr(
        "services.api.main.valuation_points_from_postgres",
        lambda requested_ticker, metric: None,
    )
    monkeypatch.setattr(
        "services.api.main.valuation_points_from_kr_warehouse",
        lambda requested_ticker, metric: SimpleNamespace(
            points=[
                ValuationPoint(
                    fiscal_year=2024,
                    metric=Decimal("5925"),
                    price=Decimal("78000"),
                    dividend=Decimal("1444"),
                    source_trace={**historical_trace, "period": "FY2024"},
                ),
                ValuationPoint(
                    fiscal_year=2025,
                    metric=Decimal(latest_metric),
                    price=Decimal("81000"),
                    dividend=Decimal("1444"),
                    source_trace=historical_trace,
                ),
            ],
            metric_label="Adjusted Operating EPS",
            price_points=[],
            meta={
                "data_backend": "kr_valuation_warehouse",
                "data_mode": "source_backed",
                "valuation_ready": True,
                "financial_numbers_allowed": True,
            },
        )
        if requested_ticker == ticker
        else None,
    )


def test_source_document_resolver_opens_local_raw_content_hash(monkeypatch, tmp_path):
    payload = b'{"status":"000","message":"source backed fixture for resolver test"}'
    digest = hashlib.sha256(payload).hexdigest()
    raw_dir = tmp_path / "storage" / "raw" / "opendart" / "005930.KS"
    raw_dir.mkdir(parents=True)
    raw_path = raw_dir / f"00126380-2024-11011-CFS-{digest[:12]}.json"
    raw_path.write_bytes(payload)
    monkeypatch.setenv("SOURCE_DOCUMENT_STORAGE_ROOT", str(tmp_path))

    response = client.get(
        "/api/source-documents/resolve",
        params={"source_document_id": f"raw:opendart:{digest}"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "found"
    assert data["source"] == "opendart"
    assert data["content_hash"] == digest
    assert data["resolver"] == "local_raw_content_hash"
    assert data["preview_available"] is True
    assert "source backed fixture" in data["preview_text"]
    assert data["local_path"].replace("\\", "/").endswith(raw_path.name)


def test_source_document_resolver_opens_kr_cache_logical_id(monkeypatch, tmp_path):
    cache_dir = tmp_path / "storage" / "cache" / "kr-valuation-inputs"
    cache_dir.mkdir(parents=True)
    cache_path = cache_dir / "005930_KS-2020-2025-valuation-inputs.json"
    cache_path.write_text(
        json.dumps({"ticker": "005930.KS", "coverage_status": "complete"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("SOURCE_DOCUMENT_STORAGE_ROOT", str(tmp_path))

    response = client.get(
        "/api/source-documents/resolve",
        params={"source_document_id": "kr-cache:005930.KS:2022:market-gap:source_no_rows_before_first_trade"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "found"
    assert data["source"] == "kr_valuation_input_cache"
    assert data["resolver"] == "local_kr_valuation_cache_logical_id"
    assert data["preview_available"] is True
    assert "005930.KS" in data["preview_text"]
    assert data["metadata"]["fiscal_year"] == 2022


def test_source_document_resolver_opens_kr_derived_valuation_input(monkeypatch, tmp_path):
    cache_dir = tmp_path / "storage" / "cache" / "kr-valuation-inputs"
    cache_dir.mkdir(parents=True)
    cache_path = cache_dir / "005930_KS-2020-2025-valuation-inputs.json"
    cache_path.write_text(
        json.dumps(
            {
                "ticker": "005930.KS",
                "coverage_status": "complete",
                "valuation_points": [{"fiscal_year": 2024, "metric": "adjusted_operating_eps"}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SOURCE_DOCUMENT_STORAGE_ROOT", str(tmp_path))

    response = client.get(
        "/api/source-documents/resolve",
        params={"source_document_id": "derived:kr:005930.KS:2024:valuation-input"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "found"
    assert data["source"] == "kr_valuation_warehouse"
    assert data["resolver"] == "local_kr_warehouse_derived_valuation_input"
    assert data["preview_available"] is True
    assert "adjusted_operating_eps" in data["preview_text"]
    assert data["metadata"]["backing_source"] == "kr_valuation_input_cache"
    assert data["metadata"]["fiscal_year"] == 2024


def test_source_document_resolver_opens_opendart_logical_id(monkeypatch, tmp_path):
    raw_dir = tmp_path / "storage" / "raw" / "opendart" / "005930.KS"
    raw_dir.mkdir(parents=True)
    raw_path = raw_dir / "00126380-2022-11011-CFS-abcdef123456.json"
    raw_path.write_text(
        json.dumps({"status": "013", "message": "No data for requested filing"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("SOURCE_DOCUMENT_STORAGE_ROOT", str(tmp_path))

    response = client.get(
        "/api/source-documents/resolve",
        params={"source_document_id": "opendart:005930.KS:2022:status:013"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "found"
    assert data["source"] == "opendart"
    assert data["resolver"] == "local_opendart_logical_id"
    assert data["preview_available"] is True
    assert "No data for requested filing" in data["preview_text"]
    assert data["local_path"].replace("\\", "/").endswith(raw_path.name)


def test_source_document_resolver_explains_logical_opendart_diagnostic_id(monkeypatch, tmp_path):
    monkeypatch.setenv("SOURCE_DOCUMENT_STORAGE_ROOT", str(tmp_path))

    response = client.get(
        "/api/source-documents/resolve",
        params={"source_document_id": "opendart:005930.KS:2022:status:013"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "logical_only"
    assert data["source"] == "opendart"
    assert data["resolver"] == "logical_source_document_id"
    assert "deterministic audit identifier" in data["metadata"]["note"]


class _FakeMappingResult:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


class _FakePostgresConnection:
    def __init__(self):
        self.statements: list[str] = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        if "WITH requested AS" in sql and "market_cap_years" in sql:
            return _FakeMappingResult(
                [
                    {
                        "ticker": "005930.KS",
                        "security_count": 1,
                        "name": "Samsung Electronics",
                        "country": "KR",
                        "currency": "KRW",
                        "market": "KRX",
                        "adjusted_years": 5,
                        "latest_adjusted_year": 2024,
                        "s1_periods": 0,
                        "s2_periods": 0,
                        "s4_periods": 0,
                        "price_years": 5,
                        "latest_price_year": 2024,
                        "market_cap_years": 0,
                        "listed_shares_years": 0,
                        "financial_fact_years": 5,
                        "financial_fact_tags": 7,
                        "latest_financial_fact_year": 2024,
                        "financial_metric_years": 5,
                        "financial_metric_keys": 7,
                        "available_metric_keys": ["diluted_eps", "revenue_share"],
                        "dividend_years": 5,
                        "consensus_forecast_years": 0,
                        "consensus_valuation_years": 0,
                        "consensus_snapshots": 0,
                        "consensus_valuation_snapshots": 0,
                        "latest_consensus_year": None,
                        "adjustment_rows": 0,
                        "source_documents": 3,
                        "raw_objects": 2,
                    }
                ]
            )
        if "FROM securities" in sql:
            return _FakeMappingResult(
                [{"id": "security-aapl", "currency": "USD", "country": "US"}]
            )
        if "FROM adjusted_earnings" in sql:
            return _FakeMappingResult([])
        if "FROM metric_values" in sql:
            return _FakeMappingResult(
                [
                    {
                        "fiscal_year": 2024,
                        "value": Decimal("6.08"),
                        "source_trace": {
                            "source_type": "sec_companyfacts_bulk_derived",
                            "source": "SEC_COMPANYFACTS_BULK_DERIVED",
                            "filing_id": "0000320193-24-000123",
                            "period": "2024FY",
                            "available_at": "2024-11-01T12:00:00+00:00",
                            "unit": "USD_per_share",
                            "currency": "USD",
                            "formula": "reported_eps_diluted from SEC companyfacts EPS fact",
                            "method": "SEC_COMPANYFACTS_BULK_DERIVED",
                            "quality_status": "source_backed_sec_companyfacts_derived",
                            "input_fact_ids": [
                                "AAPL:0000320193-24-000123:us-gaap:EarningsPerShareDiluted:2024FY"
                            ],
                        },
                        "method": "SEC_COMPANYFACTS_BULK_DERIVED",
                        "quality_status": "source_backed_sec_companyfacts_derived",
                        "formula": "reported_eps_diluted from SEC companyfacts EPS fact",
                    }
                ]
            )
        if "FROM price_bars" in sql:
            return _FakeMappingResult(
                [
                    {
                        "fiscal_year": 2024,
                        "close_price": Decimal("250.00"),
                        "source_trace": {
                            "source_type": "stooq",
                            "period": "2024-12-31",
                            "formula": "Source-backed close price observation",
                        },
                    }
                ]
            )
        if "FROM dividends" in sql:
            return _FakeMappingResult(
                [
                    {
                        "fiscal_year": 2024,
                        "amount": Decimal("1.00"),
                        "source_traces": [
                            {
                                "source_type": "nasdaq_dividend_csv",
                                "period": "2024",
                            }
                        ],
                    }
                ]
            )
        raise AssertionError(f"Unexpected SQL in fake provider test: {sql}")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakePostgresEngine:
    def __init__(self, connection: _FakePostgresConnection):
        self.connection = connection

    def connect(self):
        return self.connection


def test_forecast_snapshot_trace_includes_version_dimensions():
    row = {
        "id": "snapshot-row-1",
        "source_trace": {},
        "source": "user_consensus_csv",
        "source_url": "https://example.com/consensus.csv",
        "fiscal_year": 2027,
        "fiscal_period": "FY",
        "snapshot_date": date(2026, 6, 15),
        "estimate_case": "median",
        "metric_key": "adjusted_operating_eps",
        "unit": "per_share",
        "quality_status": "source_backed_consensus_snapshots",
    }

    trace = _forecast_trace_from_row(row, "USD")

    assert trace["snapshot_id"] == "snapshot-row-1"
    assert trace["snapshot_date"] == "2026-06-15"
    assert trace["estimate_case"] == "median"
    assert trace["metric_key"] == "adjusted_operating_eps"
    assert trace["fiscal_period"] == "FY"
    assert trace["period"] == "FY2027E"
    assert trace["available_at"] == "2026-06-15T00:00:00+00:00"
    assert trace["method"] == "user_consensus_csv"


def test_market_structure_from_marcap_trace_converts_millions():
    trace = {
        "source_type": "marcap",
        "source_document_id": "marcap-source-doc",
        "source_url": "https://github.com/financedata/marcap",
        "filing_id": "marcap-2024",
        "period": "2024-12-30",
        "unit": "per_share",
        "currency": "KRW",
        "quality_status": "open_dataset_price",
        "market_cap_krw_millions": "1234",
        "listed_shares": "567",
    }

    market_structure = _market_structure_from_price_trace(trace, "KRW")

    assert market_structure["market_cap"] == "1234000000"
    assert market_structure["listed_shares"] == "567"
    cap_trace = market_structure["market_cap_source_trace"]
    assert cap_trace["fact_name"] == "snapshot.market_cap"
    assert cap_trace["unit"] == "market_cap"
    assert cap_trace["currency"] == "KRW"
    assert "multiplied by 1,000,000" in cap_trace["formula"]


def test_market_structure_prefers_normalized_market_cap_trace():
    trace = {
        "source_type": "marcap",
        "source_document_id": "marcap-source-doc",
        "source_url": "https://github.com/financedata/marcap",
        "filing_id": "marcap-2024",
        "period": "2024-12-30",
        "unit": "per_share",
        "currency": "KRW",
        "quality_status": "open_dataset_price",
        "market_cap": "317592431660000",
        "market_cap_krw_millions": "317592431660000",
        "listed_shares": "5969782550",
    }

    market_structure = _market_structure_from_price_trace(trace, "KRW")

    assert market_structure["market_cap"] == "317592431660000"
    cap_trace = market_structure["market_cap_source_trace"]
    assert cap_trace["formula"] == "Source trace market_cap imported as market capitalization"


def test_market_cap_usd_from_snapshot_uses_fred_local_per_usd_rate():
    snapshot = {
        "currency": "KRW",
        "market_cap": "1500000000000",
        "source_trace": {
            "market_cap_source_trace": {
                "source_type": "marcap",
                "quality_status": "open_dataset_price",
            }
        },
    }
    fx_rates = {
        "KRW": {
            "rate": Decimal("1500"),
            "source_trace": {
                "source_type": "fred",
                "series_id": "DEXKOUS",
                "period": "2026-06-18",
            },
        }
    }

    result = _market_cap_usd_from_snapshot(snapshot, fx_rates)

    assert result["market_cap_usd"] == "1000000000.00"
    trace = result["market_cap_usd_source_trace"]
    assert trace["currency"] == "USD"
    assert trace["calculation_inputs"]["fx_series_id"] == "DEXKOUS"
    assert trace["formula"] == "market_cap_usd = market_cap / local_currency_per_usd_fx_rate"


def test_valuation_points_postgres_diluted_eps_uses_metric_values_fallback(monkeypatch):
    connection = _FakePostgresConnection()
    monkeypatch.setattr("services.api.postgres_provider.postgres_enabled", lambda: True)
    monkeypatch.setattr(
        "services.api.postgres_provider.get_engine",
        lambda: _FakePostgresEngine(connection),
    )

    result = valuation_points_from_postgres("AAPL", "diluted_eps")

    assert result is not None
    points, metric_label, meta = result
    assert metric_label == "Diluted EPS"
    assert meta["data_backend"] == "postgres"
    assert points[0].fiscal_year == 2024
    assert points[0].metric == Decimal("6.08")
    assert points[0].price == Decimal("250.00")
    assert points[0].dividend == Decimal("1.00")
    trace = points[0].source_trace
    assert trace["source_type"] == "sec_companyfacts_bulk_derived"
    assert trace["method"] == "SEC_COMPANYFACTS_BULK_DERIVED"
    assert trace["formula"] == "reported_eps_diluted from SEC companyfacts EPS fact"
    assert trace["input_fact_ids"]
    assert trace["price_source_trace"]["source_type"] == "stooq"
    assert trace["dividend_source_traces"][0]["source_type"] == "nasdaq_dividend_csv"
    assert any("FROM adjusted_earnings" in statement for statement in connection.statements)
    assert any("FROM metric_values" in statement for statement in connection.statements)


def test_source_coverage_postgres_rejects_placeholder_market_structure_values(monkeypatch):
    connection = _FakePostgresConnection()
    monkeypatch.setattr("services.api.postgres_provider.postgres_enabled", lambda: True)
    monkeypatch.setattr(
        "services.api.postgres_provider.get_engine",
        lambda: _FakePostgresEngine(connection),
    )

    summary = source_coverage_from_postgres(["005930.KS"], min_historical_years=3)

    assert summary is not None
    row = summary["tickers"][0]
    assert row["ticker"] == "005930.KS"
    assert row["core_ready"] is False
    assert row["missing_required"] == [
        "market_cap_evidence",
        "listed_shares_evidence",
    ]
    coverage_sql = next(
        statement for statement in connection.statements if "WITH requested AS" in statement
    )
    compact_sql = "".join(coverage_sql.split())
    assert "LOWER(BTRIM(source_trace->>'market_cap_krw_millions'))" in compact_sql
    assert "LOWER(BTRIM(source_trace->>'listed_shares'))" in compact_sql
    assert "NOTIN('unknown','n/a','na','none')" in compact_sql


def test_adjusted_endpoint_returns_source_trace():
    response = client.get("/api/security/AAPL/adjusted")
    assert response.status_code == 200
    payload = response.json()
    assert payload["series"]
    trace = payload["series"][-1]["source_trace"]
    for key in SOURCE_TRACE_AUDIT_KEYS:
        assert trace[key]


def test_normalize_adjusted_fixture_mode_is_explicit():
    response = client.post(
        "/api/security/AAPL/normalize-adjusted",
        json={"fixture": True, "start_year": 2024, "end_year": 2024},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["source"] == "fixture_non_production"
    assert payload["series"][0]["method"]


def test_normalize_adjusted_worker_control_plane_is_default(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "PersonalFastGraphs/0.1 test@example.com")
    response = client.post(
        "/api/security/AAPL/normalize-adjusted",
        json={"start_year": 2024, "end_year": 2024, "persist": True},
    )
    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "worker_required"
    assert payload["meta"]["source"] == "ingestion_worker_control_plane"
    assert payload["command"][:4] == [
        "python",
        "-m",
        "services.ingestion_worker.cli",
        "normalize-us",
    ]
    assert "--persist" in payload["command"]


def test_normalize_adjusted_worker_requires_year_range():
    response = client.post("/api/security/AAPL/normalize-adjusted", json={})
    assert response.status_code == 400
    assert "start_year and end_year" in response.json()["detail"]


def test_system_readiness_reports_source_state_without_secret_values(monkeypatch):
    monkeypatch.delenv("DATA_BACKEND", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    response = client.get("/api/v1/system/readiness")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "fixture_only"
    assert payload["data_mode"] == "fixture_non_production"
    assert payload["postgres"]["reachable"] is False
    assert "postgresql://" not in str(payload)


def test_system_source_coverage_reports_mvp_patterns_without_secrets(monkeypatch):
    monkeypatch.delenv("DATA_BACKEND", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(
        "services.api.main.source_coverage_rows_from_kr_warehouse",
        lambda tickers: [],
    )

    response = client.get("/api/v1/system/source-coverage")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "missing"
    assert payload["data_mode"] == "source_backed_required"
    assert payload["summary"]["tickers_expected"] == 10
    patterns = {row["ticker"]: row["pattern"] for row in payload["tickers"]}
    assert patterns == {
        "005930.KS": "kr_top_market_cap",
        "000660.KS": "kr_top_market_cap",
        "402340.KS": "kr_top_market_cap",
        "005380.KS": "kr_top_market_cap",
        "028260.KS": "kr_top_market_cap",
        "032830.KS": "kr_top_market_cap",
        "373220.KS": "kr_top_market_cap",
        "207940.KS": "kr_top_market_cap",
        "329180.KS": "kr_top_market_cap",
        "009155.KS": "kr_top_market_cap",
    }
    action_ids = [action["id"] for action in payload["remediation"]["next_actions"]]
    assert "collect_opendart" in action_ids
    assert "collect_pykrx_prices" in action_ids
    assert "collect_marcap" in action_ids
    assert payload["tickers"][0]["available_metric_keys"] == []
    assert "postgresql://" not in str(payload)


def test_system_source_coverage_can_require_consensus_forecast(monkeypatch):
    monkeypatch.delenv("DATA_BACKEND", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(
        "services.api.main.source_coverage_rows_from_kr_warehouse",
        lambda tickers: [],
    )

    response = client.get("/api/v1/system/source-coverage?require_consensus_forecast=true")

    assert response.status_code == 200
    payload = response.json()
    assert payload["requirements"]["consensus_forecast_required"] is True
    assert payload["summary"]["missing_consensus_forecast"] == [
        "005930.KS",
        "000660.KS",
        "402340.KS",
        "005380.KS",
        "028260.KS",
        "032830.KS",
        "373220.KS",
        "207940.KS",
        "329180.KS",
        "009155.KS",
    ]
    assert payload["summary"]["missing_by_requirement"]["consensus_forecast"] == [
        "005930.KS",
        "000660.KS",
        "402340.KS",
        "005380.KS",
        "028260.KS",
        "032830.KS",
        "373220.KS",
        "207940.KS",
        "329180.KS",
        "009155.KS",
    ]
    action_ids = [action["id"] for action in payload["remediation"]["next_actions"]]
    assert "export_consensus_template" in action_ids
    assert "import_consensus_csv" in action_ids
    assert all("consensus_forecast" in row["missing_required"] for row in payload["tickers"])


def test_system_source_coverage_marks_local_csv_forecast_overlay_ready(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("DATA_BACKEND", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    imports_dir = tmp_path / "storage" / "imports"
    imports_dir.mkdir(parents=True)
    (imports_dir / "consensus_005930.csv").write_text(
        "ticker,fiscal_year,snapshot_date,estimate_case,estimate_eps,growth_rate_pct,"
        "analyst_count,currency,source,source_url,metric_key,period_end,quality_status,"
        "source_document_id,filing_id,notes\n"
        "005930.KS,2026,2026-07-02,median,7358.69,11.44,0,KRW,"
        "manual_forecast_assumption,,adjusted_operating_eps,2026-12-31,"
        "manual_forecast_assumption,operator-doc-2026,operator-filing-2026,"
        "Manual forecast assumption from source-backed KR valuation cache.\n"
        "005930.KS,2027,2026-07-02,median,8200.87,11.44,0,KRW,"
        "manual_forecast_assumption,,adjusted_operating_eps,2027-12-31,"
        "manual_forecast_assumption,operator-doc-2027,operator-filing-2027,"
        "Manual forecast assumption from source-backed KR valuation cache.\n"
        "005930.KS,2028,2026-07-02,median,9139.44,11.44,0,KRW,"
        "manual_forecast_assumption,,adjusted_operating_eps,2028-12-31,"
        "manual_forecast_assumption,operator-doc-2028,operator-filing-2028,"
        "Manual forecast assumption from source-backed KR valuation cache.\n"
        "005930.KS,2029,2026-07-02,median,10185.42,11.44,0,KRW,"
        "manual_forecast_assumption,,adjusted_operating_eps,2029-12-31,"
        "manual_forecast_assumption,operator-doc-2029,operator-filing-2029,"
        "Manual forecast assumption from source-backed KR valuation cache.\n"
        "005930.KS,2030,2026-07-02,median,11351.11,11.44,0,KRW,"
        "manual_forecast_assumption,,adjusted_operating_eps,2030-12-31,"
        "manual_forecast_assumption,operator-doc-2030,operator-filing-2030,"
        "Manual forecast assumption from source-backed KR valuation cache.\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "services.api.main.source_coverage_rows_from_kr_warehouse",
        lambda tickers: [
            {
                "ticker": "005930.KS",
                "security_count": 1,
                "name": "Samsung Electronics",
                "market": "KR",
                "country": "KR",
                "currency": "KRW",
                "adjusted_years": 6,
                "price_years": 6,
                "market_cap_years": 6,
                "listed_shares_years": 6,
                "financial_fact_years": 6,
                "financial_fact_tags": 7,
                "financial_metric_years": 6,
                "financial_metric_keys": 7,
                "available_metric_keys": ["adjusted_operating_eps"],
                "source_documents": 50,
                "raw_objects": 50,
                "s4_periods": 6,
            }
        ],
    )

    response = client.get(
        "/api/v1/system/source-coverage"
        "?tickers=005930.KS&require_consensus_forecast=true"
    )

    assert response.status_code == 200
    payload = response.json()
    row = payload["tickers"][0]
    assert payload["data_backend"] == "kr_valuation_warehouse"
    assert payload["postgres"]["reachable"] is False
    assert payload["local_overlays"]["production_db_pending"] is True
    assert payload["summary"]["consensus_forecast_ready"] == 1
    assert payload["summary"]["missing_consensus_forecast"] == []
    assert row["consensus_forecast_ready"] is True
    assert row["local_consensus_overlay_ready"] is True
    assert row["local_consensus_overlay_source"] == "local_consensus_csv"
    assert row["counts"]["consensus_valuation_years"] == 5


def test_local_consensus_provider_prefers_per_ticker_csv_then_aggregate(
    monkeypatch, tmp_path
):
    imports_dir = tmp_path / "storage" / "imports"
    imports_dir.mkdir(parents=True)
    (imports_dir / "consensus_estimates.csv").write_text(
        _manual_consensus_csv_text(
            ["005930.KS", "000660.KS"],
            values_by_ticker={
                "005930.KS": ["7000", "7100", "7200", "7300", "7400"],
                "000660.KS": ["8000", "8100", "8200", "8300", "8400"],
            },
        ),
        encoding="utf-8",
    )
    (imports_dir / "consensus_005930.csv").write_text(
        _manual_consensus_csv_text(
            ["005930.KS"],
            values_by_ticker={
                "005930.KS": ["9000", "9100", "9200", "9300", "9400"],
            },
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    per_ticker_projection = local_consensus_projection_from_csv(
        "005930.KS",
        "median",
        2025,
        5,
        Decimal("6603"),
    )
    aggregate_projection = local_consensus_projection_from_csv(
        "000660.KS",
        "median",
        2025,
        5,
        Decimal("6603"),
    )

    assert per_ticker_projection is not None
    assert per_ticker_projection["metric_values"][0] == "9000"
    assert per_ticker_projection["quality_status"] == (
        "source_backed_manual_forecast_assumption"
    )
    assert per_ticker_projection["source_traces_by_year"]["2026"]["source_file"].replace(
        "\\", "/"
    ).endswith("/consensus_005930.csv")
    assert aggregate_projection is not None
    assert aggregate_projection["metric_values"][0] == "8000"
    assert aggregate_projection["source_traces_by_year"]["2026"]["source_file"].replace(
        "\\", "/"
    ).endswith("/consensus_estimates.csv")


def test_system_source_coverage_marks_top10_aggregate_csv_overlay_ready(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("DATA_BACKEND", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    imports_dir = tmp_path / "storage" / "imports"
    imports_dir.mkdir(parents=True)
    (imports_dir / "consensus_estimates.csv").write_text(
        _manual_consensus_csv_text(KR_TOP_TEST_TICKERS),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "services.api.main.source_coverage_rows_from_kr_warehouse",
        lambda tickers: [_kr_warehouse_coverage_row(ticker) for ticker in tickers],
    )

    response = client.get(
        "/api/v1/system/source-coverage?market=KR&require_consensus_forecast=true"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["data_backend"] == "kr_valuation_warehouse"
    assert payload["postgres"]["reachable"] is False
    assert payload["local_overlays"]["production_db_pending"] is True
    assert payload["summary"]["tickers_expected"] == 10
    assert payload["summary"]["consensus_forecast_ready"] == 10
    assert payload["summary"]["missing_consensus_forecast"] == []
    assert all(row["consensus_forecast_ready"] for row in payload["tickers"])
    assert all(row.get("local_consensus_overlay_ready") for row in payload["tickers"])
    assert all(
        row.get("local_consensus_overlay_source") == "local_consensus_csv"
        for row in payload["tickers"]
    )


def test_system_source_coverage_can_cover_all_priority_markets(monkeypatch):
    monkeypatch.delenv("DATA_BACKEND", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(
        "services.api.main.source_coverage_rows_from_kr_warehouse",
        lambda tickers: [],
    )

    response = client.get("/api/v1/system/source-coverage?market=ALL")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["tickers_expected"] == 30
    tickers = [row["ticker"] for row in payload["tickers"]]
    assert tickers[:3] == ["005930.KS", "000660.KS", "402340.KS"]
    assert "NVDA" in tickers
    assert "285A.T" in tickers
    patterns = {row["ticker"]: row["pattern"] for row in payload["tickers"]}
    assert patterns["005930.KS"] == "kr_top_market_cap"
    assert patterns["GOOG"] == "us_top_market_cap"
    assert patterns["7203.T"] == "jp_top_market_cap"
    first_action = payload["remediation"]["next_actions"][0]
    assert first_action["id"] == "run_priority_e2e"
    assert first_action["github_actions"]["command"] == "run_priority_e2e"
    assert first_action["github_actions"]["priority_e2e_markets"] == "KR,US,JP"
    action_ids = {action["id"] for action in payload["remediation"]["next_actions"]}
    assert {"collect_opendart", "collect_sec_bulk", "collect_jquants"}.issubset(action_ids)


def test_system_source_coverage_rejects_unknown_market():
    response = client.get("/api/v1/system/source-coverage?market=EU")

    assert response.status_code == 400
    assert "market must be one of" in response.json()["detail"]


def test_system_priority_universe_exposes_kr_contract_without_financial_values():
    response = client.get("/api/v1/system/priority-universe")

    assert response.status_code == 200
    payload = response.json()
    assert payload["universe_id"] == "kr-top-market-cap-priority-v2"
    assert payload["market"] == "KR"
    assert payload["data_mode"] == "source_backed_required"
    assert len(payload["tickers"]) == 10
    assert payload["tickers"][0]["ticker"] == "005930.KS"
    assert payload["tickers"][0]["rank_policy"] == "not_a_live_market_cap_rank"
    trace = payload["tickers"][0]["source_trace"]
    assert trace["source_document_id"] == "nexus-kr-top-market-cap-priority-universe-v2"
    assert trace["quality_status"] == "coverage_contract_not_financial_data"
    assert "requires_source_backed_rank_recompute" in trace["quality_flags"]
    assert "market_cap" not in payload["tickers"][0]


def test_system_priority_universe_prefers_source_backed_market_cap_rank(monkeypatch):
    live_payload = {
        "universe_id": "kr-top-market-cap-source-backed-v1",
        "label": "KR source-backed top market-cap universe",
        "market": "KR",
        "currency": "KRW",
        "data_mode": "source_backed",
        "rank_policy": "source_backed_latest_market_cap",
        "source_trace": {
            "source_document_id": "postgres-price-bars-latest-market-cap-kr",
            "source_type": "source_backed_market_cap_rank",
            "filing_id": "POSTGRES-KR-LATEST-MARKET-CAP",
            "period": "2026-06-26",
            "unit": "market_cap",
            "currency": "KRW",
            "method": "source_backed_latest_market_cap_rank",
            "formula": "sort latest source-backed market_cap evidence descending",
            "quality_status": "source_backed_market_cap_rank",
        },
        "tickers": [
            {
                "ticker": "005930.KS",
                "name": "Samsung Electronics",
                "market": "KR",
                "currency": "KRW",
                "market_cap": "500000000000000",
                "market_cap_rank": 1,
                "coverage_priority_order": 1,
                "rank_policy": "source_backed_latest_market_cap",
                "source_trace": {
                    "source_document_id": "marcap-2026",
                    "source_type": "marcap",
                    "filing_id": "marcap-2026",
                    "period": "2026-06-26",
                    "unit": "market_cap",
                    "currency": "KRW",
                    "method": "MARCAP_DAILY_CLOSE",
                    "formula": "market_cap_rank = descending latest source-backed market_cap from price_bars",
                    "quality_status": "source_backed_market_data",
                },
            }
        ],
    }
    monkeypatch.setattr(
        "services.api.main.top_market_cap_universe_from_postgres",
        lambda market: live_payload if market == "KR" else None,
    )

    response = client.get("/api/v1/system/priority-universe?market=KR")

    assert response.status_code == 200
    payload = response.json()
    assert payload["data_mode"] == "source_backed"
    assert payload["rank_policy"] == "source_backed_latest_market_cap"
    assert payload["tickers"][0]["market_cap"] == "500000000000000"
    assert payload["tickers"][0]["source_trace"]["source_document_id"] == "marcap-2026"


def test_system_priority_universe_exposes_us_and_jp_contracts_without_financial_values():
    response = client.get("/api/v1/system/priority-universe?market=US")

    assert response.status_code == 200
    payload = response.json()
    assert payload["market"] == "US"
    assert payload["tickers"][0]["ticker"] == "NVDA"
    assert payload["tickers"][0]["rank_policy"] == "not_a_live_market_cap_rank"
    assert "market_cap" not in payload["tickers"][0]

    jp_response = client.get("/api/v1/system/priority-universe?market=JP")
    assert jp_response.status_code == 200
    jp_payload = jp_response.json()
    assert jp_payload["market"] == "JP"
    assert jp_payload["tickers"][0]["ticker"].endswith(".T")
    assert jp_payload["tickers"][0]["rank_policy"] == "not_a_live_market_cap_rank"


def test_system_priority_universe_exposes_all_market_contracts():
    response = client.get("/api/v1/system/priority-universe?market=ALL")

    assert response.status_code == 200
    payload = response.json()
    assert payload["markets"] == ["KR", "US", "JP"]
    assert payload["rank_coverage_status"] == "coverage_contract_only"
    assert payload["rank_count"] == 0
    assert payload["rank_limit"] == 30
    assert payload["missing_rank_slots"] == 30
    assert payload["source_trace"]["rank_coverage_status"] == "coverage_contract_only"
    assert payload["source_trace"]["quality_status"] == "coverage_contract_not_financial_data"
    assert len(payload["universes"]) == 3
    assert sum(len(universe["tickers"]) for universe in payload["universes"]) == 30


def test_system_priority_universe_all_aggregates_partial_source_backed_rank(monkeypatch):
    live_payload = {
        "universe_id": "kr-top-market-cap-source-backed-v1",
        "label": "KR source-backed top market-cap universe",
        "market": "KR",
        "currency": "KRW",
        "data_mode": "source_backed",
        "rank_policy": "source_backed_latest_market_cap",
        "rank_coverage_status": "partial_top_market_cap_rank",
        "rank_count": 3,
        "rank_limit": 10,
        "missing_rank_slots": 7,
        "source_trace": {
            "source_document_id": "postgres-price-bars-latest-market-cap-kr",
            "source_type": "source_backed_market_cap_rank",
            "filing_id": "POSTGRES-KR-LATEST-MARKET-CAP",
            "period": "2026-06-26",
            "unit": "market_cap",
            "currency": "KRW",
            "method": "source_backed_latest_market_cap_rank",
            "formula": "sort latest source-backed market_cap evidence descending",
            "quality_status": "partial_source_backed_market_cap_rank",
            "quality_flags": ["partial_market_cap_rank", "missing_rank_slots"],
        },
        "tickers": [
            {
                "ticker": "005930.KS",
                "name": "Samsung Electronics",
                "market": "KR",
                "currency": "KRW",
                "market_cap": "500000000000000",
                "market_cap_rank": 1,
                "coverage_priority_order": 1,
                "rank_policy": "source_backed_latest_market_cap",
                "source_trace": {
                    "source_document_id": "marcap-2026",
                    "source_type": "marcap",
                    "filing_id": "marcap-2026",
                    "period": "2026-06-26",
                    "unit": "market_cap",
                    "currency": "KRW",
                    "method": "MARCAP_DAILY_CLOSE",
                    "formula": "market_cap_rank = descending latest source-backed market_cap from price_bars",
                    "quality_status": "source_backed_market_data",
                },
            }
        ],
    }
    monkeypatch.setattr(
        "services.api.main.top_market_cap_universe_from_postgres",
        lambda market: live_payload if market == "KR" else None,
    )

    response = client.get("/api/v1/system/priority-universe?market=ALL")

    assert response.status_code == 200
    payload = response.json()
    assert payload["data_mode"] == "source_backed_required"
    assert payload["rank_coverage_status"] == "partial_top_market_cap_rank"
    assert payload["rank_count"] == 3
    assert payload["rank_limit"] == 30
    assert payload["missing_rank_slots"] == 27
    assert payload["source_trace"]["quality_status"] == "partial_source_backed_market_cap_rank"
    assert "partial_market_cap_rank" in payload["source_trace"]["quality_flags"]
    assert payload["universes"][0]["market"] == "KR"
    assert payload["universes"][0]["rank_count"] == 3
    assert payload["universes"][1]["rank_coverage_status"] == "coverage_contract_only"


def test_system_priority_universe_rejects_unknown_market():
    response = client.get("/api/v1/system/priority-universe?market=EU")

    assert response.status_code == 400
    assert "market must be one of" in response.json()["detail"]


def test_system_source_coverage_validates_min_years():
    response = client.get("/api/v1/system/source-coverage?min_historical_years=0")

    assert response.status_code == 400
    assert "must be positive" in response.json()["detail"]


def test_macro_series_requires_source_backed_data(monkeypatch):
    monkeypatch.setattr("services.api.main.macro_series_from_postgres", lambda **kwargs: None)

    response = client.get("/api/v1/macro-series?source=fred&series_id=USREC")

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"] == []
    assert payload["meta"]["data_mode"] == "source_backed_required"
    assert payload["meta"]["quality_status"] == "missing_source_backed_data"


def test_macro_series_returns_source_trace(monkeypatch):
    def fake_macro_series_from_postgres(**kwargs):
        assert kwargs["source"] == "fred"
        assert kwargs["series_id"] == "USREC"
        assert kwargs["limit"] == 10
        assert kwargs["start_date"].isoformat() == "2024-01-01"
        return [
            {
                "series_id": "USREC",
                "observation_date": "2024-01-01",
                "value": "0",
                "unit": "indicator",
                "frequency": "monthly",
                "source": "fred",
                "source_url": "https://fred.stlouisfed.org/series/USREC",
                "source_document_id": "source-doc-id",
                "source_trace": {
                    "source_document_id": "source-doc-id",
                    "source_type": "fred",
                    "filing_id": "fred:USREC",
                    "period": "2024-01-01",
                    "unit": "indicator",
                    "currency": "N/A",
                    "formula": "FRED reported observation value",
                    "quality_status": "source_backed_macro",
                },
            }
        ]

    monkeypatch.setattr(
        "services.api.main.macro_series_from_postgres",
        fake_macro_series_from_postgres,
    )

    response = client.get(
        "/api/v1/macro-series?source=fred&series_id=USREC&start_date=2024-01-01&limit=10"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["data_mode"] == "source_backed"
    assert payload["meta"]["row_count"] == 1
    row = payload["data"][0]
    assert row["series_id"] == "USREC"
    for key in SOURCE_TRACE_AUDIT_KEYS:
        assert row["source_trace"][key]


def test_macro_series_rejects_invalid_date_range():
    response = client.get("/api/v1/macro-series?start_date=2025-01-01&end_date=2024-01-01")

    assert response.status_code == 400
    assert "start_date" in response.json()["detail"]


def test_industry_series_requires_source_backed_data(monkeypatch):
    monkeypatch.setattr("services.api.main.industry_series_from_postgres", lambda **kwargs: None)

    response = client.get("/api/v1/industry-series?market=KR&source=kosis")

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"] == []
    assert payload["meta"]["data_mode"] == "source_backed_required"
    assert payload["meta"]["quality_status"] == "missing_source_backed_data"


def test_industry_series_returns_source_trace(monkeypatch):
    def fake_industry_series_from_postgres(**kwargs):
        assert kwargs["market"] == "KR"
        assert kwargs["source"] == "kosis"
        assert kwargs["limit"] == 10
        return [
            {
                "market": "KR",
                "series_id": "IND:KOSIS:101:DT_TEST:ABC",
                "observation_date": "2024-01-01",
                "value": "100",
                "unit": "Index",
                "frequency": "annual",
                "category": "official_kr_statistics",
                "region": None,
                "industry": "Manufacturing",
                "source": "kosis",
                "source_url": "https://kosis.kr/openapi",
                "source_document_id": "source-doc-id",
                "dimensions": {"C1_NM": "Manufacturing"},
                "source_trace": {
                    "source_document_id": "source-doc-id",
                    "source_type": "kosis_official_api",
                    "filing_id": "kosis:IND:KOSIS:101:DT_TEST:ABC",
                    "period": "2024",
                    "unit": "Index",
                    "currency": "N/A",
                    "formula": "KOSIS reported observation value",
                    "quality_status": "source_backed_industry",
                },
            }
        ]

    monkeypatch.setattr(
        "services.api.main.industry_series_from_postgres",
        fake_industry_series_from_postgres,
    )

    response = client.get("/api/v1/industry-series?market=KR&source=kosis&limit=10")

    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["data_mode"] == "source_backed"
    assert payload["meta"]["row_count"] == 1
    row = payload["data"][0]
    assert row["industry"] == "Manufacturing"
    for key in SOURCE_TRACE_AUDIT_KEYS:
        assert row["source_trace"][key]


def test_industry_series_rejects_unbounded_limits():
    response = client.get("/api/v1/industry-series?limit=5000")

    assert response.status_code == 400
    assert "limit" in response.json()["detail"]


def test_production_blocks_fixture_fallback_without_explicit_allow(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.delenv("ALLOW_FIXTURE_FALLBACK", raising=False)
    monkeypatch.delenv("DATA_BACKEND", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    response = client.get("/api/v1/companies/AAPL/valuation-map")

    assert response.status_code == 503
    payload = response.json()["detail"]
    assert payload["code"] == "source_data_required"
    assert payload["surface"] == "valuation_map"


def test_production_allows_fixture_fallback_when_explicitly_enabled(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.setenv("ALLOW_FIXTURE_FALLBACK", "true")
    monkeypatch.delenv("DATA_BACKEND", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    response = client.get("/api/v1/companies/AAPL/valuation-map")

    assert response.status_code == 200
    assert response.json()["meta"]["data_mode"] == "fixture_non_production"


def test_production_blocks_kr_priority_fixture_financials_even_when_allowed(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.setenv("ALLOW_FIXTURE_FALLBACK", "true")
    monkeypatch.setenv("KR_VALUATION_INPUT_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("DATA_BACKEND", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    response = client.get(
        "/api/v1/companies/005930.KS/valuation-map?metric=adjusted_operating"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"] == []
    assert payload["meta"]["data_mode"] == "source_backed_required"
    assert payload["meta"]["quality_status"] == "missing_source_backed_data"
    assert payload["meta"]["financial_numbers_allowed"] is False
    trace = payload["meta"]["source_trace"]
    assert trace["source_document_id"] == "nexus-kr-top-market-cap-priority-universe-v2"
    assert trace["quality_status"] == "missing_source_backed_data"
    assert "fixture_fallback_blocked_for_kr_priority" in trace["quality_flags"]
    assert trace["financial_numbers_allowed"] is False


def test_kr_priority_valuation_map_reports_cache_blocker_metadata(monkeypatch, tmp_path):
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.setenv("ALLOW_FIXTURE_FALLBACK", "true")
    monkeypatch.setenv("KR_VALUATION_INPUT_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("DATA_BACKEND", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cache_path = tmp_path / "005930_KS-2024-2024-valuation-inputs.json"
    cache_path.write_text(
        json.dumps(
            {
                "ticker": "005930.KS",
                "status": "ok",
                "valuation_ready": False,
                "data_mode": "source_backed_raw_valuation_inputs",
                "metric_status": {
                    "status": "blocked",
                    "reason": "missing_open_dart_metric_values",
                    "quality_flags": ["missing_metric_source"],
                },
                "normalized_facts": [],
                "valuation_points": [],
                "dividend_status": {
                    "status": "blocked",
                    "reason": "missing_source_backed_dividend_per_share",
                },
                "quality_flags": ["missing_metric_source"],
                "generated_at": "2026-06-29T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    response = client.get(
        "/api/v1/companies/005930.KS/valuation-map?metric=adjusted_operating"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"] == []
    assert payload["meta"]["data_backend"] == "kr_valuation_input_cache"
    assert payload["meta"]["data_mode"] == "source_backed_required"
    assert payload["meta"]["metric_status"]["reason"] == "missing_open_dart_metric_values"
    assert payload["meta"]["valuation_ready"] is False
    assert payload["meta"]["financial_numbers_allowed"] is False


def test_kr_priority_valuation_map_uses_ready_cache_points(monkeypatch, tmp_path):
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.setenv("KR_VALUATION_INPUT_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("DATA_BACKEND", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cache_path = tmp_path / "005930_KS-2023-2024-valuation-inputs.json"
    cache_path.write_text(
        json.dumps(_kr_ready_cache_payload()),
        encoding="utf-8",
    )

    response = client.get(
        "/api/v1/companies/005930.KS/valuation-map?metric=adjusted_operating&forecast_years=1"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["data_backend"] == "kr_valuation_input_cache"
    assert payload["meta"]["data_mode"] == "source_backed_cache"
    assert payload["meta"]["price_points_meta"]["data_mode"] == "source_backed_cache"
    assert payload["meta"]["kr_cache"]["valuation_ready"] is True
    assert payload["meta"]["kr_cache"]["coverage_status"] == "partial_source_backed"
    assert payload["meta"]["kr_cache"]["full_coverage_ready"] is False
    assert payload["meta"]["kr_cache"]["coverage_years"]["valuation_points"] == [2023, 2024]
    assert payload["meta"]["kr_cache"]["missing_years"]["market_input"] == [2022]
    assert payload["meta"]["kr_cache"]["missing_years"]["financial_metric"] == [2022]
    assert (
        payload["meta"]["kr_cache"]["market_gap_diagnostics"][0]["status"]
        == "source_no_rows_before_first_trade"
    )
    assert payload["meta"]["kr_cache"]["financial_gap_diagnostics"][0]["status"] == "source_no_data"
    historical = [row for row in payload["data"] if not row["forecast_flag"]]
    assert [row["fiscal_year"] for row in historical] == [2023, 2024]
    for row in historical:
        trace = row["source_trace"]
        assert REQUIRED_SOURCE_TRACE_KEYS <= set(trace)
        assert trace["data_backend"] == "kr_valuation_input_cache"
        assert "missing_dividend_source" in trace["quality_flags"]

    audit_response = client.get(
        "/api/v1/companies/005930.KS/data-audit?metric=adjusted_operating&forecast_years=1"
    )
    assert audit_response.status_code == 200
    audit_rows = audit_response.json()["data"]
    market_gap = next(
        row
        for row in audit_rows
        if row["fact_name"] == "data_quality.kr_market_gap.source_no_rows_before_first_trade"
    )
    financial_gap = next(
        row
        for row in audit_rows
        if row["fact_name"] == "data_quality.kr_financial_gap.source_no_data"
    )
    assert market_gap["policy"] == "data_quality"
    assert market_gap["value"].startswith("No pykrx or marcap rows")
    assert REQUIRED_SOURCE_TRACE_KEYS <= set(market_gap["source_trace"])
    assert market_gap["source_trace"]["source_type"] == "kr_cache_market_gap_diagnostic"
    assert financial_gap["source_trace"]["source_type"] == "kr_cache_financial_gap_diagnostic"
    assert financial_gap["source_trace"]["source_document_id"].startswith("opendart:005930.KS:2022")
    fact_response = client.get(f"/api/data-audit/{market_gap['fact_id']}?forecast_years=1")
    assert fact_response.status_code == 200
    assert fact_response.json()["data"]["fact_id"] == market_gap["fact_id"]


def test_kr_priority_valuation_map_uses_complete_cache_without_gap_rows(monkeypatch, tmp_path):
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.setenv("KR_VALUATION_INPUT_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("DATA_BACKEND", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cache_path = tmp_path / "005930_KS-2020-2025-valuation-inputs.json"
    cache_path.write_text(
        json.dumps(_kr_complete_cache_payload()),
        encoding="utf-8",
    )

    response = client.get(
        "/api/v1/companies/005930.KS/valuation-map?metric=adjusted_operating&forecast_years=1"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["data_backend"] == "kr_valuation_input_cache"
    assert payload["meta"]["data_mode"] == "source_backed_cache"
    assert payload["meta"]["financial_numbers_allowed"] is True
    assert payload["meta"]["kr_cache"]["coverage_status"] == "complete"
    assert payload["meta"]["kr_cache"]["full_coverage_ready"] is True
    assert payload["meta"]["kr_cache"]["missing_years"] == {
        "market_input": [],
        "financial_metric": [],
    }
    assert payload["meta"]["kr_cache"]["market_gap_diagnostics"] == []
    assert payload["meta"]["kr_cache"]["financial_gap_diagnostics"] == []
    assert payload["meta"]["kr_cache"]["coverage_years"]["valuation_points"] == [
        2020,
        2021,
        2022,
        2023,
        2024,
        2025,
    ]
    historical = [row for row in payload["data"] if not row["forecast_flag"]]
    assert [row["fiscal_year"] for row in historical] == [2020, 2021, 2022, 2023, 2024, 2025]
    for row in historical:
        trace = row["source_trace"]
        assert REQUIRED_SOURCE_TRACE_KEYS <= set(trace)
        assert trace["data_backend"] == "kr_valuation_input_cache"
        assert trace["method"] == "KR_SOURCE_BACKED_PRICE_EPS_JOIN"
        assert trace["input_fact_ids"]
        assert "missing_dividend_source" not in trace["quality_flags"]
        assert trace["dividend_source_trace"]["source_type"] == "pykrx"
    assert historical[-1]["dividend"] == "1500"
    assert payload["meta"]["kr_cache"]["dividend_status"]["status"] == "ok"

    audit_response = client.get(
        "/api/v1/companies/005930.KS/data-audit?metric=adjusted_operating&forecast_years=1"
    )
    assert audit_response.status_code == 200
    fact_names = {row["fact_name"] for row in audit_response.json()["data"]}
    assert not any(name.startswith("data_quality.kr_market_gap") for name in fact_names)
    assert not any(name.startswith("data_quality.kr_financial_gap") for name in fact_names)


def test_kr_priority_valuation_map_uses_warehouse_before_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.delenv("DATA_BACKEND", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "005930_KS-2020-2025-valuation-inputs.json").write_text(
        json.dumps(_kr_complete_cache_payload()),
        encoding="utf-8",
    )
    warehouse_db = tmp_path / "warehouse.duckdb"
    load_summary = load_kr_valuation_cache_to_warehouse(
        "005930.KS",
        cache_dir=cache_dir,
        warehouse_root=tmp_path / "warehouse",
        db_path=warehouse_db,
        strict=True,
    )
    assert load_summary["status"] == "ok"
    monkeypatch.setenv("KR_VALUATION_WAREHOUSE_DB", str(warehouse_db))
    monkeypatch.setenv("KR_VALUATION_INPUT_CACHE_DIR", str(tmp_path / "empty-cache"))

    response = client.get(
        "/api/v1/companies/005930.KS/valuation-map?metric=adjusted_operating&forecast_years=1"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["data_backend"] == "kr_valuation_warehouse"
    assert payload["meta"]["data_mode"] == "source_backed"
    assert payload["meta"]["price_points_meta"]["data_mode"] == "source_backed"
    assert payload["meta"]["price_points_meta"]["source"] == "kr_valuation_warehouse_year_end_price"
    assert payload["meta"]["financial_numbers_allowed"] is True
    assert payload["meta"]["kr_cache"] is None
    assert payload["meta"]["kr_warehouse"]["valuation_ready"] is True
    historical = [row for row in payload["data"] if not row["forecast_flag"]]
    assert [row["fiscal_year"] for row in historical] == [2020, 2021, 2022, 2023, 2024, 2025]
    for row in historical:
        trace = row["source_trace"]
        assert REQUIRED_SOURCE_TRACE_KEYS <= set(trace)
        assert trace["data_backend"] == "kr_valuation_warehouse"
        assert trace["warehouse_view"] == "kr_valuation_points"
        assert "missing_dividend_source" not in trace["quality_flags"]
        assert trace["price_source_trace"]["warehouse_view"] == "kr_normalized_facts"
        assert trace["price_source_trace"]["cache_path"]
        assert trace["metric_source_trace"]["warehouse_view"] == "kr_normalized_facts"
        assert trace["metric_source_trace"]["cache_path"]
        assert trace["dividend_source_trace"]["warehouse_view"] == "kr_normalized_facts"
        assert trace["dividend_source_trace"]["cache_path"]
    assert historical[-1]["dividend"] == "1500.0"
    assert payload["meta"]["kr_warehouse"]["dividend_status"]["status"] == "ok"

    audit_response = client.get(
        "/api/v1/companies/005930.KS/data-audit?metric=adjusted_operating&forecast_years=1"
    )
    assert audit_response.status_code == 200
    audit_rows = audit_response.json()["data"]
    warehouse_eps_rows = [
        row for row in audit_rows if row["fact_name"] == "kr_warehouse.adjusted_operating_eps"
    ]
    warehouse_price_rows = [
        row for row in audit_rows if row["fact_name"] == "kr_warehouse.price_close"
    ]
    assert len(warehouse_eps_rows) == 6
    assert len(warehouse_price_rows) == 6
    for row in [*warehouse_eps_rows, *warehouse_price_rows]:
        trace = row["source_trace"]
        assert REQUIRED_SOURCE_TRACE_KEYS <= set(trace)
        assert trace["data_backend"] == "kr_valuation_warehouse"
        assert trace["warehouse_view"] == "kr_normalized_facts"
        assert trace["cache_path"]
        assert row["policy"] == "kr_warehouse_normalized_fact"

    valuation_price_rows = [
        row for row in audit_rows if row["fact_name"] == "valuation.price"
    ]
    valuation_metric_rows = [
        row for row in audit_rows if row["fact_name"] == "valuation.metric"
    ]
    valuation_dividend_rows = [
        row for row in audit_rows if row["fact_name"] == "valuation.dividend"
    ]
    assert len(valuation_price_rows) == 6
    assert len(valuation_metric_rows) == 6
    assert len(valuation_dividend_rows) == 6
    latest_price_trace = valuation_price_rows[-1]["source_trace"]
    latest_metric_trace = valuation_metric_rows[-1]["source_trace"]
    latest_dividend_trace = valuation_dividend_rows[-1]["source_trace"]
    assert latest_price_trace["source"] == "pykrx"
    assert latest_price_trace["warehouse_view"] == "kr_normalized_facts"
    assert latest_price_trace["cache_path"]
    assert (
        latest_price_trace["formula"]
        == "source-backed year-end close price from pykrx raw market data"
    )
    assert latest_metric_trace["source"] == "opendart"
    assert latest_metric_trace["warehouse_view"] == "kr_normalized_facts"
    assert latest_metric_trace["cache_path"]
    assert latest_dividend_trace["source"] == "pykrx"
    assert latest_dividend_trace["warehouse_view"] == "kr_normalized_facts"
    assert latest_dividend_trace["cache_path"]
    assert (
        latest_dividend_trace["formula"]
        == "source-backed year-end DPS from pykrx raw fundamental data"
    )

    fact_response = client.get(
        f"/api/data-audit/{warehouse_eps_rows[-1]['fact_id']}?metric=adjusted_operating&forecast_years=1"
    )
    assert fact_response.status_code == 200
    fact_payload = fact_response.json()["data"]
    assert fact_payload["fact_name"] == "kr_warehouse.adjusted_operating_eps"
    assert fact_payload["source_trace"]["warehouse_view"] == "kr_normalized_facts"

    financials_response = client.get("/api/v1/companies/005930.KS/financials")
    assert financials_response.status_code == 200
    financials_payload = financials_response.json()
    assert financials_payload["meta"]["data_backend"] == "kr_valuation_warehouse"
    assert financials_payload["meta"]["financial_numbers_allowed"] is True
    financial_rows = financials_payload["data"]
    assert [row["fiscal_year"] for row in financial_rows] == [2020, 2021, 2022, 2023, 2024, 2025]
    assert financial_rows[-1]["eps"] == "44800.0"
    assert financial_rows[-1]["revenue"] == "100000.0"
    assert financial_rows[-1]["operating_margin"] == "20.00"
    assert financial_rows[-1]["net_margin"] == "15.00"
    financial_trace = financial_rows[-1]["source_trace"]
    assert REQUIRED_SOURCE_TRACE_KEYS <= set(financial_trace)
    assert financial_trace["data_backend"] == "kr_valuation_warehouse"
    assert financial_trace["source_type"] == "kr_warehouse_financials_row"
    assert "kr_warehouse_financials_partial_row" in financial_trace["quality_flags"]

    financial_audit_response = client.get(
        "/api/v1/companies/005930.KS/data-audit?metric=adjusted_operating&forecast_years=1"
    )
    financial_audit_rows = financial_audit_response.json()["data"]
    financial_eps_rows = [
        row for row in financial_audit_rows if row["fact_name"] == "financials.eps"
    ]
    assert len(financial_eps_rows) == 6
    assert financial_eps_rows[-1]["value"] == "44800.0"
    assert financial_eps_rows[-1]["source_trace"]["warehouse_view"] == "kr_normalized_facts"
    financial_margin_rows = [
        row for row in financial_audit_rows if row["fact_name"] == "financials.operating_margin"
    ]
    assert len(financial_margin_rows) == 6
    assert financial_margin_rows[-1]["value"] == "20.00"
    margin_trace = financial_margin_rows[-1]["source_trace"]
    assert REQUIRED_SOURCE_TRACE_KEYS <= set(margin_trace)
    assert margin_trace["source_type"] == "kr_warehouse_financials_derived_metric"
    assert margin_trace["formula"] == "operating_margin = operating_income / revenue * 100"
    assert "kr_warehouse_financials_derived_metric" in margin_trace["quality_flags"]


def test_kr_valuation_cache_coverage_summarizes_complete_partial_and_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("KR_VALUATION_INPUT_CACHE_DIR", str(tmp_path))
    partial_payload = _kr_ready_cache_payload()
    complete_payload = json.loads(json.dumps(_kr_complete_cache_payload()))
    complete_payload["ticker"] = "000660.KS"
    for point in complete_payload["valuation_points"]:
        point["ticker"] = "000660.KS"
    for fact in complete_payload["normalized_facts"]:
        fact["ticker"] = "000660.KS"
    (tmp_path / "005930_KS-2023-2024-valuation-inputs.json").write_text(
        json.dumps(partial_payload),
        encoding="utf-8",
    )
    (tmp_path / "000660_KS-2020-2025-valuation-inputs.json").write_text(
        json.dumps(complete_payload),
        encoding="utf-8",
    )

    response = client.get(
        "/api/v1/system/kr-valuation-cache-coverage?tickers=005930.KS,000660.KS,402340.KS"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["data_backend"] == "kr_valuation_input_cache"
    assert payload["coverage_status"] == "missing_source_backed_cache"
    assert payload["summary"]["tickers_expected"] == 3
    assert payload["summary"]["cache_files_found"] == 2
    assert payload["summary"]["valuation_ready"] == 2
    assert payload["summary"]["complete"] == 1
    assert payload["summary"]["partial_source_backed"] == 1
    assert payload["summary"]["missing"] == 1
    assert REQUIRED_SOURCE_TRACE_KEYS <= set(payload["source_trace"])
    rows = {row["ticker"]: row for row in payload["rows"]}
    assert rows["000660.KS"]["coverage_status"] == "complete"
    assert rows["005930.KS"]["coverage_status"] == "partial_source_backed"
    assert rows["402340.KS"]["coverage_status"] == "missing_source_backed_cache"
    assert rows["005930.KS"]["valuation_years"] == [2023, 2024]
    assert rows["000660.KS"]["gap_audit_refs"] == []
    assert rows["402340.KS"]["gap_audit_refs"] == []
    gap_refs = rows["005930.KS"]["gap_audit_refs"]
    assert [ref["scope"] for ref in gap_refs] == ["market", "financial"]
    assert gap_refs[0]["fact_id"] == (
        "005930.KS-2022-data_quality.kr_market_gap.source_no_rows_before_first_trade"
    )
    assert gap_refs[0]["source_document_id"] == (
        "kr-cache:005930.KS:2022:market-gap:source_no_rows_before_first_trade"
    )
    assert gap_refs[1]["fact_id"] == "005930.KS-2022-data_quality.kr_financial_gap.source_no_data"
    assert gap_refs[1]["source_document_id"] == "opendart:005930.KS:2022:status:013"


def test_kr_priority_valuation_map_rejects_cache_points_without_storage_ready_trace(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.setenv("KR_VALUATION_INPUT_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("DATA_BACKEND", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    payload = _kr_ready_cache_payload()
    for point in payload["valuation_points"]:
        point["source_trace"].pop("source_document_id", None)
        point["source_trace"].pop("filing_id", None)
        point["source_trace"].pop("accession_number", None)
    cache_path = tmp_path / "005930_KS-2023-2024-valuation-inputs.json"
    cache_path.write_text(json.dumps(payload), encoding="utf-8")

    response = client.get(
        "/api/v1/companies/005930.KS/valuation-map?metric=adjusted_operating"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"] == []
    assert payload["meta"]["data_backend"] == "kr_valuation_input_cache"
    assert payload["meta"]["data_mode"] == "source_backed_required"
    assert payload["meta"]["kr_cache"]["valuation_ready"] is False
    assert payload["meta"]["kr_cache"]["rejected_cache_points"] == 2
    assert (
        "rejected_kr_cache_points_missing_source_trace"
        in payload["meta"]["kr_cache"]["quality_flags"]
    )


def _kr_ready_cache_payload():
    facts = []
    points = []
    for year, price, eps in [(2023, "70000", "8000"), (2024, "76000", "9000")]:
        price_fact_id = f"fact:kr:005930.KS:{year}:price_close"
        eps_fact_id = f"fact:kr:005930.KS:{year}:adjusted_operating_eps"
        price_trace = _kr_source_trace(
            year=year,
            source="pykrx",
            document_id=f"raw:pykrx:{year}",
            unit="KRW/share",
            method="PYKRX_RAW_YEAR_END_CLOSE",
            formula="source-backed year-end close price from pykrx raw market data",
        )
        eps_trace = _kr_source_trace(
            year=year,
            source="opendart",
            document_id=f"raw:opendart:{year}",
            unit="KRW/share",
            method="S3_MARKET_STANDARD_KR",
            formula="OpenDART reported EPS normalized as KR market-standard operating metric",
        )
        facts.extend(
            [
                {
                    "fact_id": price_fact_id,
                    "ticker": "005930.KS",
                    "metric": "price_close",
                    "period": f"FY{year}",
                    "fiscal_year": year,
                    "value": price,
                    "unit": "KRW/share",
                    "currency": "KRW",
                    "source_trace": price_trace,
                },
                {
                    "fact_id": eps_fact_id,
                    "ticker": "005930.KS",
                    "metric": "adjusted_operating_eps",
                    "period": f"FY{year}",
                    "fiscal_year": year,
                    "value": eps,
                    "unit": "KRW/share",
                    "currency": "KRW",
                    "source_trace": eps_trace,
                },
            ]
        )
        points.append(
            {
                "valuation_point_id": f"valuation:kr:005930.KS:{year}:adjusted_operating_eps",
                "ticker": "005930.KS",
                "fiscal_year": year,
                "period": f"FY{year}",
                "metric": "adjusted_operating_eps",
                "metric_value": eps,
                "price": price,
                "currency": "KRW",
                "source_trace": {
                    **_kr_source_trace(
                        year=year,
                        source="derived",
                        document_id=f"derived:kr:005930.KS:{year}:valuation-input",
                        unit="KRW/share",
                        method="KR_SOURCE_BACKED_PRICE_EPS_JOIN",
                        formula="source-backed year-end close price joined to OpenDART EPS",
                    ),
                    "input_fact_ids": [price_fact_id, eps_fact_id],
                    "quality_flags": [
                        "source_backed_valuation_input",
                        "missing_dividend_source",
                    ],
                },
            }
        )
    return {
        "ticker": "005930.KS",
        "status": "ok",
        "valuation_ready": True,
        "full_coverage_ready": False,
        "coverage_status": "partial_source_backed",
        "data_mode": "source_backed_raw_valuation_inputs",
        "coverage_years": {
            "price": [2023, 2024],
            "market_structure": [2023, 2024],
            "financial_metric": [2024],
            "valuation_points": [2023, 2024],
        },
        "missing_years": {
            "market_input": [2022],
            "financial_metric": [2022],
        },
        "market_gap_diagnostics": [
            {
                "fiscal_year": 2022,
                "status": "source_no_rows_before_first_trade",
                "reason": "No pykrx or marcap rows exist for this ticker before the first cached market row 2023-12-31.",
                "next_action": "keep_partial_market_history_start",
                "missing_price": True,
                "missing_market_structure": True,
                "first_available_market_date": "2023-12-31",
            }
        ],
        "financial_gap_diagnostics": [
            {
                "fiscal_year": 2022,
                "status": "source_no_data",
                "reason": "OpenDART returned a non-success status for this annual filing request.",
                "next_action": "keep_partial_or_add_alternate_source",
                "opendart_status": "013",
                "opendart_message": "조회된 데이타가 없습니다.",
                "row_count": 0,
            }
        ],
        "metric_status": {
            "status": "ok",
            "method": "S3_MARKET_STANDARD_KR",
            "quality_flags": ["source_backed_financial_metric"],
        },
        "normalized_facts": facts,
        "valuation_points": points,
        "dividend_status": {
            "status": "blocked",
            "reason": "missing_source_backed_dividend_per_share",
        },
        "quality_flags": ["source_backed_financial_metric", "partial_valuation_coverage"],
        "generated_at": "2026-06-29T00:00:00+00:00",
    }


def _kr_complete_cache_payload():
    facts = []
    points = []
    years = [2020, 2021, 2022, 2023, 2024, 2025]
    prices = ["81000", "78300", "55300", "78500", "53200", "100800"]
    eps_values = ["3841", "5777", "8057", "2131", "4985", "44800"]
    revenues = ["100000", "100000", "100000", "100000", "100000", "100000"]
    operating_incomes = ["20000", "20000", "20000", "20000", "20000", "20000"]
    net_incomes_parent = ["15000", "15000", "15000", "15000", "15000", "15000"]
    dividends = ["1444", "1444", "1444", "1444", "1444", "1500"]
    for year, price, eps, revenue, operating_income, net_income_parent, dividend in zip(
        years,
        prices,
        eps_values,
        revenues,
        operating_incomes,
        net_incomes_parent,
        dividends,
    ):
        price_fact_id = f"fact:kr:005930.KS:{year}:price_close"
        eps_fact_id = f"fact:kr:005930.KS:{year}:adjusted_operating_eps"
        dividend_fact_id = f"fact:kr:005930.KS:{year}:dividend_per_share"
        price_trace = _kr_source_trace(
            year=year,
            source="pykrx",
            document_id=f"raw:pykrx:005930.KS:{year}",
            unit="KRW/share",
            method="PYKRX_RAW_YEAR_END_CLOSE",
            formula="source-backed year-end close price from pykrx raw market data",
        )
        eps_trace = _kr_source_trace(
            year=year,
            source="opendart",
            document_id=f"raw:opendart:005930.KS:{year}",
            unit="KRW/share",
            method="S3_MARKET_STANDARD_KR",
            formula="OpenDART reported EPS normalized as KR market-standard operating metric",
        )
        dividend_trace = _kr_source_trace(
            year=year,
            source="pykrx",
            document_id=f"raw:pykrx:005930.KS:{year}:fundamental",
            unit="KRW/share",
            method="PYKRX_RAW_YEAR_END_DPS",
            formula="source-backed year-end DPS from pykrx raw fundamental data",
        )
        facts.extend(
            [
                {
                    "fact_id": price_fact_id,
                    "ticker": "005930.KS",
                    "metric": "price_close",
                    "period": f"FY{year}",
                    "fiscal_year": year,
                    "value": price,
                    "unit": "KRW/share",
                    "currency": "KRW",
                    "source_trace": price_trace,
                },
                {
                    "fact_id": eps_fact_id,
                    "ticker": "005930.KS",
                    "metric": "adjusted_operating_eps",
                    "period": f"FY{year}",
                    "fiscal_year": year,
                    "value": eps,
                    "unit": "KRW/share",
                    "currency": "KRW",
                    "source_trace": eps_trace,
                },
                {
                    "fact_id": f"fact:kr:005930.KS:{year}:revenue",
                    "ticker": "005930.KS",
                    "metric": "revenue",
                    "period": f"FY{year}",
                    "fiscal_year": year,
                    "value": revenue,
                    "unit": "KRW",
                    "currency": "KRW",
                    "source_trace": _kr_source_trace(
                        year=year,
                        source="opendart",
                        document_id=f"raw:opendart:005930.KS:{year}:revenue",
                        unit="KRW",
                        method="S3_MARKET_STANDARD_KR",
                        formula="OpenDART reported revenue line item",
                    ),
                },
                {
                    "fact_id": f"fact:kr:005930.KS:{year}:operating_income",
                    "ticker": "005930.KS",
                    "metric": "operating_income",
                    "period": f"FY{year}",
                    "fiscal_year": year,
                    "value": operating_income,
                    "unit": "KRW",
                    "currency": "KRW",
                    "source_trace": _kr_source_trace(
                        year=year,
                        source="opendart",
                        document_id=f"raw:opendart:005930.KS:{year}:operating_income",
                        unit="KRW",
                        method="S3_MARKET_STANDARD_KR",
                        formula="OpenDART reported operating income line item",
                    ),
                },
                {
                    "fact_id": f"fact:kr:005930.KS:{year}:net_income_parent",
                    "ticker": "005930.KS",
                    "metric": "net_income_parent",
                    "period": f"FY{year}",
                    "fiscal_year": year,
                    "value": net_income_parent,
                    "unit": "KRW",
                    "currency": "KRW",
                    "source_trace": _kr_source_trace(
                        year=year,
                        source="opendart",
                        document_id=f"raw:opendart:005930.KS:{year}:net_income_parent",
                        unit="KRW",
                        method="S3_MARKET_STANDARD_KR",
                        formula="OpenDART reported net income attributable to parent",
                    ),
                },
                {
                    "fact_id": dividend_fact_id,
                    "ticker": "005930.KS",
                    "metric": "dividend_per_share",
                    "period": f"FY{year}",
                    "fiscal_year": year,
                    "value": dividend,
                    "unit": "KRW/share",
                    "currency": "KRW",
                    "source_trace": dividend_trace,
                },
            ]
        )
        points.append(
            {
                "valuation_point_id": f"valuation:kr:005930.KS:{year}:adjusted_operating_eps",
                "ticker": "005930.KS",
                "fiscal_year": year,
                "period": f"FY{year}",
                "metric": "adjusted_operating_eps",
                "metric_value": eps,
                "price": price,
                "currency": "KRW",
                "source_trace": {
                    **_kr_source_trace(
                        year=year,
                        source="derived",
                        document_id=f"derived:kr:005930.KS:{year}:valuation-input",
                        unit="KRW/share",
                        method="KR_SOURCE_BACKED_PRICE_EPS_JOIN",
                        formula="source-backed year-end close price joined to OpenDART EPS",
                    ),
                    "input_fact_ids": [price_fact_id, eps_fact_id, dividend_fact_id],
                    "quality_flags": [
                        "source_backed_valuation_input",
                        "source_backed_dividend",
                    ],
                    "metadata": {
                        "price_source_trace": price_trace,
                        "metric_source_trace": eps_trace,
                        "dividend_source_trace": dividend_trace,
                    },
                },
            }
        )
    return {
        "ticker": "005930.KS",
        "status": "ok",
        "valuation_ready": True,
        "full_coverage_ready": True,
        "coverage_status": "complete",
        "data_mode": "source_backed_raw_valuation_inputs",
        "coverage_years": {
            "price": years,
            "market_structure": years,
            "financial_metric": years,
            "valuation_points": years,
        },
        "missing_years": {
            "market_input": [],
            "financial_metric": [],
        },
        "market_gap_diagnostics": [],
        "financial_gap_diagnostics": [],
        "metric_status": {
            "status": "ok",
            "method": "S3_MARKET_STANDARD_KR",
            "financial_source_count": 36,
            "financial_years": years,
            "quality_flags": ["source_backed_financial_metric"],
        },
        "normalized_facts": facts,
        "valuation_points": points,
        "dividend_status": {
            "status": "blocked",
            "reason": "missing_source_backed_dividend_per_share",
        },
        "quality_flags": ["source_backed_financial_metric", "complete_valuation_coverage"],
        "generated_at": "2026-06-30T00:00:00+00:00",
    }


def _kr_source_trace(
    *,
    year: int,
    source: str,
    document_id: str,
    unit: str,
    method: str,
    formula: str,
) -> dict:
    return {
        "source": source,
        "source_type": source,
        "source_document_id": document_id,
        "filing_id": document_id,
        "accession_number": document_id,
        "form": "raw_market_file" if source in {"pykrx", "marcap"} else "derived",
        "form_type": "raw_market_file" if source in {"pykrx", "marcap"} else "derived",
        "period": f"FY{year}",
        "fiscal_year": year,
        "fiscal_period": "FY",
        "period_start": f"{year}-01-01",
        "period_end": f"{year}-12-31",
        "available_at": f"{year + 1}-04-01T00:00:00+09:00",
        "unit": unit,
        "currency": "KRW",
        "method": method,
        "formula": formula,
        "input_fact_ids": [],
        "adjustments": [],
        "confidence": "0.90",
        "quality_flags": ["source_backed"],
        "quality_status": "source_backed",
        "version": 1,
    }


def test_adjusted_waterfall_steps_return_complete_source_trace():
    response = client.get("/api/security/AAPL/adjusted/waterfall?fiscal_year=2024")
    assert response.status_code == 200
    steps = response.json()["waterfall"]
    assert steps
    for step in steps:
        trace = step["source_trace"]
        for key in REQUIRED_SOURCE_TRACE_KEYS:
            assert trace[key], (step["label"], key, trace)


def test_valuation_map_includes_forecast_and_visibility():
    response = client.get(
        "/api/v1/companies/AAPL/valuation-map?metric=adjusted_operating&forecast_years=5"
        "&start_year=2022&end_year=2024&normal_multiple_years=1"
        "&show_price=false&show_current_valuation=false&show_custom_valuation=true"
        "&custom_valuation_multiple=22&show_payout_ratio=false&show_dividend_yield=true"
        "&show_recession_bands=false&hidden_scenario_lines=18x,19x"
    )
    assert response.status_code == 200
    payload = response.json()
    assert any(row["forecast_flag"] for row in payload["data"])
    assert payload["meta"]["line_visibility"]["price"] is False
    assert payload["meta"]["line_visibility"]["current_valuation"] is False
    assert payload["meta"]["line_visibility"]["custom_valuation"] is True
    assert payload["meta"]["line_visibility"]["custom_valuation_multiple"] == "22"
    assert payload["meta"]["line_visibility"]["payout_ratio"] is False
    assert payload["meta"]["line_visibility"]["dividend_yield"] is True
    assert payload["meta"]["line_visibility"]["recession_bands"] is False
    assert payload["meta"]["line_visibility"]["hidden_scenario_lines"] == ["18x", "19x"]
    assert payload["meta"]["range"]["start_year"] == 2022
    assert payload["meta"]["range"]["end_year"] == 2024
    assert payload["data"][0]["fiscal_year"] == 2022
    assert payload["meta"]["normal_multiple"]["window_years"] == 1
    assert payload["data"][0]["normal_multiple"] == "41.12"
    assert payload["meta"]["recession_bands"] == []
    assert len(payload["meta"]["price_points"]) == 3
    assert payload["meta"]["price_points_meta"]["frequency"] == "annual_fixture"
    assert payload["meta"]["price_points"][0]["date"] == "2022-12-31"
    assert (
        payload["meta"]["price_points"][0]["source_trace"]["source_document_id"]
        == "aapl-2022-price"
    )
    assert payload["meta"]["forecast"]["source"] in {"deterministic_trend", "user_input"}
    assert len(payload["meta"]["forecast"]["calculation_lines"]) == 11


def test_valuation_map_rows_keep_complete_source_trace_through_forecast():
    response = client.get(
        "/api/v1/companies/AAPL/valuation-map?metric=adjusted_operating"
        "&forecast_years=5&start_year=2022&end_year=2024"
    )

    assert response.status_code == 200
    rows = response.json()["data"]
    assert any(not row["forecast_flag"] for row in rows)
    assert any(row["forecast_flag"] for row in rows)
    for row in rows:
        assert REQUIRED_VALUATION_KEYS <= row.keys()
        trace = row["source_trace"]
        for key in REQUIRED_SOURCE_TRACE_KEYS:
            assert trace[key], (row["fiscal_year"], key, trace)


def test_data_audit_includes_forecast_assumption_source_trace():
    response = client.get(
        "/api/v1/companies/AAPL/data-audit?forecast_mode=custom&target_multiple=21"
    )
    assert response.status_code == 200
    payload = response.json()
    assert "forecast_assumption" in payload["meta"]["scope"]
    assert "forecast_snapshot" in payload["meta"]["scope"]
    assert "forecast_case" in payload["meta"]["scope"]
    assert "forecast_scenario" in payload["meta"]["scope"]
    rows = {
        row["fact_name"]: row
        for row in payload["data"]
        if row["fact_name"].startswith("forecast_assumption.")
    }
    assert rows["forecast_assumption.formula"]["policy"] == "forecast_assumption"
    assert rows["forecast_assumption.growth_rate_pct"]["source_trace"]["source_document_id"]
    assert rows["forecast_assumption.target_multiple"]["value"] == "21"
    assert rows["forecast_assumption.target_multiple"]["source_trace"]["period"].startswith("FY")
    assert rows["forecast_assumption.formula"]["formula"]
    chart_key_rows = {
        row["fact_name"]: row
        for row in payload["data"]
        if row["fact_name"].startswith("chart_key.")
    }
    assert chart_key_rows["chart_key.custom_multiple"]["value"] == "21.00"
    scenario_rows = {
        row["fact_name"]: row
        for row in payload["data"]
        if row["fact_name"].startswith("forecast_scenario.")
    }
    assert "forecast_scenario.21x.target_price" in scenario_rows
    assert scenario_rows["forecast_scenario.21x.target_price"]["source_trace"][
        "scenario_multiple"
    ] == "21.00"
    assert scenario_rows["forecast_scenario.21x.target_price"]["source_trace"]["formula"] == (
        "scenario_target_price = forecast metric * scenario multiple"
    )
    forecast_return_rows = [
        row
        for row in payload["data"]
        if row["fact_name"]
        in {
            "forecast.price_cagr_pct",
            "forecast.total_return_cagr_pct",
            "forecast.margin_of_safety_pct",
        }
    ]
    assert len(forecast_return_rows) >= 3
    assert all(row["policy"] == "forecast" for row in forecast_return_rows)
    assert all(row["source_trace"]["formula"] for row in forecast_return_rows)
    total_return_fact = next(
        row for row in forecast_return_rows if row["fact_name"] == "forecast.total_return_cagr_pct"
    )
    margin_of_safety_fact = next(
        row for row in forecast_return_rows if row["fact_name"] == "forecast.margin_of_safety_pct"
    )
    assert "annual_dividend" in total_return_fact["formula"]
    assert total_return_fact["source_trace"]["unit"] == "percent"
    assert margin_of_safety_fact["source_trace"]["unit"] == "percent"
    assert "target_price - start_price" in margin_of_safety_fact["formula"]
    inputs = total_return_fact["source_trace"]["calculation_inputs"]
    assert inputs["start_price"]
    assert inputs["start_price_trace"]["source_document_id"] == "aapl-2024-price"
    assert inputs["dividend_trace"]["source_document_id"] == (
        "aapl-2024-dividend_per_share"
    )
    mos_inputs = margin_of_safety_fact["source_trace"]["calculation_inputs"]
    assert mos_inputs["start_price"]
    assert mos_inputs["target_price"]
    assert mos_inputs["start_price_trace"]["source_document_id"] == "aapl-2024-price"
    case_rows = {
        row["fact_name"]: row
        for row in payload["data"]
        if row["fact_name"].startswith("forecast_case.")
    }
    assert {
        "forecast_case.low.target_price",
        "forecast_case.median.target_price",
        "forecast_case.high.target_price",
        "forecast_case.median.total_return_cagr_pct",
        "forecast_case.median.margin_of_safety_pct",
    } <= set(case_rows)
    median_case_return = case_rows["forecast_case.median.total_return_cagr_pct"]
    assert median_case_return["policy"] == "forecast_case"
    assert median_case_return["source_trace"]["unit"] == "percent"
    assert median_case_return["source_trace"]["forecast_case"] == "median"
    case_inputs = median_case_return["source_trace"]["calculation_inputs"]
    assert case_inputs["estimate_eps"]
    assert case_inputs["target_multiple"] == "21"
    assert median_case_return["source_trace"]["input_source_traces"][
        "start_price_trace"
    ]["source_document_id"] == "aapl-2024-price"
    assert any(row["fact_name"] == "valuation.yoy" for row in payload["data"])

    fact_response = client.get(
        "/api/data-audit/AAPL-2024-chart_key.custom_multiple?forecast_mode=custom&target_multiple=21"
    )
    assert fact_response.status_code == 200
    fact_detail = fact_response.json()["data"]
    assert fact_detail["value"] == "21.00"
    fact_sections = {section["title"]: section for section in fact_detail["trace_sections"]}
    assert {"Source evidence", "Calculation", "Quality"} <= set(fact_sections)
    assert any(
        row["label"] == "Source document"
        for row in fact_sections["Source evidence"]["rows"]
    )
    assert any(row["label"] == "Formula" for row in fact_sections["Calculation"]["rows"])
    return_fact_response = client.get(
        f"/api/data-audit/{total_return_fact['fact_id']}?forecast_mode=custom&target_multiple=21"
    )
    assert return_fact_response.status_code == 200
    assert return_fact_response.json()["data"]["value"] == total_return_fact["value"]
    scenario_fact_response = client.get(
        f"/api/data-audit/{scenario_rows['forecast_scenario.21x.target_price']['fact_id']}"
        "?forecast_mode=custom&target_multiple=21"
    )
    assert scenario_fact_response.status_code == 200
    scenario_detail = scenario_fact_response.json()["data"]
    scenario_sections = {
        section["title"]: section for section in scenario_detail["trace_sections"]
    }
    assert {"Source evidence", "Calculation", "Quality", "Input traces"} <= set(
        scenario_sections
    )
    calculation_rows = {
        row["label"]: row["value"] for row in scenario_sections["Calculation"]["rows"]
    }
    assert (
        calculation_rows["Formula"]
        == "scenario_target_price = forecast metric * scenario multiple"
    )
    input_keys = {row["key"] for row in scenario_sections["Input traces"]["rows"]}
    assert "forecast_metric_trace" in input_keys
    case_fact_response = client.get(
        f"/api/data-audit/{median_case_return['fact_id']}?forecast_mode=custom&target_multiple=21"
    )
    assert case_fact_response.status_code == 200
    case_detail = case_fact_response.json()["data"]
    case_sections = {
        section["title"]: section for section in case_detail["trace_sections"]
    }
    assert {"Source evidence", "Calculation", "Quality", "Input traces"} <= set(
        case_sections
    )
    case_calculation_rows = {
        row["label"]: row["value"] for row in case_sections["Calculation"]["rows"]
    }
    assert "annual_dividend" in case_calculation_rows["Formula"]
    case_input_keys = {row["key"] for row in case_sections["Input traces"]["rows"]}
    assert "forecast_snapshot_trace" in case_input_keys
    assert "start_price_trace" in case_input_keys


def test_valuation_map_schema_and_source_trace_are_complete_for_seed_universe():
    for ticker in ["AAPL", "NVDA", "005930.KS", "7203.T"]:
        response = client.get(f"/api/v1/companies/{ticker}/valuation-map?forecast_years=5")
        assert response.status_code == 200
        rows = response.json()["data"]
        assert rows
        assert any(row["forecast_flag"] for row in rows)
        for row in rows:
            assert REQUIRED_VALUATION_KEYS <= set(row)
            assert REQUIRED_SOURCE_TRACE_KEYS <= set(row["source_trace"])
            quality = validate_valuation_row(row)
            assert quality.status == "passed", (ticker, row["fiscal_year"], quality.flags)


def test_valuation_map_metric_selector_uses_requested_metric_source():
    revenue = client.get(
        "/api/v1/companies/AAPL/valuation-map?metric=revenue_share&forecast_years=1"
    )
    assert revenue.status_code == 200
    revenue_payload = revenue.json()
    assert revenue_payload["meta"]["metric_label"] == "Revenue/share"
    latest_revenue = [row for row in revenue_payload["data"] if not row["forecast_flag"]][-1]
    assert latest_revenue["metric"] == "25.38"
    assert "revenue_reported" in latest_revenue["source_trace"]["formula"]

    sales = client.get(
        "/api/v1/companies/AAPL/valuation-map?metric=sales_share&forecast_years=1"
    )
    assert sales.status_code == 200
    sales_payload = sales.json()
    assert sales_payload["meta"]["metric_label"] == "Sales/share"
    latest_sales = [row for row in sales_payload["data"] if not row["forecast_flag"]][-1]
    assert latest_sales["metric"] == latest_revenue["metric"]
    assert "revenue_reported" in latest_sales["source_trace"]["formula"]

    diluted = client.get(
        "/api/v1/companies/AAPL/valuation-map?metric=diluted_eps&forecast_years=1"
    )
    assert diluted.status_code == 200
    diluted_payload = diluted.json()
    assert diluted_payload["meta"]["metric_label"] == "Diluted EPS"
    latest_diluted = [row for row in diluted_payload["data"] if not row["forecast_flag"]][-1]
    assert latest_diluted["metric"] == "6.08"
    assert "gaap_eps_diluted" in latest_diluted["source_trace"]["formula"]

    fcf = client.get("/api/v1/companies/AAPL/valuation-map?metric=fcf_share&forecast_years=1")
    assert fcf.status_code == 200
    assert fcf.json()["meta"]["metric_label"] == "Free Cash Flow to Equity (FCFE/AFFO)"
    latest_fcf = [row for row in fcf.json()["data"] if not row["forecast_flag"]][-1]
    assert latest_fcf["metric"] == "7.06"
    assert "fcf_reported" in latest_fcf["source_trace"]["formula"]

    reit = client.get("/api/v1/companies/O/valuation-map?metric=ffo_affo&forecast_years=1")
    assert reit.status_code == 200
    latest_reit = [row for row in reit.json()["data"] if not row["forecast_flag"]][-1]
    assert latest_reit["metric"] == "3.80"
    assert latest_reit["source_trace"]["quality_status"] == "fixture_non_production_reit_proxy"

    non_reit = client.get("/api/v1/companies/AAPL/valuation-map?metric=ffo_affo")
    assert non_reit.status_code == 400

    basic_missing = client.get("/api/v1/companies/AAPL/valuation-map?metric=basic_eps")
    assert basic_missing.status_code == 400
    assert "unsupported or unavailable valuation metric" in basic_missing.json()["detail"]


def test_valuation_map_supports_manual_and_ai_review_forecasts():
    manual = client.get(
        "/api/v1/companies/AAPL/valuation-map?forecast_mode=custom&forecast_years=3&manual_eps_values=7.50,8.00,8.50&target_multiple=20"
    )
    assert manual.status_code == 200
    manual_payload = manual.json()
    forecast_rows = [row for row in manual_payload["data"] if row["forecast_flag"]]
    assert forecast_rows[0]["metric"] == "7.50"
    assert manual_payload["meta"]["forecast"]["source"] == "user_input"
    assert manual_payload["meta"]["forecast"]["manual_eps_values"] == ["7.50", "8.00", "8.50"]

    ai_review = client.get("/api/v1/companies/AAPL/valuation-map?forecast_mode=ai_review")
    assert ai_review.status_code == 200
    ai_payload = ai_review.json()
    assert ai_payload["meta"]["forecast"]["source"] == "ai_assisted_review"
    ai_trace = ai_payload["meta"]["forecast"]["source_trace"]
    assert REQUIRED_SOURCE_TRACE_KEYS <= set(ai_trace)
    assert ai_trace["method"] == "deterministic_ai_review"
    assert ai_trace["llm_generated_numbers"] is False
    assert ai_trace["ai_role"] == "commentary_only"
    assert "no LLM-generated numbers" in ai_trace["formula"]
    assert ai_trace["calculation_inputs"]["historical_growth_rate_pct"]
    ai_forecast_rows = [row for row in ai_payload["data"] if row["forecast_flag"]]
    assert ai_forecast_rows
    assert ai_forecast_rows[0]["source_trace"]["method"] == "deterministic_ai_review"
    assert ai_forecast_rows[0]["source_trace"]["llm_generated_numbers"] is False


def test_data_audit_marks_ai_review_as_non_numeric_generation():
    response = client.get("/api/v1/companies/AAPL/data-audit?forecast_mode=ai_review")
    assert response.status_code == 200
    rows = {
        row["fact_name"]: row
        for row in response.json()["data"]
        if row["fact_name"].startswith("forecast_assumption.")
    }
    assert rows["forecast_assumption.source"]["value"] == "ai_assisted_review"
    formula_trace = rows["forecast_assumption.formula"]["source_trace"]
    assert REQUIRED_SOURCE_TRACE_KEYS <= set(formula_trace)
    assert formula_trace["method"] == "deterministic_ai_review"
    assert formula_trace["llm_generated_numbers"] is False
    assert formula_trace["ai_role"] == "commentary_only"
    assert "no LLM-generated numbers" in rows["forecast_assumption.formula"]["formula"]


def test_valuation_map_rejects_invalid_forecast_query_values():
    invalid_manual = client.get(
        "/api/v1/companies/AAPL/valuation-map?forecast_mode=custom&manual_eps_values=not-a-number"
    )
    assert invalid_manual.status_code == 400
    assert "manual_eps_values" in invalid_manual.json()["detail"]

    huge_multiple = client.get(
        "/api/v1/companies/AAPL/valuation-map?forecast_mode=custom&target_multiple=1e1000000"
    )
    assert huge_multiple.status_code == 400
    assert "target_multiple" in huge_multiple.json()["detail"]


def test_forecast_modes_keep_default_multiple_without_override():
    estimates = client.get(
        "/api/v1/companies/AAPL/valuation-map?forecast_mode=estimates&forecast_years=1"
    )
    assert estimates.status_code == 200
    assert estimates.json()["meta"]["forecast"]["target_multiple"] == "16.68"

    normal = client.get(
        "/api/v1/companies/AAPL/valuation-map?forecast_mode=normal_multiple&forecast_years=1"
    )
    assert normal.status_code == 200
    assert normal.json()["meta"]["forecast"]["target_multiple"] == "33.07"


def test_forecast_case_controls_consensus_projection_and_trace():
    low = client.get(
        "/api/v1/companies/AAPL/valuation-map?forecast_mode=estimates&forecast_case=low&forecast_years=1"
    )
    high = client.get(
        "/api/v1/companies/AAPL/valuation-map?forecast_mode=estimates&forecast_case=high&forecast_years=1"
    )
    assert low.status_code == 200
    assert high.status_code == 200

    low_payload = low.json()
    high_payload = high.json()
    low_forecast = [row for row in low_payload["data"] if row["forecast_flag"]][0]
    high_forecast = [row for row in high_payload["data"] if row["forecast_flag"]][0]
    assert Decimal(high_forecast["metric"]) > Decimal(low_forecast["metric"])
    assert high_payload["meta"]["forecast"]["case"] == "high"
    assert high_payload["meta"]["forecast"]["consensus"]["selected_growth_rate_pct"] == "9.0"
    assert high_forecast["source_trace"]["forecast_case"] == "high"
    assert (
        high_forecast["source_trace"]["consensus_quality_status"]
        == "fixture_non_production_consensus_proxy"
    )


def test_source_backed_valuation_map_uses_consensus_snapshot_projection(monkeypatch):
    historical_trace = {
        "source_document_id": "adjusted-doc",
        "source_type": "postgres",
        "filing_id": "adjusted-filing",
        "period": "FY2024",
        "available_at": "2025-01-31T00:00:00+00:00",
        "unit": "per_share",
        "currency": "USD",
        "formula": "source-backed adjusted EPS",
        "quality_status": "source_backed",
    }
    consensus_trace_2025 = {
        "source_document_id": "consensus-doc-2025",
        "source_type": "user_consensus_csv",
        "filing_id": "consensus-2025",
        "period": "FY2025E",
        "available_at": "2024-12-15T00:00:00+00:00",
        "unit": "per_share",
        "currency": "USD",
        "formula": "point-in-time consensus estimate snapshot",
        "quality_status": "source_backed_consensus_snapshots",
    }
    consensus_trace_2026 = {
        **consensus_trace_2025,
        "source_document_id": "consensus-doc-2026",
        "filing_id": "consensus-2026",
        "period": "FY2026E",
    }
    monkeypatch.setattr(
        "services.api.main.valuation_points_from_postgres",
        lambda ticker, metric: (
            [
                ValuationPoint(
                    fiscal_year=2023,
                    metric=Decimal("10.00"),
                    price=Decimal("180.00"),
                    dividend=Decimal("1.00"),
                    source_trace=historical_trace,
                ),
                ValuationPoint(
                    fiscal_year=2024,
                    metric=Decimal("12.00"),
                    price=Decimal("240.00"),
                    dividend=Decimal("1.10"),
                    source_trace=historical_trace,
                ),
            ],
            "Adjusted Operating EPS",
            {"data_backend": "postgres", "currency": "USD", "country": "US"},
        ),
    )
    monkeypatch.setattr(
        "services.api.main.consensus_projection_from_postgres",
        lambda ticker, forecast_case, start_year, years, start_metric: {
            "case": "median",
            "metric_values": ["13.20", "14.50", None],
            "growth_rate_pct": "10.00",
            "analyst_count": 12,
            "quality_status": "partial_source_backed_consensus_snapshots",
            "missing_years": [2027],
            "source_trace": {
                **consensus_trace_2026,
                "missing_consensus_years": [2027],
            },
            "source_traces_by_year": {
                "2025": consensus_trace_2025,
                "2026": consensus_trace_2026,
            },
            "source_note": "point-in-time consensus estimate snapshots loaded from Postgres",
        },
    )
    monkeypatch.setattr(
        "services.api.main.price_points_from_postgres",
        lambda ticker, start_year, end_year: [
            {
                "date": "2023-01-31",
                "fiscal_year": 2023,
                "close_price": "190.00",
                "currency": "USD",
                "frequency": "monthly",
                "source_trace": {
                    **historical_trace,
                    "source_document_id": "price-doc-2023-01",
                    "period": "2023-01-31",
                    "formula": "monthly last close from price_bars",
                },
            },
            {
                "date": "2024-12-31",
                "fiscal_year": 2024,
                "close_price": "240.00",
                "currency": "USD",
                "frequency": "monthly",
                "source_trace": {
                    **historical_trace,
                    "source_document_id": "price-doc-2024-12",
                    "period": "2024-12-31",
                    "formula": "monthly last close from price_bars",
                },
            },
        ],
    )

    response = client.get(
        "/api/v1/companies/POSTGRESONLY/valuation-map"
        "?forecast_mode=estimates&forecast_case=median&forecast_years=3"
    )

    assert response.status_code == 200
    payload = response.json()
    forecast_rows = [row for row in payload["data"] if row["forecast_flag"]]
    assert [row["metric"] for row in forecast_rows[:2]] == ["13.20", "14.50"]
    assert forecast_rows[0]["forecast_source"] == "consensus_snapshot"
    assert forecast_rows[0]["source_trace"]["source_document_id"] == "consensus-doc-2025"
    assert forecast_rows[2]["metric"] == "15.95"
    assert forecast_rows[2]["source_trace"]["missing_consensus_years"] == [2027]
    assert forecast_rows[2]["source_trace"]["period"] == "FY2027E"
    assert forecast_rows[2]["source_trace"]["source_document_id"] == (
        "postgresonly-2027-missing-consensus-fallback"
    )
    assert forecast_rows[2]["source_trace"]["source_type"] == (
        "consensus_gap_deterministic_fallback"
    )
    assert payload["meta"]["data_mode"] == "source_backed"
    assert payload["meta"]["price_points_meta"]["frequency"] == "monthly"
    assert (
        payload["meta"]["price_points"][0]["source_trace"]["source_document_id"]
        == "price-doc-2023-01"
    )
    assert payload["meta"]["forecast"]["consensus"]["quality_status"] == (
        "partial_source_backed_consensus_snapshots"
    )


def test_kr_warehouse_valuation_map_uses_local_consensus_csv_projection(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("DATA_BACKEND", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    imports_dir = tmp_path / "storage" / "imports"
    imports_dir.mkdir(parents=True)
    (imports_dir / "consensus_005930.csv").write_text(
        "ticker,fiscal_year,snapshot_date,estimate_case,estimate_eps,growth_rate_pct,"
        "analyst_count,currency,source,source_url,metric_key,period_end,quality_status,"
        "source_document_id,filing_id,notes\n"
        "005930.KS,2026,2026-07-02,median,7358.69,11.44,0,KRW,"
        "manual_forecast_assumption,,adjusted_operating_eps,2026-12-31,"
        "manual_forecast_assumption,operator-doc-2026,operator-filing-2026,"
        "Manual forecast assumption from source-backed KR valuation cache.\n"
        "005930.KS,2027,2026-07-02,median,8200.87,11.44,0,KRW,"
        "manual_forecast_assumption,,adjusted_operating_eps,2027-12-31,"
        "manual_forecast_assumption,operator-doc-2027,operator-filing-2027,"
        "Manual forecast assumption from source-backed KR valuation cache.\n"
        "005930.KS,2028,2026-07-02,median,9139.44,11.44,0,KRW,"
        "manual_forecast_assumption,,adjusted_operating_eps,2028-12-31,"
        "manual_forecast_assumption,operator-doc-2028,operator-filing-2028,"
        "Manual forecast assumption from source-backed KR valuation cache.\n"
        "005930.KS,2029,2026-07-02,median,10185.42,11.44,0,KRW,"
        "manual_forecast_assumption,,adjusted_operating_eps,2029-12-31,"
        "manual_forecast_assumption,operator-doc-2029,operator-filing-2029,"
        "Manual forecast assumption from source-backed KR valuation cache.\n"
        "005930.KS,2030,2026-07-02,median,11351.11,11.44,0,KRW,"
        "manual_forecast_assumption,,adjusted_operating_eps,2030-12-31,"
        "manual_forecast_assumption,operator-doc-2030,operator-filing-2030,"
        "Manual forecast assumption from source-backed KR valuation cache.\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    historical_trace = {
        "source_document_id": "kr-warehouse-doc-2025",
        "source_type": "kr_valuation_warehouse",
        "filing_id": "KR_VALUATION_INPUT_005930.KS_2025",
        "period": "FY2025",
        "available_at": "2026-04-01T00:00:00+00:00",
        "unit": "KRW/share",
        "currency": "KRW",
        "method": "S3_MARKET_STANDARD_KR",
        "formula": "source-backed adjusted operating EPS",
        "quality_status": "source_backed",
    }
    monkeypatch.setattr("services.api.main.valuation_points_from_postgres", lambda ticker, metric: None)
    monkeypatch.setattr(
        "services.api.main.valuation_points_from_kr_warehouse",
        lambda ticker, metric: SimpleNamespace(
            points=[
                ValuationPoint(
                    fiscal_year=2024,
                    metric=Decimal("5925"),
                    price=Decimal("78000"),
                    dividend=Decimal("1444"),
                    source_trace={**historical_trace, "period": "FY2024"},
                ),
                ValuationPoint(
                    fiscal_year=2025,
                    metric=Decimal("6603"),
                    price=Decimal("81000"),
                    dividend=Decimal("1444"),
                    source_trace=historical_trace,
                ),
            ],
            metric_label="Adjusted Operating EPS",
            price_points=[],
            meta={
                "data_backend": "kr_valuation_warehouse",
                "data_mode": "source_backed",
                "valuation_ready": True,
                "financial_numbers_allowed": True,
            },
        ),
    )

    response = client.get(
        "/api/v1/companies/005930.KS/valuation-map"
        "?forecast_mode=estimates&forecast_case=median&forecast_years=5"
    )

    assert response.status_code == 200
    payload = response.json()
    forecast_rows = [row for row in payload["data"] if row["forecast_flag"]]
    assert [row["metric"] for row in forecast_rows] == [
        "7358.69",
        "8200.87",
        "9139.44",
        "10185.42",
        "11351.11",
    ]
    assert payload["meta"]["data_backend"] == "kr_valuation_warehouse"
    assert payload["meta"]["forecast"]["source"] == "consensus_snapshot"
    assert payload["meta"]["forecast"]["consensus"]["quality_status"] == (
        "source_backed_manual_forecast_assumption"
    )
    assert forecast_rows[0]["source_trace"]["source_document_id"] == "operator-filing-2026"
    assert forecast_rows[0]["source_trace"]["upstream_source_document_id"] == "operator-doc-2026"
    assert forecast_rows[0]["source_trace"]["filing_id"] == "operator-filing-2026"
    assert forecast_rows[0]["source_trace"]["period"] == "FY2026E"
    assert forecast_rows[0]["source_trace"]["assumption_type"] == "manual_assumption"
    assert forecast_rows[-1]["source_trace"]["source_document_id"] == "operator-filing-2030"
    assert forecast_rows[-1]["source_trace"]["period"] == "FY2030E"


def test_kr_warehouse_valuation_map_uses_aggregate_consensus_csv_projection(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("DATA_BACKEND", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    imports_dir = tmp_path / "storage" / "imports"
    imports_dir.mkdir(parents=True)
    (imports_dir / "consensus_estimates.csv").write_text(
        _manual_consensus_csv_text(
            ["000660.KS"],
            values_by_ticker={
                "000660.KS": ["12000", "12600", "13230", "13891.5", "14586.08"],
            },
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    _mock_kr_warehouse_valuation_points(monkeypatch, "000660.KS")

    response = client.get(
        "/api/v1/companies/000660.KS/valuation-map"
        "?forecast_mode=estimates&forecast_case=median&forecast_years=5"
    )

    assert response.status_code == 200
    payload = response.json()
    forecast_rows = [row for row in payload["data"] if row["forecast_flag"]]
    assert [row["metric"] for row in forecast_rows] == [
        "12000.00",
        "12600.00",
        "13230.00",
        "13891.50",
        "14586.08",
    ]
    assert payload["meta"]["data_backend"] == "kr_valuation_warehouse"
    assert payload["meta"]["forecast"]["source"] == "consensus_snapshot"
    assert payload["meta"]["forecast"]["consensus"]["quality_status"] == (
        "source_backed_manual_forecast_assumption"
    )
    assert forecast_rows[0]["source_trace"]["period"] == "FY2026E"
    assert forecast_rows[-1]["source_trace"]["period"] == "FY2030E"
    assert forecast_rows[0]["source_trace"]["source_file"].replace("\\", "/").endswith(
        "/consensus_estimates.csv"
    )


def test_forecast_snapshots_use_aggregate_csv_without_fixture_evidence(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("DATA_BACKEND", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    imports_dir = tmp_path / "storage" / "imports"
    imports_dir.mkdir(parents=True)
    (imports_dir / "consensus_estimates.csv").write_text(
        _manual_consensus_csv_text(["000660.KS"]),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    response = client.get("/api/v1/companies/000660.KS/forecast-snapshots")

    assert response.status_code == 200
    payload = response.json()["data"]
    serialized = json.dumps(payload)
    assert payload["meta"]["quality_status"] == (
        "source_backed_manual_forecast_assumption"
    )
    assert payload["source_trace"]["source_file"].replace("\\", "/").endswith(
        "/consensus_estimates.csv"
    )
    assert "forecast_snapshot_fixture" not in serialized
    assert "no_verified_consensus_snapshot" not in serialized


def test_data_audit_uses_aggregate_csv_forecast_traces_by_year(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("DATA_BACKEND", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    imports_dir = tmp_path / "storage" / "imports"
    imports_dir.mkdir(parents=True)
    (imports_dir / "consensus_estimates.csv").write_text(
        _manual_consensus_csv_text(
            ["000660.KS"],
            values_by_ticker={
                "000660.KS": ["12000", "12600", "13230", "13891.5", "14586.08"],
            },
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    _mock_kr_warehouse_valuation_points(monkeypatch, "000660.KS")

    response = client.get(
        "/api/v1/companies/000660.KS/data-audit"
        "?forecast_mode=estimates&forecast_case=median&forecast_years=5"
    )

    assert response.status_code == 200
    payload = response.json()
    forecast_metric_rows = [
        row for row in payload["data"] if row["fact_name"] == "forecast.metric"
    ]
    assert [row["fiscal_year"] for row in forecast_metric_rows] == [
        2026,
        2027,
        2028,
        2029,
        2030,
    ]
    assert [row["source_trace"]["period"] for row in forecast_metric_rows] == [
        "FY2026E",
        "FY2027E",
        "FY2028E",
        "FY2029E",
        "FY2030E",
    ]
    assert all(
        row["source_trace"]["source_file"].replace("\\", "/").endswith(
            "/consensus_estimates.csv"
        )
        for row in forecast_metric_rows
    )
    assert all(
        "manual-filing-000660.KS-" in row["source_trace"]["filing_id"]
        for row in forecast_metric_rows
    )
    serialized = json.dumps(payload)
    assert "forecast_snapshot_fixture" not in serialized
    assert "no_verified_consensus_snapshot" not in serialized


def test_source_backed_valuation_map_exposes_sec_metric_values_trace(monkeypatch):
    metric_trace_2023 = {
        "source_document_id": "sec-companyfacts-bulk",
        "source_type": "sec_companyfacts_bulk_derived",
        "filing_id": "CIK0000320193-companyfacts",
        "period": "FY2023",
        "available_at": "2024-11-01T12:00:00+00:00",
        "unit": "USD_per_share",
        "currency": "USD",
        "method": "SEC_COMPANYFACTS_BULK_DERIVED",
        "formula": "reported_eps_diluted from SEC companyfacts EPS fact",
        "quality_status": "source_backed_sec_companyfacts_derived",
        "input_fact_ids": ["aapl-2023-eps-diluted"],
        "price_source_trace": {
            "source_document_id": "price-doc-2023",
            "source_type": "stooq",
            "filing_id": "AAPL.US-stooq-daily",
            "period": "2023-12-31",
            "available_at": "2024-01-02T00:00:00+00:00",
            "unit": "per_share",
            "currency": "USD",
            "method": "STOOQ_DAILY_CLOSE",
            "formula": "source-backed year-end close price",
            "quality_status": "source_backed_price",
        },
        "dividend_source_traces": [
            {
                "source_document_id": "dividend-doc-2023",
                "source_type": "nasdaq_dividend_csv",
                "filing_id": "AAPL-nasdaq-dividends",
                "period": "FY2023",
                "available_at": "2024-01-31T00:00:00+00:00",
                "unit": "USD_per_share",
                "currency": "USD",
                "method": "NASDAQ_DIVIDEND_CSV",
                "formula": "sum cash dividends per share by fiscal year",
                "quality_status": "source_backed_dividend",
            }
        ],
    }
    metric_trace_2024 = {
        **metric_trace_2023,
        "period": "FY2024",
        "input_fact_ids": ["aapl-2024-eps-diluted"],
        "price_source_trace": {
            **metric_trace_2023["price_source_trace"],
            "source_document_id": "price-doc-2024",
            "period": "2024-12-31",
        },
        "dividend_source_traces": [
            {
                **metric_trace_2023["dividend_source_traces"][0],
                "source_document_id": "dividend-doc-2024",
                "period": "FY2024",
            }
        ],
    }

    monkeypatch.setattr(
        "services.api.main.valuation_points_from_postgres",
        lambda ticker, metric: (
            [
                ValuationPoint(
                    fiscal_year=2023,
                    metric=Decimal("5.95"),
                    price=Decimal("190.00"),
                    dividend=Decimal("0.96"),
                    source_trace=metric_trace_2023,
                ),
                ValuationPoint(
                    fiscal_year=2024,
                    metric=Decimal("6.08"),
                    price=Decimal("250.00"),
                    dividend=Decimal("1.00"),
                    source_trace=metric_trace_2024,
                ),
            ],
            "Diluted EPS",
            {"data_backend": "postgres", "currency": "USD", "country": "US"},
        )
        if ticker == "AAPL" and metric == "diluted_eps"
        else None,
    )
    monkeypatch.setattr(
        "services.api.main.price_points_from_postgres",
        lambda ticker, start_year, end_year: [
            {
                "date": "2023-12-31",
                "fiscal_year": 2023,
                "close_price": "190.00",
                "currency": "USD",
                "frequency": "monthly",
                "source_trace": metric_trace_2023["price_source_trace"],
            },
            {
                "date": "2024-12-31",
                "fiscal_year": 2024,
                "close_price": "250.00",
                "currency": "USD",
                "frequency": "monthly",
                "source_trace": metric_trace_2024["price_source_trace"],
            },
        ],
    )
    monkeypatch.setattr(
        "services.api.main.recession_periods_from_postgres",
        lambda start_year, end_year: [],
    )

    response = client.get(
        "/api/v1/companies/AAPL/valuation-map"
        "?metric=diluted_eps&forecast_mode=custom&forecast_years=1"
    )

    assert response.status_code == 200
    payload = response.json()
    historical_rows = [row for row in payload["data"] if not row["forecast_flag"]]
    latest_trace = historical_rows[-1]["source_trace"]
    assert payload["meta"]["data_mode"] == "source_backed"
    assert payload["meta"]["data_backend"] == "postgres"
    assert payload["meta"]["metric"] == "diluted_eps"
    assert payload["meta"]["metric_label"] == "Diluted EPS"
    assert latest_trace["source_type"] == "sec_companyfacts_bulk_derived"
    assert latest_trace["method"] == "SEC_COMPANYFACTS_BULK_DERIVED"
    assert latest_trace["input_fact_ids"] == ["aapl-2024-eps-diluted"]
    assert latest_trace["formula"] == "reported_eps_diluted from SEC companyfacts EPS fact"
    assert latest_trace["price_source_trace"]["source_type"] == "stooq"
    assert latest_trace["dividend_source_traces"][0]["source_type"] == (
        "nasdaq_dividend_csv"
    )
    assert payload["meta"]["price_points_meta"]["data_mode"] == "source_backed"
    assert payload["meta"]["price_points"][1]["source_trace"]["source_type"] == "stooq"


def test_source_backed_custom_forecast_does_not_use_consensus_projection(monkeypatch):
    trace = {
        "source_document_id": "adjusted-doc",
        "source_type": "postgres",
        "filing_id": "adjusted-filing",
        "period": "FY2024",
        "available_at": "2025-01-31T00:00:00+00:00",
        "unit": "per_share",
        "currency": "USD",
        "formula": "source-backed adjusted EPS",
        "quality_status": "source_backed",
    }
    monkeypatch.setattr(
        "services.api.main.valuation_points_from_postgres",
        lambda ticker, metric: (
            [
                ValuationPoint(
                    fiscal_year=2023,
                    metric=Decimal("10.00"),
                    price=Decimal("180.00"),
                    dividend=Decimal("1.00"),
                    source_trace=trace,
                ),
                ValuationPoint(
                    fiscal_year=2024,
                    metric=Decimal("12.00"),
                    price=Decimal("240.00"),
                    dividend=Decimal("1.10"),
                    source_trace=trace,
                ),
            ],
            "Adjusted Operating EPS",
            {"data_backend": "postgres", "currency": "USD", "country": "US"},
        ),
    )

    def fail_consensus_projection(*args, **kwargs):
        raise AssertionError("custom forecast should not read consensus projection")

    monkeypatch.setattr(
        "services.api.main.consensus_projection_from_postgres",
        fail_consensus_projection,
    )

    response = client.get(
        "/api/v1/companies/POSTGRESONLY/valuation-map"
        "?forecast_mode=custom&forecast_years=2&manual_eps_values=15.00,16.00"
    )

    assert response.status_code == 200
    forecast_rows = [row for row in response.json()["data"] if row["forecast_flag"]]
    assert forecast_rows[0]["forecast_source"] == "user_input"
    assert forecast_rows[0]["source_trace"]["source_document_id"].endswith(
        "2025-forecast-assumption"
    )
    assert forecast_rows[0]["source_trace"]["period"] == "FY2025E"
    assert forecast_rows[1]["source_trace"]["source_document_id"].endswith(
        "2026-forecast-assumption"
    )
    assert forecast_rows[1]["source_trace"]["period"] == "FY2026E"
    assert forecast_rows[1]["source_trace"]["forecast_assumption_period"] == (
        "FY2025E-FY2026E"
    )


def test_forecast_snapshots_expose_revisions_sentiment_and_scorecard():
    response = client.get("/api/v1/companies/AAPL/forecast-snapshots")
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["forecast_year"] == 2025
    assert {row["case"] for row in payload["cases"]} == {"low", "median", "high"}
    assert [row["as_of_label"] for row in payload["revisions"]] == [
        "12M prior",
        "3M prior",
        "1M prior",
        "current",
    ]
    assert payload["sentiment"]["label"] in {"positive", "neutral", "negative"}
    assert payload["scorecard"]["summary"]["required_source"] == (
        "point_in_time_consensus_snapshots"
    )
    assert payload["scorecard"]["rows"]
    assert payload["source_trace"]["quality_status"] == ("fixture_non_production_consensus_proxy")


def test_forecast_snapshots_use_local_consensus_csv_overlay(monkeypatch, tmp_path):
    monkeypatch.delenv("DATA_BACKEND", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    imports_dir = tmp_path / "storage" / "imports"
    imports_dir.mkdir(parents=True)
    (imports_dir / "consensus_005930.csv").write_text(
        "ticker,fiscal_year,snapshot_date,estimate_case,estimate_eps,growth_rate_pct,"
        "analyst_count,currency,source,source_url,metric_key,period_end,quality_status,"
        "source_document_id,filing_id,notes\n"
        "005930.KS,2026,2026-07-02,median,7358.69,11.44,0,KRW,"
        "manual_forecast_assumption,,adjusted_operating_eps,2026-12-31,"
        "manual_forecast_assumption,operator-doc-2026,operator-filing-2026,"
        "Manual forecast assumption from source-backed KR valuation cache.\n"
        "005930.KS,2027,2026-07-02,median,8200.87,11.44,0,KRW,"
        "manual_forecast_assumption,,adjusted_operating_eps,2027-12-31,"
        "manual_forecast_assumption,operator-doc-2027,operator-filing-2027,"
        "Manual forecast assumption from source-backed KR valuation cache.\n"
        "005930.KS,2028,2026-07-02,median,9139.44,11.44,0,KRW,"
        "manual_forecast_assumption,,adjusted_operating_eps,2028-12-31,"
        "manual_forecast_assumption,operator-doc-2028,operator-filing-2028,"
        "Manual forecast assumption from source-backed KR valuation cache.\n"
        "005930.KS,2029,2026-07-02,median,10185.42,11.44,0,KRW,"
        "manual_forecast_assumption,,adjusted_operating_eps,2029-12-31,"
        "manual_forecast_assumption,operator-doc-2029,operator-filing-2029,"
        "Manual forecast assumption from source-backed KR valuation cache.\n"
        "005930.KS,2030,2026-07-02,median,11351.11,11.44,0,KRW,"
        "manual_forecast_assumption,,adjusted_operating_eps,2030-12-31,"
        "manual_forecast_assumption,operator-doc-2030,operator-filing-2030,"
        "Manual forecast assumption from source-backed KR valuation cache.\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    response = client.get("/api/v1/companies/005930.KS/forecast-snapshots")

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["forecast_year"] == 2026
    assert payload["meta"]["data_mode"] == "source_backed"
    assert payload["meta"]["quality_status"] == (
        "source_backed_manual_forecast_assumption"
    )
    assert payload["source_trace"]["period"] == "FY2026E-FY2030E"
    assert payload["cases"] == [
        {
            "case": "median",
            "growth_rate_pct": "11.44",
            "estimate_eps": "7358.69",
            "source_trace": payload["cases"][0]["source_trace"],
        }
    ]
    assert payload["cases"][0]["source_trace"]["period"] == "FY2026E"
    assert payload["cases"][0]["source_trace"]["filing_id"] == "operator-filing-2026"
    assert payload["revisions"][0]["as_of_label"] == "current"
    assert payload["scorecard"]["status"] == "not_applicable_manual_forecast_assumption"
    assert "fixture" not in str(payload).lower()


def test_analyst_scorecard_fixture_contract():
    response = client.get("/api/v1/companies/AAPL/analyst-scorecard")
    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["data_mode"] == "fixture_non_production"
    assert payload["meta"]["scope"] == [
        "actual_eps",
        "estimate_1y_prior",
        "estimate_2y_prior",
        "error_1y_pct",
        "error_2y_pct",
        "result_1y",
        "result_2y",
        "hit_rate_1y_pct",
        "hit_rate_2y_pct",
    ]
    data = payload["data"]
    assert data["rows"]
    assert data["summary"]["hit_rate_1y_pct"]
    assert data["summary"]["hit_rate_2y_pct"]
    assert data["quality_status"] == "fixture_non_production_scorecard_proxy"
    for key in SOURCE_TRACE_AUDIT_KEYS:
        assert data["rows"][0]["source_trace"][key]


def test_source_backed_analyst_scorecard_bypasses_fixture_guard(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.delenv("ALLOW_FIXTURE_FALLBACK", raising=False)
    monkeypatch.delenv("DISABLE_FIXTURE_FALLBACK", raising=False)
    trace = {
        "source_document_id": "consensus-doc",
        "source_type": "postgres",
        "filing_id": "consensus-filing",
        "period": "FY2024",
        "unit": "per_share",
        "currency": "USD",
        "formula": "point-in-time consensus snapshot",
        "quality_status": "source_backed",
    }
    monkeypatch.setattr(
        "services.api.main.forecast_evidence_from_postgres",
        lambda ticker: {
            "scorecard": {
                "status": "source_backed_consensus_snapshots",
                "rows": [
                    {
                        "fiscal_year": 2024,
                        "actual_eps": "10.00",
                        "estimate_1y_prior": "10.50",
                        "estimate_2y_prior": "14.00",
                        "error_1y_pct": "5.00",
                        "error_2y_pct": "40.00",
                        "result_1y": "hit",
                        "result_2y": "miss",
                        "quality_status": "source_backed",
                        "source_trace": trace,
                    }
                ],
                "summary": {
                    "hit_rate_1y_pct": "100.00",
                    "hit_rate_2y_pct": "0.00",
                    "required_source": "point_in_time_consensus_snapshots",
                },
            },
            "source_trace": trace,
        },
    )

    response = client.get("/api/v1/companies/POSTGRESONLY/analyst-scorecard")
    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["data_mode"] == "source_backed"
    assert payload["data"]["summary"]["hit_rate_2y_pct"] == "0.00"


def test_analyst_scorecard_production_blocks_fixture_fallback(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.delenv("ALLOW_FIXTURE_FALLBACK", raising=False)
    monkeypatch.delenv("DISABLE_FIXTURE_FALLBACK", raising=False)
    monkeypatch.setattr("services.api.main.forecast_evidence_from_postgres", lambda ticker: None)

    response = client.get("/api/v1/companies/AAPL/analyst-scorecard")
    assert response.status_code == 503
    assert response.json()["detail"]["surface"] == "analyst_scorecard"


def test_seed_universe_four_markets_render_valuation_maps():
    for ticker in ["AAPL", "NVDA", "005930.KS", "7203.T"]:
        response = client.get(f"/api/company/{ticker}/valuation-map")
        assert response.status_code == 200
        payload = response.json()
        assert payload["data"]
        assert all("source_trace" in row for row in payload["data"])
        assert all(row["source_trace"] for row in payload["data"] if not row["forecast_flag"])
        assert all(
            row["source_trace"].get("period") for row in payload["data"] if not row["forecast_flag"]
        )


def test_compat_search_and_fact_audit_routes():
    search = client.get("/api/securities/search?q=samsung")
    assert search.status_code == 200
    assert search.json()["data"][0]["ticker"] == "005930.KS"

    audit = client.get("/api/v1/companies/AAPL/data-audit")
    assert audit.status_code == 200
    payload = audit.json()
    assert payload["meta"]["scope"] == [
        "adjusted_earnings",
        "valuation_map",
        "forecast",
        "forecast_assumption",
        "chart_key",
        "price_points",
        "forecast_snapshot",
        "forecast_case",
        "forecast_scenario",
        "analyst_scorecard",
        "snapshot",
        "financials",
        "fun_graphs",
        "fiscal_fitness",
        "health_check",
        "research_report",
        "performance",
        "use_of_cash",
        "screener",
        "portfolio",
        "portfolio_transaction",
        "watchlist",
    ]
    fact_names = {row["fact_name"] for row in payload["data"]}
    assert "adjusted_earnings.adjusted_eps" in fact_names
    assert "valuation.fair_value_price" in fact_names
    assert "valuation.yoy" in fact_names
    assert "forecast.metric" in fact_names
    assert "forecast.price_cagr_pct" in fact_names
    assert "forecast.total_return_cagr_pct" in fact_names
    assert "forecast_assumption.formula" in fact_names
    assert "chart_key.current_multiple" in fact_names
    assert "chart_key.payout_ratio_pct" in fact_names
    assert "chart_key.dividend_yield_pct" in fact_names
    assert "chart_key.custom_multiple" in fact_names
    assert "price_point.close_price.2024-12-31" in fact_names
    assert any(name.startswith("forecast_scenario.") for name in fact_names)
    assert "forecast_snapshot.median.estimate_eps" in fact_names
    assert "analyst_sentiment.net_revision_score_pct" in fact_names
    assert "analyst_scorecard.hit_rate_1y_pct" in fact_names
    assert "analyst_scorecard.actual_eps" in fact_names
    assert "screener.per" in fact_names
    assert "portfolio.market_value" in fact_names
    assert "portfolio_transaction.2023-01-10.buy.1.price" in fact_names
    assert "watchlist.current_price" in fact_names
    assert "snapshot.current_price" in fact_names
    assert "snapshot.market_cap" in fact_names
    assert "snapshot.listed_shares" in fact_names
    assert "financials.revenue" in fact_names
    assert "fun_graphs.revenue" in fact_names
    assert "fiscal_fitness.roe_pct" in fact_names
    assert "health_check.overall_score" in fact_names
    assert "research_report.valuation_gap_pct" in fact_names
    assert any(name.startswith("performance.total_return_pct") for name in fact_names)
    assert "use_of_cash.free_cash_flow" in fact_names

    for row in payload["data"]:
        assert row["fact_name"]
        assert row["source_trace"]
        for key in SOURCE_TRACE_AUDIT_KEYS:
            assert row["source_trace"][key], (row["fact_id"], key)

    fact_id = next(
        row["fact_id"] for row in payload["data"] if row["fact_name"] == "forecast.metric"
    )
    fact = client.get(f"/api/data-audit/{fact_id}")
    assert fact.status_code == 200
    assert fact.json()["data"]["fact_id"] == fact_id

    price_point_fact_id = next(
        row["fact_id"]
        for row in payload["data"]
        if row["fact_name"] == "price_point.close_price.2024-12-31"
    )
    price_point_fact = client.get(f"/api/data-audit/{price_point_fact_id}")
    assert price_point_fact.status_code == 200
    assert price_point_fact.json()["data"]["source_trace"]["period"] == "2024-12-31"



def test_performance_fixture_contract():
    response = client.get("/api/v1/companies/AAPL/performance")
    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["data_mode"] == "fixture_non_production"
    assert payload["data"]["rows"]
    row = payload["data"]["rows"][0]
    assert row["initial_investment"] == "10000"
    assert row["ending_value"]
    assert row["dividends_received"]
    assert row["total_return_pct"]
    assert row["annualized_total_return_pct"]
    for key in SOURCE_TRACE_AUDIT_KEYS:
        assert row["source_trace"][key]


def test_chart_svg_renders(monkeypatch, tmp_path):
    monkeypatch.setenv("CHART_CACHE_DIR", str(tmp_path / "charts"))

    response = client.get("/api/v1/charts/valuation-map/AAPL.svg")
    assert response.status_code == 200
    assert "svg" in response.text.lower()
    assert "dated points" in response.text
    assert "Source trace:" in response.text
    assert "methods=fixture_non_production" in response.text
    assert "docs=" in response.text
    assert "latest_available=" in response.text
    assert response.headers["x-chart-cache"] == "miss"
    assert response.headers["x-chart-cache-key"]
    assert response.headers["x-chart-blob-key"].endswith(".svg")

    cached = client.get("/api/v1/charts/valuation-map/AAPL.svg")
    assert cached.status_code == 200
    assert cached.headers["x-chart-cache"] == "hit"
    assert cached.headers["x-chart-cache-key"] == response.headers["x-chart-cache-key"]

    hidden_price = client.get("/api/v1/charts/valuation-map/AAPL.svg?show_price=false")
    assert hidden_price.status_code == 200
    assert hidden_price.headers["x-chart-cache-key"] != response.headers["x-chart-cache-key"]

    with_scenarios = client.get("/api/v1/charts/valuation-map/AAPL.svg?forecast_years=3")
    assert with_scenarios.status_code == 200
    assert "Scenario lines" in with_scenarios.text
    assert "Current valuation" in with_scenarios.text
    assert "Payout ratio" in with_scenarios.text
    assert "Recession bands" in with_scenarios.text

    hidden_current = client.get(
        "/api/v1/charts/valuation-map/AAPL.svg?forecast_years=3&show_current_valuation=false"
    )
    assert hidden_current.status_code == 200
    assert "Current valuation" not in hidden_current.text
    assert hidden_current.headers["x-chart-cache-key"] != with_scenarios.headers[
        "x-chart-cache-key"
    ]

    custom_valuation = client.get(
        "/api/v1/charts/valuation-map/AAPL.svg"
        "?forecast_years=3&show_custom_valuation=true&custom_valuation_multiple=22"
    )
    assert custom_valuation.status_code == 200
    assert "Custom valuation" in custom_valuation.text

    dividend_yield = client.get(
        "/api/v1/charts/valuation-map/AAPL.svg?forecast_years=3&show_dividend_yield=true"
    )
    assert dividend_yield.status_code == 200
    assert "Dividend yield" in dividend_yield.text

    hidden_recession = client.get(
        "/api/v1/charts/valuation-map/AAPL.svg?forecast_years=3&show_recession_bands=false"
    )
    assert hidden_recession.status_code == 200
    assert "Recession bands" not in hidden_recession.text

    hidden_scenarios = client.get(
        "/api/v1/charts/valuation-map/AAPL.svg?forecast_years=3&show_scenario_lines=false"
    )
    assert hidden_scenarios.status_code == 200
    assert "Scenario lines" not in hidden_scenarios.text
    assert hidden_scenarios.headers["x-chart-cache-key"] != (
        with_scenarios.headers["x-chart-cache-key"]
    )

    hidden_line_labels = ",".join([f"{multiple}x" for multiple in range(13, 24)])
    hidden_individual_scenarios = client.get(
        "/api/v1/charts/valuation-map/AAPL.svg"
        f"?forecast_years=3&target_multiple=18&hidden_scenario_lines={hidden_line_labels}"
    )
    assert hidden_individual_scenarios.status_code == 200
    assert "Scenario lines" not in hidden_individual_scenarios.text


def test_chart_png_renders(monkeypatch, tmp_path):
    monkeypatch.setenv("CHART_CACHE_DIR", str(tmp_path / "charts"))

    response = client.get(
        "/api/v1/charts/valuation-map/AAPL.png?metric=adjusted_operating&forecast_years=3"
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert response.headers["x-chart-cache-key"]
    assert response.headers["x-chart-blob-key"].endswith(".png")


def test_chart_run_creates_replayable_svg_and_png(monkeypatch, tmp_path):
    monkeypatch.setenv("CHART_CACHE_DIR", str(tmp_path / "charts"))
    monkeypatch.setenv("CHART_RUN_DIR", str(tmp_path / "chart-runs"))

    created = client.post(
        "/api/v1/charts/valuation-map/runs",
        json={
            "company_id": "AAPL",
            "metric": "adjusted_operating",
            "forecast_mode": "custom",
            "forecast_years": 3,
            "start_year": 2021,
            "end_year": 2024,
            "normal_multiple_years": 3,
            "show_price": False,
            "show_current_valuation": False,
            "show_custom_valuation": True,
            "custom_valuation_multiple": "22",
            "show_payout_ratio": False,
            "show_dividend_yield": True,
            "show_recession_bands": False,
            "hidden_scenario_lines": ["18x"],
        },
    )
    assert created.status_code == 200
    run = created.json()["data"]
    assert run["chart_run_id"]
    assert run["svg_url"].endswith(".svg")
    assert run["png_url"].endswith(".png")
    assert run["evidence_summary"]["metric"] == "adjusted_operating"
    assert run["evidence_summary"]["actual_periods"] == 4
    assert run["evidence_summary"]["forecast_periods"] == 3
    assert run["evidence_summary"]["source_document_count"] > 0
    assert "fixture_non_production" in run["evidence_summary"]["methods"]

    manifest = client.get(f"/api/v1/charts/valuation-map/runs/{run['chart_run_id']}")
    assert manifest.status_code == 200
    manifest_payload = manifest.json()["data"]
    assert manifest_payload["evidence_summary"] == run["evidence_summary"]
    assert manifest_payload["payload"]["meta"]["line_visibility"]["price"] is False
    assert manifest_payload["payload"]["meta"]["line_visibility"]["current_valuation"] is False
    assert manifest_payload["payload"]["meta"]["line_visibility"]["custom_valuation"] is True
    line_visibility = manifest_payload["payload"]["meta"]["line_visibility"]
    assert line_visibility["custom_valuation_multiple"] == "22"
    assert manifest_payload["payload"]["meta"]["line_visibility"]["payout_ratio"] is False
    assert manifest_payload["payload"]["meta"]["line_visibility"]["dividend_yield"] is True
    assert manifest_payload["payload"]["meta"]["line_visibility"]["recession_bands"] is False
    assert manifest_payload["payload"]["meta"]["range"]["start_year"] == 2021
    assert manifest_payload["payload"]["meta"]["range"]["end_year"] == 2024
    assert manifest_payload["payload"]["meta"]["normal_multiple"]["window_years"] == 3
    assert manifest_payload["request_params"]["show_current_valuation"] is False
    assert manifest_payload["request_params"]["start_year"] == 2021
    assert manifest_payload["request_params"]["end_year"] == 2024
    assert manifest_payload["request_params"]["normal_multiple_years"] == 3
    assert manifest_payload["request_params"]["show_custom_valuation"] is True
    assert manifest_payload["request_params"]["custom_valuation_multiple"] == "22"
    assert manifest_payload["request_params"]["show_payout_ratio"] is False
    assert manifest_payload["request_params"]["show_dividend_yield"] is True
    assert manifest_payload["request_params"]["show_recession_bands"] is False
    assert manifest_payload["request_params"]["hidden_scenario_lines"] == ["18x"]
    assert manifest_payload["payload"]["meta"]["line_visibility"]["hidden_scenario_lines"] == [
        "18x"
    ]
    assert manifest_payload["svg_cache_key"] == run["svg_cache_key"]

    svg = client.get(run["svg_url"])
    assert svg.status_code == 200
    assert "svg" in svg.text.lower()
    assert "Source trace:" in svg.text
    assert "Quality:" in svg.text
    assert svg.headers["x-chart-cache-key"] == run["svg_cache_key"]

    png = client.get(run["png_url"])
    assert png.status_code == 200
    assert png.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert png.headers["x-chart-cache-key"] == run["png_cache_key"]


def test_missing_chart_run_returns_404(monkeypatch, tmp_path):
    monkeypatch.setenv("CHART_RUN_DIR", str(tmp_path / "chart-runs"))

    response = client.get("/api/v1/charts/valuation-map/runs/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404


def test_chart_layouts_save_list_and_delete(monkeypatch, tmp_path):
    monkeypatch.setenv("CHART_LAYOUT_DIR", str(tmp_path / "chart-layouts"))

    saved = client.post(
        "/api/v1/chart-layouts",
        json={
            "name": "AAPL upside case",
            "company_id": "AAPL",
            "metric": "adjusted_operating",
            "forecast_mode": "custom",
            "forecast_case": "high",
            "forecast_years": 5,
            "start_year": 2021,
            "end_year": 2024,
            "normal_multiple_years": 7,
            "user_growth_rate": "12",
            "target_multiple": "21",
            "manual_eps_values": "7.50,8.10",
            "visibility": {
                "price": True,
                "metric_area": True,
                "fair_value": True,
                "normal_multiple": False,
                "current_valuation": False,
                "custom_valuation": True,
                "dividend_floor": True,
                "payout_ratio": False,
                "dividend_yield": True,
                "recession_bands": False,
                "forecast": True,
                "scenario_lines": True,
            },
            "hidden_scenario_lines": ["18x", "19x"],
        },
    )
    assert saved.status_code == 200
    layout = saved.json()["data"]
    assert layout["name"] == "AAPL upside case"
    assert layout["config"]["forecast_case"] == "high"
    assert layout["config"]["visibility"]["normal_multiple"] is False
    assert layout["config"]["visibility"]["current_valuation"] is False
    assert layout["config"]["visibility"]["custom_valuation"] is True
    assert layout["config"]["visibility"]["payout_ratio"] is False
    assert layout["config"]["visibility"]["dividend_yield"] is True
    assert layout["config"]["visibility"]["recession_bands"] is False
    assert layout["config"]["start_year"] == 2021
    assert layout["config"]["end_year"] == 2024
    assert layout["config"]["normal_multiple_years"] == 7
    assert layout["config"]["hidden_scenario_lines"] == ["18x", "19x"]
    assert layout["source_trace"]["quality_status"] == "user_provided"

    listed = client.get("/api/v1/chart-layouts")
    assert listed.status_code == 200
    items = listed.json()["data"]["items"]
    assert [item["name"] for item in items] == ["AAPL upside case"]

    deleted = client.delete(f"/api/v1/chart-layouts/{layout['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["data"]["items"] == []


def test_snapshot_exposes_company_terminal_metrics():
    response = client.get("/api/v1/companies/AAPL/snapshot")
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["per"]
    assert payload["dividend_yield"]
    assert payload["eps_cagr"]
    assert payload["roe"]
    assert payload["debt_ratio"]
    assert payload["source_trace"]


def test_source_backed_snapshot_bypasses_fixture_guard(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.delenv("ALLOW_FIXTURE_FALLBACK", raising=False)
    monkeypatch.delenv("DISABLE_FIXTURE_FALLBACK", raising=False)

    def source_snapshot(ticker: str):
        if ticker != "POSTGRESONLY":
            return None
        return {
            "ticker": ticker,
            "name": "Postgres Only Inc.",
            "market": "NASDAQ",
            "country": "US",
            "currency": "USD",
            "current_price": "100.00",
            "market_cap": "1000000000",
            "listed_shares": "10000000",
            "per": "20.00",
            "dividend_yield": None,
            "eps": "5.00",
            "eps_cagr": None,
            "roe": None,
            "roic": None,
            "debt_ratio": None,
            "eps_method": "S1_SEC_RECONCILIATION",
            "confidence": "0.95",
            "source_note": "source_backed",
            "source_trace": {"source_type": "postgres", "quality_status": "passed"},
        }

    monkeypatch.setattr("services.api.main.company_snapshot_from_postgres", source_snapshot)

    response = client.get("/api/v1/companies/POSTGRESONLY/snapshot")
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["ticker"] == "POSTGRESONLY"
    assert payload["market_cap"] == "1000000000"
    assert payload["listed_shares"] == "10000000"
    assert payload["source_note"] == "source_backed"


def test_financials_exposes_required_trends():
    response = client.get("/api/v1/companies/NVDA/financials")
    assert response.status_code == 200
    row = response.json()["data"][-1]
    for key in [
        "revenue",
        "eps",
        "fcf",
        "gross_margin",
        "operating_margin",
        "roe",
        "roic",
        "debt_to_equity",
    ]:
        assert key in row
    assert row["source_trace"]


def test_source_backed_financials_bypasses_fixture_guard(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.delenv("ALLOW_FIXTURE_FALLBACK", raising=False)
    monkeypatch.delenv("DISABLE_FIXTURE_FALLBACK", raising=False)

    def source_financials(ticker: str):
        if ticker != "POSTGRESONLY":
            return None
        return [
            {
                "fiscal_year": 2024,
                "revenue": "1000.00",
                "eps": "5.00",
                "fcf": None,
                "gross_margin": None,
                "operating_margin": None,
                "net_margin": None,
                "roe": None,
                "roic": None,
                "debt_to_equity": None,
                "method": "S1_SEC_RECONCILIATION",
                "confidence": "0.95",
                "source_trace": {"source_type": "postgres", "quality_status": "passed"},
            }
        ]

    monkeypatch.setattr("services.api.main.financials_from_postgres", source_financials)

    response = client.get("/api/v1/companies/POSTGRESONLY/financials")
    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["data_mode"] == "source_backed"
    assert payload["data"][0]["source_trace"]["source_type"] == "postgres"


def test_fun_graphs_fixture_contract():
    response = client.get("/api/v1/companies/AAPL/fun-graphs")
    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["data_mode"] == "fixture_non_production"
    assert payload["meta"]["scope"] == [
        "revenue",
        "adjusted_eps",
        "gaap_eps_diluted",
        "free_cash_flow",
        "gross_margin_pct",
        "operating_margin_pct",
        "net_margin_pct",
        "roe_pct",
        "roic_pct",
        "debt_to_equity",
    ]
    data = payload["data"]
    assert data["metrics"]
    revenue = next(metric for metric in data["metrics"] if metric["metric_key"] == "revenue")
    assert revenue["points"][-1]["value"]
    for key in SOURCE_TRACE_AUDIT_KEYS:
        assert revenue["points"][-1]["source_trace"][key]


def test_source_backed_fun_graphs_bypasses_fixture_guard(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.delenv("ALLOW_FIXTURE_FALLBACK", raising=False)
    monkeypatch.delenv("DISABLE_FIXTURE_FALLBACK", raising=False)

    trace = {
        "source_document_id": "doc",
        "source_type": "postgres",
        "filing_id": "filing",
        "period": "FY2024",
        "available_at": "2025-01-31T00:00:00+00:00",
        "unit": "reported",
        "currency": "USD",
        "formula": "source backed financial row",
        "quality_status": "passed",
    }

    def source_financials(ticker: str):
        if ticker != "POSTGRESONLY":
            return None
        return [
            {
                "fiscal_year": 2024,
                "revenue": "1000.00",
                "eps": "5.00",
                "gaap_eps_diluted": "4.95",
                "fcf": "250.00",
                "gross_margin": "60.00",
                "operating_margin": "30.00",
                "net_margin": "20.00",
                "roe": "25.00",
                "roic": "18.00",
                "debt_to_equity": "0.30",
                "method": "S1_SEC_RECONCILIATION",
                "confidence": "0.95",
                "source_trace": trace,
                "metric_traces": {"revenue": trace | {"source_document_id": "revenue-doc"}},
            }
        ]

    monkeypatch.setattr("services.api.main.financials_from_postgres", source_financials)

    response = client.get("/api/v1/companies/POSTGRESONLY/fun-graphs")
    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["data_mode"] == "source_backed"
    revenue = next(
        metric for metric in payload["data"]["metrics"] if metric["metric_key"] == "revenue"
    )
    assert revenue["points"][0]["source_trace"]["source_document_id"] == "revenue-doc"


def test_fun_graphs_production_blocks_fixture_fallback(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.delenv("ALLOW_FIXTURE_FALLBACK", raising=False)
    monkeypatch.delenv("DISABLE_FIXTURE_FALLBACK", raising=False)
    monkeypatch.setattr("services.api.main.financials_from_postgres", lambda ticker: None)

    response = client.get("/api/v1/companies/AAPL/fun-graphs")
    assert response.status_code == 503
    assert response.json()["detail"]["surface"] == "fun_graphs"


def test_screener_returns_filter_classes():
    response = client.get("/api/v1/screener")
    assert response.status_code == 200
    payload = response.json()
    rows = payload["data"]
    assert rows
    assert {"metric_to_value", "metric_to_metric", "company_relative", "passes_all"} <= set(
        rows[0]["filters"]
    )
    assert payload["meta"]["config"]["max_per"] == "25"
    assert payload["meta"]["total"] == len(rows)
    apple = next(row for row in rows if row["ticker"] == "AAPL")
    assert apple["source_trace"]["unit"] == "screening_metrics"
    assert "deterministic filter thresholds" in apple["source_trace"]["formula"]


def test_screener_custom_filters_recompute_results():
    response = client.get(
        "/api/v1/screener?max_per=10&min_roe=20&min_eps_cagr=10"
        "&max_debt_to_equity=1&min_market_cap=1000000000"
        "&min_market_cap_usd=1000000000&relative_discount_pct=10&require_roe_gt_roic=true"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["config"]["max_per"] == "10"
    assert payload["meta"]["config"]["min_market_cap"] == "1000000000"
    assert payload["meta"]["config"]["min_market_cap_usd"] == "1000000000"
    assert payload["meta"]["config"]["relative_discount_pct"] == "10"
    apple = next(row for row in payload["data"] if row["ticker"] == "AAPL")
    assert apple["filters"]["metric_to_value"] is False
    assert apple["filters"]["passes_all"] is False
    assert any("P/E" in reason for reason in apple["filter_reasons"])


def test_source_backed_screener_bypasses_fixture_guard(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.delenv("ALLOW_FIXTURE_FALLBACK", raising=False)
    monkeypatch.delenv("DISABLE_FIXTURE_FALLBACK", raising=False)
    monkeypatch.setattr(
        "services.api.main.screener_rows_from_postgres",
        lambda: [
            {
                "ticker": "POSTGRESONLY",
                "name": "Postgres Only Inc.",
                "market": "NASDAQ",
                "currency": "USD",
                "market_cap": "1000000000",
                "market_cap_usd": "1000000000",
                "listed_shares": "10000000",
                "per": "20.00",
                "normal_pe": "25.00",
                "roe": None,
                "roic": None,
                "eps_cagr": None,
                "debt_to_equity": None,
                "filters": {
                    "metric_to_value": True,
                    "metric_to_metric": False,
                    "company_relative": True,
                },
                "source_trace": {"source_type": "postgres"},
            }
        ],
    )

    response = client.get("/api/v1/screener")
    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["data_mode"] == "source_backed"
    assert payload["data"][0]["ticker"] == "POSTGRESONLY"
    assert payload["data"][0]["market_cap"] == "1000000000"
    assert payload["data"][0]["market_cap_usd"] == "1000000000"
    assert payload["data"][0]["listed_shares"] == "10000000"
    assert payload["data"][0]["filters"]["passes_all"] is False
    assert payload["data"][0]["source_trace"]["quality_status"] == "source_backed_screener"


def test_source_backed_screener_market_cap_filter(monkeypatch):
    monkeypatch.setattr(
        "services.api.main.screener_rows_from_postgres",
        lambda: [
            {
                "ticker": "BIG",
                "name": "Big Cap Inc.",
                "market": "NASDAQ",
                "currency": "USD",
                "market_cap": "5000000000",
                "market_cap_usd": "5000000000",
                "listed_shares": "100000000",
                "per": "12.00",
                "normal_pe": "18.00",
                "roe": "20.00",
                "roic": "12.00",
                "eps_cagr": "15.00",
                "debt_to_equity": "0.50",
                "source_trace": {
                    "source_type": "postgres",
                    "market_cap_source_trace": {
                        "source_type": "market_data",
                        "quality_status": "source_backed_market_data",
                    },
                    "market_cap_usd_source_trace": {
                        "source_type": "market_data",
                        "quality_status": "source_backed_market_data",
                    },
                },
            },
            {
                "ticker": "SMALL",
                "name": "Small Cap Inc.",
                "market": "NASDAQ",
                "currency": "USD",
                "market_cap": "50000000",
                "market_cap_usd": "50000000",
                "listed_shares": "10000000",
                "per": "12.00",
                "normal_pe": "18.00",
                "roe": "20.00",
                "roic": "12.00",
                "eps_cagr": "15.00",
                "debt_to_equity": "0.50",
                "source_trace": {"source_type": "postgres"},
            },
        ],
    )

    response = client.get("/api/v1/screener?min_market_cap_usd=1000000000")
    assert response.status_code == 200
    rows = {row["ticker"]: row for row in response.json()["data"]}
    assert rows["BIG"]["filters"]["metric_to_value"] is True
    assert rows["SMALL"]["filters"]["metric_to_value"] is False
    assert any("Market cap USD" in reason for reason in rows["SMALL"]["filter_reasons"])


def test_source_backed_data_audit_bypasses_fixture_guard(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.delenv("ALLOW_FIXTURE_FALLBACK", raising=False)
    monkeypatch.delenv("DISABLE_FIXTURE_FALLBACK", raising=False)
    monkeypatch.setattr(
        "services.api.main._source_backed_data_audit",
        lambda ticker, *args, **kwargs: (
            {
                "data": [
                    {
                        "fact_id": f"{ticker}-2024-adjusted_earnings.adjusted_eps",
                        "fact_name": "adjusted_earnings.adjusted_eps",
                        "value": "5.00",
                        "fiscal_year": 2024,
                        "method": "S1_SEC_RECONCILIATION",
                        "policy": "street_comparable",
                        "confidence": "0.95",
                        "quality_status": "passed",
                        "flags": [],
                        "formula": "adjusted_ni / diluted_shares",
                        "source_trace": {"source_type": "postgres", "quality_status": "passed"},
                    }
                ],
                "meta": {"ticker": ticker, "data_mode": "source_backed", "total": 1},
            }
            if ticker == "POSTGRESONLY"
            else None
        ),
    )

    response = client.get("/api/v1/companies/POSTGRESONLY/data-audit")
    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["data_mode"] == "source_backed"
    assert payload["data"][0]["source_trace"]["source_type"] == "postgres"


def test_source_backed_data_audit_includes_financial_facts(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.delenv("ALLOW_FIXTURE_FALLBACK", raising=False)
    monkeypatch.delenv("DISABLE_FIXTURE_FALLBACK", raising=False)

    trace = {
        "source_type": "SEC_COMPANYFACTS_BULK",
        "source_document_id": "sec-companyfacts-aapl",
        "filing_id": "0000320193-24-000123",
        "period": "2024FY",
        "unit": "USD/shares",
        "currency": "USD",
        "formula": "SEC companyfacts reported XBRL fact",
        "quality_status": "source_backed_sec_companyfacts",
    }
    monkeypatch.setattr(
        "services.api.main.financial_facts_from_postgres",
        lambda ticker: (
            [
                {
                    "ticker": ticker,
                    "taxonomy": "us-gaap",
                    "tag": "EarningsPerShareDiluted",
                    "label": "Earnings Per Share, Diluted",
                    "fiscal_year": 2024,
                    "fiscal_period": "FY",
                    "value": "6.08",
                    "unit": "USD/shares",
                    "currency": "USD",
                    "source": "SEC_COMPANYFACTS_BULK",
                    "quality_status": "source_backed_sec_companyfacts",
                    "source_trace": trace,
                    "metadata": {},
                }
            ]
            if ticker == "POSTGRESONLY"
            else None
        ),
    )

    response = client.get("/api/v1/companies/POSTGRESONLY/data-audit")

    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["data_mode"] == "source_backed"
    assert "financial_facts" in payload["meta"]["scope"]
    fact = payload["data"][0]
    assert fact["fact_name"] == "financial_facts.us-gaap.EarningsPerShareDiluted"
    assert fact["value"] == "6.08"
    assert fact["source_trace"]["filing_id"] == "0000320193-24-000123"

    fact_response = client.get(f"/api/data-audit/{fact['fact_id']}")
    assert fact_response.status_code == 200
    assert fact_response.json()["data"]["fact_id"] == fact["fact_id"]


def test_source_backed_data_audit_includes_forecast_assumptions(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.delenv("ALLOW_FIXTURE_FALLBACK", raising=False)
    monkeypatch.delenv("DISABLE_FIXTURE_FALLBACK", raising=False)
    monkeypatch.setattr(
        "services.api.main.adjusted_series_from_postgres",
        lambda ticker, policy: None,
    )
    monkeypatch.setattr("services.api.main.financial_facts_from_postgres", lambda ticker: None)
    monkeypatch.setattr("services.api.main.forecast_evidence_from_postgres", lambda ticker: None)
    monkeypatch.setattr("services.api.main.company_snapshot_from_postgres", lambda ticker: None)
    monkeypatch.setattr("services.api.main.financials_from_postgres", lambda ticker: None)
    monkeypatch.setattr(
        "services.api.main._source_backed_valuation_payload",
        lambda ticker, **kwargs: {
            "data": [
                {
                    "fiscal_year": 2024,
                    "metric": "6.08",
                    "price": "190.00",
                    "dividend": "1.00",
                    "normal_multiple": "25.00",
                    "fair_multiple": "20.00",
                    "fair_value_price": "121.60",
                    "forecast_flag": False,
                    "source_trace": {
                        "source_type": "postgres",
                        "source_document_id": "sec-aapl-2024",
                        "filing_id": "0000320193-24-000123",
                        "period": "FY2024",
                        "unit": "per_share",
                        "currency": "USD",
                        "formula": "source-backed valuation row",
                        "quality_status": "passed",
                    },
                },
                {
                    "fiscal_year": 2025,
                    "metric": "6.50",
                    "price": "130.00",
                    "dividend": "1.05",
                    "normal_multiple": "25.00",
                    "fair_multiple": "20.00",
                    "fair_value_price": "130.00",
                    "forecast_flag": True,
                    "forecast_source": "consensus_snapshot",
                    "source_trace": {
                        "source_type": "consensus_snapshot",
                        "source_document_id": "consensus-aapl-2025",
                        "filing_id": "consensus-aapl-2025",
                        "period": "FY2025",
                        "unit": "per_share",
                        "currency": "USD",
                        "formula": "consensus EPS * target multiple",
                        "quality_status": "source_backed_forecast_assumption",
                    },
                },
            ],
            "meta": {
                "price_points": [
                    {
                        "date": "2024-01-31",
                        "fiscal_year": 2024,
                        "close_price": "188.25",
                        "currency": "USD",
                        "frequency": "monthly",
                        "source_trace": {
                            "source_type": "postgres_price_bars",
                            "source_document_id": "price-doc-2024-01",
                            "filing_id": "price-bars-2024-01",
                            "period": "2024-01-31",
                            "unit": "close_price",
                            "currency": "USD",
                            "formula": "last available close per calendar month",
                            "quality_status": "source_backed_price_bar",
                        },
                    }
                ],
                "price_points_meta": {
                    "frequency": "monthly",
                    "historical_only": True,
                    "quality_status": "source_backed_price_bar",
                },
                "forecast": {
                    "mode": "estimates",
                    "case": "base",
                    "growth_rate_pct": "6.90",
                    "target_multiple": "20.00",
                    "analyst_count": 23,
                    "source": "consensus_snapshot",
                    "formula": "consensus EPS * target multiple",
                    "consensus": {"quality_status": "source_backed_consensus"},
                    "source_trace": {
                        "source_type": "consensus_snapshot",
                        "source_document_id": "consensus-aapl-assumptions",
                        "filing_id": "consensus-aapl-assumptions",
                        "period": "FY2025-FY2025",
                        "unit": "forecast_assumption",
                        "currency": "USD",
                        "formula": "consensus EPS * target multiple",
                        "quality_status": "source_backed_consensus",
                    },
                    "manual_eps_values": [],
                }
            },
        }
        if ticker == "POSTGRESONLY"
        else None,
    )

    response = client.get("/api/v1/companies/POSTGRESONLY/data-audit")

    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["data_mode"] == "source_backed"
    assert "forecast_assumption" in payload["meta"]["scope"]
    assert "chart_key" in payload["meta"]["scope"]
    assert "price_points" in payload["meta"]["scope"]
    rows = {
        row["fact_name"]: row
        for row in payload["data"]
        if row["fact_name"].startswith("forecast_assumption.")
    }
    assert rows["forecast_assumption.source"]["value"] == "consensus_snapshot"
    assert rows["forecast_assumption.formula"]["source_trace"]["source_document_id"] == (
        "consensus-aapl-assumptions"
    )
    chart_key_rows = {
        row["fact_name"]: row
        for row in payload["data"]
        if row["fact_name"].startswith("chart_key.")
    }
    assert chart_key_rows["chart_key.current_multiple"]["value"] == "31.25"
    assert chart_key_rows["chart_key.custom_multiple"]["value"] == "20.00"
    assert chart_key_rows["chart_key.payout_ratio_pct"]["source_trace"]["formula"] == (
        "payout_ratio_pct = dividend / selected valuation metric * 100"
    )
    price_point_rows = {
        row["fact_name"]: row
        for row in payload["data"]
        if row["fact_name"].startswith("price_point.")
    }
    assert price_point_rows["price_point.close_price.2024-01-31"]["value"] == "188.25"
    assert price_point_rows["price_point.close_price.2024-01-31"]["source_trace"]["period"] == (
        "2024-01-31"
    )


def test_portfolio_sample_and_import_return_holdings():
    saved = client.get("/api/v1/portfolio")
    assert saved.status_code == 200
    assert saved.json()["data"]["holdings"]
    saved_holding = saved.json()["data"]["holdings"][0]
    assert saved_holding["source_trace"]["unit"] == "portfolio_holding"
    assert saved_holding["source_trace"]["ticker"] == saved_holding["ticker"]

    sample = client.get("/api/v1/portfolio/sample")
    assert sample.status_code == 200
    assert sample.json()["data"]["holdings"]

    imported = client.post(
        "/api/v1/portfolio/import",
        json={
            "csv_text": (
                "date,ticker,side,quantity,price,currency,sector\n"
                "2024-01-01,AAPL,buy,1,100,USD,Technology\n"
            )
        },
    )
    assert imported.status_code == 200
    assert imported.json()["data"]["holdings"][0]["ticker"] == "AAPL"
    assert imported.json()["data"]["import_trace"]["source_type"] == "user_csv"
    assert imported.json()["data"]["import_trace"]["rows"] == 1
    assert imported.json()["data"]["holdings"][0]["source_trace"]["source_type"] == "user_csv"


def test_data_audit_includes_screener_and_portfolio_facts():
    response = client.get("/api/v1/companies/AAPL/data-audit")
    assert response.status_code == 200
    payload = response.json()
    assert "screener" in payload["meta"]["scope"]
    assert "portfolio" in payload["meta"]["scope"]
    rows = {row["fact_name"]: row for row in payload["data"]}
    assert rows["screener.per"]["source_trace"]["unit"] == "screening_metrics"
    assert "screener.market_cap" in rows
    assert "screener.market_cap_usd" in rows
    assert "screener.listed_shares" in rows
    assert rows["portfolio.market_value"]["source_trace"]["unit"] == "portfolio_holding"
    assert rows["portfolio_transaction.2023-01-10.buy.1.price"]["value"] == "130"
    assert rows["portfolio_transaction.2023-01-10.buy.1.price"]["source_trace"][
        "transaction_side"
    ] == "buy"
    tx_fact = client.get(
        f"/api/data-audit/{rows['portfolio_transaction.2023-01-10.buy.1.price']['fact_id']}"
    )
    assert tx_fact.status_code == 200
    assert tx_fact.json()["data"]["source_trace"]["period"] == "2023-01-10"


def test_source_backed_portfolio_bypasses_fixture_guard(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.delenv("ALLOW_FIXTURE_FALLBACK", raising=False)
    monkeypatch.delenv("DISABLE_FIXTURE_FALLBACK", raising=False)
    monkeypatch.setattr(
        "services.api.main.portfolio_from_postgres",
        lambda owner_key="default": {
            "as_of": "2026-06-04",
            "holdings": [],
            "total_market_value": "0.00",
            "xirr": None,
            "sector_weights": {},
            "source_trace": {"source_type": "postgres", "quality_status": "source_backed_empty"},
        },
    )

    response = client.get("/api/v1/portfolio")
    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["data_mode"] == "source_backed"
    assert payload["data"]["source_trace"]["source_type"] == "postgres"


def test_source_backed_portfolio_import_uses_postgres_when_available(monkeypatch):
    monkeypatch.setattr(
        "services.api.main.store_portfolio_csv_to_postgres",
        lambda csv_text, owner_key="default", replace_existing=True: {
            "as_of": "2026-06-04",
            "holdings": [{"ticker": "AAPL", "transactions": []}],
            "total_market_value": "0.00",
            "xirr": None,
            "sector_weights": {},
            "import_trace": {"source_type": "user_csv", "rows": 1},
        },
    )

    response = client.post(
        "/api/v1/portfolio/import",
        json={
            "csv_text": (
                "date,ticker,side,quantity,price,currency,sector\n"
                "2024-01-01,AAPL,buy,1,100,USD,Technology\n"
            )
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["data_mode"] == "source_backed"
    assert payload["data"]["import_trace"]["source_type"] == "user_csv"


def test_watchlist_fixture_add_and_remove_contract():
    response = client.get("/api/v1/watchlist")
    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["data_mode"] == "fixture_non_production"
    assert {item["ticker"] for item in payload["data"]["items"]} >= {"AAPL", "NVDA"}
    assert payload["data"]["source_trace"]["quality_status"] == ("fixture_non_production_watchlist")
    assert payload["data"]["items"][0]["source_trace"]["unit"] == "watchlist_item"

    added = client.post(
        "/api/v1/watchlist/items",
        json={"ticker": "MSFT", "note": "wide moat software", "persist": False},
    )
    assert added.status_code == 200
    added_items = added.json()["data"]["items"]
    msft = next(item for item in added_items if item["ticker"] == "MSFT")
    assert msft["note"] == "wide moat software"
    assert msft["source_trace"]["quality_status"] == "fixture_non_production_watchlist"
    assert msft["source_trace"]["unit"] == "watchlist_item"

    removed = client.delete("/api/v1/watchlist/items/NVDA")
    assert removed.status_code == 200
    assert "NVDA" not in {item["ticker"] for item in removed.json()["data"]["items"]}


def test_source_backed_watchlist_bypasses_fixture_guard(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.delenv("ALLOW_FIXTURE_FALLBACK", raising=False)
    monkeypatch.delenv("DISABLE_FIXTURE_FALLBACK", raising=False)
    monkeypatch.setattr(
        "services.api.main.watchlist_from_postgres",
        lambda owner_key="default", name="Default": {
            "id": "watchlist-id",
            "name": name,
            "owner_key": owner_key,
            "items": [
                {
                    "ticker": "POSTGRESONLY",
                    "name": "Postgres Only Inc.",
                    "market": "NASDAQ",
                    "country": "US",
                    "currency": "USD",
                    "current_price": "100.00",
                    "per": "20.00",
                    "dividend_yield": None,
                    "eps_cagr": None,
                    "quality_status": "source_backed",
                    "note": "source-backed watchlist item",
                    "source_trace": {"source_type": "postgres", "quality_status": "source_backed"},
                }
            ],
            "source_trace": {"source_type": "postgres", "quality_status": "source_backed"},
        },
    )

    response = client.get("/api/v1/watchlist")

    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["data_mode"] == "source_backed"
    assert payload["data"]["items"][0]["ticker"] == "POSTGRESONLY"


def test_source_backed_watchlist_mutations_use_postgres(monkeypatch):
    def add_item(ticker: str, note=None, owner_key="default", name="Default"):
        return {
            "id": "watchlist-id",
            "name": name,
            "owner_key": owner_key,
            "items": [{"ticker": ticker.upper(), "note": note, "source_trace": {}}],
            "source_trace": {"source_type": "postgres"},
        }

    def remove_item(ticker: str, owner_key="default", name="Default"):
        return {
            "id": "watchlist-id",
            "name": name,
            "owner_key": owner_key,
            "items": [],
            "source_trace": {"source_type": "postgres"},
        }

    monkeypatch.setattr("services.api.main.add_watchlist_item_to_postgres", add_item)
    monkeypatch.setattr("services.api.main.remove_watchlist_item_from_postgres", remove_item)

    added = client.post("/api/v1/watchlist/items", json={"ticker": "AAPL", "note": "core"})
    assert added.status_code == 200
    assert added.json()["meta"]["data_mode"] == "source_backed"
    assert added.json()["data"]["items"][0]["note"] == "core"

    removed = client.delete("/api/v1/watchlist/items/AAPL")
    assert removed.status_code == 200
    assert removed.json()["meta"]["data_mode"] == "source_backed"
    assert removed.json()["data"]["items"] == []


def test_use_of_cash_fixture_contract():
    response = client.get("/api/v1/companies/AAPL/use-of-cash")
    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["data_mode"] == "fixture_non_production"
    assert "share_repurchases" in payload["meta"]["scope"]
    latest = payload["data"][-1]
    assert latest["free_cash_flow"]
    assert latest["dividend_per_share"] == "1.00"
    assert latest["share_repurchases"] is None
    assert "missing_share_repurchases_source" in latest["flags"]
    for key in SOURCE_TRACE_AUDIT_KEYS:
        assert latest["source_trace"][key]


def test_fiscal_fitness_fixture_contract():
    response = client.get("/api/v1/companies/AAPL/fiscal-fitness")
    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["data_mode"] == "fixture_non_production"
    assert "roe_pct" in payload["meta"]["scope"]
    latest_rows = [row for row in payload["data"] if row["fiscal_year"] == 2024]
    by_key = {row["metric_key"]: row for row in latest_rows}
    assert by_key["roe_pct"]["value"] == "151.1"
    assert by_key["fcf_margin_pct"]["value"] == "27.83"
    assert by_key["current_ratio"]["value"] is None
    assert "missing_current_ratio_source" in by_key["current_ratio"]["flags"]
    for key in SOURCE_TRACE_AUDIT_KEYS:
        assert by_key["roe_pct"]["source_trace"][key]


def test_health_check_fixture_contract():
    response = client.get("/api/v1/companies/AAPL/health-check")
    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["data_mode"] == "fixture_non_production"
    data = payload["data"]
    assert data["overall_score"]
    assert data["rating"] in {"strong", "healthy", "mixed", "watch"}
    assert data["quality_status"] == "fixture_non_production_health_check"
    assert {axis["axis_key"] for axis in data["axes"]} == {
        "profitability",
        "cash_generation",
        "financial_strength",
        "growth",
        "predictability",
    }
    for key in SOURCE_TRACE_AUDIT_KEYS:
        assert data["source_trace"][key]


def test_research_report_fixture_contract():
    response = client.get("/api/v1/companies/AAPL/research-report")
    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["data_mode"] == "fixture_non_production"
    data = payload["data"]
    assert data["title"] == "AAPL Source-Audited Research Report"
    assert data["executive_summary"]
    assert {section["section_key"] for section in data["sections"]} == {
        "valuation",
        "quality",
        "forecast",
        "capital_allocation",
        "data_quality",
    }
    assert {fact["fact_name"] for fact in data["audit_facts"]} >= {
        "research_report.valuation_gap_pct",
        "research_report.health_score",
        "research_report.forecast_total_return_cagr_pct",
    }
    for key in SOURCE_TRACE_AUDIT_KEYS:
        assert data["source_trace"][key]
    for section in data["sections"]:
        for key in SOURCE_TRACE_AUDIT_KEYS:
            assert section["source_trace"][key]
        assert section["evidence"]
        for item in section["evidence"]:
            assert item["source_trace"]
            assert item["source_trace"]["source_type"]
            assert item["source_trace"]["quality_status"]
    for fact in data["audit_facts"]:
        for key in SOURCE_TRACE_AUDIT_KEYS:
            assert fact["source_trace"][key], (fact["fact_name"], key)


def test_research_report_exports_are_downloadable_and_source_traced():
    markdown = client.get("/api/v1/companies/AAPL/exports/research-report.md")
    assert markdown.status_code == 200
    assert markdown.headers["x-export-version"] == "research_export_v1"
    assert "attachment; filename=aapl-research-report.md" in markdown.headers[
        "content-disposition"
    ]
    assert "# AAPL Source-Audited Research Report" in markdown.text
    assert "research_report.valuation_gap_pct" in markdown.text
    assert "## Source Trace" in markdown.text

    json_response = client.get(
        "/api/v1/companies/AAPL/exports/research-report.json?forecast_mode=custom&target_multiple=21"
    )
    assert json_response.status_code == 200
    bundle = json_response.json()
    assert bundle["manifest"]["ticker"] == "AAPL"
    assert bundle["report"]["source_trace"]["source_type"] == "research_report_derived"
    assert any(
        row["fact_name"] == "research_report.valuation_gap_pct"
        for row in bundle["data_audit"]
    )
    assert any(
        row["fact_name"] == "forecast_assumption.formula"
        for row in bundle["data_audit"]
    )
    assert any(
        row["fact_name"] == "chart_key.custom_multiple" and row["value"] == "21.00"
        for row in bundle["data_audit"]
    )
    assert all("trace_sections" in row for row in bundle["data_audit"])
    chart_key_detail = next(
        row for row in bundle["data_audit"] if row["fact_name"] == "chart_key.custom_multiple"
    )
    assert {section["title"] for section in chart_key_detail["trace_sections"]} >= {
        "Source evidence",
        "Calculation",
        "Quality",
    }

    csv_response = client.get(
        "/api/v1/companies/AAPL/exports/data-audit.csv?forecast_mode=custom&target_multiple=21"
    )
    assert csv_response.status_code == 200
    assert csv_response.text.splitlines()[0].startswith("fact_id,fact_name,fiscal_year")
    assert "adjusted_earnings.adjusted_eps" in csv_response.text
    assert "forecast_assumption.formula" in csv_response.text
    assert "chart_key.custom_multiple" in csv_response.text
    assert "21.00" in csv_response.text
    assert "source_document_id" in csv_response.text
    header = csv_response.text.splitlines()[0]
    assert "input_trace_keys" in header
    assert "calculation_inputs_json" in header
    assert "source_trace_json" in header
    assert "forecast_metric_trace" in csv_response.text


def test_research_metadata_requires_source_backed_rows(monkeypatch):
    monkeypatch.setattr(
        "services.api.main.research_metadata_from_postgres",
        lambda ticker, limit=25: None,
    )

    response = client.get("/api/v1/companies/AAPL/research-metadata")

    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["data_mode"] == "source_backed_required"
    assert payload["meta"]["financial_numbers_allowed"] is False
    assert payload["data"]["items"] == []
    trace = payload["data"]["source_trace"]
    for key in SOURCE_TRACE_AUDIT_KEYS:
        assert trace[key], key
    assert trace["financial_numbers_allowed"] is False
    assert "research_metadata_not_loaded" in trace["quality_flags"]


def test_research_metadata_source_backed_contract(monkeypatch):
    trace = {
        "source_document_id": "research-doc",
        "source_type": "naver_search_research",
        "filing_id": "naver_search_research-005930-2024-2026",
        "period": "2026-06-27",
        "available_at": "2026-06-27T00:00:00+00:00",
        "unit": "research_metadata",
        "currency": "N/A",
        "method": "metadata_only_no_financial_numbers",
        "formula": "raw_objects/source_documents metadata lookup",
        "quality_status": "source_backed_research_metadata",
        "financial_numbers_allowed": False,
    }
    monkeypatch.setattr(
        "services.api.main.research_metadata_from_postgres",
        lambda ticker, limit=25: {
            "ticker": ticker,
            "data_mode": "source_backed",
            "policy": "metadata_only_no_financial_numbers",
            "quality_status": "source_backed_research_metadata",
            "items": [
                {
                    "source": "naver_search_research",
                    "source_label": "Naver research search metadata",
                    "ticker": ticker,
                    "identifier": "naver_search_research-005930-2024-2026",
                    "title": "Samsung Electronics research metadata",
                    "link": "https://example.com/research",
                    "description": "metadata only",
                    "source_url": "https://openapi.naver.com/v1/search/webkr.json",
                    "source_document_id": "research-doc",
                    "content_hash": "abc",
                    "content_type": "application/json",
                    "item_count": 1,
                    "financial_numbers_allowed": False,
                    "terms_note": "terms review required",
                    "source_note": "metadata only",
                    "source_trace": trace,
                }
            ],
            "source_trace": trace | {"source_type": "research_metadata"},
            "meta": {
                "source": "postgres",
                "financial_numbers_allowed": False,
                "row_count": 1,
                "item_count": 1,
            },
        },
    )

    response = client.get("/api/v1/companies/005930.KS/research-metadata")

    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["data_mode"] == "source_backed"
    assert payload["meta"]["policy"] == "metadata_only_no_financial_numbers"
    item = payload["data"]["items"][0]
    assert item["financial_numbers_allowed"] is False
    assert item["source_trace"]["method"] == "metadata_only_no_financial_numbers"
    assert item["source_trace"]["unit"] == "research_metadata"


def test_fiscal_fitness_production_blocks_fixture_fallback(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.delenv("ALLOW_FIXTURE_FALLBACK", raising=False)
    monkeypatch.delenv("DISABLE_FIXTURE_FALLBACK", raising=False)
    monkeypatch.setattr("services.api.main.financials_from_postgres", lambda ticker: None)

    response = client.get("/api/v1/companies/AAPL/fiscal-fitness")

    assert response.status_code == 503
    assert response.json()["detail"]["surface"] == "fiscal_fitness"


def test_health_check_production_blocks_fixture_fallback(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.delenv("ALLOW_FIXTURE_FALLBACK", raising=False)
    monkeypatch.delenv("DISABLE_FIXTURE_FALLBACK", raising=False)
    monkeypatch.setattr("services.api.main.financials_from_postgres", lambda ticker: None)

    response = client.get("/api/v1/companies/AAPL/health-check")

    assert response.status_code == 503
    assert response.json()["detail"]["surface"] == "health_check"


def test_research_report_production_blocks_fixture_fallback(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.delenv("ALLOW_FIXTURE_FALLBACK", raising=False)
    monkeypatch.delenv("DISABLE_FIXTURE_FALLBACK", raising=False)
    monkeypatch.setattr("services.api.main.financials_from_postgres", lambda ticker: None)

    response = client.get("/api/v1/companies/AAPL/research-report")

    assert response.status_code == 503
    assert response.json()["detail"]["surface"] == "research_report"


def test_source_backed_fiscal_fitness_bypasses_fixture_guard(monkeypatch):
    trace = {
        "source_document_id": "source-doc",
        "source_type": "postgres",
        "filing_id": "filing",
        "period": "FY2024",
        "available_at": "2025-01-31T00:00:00+00:00",
        "unit": "reported",
        "currency": "USD",
        "method": "postgres",
        "formula": "source-backed test input",
        "quality_status": "passed",
    }
    fcf_trace = trace | {"source_document_id": "fcf-source-doc"}
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.delenv("ALLOW_FIXTURE_FALLBACK", raising=False)
    monkeypatch.delenv("DISABLE_FIXTURE_FALLBACK", raising=False)
    monkeypatch.setattr(
        "services.api.main.financials_from_postgres",
        lambda ticker: [
            {
                "fiscal_year": 2024,
                "revenue": "1000",
                "eps": "10",
                "fcf": "250",
                "gross_margin": "40",
                "operating_margin": "20",
                "net_margin": "15",
                "roe": "18",
                "roic": "12",
                "debt_to_equity": "0.5",
                "method": "postgres",
                "confidence": "0.90",
                "source_trace": trace,
                "metric_traces": {"fcf": fcf_trace, "revenue": trace},
            }
        ],
    )

    response = client.get("/api/v1/companies/AAPL/fiscal-fitness")

    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["data_mode"] == "source_backed"
    row = next(item for item in payload["data"] if item["metric_key"] == "fcf_margin_pct")
    assert row["value"] == "25.00"
    assert row["quality_status"] == "source_backed_derived"
    assert row["source_trace"]["metric_input_traces"]["fcf"]["source_document_id"] == (
        "fcf-source-doc"
    )


def test_source_backed_health_check_bypasses_fixture_guard(monkeypatch):
    trace = {
        "source_document_id": "source-doc",
        "source_type": "postgres",
        "filing_id": "filing",
        "period": "FY2024",
        "available_at": "2025-01-31T00:00:00+00:00",
        "unit": "reported",
        "currency": "USD",
        "method": "postgres",
        "formula": "source-backed test input",
        "quality_status": "passed",
    }
    forecast_trace = trace | {"source_document_id": "forecast-source-doc"}
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.delenv("ALLOW_FIXTURE_FALLBACK", raising=False)
    monkeypatch.delenv("DISABLE_FIXTURE_FALLBACK", raising=False)
    monkeypatch.setattr(
        "services.api.main.financials_from_postgres",
        lambda ticker: [
            {
                "fiscal_year": 2023,
                "revenue": "800",
                "eps": "8",
                "fcf": "160",
                "gross_margin": "45",
                "operating_margin": "25",
                "net_margin": "15",
                "roe": "18",
                "roic": "12",
                "debt_to_equity": "0.5",
                "method": "postgres",
                "confidence": "0.90",
                "source_trace": trace,
            },
            {
                "fiscal_year": 2024,
                "revenue": "1000",
                "eps": "10",
                "fcf": "250",
                "gross_margin": "50",
                "operating_margin": "30",
                "net_margin": "20",
                "roe": "22",
                "roic": "16",
                "debt_to_equity": "0.4",
                "method": "postgres",
                "confidence": "0.90",
                "source_trace": trace,
            },
        ],
    )
    monkeypatch.setattr(
        "services.api.main.forecast_evidence_from_postgres",
        lambda ticker: {
            "forecast_year": 2025,
            "source_trace": forecast_trace,
            "scorecard": {
                "status": "source_backed_scorecard",
                "summary": {
                    "hit_rate_1y_pct": "80",
                    "hit_rate_2y_pct": "60",
                },
            },
            "sentiment": {
                "net_revision_score_pct": "10",
                "quality_status": "source_backed",
            },
        },
    )

    response = client.get("/api/v1/companies/AAPL/health-check")

    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["data_mode"] == "source_backed"
    assert payload["data"]["source_trace"]["source_type"] == "health_check_derived"
    assert payload["data"]["overall_score"]


def test_source_backed_research_report_bypasses_fixture_guard(monkeypatch):
    trace = {
        "source_document_id": "source-doc",
        "source_type": "postgres",
        "filing_id": "filing",
        "period": "FY2024",
        "available_at": "2025-01-31T00:00:00+00:00",
        "unit": "reported",
        "currency": "USD",
        "method": "postgres",
        "formula": "source-backed test input",
        "quality_status": "passed",
    }
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.delenv("ALLOW_FIXTURE_FALLBACK", raising=False)
    monkeypatch.delenv("DISABLE_FIXTURE_FALLBACK", raising=False)
    monkeypatch.setattr(
        "services.api.main.financials_from_postgres",
        lambda ticker: [
            {
                "fiscal_year": 2023,
                "revenue": "800",
                "eps": "8",
                "fcf": "160",
                "gross_margin": "45",
                "operating_margin": "25",
                "net_margin": "15",
                "roe": "18",
                "roic": "12",
                "debt_to_equity": "0.5",
                "method": "postgres",
                "confidence": "0.90",
                "source_trace": trace,
            },
            {
                "fiscal_year": 2024,
                "revenue": "1000",
                "eps": "10",
                "fcf": "250",
                "gross_margin": "50",
                "operating_margin": "30",
                "net_margin": "20",
                "roe": "22",
                "roic": "16",
                "debt_to_equity": "0.4",
                "method": "postgres",
                "confidence": "0.90",
                "source_trace": trace,
            },
        ],
    )
    monkeypatch.setattr(
        "services.api.main.company_snapshot_from_postgres",
        lambda ticker: {"currency": "USD", "source_trace": trace},
    )
    monkeypatch.setattr(
        "services.api.main.forecast_evidence_from_postgres",
        lambda ticker: {"source_trace": trace, "sentiment": {"label": "positive"}},
    )
    monkeypatch.setattr(
        "services.api.main._source_backed_valuation_payload",
        lambda ticker, **kwargs: None,
    )
    monkeypatch.setattr("services.api.main._source_backed_use_of_cash_rows", lambda ticker: None)

    response = client.get("/api/v1/companies/AAPL/research-report")

    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["data_mode"] == "source_backed"
    assert payload["meta"]["partial"] is True
    assert "valuation" in payload["meta"]["missing_scopes"]
    assert payload["data"]["source_trace"]["source_type"] == "research_report_derived"
    assert "missing_valuation_map" in payload["data"]["flags"]
    for fact in payload["data"]["audit_facts"]:
        for key in SOURCE_TRACE_AUDIT_KEYS:
            assert fact["source_trace"][key], (fact["fact_name"], key)


def test_use_of_cash_production_blocks_fixture_fallback(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.delenv("ALLOW_FIXTURE_FALLBACK", raising=False)
    monkeypatch.delenv("DISABLE_FIXTURE_FALLBACK", raising=False)
    monkeypatch.setattr("services.api.main.use_of_cash_inputs_from_postgres", lambda ticker: None)

    response = client.get("/api/v1/companies/AAPL/use-of-cash")

    assert response.status_code == 503
    assert response.json()["detail"]["surface"] == "use_of_cash"


def test_source_backed_use_of_cash_preserves_missing_dividend(monkeypatch):
    trace = {
        "source_document_id": "source-doc",
        "source_type": "postgres",
        "filing_id": "filing",
        "period": "FY2024",
        "available_at": "2025-01-31T00:00:00+00:00",
        "unit": "reported",
        "currency": "USD",
        "method": "postgres",
        "formula": "source-backed test input",
        "quality_status": "passed",
    }
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.delenv("ALLOW_FIXTURE_FALLBACK", raising=False)
    monkeypatch.delenv("DISABLE_FIXTURE_FALLBACK", raising=False)
    monkeypatch.setattr(
        "services.api.main.use_of_cash_inputs_from_postgres",
        lambda ticker: (
            [
                {
                    "fiscal_year": 2024,
                    "revenue": "1000",
                    "fcf": "250",
                    "eps": "10",
                    "debt_to_equity": "0.5",
                    "method": "S1_SEC_RECONCILIATION",
                    "confidence": "0.95",
                    "source_trace": trace,
                }
            ],
            [
                {
                    "fiscal_year": 2024,
                    "metric": "10",
                    "dividend": None,
                    "forecast_flag": False,
                    "source_trace": trace,
                }
            ],
            "USD",
        ),
    )

    response = client.get("/api/v1/companies/AAPL/use-of-cash")

    assert response.status_code == 200
    payload = response.json()
    row = payload["data"][0]
    assert payload["meta"]["data_mode"] == "source_backed"
    assert row["dividend_per_share"] is None
    assert row["dividend_payout_pct"] is None
    assert "missing_dividend_source" in row["flags"]
    assert row["quality_status"] == "source_backed_partial"
