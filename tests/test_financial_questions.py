"""fixture_non_production: pure monetary view, not actual issuer coverage."""

import json
from copy import deepcopy

import pytest

from services.market_dashboard.financial_questions import build_financial_questions

NS = "{https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full}"
ACCOUNTS = {
    "revenue": "Revenue",
    "operating_income": "OperatingIncomeLoss",
    "net_income": "ProfitLoss",
    "operating_cash": "CashFlowsFromUsedInOperatingActivities",
}


def row(metric, value, **changes):
    result = {
        "metric": metric,
        "account_id": NS + ACCOUNTS[metric],
        "company_id": "corp",
        "basis": "CFS",
        "currency": "KRW",
        "unit": "원",
        "frequency": "ttm",
        "period_start": "2025-01-01",
        "period_end": "2025-12-31",
        "value": value,
        "exact_value": str(value),
        "dimensions": {},
        "source_trace": {
            "formula": "sum(verified standalone quarters)",
            "inputs": [
                {
                    "source": "fixture_non_production",
                    "source_document_id": "ab" * 32,
                    "available_at": published,
                    "unit": "KRW",
                    "currency": "KRW",
                    "period": period,
                    "input_fact_ids": [f"{metric}:{index}"],
                }
                for index, (period, published) in enumerate(
                    [
                        ("2025-01-01/2025-03-31", "2025-05-15"),
                        ("2025-04-01/2025-06-30", "2025-08-15"),
                        ("2025-07-01/2025-09-30", "2025-11-15"),
                        ("2025-10-01/2025-12-31", "2026-03-15"),
                    ]
                )
            ],
        },
    }
    return result | changes


def view(*rows):
    return {"ttm": {"CFS": {"status": "ready", "rows": list(rows)}}}


def result_rows(result, name="rows"):
    return {item["metric"]: item for item in result[name]}


def test_company_amounts_and_ratios_need_no_eps_or_share_basis():
    source = view(
        row("revenue", 1000),
        row("operating_income", 100),
        row("net_income", 80),
        row("operating_cash", 120),
    )
    original = deepcopy(source)
    result = build_financial_questions(source)
    money, ratios = result_rows(result), result_rows(result, "comparisons")
    assert result["status"] == "ready"
    assert result["scope"] == "company_financial_totals"
    assert result["per_share_status"] == "not_calculated"
    assert money["net_income"]["exact_value"] == "80"
    assert ratios["cash_conversion"]["value"] == 150
    assert ratios["net_margin"]["value"] == 8
    assert ratios["operating_margin"]["value"] == 10
    assert ratios["cash_conversion"]["available_at"] == "2026-03-15"
    assert len(ratios["cash_conversion"]["input_fact_ids"]) == 8
    assert money["capex"]["value"] is None and money["free_cash"]["value"] is None
    assert source == original


@pytest.mark.parametrize("profit", [0, -1])
def test_zero_and_losses_remain_amounts_but_not_cash_conversion_ratios(profit):
    result = build_financial_questions(view(row("net_income", profit), row("operating_cash", 10)))
    assert result_rows(result)["net_income"]["value"] == profit
    ratio = result_rows(result, "comparisons")["cash_conversion"]
    assert ratio["value"] is None and ratio["reason"] == "non_positive_profit"


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"basis": "OFS"}, "basis_mismatch"),
        ({"currency": "USD"}, "currency_or_unit_mismatch"),
        ({"unit": "원/주"}, "currency_or_unit_mismatch"),
        ({"company_id": "other"}, "company_identity_required"),
        ({"dimensions": {"BusinessSegmentsAxis": "SemiconductorMember"}}, "dimension_unverified"),
        (
            {"dimensions": {"ConsolidatedAndSeparateFinancialStatementsAxis": "SeparateMember"}},
            "dimension_unverified",
        ),
        ({"period_start": "2025-01-02"}, "period_mismatch"),
        ({"period_end": "2025-06-30"}, "period_unverified"),
    ],
)
def test_mixed_inputs_do_not_unlock_ratios(change, reason):
    source = view(row("net_income", 8), row("operating_cash", 10, **change))
    result = build_financial_questions(source, company_id="corp")
    bad = result_rows(result)["operating_cash"]
    assert bad["value"] is None and bad["reason"] == reason
    assert result_rows(result, "comparisons")["cash_conversion"]["value"] is None


