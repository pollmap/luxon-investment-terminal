"""Independent bounded logical verification. No connection, write or success receipt.

Caller owns a read-only repeatable-read transaction and hard timeout. Explicit C
order avoids Linux/Windows locale differences; per-series sorting avoids sorting
the entire multi-decade payload table into a large temporary file.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import OrderedDict

from . import copy_stream as codec

PROTOCOL = "market-logical-c-order-v1"
BATCH = 400
TABLES = {
    "source": ("public.market_time_series", "public.market_source_traces", False),
    "restored": ("market_compact_restore.rows", "market_compact_restore.traces", True),
}


class Lookup:
    def __init__(self, db, table, check):
        self.db, self.table, self.check = db, table, check
        self.cache = OrderedDict()
        self.bytes = 0

    def __contains__(self, digest):
        try:
            self[digest]
            return True
        except KeyError:
            return False

    def __getitem__(self, digest):
        self.check()
        if digest in self.cache:
            self.cache.move_to_end(digest)
            return self.cache[digest]
        if not isinstance(digest, str) or len(digest) != 64:
            raise KeyError(digest)
        value = self.db.execute(
            "SELECT digest,byte_count,payload FROM " + self.table + " WHERE digest=%s",
            (digest,),
        ).fetchone()
        if value is None:
            raise KeyError(digest)
        record = codec.validate_trace(value)
        # Check all records even when one record is too large for the cache.
        if record.byte_count <= 16 * 1024**2:
            while self.cache and (
                len(self.cache) >= 256 or self.bytes + record.byte_count > 16 * 1024**2
            ):
                _, old = self.cache.popitem(last=False)
                self.bytes -= old.byte_count
            self.cache[digest] = record
            self.bytes += record.byte_count
        return record


def _cursor_rows(db, name, statement, params, check):
    with db.cursor(name=name) as cursor:
        cursor.execute(statement, params)
        while True:
            check()
            rows = cursor.fetchmany(BATCH)
            if not rows:
                break
            yield from rows


def _framed(checksum, value):
    body = codec.encode_json(value).encode("utf8")
    checksum.update(len(body).to_bytes(8, "big"))
    checksum.update(body)


def verify_snapshot(db, target, *, check, max_rows, max_traces):
    """Read every row and trace. The result is data evidence, not restore approval."""
    if (
        target not in TABLES
        or type(max_rows) is not int
        or type(max_traces) is not int
        or min(max_rows, max_traces) < 0
    ):
        raise codec.Refused("Fixed target and exact positive bounds required")
    if (
        db.info.transaction_status.name != "INTRANS"
        or db.info.encoding.lower().replace("-", "") != "utf8"
    ):
        raise codec.Refused("Active UTF8 snapshot required")
    settings = db.execute(
        "SELECT current_setting('transaction_read_only'),current_setting('transaction_isolation')"
    ).fetchone()
    if settings[0] != "on" or settings[1] not in ("repeatable read", "serializable"):
        raise codec.Refused("Read-only repeatable snapshot required")
    table, traces, has_original = TABLES[target]
    lookup = Lookup(db, traces, check)
    row_hash, trace_hash = hashlib.sha256(), hashlib.sha256()
    rows_count, series_count, trace_count, original_count = 0, 0, 0, 0
    previous_series = None
    for (series,) in _cursor_rows(
        db,
        "verify_series",
        "SELECT series FROM " + table + ' GROUP BY series ORDER BY series COLLATE "C"',
        (),
        check,
    ):
        key = series.encode("utf8")
        if previous_series is not None and key <= previous_series:
            raise codec.Refused("Series order differs from C order")
        previous_series = key
        series_count += 1
        previous_time = None
        for row in _cursor_rows(
            db,
            "verify_rows",
            "SELECT series,time,digest,payload FROM "
            + table
            + ' WHERE series=%s ORDER BY time COLLATE "C"',
            (series,),
            check,
        ):
            codec.validate_series(row)
            stamp = row[1].encode("utf8")
            if row[0] != series or previous_time is not None and stamp <= previous_time:
                raise codec.Refused("Duplicate or unordered row key")
            previous_time = stamp
            row_hash.update(codec.row_identity(row, lookup))
            rows_count += 1
            if rows_count > max_rows:
                raise codec.Refused("Source row bound exceeded")
    previous_trace = None
    flag = ",original" if has_original else ",true AS original"
    for digest, byte_count, payload, original in _cursor_rows(
        db,
        "verify_traces",
        "SELECT digest,byte_count,payload"
        + flag
        + " FROM "
        + traces
        + ' ORDER BY digest COLLATE "C"',
        (),
        check,
    ):
        codec.validate_trace((digest, byte_count, payload))
        if type(original) is not bool or previous_trace is not None and digest <= previous_trace:
            raise codec.Refused("Invalid original trace flag or key order")
        previous_trace = digest
        trace_count += 1
        if trace_count > max_traces:
            raise codec.Refused("Trace population bound exceeded")
        if original:
            _framed(trace_hash, [digest, byte_count, payload])
            original_count += 1
    indexes = db.execute(
        "SELECT n.nspname||'.'||c.relname,i.indisvalid,i.indisready,i.indisprimary,"
        "array(SELECT a.attname FROM unnest(i.indkey) WITH ORDINALITY k(attnum,n) "
        "JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum ORDER BY k.n) "
        "FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE i.indrelid IN (%s::regclass,%s::regclass)",
        (table, traces),
    ).fetchall()
    if (
        not indexes
        or any(not r[1] or not r[2] for r in indexes)
        or not any(r[0] == table and r[3] and list(r[4]) == ["series", "time"] for r in indexes)
        or not any(r[0] == traces and r[3] and list(r[4]) == ["digest"] for r in indexes)
    ):
        raise codec.Refused("Expected primary keys and valid indexes required")
    check()
    return {
        "protocol": PROTOCOL,
        "target": target,
        "rows": rows_count,
        "series": series_count,
        "logical_sha256": row_hash.hexdigest(),
        "original_traces": original_count,
        "original_traces_sha256": trace_hash.hexdigest(),
        "validated_total_traces": trace_count,
        "indexes_valid": True,
        "restore_verified": False,
    }


def equal_reports(source, restored):
    for report in (source, restored):
        if not isinstance(report, dict):
            raise codec.Refused("Incomplete independent verification report")
        if any(
            type(report.get(k)) is not int or report[k] < 0
            for k in ("rows", "series", "original_traces")
        ):
            raise codec.Refused("Exact nonnegative population counts required")
        if any(
            not isinstance(report.get(k), str) or re.fullmatch(r"[0-9a-f]{64}", report[k]) is None
            for k in ("logical_sha256", "original_traces_sha256")
        ):
            raise codec.Refused("Independent SHA256 values required")
        if report.get("indexes_valid") is not True or report["series"] > report["rows"]:
            raise codec.Refused("Invalid verification evidence")
    keys = (
        "protocol",
        "rows",
        "series",
        "logical_sha256",
        "original_traces",
        "original_traces_sha256",
    )
    if (
        source.get("target") != "source"
        or restored.get("target") != "restored"
        or source.get("protocol") != PROTOCOL
        or restored.get("protocol") != PROTOCOL
        or not source.get("indexes_valid")
        or not restored.get("indexes_valid")
        or any(source.get(k) != restored.get(k) for k in keys)
    ):
        raise codec.Refused("Independent source and restored data differ")
    return {"data_equal": True, "protocol": PROTOCOL, "restore_verified": False}


if __name__ == "__main__":
    print(
        json.dumps(
            {"executed": False, "protocol": PROTOCOL, "writes": False, "restore_verified": False}
        )
    )
