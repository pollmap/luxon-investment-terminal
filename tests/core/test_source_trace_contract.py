from __future__ import annotations

from decimal import Decimal

import pytest

from packages.core import SourceTrace, build_aapl_e2e_stub
from services.ingestion_worker.repository import _storage_ready_trace


def test_source_trace_normalizes_legacy_aliases() -> None:
    trace = SourceTrace(
        source_type="sec_filing",
        source_document_id="sec-doc-0000320193-24-000123",
        accession_number="0000320193-24-000123",
        form_type="10-K",
        period="FY2024",
        unit="USD/share",
        currency="USD",
        method="company_reported",
        formula="us-gaap:EarningsPerShareDiluted",
        accepted_at="2024-11-01T12:00:00+00:00",
        confidence=0.97,
    )

    assert trace.source == "sec_filing"
    assert trace.filing_id == "0000320193-24-000123"
    assert trace.form == "10-K"
    assert trace.available_at == trace.accepted_at
    assert trace.confidence == Decimal("0.97")
    trace.assert_storage_ready()
    trace.assert_point_in_time_ready()


def test_source_trace_storage_gate_rejects_missing_required_fields() -> None:
    trace = SourceTrace()

    assert trace.missing_storage_fields() == [
        "source",
        "source_document_id",
        "filing_id",
        "period",
        "unit",
        "currency",
        "method",
        "formula",
    ]
    with pytest.raises(ValueError, match="source_trace is not storage-ready"):
        trace.assert_storage_ready()
    with pytest.raises(ValueError, match="source_trace is not point-in-time-ready"):
        trace.assert_point_in_time_ready()


def test_source_trace_point_in_time_gate_derives_available_at() -> None:
    trace = SourceTrace(
        source="sec_companyfacts_bulk",
        source_document_id="sec-doc-0000320193-24-000123",
        filing_id="0000320193-24-000123",
        form="10-K",
        period="FY2024",
        unit="USD/share",
        currency="USD",
        method="SEC_COMPANYFACTS_BULK",
        formula="SEC companyfacts us-gaap:EarningsPerShareDiluted reported fact",
        filed_at="2024-11-01T12:00:00+00:00",
    )

    assert trace.available_at == trace.filed_at
    assert trace.missing_point_in_time_fields() == []
    trace.assert_point_in_time_ready()


def test_aapl_e2e_stub_preserves_contract() -> None:
    stub = build_aapl_e2e_stub()

    assert stub["entity"].ticker == "AAPL"
    assert stub["raw_filings"][0].append_only is True

    for raw_filing in stub["raw_filings"]:
        raw_filing.source_trace.assert_storage_ready()
        raw_filing.source_trace.assert_point_in_time_ready()
        assert "fixture_non_production" in raw_filing.source_trace.quality_flags

    for fact in stub["normalized_facts"]:
        fact.source_trace.assert_storage_ready()
        fact.source_trace.assert_point_in_time_ready()
        assert fact.version == fact.source_trace.version
        assert "fixture_non_production" in fact.source_trace.quality_flags

    for metric in stub["derived_metrics"]:
        metric.source_trace.assert_storage_ready()
        metric.source_trace.assert_point_in_time_ready()
        assert metric.formula
        assert metric.input_fact_ids
        assert metric.source_trace.input_fact_ids == metric.input_fact_ids


def test_storage_ready_trace_fills_repository_context() -> None:
    trace = _storage_ready_trace(
        {"source_type": "sec_companyfacts_bulk", "source_document_id": None},
        source_document_id="source-doc-1",
        filing_id="0000320193-24-000123",
        period="FY2024",
        unit="USD/share",
        currency="USD",
        method="SEC_COMPANYFACTS_BULK",
        formula="SEC companyfacts us-gaap:EarningsPerShareDiluted reported fact",
        accepted_at="2024-11-01T12:00:00+00:00",
        quality_status="source_backed_sec_companyfacts",
    )

    assert trace["source"] == "sec_companyfacts_bulk"
    assert trace["source_type"] == "sec_companyfacts_bulk"
    assert trace["source_document_id"] == "source-doc-1"
    assert trace["filing_id"] == "0000320193-24-000123"
    assert trace["accession_number"] == "0000320193-24-000123"
    assert trace["period"] == "FY2024"
    assert trace["available_at"].startswith("2024-11-01T12:00:00")
    assert trace["method"] == "SEC_COMPANYFACTS_BULK"
    assert "source_backed_sec_companyfacts" in trace["quality_flags"]


def test_storage_ready_trace_rejects_missing_formula() -> None:
    with pytest.raises(ValueError, match="formula"):
        _storage_ready_trace(
            {"source_type": "sec_companyfacts_bulk"},
            filing_id="0000320193-24-000123",
            period="FY2024",
            unit="USD/share",
            currency="USD",
            method="SEC_COMPANYFACTS_BULK",
        )


def test_storage_ready_trace_derives_logical_source_document_id() -> None:
    trace = _storage_ready_trace(
        {"source_type": "user_consensus_csv"},
        filing_id="user_consensus_csv:adjusted_operating_eps:2027:2026-06-15:median",
        period="FY2027E",
        unit="per_share",
        currency="USD",
        method="user_consensus_csv",
        formula="point-in-time consensus estimate snapshot",
    )

    assert trace["source_document_id"] == (
        "user_consensus_csv:user_consensus_csv:adjusted_operating_eps:2027:"
        "2026-06-15:median:FY2027E"
    )
