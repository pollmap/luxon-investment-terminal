"""fixture_non_production: mock native COPY only; no PG/process/provider operations."""

import json
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace

import pytest
from test_market_compact_replay_batch import Database, Result, make

from ops.market.compact_restore import optimized_prefix_replay as m

c = m.codec


class ReuseDatabase(Database):
    certified = True  # Explicit fake owned-index proof, never production evidence.

    def __init__(self, original):
        super().__init__(original.incoming, original.stored, original.traces)
        self.stage_exists, self.stage_data = False, []
        self.copy_failure = self.commit_failure = None

    @contextmanager
    def transaction(self):
        assert self.info.transaction_status.name == 'IDLE'
        self.transactions.append('begin')
        self.info.transaction_status.name = 'INTRANS'
        old = self.stage_exists, deepcopy(self.stage_data)
        try:
            yield
            if self.commit_failure:
                raise self.commit_failure
        except BaseException:
            self.stage_exists, self.stage_data = old
            self.transactions.append('rollback')
            raise
        else:
            self.transactions.append('commit')
            self.stage_data = []  # PostgreSQL ON COMMIT DELETE ROWS contract fixture.
            if self.on_commit:
                self.on_commit()
        finally:
            self.info.transaction_status.name = 'IDLE'

    def execute(self, sql, params=None):
        if sql in (m.CREATE_STAGE, m.EMPTY_STAGE, m.SELECT_STAGE, m.DROP_STAGE):
            assert self.info.transaction_status.name == 'INTRANS'
            self.calls.append((sql, params))
            if sql == m.CREATE_STAGE:
                assert not self.stage_exists
                self.stage_exists = True
                return Result([])
            if sql == m.EMPTY_STAGE:
                assert self.stage_exists
                return Result([(bool(self.stage_data),)])
            if sql == m.DROP_STAGE:
                assert self.stage_exists
                self.stage_exists, self.stage_data = False, []
                return Result([])
            return Result(self.stage_data)
        return super().execute(sql, params)

    @contextmanager
    def copy(self, sql):
        assert sql == m.COPY_STAGE and self.stage_exists
        if self.copy_failure:
            raise self.copy_failure

        def written(raw):
            self.raw_copies.append(raw)
            self.stage_data = deepcopy(self.incoming)

        class Writer:
            write = staticmethod(written)

        yield Writer()


def optimized(count=3, *, keys=None):
    old, original, budget, event, before, after = make(count, keys=keys)
    db = ReuseDatabase(original)
    report = {'restore_verified': False}
    sink = m.PrefixReplaySink(db, old.manifest, budget, report)
    sink.locked = True  # Fake lock only; runtime gets this from inherited open().
    return sink, db, budget, report, event, before, after


@pytest.mark.parametrize('count', [1, 400])
def test_reuses_one_typed_stage_and_preserves_all_exact_identities(count, monkeypatch):
    sink, db, _, report, event, before, after = optimized(count)
    before_hash = m.cp.codec_identity()
    identities = []
    original_identity = c.row_identity

    def observed(row, records):
        result = original_identity(row, records)
        identities.append(result)
        return result

    monkeypatch.setattr(c, 'row_identity', observed)
    saved = deepcopy((db.incoming, db.stored, db.traces))
    sink.consume(event, before, after, replay=True)
    first_ids = list(identities)
    sink.consume(event, before, after, replay=True)
    assert identities == first_ids * 2 and len(first_ids) == count * 2
    assert sum(sql == m.CREATE_STAGE for sql, _ in db.calls) == 1
    assert not any(sql in c.CREATE.values() or sql.startswith('TRUNCATE')
                   for sql, _ in db.calls)
    assert db.raw_copies == [event.raw, event.raw] and db.stage_data == []
    assert db.transactions == ['begin', 'commit', 'begin', 'commit']
    assert (db.incoming, db.stored, db.traces) == saved
    assert report['prefix_optimization']['successful_batches'] == 2
    assert report['prefix_optimization']['verified_series_rows'] == count * 2
    assert report['row_progress']['ordered_hash_verified'] is False
    assert report['restore_verified'] is False and sink.checkpoint is None
    assert sum(sql == m.cp.TRACE_SELECT for sql, _ in db.calls) == 2
    assert sink.traces.validations == 1
    assert set(report['prefix_optimization']['last_batch']['seconds']) == {
        'framing', 'stage_setup', 'copy', 'decode_fetch', 'key_query',
        'canonical_compare', 'commit',
    }
    assert m.cp.codec_identity() == before_hash


