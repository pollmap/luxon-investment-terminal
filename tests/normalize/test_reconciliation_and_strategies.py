from decimal import Decimal
from pathlib import Path

from backend.normalize.parsers.reconciliation import find_reconciliation_tables
from backend.normalize.schemas import NormalizationPolicy, SourceDocument
from backend.normalize.strategies.s1_sec_reconciliation import S1SecReconciliationStrategy
from backend.normalize.strategies.s2_xbrl_special_items import S2XbrlSpecialItemsStrategy
from backend.normalize.strategies.s4_gaap_fallback import S4GaapFallbackStrategy


def test_reconciliation_parser_extracts_adjusted_eps():
    html = Path("tests/fixtures/sec/aapl_ex99_earnings.html").read_text(encoding="utf-8")
    tables = find_reconciliation_tables(html)
    assert tables
    rows = {row.row_type: row.value for row in tables[0].rows}
    assert rows["adjusted_eps"] == Decimal("6.08")
    assert rows["gaap_eps_diluted"] == Decimal("6.08")


def test_s1_strategy_uses_company_adjusted_eps():
    html = Path("tests/fixtures/sec/aapl_ex99_earnings.html").read_text(encoding="utf-8")
    doc = SourceDocument(
        id="aapl-test",
        ticker="AAPL",
        accession_number="fixture",
        form_type="8-K",
        filing_url="https://www.sec.gov/fixture-index.html",
        source_url="https://www.sec.gov/fixture-ex99.html",
        content=html,
        metadata={"fiscal_year": 2024},
    )
    result = S1SecReconciliationStrategy([doc]).normalize("AAPL", NormalizationPolicy())
    assert result.series
    record = result.series[0]
    assert record.method == "S1_SEC_RECONCILIATION"
    assert record.company_adjusted_eps == Decimal("6.08")
    assert "gaap_fallback" not in record.flags


def test_s1_strategy_allocates_aggregate_tax_effect_once_and_warns_on_bridge_mismatch():
    html = """
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
    """
    doc = SourceDocument(
        id="crm-test",
        ticker="CRM",
        accession_number="fixture",
        form_type="8-K",
        filing_url="https://www.sec.gov/fixture-index.html",
        source_url="https://www.sec.gov/fixture-ex99.html",
        content=html,
        metadata={"fiscal_year": 2024},
    )
    result = S1SecReconciliationStrategy([doc]).normalize("CRM", NormalizationPolicy())
    record = result.series[0]
    tax_effects = [item.tax_effect for item in record.adjustments]
    assert sum(tax_effects, Decimal("0")) == Decimal("987.000000")
    assert all(item != Decimal("987") for item in tax_effects)
    assert "eps_reconciliation_outside_tolerance" in record.flags
    assert record.quality_status == "warning"


def test_s2_strategy_computes_adjusted_eps_from_special_items():
    facts = {
        2024: {
            "NetIncomeLoss": "1000",
            "EarningsPerShareDiluted": "10",
            "WeightedAverageNumberOfDilutedSharesOutstanding": "100",
            "RestructuringCharges": "100",
            "IncomeTaxExpenseBenefit": "210",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": "1000",
        }
    }
    result = S2XbrlSpecialItemsStrategy(facts).normalize("TEST", NormalizationPolicy())
    assert result.series[0].adjusted_eps == Decimal("10.79")
    assert "inferred_tax_effect" in result.series[0].flags


def test_s4_fallback_is_transparent():
    result = S4GaapFallbackStrategy({2024: {"gaap_eps_diluted": "4.20"}}).normalize(
        "TEST",
        NormalizationPolicy(),
    )
    record = result.series[0]
    assert record.adjusted_eps == Decimal("4.20")
    assert record.quality_status == "fallback"
    assert record.flags == ["gaap_fallback"]
