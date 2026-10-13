"""fixture_non_production: source isolation, bounds and explicit page receipts."""

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from services.market_dashboard import app as api
from services.market_dashboard.analysis_inputs import analysis_prices
from services.market_dashboard.openalgo_bridge import history_via_openalgo
from services.market_dashboard.price_history import select_price_history
from services.market_dashboard.price_window import price_window
from services.market_dashboard.store import Store
from services.market_dashboard.time_series import put_series
from services.market_dashboard.toss import TossCollector
from services.market_dashboard.toss_backfill import TossBackfill

CODE = "005930"
TRACE = {"source": "fixture_non_production", "sha256": "a" * 64}


def bar(day, value=100):
    return {
        "date": day,
        "time": day + "T00:00:00+09:00",
        "open": value,
        "high": value + 10,
        "low": value - 10,
        "close": value,
        "volume": 10,
        "source_trace": TRACE,
    }


def page(day, cursor=None, value=100):
    return {
        "symbol": CODE,
        "candles": [
            {
                "timestamp": day + "T00:00:00+09:00",
                "openPrice": value,
                "highPrice": value + 10,
                "lowPrice": value - 10,
                "closePrice": value,
                "volume": 10,
                "currency": "KRW",
            }
        ],
        "nextBefore": cursor,
    }


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("MARKET_REDIS_URL", raising=False)
    monkeypatch.delenv("MARKET_OPENALGO_URL", raising=False)
    result = Store("sqlite://", tmp_path)
    result.put("universe", {"rows": [{"code": CODE, "market": "KOSPI"}]})
    monkeypatch.setattr(api, "get_store", lambda: result)
    yield result
    result.engine.dispose()


def archive(store, key, days, **fields):
    put_series(
        store,
        key,
        {
            "rows": [bar(day) for day in days],
            "venue": "통합",
            "adjustment": "source",
            "status": "ready",
            "source_trace": TRACE,
            **fields,
        },
    )


def test_widest_compatible_archive_is_selected_without_merging(store):
    archive(store, f"bars:{CODE}:통합:day", ["2025-01-01", "2026-01-01"])
    archive(
        store,
        "prices:" + CODE,
        ["1975-06-11", "2026-01-01"],
        source_trace={**TRACE, "source": "fixture_non_production_preserved_archive"},
    )
    result = price_window(store, CODE, range_id=100000)
    assert [r["date"] for r in result["rows"]] == ["1975-06-11", "2026-01-01"]
    assert result["source_trace"]["source"] == "fixture_non_production_preserved_archive"
    assert result["history_coverage"]["stored"]["rows"] == 2
    assert result["history_coverage"]["verification"]["status"] == "unverified"
    assert result["history_coverage"]["requested"]["start"] is None


@pytest.mark.parametrize(
    "fields", [{"venue": "KRX"}, {"adjustment": "raw"}, {"venue": None, "adjustment": None}]
)
def test_long_incompatible_or_unknown_archive_cannot_displace_toss(store, fields):
    archive(store, f"bars:{CODE}:통합:day", ["2025-01-01", "2026-01-01"])
    archive(store, "prices:" + CODE, ["1975-06-11", "2026-01-01"], **fields)
    result = price_window(store, CODE, range_id=100000)
    assert result["rows"][0]["date"] == "2025-01-01"
    assert result["venue"] == "통합" and result["adjustment"] == "source"


def test_explicit_venue_keeps_actual_legacy_source_and_unknown_only_falls_back(store):
    archive(store, f"bars:{CODE}:통합:day", ["2026-01-01"])
    archive(store, "prices:" + CODE, ["1975-06-11", "2026-01-01"], venue="KRX")
    selected, _ = select_price_history(store, CODE, venue="KRX")
    assert selected["venue"] == "KRX"
    store.put(
        "prices:000660",
        {
            "rows": [{"date": "1990-01-02", "close": 10}],
            "source_trace": {"source": "fixture_non_production_legacy"},
        },
    )
    result = price_window(store, "000660", range_id=100000)
    assert result["venue"] is None and result["rows"][0]["open"] is None
    assert result["history_coverage"]["selection"]["reason"] == "unverified_fallback"


