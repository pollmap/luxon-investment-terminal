"""fixture_non_production: real SQLite transactions, synthetic archive receipts.

No PostgreSQL/production process, connection, restore or migration permission.
"""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from ops.market.compact_restore import delta_archive as d

c = d.codec


def encoded(value):
    return c.encode_json(value).encode()


def h(value):
    return hashlib.sha256(value).hexdigest()


def trace(text):
    record = c.TraceRecord.from_value({"source": "fixture", "note": text})
    return [record.digest, record.byte_count, record.payload]


def row(key, value, record, digest="1" * 64):
    return [
        key,
        "2026-10-10",
        digest,
        c.encode_json(
            {
                "value": value,
                "source_trace": json.loads(record[2]),
            }
        ),
    ]


def snapshot(rows, traces, *, compact=False):
    rows = sorted(rows, key=lambda r: (r[0].encode(), r[1].encode()))
    traces = sorted(traces)
    records = {r[0]: c.TraceRecord(*r) for r in traces}
    if compact:
        rows, generated = c.compact_rows(rows, records)
        records.update(generated)
    return d.Snapshot(lambda: iter(rows), lambda: iter(traces), records)


def report(view, moment):
    rows, traces = list(view.rows()), list(view.traces())
    keys = [r[0] for r in traces]
    checksum = hashlib.sha256()
    for item in traces:
        checksum.update(d.frame(item))
    return {
        "target": "source",
        "protocol": d.ordered.PROTOCOL,
        "rows": len(rows),
        "series": len({r[0] for r in rows}),
        "logical_sha256": c.ordered_logical_hash(rows, view.records)[1],
        "original_traces": len(traces),
        "original_traces_sha256": checksum.hexdigest(),
        "original_trace_digests": keys,
        "original_trace_inventory_sha256": h(encoded(keys)),
        "indexes_valid": True,
        "restore_verified": False,
        "fixture_non_production": True,
        "source_catalogue": {
            "snapshot": "1-2-3",
            "catalogue_sha256": "a" * 64,
            "snapshot_created_at": moment,
        },
        "checked_at": moment,
    }


def write_evidence(tmp_path, base, current):
    archive = tmp_path / "dated.dump"
    archive.write_bytes(b"PGDMP-fixture-not-a-real-backup")
    source = report(base, "2026-10-10T01:00:00+00:00")
    target = report(current, "2026-10-10T02:00:00+00:00")
    receipt = {
        "received": True,
        "published": True,
        "exit_waited": True,
        "child_stopped": True,
        "threads_stopped": True,
        "exit_code": 0,
        "bytes": archive.stat().st_size,
        "sha256": h(archive.read_bytes()),
    }
    paths, hashes = {}, {}
    for name, value in (
        ("base_source", source),
        ("current_source", target),
        ("archive_receipt", receipt),
    ):
        paths[name] = tmp_path / (name + ".json")
        paths[name].write_bytes(encoded(value))
        hashes[name] = h(paths[name].read_bytes())
    result = {
        "state": "verified",
        "executed": True,
        "data_equal": True,
        "cluster_stopped": True,
        "originals_preserved": True,
        "archive_unchanged": True,
        "input_paths_modified": False,
        "production_contacted": False,
        "fixture_non_production": True,
        "fixture_restore_verified": True,
        "restore_verified": False,
        "production_proof": False,
        "current_fence_valid": False,
        "proof_scope": "dated_archive_independent_restore_only",
        "source_report_sha256": hashes["base_source"],
        "archive_receipt_sha256": hashes["archive_receipt"],
        "archive_sha256": receipt["sha256"],
        "original_trace_inventory_sha256": source["original_trace_inventory_sha256"],
        "verification": {**source, "target": "restored"},
    }
    paths["base_result"] = tmp_path / "RESULT.json"
    paths["base_result"].write_bytes(encoded(result))
    hashes["base_result"] = h(paths["base_result"].read_bytes())
    return d.Evidence(**paths, archive=archive, expected_sha256=hashes)