@pytest.mark.parametrize('defect', ['missing', 'duplicate', 'other_key', 'digest', 'payload',
    'trace_missing', 'trace_hash', 'incoming_duplicate'])
def test_missing_changed_or_duplicate_rows_rollback_and_clear_cache(defect):
    sink, db, _, report, event, before, after = optimized()
    if defect == 'missing':
        db.stored.pop()
    elif defect == 'duplicate':
        db.stored[1] = db.stored[0]
    elif defect == 'other_key':
        db.stored[0] = ('other', *db.stored[0][1:])
    elif defect == 'digest':
        db.stored[0] = (*db.stored[0][:2], 'f' * 64, db.stored[0][3])
    elif defect == 'payload':
        db.stored[0] = (*db.stored[0][:3], c.encode_json({'value': 999}))
    elif defect == 'trace_missing':
        db.traces.clear()
    elif defect == 'trace_hash':
        key = next(iter(db.traces))
        db.traces[key] = (key, 2, '{}')
    else:
        db.incoming[1] = db.incoming[0]
    saved = deepcopy((db.incoming, db.stored, db.traces))
    with pytest.raises((c.Refused, ValueError, KeyError)):
        sink.consume(event, before, after, replay=True)
    assert db.transactions == ['begin', 'rollback']
    assert not db.stage_exists and not sink.stage.created and sink.traces.bytes == 0
    assert (db.incoming, db.stored, db.traces) == saved
    assert report['prefix_optimization']['verified_series_rows'] == 0
    assert report['row_progress']['checkpoint_reload_required'] is True


def test_next_batch_rereads_cached_digest_and_rejects_persisted_tampering():
    sink, db, _, report, event, before, after = optimized()
    sink.consume(event, before, after, replay=True)
    key = next(iter(db.traces))
    db.traces[key] = (key, 2, '{}')
    with pytest.raises(ValueError, match='bytes or hash'):
        sink.consume(event, before, after, replay=True)
    assert sink.traces.bytes == 0 and report['prefix_optimization']['successful_batches'] == 1
    assert report['row_progress']['verified_prefix']['series_rows'] == after.series_rows
    assert db.stage_exists and db.stage_data == []


def test_exposed_verified_value_cannot_poison_retained_cache():
    sink, db, _, _, event, before, after = optimized()
    sink.consume(event, before, after, replay=True)
    record = next(iter(sink.traces.cache.values()))
    value = record.verified_value()
    value['source'] = 'mutated'
    assert record.verified_value()['source'] == 'fixture_non_production'
    with pytest.raises(AttributeError, match='immutable'):
        record.payload = '{}'


def test_existing_polluted_stage_is_refused_without_clearing_or_trusting_it():
    sink, db, _, _, event, before, after = optimized()
    sink.consume(event, before, after, replay=True)
    db.stage_data = [('pollution',)]
    with pytest.raises(c.Refused, match='not_empty'):
        sink.consume(event, before, after, replay=True)
    assert db.stage_data == [('pollution',)]
    assert not any(sql == m.DROP_STAGE for sql, _ in db.calls)


@pytest.mark.parametrize('failure', [KeyboardInterrupt(), RuntimeError('fixture secret')])
def test_copy_cancellation_and_failure_roll_back_and_do_not_leak_exception(failure):
    sink, db, _, report, event, before, after = optimized()
    db.copy_failure = failure
    with pytest.raises(type(failure)):
        sink.consume(event, before, after, replay=True)
    assert not db.stage_exists and not sink.stage.created
    assert sink.traces.bytes == 0 and 'fixture secret' not in json.dumps(report)
    assert report['prefix_optimization']['failed_batches'] == 1


def test_failed_commit_never_marks_prefix_batch_verified():
    sink, db, _, report, event, before, after = optimized()
    db.commit_failure = RuntimeError('fixture commit failure')
    with pytest.raises(RuntimeError):
        sink.consume(event, before, after, replay=True)
    assert db.transactions == ['begin', 'rollback']
    assert report['prefix_optimization']['successful_batches'] == 0
    assert not sink.stage.created and sink.traces.bytes == 0


def test_post_commit_deadline_does_not_advance_observed_verification():
    sink, db, budget, report, event, before, after = optimized(1)
    db.on_commit = lambda: setattr(budget, 'fail_at', len(budget.calls) + 1)
    with pytest.raises(c.Refused, match='elapsed-time'):
        sink.consume(event, before, after, replay=True)
    assert db.transactions == ['begin', 'commit']
    assert sink.stage.created and db.stage_data == []
    assert report['prefix_optimization']['successful_batches'] == 0
    assert report['row_progress']['checkpoint_reload_required'] is True


