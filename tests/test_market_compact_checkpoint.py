"""fixture_non_production. Pure coordinator/SQL-contract tests; no PG/process."""

import copy
import hashlib
import io
import json
import sys
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ops.market.compact_restore import checkpoint as m

c = m.codec


def test_packaged_codec_identity_uses_repository_without_import_path_changes():
    assert m.ROOT == Path(__file__).resolve().parents[1]
    paths = (
        m.HERE / "copy_stream.py",
        m.ROOT / "services/market_dashboard/source_trace_store.py",
        Path(m.__file__),
    )
    checksum = hashlib.sha256()
    for path in paths:
        body = path.read_bytes()
        checksum.update(len(body).to_bytes(8, "big") + body)
        assert "sys.path.insert" not in body.decode("utf-8")
    assert m.codec_identity() == checksum.hexdigest()


def line(value):
    # Fixture encoder only. COPY field decoding is exclusively NativeCopyStage.
    return str(value).encode().replace(b"\\", b"\\\\").replace(b"\t", b"\\t").replace(b"\n", b"\\n")


def fixture(tmp_path, *, batch=1):
    rows = []
    for n in range(3):
        payload = c.encode_json({"source": "fixture_non_production", "value": n})
        rows.append(("fixture", str(n), f"{n:064x}", payload))
    trace = c.TraceRecord.from_value({"source": "fixture_non_production", "financial_only": True})
    blocks = []
    by_raw = {}
    for table, values in (
        (c.TRACES, [(trace.digest, trace.byte_count, trace.payload)]),
        (c.SERIES, rows),
    ):
        block = (f"COPY public.{table} ({', '.join(c.COLUMNS[table])}) FROM stdin;\n").encode()
        for row in values:
            raw = b"\t".join(line(v) for v in row) + b"\n"
            block += raw
            by_raw[raw] = row
        blocks.append(block + b"\\.\n")
    wire = b"".join(blocks)
    archive = tmp_path / "fixture-target-only.dump"
    archive.write_bytes(b"fixture_non_production-custom-archive-marker")
    manifest = m.Manifest(
        hashlib.sha256(archive.read_bytes()).hexdigest(),
        archive.stat().st_size,
        "1" * 64,
        m.codec_identity(),
        1,
        3,
        max_batch_rows=batch,
    )
    return wire, archive, manifest, by_raw


def budget(tmp_path, **kwargs):
    return m.Budget(
        tmp_path,
        max_growth_bytes=1024**3,
        max_seconds=30,
        free_for_data=lambda: 20 * 1024**3,
        **kwargs,
    )


class MemorySink:
    """Explicit fake transaction model; NOT evidence of native PG integration."""

    def __init__(self, raw_rows, *, fail_sequence=None):
        self.cp = m.Checkpoint()
        self.raw_rows = raw_rows
        self.rows = {}
        self.trace = {}
        self.writes = 0
        self.closed = 0
        self.fail_sequence = fail_sequence
        self.identity = None

    def open(self):
        return self.cp

    def check_counts(self, cp):
        if (len(self.trace), len(self.rows)) != (cp.trace_rows, cp.series_rows):
            raise c.Refused("fixture checkpoint/table mismatch")

    def close(self):
        self.closed += 1

    def consume(self, event, before, after, *, replay):
        snapshots = copy.deepcopy((self.cp, self.rows, self.trace, self.writes))
        try:
            if isinstance(event, c.CopyBatch):
                target = self.trace if event.table == c.TRACES else self.rows
                for raw in event.raw.splitlines(keepends=True):
                    row = self.raw_rows[raw]
                    key = row[0] if event.table == c.TRACES else row[:2]
                    if replay:
                        if target.get(key) != row:
                            raise c.Refused("fixture prefix content differs")
                    else:
                        if key in target:
                            raise c.Refused("fixture duplicate")
                        target[key] = row
                        self.writes += 1
            if not replay:
                if self.cp != before:
                    raise c.Refused("fixture checkpoint CAS")
                if after.sequence == self.fail_sequence:
                    raise RuntimeError("fixture mid transaction failure")
                self.cp = after
        except Exception:
            self.cp, self.rows, self.trace, self.writes = snapshots
            raise


def run(tmp_path, wire, archive, manifest, sink, *, name="report.json", exit_code=0, guard=None):
    return m.restore_prefix(
        io.BytesIO(wire),
        sink,
        manifest,
        archive=archive,
        budget=guard or budget(tmp_path),
        child_exit=lambda: exit_code,
        evidence_path=tmp_path / name,
    )


