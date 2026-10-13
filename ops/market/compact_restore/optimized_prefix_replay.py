"""Prefix-only native COPY reuse; frozen codec, append and full-hash gates stay intact.

No connections or processes are opened by constructing these adapters. The runner
must first certify its owned loopback PG17 connection through indexed_resume.
"""

from __future__ import annotations

import hashlib
import sys
import time
from collections import OrderedDict
from collections.abc import Mapping
from contextlib import contextmanager
from copy import deepcopy

from . import checkpoint as cp
from . import resume_restore as resume
from .replay_batch import ROW_BATCH_PREFIX

codec = cp.codec
STAGE = 'pg_temp._market_prefix_replay_rows_v1'
CREATE_STAGE = (
    'CREATE TEMP TABLE _market_prefix_replay_rows_v1 '
    '(_position bigint GENERATED ALWAYS AS IDENTITY,series varchar(128) NOT NULL,'
    'time varchar(40) NOT NULL,digest varchar(64) NOT NULL,payload text NOT NULL) '
    'ON COMMIT DELETE ROWS'
)
EMPTY_STAGE = 'SELECT EXISTS(SELECT 1 FROM ' + STAGE + ')'
COPY_STAGE = 'COPY ' + STAGE + ' (series,time,digest,payload) FROM STDIN'
SELECT_STAGE = 'SELECT series,time,digest,payload FROM ' + STAGE + ' ORDER BY _position'
DROP_STAGE = 'DROP TABLE ' + STAGE


def object_bytes(value):
    """Count retained decoded JSON and its containers, not only raw byte_count."""
    total, pending, seen = 0, [value], set()
    while pending:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        total += sys.getsizeof(item)
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return total


class VerifiedRecord:
    """Private immutable proof; callers receive copies and cannot poison the cache."""

    __slots__ = ('digest', 'byte_count', 'payload', '_value', 'retained_bytes')

    def __init__(self, row):
        # Keep every original UTF8/hash/canonical validation for a new tuple.
        record = codec.TraceRecord(*row)
        value = record.verified_value()
        for name, item in zip(('digest', 'byte_count', 'payload'), row, strict=True):
            object.__setattr__(self, name, item)
        object.__setattr__(self, '_value', value)
        object.__setattr__(self, 'retained_bytes', object_bytes(value) + object_bytes(tuple(row)))

    def __setattr__(self, name, value):
        raise AttributeError('Verified prefix records are immutable')

    def matches(self, row):
        return (self.digest, self.byte_count, self.payload) == tuple(row)

    def verified_value(self):
        return deepcopy(self._value)


class VerifiedTraceCache(Mapping):
    """Fresh DB bytes once per digest per batch; bounded immutable proof reuse.

    A previous batch's digest alone is never proof of unchanged persisted data.
    Every distinct cached digest is re-read in the next batch before reuse.
    """

    def __init__(self, db, *, max_entries=256, max_bytes=16 * 1024**2):
        if (type(max_entries) is not int or not 1 <= max_entries <= 256
                or type(max_bytes) is not int or not 1 <= max_bytes <= 16 * 1024**2):
            raise codec.Refused('prefix_trace_cache_limits_invalid')
        self.db, self.max_entries, self.max_bytes = db, max_entries, max_bytes
        self.cache, self.checked = OrderedDict(), set()
        self.bytes = self.db_reads = self.validations = self.reuses = 0

    def begin_batch(self):
        self.checked.clear()

    def clear(self):
        self.cache.clear()
        self.checked.clear()
        self.bytes = 0

    def __getitem__(self, key):
        if self.db.info.transaction_status.name != 'INTRANS':
            raise codec.Refused('prefix_trace_active_transaction_required')
        if not cp.digest_valid(key):
            raise KeyError(key)
        if key in self.checked and key in self.cache:
            self.cache.move_to_end(key)
            self.reuses += 1
            return self.cache[key]
        row = self.db.execute(cp.TRACE_SELECT, (key,)).fetchone()
        self.db_reads += 1
        if row is None:
            raise KeyError(key)
        if (len(row) != 3 or row[0] != key or type(row[1]) is not int
                or not 0 <= row[1] <= 2**31 - 1 or not isinstance(row[2], str)):
            raise codec.Refused('prefix_trace_record_invalid')
        previous = self.cache.get(key)
        if previous is not None and previous.matches(row):
            record = previous
            self.reuses += 1
        else:
            record = VerifiedRecord(row)
            self.validations += 1
            if previous is not None:
                self.bytes -= previous.retained_bytes
                del self.cache[key]
                self.checked.discard(key)
        if record.retained_bytes <= self.max_bytes:
            while self.cache and (
                    (len(self.cache) >= self.max_entries and key not in self.cache)
                    or self.bytes + (0 if key in self.cache else record.retained_bytes)
                    > self.max_bytes):
                evicted, old = self.cache.popitem(last=False)
                self.bytes -= old.retained_bytes
                self.checked.discard(evicted)
            if key not in self.cache:
                self.cache[key] = record
                self.bytes += record.retained_bytes
            self.cache.move_to_end(key)
            self.checked.add(key)
        return record

    def __iter__(self):
        raise codec.Refused('Full trace ledger materialization is forbidden')

    def __len__(self):
        raise codec.Refused('Full trace ledger materialization is forbidden')

    def stats(self):
        return {'entries': len(self.cache), 'retained_bytes': self.bytes,
                'max_entries': self.max_entries, 'max_bytes': self.max_bytes,
                'db_reads': self.db_reads, 'full_validations': self.validations,
                'verified_reuses': self.reuses}