def test_window_and_all_api_preserve_stored_bounds_and_short_display(store):
    archive(
        store,
        f"bars:{CODE}:통합:day",
        ["1975-06-11", "2024-01-02", "2026-01-02"],
        backfill_complete=False,
        next_before="1975-06-10T00:00:00+09:00",
    )
    client = TestClient(api.app)
    short = client.get(api.BASE + f"/securities/{CODE}/price-window?range=63").json()
    full = client.get(api.BASE + f"/securities/{CODE}/prices?period=all").json()
    assert len(short["rows"]) == 1 and len(full["rows"]) == 3
    assert short["coverage"]["start"] == "1975-06-11"
    assert short["history_coverage"]["displayed"]["start"] == "2026-01-02"
    assert short["history_coverage"]["collection"]["older_pages_pending"]
    assert not short["history_coverage"]["collection"]["backfill_complete"]
    assert full["history_coverage"]["stored"]["rows"] == 3
    assert client.get(api.BASE + f"/securities/{CODE}/prices?venue=bad").status_code == 422


def test_analysis_reads_same_archive_and_retains_unverified_gate(store):
    archive(store, f"bars:{CODE}:통합:day", ["2024-01-02", "2026-01-02"])
    archive(store, "prices:" + CODE, ["1975-06-11", "2026-01-02"])
    result, venue = analysis_prices(store, CODE, "auto", 0)
    assert venue == "통합" and result["rows"][0]["date"] == "1975-06-11"
    assert result["history_coverage"]["verification"]["status"] == "unverified"


def test_corrected_series_cannot_reuse_old_coverage_verification(store):
    key = f"bars:{CODE}:통합:day"
    archive(store, key, ["2026-01-01", "2026-01-02"])
    manifest = store.metadata(key)
    proof = {
        "status": "verified",
        "series_revision": manifest["series_revision"],
        "rows": 2,
        "start": "2026-01-01",
        "end": "2026-01-02",
        "ordered_sha256": "b" * 64,
        "source_references_verified": True,
        "calendar_checked": True,
    }
    store.put(key, {**manifest, "history_verification": proof})
    assert price_window(store, CODE)["history_coverage"]["verification"]["status"] == "verified"
    archive(store, key, ["2026-01-01", "2026-01-02", "2026-01-03"], history_verification=proof)
    assert price_window(store, CODE)["history_coverage"]["verification"]["status"] == "unverified"


def test_openalgo_uses_actual_pre1990_bounds_and_never_calls_provider(store, monkeypatch):
    archive(store, f"bars:{CODE}:통합:day", ["1975-06-11", "2026-01-02"])
    monkeypatch.setenv("MARKET_OPENALGO_URL", "http://fixture.invalid")
    monkeypatch.setattr(
        "services.market_dashboard.openalgo_bridge.internal_key", lambda: "fixture_key"
    )
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs["json"]))
        return httpx.Response(
            200, json={"status": "success", "data": []}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx, "post", post)
    assert history_via_openalgo(CODE, "day", None, None) == []
    assert calls[0][1]["start_date"] == "1975-06-11"
    assert calls[0][1]["end_date"] == "2026-01-02"