def test_rollback_of_initial_create_allows_a_clean_explicit_next_call():
    sink, db, _, _, event, before, after = optimized()
    db.query_failure = RuntimeError('fixture')
    with pytest.raises(RuntimeError):
        sink.consume(event, before, after, replay=True)
    db.query_failure = None
    sink.consume(event, before, after, replay=True)
    assert sum(sql == m.CREATE_STAGE for sql, _ in db.calls) == 2
    assert db.stage_data == [] and sink.replay_series_rows == event.row_count


@pytest.mark.parametrize('kind', ['rows', 'bytes', 'bad_frame'])
def test_frozen_limits_and_framing_reject_before_copy_or_stage(kind):
    sink, db, _, _, event, before, after = optimized()
    if kind == 'rows':
        sink.stage.limits = replace(sink.stage.limits, max_batch_rows=2)
    elif kind == 'bytes':
        sink.stage.limits = replace(sink.stage.limits, max_batch_bytes=2)
    else:
        event = replace(event, raw=event.raw[:-1])
    with pytest.raises(c.Refused):
        sink.consume(event, before, after, replay=True)
    assert not db.calls and not db.raw_copies


@pytest.mark.parametrize('unowned', ['lock', 'certification'])
def test_uncertified_or_unowned_connection_does_not_touch_database(unowned):
    sink, db, _, _, event, before, after = optimized()
    if unowned == 'lock':
        sink.locked = False
    else:
        db.certified = False
    with pytest.raises(c.Refused, match='certified_owned'):
        sink.consume(event, before, after, replay=True)
    assert not db.calls


@pytest.mark.parametrize('table,is_batch,replay', [
    (c.TRACES, True, True), (c.TRACES, True, False), (c.SERIES, True, False),
    (c.SERIES, False, True), (c.SERIES, False, False)])
def test_append_trace_and_end_switch_to_original_sink_with_empty_resources(
        table, is_batch, replay, monkeypatch):
    sink, db, _, _, event, before, after = optimized()
    sink.consume(event, before, after, replay=True)
    expected = []
    monkeypatch.setattr(m.resume.ResumeSink, 'consume',
                        lambda self, *args, **kwargs: expected.append((args, kwargs)))
    next_event = c.CopyBatch(table, 1, b'fixture\n') if is_batch else c.CopyEnd(table, 1)
    sink.consume(next_event, before, after, replay=replay)
    assert expected == [((next_event, before, after), {'replay': replay})]
    assert not sink.stage.created and not db.stage_exists and sink.traces.bytes == 0
    assert sum(sql == m.DROP_STAGE for sql, _ in db.calls) == 1


def test_cache_bounds_include_decoded_objects_and_evictions():
    _, db, _, _, _, _, _ = optimized()
    cache = m.VerifiedTraceCache(db, max_entries=1, max_bytes=4096)
    first = next(iter(db.traces))
    second_record = c.TraceRecord.from_value({'source': 'second_fixture'})
    db.traces[second_record.digest] = (
        second_record.digest, second_record.byte_count, second_record.payload)
    with db.transaction():
        cache.begin_batch()
        cache[first]
        assert cache.stats()['retained_bytes'] > db.traces[first][1]
        cache[second_record.digest]
    assert list(cache.cache) == [second_record.digest] and cache.bytes <= 4096
    assert first not in cache.checked
    cache.clear()
    assert cache.bytes == 0 and not cache.checked


def test_oversized_decoded_trace_is_not_retained_and_ledger_scan_is_refused():
    _, db, _, _, _, _, _ = optimized()
    cache = m.VerifiedTraceCache(db, max_bytes=1)
    key = next(iter(db.traces))
    with db.transaction():
        assert cache[key].digest == key and cache.bytes == 0
    with pytest.raises(c.Refused, match='materialization'):
        list(cache)


def test_progress_sidecars_are_bounded_observations_not_checkpoint_or_hash_authority():
    sink, _, _, report, event, before, after = optimized()
    now = [0.0]
    sink.clock = lambda: now[0]
    sink.started_at = 0.0
    observations = []
    sink.progress_writer = lambda sequence, value: observations.append((sequence, value))
    sink.consume(event, before, after, replay=True)
    now[0] = 20.0
    sink.consume(event, before, after, replay=True)
    assert len(observations) == 1
    now[0] = 60.0
    sink.consume(event, before, after, replay=True)
    assert [sequence for sequence, _ in observations] == [1, 2]
    snapshot = observations[-1][1]
    assert snapshot['observation_only'] is True
    assert snapshot['checkpoint_authority'] == 'database_only'
    assert snapshot['archive_sha256'] == sink.manifest.archive_sha256
    assert snapshot['frozen_codec_sha256'] == sink.manifest.codec_sha256
    assert snapshot['restore_verified'] is snapshot['ordered_hash_verified'] is False
    assert snapshot['source_references_verified'] is False
    assert report['row_progress']['committed'] is None
    snapshot['row_progress']['verified_prefix']['series_rows'] = 99999
    assert report['row_progress']['verified_prefix']['series_rows'] == after.series_rows


