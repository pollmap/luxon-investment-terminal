"""fixture_non_production: the metric menu must share chart accounting gates."""

import pytest

from services.market_dashboard.analysis_inputs import metric_availability
from services.market_dashboard.valuation_series import valuation_series


def sample(**patch):
    return {
        "metric": "OCF",
        "value": 10,
        "basis": "CFS",
        "frequency": "annual",
        "period_start": "2024-01-01",
        "period_end": "2024-12-31",
        "period_verified": True,
        "security_code": "005930",
        "share_class_verified": True,
        "currency": "KRW",
        "per_share_basis": "fixture",
        "published_at": "2025-03-01",
        "source_trace": {"source": "fixture_non_production"},
        **patch,
    }


def prices(**patch):
    return {
        "rows": [{"date": "2025-03-03", "close": 100}],
        "venue": "KRX",
        "per_share_basis": "fixture",
        **patch,
    }


def availability(f, p=None):
    result = metric_availability([f], p if p is not None else prices(), "005930")
    return next(m for m in result["metrics"] if m["code"] == "OCF")


@pytest.mark.parametrize(
    "patch",
    [
        {"period_start": "2024-10-01"},
        {"frequency": "quarterly"},
        {"period_verified": False},
        {"security_code": "005935"},
        {"currency": "USD"},
        {"share_class_verified": False},
        {"per_share_basis": "different"},
        {"published_at": None},
        {"value": None},
    ],
)
def test_invalid_chart_inputs_are_never_advertised_as_comparable(patch):
    f = sample(**patch)
    m = availability(f)
    assert m["available"] and not m["comparison_available"]
    assert m["comparison_frequencies"] == [] and m["comparison_periods"] == 0
    result = valuation_series([f], prices(), code="005930", metric="OCF", multiple=8)
    assert not result["availability"]["comparison"]


def test_actual_annual_and_ttm_ranges_are_separate_from_unverified_observations():
    rows = [
        sample(),
        sample(period_start="2023-01-01", period_end="2023-12-31", period_verified=False),
        sample(
            frequency="ttm",
            period_start="2024-04-01",
            period_end="2025-03-31",
            published_at="2025-05-01",
        ),
    ]
    # Both publications have an observed following session. A price ending in
    # March cannot certify the TTM first disclosed in May.
    observed = prices(rows=[{"date": "2025-03-03", "close": 100},
                            {"date": "2025-05-02", "close": 101}])
    m = next(m for m in metric_availability(rows, observed, "005930")["metrics"]
             if m["code"] == "OCF")
    assert m["start"] == "2023-12-31" and m["comparison_start"] == "2024-12-31"
    assert m["comparison_periods"] == 2 and m["comparison_frequencies"] == ["annual", "ttm"]


def test_missing_prices_and_unknown_market_cannot_enable_comparison():
    assert not availability(sample(), prices(rows=[]))["comparison_available"]
    assert availability(sample(), prices(rows=[]))["status"] == "prices_not_collected"
    assert not availability(sample(), prices(venue="PROVIDER"))["comparison_available"]
    assert availability(sample(), prices(rows=[], row_count=100))["comparison_available"]


