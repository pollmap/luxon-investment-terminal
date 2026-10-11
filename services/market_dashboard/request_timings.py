"""Request-local, fixed-name diagnostics; no URLs, identifiers, SQL or payloads."""

import asyncio
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

STAGES = (
    'revision_wait', 'revision_db', 'revision_sql', 'revision_result',
    'read_guard', 'db_checkout', 'executor_wait', 'rows_lock', 'rows_inputs',
    'rows_build', 'selected_read',
    'search_lock', 'search_sql', 'search_result', 'search_decode',
    'response_lock', 'response_build', 'response_encode', 'response_json',
    'response_gzip', 'flight_wait',
)
COUNTERS = (*STAGES, 'rows_built', 'rows_reused', 'response_hit', 'flight_join', 'revision_join',
            'search_full', 'search_delta', 'search_delta_rows', 'search_fallback')
_current = ContextVar('market_request_timings', default=None)


class RequestTimings:
    def __init__(self):
        self.durations = {}
        self.counts = {}
        self.lock = threading.Lock()

    def add(self, name, elapsed):
        if name not in STAGES:
            raise ValueError('Unknown timing stage')
        with self.lock:
            self.durations[name] = self.durations.get(name, 0.0) + max(0.0, elapsed * 1000)
            self.counts[name] = self.counts.get(name, 0) + 1

    def count(self, name, value=1):
        if name not in COUNTERS or not isinstance(value, int) or value < 0:
            raise ValueError('Invalid timing counter')
        with self.lock:
            self.counts[name] = self.counts.get(name, 0) + value

    def headers(self):
        with self.lock:
            # Only observed stages appear. Missing shared work is not zero-ms DB work.
            timing = ', '.join(f'{key};dur={min(self.durations[key], 99999999):.2f}'
                               for key in STAGES if key in self.durations)
            counts = ', '.join(f'{key}={min(self.counts[key], 999999999)}'
                               for key in COUNTERS if key in self.counts)
        return timing, counts


@contextmanager
def collect(enabled=True):
    record = RequestTimings() if enabled else None
    token = _current.set(record)
    try:
        yield record
    finally:
        _current.reset(token)


@contextmanager
def stage(name):
    record = _current.get()
    if record is None:
        yield
        return
    started = time.perf_counter()
    try:
        yield
    finally:
        record.add(name, time.perf_counter() - started)


def count(name, value=1):
    record = _current.get()
    if record is not None:
        record.count(name, value)


def timed(name):
    def decorate(function):
        @wraps(function)
        def measured(*args, **kwargs):
            with stage(name):
                return function(*args, **kwargs)
        return measured
    return decorate


@contextmanager
def timed_lock(lock, name):
    with stage(name):
        lock.acquire()
    try:
        yield
    finally:
        lock.release()


def read_revisions(store, keys):
    # Includes connection checkout and SQL execution, not executor queue time.
    # It is not a measurement of PostgreSQL server execution alone.
    with stage('revision_db'):
        return store.revisions(keys)


async def to_thread(function, *args):
    record = _current.get()
    if record is None:
        return await asyncio.to_thread(function, *args)
    scheduled = time.perf_counter()

    def execute():
        record.add('executor_wait', time.perf_counter() - scheduled)
        return function(*args)

    # asyncio copies this request's context into the executor. A shared task
    # records work for its creator; other callers record only their own wait.
    return await asyncio.to_thread(execute)
