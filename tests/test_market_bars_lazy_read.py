"""fixture_non_production: OpenAlgo success avoids a second full history read."""

from copy import deepcopy
from datetime import date, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from services.market_dashboard import app as api
from services.market_dashboard.bars import aggregate_bars
from services.market_dashboard.response_cache import RevisionResponseCache
from services.market_dashboard.store import Store
from services.market_dashboard.time_series import put_series

CODE = "005930"
BASE = api.BASE + f"/securities/{CODE}/bars"
TRACE = {"source": "fixture_non_production", "unit": "주", "period": "2026-04-01"}


@pytest.fixture
def fixture_store(tmp_path, monkeypatch):
    for name in ("MARKET_REDIS_URL", "MARKET_OPENALGO_URL"):
        monkeypatch.delenv(name, raising=False)

    def forbidden(*_args, **_kwargs):
        pytest.fail("No external network or provider call is allowed")

    monkeypatch.setattr(httpx, "get", forbidden)
    monkeypatch.setattr(httpx, "post", forbidden)
    store = Store("sqlite://", tmp_path)
    store.put("universe", {"rows": [{
        "code": CODE, "name": "fixture_non_production", "market": "KOSPI",
    }]})
    monkeypatch.setattr(api, "get_store", lambda: store)
    monkeypatch.setattr(api, "_detail_responses", RevisionResponseCache())
    yield store
    store.engine.dispose()


def fixture_rows():
    rows = []
    for i in range(140):
        day = date(2026, 1, 1) + timedelta(days=i)
        if day.weekday() < 5:
            rows.append({
                "date": day.isoformat(), "time": day.isoformat() + "T15:30:00+09:00",
                "open": 100 + i, "high": 110 + i, "low": 90 + i,
                "close": 105 + i, "volume": 0 if i == 90 else 10 + i,
                "confirmed": True, "complete": True,
            })
    return rows


def save(store, key, rows, normalized, *, venue="통합", adjustment="source"):
    data = {
        "rows": deepcopy(rows), "venue": venue, "adjustment": adjustment,
        "source_trace": TRACE, "status": "ready" if rows else "not_collected",
        "session_date": "2026-05-20", "received_at": "2026-05-20T18:00:00+09:00",
    }
    if normalized:
        put_series(store, key, data)
    else:
        store.put(key, data)
    return store.get(key)


def expected(data, rows, interval, adjustment, transport):
    if adjustment != "source" and data.get("adjustment") != adjustment:
        data = {}
    data = {**data, "rows": rows}
    if interval == "5min":
        return {
            **data,
            "rows": [r for r in rows if "2026-04-01" <= r["date"] <= "2026-04-30"],
            "interval": interval, "venue": "통합", "transport": transport,
            "status": data.get("status", "not_collected"),
        }
    return {
        **aggregate_bars(rows, interval=interval, start="2026-04-01", end="2026-04-30"),
        "code": CODE, "venue": data.get("venue"), "adjustment": data.get("adjustment"),
        "source_trace": data.get("source_trace"), "transport": transport,
    }


def request(interval, adjustment="source", **extra):
    return TestClient(api.app).get(BASE, params={
        "venue": "통합", "interval": interval, "adjustment": adjustment,
        "start": "2026-04-01", "end": "2026-04-30", **extra,
    })


def watch_history_gets(store, monkeypatch, *, forbidden=False):
    original = store.get
    calls = []

    def get(key, *args, **kwargs):
        if key.startswith(("bars:", "krx-bars:", "prices:")):
            calls.append(key)
            if forbidden:
                pytest.fail("public bars hydrated full history despite OpenAlgo success")
        return original(key, *args, **kwargs)

    monkeypatch.setattr(store, "get", get)
    return calls


