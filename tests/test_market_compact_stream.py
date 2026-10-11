"""fixture_non_production. Pure tests; no PostgreSQL, sockets or subprocesses."""

import hashlib
import io
import json
from types import SimpleNamespace

import pytest

from ops.market.compact_restore import copy_stream as m


def encode_field(value):
    """Fixture encoder only; never a decoder or an execution path."""
    if value is None:
        return b"\\N"
    return (
        str(value)
        .encode("utf-8")
        .replace(b"\\", b"\\\\")
        .replace(b"\t", b"\\t")
        .replace(b"\n", b"\\n")
        .replace(b"\r", b"\\r")
        .replace(b"\b", b"\\b")
        .replace(b"\f", b"\\f")
        .replace(b"\v", b"\\v")
    )


def wire(row, ending=b"\n"):
    return b"\t".join(encode_field(value) for value in row) + ending


def header(table, quoted=False):
    quote = lambda value: '"' + value + '"' if quoted else value  # noqa: E731
    return (
        "COPY "
        + quote("public")
        + "."
        + quote(table)
        + " ("
        + ", ".join(quote(col) for col in m.COLUMNS[table])
        + ") FROM stdin;"
    ).encode()


def dump(traces=(), rows=(), *, ending=b"\n", quoted=False, wrapped=False):
    result = b""
    if wrapped:
        result += (
            b"-- PostgreSQL database dump"
            + ending
            + b"\\restrict safeToken123"
            + ending
            + ending.join(sorted(m.PREAMBLE))
            + ending
        )
    for table, lines in ((m.TRACES, traces), (m.SERIES, rows)):
        result += header(table, quoted) + ending + b"".join(lines) + b"\\." + ending
    if wrapped:
        result += (
            b"-- PostgreSQL database dump complete" + ending + b"\\unrestrict safeToken123" + ending
        )
    return result


def parse(raw, counts=None, **kwargs):
    return list(
        m.parse_copy_stream(
            io.BytesIO(raw), expected_rows=counts or {m.TRACES: 0, m.SERIES: 0}, **kwargs
        )
    )


def fixture():
    trace = m.TraceRecord.from_value(
        {
            "source": "fixture_non_production",
            "evidence": "원문\t\n\r\\N" * 100,
            "received_at": "2026-10-10T00:00:00Z",
            "missing": None,
        }
    )
    value = {
        "time": "2025-01-01",
        "zero": 0,
        "negative": -123.5,
        "null": None,
        "text": "문자\t개행\n역슬래시\\N",
        "source_trace": trace.verified_value(),
        "nested": [{"source_trace": trace.verified_value()}],
    }
    row = ("fixture:005930", "2025-01-01", "f" * 64, m.encode_json(value))
    return trace, row


@pytest.mark.parametrize("ending", [b"\n", b"\r\n"])
@pytest.mark.parametrize("quoted", [False, True])
@pytest.mark.parametrize("wrapped", [False, True])
def test_normal_pg_restore_framing_preserves_every_byte(ending, quoted, wrapped):
    trace, row = fixture()
    left, right = wire((trace.digest, trace.byte_count, trace.payload), ending), wire(row, ending)
    events = parse(
        dump([left], [right], ending=ending, quoted=quoted, wrapped=wrapped),
        {m.TRACES: 1, m.SERIES: 1},
    )
    assert events == [
        m.CopyBatch(m.TRACES, 1, left),
        m.CopyEnd(m.TRACES, 1),
        m.CopyBatch(m.SERIES, 1, right),
        m.CopyEnd(m.SERIES, 1),
    ]