def test_bounded_stream_is_not_a_complete_restore_receipt(tmp_path):
    wire, archive, manifest, raw = fixture(tmp_path)
    sink = MemorySink(raw)
    result = run(tmp_path, wire, archive, manifest, sink)
    assert result["stream_complete"] is True
    assert result["restore_verified"] is False
    assert result["state"] == "awaiting_ordered_verification"
    assert sink.cp.series_complete and len(sink.trace) == 1 and len(sink.rows) == 3
    assert json.loads((tmp_path / "report.json").read_text()) == result
    assert archive.read_bytes() == b"fixture_non_production-custom-archive-marker"


def test_failure_rolls_back_batch_and_resume_replays_each_saved_row(tmp_path):
    wire, archive, manifest, raw = fixture(tmp_path)
    sink = MemorySink(raw, fail_sequence=4)
    result = run(tmp_path, wire, archive, manifest, sink)
    assert result["state"] == "interrupted" and sink.cp.sequence == 3
    assert len(sink.rows) == 1 and len(sink.trace) == 1
    evidence = (tmp_path / "report.json").read_bytes()
    sink.fail_sequence = None
    result = run(tmp_path, wire, archive, manifest, sink, name="resumed.json")
    assert result["stream_complete"] and sink.writes == 4
    assert (tmp_path / "report.json").read_bytes() == evidence
    before = sink.writes
    result = run(tmp_path, wire, archive, manifest, sink, name="completed-replay.json")
    assert result["stream_complete"] and sink.writes == before


def test_prefix_hash_mismatch_never_appends_new_rows(tmp_path):
    wire, archive, manifest, raw = fixture(tmp_path)
    sink = MemorySink(raw, fail_sequence=4)
    run(tmp_path, wire, archive, manifest, sink)
    sink.cp = replace(sink.cp, prefix_sha256="f" * 64)
    before = copy.deepcopy((sink.rows, sink.trace, sink.writes))
    sink.fail_sequence = None
    result = run(tmp_path, wire, archive, manifest, sink, name="refused.json")
    assert result["state"] == "interrupted"
    assert (sink.rows, sink.trace, sink.writes) == before


def test_previously_committed_destination_mutation_is_detected(tmp_path):
    wire, archive, manifest, raw = fixture(tmp_path)
    sink = MemorySink(raw, fail_sequence=4)
    run(tmp_path, wire, archive, manifest, sink)
    key = next(iter(sink.rows))
    sink.rows[key] = (*sink.rows[key][:3], '{"changed":true}')
    sink.fail_sequence = None
    count = sink.writes
    result = run(tmp_path, wire, archive, manifest, sink, name="refused.json")
    assert result["state"] == "interrupted" and sink.writes == count


@pytest.mark.parametrize("error", ["nonzero", "truncated", "changed_archive"])
def test_source_or_child_failure_never_completes(tmp_path, error):
    wire, archive, manifest, raw = fixture(tmp_path)
    original = archive.read_bytes()
    if error == "truncated":
        wire = wire[:-3]
    if error == "changed_archive":
        archive.write_bytes(original.replace(b"marker", b"broken"))
    sink = MemorySink(raw)
    result = run(tmp_path, wire, archive, manifest, sink, exit_code=1 if error == "nonzero" else 0)
    assert result["state"] == "interrupted" and not result["restore_verified"]
    assert result["checkpoint_reload_required"] is True
    assert archive.exists()
    if error == "changed_archive":
        assert sink.writes == 0


def test_full_growth_budget_is_checked_before_work(tmp_path):
    with pytest.raises(c.Refused, match="growth budget"):
        m.Budget(
            tmp_path,
            max_growth_bytes=1024**3,
            max_seconds=30,
            free_for_data=lambda: m.MIN_FREE + 1024**3 - 1,
        )


def test_disk_and_time_checks_bound_each_batch(tmp_path):
    state = {"free": 20 * 1024**3, "now": 0}
    guard = m.Budget(
        tmp_path,
        max_growth_bytes=1024**3,
        max_seconds=30,
        free_for_data=lambda: state["free"],
        monotonic=lambda: state["now"],
    )
    guard.check()
    state["free"] -= 1024**3
    with pytest.raises(c.Refused, match="disk"):
        guard.check(1)
    state["free"] += 1024**3
    state["now"] = 30
    with pytest.raises(c.Refused, match="elapsed"):
        guard.check()


