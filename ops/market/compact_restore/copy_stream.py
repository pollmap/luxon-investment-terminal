"""Bounded pg_restore text framing; PostgreSQL, never Python, decodes COPY.

This module neither launches a process nor connects/commits to a database. A
future restore driver must retain the custom dump, check pg_restore exit status,
verify the complete trace table first, and perform ordered full-row verification
before publishing a new isolated database. Iterator exhaustion is mandatory.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import ChainMap
from dataclasses import dataclass
from typing import BinaryIO

from services.market_dashboard.source_trace_store import (
    FORMAT,
    FORMAT_KEY,
    REF_KEY,
    TraceRecord,
    compact_payloads,
    encode_json,
)

TRACES = "market_source_traces"
SERIES = "market_time_series"
TABLES = (TRACES, SERIES)
COLUMNS = {
    TRACES: ("digest", "byte_count", "payload"),
    SERIES: ("series", "time", "digest", "payload"),
}
# Exact pg_restore metadata only. These strings are ignored, NEVER executed.
PREAMBLE = frozenset(
    (
        b"SET statement_timeout = 0;",
        b"SET lock_timeout = 0;",
        b"SET idle_in_transaction_session_timeout = 0;",
        b"SET transaction_timeout = 0;",
        b"SET client_encoding = 'UTF8';",
        b"SET standard_conforming_strings = on;",
        b"SELECT pg_catalog.set_config('search_path', '', false);",
        b"SET check_function_bodies = false;",
        b"SET xmloption = content;",
        b"SET client_min_messages = warning;",
        b"SET row_security = off;",
    )
)


class Refused(ValueError):
    """No raw payload/SQL contents are included in diagnostics."""


@dataclass(frozen=True)
class Limits:
    max_batch_rows: int = 400
    max_batch_bytes: int = 16 * 1024**2
    max_stream_bytes: int = 256 * 1024**3

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in vars(self).values()):
            raise Refused("Positive integer limits required")
        if self.max_batch_rows > 400:
            raise Refused("At most 400 rows per staging batch")


DEFAULT_LIMITS = Limits()


@dataclass(frozen=True)
class CopyBatch:
    table: str
    row_count: int
    raw: bytes


@dataclass(frozen=True)
class CopyEnd:
    table: str
    row_count: int


def _header(line):
    # Optional quoting is limited to these exact identifiers and column order.
    for table, columns in COLUMNS.items():
        identifier = lambda name: rf'(?:{name}|"{name}")'  # noqa: E731
        pattern = (
            rf"COPY {identifier('public')}\.{identifier(table)} \("
            + r", ?".join(identifier(col) for col in columns)
            + r"\) FROM stdin;"
        )
        if re.fullmatch(pattern.encode("ascii"), line):
            return table
    return None


def _line(raw):
    if not raw.endswith(b"\n"):
        raise Refused("Truncated physical line")
    content = raw[:-2] if raw.endswith(b"\r\n") else raw[:-1]
    if b"\r" in content or b"\x00" in content:
        raise Refused("Bare CR or NUL in physical line")
    try:
        content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise Refused("Non-UTF8 input") from exc
    return content, b"\r\n" if raw.endswith(b"\r\n") else b"\n"


def parse_copy_stream(stream: BinaryIO, *, expected_rows: dict[str, int], limits=DEFAULT_LIMITS):
    """Yield unchanged raw batches and block ends, strictly traces then series.

    Counts must come from a separately verified dump receipt, never from this
    parser's own output. No SQL or psql command from the input is executed. Only
    normal pg_restore COPY-text output is supported: not CSV/binary/INSERTs or
    escaped physical newline continuations. Partial iteration is NOT success.
    """
    if set(expected_rows) != set(TABLES) or any(
        type(n) is not int or n < 0 for n in expected_rows.values()
    ):
        raise Refused("Exact nonnegative counts for both target tables required")
    expected_rows = dict(expected_rows)
    active = None
    completed = []
    table_count = batch_count = total_bytes = 0
    pending = bytearray()
    restriction = None
    restriction_closed = False
    dump_started = dump_finished = False
    ending = None
    while True:
        raw = stream.readline(limits.max_batch_bytes + 1)
        if not isinstance(raw, bytes):
            raise Refused("Binary stream required")
        if not raw:
            break
        total_bytes += len(raw)
        if len(raw) > limits.max_batch_bytes or total_bytes > limits.max_stream_bytes:
            raise Refused("Stream or physical row byte budget exceeded")
        line, this_ending = _line(raw)
        if active:
            if ending is not None and ending != this_ending:
                raise Refused("Mixed COPY line endings")
            ending = this_ending
            if line == b"\\.":
                if table_count != expected_rows[active]:
                    raise Refused("COPY block row count does not match receipt")
                if pending:
                    yield CopyBatch(active, batch_count, bytes(pending))
                yield CopyEnd(active, table_count)
                completed.append(active)
                active = None
                pending.clear()
                batch_count = 0
                continue
            # Raw escaping stays untouched. Column/null/escape semantics are
            # validated by PostgreSQL COPY into the NOT NULL typed staging table.
            if table_count >= expected_rows[active]:
                raise Refused("COPY block exceeds verified row count")
            if pending and (
                batch_count >= limits.max_batch_rows
                or len(pending) + len(raw) > limits.max_batch_bytes
            ):
                yield CopyBatch(active, batch_count, bytes(pending))
                pending.clear()
                batch_count = 0
            pending.extend(raw)
            table_count += 1
            batch_count += 1
            continue
        if line == b"-- PostgreSQL database dump":
            if dump_started or completed or dump_finished:
                raise Refused("Unexpected dump start")
            dump_started = True
            continue
        if line == b"-- PostgreSQL database dump complete":
            if completed != list(TABLES) or dump_finished:
                raise Refused("Premature or repeated dump footer")
            dump_finished = True
            continue
        if not line or line.startswith(b"--"):
            continue
        match = re.fullmatch(rb"\\(restrict|unrestrict) ([A-Za-z0-9]{1,256})", line)
        if match:
            command, token = match.groups()
            if command == b"restrict" and not restriction and not completed and not dump_finished:
                restriction = token
            elif (
                command == b"unrestrict"
                and token == restriction
                and completed == list(TABLES)
                and not restriction_closed
            ):
                restriction_closed = True
            else:
                raise Refused("Unbalanced pg_restore restriction markers")
            continue
        if line in PREAMBLE and not completed and not dump_finished:
            continue
        table = _header(line)
        if (
            table is None
            or len(completed) >= len(TABLES)
            or table != TABLES[len(completed)]
            or dump_finished
            or restriction_closed
        ):
            raise Refused("Unsupported SQL, COPY header, table order or trailer")
        active = table
        table_count = 0
        ending = None
    if (
        active
        or completed != list(TABLES)
        or (dump_started and not dump_finished)
        or (restriction is not None and not restriction_closed)
    ):
        raise Refused("Incomplete COPY stream")


# These are the only SQL statements that this adapter can execute. No identifier,
# column, SQL option, statement or query received from the dump is interpolated.
CREATE = {
    TRACES: "CREATE TEMP TABLE _market_restore_trace_stage ("
    "_position bigint GENERATED ALWAYS AS IDENTITY,"
    "digest varchar(64) NOT NULL,byte_count integer NOT NULL,payload text NOT NULL) "
    "ON COMMIT DROP",
    SERIES: "CREATE TEMP TABLE _market_restore_series_stage ("
    "_position bigint GENERATED ALWAYS AS IDENTITY,"
    "series varchar(128) NOT NULL,time varchar(40) NOT NULL,"
    "digest varchar(64) NOT NULL,payload text NOT NULL) ON COMMIT DROP",
}
STAGE = {
    TRACES: "pg_temp._market_restore_trace_stage",
    SERIES: "pg_temp._market_restore_series_stage",
}
COPY = {
    table: "COPY " + STAGE[table] + " (" + ",".join(COLUMNS[table]) + ") FROM STDIN"
    for table in TABLES
}
SELECT = {
    table: "SELECT " + ",".join(COLUMNS[table]) + " FROM " + STAGE[table] + " ORDER BY _position"
    for table in TABLES
}
TRUNCATE = {table: "TRUNCATE " + STAGE[table] + " RESTART IDENTITY" for table in TABLES}


class NativeCopyStage:
    """Use an existing caller-owned transaction; never open/commit/rollback it.

    Stage setup/decoding is not proof of the final database or complete trace
    restoration. The orchestrator owns those gates and rollback on ANY error.
    """

    def __init__(self, connection, *, limits=DEFAULT_LIMITS):
        self.connection = connection
        self.limits = limits
        self.created = False

    def _transaction(self):
        if (
            self.connection.autocommit is not False
            or self.connection.info.transaction_status.name != "INTRANS"
        ):
            raise Refused("An explicit active caller-owned transaction is required")
        if self.connection.info.encoding.lower().replace("-", "") != "utf8":
            raise Refused("Native COPY requires an already UTF8 client connection")

    def create(self):
        self._transaction()
        if self.created:
            raise Refused("Staging tables already created by this adapter")
        for statement in CREATE.values():
            self.connection.execute(statement)
        self.created = True

    def decode(self, batch: CopyBatch):
        self._transaction()
        if not self.created or batch.table not in TABLES:
            raise Refused("Unknown or uninitialized staging table")
        if (
            type(batch.row_count) is not int
            or not 1 <= batch.row_count <= self.limits.max_batch_rows
            or not isinstance(batch.raw, bytes)
            or not 0 < len(batch.raw) <= self.limits.max_batch_bytes
        ):
            raise Refused("Invalid staging batch limits")
        # Count LF, not splitlines(): raw VT/FF are data bytes in COPY text.
        raw_lines = batch.raw.split(b"\n")
        if raw_lines[-1] != b"" or len(raw_lines) - 1 != batch.row_count:
            raise Refused("Invalid staging batch framing")
        endings = set()
        for line in raw_lines[:-1]:
            content, ending = _line(line + b"\n")
            endings.add(ending)
            if content == b"\\.":
                raise Refused("COPY terminator cannot be a staging data row")
        if len(endings) != 1:
            raise Refused("Mixed staging line endings")
        with self.connection.cursor() as cursor:
            with cursor.copy(COPY[batch.table]) as writer:
                writer.write(batch.raw)
        rows = self.connection.execute(SELECT[batch.table]).fetchall()
        if len(rows) != batch.row_count:
            raise Refused("Native decoded row count differs")
        if batch.table == TRACES:
            for row in rows:
                validate_trace(row)
        else:
            for row in rows:
                validate_series(row)
        self.connection.execute(TRUNCATE[batch.table])
        return rows


def validate_trace(row):
    if (
        len(row) != 3
        or not isinstance(row[0], str)
        or type(row[1]) is not int
        or not 0 <= row[1] <= 2**31 - 1
        or not isinstance(row[2], str)
    ):
        raise Refused("Invalid decoded trace row")
    record = TraceRecord(*row)
    record.verified_value()  # Includes exact UTF8 byte count/hash/canonical JSON.
    return record


def validate_series(row):
    if (
        len(row) != 4
        or any(not isinstance(v, str) or "\x00" in v for v in row)
        or not 1 <= len(row[0]) <= 128
        or not 1 <= len(row[1]) <= 40
        or not re.fullmatch(r"[0-9a-f]{64}", row[2])
    ):
        raise Refused("Invalid decoded time-series row")
    return row


def logical_value(body, records):
    """Same time-series envelope semantics as timeseries_migration.decode."""
    value = json.loads(body)
    if not isinstance(value, dict):
        raise Refused("Time-series payload must be an object")
    if FORMAT_KEY not in value:
        encode_json(value)  # Refuse nonfinite numbers rather than alter them.
        return value
    if (
        set(value) != {FORMAT_KEY, "row"}
        or value[FORMAT_KEY] != FORMAT
        or not isinstance(value["row"], dict)
    ):
        raise Refused("Unknown time-series envelope")

    def visit(item):
        if isinstance(item, list):
            return [visit(child) for child in item]
        if not isinstance(item, dict):
            return item
        result = {}
        for key, child in item.items():
            if key == "source_trace" and isinstance(child, dict) and REF_KEY in child:
                identity = child[REF_KEY]
                if (
                    set(child) != {REF_KEY}
                    or not isinstance(identity, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", identity)
                    or identity not in records
                    or records[identity].digest != identity
                ):
                    raise Refused("Missing or malformed source trace")
                result[key] = records[identity].verified_value()
            else:
                result[key] = visit(child)
        return result

    restored = visit(value["row"])
    encode_json(restored)
    return restored


def row_identity(row, records):
    """Length-framed canonical bytes, EXACTLY verify_rows' checksum input.

    A final checksum must consume SQL ORDER BY series,time in the same database
    collation as the original verifier. pg_restore dump order is NOT guaranteed;
    hashing unsorted COPY events is not a final equivalence proof.
    """
    validate_series(row)
    canonical = encode_json([*row[:3], logical_value(row[3], records)]).encode("utf-8")
    return len(canonical).to_bytes(8, "big") + canonical


def ordered_logical_hash(rows, records):
    checksum = hashlib.sha256()
    count = 0
    for row in rows:
        checksum.update(row_identity(row, records))
        count += 1
    return count, checksum.hexdigest()


def compact_rows(rows, records, *, limits=DEFAULT_LIMITS):
    """Pure bounded adapter. Preserve keys/digests/values; return separate output.

    records must be a verified lookup of the ALREADY completely restored source
    trace table. Never replace that table with just the newly returned records:
    financial facts can reference original records not used by these rows.
    """
    if not isinstance(rows, (tuple, list)) or len(rows) > limits.max_batch_rows:
        raise Refused("A bounded time-series row batch is required")
    for row in rows:
        validate_series(row)
    if len({tuple(row[:2]) for row in rows}) != len(rows):
        raise Refused("Duplicate time-series keys within a batch")
    if sum(len(row[3].encode("utf-8")) for row in rows) > limits.max_batch_bytes:
        raise Refused("Decoded payload batch exceeds byte budget")
    logical = [encode_json(logical_value(row[3], records)) for row in rows]
    bodies, added = compact_payloads(logical)
    for key, record in added.items():
        if key in records and records[key] != record:
            raise Refused("New evidence conflicts with original evidence")
    # Do not copy the full source ledger. A driver should provide only each
    # batch's referenced records, or an immutable verified lookup.
    combined = ChainMap(added, records)
    output = [(*row[:3], body) for row, body in zip(rows, bodies, strict=True)]
    for old, new in zip(rows, output, strict=True):
        if row_identity(old, combined) != row_identity(new, combined):
            raise Refused("Compaction changed row identity or logical data")
    return output, added