def test_raw_price_revision_invalidates_metric_menu_without_changing_filing(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from services.market_dashboard import analysis_inputs
    from services.market_dashboard import app as market
    from services.market_dashboard.response_cache import RevisionResponseCache
    from services.market_dashboard.store import Store

    store = Store("sqlite://", tmp_path)
    store.put(
        "universe",
        {"rows": [{"code": "005930", "name": "fixture_non_production", "source_trace": {}}]},
    )
    evidence = {
        "verified": True,
        "no_split_interval": True,
        "basis_id": "fixture",
        "security_code": "005930",
        "start": "2020-01-01",
        "end": "2026-12-31",
        "source_trace": {
            "sha256": "fixture_non_production",
            "source_url": "https://example.test/filing",
        },
    }
    store.put("share-basis:005930", evidence)
    store.put("bars:005930:통합:raw", {"adjustment": "raw", "venue": "통합", "rows": []})
    monkeypatch.setattr(market, "get_store", lambda: store)
    monkeypatch.setattr(market, "_detail_responses", RevisionResponseCache())
    monkeypatch.setattr(analysis_inputs, "security_facts", lambda s, c: ([sample()], {}))

    def ocf(body):
        return next(m for m in body["metrics"] if m["code"] == "OCF")

    with TestClient(market.app) as client:
        path = market.BASE + "/securities/005930/analysis/metrics"
        first = client.get(path)
        assert not ocf(first.json())["comparison_available"]
        store.put(
            "bars:005930:통합:raw",
            {"adjustment": "raw", "venue": "통합", "rows": [{"date": "2025-03-03", "close": 100}]},
        )
        second = client.get(path, headers={"if-none-match": first.headers["etag"]})
        assert second.status_code == 200 and ocf(second.json())["comparison_available"]
        store.put("bars:005930:통합:raw", {"adjustment": "raw", "venue": "통합", "rows": []})
        third = client.get(path, headers={"if-none-match": second.headers["etag"]})
        assert third.status_code == 200 and not ocf(third.json())["comparison_available"]
    store.engine.dispose()


@pytest.mark.parametrize(
    "status,reason",
    [
        ("partial_period_inputs", "연간·최근 12개월 자료가 부족해요"),
        ("split_basis_required", "주당 기준 확인 중"),
    ],
)
def test_partial_calculation_inputs_explain_disabled_metric_without_enabling_comparison(
    status, reason
):
    readiness = [
        {
            "metric": "OCF",
            "input_periods": 2,
            "status": status,
            "input_start": "2024-01-01",
            "input_end": "2024-06-30",
        }
    ]
    result = metric_availability([], prices(), "005930", input_readiness=readiness)
    ocf = next(m for m in result["metrics"] if m["code"] == "OCF")
    assert ocf["status"] == status and ocf["reason"] == reason
    assert ocf["available"] is False and ocf["comparison_available"] is False
    assert ocf["start"] is None and ocf["comparison_periods"] == 0
    assert ocf["input_readiness"] == readiness
    missing = next(m for m in result["metrics"] if m["code"] == "EBITDA")
    assert missing["status"] == "inputs_not_collected"


def test_ready_metric_retains_existing_status_despite_other_partial_periods():
    result = metric_availability(
        [sample()],
        prices(),
        "005930",
        input_readiness=[{"metric": "OCF", "input_periods": 2, "status": "partial_period_inputs"}],
    )
    ocf = next(m for m in result["metrics"] if m["code"] == "OCF")
    assert ocf["status"] == "ready" and ocf["comparison_available"]


def test_input_readiness_scopes_existing_projection_to_issuer_without_new_reads(monkeypatch):
    from services.market_dashboard import analysis_inputs

    readiness = [
        {"company_id": "00126380", "metric": "OCF", "status": "partial_period_inputs"},
        {"company_id": "other", "metric": "EBITDA", "status": "partial_period_inputs"},
    ]
    model = {"fundamental_metrics": {"readiness": readiness}}
    monkeypatch.setattr(analysis_inputs, "read_view", lambda *a: model)

    class Store:
        def __init__(self):
            self.calls = []

        def get(self, key, default=None):
            self.calls.append(key)
            return {"codes": {"005930": "00126380"}} if key == "dart-corporations" else default

    store = Store()
    rows, official = analysis_inputs.security_facts(store, "005930")
    assert rows == [] and official["metric_input_readiness"] == readiness[:1]
    assert store.calls == ["dart-corporations", "detail:005930"]
    assert "metric_input_readiness" not in model


def test_public_metric_response_keeps_partial_input_disabled_and_explains_why(
    tmp_path, monkeypatch
):
    from fastapi.testclient import TestClient

    from services.market_dashboard import analysis_inputs
    from services.market_dashboard import app as market
    from services.market_dashboard.response_cache import RevisionResponseCache
    from services.market_dashboard.store import Store

    store = Store("sqlite://", tmp_path)
    store.put(
        "universe",
        {"rows": [{"code": "005930", "name": "fixture_non_production", "source_trace": {}}]},
    )
    monkeypatch.setattr(market, "get_store", lambda: store)
    monkeypatch.setattr(market, "_detail_responses", RevisionResponseCache())
    readiness = [{"metric": "OCF", "input_periods": 2, "status": "partial_period_inputs"}]
    monkeypatch.setattr(
        analysis_inputs, "security_facts", lambda *args: ([], {"metric_input_readiness": readiness})
    )
    with TestClient(market.app) as client:
        response = client.get(market.BASE + "/securities/005930/analysis/metrics")
        assert response.status_code == 200
        ocf = next(m for m in response.json()["metrics"] if m["code"] == "OCF")
        assert ocf["status"] == "partial_period_inputs"
        assert ocf["available"] is False and ocf["comparison_available"] is False
        # Existing UI renders this reason on its disabled option; no enum change required.
        assert ocf["reason"] == "연간·최근 12개월 자료가 부족해요"
    store.engine.dispose()