def test_failure_evidence_never_overwrites_existing_file(tmp_path):
    target = tmp_path / "existing.json"
    target.write_bytes(b"previous evidence")
    with pytest.raises(FileExistsError):
        m.write_evidence(target, {"restore_verified": False})
    assert target.read_bytes() == b"previous evidence"


@pytest.mark.parametrize(
    "changes",
    [
        dict(trace_rows=-1),
        dict(series_rows=True),
        dict(prefix_sha256="oops"),
        dict(sequence="1"),
        dict(series_complete=True),
    ],
)
def test_invalid_checkpoint_refused(changes):
    with pytest.raises(c.Refused):
        m.Checkpoint(**changes)


class Cursor:
    rowcount = 1

    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class SqlContract:
    """Records fixed SQL only; native PostgreSQL correctness is a remaining gate."""

    autocommit = True

    def __init__(self, root, identity):
        self.info = SimpleNamespace(
            host="127.0.0.1",
            port=m.PORT,
            dbname=m.DATABASE,
            transaction_status=SimpleNamespace(name="IDLE"),
        )
        self.root, self.identity = root, identity
        self.calls, self.transactions = [], []

    @contextmanager
    def transaction(self):
        self.transactions.append("begin")
        self.info.transaction_status.name = "INTRANS"
        try:
            yield
            self.transactions.append("commit")
        except Exception:
            self.transactions.append("rollback")
            raise
        finally:
            self.info.transaction_status.name = "IDLE"

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if sql.startswith("SELECT current_database"):
            return Cursor((m.DATABASE, "127.0.0.1", m.PORT, str(self.root)))
        if sql.startswith("SELECT pg_"):
            return Cursor((True,))
        if "to_regclass" in sql:
            return Cursor(("market_compact_restore.checkpoint",))
        if sql.startswith("SELECT identity"):
            return Cursor((self.identity, c.encode_json(m.asdict(m.Checkpoint()))))
        if sql.startswith("SELECT (SELECT count"):
            return Cursor((0, 0))
        return Cursor()


@pytest.mark.parametrize(
    "field,value",
    [
        ("host", "example.com"),
        ("port", 54420),
        ("port", 54431),
        ("port", 54432),
        ("dbname", "stocks_restore"),
    ],
)
def test_pg_sink_refuses_existing_or_remote_target_before_sql(tmp_path, field, value):
    _, _, manifest, _ = fixture(tmp_path)
    db = SqlContract(tmp_path, manifest.identity())
    setattr(db.info, field, value)
    with pytest.raises(c.Refused):
        m.PgBatchSink(db, manifest, budget(tmp_path)).open()
    assert db.calls == []


def test_pg_sink_resume_identity_mismatch_rolls_back_and_unlocks(tmp_path):
    _, _, manifest, _ = fixture(tmp_path)
    changed = replace(manifest, max_batch_rows=2)
    db = SqlContract(tmp_path, changed.identity())
    with pytest.raises(c.Refused, match="Resume"):
        m.PgBatchSink(db, manifest, budget(tmp_path)).open()
    assert db.transactions == ["begin", "rollback"]
    assert db.calls[-1][0] == "SELECT pg_advisory_unlock(18431,114)"
    assert not any(sql.startswith(("INSERT", "UPDATE", "CREATE")) for sql, _ in db.calls)


def test_pg_sink_end_and_checkpoint_share_one_transaction(tmp_path):
    _, _, manifest, _ = fixture(tmp_path)
    db = SqlContract(tmp_path, manifest.identity())
    sink = m.PgBatchSink(db, manifest, budget(tmp_path))
    before = sink.open()
    event = c.CopyEnd(c.TRACES, 0)
    after = before.advance(event)
    sink.consume(event, before, after, replay=False)
    assert db.transactions == ["begin", "commit", "begin", "commit"]
    update = [(sql, params) for sql, params in db.calls if sql.startswith("UPDATE")]
    assert len(update) == 1 and json.loads(update[0][1][0])["trace_complete"]
    assert all("public." not in sql for sql, _ in db.calls)
    sink.close()


