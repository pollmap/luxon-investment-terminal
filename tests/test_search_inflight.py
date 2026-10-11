import asyncio
import threading

import pytest

from services.market_dashboard.response_cache import InFlightResponses


def test_overlapping_reads_share_work_but_next_read_is_fresh():
    async def scenario():
        cache = InFlightResponses()
        entered, release = threading.Event(), threading.Event()
        calls = 0

        def build():
            nonlocal calls
            calls += 1
            entered.set()
            assert release.wait(3)
            return calls

        tasks = [asyncio.create_task(cache.run("query", build)) for _ in range(100)]
        assert await asyncio.to_thread(entered.wait, 3)
        await asyncio.sleep(0)
        release.set()
        assert await asyncio.gather(*tasks) == [1] * 100
        assert not cache.tasks
        assert await cache.run("query", build) == 2
        assert not cache.tasks

    asyncio.run(scenario())


def test_disconnected_waiter_does_not_cancel_other_readers():
    async def scenario():
        cache = InFlightResponses()
        entered, release = threading.Event(), threading.Event()

        def build():
            entered.set()
            assert release.wait(3)
            return "last-good"

        first = asyncio.create_task(cache.run("query", build))
        assert await asyncio.to_thread(entered.wait, 3)
        second = asyncio.create_task(cache.run("query", build))
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        assert await second == "last-good"
        assert not cache.tasks

    asyncio.run(scenario())


def test_errors_are_not_cached_and_query_keys_are_isolated():
    async def scenario():
        cache = InFlightResponses(max_entries=1)

        def failed():
            raise ValueError("fixture")

        with pytest.raises(ValueError, match="fixture"):
            await cache.run("query", failed)
        assert not cache.tasks
        assert await cache.run("query", lambda: "recovered") == "recovered"
        assert await asyncio.gather(
            cache.run("ordinary", lambda: "005930"),
            cache.run("preferred", lambda: "005935"),
        ) == ["005930", "005935"]
        assert not cache.tasks

    asyncio.run(scenario())
