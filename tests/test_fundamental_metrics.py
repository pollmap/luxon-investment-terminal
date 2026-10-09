"""fixture_non_production: distinct metric, period, attribution and split gates."""

from copy import deepcopy

from services.market_dashboard.analysis_inputs import metric_availability
from services.market_dashboard.analysis_metrics import definitions
from services.market_dashboard.fundamental_metrics import calculate_fundamental_metrics
from services.market_dashboard.valuation_series import valuation_series


def facts(start="2024-01-01", end="2024-12-31", published="2025-03-14", **values):
    numbers = {
        "WeightedAverageShares": 100,
        "CashFlowsFromUsedInOperatingActivities": 1000,
        "Revenue": 5000,
        "ProfitLoss": 600,
        "ProfitLossFromContinuingOperations": 600,
        "IncomeTaxExpenseContinuingOperations": 150,
        "InterestExpense": 50,
        "DepreciationExpense": 120,
        "AmortisationExpense": 30,
        "PurchaseOfPropertyPlantAndEquipment": 200,
        "PurchaseOfIntangibleAssets": 50,
        **values,
    }
    return [
        {
            "account_id": "{https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full}" + name,
            "company_id": "corp",
            "basis": "CFS",
            "period_start": start,
            "period_end": end,
            "period_verified": True,
            "published_at": published,
            "filing_id": published,
            "fact_id": name + start + published,
            "value": str(value),
            "currency": "KRW",
            "unit": "{http://www.xbrl.org/2003/instance}shares"
            if name == "WeightedAverageShares"
            else "{iso}KRW",
            "source_trace": {"source": "fixture_non_production"},
            "dimensions": {},
            "per_share_basis": "fixture-aligned",
        }
        for name, value in numbers.items()
    ]


def test_different_metrics_never_alias_and_trace_every_input():
    result = calculate_fundamental_metrics(facts())
    rows = {r["metric"]: r for r in result["rows"] if r["frequency"] == "annual"}
    assert {m: r["value"] for m, r in rows.items()} == {
        "OCF": 10,
        "FCF": 7.5,
        "EBIT": 8,
        "EBITDA": 9.5,
        "SALES": 50,
    }
    assert all(r["input_fact_ids"] and r["formula_version"] for r in rows.values())
    assert "FCFE" not in rows and "EPS" not in rows and "AFFO" not in rows


def test_missing_capex_is_not_zero_and_finance_costs_are_not_interest():
    source = [
        f
        for f in facts()
        if not f["account_id"].endswith(("}PurchaseOfIntangibleAssets", "}InterestExpense"))
    ]
    metrics = {r["metric"] for r in calculate_fundamental_metrics(source)["rows"]}
    assert metrics == {"OCF", "SALES"}


def test_tax_scope_segment_and_conflicting_shares_are_rejected():
    source = [
        f for f in facts() if not f["account_id"].endswith("}ProfitLossFromContinuingOperations")
    ]
    assert not any(
        r["metric"] in ("EBIT", "EBITDA") for r in calculate_fundamental_metrics(source)["rows"]
    )
    source = facts()
    for f in source:
        if f["account_id"].endswith("}CashFlowsFromUsedInOperatingActivities"):
            f["dimensions"] = {"{ifrs}SegmentsAxis": "{ifrs}MobileMember"}
    assert "OCF" not in {r["metric"] for r in calculate_fundamental_metrics(source)["rows"]}
    source = facts()
    source.append({**source[0], "value": "101", "fact_id": "conflict"})
    assert not calculate_fundamental_metrics(source)["rows"]


def test_ttm_sums_cash_not_per_share_values_and_requires_share_basis():
    source = []
    for i, (start, end, pub) in enumerate(
        [
            ("2024-01-01", "2024-03-31", "2024-05-14"),
            ("2024-04-01", "2024-06-30", "2024-08-14"),
            ("2024-07-01", "2024-09-30", "2024-11-14"),
            ("2024-10-01", "2024-12-31", "2025-03-14"),
        ]
    ):
        source += facts(start, end, pub, WeightedAverageShares=100 if i < 2 else 200)
    rows = [
        r
        for r in calculate_fundamental_metrics(source)["rows"]
        if r["metric"] == "OCF" and r["frequency"] == "ttm"
    ]
    assert len(rows) == 1
    expected = 4000 / ((100 * 182 + 200 * 184) / 366)
    assert abs(rows[0]["value"] - expected) < 1e-10
    for f in source:
        f["per_share_basis"] = None
    assert not calculate_fundamental_metrics(source)["rows"]


def test_new_metric_does_not_inherit_fifteen_and_server_controls_comparison():
    fact = next(r for r in calculate_fundamental_metrics(facts())["rows"] if r["metric"] == "OCF")
    fact["security_code"] = "005930"
    prices = {
        "venue": "KRX",
        "per_share_basis": "fixture-aligned",
        "rows": [{"date": "2025-03-17", "close": 100}],
    }
    result = valuation_series([fact], prices, code="005930", metric="OCF")
    assert result["multiple"] is None and result["multiple_source"] == "input_required"
    assert result["series"][0]["comparison_eligible"] is True
    assert result["series"][0]["reference_price"] is None
    assert (
        valuation_series([fact], prices, code="005930", metric="OCF", multiple=8)["series"][0][
            "reference_price"
        ]
        == 80
    )
    fact["per_share_basis"] = None
    result = valuation_series([fact], prices, code="005930", metric="OCF", multiple=8)
    assert result["series"][0]["comparison_eligible"] is False
    assert result["periods"][0]["value"] == 10


