"""fixture_non_production: keyset count contracts; no PostgreSQL or processes."""

from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest

from ops.market.compact_restore import resume_restore as m


class Budget:
    def __init__(self, fail_at=None):
        self.calls = 0
        self.fail_at = fail_at

    def check(self):
        self.calls += 1
        if self.calls == self.fail_at:
            raise m.cp.codec.Refused("Restore elapsed-time budget exhausted")
        return 17000


class Database:
    def __init__(self, traces, series):
        self.traces, self.series = traces, series
        self.calls, self.timeouts = [], []
        self.transactions = []
        self.overrides = {}
        self.failure = None
        self.info = SimpleNamespace(transaction_status=SimpleNamespace(name="INTRANS"))

    @contextmanager
    def transaction(self):
        assert self.info.transaction_status.name == "IDLE"
        self.info.transaction_status.name = "INTRANS"
        self.transactions.append("begin")
        try:
            yield
        except BaseException:
            self.transactions.append("rollback")
            raise
        else:
            self.transactions.append("commit")
        finally:
            self.info.transaction_status.name = "IDLE"

    def execute(self, statement, parameters=None):
        self.calls.append((statement, parameters))
        if statement == "SELECT set_config('statement_timeout',%s,true)":
            self.timeouts.append(int(parameters[0]))
            assert 0 < int(parameters[0]) <= 30000
            return None
        if statement == "SELECT set_config('max_parallel_workers_per_gather','0',true)":
            return None
        assert "LIMIT %s" in statement and "OFFSET" not in statement
        assert "COUNT(" not in statement.upper() and "payload" not in statement
        assert parameters[-1] == m.COUNT_BATCH_ROWS
        if self.failure:
            raise self.failure
        if statement.startswith(m.TRACE_COUNT_PAGE):
            kind, rows = "traces", self.traces
            if len(parameters) > 1:
                rows = [row for row in rows if row[0] > parameters[0]]
        elif statement.startswith(m.SERIES_COUNT_PAGE):
            kind, rows = "series", self.series
            if len(parameters) > 1:
                rows = [row for row in rows if tuple(row[:2]) > parameters[:2]]
        else:
            raise AssertionError("Only bounded count reads are permitted")
        rows = next(self.overrides[kind]) if kind in self.overrides else rows[:parameters[-1]]
        return SimpleNamespace(fetchmany=lambda maximum: rows[:maximum])


def build(monkeypatch, *, traces=None, series=None, trace_rows=2, series_rows=5, fail_at=None):
    monkeypatch.setattr(m, "COUNT_BATCH_ROWS", 2)
    traces = traces if traces is not None else [
        ("1" * 64, True), ("2" * 64, False), ("3" * 64, True),
    ]
    series = series if series is not None else [("fixture", str(n)) for n in range(5)]
    db, budget = Database(traces, series), Budget(fail_at)
    report = {"restore_verified": False, "processes": []}
    manifest = m.cp.Manifest("a" * 64, 1, "b" * 64, "c" * 64, 3, 7)
    sink = m.ResumeSink(db, manifest, budget, report)
    sink.locked = True  # Dedicated transaction/ownership fixture, not a real lock.
    state = m.cp.Checkpoint(trace_rows=trace_rows, series_rows=series_rows, trace_complete=True)
    return sink, db, budget, report, state


def test_exact_count_batches_do_not_claim_commit_prefix_or_source_verification(monkeypatch):
    sink, db, _, report, state = build(monkeypatch)
    progress = deepcopy(report["row_progress"])
    sink.check_counts(state)
    assert report["checkpoint_counts"] == {
        "method": "exact-pk-keyset-v1", "state": "matched_checkpoint",
        "trace_rows": 2, "series_rows": 5, "trace_batches": 2, "series_batches": 3,
        "source_references_verified": False, "ordered_hash_verified": False,
    }
    assert report["row_progress"] == progress and report["processes"] == []
    assert report["restore_verified"] is False and sink.checkpoint is None
    assert db.timeouts and set(db.timeouts) == {17000}
    assert all(sql.startswith("SELECT") for sql, _ in db.calls)


def test_empty_prefix_requires_both_empty_terminal_pages(monkeypatch):
    sink, db, _, report, state = build(
        monkeypatch, traces=[], series=[], trace_rows=0, series_rows=0,
    )
    sink.check_counts(state)
    assert report["checkpoint_counts"]["state"] == "matched_checkpoint"
    assert sum("LIMIT %s" in sql for sql, _ in db.calls) == 2


@pytest.mark.parametrize("trace_rows,series_rows", [(1, 5), (3, 5), (2, 4), (2, 6), (2, 8)])
def test_missing_excess_or_wrong_checkpoint_counts_are_refused(
        monkeypatch, trace_rows, series_rows):
    sink, _, _, report, state = build(
        monkeypatch, trace_rows=trace_rows, series_rows=series_rows,
    )
    with pytest.raises(m.cp.codec.Refused, match="count differs"):
        sink.check_counts(state)
    assert report["failure_diagnostic"]["code"] == "checkpoint_count_differs"
    assert report["row_progress"]["committed"] is None
    assert report.get("checkpoint_counts", {}).get("state") != "matched_checkpoint"