@pytest.mark.parametrize("normalized", [False, True])
@pytest.mark.parametrize("interval", ["day", "week", "month", "5min"])
def test_success_uses_manifest_and_preserves_response(
    fixture_store, monkeypatch, normalized, interval,
):
    store = fixture_store
    adjustment = "raw" if interval == "5min" else "source"
    key = f"bars:{CODE}:통합:{'5min' if interval == '5min' else 'day'}"
    data = save(store, key, fixture_rows(), normalized, adjustment=adjustment)
    # Match the existing sidecar contract, including W/M's current aggregation.
    remote = (data["rows"] if interval == "5min"
              else aggregate_bars(data["rows"], interval=interval)["rows"])
    calls = []

    def bridge(*args):
        calls.append(args)
        return deepcopy(remote)

    monkeypatch.setattr(api, "history_via_openalgo", bridge)
    reads = watch_history_gets(store, monkeypatch, forbidden=True)
    monkeypatch.setattr(store.time_series, "rows", lambda *args, **kwargs: pytest.fail(
        "OpenAlgo success must not materialize stored series in the public build",
    ))
    response = request(interval, adjustment)
    assert response.status_code == 200
    assert response.json() == expected(data, remote, interval, adjustment, "openalgo")
    assert calls == [(CODE, interval, None, None)]  # No range pushdown in this change.
    assert reads == []


@pytest.mark.parametrize("normalized", [False, True])
@pytest.mark.parametrize("interval", ["day", "5min"])
@pytest.mark.parametrize("outcome", ["disabled", "timeout", "http_error", "invalid", "empty"])
def test_fallback_reads_same_stored_snapshot_once(
    fixture_store, monkeypatch, normalized, interval, outcome,
):
    store = fixture_store
    adjustment = "raw" if interval == "5min" else "source"
    key = f"bars:{CODE}:통합:{'5min' if interval == '5min' else 'day'}"
    data = save(store, key, fixture_rows(), normalized, adjustment=adjustment)
    calls = []

    def bridge(*args):
        calls.append(args)
        if outcome == "timeout":
            raise httpx.ReadTimeout("fixture_non_production")
        if outcome == "http_error":
            raise httpx.HTTPStatusError(
                "fixture_non_production", request=httpx.Request("POST", "http://fixture.invalid"),
                response=httpx.Response(503),
            )
        if outcome == "invalid":
            raise ValueError("fixture_non_production")
        return None if outcome == "disabled" else []

    monkeypatch.setattr(api, "history_via_openalgo", bridge)
    reads = watch_history_gets(store, monkeypatch)
    response = request(interval, adjustment)
    assert response.status_code == 200
    transport = "stored" if outcome == "disabled" else "stored_fallback"
    assert response.json() == expected(data, data["rows"], interval, adjustment, transport)
    assert reads == [key]
    assert calls == [(CODE, interval, None, None)]


@pytest.mark.parametrize("normalized", [False, True])
@pytest.mark.parametrize("interval", ["day", "5min"])
def test_genuine_empty_success_never_loads_history(
    fixture_store, monkeypatch, normalized, interval,
):
    store = fixture_store
    adjustment = "raw" if interval == "5min" else "source"
    key = f"bars:{CODE}:통합:{'5min' if interval == '5min' else 'day'}"
    data = save(store, key, [], normalized, adjustment=adjustment)
    monkeypatch.setattr(api, "history_via_openalgo", lambda *args: [])
    reads = watch_history_gets(store, monkeypatch, forbidden=True)
    response = request(interval, adjustment)
    assert response.status_code == 200
    assert response.json() == expected(data, [], interval, adjustment, "openalgo")
    assert reads == []


@pytest.mark.parametrize("has_rows", [False, True])
def test_legacy_series_manifest_without_count_checks_only_one_row(
    fixture_store, monkeypatch, has_rows,
):
    store = fixture_store
    key = f"bars:{CODE}:통합:day"
    data = save(store, key, fixture_rows() if has_rows else [], True)
    metadata = store.metadata(key)
    metadata.pop("row_count")
    store.put(key, metadata)
    data.pop("row_count")
    original = store.time_series.rows
    checks = []

    def rows(*args, **kwargs):
        checks.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(store.time_series, "rows", rows)
    monkeypatch.setattr(api, "history_via_openalgo", lambda *args: [])
    reads = watch_history_gets(store, monkeypatch, forbidden=not has_rows)
    response = request("day")
    assert response.status_code == 200
    assert checks[0] == {"limit": 1}
    assert reads == ([key] if has_rows else [])
    assert response.json() == expected(
        data, data["rows"], "day", "source", "stored_fallback" if has_rows else "openalgo",
    )