class Pages:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    async def get(self, path, params):
        self.calls.append((path, dict(params)))
        data = next(self.responses)
        digest = hashlib.sha256(json.dumps(data).encode()).hexdigest()
        # Synthetic receipt from this exact fixture page, never a live source.
        return data, {'source': 'toss', 'sha256': digest,
                      'source_document_id': digest, 'fixture_non_production': True}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [
        {},
        {"candles": {}},
        {"candles": ""},
        {"candles": None},
        {"candles": [], "nextBefore": "2026-01-01T00:00:00+09:00"},
    ],
)
async def test_malformed_page_cannot_finish_or_erase_archive(store, invalid):
    archive(store, f"bars:{CODE}:통합:day", ["2026-01-02"], next_before="2026-01-01T00:00:00+09:00")
    before = deepcopy(store.get(f"bars:{CODE}:통합:day"))
    collector = TossCollector(store, Pages([invalid]))
    with pytest.raises(ValueError):
        await collector.history(CODE, resume=True)
    assert store.get(f"bars:{CODE}:통합:day") == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cursor",
    ["2026-01-01T00:00:00+09:00", "2026-01-03T00:00:00+09:00", "opaque", "2025-12-31T00:00:00"],
)
async def test_stalled_forward_or_unscoped_cursor_preserves_last_good_page(store, cursor):
    archive(store, f"bars:{CODE}:통합:day", ["2026-01-02"], next_before="2026-01-01T00:00:00+09:00")
    before = deepcopy(store.metadata(f"bars:{CODE}:통합:day"))
    collector = TossCollector(store, Pages([page("2026-01-01", cursor)]))
    with pytest.raises(ValueError):
        await collector.history(CODE, resume=True)
    assert store.metadata(f"bars:{CODE}:통합:day") == before


@pytest.mark.asyncio
async def test_page_cannot_return_observation_after_requested_cursor(store):
    archive(store, f"bars:{CODE}:통합:day", ["2026-01-02"], next_before="2026-01-01T00:00:00+09:00")
    collector = TossCollector(store, Pages([page("2026-01-02", None)]))
    with pytest.raises(ValueError, match="upper bound"):
        await collector.history(CODE, resume=True)
    assert store.metadata(f"bars:{CODE}:통합:day")["row_count"] == 1


@pytest.mark.asyncio
async def test_valid_prefix_survives_restart_and_final_empty_page_records_receipt(store):
    collector = TossCollector(
        store, Pages([page("2026-01-02", "2026-01-01T00:00:00+09:00"), {"candles": {}}])
    )
    with pytest.raises(ValueError):
        await collector.history(CODE, pages=2)
    saved = store.metadata(f"bars:{CODE}:통합:day")
    assert saved["next_before"] == "2026-01-01T00:00:00+09:00" and saved["status"] == "partial"
    resumed = TossCollector(
        store,
        Pages(
            [page("2026-01-01", "2025-12-31T00:00:00+09:00"), {"candles": [], "nextBefore": None}]
        ),
    )
    await resumed.history(CODE, pages=2, resume=True)
    saved = store.metadata(f"bars:{CODE}:통합:day")
    assert saved["backfill_complete"] and saved["history_terminal"]["explicit_next_before_null"]
    expected = hashlib.sha256(json.dumps({"candles": [], "nextBefore": None}).encode()).hexdigest()
    assert saved["history_terminal"]["sha256"] == expected
    assert saved["history_terminal"]["code"] == CODE
    assert saved["history_terminal"]["kind"] == 'history'
    assert saved["history_terminal"]["venue"] == '통합'
    assert saved["history_terminal"]["adjustment"] == 'source'
    assert saved["row_count"] == 2


@pytest.mark.asyncio
async def test_partial_archive_without_cursor_can_resume_instead_of_refreshing_head_forever(store):
    key = f"bars:{CODE}:통합:day"
    archive(store, key, ["2026-01-01"], backfill_complete=False)
    collector = TossCollector(store, Pages([page("2026-01-01", "2025-12-31T00:00:00+09:00")]))
    await collector.history(CODE)
    assert store.metadata(key)["next_before"] == "2025-12-31T00:00:00+09:00"
    collector.client = Pages([page("2025-12-31", None)])
    await collector.history(CODE, resume=True)
    assert store.metadata(key)["backfill_complete"]
    assert store.metadata(key)["row_count"] == 2


