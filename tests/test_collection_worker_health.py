from copy import deepcopy

import pytest

from services.market_dashboard.collection import collection_snapshot, worker_snapshot
from services.market_dashboard.store import Store


@pytest.mark.parametrize("status", ["collecting", "ready", "running", "starting"])
def test_old_running_claim_is_degraded_without_rewriting_its_evidence(status):
    source = {"status": status, "updated_at": "2026-10-06T16:50:21+00:00", "count": 2766}
    original = deepcopy(source)
    result = worker_snapshot(source, checked_at="2026-10-08T15:20:54Z")
    assert result["status"] == "degraded"
    assert result["reported_status"] == status
    assert result["heartbeat_status"] == "stale"
    assert result["heartbeat_age_seconds"] == 167433
    assert result["updated_at"] == source["updated_at"]
    assert result["count"] == 2766
    assert source == original


def test_fresh_offset_timestamp_and_boundary_are_current():
    source = {"status": "ready", "updated_at": "2026-10-09T00:00:00+09:00"}
    result = worker_snapshot(source, checked_at="2026-10-08T15:15:00Z", max_age_seconds=900)
    assert result["status"] == "ready"
    assert result["heartbeat_status"] == "current"
    assert result["heartbeat_age_seconds"] == 900


@pytest.mark.parametrize("stamp,freshness", [
    (None, "missing"), ("", "missing"), ("garbled", "invalid"),
    ("2026-10-09", "invalid"), ("2026-10-08T15:30:00Z", "invalid"),
])
def test_unknown_or_future_heartbeat_does_not_claim_running(stamp, freshness):
    result = worker_snapshot({"status": "collecting", "updated_at": stamp},
                             checked_at="2026-10-08T15:20:00Z")
    assert result["status"] == "degraded"
    assert result["heartbeat_status"] == freshness
    assert result["heartbeat_age_seconds"] is None


@pytest.mark.parametrize("status", ["error", "stopped", "capacity_limited", "disabled"])
def test_explicit_failure_or_pause_is_not_downgraded_to_generic_degraded(status):
    source = {"status": status, "updated_at": "2026-10-01T00:00:00Z"}
    result = worker_snapshot(source, checked_at="2026-10-08T15:20:00Z")
    assert result["status"] == status
    assert result["heartbeat_status"] == "stale"


def test_custom_slow_refresh_interval_does_not_report_normal_wait_as_failure(monkeypatch):
    monkeypatch.setenv("MARKET_UNIVERSE_INTERVAL", "1800")
    source = {"status": "ready", "updated_at": "2026-10-08T15:00:00Z"}
    result = worker_snapshot(source, checked_at="2026-10-08T15:20:00Z")
    assert result["heartbeat_stale_after_seconds"] == 5400
    assert result["status"] == "ready"


def test_collection_health_does_not_change_coverage_or_last_good_data(tmp_path):
    store = Store("sqlite://", tmp_path)
    worker = {"status": "collecting", "updated_at": "2000-01-01T00:00:00Z"}
    source = {"attempts": 1, "sources": {"quote": {"status": "ready"}}}
    store.put("worker", worker)
    store.put("collection:005930", source)
    result = collection_snapshot(store, [{"code": "005930"}])
    assert result["worker"]["status"] == "degraded"
    assert result["remaining"] == 0
    assert result["sources"]["quote"]["ready"] == 1
    assert result["sources"]["official"]["pending"] == 1
    assert store.get("worker") == worker
    assert store.get("collection:005930") == source


def test_derived_health_is_idempotent_and_keeps_original_worker_status():
    checked = "2026-10-08T15:20:00Z"
    first = worker_snapshot({"status": "collecting", "updated_at": "2026-10-01T00:00:00Z"},
                            checked_at=checked)
    assert worker_snapshot(first, checked_at=checked) == first
