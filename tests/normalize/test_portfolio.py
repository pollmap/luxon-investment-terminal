from decimal import Decimal

from packages.valuation.portfolio import build_portfolio_summary, parse_transactions_csv


def test_parse_transactions_csv_and_build_summary():
    transactions = parse_transactions_csv(
        "date,ticker,side,quantity,price,currency,sector\n"
        "2024-01-01,AAPL,buy,2,100,USD,Technology\n"
        "2024-06-01,AAPL,sell,1,120,USD,Technology\n"
    )
    summary = build_portfolio_summary(transactions, {"AAPL": Decimal("150")})
    assert summary["holdings"][0]["quantity"] == Decimal("1")
    assert summary["holdings"][0]["market_value"] == Decimal("150.00")
    assert summary["sector_weights"]["Technology"] == Decimal("100.00")
