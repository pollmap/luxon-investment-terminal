import csv
import json
from pathlib import Path

from services.api.sample_data import GAAP_FACTS, PORTFOLIO_FIXTURE_CSV, SAMPLE_SECURITY_META


FIXTURE_ROOT = Path("tests/fixtures/terminal")


def test_seed_universe_is_loaded_from_json_fixture():
    fixture = json.loads((FIXTURE_ROOT / "seed_universe.json").read_text(encoding="utf-8"))
    for ticker in ["AAPL", "NVDA", "005930.KS", "7203.T"]:
        assert ticker in fixture["securities"]
        assert ticker in SAMPLE_SECURITY_META
        assert ticker in GAAP_FACTS


def test_portfolio_transactions_fixture_is_csv():
    rows = list(csv.DictReader((FIXTURE_ROOT / "portfolio_transactions.csv").open(encoding="utf-8")))
    assert rows
    assert "AAPL" in PORTFOLIO_FIXTURE_CSV
    assert {"date", "ticker", "side", "quantity", "price", "currency", "sector"} <= set(rows[0])


def test_financial_series_fixture_covers_seed_universe():
    rows = list(csv.DictReader((FIXTURE_ROOT / "financial_series.csv").open(encoding="utf-8")))
    covered = {row["ticker"] for row in rows}
    assert {"AAPL", "NVDA", "005930.KS", "7203.T"} <= covered