def test_availability_does_not_mutate_global_definitions():
    before = deepcopy(definitions())
    result = metric_availability(facts(), {})
    assert result["metrics"][0]["available"] is False
    assert definitions() == before


def test_fcfe_requires_filing_specific_classification_and_does_not_double_subtract_leases():
    source = facts(
        CashFlowsFromUsedInIncreaseDecreaseInCurrentBorrowings=50,
        ProceedsFromNoncurrentBorrowings=100,
        RepaymentsOfNoncurrentBorrowings=30,
        InterestPaidClassifiedAsOperatingActivities=25,
    )
    for f in source:
        f["company_id"] = "00126380"
        if "RepaymentsOfLongTerm" in f["account_id"]:
            f["account_id"] = (
                "{http://dart.fss.or.kr/taxonomy/2024-12-31/entity00126380}RepaymentsOfNoncurrentBorrowings"
            )
    review = {
        "verified": True,
        "company_id": "00126380",
        "basis": "CFS",
        "filing_id": "2025-03-14",
        "periods": [["2024-01-01", "2024-12-31"]],
        "all_interest_in_ocf": True,
        "lease_principal_in_debt_repaid": True,
        "complete_debt_cashflows": True,
        "source_trace": {
            "source_url": "https://example.test/fixture",
            "sha256": "fixture_non_production",
        },
    }
    assert "FCFE" not in {r["metric"] for r in calculate_fundamental_metrics(source)["rows"]}
    result = calculate_fundamental_metrics(source, [review])
    row = next(r for r in result["rows"] if r["metric"] == "FCFE")
    assert row["value"] == 8.7  # (1000-200-50+50+100-30)/100. Interest already in OCF.
    assert (
        row["cashflow_policy"] == review
        and "cashflow-policy:fixture_non_production" in row["input_fact_ids"]
    )
    for change in (
        {"filing_id": "wrong"},
        {"basis": "OFS"},
        {"lease_principal_in_debt_repaid": False},
    ):
        assert not any(
            r["metric"] == "FCFE"
            for r in calculate_fundamental_metrics(source, [{**review, **change}])["rows"]
        )


def test_cashflow_ttm_uses_cumulative_correction_after_its_publication():
    source = facts("2024-01-01", "2024-03-31", "2024-05-14")
    source += facts("2024-04-01", "2024-06-30", "2024-08-14")
    source += facts("2024-07-01", "2024-09-30", "2024-11-14")
    source += facts("2024-10-01", "2024-12-31", "2025-03-14")
    source += facts(
        "2024-01-01", "2024-06-30", "2025-04-01", CashFlowsFromUsedInOperatingActivities=2200
    )
    rows = [
        r
        for r in calculate_fundamental_metrics(source)["rows"]
        if r["metric"] == "OCF" and r["frequency"] == "ttm"
    ]
    assert [(r["published_at"], r["value"]) for r in rows] == [
        ("2025-03-14", 40),
        ("2025-04-01", 42),
    ]
    assert rows[-1]["input_fact_ids"] != rows[0]["input_fact_ids"]
    assert any("2025-04-01" in identity for identity in rows[-1]["input_fact_ids"])


def test_comparison_does_not_request_multiple_for_missing_or_negative_metric():
    prices = {
        "venue": "KRX",
        "per_share_basis": "fixture-aligned",
        "rows": [{"date": "2025-03-17", "close": 100}],
    }
    missing = valuation_series([], prices, code="005935", metric="OCF")
    assert missing["availability"]["reasons"] == ["no_available_fact"]
    fact = next(
        r
        for r in calculate_fundamental_metrics(facts(CashFlowsFromUsedInOperatingActivities=-1000))[
            "rows"
        ]
        if r["metric"] == "OCF"
    )
    fact["security_code"] = "005930"
    negative = valuation_series([fact], prices, code="005930", metric="OCF")
    assert negative["availability"]["reasons"] == ["non_positive_metric"]
    assert negative["periods"][0]["value"] == -10
    assert negative["series"][0]["reference_price"] is None


def test_partial_period_readiness_preserves_evidence_without_creating_annual_values():
    source = facts("2024-01-01", "2024-03-31", "2024-05-14")
    before = deepcopy(source)
    result = calculate_fundamental_metrics(source)
    assert result["rows"] == [] and source == before
    ocf = next(r for r in result["readiness"] if r["metric"] == "OCF")
    assert ocf == {
        "company_id": "corp",
        "basis": "CFS",
        "metric": "OCF",
        "status": "partial_period_inputs",
        "input_start": "2024-01-01",
        "input_end": "2024-03-31",
        "input_periods": 1,
        "input_frequencies": ["quarterly"],
    }
    assert not any(r["metric"] == "FCFE" for r in result["readiness"])


def test_full_quarters_with_missing_share_basis_are_not_reported_as_missing_inputs():
    source = []
    for start, end, pub in [
        ("2024-01-01", "2024-03-31", "2024-05-14"),
        ("2024-04-01", "2024-06-30", "2024-08-14"),
        ("2024-07-01", "2024-09-30", "2024-11-14"),
        ("2024-10-01", "2024-12-31", "2025-03-14"),
    ]:
        source.extend(facts(start, end, pub))
    for fact in source:
        fact["per_share_basis"] = None
    result = calculate_fundamental_metrics(source)
    assert result["rows"] == []
    assert all(r["status"] == "split_basis_required" for r in result["readiness"])
    assert result["rejected"]["OCF_ttm_share_basis_required"] == 1
