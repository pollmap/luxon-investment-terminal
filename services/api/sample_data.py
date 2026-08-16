from __future__ import annotations

import csv
import json
from decimal import Decimal
from pathlib import Path

from backend.normalize.enums import NormalizationMethod, QualityStatus, SectorPolicy
from backend.normalize.schemas import (
    AdjustedEarningsRecord,
    NormalizationPolicy,
    NormalizationResult,
    SourceDocument,
    SourceTrace,
    WaterfallStep,
)
from backend.normalize.service import NormalizationService
from packages.valuation.engine import calculate_cagr


SAMPLE_SECURITY_META = {
    "AAPL": {"name": "Apple Inc.", "market": "US", "country": "US", "currency": "USD", "sector_policy": "default", "source_label": "fixture_non_production"},
    "NVDA": {"name": "NVIDIA Corp.", "market": "US", "country": "US", "currency": "USD", "sector_policy": "default", "source_label": "fixture_non_production"},
    "005930.KS": {"name": "Samsung Electronics Co., Ltd.", "market": "KR", "country": "KR", "currency": "KRW", "sector_policy": "default", "source_label": "fixture_non_production"},
    "7203.T": {"name": "Toyota Motor Corp.", "market": "JP", "country": "JP", "currency": "JPY", "sector_policy": "default", "source_label": "fixture_non_production"},
    "CRM": {"name": "Salesforce Inc.", "market": "US", "country": "US", "currency": "USD", "sector_policy": "default", "source_label": "fixture_non_production"},
    "O": {"name": "Realty Income Corp.", "market": "US", "country": "US", "currency": "USD", "sector_policy": "reit", "source_label": "fixture_non_production"},
    "JPM": {"name": "JPMorgan Chase & Co.", "market": "US", "country": "US", "currency": "USD", "sector_policy": "bank", "source_label": "fixture_non_production"},
}


FINANCIAL_SERIES = {
    "AAPL": {
        2020: {"revenue": "274515", "fcf": "73365", "gross_margin": "38.2", "op_margin": "24.1", "net_margin": "20.9", "roe": "73.7", "roic": "34.7", "debt_to_equity": "1.72"},
        2021: {"revenue": "365817", "fcf": "92953", "gross_margin": "41.8", "op_margin": "29.8", "net_margin": "25.9", "roe": "147.4", "roic": "49.5", "debt_to_equity": "1.98"},
        2022: {"revenue": "394328", "fcf": "111443", "gross_margin": "43.3", "op_margin": "30.3", "net_margin": "25.3", "roe": "175.5", "roic": "56.8", "debt_to_equity": "1.76"},
        2023: {"revenue": "383285", "fcf": "99584", "gross_margin": "44.1", "op_margin": "29.8", "net_margin": "25.3", "roe": "171.9", "roic": "56.0", "debt_to_equity": "1.79"},
        2024: {"revenue": "391035", "fcf": "108807", "gross_margin": "46.2", "op_margin": "31.5", "net_margin": "24.0", "roe": "151.1", "roic": "53.1", "debt_to_equity": "1.87"},
    },
    "NVDA": {
        2020: {"revenue": "10918", "fcf": "4314", "gross_margin": "62.0", "op_margin": "26.1", "net_margin": "25.6", "roe": "25.9", "roic": "20.8", "debt_to_equity": "0.23"},
        2021: {"revenue": "16675", "fcf": "4694", "gross_margin": "62.3", "op_margin": "27.2", "net_margin": "26.0", "roe": "29.8", "roic": "22.1", "debt_to_equity": "0.47"},
        2022: {"revenue": "26914", "fcf": "8132", "gross_margin": "64.9", "op_margin": "37.3", "net_margin": "36.2", "roe": "44.8", "roic": "30.9", "debt_to_equity": "0.41"},
        2023: {"revenue": "26974", "fcf": "3808", "gross_margin": "56.9", "op_margin": "20.7", "net_margin": "16.2", "roe": "19.8", "roic": "13.9", "debt_to_equity": "0.34"},
        2024: {"revenue": "60922", "fcf": "27021", "gross_margin": "73.8", "op_margin": "54.1", "net_margin": "48.9", "roe": "69.2", "roic": "55.0", "debt_to_equity": "0.27"},
    },
    "005930.KS": {
        2020: {"revenue": "236807", "fcf": "20500", "gross_margin": "39.0", "op_margin": "15.2", "net_margin": "11.0", "roe": "10.0", "roic": "8.0", "debt_to_equity": "0.06"},
        2021: {"revenue": "279605", "fcf": "18200", "gross_margin": "40.6", "op_margin": "18.5", "net_margin": "14.0", "roe": "13.9", "roic": "10.9", "debt_to_equity": "0.05"},
        2022: {"revenue": "302231", "fcf": "9500", "gross_margin": "37.0", "op_margin": "14.4", "net_margin": "18.1", "roe": "17.1", "roic": "11.5", "debt_to_equity": "0.04"},
        2023: {"revenue": "258935", "fcf": "-7800", "gross_margin": "30.1", "op_margin": "2.5", "net_margin": "5.6", "roe": "4.2", "roic": "1.6", "debt_to_equity": "0.05"},
        2024: {"revenue": "300900", "fcf": "11200", "gross_margin": "34.2", "op_margin": "10.8", "net_margin": "11.3", "roe": "9.8", "roic": "7.0", "debt_to_equity": "0.05"},
    },
    "7203.T": {
        2020: {"revenue": "29929992", "fcf": "1140000", "gross_margin": "18.2", "op_margin": "8.2", "net_margin": "6.9", "roe": "10.4", "roic": "6.8", "debt_to_equity": "0.94"},
        2021: {"revenue": "27214594", "fcf": "920000", "gross_margin": "18.7", "op_margin": "8.1", "net_margin": "8.2", "roe": "11.0", "roic": "6.5", "debt_to_equity": "0.91"},
        2022: {"revenue": "31379507", "fcf": "760000", "gross_margin": "17.9", "op_margin": "9.5", "net_margin": "7.8", "roe": "10.2", "roic": "7.1", "debt_to_equity": "0.88"},
        2023: {"revenue": "37154298", "fcf": "680000", "gross_margin": "19.0", "op_margin": "7.9", "net_margin": "6.6", "roe": "9.1", "roic": "5.9", "debt_to_equity": "0.86"},
        2024: {"revenue": "45095325", "fcf": "1820000", "gross_margin": "20.3", "op_margin": "11.9", "net_margin": "11.0", "roe": "15.8", "roic": "9.5", "debt_to_equity": "0.82"},
    },
    "CRM": {
        2020: {"revenue": "17098", "fcf": "3500", "gross_margin": "74.4", "op_margin": "3.4", "net_margin": "0.7", "roe": "0.7", "roic": "1.2", "debt_to_equity": "0.08"},
        2021: {"revenue": "21252", "fcf": "4090", "gross_margin": "74.4", "op_margin": "2.1", "net_margin": "19.2", "roe": "7.0", "roic": "1.3", "debt_to_equity": "0.05"},
        2022: {"revenue": "26492", "fcf": "6020", "gross_margin": "73.9", "op_margin": "2.1", "net_margin": "5.5", "roe": "2.4", "roic": "1.7", "debt_to_equity": "0.18"},
        2023: {"revenue": "31352", "fcf": "6310", "gross_margin": "73.3", "op_margin": "3.3", "net_margin": "0.7", "roe": "0.3", "roic": "2.5", "debt_to_equity": "0.16"},
        2024: {"revenue": "34857", "fcf": "9900", "gross_margin": "75.5", "op_margin": "14.4", "net_margin": "11.9", "roe": "7.5", "roic": "6.2", "debt_to_equity": "0.15"},
    },
    "O": {
        2020: {"revenue": "1647", "fcf": "1040", "gross_margin": "92.0", "op_margin": "34.0", "net_margin": "24.0", "roe": "4.1", "roic": "3.8", "debt_to_equity": "0.75"},
        2021: {"revenue": "2081", "fcf": "1200", "gross_margin": "92.4", "op_margin": "33.1", "net_margin": "17.3", "roe": "3.5", "roic": "3.6", "debt_to_equity": "0.78"},
        2022: {"revenue": "3297", "fcf": "1900", "gross_margin": "92.8", "op_margin": "38.0", "net_margin": "26.4", "roe": "5.2", "roic": "4.2", "debt_to_equity": "0.82"},
        2023: {"revenue": "4080", "fcf": "2320", "gross_margin": "92.9", "op_margin": "36.5", "net_margin": "21.4", "roe": "4.8", "roic": "4.0", "debt_to_equity": "0.84"},
        2024: {"revenue": "5140", "fcf": "2820", "gross_margin": "93.0", "op_margin": "35.7", "net_margin": "15.2", "roe": "4.0", "roic": "3.9", "debt_to_equity": "0.86"},
    },
    "JPM": {
        2020: {"revenue": "119543", "fcf": "29131", "gross_margin": "100.0", "op_margin": "34.0", "net_margin": "24.4", "roe": "10.9", "roic": "6.9", "debt_to_equity": "1.65"},
        2021: {"revenue": "121649", "fcf": "48334", "gross_margin": "100.0", "op_margin": "48.7", "net_margin": "39.7", "roe": "18.7", "roic": "9.5", "debt_to_equity": "1.58"},
        2022: {"revenue": "128695", "fcf": "37676", "gross_margin": "100.0", "op_margin": "38.5", "net_margin": "29.3", "roe": "14.0", "roic": "7.3", "debt_to_equity": "1.53"},
        2023: {"revenue": "158104", "fcf": "49552", "gross_margin": "100.0", "op_margin": "43.2", "net_margin": "31.3", "roe": "17.9", "roic": "8.9", "debt_to_equity": "1.50"},
        2024: {"revenue": "177556", "fcf": "58500", "gross_margin": "100.0", "op_margin": "44.1", "net_margin": "32.9", "roe": "18.4", "roic": "9.0", "debt_to_equity": "1.48"},
    },
}