@pytest.mark.parametrize(
    "escaped",
    [
        rb"\t",
        rb"\n",
        rb"\r",
        rb"\b",
        rb"\f",
        rb"\v",
        rb"\\",
        rb"\N",
        rb"\\N",
        rb"\1",
        rb"\12",
        rb"\123",
        rb"\x1",
        rb"\x7F",
        rb"\355\225\234",
        rb"\xed\x95\x9c",
        rb"\.",
        rb"\\.",
        rb"\q",
    ],
)
def test_escape_sequences_are_forwarded_not_reinterpreted(escaped):
    # Some cases are intentionally invalid for the real typed table (e.g. NULL).
    # Native PostgreSQL, not this framing test, must accept/reject their semantics.
    raw_row = b"fixture\t2025\t" + b"f" * 64 + b"\t" + escaped + b"\n"
    events = parse(dump(rows=[raw_row]), {m.TRACES: 0, m.SERIES: 1})
    assert events[1].raw == raw_row


@pytest.mark.parametrize(
    "bad",
    [
        b"COPY public.market_snapshots (key,payload) FROM stdin;",
        b"COPY other.market_time_series (series,time,digest,payload) FROM stdin;",
        b"COPY public.market_time_series (time,series,digest,payload) FROM stdin;",
        b"COPY public.market_time_series (series,time,digest,payload,other) FROM stdin;",
        b"COPY public.market_time_series FROM stdin;",
        b"COPY public.market_time_series (series,time,digest,payload) FROM PROGRAM 'touch no';",
        b"COPY public.market_time_series (series,time,digest,payload) FROM '/tmp/file';",
        b"COPY public.market_time_series (series,time,digest,payload) FROM stdin WITH CSV;",
        b"COPY public.market_time_series (series,time,digest,payload) FROM stdin; DELETE FROM x;",
        b"SELECT pg_catalog.set_config('search_path', 'public', false);",
        b"SET client_encoding = 'LATIN1';",
        b"\\! touch forbidden",
        b"DROP TABLE public.market_time_series;",
    ],
)
def test_nonallowlisted_input_is_rejected_without_execution(bad):
    with pytest.raises(m.Refused):
        parse(bad + b"\n" + dump())


def test_source_trace_table_must_be_complete_and_first():
    with pytest.raises(m.Refused):
        parse(header(m.SERIES) + b"\n\\.\n" + header(m.TRACES) + b"\n\\.\n")
    with pytest.raises(m.Refused):
        parse(dump() + header(m.SERIES) + b"\n\\.\n")


@pytest.mark.parametrize(
    "raw", [b"", b"COPY", header(m.TRACES) + b"\n", dump()[:-1], dump()[:-3], dump() + b"unclosed"]
)
def test_truncated_stream_is_not_success(raw):
    with pytest.raises(m.Refused):
        parse(raw)


def test_dump_footer_and_restrict_pair_are_required_when_present():
    for raw in [
        dump(wrapped=True).replace(b"-- PostgreSQL database dump complete\n", b""),
        dump(wrapped=True).replace(b"\\unrestrict safeToken123\n", b""),
        dump(wrapped=True).replace(b"\\unrestrict safeToken123", b"\\unrestrict wrong"),
    ]:
        with pytest.raises(m.Refused):
            parse(raw)


@pytest.mark.parametrize("raw", [b"\xff\n", b"a\x00b\n", b"a\rb\n"])
def test_invalid_utf8_nul_and_bare_cr_rejected(raw):
    with pytest.raises(m.Refused):
        parse(dump(rows=[raw]), {m.TRACES: 0, m.SERIES: 1})


def test_counts_limits_and_mixed_line_endings():
    with pytest.raises(m.Refused):
        parse(dump(), {m.TRACES: 1, m.SERIES: 0})
    with pytest.raises(m.Refused):
        parse(dump(rows=[b"row\n"]))
    with pytest.raises(m.Refused):
        parse(dump(rows=[b"a\n", b"b\r\n"]), {m.TRACES: 0, m.SERIES: 2})
    with pytest.raises(m.Refused):
        parse(dump(), limits=m.Limits(max_stream_bytes=10))
    with pytest.raises(m.Refused):
        parse(
            dump(rows=[b"x" * 1024 + b"\n"]),
            {m.TRACES: 0, m.SERIES: 1},
            limits=m.Limits(max_batch_bytes=512),
        )
    with pytest.raises(m.Refused):
        parse(dump(), {m.TRACES: True, m.SERIES: 0})
    with pytest.raises(m.Refused):
        m.Limits(max_batch_rows=401)