class Records:
    def __init__(self, db):
        self.db = db

    def __getitem__(self, key):
        record = self.db.execute(
            "SELECT digest,n,body FROM traces WHERE digest=?", (key,)
        ).fetchone()
        if record is None:
            raise KeyError(key)
        return c.validate_trace(record)

    def __contains__(self, key):
        try:
            self[key]
            return True
        except KeyError:
            return False


class SQLiteSink:
    """Real isolated transaction adapter; no SQL from archive is ever executed."""

    def __init__(self, path, base):
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.execute("CREATE TABLE rows(s TEXT,t TEXT,d TEXT,p TEXT,PRIMARY KEY(s,t))")
        self.db.execute(
            "CREATE TABLE traces(digest TEXT PRIMARY KEY,n INTEGER,body TEXT,original INTEGER)"
        )
        originals = {r[0] for r in base.traces()}
        self.db.executemany("INSERT INTO rows VALUES(?,?,?,?)", base.rows())
        self.db.executemany(
            "INSERT INTO traces VALUES(?,?,?,?)",
            [
                (key, record.byte_count, record.payload, key in originals)
                for key, record in base.records.items()
            ],
        )
        self.log = []
        self.fail_after_change = False

    @contextmanager
    def transaction(self):
        self.log.append("begin")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.db.execute("ROLLBACK")
            self.log.append("rollback")
            raise
        else:
            self.db.execute("COMMIT")
            self.log.append("commit")

    def snapshot(self):
        return d.Snapshot(
            lambda: iter(
                self.db.execute(
                    "SELECT s,t,d,p FROM rows ORDER BY s COLLATE BINARY,t COLLATE BINARY"
                )
            ),
            lambda: iter(
                self.db.execute("SELECT digest,n,body FROM traces WHERE original=1 ORDER BY digest")
            ),
            Records(self.db),
        )

    def add_original_trace(self, value):
        old = self.db.execute(
            "SELECT digest,n,body FROM traces WHERE digest=?", value[:1]
        ).fetchone()
        if old is not None:
            d.require(list(old) == value, "sink_trace_conflict")
            self.db.execute("UPDATE traces SET original=1 WHERE digest=?", value[:1])
        else:
            self.db.execute("INSERT INTO traces VALUES(?,?,?,1)", value)

    def change_row(self, before, after):
        key = (after or before)[:2]
        old = self.db.execute("SELECT s,t,d,p FROM rows WHERE s=? AND t=?", key).fetchone()
        d.require(
            old is None
            if before is None
            else old is not None
            and c.row_identity(old, Records(self.db)) == c.row_identity(before, {}),
            "sink_before_mismatch",
        )
        if after is None:
            self.db.execute("DELETE FROM rows WHERE s=? AND t=?", key)
        elif before is None:
            self.db.execute("INSERT INTO rows VALUES(?,?,?,?)", after)
        else:
            self.db.execute("UPDATE rows SET d=?,p=? WHERE s=? AND t=?", (*after[2:], *key))
        if self.fail_after_change:
            raise d.Refused("fixture_interruption")

    def state(self):
        return (
            self.db.execute("SELECT * FROM rows ORDER BY s,t").fetchall(),
            self.db.execute("SELECT * FROM traces ORDER BY digest").fetchall(),
        )


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setattr(d.files, "_disk_free", lambda _: 10 * 1024**3)
    t1, t2, t3 = (
        trace("한글\t개행\n\\N"),
        trace("financial-only original"),
        trace("financial-only new"),
    )
    base = snapshot(
        [row("가격:가", 1, t1), row("가격:나", None, t1), row("z", 3, t1)], [t1, t2], compact=True
    )
    current = snapshot(
        [row("가격:가", 2, t1), row("가격:다", 0, t1), row("z", 3, t1)], [t1, t2, t3]
    )
    evidence = write_evidence(tmp_path, base, current)
    path = tmp_path / "changes.jsonl"
    return evidence, base, current, path


def built(case):
    evidence, base, current, path = case
    result = d.build(path, evidence, base, current)
    return result, path