@pytest.mark.asyncio
async def test_head_gap_and_old_cursor_are_independent_across_restart(store):
    key = f"bars:{CODE}:통합:day"
    archive(store, key, ["2010-01-01", "2020-01-01"], next_before="2009-12-31T00:00:00+09:00")
    collector = TossCollector(store, Pages([page("2026-01-01", "2025-12-31T00:00:00+09:00")]))
    await collector.history(CODE)
    snapshot = store.metadata(key)
    assert snapshot["next_before"] == "2009-12-31T00:00:00+09:00"
    assert snapshot["gap_next_before"] == "2025-12-31T00:00:00+09:00"
    original_head = snapshot["head_received_at"]
    resumed = TossCollector(
        store,
        Pages(
            [
                page("2025-12-31", "2020-01-01T00:00:00+09:00"),
                page("2020-01-01", "2019-12-31T00:00:00+09:00"),
            ]
        ),
    )
    await resumed.history(CODE, pages=2, repair=True)
    snapshot = store.metadata(key)
    assert snapshot["gap_status"] == "closed" and not snapshot["gap_next_before"]
    assert snapshot["next_before"] == "2009-12-31T00:00:00+09:00"
    assert snapshot["head_received_at"] == original_head and snapshot["gap_received_at"]
    assert not snapshot["backfill_complete"]
    assert snapshot["row_count"] == 4


@pytest.mark.asyncio
async def test_terminal_before_gap_overlap_does_not_claim_full_history(store):
    key = f"bars:{CODE}:통합:day"
    archive(store, key, ["2010-01-01", "2020-01-01"], backfill_complete=True)
    collector = TossCollector(store, Pages([page("2026-01-01", "2025-12-31T00:00:00+09:00")]))
    await collector.history(CODE)
    collector.client = Pages([page("2025-12-31", None)])
    await collector.history(CODE, repair=True)
    snapshot = store.metadata(key)
    assert snapshot["history_complete"] and not snapshot["backfill_complete"]
    assert snapshot["gap_status"] == "source_gap"
    assert price_window(store, CODE)["history_coverage"]["collection"]["gap_status"] == "source_gap"


@pytest.mark.asyncio
async def test_queue_alternates_gap_and_old_pages_without_resetting_either_cursor(
    store, monkeypatch
):
    monkeypatch.setenv("MARKET_TOSS_HISTORY_BURST", "1")
    key = f"bars:{CODE}:통합:day"
    archive(store, key, ["2010-01-01", "2020-01-01"], next_before="2009-12-31T00:00:00+09:00")
    collector = TossCollector(store, Pages([page("2026-01-01", "2025-12-31T00:00:00+09:00")]))
    await collector.history(CODE)
    client = Pages(
        [
            page("2025-12-31", "2025-12-30T00:00:00+09:00"),
            page("2009-12-31", "2009-12-30T00:00:00+09:00"),
        ]
    )
    collector.client = client
    queue = TossBackfill(
        collector, lambda _: SimpleNamespace(total=100 * 1024**3, free=50 * 1024**3)
    )
    store.put("toss-backfill:" + CODE + ":history", {"visits": 1})
    store.put("toss-backfill-progress", {"next_code": CODE, "phase": 0, "attempts": 1})
    await queue.step()
    assert store.metadata(key)["next_before"] == "2009-12-31T00:00:00+09:00"
    assert store.metadata(key)["gap_next_before"] == "2025-12-30T00:00:00+09:00"
    progress = store.get("toss-backfill-progress")
    store.put("toss-backfill-progress", {**progress, "phase": 0, "attempts": 2})
    await queue.step()
    assert [params["before"] for _, params in client.calls] == [
        "2025-12-31T00:00:00+09:00",
        "2009-12-31T00:00:00+09:00",
    ]
    assert store.metadata(key)["next_before"] == "2009-12-30T00:00:00+09:00"
    assert store.metadata(key)["gap_next_before"] == "2025-12-30T00:00:00+09:00"
