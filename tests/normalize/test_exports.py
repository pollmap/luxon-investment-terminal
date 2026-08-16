import csv
import io

from packages.valuation.exports import (
    audit_rows_to_csv,
    build_research_export_bundle,
    research_bundle_to_json,
    research_report_to_markdown,
)

TRACE = {
    "source_document_id": "doc-1",
    "source_type": "sec_exhibit",
    "accession_number": "0000320193-24-000123",
    "filing_id": "filing-1",
    "period": "FY2024",
    "unit": "per_share",
    "currency": "USD",
    "formula": "source traced formula",
    "quality_status": "passed",
}


def test_research_export_markdown_preserves_evidence_and_trace():
    report = {
        "title": "AAPL Source-Audited Research Report",
        "quality_status": "passed",
        "flags": [],
        "executive_summary": ["AAPL trades versus deterministic fair value."],
        "sections": [
            {
                "section_key": "valuation",
                "title": "Valuation",
                "verdict": "premium_to_fair_value",
                "bullets": ["Fair value is formula-based."],
                "flags": [],
                "quality_status": "passed",
                "evidence": [
                    {
                        "label": "Fair value",
                        "value": "91.20",
                        "unit": "USD",
                        "source_trace": TRACE,
                    }
                ],
                "source_trace": TRACE,
            }
        ],
        "source_trace": TRACE,
    }
    audit_rows = [
        {
            "fact_id": "AAPL-2024-valuation.fair_value_price",
            "fact_name": "valuation.fair_value_price",
            "fiscal_year": 2024,
            "value": "91.20",
            "method": "valuation_map",
            "policy": "valuation_map",
            "confidence": None,
            "quality_status": "passed",
            "flags": [],
            "formula": "metric * fair_multiple",
            "source_trace": TRACE,
        }
    ]

    bundle = build_research_export_bundle("AAPL", report, audit_rows)
    markdown = research_report_to_markdown(bundle)
    json_payload = research_bundle_to_json(bundle)
    csv_payload = audit_rows_to_csv(audit_rows)

    assert "# AAPL Source-Audited Research Report" in markdown
    assert "premium_to_fair_value" in markdown
    assert "valuation.fair_value_price" in markdown
    assert '"export_version": "research_export_v1"' in json_payload
    assert '"trace_sections"' in json_payload
    assert bundle["data_audit"][0]["trace_sections"][0]["title"] == "Source evidence"
    assert "source_document_id" in csv_payload
    assert "doc-1" in csv_payload
    assert "source_trace_json" in csv_payload
    parsed_rows = list(csv.DictReader(io.StringIO(csv_payload)))
    assert parsed_rows[0]["source_type"] == "sec_exhibit"
    assert parsed_rows[0]["accession_number"] == "0000320193-24-000123"
    assert '"source_document_id":"doc-1"' in parsed_rows[0]["source_trace_json"]


def test_audit_csv_preserves_nested_input_trace_keys():
    audit_rows = [
        {
            "fact_id": "AAPL-2025-forecast.total_return_cagr_pct",
            "fact_name": "forecast.total_return_cagr_pct",
            "fiscal_year": 2025,
            "value": "12.34",
            "method": "forecast_derived",
            "policy": "forecast",
            "confidence": None,
            "quality_status": "passed",
            "flags": [],
            "formula": "total_return_cagr_pct = deterministic formula",
            "source_trace": {
                **TRACE,
                "calculation_inputs": {
                    "start_price": "190.00",
                    "target_price": "220.00",
                    "forecast_year": 1,
                    "price_source_trace": {
                        "source_type": "price_point",
                        "source_document_id": "aapl-2024-price",
                    },
                },
                "dividend_source_trace": {
                    "source_type": "dividend",
                    "source_document_id": "aapl-2024-dividend",
                },
            },
        }
    ]

    csv_payload = audit_rows_to_csv(audit_rows)
    row = next(csv.DictReader(io.StringIO(csv_payload)))

    assert row["input_trace_keys"] == "calculation_inputs,dividend_source_trace"
    assert '"target_price":"220.00"' in row["calculation_inputs_json"]
    assert '"dividend_source_trace"' in row["source_trace_json"]