def rewrite_archive(path, transform):
    records = [json.loads(line) for line in path.read_bytes().splitlines()]
    records = transform(records)
    content = b""
    for item in records:
        if item["kind"] == "complete":
            item["prefix_sha256"] = h(content)
        content += encoded(item) + b"\n"
    path.write_bytes(content)
    return h(content)


def test_actual_sqlite_apply_full_identity_delete_correction_and_financial_only_trace(
    case, tmp_path
):
    evidence, base, current, _ = case
    original_files = {
        p: p.read_bytes() for p in (evidence.archive, evidence.base_result, evidence.base_source)
    }
    result, path = built(case)
    assert result["inserted"] == result["updated"] == result["deleted"] == 1
    assert result["trace_additions"] == 1
    assert result["archive_sha256"] == h(path.read_bytes())
    sink = SQLiteSink(tmp_path / "independent.sqlite", base)
    try:
        applied = d.apply(path, result["archive_sha256"], evidence, sink)
        assert applied["data_equal"] is True
        assert applied["production_proof"] is applied["current_fence_valid"] is False
        assert applied["permission_to_execute"] is applied["indexes_verified"] is False
        assert sink.log == ["begin", "commit"]
        assert report(sink.snapshot(), "2026-10-10T02:00:00+00:00") == report(
            current, "2026-10-10T02:00:00+00:00"
        )
        assert all(p.read_bytes() == value for p, value in original_files.items())
    finally:
        sink.db.close()


def test_provenance_only_change_is_not_hidden_by_unchanged_digest(tmp_path):
    t1, t2 = trace("source receipt before"), trace("source receipt after")
    old = snapshot([row("005930", 0, t1)], [t1])
    new = snapshot([row("005930", 0, t2)], [t1, t2])
    evidence = write_evidence(tmp_path, old, new)
    result = d.build(tmp_path / "delta", evidence, old, new)
    assert result["updated"] == 1 and result["trace_additions"] == 1


@pytest.mark.parametrize("kind", ["deleted", "bad_bytes", "duplicate", "unordered"])
def test_bad_or_removed_original_trace_rejected(case, kind):
    evidence, old, new, path = case
    traces = list(new.traces())
    if kind == "deleted":
        traces = [r for r in traces if r[0] != next(old.traces())[0]]
    elif kind == "bad_bytes":
        traces[0] = [*traces[0][:2], "{}"]
    elif kind == "duplicate":
        traces.insert(0, traces[0])
    else:
        traces.reverse()
    bad = replace(new, traces=lambda: iter(traces))
    with pytest.raises(d.Refused):
        d.build(path, evidence, old, bad)
    assert not path.exists()


@pytest.mark.parametrize("kind", ["duplicate", "unordered", "changed", "missing"])
def test_input_rows_must_match_whole_verified_report(case, kind):
    evidence, old, new, path = case
    rows = list(new.rows())
    if kind == "duplicate":
        rows.insert(0, rows[0])
    elif kind == "unordered":
        rows.reverse()
    elif kind == "changed":
        rows[0] = [*rows[0][:3], '{"changed":true}']
    else:
        rows.pop()
    with pytest.raises(d.Refused):
        d.build(path, evidence, old, replace(new, rows=lambda: iter(rows)))
    assert not path.exists()


@pytest.mark.parametrize(
    "field",
    ["state", "data_equal", "cluster_stopped", "archive_unchanged", "fixture_restore_verified"],
)
def test_fake_or_incomplete_base_success_marker_rejected(case, field):
    evidence, old, new, path = case
    value = json.loads(evidence.base_result.read_bytes())
    value[field] = "interrupted" if field == "state" else False
    evidence.base_result.write_bytes(encoded(value))
    hashes = {**evidence.expected_sha256, "base_result": h(evidence.base_result.read_bytes())}
    with pytest.raises(d.Refused, match="completed_independent_restore"):
        d.build(path, replace(evidence, expected_sha256=hashes), old, new)
    assert not path.exists()


def test_receipt_hash_and_real_archive_bytes_are_verified(case):
    evidence, old, new, path = case
    evidence.archive.write_bytes(b"PGDMP-mutated")
    with pytest.raises(d.Refused, match="archive_changed"):
        d.build(path, evidence, old, new)
    assert not path.exists()


