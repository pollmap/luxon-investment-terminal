from __future__ import annotations

from pathlib import Path

import pytest

from services.api.chart_cache import (
    _valuation_chart_cache_path,
    render_cached_valuation_chart,
)


def _payload(ticker: str = "AAPL") -> dict:
    return {
        "data": [],
        "meta": {
            "ticker": ticker,
            "metric": "adjusted_operating",
            "data_mode": "fixture_non_production",
        },
    }


@pytest.mark.parametrize(
    "ticker",
    [
        "../AAPL",
        "..\\AAPL",
        "AAPL/../../outside",
        "AAPL\\..\\..\\outside",
        "AAPL*",
        "AAPL?",
        "[AAPL]",
    ],
)
def test_chart_cache_rejects_traversal_and_wildcard_tickers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ticker: str,
) -> None:
    cache_root = tmp_path / "charts"
    monkeypatch.setenv("CHART_CACHE_DIR", str(cache_root))
    renderer_called = False

    def renderer(_rows: list[dict], _visibility: dict | None) -> str:
        nonlocal renderer_called
        renderer_called = True
        return "<svg/>"

    with pytest.raises(ValueError, match="ticker"):
        render_cached_valuation_chart(ticker, _payload(ticker), "svg", renderer)

    assert renderer_called is False
    assert cache_root.exists() is False


@pytest.mark.parametrize("ticker", ["AAPL", "brk.b", "005930.KS", "285A.T"])
def test_chart_cache_keeps_valid_market_tickers_inside_resolved_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ticker: str,
) -> None:
    cache_root = tmp_path / "nested" / ".." / "charts"
    monkeypatch.setenv("CHART_CACHE_DIR", str(cache_root))

    result = render_cached_valuation_chart(
        ticker,
        _payload(ticker.strip().upper()),
        "svg",
        lambda _rows, _visibility: "<svg/>",
    )

    resolved_root = cache_root.resolve()
    resolved_path = Path(result.local_path).resolve()
    assert resolved_root in resolved_path.parents
    assert resolved_path.parent.name == ticker.strip().upper()
    assert result.blob_key.startswith(
        f"rendered/charts/valuation-map/{ticker.strip().upper()}/"
    )


def test_chart_cache_path_rejects_resolved_root_escape(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cache_root = tmp_path / "charts"
    monkeypatch.setenv("CHART_CACHE_DIR", str(cache_root))

    with pytest.raises(ValueError, match="outside the configured cache directory"):
        _valuation_chart_cache_path("../..", "cache-key", "svg")
