import csv
import hashlib
import io
import json
import zipfile
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pandas as pd
import pytest

from data.blob_queue import BlobQueueItem, BlobUploadQueue
from packages.connectors.base import ConnectorDocument, ConnectorRequest
from packages.connectors.ecos import EcosConnector
from packages.connectors.edinet import EdinetConnector
from packages.connectors.estat import EStatConnector
from packages.connectors.finance_data_reader import FinanceDataReaderConnector
from packages.connectors.fred import FredConnector
from packages.connectors.jquants import JQuantsConnector
from packages.connectors.kosis import KosisConnector
from packages.connectors.marcap import MarcapConnector
from packages.connectors.opendart import DEFAULT_CORP_CODES, OpenDartConnector
from packages.connectors.pykrx import PyKrxConnector
from packages.connectors.research_metadata import (
    HankyungConsensusMetadataConnector,
    NaverResearchSearchConnector,
)
from packages.connectors.sec import SecBulkConnector
from packages.connectors.stooq import StooqConnector
from packages.core.source_trace import SourceTrace
from packages.core.universe import (
    JP_TOP_MARKET_CAP_PRIORITY_TICKERS,
    KR_TOP_MARKET_CAP_PRIORITY_TICKERS,
    TOP_MARKET_CAP_PRIORITY_TICKERS,
)
import services.ingestion_worker.cli as ingestion_cli
from services.api.source_coverage import build_source_coverage_report
from services.ingestion_worker.cli import (
    _deployment_gate_output_summary,
    _fred_recession_periods,
    _kr_readiness_output_summary,
    _official_stat_industry_payload,
    _persist_market_standard_document,
    build_consensus_workpaper,
    build_kr_valuation_inputs,
    collect_ecos_series,
    collect_edinet_filings,
    collect_estat_tables,
    collect_fdr_prices,
    collect_fred_series,
    collect_jquants_data,
    collect_kosis_tables,
    collect_marcap_data,
    collect_opendart_dividends,
    collect_pykrx_fundamentals,
    collect_market_documents,
    collect_pykrx_prices,
    collect_research_metadata,
    collect_sec_bulk_archives,
    collect_stooq_prices,
    deployment_gate,
    deployment_preflight,
    doctor,
    export_consensus_template,
    export_deterministic_forecast_csv,
    import_consensus_csv,
    import_fnguide_export,
    import_market_csv,
    inspect_raw_kr_evidence,
    kr_production_readiness,
    load_sec_bulk_warehouse,
    normalize_us_batch_run,
    run_p1_e2e,
    run_priority_e2e,
    run_source_e2e,
    source_coverage_report,
    validate_consensus_csv,
)
from services.ingestion_worker.data_lake_plan import (
    build_data_lake_plan,
    render_data_lake_plan_markdown,
)
from services.ingestion_worker.market_standard import normalize_market_standard_document
from services.ingestion_worker.official_stats import normalize_official_stat_document
from services.ingestion_worker.source_catalog import (
    render_source_catalog_markdown,
    source_catalog_payload,
)


def _marcap_parquet_payload() -> bytes:
    frame = pd.DataFrame(
        [
            {
                "Date": "2024-01-02",
                "Rank": 1,
                "Code": "005930",
                "Name": "Samsung Electronics",
                "Open": 78000,
                "High": 79000,
                "Low": 77000,
                "Close": 78500,
                "Volume": 1000000,
                "Amount": 78500000000,
                "Changes": 1200,
                "ChangeCode": "2",
                "ChagesRatio": 1.55,
                "Marcap": 468000000,
                "Stocks": 5969782550,
                "MarketId": "STK",
                "Market": "KOSPI",
                "Dept": "",
            },
            {
                "Date": "2024-01-02",
                "Rank": 2,
                "Code": "000660",
                "Name": "SK hynix",
                "Open": 140000,
                "High": 142000,
                "Low": 139000,
                "Close": 141500,
                "Volume": 500000,
                "Amount": 70750000000,
                "Changes": 2500,
                "ChangeCode": "2",
                "ChagesRatio": 1.8,
                "Marcap": 103000000,
                "Stocks": 728002365,
                "MarketId": "STK",
                "Market": "KOSPI",
                "Dept": "",
            },
        ]
    )
    buffer = io.BytesIO()
    frame.to_parquet(buffer, index=False)
    return buffer.getvalue()


