from decimal import Decimal

from packages.valuation.screener import ScreenerConfig, apply_screener_filters


def test_screener_config_recomputes_all_filter_classes():
    rows = [
        {
            "ticker": "PASS",
            "per": "12",
            "normal_pe": "18",
            "roe": "20",
            "roic": "12",
            "eps_cagr": "15",
            "debt_to_equity": "0.5",
        },
        {
            "ticker": "WATCH",
            "per": "30",
            "normal_pe": "18",
            "roe": "10",
            "roic": "12",
            "eps_cagr": "3",
            "debt_to_equity": "2.5",
        },
    ]
    config = ScreenerConfig(
        max_per=Decimal("20"),
        min_roe=Decimal("15"),
        min_eps_cagr=Decimal("10"),
        max_debt_to_equity=Decimal("1"),
        relative_discount_pct=Decimal("10"),
    )

    screened = apply_screener_filters(rows, config)

    assert screened[0]["filters"] == {
        "metric_to_value": True,
        "metric_to_metric": True,
        "company_relative": True,
        "passes_all": True,
    }
    assert screened[1]["filters"]["passes_all"] is False
    assert any("EPS CAGR" in reason for reason in screened[0]["filter_reasons"])


def test_screener_can_disable_metric_to_metric_filter():
    rows = [
        {
            "ticker": "NO_ROIC",
            "per": "12",
            "normal_pe": "20",
            "roe": None,
            "roic": None,
            "eps_cagr": None,
            "debt_to_equity": None,
        }
    ]
    config = ScreenerConfig(require_roe_gt_roic=False)

    screened = apply_screener_filters(rows, config)

    assert screened[0]["filters"]["metric_to_metric"] is True
    assert "ROE > ROIC disabled" in screened[0]["filter_reasons"]


def test_screener_market_cap_threshold_is_metric_to_value_filter():
    rows = [
        {
            "ticker": "MEGA",
            "per": "12",
            "normal_pe": "18",
            "roe": "20",
            "roic": "12",
            "eps_cagr": "15",
            "debt_to_equity": "0.5",
            "market_cap": "500000000000",
        },
        {
            "ticker": "SMALL",
            "per": "12",
            "normal_pe": "18",
            "roe": "20",
            "roic": "12",
            "eps_cagr": "15",
            "debt_to_equity": "0.5",
            "market_cap": "100000000",
        },
    ]
    config = ScreenerConfig(min_market_cap=Decimal("1000000000"))

    screened = apply_screener_filters(rows, config)

    assert screened[0]["filters"]["metric_to_value"] is True
    assert screened[1]["filters"]["metric_to_value"] is False
    assert any("Market cap" in reason for reason in screened[1]["filter_reasons"])


def test_screener_market_cap_usd_threshold_is_cross_market_filter():
    rows = [
        {
            "ticker": "KR_BIG",
            "per": "12",
            "normal_pe": "18",
            "roe": "20",
            "roic": "12",
            "eps_cagr": "15",
            "debt_to_equity": "0.5",
            "market_cap_usd": "5000000000",
        },
        {
            "ticker": "KR_SMALL",
            "per": "12",
            "normal_pe": "18",
            "roe": "20",
            "roic": "12",
            "eps_cagr": "15",
            "debt_to_equity": "0.5",
            "market_cap_usd": "50000000",
        },
    ]
    config = ScreenerConfig(min_market_cap_usd=Decimal("1000000000"))

    screened = apply_screener_filters(rows, config)

    assert screened[0]["filters"]["metric_to_value"] is True
    assert screened[1]["filters"]["metric_to_value"] is False
    assert any("Market cap USD" in reason for reason in screened[1]["filter_reasons"])
