"""Demand-based request fairness for one provider API group.

History gets at least one admission per four contested admissions. Unused
capacity is immediately borrowed; there is no token credit after idle/pause.
The rolling 60-second observation counts admissions, not upstream successes.
"""

import asyncio
import time
from collections import Counter, deque
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar

WORKLOADS = ("latest", "interactive", "history")
_workload = ContextVar("market_request_workload", default="latest")


@contextmanager
def request_workload(kind):
    if kind not in WORKLOADS:
        raise ValueError("Unknown collection workload")
    token = _workload.set(kind)
    try:
        yield
    finally:
        _workload.reset(token)


def current_workload():
    return _workload.get()


class FairRequestGate:
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.condition = asyncio.Condition()
        self.waiting = {kind: deque() for kind in WORKLOADS}
        self.active = None
        self.foreground_streak = 0
        self.last_foreground = "interactive"
        self.admissions = deque()

    def reset(self):
        """A provider cooldown never accrues a history/foreground entitlement."""
        self.foreground_streak = 0

    def _next(self):
        latest, interactive, history = (bool(self.waiting[k]) for k in WORKLOADS)
        if history and (not (latest or interactive) or self.foreground_streak >= 3):
            return "history"
        if latest and interactive:
            return "latest" if self.last_foreground == "interactive" else "interactive"
        return (
            "latest" if latest else "interactive" if interactive else "history" if history else None
        )

    @asynccontextmanager
    async def slot(self, kind=None):
        kind = kind or current_workload()
        if kind not in WORKLOADS:
            raise ValueError("Unknown collection workload")
        ticket = object()
        async with self.condition:
            self.waiting[kind].append(ticket)
            try:
                while (
                    self.active is not None
                    or self._next() != kind
                    or self.waiting[kind][0] is not ticket
                ):
                    await self.condition.wait()
                stamp = self.clock()
                contested = bool(self.waiting["history"]) and bool(
                    self.waiting["latest"] or self.waiting["interactive"]
                )
                self.waiting[kind].popleft()
                self.active = ticket
                self.foreground_streak = (
                    self.foreground_streak + 1 if contested and kind != "history" else 0
                )
                if kind != "history":
                    self.last_foreground = kind
                self.admissions.append((stamp, kind, contested))
            except BaseException:
                if ticket in self.waiting[kind]:
                    self.waiting[kind].remove(ticket)
                self.condition.notify_all()
                raise
        try:
            yield
        finally:
            async with self.condition:
                self.active = None
                if not any(self.waiting.values()):
                    self.reset()
                self.condition.notify_all()

    def snapshot(self):
        cutoff = self.clock() - 60
        while self.admissions and self.admissions[0][0] < cutoff:
            self.admissions.popleft()
        counts = Counter(kind for _, kind, _ in self.admissions)
        contested = Counter(kind for _, kind, both in self.admissions if both)
        return {
            "window_seconds": 60,
            "history_minimum_contested_share": 0.25,
            "admissions": {kind: counts[kind] for kind in WORKLOADS},
            "contested_admissions": {kind: contested[kind] for kind in WORKLOADS},
            "waiting": {kind: len(rows) for kind, rows in self.waiting.items()},
            "scope": "per_api_group_admissions_not_data_completion",
        }