def test_parent_attributed_profit_does_not_become_group_cash_conversion():
    source = view(
        row("net_income", 8, account_id=NS + "ProfitLossAttributableToOwnersOfParent"),
        row("operating_cash", 10),
    )
    result = build_financial_questions(source)
    # Retain the source amount, but do not silently compare different populations.
    assert result_rows(result)["net_income"]["value"] == 8
    assert (
        result_rows(result, "comparisons")["cash_conversion"]["reason"] == "entity_scope_unverified"
    )


def test_missing_company_or_filing_evidence_and_collection_clock_are_blocked():
    for changes, reason in [
        ({"company_id": None}, "company_identity_required"),
        ({"source_trace": {}}, "source_evidence_required"),
    ]:
        result = build_financial_questions(view(row("net_income", 1, **changes)))
        assert result_rows(result)["net_income"]["reason"] == reason
    source = row("net_income", 10)
    source["source_trace"]["inputs"][0]["quality_flags"] = ["publication_time_unverified"]
    assert (
        result_rows(build_financial_questions(view(source)))["net_income"]["reason"]
        == "publication_unverified"
    )


def test_restatement_selects_latest_but_equal_time_conflict_is_unavailable():
    first, corrected = row("net_income", 10), row("net_income", 12)
    corrected["source_trace"]["inputs"][-1]["available_at"] = "2026-04-01"
    corrected["source_trace"]["inputs"][-1]["input_fact_ids"] = ["correction-fact"]
    result = build_financial_questions(view(first, corrected))
    item = result_rows(result)["net_income"]
    assert item["value"] == 12 and item["available_at"] == "2026-04-01"
    assert "correction-fact" in item["input_fact_ids"]
    conflict = row("net_income", 13)
    conflict["source_trace"]["inputs"][-1]["available_at"] = "2026-04-01"
    result = build_financial_questions(view(first, corrected, conflict))
    assert result_rows(result)["net_income"]["reason"] == "conflicting_inputs"


def test_source_observation_locators_are_explicit_and_not_database_ids():
    source = row("net_income", 10)
    for evidence in source["source_trace"]["inputs"]:
        evidence.pop("input_fact_ids")
    item = result_rows(build_financial_questions(view(source)))["net_income"]
    assert all(identity.startswith("source-observation:") for identity in item["input_fact_ids"])
    assert all(
        reference["input_kind"] == "source_observation"
        for reference in item["source_trace"]["inputs"]
    )


def capex_facts(*, intangible_missing=False, negative=False):
    facts = []
    for index, (start, end, published) in enumerate(
        [
            ("2025-01-01", "2025-03-31", "2025-05-15"),
            ("2025-04-01", "2025-06-30", "2025-08-15"),
            ("2025-07-01", "2025-09-30", "2025-11-15"),
            ("2025-10-01", "2025-12-31", "2026-03-15"),
        ]
    ):
        for account, value in [
            ("PurchaseOfPropertyPlantAndEquipment", -2 if negative else 2),
            ("PurchaseOfIntangibleAssets", 1),
        ]:
            if intangible_missing and account == "PurchaseOfIntangibleAssets":
                continue
            facts.append(
                {
                    "account_id": NS + account,
                    "company_id": "corp",
                    "basis": "CFS",
                    "currency": "KRW",
                    "unit": "{iso}KRW",
                    "kind": "duration",
                    "period_verified": True,
                    "period_start": start,
                    "period_end": end,
                    "value": value,
                    "published_at": published,
                    "dimensions": {},
                    "fact_id": f"{account}:{index}",
                    "source_trace": {
                        "source": "fixture_non_production",
                        "source_document_id": "ef" * 32,
                        "available_at": published,
                        "unit": "KRW",
                    },
                }
            )
    return facts


