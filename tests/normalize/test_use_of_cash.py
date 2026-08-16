from packages.valuation.use_of_cash import build_use_of_cash_rows

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


def test_use_of_cash_does_not_invent_missing_dividend():
    rows = build_use_of_cash_rows(
        "AAPL",
        [
            {
                "fiscal_year": 2024,
                "revenue": "100",
                "fcf": "25",
                "eps": "5",
                "debt_to_equity": "0.4",
                "source_trace": TRACE,
            }
        ],
        [{"fiscal_year": 2024, "metric": "5", "source_trace": TRACE}],
        currency="USD",
        data_mode="source_backed",
    )

    row = rows[0]
    assert row["dividend_per_share"] is None
    assert row["dividend_payout_pct"] is None
    assert "missing_dividend_source" in row["flags"]
    assert row["fcf_margin_pct"] == 25


def test_use_of_cash_invalid_decimal_is_flagged():
    rows = build_use_of_cash_rows(
        "AAPL",
        [{"fiscal_year": 2024, "revenue": "bad", "fcf": "25", "source_trace": TRACE}],
        [{"fiscal_year": 2024, "metric": "5", "dividend": "1", "source_trace": TRACE}],
        currency="USD",
        data_mode="source_backed",
    )

    row = rows[0]
    assert row["fcf_margin_pct"] is None
    assert "invalid_decimal:revenue" in row["flags"]
    assert row["quality_status"] == "source_backed_warning"
