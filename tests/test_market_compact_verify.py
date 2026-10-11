"""fixture_non_production: independent verifier fake-DB contract tests only."""

import copy
import hashlib
from types import SimpleNamespace

import pytest

from ops.market.compact_restore import ordered_verify as v

c = v.codec


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class NamedCursor:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.values, self.position = [], 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.db.closed.append(self.name)

    def execute(self, sql, params):
        self.db.statements.append((sql, params))
        if self.name == "verify_series":
            assert sql == v._series_statement(v.TABLES[self.db.target][0])
            assert 'ORDER BY series COLLATE "C"' in sql
            series = sorted(
                {row[0] for row in self.db.rows}, key=lambda value: value.encode("utf8")
            )
            self.values = [(name,) for name in self.db.series_override or series]
        elif self.name == "verify_rows":
            assert 'WHERE series=%s ORDER BY time COLLATE "C"' in sql
            self.values = sorted(
                [row for row in self.db.rows if row[0] == params[0]],
                key=lambda row: row[1].encode("utf8"),
            )
            if self.db.row_override is not None and params[0] == self.db.row_override[0]:
                self.values = self.db.row_override[1]
        elif self.name == "verify_traces":
            assert 'ORDER BY digest COLLATE "C"' in sql
            self.values = sorted(self.db.traces, key=lambda row: row[0])
            if self.db.trace_override is not None:
                self.values = self.db.trace_override
            if self.db.target == "source":
                assert ",true AS original" in sql
        else:
            raise AssertionError("Unknown named cursor")

    def fetchmany(self, size):
        assert size == 400
        self.db.fetches.append((self.name, size))
        part = self.values[self.position : self.position + size]
        self.position += size
        return part

    def fetchall(self):
        raise AssertionError("Full payload cursor materialization forbidden")


class FakeDB:
    def __init__(self, target, rows, traces):
        self.target, self.rows, self.traces = target, rows, traces
        self.info = SimpleNamespace(
            transaction_status=SimpleNamespace(name="INTRANS"), encoding="utf-8"
        )
        self.settings = ("on", "repeatable read")
        table, trace_table, _ = v.TABLES[target]
        self.indexes = [
            (table, True, True, True, ["series", "time"]),
            (trace_table, True, True, True, ["digest"]),
        ]
        self.statements, self.fetches, self.closed = [], [], []
        self.series_override = self.row_override = self.trace_override = None

    def cursor(self, name):
        return NamedCursor(self, name)

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        assert sql.startswith("SELECT "), "Verifier must not write or execute source text"
        if sql.startswith("SELECT current_setting"):
            return Result([self.settings])
        if "FROM pg_index" in sql:
            assert params == v.TABLES[self.target][:2]
            return Result(self.indexes)
        if "WHERE digest=%s" in sql:
            return Result([row[:3] for row in self.traces if row[0] == params[0]])
        raise AssertionError("Unknown fixed query")


def fixtures():
    trace = c.TraceRecord.from_value(
        {"source": "fixture_non_production", "document": "가나다" * 500}
    )
    financial = c.TraceRecord.from_value(
        {"source": "fixture_non_production", "financial_only": True}
    )
    additional = c.TraceRecord.from_value({"source": "fixture_non_production", "added_only": True})
    records = {r.digest: r for r in (trace, financial)}
    rows = []
    for number, (series, date) in enumerate(
        (("가", "2025"), ("B", "Z"), ("a", "2025"), ("B", "a"))
    ):
        value = {
            "value": number,
            "null": None,
            "text": "한글\t\n\\N",
            "source_trace": trace.verified_value(),
        }
        rows.append((series, date, f"{number:064x}", c.encode_json(value)))
    compact, created = c.compact_rows(rows, records)
    assert any(left[3] != right[3] for left, right in zip(rows, compact, strict=True))
    merged = {**records, **created, additional.digest: additional}
    source = FakeDB(
        "source", rows, [(r.digest, r.byte_count, r.payload, True) for r in records.values()]
    )
    restored = FakeDB(
        "restored",
        compact,
        [(r.digest, r.byte_count, r.payload, r.digest in records) for r in merged.values()],
    )
    return source, restored, records, financial.digest


def verify(db, **limits):
    checks = []
    report = v.verify_snapshot(
        db,
        db.target,
        check=lambda: checks.append(True),
        max_rows=limits.get("max_rows", 4),
        max_traces=limits.get("max_traces", 3),
    )
    assert checks
    assert all(
        sql.startswith("SELECT ") or sql == v._series_statement(v.TABLES[db.target][0])
        for sql, _ in db.statements
    )
    return report