FORECAST_PRESETS = {
    "AAPL": {"consensus_low_growth_rate": "5.0", "consensus_growth_rate": "7.0", "consensus_high_growth_rate": "9.0", "lt_growth_rate": "8.0", "analyst_count": 31},
    "NVDA": {"consensus_low_growth_rate": "14.0", "consensus_growth_rate": "18.0", "consensus_high_growth_rate": "22.0", "lt_growth_rate": "20.0", "analyst_count": 44},
    "CRM": {"consensus_low_growth_rate": "8.0", "consensus_growth_rate": "11.0", "consensus_high_growth_rate": "14.0", "lt_growth_rate": "12.5", "analyst_count": 38},
    "O": {"consensus_low_growth_rate": "2.5", "consensus_growth_rate": "4.0", "consensus_high_growth_rate": "5.5", "lt_growth_rate": "3.5", "analyst_count": 18},
    "JPM": {"consensus_low_growth_rate": "3.5", "consensus_growth_rate": "5.0", "consensus_high_growth_rate": "6.5", "lt_growth_rate": "5.5", "analyst_count": 25},
    "005930.KS": {"consensus_low_growth_rate": "9.0", "consensus_growth_rate": "9.0", "consensus_high_growth_rate": "9.0", "lt_growth_rate": "8.5", "analyst_count": 0},
    "7203.T": {"consensus_low_growth_rate": "4.5", "consensus_growth_rate": "4.5", "consensus_high_growth_rate": "4.5", "lt_growth_rate": "4.0", "analyst_count": 0},
}


PORTFOLIO_FIXTURE_CSV = """date,ticker,side,quantity,price,currency,sector
2023-01-10,AAPL,buy,10,130,USD,Technology
2023-05-17,NVDA,buy,4,310,USD,Semiconductors
2024-02-12,005930.KS,buy,20,74000,KRW,Semiconductors
2024-03-20,7203.T,buy,30,2850,JPY,Automobiles
2024-11-15,NVDA,sell,1,135,USD,Semiconductors
"""


