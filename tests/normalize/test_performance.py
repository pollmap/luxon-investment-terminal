from decimal import Decimal

from packages.valuation.performance import build_performance_table

TRACE = {
    "source_document_id": "price-doc",
    "source_type": "fixture",
    "filing_id": "market-price",
    "period": "FY2024",
    "unit": "per_share",
    "currency": "USD",
    "formula": "fixture price and dividend",
    "quality_status": "passed",
}


def test_performance_table_calculates_total_return_and_cagr():
    table = build_performance_table(
        "AAPL",
        [
            {
                "fiscal_year": 2020,
                "price": "100",
                "dividend": "1",
                "forecast_flag": False,
                "source_trace": TRACE,
            },
            {
                "fiscal_year": 2021,
                "price": "120",
                "dividend": "1",
                "forecast_flag": False,
                "source_trace": TRACE,
            },
            {
                "fiscal_year": 2022,
                "price": "150",
                "dividend": "1",
                "forecast_flag": False,
                "source_trace": TRACE,
            },
        ],
        currency="USD",
        initial_investment=Decimal("10000"),
        data_mode="source_backed",
    )

    row = table["rows"][0]
    assert row["shares_purchased"] == Decimal("100")
    assert row["ending_value"] == Decimal("15000")
    assert row["dividends_received"] == Decimal("200")
    assert row["total_return_pct"] == Decimal("52.00")
    assert row["reinvested_shares"] > row["shares_purchased"]
    assert row["reinvested_dividends"] > row["dividends_received"]
    assert row["reinvested_ending_value"] > row["ending_value"]
    assert row["reinvested_total_return_pct"] == Decimal("52.26")
    assert row["annualized_total_return_pct"] > Decimal("23")
    assert row["reinvested_annualized_total_return_pct"] > row["annualized_total_return_pct"]
    assert row["source_trace"]["source_type"] == "performance_derived"
    assert table["quality_status"] == "source_backed_derived"


def test_performance_table_marks_insufficient_history():
    table = build_performance_table(
        "AAPL",
        [{"fiscal_year": 2024, "price": "100", "forecast_flag": False}],
        currency="USD",
        data_mode="source_backed",
    )

    assert table["rows"] == []
    assert "insufficient_history" in table["flags"]
    assert table["quality_status"] == "source_backed_partial"