def test_changed_metadata_is_not_accepted(case):
    evidence, old, new, path = case
    evidence.current_source.write_bytes(b"{}")
    with pytest.raises(d.Refused, match="metadata_changed"):
        d.build(path, evidence, old, new)


@pytest.mark.parametrize(
    "fault", ["sink", "baseline", "truncated", "unknown", "duplicate", "wrong_key", "after_tamper"]
)
def test_independent_apply_rollback_preserves_all_base_rows_and_traces(case, tmp_path, fault):
    evidence, old, _, _ = case
    result, path = built(case)
    sink = SQLiteSink(tmp_path / "restore.sqlite", old)
    expected_sha = result["archive_sha256"]
    if fault == "sink":
        sink.fail_after_change = True
    elif fault == "baseline":
        sink.db.execute("UPDATE rows SET p='{}' WHERE s='z'")
    elif fault == "truncated":
        path.write_bytes(b"\n".join(path.read_bytes().splitlines()[:-1]) + b"\n")
        expected_sha = h(path.read_bytes())
    else:

        def mutate(records):
            index = next(
                i for i, r in enumerate(records) if r["kind"] == "row" and r["after"] is not None
            )
            if fault == "unknown":
                records.insert(index + 1, {"kind": "sql", "text": "DROP TABLE rows"})
            elif fault == "duplicate":
                records.insert(index + 1, records[index])
            elif fault == "wrong_key":
                record = next(
                    r for r in records if r["kind"] == "row" and r["before"] and r["after"]
                )
                record["after"][0] = "other"
            else:
                records[index]["after"][3] = '{"value":"tampered"}'
            return records

        expected_sha = rewrite_archive(path, mutate)
    before = sink.state()
    try:
        with pytest.raises(d.Refused):
            d.apply(path, expected_sha, evidence, sink)
        assert sink.state() == before
        assert sink.log == ["begin", "rollback"]
    finally:
        sink.db.close()


def test_wrong_delta_sha_prevents_transaction(case, tmp_path):
    evidence, old, _, _ = case
    _, path = built(case)
    sink = SQLiteSink(tmp_path / "restore.sqlite", old)
    try:
        with pytest.raises(d.Refused, match="archive_changed"):
            d.apply(path, "0" * 64, evidence, sink)
        assert not sink.log
    finally:
        sink.db.close()


@pytest.mark.parametrize("limit", ["max_archive_bytes", "max_record_bytes", "max_metadata_bytes"])
def test_resource_limits_never_publish_partial(case, limit):
    evidence, old, new, path = case
    with pytest.raises(d.Refused):
        d.build(path, evidence, old, new, limits=replace(d.Limits(), **{limit: 100}))
    assert not path.exists()


def test_interruption_preserves_partial_and_existing_files(case):
    evidence, old, new, path = case
    existing = evidence.base_result.read_bytes()

    def interrupted_rows():
        yield next(new.rows())
        raise d.Refused("fixture_interruption")

    with pytest.raises(d.Refused, match="fixture_interruption"):
        d.build(path, evidence, old, replace(new, rows=interrupted_rows))
    partials = list(path.parent.glob(path.name + ".*.partial"))
    assert len(partials) == 1 and partials[0].stat().st_size > 0
    assert not path.exists() and evidence.base_result.read_bytes() == existing


def test_existing_destination_not_overwritten(case):
    evidence, old, new, path = case
    path.write_bytes(b"preserve")
    with pytest.raises(d.Refused, match="destination_exists"):
        d.build(path, evidence, old, new)
    assert path.read_bytes() == b"preserve"


