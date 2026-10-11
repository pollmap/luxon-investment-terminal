"""fixture_non_production: pure SQL contracts, not native PostgreSQL evidence."""

import copy
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from ops.market.compact_restore import checkpoint as cp
from ops.market.compact_restore import replay_batch as batch
from ops.market.compact_restore.resume_restore import ResumeSink

c = cp.codec


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def fetchmany(self, size):
        assert size <= 401
        return self.rows[:size]


class Budget:
    def __init__(self, fail_at=None):
        self.calls = []
        self.fail_at = fail_at

    def check(self, incoming=0):
        self.calls.append(incoming)
        if len(self.calls) == self.fail_at:
            raise c.Refused("Restore elapsed-time budget exhausted")
        return 10000


class Database:
    """Typed decode rows stand in for PG COPY; actual adapter still validates framing."""

    autocommit = True

    def __init__(self, incoming, stored, traces):
        self.incoming, self.stored, self.traces = incoming, stored, traces
        self.calls, self.transactions, self.raw_copies = [], [], []
        self.info = SimpleNamespace(encoding="utf-8",
                                    transaction_status=SimpleNamespace(name="IDLE"))
        self.query_failure = None
        self.on_commit = None

    @contextmanager
    def transaction(self):
        self.transactions.append("begin")
        self.info.transaction_status.name = "INTRANS"
        try:
            yield
        except BaseException:
            self.transactions.append("rollback")
            raise
        else:
            self.transactions.append("commit")
            if self.on_commit:
                self.on_commit()
        finally:
            self.info.transaction_status.name = "IDLE"

    def execute(self, sql, params=None):
        assert self.info.transaction_status.name == "INTRANS"
        self.calls.append((sql, params))
        if sql.startswith(batch.ROW_BATCH_PREFIX):
            if self.query_failure:
                raise self.query_failure
            if sum(len(row[3].encode("utf8")) for row in self.stored) > params[0]:
                return Result([(*row[:3], None) for row in self.stored])
            return Result(self.stored)
        if sql == cp.TRACE_SELECT:
            row = self.traces.get(params[0])
            return Result([row] if row else [])
        if sql == cp.ROW_SELECT:
            return Result([row for row in self.stored if row[:2] == params])
        if sql == c.SELECT[c.SERIES]:
            return Result(self.incoming)
        if sql in c.CREATE.values() or sql == c.TRUNCATE[c.SERIES]:
            return Result([])
        if sql == "SELECT set_config('statement_timeout',%s,true)":
            assert params == ("10000",)
            return Result([])
        raise AssertionError("Unexpected SQL: fixture prevents persistent writes")

    @contextmanager
    def cursor(self):
        yield self

    @contextmanager
    def copy(self, sql):
        assert sql == c.COPY[c.SERIES]
        yield SimpleNamespace(write=self.raw_copies.append)


def make(count=3, *, keys=None):
    trace = c.TraceRecord.from_value({"source": "fixture_non_production", "note": "한글\n\\"})
    incoming, stored = [], []
    for n in range(count):
        key = keys[n] if keys else ("fixture", str(n))
        digest = f"{n:064x}"
        body = {"value": n, "source_trace": trace.verified_value()}
        incoming.append((*key, digest, c.encode_json(body)))
        stored.append((*key, digest, c.encode_json({c.FORMAT_KEY: c.FORMAT, "row": {
            "value": n, "source_trace": {c.REF_KEY: trace.digest},
        }})))
    db = Database(incoming, list(reversed(stored)), {
        trace.digest: (trace.digest, trace.byte_count, trace.payload),
    })
    manifest = cp.Manifest("a" * 64, 1, "b" * 64, "c" * 64, 1, count)
    budget = Budget()
    sink = batch.ReplayBatchSink(db, manifest, budget)
    sink.locked = True  # Explicit ownership fixture; no actual lock or PG contact.
    raw = b"".join(b"\t".join(str(v).encode().replace(b"\\", b"\\\\")
                                .replace(b"\t", b"\\t").replace(b"\n", b"\\n")
                                for v in row) + b"\n" for row in incoming)
    event = c.CopyBatch(c.SERIES, count, raw)
    before = cp.Checkpoint(trace_complete=True, trace_rows=1)
    after = before.advance(event)
    return sink, db, budget, event, before, after