@pytest.mark.parametrize("target", ["source", "restored"])
def test_series_enumeration_seeks_native_pk_and_only_sorts_distinct_keys_in_c(target):
    table = v.TABLES[target][0]
    sql = v._series_statement(table)
    assert sql.startswith("WITH RECURSIVE series_keys(series) AS (")
    assert f"(SELECT series FROM {table} ORDER BY series LIMIT 1)" in sql
    assert "UNION ALL SELECT next_key.series FROM series_keys AS current_key" in sql
    assert f"CROSS JOIN LATERAL (SELECT series FROM {table} " in sql
    assert "WHERE series > current_key.series ORDER BY series LIMIT 1) AS next_key" in sql
    assert sql.endswith('SELECT series FROM series_keys ORDER BY series COLLATE "C"')
    assert sql.count("COLLATE") == 1
    assert "GROUP BY" not in sql and "OFFSET" not in sql and "payload" not in sql


@pytest.mark.parametrize("table", ["other.table", "public.market_time_series; DELETE FROM x", ""])
def test_series_enumeration_refuses_nonfixed_identifier(table):
    with pytest.raises(c.Refused, match="Fixed series table"):
        v._series_statement(table)


def test_many_distinct_keys_keep_all_rows_c_order_hash_and_bounded_fetches():
    source, _, records, _ = fixtures()
    prototype = source.rows[0]
    # Mix locale-sensitive labels and duplicate series across fetch boundaries.
    source.rows = [
        (f"{label}{number:04}", stamp, prototype[2], prototype[3])
        for number in range(140)
        for label in ("가", "a", "B")
        for stamp in ("z", "A")
    ]
    report = verify(source, max_rows=840)
    expected = c.ordered_logical_hash(
        sorted(source.rows, key=lambda row: (row[0].encode("utf8"), row[1].encode("utf8"))),
        records,
    )
    assert (report["rows"], report["logical_sha256"]) == expected
    assert report["series"] == 420
    assert source.fetches.count(("verify_series", 400)) == 3  # 400,20,EOF
    assert source.closed.count("verify_rows") == 420


def test_c_order_hash_matches_codec_and_all_originals_include_unreferenced():
    source, restored, records, financial = fixtures()
    actual_source, actual_restored = verify(source), verify(restored)
    ordered = sorted(source.rows, key=lambda row: (row[0].encode("utf8"), row[1].encode("utf8")))
    count, checksum = c.ordered_logical_hash(ordered, records)
    assert actual_source["logical_sha256"] == checksum and count == 4
    assert actual_source["original_traces"] == 2
    assert financial in records and actual_restored["original_traces"] == 2
    assert (
        actual_source["validated_total_traces"] == 2
        and actual_restored["validated_total_traces"] == 3
    )
    assert v.equal_reports(actual_source, actual_restored) == {
        "data_equal": True,
        "protocol": v.PROTOCOL,
        "restore_verified": False,
    }
    checksum = hashlib.sha256()
    for record in sorted(records.values(), key=lambda row: row.digest):
        v._framed(checksum, [record.digest, record.byte_count, record.payload])
    assert actual_source["original_traces_sha256"] == checksum.hexdigest()
    assert source.closed.count("verify_rows") == 3


@pytest.mark.parametrize(
    "damage", ["series_order", "duplicate_series", "time_order", "duplicate_row", "wrong_series"]
)
def test_database_order_and_duplicate_keys_are_independently_checked(damage):
    source, _, _, _ = fixtures()
    first = sorted([row for row in source.rows if row[0] == "B"], key=lambda row: row[1])
    if damage == "series_order":
        source.series_override = ["a", "B", "가"]
    elif damage == "duplicate_series":
        source.series_override = ["B", "B", "가"]
    elif damage == "time_order":
        source.row_override = ("B", list(reversed(first)))
    elif damage == "duplicate_row":
        source.row_override = ("B", [first[0], first[0]])
    else:
        source.row_override = ("B", [("wrong", *first[0][1:])])
    with pytest.raises(c.Refused):
        verify(source)


@pytest.mark.parametrize(
    "limits",
    [
        dict(max_rows=3),
        dict(max_traces=1),
        dict(max_rows=-1),
        dict(max_rows=True),
        dict(max_traces="2"),
    ],
)
def test_population_and_bound_types_are_checked(limits):
    source, _, _, _ = fixtures()
    with pytest.raises(c.Refused):
        verify(source, **limits)


def test_added_trace_count_is_included_in_bound():
    _, restored, _, _ = fixtures()
    with pytest.raises(c.Refused, match="Trace population"):
        verify(restored, max_traces=2)


def test_empty_snapshot_has_zero_counts_and_empty_hashes():
    db = FakeDB("source", [], [])
    result = verify(db, max_rows=0, max_traces=0)
    assert result["rows"] == result["series"] == result["original_traces"] == 0
    assert result["logical_sha256"] == hashlib.sha256(b"").hexdigest()


@pytest.mark.parametrize("flag", [None, 1, 0, "true"])
def test_original_flag_requires_boolean(flag):
    _, restored, _, financial = fixtures()
    restored.traces = [
        (*row[:3], flag if row[0] == financial else row[3]) for row in restored.traces
    ]
    with pytest.raises(c.Refused, match="original trace flag"):
        verify(restored)


