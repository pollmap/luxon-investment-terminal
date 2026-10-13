"""fixture_non_production: preserve OCF evidence across unchanged comparatives."""

from copy import deepcopy

import pytest

from services.market_dashboard.eps_calculation import days, quarters_at
from services.market_dashboard.fundamental_metrics import VERSION, calculate_fundamental_metrics
from services.market_dashboard.official_views import PROJECTION_VERSION, read_view
from services.market_dashboard.store import Store


def period(start, end, publication, amount, shares=100, share_basis="fixture-reviewed-unit"):
    identity = start + ":" + end + ":" + publication
    return {
        "company_id": "fixture_non_production",
        "basis": "CFS",
        "metric": "OCF",
        "period_start": start,
        "period_end": end,
        "published_at": publication,
        "filing_id": publication,
        "days": days(start, end),
        "profit": str(amount),
        "weighted_shares": str(shares),
        "per_share_basis": share_basis,
        "currency": "KRW",
        "unit": "원/주",
        "share_class": "ordinary",
        "period_verified": True,
        "share_class_verified": True,
        "attribution": "company_total_per_weighted_ordinary_share",
        "formula": "operating cash / weighted ordinary shares",
        "formula_version": "fixture-v1",
        "input_fact_ids": [identity],
        "source_trace": {"source": "fixture_non_production"},
    }


def inputs(change=None):
    rows = [
        period("2025-01-01", "2025-03-31", "2025-05-15", 100),
        period("2025-01-01", "2025-06-30", "2025-08-14", 220),
        period("2025-01-01", "2025-09-30", "2025-11-14", 330),
        period("2025-01-01", "2025-12-31", "2026-03-17", 460),
        period("2026-01-01", "2026-03-31", "2026-05-15", 130),
        period("2026-01-01", "2026-06-30", "2026-08-14", 280),
        period("2025-01-01", "2025-03-31", "2026-05-15", 100),
        period("2025-01-01", "2025-06-30", "2026-08-14", 220),
    ]
    if change:
        rows[-1].update(change)
    return rows


def q3(rows):
    return next(
        (r for r in rows if (r["period_start"], r["period_end"])
         == ("2025-07-01", "2025-09-30")),
        None,
    )


def financial_facts(change=None):
    facts = []
    for row in inputs(change):
        for account, amount, unit in (
            ("WeightedAverageShares", row["weighted_shares"],
             "{http://www.xbrl.org/2003/instance}shares"),
            ("CashFlowsFromUsedInOperatingActivities", row["profit"], "{iso}KRW"),
        ):
            facts.append({
                **{key: row[key] for key in (
                    "company_id", "basis", "period_start", "period_end", "period_verified",
                    "published_at", "filing_id", "per_share_basis", "currency", "source_trace",
                )},
                "account_id": "{https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full}" + account,
                "fact_id": account + ":" + row["input_fact_ids"][0],
                "value": amount,
                "unit": unit,
                "dimensions": {},
            })
    return facts


def test_identical_verified_comparative_keeps_original_quarter_and_evidence():
    source = inputs()
    original = deepcopy(source)
    result = q3(quarters_at(source, "2026-08-14"))
    assert result["profit"] == "110" and result["weighted_shares"] == "100"
    assert result["published_at"] == "2025-11-14"
    assert result["input_fact_ids"] == [
        "2025-01-01:2025-06-30:2025-08-14", "2025-01-01:2025-09-30:2025-11-14",
    ]
    assert result["source_trace"]["input_fact_ids"] == result["input_fact_ids"]
    assert result["source_trace"]["available_at"] == "2025-11-14"
    assert source == original


@pytest.mark.parametrize("change", [
    {"profit": "221"}, {"weighted_shares": "101"}, {"per_share_basis": None},
    {"per_share_basis": "different"}, {"currency": "USD"}, {"unit": "억원"},
    {"period_verified": False}, {"share_class_verified": False},
    {"share_class": "preferred"}, {"attribution": "different"},
    {"dimensions": {"segment": "other"}}, {"formula": "different"},
    {"formula_version": "different"}, {"company_id": "other"}, {"basis": "OFS"},
])
def test_changed_or_unverified_comparative_remains_blocked(change):
    assert q3(quarters_at(inputs(change), "2026-08-14")) is None


def test_actual_revision_then_reversion_is_not_an_unchanged_republication():
    source = inputs()
    revised = {**source[1], "profit": "221", "published_at": "2026-04-01"}
    assert q3(quarters_at(source + [revised], "2026-08-14")) is None


def test_future_comparative_never_changes_past_calculation():
    source = inputs({"profit": "221"})
    before = quarters_at(source, "2026-03-17")
    assert before == quarters_at(source[:4], "2026-03-17")
    assert q3(before)["profit"] == "110"


@pytest.mark.parametrize("metric", ["EPS", "EBITDA", "FCF"])
def test_republication_exception_does_not_extend_to_other_metrics(metric):
    source = [{**row, "metric": metric} for row in inputs()]
    assert q3(quarters_at(source, "2026-08-14")) is None


def test_no_original_evidence_before_cumulative_remains_blocked():
    source = [r for r in inputs() if r["published_at"] != "2025-08-14"]
    assert q3(quarters_at(source, "2026-08-14")) is None


def test_fundamental_pipeline_keeps_latest_ttm_across_year_boundary():
    facts = financial_facts()
    original = deepcopy(facts)
    result = calculate_fundamental_metrics(facts)
    ttm = [r for r in result["rows"] if r["metric"] == "OCF" and r["frequency"] == "ttm"]
    latest = max(ttm, key=lambda r: (r["period_end"], r["published_at"]))
    assert (latest["period_start"], latest["period_end"]) == ("2025-07-01", "2026-06-30")
    assert latest["exact_value"] == "5.2"
    assert latest["numerator"] == "520" and latest["weighted_shares"] == "100"
    assert latest["published_at"] == "2026-08-14"
    assert latest["formula_version"] == VERSION == "fundamental-metrics-v4"
    assert facts == original


def test_old_analysis_projection_rebuilds_with_same_source_revision(tmp_path):
    store = Store("sqlite://", tmp_path)
    source = {"xbrl_facts": financial_facts()}
    store.put("official:005930", source)
    revision = store.revisions(("official:005930",))["official:005930"]
    store.put("official-view:005930:analysis", {
        "projection_version": 14, "source_revision": revision,
        "share_basis_revision": None, "payload": {"fixture_old_projection": True},
    })
    result = read_view(store, "005930", "analysis")
    rows = [r for r in result["fundamental_metrics"]["rows"]
            if r["metric"] == "OCF" and r["frequency"] == "ttm"]
    assert max(r["period_end"] for r in rows) == "2026-06-30"
    assert "fixture_old_projection" not in result
    assert result["fundamental_metrics"]["formula_version"] == "fundamental-metrics-v4"
    cached = store.get("official-view:005930:analysis")
    assert cached["projection_version"] == PROJECTION_VERSION
    assert PROJECTION_VERSION > 14
    assert cached["source_revision"] == revision
    assert store.get("official:005930") == source
    assert store.revisions(("official:005930",))["official:005930"] == revision


def test_unverified_half_year_comparative_keeps_prior_valid_ttm_only():
    result = calculate_fundamental_metrics(financial_facts({"per_share_basis": None}))
    rows = [r for r in result["rows"] if r["metric"] == "OCF" and r["frequency"] == "ttm"]
    assert max(r["period_end"] for r in rows) == "2026-03-31"
    assert not any(r["period_end"] == "2026-06-30" for r in rows)