class PrefixStage:
    def __init__(self, db, limits):
        self.db, self.limits, self.created = db, limits, False

    def prepare(self):
        if (self.db.info.transaction_status.name != 'INTRANS'
                or self.db.info.encoding.lower().replace('-', '') != 'utf8'):
            raise codec.Refused('prefix_native_owned_utf8_transaction_required')
        if not self.created:
            self.db.execute(CREATE_STAGE)
            self.created = True
        if self.db.execute(EMPTY_STAGE).fetchone() != (False,):
            raise codec.Refused('prefix_stage_not_empty')

    def framing(self, event):
        if (event.table != codec.SERIES or type(event.row_count) is not int
                or not 1 <= event.row_count <= self.limits.max_batch_rows
                or self.limits.max_batch_rows > 400 or self.limits.max_batch_bytes > 16 * 1024**2
                or not isinstance(event.raw, bytes)
                or not 0 < len(event.raw) <= self.limits.max_batch_bytes):
            raise codec.Refused('Invalid staging batch limits')
        lines = event.raw.split(b'\n')
        if lines[-1] != b'' or len(lines) - 1 != event.row_count:
            raise codec.Refused('Invalid staging batch framing')
        endings = set()
        for line in lines[:-1]:
            content, ending = codec._line(line + b'\n')
            endings.add(ending)
            if content == b'\\.':
                raise codec.Refused('COPY terminator cannot be a staging data row')
        if len(endings) != 1:
            raise codec.Refused('Mixed staging line endings')

    def copy(self, event):
        with self.db.cursor() as cursor, cursor.copy(COPY_STAGE) as writer:
            writer.write(event.raw)  # PostgreSQL, not Python, decodes fields/escapes/nulls.

    def values(self, event):
        values = self.db.execute(SELECT_STAGE).fetchmany(self.limits.max_batch_rows + 1)
        if len(values) != event.row_count:
            raise codec.Refused('Native decoded row count differs')
        for value in values:
            codec.validate_series(value)
        return values