def test_plan_mode_never_constructs_database_or_budget(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("fixture no execution")

    monkeypatch.setattr(m, "PgBatchSink", forbidden)
    monkeypatch.setattr(m, "Budget", forbidden)
    m.main()
    result = json.loads(capsys.readouterr().out)
    assert result["executed"] is False and result["opens_process"] is False


def test_package_cli_is_offline_and_has_no_apply_mode(monkeypatch, capsys):
    from ops.market.compact_restore import __main__ as entry

    def forbidden(*args, **kwargs):
        raise AssertionError("fixture no execution")

    monkeypatch.setattr(m, "PgBatchSink", forbidden)
    monkeypatch.setattr(m, "Budget", forbidden)
    monkeypatch.setattr(m, "verify_archive", forbidden)
    monkeypatch.setattr(sys, "argv", ["compact_restore"])
    entry.cli()
    result = json.loads(capsys.readouterr().out)
    assert result["executed"] is False and result["connects_to_database"] is False
    assert result["opens_process"] is False
    for option in ("--run", "--apply"):
        monkeypatch.setattr(sys, "argv", ["compact_restore", option])
        with pytest.raises(SystemExit) as failure:
            entry.cli()
        assert failure.value.code == 2


def test_changed_codec_is_refused_before_target_open(tmp_path):
    wire, archive, manifest, raw = fixture(tmp_path)
    sink = MemorySink(raw)
    result = run(tmp_path, wire, archive, replace(manifest, codec_sha256="f" * 64), sink)
    assert result["state"] == "interrupted" and sink.writes == 0


def test_trace_lookup_repeated_digest_uses_bounded_batch_cache():
    records = [
        c.TraceRecord.from_value({"source": "fixture_non_production", "n": n}) for n in range(3)
    ]
    by_id = {r.digest: (r.digest, r.byte_count, r.payload) for r in records}
    calls = []

    def execute(sql, params):
        assert sql == m.TRACE_SELECT
        calls.append(params[0])
        return Cursor(by_id.get(params[0]))

    lookup = m.TraceLookup(SimpleNamespace(execute=execute))
    lookup.max_entries = 2
    for _ in range(400):
        assert lookup[records[0].digest] == records[0]
    assert len(calls) == 1
    for r in records[1:]:
        assert lookup[r.digest] == r
    assert len(lookup.cache) == 2 and records[0].digest not in lookup.cache
    assert lookup.cache_bytes == sum(r.byte_count for r in records[1:])
    assert lookup[records[0].digest] == records[0]
    assert len(calls) == 4


def test_trace_lookup_serialized_byte_budget_and_misses_do_not_hide_later_insert():
    record = c.TraceRecord.from_value({"source": "fixture_non_production"})
    rows = {}
    lookup = m.TraceLookup(SimpleNamespace(execute=lambda sql, params: Cursor(rows.get(params[0]))))
    with pytest.raises(KeyError):
        lookup[record.digest]
    rows[record.digest] = (record.digest, record.byte_count, record.payload)
    lookup.max_bytes = record.byte_count - 1
    assert lookup[record.digest] == record
    assert lookup.cache_bytes == 0 and not lookup.cache


def test_actual_pg_sink_native_batch_and_checkpoint_use_same_transaction(tmp_path, monkeypatch):
    _, _, manifest, raw = fixture(tmp_path)
    value = next(row for row in raw.values() if len(row) == 3)
    db = SqlContract(tmp_path, manifest.identity())

    class BatchCursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def executemany(self, sql, rows):
            assert db.info.transaction_status.name == "INTRANS"
            db.calls.append((sql, list(rows)))

    class NativeStageFixture:
        def __init__(self, view, **kwargs):
            assert view.autocommit is False
            assert view.info.transaction_status.name == "INTRANS"

        def create(self):
            pass

        def decode(self, event):
            return [value]

    monkeypatch.setattr(c, "NativeCopyStage", NativeStageFixture)
    db.cursor = BatchCursor
    sink = m.PgBatchSink(db, manifest, budget(tmp_path))
    before = sink.open()
    event = c.CopyBatch(c.TRACES, 1, b"fixture_non_production\n")
    sink.consume(event, before, before.advance(event), replay=False)
    assert db.transactions == ["begin", "commit", "begin", "commit"]
    mutations = [(sql, values) for sql, values in db.calls if sql.startswith(("INSERT", "UPDATE"))]
    assert len(mutations) == 2
    assert mutations[0][1] == [value] and mutations[1][0].startswith("UPDATE")
    sink.close()
