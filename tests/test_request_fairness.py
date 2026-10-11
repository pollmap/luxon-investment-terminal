"""fixture_non_production: fair admissions do not assert collected data."""

import asyncio

import pytest

from services.market_dashboard.request_fairness import (
    FairRequestGate,
    current_workload,
    request_workload,
)


@pytest.mark.asyncio
async def test_history_has_one_in_four_contested_admissions():
    stamp = [0]
    gate = FairRequestGate(clock=lambda: stamp[0])
    order = []

    async def call(kind):
        with request_workload(kind):
            async with gate.slot():
                order.append(kind)
                # Slow provider responses must not reset fairness while both
                # queues are still waiting, even across observation windows.
                stamp[0] += 61
                await asyncio.sleep(0)

    async with gate.slot("latest"):
        tasks = [
            asyncio.create_task(call(kind))
            for kind in (["latest"] * 30 + ["interactive"] * 30 + ["history"] * 20)
        ]
        await asyncio.sleep(0)
    await asyncio.gather(*tasks)
    assert all(order[i : i + 4].count("history") == 1 for i in range(0, 60, 4))
    assert {"latest", "interactive", "history"} == set(order)
    assert gate.snapshot()["waiting"] == {"latest": 0, "interactive": 0, "history": 0}


@pytest.mark.asyncio
async def test_idle_and_pause_do_not_accumulate_reserved_credit():
    stamp = [0]
    gate = FairRequestGate(clock=lambda: stamp[0])
    for _ in range(12):
        async with gate.slot("history"):
            pass
    assert gate.foreground_streak == 0
    stamp[0] = 61
    gate.reset()
    assert sum(gate.snapshot()["admissions"].values()) == 0
    order = []

    async def call(kind):
        async with gate.slot(kind):
            order.append(kind)

    async with gate.slot("latest"):
        tasks = [asyncio.create_task(call(kind)) for kind in ["history"] * 8 + ["latest"] * 8]
        await asyncio.sleep(0)
    await asyncio.gather(*tasks)
    assert order[:4] == ["latest", "latest", "latest", "history"]


@pytest.mark.asyncio
async def test_cancelled_waiter_never_consumes_capacity_or_blocks_group():
    gate = FairRequestGate()

    async def call(kind):
        async with gate.slot(kind):
            return kind

    async with gate.slot("latest"):
        cancelled = asyncio.create_task(call("history"))
        next_request = asyncio.create_task(call("interactive"))
        await asyncio.sleep(0)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
    assert await asyncio.wait_for(next_request, 1) == "interactive"
    assert gate.snapshot()["waiting"] == {"latest": 0, "interactive": 0, "history": 0}
    assert gate.snapshot()["admissions"]["history"] == 0


def test_context_never_leaks_to_unrelated_latest_work():
    assert current_workload() == "latest"
    with request_workload("history"):
        assert current_workload() == "history"
    assert current_workload() == "latest"