def test_capex_requires_both_actual_components_and_same_period_and_sign():
    source = view(row("net_income", 80), row("operating_cash", 120))
    result = build_financial_questions(source, dated_facts=capex_facts())
    rows = result_rows(result)
    assert rows["capex"]["value"] == 12 and rows["free_cash"]["value"] == 108
    assert len(rows["free_cash"]["input_fact_ids"]) == 12
    assert (
        result_rows(
            build_financial_questions(source, dated_facts=capex_facts(intangible_missing=True))
        )["capex"]["value"]
        is None
    )
    assert result_rows(
        build_financial_questions(source, dated_facts=capex_facts(intangible_missing=True))
    )["capex"]["missing_inputs"] == ["intangibles"]
    assert (
        result_rows(build_financial_questions(source, dated_facts=capex_facts(negative=True)))[
            "capex"
        ]["reason"]
        == "capex_sign_unverified"
    )
    facts = capex_facts()
    facts[-1]["published_at"] = "2025-12-01"
    assert (
        result_rows(build_financial_questions(source, dated_facts=facts))["capex"]["value"] is None
    )


def test_missing_view_is_explicit_and_invalid_basis_is_rejected():
    result = build_financial_questions({})
    assert result["status"] == "not_available"
    assert all(item["value"] is None for item in result["rows"])
    with pytest.raises(ValueError):
        build_financial_questions({}, "mixed")


def test_exact_decimal_amount_survives_float_display_and_negative_ocf_is_real():
    source = view(row("net_income", "100000000000000001"), row("operating_cash", -25))
    result = build_financial_questions(source)
    assert result_rows(result)["net_income"]["exact_value"] == "100000000000000001"
    assert result_rows(result)["operating_cash"]["value"] == -25
    assert result_rows(result, "comparisons")["cash_conversion"]["value"] < 0


def test_an_intermediate_quarter_disclosed_before_its_end_is_not_hidden_by_latest_filing():
    source = row("operating_cash", 100)
    source["source_trace"]["inputs"][2]["available_at"] = "2025-09-01"
    result = build_financial_questions(view(source))
    assert result_rows(result)["operating_cash"]["reason"] == "publication_unverified"
    source["source_trace"]["inputs"][2]["available_at"] = "2025-11-15"
    source["source_trace"]["inputs"][2]["input_period_start"] = "2025-07-01"
    source["source_trace"]["inputs"][2]["input_period_end"] = "invalid"
    assert (
        result_rows(build_financial_questions(view(source)))["operating_cash"]["reason"]
        == "period_unverified"
    )


def test_explicit_source_publication_does_not_relabel_collection_clock():
    source = row("net_income", 100)
    original = deepcopy(source)
    for evidence in source["source_trace"]["inputs"]:
        evidence["input_published_at"] = evidence["available_at"]
        evidence["input_company_id"] = "corp"
        evidence["available_at"] = None
        evidence["quality_flags"] = ["publication_time_unverified"]
    result = build_financial_questions(view(source))
    assert result_rows(result)["net_income"]["value"] == 100
    assert all(t["available_at"] is None for t in source["source_trace"]["inputs"])
    assert original["source_trace"]["inputs"][0]["available_at"] == "2025-05-15"


def test_source_url_keys_are_not_public_but_raw_trace_is_preserved():
    source = row("net_income", 100)
    unsafe = "https://opendart.fss.or.kr/api/fnlttMultiAcnt.json?crtfc_key=fixture-secret"
    source["source_trace"]["inputs"][0]["source_url"] = unsafe
    source["source_trace"]["inputs"][1].update(source_url=unsafe, filing_id="20260315000001")
    source["source_trace"]["inputs"][2]["source_url"] = "https://attacker.example/document"
    source["source_trace"]["inputs"][3]["source_url"] = (
        "https://opendart.fss.or.kr/api/list.json?corp_code=00126380"
    )
    result = build_financial_questions(view(source))
    encoded = json.dumps(result)
    assert "fixture-secret" not in encoded and "crtfc_key" not in encoded
    assert "attacker.example" not in encoded
    assert "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260315000001" in encoded
    assert "https://opendart.fss.or.kr/api/list.json?corp_code=00126380" in encoded
    assert source["source_trace"]["inputs"][0]["source_url"] == unsafe