def test_bounded_reader_and_batches_do_not_buffer_all_rows():
    class CheckedStream(io.BytesIO):
        def readline(self, limit=-1):
            assert limit == 1025
            return super().readline(limit)

    rows = [b"x" * 100 + b"\n"] * 1000
    iterator = m.parse_copy_stream(
        CheckedStream(dump(rows=rows)),
        expected_rows={m.TRACES: 0, m.SERIES: 1000},
        limits=m.Limits(max_batch_bytes=1024),
    )
    assert next(iterator) == m.CopyEnd(m.TRACES, 0)
    batches = [event for event in iterator if isinstance(event, m.CopyBatch)]
    assert sum(batch.row_count for batch in batches) == 1000
    assert max(len(batch.raw) for batch in batches) <= 1024
    assert max(batch.row_count for batch in batches) <= 400
    events = parse(dump(rows=[b"x\n"] * 801), {m.TRACES: 0, m.SERIES: 801})
    assert [event.row_count for event in events if isinstance(event, m.CopyBatch)] == [400, 400, 1]


class FakeConnection:
    """SQL-protocol assertion fake, NOT a substitute for PostgreSQL COPY parsing."""

    def __init__(self, rows, error=False):
        self.autocommit = False
        self.info = SimpleNamespace(
            transaction_status=SimpleNamespace(name="INTRANS"), encoding="utf-8"
        )
        self.rows, self.error = rows, error
        self.statements, self.raw = [], []

    def execute(self, sql):
        self.statements.append(sql)
        assert sql in {*m.CREATE.values(), *m.SELECT.values(), *m.TRUNCATE.values()}
        return self

    def fetchall(self):
        return self.rows

    def cursor(self):
        return self

    def copy(self, sql):
        self.statements.append(sql)
        assert sql in m.COPY.values()
        return self

    def write(self, raw):
        self.raw.append(raw)
        if self.error:
            raise ValueError("fixture native COPY error")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_staging_uses_only_fixed_temporary_sql_and_never_commits():
    trace, row = fixture()
    db = FakeConnection([row])
    stage = m.NativeCopyStage(db)
    stage.create()
    assert stage.decode(m.CopyBatch(m.SERIES, 1, wire(row))) == [row]
    assert db.raw == [wire(row)]
    assert db.statements[-1] == m.TRUNCATE[m.SERIES]
    db.rows = [(trace.digest, trace.byte_count, trace.payload)]
    assert stage.decode(m.CopyBatch(m.TRACES, 1, wire(db.rows[0]))) == db.rows
    # The fake has no commit(), rollback(), subprocess() or target INSERT method.


@pytest.mark.parametrize(
    "mutate",
    [
        "autocommit",
        "idle",
        "uncreated",
        "unknown",
        "marker",
        "count",
        "native_error",
        "decoded_count",
    ],
)
def test_staging_errors_propagate_no_commit_or_partial_success(mutate):
    _, row = fixture()
    db = FakeConnection([row], error=mutate == "native_error")
    stage = m.NativeCopyStage(db)
    if mutate != "uncreated":
        stage.create()
    batch = m.CopyBatch(m.SERIES, 1, wire(row))
    if mutate == "autocommit":
        db.autocommit = True
    if mutate == "idle":
        db.info.transaction_status.name = "IDLE"
    if mutate == "unknown":
        batch = m.CopyBatch("market_snapshots", 1, wire(row))
    if mutate == "marker":
        batch = m.CopyBatch(m.SERIES, 1, b"\\.\n")
    if mutate == "count":
        batch = m.CopyBatch(m.SERIES, 2, wire(row))
    if mutate == "decoded_count":
        db.rows = []
    with pytest.raises(ValueError):
        stage.decode(batch)
    assert not any(sql in m.TRUNCATE.values() for sql in db.statements)


