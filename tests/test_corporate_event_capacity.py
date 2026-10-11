"""fixture_non_production: bounded loop checks, no provider or production store."""

import asyncio
from types import SimpleNamespace

import pytest

from services.market_dashboard import corporate_events


@pytest.mark.parametrize("limited", [[True], [True, False], [False, True, False]])
def test_capacity_pause_preserves_events_and_cursor_until_resume(monkeypatch, limited):
    saved = {"events": [{"amount": "374", "source": "fixture_non_production"}], "cursor": 7}
    checks, repairs, calls, waits = [], [], [], []
    source = SimpleNamespace(store=saved, dart="fixture_non_production")

    def capacity(store, queue):
        assert store is saved and queue == "corporate-events"
        result = limited[len(checks)]
        checks.append(result)
        return {"limited": result}

    def repair(store):
        assert not checks[-1]
        repairs.append(True)

    def target(store):
        assert not checks[-1]
        store["cursor"] += 1
        return "005930"

    async def collect(provider, code):
        assert not checks[-1] and provider is source and code == "005930"
        calls.append(code)

    async def sleep(seconds):
        waits.append(seconds)
        if len(waits) == len(limited):
            raise asyncio.CancelledError

    monkeypatch.setattr("services.market_dashboard.storage_budget.collection_capacity", capacity)
    monkeypatch.setattr(corporate_events, "repair_related_events", repair)
    monkeypatch.setattr(corporate_events, "next_target", target)
    monkeypatch.setattr(corporate_events, "collect_events", collect)
    monkeypatch.setattr(corporate_events.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(corporate_events.corporate_events_loop(source))
    assert waits == [60 if paused else 15 for paused in limited]
    assert len(repairs) == int(False in limited)
    assert len(calls) == limited.count(False)
    assert saved["cursor"] == 7 + limited.count(False)
    assert saved["events"] == [{"amount": "374", "source": "fixture_non_production"}]