def test_observation_failure_is_safe_and_does_not_become_restore_success():
    sink, db, _, report, event, before, after = optimized()

    def failed(*_args):
        raise OSError('fixture secret must not be published')

    sink.progress_writer = failed
    with pytest.raises(OSError):
        sink.consume(event, before, after, replay=True)
    assert db.transactions == ['begin', 'commit']
    assert report['row_progress']['checkpoint_reload_required'] is True
    assert report['restore_verified'] is False and 'fixture secret' not in json.dumps(report)
    assert report['failure_diagnostic']['phase'] == 'prefix_observation'


@pytest.mark.parametrize('prefix_ok,pipe_ok', [(True, True), (False, True), (True, False)])
def test_explicit_sink_factory_preserves_archive_and_child_exit_gates(
        tmp_path, monkeypatch, prefix_ok, pipe_ok):
    from types import SimpleNamespace

    archive = tmp_path / 'fixture.dump'
    archive.write_bytes(b'PGDMP fixture_non_production')
    calls = []
    manifest = m.cp.Manifest('a' * 64, archive.stat().st_size, 'b' * 64,
                             m.cp.codec_identity(), 0, 0)

    class Budget:
        max_seconds, started = 60, 0
        clock = staticmethod(lambda: 1)

        def check(self, incoming=0, *, force=False):
            calls.append(('budget', incoming, force))
            return 10000

    class Child:
        stream = object()

        def __enter__(self):
            calls.append(('child_enter',))
            return self

        def __exit__(self, *_args):
            calls.append(('child_exit',))

        def wait_exit(self):
            return 0

        def safe_status(self):
            return {'exit_code': 0, 'exit_waited': pipe_ok, 'threads_stopped': True}

    db = object()

    @contextmanager
    def connect():
        yield db

    runner = SimpleNamespace(
        budget=Budget(), inputs={'archive': archive}, attempt=tmp_path, report={},
        command=lambda *args, **kwargs: (0, b'fixture ordered TOC'), connect=connect,
        tool=lambda _name: tmp_path / 'fake-never-started',
    )
    proof = object()
    monkeypatch.setattr(m.cp, 'verify_archive',
                        lambda path, identity, budget: calls.append(('archive', path, identity))
                        or manifest.archive_sha256)
    monkeypatch.setattr(m.resume.supervisor.VerifiedArchive, 'from_verified',
                        lambda *args, **kwargs: proof)
    monkeypatch.setattr(m.resume.local, 'ordered_toc', lambda _raw: b'fixture')
    monkeypatch.setattr(m.resume.supervisor, 'open_pg_restore',
                        lambda *args, **kwargs: calls.append(('child_open', args[1])) or Child())
    expected_sink = object()

    def factory(got_db, got_manifest, got_budget, got_report):
        assert (got_db, got_manifest, got_budget, got_report) == (
            db, manifest, runner.budget, runner.report)
        calls.append(('factory',))
        return expected_sink

    def coordinator(stream, sink, got_manifest, **kwargs):
        assert sink is expected_sink and got_manifest is manifest
        assert kwargs['archive'] is archive and kwargs['budget'] is runner.budget
        assert kwargs['child_exit']() == 0
        assert kwargs['evidence_path'] == tmp_path / 'prefix-result.json'
        return {'state': 'awaiting_ordered_verification' if prefix_ok else 'interrupted',
                'stream_complete': prefix_ok}

    monkeypatch.setattr(m.cp, 'restore_prefix', coordinator)
    if prefix_ok and pipe_ok:
        m.restore_with_prefix_sink(runner, manifest, sink_factory=factory)
        assert calls[-1] == ('budget', 0, True)
    else:
        with pytest.raises(m.resume.local.Refused):
            m.restore_with_prefix_sink(runner, manifest, sink_factory=factory)
    assert ('archive', archive, manifest) in calls and ('child_open', proof) in calls
    assert ('child_exit',) in calls and 'pipe' in runner.report
    assert 'restore_verified' not in runner.report