@pytest.mark.parametrize(
    "damage", ["remove_unreferenced", "flag_false", "invalid_unreferenced_bytes"]
)
def test_unreferenced_original_evidence_cannot_disappear_or_be_reclassified(damage):
    source, restored, _, financial = fixtures()
    original = verify(source)
    if damage == "remove_unreferenced":
        restored.traces = [row for row in restored.traces if row[0] != financial]
    elif damage == "flag_false":
        restored.traces = [
            (*row[:3], False if row[0] == financial else row[3]) for row in restored.traces
        ]
    else:
        restored.traces = [
            (row[0], row[1], row[2] + " " if row[0] == financial else row[2], row[3])
            for row in restored.traces
        ]
    with pytest.raises(ValueError):
        v.equal_reports(original, verify(restored))


def test_unused_new_trace_is_still_validated():
    _, restored, _, _ = fixtures()
    restored.traces = [
        (row[0], row[1], row[2] if row[3] else "{}", row[3]) for row in restored.traces
    ]
    with pytest.raises(ValueError):
        verify(restored)


@pytest.mark.parametrize(
    "damage", ["missing_row_pk", "missing_trace_pk", "invalid", "not_ready", "wrong_key_order"]
)
def test_invalid_primary_key_or_index_refused(damage):
    source, _, _, _ = fixtures()
    if damage == "missing_row_pk":
        source.indexes = source.indexes[1:]
    elif damage == "missing_trace_pk":
        source.indexes = source.indexes[:1]
    else:
        row = list(source.indexes[0])
        if damage == "invalid":
            row[1] = False
        elif damage == "not_ready":
            row[2] = False
        else:
            row[4] = ["time", "series"]
        source.indexes[0] = tuple(row)
    with pytest.raises(c.Refused, match="primary keys"):
        verify(source)


@pytest.mark.parametrize(
    "status,encoding,settings",
    [
        ("IDLE", "utf8", ("on", "repeatable read")),
        ("INERROR", "utf8", ("on", "repeatable read")),
        ("INTRANS", "latin1", ("on", "repeatable read")),
        ("INTRANS", "utf8", ("off", "repeatable read")),
        ("INTRANS", "utf8", ("on", "read committed")),
    ],
)
def test_requires_active_utf8_readonly_repeatable_snapshot(status, encoding, settings):
    source, _, _, _ = fixtures()
    source.info.transaction_status.name = status
    source.info.encoding = encoding
    source.settings = settings
    with pytest.raises(c.Refused):
        verify(source)
    assert not source.fetches


def test_serializable_readonly_snapshot_is_supported():
    source, _, _, _ = fixtures()
    source.settings = ("on", "serializable")
    assert verify(source)["indexes_valid"] is True


def test_payload_cursor_never_materializes_more_than_400_rows():
    source, _, _, _ = fixtures()
    prototype = source.rows[0]
    source.rows = [
        ("fixture_non_production", f"{n:04}", prototype[2], prototype[3]) for n in range(801)
    ]
    result = verify(source, max_rows=801)
    assert result["rows"] == 801
    assert source.fetches.count(("verify_rows", 400)) == 4  # 400,400,1,EOF


def test_guard_failure_stops_read_and_closes_named_cursor():
    source, _, _, _ = fixtures()
    calls = 0

    def check():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise TimeoutError("fixture_non_production timeout")

    with pytest.raises(TimeoutError):
        v.verify_snapshot(source, "source", check=check, max_rows=4, max_traces=3)
    assert "verify_rows" in source.closed and "verify_series" in source.closed


@pytest.mark.parametrize(
    "field,new_value",
    [
        ("protocol", "wrong"),
        ("rows", 999),
        ("series", 9),
        ("logical_sha256", "a" * 64),
        ("original_traces", 0),
        ("original_traces_sha256", "b" * 64),
        ("indexes_valid", False),
        ("target", "source"),
    ],
)
def test_equal_reports_rejects_each_material_difference(field, new_value):
    source, restored, _, _ = fixtures()
    left, right = verify(source), verify(restored)
    right[field] = new_value
    with pytest.raises(c.Refused):
        v.equal_reports(left, right)


@pytest.mark.parametrize(
    "damage", ["missing_fields", "missing_rows", "string_rows", "boolean_rows", "invalid_hash"]
)
def test_equal_reports_rejects_equally_malformed_reports(damage):
    source, restored, _, _ = fixtures()
    left, right = verify(source), verify(restored)
    if damage == "missing_fields":
        left = {"target": "source", "protocol": v.PROTOCOL, "indexes_valid": True}
        right = {"target": "restored", "protocol": v.PROTOCOL, "indexes_valid": True}
    else:
        for report in (left, right):
            if damage == "missing_rows":
                report.pop("rows")
            elif damage == "string_rows":
                report["rows"] = "4"
            elif damage == "boolean_rows":
                report["rows"] = True
            else:
                report["logical_sha256"] = "not a hash"
    before = copy.deepcopy((left, right))
    with pytest.raises(c.Refused):
        v.equal_reports(left, right)
    assert (left, right) == before
