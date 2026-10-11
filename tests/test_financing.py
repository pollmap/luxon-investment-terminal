import pytest
from fastapi.testclient import TestClient
from services.market_dashboard import app as market_app
from services.market_dashboard.financing import public_financing
from services.market_dashboard.store import Store
from services.market_dashboard.providers import normalize_stock

def test_endpoint_validates_symbol_period_and_strips_private_evidence(tmp_path, monkeypatch):
    store = Store(url="sqlite://", root=tmp_path)
    stock = normalize_stock(
        {"itemCode": "005930", "stockName": "fixture_non_production", "closePriceRaw": "100"},
        "KOSPI",
        {"source":"fixture", "url":"https://example.invalid"},
    )
    store.put("universe", {"rows": [stock]})
    store.put(
        "financing:005930",
        {
            "code": "005930",
            "credit": {
                "status": "ready",
                "rows": [{"date":"2026-10-02", "balance":1200, "source_trace":{"url":"https://example.invalid"}}],
            },
        },
    )
    monkeypatch.setattr(market_app, "get_store", lambda: store)
    market_app._rows_cache.clear()
    with TestClient(market_app.app) as client:
        path = market_app.BASE + "/securities/005930/financing"
        response = client.get(path)
        assert (
            response.status_code == 200 and response.json()["credit"]["rows"][0]["balance"] == 1200
        )
        assert "source_trace" not in response.text and "example.invalid" not in response.text
        assert client.get(path + "?period=bad").status_code == 422
        assert client.get(path.replace("005930", "999999")).status_code == 404
        assert "005930" in store.active()


def test_month_uses_calendar_not_fixed_30_days():
    data = public_financing(
        {
            "credit": {
                "rows": [
                    {"date": "2024-02-28", "balance": 1},
                    {"date": "2024-02-29", "balance": 2},
                    {"date": "2024-03-31", "balance": 3},
                ]
            }
        },
        "month",
    )
    assert [row["date"] for row in data["credit"]["rows"]] == ["2024-02-29", "2024-03-31"]


def test_worker_credentials_are_allowlisted_and_preserve_environment(tmp_path, monkeypatch):
    from services.market_dashboard.runtime import load_worker_credentials

    path = tmp_path / "runtime.env"
    path.write_text(
        'DART_API_KEY="fixture_file"\nTOSS_CLIENT_ID=fixture_toss\nNEXT_PUBLIC_SECRET=bad\nMARKET_AUTH_SECRET=retired\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("DART_API_KEY", "fixture_existing")
    monkeypatch.setenv("TOSS_CLIENT_ID", " ")
    monkeypatch.delenv("NEXT_PUBLIC_SECRET", raising=False)
    monkeypatch.delenv("MARKET_AUTH_SECRET", raising=False)
    assert load_worker_credentials(path) == ["TOSS_CLIENT_ID"]
    import os

    assert os.environ["DART_API_KEY"] == "fixture_existing"
    assert os.environ["TOSS_CLIENT_ID"] == "fixture_toss"
    assert "NEXT_PUBLIC_SECRET" not in os.environ and "MARKET_AUTH_SECRET" not in os.environ
    monkeypatch.delenv("TOSS_CLIENT_ID")