def test_native_non_utf8_connection_rejected_before_sql():
    db = FakeConnection([])
    db.info.encoding = "latin-1"
    with pytest.raises(m.Refused):
        m.NativeCopyStage(db).create()
    assert db.statements == []


def test_receipt_cannot_be_mutated_after_iteration_starts():
    counts = {m.TRACES: 0, m.SERIES: 1}
    iterator = m.parse_copy_stream(io.BytesIO(dump(rows=[b"data\n"])), expected_rows=counts)
    assert next(iterator) == m.CopyEnd(m.TRACES, 0)
    counts[m.SERIES] = 0
    assert list(iterator)[-1] == m.CopyEnd(m.SERIES, 1)


def test_trace_bytes_and_bad_native_rows_are_verified():
    trace, row = fixture()
    assert m.validate_trace((trace.digest, trace.byte_count, trace.payload)) == trace
    for bad in [
        (trace.digest, trace.byte_count + 1, trace.payload),
        (trace.digest, trace.byte_count, trace.payload + " "),
        ("0" * 64, trace.byte_count, trace.payload),
        (trace.digest, None, trace.payload),
    ]:
        with pytest.raises(ValueError):
            m.validate_trace(bad)
    for bad in [
        (None, *row[1:]),
        ("x" * 129, *row[1:]),
        (row[0], "x" * 41, *row[2:]),
        (*row[:2], "not-digest", row[3]),
        (*row[:3], None),
    ]:
        with pytest.raises(m.Refused):
            m.validate_series(bad)


def test_codec_preserves_inline_shared_nested_trace_and_original_identity():
    trace, row = fixture()
    original = tuple(row)
    records = {trace.digest: trace}
    output, added = m.compact_rows([row], records)
    assert tuple(row) == original
    assert records == {trace.digest: trace}
    assert output[0][:3] == row[:3]
    assert output[0][3] != row[3]
    assert m.logical_value(output[0][3], added) == json.loads(row[3])
    assert m.row_identity(row, records) == m.row_identity(output[0], added)
    output2, added2 = m.compact_rows(output, added)
    assert output2 == output and added2 == added


def test_missing_unknown_conflicting_trace_and_duplicate_keys_refused():
    trace, row = fixture()
    output, records = m.compact_rows([row], {})
    with pytest.raises(m.Refused):
        m.compact_rows(output, {})
    with pytest.raises(m.Refused):
        m.compact_rows([row, row], {})
    corrupt = m.TraceRecord(trace.digest, trace.byte_count, trace.payload + " ")
    with pytest.raises(m.Refused):
        m.compact_rows([row], {trace.digest: corrupt})
    with pytest.raises(m.Refused):
        m.logical_value(m.encode_json({m.FORMAT_KEY: "unknown", "row": {}}), records)


def test_logical_hash_matches_existing_verify_rows_framing():
    # Independent framing oracle; no work/candidate dependency in CI.
    trace, row = fixture()
    rows = [row, (row[0], "2025-01-02", "e" * 64, row[3])]
    output, records = m.compact_rows(rows, {trace.digest: trace})
    expected = hashlib.sha256()
    for left, right in zip(rows, output, strict=True):
        assert left[:3] == right[:3]
        assert m.logical_value(right[3], records) == json.loads(left[3])
        canonical = json.dumps(
            [*left[:3], json.loads(left[3])], ensure_ascii=False,
            sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
        expected.update(len(canonical).to_bytes(8, "big"))
        expected.update(canonical)
    assert m.ordered_logical_hash(rows, records) == (2, expected.hexdigest())
    assert m.ordered_logical_hash(output, records) == (2, expected.hexdigest())
    assert m.ordered_logical_hash(list(reversed(rows)), records)[1] != expected.hexdigest()
    # Unsorted dump hashing is explicitly not accepted as the final ordered proof.