def test_blob_queue_writes_upload_manifest(tmp_path):
    queue = BlobUploadQueue(tmp_path)
    path = queue.enqueue(
        BlobQueueItem(
            local_path="storage/raw/sec/AAPL/file.html",
            blob_key="raw/sec/AAPL/hash.html",
            content_type="text/html",
            metadata={"ticker": "AAPL"},
        )
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["blob_key"] == "raw/sec/AAPL/hash.html"
    assert payload["metadata"]["ticker"] == "AAPL"


def test_market_csv_import_dry_run_validates_shape(tmp_path):
    csv_path = tmp_path / "market.csv"
    csv_path.write_text(
        "ticker,fiscal_year,trade_date,close_price,dividend,currency,source,source_url\n"
        "AAPL,2024,2024-12-31,250.00,1.00,USD,nasdaq_export,https://example.com/aapl.csv\n",
        encoding="utf-8",
    )

    summary = import_market_csv(csv_path, persist=False)
    assert summary == {
        "rows": 1,
        "persisted": False,
        "tickers": ["AAPL"],
        "fiscal_years": [2024],
        "date_range": {"start": "2024-12-31", "end": "2024-12-31"},
        "price_rows": 1,
        "dividend_rows": 1,
        "market_cap_rows": 0,
        "listed_shares_rows": 0,
        "source_types": ["nasdaq_export"],
        "currencies": ["USD"],
    }


def test_market_csv_import_accepts_source_backed_market_structure(tmp_path):
    csv_path = tmp_path / "market_structure.csv"
    csv_path.write_text(
        "ticker,fiscal_year,trade_date,close_price,market_cap,listed_shares,"
        "currency,source,source_url\n"
        "NVDA,2024,2024-12-31,134.29,3280000000000,24490000000,"
        "USD,exchange_market_data,https://example.com/nvda-market.csv\n",
        encoding="utf-8",
    )

    summary = import_market_csv(csv_path, persist=False)

    assert summary["market_cap_rows"] == 1
    assert summary["listed_shares_rows"] == 1
    assert summary["source_types"] == ["exchange_market_data"]


def test_market_csv_import_rejects_non_positive_close(tmp_path):
    csv_path = tmp_path / "market_bad_close.csv"
    csv_path.write_text(
        "ticker,fiscal_year,trade_date,close_price,currency,source\n"
        "AAPL,2024,2024-12-31,0,USD,nasdaq_export\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="close_price must be positive"):
        import_market_csv(csv_path, persist=False)


def test_market_csv_import_rejects_negative_dividend(tmp_path):
    csv_path = tmp_path / "market_bad_dividend.csv"
    csv_path.write_text(
        "ticker,fiscal_year,trade_date,close_price,dividend,currency,source\n"
        "AAPL,2024,2024-12-31,250.00,-1.00,USD,nasdaq_export\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="dividend must be non-negative"):
        import_market_csv(csv_path, persist=False)


def test_consensus_csv_import_dry_run_validates_shape(tmp_path):
    csv_path = tmp_path / "consensus.csv"
    payload = (
        "ticker,fiscal_year,snapshot_date,estimate_case,estimate_eps,growth_rate_pct,"
        "analyst_count,currency,source,source_url\n"
        "AAPL,2025,2024-12-31,median,6.51,7.0,31,USD,user_csv,https://example.com/aapl.csv\n"
    )
    csv_path.write_text(
        payload,
        encoding="utf-8",
    )
    digest = hashlib.sha256(csv_path.read_bytes()).hexdigest()

    summary = import_consensus_csv(csv_path, persist=False)
    assert summary == {
        "rows": 1,
        "persisted": False,
        "tickers": ["AAPL"],
        "fiscal_years": [2025],
        "snapshot_dates": ["2024-12-31"],
        "estimate_cases": {"median": 1},
        "source_types": ["user_csv"],
        "quality_statuses": ["user_provided_consensus_snapshot"],
        "assumption_types": {"external_consensus": 1},
        "manual_assumption_rows": 0,
        "external_consensus_rows": 1,
        "source_file_content_hash": digest,
        "source_evidence_status": "file_hashed",
        "source_file": str(csv_path),
    }


def test_consensus_csv_persist_stores_file_evidence_and_trace(monkeypatch, tmp_path):
    csv_path = tmp_path / "consensus.csv"
    payload = (
        "ticker,fiscal_year,snapshot_date,estimate_case,estimate_eps,growth_rate_pct,"
        "analyst_count,currency,source,source_url,source_document_id\n"
        "AAPL,2025,2024-12-31,median,6.51,7.0,31,USD,user_csv,"
        "https://example.com/aapl.csv,upstream-consensus-doc\n"
    )
    csv_path.write_text(payload, encoding="utf-8")
    digest = hashlib.sha256(csv_path.read_bytes()).hexdigest()

    class FakeRepo:
        def __init__(self):
            self.source_documents = []
            self.raw_objects = []
            self.consensus_snapshots = []
            self.finished_runs = []

        def start_run(self, **kwargs):
            self.run = kwargs
            return "run-id"

        def finish_run(self, run_id, status="succeeded", error_summary=None):
            self.finished_runs.append(
                {"run_id": run_id, "status": status, "error_summary": error_summary}
            )

        def store_source_document(self, security_id, document, source_type):
            self.source_documents.append(
                {
                    "security_id": security_id,
                    "document": document,
                    "source_type": source_type,
                }
            )
            return "stored-source-document"

        def store_raw_object(self, **kwargs):
            self.raw_objects.append(kwargs)

        def ensure_security(self, ticker, name, country, currency, exchange):
            return SimpleNamespace(id=f"{ticker}-security")

        def store_consensus_snapshot(self, *args, **kwargs):
            self.consensus_snapshots.append(
                {
                    "security_id": args[0],
                    "metric_key": args[1],
                    "fiscal_year": args[2],
                    "estimate_case": args[4],
                    "source_trace": args[10],
                    "source_document_id": kwargs["source_document_id"],
                }
            )

    class FakeQueue:
        def __init__(self):
            self.items = []

        def enqueue(self, item):
            self.items.append(item)

    repo = FakeRepo()
    queue = FakeQueue()

    def fake_raw_write(document):
        raw_path = tmp_path / "raw" / f"{document.identifier}.csv"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(document.payload)
        return raw_path, digest

    monkeypatch.setattr(ingestion_cli, "IngestionRepository", lambda: repo)
    monkeypatch.setattr(ingestion_cli, "BlobUploadQueue", lambda: queue)
    monkeypatch.setattr(ingestion_cli, "_write_raw_document", fake_raw_write)

    summary = ingestion_cli.import_consensus_csv(csv_path, persist=True)

    assert summary["persisted"] is True
    assert summary["consensus_snapshots"] == 1
    assert summary["source_document_id"] == "stored-source-document"
    assert summary["raw_object_content_hash"] == digest
    assert summary["source_evidence_status"] == "file_hashed_and_raw_object_ready"
    assert repo.finished_runs == [
        {"run_id": "run-id", "status": "succeeded", "error_summary": None}
    ]
    assert repo.source_documents[0]["source_type"] == "user_consensus_csv"
    assert repo.source_documents[0]["document"].content_hash == digest
    assert repo.raw_objects[0]["source_document_id"] == "stored-source-document"
    assert repo.raw_objects[0]["content_hash"] == digest
    trace = repo.consensus_snapshots[0]["source_trace"]
    assert trace["source_document_id"] == "stored-source-document"
    assert trace["upstream_source_document_id"] == "upstream-consensus-doc"
    assert trace["method"] == "point_in_time_consensus_snapshot"
    assert trace["assumption_type"] == "external_consensus"
    assert trace["llm_generated_numbers"] is False
    assert "source-backed point-in-time consensus" in trace["formula"]
    assert trace["source_file_content_hash"] == digest
    assert trace["source_file_row_number"] == 2


def test_consensus_csv_import_rejects_unknown_case(tmp_path):
    csv_path = tmp_path / "consensus_bad_case.csv"
    csv_path.write_text(
        "ticker,fiscal_year,snapshot_date,estimate_case,estimate_eps,currency,source\n"
        "AAPL,2025,2024-12-31,moonshot,6.51,USD,user_csv\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="estimate_case"):
        import_consensus_csv(csv_path, persist=False)


def test_consensus_csv_import_rejects_non_positive_eps(tmp_path):
    csv_path = tmp_path / "consensus_bad_eps.csv"
    csv_path.write_text(
        "ticker,fiscal_year,snapshot_date,estimate_case,estimate_eps,currency,source\n"
        "AAPL,2025,2024-12-31,median,0,USD,user_csv\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="estimate_eps must be positive"):
        import_consensus_csv(csv_path, persist=False)


def test_consensus_csv_import_rejects_rows_without_source_evidence(tmp_path):
    csv_path = tmp_path / "consensus_missing_source_evidence.csv"
    csv_path.write_text(
        "ticker,fiscal_year,snapshot_date,estimate_case,estimate_eps,currency,source\n"
        "AAPL,2025,2024-12-31,median,6.51,USD,user_csv\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="one of source_url, source_document_id, or filing_id is required"):
        import_consensus_csv(csv_path, persist=False)


def test_consensus_csv_import_rejects_template_quality_status_after_fill(tmp_path):
    csv_path = tmp_path / "consensus_template_status.csv"
    csv_path.write_text(
        "ticker,fiscal_year,snapshot_date,estimate_case,estimate_eps,currency,source,"
        "source_url,quality_status\n"
        "AAPL,2025,2024-12-31,median,6.51,USD,user_csv,"
        "https://example.com/aapl.csv,template_pending_source_value\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="quality_status 'template_pending_source_value' is not import-ready"):
        import_consensus_csv(csv_path, persist=False)


def test_consensus_csv_import_rejects_fastgraphs_as_numeric_source(tmp_path):
    csv_path = tmp_path / "consensus_fastgraphs.csv"
    csv_path.write_text(
        "ticker,fiscal_year,snapshot_date,estimate_case,estimate_eps,currency,source,"
        "source_url,quality_status\n"
        "005930.KS,2026,2026-07-01,median,44800,KRW,fastgraphs_screenshot,"
        "https://app.fastgraphs.com/security/fastgraphs/summary?fgID=example,"
        "user_provided_consensus_snapshot\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="blocked consensus evidence source:"):
        import_consensus_csv(csv_path, persist=False)


def test_consensus_csv_import_rejects_llm_generated_numeric_source(tmp_path):
    csv_path = tmp_path / "consensus_llm.csv"
    csv_path.write_text(
        "ticker,fiscal_year,snapshot_date,estimate_case,estimate_eps,currency,source,"
        "source_document_id,quality_status\n"
        "005930.KS,2026,2026-07-01,median,44800,KRW,chatgpt_forecast,"
        "operator-note-005930-2026,user_provided_consensus_snapshot\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="blocked consensus evidence source:"):
        import_consensus_csv(csv_path, persist=False)


def test_consensus_csv_import_rejects_manual_assumption_without_notes(tmp_path):
    csv_path = tmp_path / "consensus_manual_no_notes.csv"
    csv_path.write_text(
        "ticker,fiscal_year,snapshot_date,estimate_case,estimate_eps,currency,source,"
        "source_url,quality_status\n"
        "005930.KS,2026,2026-07-01,median,44800,KRW,manual_forecast_assumption,"
        "https://example.com/manual-note,manual_forecast_assumption\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="manual forecast assumptions require notes"):
        import_consensus_csv(csv_path, persist=False)


def test_consensus_csv_import_tracks_manual_forecast_assumption(tmp_path):
    csv_path = tmp_path / "consensus_manual.csv"
    csv_path.write_text(
        "ticker,fiscal_year,snapshot_date,estimate_case,estimate_eps,currency,source,"
        "source_url,quality_status,notes\n"
        "005930.KS,2026,2026-07-01,median,44800,KRW,manual_forecast_assumption,"
        "https://example.com/manual-note,manual_forecast_assumption,"
        "Operator scenario based on source-traced research workpaper\n",
        encoding="utf-8",
    )

    summary = import_consensus_csv(csv_path, persist=False)

    assert summary["source_types"] == ["manual_forecast_assumption"]
    assert summary["quality_statuses"] == ["manual_forecast_assumption"]
    assert summary["assumption_types"] == {"manual_assumption": 1}
    assert summary["manual_assumption_rows"] == 1
    assert summary["external_consensus_rows"] == 0


def test_consensus_template_exports_blank_source_backed_rows(tmp_path):
    out = tmp_path / "consensus_template.csv"

    summary = export_consensus_template(
        tickers="AAPL,005930.KS,7203.T",
        start_year=2026,
        years=2,
        cases="low,median,high,median",
        snapshot_date="2026-06-15",
        out=out,
    )

    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert summary == {
        "status": "template_created",
        "path": str(out),
        "rows": 18,
        "tickers": ["AAPL", "005930.KS", "7203.T"],
        "fiscal_years": [2026, 2027],
        "estimate_cases": ["low", "median", "high"],
        "snapshot_date": "2026-06-15",
        "import_ready": False,
        "required_before_import": [
            "estimate_eps",
            "source",
            "source_url_or_source_document_id_or_filing_id",
        ],
        "policy": "template only; no generated financial estimates",
    }
    assert {row["estimate_eps"] for row in rows} == {""}
    assert {row["source"] for row in rows} == {""}
    assert {row["currency"] for row in rows} == {"USD", "KRW", "JPY"}
    assert {row["quality_status"] for row in rows} == {"template_pending_source_value"}

    with pytest.raises(ValueError, match="estimate_eps"):
        import_consensus_csv(out, persist=False)


def test_export_deterministic_forecast_csv_uses_source_backed_cagr(tmp_path):
    cache_dir = tmp_path / "kr-valuation-inputs"
    cache_dir.mkdir()
    cache_path = cache_dir / "005930_KS-2024-2025-valuation-inputs.json"
    cache_path.write_text(
        json.dumps(
            {
                "ticker": "005930.KS",
                "valuation_points": [
                    _forecast_basis_point("005930.KS", 2024, "1000"),
                    _forecast_basis_point("005930.KS", 2025, "1100"),
                ],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "forecast_assumptions.csv"

    summary = export_deterministic_forecast_csv(
        tickers="005930.KS",
        start_year=2026,
        years=2,
        cases="median",
        snapshot_date="2026-07-01",
        metric_key="adjusted_operating_eps",
        cache_dir=cache_dir,
        out=out,
    )

    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert summary["status"] == "deterministic_forecast_csv_created"
    assert summary["validation_status"] == "ready"
    assert summary["import_ready_candidate"] is True
    assert summary["policy"] == (
        "deterministic historical CAGR manual assumption; "
        "external consensus is not claimed; no LLM-generated numbers"
    )
    assert [row["fiscal_year"] for row in rows] == ["2026", "2027"]
    assert [row["estimate_eps"] for row in rows] == ["1210", "1331"]
    assert {row["growth_rate_pct"] for row in rows} == {"10"}
    assert {row["source"] for row in rows} == {"manual_forecast_assumption"}
    assert {row["quality_status"] for row in rows} == {"manual_forecast_assumption"}
    assert all(row["source_document_id"] for row in rows)
    assert all(row["filing_id"] for row in rows)
    assert all("source-backed KR valuation cache" in row["notes"] for row in rows)
    assert all("no LLM-generated numbers" in row["notes"] for row in rows)

    validation = validate_consensus_csv(
        out,
        tickers="005930.KS",
        start_year=2026,
        years=2,
        cases="median,current",
        case_mode="any",
    )
    import_summary = import_consensus_csv(out, persist=False)
    assert validation["status"] == "ready"
    assert validation["import_ready"] is True
    assert import_summary["manual_assumption_rows"] == 2
    assert import_summary["external_consensus_rows"] == 0


def test_export_deterministic_forecast_csv_requires_two_positive_points(tmp_path):
    cache_dir = tmp_path / "kr-valuation-inputs"
    cache_dir.mkdir()
    cache_path = cache_dir / "005930_KS-2025-2025-valuation-inputs.json"
    cache_path.write_text(
        json.dumps(
            {
                "ticker": "005930.KS",
                "valuation_points": [
                    _forecast_basis_point("005930.KS", 2025, "1100"),
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="at least two positive source-backed"):
        export_deterministic_forecast_csv(
            tickers="005930.KS",
            start_year=2026,
            years=2,
            cases="median",
            snapshot_date="2026-07-01",
            metric_key="adjusted_operating_eps",
            cache_dir=cache_dir,
            out=tmp_path / "forecast_assumptions.csv",
        )


def _forecast_basis_point(ticker: str, fiscal_year: int, value: str) -> dict[str, object]:
    trace = {
        "source": "derived",
        "source_type": "derived_valuation_input",
        "source_document_id": f"derived:kr:{ticker}:{fiscal_year}:valuation-input",
        "filing_id": f"KR_VALUATION_INPUT_{ticker}_{fiscal_year}",
        "form": "derived_valuation_input",
        "period": f"FY{fiscal_year}",
        "fiscal_year": fiscal_year,
        "fiscal_period": "FY",
        "period_start": f"{fiscal_year}-01-01",
        "period_end": f"{fiscal_year}-12-31",
        "available_at": f"{fiscal_year + 1}-04-01T00:00:00+09:00",
        "unit": "KRW/share",
        "currency": "KRW",
        "method": "KR_SOURCE_BACKED_PRICE_EPS_JOIN",
        "formula": "source-backed test valuation point for deterministic forecast export",
        "input_fact_ids": [f"fact:kr:{ticker}:{fiscal_year}:adjusted_operating_eps"],
        "confidence": "0.85",
        "quality_flags": ["source_backed_valuation_input", "source_backed"],
        "quality_status": "source_backed",
        "version": 1,
    }
    return {
        "valuation_point_id": f"valuation:kr:{ticker}:{fiscal_year}:adjusted_operating_eps",
        "entity_id": f"kr:{ticker}",
        "ticker": ticker,
        "fiscal_year": fiscal_year,
        "period": f"FY{fiscal_year}",
        "metric": "adjusted_operating_eps",
        "metric_value": value,
        "price": "70000",
        "currency": "KRW",
        "source_trace": trace,
        "quality_flags": ["source_backed_valuation_input", "source_backed"],
    }


def test_consensus_workpaper_documents_required_evidence_without_numbers(tmp_path):
    csv_path = tmp_path / "consensus_005930.csv"
    out = tmp_path / "consensus_005930_workpaper.md"
    export_consensus_template(
        tickers="005930.KS",
        start_year=2026,
        years=5,
        cases="median",
        snapshot_date="2026-07-01",
        out=csv_path,
    )

    summary = build_consensus_workpaper(
        tickers="005930.KS",
        csv_path=csv_path,
        start_year=2026,
        years=5,
        template_cases="median",
        validation_cases="median,current",
        case_mode="any",
        out=out,
    )

    content = out.read_text(encoding="utf-8")
    assert summary["status"] == "workpaper_created"
    assert summary["csv_validation_status"] == "invalid_input"
    assert summary["csv_import_ready"] is False
    assert summary["policy"] == "operator workpaper only; no generated financial estimates"
    assert "# Consensus Forecast Evidence Workpaper" in content
    assert "`005930.KS`" in content
    assert "2026" in content
    assert "2030" in content
    assert "TODO" in content
    assert "LLM-generated numbers" in content
    assert "export-consensus-template --tickers 005930.KS" in content
    assert "validate-consensus-csv --path" in content
    assert "import-consensus-csv --path" in content
    assert "estimate_eps" in content
    assert "source_url`, `source_document_id`, or `filing_id`" in content
    assert "### Invalid Rows Sample" in content
    assert "row 2: estimate_eps must be a decimal number" in content
    assert "### Missing Required Rows Sample" in content
    assert "| `005930.KS` | 2026 | `median,current` |" in content


def test_consensus_csv_validation_reports_missing_required_forecast_rows(tmp_path):
    csv_path = tmp_path / "consensus_partial.csv"
    csv_path.write_text(
        "ticker,fiscal_year,snapshot_date,estimate_case,estimate_eps,growth_rate_pct,"
        "analyst_count,currency,source,source_url\n"
        "AAPL,2025,2024-12-31,median,6.51,7.0,31,USD,user_csv,https://example.com/aapl.csv\n",
        encoding="utf-8",
    )

    summary = validate_consensus_csv(
        csv_path,
        tickers="AAPL",
        start_year=2025,
        years=2,
        cases="median,current",
        case_mode="any",
    )

    assert summary["status"] == "missing_required_rows"
    assert summary["import_ready"] is False
    assert summary["valid_rows"] == 1
    assert summary["invalid_row_count"] == 0
    assert summary["expected"]["required_rows"] == 2
    assert summary["missing_required_count"] == 1
    assert summary["missing_required_sample"] == [
        {
            "ticker": "AAPL",
            "fiscal_year": 2026,
            "estimate_cases_allowed": ["median", "current"],
        }
    ]
    assert "validate-consensus-csv" in summary["next_commands"][1]


def test_consensus_csv_validation_accepts_median_or_current_case_for_gate(tmp_path):
    csv_path = tmp_path / "consensus_ready.csv"
    csv_path.write_text(
        "ticker,fiscal_year,snapshot_date,estimate_case,estimate_eps,growth_rate_pct,"
        "analyst_count,currency,source,source_url\n"
        "AAPL,2025,2024-12-31,median,6.51,7.0,31,USD,user_csv,https://example.com/aapl-2025.csv\n"
        "AAPL,2026,2024-12-31,current,7.02,7.8,31,USD,user_csv,https://example.com/aapl-2026.csv\n",
        encoding="utf-8",
    )

    summary = validate_consensus_csv(
        csv_path,
        tickers="AAPL",
        start_year=2025,
        years=2,
        cases="median,current",
        case_mode="any",
    )

    assert summary["status"] == "ready"
    assert summary["import_ready"] is True
    assert summary["coverage"]["covered_periods"] == 2
    assert summary["missing_required_count"] == 0
    assert summary["import_summary"]["estimate_cases"] == {"current": 1, "median": 1}


def test_consensus_csv_validation_samples_template_errors_without_trace_values(tmp_path):
    out = tmp_path / "consensus_template.csv"
    export_consensus_template(
        tickers="AAPL",
        start_year=2026,
        years=1,
        cases="median",
        snapshot_date="2026-06-15",
        out=out,
    )

    summary = validate_consensus_csv(
        out,
        tickers="AAPL",
        start_year=2026,
        years=1,
        cases="median,current",
        case_mode="any",
    )

    assert summary["status"] == "invalid_input"
    assert summary["import_ready"] is False
    assert summary["invalid_row_count"] == 1
    assert "estimate_eps" in summary["invalid_rows_sample"][0]["error"]


def test_consensus_csv_validation_reports_missing_file_as_invalid_input(tmp_path):
    missing = tmp_path / "missing_consensus.csv"

    summary = validate_consensus_csv(
        missing,
        tickers="AAPL",
        start_year=2026,
        years=1,
        cases="median,current",
        case_mode="any",
    )

    assert summary["status"] == "invalid_input"
    assert summary["import_ready"] is False
    assert "CSV file not found" in summary["error"]
    assert summary["missing_required_count"] == 1


def test_fnguide_export_import_dry_run_accepts_canonical_csv(tmp_path):
    csv_path = tmp_path / "fnguide_dataguide.csv"
    csv_path.write_text(
        "ticker,name,fiscal_year,metric_key,value,unit,currency\n"
        "005930,삼성전자,2024,매출액,300000000,raw,KRW\n"
        "005930,삼성전자,2024,EPS,5432,per_share,KRW\n",
        encoding="utf-8",
    )

    summary = import_fnguide_export(csv_path, persist=False)

    assert summary["rows"] == 2
    assert summary["metric_rows"] == 2
    assert summary["skipped_rows"] == 0
    assert summary["persisted"] is False
    assert summary["source_type"] == "fnguide_user_export"
    assert summary["tickers"] == ["005930.KS"]
    assert summary["fiscal_years"] == [2024]
    assert summary["metric_keys"] == ["reported_eps", "revenue"]
    assert summary["units"] == ["per_share", "raw"]
    assert summary["currencies"] == ["KRW"]


def test_fnguide_export_import_dry_run_accepts_korean_headers(tmp_path):
    csv_path = tmp_path / "fnguide_korean_headers.csv"
    csv_path.write_text(
        "종목코드,종목명,회계연도,계정명,금액,단위,통화\n"
        "A005930,삼성전자,2024/12,영업이익,\"25,000\",raw,KRW\n"
        "A005930,삼성전자,2024/12,ROE,9.1,percent,KRW\n",
        encoding="utf-8",
    )

    summary = import_fnguide_export(csv_path, persist=False)

    assert summary["rows"] == 2
    assert summary["metric_rows"] == 2
    assert summary["skipped_rows"] == 0


def test_fnguide_export_import_rejects_invalid_ticker(tmp_path):
    csv_path = tmp_path / "fnguide_bad_ticker.csv"
    csv_path.write_text(
        "ticker,name,fiscal_year,metric_key,value,unit,currency\n"
        "SAMSUNG,Samsung Electronics,2024,EPS,5432,per_share,KRW\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid FnGuide/DataGuide metric row"):
        import_fnguide_export(csv_path, persist=False)


def test_fnguide_export_import_rejects_invalid_currency(tmp_path):
    csv_path = tmp_path / "fnguide_bad_currency.csv"
    csv_path.write_text(
        "ticker,name,fiscal_year,metric_key,value,unit,currency\n"
        "005930,Samsung Electronics,2024,EPS,5432,per_share,KRWX\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="currency must be a 3-letter ISO code"):
        import_fnguide_export(csv_path, persist=False)


def test_source_catalog_filters_markets_and_premium_lane():
    payload = source_catalog_payload("KR", include_premium=False)
    ids = {source["id"] for source in payload["sources"]}

    assert "opendart_xbrl" in ids
    assert "pykrx" in ids
    assert "marcap_dataset" in ids
    assert "naver_search_research" in ids
    assert "hankyung_consensus_metadata" in ids
    assert "ecos" in ids
    assert "kosis" in ids
    assert "fnguide_dataguide" not in ids
    assert all("KR" in source["markets"] for source in payload["sources"])

    markdown = render_source_catalog_markdown(payload)
    assert "| Source | Markets | Lane | Priority | Coverage | Raw prefix |" in markdown
    assert "OpenDART" in markdown


def test_data_lake_plan_builds_partitioned_jobs_without_premium_by_default():
    plan = build_data_lake_plan(markets="US,KR", years="2024:2025", partition="annual")
    source_ids = {source["id"] for source in plan["sources"]}
    job_ids = [job["id"] for job in plan["jobs"]]

    assert plan["years"] == {"start": 2024, "end": 2025}
    assert "fnguide_dataguide" not in source_ids
    assert "sec_bulk_companyfacts" in source_ids
    assert "opendart_xbrl" in source_ids
    assert "ecos" in source_ids
    assert "kosis" in source_ids
    assert any("normalize-us-batch" in (job["command"] or "") for job in plan["jobs"])
    assert any("collect-fdr-prices" in (job["command"] or "") for job in plan["jobs"])
    assert any("collect-pykrx-prices" in (job["command"] or "") for job in plan["jobs"])
    assert any("collect-marcap" in (job["command"] or "") for job in plan["jobs"])
    assert any("opendart_xbrl:KR:2024:2024:005930.KS" in job_id for job_id in job_ids)
    assert any("opendart_xbrl:KR:2024:2024:373220.KS" in job_id for job_id in job_ids)
    marcap_commands = [job["command"] or "" for job in plan["jobs"] if job["source_id"] == "marcap_dataset"]
    assert any(
        all(ticker in command for ticker in ("005930.KS", "000660.KS", "373220.KS"))
        for command in marcap_commands
    )
    assert any(job["source_id"] == "ecos" and not job["executable"] for job in plan["jobs"])
    assert any(job["source_id"] == "kosis" and not job["executable"] for job in plan["jobs"])


def test_data_lake_plan_can_include_premium_and_exclude_wrappers():
    plan = build_data_lake_plan(
        markets="KR",
        years="2025:2025",
        include_premium=True,
        include_wrappers=False,
    )
    source_ids = {source["id"] for source in plan["sources"]}
    premium_jobs = [job for job in plan["jobs"] if job["source_id"] == "fnguide_dataguide"]

    assert "fnguide_dataguide" in source_ids
    assert "pykrx" not in source_ids
    assert "finance_data_reader" not in source_ids
    assert premium_jobs
    assert premium_jobs[0]["executable"] is False
    assert premium_jobs[0]["scope"] == "licensed-user-excel-csv"
    assert "import-fnguide-export" in (premium_jobs[0]["command"] or "")


def test_data_lake_plan_markdown_renders_commands():
    plan = build_data_lake_plan(markets="US", years="2024:2024", tickers="AAPL,NVDA")
    markdown = render_data_lake_plan_markdown(plan)

    assert "# Data Lake Ingestion Plan" in markdown
    assert "collect-sec-bulk --archives companyfacts,submissions" in markdown
    assert "load-sec-bulk-warehouse --tickers AAPL,NVDA" in markdown
    assert "normalize-us-batch --tickers AAPL,NVDA" in markdown
    assert "collect-fred --series" in markdown
    assert "collect-stooq-prices --market US --tickers AAPL,NVDA" in markdown
    assert "collect-fdr-prices --market US --tickers AAPL,NVDA" in markdown
    assert "sec_bulk_companyfacts" in markdown


def test_data_lake_plan_markdown_renders_pykrx_commands():
    plan = build_data_lake_plan(markets="KR", years="2024:2024", tickers="005930.KS,000660.KS")
    markdown = render_data_lake_plan_markdown(plan)

    assert "collect-pykrx-prices --tickers 005930.KS,000660.KS" in markdown
    assert "collect-marcap --tickers 005930.KS,000660.KS" in markdown
    assert "collect-research-metadata --market KR --sources naver" in markdown
    assert "collect-research-metadata --market KR --sources hankyung" in markdown
    assert any(job["type"] == "pykrx-daily-ohlcv" for job in plan["jobs"])
    assert any(job["type"] == "marcap-yearly-parquet" for job in plan["jobs"])
    assert any(job["source_id"] == "naver_search_research" for job in plan["jobs"])
    assert any(job["source_id"] == "hankyung_consensus_metadata" for job in plan["jobs"])


def test_naver_research_connector_collects_metadata_only():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Naver-Client-Id"] == "client-id"
        assert request.headers["X-Naver-Client-Secret"] == "client-secret"
        assert request.url.params["query"] == "005930 증권사 리포트 기업분석 컨센서스"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "title": "<b>삼성전자</b> 기업분석 리포트",
                        "link": "https://example.com/report",
                        "description": "metadata only",
                    }
                ]
            },
            request=request,
        )

    connector = NaverResearchSearchConnector(
        client_id="client-id",
        client_secret="client-secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        endpoint="https://openapi.naver.test/v1/search/webkr.json",
    )

    documents = connector.collect(
        ConnectorRequest(ticker="005930.KS", market="KR", start_year=2024, end_year=2025)
    )

    assert len(documents) == 1
    document = documents[0]
    payload = json.loads(document.payload.decode("utf-8"))
    assert document.source == "naver_search_research"
    assert document.metadata["collection_scope"] == "metadata_only"
    assert document.metadata["financial_numbers_allowed"] is False
    assert document.metadata["item_count"] == 1
    assert payload["items"][0]["title"] == "삼성전자 기업분석 리포트"
    assert payload["financial_numbers_allowed"] is False


def test_hankyung_consensus_connector_collects_metadata_only():
    html = """
    <html>
      <body>
        <a href="/analysis/report/123">Samsung Electronics analysis report</a>
        <a href="/notice">notice</a>
      </body>
    </html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["search_text"] == "005930"
        return httpx.Response(200, text=html, request=request)

    connector = HankyungConsensusMetadataConnector(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        base_url="https://consensus.hankyung.test/analysis/list",
    )

    documents = connector.collect(
        ConnectorRequest(ticker="A005930", market="KR", start_year=2024, end_year=2025)
    )

    assert len(documents) == 1
    document = documents[0]
    payload = json.loads(document.payload.decode("utf-8"))
    assert document.source == "hankyung_consensus_metadata"
    assert document.metadata["collection_scope"] == "metadata_only"
    assert document.metadata["financial_numbers_allowed"] is False
    assert document.metadata["item_count"] == 1
    assert payload["items"][0]["title"] == "Samsung Electronics analysis report"
    assert payload["financial_numbers_allowed"] is False


def test_research_metadata_collect_reports_failures_without_numbers():
    summary = collect_research_metadata(
        "005930.KS",
        "KR",
        "unknown",
        2024,
        2025,
        persist=False,
        continue_on_error=True,
    )

    assert summary["status"] == "failed"
    assert summary["documents"] == []
    assert summary["failures"][0]["source"] == "unknown"
    assert summary["policy"] == "metadata_only_no_financial_numbers"


def test_data_lake_plan_markdown_renders_jquants_commands():
    plan = build_data_lake_plan(markets="JP", years="2024:2024", tickers="7203.T,6758.T")
    markdown = render_data_lake_plan_markdown(plan)

    assert "collect-jquants --tickers 7203.T,6758.T" in markdown
    assert any(job["type"] == "jquants-statements-prices-dividends" for job in plan["jobs"])
    assert "collect-edinet --tickers 7203.T,6758.T" in markdown
    assert any(job["type"] == "edinet-xbrl-csv-filings" for job in plan["jobs"])
    assert "collect-estat --stats-data-ids <statsDataId,...>" in markdown
    assert any(job["source_id"] == "estat" and not job["executable"] for job in plan["jobs"])


def test_sec_bulk_connector_collects_companyfacts_and_submissions_zip():
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["User-Agent"] == "PersonalFastGraphs/0.1 test@example.com"
        seen_paths.append(request.url.path)
        return httpx.Response(
            200,
            content=b"PK\x03\x04sec-bulk-fixture",
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    connector = SecBulkConnector(
        user_agent="PersonalFastGraphs/0.1 test@example.com",
        client=client,
        archive_urls={
            "companyfacts": "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip",
            "submissions": (
                "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"
            ),
        },
    )

    documents = connector.collect_bulk(["companyfacts", "submissions"])

    assert [document.metadata["archive"] for document in documents] == [
        "companyfacts",
        "submissions",
    ]
    assert documents[0].source == "sec_bulk"
    assert documents[0].ticker == "BULK"
    assert documents[0].content_type == "application/zip"
    assert documents[0].payload.startswith(b"PK")
    assert "/xbrl/companyfacts.zip" in seen_paths[0]
    assert "/bulkdata/submissions.zip" in seen_paths[1]


def test_collect_sec_bulk_archives_dry_run_uses_connector(monkeypatch):
    class FakeSecBulkConnector:
        def collect_bulk(
            self,
            archives: list[str],
            *,
            force_refresh: bool = False,
        ) -> list[ConnectorDocument]:
            assert force_refresh is True
            return [
                ConnectorDocument(
                    source="sec_bulk",
                    market="US",
                    ticker="BULK",
                    identifier=f"sec-bulk-{archive}",
                    url=f"https://www.sec.gov/{archive}.zip",
                    payload=b"PK\x03\x04" + archive.encode("utf-8"),
                    content_type="application/zip",
                    metadata={"archive": archive, "content_length": len(archive) + 4},
                )
                for archive in archives
            ]

    monkeypatch.setattr("services.ingestion_worker.cli.SecBulkConnector", FakeSecBulkConnector)

    summary = collect_sec_bulk_archives(
        "companyfacts,submissions",
        persist=False,
        force_refresh=True,
    )

    assert summary["status"] == "ok"
    assert summary["market"] == "US"
    assert summary["archives"] == ["companyfacts", "submissions"]
    assert summary["zip_archives"] == 2
    assert summary["bytes"] > 0
    assert summary["persisted"] == []


def test_load_sec_bulk_warehouse_dry_run_extracts_companyfacts_metrics(tmp_path):
    companyfacts_zip, submissions_zip = _sec_bulk_fixture_zips(tmp_path)

    summary = load_sec_bulk_warehouse(
        companyfacts_zip=companyfacts_zip,
        submissions_zip=submissions_zip,
        tickers="AAPL",
        persist=False,
    )

    assert summary["status"] == "ok"
    assert summary["companies"] == ["AAPL"]
    assert summary["financial_facts"] == 8
    assert summary["metric_values"] == 16
    assert summary["persisted"] == {
        "financial_facts": 0,
        "metric_values": 0,
        "source_documents": 0,
    }

    from services.ingestion_worker.sec_bulk_warehouse import (
        derived_metric_rows,
        parse_companyfacts_zip,
        parse_submissions_zip,
        primary_metric_rows,
    )

    submissions = parse_submissions_zip(submissions_zip)
    rows = parse_companyfacts_zip(companyfacts_zip, submissions=submissions, tickers=["AAPL"])
    eps_row = next(row for row in rows if row.metric_key == "reported_eps_diluted")
    assert (
        eps_row.source_trace["formula"]
        == "SEC companyfacts us-gaap:EarningsPerShareDiluted reported XBRL fact"
    )
    assert eps_row.source_trace["method"] == "SEC_COMPANYFACTS_BULK"
    primary_rows = primary_metric_rows(rows)
    derived_rows = derived_metric_rows(primary_rows)
    derived_by_key = {row.metric_key: row for row in derived_rows}
    assert set(derived_by_key) == {
        "basic_eps",
        "diluted_eps",
        "ebit_share",
        "ebitda_share",
        "fcf_share",
        "operating_cash_flow_share",
        "revenue_share",
        "sales_share",
    }
    assert derived_by_key["basic_eps"].value == Decimal("6.11")
    assert derived_by_key["diluted_eps"].value == Decimal("6.08")
    assert (
        derived_by_key["sales_share"].source_trace["formula"]
        == "revenue_reported / diluted_shares"
    )
    assert (
        derived_by_key["fcf_share"].source_trace["formula"]
        == "free_cash_flow = (operating_cash_flow_reported - abs(capex_reported)) / diluted_shares"
    )
    assert derived_by_key["fcf_share"].source_trace["input_fact_ids"]
    assert derived_by_key["ebitda_share"].source_trace["quality_flags"] == [
        "ebitda_xbrl_reconstructed_from_operating_income_plus_dda"
    ]


def _sec_bulk_fixture_zips(tmp_path: Path) -> tuple[Path, Path]:
    submissions_zip = tmp_path / "submissions.zip"
    companyfacts_zip = tmp_path / "companyfacts.zip"
    with zipfile.ZipFile(submissions_zip, "w") as archive:
        archive.writestr(
            "CIK0000320193.json",
            json.dumps(
                {
                    "cik": "0000320193",
                    "name": "Apple Inc.",
                    "tickers": ["AAPL"],
                    "exchanges": ["Nasdaq"],
                }
            ),
        )
    fact_payload = {
        "cik": 320193,
        "entityName": "Apple Inc.",
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "label": "Revenue",
                    "units": {
                        "USD": [
                            {
                                "fy": 2024,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2024-11-01",
                                "start": "2023-10-01",
                                "end": "2024-09-28",
                                "val": 391035000000,
                                "accn": "0000320193-24-000123",
                                "frame": "CY2024",
                            }
                        ]
                    },
                },
                "EarningsPerShareDiluted": {
                    "label": "Earnings Per Share Diluted",
                    "units": {
                        "USD/shares": [
                            {
                                "fy": 2024,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2024-11-01",
                                "start": "2023-10-01",
                                "end": "2024-09-28",
                                "val": 6.08,
                                "accn": "0000320193-24-000123",
                                "frame": "CY2024",
                            }
                        ]
                    },
                },
                "EarningsPerShareBasic": {
                    "label": "Earnings Per Share Basic",
                    "units": {
                        "USD/shares": [
                            {
                                "fy": 2024,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2024-11-01",
                                "start": "2023-10-01",
                                "end": "2024-09-28",
                                "val": 6.11,
                                "accn": "0000320193-24-000123",
                                "frame": "CY2024",
                            }
                        ]
                    },
                },
                "WeightedAverageNumberOfDilutedSharesOutstanding": {
                    "label": "Weighted Average Diluted Shares",
                    "units": {
                        "shares": [
                            {
                                "fy": 2024,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2024-11-01",
                                "start": "2023-10-01",
                                "end": "2024-09-28",
                                "val": 15408000000,
                                "accn": "0000320193-24-000123",
                                "frame": "CY2024",
                            }
                        ]
                    },
                },
                "NetCashProvidedByUsedInOperatingActivities": {
                    "label": "Operating Cash Flow",
                    "units": {
                        "USD": [
                            {
                                "fy": 2024,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2024-11-01",
                                "start": "2023-10-01",
                                "end": "2024-09-28",
                                "val": 122151000000,
                                "accn": "0000320193-24-000123",
                                "frame": "CY2024",
                            }
                        ]
                    },
                },
                "PaymentsToAcquirePropertyPlantAndEquipment": {
                    "label": "Capital Expenditures",
                    "units": {
                        "USD": [
                            {
                                "fy": 2024,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2024-11-01",
                                "start": "2023-10-01",
                                "end": "2024-09-28",
                                "val": 9447000000,
                                "accn": "0000320193-24-000123",
                                "frame": "CY2024",
                            }
                        ]
                    },
                },
                "OperatingIncomeLoss": {
                    "label": "Operating Income",
                    "units": {
                        "USD": [
                            {
                                "fy": 2024,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2024-11-01",
                                "start": "2023-10-01",
                                "end": "2024-09-28",
                                "val": 123216000000,
                                "accn": "0000320193-24-000123",
                                "frame": "CY2024",
                            }
                        ]
                    },
                },
                "DepreciationDepletionAndAmortization": {
                    "label": "Depreciation Depletion and Amortization",
                    "units": {
                        "USD": [
                            {
                                "fy": 2024,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2024-11-01",
                                "start": "2023-10-01",
                                "end": "2024-09-28",
                                "val": 11445000000,
                                "accn": "0000320193-24-000123",
                                "frame": "CY2024",
                            }
                        ]
                    },
                },
            }
        },
    }
    with zipfile.ZipFile(companyfacts_zip, "w") as archive:
        archive.writestr("CIK0000320193.json", json.dumps(fact_payload))
    return companyfacts_zip, submissions_zip


def test_fred_connector_collects_observations_with_metadata():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/series/observations"):
            return httpx.Response(
                200,
                json={
                    "observations": [
                        {
                            "date": "2024-01-01",
                            "value": "0",
                            "realtime_start": "2024-01-01",
                            "realtime_end": "2024-01-01",
                        },
                        {
                            "date": "2024-02-01",
                            "value": "1",
                            "realtime_start": "2024-02-01",
                            "realtime_end": "2024-02-01",
                        },
                    ]
                },
                request=request,
            )
        return httpx.Response(
            200,
            json={"seriess": [{"id": "USREC", "units_short": "Index", "frequency_short": "M"}]},
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    documents = FredConnector(api_key="test-key", client=client).collect(
        ConnectorRequest(ticker="USREC", market="GLOBAL", start_year=2024, end_year=2024)
    )
    payload = json.loads(documents[0].payload.decode("utf-8"))

    assert documents[0].source == "fred"
    assert documents[0].identifier == "USREC-2024-2024-observations"
    assert documents[0].metadata["series_id"] == "USREC"
    assert "test-key" not in str(documents[0].url)
    assert "test-key" not in documents[0].metadata["series_url"]
    assert payload["series"][0]["id"] == "USREC"
    assert len(payload["observations"]) == 2


def test_collect_fred_series_dry_run_uses_connector(monkeypatch):
    class FakeFredConnector:
        def collect(self, request: ConnectorRequest) -> list[ConnectorDocument]:
            payload = {
                "series_id": request.ticker,
                "series": [{"id": request.ticker, "units_short": "Pct", "frequency_short": "D"}],
                "observations": [{"date": "2024-01-01", "value": "4.25"}],
            }
            return [
                ConnectorDocument(
                    source="fred",
                    market="GLOBAL",
                    ticker=request.ticker,
                    identifier=f"{request.ticker}-fixture",
                    url="https://api.stlouisfed.org/fred/series/observations",
                    payload=json.dumps(payload).encode("utf-8"),
                    content_type="application/json",
                    metadata={"series_id": request.ticker},
                )
            ]

    monkeypatch.setattr("services.ingestion_worker.cli.FredConnector", FakeFredConnector)

    summary = collect_fred_series("DGS10,USREC", 2024, 2024, persist=False)

    assert summary["status"] == "ok"
    assert summary["series"] == ["DGS10", "USREC"]
    assert summary["observation_count"] == 2
    assert summary["persisted"] == []


def test_ecos_connector_collects_statistic_search_json():
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        assert "/StatisticSearch/ecos-key/json/kr/1/100000/722Y001/M/202401/202412/0101000" in url
        return httpx.Response(
            200,
            json={"StatisticSearch": {"row": [{"TIME": "202401", "DATA_VALUE": "1350.0"}]}},
            request=request,
        )

    documents = EcosConnector(
        api_key="ecos-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    ).collect(
        ConnectorRequest(
            ticker="722Y001:M:0101000",
            market="KR",
            start_year=2024,
            end_year=2024,
        )
    )
    payload = json.loads(documents[0].payload.decode("utf-8"))

    assert documents[0].source == "ecos"
    assert documents[0].metadata["source_type"] == "ecos_official_api"
    assert documents[0].metadata["period_start"] == "202401"
    assert "ecos-key" not in str(documents[0].url)
    assert payload["StatisticSearch"]["row"][0]["DATA_VALUE"] == "1350.0"
    observations = normalize_official_stat_document(documents[0])
    assert observations[0].series_id.startswith("ECOS:722Y001:0101000")
    assert observations[0].observation_date.isoformat() == "2024-01-01"
    assert observations[0].value == Decimal("1350.0")
    assert observations[0].source_trace["method"] == "ECOS_STATISTIC_SEARCH"
    industry_payload = _official_stat_industry_payload(documents[0], observations[0])
    assert industry_payload is not None
    assert industry_payload["market"] == "KR"
    assert industry_payload["category"] == "official_kr_macro_industry"
    assert industry_payload["series_id"].startswith("IND:ECOS:722Y001:0101000")


def test_kosis_connector_collects_statistics_data_json():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/statisticsData.do")
        assert request.url.params["orgId"] == "101"
        assert request.url.params["tblId"] == "DT_TEST"
        assert request.url.params["startPrdDe"] == "2024"
        return httpx.Response(
            200,
            json=[
                {
                    "PRD_DE": "2024",
                    "PRD_SE": "Y",
                    "C1_NM": "Manufacturing",
                    "UNIT_NM": "Index",
                    "DT": "100",
                }
            ],
            request=request,
        )

    documents = KosisConnector(
        api_key="kosis-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    ).collect(ConnectorRequest(ticker="101:DT_TEST", market="KR", start_year=2024, end_year=2024))

    assert documents[0].source == "kosis"
    assert documents[0].metadata["org_id"] == "101"
    assert documents[0].metadata["tbl_id"] == "DT_TEST"
    assert "kosis-key" not in str(documents[0].url)
    assert _json_payload_row_count_for_test(documents[0]) == 1
    observations = normalize_official_stat_document(documents[0])
    assert observations[0].series_id.startswith("KOSIS:101:DT_TEST:")
    assert observations[0].observation_date.isoformat() == "2024-01-01"
    assert observations[0].value == Decimal("100")
    assert observations[0].source_trace["dimensions"] == {"C1_NM": "Manufacturing"}
    industry_payload = _official_stat_industry_payload(documents[0], observations[0])
    assert industry_payload is not None
    assert industry_payload["market"] == "KR"
    assert industry_payload["category"] == "official_kr_statistics"
    assert industry_payload["industry"] == "Manufacturing"
    assert industry_payload["dimensions"]["tbl_id"] == "DT_TEST"


def test_estat_connector_collects_stats_data_json():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/getStatsData")
        assert request.url.params["statsDataId"] == "C0020050213000"
        return httpx.Response(
            200,
            json={
                "GET_STATS_DATA": {
                    "STATISTICAL_DATA": {
                        "DATA_INF": {"VALUE": [{"@time": "2024", "$": "12.3"}]}
                    }
                }
            },
            request=request,
        )

    documents = EStatConnector(
        app_id="estat-app-id",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    ).collect(
        ConnectorRequest(ticker="C0020050213000", market="JP", start_year=2024, end_year=2024)
    )

    assert documents[0].source == "estat"
    assert documents[0].metadata["stats_data_id"] == "C0020050213000"
    assert "estat-app-id" not in str(documents[0].url)
    assert _json_payload_row_count_for_test(documents[0]) == 1
    observations = normalize_official_stat_document(documents[0])
    assert observations[0].series_id.startswith("ESTAT:C0020050213000:")
    assert observations[0].observation_date.isoformat() == "2024-01-01"
    assert observations[0].value == Decimal("12.3")
    assert observations[0].source_trace["method"] == "ESTAT_GET_STATS_DATA"
    industry_payload = _official_stat_industry_payload(documents[0], observations[0])
    assert industry_payload is not None
    assert industry_payload["market"] == "JP"
    assert industry_payload["category"] == "official_jp_statistics"
    assert industry_payload["industry"] == "C0020050213000"


def test_collect_official_statistics_dry_runs_use_connectors(monkeypatch):
    class FakeEcosConnector:
        def collect(self, request: ConnectorRequest) -> list[ConnectorDocument]:
            return [
                _json_doc(
                    "ecos",
                    "KR",
                    request.ticker,
                    {"StatisticSearch": {"row": [{"TIME": "2024", "DATA_VALUE": "1"}]}},
                    {"stat_code": "722Y001", "item_code": "0101000", "cycle": "A"},
                )
            ]

    class FakeKosisConnector:
        def collect(self, request: ConnectorRequest) -> list[ConnectorDocument]:
            return [
                _json_doc(
                    "kosis",
                    "KR",
                    request.ticker,
                    [{"PRD_DE": "2024", "PRD_SE": "Y", "C1_NM": "Total", "DT": "1"}],
                    {"org_id": "101", "tbl_id": "DT_TEST"},
                )
            ]

    class FakeEStatConnector:
        def collect(self, request: ConnectorRequest) -> list[ConnectorDocument]:
            return [
                _json_doc(
                    "estat",
                    "JP",
                    request.ticker,
                    {
                        "GET_STATS_DATA": {
                            "STATISTICAL_DATA": {
                                "DATA_INF": {"VALUE": [{"@time": "2024", "$": "1"}]}
                            }
                        }
                    },
                    {"stats_data_id": "C0020050213000"},
                )
            ]

    monkeypatch.setattr("services.ingestion_worker.cli.EcosConnector", FakeEcosConnector)
    monkeypatch.setattr("services.ingestion_worker.cli.KosisConnector", FakeKosisConnector)
    monkeypatch.setattr("services.ingestion_worker.cli.EStatConnector", FakeEStatConnector)

    ecos = collect_ecos_series("722Y001:M:0101000", 2024, 2024, persist=False)
    kosis = collect_kosis_tables("101:DT_TEST", 2024, 2024, persist=False)
    estat = collect_estat_tables("C0020050213000", 2024, 2024, persist=False)

    assert ecos["observation_count"] == 1
    assert kosis["observation_count"] == 1
    assert estat["observation_count"] == 1
    assert ecos["normalized_observation_count"] == 1
    assert kosis["normalized_observation_count"] == 1
    assert estat["normalized_observation_count"] == 1
    assert ecos["persisted"] == kosis["persisted"] == estat["persisted"] == []


def _json_doc(
    source: str,
    market: str,
    ticker: str,
    payload: object,
    metadata: dict | None = None,
) -> ConnectorDocument:
    return ConnectorDocument(
        source=source,
        market=market,
        ticker=ticker,
        identifier=f"{ticker}-fixture",
        url=f"https://example.com/{source}",
        payload=json.dumps(payload).encode("utf-8"),
        content_type="application/json",
        metadata={"source_type": f"{source}_official_api"} | (metadata or {}),
    )


def _json_payload_row_count_for_test(document: ConnectorDocument) -> int:
    payload = json.loads(document.payload.decode("utf-8"))
    if isinstance(payload, list):
        return len(payload)
    ecos_rows = payload.get("StatisticSearch")
    if isinstance(ecos_rows, dict) and isinstance(ecos_rows.get("row"), list):
        return len(ecos_rows["row"])
    estat_data = payload.get("GET_STATS_DATA")
    if isinstance(estat_data, dict):
        values = (
            estat_data.get("STATISTICAL_DATA", {})
            .get("DATA_INF", {})
            .get("VALUE", [])
        )
        if isinstance(values, list):
            return len(values)
    return 0


def test_fred_recession_periods_handles_closed_and_open_periods():
    from datetime import date

    periods = _fred_recession_periods(
        [
            {"date": "2024-01-01", "value": "0"},
            {"date": "2024-02-01", "value": "1"},
            {"date": "2024-03-01", "value": "1"},
            {"date": "2024-04-01", "value": "0"},
            {"date": "2024-05-01", "value": "1"},
        ]
    )

    assert periods[0] == (date(2024, 2, 1), date(2024, 3, 1))
    assert periods[1] == (date(2024, 5, 1), None)


def test_stooq_connector_collects_daily_csv():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["s"] == "aapl.us"
        assert request.url.params["i"] == "d"
        return httpx.Response(
            200,
            text="Date,Open,High,Low,Close,Volume\n2024-01-02,180,182,179,181.50,1000\n",
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    documents = StooqConnector(client=client).collect(
        ConnectorRequest(ticker="AAPL", market="US", start_year=2024, end_year=2024)
    )

    assert documents[0].source == "stooq"
    assert documents[0].ticker == "AAPL"
    assert documents[0].metadata["stooq_symbol"] == "aapl.us"
    assert b"2024-01-02" in documents[0].payload


def test_collect_stooq_prices_dry_run_uses_connector(monkeypatch):
    class FakeStooqConnector:
        def collect(self, request: ConnectorRequest) -> list[ConnectorDocument]:
            return [
                ConnectorDocument(
                    source="stooq",
                    market=request.market,
                    ticker=request.ticker,
                    identifier=f"{request.ticker}-fixture",
                    url="https://stooq.com/q/d/l/?s=aapl.us&i=d",
                    payload=(
                        b"Date,Open,High,Low,Close,Volume\n"
                        b"2024-01-02,180,182,179,181.50,1000\n"
                    ),
                    content_type="text/csv",
                    metadata={"stooq_symbol": "aapl.us"},
                )
            ]

    monkeypatch.setattr("services.ingestion_worker.cli.StooqConnector", FakeStooqConnector)

    summary = collect_stooq_prices("AAPL,NVDA", "US", 2024, 2024, persist=False)

    assert summary["status"] == "ok"
    assert summary["tickers"] == ["AAPL", "NVDA"]
    assert summary["price_rows"] == 2
    assert summary["persisted"] == []


def test_finance_data_reader_connector_collects_daily_csv():
    class FakeFdr:
        @staticmethod
        def DataReader(symbol: str, start: str, end: str):
            assert symbol == "AAPL"
            assert start == "2024-01-01"
            assert end == "2024-12-31"
            return pd.DataFrame(
                [
                    {
                        "Open": 180.0,
                        "High": 182.0,
                        "Low": 179.0,
                        "Close": 181.5,
                        "Volume": 1000,
                        "Change": 0.01,
                    }
                ],
                index=pd.to_datetime(["2024-01-02"]),
            )

    documents = FinanceDataReaderConnector(fdr_module=FakeFdr()).collect(
        ConnectorRequest(ticker="AAPL", market="US", start_year=2024, end_year=2024)
    )

    assert documents[0].source == "finance_data_reader"
    assert documents[0].ticker == "AAPL"
    assert documents[0].metadata["fdr_symbol"] == "AAPL"
    assert b"2024-01-02" in documents[0].payload
    assert b"181.5" in documents[0].payload


def test_collect_fdr_prices_dry_run_uses_connector(monkeypatch):
    class FakeFdrConnector:
        def collect(self, request: ConnectorRequest) -> list[ConnectorDocument]:
            return [
                ConnectorDocument(
                    source="finance_data_reader",
                    market=request.market,
                    ticker=request.ticker,
                    identifier=f"{request.ticker}-fixture",
                    url="https://github.com/financedata/financedatareader",
                    payload=(
                        b"date,Open,High,Low,Close,Volume,Change\n"
                        b"2024-01-02,180,182,179,181.50,1000,0.01\n"
                    ),
                    content_type="text/csv",
                    metadata={"fdr_symbol": request.ticker},
                )
            ]

    monkeypatch.setattr(
        "services.ingestion_worker.cli.FinanceDataReaderConnector",
        FakeFdrConnector,
    )

    summary = collect_fdr_prices("AAPL,NVDA", "US", 2024, 2024, persist=False)

    assert summary["status"] == "ok"
    assert summary["market"] == "US"
    assert summary["tickers"] == ["AAPL", "NVDA"]
    assert summary["price_rows"] == 2
    assert summary["persisted"] == []


def test_pykrx_connector_collects_daily_ohlcv_csv():
    class FakeStock:
        def get_market_ohlcv(self, start: str, end: str, ticker: str):
            assert start == "20240101"
            assert end == "20241231"
            assert ticker == "005930"
            return pd.DataFrame(
                [
                    {
                        "시가": 70000,
                        "고가": 71000,
                        "저가": 69000,
                        "종가": 70500,
                        "거래량": 12345,
                        "거래대금": 870000000,
                        "등락률": 1.2,
                    }
                ],
                index=pd.to_datetime(["2024-01-02"]),
            )

    documents = PyKrxConnector(stock_module=FakeStock()).collect(
        ConnectorRequest(ticker="005930.KS", market="KR", start_year=2024, end_year=2024)
    )
    payload = documents[0].payload.decode("utf-8-sig")

    assert documents[0].source == "pykrx"
    assert documents[0].market == "KR"
    assert documents[0].ticker == "005930.KS"
    assert documents[0].metadata["krx_code"] == "005930"
    assert documents[0].metadata["endpoint"] == "get_market_ohlcv"
    assert "종가" in payload
    assert "70500" in payload


def test_pykrx_connector_collects_daily_fundamental_csv():
    class FakeStock:
        def get_market_fundamental_by_date(self, start: str, end: str, ticker: str):
            assert start == "20240101"
            assert end == "20241231"
            assert ticker == "005930"
            return pd.DataFrame(
                [
                    {
                        "BPS": 52000,
                        "PER": 12.5,
                        "PBR": 1.1,
                        "EPS": 3164,
                        "DPS": 1444,
                        "DIV": 2.7,
                    }
                ],
                index=pd.to_datetime(["2024-12-30"]),
            )

    documents = PyKrxConnector(stock_module=FakeStock()).collect_fundamentals(
        ConnectorRequest(ticker="005930.KS", market="KR", start_year=2024, end_year=2024)
    )
    payload = documents[0].payload.decode("utf-8-sig")

    assert documents[0].source == "pykrx"
    assert documents[0].market == "KR"
    assert documents[0].ticker == "005930.KS"
    assert documents[0].identifier == "005930-2024-2024-fundamental"
    assert documents[0].metadata["endpoint"] == "get_market_fundamental_by_date"
    assert "DPS" in payload
    assert "1444" in payload


def test_collect_pykrx_prices_dry_run_uses_connector(monkeypatch):
    class FakePyKrxConnector:
        def collect(self, request: ConnectorRequest) -> list[ConnectorDocument]:
            return [
                ConnectorDocument(
                    source="pykrx",
                    market="KR",
                    ticker=request.ticker,
                    identifier=f"{request.ticker}-fixture",
                    url="https://github.com/sharebook-kr/pykrx",
                    payload=(
                        "date,시가,고가,저가,종가,거래량,거래대금,등락률\n"
                        "2024-01-02,70000,71000,69000,70500,12345,870000000,1.2\n"
                    ).encode("utf-8-sig"),
                    content_type="text/csv",
                    metadata={"krx_code": request.ticker.split(".", 1)[0]},
                )
            ]

    monkeypatch.setattr("services.ingestion_worker.cli.PyKrxConnector", FakePyKrxConnector)
    monkeypatch.setattr(
        "services.ingestion_worker.cli._write_raw_document",
        lambda document: (Path(f"storage/raw/pykrx/{document.identifier}.csv"), f"hash-{document.identifier}"),
    )

    summary = collect_pykrx_prices(
        "005930.KS,000660.KS",
        2024,
        2024,
        persist=False,
        sleep_seconds=0,
    )

    assert summary["status"] == "ok"
    assert summary["market"] == "KR"
    assert summary["tickers"] == ["005930.KS", "000660.KS"]
    assert summary["price_rows"] == 2
    assert summary["persisted"] == []
    assert [item["identifier"] for item in summary["raw_documents"]] == [
        "005930.KS-fixture",
        "000660.KS-fixture",
    ]


def test_collect_pykrx_fundamentals_dry_run_uses_connector(monkeypatch):
    class FakePyKrxConnector:
        def collect_fundamentals(self, request: ConnectorRequest) -> list[ConnectorDocument]:
            return [
                ConnectorDocument(
                    source="pykrx",
                    market="KR",
                    ticker=request.ticker,
                    identifier=f"{request.ticker}-fundamental-fixture",
                    url="https://github.com/sharebook-kr/pykrx",
                    payload=(
                        "date,BPS,PER,PBR,EPS,DPS,DIV\n"
                        "2024-12-30,52000,12.5,1.1,3164,1444,2.7\n"
                    ).encode("utf-8-sig"),
                    content_type="text/csv",
                    metadata={
                        "krx_code": request.ticker.split(".", 1)[0],
                        "endpoint": "get_market_fundamental_by_date",
                    },
                )
            ]

    monkeypatch.setattr("services.ingestion_worker.cli.PyKrxConnector", FakePyKrxConnector)
    monkeypatch.setattr(
        "services.ingestion_worker.cli._write_raw_document",
        lambda document: (Path(f"storage/raw/pykrx/{document.identifier}.csv"), f"hash-{document.identifier}"),
    )

    summary = collect_pykrx_fundamentals(
        "005930.KS,000660.KS",
        2024,
        2024,
        persist=False,
        sleep_seconds=0,
    )

    assert summary["status"] == "ok"
    assert summary["market"] == "KR"
    assert summary["tickers"] == ["005930.KS", "000660.KS"]
    assert summary["fundamental_rows"] == 2
    assert summary["persisted"] == []
    assert [item["identifier"] for item in summary["raw_documents"]] == [
        "005930.KS-fundamental-fixture",
        "000660.KS-fundamental-fixture",
    ]


def test_collect_opendart_dividends_dry_run_uses_connector(monkeypatch):
    class FakeOpenDartConnector:
        def collect_dividends(self, request: ConnectorRequest) -> list[ConnectorDocument]:
            return [
                ConnectorDocument(
                    source="opendart_dividends",
                    market="KR",
                    ticker=request.ticker,
                    identifier=f"{request.ticker}-2024-alotMatter",
                    url="https://opendart.fss.or.kr/api/alotMatter.json?crtfc_key=REDACTED",
                    payload=json.dumps(
                        {
                            "status": "000",
                            "message": "OK",
                            "bsns_year": "2024",
                            "list": [
                                {
                                    "se": "\uc8fc\ub2f9 \ud604\uae08\ubc30\ub2f9\uae08(\uc6d0)",
                                    "stock_knd": "\ubcf4\ud1b5\uc8fc",
                                    "thstrm": "1,444",
                                }
                            ],
                        }
                    ).encode("utf-8"),
                    content_type="application/json",
                    metadata={"endpoint": "alotMatter", "bsns_year": 2024},
                )
            ]

    monkeypatch.setattr("services.ingestion_worker.cli.OpenDartConnector", FakeOpenDartConnector)
    monkeypatch.setattr(
        "services.ingestion_worker.cli._write_raw_document",
        lambda document: (
            Path(f"storage/raw/opendart_dividends/{document.identifier}.json"),
            f"hash-{document.identifier}",
        ),
    )

    summary = collect_opendart_dividends(
        "005930.KS,000660.KS",
        2024,
        2024,
        persist=False,
        sleep_seconds=0,
    )

    assert summary["status"] == "ok"
    assert summary["market"] == "KR"
    assert summary["tickers"] == ["005930.KS", "000660.KS"]
    assert summary["dividend_rows"] == 2
    assert summary["persisted"] == []
    assert [item["source"] for item in summary["raw_documents"]] == [
        "opendart_dividends",
        "opendart_dividends",
    ]


def test_collect_market_documents_dry_run_caches_opendart_raw(monkeypatch):
    class FakeOpenDartConnector:
        def collect(self, request: ConnectorRequest) -> list[ConnectorDocument]:
            assert request.ticker == "005930.KS"
            assert request.market == "KR"
            assert request.start_year == 2024
            assert request.end_year == 2024
            return [
                ConnectorDocument(
                    source="opendart",
                    market="KR",
                    ticker=request.ticker,
                    identifier="00126380-2024-11011-CFS",
                    url=(
                        "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
                        "?crtfc_key=REDACTED"
                    ),
                    payload=(
                        b'{"status":"000","list":[{"account_nm":"EPS",'
                        b'"thstrm_amount":"1000"}]}'
                    ),
                    content_type="application/json",
                    metadata={"corp_code": "00126380", "bsns_year": 2024},
                )
            ]

    monkeypatch.setattr("services.ingestion_worker.cli.OpenDartConnector", FakeOpenDartConnector)
    monkeypatch.setattr(
        "services.ingestion_worker.cli._write_raw_document",
        lambda document: (
            Path(f"storage/raw/opendart/{document.identifier}.json"),
            f"hash-{document.identifier}",
        ),
    )

    summary = collect_market_documents("KR", "005930.KS", 2024, 2024, persist=False)

    assert summary["status"] == "ok"
    assert summary["persisted"] == []
    assert summary["raw_documents"] == [
        {
            "source": "opendart",
            "ticker": "005930.KS",
            "identifier": "00126380-2024-11011-CFS",
            "local_path": str(Path("storage/raw/opendart/00126380-2024-11011-CFS.json")),
            "content_hash": "hash-00126380-2024-11011-CFS",
        }
    ]


def test_marcap_connector_collects_yearly_parquet():
    payload = _marcap_parquet_payload()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/marcap-2024.parquet")
        return httpx.Response(200, content=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    documents = MarcapConnector(client=client).collect(
        ConnectorRequest(ticker="KR_MARKET", market="KR", start_year=2024, end_year=2024)
    )

    assert documents[0].source == "marcap"
    assert documents[0].market == "KR"
    assert documents[0].ticker == "KR_MARKET"
    assert documents[0].identifier == "marcap-2024"
    assert documents[0].content_type == "application/vnd.apache.parquet"
    assert documents[0].payload.startswith(b"PAR1")
    assert documents[0].metadata["year"] == 2024


def test_collect_marcap_data_dry_run_uses_connector(monkeypatch):
    class FakeMarcapConnector:
        def collect(self, request: ConnectorRequest) -> list[ConnectorDocument]:
            assert request.ticker == "KR_MARKET"
            assert request.market == "KR"
            assert request.start_year == 2024
            assert request.end_year == 2024
            return [
                ConnectorDocument(
                    source="marcap",
                    market="KR",
                    ticker="KR_MARKET",
                    identifier="marcap-2024",
                    url="https://raw.githubusercontent.com/FinanceData/marcap/master/data/marcap-2024.parquet",
                    payload=_marcap_parquet_payload(),
                    content_type="application/vnd.apache.parquet",
                    metadata={"year": 2024, "format": "parquet"},
                )
            ]

    monkeypatch.setattr("services.ingestion_worker.cli.MarcapConnector", FakeMarcapConnector)
    monkeypatch.setattr(
        "services.ingestion_worker.cli._write_raw_document",
        lambda document: (Path(f"storage/raw/marcap/{document.identifier}.parquet"), f"hash-{document.identifier}"),
    )

    summary = collect_marcap_data("005930.KS,000660.KS", 2024, 2024, persist=False)

    assert summary["status"] == "ok"
    assert summary["market"] == "KR"
    assert summary["tickers"] == ["005930.KS", "000660.KS"]
    assert summary["price_rows"] == 2
    assert summary["persisted"] == []
    assert summary["raw_documents"] == [
        {
            "source": "marcap",
            "ticker": "KR_MARKET",
            "identifier": "marcap-2024",
            "local_path": str(Path("storage/raw/marcap/marcap-2024.parquet")),
            "content_hash": "hash-marcap-2024",
        }
    ]


def test_inspect_raw_kr_evidence_reads_cached_pykrx_and_marcap(tmp_path):
    pykrx_dir = tmp_path / "pykrx" / "005930.KS"
    pykrx_dir.mkdir(parents=True)
    (pykrx_dir / "005930-2024-2024-ohlcv-abc123abc123.csv").write_text(
        "date,open,high,low,close,volume,value_traded,change_pct\n"
        "2024-01-02,78000,79000,77000,78500,1000000,78500000000,1.55\n",
        encoding="utf-8",
    )

    marcap_dir = tmp_path / "marcap" / "KR_MARKET"
    marcap_dir.mkdir(parents=True)
    (marcap_dir / "marcap-2024-def456def456.parquet").write_bytes(_marcap_parquet_payload())

    summary = inspect_raw_kr_evidence("005930.KS", 2024, 2024, raw_root=tmp_path)

    assert summary["status"] == "ok"
    assert summary["market"] == "KR"
    assert summary["data_mode"] == "raw_source_evidence_only"
    assert summary["summary"]["tickers_ok"] == 1
    assert summary["summary"]["valuation_ready"] == 0

    ticker = summary["tickers"][0]
    assert ticker["status"] == "ok"
    assert ticker["valuation_ready"] is False
    assert ticker["pykrx"]["row_count"] == 1
    assert ticker["marcap"]["row_count"] == 1
    assert ticker["marcap"]["market_cap_rows"] == 1
    assert ticker["marcap"]["listed_shares_rows"] == 1
    assert ticker["opendart"]["files"] == []
    assert ticker["opendart"]["eps_years"] == []

    trace = ticker["source_trace"]
    assert trace["source_type"] == "raw_kr_market_evidence"
    assert trace["method"] == "RAW_KR_MARKET_EVIDENCE_INSPECTION"
    assert trace["quality_status"] == "raw_evidence_available"
    assert "missing_opendart_raw_file" in trace["quality_flags"]
    assert "missing_opendart_adjusted_operating_eps" in trace["quality_flags"]
    assert trace["input_sources"]["pykrx"][0]["content_hash"]
    assert trace["input_sources"]["marcap"][0]["source_trace"]["source_document_id"].startswith(
        "raw:marcap:"
    )
    assert trace["input_sources"]["opendart"] == []


def test_inspect_raw_kr_evidence_requires_requested_year_coverage(tmp_path):
    pykrx_dir = tmp_path / "pykrx" / "005930.KS"
    pykrx_dir.mkdir(parents=True)
    (pykrx_dir / "005930-2024-2024-ohlcv-abc123abc123.csv").write_text(
        "date,open,high,low,close,volume,value_traded,change_pct\n"
        "2024-01-02,78000,79000,77000,78500,1000000,78500000000,1.55\n",
        encoding="utf-8",
    )

    marcap_dir = tmp_path / "marcap" / "KR_MARKET"
    marcap_dir.mkdir(parents=True)
    (marcap_dir / "marcap-2024-def456def456.parquet").write_bytes(_marcap_parquet_payload())

    summary = inspect_raw_kr_evidence("005930.KS", 2023, 2024, raw_root=tmp_path)

    assert summary["status"] == "missing"
    assert summary["summary"]["tickers_ok"] == 0
    ticker = summary["tickers"][0]
    assert ticker["status"] == "missing"
    assert ticker["valuation_ready"] is False
    assert ticker["coverage_years"]["expected"] == [2023, 2024]
    assert ticker["coverage_years"]["pykrx"] == [2024]
    assert ticker["coverage_years"]["marcap"] == [2024]
    assert ticker["missing_years"]["pykrx"] == [2023]
    assert ticker["missing_years"]["market_cap"] == [2023]
    assert ticker["missing_years"]["listed_shares"] == [2023]
    check_names = {check["name"]: check for check in ticker["checks"]}
    assert check_names["pykrx_rows"]["ok"] is True
    assert check_names["pykrx_year_coverage"]["ok"] is False
    assert check_names["market_cap_year_coverage"]["ok"] is False
    assert "missing_pykrx_year_coverage" in ticker["source_trace"]["quality_flags"]
    action_ids = [action["id"] for action in summary["next_actions"]]
    assert "collect_pykrx_kr" in action_ids
    assert "collect_marcap_kr" in action_ids


def test_inspect_raw_kr_evidence_can_require_opendart_metrics(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENDART_API_KEY", raising=False)
    monkeypatch.delenv("DART_API_KEY", raising=False)
    pykrx_dir = tmp_path / "pykrx" / "005930.KS"
    pykrx_dir.mkdir(parents=True)
    (pykrx_dir / "005930-2024-2024-ohlcv-abc123abc123.csv").write_text(
        "date,open,high,low,close,volume,value_traded,change_pct\n"
        "2024-01-02,78000,79000,77000,78500,1000000,78500000000,1.55\n",
        encoding="utf-8",
    )

    marcap_dir = tmp_path / "marcap" / "KR_MARKET"
    marcap_dir.mkdir(parents=True)
    (marcap_dir / "marcap-2024-def456def456.parquet").write_bytes(_marcap_parquet_payload())

    summary = inspect_raw_kr_evidence(
        "005930.KS",
        2024,
        2024,
        raw_root=tmp_path,
        require_opendart=True,
    )

    assert summary["status"] == "missing"
    assert summary["require_opendart"] is True
    assert summary["summary"]["tickers_ok"] == 0
    assert summary["summary"]["valuation_ready"] == 0
    ticker = summary["tickers"][0]
    assert ticker["status"] == "missing"
    assert ticker["valuation_ready"] is False
    assert "missing_opendart_raw_file" in ticker["source_trace"]["quality_flags"]
    required_checks = {check["name"]: check for check in ticker["checks"]}
    assert required_checks["opendart_raw_file"]["required"] is True
    assert required_checks["opendart_adjusted_operating_eps"]["ok"] is False
    action_ids = [action["id"] for action in summary["next_actions"]]
    assert action_ids[:2] == ["load_local_secrets", "collect_opendart_kr"]
    assert "inspect_raw_kr" in action_ids
    assert "build_kr_valuation_inputs" in action_ids
    serialized_actions = json.dumps(summary["next_actions"], ensure_ascii=False)
    assert "OPENDART_API_KEY=" not in serialized_actions
    assert "DART_API_KEY=" not in serialized_actions


def test_inspect_raw_kr_evidence_accepts_explained_partial_source_backed_years(tmp_path):
    pykrx_dir = tmp_path / "pykrx" / "005930.KS"
    pykrx_dir.mkdir(parents=True)
    (pykrx_dir / "005930-2024-2024-ohlcv-abc123abc123.csv").write_text(
        "date,open,high,low,close,volume,value_traded,change_pct\n"
        "2024-01-02,78000,79000,77000,78500,1000000,78500000000,1.55\n",
        encoding="utf-8",
    )

    marcap_dir = tmp_path / "marcap" / "KR_MARKET"
    marcap_dir.mkdir(parents=True)
    (marcap_dir / "marcap-2024-def456def456.parquet").write_bytes(_marcap_parquet_payload())

    opendart_dir = tmp_path / "opendart" / "005930.KS"
    opendart_dir.mkdir(parents=True)
    (opendart_dir / "00126380-2023-11011-CFS-no-data.json").write_text(
        json.dumps(
            {
                "status": "013",
                "message": "조회된 데이타가 없습니다.",
                "bsns_year": "2023",
                "list": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (opendart_dir / "00126380-2024-11011-CFS-abc789abc789.json").write_text(
        json.dumps(
            {
                "status": "000",
                "message": "normal",
                "list": [
                    {
                        "bsns_year": "2024",
                        "account_id": "ifrs-full_DilutedEarningsLossesPerShare",
                        "account_nm": "Diluted EPS",
                        "thstrm_amount": "3164",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    summary = inspect_raw_kr_evidence(
        "005930.KS",
        2023,
        2024,
        raw_root=tmp_path,
        require_opendart=True,
    )

    assert summary["status"] == "ok"
    assert summary["summary"]["tickers_ok"] == 1
    assert summary["summary"]["valuation_ready"] == 1
    assert summary["summary"]["partial_source_backed"] == 1
    ticker = summary["tickers"][0]
    assert ticker["status"] == "ok"
    assert ticker["valuation_ready"] is True
    assert ticker["full_coverage_ready"] is False
    assert ticker["coverage_status"] == "partial_source_backed"
    assert ticker["valuation_years"] == [2024]
    assert ticker["coverage_years"]["valuation_points"] == [2024]
    assert ticker["missing_years"]["pykrx"] == [2023]
    assert ticker["market_gap_diagnostics"][0]["status"] == "source_no_rows_before_first_trade"
    assert ticker["financial_gap_diagnostics"][0]["status"] == "source_no_data"
    assert "partial_valuation_coverage" in ticker["source_trace"]["quality_flags"]
    action_ids = [action["id"] for action in summary["next_actions"]]
    assert "collect_opendart_kr" not in action_ids
    assert "collect_pykrx_kr" not in action_ids
    assert "collect_marcap_kr" not in action_ids
    assert action_ids[-1] == "build_kr_valuation_inputs"


def test_inspect_raw_kr_evidence_reports_missing_raw_inputs(tmp_path):
    summary = inspect_raw_kr_evidence("005930.KS", 2024, 2024, raw_root=tmp_path)

    assert summary["status"] == "missing"
    ticker = summary["tickers"][0]
    assert ticker["status"] == "missing"
    assert "missing_pykrx_raw_file" in ticker["source_trace"]["quality_flags"]
    assert "missing_marcap_raw_file" in ticker["source_trace"]["quality_flags"]


def test_build_kr_valuation_inputs_writes_source_backed_market_facts(tmp_path):
    raw_root = tmp_path / "raw"
    pykrx_dir = raw_root / "pykrx" / "005930.KS"
    pykrx_dir.mkdir(parents=True)
    (pykrx_dir / "005930-2024-2024-ohlcv-abc123abc123.csv").write_text(
        "date,open,high,low,close,volume,value_traded,change_pct\n"
        "2024-01-02,78000,79000,77000,78500,1000000,78500000000,1.55\n",
        encoding="utf-8",
    )

    marcap_dir = raw_root / "marcap" / "KR_MARKET"
    marcap_dir.mkdir(parents=True)
    (marcap_dir / "marcap-2024-def456def456.parquet").write_bytes(_marcap_parquet_payload())

    summary = build_kr_valuation_inputs(
        "005930.KS",
        2024,
        2024,
        raw_root=raw_root,
        out_dir=tmp_path / "out",
    )

    assert summary["status"] == "missing"
    assert summary["summary"]["valuation_ready"] == 0
    assert summary["summary"]["partial_source_backed"] == 0
    ticker = summary["tickers"][0]
    assert ticker["status"] == "missing"
    assert ticker["valuation_ready"] is False
    assert ticker["full_coverage_ready"] is False
    assert ticker["coverage_status"] == "blocked"
    assert ticker["metric_status"]["status"] == "blocked"
    assert ticker["normalized_fact_count"] == 3
    action_ids = [action["id"] for action in summary["next_actions"]]
    assert "collect_opendart_kr" in action_ids
    assert action_ids[-1] == "build_kr_valuation_inputs"

    payload = json.loads(Path(ticker["output_path"]).read_text(encoding="utf-8"))
    assert payload["valuation_points"] == []
    assert payload["metric_status"]["reason"] == "missing_open_dart_metric_values"
    assert payload["dividend_status"]["status"] == "blocked"
    assert payload["dividend_status"]["reason"] == "missing_source_backed_dividend_per_share"
    facts = {fact["metric"]: fact for fact in payload["normalized_facts"]}
    assert facts["price_close"]["value"] == "78500"
    assert facts["market_cap"]["value"] == "468000000000000"
    assert facts["listed_shares"]["value"] == "5969782550"

    for fact in facts.values():
        trace = SourceTrace(**fact["source_trace"])
        trace.assert_storage_ready()
        assert trace.source_document_id
        assert trace.filing_id
        assert trace.form == "raw_market_file"
        assert trace.method


def test_build_kr_valuation_inputs_uses_opendart_eps_for_source_backed_points(tmp_path):
    raw_root = tmp_path / "raw"
    pykrx_dir = raw_root / "pykrx" / "005930.KS"
    pykrx_dir.mkdir(parents=True)
    (pykrx_dir / "005930-2024-2024-ohlcv-abc123abc123.csv").write_text(
        "date,open,high,low,close,volume,value_traded,change_pct\n"
        "2024-12-30,53200,54000,53000,53200,1000000,53200000000,0.20\n",
        encoding="utf-8",
    )
    (pykrx_dir / "005930-2024-2024-fundamental-div123div123.csv").write_text(
        "date,BPS,PER,PBR,EPS,DPS,DIV\n"
        "2024-12-30,52000,12.5,1.1,3164,1444,2.7\n",
        encoding="utf-8",
    )

    marcap_dir = raw_root / "marcap" / "KR_MARKET"
    marcap_dir.mkdir(parents=True)
    (marcap_dir / "marcap-2024-def456def456.parquet").write_bytes(_marcap_parquet_payload())

    opendart_dir = raw_root / "opendart" / "005930.KS"
    opendart_dir.mkdir(parents=True)
    (opendart_dir / "00126380-2024-11011-CFS-abc789abc789.json").write_text(
        json.dumps(
            {
                "status": "000",
                "message": "정상",
                "list": [
                    {
                        "bsns_year": "2024",
                        "account_id": "ifrs-full_Revenue",
                        "account_nm": "Revenue",
                        "thstrm_amount": "300000000000000",
                    },
                    {
                        "bsns_year": "2024",
                        "account_id": "dart_OperatingIncomeLoss",
                        "account_nm": "Operating income",
                        "thstrm_amount": "25000000000000",
                    },
                    {
                        "bsns_year": "2024",
                        "account_id": "ifrs-full_ProfitLossAttributableToOwnersOfParent",
                        "account_nm": "Net income attributable to owners of parent",
                        "thstrm_amount": "21000000000000",
                    },
                    {
                        "bsns_year": "2024",
                        "account_id": "ifrs-full_DilutedEarningsLossesPerShare",
                        "account_nm": "Diluted EPS",
                        "thstrm_amount": "3164",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    summary = build_kr_valuation_inputs(
        "005930.KS",
        2024,
        2024,
        raw_root=raw_root,
        out_dir=tmp_path / "out",
    )

    assert summary["status"] == "ok"
    ticker = summary["tickers"][0]
    assert ticker["status"] == "ok"
    assert ticker["valuation_ready"] is True
    assert ticker["valuation_point_count"] == 1
    assert ticker["metric_status"]["status"] == "ok"
    assert ticker["metric_status"]["method"] == "S3_MARKET_STANDARD_KR"

    payload = json.loads(Path(ticker["output_path"]).read_text(encoding="utf-8"))
    facts = {fact["metric"]: fact for fact in payload["normalized_facts"]}
    assert facts["adjusted_operating_eps"]["value"] == "3164"
    assert facts["gaap_diluted_eps"]["value"] == "3164"
    assert facts["revenue"]["value"] == "300000000000000"
    assert facts["operating_income"]["value"] == "25000000000000"
    assert facts["net_income_parent"]["value"] == "21000000000000"
    assert facts["dividend_per_share"]["value"] == "1444"
    assert payload["dividend_status"]["status"] == "ok"
    assert payload["dividend_status"]["method"] == "PYKRX_RAW_YEAR_END_DPS"
    assert payload["coverage_years"]["dividend"] == [2024]

    point = payload["valuation_points"][0]
    assert point["metric"] == "adjusted_operating_eps"
    assert point["metric_value"] == "3164"
    assert point["price"] == "53200"
    assert point["dividend"] == "1444"
    assert "source_backed_dividend" in point["quality_flags"]
    assert "missing_dividend_source" not in point["quality_flags"]
    point_trace = SourceTrace(**point["source_trace"])
    point_trace.assert_storage_ready()
    assert set(point_trace.input_fact_ids) == {
        facts["price_close"]["fact_id"],
        facts["adjusted_operating_eps"]["fact_id"],
        facts["dividend_per_share"]["fact_id"],
    }

    for metric in (
        "adjusted_operating_eps",
        "gaap_diluted_eps",
        "revenue",
        "operating_income",
        "net_income_parent",
    ):
        trace = SourceTrace(**facts[metric]["source_trace"])
        trace.assert_storage_ready()
        assert trace.form == "opendart_fnlttSinglAcntAll"
        assert trace.method == "S3_MARKET_STANDARD_KR"
    dividend_trace = SourceTrace(**facts["dividend_per_share"]["source_trace"])
    dividend_trace.assert_storage_ready()
    assert dividend_trace.form == "raw_market_file"
    assert dividend_trace.method == "PYKRX_RAW_YEAR_END_DPS"


def test_build_kr_valuation_inputs_uses_opendart_dividend_fallback(tmp_path):
    raw_root = tmp_path / "raw"
    pykrx_dir = raw_root / "pykrx" / "005930.KS"
    pykrx_dir.mkdir(parents=True)
    (pykrx_dir / "005930-2024-2024-ohlcv-abc123abc123.csv").write_text(
        "date,open,high,low,close,volume,value_traded,change_pct\n"
        "2024-12-30,53200,54000,53000,53200,1000000,53200000000,0.20\n",
        encoding="utf-8",
    )

    marcap_dir = raw_root / "marcap" / "KR_MARKET"
    marcap_dir.mkdir(parents=True)
    (marcap_dir / "marcap-2024-def456def456.parquet").write_bytes(_marcap_parquet_payload())

    opendart_dir = raw_root / "opendart" / "005930.KS"
    opendart_dir.mkdir(parents=True)
    (opendart_dir / "00126380-2024-11011-CFS-abc789abc789.json").write_text(
        json.dumps(
            {
                "status": "000",
                "message": "OK",
                "list": [
                    {
                        "bsns_year": "2024",
                        "account_id": "ifrs-full_Revenue",
                        "account_nm": "Revenue",
                        "thstrm_amount": "300000000000000",
                    },
                    {
                        "bsns_year": "2024",
                        "account_id": "dart_OperatingIncomeLoss",
                        "account_nm": "Operating income",
                        "thstrm_amount": "25000000000000",
                    },
                    {
                        "bsns_year": "2024",
                        "account_id": "ifrs-full_ProfitLossAttributableToOwnersOfParent",
                        "account_nm": "Net income attributable to owners of parent",
                        "thstrm_amount": "21000000000000",
                    },
                    {
                        "bsns_year": "2024",
                        "account_id": "ifrs-full_DilutedEarningsLossesPerShare",
                        "account_nm": "Diluted EPS",
                        "thstrm_amount": "3164",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    opendart_dividend_dir = raw_root / "opendart_dividends" / "005930.KS"
    opendart_dividend_dir.mkdir(parents=True)
    (opendart_dividend_dir / "00126380-2024-alotMatter-012345abcdef.json").write_text(
        json.dumps(
            {
                "status": "000",
                "message": "OK",
                "bsns_year": "2024",
                "list": [
                    {
                        "se": "\uc8fc\ub2f9 \ud604\uae08\ubc30\ub2f9\uae08(\uc6d0)",
                        "stock_knd": "\uc6b0\uc120\uc8fc",
                        "thstrm": "1,445",
                    },
                    {
                        "se": "\uc8fc\ub2f9 \ud604\uae08\ubc30\ub2f9\uae08(\uc6d0)",
                        "stock_knd": "\ubcf4\ud1b5\uc8fc",
                        "thstrm": "1,444",
                    },
                    {
                        "se": "\ud604\uae08\ubc30\ub2f9\uc218\uc775\ub960",
                        "stock_knd": "\ubcf4\ud1b5\uc8fc",
                        "thstrm": "2.70",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    summary = build_kr_valuation_inputs(
        "005930.KS",
        2024,
        2024,
        raw_root=raw_root,
        out_dir=tmp_path / "out",
    )

    assert summary["status"] == "ok"
    ticker = summary["tickers"][0]
    assert ticker["valuation_ready"] is True
    assert ticker["dividend_status"]["status"] == "ok"
    assert ticker["dividend_status"]["method"] == "OPENDART_ALOT_MATTER_DPS"

    payload = json.loads(Path(ticker["output_path"]).read_text(encoding="utf-8"))
    facts = {fact["metric"]: fact for fact in payload["normalized_facts"]}
    assert facts["dividend_per_share"]["value"] == "1444"
    assert payload["coverage_years"]["dividend"] == [2024]

    dividend_trace = SourceTrace(**facts["dividend_per_share"]["source_trace"])
    dividend_trace.assert_storage_ready()
    assert dividend_trace.source == "opendart_dividends"
    assert dividend_trace.form == "opendart_alotMatter"
    assert dividend_trace.method == "OPENDART_ALOT_MATTER_DPS"
    assert dividend_trace.metadata["opendart_status"] == "000"
    assert "opendart_dividend_fallback" in dividend_trace.quality_flags

    point = payload["valuation_points"][0]
    assert point["dividend"] == "1444"
    assert "source_backed_dividend" in point["quality_flags"]
    point_trace = SourceTrace(**point["source_trace"])
    point_trace.assert_storage_ready()
    assert facts["dividend_per_share"]["fact_id"] in point_trace.input_fact_ids


def test_build_kr_valuation_inputs_treats_opendart_dash_dps_as_source_backed_zero(tmp_path):
    raw_root = tmp_path / "raw"
    pykrx_dir = raw_root / "pykrx" / "005930.KS"
    pykrx_dir.mkdir(parents=True)
    (pykrx_dir / "005930-2024-2024-ohlcv-abc123abc123.csv").write_text(
        "date,open,high,low,close,volume,value_traded,change_pct\n"
        "2024-12-30,53200,54000,53000,53200,1000000,53200000000,0.20\n",
        encoding="utf-8",
    )

    marcap_dir = raw_root / "marcap" / "KR_MARKET"
    marcap_dir.mkdir(parents=True)
    (marcap_dir / "marcap-2024-def456def456.parquet").write_bytes(_marcap_parquet_payload())

    opendart_dir = raw_root / "opendart" / "005930.KS"
    opendart_dir.mkdir(parents=True)
    (opendart_dir / "00126380-2024-11011-CFS-abc789abc789.json").write_text(
        json.dumps(
            {
                "status": "000",
                "message": "OK",
                "list": [
                    {
                        "bsns_year": "2024",
                        "account_id": "ifrs-full_Revenue",
                        "account_nm": "Revenue",
                        "thstrm_amount": "300000000000000",
                    },
                    {
                        "bsns_year": "2024",
                        "account_id": "dart_OperatingIncomeLoss",
                        "account_nm": "Operating income",
                        "thstrm_amount": "25000000000000",
                    },
                    {
                        "bsns_year": "2024",
                        "account_id": "ifrs-full_ProfitLossAttributableToOwnersOfParent",
                        "account_nm": "Net income attributable to owners of parent",
                        "thstrm_amount": "21000000000000",
                    },
                    {
                        "bsns_year": "2024",
                        "account_id": "ifrs-full_DilutedEarningsLossesPerShare",
                        "account_nm": "Diluted EPS",
                        "thstrm_amount": "3164",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    opendart_dividend_dir = raw_root / "opendart_dividends" / "005930.KS"
    opendart_dividend_dir.mkdir(parents=True)
    (opendart_dividend_dir / "00126380-2024-alotMatter-012345abcdef.json").write_text(
        json.dumps(
            {
                "status": "000",
                "message": "OK",
                "bsns_year": "2024",
                "list": [
                    {
                        "se": "\uc8fc\ub2f9 \ud604\uae08\ubc30\ub2f9\uae08(\uc6d0)",
                        "stock_knd": "\ubcf4\ud1b5\uc8fc",
                        "thstrm": "-",
                    },
                    {
                        "se": "\ud604\uae08\ubc30\ub2f9\uc218\uc775\ub960",
                        "stock_knd": "\ubcf4\ud1b5\uc8fc",
                        "thstrm": "-",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    summary = build_kr_valuation_inputs(
        "005930.KS",
        2024,
        2024,
        raw_root=raw_root,
        out_dir=tmp_path / "out",
    )

    assert summary["status"] == "ok"
    ticker = summary["tickers"][0]
    assert ticker["valuation_ready"] is True
    assert ticker["dividend_status"]["status"] == "ok"
    assert ticker["dividend_status"]["method"] == "OPENDART_ALOT_MATTER_DPS"

    payload = json.loads(Path(ticker["output_path"]).read_text(encoding="utf-8"))
    facts = {fact["metric"]: fact for fact in payload["normalized_facts"]}
    assert facts["dividend_per_share"]["value"] == "0"

    dividend_trace = SourceTrace(**facts["dividend_per_share"]["source_trace"])
    dividend_trace.assert_storage_ready()
    assert dividend_trace.method == "OPENDART_ALOT_MATTER_DPS"
    assert "source_backed_dividend" in dividend_trace.quality_flags
    assert "opendart_dash_no_cash_dividend_assumed_zero" in dividend_trace.quality_flags

    point = payload["valuation_points"][0]
    assert point["dividend"] == "0"
    assert "missing_dividend_source" not in point["quality_flags"]
    point_trace = SourceTrace(**point["source_trace"])
    point_trace.assert_storage_ready()
    assert facts["dividend_per_share"]["fact_id"] in point_trace.input_fact_ids


def test_build_kr_valuation_inputs_marks_partial_years_source_backed(tmp_path):
    raw_root = tmp_path / "raw"
    pykrx_dir = raw_root / "pykrx" / "005930.KS"
    pykrx_dir.mkdir(parents=True)
    (pykrx_dir / "005930-2024-2024-ohlcv-abc123abc123.csv").write_text(
        "date,open,high,low,close,volume,value_traded,change_pct\n"
        "2024-12-30,53200,54000,53000,53200,1000000,53200000000,0.20\n",
        encoding="utf-8",
    )

    marcap_dir = raw_root / "marcap" / "KR_MARKET"
    marcap_dir.mkdir(parents=True)
    (marcap_dir / "marcap-2024-def456def456.parquet").write_bytes(_marcap_parquet_payload())

    opendart_dir = raw_root / "opendart" / "005930.KS"
    opendart_dir.mkdir(parents=True)
    (opendart_dir / "00126380-2024-11011-CFS-abc789abc789.json").write_text(
        json.dumps(
            {
                "status": "000",
                "message": "normal",
                "list": [
                    {
                        "bsns_year": "2024",
                        "account_id": "ifrs-full_DilutedEarningsLossesPerShare",
                        "account_nm": "Diluted EPS",
                        "thstrm_amount": "3164",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    summary = build_kr_valuation_inputs(
        "005930.KS",
        2023,
        2024,
        raw_root=raw_root,
        out_dir=tmp_path / "out",
    )

    assert summary["status"] == "ok"
    assert summary["summary"]["valuation_ready"] == 1
    assert summary["summary"]["partial_source_backed"] == 1
    ticker = summary["tickers"][0]
    assert ticker["status"] == "ok"
    assert ticker["valuation_ready"] is True
    assert ticker["full_coverage_ready"] is False
    assert ticker["coverage_status"] == "partial_source_backed"
    assert ticker["valuation_point_count"] == 1
    assert ticker["valuation_years"] == [2024]
    assert ticker["metric_status"]["status"] == "partial"
    assert ticker["metric_status"]["missing_years"] == [2023]
    assert ticker["missing_years"] == {
        "market_input": [2023],
        "financial_metric": [2023],
    }
    assert ticker["market_gap_diagnostics"] == [
        {
            "fiscal_year": 2023,
            "status": "source_no_rows_before_first_trade",
            "reason": "No pykrx or marcap rows exist for this ticker before the first cached market row 2024-01-02.",
            "next_action": "keep_partial_market_history_start",
            "missing_price": True,
            "missing_market_structure": True,
            "first_available_market_date": "2024-01-02",
            "pykrx_source_document_id": None,
            "marcap_source_document_id": None,
        }
    ]
    assert "partial_valuation_coverage" in ticker["quality_flags"]
    assert "missing_market_input_2023" in ticker["quality_flags"]
    assert "missing_financial_metric_2023" in ticker["quality_flags"]
    action_ids = [action["id"] for action in summary["next_actions"]]
    assert "document_market_history_start" in action_ids
    assert "collect_kr_market_raw" not in action_ids
    assert "collect_kr_market_structure_raw" not in action_ids

    payload = json.loads(Path(ticker["output_path"]).read_text(encoding="utf-8"))
    assert payload["coverage_status"] == "partial_source_backed"
    assert payload["full_coverage_ready"] is False
    assert payload["valuation_ready"] is True
    assert payload["coverage_years"]["valuation_points"] == [2024]
    assert payload["market_gap_diagnostics"][0]["status"] == "source_no_rows_before_first_trade"


def test_build_kr_valuation_inputs_distinguishes_opendart_no_data_years(tmp_path):
    raw_root = tmp_path / "raw"
    pykrx_dir = raw_root / "pykrx" / "005930.KS"
    pykrx_dir.mkdir(parents=True)
    (pykrx_dir / "005930-2024-2024-ohlcv-abc123abc123.csv").write_text(
        "date,open,high,low,close,volume,value_traded,change_pct\n"
        "2024-12-30,100000,101000,99000,100500,1000000,100500000000,0.20\n",
        encoding="utf-8",
    )

    marcap_dir = raw_root / "marcap" / "KR_MARKET"
    marcap_dir.mkdir(parents=True)
    (marcap_dir / "marcap-2024-def456def456.parquet").write_bytes(_marcap_parquet_payload())

    opendart_dir = raw_root / "opendart" / "005930.KS"
    opendart_dir.mkdir(parents=True)
    (opendart_dir / "00999999-2024-11011-CFS-no-data.json").write_text(
        json.dumps(
            {
                "status": "013",
                "message": "조회된 데이타가 없습니다.",
                "bsns_year": "2024",
                "list": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    summary = build_kr_valuation_inputs(
        "005930.KS",
        2024,
        2024,
        raw_root=raw_root,
        out_dir=tmp_path / "out",
    )

    ticker = summary["tickers"][0]
    assert ticker["metric_status"]["status"] == "blocked"
    assert ticker["financial_gap_diagnostics"] == [
        {
            "fiscal_year": 2024,
            "source_document_id": ticker["financial_gap_diagnostics"][0]["source_document_id"],
            "filing_id": "00999999-2024-11011-CFS-no-data",
            "opendart_status": "013",
            "opendart_message": "조회된 데이타가 없습니다.",
            "row_count": 0,
            "status": "source_no_data",
            "reason": "OpenDART returned a non-success status for this annual filing request.",
            "next_action": "keep_partial_or_add_alternate_source",
        }
    ]
    action_ids = [action["id"] for action in summary["next_actions"]]
    assert "collect_opendart_kr" not in action_ids
    assert "document_opendart_no_data_years" in action_ids
    assert action_ids[-1] == "build_kr_valuation_inputs"


def test_inspect_raw_kr_evidence_reports_opendart_metric_readiness(tmp_path):
    pykrx_dir = tmp_path / "pykrx" / "005930.KS"
    pykrx_dir.mkdir(parents=True)
    (pykrx_dir / "005930-2024-2024-ohlcv-abc123abc123.csv").write_text(
        "date,open,high,low,close,volume,value_traded,change_pct\n"
        "2024-12-30,53200,54000,53000,53200,1000000,53200000000,0.20\n",
        encoding="utf-8",
    )

    marcap_dir = tmp_path / "marcap" / "KR_MARKET"
    marcap_dir.mkdir(parents=True)
    (marcap_dir / "marcap-2024-def456def456.parquet").write_bytes(_marcap_parquet_payload())

    opendart_dir = tmp_path / "opendart" / "005930.KS"
    opendart_dir.mkdir(parents=True)
    (opendart_dir / "00126380-2024-11011-CFS-abc789abc789.json").write_text(
        json.dumps(
                {
                    "status": "000",
                    "message": "normal",
                    "list": [
                        {
                            "bsns_year": "2024",
                            "account_id": "ifrs-full_Revenue",
                            "account_nm": "Revenue",
                            "thstrm_amount": "300000000000000",
                        },
                        {
                            "bsns_year": "2024",
                            "account_id": "dart_OperatingIncomeLoss",
                            "account_nm": "Operating income",
                            "thstrm_amount": "25000000000000",
                        },
                        {
                            "bsns_year": "2024",
                            "account_id": "ifrs-full_ProfitLossAttributableToOwnersOfParent",
                            "account_nm": "Net income attributable to owners of parent",
                            "thstrm_amount": "21000000000000",
                        },
                        {
                            "bsns_year": "2024",
                            "account_id": "ifrs-full_DilutedEarningsLossesPerShare",
                            "account_nm": "Diluted EPS",
                        "thstrm_amount": "3164",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    summary = inspect_raw_kr_evidence(
        "005930.KS",
        2024,
        2024,
        raw_root=tmp_path,
        require_opendart=True,
    )

    assert summary["status"] == "ok"
    assert summary["summary"]["valuation_ready"] == 1
    ticker = summary["tickers"][0]
    assert ticker["valuation_ready"] is True
    assert ticker["opendart"]["eps_years"] == [2024]
    assert ticker["opendart"]["files"][0]["has_adjusted_operating_eps"] is True
    assert all(check["ok"] for check in ticker["checks"] if check["required"])


def test_persist_pykrx_price_bars_emits_storage_ready_source_trace():
    calls: list[dict] = []

    class FakeRepo:
        def store_price_bar(
            self,
            security_id,
            fiscal_year,
            trade_date,
            close_price,
            currency,
            source,
            source_trace,
        ):
            trace = SourceTrace(**source_trace)
            trace.assert_storage_ready()
            calls.append(
                {
                    "security_id": security_id,
                    "fiscal_year": fiscal_year,
                    "trade_date": trade_date,
                    "close_price": close_price,
                    "currency": currency,
                    "source": source,
                    "source_trace": trace.model_dump(mode="json"),
                }
            )

    document = ConnectorDocument(
        source="pykrx",
        market="KR",
        ticker="005930.KS",
        identifier="005930-2024-2024-ohlcv",
        url="https://github.com/sharebook-kr/pykrx",
        payload=(
            "date,open,high,low,close,volume,value_traded,change_pct\n"
            "2024-01-02,70000,71000,69000,70500,12345,870000000,1.2\n"
        ).encode("utf-8-sig"),
        content_type="text/csv",
        metadata={"krx_code": "005930", "endpoint": "get_market_ohlcv"},
    )

    count = ingestion_cli._persist_pykrx_price_bars(
        FakeRepo(),
        "security-005930",
        "source-document-pykrx",
        document,
    )

    assert count == 1
    trace = calls[0]["source_trace"]
    assert trace["source"] == "pykrx"
    assert trace["source_document_id"] == "source-document-pykrx"
    assert trace["filing_id"] == "005930-2024-2024-ohlcv"
    assert trace["period"] == "2024-01-02"
    assert trace["unit"] == "per_share"
    assert trace["currency"] == "KRW"
    assert trace["method"] == "PYKRX_DAILY_CLOSE"
    assert trace["quality_status"] == "source_backed_price"
    assert trace["formula"] == "pykrx OHLCV CSV close column imported as price_bars.close_price"


def test_persist_marcap_price_bars_preserves_market_cap_evidence_trace():
    calls: list[dict] = []

    class FakeRepo:
        def ensure_security(self, ticker, name, market, currency, exchange):
            assert ticker == "005930.KS"
            assert market == "KR"
            assert currency == "KRW"
            return SimpleNamespace(id="security-005930")

        def store_price_bar(
            self,
            security_id,
            fiscal_year,
            trade_date,
            close_price,
            currency,
            source,
            source_trace,
        ):
            trace = SourceTrace(**source_trace)
            trace.assert_storage_ready()
            calls.append(
                {
                    "security_id": security_id,
                    "fiscal_year": fiscal_year,
                    "trade_date": trade_date,
                    "close_price": close_price,
                    "currency": currency,
                    "source": source,
                    "source_trace": trace.model_dump(mode="json"),
                }
            )

    document = ConnectorDocument(
        source="marcap",
        market="KR",
        ticker="KR_MARKET",
        identifier="marcap-2024",
        url="https://raw.githubusercontent.com/FinanceData/marcap/master/data/marcap-2024.parquet",
        payload=_marcap_parquet_payload(),
        content_type="application/vnd.apache.parquet",
        metadata={"year": 2024, "format": "parquet"},
    )

    count = ingestion_cli._persist_marcap_price_bars(
        FakeRepo(),
        "source-document-marcap",
        document,
        {"005930"},
        {},
    )

    assert count == 1
    trace = calls[0]["source_trace"]
    assert trace["source"] == "marcap"
    assert trace["source_document_id"] == "source-document-marcap"
    assert trace["filing_id"] == "marcap-2024"
    assert trace["period"] == "2024-01-02"
    assert trace["unit"] == "per_share"
    assert trace["currency"] == "KRW"
    assert trace["method"] == "MARCAP_DAILY_CLOSE"
    assert trace["quality_status"] == "open_dataset_price"
    assert trace["rank"] == "1"
    assert trace["market_cap"] == "468000000000000"
    assert trace["market_cap_unit"] == "KRW"
    assert trace["market_cap_raw"] == "468000000"
    assert trace["market_cap_raw_unit_detected"] == "KRW_millions"
    assert trace["market_cap_quality_flags"] == ["marcap_market_cap_converted_from_krw_millions"]
    assert trace["listed_shares"] == "5969782550"
    assert trace["listed_shares_unit"] == "shares"
    assert "Close, Marcap, and Stocks" in trace["formula"]


def test_jquants_connector_collects_prices_statements_and_dividends():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if "/token/auth_refresh" in url:
            return httpx.Response(200, json={"idToken": "id-token"})
        assert request.headers["Authorization"] == "Bearer id-token"
        if "/prices/daily_quotes" in url:
            return httpx.Response(
                200,
                json={
                    "daily_quotes": [
                        {
                            "Date": "2024-01-04",
                            "Code": "72030",
                            "Close": "2600",
                            "AdjustmentClose": "2600",
                        }
                    ]
                },
            )
        if "/fins/statements" in url:
            return httpx.Response(
                200,
                json={
                    "statements": [
                        {
                            "LocalCode": "72030",
                            "EarningsPerShare": "365.94",
                        }
                    ]
                },
            )
        if "/fins/dividend" in url:
            return httpx.Response(
                200,
                json={
                    "dividend": [
                        {
                            "Code": "72030",
                            "ExDate": "2024-03-28",
                            "GrossDividendRate": "45.0",
                        }
                    ]
                },
            )
        raise AssertionError(f"unexpected URL: {url}")

    connector = JQuantsConnector(
        refresh_token="refresh-token",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    documents = connector.collect_bundle(
        ConnectorRequest(ticker="7203.T", market="JP", start_year=2024, end_year=2024)
    )

    assert sum("/token/auth_refresh" in url for url in calls) == 1
    assert {document.metadata["endpoint"] for document in documents} == {
        "/prices/daily_quotes",
        "/fins/statements",
        "/fins/dividend",
    }
    assert sum(document.metadata["row_count"] for document in documents) == 3


def test_collect_jquants_data_dry_run_uses_bundle_connector(monkeypatch):
    class FakeJQuantsConnector:
        def collect_bundle(
            self,
            request: ConnectorRequest,
            endpoints: list[str],
        ) -> list[ConnectorDocument]:
            docs: list[ConnectorDocument] = []
            if "daily_quotes" in endpoints:
                docs.append(
                    ConnectorDocument(
                        source="jquants",
                        market="JP",
                        ticker=request.ticker,
                        identifier=f"{request.ticker}-daily-quotes-fixture",
                        url="https://api.jquants.com/v1/prices/daily_quotes",
                        payload=json.dumps(
                            {
                                "daily_quotes": [
                                    {
                                        "Date": "2024-01-04",
                                        "Close": "2600",
                                        "AdjustmentClose": "2600",
                                    }
                                ]
                            }
                        ).encode("utf-8"),
                        content_type="application/json",
                        metadata={
                            "endpoint": "/prices/daily_quotes",
                            "payload_key": "daily_quotes",
                        },
                    )
                )
            if "statements" in endpoints:
                docs.append(
                    ConnectorDocument(
                        source="jquants",
                        market="JP",
                        ticker=request.ticker,
                        identifier=f"{request.ticker}-statements-fixture",
                        url="https://api.jquants.com/v1/fins/statements",
                        payload=json.dumps(
                            {"statements": [{"EarningsPerShare": "365.94"}]}
                        ).encode("utf-8"),
                        content_type="application/json",
                        metadata={"endpoint": "/fins/statements", "payload_key": "statements"},
                    )
                )
            if "dividends" in endpoints:
                docs.append(
                    ConnectorDocument(
                        source="jquants",
                        market="JP",
                        ticker=request.ticker,
                        identifier=f"{request.ticker}-dividend-fixture",
                        url="https://api.jquants.com/v1/fins/dividend",
                        payload=json.dumps(
                            {"dividend": [{"ExDate": "2024-03-28", "GrossDividendRate": "45.0"}]}
                        ).encode("utf-8"),
                        content_type="application/json",
                        metadata={"endpoint": "/fins/dividend", "payload_key": "dividend"},
                    )
                )
            return docs

    monkeypatch.setattr("services.ingestion_worker.cli.JQuantsConnector", FakeJQuantsConnector)

    summary = collect_jquants_data(
        "7203.T,6758.T",
        2024,
        2024,
        persist=False,
    )

    assert summary["status"] == "ok"
    assert summary["market"] == "JP"
    assert summary["tickers"] == ["7203.T", "6758.T"]
    assert summary["price_rows"] == 2
    assert summary["statement_rows"] == 2
    assert summary["dividend_rows"] == 2
    assert summary["persisted"] == []


def test_edinet_connector_collects_metadata_and_csv_zip():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if request.url.path.endswith("/documents.json"):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "docID": "S100TEST",
                            "edinetCode": "E02144",
                            "secCode": "72030",
                            "filerName": "Toyota Motor Corporation",
                            "docTypeCode": "120",
                            "docDescription": "Annual Securities Report",
                            "periodStart": "2023-04-01",
                            "periodEnd": "2024-03-31",
                            "submitDateTime": "2024-06-28 10:00",
                            "xbrlFlag": "1",
                            "csvFlag": "1",
                        }
                    ]
                },
            )
        if "/documents/S100TEST" in request.url.path:
            assert "type=5" in url
            return httpx.Response(
                200,
                content=b"PK\x03\x04csv-zip",
                headers={"content-type": "application/zip"},
            )
        raise AssertionError(f"unexpected URL: {url}")

    connector = EdinetConnector(
        api_key="edinet-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    documents = connector.collect_bundle(
        ConnectorRequest(ticker="7203.T", market="JP", start_year=2024, end_year=2024),
        download_types=["metadata", "csv"],
    )

    assert any("documents.json" in url for url in calls)
    assert any("/documents/S100TEST" in url for url in calls)
    assert {document.metadata["document_type"] for document in documents} == {
        "metadata_list",
        "xbrl_to_csv_zip",
    }
    assert all("edinet-key" not in document.url for document in documents)
    assert any("Subscription-Key=REDACTED" in document.url for document in documents)
    assert any(
        document.payload.startswith(b"PK")
        for document in documents
        if document.metadata["document_type"] == "xbrl_to_csv_zip"
    )


def test_collect_edinet_filings_dry_run_uses_bundle_connector(monkeypatch):
    class FakeEdinetConnector:
        def collect_bundle(
            self,
            request: ConnectorRequest,
            download_types: list[str],
            doc_type_codes: list[str],
        ) -> list[ConnectorDocument]:
            assert download_types == ["metadata", "csv"]
            assert doc_type_codes == ["120"]
            return [
                ConnectorDocument(
                    source="edinet",
                    market="JP",
                    ticker=request.ticker,
                    identifier=f"{request.ticker}-metadata",
                    url="https://api.edinet-fsa.go.jp/api/v2/documents.json",
                    payload=json.dumps({"results": [{"docID": "S100TEST"}]}).encode("utf-8"),
                    content_type="application/json",
                    metadata={"document_type": "metadata_list", "row_count": 1},
                ),
                ConnectorDocument(
                    source="edinet",
                    market="JP",
                    ticker=request.ticker,
                    identifier=f"{request.ticker}-csv",
                    url="https://api.edinet-fsa.go.jp/api/v2/documents/S100TEST",
                    payload=b"PK\x03\x04csv-zip",
                    content_type="application/zip",
                    metadata={"document_type": "xbrl_to_csv_zip", "doc_id": "S100TEST"},
                ),
            ]

    monkeypatch.setattr("services.ingestion_worker.cli.EdinetConnector", FakeEdinetConnector)

    summary = collect_edinet_filings(
        "7203.T,6758.T",
        2024,
        2024,
        persist=False,
    )

    assert summary["status"] == "ok"
    assert summary["market"] == "JP"
    assert summary["tickers"] == ["7203.T", "6758.T"]
    assert summary["metadata_documents"] == 2
    assert summary["csv_zips"] == 2
    assert summary["xbrl_zips"] == 0
    assert summary["persisted"] == []


def test_doctor_reports_missing_required_production_configuration(monkeypatch):
    for key in (
        "DATABASE_URL",
        "DATA_BACKEND",
        "SEC_USER_AGENT",
        "OPENDART_API_KEY",
        "DART_API_KEY",
        "JQUANTS_REFRESH_TOKEN",
        "JQUANTS_EMAIL",
        "JQUANTS_PASSWORD",
        "BLOB_READ_WRITE_TOKEN",
        "EDINET_API_KEY",
        "FRED_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    summary = doctor(markets="US,KR,JP", require_blob=True, strict=True)

    assert summary["status"] == "needs_configuration"
    assert "DATABASE_URL" in summary["missing_required"]
    assert "SEC_USER_AGENT" in summary["missing_required"]
    assert "OPENDART_API_KEY" in summary["missing_required"]
    assert "JQUANTS credentials" in summary["missing_required"]
    assert "BLOB_READ_WRITE_TOKEN" in summary["missing_required"]
    assert "FRED_API_KEY" in summary["missing_required"]
    assert "EDINET_API_KEY" in summary["missing_required"]


def test_doctor_reports_loaded_local_env_key_names_without_values(monkeypatch):
    monkeypatch.setattr(
        ingestion_cli,
        "LOCAL_ENV_KEYS",
        {"DATABASE_URL", "SEC_USER_AGENT"},
    )
    monkeypatch.setenv("DATABASE_URL", "postgresql://secret-example")
    monkeypatch.setenv("DATA_BACKEND", "postgres")
    monkeypatch.setenv("SEC_USER_AGENT", "PrivateAgent/1.0 owner@example.com")

    summary = doctor(markets="US", require_blob=False, strict=False)

    assert summary["local_env_loaded"] is True
    assert summary["local_env_loaded_keys"] == ["DATABASE_URL", "SEC_USER_AGENT"]
    assert "postgresql://secret-example" not in json.dumps(summary)
    assert "PrivateAgent/1.0 owner@example.com" not in json.dumps(summary)


def test_doctor_accepts_dart_api_key_alias_for_kr_market(monkeypatch):
    monkeypatch.setenv("DATA_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example")
    monkeypatch.delenv("OPENDART_API_KEY", raising=False)
    monkeypatch.setenv("DART_API_KEY", "dart-test-token")
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "blob-test-token")
    monkeypatch.setenv("FRED_API_KEY", "fred-test-token")

    summary = doctor(markets="KR", require_blob=False, strict=True)

    assert summary["status"] == "ok"
    assert "OPENDART_API_KEY" not in summary["missing_required"]


def test_doctor_kr_only_does_not_require_us_or_jp_credentials(monkeypatch):
    for key in (
        "DATABASE_URL",
        "DATA_BACKEND",
        "SEC_USER_AGENT",
        "OPENDART_API_KEY",
        "DART_API_KEY",
        "JQUANTS_REFRESH_TOKEN",
        "JQUANTS_EMAIL",
        "JQUANTS_PASSWORD",
        "BLOB_READ_WRITE_TOKEN",
        "EDINET_API_KEY",
        "FRED_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    summary = doctor(markets="KR", require_blob=True, strict=True)

    assert summary["markets"] == ["KR"]
    assert "OPENDART_API_KEY" in summary["missing_required"]
    assert "SEC_USER_AGENT" not in summary["missing_required"]
    assert "JQUANTS credentials" not in summary["missing_required"]
    assert "EDINET_API_KEY" not in summary["missing_required"]


def test_doctor_accepts_minimum_config_for_selected_us_market(monkeypatch):
    monkeypatch.setenv("DATA_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example")
    monkeypatch.setenv("SEC_USER_AGENT", "PersonalFastGraphs/0.1 tests@example.com")
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "blob-test-token")
    monkeypatch.setenv("FRED_API_KEY", "fred-test-token")

    summary = doctor(markets="US", require_blob=False, strict=True)

    assert summary["status"] == "ok"
    assert summary["markets"] == ["US"]


def test_source_coverage_report_without_postgres_is_missing(monkeypatch):
    monkeypatch.delenv("DATA_BACKEND", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    summary = source_coverage_report(tickers="AAPL,NVDA")

    assert summary["status"] == "missing"
    assert summary["postgres"]["reachable"] is False
    assert summary["summary"]["missing_core"] == ["AAPL", "NVDA"]
    prerequisites = {item["name"]: item for item in summary["remediation"]["prerequisites"]}
    assert "DATA_BACKEND=postgres" in prerequisites
    assert "DATABASE_URL" in prerequisites
    assert "SEC_USER_AGENT" in prerequisites
    assert all(item["required"] is True for item in prerequisites.values())


def test_source_coverage_defaults_to_kr_top_market_cap_priority(monkeypatch):
    monkeypatch.delenv("DATA_BACKEND", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(
        ingestion_cli,
        "source_coverage_rows_from_kr_warehouse",
        lambda tickers: None,
    )

    summary = source_coverage_report()

    assert summary["summary"]["tickers_expected"] == 10
    assert summary["summary"]["missing_core"][0:3] == ["005930.KS", "000660.KS", "402340.KS"]
    assert summary["tickers"][0]["pattern"] == "kr_top_market_cap"
    actions = {action["id"]: action for action in summary["remediation"]["next_actions"]}
    assert actions["collect_opendart"]["tickers"][0:3] == ["005930.KS", "000660.KS", "402340.KS"]
    assert actions["collect_marcap"]["tickers"][0:3] == ["005930.KS", "000660.KS", "402340.KS"]


def test_source_coverage_can_target_all_priority_markets(monkeypatch):
    monkeypatch.delenv("DATA_BACKEND", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(
        ingestion_cli,
        "source_coverage_rows_from_kr_warehouse",
        lambda tickers: None,
    )

    summary = source_coverage_report(market="ALL")

    assert summary["summary"]["tickers_expected"] == len(TOP_MARKET_CAP_PRIORITY_TICKERS)
    assert summary["summary"]["missing_core"] == list(TOP_MARKET_CAP_PRIORITY_TICKERS)
    patterns = {row["ticker"]: row["pattern"] for row in summary["tickers"]}
    assert patterns["005930.KS"] == "kr_top_market_cap"
    assert patterns["GOOG"] == "us_top_market_cap"
    assert patterns["7203.T"] == "jp_top_market_cap"
    next_actions = summary["remediation"]["next_actions"]
    assert next_actions[0]["id"] == "run_priority_e2e"
    assert next_actions[0]["github_actions"]["command"] == "run_priority_e2e"
    assert next_actions[0]["github_actions"]["priority_e2e_markets"] == "KR,US,JP"
    assert "run-priority-e2e --markets KR,US,JP" in next_actions[0]["cli_commands"][0]
    actions = {action["id"]: action for action in summary["remediation"]["next_actions"]}
    assert {"collect_opendart", "collect_sec_bulk", "collect_jquants"}.issubset(actions)


def test_ingestion_worker_source_coverage_workflow_defaults_to_kr_gate():
    workflow = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ingestion-worker.yml"
    ).read_text(encoding="utf-8")

    assert "default: run_source_e2e" in workflow
    assert "default: KR" in workflow
    assert "default: 005930.KS" in workflow
    assert "source_coverage_market:" in workflow
    assert "default: KR" in workflow
    assert "source_coverage_tickers:" in workflow
    assert "005930.KS,000660.KS,402340.KS" in workflow
    assert "INPUT_SOURCE_COVERAGE_MARKET: ${{ inputs.source_coverage_market }}" in workflow
    assert "INPUT_SOURCE_COVERAGE_TICKERS: ${{ inputs.source_coverage_tickers }}" in workflow
    assert 'if [[ -n "${INPUT_SOURCE_COVERAGE_TICKERS}" ]]; then' in workflow
    assert '--market "${INPUT_SOURCE_COVERAGE_MARKET}"' in workflow
    assert '--tickers "${INPUT_SOURCE_COVERAGE_TICKERS}"' in workflow


def test_ingestion_worker_run_source_e2e_workflow_uses_market_defaults():
    workflow = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ingestion-worker.yml"
    ).read_text(encoding="utf-8")

    assert "source_e2e_tickers:" in workflow
    assert "INPUT_SOURCE_E2E_TICKERS: ${{ inputs.source_e2e_tickers }}" in workflow
    assert 'if [[ -n "${INPUT_SOURCE_E2E_TICKERS}" ]]; then' in workflow
    assert '--tickers "${INPUT_SOURCE_E2E_TICKERS}"' in workflow
    assert "KR run_source_e2e" in workflow
    assert "JP run_source_e2e" in workflow
    assert "J-Quants credentials are required for JP run_source_e2e" in workflow
    assert "EDINET_API_KEY secret is required for JP run_source_e2e" in workflow
    assert 'run-source-e2e \\' in workflow


def test_ingestion_worker_run_priority_e2e_workflow_runs_global_order():
    workflow = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ingestion-worker.yml"
    ).read_text(encoding="utf-8")

    assert "run_priority_e2e" in workflow
    assert "priority_e2e_markets:" in workflow
    assert "default: KR" in workflow
    assert "JQUANTS_EMAIL: ${{ secrets.JQUANTS_EMAIL }}" in workflow
    assert "JQUANTS_PASSWORD: ${{ secrets.JQUANTS_PASSWORD }}" in workflow
    assert "SEC_USER_AGENT secret is required for US run_priority_e2e" in workflow
    assert "OPENDART_API_KEY or DART_API_KEY secret is required for KR run_priority_e2e" in workflow
    assert "J-Quants credentials are required for JP run_priority_e2e" in workflow
    assert "EDINET_API_KEY secret is required for JP run_priority_e2e" in workflow
    assert "run-priority-e2e \\" in workflow
    assert "INPUT_PRIORITY_E2E_MARKETS: ${{ inputs.priority_e2e_markets }}" in workflow
    assert '--markets "${INPUT_PRIORITY_E2E_MARKETS}"' in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262" in workflow
    assert "persist-credentials: false" in workflow
    install_block = workflow.split("- name: Install dependencies", 1)[1].split(
        "- name: Validate required secrets", 1
    )[0]
    assert "${{ secrets." not in install_block
    assert "pnpm install --frozen-lockfile" in install_block


def test_kr_e2e_operator_entrypoints_are_kr_only():
    root = Path(__file__).resolve().parents[2]
    package = json.loads((root / "package.json").read_text(encoding="utf-8"))
    scripts = package["scripts"]

    assert scripts["doctor:kr"] == "python -m services.ingestion_worker.cli doctor --markets KR --strict"
    assert scripts["secrets:local"] == "powershell -NoProfile -ExecutionPolicy Bypass -File scripts/set-local-secrets.ps1"
    assert "--markets KR --require-blob --strict" in scripts["doctor:kr:deploy"]
    assert "doctor --markets KR --strict" in scripts["e2e:source:kr:check"]
    assert "run-source-e2e --market KR" in scripts["e2e:source:kr:check"]
    assert "--dry-run" in scripts["e2e:source:kr:check"]
    assert "--summary-only" in scripts["e2e:source:kr:check"]
    assert "--summary-only" in scripts["e2e:source:kr:dry-run"]
    assert "--summary-only" in scripts["e2e:source:kr:local-dry-run"]
    assert "--tickers 005930.KS" in scripts["e2e:source:kr:005930:check"]
    assert "--tickers 005930.KS" in scripts["e2e:source:kr:005930"]
    assert "--dry-run" in scripts["e2e:source:kr:005930:dry-run"]
    assert "--summary-only" in scripts["e2e:source:kr:005930:check"]
    assert "--summary-only" in scripts["e2e:source:kr:005930:dry-run"]
    assert "--summary-only" in scripts["e2e:source:kr:005930:local-dry-run"]
    assert scripts["e2e:source:kr:top10"] == scripts["e2e:source:kr"]
    assert scripts["e2e:source:kr:top10:dry-run"] == scripts["e2e:source:kr:dry-run"]
    assert scripts["e2e:source:kr:top10:local-dry-run"] == scripts["e2e:source:kr:local-dry-run"]
    assert "--persist" not in scripts["collect:pykrx:kr:005930:raw"]
    assert "--persist" not in scripts["collect:marcap:kr:005930:raw"]
    assert "--persist" not in scripts["collect:opendart:kr:005930:raw"]
    assert "--tickers 005930.KS --years 2020:2025" in scripts["collect:pykrx:kr:005930:raw"]
    assert "--tickers 005930.KS --years 2020:2025" in scripts["collect:marcap:kr:005930:raw"]
    assert (
        "collect --market KR --ticker 005930.KS --years 2020:2025"
        in scripts["collect:opendart:kr:005930:raw"]
    )
    assert "pnpm collect:opendart:kr:005930:raw" in scripts["collect:kr:005930:raw"]
    assert "pnpm collect:pykrx:kr:005930:raw" in scripts["collect:kr:005930:raw"]
    assert "pnpm collect:marcap:kr:005930:raw" in scripts["collect:kr:005930:raw"]
    assert "--require-opendart --strict" in scripts["inspect:raw:kr:005930"]
    assert "--strict" in scripts["build:valuation-inputs:kr:005930"]
    assert (
        "load-kr-valuation-warehouse --tickers 005930.KS --strict"
        in scripts["load:valuation-warehouse:kr:005930"]
    )
    assert (
        "load-kr-valuation-postgres --tickers 005930.KS --strict"
        in scripts["load:valuation-postgres:kr:005930"]
    )
    assert "--dry-run --strict" in scripts["load:valuation-postgres:kr:005930:dry-run"]
    assert "005930.KS,000660.KS,402340.KS,005380.KS" in scripts["inspect:raw:kr:top10"]
    assert "build-kr-valuation-inputs --tickers 005930.KS,000660.KS,402340.KS" in scripts["build:valuation-inputs:kr:top10"]
    assert "load-kr-valuation-warehouse --tickers 005930.KS,000660.KS,402340.KS" in scripts["load:valuation-warehouse:kr:top10"]
    assert "load-kr-valuation-postgres --tickers 005930.KS,000660.KS,402340.KS" in scripts["load:valuation-postgres:kr:top10"]
    assert "--require-opendart --strict" in scripts["inspect:raw:kr:top10"]
    assert scripts["build:valuation-inputs:kr:top10"].endswith("--years 2020:2025 --strict")
    assert scripts["load:valuation-warehouse:kr:top10"].endswith("--strict")
    assert scripts["load:valuation-postgres:kr:top10"].endswith("--strict")
    assert scripts["load:valuation-postgres:kr:top10:dry-run"].endswith("--dry-run --strict")
    assert "kr-production-readiness --tickers 005930.KS,000660.KS,402340.KS" in scripts["readiness:kr:top10"]
    assert scripts["readiness:kr:top10"].endswith("--years 2020:2025 --summary-only")
    assert scripts["readiness:kr:top10:strict"].endswith(
        "--years 2020:2025 --require-consensus-forecast --summary-only --strict"
    )
    assert "--tickers 005930.KS --min-historical-years 3" in scripts["source:coverage:kr:005930"]
    assert scripts["template:consensus:kr:005930"].endswith(
        "--tickers 005930.KS --cases median --out storage/imports/consensus_005930.csv"
    )
    assert scripts["validate:consensus:kr:005930"].endswith(
        "--path storage/imports/consensus_005930.csv --tickers 005930.KS --cases median,current --case-mode any --strict"
    )
    assert scripts["workpaper:consensus:kr:005930"].endswith(
        "--tickers 005930.KS --csv-path storage/imports/consensus_005930.csv "
        "--template-cases median --validation-cases median,current --case-mode any "
        "--out storage/imports/consensus_005930_workpaper.md"
    )
    assert scripts["deploy:gate:kr:005930"].endswith(
        "--tickers 005930.KS --require-blob --require-consensus-forecast --summary-only --strict"
    )
    assert scripts["deploy:gate"].endswith("--require-blob --require-consensus-forecast --summary-only --strict")
    assert "--require-kr-top10-partial-audit" in scripts["smoke:api:kr:partial"]
    assert "run-kr-production-smoke.ps1" in scripts["workflow:kr:smoke"]

    kr_helper = (root / "scripts" / "run-kr-e2e.ps1").read_text(encoding="utf-8")
    secret_helper = (root / "scripts" / "set-local-secrets.ps1").read_text(encoding="utf-8")
    priority_helper = (root / "scripts" / "run-priority-e2e.ps1").read_text(encoding="utf-8")
    production_smoke_helper = (root / "scripts" / "run-kr-production-smoke.ps1").read_text(
        encoding="utf-8"
    )
    assert "Read-Host" in secret_helper
    assert "-AsSecureString" in secret_helper
    assert ".env.local" in secret_helper
    assert "DART_API_KEY" in secret_helper
    assert "Secret values were not printed" in secret_helper
    assert '[string]$Tickers = "005930.KS"' in kr_helper
    assert '"run-source-e2e"' in kr_helper
    assert '"--market", "KR"' in kr_helper
    assert '"--tickers", $Tickers' in kr_helper
    assert '[string]$Markets = "KR"' in priority_helper
    assert '[Parameter(Mandatory = $true)]' in production_smoke_helper
    assert '"workflow", "run", "kr-e2e.yml"' in production_smoke_helper
    assert '"run_api_smoke=true"' in production_smoke_helper
    assert '"preview_base_url=$BaseUrl"' in production_smoke_helper
    assert "gh auth status" in production_smoke_helper
    assert '[string]$RunLabel = ""' in production_smoke_helper
    assert '[string]$PartialTickers = "005930.KS"' in production_smoke_helper
    assert "[switch]$PartialAudit" in production_smoke_helper
    assert "Assert-PartialAuditTickers" in production_smoke_helper
    assert "PartialTickers is required when -PartialAudit is set." in production_smoke_helper
    assert "Partial audit tickers: $PartialTickers" in production_smoke_helper
    assert "kr-smoke-$SmokeMode-" in production_smoke_helper
    assert '"api_smoke_mode=$SmokeMode"' in production_smoke_helper
    assert '"partial_audit_tickers=$PartialTickers"' in production_smoke_helper
    assert '"run_label=$RunMarker"' in production_smoke_helper
    assert "Run marker: $RunMarker" in production_smoke_helper
    assert "[switch]$Watch" in production_smoke_helper
    assert "[switch]$PreflightOnly" in production_smoke_helper
    assert "Assert-HttpsBaseUrl -Value $BaseUrl" in production_smoke_helper
    assert 'Test-GitHubSecretExists -Name "PF_SESSION_COOKIE"' in production_smoke_helper
    assert 'gh variable get $Name --json value --jq ".value"' in production_smoke_helper
    assert 'Get-GitHubVariableValue -Name "KR_SMOKE_BASE_URL"' in production_smoke_helper
    assert "BaseUrl must exactly match the trusted KR_SMOKE_BASE_URL" in production_smoke_helper
    assert (
        "Preflight passed: gh auth, workflow, trusted HTTPS URL, and "
        "PF_SESSION_COOKIE secret are present."
        in production_smoke_helper
    )
    assert 'gh run list --workflow "kr-e2e.yml"' in production_smoke_helper
    assert "displayTitle" in production_smoke_helper
    assert "*$RunMarker*" in production_smoke_helper
    assert "gh run watch $run.databaseId --exit-status" in production_smoke_helper
    assert "$env:PF_SESSION_COOKIE" not in production_smoke_helper
    assert "Read-Host" not in production_smoke_helper


def test_kr_e2e_workflow_runs_kr_only_source_path():
    workflow = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "kr-e2e.yml"
    ).read_text(encoding="utf-8")

    assert "name: KR Top 10 E2E" in workflow
    assert "doctor --markets KR --strict" in workflow
    assert "run-source-e2e" in workflow
    assert "--market KR" in workflow
    assert "source-coverage" in workflow
    assert "deploy-gate" in workflow
    assert "005930.KS,000660.KS,402340.KS" in workflow
    assert "DATABASE_URL: ${{ secrets.DATABASE_URL }}" in workflow
    assert "OPENDART_API_KEY: ${{ secrets.OPENDART_API_KEY }}" in workflow
    assert "DART_API_KEY: ${{ secrets.DART_API_KEY }}" in workflow
    assert "BLOB_READ_WRITE_TOKEN: ${{ secrets.BLOB_READ_WRITE_TOKEN }}" in workflow
    assert "INPUT_YEARS: ${{ inputs.years }}" in workflow
    assert '--years "${INPUT_YEARS}"' in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "persist-credentials: false" in workflow
    assert "--market US" not in workflow
    assert "--market JP" not in workflow


def test_kr_e2e_workflow_can_run_protected_vercel_smoke():
    workflow = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "kr-e2e.yml"
    ).read_text(encoding="utf-8")

    assert "run_api_smoke:" in workflow
    assert "api_smoke_mode:" in workflow
    assert "partial_audit_tickers:" in workflow
    assert "preview_base_url:" in workflow
    assert "run_label:" in workflow
    assert "run-name: KR Top 10 E2E ${{ inputs.run_label }}" in workflow
    assert "PF_SESSION_COOKIE: ${{ secrets.PF_SESSION_COOKIE }}" in workflow
    assert "RUN_LABEL: ${{ inputs.run_label }}" in workflow
    assert "TRUSTED_SMOKE_BASE_URL: ${{ vars.KR_SMOKE_BASE_URL }}" in workflow
    assert "KR_SMOKE_BASE_URL repository variable is required" in workflow
    assert "preview_base_url must match the trusted KR_SMOKE_BASE_URL" in workflow
    assert "PF_SESSION_COOKIE secret is required when run_api_smoke=true." in workflow
    assert "api_smoke_mode must be either full or partial." in workflow
    assert "partial_audit_tickers is required when api_smoke_mode=partial." in workflow
    assert "Mark run label" in workflow
    assert "KR run label: $RUN_LABEL" in workflow
    assert "API smoke mode: ${INPUT_API_SMOKE_MODE}" in workflow
    assert "Partial audit tickers: ${INPUT_PARTIAL_AUDIT_TICKERS}" in workflow
    assert "Run protected Vercel KR API smoke" in workflow
    assert 'pnpm smoke:api:kr:partial -- \\' in workflow
    assert '--expect-kr-top10-partial-tickers "${INPUT_PARTIAL_AUDIT_TICKERS}"' in workflow
    assert 'pnpm smoke:api:kr -- --base-url "${TRUSTED_SMOKE_BASE_URL}"' in workflow
    install_block = workflow.split("- name: Install dependencies", 1)[1].split(
        "- name: Validate KR secrets", 1
    )[0]
    assert "${{ secrets." not in install_block


def test_source_coverage_builder_marks_core_and_consensus_readiness():
    summary = build_source_coverage_report(
        [
            {
                "ticker": "AAPL",
                "security_count": 1,
                "adjusted_years": 5,
                "price_years": 5,
                "market_cap_years": 5,
                "listed_shares_years": 5,
                "financial_fact_years": 5,
                "financial_fact_tags": 8,
                "latest_financial_fact_year": 2024,
                "financial_metric_years": 5,
                "financial_metric_keys": 8,
                "available_metric_keys": [
                    "basic_eps",
                    "diluted_eps",
                    "fcf_share",
                    "operating_cash_flow_share",
                    "sales_share",
                ],
                "consensus_forecast_years": 5,
                "consensus_valuation_years": 5,
                "consensus_snapshots": 15,
                "consensus_valuation_snapshots": 5,
                "source_documents": 3,
                "raw_objects": 2,
                "s1_periods": 5,
            },
            {
                "ticker": "CRM",
                "security_count": 1,
                "adjusted_years": 1,
                "price_years": 0,
                "source_documents": 1,
            },
        ],
        ["AAPL", "CRM"],
        min_historical_years=3,
        min_forecast_years=5,
    )

    assert summary["status"] == "partial"
    rows = {row["ticker"]: row for row in summary["tickers"]}
    assert rows["AAPL"]["core_ready"] is True
    assert rows["AAPL"]["consensus_forecast_ready"] is True
    assert rows["AAPL"]["counts"]["financial_fact_years"] == 5
    assert rows["AAPL"]["counts"]["market_cap_years"] == 5
    assert rows["AAPL"]["counts"]["listed_shares_years"] == 5
    assert rows["AAPL"]["available_metric_keys"] == [
        "basic_eps",
        "diluted_eps",
        "fcf_share",
        "operating_cash_flow_share",
        "sales_share",
    ]
    assert rows["AAPL"]["latest_years"]["financial_fact"] == 2024
    assert rows["AAPL"]["method_counts"]["s1"] == 5
    aapl_checks = {check["name"]: check for check in rows["AAPL"]["checks"]}
    assert aapl_checks["market_cap_evidence"]["ok"] is True
    assert aapl_checks["market_cap_evidence"]["required"] is True
    assert aapl_checks["listed_shares_evidence"]["ok"] is True
    assert aapl_checks["listed_shares_evidence"]["required"] is False
    assert rows["CRM"]["core_ready"] is False
    assert rows["CRM"]["missing_required"] == [
        "adjusted_earnings",
        "price_bars",
        "financial_metrics",
    ]
    assert summary["summary"]["missing_by_requirement"] == {
        "adjusted_earnings": ["CRM"],
        "financial_metrics": ["CRM"],
        "price_bars": ["CRM"],
    }
    action_ids = [action["id"] for action in summary["remediation"]["next_actions"]]
    assert action_ids == [
        "collect_sec_bulk",
        "load_sec_bulk_warehouse",
        "normalize_us_batch",
        "collect_stooq_prices_us",
    ]
    assert summary["remediation"]["next_actions"][1]["cli_commands"] == [
        (
            "python -m services.ingestion_worker.cli load-sec-bulk-warehouse "
            "--tickers CRM --persist"
        )
    ]
    assert summary["remediation"]["next_actions"][2]["github_actions"]["command"] == (
        "normalize_us_batch"
    )
    prerequisites = {item["name"]: item for item in summary["remediation"]["prerequisites"]}
    assert prerequisites["SEC_USER_AGENT"]["required"] is True


def test_kr_top_market_cap_coverage_requires_market_structure_evidence():
    summary = build_source_coverage_report(
        [
            {
                "ticker": "005930.KS",
                "security_count": 1,
                "adjusted_years": 5,
                "price_years": 5,
                "financial_metric_years": 5,
                "financial_metric_keys": 8,
                "source_documents": 3,
                "raw_objects": 2,
                "s3_periods": 5,
            }
        ],
        ["005930.KS"],
        min_historical_years=3,
    )

    assert summary["status"] == "partial"
    row = summary["tickers"][0]
    assert row["core_ready"] is False
    checks = {check["name"]: check for check in row["checks"]}
    assert checks["market_cap_evidence"]["required"] is True
    assert checks["listed_shares_evidence"]["required"] is True
    assert row["missing_required"] == [
        "market_cap_evidence",
        "listed_shares_evidence",
    ]
    assert summary["summary"]["missing_core"] == ["005930.KS"]
    assert summary["summary"]["missing_by_requirement"] == {
        "listed_shares_evidence": ["005930.KS"],
        "market_cap_evidence": ["005930.KS"],
    }
    actions = {action["id"]: action for action in summary["remediation"]["next_actions"]}
    assert actions["collect_marcap"]["requirements"] == [
        "price_bars",
        "market_cap",
        "listed_shares",
    ]
    assert actions["collect_marcap"]["tickers"] == ["005930.KS"]


def test_us_top_market_cap_coverage_requires_market_cap_evidence():
    summary = build_source_coverage_report(
        [
            {
                "ticker": "NVDA",
                "security_count": 1,
                "adjusted_years": 5,
                "price_years": 5,
                "financial_metric_years": 5,
                "financial_metric_keys": 8,
                "source_documents": 3,
                "raw_objects": 2,
                "s1_periods": 5,
            }
        ],
        ["NVDA"],
        min_historical_years=3,
    )

    row = summary["tickers"][0]
    checks = {check["name"]: check for check in row["checks"]}
    assert row["core_ready"] is False
    assert checks["market_cap_evidence"]["required"] is True
    assert checks["listed_shares_evidence"]["required"] is False
    assert row["missing_required"] == ["market_cap_evidence"]
    actions = {action["id"]: action for action in summary["remediation"]["next_actions"]}
    assert "import_market_structure_csv_us" in actions
    assert actions["import_market_structure_csv_us"]["requirements"] == ["market_cap_evidence"]


def test_source_coverage_accepts_financial_facts_as_source_evidence():
    summary = build_source_coverage_report(
        [
            {
                "ticker": "AAPL",
                "security_count": 1,
                "adjusted_years": 5,
                "price_years": 5,
                "market_cap_years": 5,
                "financial_fact_years": 5,
                "financial_fact_tags": 7,
                "financial_metric_years": 5,
                "financial_metric_keys": 3,
                "source_documents": 0,
                "raw_objects": 0,
                "s1_periods": 5,
            },
        ],
        ["AAPL"],
        min_historical_years=3,
    )

    row = summary["tickers"][0]
    source_check = next(check for check in row["checks"] if check["name"] == "source_evidence")
    assert source_check["ok"] is True
    assert row["core_ready"] is True


def test_source_coverage_requires_metric_values_for_core_readiness():
    summary = build_source_coverage_report(
        [
            {
                "ticker": "AAPL",
                "security_count": 1,
                "adjusted_years": 5,
                "price_years": 5,
                "market_cap_years": 5,
                "financial_metric_years": 0,
                "financial_metric_keys": 0,
                "source_documents": 3,
                "s1_periods": 5,
            },
        ],
        ["AAPL"],
        min_historical_years=3,
    )

    row = summary["tickers"][0]
    metric_check = next(check for check in row["checks"] if check["name"] == "financial_metrics")
    assert metric_check["required"] is True
    assert metric_check["ok"] is False
    assert row["core_ready"] is False
    assert row["missing_required"] == ["financial_metrics"]
    assert summary["summary"]["missing_by_requirement"] == {"financial_metrics": ["AAPL"]}
    actions = summary["remediation"]["next_actions"]
    assert [action["id"] for action in actions] == [
        "collect_sec_bulk",
        "load_sec_bulk_warehouse",
    ]
    assert actions[0]["github_actions"]["workflow"] == "ingestion-worker.yml"


def test_source_coverage_can_require_consensus_forecast_readiness():
    summary = build_source_coverage_report(
        [
            {
                "ticker": "AAPL",
                "security_count": 1,
                "adjusted_years": 5,
                "price_years": 5,
                "market_cap_years": 5,
                "financial_metric_years": 5,
                "financial_metric_keys": 3,
                "source_documents": 3,
                "s1_periods": 5,
                "consensus_forecast_years": 2,
                "consensus_valuation_years": 2,
            },
        ],
        ["AAPL"],
        min_historical_years=3,
        min_forecast_years=5,
        require_consensus_forecast=True,
    )

    row = summary["tickers"][0]
    assert summary["status"] == "partial"
    assert summary["requirements"]["consensus_forecast_required"] is True
    assert row["core_ready"] is True
    assert row["consensus_forecast_ready"] is False
    assert row["missing_required"] == ["consensus_forecast"]
    assert summary["remediation"]["next_actions"] == [
        {
            "id": "consensus_workpaper",
            "priority": 38,
            "requirements": ["consensus_forecast"],
            "tickers": ["AAPL"],
            "description": (
                "Create a forecast evidence workpaper before filling the 1Y-5Y CSV; "
                "document accepted sources, blocked inputs, required rows, and trace anchors."
            ),
            "cli_commands": [
                (
                    "python -m services.ingestion_worker.cli consensus-workpaper "
                    "--tickers AAPL --csv-path storage/imports/consensus_aapl.csv "
                    "--template-cases median --validation-cases median,current "
                    "--case-mode any --out storage/imports/consensus_aapl_workpaper.md"
                )
            ],
            "github_actions": {
                "workflow": "ingestion-worker.yml",
                "command": "consensus_workpaper",
                "coverage_tickers": "AAPL",
                "csv_path": "storage/imports/consensus_aapl.csv",
                "persist": False,
            },
        },
        {
            "id": "export_consensus_template",
            "priority": 39,
            "requirements": ["consensus_forecast"],
            "tickers": ["AAPL"],
            "description": (
                "Create a blank 1Y-5Y forecast snapshot CSV template; "
                "fill EPS, source, and a trace anchor from traceable evidence before import."
            ),
            "cli_commands": [
                (
                    "python -m services.ingestion_worker.cli export-consensus-template "
                    "--tickers AAPL --cases median --out storage/imports/consensus_aapl.csv"
                )
            ],
            "github_actions": {
                "workflow": "ingestion-worker.yml",
                "command": "export_consensus_template",
                "coverage_tickers": "AAPL",
                "csv_path": "storage/imports/consensus_aapl.csv",
                "persist": False,
            },
        },
        {
            "id": "validate_consensus_csv",
            "priority": 40,
            "requirements": ["consensus_forecast"],
            "tickers": ["AAPL"],
            "description": (
                "Validate the filled 1Y-5Y forecast CSV for required ticker-year "
                "coverage, median/current cases, trace anchors, and blocked "
                "template quality statuses before import."
            ),
            "cli_commands": [
                (
                    "python -m services.ingestion_worker.cli validate-consensus-csv "
                    "--path storage/imports/consensus_aapl.csv "
                    "--tickers AAPL --cases median,current --case-mode any --strict"
                )
            ],
            "github_actions": {
                "workflow": "ingestion-worker.yml",
                "command": "validate_consensus_csv",
                "coverage_tickers": "AAPL",
                "csv_path": "storage/imports/consensus_aapl.csv",
                "persist": False,
            },
        },
        {
            "id": "import_consensus_csv",
            "priority": 41,
            "requirements": ["consensus_forecast"],
            "tickers": ["AAPL"],
            "description": (
                "Import user-verified 1Y-5Y consensus forecast snapshots; "
                "do not synthesize analyst estimates or omit trace anchors."
            ),
            "cli_commands": [
                (
                    "python -m services.ingestion_worker.cli import-consensus-csv "
                    "--path storage/imports/consensus_aapl.csv --persist"
                )
            ],
            "github_actions": {
                "workflow": "ingestion-worker.yml",
                "command": "import_consensus_csv",
                "coverage_tickers": "AAPL",
                "csv_path": "storage/imports/consensus_aapl.csv",
                "persist": True,
            },
        }
    ]


def test_source_coverage_uses_single_ticker_consensus_csv_path_for_samsung():
    summary = build_source_coverage_report(
        [
            {
                "ticker": "005930.KS",
                "security_count": 1,
                "adjusted_years": 5,
                "price_years": 5,
                "market_cap_years": 5,
                "listed_shares_years": 5,
                "financial_metric_years": 5,
                "financial_metric_keys": 3,
                "source_documents": 3,
                "s1_periods": 5,
                "consensus_forecast_years": 0,
                "consensus_valuation_years": 0,
            },
        ],
        ["005930.KS"],
        min_historical_years=3,
        min_forecast_years=5,
        require_consensus_forecast=True,
    )

    actions = summary["remediation"]["next_actions"]
    assert [action["id"] for action in actions] == [
        "consensus_workpaper",
        "export_consensus_template",
        "export_deterministic_forecast_csv",
        "validate_consensus_csv",
        "import_consensus_csv",
    ]
    assert all(
        action["github_actions"]["csv_path"] == "storage/imports/consensus_005930.csv"
        for action in actions
    )
    assert "--csv-path storage/imports/consensus_005930.csv" in actions[0]["cli_commands"][0]
    assert "--cases median --out storage/imports/consensus_005930.csv" in actions[1]["cli_commands"][0]
    assert "--tickers 005930.KS --cases median --out storage/imports/consensus_005930.csv" in actions[2]["cli_commands"][0]
    assert "--path storage/imports/consensus_005930.csv" in actions[3]["cli_commands"][0]
    assert "--path storage/imports/consensus_005930.csv" in actions[4]["cli_commands"][0]


def test_source_coverage_reports_template_pending_consensus_csv(monkeypatch, tmp_path):
    imports_dir = tmp_path / "storage" / "imports"
    imports_dir.mkdir(parents=True)
    csv_path = imports_dir / "consensus_005930.csv"
    csv_path.write_text(
        "ticker,fiscal_year,snapshot_date,estimate_case,estimate_eps,growth_rate_pct,"
        "analyst_count,currency,source,source_url,metric_key,period_end,quality_status,"
        "source_document_id,filing_id,notes\n"
        "005930.KS,2026,2026-07-01,median,,,,KRW,,,adjusted_operating_eps,,"
        "template_pending_source_value,,,Fill source-backed value before import\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    summary = build_source_coverage_report(
        [
            {
                "ticker": "005930.KS",
                "security_count": 1,
                "adjusted_years": 5,
                "price_years": 5,
                "market_cap_years": 5,
                "listed_shares_years": 5,
                "financial_metric_years": 5,
                "financial_metric_keys": 3,
                "source_documents": 3,
                "s1_periods": 5,
                "consensus_forecast_years": 0,
                "consensus_valuation_years": 0,
            },
        ],
        ["005930.KS"],
        min_historical_years=3,
        min_forecast_years=5,
        require_consensus_forecast=True,
    )

    preflight = summary["remediation"]["forecast_csv_preflight"]
    assert preflight["path"] == "storage/imports/consensus_005930.csv"
    assert preflight["exists"] is True
    assert preflight["status"] == "template_pending"
    assert preflight["rows"] == 1
    assert preflight["candidate_rows"] == 1
    assert preflight["ready_rows"] == 0
    assert preflight["missing_value_rows"] == 1
    assert preflight["missing_trace_rows"] == 1
    assert preflight["missing_periods"] == [
        {
            "ticker": "005930.KS",
            "fiscal_year": 2026,
            "estimate_cases_allowed": ["median", "current"],
        },
        {
            "ticker": "005930.KS",
            "fiscal_year": 2027,
            "estimate_cases_allowed": ["median", "current"],
        },
        {
            "ticker": "005930.KS",
            "fiscal_year": 2028,
            "estimate_cases_allowed": ["median", "current"],
        },
        {
            "ticker": "005930.KS",
            "fiscal_year": 2029,
            "estimate_cases_allowed": ["median", "current"],
        },
        {
            "ticker": "005930.KS",
            "fiscal_year": 2030,
            "estimate_cases_allowed": ["median", "current"],
        },
    ]
    assert preflight["missing_manual_notes_rows"] == 0
    assert preflight["invalid_value_rows"] == 0
    assert preflight["invalid_currency_rows"] == 0
    assert preflight["blocked_evidence_rows"] == 0
    assert preflight["manual_assumption_ready_rows"] == 0
    assert preflight["external_consensus_ready_rows"] == 0
    assert preflight["import_ready_candidate"] is False


def test_source_coverage_reports_candidate_ready_consensus_csv(monkeypatch, tmp_path):
    imports_dir = tmp_path / "storage" / "imports"
    imports_dir.mkdir(parents=True)
    csv_path = imports_dir / "consensus_aapl.csv"
    csv_path.write_text(
        "ticker,fiscal_year,snapshot_date,estimate_case,estimate_eps,growth_rate_pct,"
        "analyst_count,currency,source,source_url,metric_key,period_end,quality_status,"
        "source_document_id,filing_id,notes\n"
        "AAPL,2026,2026-07-01,median,7.10,7.0,30,USD,user_consensus_csv,,"
        "adjusted_operating_eps,,source_backed_consensus_snapshot,operator-doc-2026,,\n"
        "AAPL,2027,2026-07-01,current,7.60,7.0,30,USD,user_consensus_csv,,"
        "adjusted_operating_eps,,source_backed_consensus_snapshot,operator-doc-2027,,\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    summary = build_source_coverage_report(
        [
            {
                "ticker": "AAPL",
                "security_count": 1,
                "adjusted_years": 5,
                "price_years": 5,
                "market_cap_years": 5,
                "financial_metric_years": 5,
                "financial_metric_keys": 3,
                "source_documents": 3,
                "s1_periods": 5,
                "consensus_forecast_years": 0,
                "consensus_valuation_years": 0,
            },
        ],
        ["AAPL"],
        min_historical_years=3,
        min_forecast_years=2,
        require_consensus_forecast=True,
    )

    preflight = summary["remediation"]["forecast_csv_preflight"]
    assert preflight["path"] == "storage/imports/consensus_aapl.csv"
    assert preflight["exists"] is True
    assert preflight["status"] == "candidate_ready"
    assert preflight["required_periods"] == 2
    assert preflight["covered_periods"] == 2
    assert preflight["ready_rows"] == 2
    assert preflight["missing_periods"] == []
    assert preflight["invalid_value_rows"] == 0
    assert preflight["invalid_currency_rows"] == 0
    assert preflight["blocked_evidence_rows"] == 0
    assert preflight["manual_assumption_ready_rows"] == 0
    assert preflight["external_consensus_ready_rows"] == 2
    assert preflight["assumption_types"] == {
        "manual_assumption": 0,
        "external_consensus": 2,
    }
    assert preflight["import_ready_candidate"] is True


def test_source_coverage_preflight_blocks_manual_assumption_without_notes(
    monkeypatch, tmp_path
):
    imports_dir = tmp_path / "storage" / "imports"
    imports_dir.mkdir(parents=True)
    csv_path = imports_dir / "consensus_005930.csv"
    csv_path.write_text(
        "ticker,fiscal_year,snapshot_date,estimate_case,estimate_eps,growth_rate_pct,"
        "analyst_count,currency,source,source_url,metric_key,period_end,quality_status,"
        "source_document_id,filing_id,notes\n"
        "005930.KS,2026,2026-07-01,median,44800,,0,KRW,"
        "manual_forecast_assumption,,adjusted_operating_eps,,manual_forecast_assumption,"
        "operator-doc-2026,,\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    summary = build_source_coverage_report(
        [
            {
                "ticker": "005930.KS",
                "security_count": 1,
                "adjusted_years": 5,
                "price_years": 5,
                "market_cap_years": 5,
                "listed_shares_years": 5,
                "financial_metric_years": 5,
                "financial_metric_keys": 3,
                "source_documents": 3,
                "s1_periods": 5,
                "consensus_forecast_years": 0,
                "consensus_valuation_years": 0,
            },
        ],
        ["005930.KS"],
        min_historical_years=3,
        min_forecast_years=1,
        require_consensus_forecast=True,
    )

    preflight = summary["remediation"]["forecast_csv_preflight"]
    assert preflight["status"] == "invalid_candidate"
    assert preflight["candidate_rows"] == 1
    assert preflight["ready_rows"] == 0
    assert preflight["missing_value_rows"] == 0
    assert preflight["missing_trace_rows"] == 0
    assert preflight["missing_manual_notes_rows"] == 1
    assert preflight["invalid_value_rows"] == 0
    assert preflight["invalid_currency_rows"] == 0
    assert preflight["blocked_evidence_rows"] == 0
    assert preflight["manual_assumption_ready_rows"] == 0
    assert preflight["external_consensus_ready_rows"] == 0
    assert preflight["import_ready_candidate"] is False


def test_source_coverage_preflight_detects_manual_alias_without_notes(
    monkeypatch, tmp_path
):
    imports_dir = tmp_path / "storage" / "imports"
    imports_dir.mkdir(parents=True)
    csv_path = imports_dir / "consensus_005930.csv"
    csv_path.write_text(
        "ticker,fiscal_year,snapshot_date,estimate_case,estimate_eps,growth_rate_pct,"
        "analyst_count,currency,source,source_url,metric_key,period_end,quality_status,"
        "source_document_id,filing_id,notes\n"
        "005930.KS,2026,2026-07-01,median,44800,,0,KRW,"
        "operator_manual_forecast,,adjusted_operating_eps,,source_backed_manual,"
        "operator-doc-2026,,\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    summary = build_source_coverage_report(
        [
            {
                "ticker": "005930.KS",
                "security_count": 1,
                "adjusted_years": 5,
                "price_years": 5,
                "market_cap_years": 5,
                "listed_shares_years": 5,
                "financial_metric_years": 5,
                "financial_metric_keys": 3,
                "source_documents": 3,
                "s1_periods": 5,
                "consensus_forecast_years": 0,
                "consensus_valuation_years": 0,
            },
        ],
        ["005930.KS"],
        min_historical_years=3,
        min_forecast_years=1,
        require_consensus_forecast=True,
    )

    preflight = summary["remediation"]["forecast_csv_preflight"]
    assert preflight["status"] == "invalid_candidate"
    assert preflight["ready_rows"] == 0
    assert preflight["missing_manual_notes_rows"] == 1
    assert preflight["manual_assumption_ready_rows"] == 0
    assert preflight["external_consensus_ready_rows"] == 0


def test_source_coverage_preflight_blocks_proprietary_or_fixture_sources(
    monkeypatch, tmp_path
):
    imports_dir = tmp_path / "storage" / "imports"
    imports_dir.mkdir(parents=True)
    csv_path = imports_dir / "consensus_aapl.csv"
    csv_path.write_text(
        "ticker,fiscal_year,snapshot_date,estimate_case,estimate_eps,growth_rate_pct,"
        "analyst_count,currency,source,source_url,metric_key,period_end,quality_status,"
        "source_document_id,filing_id,notes\n"
        "AAPL,2026,2026-07-01,median,7.10,7.0,30,USD,"
        "FAST Graphs screenshot,https://app.fastgraphs.com/security/fastgraphs/summary,"
        "adjusted_operating_eps,,source_backed_consensus_snapshot,operator-doc-2026,,\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    summary = build_source_coverage_report(
        [
            {
                "ticker": "AAPL",
                "security_count": 1,
                "adjusted_years": 5,
                "price_years": 5,
                "market_cap_years": 5,
                "financial_metric_years": 5,
                "financial_metric_keys": 3,
                "source_documents": 3,
                "s1_periods": 5,
                "consensus_forecast_years": 0,
                "consensus_valuation_years": 0,
            },
        ],
        ["AAPL"],
        min_historical_years=3,
        min_forecast_years=1,
        require_consensus_forecast=True,
    )

    preflight = summary["remediation"]["forecast_csv_preflight"]
    assert preflight["status"] == "invalid_candidate"
    assert preflight["ready_rows"] == 0
    assert preflight["blocked_evidence_rows"] == 1
    assert preflight["import_ready_candidate"] is False


def test_source_coverage_keeps_shared_consensus_csv_path_for_multi_ticker_batches():
    summary = build_source_coverage_report(
        [
            {
                "ticker": "AAPL",
                "security_count": 1,
                "adjusted_years": 5,
                "price_years": 5,
                "market_cap_years": 5,
                "financial_metric_years": 5,
                "financial_metric_keys": 3,
                "source_documents": 3,
                "s1_periods": 5,
                "consensus_forecast_years": 0,
                "consensus_valuation_years": 0,
            },
            {
                "ticker": "NVDA",
                "security_count": 1,
                "adjusted_years": 5,
                "price_years": 5,
                "market_cap_years": 5,
                "financial_metric_years": 5,
                "financial_metric_keys": 3,
                "source_documents": 3,
                "s1_periods": 5,
                "consensus_forecast_years": 0,
                "consensus_valuation_years": 0,
            },
        ],
        ["AAPL", "NVDA"],
        min_historical_years=3,
        min_forecast_years=5,
        require_consensus_forecast=True,
    )

    actions = summary["remediation"]["next_actions"]
    assert [action["id"] for action in actions] == [
        "consensus_workpaper",
        "export_consensus_template",
        "validate_consensus_csv",
        "import_consensus_csv",
    ]
    assert all(
        action["github_actions"]["csv_path"] == "storage/imports/consensus_estimates.csv"
        for action in actions
    )
    assert "--csv-path storage/imports/consensus_estimates.csv" in actions[0]["cli_commands"][0]
    assert "--cases median --out storage/imports/consensus_estimates.csv" in actions[1]["cli_commands"][0]
    assert "--path storage/imports/consensus_estimates.csv" in actions[2]["cli_commands"][0]
    assert "--path storage/imports/consensus_estimates.csv" in actions[3]["cli_commands"][0]


def test_source_coverage_requires_median_or_current_consensus_for_forecast_gate():
    summary = build_source_coverage_report(
        [
            {
                "ticker": "AAPL",
                "security_count": 1,
                "adjusted_years": 5,
                "price_years": 5,
                "market_cap_years": 5,
                "financial_metric_years": 5,
                "financial_metric_keys": 3,
                "source_documents": 3,
                "s1_periods": 5,
                "consensus_forecast_years": 5,
                "consensus_valuation_years": 0,
                "consensus_snapshots": 10,
                "consensus_valuation_snapshots": 0,
            },
        ],
        ["AAPL"],
        min_historical_years=3,
        min_forecast_years=5,
        require_consensus_forecast=True,
    )

    row = summary["tickers"][0]
    consensus_check = next(
        check for check in row["checks"] if check["name"] == "consensus_forecast"
    )
    assert row["core_ready"] is True
    assert row["consensus_forecast_ready"] is False
    assert row["missing_required"] == ["consensus_forecast"]
    assert row["counts"]["consensus_forecast_years"] == 5
    assert row["counts"]["consensus_valuation_years"] == 0
    assert consensus_check["detail"] == (
        "needs 5 forecast years with median/current adjusted operating EPS snapshots"
    )


def test_source_coverage_remediation_groups_kr_and_jp_actions():
    summary = build_source_coverage_report(
        [
            {
                "ticker": "005930.KS",
                "security_count": 1,
                "adjusted_years": 0,
                "price_years": 0,
                "source_documents": 0,
            },
            {
                "ticker": "7203.T",
                "security_count": 1,
                "adjusted_years": 0,
                "price_years": 0,
                "source_documents": 0,
            },
        ],
        ["005930.KS", "7203.T"],
        min_historical_years=3,
    )

    actions = {action["id"]: action for action in summary["remediation"]["next_actions"]}
    first_action = summary["remediation"]["next_actions"][0]
    assert first_action["id"] == "run_priority_e2e"
    assert first_action["market_order"] == ["KR", "JP"]
    assert first_action["github_actions"]["priority_e2e_markets"] == "KR,JP"
    assert "run-priority-e2e --markets KR,JP" in first_action["cli_commands"][0]
    assert actions["collect_opendart"]["tickers"] == ["005930.KS"]
    assert actions["collect_opendart"]["github_actions"]["command"] == "collect_market"
    assert actions["collect_pykrx_prices"]["tickers"] == ["005930.KS"]
    assert actions["collect_marcap"]["tickers"] == ["005930.KS"]
    assert actions["collect_marcap"]["github_actions"]["command"] == "collect_marcap"
    assert actions["collect_marcap"]["requirements"] == [
        "price_bars",
        "market_cap",
        "listed_shares",
    ]
    assert actions["collect_jquants"]["tickers"] == ["7203.T"]
    assert actions["collect_edinet"]["tickers"] == ["7203.T"]
    assert actions["collect_stooq_prices_jp"]["github_actions"]["market"] == "JP"
    assert actions["import_market_structure_csv_jp"]["requirements"] == [
        "market_cap_evidence"
    ]
    prerequisites = {item["name"]: item for item in summary["remediation"]["prerequisites"]}
    assert "OPENDART_API_KEY" in prerequisites
    assert "JQUANTS credentials" in prerequisites
    assert "EDINET_API_KEY" in prerequisites


def test_deployment_preflight_static_checks_repo_shape(monkeypatch):
    for key in (
        "DATABASE_URL",
        "DATA_BACKEND",
        "SEC_USER_AGENT",
        "OPENDART_API_KEY",
        "DART_API_KEY",
        "JQUANTS_REFRESH_TOKEN",
        "JQUANTS_EMAIL",
        "JQUANTS_PASSWORD",
        "BLOB_READ_WRITE_TOKEN",
        "FRED_API_KEY",
        "AUTH_REQUIRED",
        "API_AUTH_REQUIRED",
        "API_AUTH_DISABLED",
        "AUTH_SECRET",
        "AUTH_GITHUB_ID",
        "AUTH_GITHUB_SECRET",
        "AUTH_ALLOWED_EMAILS",
    ):
        monkeypatch.delenv(key, raising=False)

    summary = deployment_preflight(markets="US,KR,JP", strict=False, root=Path.cwd())

    assert summary["status"] == "ok"
    checks = {check["name"]: check for check in summary["checks"]}
    assert checks["vercel_api_route"]["ok"] is True
    assert checks["vercel_next_route_fallback_absent"]["ok"] is True
    assert checks["alembic_single_head"]["ok"] is True
    assert checks["production_fixture_fallback_documented_false"]["ok"] is True
    assert checks["blob_sync_dry_run_gate"]["ok"] is True
    assert checks["deploy_gate_action_present"]["ok"] is True
    assert checks["normalize_us_batch_action_present"]["ok"] is True
    assert checks["collect_sec_bulk_action_present"]["ok"] is True
    assert checks["load_sec_bulk_warehouse_action_present"]["ok"] is True
    assert checks["run_source_e2e_action_present"]["ok"] is True
    assert checks["kr_e2e_action_present"]["ok"] is True
    assert checks["run_p1_e2e_action_present"]["ok"] is True
    assert checks["data_lake_plan_action_present"]["ok"] is True
    assert checks["collect_fred_action_present"]["ok"] is True
    assert checks["collect_ecos_action_present"]["ok"] is True
    assert checks["collect_kosis_action_present"]["ok"] is True
    assert checks["collect_estat_action_present"]["ok"] is True
    assert checks["collect_stooq_action_present"]["ok"] is True
    assert checks["collect_fdr_action_present"]["ok"] is True
    assert checks["collect_pykrx_action_present"]["ok"] is True
    assert checks["collect_marcap_action_present"]["ok"] is True
    assert checks["collect_jquants_action_present"]["ok"] is True
    assert checks["collect_edinet_action_present"]["ok"] is True
    assert checks["import_fnguide_action_present"]["ok"] is True
    assert checks["export_consensus_template_action_present"]["ok"] is True
    assert checks["consensus_workpaper_action_present"]["ok"] is True
    assert checks["forecast_gate_required_for_deploy"]["ok"] is True
    assert checks["source_secret_audit_action_present"]["ok"] is True
    assert checks["source_metadata_secret_audit"]["ok"] is True


def test_deployment_preflight_strict_requires_private_runtime_config(monkeypatch):
    for key in (
        "DATABASE_URL",
        "DATA_BACKEND",
        "SEC_USER_AGENT",
        "OPENDART_API_KEY",
        "DART_API_KEY",
        "JQUANTS_REFRESH_TOKEN",
        "JQUANTS_EMAIL",
        "JQUANTS_PASSWORD",
        "BLOB_READ_WRITE_TOKEN",
        "FRED_API_KEY",
        "AUTH_REQUIRED",
        "API_AUTH_REQUIRED",
        "API_AUTH_DISABLED",
        "AUTH_SECRET",
        "AUTH_GITHUB_ID",
        "AUTH_GITHUB_SECRET",
        "AUTH_ALLOWED_EMAILS",
    ):
        monkeypatch.delenv(key, raising=False)

    summary = deployment_preflight(
        markets="US,KR,JP",
        require_blob=True,
        strict=True,
        root=Path.cwd(),
    )

    assert summary["status"] == "needs_configuration"
    assert "DATABASE_URL" in summary["missing_required"]
    assert "AUTH_REQUIRED=true" in summary["missing_required"]
    assert "API_AUTH_REQUIRED=true" in summary["missing_required"]
    assert "AUTH_SECRET" in summary["missing_required"]


def test_deployment_gate_combines_preflight_and_source_coverage(monkeypatch):
    for key in (
        "DATABASE_URL",
        "DATA_BACKEND",
        "SEC_USER_AGENT",
        "BLOB_READ_WRITE_TOKEN",
        "AUTH_REQUIRED",
        "API_AUTH_REQUIRED",
        "AUTH_SECRET",
        "AUTH_GITHUB_ID",
        "AUTH_GITHUB_SECRET",
        "AUTH_ALLOWED_EMAILS",
    ):
        monkeypatch.delenv(key, raising=False)

    summary = deployment_gate(
        markets="US",
        tickers="AAPL,NVDA",
        require_blob=True,
        strict=True,
        root=Path.cwd(),
    )

    assert summary["status"] == "needs_configuration"
    assert summary["preflight"]["status"] == "needs_configuration"
    assert summary["source_coverage"]["status"] == "missing"
    assert "DATABASE_URL" in summary["missing_required"]
    assert "source_coverage:AAPL" in summary["missing_required"]
    assert "source_coverage:NVDA" in summary["missing_required"]


def test_deployment_gate_reports_source_data_gap_when_config_is_static_ok(monkeypatch):
    for key in (
        "DATABASE_URL",
        "DATA_BACKEND",
        "SEC_USER_AGENT",
        "BLOB_READ_WRITE_TOKEN",
        "AUTH_REQUIRED",
        "API_AUTH_REQUIRED",
        "AUTH_SECRET",
        "AUTH_GITHUB_ID",
        "AUTH_GITHUB_SECRET",
        "AUTH_ALLOWED_EMAILS",
    ):
        monkeypatch.delenv(key, raising=False)

    summary = deployment_gate(
        markets="US",
        tickers="AAPL",
        require_blob=False,
        strict=False,
        root=Path.cwd(),
    )

    assert summary["status"] == "needs_source_data"
    assert summary["preflight"]["status"] == "ok"
    assert summary["source_coverage"]["status"] == "missing"
    assert summary["missing_required"] == ["source_coverage:AAPL"]


def test_deployment_gate_can_block_on_missing_consensus_forecast(monkeypatch):
    for key in (
        "DATABASE_URL",
        "DATA_BACKEND",
        "SEC_USER_AGENT",
        "BLOB_READ_WRITE_TOKEN",
        "AUTH_REQUIRED",
        "API_AUTH_REQUIRED",
        "AUTH_SECRET",
        "AUTH_GITHUB_ID",
        "AUTH_GITHUB_SECRET",
        "AUTH_ALLOWED_EMAILS",
    ):
        monkeypatch.delenv(key, raising=False)

    def coverage(*args, **kwargs):
        return build_source_coverage_report(
            [
                {
                    "ticker": "AAPL",
                    "security_count": 1,
                    "adjusted_years": 5,
                    "price_years": 5,
                    "market_cap_years": 5,
                    "listed_shares_years": 5,
                    "financial_metric_years": 5,
                    "financial_metric_keys": 3,
                    "source_documents": 2,
                    "s1_periods": 5,
                    "consensus_forecast_years": 0,
                },
            ],
            ["AAPL"],
            require_consensus_forecast=kwargs["require_consensus_forecast"],
        )

    monkeypatch.setattr("services.ingestion_worker.cli.source_coverage_from_postgres", coverage)

    summary = deployment_gate(
        markets="US",
        tickers="AAPL",
        require_consensus_forecast=True,
        strict=False,
        root=Path.cwd(),
    )

    assert summary["status"] == "needs_source_data"
    assert summary["preflight"]["status"] == "ok"
    assert summary["source_coverage"]["summary"]["missing_core"] == []
    assert summary["source_coverage"]["summary"]["missing_consensus_forecast"] == ["AAPL"]
    assert summary["missing_required"] == ["consensus_forecast:AAPL"]


def test_deployment_gate_output_summary_is_operator_compact(monkeypatch):
    for key in (
        "DATABASE_URL",
        "DATA_BACKEND",
        "SEC_USER_AGENT",
        "BLOB_READ_WRITE_TOKEN",
        "AUTH_REQUIRED",
        "API_AUTH_REQUIRED",
        "AUTH_SECRET",
        "AUTH_GITHUB_ID",
        "AUTH_GITHUB_SECRET",
        "AUTH_ALLOWED_EMAILS",
    ):
        monkeypatch.delenv(key, raising=False)

    summary = deployment_gate(
        markets="US",
        tickers="AAPL,NVDA",
        require_blob=True,
        require_consensus_forecast=True,
        strict=True,
        root=Path.cwd(),
    )
    compact = _deployment_gate_output_summary(summary)

    assert compact["status"] == "needs_configuration"
    assert compact["mode"] == "strict"
    assert compact["preflight_status"] == "needs_configuration"
    assert compact["source_coverage_status"] == "missing"
    assert compact["tickers_expected"] == 2
    assert compact["core_ready"] == 0
    assert compact["consensus_forecast_ready"] == 0
    assert compact["missing_required_count"] == len(summary["missing_required"])
    assert "DATABASE_URL" in compact["preflight_missing_required"]
    assert "source_coverage:AAPL" in compact["missing_required"]
    assert "preflight" not in compact
    assert "source_coverage" not in compact


def test_kr_production_readiness_reports_local_ready_before_postgres(monkeypatch):
    def cache_coverage(tickers):
        rows = [
            {
                "ticker": ticker,
                "valuation_ready": True,
                "coverage_status": "complete",
                "quality_flags": [],
            }
            for ticker in tickers
        ]
        return {
            "market": "KR",
            "coverage_status": "complete",
            "quality_status": "source_backed_cache_complete",
            "summary": {
                "tickers_expected": len(rows),
                "cache_files_found": len(rows),
                "valuation_ready": len(rows),
                "complete": len(rows),
                "partial_source_backed": 0,
                "missing": 0,
                "full_coverage_ready": len(rows),
                "financial_numbers_allowed": len(rows),
            },
            "quality_flags": [],
            "rows": rows,
            "source_trace": {"source": "kr_valuation_input_cache"},
        }

    def coverage(*args, **kwargs):
        return build_source_coverage_report(
            [],
            ["005930.KS", "000660.KS"],
            postgres_reachable=False,
            error="not_configured",
        )

    monkeypatch.setattr(
        "services.ingestion_worker.cli.kr_valuation_cache_universe_coverage",
        cache_coverage,
    )
    monkeypatch.setattr("services.ingestion_worker.cli.source_coverage_report", coverage)

    summary = kr_production_readiness(tickers="005930.KS,000660.KS")

    assert summary["status"] == "ready_for_protected_smoke"
    assert summary["summary"]["local_cache_ready"] is True
    assert summary["summary"]["production_ready"] is False
    assert summary["missing_required"] == [
        "source_coverage:005930.KS",
        "source_coverage:000660.KS",
    ]
    assert [command["id"] for command in summary["next_commands"]] == [
        "local_raw_dry_run",
        "build_kr_valuation_inputs",
        "load_kr_valuation_warehouse",
        "load_kr_valuation_postgres",
        "source_coverage",
        "protected_partial_smoke",
        "full_production_gate",
    ]
    assert (
        "load-kr-valuation-postgres --tickers 005930.KS,000660.KS"
        in summary["next_commands"][3]["command"]
    )
    assert summary["next_commands"][4]["command"].endswith("--strict")


def test_kr_production_readiness_separates_local_warehouse_from_postgres(monkeypatch):
    def cache_coverage(tickers):
        rows = [
            {
                "ticker": ticker,
                "valuation_ready": True,
                "coverage_status": "complete",
                "quality_flags": [],
            }
            for ticker in tickers
        ]
        return {
            "market": "KR",
            "coverage_status": "complete",
            "quality_status": "source_backed_cache_complete",
            "summary": {
                "tickers_expected": len(rows),
                "cache_files_found": len(rows),
                "valuation_ready": len(rows),
                "complete": len(rows),
                "partial_source_backed": 0,
                "missing": 0,
                "full_coverage_ready": len(rows),
                "financial_numbers_allowed": len(rows),
            },
            "quality_flags": [],
            "rows": rows,
            "source_trace": {"source": "kr_valuation_input_cache"},
        }

    def coverage(*args, **kwargs):
        report = build_source_coverage_report(
            [
                {
                    "ticker": "005930.KS",
                    "security_count": 1,
                    "adjusted_years": 5,
                    "price_years": 5,
                    "market_cap_years": 5,
                    "listed_shares_years": 5,
                    "financial_metric_years": 5,
                    "financial_metric_keys": 3,
                    "source_documents": 2,
                    "raw_objects": 2,
                    "s3_periods": 5,
                },
            ],
            ["005930.KS"],
            postgres_reachable=False,
            error="not_configured_local_warehouse",
        )
        report["data_backend"] = "kr_valuation_warehouse"
        report["data_mode"] = "local_source_backed_warehouse"
        return report

    monkeypatch.setattr(
        "services.ingestion_worker.cli.kr_valuation_cache_universe_coverage",
        cache_coverage,
    )
    monkeypatch.setattr("services.ingestion_worker.cli.source_coverage_report", coverage)

    summary = kr_production_readiness(tickers="005930.KS")

    assert summary["status"] == "local_warehouse_ready"
    assert summary["summary"]["local_warehouse_ready"] is True
    assert summary["summary"]["production_ready"] is False
    assert summary["summary"]["source_coverage_mode"] == "local_source_backed_warehouse"
    assert summary["missing_required"] == []
    assert [command["id"] for command in summary["next_commands"]][-2:] == [
        "protected_partial_smoke",
        "full_production_gate",
    ]
    output = _kr_readiness_output_summary(summary)
    assert output["source_coverage_status"] == "ready"
    assert output["production_status"] == "local_warehouse_only"
    assert output["production_postgres"]["reachable"] is False


def test_kr_production_readiness_reports_production_ready_with_consensus(monkeypatch):
    def cache_coverage(tickers):
        rows = [
            {
                "ticker": ticker,
                "valuation_ready": True,
                "coverage_status": "complete",
                "quality_flags": [],
            }
            for ticker in tickers
        ]
        return {
            "market": "KR",
            "coverage_status": "complete",
            "quality_status": "source_backed_cache_complete",
            "summary": {
                "tickers_expected": len(rows),
                "cache_files_found": len(rows),
                "valuation_ready": len(rows),
                "complete": len(rows),
                "partial_source_backed": 0,
                "missing": 0,
                "full_coverage_ready": len(rows),
                "financial_numbers_allowed": len(rows),
            },
            "quality_flags": [],
            "rows": rows,
            "source_trace": {"source": "kr_valuation_input_cache"},
        }

    def coverage(*args, **kwargs):
        return build_source_coverage_report(
            [
                {
                    "ticker": "005930.KS",
                    "security_count": 1,
                    "adjusted_years": 5,
                    "price_years": 5,
                    "market_cap_years": 5,
                    "listed_shares_years": 5,
                    "financial_metric_years": 5,
                    "financial_metric_keys": 3,
                    "source_documents": 2,
                    "s3_periods": 5,
                    "consensus_forecast_years": 5,
                    "consensus_valuation_years": 5,
                    "consensus_snapshots": 1,
                    "consensus_valuation_snapshots": 1,
                },
            ],
            ["005930.KS"],
            require_consensus_forecast=kwargs["require_consensus_forecast"],
        )

    monkeypatch.setattr(
        "services.ingestion_worker.cli.kr_valuation_cache_universe_coverage",
        cache_coverage,
    )
    monkeypatch.setattr("services.ingestion_worker.cli.source_coverage_report", coverage)

    summary = kr_production_readiness(
        tickers="005930.KS",
        require_consensus_forecast=True,
    )

    assert summary["status"] == "production_ready"
    assert summary["summary"]["production_ready"] is True
    assert summary["summary"]["production_consensus_forecast_ready"] == 1
    assert summary["missing_required"] == []
    assert "--require-consensus-forecast" in summary["next_commands"][4]["command"]
    assert summary["next_commands"][4]["command"].endswith("--require-consensus-forecast --strict")
    assert "--require-consensus-forecast" in summary["next_commands"][-1]["command"]
    output = _kr_readiness_output_summary(summary)
    assert output["source_coverage_status"] == "ready"
    assert output["production_status"] == "production_ready"


def test_run_source_e2e_dry_run_reports_us_prerequisites(monkeypatch):
    monkeypatch.setattr(
        ingestion_cli,
        "LOCAL_ENV_KEYS",
        {"DATABASE_URL", "SEC_USER_AGENT"},
    )
    for key in ("DATABASE_URL", "DATA_BACKEND", "SEC_USER_AGENT"):
        monkeypatch.delenv(key, raising=False)

    summary = run_source_e2e(
        market="US",
        tickers="AAPL",
        start_year=2020,
        end_year=2025,
        persist=True,
        dry_run=True,
    )

    assert summary["status"] == "needs_configuration"
    assert summary["tickers"] == ["AAPL"]
    assert summary["local_env_loaded"] is True
    assert summary["local_env_loaded_keys"] == ["DATABASE_URL", "SEC_USER_AGENT"]
    assert summary["executed_steps"] == []
    assert [step["id"] for step in summary["steps"]] == [
        "collect_sec_bulk",
        "load_sec_bulk_warehouse",
        "normalize_us_batch",
        "collect_stooq_prices_us",
        "source_coverage",
    ]
    assert set(summary["missing_required"]) == {
        "DATA_BACKEND=postgres",
        "DATABASE_URL",
        "SEC_USER_AGENT",
    }


def test_run_source_e2e_executes_us_steps_in_order(monkeypatch):
    monkeypatch.setenv("DATA_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example")
    monkeypatch.setenv("SEC_USER_AGENT", "PersonalFastGraphs/0.1 test@example.com")
    calls: list[str] = []

    def fake_collect_sec_bulk(*args, **kwargs):
        calls.append("collect_sec_bulk")
        return {"status": "ok", "archives": ["companyfacts", "submissions"]}

    def fake_load_sec_bulk(*args, **kwargs):
        calls.append("load_sec_bulk_warehouse")
        return {"status": "ok", "metric_values": 10}

    def fake_normalize_batch(*args, **kwargs):
        calls.append("normalize_us_batch")
        return {"status": "ok", "tickers_completed": ["AAPL"], "failed": []}

    def fake_collect_stooq(*args, **kwargs):
        calls.append("collect_stooq_prices_us")
        return {"status": "ok", "price_rows": 5}

    def fake_source_coverage(*args, **kwargs):
        calls.append("source_coverage")
        return build_source_coverage_report(
            [
                {
                    "ticker": "AAPL",
                    "security_count": 1,
                    "adjusted_years": 5,
                    "price_years": 5,
                    "market_cap_years": 5,
                    "financial_metric_years": 5,
                    "financial_metric_keys": 3,
                    "source_documents": 2,
                    "s1_periods": 5,
                },
            ],
            ["AAPL"],
        )

    monkeypatch.setattr(
        "services.ingestion_worker.cli.collect_sec_bulk_archives",
        fake_collect_sec_bulk,
    )
    monkeypatch.setattr("services.ingestion_worker.cli.load_sec_bulk_warehouse", fake_load_sec_bulk)
    monkeypatch.setattr(
        "services.ingestion_worker.cli.normalize_us_batch_run",
        fake_normalize_batch,
    )
    monkeypatch.setattr("services.ingestion_worker.cli.collect_stooq_prices", fake_collect_stooq)
    monkeypatch.setattr(
        "services.ingestion_worker.cli.source_coverage_report",
        fake_source_coverage,
    )

    summary = run_source_e2e(
        market="US",
        tickers="AAPL",
        start_year=2020,
        end_year=2025,
        persist=True,
    )

    assert calls == [
        "collect_sec_bulk",
        "load_sec_bulk_warehouse",
        "normalize_us_batch",
        "collect_stooq_prices_us",
        "source_coverage",
    ]
    assert summary["status"] == "ok"
    assert summary["missing_required"] == []
    assert summary["executed_steps"] == calls
    assert summary["coverage"]["status"] == "ready"


def test_run_source_e2e_dry_run_reports_kr_prerequisites(monkeypatch):
    for key in ("DATABASE_URL", "DATA_BACKEND", "OPENDART_API_KEY", "DART_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    summary = run_source_e2e(
        market="KR",
        start_year=2020,
        end_year=2025,
        persist=True,
        dry_run=True,
    )

    assert summary["status"] == "needs_configuration"
    assert summary["tickers"] == list(KR_TOP_MARKET_CAP_PRIORITY_TICKERS)
    assert summary["executed_steps"] == []
    assert [step["id"] for step in summary["steps"]] == [
        "collect_opendart_kr",
        "collect_pykrx_prices_kr",
        "collect_marcap_kr",
        "build_kr_valuation_inputs",
        "load_kr_valuation_postgres",
        "source_coverage",
    ]
    assert set(summary["missing_required"]) == {
        "DATA_BACKEND=postgres",
        "DATABASE_URL",
        "OPENDART_API_KEY",
    }


def test_run_source_e2e_defaults_to_kr_priority_universe(monkeypatch):
    for key in ("DATABASE_URL", "DATA_BACKEND", "OPENDART_API_KEY", "DART_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    summary = run_source_e2e(
        start_year=2020,
        end_year=2025,
        persist=True,
        dry_run=True,
    )

    assert summary["market"] == "KR"
    assert summary["tickers"] == list(KR_TOP_MARKET_CAP_PRIORITY_TICKERS)
    assert [step["id"] for step in summary["steps"]] == [
        "collect_opendart_kr",
        "collect_pykrx_prices_kr",
        "collect_marcap_kr",
        "build_kr_valuation_inputs",
        "load_kr_valuation_postgres",
        "source_coverage",
    ]


def test_run_source_e2e_local_kr_dry_run_reports_raw_evidence(monkeypatch):
    for key in ("DATABASE_URL", "DATA_BACKEND", "OPENDART_API_KEY", "DART_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    def fake_inspect_raw_kr_evidence(*args, **kwargs):
        return {
            "status": "ok",
            "market": "KR",
            "summary": {
                "tickers_expected": 1,
                "tickers_ok": 1,
                "valuation_ready": 1,
                "missing": [],
            },
            "next_actions": [
                {
                    "id": "build_kr_valuation_inputs",
                    "command": "python -m services.ingestion_worker.cli build-kr-valuation-inputs --tickers 005930.KS --years 2020:2025 --strict",
                    "reason": "Build source-backed valuation-map input cache once raw evidence is complete.",
                    "secrets_redacted": True,
                }
            ],
            "tickers": [
                {
                    "ticker": "005930.KS",
                    "status": "ok",
                    "valuation_ready": True,
                }
            ],
        }

    monkeypatch.setattr(
        "services.ingestion_worker.cli.inspect_raw_kr_evidence",
        fake_inspect_raw_kr_evidence,
    )

    summary = run_source_e2e(
        market="KR",
        tickers="005930.KS",
        start_year=2024,
        end_year=2024,
        persist=False,
        dry_run=True,
    )

    assert summary["status"] == "local_raw_ready"
    assert summary["missing_required"] == ["OPENDART_API_KEY"]
    assert summary["local_raw_evidence"]["status"] == "ok"
    assert summary["local_raw_evidence"]["mode"] == "offline_raw_evidence_check"
    assert summary["local_raw_evidence"]["raw_evidence"]["summary"]["valuation_ready"] == 1
    assert summary["local_raw_evidence"]["next_actions"][0]["id"] == "build_kr_valuation_inputs"
    assert summary["completion_gate"]["status"] == "ready_for_valuation_cache_build"
    assert summary["completion_gate"]["local_raw_ready"] is True
    assert summary["completion_gate"]["required_proofs"][0].startswith("OpenDART financial facts")
    assert [command["id"] for command in summary["completion_gate"]["next_commands"]] == [
        "build_kr_valuation_inputs",
        "load_kr_valuation_warehouse",
        "load_kr_valuation_postgres",
        "api_valuation_map_probe",
    ]
    assert "load-kr-valuation-warehouse --tickers 005930.KS" in summary["completion_gate"]["next_commands"][1]["command"]
    assert "load-kr-valuation-postgres --tickers 005930.KS" in summary["completion_gate"]["next_commands"][2]["command"]
    assert summary["completion_gate"]["deployment_commands"][0]["id"] == "load_kr_valuation_postgres"
    assert summary["completion_gate"]["deployment_commands"][1]["id"] == "source_coverage_postgres"
    assert "DATA_BACKEND=postgres" in summary["completion_gate"]["deployment_commands"][0]["requires"]


def test_e2e_summary_output_keeps_operator_signal_without_raw_payload():
    summary = {
        "status": "needs_source_data",
        "market": "KR",
        "tickers": ["005930.KS", "000660.KS"],
        "years": "2020:2025",
        "policy": "street_comparable",
        "persist": False,
        "dry_run": True,
        "missing_required": ["OPENDART_API_KEY"],
        "executed_steps": [],
        "failed": [],
        "coverage": None,
        "local_raw_evidence": {
            "status": "partial",
            "mode": "offline_raw_evidence_check",
            "next_actions": [
                {
                    "id": "collect_opendart_kr",
                    "ticker": "000660.KS",
                    "command": "python -m services.ingestion_worker.cli collect --market KR --ticker 000660.KS",
                    "reason": "OpenDART evidence missing.",
                    "source_trace": {"filing_id": "must-not-print"},
                }
            ],
            "raw_evidence": {
                "summary": {
                    "tickers_expected": 2,
                    "tickers_ok": 1,
                    "valuation_ready": 1,
                    "missing": ["000660.KS"],
                },
                "tickers": [
                    {
                        "ticker": "005930.KS",
                        "status": "ok",
                        "valuation_ready": True,
                        "source_trace": {"filing_id": "must-not-print"},
                        "checks": [
                            {
                                "name": "opendart",
                                "ok": True,
                                "required": True,
                                "raw_payload": "must-not-print",
                            }
                        ],
                    },
                    {
                        "ticker": "000660.KS",
                        "status": "missing",
                        "valuation_ready": False,
                        "missing_years": {"opendart": [2020]},
                        "checks": [
                            {"name": "opendart", "ok": False, "required": True},
                            {"name": "marcap", "ok": True, "required": True},
                        ],
                    },
                ],
            },
        },
        "completion_gate": {
            "status": "needs_source_data",
            "market": "KR",
            "tickers": ["005930.KS", "000660.KS"],
            "years": "2020:2025",
            "coverage_status": None,
            "local_raw_ready": False,
            "missing_required": ["OPENDART_API_KEY"],
            "required_proofs": [
                "OpenDART financial facts, pykrx prices, and marcap evidence are present",
                "warehouse load succeeds and rejects non-production rows",
            ],
            "next_commands": [
                {
                    "id": "build_kr_valuation_inputs",
                    "command": "python -m services.ingestion_worker.cli build-kr-valuation-inputs --tickers 005930.KS,000660.KS --years 2020:2025 --strict",
                    "proves": "valuation-map input cache",
                    "source_trace": {"filing_id": "must-not-print"},
                }
            ],
        },
    }

    compact = ingestion_cli._e2e_output_summary(summary)
    serialized = json.dumps(compact, ensure_ascii=False)

    assert compact["status"] == "needs_source_data"
    assert compact["local_raw_evidence"]["summary"]["valuation_ready"] == 1
    assert compact["local_raw_evidence"]["tickers"][1] == {
        "ticker": "000660.KS",
        "status": "missing",
        "valuation_ready": False,
        "missing_years": {"opendart": {"count": 1, "start": 2020, "end": 2020}},
        "failed_required_checks": ["opendart"],
    }
    assert compact["next_actions"][0]["id"] == "collect_opendart_kr"
    assert compact["completion_gate"]["status"] == "needs_source_data"
    assert compact["completion_gate"]["next_commands"][0]["id"] == "build_kr_valuation_inputs"
    assert compact["completion_gate"]["deployment_commands"] == []
    assert "source_trace" not in serialized
    assert "raw_payload" not in serialized
    assert "must-not-print" not in serialized


def test_run_source_e2e_executes_kr_steps_in_order(monkeypatch):
    monkeypatch.setenv("DATA_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example")
    monkeypatch.setenv("OPENDART_API_KEY", "opendart-test-token")
    calls: list[str] = []

    def fake_collect_market_documents(*args, **kwargs):
        calls.append(f"collect_opendart_kr:{args[1]}")
        return {"status": "ok", "market": "KR", "ticker": args[1], "documents": []}

    def fake_collect_pykrx(*args, **kwargs):
        calls.append("collect_pykrx_prices_kr")
        return {"status": "ok", "market": "KR", "price_rows": 10}

    def fake_collect_marcap(*args, **kwargs):
        calls.append("collect_marcap_kr")
        return {"status": "ok", "market": "KR", "price_rows": 10}

    def fake_build_kr_valuation_inputs(*args, **kwargs):
        calls.append("build_kr_valuation_inputs")
        return {"status": "ok", "market": "KR", "summary": {"valuation_ready": 2}}

    def fake_load_kr_valuation_postgres(*args, **kwargs):
        calls.append("load_kr_valuation_postgres")
        return {"status": "ok", "market": "KR", "adjusted_earnings": 10}

    def fake_source_coverage(*args, **kwargs):
        calls.append("source_coverage")
        return build_source_coverage_report(
            [
                {
                    "ticker": "005930.KS",
                    "security_count": 1,
                    "adjusted_years": 5,
                    "price_years": 5,
                    "market_cap_years": 5,
                    "listed_shares_years": 5,
                    "financial_metric_years": 5,
                    "financial_metric_keys": 3,
                    "source_documents": 2,
                    "s3_periods": 5,
                },
                {
                    "ticker": "000660.KS",
                    "security_count": 1,
                    "adjusted_years": 5,
                    "price_years": 5,
                    "market_cap_years": 5,
                    "listed_shares_years": 5,
                    "financial_metric_years": 5,
                    "financial_metric_keys": 3,
                    "source_documents": 2,
                    "s3_periods": 5,
                },
            ],
            ["005930.KS", "000660.KS"],
        )

    monkeypatch.setattr(
        "services.ingestion_worker.cli.collect_market_documents",
        fake_collect_market_documents,
    )
    monkeypatch.setattr("services.ingestion_worker.cli.collect_pykrx_prices", fake_collect_pykrx)
    monkeypatch.setattr("services.ingestion_worker.cli.collect_marcap_data", fake_collect_marcap)
    monkeypatch.setattr(
        "services.ingestion_worker.cli.build_kr_valuation_inputs",
        fake_build_kr_valuation_inputs,
    )
    monkeypatch.setattr(
        "services.ingestion_worker.cli.load_kr_valuation_cache_to_postgres",
        fake_load_kr_valuation_postgres,
    )
    monkeypatch.setattr(
        "services.ingestion_worker.cli.source_coverage_report",
        fake_source_coverage,
    )

    summary = run_source_e2e(
        market="KR",
        tickers="005930.KS,000660.KS",
        start_year=2020,
        end_year=2025,
        persist=True,
    )

    assert calls == [
        "collect_opendart_kr:005930.KS",
        "collect_opendart_kr:000660.KS",
        "collect_pykrx_prices_kr",
        "collect_marcap_kr",
        "build_kr_valuation_inputs",
        "load_kr_valuation_postgres",
        "source_coverage",
    ]
    assert summary["status"] == "ok"
    assert summary["missing_required"] == []
    assert summary["executed_steps"] == [
        "collect_opendart_kr",
        "collect_pykrx_prices_kr",
        "collect_marcap_kr",
        "build_kr_valuation_inputs",
        "load_kr_valuation_postgres",
        "source_coverage",
    ]
    assert summary["coverage"]["status"] == "ready"


def test_run_source_e2e_dry_run_reports_jp_prerequisites(monkeypatch):
    for key in (
        "DATABASE_URL",
        "DATA_BACKEND",
        "JQUANTS_REFRESH_TOKEN",
        "JQUANTS_EMAIL",
        "JQUANTS_PASSWORD",
        "EDINET_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    summary = run_source_e2e(
        market="JP",
        start_year=2020,
        end_year=2025,
        persist=True,
        dry_run=True,
    )

    assert summary["status"] == "needs_configuration"
    assert summary["tickers"] == list(JP_TOP_MARKET_CAP_PRIORITY_TICKERS)
    assert summary["executed_steps"] == []
    assert [step["id"] for step in summary["steps"]] == [
        "collect_jquants_jp",
        "collect_edinet_jp",
        "collect_stooq_prices_jp",
        "source_coverage",
    ]
    assert set(summary["missing_required"]) == {
        "DATA_BACKEND=postgres",
        "DATABASE_URL",
        "JQUANTS credentials",
        "EDINET_API_KEY",
    }


def test_run_source_e2e_executes_jp_steps_in_order(monkeypatch):
    monkeypatch.setenv("DATA_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example")
    monkeypatch.setenv("JQUANTS_REFRESH_TOKEN", "jquants-test-token")
    monkeypatch.setenv("EDINET_API_KEY", "edinet-test-token")
    calls: list[str] = []

    def fake_collect_jquants(*args, **kwargs):
        calls.append("collect_jquants_jp")
        return {"status": "ok", "market": "JP", "statement_rows": 10}

    def fake_collect_edinet(*args, **kwargs):
        calls.append("collect_edinet_jp")
        return {"status": "ok", "market": "JP", "metadata_documents": 10}

    def fake_collect_stooq(*args, **kwargs):
        calls.append("collect_stooq_prices_jp")
        return {"status": "ok", "market": "JP", "price_rows": 10}

    def fake_source_coverage(*args, **kwargs):
        calls.append("source_coverage")
        return build_source_coverage_report(
            [
                {
                    "ticker": "7203.T",
                    "security_count": 1,
                    "adjusted_years": 5,
                    "price_years": 5,
                    "market_cap_years": 5,
                    "financial_metric_years": 5,
                    "financial_metric_keys": 3,
                    "source_documents": 2,
                    "s3_periods": 5,
                },
                {
                    "ticker": "9983.T",
                    "security_count": 1,
                    "adjusted_years": 5,
                    "price_years": 5,
                    "market_cap_years": 5,
                    "financial_metric_years": 5,
                    "financial_metric_keys": 3,
                    "source_documents": 2,
                    "s3_periods": 5,
                },
            ],
            ["7203.T", "9983.T"],
        )

    monkeypatch.setattr("services.ingestion_worker.cli.collect_jquants_data", fake_collect_jquants)
    monkeypatch.setattr(
        "services.ingestion_worker.cli.collect_edinet_filings",
        fake_collect_edinet,
    )
    monkeypatch.setattr("services.ingestion_worker.cli.collect_stooq_prices", fake_collect_stooq)
    monkeypatch.setattr(
        "services.ingestion_worker.cli.source_coverage_report",
        fake_source_coverage,
    )

    summary = run_source_e2e(
        market="JP",
        tickers="7203.T,9983.T",
        start_year=2020,
        end_year=2025,
        persist=True,
    )

    assert calls == [
        "collect_jquants_jp",
        "collect_edinet_jp",
        "collect_stooq_prices_jp",
        "source_coverage",
    ]
    assert summary["status"] == "ok"
    assert summary["missing_required"] == []
    assert summary["executed_steps"] == [
        "collect_jquants_jp",
        "collect_edinet_jp",
        "collect_stooq_prices_jp",
        "source_coverage",
    ]
    assert summary["coverage"]["status"] == "ready"


def test_run_priority_e2e_dry_run_reports_kr_us_jp_order(monkeypatch):
    monkeypatch.setattr(
        ingestion_cli,
        "LOCAL_ENV_KEYS",
        {"DATABASE_URL", "OPENDART_API_KEY", "SEC_USER_AGENT"},
    )
    for key in (
        "DATABASE_URL",
        "DATA_BACKEND",
        "SEC_USER_AGENT",
        "OPENDART_API_KEY",
        "DART_API_KEY",
        "JQUANTS_REFRESH_TOKEN",
        "JQUANTS_EMAIL",
        "JQUANTS_PASSWORD",
        "EDINET_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    summary = run_priority_e2e(
        markets="ALL",
        start_year=2020,
        end_year=2025,
        persist=True,
        dry_run=True,
    )

    assert summary["status"] == "needs_configuration"
    assert summary["market_order"] == ["KR", "US", "JP"]
    assert summary["local_env_loaded"] is True
    assert summary["local_env_loaded_keys"] == [
        "DATABASE_URL",
        "OPENDART_API_KEY",
        "SEC_USER_AGENT",
    ]
    assert summary["executed_markets"] == []
    assert [step["id"] for step in summary["steps"]] == [
        "run_source_e2e_kr",
        "run_source_e2e_us",
        "run_source_e2e_jp",
        "source_coverage_all",
    ]
    assert summary["tickers"][0:3] == list(KR_TOP_MARKET_CAP_PRIORITY_TICKERS[:3])
    assert "KR:OPENDART_API_KEY" in summary["missing_required"]
    assert "US:SEC_USER_AGENT" in summary["missing_required"]
    assert "JP:JQUANTS credentials" in summary["missing_required"]
    assert "JP:EDINET_API_KEY" in summary["missing_required"]


def test_run_priority_e2e_executes_market_runners_in_order(monkeypatch):
    calls: list[str] = []

    def fake_run_source_e2e(*, market, **kwargs):
        calls.append(f"run_source_e2e_{market.lower()}")
        tickers = {
            "KR": ["005930.KS"],
            "US": ["AAPL"],
            "JP": ["7203.T"],
        }[market]
        return {
            "status": "ok",
            "market": market,
            "tickers": tickers,
            "missing_required": [],
            "coverage": {"status": "ready"},
        }

    def fake_source_coverage(*args, **kwargs):
        calls.append("source_coverage_all")
        assert kwargs["market"] == "ALL"
        assert kwargs["tickers"] == "005930.KS,AAPL,7203.T"
        return build_source_coverage_report(
            [
                {
                    "ticker": "005930.KS",
                    "security_count": 1,
                    "adjusted_years": 5,
                    "price_years": 5,
                    "market_cap_years": 5,
                    "listed_shares_years": 5,
                    "financial_metric_years": 5,
                    "financial_metric_keys": 3,
                    "source_documents": 2,
                },
                {
                    "ticker": "AAPL",
                    "security_count": 1,
                    "adjusted_years": 5,
                    "price_years": 5,
                    "market_cap_years": 5,
                    "listed_shares_years": 5,
                    "financial_metric_years": 5,
                    "financial_metric_keys": 3,
                    "source_documents": 2,
                },
                {
                    "ticker": "7203.T",
                    "security_count": 1,
                    "adjusted_years": 5,
                    "price_years": 5,
                    "market_cap_years": 5,
                    "financial_metric_years": 5,
                    "financial_metric_keys": 3,
                    "source_documents": 2,
                },
            ],
            ["005930.KS", "AAPL", "7203.T"],
        )

    monkeypatch.setattr("services.ingestion_worker.cli.run_source_e2e", fake_run_source_e2e)
    monkeypatch.setattr(
        "services.ingestion_worker.cli.source_coverage_report",
        fake_source_coverage,
    )

    summary = run_priority_e2e(
        markets="JP,US,KR",
        start_year=2020,
        end_year=2025,
        persist=True,
    )

    assert calls == [
        "run_source_e2e_kr",
        "run_source_e2e_us",
        "run_source_e2e_jp",
        "source_coverage_all",
    ]
    assert summary["status"] == "ok"
    assert summary["market_order"] == ["KR", "US", "JP"]
    assert summary["executed_markets"] == ["KR", "US", "JP"]
    assert summary["tickers"] == ["005930.KS", "AAPL", "7203.T"]
    assert summary["coverage"]["status"] == "ready"


def test_run_p1_e2e_dry_run_reports_cross_market_prerequisites(monkeypatch):
    for key in (
        "DATABASE_URL",
        "DATA_BACKEND",
        "SEC_USER_AGENT",
        "OPENDART_API_KEY",
        "DART_API_KEY",
        "JQUANTS_REFRESH_TOKEN",
        "JQUANTS_EMAIL",
        "JQUANTS_PASSWORD",
        "EDINET_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    summary = run_p1_e2e(
        us_ticker="AAPL",
        kr_ticker="005930.KS",
        jp_ticker="7203.T",
        start_year=2020,
        end_year=2025,
        persist=True,
        dry_run=True,
    )

    assert summary["status"] == "needs_configuration"
    assert summary["tickers"] == ["AAPL", "005930.KS", "7203.T"]
    assert summary["executed_steps"] == []
    assert [step["id"] for step in summary["steps"]] == [
        "run_source_e2e_us",
        "collect_opendart_kr",
        "collect_pykrx_prices_kr",
        "collect_marcap_kr",
        "collect_jquants_jp",
        "collect_edinet_jp",
        "collect_stooq_prices_jp",
        "source_coverage",
    ]
    assert set(summary["missing_required"]) == {
        "DATA_BACKEND=postgres",
        "DATABASE_URL",
        "SEC_USER_AGENT",
        "OPENDART_API_KEY",
        "JQUANTS credentials",
        "EDINET_API_KEY",
    }


def test_run_p1_e2e_executes_cross_market_steps_in_order(monkeypatch):
    monkeypatch.setenv("DATA_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example")
    monkeypatch.setenv("SEC_USER_AGENT", "PersonalFastGraphs/0.1 test@example.com")
    monkeypatch.setenv("OPENDART_API_KEY", "opendart-test-token")
    monkeypatch.setenv("JQUANTS_REFRESH_TOKEN", "jquants-test-token")
    monkeypatch.setenv("EDINET_API_KEY", "edinet-test-token")
    calls: list[str] = []

    def fake_run_source_e2e(*args, **kwargs):
        calls.append("run_source_e2e_us")
        return {"status": "ok", "tickers": ["AAPL"], "coverage": {"status": "ready"}}

    def fake_collect_market_documents(*args, **kwargs):
        calls.append("collect_opendart_kr")
        return {"status": "ok", "market": "KR", "ticker": "005930.KS", "documents": []}

    def fake_collect_pykrx(*args, **kwargs):
        calls.append("collect_pykrx_prices_kr")
        return {"status": "ok", "market": "KR", "price_rows": 5}

    def fake_collect_marcap(*args, **kwargs):
        calls.append("collect_marcap_kr")
        return {"status": "ok", "market": "KR", "price_rows": 5}

    def fake_collect_jquants(*args, **kwargs):
        calls.append("collect_jquants_jp")
        return {"status": "ok", "market": "JP", "statement_rows": 5}

    def fake_collect_edinet(*args, **kwargs):
        calls.append("collect_edinet_jp")
        return {"status": "ok", "market": "JP", "metadata_documents": 5}

    def fake_collect_stooq(*args, **kwargs):
        calls.append("collect_stooq_prices_jp")
        return {"status": "ok", "market": "JP", "price_rows": 5}

    def fake_source_coverage(*args, **kwargs):
        calls.append("source_coverage")
        return build_source_coverage_report(
            [
                {
                    "ticker": "AAPL",
                    "security_count": 1,
                    "adjusted_years": 5,
                    "price_years": 5,
                    "market_cap_years": 5,
                    "financial_metric_years": 5,
                    "financial_metric_keys": 3,
                    "source_documents": 2,
                    "s1_periods": 5,
                },
                {
                    "ticker": "005930.KS",
                    "security_count": 1,
                    "adjusted_years": 5,
                    "price_years": 5,
                    "market_cap_years": 5,
                    "listed_shares_years": 5,
                    "financial_metric_years": 5,
                    "financial_metric_keys": 3,
                    "source_documents": 2,
                    "s3_periods": 5,
                },
                {
                    "ticker": "7203.T",
                    "security_count": 1,
                    "adjusted_years": 5,
                    "price_years": 5,
                    "market_cap_years": 5,
                    "financial_metric_years": 5,
                    "financial_metric_keys": 3,
                    "source_documents": 2,
                    "s3_periods": 5,
                },
            ],
            ["AAPL", "005930.KS", "7203.T"],
        )

    monkeypatch.setattr("services.ingestion_worker.cli.run_source_e2e", fake_run_source_e2e)
    monkeypatch.setattr(
        "services.ingestion_worker.cli.collect_market_documents",
        fake_collect_market_documents,
    )
    monkeypatch.setattr("services.ingestion_worker.cli.collect_pykrx_prices", fake_collect_pykrx)
    monkeypatch.setattr("services.ingestion_worker.cli.collect_marcap_data", fake_collect_marcap)
    monkeypatch.setattr("services.ingestion_worker.cli.collect_jquants_data", fake_collect_jquants)
    monkeypatch.setattr(
        "services.ingestion_worker.cli.collect_edinet_filings",
        fake_collect_edinet,
    )
    monkeypatch.setattr("services.ingestion_worker.cli.collect_stooq_prices", fake_collect_stooq)
    monkeypatch.setattr(
        "services.ingestion_worker.cli.source_coverage_report",
        fake_source_coverage,
    )

    summary = run_p1_e2e(
        us_ticker="AAPL",
        kr_ticker="005930.KS",
        jp_ticker="7203.T",
        start_year=2020,
        end_year=2025,
        persist=True,
        continue_on_error=True,
    )

    assert calls == [
        "run_source_e2e_us",
        "collect_opendart_kr",
        "collect_pykrx_prices_kr",
        "collect_marcap_kr",
        "collect_jquants_jp",
        "collect_edinet_jp",
        "collect_stooq_prices_jp",
        "source_coverage",
    ]
    assert summary["status"] == "ok"
    assert summary["missing_required"] == []
    assert summary["executed_steps"] == calls
    assert summary["coverage"]["status"] == "ready"


def test_run_p1_e2e_accepts_dart_api_key_alias(monkeypatch):
    monkeypatch.setenv("DATA_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example")
    monkeypatch.setenv("SEC_USER_AGENT", "PersonalFastGraphs/0.1 test@example.com")
    monkeypatch.delenv("OPENDART_API_KEY", raising=False)
    monkeypatch.setenv("DART_API_KEY", "dart-test-token")
    monkeypatch.setenv("JQUANTS_REFRESH_TOKEN", "jquants-test-token")
    monkeypatch.setenv("EDINET_API_KEY", "edinet-test-token")

    summary = run_p1_e2e(
        us_ticker="AAPL",
        kr_ticker="005930.KS",
        jp_ticker="7203.T",
        start_year=2020,
        end_year=2025,
        persist=True,
        dry_run=True,
    )

    assert summary["status"] == "planned"
    assert "OPENDART_API_KEY" not in summary["missing_required"]


def test_normalize_us_batch_continues_and_reports_failed_tickers(monkeypatch):
    def fake_normalize(ticker, *args, **kwargs):
        if ticker == "CRM":
            raise RuntimeError("fixture failure")
        return {
            "ticker": ticker,
            "status": "ok",
            "source_documents": 1,
            "series_count": 2,
            "failed_strategies": [],
            "warnings": [],
            "persisted": {},
        }

    monkeypatch.setattr("services.ingestion_worker.cli.normalize_us_ticker", fake_normalize)

    summary = normalize_us_batch_run(
        "AAPL,CRM,NVDA",
        2020,
        2025,
        continue_on_error=True,
    )

    assert summary["status"] == "partial"
    assert summary["tickers_requested"] == ["AAPL", "CRM", "NVDA"]
    assert summary["tickers_completed"] == ["AAPL", "NVDA"]
    assert summary["failed"] == [
        {
            "ticker": "CRM",
            "status": "failed",
            "error_type": "RuntimeError",
            "error": "fixture failure",
        }
    ]


def test_normalize_us_batch_stops_on_first_error_by_default(monkeypatch):
    calls: list[str] = []

    def fake_normalize(ticker, *args, **kwargs):
        calls.append(ticker)
        if ticker == "CRM":
            raise RuntimeError("fixture failure")
        return {
            "ticker": ticker,
            "status": "ok",
            "source_documents": 1,
            "series_count": 2,
            "failed_strategies": [],
            "warnings": [],
            "persisted": {},
        }

    monkeypatch.setattr("services.ingestion_worker.cli.normalize_us_ticker", fake_normalize)

    summary = normalize_us_batch_run("AAPL,CRM,NVDA", 2020, 2025)

    assert calls == ["AAPL", "CRM"]
    assert summary["status"] == "failed"
    assert summary["tickers_completed"] == ["AAPL"]
    assert [row["status"] for row in summary["results"]] == ["ok", "failed"]


def test_opendart_connector_collects_source_json_without_deriving_numbers():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "fnlttSinglAcntAll.json" in str(request.url)
        return httpx.Response(
            200,
            json={
                "status": "000",
                "message": "정상",
                "list": [{"account_nm": "희석주당이익", "thstrm_amount": "1000"}],
            },
        )

    connector = OpenDartConnector(
        api_key="key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    documents = connector.collect(
        ConnectorRequest(ticker="005930.KS", market="KR", start_year=2024, end_year=2024)
    )

    assert len(documents) == 1
    assert documents[0].source == "opendart"
    assert documents[0].content_type == "application/json"
    assert "crtfc_key=key" not in documents[0].url
    assert "crtfc_key=REDACTED" in documents[0].url
    assert json.loads(documents[0].payload.decode("utf-8"))["status"] == "000"


def test_opendart_connector_collects_dividend_source_json_without_deriving_numbers():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "alotMatter.json" in str(request.url)
        assert request.url.params["corp_code"] == "00126380"
        assert request.url.params["reprt_code"] == "11011"
        return httpx.Response(
            200,
            json={
                "status": "000",
                "message": "OK",
                "bsns_year": "2024",
                "list": [
                    {
                        "se": "\uc8fc\ub2f9 \ud604\uae08\ubc30\ub2f9\uae08(\uc6d0)",
                        "stock_knd": "\ubcf4\ud1b5\uc8fc",
                        "thstrm": "1,444",
                    }
                ],
            },
            request=request,
        )

    connector = OpenDartConnector(
        api_key="key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    documents = connector.collect_dividends(
        ConnectorRequest(ticker="005930.KS", market="KR", start_year=2024, end_year=2024)
    )

    assert len(documents) == 1
    assert documents[0].source == "opendart_dividends"
    assert documents[0].identifier == "00126380-2024-alotMatter"
    assert documents[0].content_type == "application/json"
    assert documents[0].metadata["endpoint"] == "alotMatter"
    assert "crtfc_key=key" not in documents[0].url
    assert "crtfc_key=REDACTED" in documents[0].url
    assert json.loads(documents[0].payload.decode("utf-8"))["status"] == "000"


def test_opendart_connector_has_corp_codes_for_kr_priority_universe():
    missing = [
        ticker
        for ticker in KR_TOP_MARKET_CAP_PRIORITY_TICKERS
        if ticker not in DEFAULT_CORP_CODES
    ]

    assert missing == []


def test_opendart_connector_collects_all_kr_priority_tickers_with_redacted_keys():
    requested_corp_codes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_corp_codes.append(str(request.url.params["corp_code"]))
        return httpx.Response(
            200,
            json={
                "status": "000",
                "message": "OK",
                "list": [{"account_nm": "Earnings per share", "thstrm_amount": "1000"}],
            },
        )

    connector = OpenDartConnector(
        api_key="key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    for ticker in KR_TOP_MARKET_CAP_PRIORITY_TICKERS:
        documents = connector.collect(
            ConnectorRequest(ticker=ticker, market="KR", start_year=2024, end_year=2024)
        )

        assert len(documents) == 1
        assert documents[0].ticker == ticker
        assert "crtfc_key=key" not in documents[0].url
        assert "crtfc_key=REDACTED" in documents[0].url
        assert documents[0].metadata["corp_code"] == DEFAULT_CORP_CODES[ticker]

    assert requested_corp_codes == [
        DEFAULT_CORP_CODES[ticker] for ticker in KR_TOP_MARKET_CAP_PRIORITY_TICKERS
    ]


def test_opendart_connector_accepts_dart_api_key_alias(monkeypatch):
    monkeypatch.delenv("OPENDART_API_KEY", raising=False)
    monkeypatch.setenv("DART_API_KEY", "dart-test-token")

    connector = OpenDartConnector(
        client=httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200)),
        ),
    )

    assert connector.api_key == "dart-test-token"


def test_jquants_connector_uses_refresh_token_and_collects_statements():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "/token/auth_refresh" in str(request.url):
            return httpx.Response(200, json={"idToken": "id-token"})
        assert request.headers["Authorization"] == "Bearer id-token"
        return httpx.Response(
            200,
            json={"statements": [{"LocalCode": "72030", "EarningsPerShare": "365.94"}]},
        )

    connector = JQuantsConnector(
        refresh_token="refresh-token",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    documents = connector.collect(
        ConnectorRequest(ticker="7203.T", market="JP", start_year=2024, end_year=2024)
    )

    assert any("/token/auth_refresh" in url for url in calls)
    assert len(documents) == 1
    assert documents[0].metadata["local_code"] == "72030"
    assert (
        json.loads(documents[0].payload.decode("utf-8"))["statements"][0]["EarningsPerShare"]
        == "365.94"
    )


def test_opendart_market_standard_normalizer_extracts_reported_eps_and_metrics():
    document = ConnectorDocument(
        source="opendart",
        market="KR",
        ticker="005930.KS",
        identifier="00126380-2024-11011-CFS",
        url="https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
        payload=json.dumps(
            {
                "status": "000",
                "list": [
                    {"account_nm": "매출액", "thstrm_amount": "300000"},
                    {"account_nm": "영업이익", "thstrm_amount": "25000"},
                    {"account_nm": "희석주당이익", "thstrm_amount": "5000"},
                ],
            },
            ensure_ascii=False,
        ).encode("utf-8"),
        content_type="application/json",
        metadata={"bsns_year": 2024},
    )

    result = normalize_market_standard_document(document, "security-id", "KRW")

    assert result.adjusted_record is not None
    assert result.adjusted_record.method == "S3_MARKET_STANDARD_KR"
    assert result.adjusted_record.adjusted_eps == 5000
    assert result.adjusted_record.source_trace.method == "S3_MARKET_STANDARD_KR"
    metric_keys = {metric.metric_key for metric in result.metrics}
    assert {"revenue", "operating_income"} <= metric_keys
    assert all(
        metric.source_trace["method"] == "S3_MARKET_STANDARD_KR" for metric in result.metrics
    )


def test_opendart_market_standard_normalizer_handles_account_label_variants():
    document = ConnectorDocument(
        source="opendart",
        market="KR",
        ticker="005930.KS",
        identifier="00126380-2024-11011-CFS",
        url="https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
        payload=json.dumps(
            {
                "status": "000",
                "list": [
                    {"account_nm": "수익(매출액)", "thstrm_amount": "300000"},
                    {"account_nm": "매출원가", "thstrm_amount": "180000"},
                    {"account_nm": "Cost of sales", "thstrm_amount": "175000"},
                    {"account_nm": "영업이익(손실)", "thstrm_amount": "25000"},
                    {
                        "account_nm": "지배기업의 소유주에게 귀속되는 당기순이익",
                        "thstrm_amount": "21000",
                    },
                    {"account_nm": "보통주기본주당순이익", "thstrm_amount": "4900"},
                    {"account_nm": "보통주희석주당순이익", "thstrm_amount": "4850"},
                ],
            },
            ensure_ascii=False,
        ).encode("utf-8"),
        content_type="application/json",
        metadata={"bsns_year": 2024},
    )

    result = normalize_market_standard_document(document, "security-id", "KRW")

    assert result.adjusted_record is not None
    assert result.adjusted_record.gaap_ni == Decimal("21000")
    assert result.adjusted_record.adjusted_eps == Decimal("4850")
    by_key = {metric.metric_key: metric for metric in result.metrics}
    assert by_key["revenue"].value == Decimal("300000")
    assert by_key["operating_income"].value == Decimal("25000")
    assert by_key["net_income_parent"].value == Decimal("21000")
    assert "cost_of_sales" not in by_key
    assert all(metric.source_trace["source_type"] == "opendart" for metric in result.metrics)


def test_opendart_market_standard_normalizer_uses_account_ids_when_labels_vary():
    document = ConnectorDocument(
        source="opendart",
        market="KR",
        ticker="005930.KS",
        identifier="00126380-2024-11011-CFS",
        url="https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
        payload=json.dumps(
            {
                "status": "000",
                "list": [
                    {
                        "account_id": "ifrs-full_Revenue",
                        "account_nm": "custom top-line label",
                        "thstrm_amount": "300000",
                    },
                    {
                        "account_id": "ifrs-full_CostOfSales",
                        "account_nm": "custom cost label",
                        "thstrm_amount": "180000",
                    },
                    {
                        "account_id": "dart_OperatingIncomeLoss",
                        "account_nm": "custom operating label",
                        "thstrm_amount": "25000",
                    },
                    {
                        "account_id": "ifrs-full_ProfitLossAttributableToOwnersOfParent",
                        "account_nm": "custom parent label",
                        "thstrm_amount": "21000",
                    },
                    {
                        "account_id": "ifrs-full_DilutedEarningsLossesPerShare",
                        "account_nm": "custom diluted eps label",
                        "thstrm_amount": "4850",
                    },
                ],
            },
            ensure_ascii=False,
        ).encode("utf-8"),
        content_type="application/json",
        metadata={"bsns_year": 2024},
    )

    result = normalize_market_standard_document(document, "security-id", "KRW")

    assert result.adjusted_record is not None
    assert result.adjusted_record.gaap_ni == Decimal("21000")
    assert result.adjusted_record.adjusted_eps == Decimal("4850")
    by_key = {metric.metric_key: metric for metric in result.metrics}
    assert by_key["revenue"].value == Decimal("300000")
    assert by_key["operating_income"].value == Decimal("25000")
    assert by_key["net_income_parent"].value == Decimal("21000")
    assert "cost_of_sales" not in by_key


def test_opendart_market_standard_normalizer_handles_singular_eps_tags_and_korean_labels():
    singular_tag_document = ConnectorDocument(
        source="opendart",
        market="KR",
        ticker="329180.KS",
        identifier="01390344-2024-11011-CFS",
        url="https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
        payload=json.dumps(
            {
                "status": "000",
                "list": [
                    {
                        "account_id": "ifrs-full_BasicEarningsLossPerShare",
                        "account_nm": "기본주당이익(손실)",
                        "thstrm_amount": "7001",
                    },
                ],
            },
            ensure_ascii=False,
        ).encode("utf-8"),
        content_type="application/json",
        metadata={"bsns_year": 2024},
    )
    korean_label_document = ConnectorDocument(
        source="opendart",
        market="KR",
        ticker="373220.KS",
        identifier="01515323-2021-11011-CFS",
        url="https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
        payload=json.dumps(
            {
                "status": "000",
                "list": [
                    {
                        "account_id": "-표준계정코드 미사용-",
                        "account_nm": "보통주 기본 및 희석주당이익(손실)",
                        "thstrm_amount": "3963",
                    },
                    {
                        "account_id": "-표준계정코드 미사용-",
                        "account_nm": "보통주 기본 및 희석주당계속영업이익(손실)",
                        "thstrm_amount": "3036",
                    },
                ],
            },
            ensure_ascii=False,
        ).encode("utf-8"),
        content_type="application/json",
        metadata={"bsns_year": 2021},
    )

    singular_result = normalize_market_standard_document(singular_tag_document, "security-id", "KRW")
    korean_label_result = normalize_market_standard_document(korean_label_document, "security-id", "KRW")

    assert singular_result.adjusted_record is not None
    assert singular_result.adjusted_record.adjusted_eps == Decimal("7001")
    assert korean_label_result.adjusted_record is not None
    assert korean_label_result.adjusted_record.adjusted_eps == Decimal("3963")
    assert korean_label_result.adjusted_record.gaap_eps_diluted == Decimal("3963")


def test_market_standard_persist_injects_source_document_id_into_traces():
    source_document_id = "11111111-1111-1111-1111-111111111111"
    calls: dict[str, list] = {"adjusted": [], "metrics": []}

    class FakeRepo:
        def store_adjusted_earnings(self, security_id, source_document_id_arg, record):
            calls["adjusted"].append((security_id, source_document_id_arg, record))

        def store_metric_value(self, **kwargs):
            calls["metrics"].append(kwargs)

    document = ConnectorDocument(
        source="opendart",
        market="KR",
        ticker="005930.KS",
        identifier="00126380-2024-11011-CFS",
        url="https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
        payload=json.dumps(
            {
                "status": "000",
                "list": [
                    {"account_nm": "매출액", "thstrm_amount": "300000"},
                    {"account_nm": "영업이익", "thstrm_amount": "25000"},
                    {"account_nm": "희석주당이익", "thstrm_amount": "5000"},
                ],
            },
            ensure_ascii=False,
        ).encode("utf-8"),
        content_type="application/json",
        metadata={"bsns_year": 2024},
    )

    counts = _persist_market_standard_document(
        FakeRepo(),
        "security-id",
        source_document_id,
        document,
    )

    assert counts == {"adjusted_earnings": 1, "metric_values": 2}
    adjusted_call = calls["adjusted"][0]
    assert adjusted_call[1] == source_document_id
    assert adjusted_call[2].source_trace.source_document_id == source_document_id
    assert all(
        metric_call["source_trace"]["source_document_id"] == source_document_id
        for metric_call in calls["metrics"]
    )


def test_jquants_market_standard_normalizer_extracts_reported_eps_and_metrics():
    document = ConnectorDocument(
        source="jquants",
        market="JP",
        ticker="7203.T",
        identifier="72030-fins-statements-2024-2024",
        url="https://api.jquants.com/v1/fins/statements?code=72030",
        payload=json.dumps(
            {
                "statements": [
                    {
                        "LocalCode": "72030",
                        "CurrentFiscalYearEndDate": "2024-03-31",
                        "NetSales": "45095325",
                        "OperatingProfit": "5352944",
                        "OrdinaryProfit": "6965085",
                        "ProfitAttributableToOwnersOfParent": "4949336",
                        "DilutedEarningsPerShare": "365.94",
                    }
                ]
            }
        ).encode("utf-8"),
        content_type="application/json",
        metadata={"local_code": "72030"},
    )

    result = normalize_market_standard_document(document, "security-id", "JPY")

    assert result.adjusted_record is not None
    assert result.adjusted_record.method == "S3_MARKET_STANDARD_JP"
    assert str(result.adjusted_record.adjusted_eps) == "365.94"
    assert result.adjusted_record.source_trace.method == "S3_MARKET_STANDARD_JP"
    metric_keys = {metric.metric_key for metric in result.metrics}
    assert {"revenue", "operating_income", "recurring_income", "net_income"} <= metric_keys
    assert all(
        metric.source_trace["method"] == "S3_MARKET_STANDARD_JP" for metric in result.metrics
    )