class PrefixReplaySink(resume.ResumeSink):
    def __init__(self, db, manifest, budget, report, *, clock=time.perf_counter):
        super().__init__(db, manifest, budget, report)
        self.stage = PrefixStage(self.db, manifest.limits())
        self.traces = VerifiedTraceCache(self.db)
        self.clock = clock
        self.metrics = {'adapter': 'prefix-native-reuse-v1', 'successful_batches': 0,
                        'failed_batches': 0, 'verified_series_rows': 0, 'phase_seconds': {},
                        'ordered_hash_verified': False, 'source_references_verified': False}
        self.report['prefix_optimization'] = self.metrics
        self.progress_writer = None
        self.progress_sequence = 0
        self.started_at = self.clock()
        self.last_progress_at = float('-inf')

    def observe(self, *, force=False):
        now = self.clock()
        if self.progress_writer is None or not (force or now - self.last_progress_at >= 60):
            return
        if self.progress_sequence >= 512:
            raise codec.Refused('prefix_observation_limit')
        try:
            self.budget.check()
            self.progress_sequence += 1
            snapshot = {
                'format': 'prefix-replay-observation-v1', 'observation_only': True,
                'checkpoint_authority': 'database_only', 'sequence': self.progress_sequence,
                'frozen_codec_sha256': self.manifest.codec_sha256,
                'archive_sha256': self.manifest.archive_sha256,
                'restore_verified': False, 'ordered_hash_verified': False,
                'source_references_verified': False,
                'elapsed_seconds': round(max(0.0, now - self.started_at), 6),
                'row_progress': deepcopy(self.report['row_progress']),
                'prefix_optimization': deepcopy(self.metrics),
            }
            for field in ('count_index_contract', 'checkpoint_counts'):
                if field in self.report:
                    snapshot[field] = deepcopy(self.report[field])
            self.progress_writer(self.progress_sequence, snapshot)
            self.last_progress_at = now
            self.budget.check()
        except BaseException as error:
            self.metrics['observation_error'] = type(error).__name__
            self.report['row_progress']['checkpoint_reload_required'] = True
            self._record(error, 'prefix_observation')
            raise

    @contextmanager
    def phase(self, name, measurements):
        self.metrics['last_batch']['phase'] = name
        start = self.clock()
        try:
            yield
        finally:
            elapsed = max(0.0, self.clock() - start)
            measurements[name] = measurements.get(name, 0.0) + elapsed
            totals = self.metrics['phase_seconds']
            totals[name] = round(totals.get(name, 0.0) + elapsed, 6)

    def clear_prefix(self):
        self.traces.clear()
        self.metrics['cache'] = self.traces.stats()
        if self.stage.created:
            if not self.locked:
                raise codec.Refused('Restore target is not exclusively owned')
            self.budget.check()
            with self.db.transaction():
                self.db.execute(DROP_STAGE)
            self.stage.created = False
            self.budget.check()

    def consume(self, event, before, after, *, replay):
        is_prefix = (replay is True and isinstance(event, codec.CopyBatch)
                     and event.table == codec.SERIES)
        if not is_prefix:
            if self.stage.created:
                self.observe(force=True)
            self.clear_prefix()
            return super().consume(event, before, after, replay=replay)
        progress = self.report['row_progress']
        progress['processed'] = {'trace_rows': after.trace_rows, 'series_rows': after.series_rows}
        self.metrics['last_batch'] = {
            'rows': event.row_count, 'state': 'checking', 'phase': 'budget',
        }
        measurements, was_created = {}, self.stage.created
        self.traces.begin_batch()
        committed = False
        try:
            if not self.locked or getattr(self.db, 'certified', False) is not True:
                raise codec.Refused('prefix_certified_owned_pg17_required')
            with self.phase('framing', measurements):
                self.stage.framing(event)
            self.budget.check(event.row_count * 128 + len(event.raw) * 4)
            with self.db.transaction():
                self.db.execute("SELECT set_config('statement_timeout',%s,true)",
                                (str(self.budget.check()),))
                with self.phase('stage_setup', measurements):
                    self.stage.prepare()
                with self.phase('copy', measurements):
                    self.stage.copy(event)
                with self.phase('decode_fetch', measurements):
                    values = self.stage.values(event)
                    keys = [tuple(row[:2]) for row in values]
                    if len(set(keys)) != len(keys):
                        raise codec.Refused('Duplicate time-series keys within a batch')
                self.budget.check()
                with self.phase('key_query', measurements):
                    statement = ROW_BATCH_PREFIX + ','.join(['(%s,%s)'] * len(keys)) + ') LIMIT %s'
                    parameters = ((self.manifest.max_batch_bytes,)
                                  + tuple(part for key in keys for part in key) + (len(keys) + 1,))
                    found = self.db.execute(statement, parameters).fetchmany(len(keys) + 1)
                    if len(found) != len(keys):
                        raise codec.Refused('Previously committed logical row differs')
                    stored = {}
                    for value in found:
                        codec.validate_series(value)
                        key = tuple(value[:2])
                        if key in stored:
                            raise codec.Refused('Previously committed logical row differs')
                        stored[key] = value
                    if stored.keys() != set(keys):
                        raise codec.Refused('Previously committed logical row differs')
                with self.phase('canonical_compare', measurements):
                    for index, value in enumerate(values):
                        if index % 64 == 0:
                            self.budget.check()
                        if codec.row_identity(value, self.traces) != codec.row_identity(
                                stored[keys[index]], self.traces):
                            raise codec.Refused('Previously committed logical row differs')
                self.budget.check()
                # Time transaction exit separately; never claim success before it.
                self.metrics['last_batch']['phase'] = 'commit'
                commit_started = self.clock()
            committed = True
            elapsed = max(0.0, self.clock() - commit_started)
            measurements['commit'] = elapsed
            totals = self.metrics['phase_seconds']
            totals['commit'] = round(totals.get('commit', 0.0) + elapsed, 6)
            self.budget.check()
        except BaseException as error:
            # If first CREATE rolled back, the stage does not exist. Existing
            # stage rows roll back to the prior empty state; do not hide pollution.
            if not committed and not was_created:
                self.stage.created = False
            self.traces.clear()
            self.metrics['cache'] = self.traces.stats()
            self.metrics['failed_batches'] += 1
            self.metrics['last_batch'].update(
                state='interrupted', seconds={k: round(v, 6) for k, v in measurements.items()},
            )
            progress['checkpoint_reload_required'] = True
            self._record(error, 'prefix_replay', before)
            raise
        self.replay_series_rows += len(values)
        self.replay_series_batches += 1
        self.metrics['successful_batches'] += 1
        self.metrics['verified_series_rows'] += len(values)
        self.metrics['cache'] = self.traces.stats()
        self.metrics['last_batch'].update(
            state='verified_prefix_batch',
            seconds={k: round(v, 6) for k, v in measurements.items()},
        )
        self.report.update(self.replay_stats())
        progress['verified_prefix'] = dict(progress['processed'])
        self.observe()

    def close(self):
        # Connection teardown drops the session-only stage, including on deadline.
        # No cleanup exception should cause persisted prefix rows to be rewritten.
        self.traces.clear()
        self.metrics['cache'] = self.traces.stats()
        return super().close()


