from decimal import Decimal

from packages.valuation.fiscal_fitness import build_fiscal_fitness_rows

TRACE = {
    "source_document_id": "doc",
    "source_type": "fixture",
    "filing_id": "filing",
    "period": "FY2024",
    "unit": "reported",
    "currency": "USD",
    "formula": "fixture test trace",
    "quality_status": "passed",
}


def test_fiscal_fitness_derives_margin_and_growth_without_inventing_liquidity():
    rows = build_fiscal_fitness_rows(
        "AAPL",
        [
            {
                "fiscal_year": 2023,
                "revenue": "100",
                "eps": "5",
                "fcf": "20",
                "roe": "30",
                "roic": "18",
                "debt_to_equity": "0.4",
                "source_trace": TRACE,
            },
            {
                "fiscal_year": 2024,
                "revenue": "125",
                "eps": "6",
                "fcf": "25",
                "roe": "32",
                "roic": "19",
                "debt_to_equity": "0.3",
                "source_trace": TRACE,
            },
        ],
        currency="USD",
        data_mode="source_backed",
    )

    by_key = {(row["fiscal_year"], row["metric_key"]): row for row in rows}
    assert by_key[(2024, "fcf_margin_pct")]["value"] == Decimal("20.00")
    assert by_key[(2024, "revenue_growth_pct")]["value"] == Decimal("25.00")
    assert by_key[(2024, "eps_growth_pct")]["value"] == Decimal("20.00")
    assert by_key[(2024, "current_ratio")]["value"] is None
    assert "missing_current_ratio_source" in by_key[(2024, "current_ratio")]["flags"]


def test_fiscal_fitness_invalid_decimal_is_flagged():
    rows = build_fiscal_fitness_rows(
        "AAPL",
        [{"fiscal_year": 2024, "revenue": "bad", "fcf": "25", "source_trace": TRACE}],
        currency="USD",
        data_mode="source_backed",
    )

    fcf_margin = next(row for row in rows if row["metric_key"] == "fcf_margin_pct")
    assert fcf_margin["value"] is None
    assert "invalid_decimal:revenue" in fcf_margin["flags"]
    assert fcf_margin["quality_status"] == "source_backed_warning"


def test_fiscal_fitness_rejects_non_finite_decimal():
    rows = build_fiscal_fitness_rows(
        "AAPL",
        [{"fiscal_year": 2024, "gross_margin": "NaN", "source_trace": TRACE}],
        currency="USD",
        data_mode="source_backed",
    )

    gross_margin = next(row for row in rows if row["metric_key"] == "gross_margin_pct")
    assert gross_margin["value"] is None
    assert "invalid_decimal:gross_margin" in gross_margin["flags"]
    assert gross_margin["quality_status"] == "source_backed_warning"


def test_fiscal_fitness_preserves_metric_specific_trace():
    revenue_trace = TRACE | {"source_document_id": "revenue-doc"}
    fcf_trace = TRACE | {"source_document_id": "fcf-doc"}
    rows = build_fiscal_fitness_rows(
        "AAPL",
        [
            {
                "fiscal_year": 2024,
                "revenue": "100",
                "fcf": "20",
                "source_trace": TRACE,
                "metric_traces": {"revenue": revenue_trace, "fcf": fcf_trace},
            }
        ],
        currency="USD",
        data_mode="source_backed",
    )

    fcf_margin = next(row for row in rows if row["metric_key"] == "fcf_margin_pct")
    assert fcf_margin["source_trace"]["metric_input_traces"]["revenue"] == revenue_trace
    assert fcf_margin["source_trace"]["metric_input_traces"]["fcf"] == fcf_trace
