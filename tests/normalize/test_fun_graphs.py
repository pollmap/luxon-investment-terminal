from decimal import Decimal

from packages.valuation.fun_graphs import build_fun_graphs

TRACE = {
    "source_document_id": "financial-doc",
    "source_type": "fixture",
    "filing_id": "financial-filing",
    "period": "FY2024",
    "available_at": "2024-11-01T12:00:00+00:00",
    "unit": "reported",
    "currency": "USD",
    "method": "fixture_non_production",
    "formula": "fixture financial fact",
    "quality_status": "passed",
}


def test_fun_graphs_builds_source_traced_metric_series():
    payload = build_fun_graphs(
        "AAPL",
        [
            {
                "fiscal_year": 2023,
                "revenue": "100",
                "eps": "5",
                "gaap_eps_diluted": "4.9",
                "fcf": "20",
                "gross_margin": "40",
                "operating_margin": "25",
                "net_margin": "20",
                "roe": "35",
                "roic": "18",
                "debt_to_equity": "0.4",
                "method": "S1_SEC_RECONCILIATION",
                "confidence": "0.95",
                "source_trace": TRACE,
            },
            {
                "fiscal_year": 2024,
                "revenue": "125",
                "eps": "6",
                "gaap_eps_diluted": "5.8",
                "fcf": "25",
                "gross_margin": "42",
                "operating_margin": "27",
                "net_margin": "22",
                "roe": "36",
                "roic": "19",
                "debt_to_equity": "0.3",
                "method": "S1_SEC_RECONCILIATION",
                "confidence": "0.95",
                "source_trace": TRACE,
            },
        ],
        currency="USD",
        data_mode="source_backed",
    )

    by_key = {metric["metric_key"]: metric for metric in payload["metrics"]}
    assert by_key["revenue"]["points"][1]["value"] == Decimal("125")
    assert by_key["adjusted_eps"]["points"][1]["value"] == Decimal("6")
    assert by_key["roe_pct"]["points"][1]["source_trace"]["source_type"] == "fun_graphs_derived"
    assert by_key["roe_pct"]["points"][1]["source_trace"]["formula"]
    assert payload["summary"]["quality_status"] == "source_backed_derived"


def test_fun_graphs_flags_missing_metric_source():
    payload = build_fun_graphs(
        "AAPL",
        [{"fiscal_year": 2024, "revenue": "bad", "source_trace": TRACE}],
        currency="USD",
        data_mode="source_backed",
    )

    revenue = next(metric for metric in payload["metrics"] if metric["metric_key"] == "revenue")
    assert revenue["points"][0]["value"] is None
    assert "invalid_decimal:revenue" in revenue["points"][0]["flags"]
    assert revenue["points"][0]["quality_status"] == "source_backed_warning"
