"""Bounded, revision-aware public responses; no time-based freshness shortcut."""

import asyncio
import gzip
import json
import threading
from collections import OrderedDict

from . import request_timings as timings


class RevisionReadBatcher:
    """Combine keys queued in one event-loop turn, without retaining revisions."""

    def __init__(self, max_entries=256, max_keys=1024):
        self.max_entries = max_entries
        self.max_keys = max_keys
        self.pending = {}
        self.running = set()

    async def read(self, store, keys):
        with timings.stage('revision_wait'):
            return await self._read(store, keys)

    async def _read(self, store, keys):
        keys = tuple(dict.fromkeys(keys))
        if not keys:
            return {}
        loop = asyncio.get_running_loop()
        identity = (loop, store)
        batch = self.pending.get(identity)
        if len(keys) > self.max_keys or (
            batch is None and len(self.pending) + len(self.running) >= self.max_entries
        ) or (batch is not None and len(batch['keys'] | set(keys)) > self.max_keys):
            return await timings.to_thread(timings.read_revisions, store, keys)
        if batch is None:
            future = loop.create_future()
            # A cancelled last waiter must not leave an unhandled exception.
            future.add_done_callback(self._observe)
            batch = {'keys': set(), 'future': future, 'waiters': 0, 'dispatched': False}
            self.pending[identity] = batch
            batch['handle'] = loop.call_soon(self._dispatch, identity, batch)
        else:
            timings.count('revision_join')
        batch['keys'].update(keys)
        batch['waiters'] += 1
        try:
            revisions = await asyncio.shield(batch['future'])
            return {key: revisions[key] for key in keys if key in revisions}
        finally:
            batch['waiters'] -= 1
            if not batch['waiters'] and not batch['dispatched']:
                if self.pending.get(identity) is batch:
                    self.pending.pop(identity)
                batch['handle'].cancel()
                batch['future'].cancel()

    @staticmethod
    def _observe(future):
        if not future.cancelled():
            future.exception()

    def _dispatch(self, identity, batch):
        if self.pending.get(identity) is not batch:
            return
        self.pending.pop(identity)
        batch['dispatched'] = True
        task = identity[0].create_task(self._load(identity[1], batch))
        self.running.add(task)
        task.add_done_callback(self.running.discard)

    async def _load(self, store, batch):
        future = batch['future']
        try:
            revisions = await timings.to_thread(
                timings.read_revisions, store, tuple(sorted(batch['keys']))
            )
        except asyncio.CancelledError:
            future.cancel()
            raise
        except Exception as error:
            future.set_exception(error)
        else:
            future.set_result(revisions)


class InFlightResponses:
    """Share only overlapping reads; the next request always checks current revisions.

    Awaiters cannot cancel the shared read. Completed reads are removed immediately,
    including failures, so this is not a TTL cache and cannot pin a stale quote.
    """

    def __init__(self, max_entries=256):
        self.max_entries = max_entries
        self.tasks = {}

    async def run(self, key, build):
        with timings.stage('flight_wait'):
            return await self._run(key, build)

    async def _run(self, key, build):
        identity = (asyncio.get_running_loop(), key)
        task = self.tasks.get(identity)
        if task is None:
            if len(self.tasks) >= self.max_entries:
                return await timings.to_thread(build)
            task = asyncio.create_task(timings.to_thread(build))
            self.tasks[identity] = task

            def finished(completed):
                if self.tasks.get(identity) is completed:
                    self.tasks.pop(identity, None)
                # A disconnected last waiter must not leave an unhandled failure.
                if not completed.cancelled():
                    completed.exception()

            task.add_done_callback(finished)
        else:
            timings.count('flight_join')
        return await asyncio.shield(task)


class RevisionResponseCache:
    def __init__(self, max_bytes=16 * 1024**2, max_entries=128):
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self.entries = OrderedDict()
        self.size = 0
        self.lock = threading.Lock()
        # Bound lock allocation and coalesce concurrent requests for the same symbol.
        self.build_locks = [threading.Lock() for _ in range(32)]

    def response(self, store, key, dependencies, build, *, initial_revision=None):
        identity = (store, key)
        # A symbol view can validate the selected values of a compatibility-wide
        # snapshot without rebuilding when an unrelated symbol changes.
        revisions = (
            dependencies if callable(dependencies)
            else lambda: timings.read_revisions(store, dependencies)
        )
        lock = self.build_locks[hash(identity) % len(self.build_locks)]
        with timings.timed_lock(lock, 'response_lock'):
            revision = revisions() if initial_revision is None else initial_revision
            with self.lock:
                cached = self.entries.get(identity)
                if cached and cached[0] == revision:
                    self.entries.move_to_end(identity)
                    timings.count('response_hit')
                    return cached[1]
            # JSON serialization/compression never holds the shared read-model lock.
            with timings.stage('response_build'):
                payload = build()
            with timings.stage('response_encode'):
                body = gzip.compress(
                    json.dumps(
                        payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")
                    ).encode("utf-8"),
                    compresslevel=6,
                    mtime=0,
                )
            # A writer may have updated a dependency while the response was built.
            # Serve this observed snapshot, but never cache it under the newer revision.
            if revisions() == revision:
                with self.lock:
                    previous = self.entries.pop(identity, None)
                    if previous:
                        self.size -= len(previous[1])
                    if len(body) <= self.max_bytes:
                        while self.entries and (
                            self.size + len(body) > self.max_bytes
                            or len(self.entries) >= self.max_entries
                        ):
                            _, evicted = self.entries.popitem(last=False)
                            self.size -= len(evicted[1])
                        self.entries[identity] = (revision, body)
                        self.size += len(body)
            return body