@pytest.mark.parametrize("kind,pages", [
    ("traces", [[("1" * 64, True), ("1" * 64, True)]]),
    ("traces", [[("1" * 64, True)], [("1" * 64, True)]]),
    ("series", [[("fixture", "0"), ("fixture", "0")]]),
    ("series", [[("fixture", "0"), ("fixture", "1")], [("fixture", "1")]]),
    ("series", [[("fixture", "1"), ("fixture", "0")]]),
])
def test_duplicate_or_nonadvancing_keys_fail_without_repeating(monkeypatch, kind, pages):
    sink, db, _, report, state = build(monkeypatch)
    db.overrides[kind] = iter(pages)
    with pytest.raises(m.local.Refused, match="key_not_advancing"):
        sink.check_counts(state)
    assert report["checkpoint_counts"]["state"] == "interrupted"
    assert report["row_progress"]["verified_prefix"]["series_rows"] == 0


@pytest.mark.parametrize("kind,rows", [
    ("traces", [("bad-digest", True)]),
    ("traces", [("1" * 64, 1)]),
    ("series", [("", "1")]),
    ("series", [("fixture", "1", "unexpected")]),
    ("series", [("fixture", "0"), ("fixture", "1"), ("fixture", "2")]),
])
def test_malformed_or_oversized_pages_are_refused(monkeypatch, kind, rows):
    sink, db, _, report, state = build(monkeypatch)
    db.overrides[kind] = iter([rows])
    with pytest.raises(m.local.Refused, match="page_invalid"):
        sink.check_counts(state)
    assert report["checkpoint_counts"]["state"] == "interrupted"


@pytest.mark.parametrize("fail_at", [1, 4, 12])
def test_overall_budget_remains_enforced_during_count(monkeypatch, fail_at):
    sink, _, _, report, state = build(monkeypatch, fail_at=fail_at)
    with pytest.raises(m.cp.codec.Refused, match="elapsed-time"):
        sink.check_counts(state)
    assert report.get("checkpoint_counts", {}).get("state") != "matched_checkpoint"
    assert report["row_progress"]["committed"] is None


def test_sql_timeout_is_preserved_and_never_reports_a_count_match(monkeypatch):
    sink, db, _, report, state = build(monkeypatch)
    class QueryCanceled(Exception):
        sqlstate = "57014"
    db.failure = QueryCanceled("fixture-sensitive-sql")
    with pytest.raises(QueryCanceled):
        sink.check_counts(state)
    assert report["failure_diagnostic"]["sqlstate"] == "57014"
    assert "fixture-sensitive-sql" not in repr(report)
    assert report["checkpoint_counts"]["state"] == "interrupted"


@pytest.mark.parametrize("missing", ["lock", "transaction"])
def test_foreign_or_failed_connections_are_refused_before_sql(monkeypatch, missing):
    sink, db, _, _, state = build(monkeypatch)
    if missing == "lock":
        sink.locked = False
    else:
        db.info.transaction_status.name = "INERROR"
    with pytest.raises(m.local.Refused, match="owned_transaction"):
        sink.check_counts(state)
    assert db.calls == []


def test_final_copy_count_opens_only_an_owned_read_transaction(monkeypatch):
    sink, db, _, report, state = build(monkeypatch)
    db.info.transaction_status.name = "IDLE"
    sink.check_counts(state)
    assert db.transactions == ["begin", "commit"]
    assert db.info.transaction_status.name == "IDLE"
    assert report["checkpoint_counts"]["state"] == "matched_checkpoint"
    assert report["row_progress"]["committed"] is None


def test_quoted_and_korean_keys_remain_parameters(monkeypatch):
    keys = [("a'; --", "1"), ("가", "1"), ("나", "1")]
    sink, db, _, report, state = build(monkeypatch, series=keys, series_rows=3)
    sink.check_counts(state)
    pages = [(sql, params) for sql, params in db.calls if sql.startswith(m.SERIES_COUNT_PAGE)]
    assert pages[1][1] == ("가", "1", 2)
    assert all("가" not in sql and "a'; --" not in sql for sql, _ in pages)
    assert report["checkpoint_counts"]["series_rows"] == 3


def test_matching_counts_do_not_hide_a_missing_embedded_source_reference(monkeypatch):
    from test_market_compact_replay_batch import make

    built, replay_db, budget, event, before, after = make()
    other = m.cp.codec.TraceRecord.from_value({"source": "other_fixture_non_production"})
    replay_db.traces = {other.digest: (other.digest, other.byte_count, other.payload)}
    sink, count_db, _, report, state = build(
        monkeypatch, traces=[(other.digest, True)],
        series=sorted(tuple(row[:2]) for row in replay_db.stored), trace_rows=1, series_rows=3,
    )
    sink.check_counts(state)
    assert report["checkpoint_counts"]["state"] == "matched_checkpoint"
    sink.db = m.ExistingConnection(replay_db)
    sink.manifest, sink.budget = built.manifest, budget
    with pytest.raises(m.cp.codec.Refused, match="source trace"):
        sink.consume(event, before, after, replay=True)
    assert report["checkpoint_counts"]["source_references_verified"] is False
    assert report["row_progress"]["verified_prefix"]["series_rows"] == 0
    assert replay_db.transactions == ["begin", "rollback"]