def test_zero_delta_still_scans_full_inputs_and_keeps_no_production_permission(tmp_path):
    t = trace("original")
    base = snapshot([row(str(i).zfill(5), i, t) for i in range(1000)], [t])
    counts = [0, 0]

    def counted(index):
        for item in base.rows():
            counts[index] += 1
            assert abs(counts[0] - counts[1]) <= 1
            yield item

    old, current = replace(base, rows=lambda: counted(0)), replace(base, rows=lambda: counted(1))
    evidence = write_evidence(tmp_path, base, base)
    result = d.build(tmp_path / "zero", evidence, old, current)
    assert counts == [1000, 1000]
    assert (
        result["bytes"] < 10000
        and result["updated"] == result["deleted"] == result["inserted"] == 0
    )
    assert result["production_proof"] is result["permission_to_execute"] is False


def test_verified_current_report_still_cannot_remove_financial_original_trace(tmp_path):
    used, financial = trace("used"), trace("unreferenced financial")
    old = snapshot([row("a", 1, used)], [used, financial])
    new = snapshot([row("a", 1, used)], [used])
    evidence = write_evidence(tmp_path, old, new)
    with pytest.raises(d.Refused, match="original_trace_deleted"):
        d.build(tmp_path / "changes", evidence, old, new)


def test_sink_existing_compact_trace_conflict_rolls_back(case, tmp_path):
    evidence, old, new, _ = case
    result, path = built(case)
    added = next(r for r in new.traces() if r[0] not in old.records)
    sink = SQLiteSink(tmp_path / "restore.sqlite", old)
    sink.db.execute("INSERT INTO traces VALUES(?,?,?,0)", (added[0], 2, "{}"))
    before = sink.state()
    try:
        with pytest.raises(d.Refused, match="sink_trace_conflict"):
            d.apply(path, result["archive_sha256"], evidence, sink)
        assert sink.state() == before and sink.log[-1] == "rollback"
    finally:
        sink.db.close()


def test_existing_equal_compact_trace_is_preserved_and_becomes_original(case, tmp_path):
    evidence, old, new, _ = case
    result, path = built(case)
    added = next(r for r in new.traces() if r[0] not in old.records)
    sink = SQLiteSink(tmp_path / "restore.sqlite", old)
    sink.db.execute("INSERT INTO traces VALUES(?,?,?,0)", added)
    try:
        d.apply(path, result["archive_sha256"], evidence, sink)
        actual = sink.db.execute(
            "SELECT digest,n,body,original FROM traces WHERE digest=?", added[:1]
        ).fetchone()
        assert list(actual) == [*added, 1]
    finally:
        sink.db.close()


@pytest.mark.parametrize("malformed", ["duplicate_json_key", "after_footer", "footer_bool_count"])
def test_malformed_archive_rejected_with_expected_file_hash(case, tmp_path, malformed):
    evidence, old, _, _ = case
    _, path = built(case)
    raw = path.read_bytes()
    if malformed == "duplicate_json_key":
        raw = raw.replace(b'{"kind":"header",', b'{"kind":"header","kind":"header",', 1)
        path.write_bytes(raw)
    elif malformed == "after_footer":
        path.write_bytes(raw + b"{}\n")
    else:

        def mutate(records):
            records[-1]["counts"]["inserted"] = True
            return records

        rewrite_archive(path, mutate)
    sink = SQLiteSink(tmp_path / "restore.sqlite", old)
    before = sink.state()
    try:
        with pytest.raises(d.Refused):
            d.apply(path, h(path.read_bytes()), evidence, sink)
        assert sink.state() == before and sink.log[-1] == "rollback"
    finally:
        sink.db.close()


def test_disk_floor_and_time_budget_preserve_incomplete_artifact(case, monkeypatch):
    evidence, old, new, path = case
    monkeypatch.setattr(d.files, "_disk_free", lambda _: 1024**3)
    with pytest.raises(d.Refused, match="disk_floor"):
        d.build(path, evidence, old, new)
    assert not path.exists()
    guard = d.Guard(replace(d.Limits(), max_seconds=1), lambda: None)
    monkeypatch.setattr(d.time, "monotonic", lambda: guard.started + 2)
    with pytest.raises(d.Refused, match="time_limit"):
        guard.check()


