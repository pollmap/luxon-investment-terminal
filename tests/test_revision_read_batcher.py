"""Fixture-only concurrency contracts; no retained revisions or TTL window."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from services.market_dashboard import response_cache
from services.market_dashboard.response_cache import RevisionReadBatcher, RevisionResponseCache


class RevisionStore:
    def __init__(self, values):
        self.values = values
        self.calls = []

    def revisions(self, keys):
        self.calls.append(tuple(keys))
        return {key: self.values[key] for key in keys if key in self.values}


def test_twenty_overlapping_key_reads_use_one_query_and_next_read_is_fresh():
    async def scenario():
        batcher = RevisionReadBatcher()
        source = RevisionStore({"universe": "r1", **{f"detail:{i}": str(i) for i in range(20)}})
        results = await asyncio.gather(
            *(batcher.read(source, ("universe", f"detail:{i}", "absent")) for i in range(20))
        )
        assert len(source.calls) == 1
        assert len(source.calls[0]) == 22
        assert results == [{"universe": "r1", f"detail:{i}": str(i)} for i in range(20)]
        source.values["universe"] = "r2"
        assert await batcher.read(source, ("universe",)) == {"universe": "r2"}
        assert len(source.calls) == 2
        assert not batcher.pending and not batcher.running

    asyncio.run(scenario())


def test_stores_and_event_loops_do_not_share_revisions():
    batcher = RevisionReadBatcher()

    async def scenario(label):
        first = RevisionStore({"quote": label})
        second = RevisionStore({"quote": label + "-other-store"})
        result = await asyncio.gather(
            batcher.read(first, ("quote",)), batcher.read(second, ("quote",))
        )
        assert result == [{"quote": label}, {"quote": label + "-other-store"}]
        assert len(first.calls) == len(second.calls) == 1

    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in [
            pool.submit(asyncio.run, scenario(label)) for label in ("first-loop", "second-loop")
        ]:
            future.result(timeout=3)
    assert not batcher.pending and not batcher.running


def test_cancelled_waiter_does_not_cancel_shared_revision_read():
    async def scenario():
        entered, release = threading.Event(), threading.Event()
        source = RevisionStore({"quote": "fixture_non_production"})
        original = source.revisions

        def blocked(keys):
            entered.set()
            assert release.wait(3)
            return original(keys)

        source.revisions = blocked
        batcher = RevisionReadBatcher()
        first = asyncio.create_task(batcher.read(source, ("quote",)))
        second = asyncio.create_task(batcher.read(source, ("quote",)))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
        finally:
            release.set()
        assert await second == {"quote": "fixture_non_production"}
        assert len(source.calls) == 1
        assert not batcher.pending and not batcher.running

    asyncio.run(scenario())


def test_failures_are_shared_but_never_retained():
    async def scenario():
        source = RevisionStore({"quote": "r2"})
        calls = 0

        def failed(keys):
            nonlocal calls
            calls += 1
            raise RuntimeError("fixture failure")

        source.revisions = failed
        batcher = RevisionReadBatcher()
        results = await asyncio.gather(
            *(batcher.read(source, ("quote",)) for _ in range(20)), return_exceptions=True
        )
        assert all(isinstance(result, RuntimeError) for result in results)
        assert calls == 1
        source.revisions = lambda _keys: {"quote": "r2"}
        assert await batcher.read(source, ("quote",)) == {"quote": "r2"}
        assert not batcher.pending and not batcher.running

    asyncio.run(scenario())


def test_loop_shutdown_releases_pending_and_running_batches(monkeypatch):
    batcher = RevisionReadBatcher()

    async def scenario():
        entered = asyncio.Event()

        async def stalled(*_args):
            entered.set()
            await asyncio.Future()

        monkeypatch.setattr(response_cache.asyncio, "to_thread", stalled)
        asyncio.create_task(batcher.read(RevisionStore({"quote": "r1"}), ("quote",)))
        await entered.wait()
        # asyncio.run cancels both the disconnected waiter and in-flight load.

    asyncio.run(scenario())
    assert not batcher.pending and not batcher.running


def test_batch_limits_preserve_unbatched_results():
    async def scenario():
        source = RevisionStore({str(i): str(i) for i in range(6)})
        batcher = RevisionReadBatcher(max_entries=1, max_keys=2)
        results = await asyncio.gather(
            batcher.read(source, ("0", "1")),
            batcher.read(source, ("2", "3")),
            batcher.read(source, ("3", "4", "5")),
        )
        assert results == [
            {"0": "0", "1": "1"},
            {"2": "2", "3": "3"},
            {"3": "3", "4": "4", "5": "5"},
        ]
        assert len(source.calls) == 3
        assert await batcher.read(source, ()) == {}
        assert not batcher.pending and not batcher.running

    asyncio.run(scenario())


def test_initial_batch_revision_does_not_cache_a_changed_build():
    source = RevisionStore({"quote": "r1"})
    cache = RevisionResponseCache()

    def changed_during_build():
        source.values["quote"] = "r2"
        return {"price": 100, "as_of": "2026-10-02", "source": "fixture_non_production"}

    cache.response(
        source, "summary", ("quote",), changed_during_build, initial_revision={"quote": "r1"}
    )
    assert not cache.entries
    assert len(source.calls) == 1  # The final revision check is never skipped.
