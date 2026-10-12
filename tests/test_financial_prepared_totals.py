"""fixture_non_production: reuse exact calculated amounts, never invert EPS."""

from copy import deepcopy

import pytest

from services.market_dashboard.financial_questions import build_financial_questions
from services.market_dashboard.fundamental_metrics import calculate_fundamental_metrics

NS = "{https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full}"


def prepared():
    facts = []
    for index, (start, end, publication) in enumerate(
        [
            ("2025-01-01", "2025-03-31", "2025-05-15"),
            ("2025-04-01", "2025-06-30", "2025-08-15"),
            ("2025-07-01", "2025-09-30", "2025-11-15"),
            ("2025-10-01", "2025-12-31", "2026-03-15"),
        ]
    ):
        for account, value in [
            ("CashFlowsFromUsedInOperatingActivities", 1000),
            ("Revenue", 5000),
            ("WeightedAverageShares", 100),
            ("PurchaseOfPropertyPlantAndEquipment", 200),
            ("PurchaseOfIntangibleAssets", 50),
        ]:
            facts.append(
                {
                    "account_id": NS + account,
                    "company_id": "corp",
                    "basis": "CFS",
                    "period_start": start,
                    "period_end": end,
                    "period_verified": True,
                    "published_at": publication,
                    "filing_id": publication,
                    "fact_id": f"{account}:{index}",
                    "value": str(value),
                    "currency": "KRW",
                    "unit": "{http://www.xbrl.org/2003/instance}shares"
                    if account == "WeightedAverageShares"
                    else "{iso}KRW",
                    "source_trace": {
                        "source": "fixture_non_production",
                        "sha256": "ab" * 32,
                        "available_at": publication,
                    },
                    "dimensions": {},
                    "per_share_basis": "reviewed-unit",
                }
            )
    return calculate_fundamental_metrics(facts)


def items(result):
    return {row["metric"]: row for row in result["rows"]}


def test_prepared_numerator_connects_even_when_legacy_summary_is_missing():
    model = prepared()
    original = deepcopy(model)
    result = build_financial_questions({"fundamental_metrics": model}, company_id="corp")
    rows = items(result)
    assert result["status"] == "partial"
    assert rows["operating_cash"]["exact_value"] == "4000"
    assert rows["revenue"]["exact_value"] == "20000"
    assert rows["free_cash"]["exact_value"] == "3000"
    assert rows["net_income"]["value"] is None and rows["capex"]["value"] is None
    assert rows["operating_cash"]["input_model_version"] == "fundamental-metrics-v4"
    lineage = rows["operating_cash"]["source_trace"]["inputs"][0]
    assert lineage["period_kind"] == "prepared_calculation_window"
    assert lineage["calculation_fact_id"] == rows["operating_cash"]["input_model_fact_id"]
    assert lineage["calculation_version"] == "fundamental-metrics-v4"
    assert rows["free_cash"]["missing_inputs"] == []
    assert all(row["value"] is None for row in result["comparisons"])
    assert model == original


def test_per_share_value_cannot_change_company_amount_or_create_missing_numerator():
    model = prepared()
    for row in model["rows"]:
        row["value"] = 999999
        row["exact_value"] = "999999"
        row["weighted_shares"] = "999999999"
    result = build_financial_questions({"fundamental_metrics": model}, company_id="corp")
    assert items(result)["operating_cash"]["exact_value"] == "4000"
    for row in model["rows"]:
        row.pop("numerator", None)
    result = build_financial_questions({"fundamental_metrics": model}, company_id="corp")
    assert result["status"] == "not_available"
    assert all(row["value"] is None for row in result["rows"])


@pytest.mark.parametrize(
    "change",
    [
        {"company_id": "another"},
        {"basis": "OFS"},
        {"frequency": "annual"},
        {"formula_version": "unknown-model"},
        {"attribution": "ordinary_equity_eps"},
        {"currency": "USD"},
        {"unit": "원"},
        {"period_verified": False},
        {"input_fact_ids": []},
        {"fact_id": None},
        {"published_at": "2025-12-01"},
        {"dimensions": {"BusinessSegmentsAxis": "SemiconductorMember"}},
    ],
)
def test_unverified_prepared_contract_is_not_promoted(change):
    model = prepared()
    model["rows"] = [{**row, **change} for row in model["rows"]]
    result = build_financial_questions({"fundamental_metrics": model}, company_id="corp")
    assert all(row["value"] is None for row in result["rows"])


def test_eps_numerator_does_not_become_company_profit_and_old_fcf_is_not_latest_fcf():
    model = prepared()
    template = next(row for row in model["rows"] if row["metric"] == "OCF")
    model["rows"].append({**template, "metric": "EPS", "numerator": "99999999"})
    for row in model["rows"]:
        if row["metric"] == "FCF":
            row.update(period_start="2024-01-01", period_end="2024-12-31")
    result = build_financial_questions({"fundamental_metrics": model}, company_id="corp")
    assert items(result)["net_income"]["value"] is None
    assert items(result)["free_cash"]["value"] is None
    assert items(result)["free_cash"]["reason"] == "period_mismatch"
    assert result["period_end"] == "2025-12-31"


def test_loss_and_zero_company_cash_remain_amounts_without_price_eligibility():
    model = prepared()
    for row in model["rows"]:
        if row["metric"] == "OCF":
            row["numerator"] = "-25"
        if row["metric"] == "SALES":
            row["numerator"] = "0"
    result = build_financial_questions({"fundamental_metrics": model}, company_id="corp")
    assert items(result)["operating_cash"]["value"] == -25
    assert items(result)["revenue"]["value"] == 0
    assert all(row["value"] is None for row in result["comparisons"])