def recession_bands_for(ticker: str, start_year: int, end_year: int) -> list[dict]:
    meta = SAMPLE_SECURITY_META.get(ticker.upper(), {})
    if meta.get("country") != "US" or start_year > 2020 or end_year < 2020:
        return []
    return [
        {
            "series_id": "USREC",
            "start_date": "2020-02-01",
            "end_date": "2020-04-30",
            "source": "fred_fixture",
            "source_trace": {
                "source_type": "fixture_non_production_macro",
                "source_document_id": "fred-usrec-2020-fixture",
                "filing_id": "fred-usrec",
                "period": "2020-02-01:2020-04-30",
                "available_at": "2020-05-01T00:00:00+00:00",
                "unit": "indicator",
                "currency": "N/A",
                "formula": "Contiguous FRED USREC observations equal to 1",
                "method": "FRED_RECESSION_BAND_FIXTURE",
                "quality_status": "fixture_non_production",
                "source_url": "https://fred.stlouisfed.org/series/USREC",
            },
        }
    ]


GAAP_FACTS = {
    "AAPL": {
        2020: {"gaap_eps_diluted": "3.28", "gaap_ni": "57411000000", "diluted_shares": "17528214000"},
        2021: {"gaap_eps_diluted": "5.61", "gaap_ni": "94680000000", "diluted_shares": "16864919000"},
        2022: {"gaap_eps_diluted": "6.11", "gaap_ni": "99803000000", "diluted_shares": "16325819000"},
        2023: {"gaap_eps_diluted": "6.13", "gaap_ni": "96995000000", "diluted_shares": "15812547000"},
        2024: {"gaap_eps_diluted": "6.08", "gaap_ni": "93736000000", "diluted_shares": "15408000000"},
    },
    "NVDA": {
        2020: {"gaap_eps_diluted": "0.64", "gaap_ni": "2796000000", "diluted_shares": "4350000000"},
        2021: {"gaap_eps_diluted": "1.73", "gaap_ni": "4332000000", "diluted_shares": "2506000000"},
        2022: {"gaap_eps_diluted": "3.85", "gaap_ni": "9752000000", "diluted_shares": "2535000000"},
        2023: {"gaap_eps_diluted": "1.74", "gaap_ni": "4368000000", "diluted_shares": "2507000000"},
        2024: {"gaap_eps_diluted": "11.93", "gaap_ni": "29760000000", "diluted_shares": "2495000000"},
    },
    "CRM": {
        2020: {"gaap_eps_diluted": "0.15", "gaap_ni": "126000000", "diluted_shares": "840000000"},
        2021: {"gaap_eps_diluted": "4.38", "gaap_ni": "4072000000", "diluted_shares": "930000000"},
        2022: {"gaap_eps_diluted": "1.48", "gaap_ni": "1444000000", "diluted_shares": "976000000"},
        2023: {"gaap_eps_diluted": "0.21", "gaap_ni": "208000000", "diluted_shares": "990000000"},
        2024: {"gaap_eps_diluted": "4.20", "gaap_ni": "4136000000", "diluted_shares": "985000000"},
    },
    "O": {
        2020: {"gaap_eps_diluted": "1.16", "gaap_ni": "395000000", "diluted_shares": "340000000"},
        2021: {"gaap_eps_diluted": "0.87", "gaap_ni": "360000000", "diluted_shares": "414000000"},
        2022: {"gaap_eps_diluted": "1.42", "gaap_ni": "869000000", "diluted_shares": "612000000"},
        2023: {"gaap_eps_diluted": "1.26", "gaap_ni": "872000000", "diluted_shares": "692000000"},
        2024: {"gaap_eps_diluted": "1.05", "gaap_ni": "780000000", "diluted_shares": "743000000"},
    },
    "JPM": {
        2020: {"gaap_eps_diluted": "8.88", "gaap_ni": "29131000000", "diluted_shares": "3280000000"},
        2021: {"gaap_eps_diluted": "15.36", "gaap_ni": "48334000000", "diluted_shares": "3147000000"},
        2022: {"gaap_eps_diluted": "12.09", "gaap_ni": "37676000000", "diluted_shares": "3116000000"},
        2023: {"gaap_eps_diluted": "16.23", "gaap_ni": "49552000000", "diluted_shares": "3053000000"},
        2024: {"gaap_eps_diluted": "18.22", "gaap_ni": "58500000000", "diluted_shares": "3210000000"},
    },
    "005930.KS": {
        2020: {"gaap_eps_diluted": "3841", "gaap_ni": "26090846000000", "diluted_shares": "6792669250"},
        2021: {"gaap_eps_diluted": "5777", "gaap_ni": "39243791000000", "diluted_shares": "6792669250"},
        2022: {"gaap_eps_diluted": "8057", "gaap_ni": "54730018000000", "diluted_shares": "6792669250"},
        2023: {"gaap_eps_diluted": "2131", "gaap_ni": "14473401000000", "diluted_shares": "6792669250"},
        2024: {"gaap_eps_diluted": "4990", "gaap_ni": "33884896000000", "diluted_shares": "6792669250"},
    },
    "7203.T": {
        2020: {"gaap_eps_diluted": "150.10", "gaap_ni": "2076183000000", "diluted_shares": "13831900000"},
        2021: {"gaap_eps_diluted": "245.05", "gaap_ni": "2245261000000", "diluted_shares": "9162700000"},
        2022: {"gaap_eps_diluted": "205.23", "gaap_ni": "2451759000000", "diluted_shares": "11945900000"},
        2023: {"gaap_eps_diluted": "179.47", "gaap_ni": "2451318000000", "diluted_shares": "13658900000"},
        2024: {"gaap_eps_diluted": "365.94", "gaap_ni": "4944933000000", "diluted_shares": "13513200000"},
    },
}


PRICE_DIVIDEND = {
    "AAPL": {2020: ("132", "0.80"), 2021: ("177", "0.85"), 2022: ("129", "0.90"), 2023: ("192", "0.94"), 2024: ("250", "1.00")},
    "NVDA": {2020: ("130", "0.16"), 2021: ("294", "0.16"), 2022: ("146", "0.16"), 2023: ("495", "0.16"), 2024: ("138", "0.04")},
    "CRM": {2020: ("222", "0"), 2021: ("254", "0"), 2022: ("132", "0"), 2023: ("263", "0"), 2024: ("334", "1.60")},
    "O": {2020: ("62", "2.80"), 2021: ("71", "2.85"), 2022: ("63", "2.97"), 2023: ("57", "3.07"), 2024: ("53", "3.16")},
    "JPM": {2020: ("127", "3.60"), 2021: ("158", "3.70"), 2022: ("134", "4.00"), 2023: ("170", "4.10"), 2024: ("240", "4.60")},
    "005930.KS": {2020: ("81000", "2994"), 2021: ("78300", "2994"), 2022: ("55300", "1444"), 2023: ("78500", "1444"), 2024: ("53200", "1444")},
    "7203.T": {2020: ("1550", "44"), 2021: ("2228", "52"), 2022: ("1817", "53"), 2023: ("1880", "60"), 2024: ("3140", "75")},
}


FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "terminal"


def _year_keyed(raw: dict) -> dict:
    return {
        ticker: {int(year): values for year, values in rows.items()}
        for ticker, rows in raw.items()
    }


def _load_terminal_json_fixture() -> dict | None:
    path = FIXTURE_ROOT / "seed_universe.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _load_financial_series_fixture() -> dict:
    path = FIXTURE_ROOT / "financial_series.csv"
    if not path.exists():
        return FINANCIAL_SERIES
    rows: dict[str, dict[int, dict[str, str]]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            ticker = raw.pop("ticker")
            fiscal_year = int(raw.pop("fiscal_year"))
            rows.setdefault(ticker, {})[fiscal_year] = raw
    return rows


def _load_portfolio_fixture_csv() -> str:
    path = FIXTURE_ROOT / "portfolio_transactions.csv"
    if not path.exists():
        return PORTFOLIO_FIXTURE_CSV
    return path.read_text(encoding="utf-8").strip()


_terminal_fixture = _load_terminal_json_fixture()
if _terminal_fixture:
    SAMPLE_SECURITY_META = _terminal_fixture["securities"]
    FORECAST_PRESETS = _terminal_fixture["forecast_presets"]
    GAAP_FACTS = _year_keyed(_terminal_fixture["gaap_facts"])
    PRICE_DIVIDEND = {
        ticker: {int(year): tuple(values) for year, values in rows.items()}
        for ticker, rows in _terminal_fixture["price_dividend"].items()
    }
FINANCIAL_SERIES = _load_financial_series_fixture()
PORTFOLIO_FIXTURE_CSV = _load_portfolio_fixture_csv()


S1_HTML = {
    "AAPL": """
    <html><body><h2>Reconciliation of GAAP to Non-GAAP Results</h2>
    <table>
      <tr><th>Year ended September 28, 2024</th><th>2024</th></tr>
      <tr><td>GAAP net income</td><td>93,736</td></tr>
      <tr><td>GAAP diluted EPS</td><td>6.08</td></tr>
      <tr><td>Adjusted diluted EPS</td><td>6.08</td></tr>
      <tr><td>Weighted average diluted shares</td><td>15,408</td></tr>
    </table></body></html>
    """,
    "NVDA": """
    <html><body><h2>Reconciliation of GAAP and Non-GAAP Financial Measures</h2>
    <table>
      <tr><th>Fiscal year 2024</th><th>2024</th></tr>
      <tr><td>GAAP net income</td><td>29,760</td></tr>
      <tr><td>GAAP diluted EPS</td><td>11.93</td></tr>
      <tr><td>Acquisition-related and other costs</td><td>1,000</td></tr>
      <tr><td>Tax effect of non-GAAP adjustments</td><td>210</td></tr>
      <tr><td>Non-GAAP diluted EPS</td><td>12.25</td></tr>
      <tr><td>Weighted average diluted shares</td><td>2,495</td></tr>
    </table></body></html>
    """,
    "CRM": """
    <html><body><h2>Reconciliation of GAAP to Non-GAAP</h2>
    <table>
      <tr><th>Fiscal year 2024</th><th>2024</th></tr>
      <tr><td>GAAP net income</td><td>4,136</td></tr>
      <tr><td>GAAP diluted EPS</td><td>4.20</td></tr>
      <tr><td>Stock-based compensation expense</td><td>3,800</td></tr>
      <tr><td>Amortization of acquired intangible assets</td><td>900</td></tr>
      <tr><td>Tax effect of non-GAAP adjustments</td><td>987</td></tr>
      <tr><td>Non-GAAP diluted EPS</td><td>8.05</td></tr>
      <tr><td>Weighted average diluted shares</td><td>985</td></tr>
    </table></body></html>
    """,
}


XBRL_FACTS = {
    "O": {
        2024: {
            "NetIncomeLoss": "780000000",
            "EarningsPerShareDiluted": "1.05",
            "WeightedAverageNumberOfDilutedSharesOutstanding": "743000000",
            "AssetImpairmentCharges": "50000000",
            "IncomeTaxExpenseBenefit": "0",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": "780000000",
        }
    }
}


def sample_source_documents(ticker: str) -> list[SourceDocument]:
    ticker = ticker.upper()
    html = S1_HTML.get(ticker)
    if not html:
        return []
    return [
        SourceDocument(
            id=f"{ticker.lower()}-fixture-2024",
            ticker=ticker,
            accession_number=f"{ticker.lower()}-fixture-2024",
            form_type="8-K",
            filing_url=f"https://www.sec.gov/Archives/edgar/data/{ticker.lower()}/fixture-index.html",
            source_url=f"https://www.sec.gov/Archives/edgar/data/{ticker.lower()}/ex99-fixture.html",
            description="Synthetic non-production fixture shaped like an earnings release reconciliation",
            document_type="EX-99.1",
            content=html,
            metadata={"fiscal_year": 2024},
        )
    ]


def sample_normalization_service(ticker: str) -> NormalizationService:
    ticker = ticker.upper()
    return NormalizationService(
        source_documents=sample_source_documents(ticker),
        xbrl_facts_by_year=XBRL_FACTS.get(ticker, {}),
        gaap_facts_by_year=GAAP_FACTS.get(ticker, {}),
    )


def sample_normalization_result(
    ticker: str,
    policy: NormalizationPolicy | None = None,
    start_year: int | None = None,
    end_year: int | None = None,
) -> NormalizationResult:
    ticker = ticker.upper()
    policy = policy or NormalizationPolicy()
    direct = sample_normalization_service(ticker).normalize(ticker, policy, start_year, end_year)
    existing = {
        row.fiscal_year: _with_complete_source_trace(row, ticker)
        for row in direct.series
    }
    merged: list[AdjustedEarningsRecord] = []
    for year in sorted(GAAP_FACTS.get(ticker, {})):
        if start_year is not None and year < start_year:
            continue
        if end_year is not None and year > end_year:
            continue
        merged.append(existing.get(year) or _fallback_adjusted_record(ticker, year, policy))
    return NormalizationResult(
        ticker=ticker,
        policy=policy,
        series=merged,
        failed_strategies=direct.failed_strategies,
        warnings=direct.warnings,
    )


def _fallback_adjusted_record(ticker: str, year: int, policy: NormalizationPolicy) -> AdjustedEarningsRecord:
    meta = SAMPLE_SECURITY_META[ticker]
    facts = GAAP_FACTS[ticker][year]
    method = _fallback_method(meta["market"])
    source_trace = SourceTrace(**source_trace_for(ticker, year, "gaap_eps_diluted"))
    quality_status = QualityStatus.PASSED if method in {
        NormalizationMethod.S3_MARKET_STANDARD_KR,
        NormalizationMethod.S3_MARKET_STANDARD_JP,
    } else QualityStatus.FALLBACK
    flags = [] if quality_status == QualityStatus.PASSED else ["gaap_fallback"]
    return AdjustedEarningsRecord(
        security_id=ticker,
        ticker=ticker,
        fiscal_year=year,
        fiscal_period="FY",
        gaap_ni=Decimal(facts["gaap_ni"]),
        gaap_eps_diluted=Decimal(facts["gaap_eps_diluted"]),
        adjusted_ni=Decimal(facts["gaap_ni"]),
        adjusted_eps=Decimal(facts["gaap_eps_diluted"]),
        diluted_shares=Decimal(facts["diluted_shares"]),
        currency=meta["currency"],
        method=method,
        policy=policy.key,
        sector_policy=SectorPolicy(meta["sector_policy"]),
        confidence=Decimal("0.65") if quality_status == QualityStatus.PASSED else Decimal("0.35"),
        quality_status=quality_status,
        flags=flags,
        formula="adjusted_eps = gaap_eps_diluted; transparent fallback or market-standard mapping for fixture period",
        source_trace=source_trace,
        waterfall=[
            WaterfallStep(
                label="GAAP diluted EPS",
                category="gaap_eps_diluted",
                after_tax_impact=Decimal(facts["gaap_ni"]),
                eps_impact=Decimal(facts["gaap_eps_diluted"]),
                source_trace=source_trace,
            )
        ],
        metadata={"data_mode": "fixture_non_production"},
    )


def _with_complete_source_trace(record: AdjustedEarningsRecord, ticker: str) -> AdjustedEarningsRecord:
    meta = SAMPLE_SECURITY_META[ticker]
    trace = _complete_trace(
        record.source_trace,
        ticker=ticker,
        fiscal_year=record.fiscal_year,
        fiscal_period=record.fiscal_period,
        currency=record.currency or meta["currency"],
        formula=record.formula or "adjusted_eps from selected normalization strategy",
        quality_status=str(record.quality_status),
        unit="per_share",
        fact_name="adjusted_eps",
        method=str(record.method),
    )
    waterfall = [
        step.model_copy(
            update={
                "source_trace": SourceTrace(
                    **_complete_trace(
                        step.source_trace,
                        ticker=ticker,
                        fiscal_year=record.fiscal_year,
                        fiscal_period=record.fiscal_period,
                        currency=record.currency or meta["currency"],
                        formula=step.category,
                        quality_status=str(record.quality_status),
                        unit="reported" if step.category == "gaap_ni" else "per_share",
                        fact_name=step.category,
                        method=str(record.method),
                    )
                )
            }
        )
        for step in record.waterfall
    ]
    adjustments = [
        adjustment.model_copy(
            update={
                "source_trace": SourceTrace(
                    **_complete_trace(
                        adjustment.source_trace,
                        ticker=ticker,
                        fiscal_year=record.fiscal_year,
                        fiscal_period=record.fiscal_period,
                        currency=record.currency or meta["currency"],
                        formula=f"{adjustment.canonical_category} after-tax adjustment",
                        quality_status=str(record.quality_status),
                        unit="reported",
                        fact_name=adjustment.canonical_category,
                        method=str(adjustment.source),
                    )
                )
            }
        )
        for adjustment in record.adjustments
    ]
    return record.model_copy(
        update={
            "source_trace": SourceTrace(**trace),
            "waterfall": waterfall,
            "adjustments": adjustments,
        }
    )


def _complete_trace(
    source_trace: SourceTrace,
    ticker: str,
    fiscal_year: int,
    fiscal_period: str,
    currency: str,
    formula: str,
    quality_status: str,
    unit: str,
    fact_name: str,
    method: str,
) -> dict:
    fallback = source_trace_for(ticker, fiscal_year, fact_name)
    trace = source_trace.model_dump(mode="json")
    trace.update(
        {
            "source": _trace_or_default(trace, "source", fallback.get("source")),
            "source_type": _trace_or_default(
                trace,
                "source_type",
                fallback.get("source_type"),
            ),
            "source_document_id": _trace_or_default(
                trace,
                "source_document_id",
                f"{ticker.lower()}-{fiscal_year}-{fact_name}",
            ),
            "filing_id": _trace_or_default(
                trace,
                "filing_id",
                trace.get("accession_number") or f"{ticker.lower()}-{fiscal_year}-fixture",
            ),
            "period": _trace_or_default(trace, "period", f"{fiscal_period}{fiscal_year}"),
            "available_at": _trace_or_default(
                trace,
                "available_at",
                _fixture_available_at(fiscal_year),
            ),
            "unit": _trace_or_default(trace, "unit", unit),
            "currency": _trace_or_default(trace, "currency", currency),
            "method": _trace_or_default(trace, "method", method),
            "formula": _trace_or_default(trace, "formula", formula),
            "quality_status": _trace_or_default(trace, "quality_status", quality_status),
        }
    )
    return trace


def _trace_or_default(trace: dict, key: str, default: object) -> object:
    value = trace.get(key)
    if value is None:
        return default
    if isinstance(value, str) and value.strip().lower() in {"", "unknown", "n/a", "na", "none"}:
        return default
    return value


def _fallback_method(market: str) -> NormalizationMethod:
    if market == "KR":
        return NormalizationMethod.S3_MARKET_STANDARD_KR
    if market == "JP":
        return NormalizationMethod.S3_MARKET_STANDARD_JP
    return NormalizationMethod.S4_GAAP_FALLBACK


def price_dividend_for(ticker: str, year: int) -> tuple[Decimal, Decimal]:
    price, dividend = PRICE_DIVIDEND.get(ticker.upper(), {}).get(year, ("100", "0"))
    return Decimal(price), Decimal(dividend)


def price_points_for(ticker: str, start_year: int, end_year: int) -> list[dict]:
    ticker = ticker.upper()
    meta = SAMPLE_SECURITY_META[ticker]
    rows: list[dict] = []
    for year, (price, _dividend) in sorted(PRICE_DIVIDEND.get(ticker, {}).items()):
        if year < start_year or year > end_year:
            continue
        trace = source_trace_for(ticker, year, "price")
        trace.update(
            {
                "period": f"{year}-12-31",
                "formula": "fixture year-end close price exposed as a chart price point",
                "frequency": "annual",
            }
        )
        rows.append(
            {
                "date": f"{year}-12-31",
                "fiscal_year": year,
                "close_price": price,
                "currency": meta["currency"],
                "frequency": "annual",
                "source_trace": trace,
            }
        )
    return rows


def selected_valuation_metric(
    ticker: str,
    year: int,
    metric: str,
    normalized: AdjustedEarningsRecord | None,
) -> tuple[Decimal | None, dict, str]:
    ticker = ticker.upper()
    if metric == "adjusted_operating":
        if normalized and normalized.adjusted_eps is not None:
            return (
                Decimal(str(normalized.adjusted_eps)),
                normalized.source_trace.model_dump(mode="json"),
                "Adjusted Operating EPS",
            )
        metric = "gaap_diluted_eps"

    if metric in {"gaap_diluted_eps", "diluted_eps"}:
        gaap = GAAP_FACTS[ticker].get(year)
        if not gaap:
            return (
                None,
                source_trace_for(ticker, year, "gaap_eps_diluted"),
                "Diluted EPS" if metric == "diluted_eps" else "GAAP Diluted EPS",
            )
        trace = source_trace_for(ticker, year, "gaap_eps_diluted")
        trace["formula"] = (
            "gaap_eps_diluted from source fixture; replace with filing EPS fact before production use"
        )
        label = "Diluted EPS" if metric == "diluted_eps" else "GAAP Diluted EPS"
        return Decimal(str(gaap["gaap_eps_diluted"])), trace, label

    source_backed_only_labels = {
        "smart_metric": "Smart Metric",
        "basic_eps": "Basic EPS",
        "operating_cash_flow_share": "Operating Cash Flow (OCF/FFO)",
        "ebitda_share": "EBITDA/share",
        "ebit_share": "EBIT/share",
    }
    if metric in source_backed_only_labels:
        trace = source_trace_for(ticker, year, metric)
        trace["quality_status"] = "source_backed_required"
        trace["formula"] = (
            f"{metric} requires a source-backed metric_values row from ingestion; "
            "fixture fallback intentionally refuses to synthesize this value"
        )
        trace["quality_flags"] = ["source_backed_metric_missing"]
        return None, trace, source_backed_only_labels[metric]

    if metric in {"revenue_share", "sales_share", "fcf_share", "ffo_affo"}:
        if metric == "ffo_affo" and SAMPLE_SECURITY_META[ticker]["sector_policy"] != "reit":
            return None, source_trace_for(ticker, year, metric), "FFO/AFFO"
        facts = FINANCIAL_SERIES.get(ticker, {}).get(year)
        gaap = GAAP_FACTS.get(ticker, {}).get(year)
        if not facts or not gaap:
            return None, source_trace_for(ticker, year, metric), metric
        source_field = "revenue" if metric in {"revenue_share", "sales_share"} else "fcf"
        scale = _financial_statement_scale(ticker)
        diluted_shares = Decimal(str(gaap["diluted_shares"]))
        if diluted_shares <= 0:
            return None, source_trace_for(ticker, year, metric), metric
        value = (Decimal(str(facts[source_field])) * scale / diluted_shares).quantize(
            Decimal("0.01")
        )
        trace = source_trace_for(ticker, year, metric)
        trace["unit"] = f"{SAMPLE_SECURITY_META[ticker]['currency']}_per_share"
        trace["formula"] = f"{source_field}_reported * statement_scale / diluted_shares"
        trace["statement_scale"] = str(scale)
        trace["diluted_shares"] = str(diluted_shares)
        if metric == "ffo_affo":
            trace["quality_status"] = "fixture_non_production_reit_proxy"
            trace["formula"] = (
                "reit fixture proxy: fcf_reported * statement_scale / diluted_shares; "
                "replace with source FFO/AFFO reconciliation"
            )
        label = {
            "revenue_share": "Revenue/share",
            "sales_share": "Sales/share",
            "fcf_share": "Free Cash Flow to Equity (FCFE/AFFO)",
            "ffo_affo": "FFO/AFFO",
        }[metric]
        return value, trace, label

    return None, source_trace_for(ticker, year, metric), metric


def _financial_statement_scale(ticker: str) -> Decimal:
    market = SAMPLE_SECURITY_META[ticker.upper()]["market"]
    return Decimal("1000000000") if market == "KR" else Decimal("1000000")


def source_trace_for(ticker: str, year: int, fact_name: str) -> dict:
    meta = SAMPLE_SECURITY_META[ticker.upper()]
    source_type = {
        "US": "sec_fixture",
        "KR": "opendart_fixture",
        "JP": "jquants_fixture",
    }.get(meta["market"], "fixture")
    return {
        "source": source_type,
        "source_type": source_type,
        "source_document_id": f"{ticker.lower()}-{year}-{fact_name}",
        "filing_id": f"{ticker.lower()}-{year}-fixture",
        "period": f"FY{year}",
        "available_at": _fixture_available_at(year),
        "unit": (
            "per_share"
            if "eps" in fact_name.lower()
            or "share" in fact_name.lower()
            or fact_name == "ffo_affo"
            else "reported"
        ),
        "currency": meta["currency"],
        "method": "fixture_non_production",
        "formula": "fixture value for non-production software test; replace with connector output before production use",
        "quality_status": "fixture_non_production",
    }


def _fixture_available_at(year: int) -> str:
    # Fixture traces are non-production, but still model point-in-time availability.
    available_year = min(max(year + 1, 1900), 2026)
    return f"{available_year}-01-01T00:00:00+00:00"


def snapshot_for(ticker: str) -> dict:
    ticker = ticker.upper()
    meta = SAMPLE_SECURITY_META[ticker]
    result = sample_normalization_result(ticker, NormalizationPolicy())
    latest = result.series[-1]
    first = next((row for row in result.series if row.adjusted_eps and row.adjusted_eps > 0), latest)
    price, dividend = price_dividend_for(ticker, latest.fiscal_year)
    latest_eps = Decimal(str(latest.adjusted_eps or latest.gaap_eps_diluted or 0))
    per = (price / latest_eps).quantize(Decimal("0.01")) if latest_eps > 0 else None
    dividend_yield = ((dividend / price) * Decimal("100")).quantize(Decimal("0.01")) if price > 0 else Decimal("0")
    eps_cagr = calculate_cagr(
        Decimal(str(first.adjusted_eps or first.gaap_eps_diluted or 0)),
        latest_eps,
        max(1, latest.fiscal_year - first.fiscal_year),
    ).quantize(Decimal("0.01"))
    latest_financial = FINANCIAL_SERIES[ticker][latest.fiscal_year]
    return {
        "id": ticker,
        "ticker": ticker,
        "name": meta["name"],
        "market": meta["market"],
        "country": meta["country"],
        "currency": meta["currency"],
        "sector_policy": meta["sector_policy"],
        "current_price": str(price),
        "per": str(per) if per is not None else None,
        "dividend_yield": str(dividend_yield),
        "eps": str(latest_eps),
        "eps_cagr": str(eps_cagr),
        "roe": latest_financial["roe"],
        "roic": latest_financial["roic"],
        "debt_ratio": latest_financial["debt_to_equity"],
        "eps_method": latest.method,
        "confidence": str(latest.confidence),
        "source_note": meta["source_label"],
        "source_trace": latest.source_trace.model_dump(mode="json"),
    }


def financials_for(ticker: str) -> list[dict]:
    ticker = ticker.upper()
    result_by_year = {
        row.fiscal_year: row
        for row in sample_normalization_result(ticker, NormalizationPolicy()).series
    }
    rows: list[dict] = []
    for year, facts in sorted(FINANCIAL_SERIES[ticker].items()):
        normalized = result_by_year.get(year)
        gaap = GAAP_FACTS[ticker][year]
        eps = str(normalized.adjusted_eps) if normalized else gaap["gaap_eps_diluted"]
        gaap_eps = str(normalized.gaap_eps_diluted) if normalized else gaap["gaap_eps_diluted"]
        method = normalized.method if normalized else "S4_GAAP_FALLBACK"
        confidence = str(normalized.confidence) if normalized else "0.35"
        source_trace = (
            normalized.source_trace.model_dump(mode="json")
            if normalized
            else source_trace_for(ticker, year, "gaap_eps_diluted")
        )
        rows.append(
            {
                "fiscal_year": year,
                "revenue": facts["revenue"],
                "eps": eps,
                "gaap_eps_diluted": gaap_eps,
                "adjusted_eps": eps,
                "fcf": facts["fcf"],
                "gross_margin": facts["gross_margin"],
                "operating_margin": facts["op_margin"],
                "net_margin": facts["net_margin"],
                "roe": facts["roe"],
                "roic": facts["roic"],
                "debt_to_equity": facts["debt_to_equity"],
                "method": method,
                "confidence": confidence,
                "source_trace": source_trace
                | {"financial_fact_trace": source_trace_for(ticker, year, "financials")},
            }
        )
    return rows


def forecast_evidence_for(ticker: str) -> dict:
    ticker = ticker.upper()
    meta = SAMPLE_SECURITY_META[ticker]
    result = sample_normalization_result(ticker, NormalizationPolicy())
    latest = result.series[-1]
    latest_eps = Decimal(str(latest.adjusted_eps or latest.gaap_eps_diluted or 0))
    forecast_year = latest.fiscal_year + 1
    presets = FORECAST_PRESETS.get(ticker, {})
    analyst_count = int(presets.get("analyst_count") or 0)
    source_trace = source_trace_for(ticker, forecast_year, "forecast_snapshot")
    quality_status = (
        "fixture_non_production_consensus_proxy"
        if analyst_count > 0
        else "no_verified_consensus_snapshot"
    )
    source_trace.update(
        {
            "source_type": "forecast_snapshot_fixture",
            "source_document_id": f"{ticker.lower()}-{forecast_year}-forecast-snapshot-proxy",
            "filing_id": f"{ticker.lower()}-{forecast_year}-forecast-snapshot-proxy",
            "period": f"FY{forecast_year}E",
            "unit": "per_share",
            "currency": meta["currency"],
            "formula": (
                "non-production proxy: latest adjusted EPS multiplied by fixture "
                "low/median/high growth rates"
            ),
            "quality_status": quality_status,
        }
    )
    cases = _forecast_case_rows(latest_eps, presets, source_trace)
    revisions = _forecast_revision_rows(cases, analyst_count, source_trace)
    sentiment = _forecast_sentiment(revisions, analyst_count, quality_status)
    scorecard = _forecast_scorecard_rows(ticker, result, source_trace)
    return {
        "ticker": ticker,
        "forecast_year": forecast_year,
        "metric_name": "Adjusted Operating EPS",
        "cases": cases,
        "revisions": revisions,
        "sentiment": sentiment,
        "scorecard": scorecard,
        "source_trace": source_trace,
        "meta": {
            "data_mode": "fixture_non_production",
            "quality_status": quality_status,
            "source_note": (
                "Proxy values are for UI and contract testing only. Production requires "
                "point-in-time consensus snapshots or explicit user-entered estimates."
            ),
        },
    }


def _forecast_case_rows(latest_eps: Decimal, presets: dict, source_trace: dict) -> list[dict]:
    rows: list[dict] = []
    for case_name, key in (
        ("low", "consensus_low_growth_rate"),
        ("median", "consensus_growth_rate"),
        ("high", "consensus_high_growth_rate"),
    ):
        growth = Decimal(str(presets.get(key) or presets.get("consensus_growth_rate") or "5"))
        estimate = (latest_eps * (Decimal("1") + growth / Decimal("100"))).quantize(
            Decimal("0.01")
        )
        rows.append(
            {
                "case": case_name,
                "growth_rate_pct": str(growth),
                "estimate_eps": str(estimate),
                "source_trace": source_trace,
            }
        )
    return rows


def _forecast_revision_rows(cases: list[dict], analyst_count: int, source_trace: dict) -> list[dict]:
    median = Decimal(next(row["estimate_eps"] for row in cases if row["case"] == "median"))
    rows: list[dict] = []
    previous_estimate: Decimal | None = None
    for label, age_months, factor, analyst_delta in (
        ("12M prior", 12, Decimal("0.96"), -4),
        ("3M prior", 3, Decimal("0.98"), -2),
        ("1M prior", 1, Decimal("1.01"), -1),
        ("current", 0, Decimal("1.00"), 0),
    ):
        estimate = (median * factor).quantize(Decimal("0.01"))
        revision_delta = None
        if previous_estimate and previous_estimate > 0:
            revision_delta = (((estimate / previous_estimate) - 1) * 100).quantize(
                Decimal("0.01")
            )
        rows.append(
            {
                "as_of_label": label,
                "age_months": age_months,
                "estimate_eps": str(estimate),
                "analyst_count": max(0, analyst_count + analyst_delta),
                "revision_delta_pct": str(revision_delta) if revision_delta is not None else None,
                "quality_status": source_trace["quality_status"],
                "source_trace": source_trace,
            }
        )
        previous_estimate = estimate
    return rows


def _forecast_sentiment(revisions: list[dict], analyst_count: int, quality_status: str) -> dict:
    current = Decimal(revisions[-1]["estimate_eps"])
    prior = Decimal(revisions[1]["estimate_eps"])
    net_revision = (((current / prior) - 1) * 100).quantize(Decimal("0.01")) if prior else Decimal("0")
    label = "positive" if net_revision > 0 else "negative" if net_revision < 0 else "neutral"
    up_revisions = max(0, analyst_count // 3) if net_revision > 0 else max(0, analyst_count // 6)
    down_revisions = max(0, analyst_count // 6) if net_revision > 0 else max(0, analyst_count // 3)
    unchanged = max(0, analyst_count - up_revisions - down_revisions)
    return {
        "label": label,
        "net_revision_score_pct": str(net_revision),
        "up_revisions": up_revisions,
        "down_revisions": down_revisions,
        "unchanged": unchanged,
        "quality_status": quality_status,
    }


def _forecast_scorecard_rows(ticker: str, result: NormalizationResult, source_trace: dict) -> dict:
    rows: list[dict] = []
    for record in result.series[-3:]:
        actual = Decimal(str(record.adjusted_eps or record.gaap_eps_diluted or 0))
        one_year_estimate = (actual * Decimal("1.04")).quantize(Decimal("0.01"))
        two_year_estimate = (actual * Decimal("0.92")).quantize(Decimal("0.01"))
        one_year_error = _forecast_error_pct(one_year_estimate, actual)
        two_year_error = _forecast_error_pct(two_year_estimate, actual)
        rows.append(
            {
                "fiscal_year": record.fiscal_year,
                "actual_eps": str(actual),
                "estimate_1y_prior": str(one_year_estimate),
                "estimate_2y_prior": str(two_year_estimate),
                "error_1y_pct": str(one_year_error),
                "error_2y_pct": str(two_year_error),
                "result_1y": "hit" if abs(one_year_error) <= Decimal("10") else "miss",
                "result_2y": "hit" if abs(two_year_error) <= Decimal("20") else "miss",
                "quality_status": "fixture_non_production_scorecard_proxy",
                "source_trace": source_trace,
            }
        )
    return {
        "ticker": ticker,
        "status": "fixture_non_production_scorecard_proxy",
        "rows": rows,
        "summary": {
            "hit_rate_1y_pct": _hit_rate(rows, "result_1y"),
            "hit_rate_2y_pct": _hit_rate(rows, "result_2y"),
            "required_source": "point_in_time_consensus_snapshots",
        },
    }


def _forecast_error_pct(estimate: Decimal, actual: Decimal) -> Decimal:
    if actual == 0:
        return Decimal("0")
    return (((estimate / actual) - 1) * 100).quantize(Decimal("0.01"))


def _hit_rate(rows: list[dict], field: str) -> str:
    if not rows:
        return "0.00"
    hits = sum(1 for row in rows if row[field] == "hit")
    return ((Decimal(hits) / Decimal(len(rows))) * Decimal("100")).quantize(
        Decimal("0.01")
    ).to_eng_string()


def screener_rows() -> list[dict]:
    rows = []
    for ticker in SAMPLE_SECURITY_META:
        snapshot = snapshot_for(ticker)
        normal_pe = _normal_pe_for(ticker)
        roe = Decimal(snapshot["roe"])
        roic = Decimal(snapshot["roic"])
        per = Decimal(snapshot["per"]) if snapshot["per"] else Decimal("0")
        rows.append(
            {
                "ticker": ticker,
                "name": snapshot["name"],
                "market": snapshot["market"],
                "currency": snapshot["currency"],
                "market_cap": snapshot.get("market_cap"),
                "market_cap_usd": snapshot.get("market_cap_usd"),
                "listed_shares": snapshot.get("listed_shares"),
                "per": snapshot["per"],
                "normal_pe": str(normal_pe),
                "roe": snapshot["roe"],
                "roic": snapshot["roic"],
                "eps_cagr": snapshot["eps_cagr"],
                "debt_to_equity": snapshot["debt_ratio"],
                "filters": {
                    "metric_to_value": per < Decimal("25"),
                    "metric_to_metric": roe > roic,
                    "company_relative": per < normal_pe,
                },
                "source_trace": snapshot["source_trace"],
            }
        )
    return rows


def _normal_pe_for(ticker: str) -> Decimal:
    result = sample_normalization_result(ticker, NormalizationPolicy())
    multiples = []
    for row in result.series:
        eps = Decimal(str(row.adjusted_eps or row.gaap_eps_diluted or 0))
        if eps <= 0:
            continue
        price, _ = price_dividend_for(ticker, row.fiscal_year)
        multiples.append(price / eps)
    return (sum(multiples) / Decimal(len(multiples))).quantize(Decimal("0.01")) if multiples else Decimal("0")