@pytest.mark.parametrize("normalized", [False, True])
@pytest.mark.parametrize("choice", ["primary", "krx", "prices", "empty_krx", "split_mismatch"])
def test_krx_selection_order_is_unchanged(fixture_store, monkeypatch, normalized, choice):
    store = fixture_store
    primary, krx, prices = f"bars:{CODE}:KRX:day", f"krx-bars:{CODE}:KRX:raw", f"prices:{CODE}"
    rows = fixture_rows()
    adjustments = "split" if choice == "split_mismatch" else "source"
    price_data = save(store, prices, rows, normalized, venue="KRX")
    if choice != "prices":
        krx_data = save(store, krx, [] if choice == "empty_krx" else rows, normalized,
                        venue="KRX", adjustment="raw")
    if choice in ("primary", "split_mismatch"):
        primary_data = save(store, primary, rows, normalized, venue="KRX")
    if choice == "primary":
        selected, expected_reads = primary_data, [primary]
    elif choice == "split_mismatch":
        selected, expected_reads = {}, [primary]
    elif choice == "prices":
        selected, expected_reads = price_data, [primary, krx, prices]
    else:
        selected, expected_reads = krx_data, [primary, krx]

    def no_bridge(*args):
        pytest.fail("KRX must not query integrated OpenAlgo history")

    monkeypatch.setattr(api, "history_via_openalgo", no_bridge)
    reads = watch_history_gets(store, monkeypatch)
    response = request("day", adjustments, venue="KRX")
    assert response.status_code == 200
    assert response.json() == expected(
        selected, selected.get("rows", []), "day", adjustments, "stored",
    )
    assert reads == expected_reads


@pytest.mark.parametrize("outcome", ["success", "empty", "disabled", "error"])
def test_mismatched_five_minute_adjustment_stays_rejected(fixture_store, monkeypatch, outcome):
    store = fixture_store
    key = f"bars:{CODE}:통합:5min"
    save(store, key, fixture_rows(), False, adjustment="split")
    remote = fixture_rows() if outcome == "success" else []

    def bridge(*_args):
        if outcome == "error":
            raise KeyError("fixture_non_production")
        return None if outcome == "disabled" else deepcopy(remote)

    monkeypatch.setattr(api, "history_via_openalgo", bridge)
    reads = watch_history_gets(store, monkeypatch)
    response = request("5min", "raw")
    assert response.status_code == 200
    transport = ("stored" if outcome == "disabled" else "stored_fallback"
                 if outcome == "error" else "openalgo")
    assert response.json() == expected({}, remote, "5min", "raw", transport)
    assert reads == ([key] if outcome in ("disabled", "error") else [])


def test_missing_store_still_accepts_remote_and_revalidates_revision(fixture_store, monkeypatch):
    store = fixture_store
    calls = []

    def bridge(*args):
        calls.append(args)
        return fixture_rows()

    monkeypatch.setattr(api, "history_via_openalgo", bridge)
    reads = watch_history_gets(store, monkeypatch, forbidden=True)
    first = request("day")
    assert first.status_code == 200
    assert first.json() == expected({}, fixture_rows(), "day", "source", "openalgo")
    same = TestClient(api.app).get(BASE, params={
        "venue": "통합", "interval": "day", "start": "2026-04-01", "end": "2026-04-30",
    }, headers={"If-None-Match": first.headers["etag"]})
    assert same.status_code == 304
    assert len(calls) == 1
    store.put(f"bars:{CODE}:통합:day", {
        "rows": [], "venue": "통합", "adjustment": "source", "source_trace": TRACE,
    })
    changed = request("day")
    assert changed.status_code == 200
    assert changed.headers["etag"] != first.headers["etag"]
    assert changed.json()["source_trace"] == TRACE
    assert len(calls) == 2
    assert reads == []