def test_unchanged_rows_do_not_get_extra_json_expansion(tmp_path, monkeypatch):
    t = trace("original")
    view = snapshot([row(str(i), i, t) for i in range(20)], [t])
    evidence = write_evidence(tmp_path, view, view)

    def rejected(_):
        raise AssertionError("unchanged frame need not be expanded again")

    monkeypatch.setattr(d, "inline_row", rejected)
    result = d.build(tmp_path / "changes", evidence, view, view)
    assert result["updated"] == result["inserted"] == result["deleted"] == 0


def test_input_change_after_apply_begins_rolls_back(case, tmp_path):
    evidence, old, _, _ = case
    result, path = built(case)
    sink = SQLiteSink(tmp_path / "restore.sqlite", old)
    original = sink.change_row

    def change_then_damage_receipt(before, after):
        original(before, after)
        evidence.current_source.write_bytes(b"{}")

    sink.change_row = change_then_damage_receipt
    before = sink.state()
    try:
        with pytest.raises(d.Refused, match="metadata_changed"):
            d.apply(path, result["archive_sha256"], evidence, sink)
        assert sink.state() == before and sink.log[-1] == "rollback"
    finally:
        sink.db.close()


@pytest.mark.parametrize("target", ["source", "restored"])
def test_pg_reader_reuses_fixed_c_order_and_all_originals(monkeypatch, target):
    calls = []
    db = SimpleNamespace(
        info=SimpleNamespace(encoding="utf-8", transaction_status=SimpleNamespace(name="INTRANS")),
        execute=lambda sql: SimpleNamespace(fetchone=lambda: ("on", "repeatable read")),
    )
    t = trace("financial-only")
    r = row("005930", 1, t)

    def cursor(database, name, sql, params, check):
        assert database is db
        check()
        calls.append((name, sql, params))
        if name.startswith("delta_series"):
            assert sql.startswith("WITH RECURSIVE") and 'COLLATE "C"' in sql
            yield ("005930",)
        elif name.startswith("delta_rows"):
            assert (
                params == (d.DEFAULT_LIMITS.max_record_bytes, "005930")
                and 'ORDER BY time COLLATE "C"' in sql
            )
            assert "CASE WHEN octet_length(payload)<=%s" in sql
            yield r
        else:
            assert params == (d.DEFAULT_LIMITS.max_record_bytes,)
            assert "CASE WHEN octet_length(payload)<=%s" in sql
            assert (" WHERE original" in sql) == (target == "restored")
            assert 'ORDER BY digest COLLATE "C"' in sql
            yield t

    monkeypatch.setattr(d.ordered, "_cursor_rows", cursor)

    class Cursor:
        def __init__(self, name):
            self.name = name

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def execute(self, sql, params):
            self.rows = cursor(db, self.name, sql, params, lambda: None)

        def fetchmany(self, count):
            assert count == 1
            value = next(self.rows, None)
            return [] if value is None else [value]

    db.cursor = lambda name: Cursor(name)
    reader = d.pg_readers(db, target, check=lambda: None)
    assert list(reader.rows()) == [r]
    assert list(reader.traces()) == [t]
    assert len(calls) == 3
    db.info.transaction_status.name = "IDLE"
    with pytest.raises(d.Refused, match="snapshot_ended"):
        list(reader.rows())


@pytest.mark.parametrize("settings", [("off", "repeatable read"), ("on", "read committed")])
def test_pg_reader_refuses_live_changing_or_write_transaction(settings):
    db = SimpleNamespace(
        info=SimpleNamespace(encoding="utf8", transaction_status=SimpleNamespace(name="INTRANS")),
        execute=lambda sql: SimpleNamespace(fetchone=lambda: settings),
    )
    with pytest.raises(d.Refused, match="readonly_repeatable"):
        d.pg_readers(db, "source", check=lambda: None)
    with pytest.raises(d.Refused, match="fixed_read_target"):
        d.pg_readers(db, "public.market_financial_facts", check=lambda: None)