@pytest.mark.parametrize("count", [1, 400])
def test_all_rows_are_verified_with_one_exact_parameterized_query(count, monkeypatch):
    keys = [("한글'; --", "time\\\t")]+[("fixture", str(n)) for n in range(1, count)]
    sink, db, budget, event, before, after = make(count, keys=keys)
    original = c.row_identity
    verified = []

    def observed(row, records):
        verified.append(row)
        return original(row, records)

    monkeypatch.setattr(c, "row_identity", observed)
    saved = copy.deepcopy((db.stored, db.traces))
    sink.consume(event, before, after, replay=True)
    selects = [(sql, args) for sql, args in db.calls if sql.startswith(batch.ROW_BATCH_PREFIX)]
    assert len(selects) == 1
    assert selects[0][0].count("(%s,%s)") == count
    assert selects[0][1] == ((sink.manifest.max_batch_bytes,)
                            + tuple(part for key in keys for part in key) + (count + 1,))
    assert "한글" not in selects[0][0]
    assert len(verified) == count * 2
    assert sorted(verified) == sorted(db.incoming + db.stored)
    assert sum(sql == cp.TRACE_SELECT for sql, _ in db.calls) == 1
    assert not any(sql == cp.ROW_SELECT for sql, _ in db.calls)
    assert db.transactions == ["begin", "commit"]
    assert (db.stored, db.traces) == saved and sink.checkpoint is None
    assert db.raw_copies == [event.raw]
    assert budget.calls[0] == count * 128 + len(event.raw) * 4
    assert sink.replay_stats() == {"replay_method": "exact-pk-batch-v1",
                                  "replay_validated_series_rows": count,
                                  "replay_validated_series_batches": 1}


@pytest.mark.parametrize("defect", ["missing", "duplicate", "other_key", "digest", "payload",
                                  "trace_missing", "trace_hash", "incoming_duplicate"])
def test_changed_or_incomplete_prefix_rolls_back_without_modifying_evidence(defect):
    sink, db, _budget, event, before, after = make()
    if defect == "missing":
        db.stored.pop()
    elif defect == "duplicate":
        db.stored[1] = db.stored[0]
    elif defect == "other_key":
        db.stored[0] = ("different", *db.stored[0][1:])
    elif defect == "digest":
        db.stored[0] = (*db.stored[0][:2], "f" * 64, db.stored[0][3])
    elif defect == "payload":
        db.stored[0] = (*db.stored[0][:3], c.encode_json({"value": 999}))
    elif defect == "trace_missing":
        db.traces.clear()
    elif defect == "trace_hash":
        key = next(iter(db.traces))
        db.traces[key] = (key, 2, "{}")
    else:
        db.incoming[1] = db.incoming[0]
    saved = copy.deepcopy((db.incoming, db.stored, db.traces))
    with pytest.raises((c.Refused, ValueError)):
        sink.consume(event, before, after, replay=True)
    assert db.transactions == ["begin", "rollback"]
    assert (db.incoming, db.stored, db.traces) == saved
    assert sink.checkpoint is None
    assert sink.replay_series_rows == sink.replay_series_batches == 0


def test_missing_exclusive_lock_refuses_before_database_or_budget():
    sink, db, budget, event, before, after = make()
    sink.locked = False
    with pytest.raises(c.Refused, match="exclusively"):
        sink.consume(event, before, after, replay=True)
    assert not db.calls and not budget.calls


@pytest.mark.parametrize("fail_at", [1, 2, 3, 4, 5, 6])
def test_deadline_and_commit_boundary_never_report_success(fail_at):
    sink, db, budget, event, before, after = make(1)
    budget.fail_at = fail_at
    with pytest.raises(c.Refused, match="elapsed-time"):
        sink.consume(event, before, after, replay=True)
    assert db.transactions == ([] if fail_at == 1 else
                               ["begin", "commit"] if fail_at == 6 else ["begin", "rollback"])
    assert sink.checkpoint is None
    assert sink.replay_series_rows == sink.replay_series_batches == 0


def test_query_error_rolls_back_and_propagates_original_exception():
    sink, db, _budget, event, before, after = make()
    failure = RuntimeError("fixture query error")
    db.query_failure = failure
    with pytest.raises(RuntimeError) as found:
        sink.consume(event, before, after, replay=True)
    assert found.value is failure
    assert db.transactions == ["begin", "rollback"]