def restore_with_prefix_sink(runner, manifest, *, sink_factory=PrefixReplaySink):
    """Explicit factory injection; same archive/child/end-of-stream protocol.

    The frozen coordinator, run() budgets and final_verify() are not replaced.
    """
    runner.budget.check(force=True)
    archive = runner.inputs['archive']
    before = archive.stat()
    sha = cp.verify_archive(archive, manifest, runner.budget)
    proof = resume.supervisor.VerifiedArchive.from_verified(
        archive, sha, manifest.archive_bytes, before=before, after=archive.stat(),
    )
    _, raw_toc = runner.command('pg_restore', ['--list', str(archive)], seconds=30)
    toc = resume.local.ordered_toc(raw_toc)
    toc_path = runner.attempt / 'ordered.list'
    resume.local._write_new(toc_path, toc)
    child = None
    try:
        with runner.connect() as db:
            remaining = runner.budget.max_seconds - (runner.budget.clock() - runner.budget.started)
            with resume.supervisor.open_pg_restore(
                    runner.tool('pg_restore'), proof, ordered_toc=toc_path,
                    toc_sha256=hashlib.sha256(toc).hexdigest(),
                    limits=resume.supervisor.Limits(max_seconds=remaining)) as child:
                try:
                    sink = sink_factory(db, manifest, runner.budget, runner.report)
                    if isinstance(sink, PrefixReplaySink):
                        sink.progress_writer = lambda sequence, snapshot: resume.local._report_file(
                            runner.attempt / f'prefix-progress-{sequence:04d}.json', snapshot,
                        )
                    result = cp.restore_prefix(
                        child.stream, sink,
                        manifest, archive=archive, budget=runner.budget,
                        child_exit=child.wait_exit,
                        evidence_path=runner.attempt / 'prefix-result.json',
                    )
                    runner.report['prefix'] = result
                    if (result['state'] != 'awaiting_ordered_verification'
                            or not result['stream_complete']):
                        raise resume.local.Refused('prefix_restore_incomplete')
                except (Exception, KeyboardInterrupt):
                    runner.report['stream_error_code'] = 'prefix_restore_interrupted'
                    try:
                        resume.local._report_file(runner.attempt / 'stream-interrupted.json', {
                            'error_code': 'prefix_restore_interrupted', 'restore_verified': False,
                            'checkpoint_reload_required': True,
                        })
                    except Exception:
                        runner.report['stream_evidence_error_code'] = 'evidence_write_failed'
                    raise
    finally:
        if child is not None:
            runner.report['pipe'] = child.safe_status()
    status = runner.report.get('pipe', {})
    if (status.get('exit_code') != 0 or not status.get('exit_waited')
            or not status.get('threads_stopped')):
        raise resume.local.Refused('restore_stream_incomplete')
    runner.budget.check(force=True)