def test_disk_minimum_cannot_be_relaxed(case, monkeypatch):
    assert d.DEFAULT_LIMITS.min_free_bytes == d.checkpoint.MIN_FREE == 6 * 1024**3
    with pytest.raises(d.Refused, match="disk_floor_below_6gib"):
        d.Limits(min_free_bytes=d.checkpoint.MIN_FREE - 1)
    evidence, old, new, path = case
    monkeypatch.setattr(d.files, "_disk_free", lambda _: d.checkpoint.MIN_FREE)
    with pytest.raises(d.Refused, match="disk_floor"):
        d.build(path, evidence, old, new)
    assert not path.exists()


def test_repeated_trace_expansion_is_bounded_before_frozen_codec(monkeypatch):
    t = trace("가" * 640)
    record = c.TraceRecord(*t)
    value = {"values": [{"source_trace": {c.REF_KEY: record.digest}} for _ in range(20)]}
    raw = ["005930", "2026-10-10", "1" * 64, c.encode_json({c.FORMAT_KEY: c.FORMAT, "row": value})]
    assert len(raw[3].encode()) < 4096 and record.byte_count < 4096

    def no_expand(*_):
        raise AssertionError("oversized logical row reached frozen expansion")

    monkeypatch.setattr(c, "row_identity", no_expand)
    with pytest.raises(d.Refused, match="logical_row_size_limit"):
        d.identity(raw, {record.digest: record}, 4096)


def test_expansion_preflight_preserves_exact_canonical_bytes_and_checks_forged_count():
    t = trace('가\\\n"' * 20)
    original = row("005930", 0, t)
    compact, records = c.compact_rows([original], {t[0]: c.TraceRecord(*t)})
    expected = c.row_identity(compact[0], records)
    assert d.identity(compact[0], records, len(expected)) == expected
    with pytest.raises(d.Refused, match="logical_row_size_limit"):
        d.identity(compact[0], records, len(expected) - 1)
    record = records[t[0]]
    with pytest.raises(d.Refused, match="invalid_row_identity"):
        d.identity(compact[0], {t[0]: replace(record, byte_count=1)})


def test_lookup_refuses_oversized_sql_trace_before_decoding(monkeypatch):
    digest = "a" * 64
    calls = []

    def execute(sql, params):
        calls.append((sql, params))
        return SimpleNamespace(fetchone=lambda: (digest, 10000, None))

    def no_decode(_):
        raise AssertionError("oversized trace reached JSON validation")

    monkeypatch.setattr(d, "validated_trace", no_decode)
    lookup = d.LimitedLookup(
        SimpleNamespace(execute=execute), "public.market_source_traces", lambda: None, 4096
    )
    with pytest.raises(d.Refused, match="trace_size_limit"):
        lookup[digest]
    assert "CASE WHEN octet_length(payload)<=%s" in calls[0][0]
    assert calls[0][1] == (4096, digest)


def test_older_snapshot_cannot_be_promoted_by_later_verification(case):
    evidence, _, _, _ = case
    target = json.loads(evidence.current_source.read_bytes())
    target["source_catalogue"]["snapshot_created_at"] = "2026-10-10T00:00:00+00:00"
    evidence.current_source.write_bytes(encoded(target))
    evidence = replace(
        evidence,
        expected_sha256={
            **evidence.expected_sha256,
            "current_source": h(evidence.current_source.read_bytes()),
        },
    )
    with pytest.raises(d.Refused, match="current_snapshot_predates_base"):
        evidence.load(d.Guard(d.DEFAULT_LIMITS, lambda: None))


def test_equal_snapshot_time_zero_delta_is_permitted(tmp_path):
    t = trace("original")
    view = snapshot([row("005930", 0, t)], [t])
    evidence = write_evidence(tmp_path, view, view)
    target = json.loads(evidence.current_source.read_bytes())
    target["source_catalogue"]["snapshot_created_at"] = "2026-10-10T01:00:00+00:00"
    evidence.current_source.write_bytes(encoded(target))
    evidence = replace(
        evidence,
        expected_sha256={
            **evidence.expected_sha256,
            "current_source": h(evidence.current_source.read_bytes()),
        },
    )
    assert evidence.load(d.Guard(d.DEFAULT_LIMITS, lambda: None))["production_proof"] is False