@pytest.mark.parametrize("table,is_batch,replay", [
    (c.TRACES, True, True), (c.SERIES, True, False),
    (c.TRACES, True, False), (c.SERIES, False, True), (c.SERIES, False, False),
])
def test_original_trace_append_and_end_events_use_unchanged_parent(
    table, is_batch, replay, monkeypatch,
):
    sink, db, budget, _event, before, after = make()
    event = c.CopyBatch(table, 1, b"fixture\n") if is_batch else c.CopyEnd(table, 1)
    calls = []

    def original(self, *args, **kwargs):
        calls.append((self, args, kwargs))

    monkeypatch.setattr(cp.PgBatchSink, "consume", original)
    sink.consume(event, before, after, replay=replay)
    assert calls == [(sink, (event, before, after), {"replay": replay})]
    assert not db.calls and not budget.calls


def test_resume_failure_recorder_wraps_batch_replay_without_frozen_changes():
    _sink, db, budget, event, before, after = make()
    report = {}
    sink = ResumeSink(db, _sink.manifest, budget, report)
    sink.locked = True
    db.stored.pop()
    with pytest.raises(c.Refused, match="logical row differs"):
        sink.consume(event, before, after, replay=True)
    assert report["failure_diagnostic"]["code"] == "prefix_row_differs"
    assert report["failure_diagnostic"]["phase"] == "prefix_replay"
    assert db.transactions == ["begin", "rollback"]
    assert report["replay_validated_series_rows"] == 0


def test_resume_report_counts_only_completed_prefix_batches():
    built, db, budget, event, before, after = make()
    report = {}
    sink = ResumeSink(db, built.manifest, budget, report)
    sink.locked = True
    sink.consume(event, before, after, replay=True)
    assert report["replay_validated_series_rows"] == 3
    assert report["replay_validated_series_batches"] == 1
    assert report["row_progress"]["verified_prefix"]["series_rows"] == after.series_rows
    assert report["row_progress"]["ordered_hash_verified"] is False
    db.stored.pop()
    with pytest.raises(c.Refused):
        sink.consume(event, before, after, replay=True)
    assert report["replay_validated_series_rows"] == 3
    assert report["replay_validated_series_batches"] == 1
    assert report["row_progress"]["checkpoint_reload_required"] is True


def test_oversized_saved_payload_is_blocked_before_transfer():
    sink, db, _budget, event, before, after = make(1)
    sink.manifest = replace(sink.manifest, max_batch_bytes=len(event.raw))
    db.stored[0] = (*db.stored[0][:3], "x" * (len(event.raw) + 1))
    with pytest.raises(c.Refused, match="Invalid decoded"):
        sink.consume(event, before, after, replay=True)
    statement = next(sql for sql, _ in db.calls if sql.startswith(batch.ROW_BATCH_PREFIX))
    assert "sum(octet_length(payload)) OVER () <= %s THEN payload ELSE NULL" in statement
    assert db.transactions == ["begin", "rollback"]


@pytest.mark.parametrize("limit", ["rows", "bytes"])
def test_native_stage_limits_still_reject_before_prefix_query(limit):
    sink, db, _budget, event, before, after = make()
    sink.manifest = replace(sink.manifest, **{
        "max_batch_rows" if limit == "rows" else "max_batch_bytes": 2,
    })
    with pytest.raises(c.Refused, match="limits"):
        sink.consume(event, before, after, replay=True)
    assert db.transactions == ["begin", "rollback"]
    assert not any(sql.startswith(batch.ROW_BATCH_PREFIX) for sql, _ in db.calls)


def test_frozen_and_batch_replay_verify_same_400_rows_with_fewer_selects(monkeypatch):
    observations = []
    original = c.row_identity
    for sink_type in (cp.PgBatchSink, batch.ReplayBatchSink):
        built, db, budget, event, before, after = make(400)
        sink = sink_type(db, built.manifest, budget)
        sink.locked = True
        identities = []

        def measured(row, records, identities=identities):
            identity = original(row, records)
            identities.append(identity)
            return identity

        monkeypatch.setattr(c, "row_identity", measured)
        saved = copy.deepcopy((db.stored, db.traces))
        sink.consume(event, before, after, replay=True)
        assert (db.stored, db.traces) == saved
        assert db.transactions == ["begin", "commit"]
        observations.append((identities, sum(
            sql == cp.ROW_SELECT or sql.startswith(batch.ROW_BATCH_PREFIX)
            for sql, _ in db.calls
        )))
    assert observations[0][0] == observations[1][0]
    assert len(observations[0][0]) == 800
    assert [count for _, count in observations] == [400, 1]
