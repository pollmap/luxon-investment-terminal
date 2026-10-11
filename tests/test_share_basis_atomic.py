"""Independent SQLite writers preserve explicit reviews and atomic history."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
from threading import Event
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import event, select

from services.market_dashboard.share_basis_review_store import review_index_key
from services.market_dashboard.share_basis_worker import (
    apply_reviewed_window,
    review_share_basis,
    validate_page,
)
from services.market_dashboard.store import Store

CODE = "005930"
COMPANY = "00126380"
KEY = "share-basis:" + CODE
GUARD = review_index_key(CODE)
EVIDENCE = {
    "security_code": CODE, "company_id": COMPANY, "verified": True,
    "no_split_interval": True, "start": "2024-01-01", "end": "2025-09-30",
    "basis_id": "fixture_non_production", "basis_date": "2025-09-30",
    "source_trace": {"source_url": "https://example.test/fixture_non_production",
                     "sha256": "a" * 64},
}
HISTORY = "share-basis-history:" + CODE + ":" + hashlib.sha256(
    json.dumps(EVIDENCE, sort_keys=True).encode()
).hexdigest()[:32]


@pytest.fixture
def stores(tmp_path, monkeypatch):
    monkeypatch.delenv("MARKET_DATABASE_URL", raising=False)
    monkeypatch.delenv("MARKET_REDIS_URL", raising=False)
    url = "sqlite:///" + (tmp_path / "fixture.db").as_posix()
    instances = [Store(url, tmp_path) for _ in range(3)]
    instances[0].put("dart-corporations", {"codes": {CODE: COMPANY}})
    instances[0].put(KEY, EVIDENCE)
    try:
        yield instances
    finally:
        for store in instances:
            store.engine.dispose()


def job_for(store):
    query = {"corp_code": COMPANY, "bgn_de": "20251001", "end_de": "20251002",
             "pblntf_ty": "I", "last_reprt_at": "N", "page_no": "1"}
    payload = {"status": "013", "message": "fixture_non_production"}
    # verified_window uses its fixed source namespace and exact archived bytes.
    digest = store.raw("dart-share-window", "https://example.test/disclosure",
                       json.dumps(payload).encode())
    page, _ = validate_page(payload, query, digest)
    return {"company_id": COMPANY, "begin": "20251001", "end": "2025-10-02",
            "pages": [page]}


def manual_evidence():
    return {**deepcopy(EVIDENCE), "fact_unit_reviews": [{"fixture_non_production": "manual"}]}


def test_late_worker_rejects_real_other_store_update_before_cas(stores, monkeypatch):
    automatic, manual, observer = stores
    job = job_for(automatic)
    ready, resume = Event(), Event()
    original = automatic.compare_and_put

    def paused(*args, **kwargs):
        ready.set()
        assert resume.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(automatic, "compare_and_put", paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(apply_reviewed_window, automatic, CODE, COMPANY,
                             deepcopy(EVIDENCE), job, None)
        try:
            assert ready.wait(5)
            manual.put(KEY, manual_evidence())
            revision = manual.revisions([KEY])[KEY]
        finally:
            resume.set()
        with pytest.raises(ValueError, match="changed during review"):
            future.result(timeout=5)
    assert observer.get(KEY) == manual_evidence()
    assert observer.revisions([KEY])[KEY] == revision
    assert observer.get(HISTORY) is None


def test_manual_writer_waiting_during_history_insert_wins_after_atomic_commit(stores):
    automatic, manual, observer = stores
    job = job_for(automatic)
    at_history, resume, manual_attempt = Event(), Event(), Event()

    def pause_history(_conn, _cursor, statement, parameters, _context, _many):
        if statement.startswith("INSERT INTO market_snapshots") and parameters[0] == HISTORY:
            at_history.set()
            assert resume.wait(5)

    def see_manual(_conn, _cursor, statement, parameters, _context, _many):
        if statement.startswith("INSERT INTO market_snapshots") and parameters[0] == KEY:
            manual_attempt.set()

    event.listen(automatic.engine, "before_cursor_execute", pause_history)
    event.listen(manual.engine, "before_cursor_execute", see_manual)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            auto = pool.submit(apply_reviewed_window, automatic, CODE, COMPANY,
                               deepcopy(EVIDENCE), job, None)
            try:
                assert at_history.wait(5)
                explicit = pool.submit(manual.put, KEY, manual_evidence())
                assert manual_attempt.wait(5)
                # WAL reader sees neither an uncommitted basis nor its history.
                assert observer.get(KEY) == EVIDENCE
                assert observer.get(HISTORY) is None
                assert not explicit.done()
            finally:
                resume.set()
            assert auto.result(timeout=5)["status"] == "ready"
            assert explicit.result(timeout=5) is True
    finally:
        event.remove(automatic.engine, "before_cursor_execute", pause_history)
        event.remove(manual.engine, "before_cursor_execute", see_manual)
    assert observer.get(KEY) == manual_evidence()
    assert observer.get(HISTORY) == EVIDENCE


@pytest.mark.parametrize("existing", [False, True])
def test_guard_insert_or_revision_change_rejects_without_history(stores, existing):
    automatic, manual, observer = stores
    if existing:
        automatic.put(GUARD, {"entries": {"fixture": "old"}})
    revision = automatic.revisions([GUARD]).get(GUARD)
    manual.put(GUARD, {"entries": {"fixture": "new"}})
    assert automatic.compare_and_put(KEY, EVIDENCE, manual_evidence(), history_key=HISTORY,
                                     revision_guards={GUARD: revision}) is False
    assert observer.get(KEY) == EVIDENCE
    assert observer.get(HISTORY) is None


def test_failure_after_real_basis_write_rolls_back_history_and_revision(stores, monkeypatch):
    automatic, _, observer = stores
    revision = observer.revisions([KEY])[KEY]
    notifications = []
    original = automatic.put

    def fail_after_write(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("fixture_non_production interrupted before commit")

    monkeypatch.setattr(automatic, "put", fail_after_write)
    monkeypatch.setattr(automatic, "notify_changed", notifications.append)
    with pytest.raises(RuntimeError, match="interrupted"):
        automatic.compare_and_put(KEY, EVIDENCE, manual_evidence(), history_key=HISTORY)
    assert observer.get(KEY) == EVIDENCE
    assert automatic.get(KEY, use_redis=False) == EVIDENCE
    assert observer.revisions([KEY])[KEY] == revision
    assert observer.get(HISTORY) is None
    assert notifications == []


def test_conflicting_immutable_history_is_not_overwritten(stores):
    automatic, manual, observer = stores
    manual.put(HISTORY, {"fixture_non_production": "conflicting history"})
    revisions = observer.revisions([KEY, HISTORY])
    with pytest.raises(ValueError, match="Immutable snapshot history"):
        automatic.compare_and_put(KEY, EVIDENCE, manual_evidence(), history_key=HISTORY)
    assert observer.get(KEY) == EVIDENCE
    assert observer.revisions([KEY, HISTORY]) == revisions


def test_commit_invalidates_cache_advances_revision_and_notifies_after_commit(stores, monkeypatch):
    import services.market_dashboard.store as module

    automatic, _, observer = stores
    old_revision = automatic.revisions([KEY])[KEY]
    assert automatic.get(KEY) == EVIDENCE  # Populate decode cache.
    notifications = []

    def committed(name):
        assert observer.get(KEY) == manual_evidence()
        assert observer.get(HISTORY) == EVIDENCE
        notifications.append(name)

    monkeypatch.setattr(automatic, "notify_changed", committed)
    monkeypatch.setattr(module, "now", lambda: "2020-01-01T00:00:00+00:00")
    assert automatic.compare_and_put(KEY, EVIDENCE, manual_evidence(), history_key=HISTORY)
    assert automatic.get(KEY, use_redis=False) == manual_evidence()
    new_revision = automatic.revisions([KEY])[KEY]
    assert datetime.fromisoformat(new_revision) > datetime.fromisoformat(old_revision)
    assert notifications == [HISTORY, KEY]
    saved = automatic.revisions([KEY, HISTORY])
    assert automatic.compare_and_put(KEY, manual_evidence(), manual_evidence(),
                                     history_key="fixture-noop-history") is False
    assert automatic.revisions([KEY, HISTORY]) == saved
    assert automatic.get("fixture-noop-history") is None
    assert notifications == [HISTORY, KEY]


def test_legacy_json_layout_compares_semantics_and_keeps_original_history(stores):
    automatic, _, observer = stores
    legacy = json.dumps(EVIDENCE, ensure_ascii=False, indent=2)
    with automatic.engine.begin() as db:
        db.execute(automatic.snapshots.update().where(automatic.snapshots.c.key == KEY)
                   .values(payload=legacy))
    assert automatic.compare_and_put(
        KEY, deepcopy(EVIDENCE), manual_evidence(), history_key=HISTORY
    )
    with observer.read_connection() as db:
        archived = db.execute(select(observer.snapshots.c.payload).where(
            observer.snapshots.c.key == HISTORY)).scalar_one()
    assert archived == legacy


def test_two_independent_automatic_writers_only_one_commits_same_expected(stores):
    first, second, observer = stores
    job = job_for(first)
    with ThreadPoolExecutor(max_workers=2) as pool:
        attempts = [pool.submit(apply_reviewed_window, store, CODE, COMPANY,
                                deepcopy(EVIDENCE), job, None) for store in (first, second)]
        successes = 0
        for attempt in attempts:
            try:
                successes += attempt.result(timeout=5)["status"] == "ready"
            except ValueError as exc:
                assert "changed during review" in str(exc)
    assert successes == 1
    assert observer.get(KEY)["end"] == "2025-10-02"
    assert observer.get(HISTORY) == EVIDENCE


def test_cached_basis_mutation_during_provider_wait_cannot_change_expected(stores):
    import asyncio

    automatic, _, observer = stores
    automatic.put("universe", {"rows": [{"code": CODE, "name": "fixture_non_production",
                                       "market": "KOSPI"}]})
    automatic.put(f"bars:{CODE}:통합:raw", {"adjustment": "raw", "rows": [
        {"date": "2025-10-02", "close": 100}]})

    async def provider(url, **_kwargs):
        # Same-process callers receive mutable decoded values. A reviewer can
        # mutate then persist one while the worker awaits provider evidence.
        live = automatic.get(KEY, use_redis=False)
        live["fact_unit_reviews"] = manual_evidence()["fact_unit_reviews"]
        automatic.put(KEY, live)
        return httpx.Response(200, json={"status": "013"},
                              request=httpx.Request("GET", url))

    source = SimpleNamespace(store=automatic, dart="fixture_non_production", _dart_get=provider)
    result = asyncio.run(review_share_basis(source, CODE))
    assert result["status"] == "review_required"
    assert observer.get(KEY) == manual_evidence()
    assert observer.get(HISTORY) is None


def test_missing_basis_never_inserts_history_or_recreates_retracted_evidence(stores):
    automatic, manual, observer = stores
    with manual.engine.begin() as db:
        db.execute(manual.snapshots.delete().where(manual.snapshots.c.key == KEY))
    assert automatic.compare_and_put(KEY, EVIDENCE, manual_evidence(), history_key=HISTORY) is False
    assert observer.get(KEY) is None
    assert observer.get(HISTORY) is None


def test_existing_identical_history_retains_revision(stores):
    automatic, _, observer = stores
    automatic.put(HISTORY, EVIDENCE)
    revision = observer.revisions([HISTORY])[HISTORY]
    assert automatic.compare_and_put(KEY, EVIDENCE, manual_evidence(), history_key=HISTORY)
    assert observer.get(HISTORY) == EVIDENCE
    assert observer.revisions([HISTORY])[HISTORY] == revision
