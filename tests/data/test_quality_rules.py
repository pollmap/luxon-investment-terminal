from packages.quality import validate_source_trace, validate_valuation_row


def test_validate_source_trace_requires_provenance_fields():
    result = validate_source_trace({"source_document_id": "aapl-2024"})
    assert result.status == "warning"
    assert "missing_source_trace:source" in result.flags
    assert "missing_source_trace:method" in result.flags


def test_validate_source_trace_accepts_legacy_source_type_alias():
    result = validate_source_trace(
        {
            "source_document_id": "aapl-2024",
            "source_type": "sec_fixture",
            "filing_id": "aapl-2024-fixture",
            "period": "FY2024",
            "available_at": "2024-11-01T12:00:00+00:00",
            "unit": "per_share",
            "currency": "USD",
            "method": "company_reported",
            "formula": "reported diluted eps",
            "quality_status": "fixture_non_production",
        }
    )
    assert result.status == "passed"
    assert result.flags == []


def test_validate_valuation_row_passes_complete_trace():
    result = validate_valuation_row(
        {
            "metric": "6.08",
            "price": "250",
            "normal_multiple": "20",
            "fair_multiple": "15",
            "fair_value_price": "91.20",
            "source_trace": {
                "source_document_id": "aapl-2024",
                "source_type": "sec_fixture",
                "filing_id": "aapl-2024-fixture",
                "period": "FY2024",
                "available_at": "2024-11-01T12:00:00+00:00",
                "unit": "per_share",
                "currency": "USD",
                "method": "company_reported",
                "formula": "reported diluted eps",
                "quality_status": "fixture_non_production",
            },
        }
    )
    assert result.status == "passed"
    assert result.flags == []
