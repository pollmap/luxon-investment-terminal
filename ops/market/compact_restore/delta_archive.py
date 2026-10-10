"""Bounded exact-PK changes between verified dated and current snapshots.

No connection, process, SQL execution, production permit or checkpoint mutation.
Callers supply stable C-ordered readers and an independently owned transactional
restore sink. A completed file is recovery data, NEVER a current writer fence.
The original archive/receipts are read-only and remain necessary for recovery.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import archive_receiver as files
from . import checkpoint
from . import copy_stream as codec
from . import ordered_verify as ordered

SCHEMA = "market-exact-pk-delta-v1"
FIELDS = ("rows", "series", "logical_sha256", "original_traces", "original_traces_sha256")
Refused = codec.Refused


def require(condition, code):
    if not condition:
        raise Refused(code)


def sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def frame(value):
    body = codec.encode_json(value).encode("utf8")
    return len(body).to_bytes(8, "big") + body


@dataclass(frozen=True)
class Limits:
    max_archive_bytes: int = 1024**3
    max_record_bytes: int = 16 * 1024**2
    max_metadata_bytes: int = 8 * 1024**2
    max_input_archive_bytes: int = 256 * 1024**3
    max_rows: int = 30_000_000
    max_traces: int = 1_000_000
    max_seconds: int = 5400
    min_free_bytes: int = checkpoint.MIN_FREE

    def __post_init__(self):
        require(all(type(v) is int and v > 0 for v in vars(self).values()), "invalid_limits")
        require(self.max_record_bytes <= 16 * 1024**2, "record_limit_exceeds_16mib")
        require(self.min_free_bytes >= checkpoint.MIN_FREE, "disk_floor_below_6gib")


DEFAULT_LIMITS = Limits()


def validated_trace(row):
    try:
        return codec.validate_trace(row)
    except (ValueError, TypeError, KeyError) as error:
        raise Refused("invalid_trace_bytes") from error


def identity(row, records, maximum=16 * 1024**2):
    try:
        # Count reference expansion BEFORE the frozen codec allocates its full
        # logical object. Each verified trace is canonical, so replacing the
        # reference object's byte size with its payload size is exact.
        codec.validate_series(row)
        require(len(row[3].encode("utf8")) <= maximum, "row_size_limit")
        value = json.loads(row[3])
        require(isinstance(value, dict), "invalid_row_identity")
        compact = codec.FORMAT_KEY in value
        if compact:
            require(
                set(value) == {codec.FORMAT_KEY, "row"}
                and value[codec.FORMAT_KEY] == codec.FORMAT
                and isinstance(value["row"], dict),
                "invalid_row_identity",
            )
            value = value["row"]
        size = 8 + len(codec.encode_json([*row[:3], value]).encode("utf8"))

        def visit(item):
            nonlocal size
            if isinstance(item, list):
                for child in item:
                    visit(child)
            elif isinstance(item, dict):
                for key, child in item.items():
                    if key == "source_trace" and isinstance(child, dict) and codec.REF_KEY in child:
                        digest = child[codec.REF_KEY]
                        require(
                            set(child) == {codec.REF_KEY} and sha(digest), "invalid_row_identity"
                        )
                        record = records[digest]
                        require(
                            record.digest == digest
                            and type(record.byte_count) is int
                            and 0 <= record.byte_count <= maximum
                            and len(record.payload.encode("utf8")) <= maximum,
                            "trace_size_limit",
                        )
                        # Validate the claimed byte count before using it as a
                        # size proof; never trust a small forged count.
                        record.verified_value()
                        size += record.byte_count - len(codec.encode_json(child).encode("utf8"))
                    else:
                        visit(child)

        if compact:
            visit(value)
        require(size <= maximum, "logical_row_size_limit")
        return codec.row_identity(row, records)
    except Refused:
        raise
    except (ValueError, TypeError, KeyError, RecursionError) as error:
        raise Refused("invalid_row_identity") from error


class Guard:
    def __init__(self, limits, check):
        self.limits, self.callback, self.started = limits, check, time.monotonic()

    def check(self):
        require(time.monotonic() - self.started < self.limits.max_seconds, "time_limit")
        self.callback()


def plain(path):
    path = Path(path)
    require(path.is_absolute(), "absolute_path_required")
    files._plain_parent(path)
    s = path.lstat()
    require(
        stat.S_ISREG(s.st_mode) and not getattr(s, "st_file_attributes", 0) & 0x400,
        "plain_file_required",
    )
    require(s.st_nlink == 1, "linked_file_refused")
    return path, (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)


def read_json(path, expected, guard):
    require(sha(expected), "metadata_hash_required")
    path, before = plain(path)
    require(before[2] <= guard.limits.max_metadata_bytes, "metadata_limit")
    guard.check()
    data = path.read_bytes()
    require(
        hashlib.sha256(data).hexdigest() == expected and plain(path)[1] == before,
        "metadata_changed",
    )
    value = json.loads(data)
    require(isinstance(value, dict), "metadata_object_required")
    return value


def hash_file(path, expected, maximum, guard, *, magic=None):
    require(sha(expected), "archive_hash_required")
    path, before = plain(path)
    require(0 < before[2] <= maximum, "archive_size_limit")
    h = hashlib.sha256()
    with path.open("rb") as stream:
        if magic is not None:
            require(stream.read(len(magic)) == magic, "archive_magic_invalid")
            stream.seek(0)
        while part := stream.read(1024**2):
            guard.check()
            h.update(part)
    require(h.hexdigest() == expected and plain(path)[1] == before, "archive_changed")
    return before


def report_valid(value, target, limits):
    require(
        isinstance(value, dict)
        and value.get("protocol") == ordered.PROTOCOL
        and value.get("target") == target
        and value.get("indexes_valid") is True,
        "independent_report_required",
    )
    require(
        all(
            type(value.get(k)) is int and value[k] >= 0
            for k in ("rows", "series", "original_traces")
        ),
        "invalid_report_counts",
    )
    require(
        value["series"] <= value["rows"] <= limits.max_rows
        and value["original_traces"] <= limits.max_traces,
        "report_population_limit",
    )
    require(
        all(sha(value.get(k)) for k in ("logical_sha256", "original_traces_sha256")),
        "report_hash_required",
    )


def source_valid(value, limits):
    report_valid(value, "source", limits)
    require(value.get("restore_verified") is False, "source_is_not_restore_receipt")
    catalogue = value.get("source_catalogue", {})
    require(
        isinstance(catalogue, dict)
        and re.fullmatch(r"[0-9A-Fa-f]+-[0-9A-Fa-f]+-[0-9]+", catalogue.get("snapshot", ""))
        and sha(catalogue.get("catalogue_sha256")),
        "exported_snapshot_required",
    )
    moments = [
        datetime.fromisoformat(v.replace("Z", "+00:00"))
        for v in (catalogue["snapshot_created_at"], value["checked_at"])
    ]
    require(
        all(v.utcoffset() is not None for v in moments) and moments[0] <= moments[1],
        "snapshot_chronology_invalid",
    )
    keys = value.get("original_trace_digests")
    require(
        isinstance(keys, list)
        and len(keys) == value["original_traces"]
        and all(sha(k) for k in keys)
        and keys == sorted(set(keys))
        and hashlib.sha256(codec.encode_json(keys).encode()).hexdigest()
        == value.get("original_trace_inventory_sha256"),
        "original_inventory_invalid",
    )


@dataclass(frozen=True)
class Evidence:
    """Paths and expected file hashes, not caller-supplied success flags."""

    base_result: Path
    base_source: Path
    current_source: Path
    archive_receipt: Path
    archive: Path
    expected_sha256: dict

    def load(self, guard):
        names = ("base_result", "base_source", "current_source", "archive_receipt")
        require(set(self.expected_sha256) == set(names), "exact_evidence_hashes_required")
        values = {k: read_json(getattr(self, k), self.expected_sha256[k], guard) for k in names}
        base, source, current, receipt = (values[k] for k in names)
        fixture = base.get("fixture_non_production")
        require(
            type(fixture) is bool
            and current.get("fixture_non_production") is fixture
            and source.get("fixture_non_production") is fixture,
            "fixture_scope_mismatch",
        )
        require(
            base.get("state") == "verified"
            and all(
                base.get(k) is True
                for k in (
                    "executed",
                    "data_equal",
                    "cluster_stopped",
                    "originals_preserved",
                    "archive_unchanged",
                )
            )
            and base.get("input_paths_modified") is False
            and base.get("production_contacted") is False
            and base.get("fixture_restore_verified" if fixture else "restore_verified") is True,
            "completed_independent_restore_required",
        )
        source_valid(source, guard.limits)
        source_valid(current, guard.limits)
        restored = base.get("verification")
        report_valid(restored, "restored", guard.limits)
        ordered.equal_reports(source, restored)
        require(
            base.get("source_report_sha256") == self.expected_sha256["base_source"]
            and base.get("archive_receipt_sha256") == self.expected_sha256["archive_receipt"]
            and base.get("original_trace_inventory_sha256")
            == source["original_trace_inventory_sha256"],
            "base_receipt_binding_invalid",
        )
        require(
            all(
                receipt.get(k) is True
                for k in (
                    "received",
                    "published",
                    "exit_waited",
                    "child_stopped",
                    "threads_stopped",
                )
            )
            and type(receipt.get("exit_code")) is int
            and receipt["exit_code"] == 0
            and sha(receipt.get("sha256"))
            and base.get("archive_sha256") == receipt["sha256"],
            "completed_archive_receipt_required",
        )
        signature = hash_file(
            self.archive,
            receipt["sha256"],
            guard.limits.max_input_archive_bytes,
            guard,
            magic=b"PGDMP",
        )
        require(
            type(receipt.get("bytes")) is int and signature[2] == receipt["bytes"],
            "archive_receipt_size_invalid",
        )
        require(
            datetime.fromisoformat(current["checked_at"].replace("Z", "+00:00"))
            >= datetime.fromisoformat(source["checked_at"].replace("Z", "+00:00"))
            and datetime.fromisoformat(
                current["source_catalogue"]["snapshot_created_at"].replace("Z", "+00:00")
            )
            >= datetime.fromisoformat(
                source["source_catalogue"]["snapshot_created_at"].replace("Z", "+00:00")
            ),
            "current_snapshot_predates_base",
        )
        return {
            "schema": SCHEMA,
            "protocol": ordered.PROTOCOL,
            "evidence_sha256": dict(self.expected_sha256),
            "archive_sha256": receipt["sha256"],
            "archive_bytes": receipt["bytes"],
            "base": source,
            "current": current,
            "fixture_non_production": fixture,
            "production_proof": False,
        }


@dataclass
class Snapshot:
    """Factories must reopen the same stable snapshot; records is a bounded lookup.

    rows yields C-ordered (series,time,digest,payload). traces yields every
    ORIGINAL (digest,byte_count,payload), including financial-only entries.
    Generated compact-only records belong in records, not the original iterator.
    """

    rows: object
    traces: object
    records: object


class LimitedLookup(ordered.Lookup):
    """Same bounded LRU, but reject large SQL payloads before JSON validation."""

    def __init__(self, db, table, check, maximum):
        super().__init__(db, table, check)
        self.maximum = maximum

    def __getitem__(self, digest):
        self.check()
        require(sha(digest), "invalid_trace_key")
        if digest in self.cache:
            self.cache.move_to_end(digest)
            return self.cache[digest]
        row = self.db.execute(
            "SELECT digest,byte_count,CASE WHEN octet_length(payload)<=%s "
            "THEN payload ELSE NULL END FROM " + self.table + " WHERE digest=%s",
            (self.maximum, digest),
        ).fetchone()
        if row is None:
            raise KeyError(digest)
        require(
            row[0] == digest
            and isinstance(row[2], str)
            and type(row[1]) is int
            and 0 <= row[1] <= self.maximum
            and len(row[2].encode("utf8")) <= self.maximum,
            "trace_size_limit",
        )
        record = validated_trace(row)
        while self.cache and (
            len(self.cache) >= 256 or self.bytes + record.byte_count > self.maximum
        ):
            _, previous = self.cache.popitem(last=False)
            self.bytes -= previous.byte_count
        self.cache[digest] = record
        self.bytes += record.byte_count
        return record


def pg_readers(db, target, *, check, limits=DEFAULT_LIMITS):
    """Reuse maintained fixed-table C-order readers inside an EXISTING snapshot.

    No connect/BEGIN/COMMIT here. A PostgreSQL write sink is deliberately not
    supplied: applying to a completed restore needs separately reviewed ownership,
    checkpoint retirement, disk guards and transaction/attempt receipts.
    """
    require(target in ordered.TABLES, "fixed_read_target_required")
    require(
        db.info.transaction_status.name == "INTRANS"
        and db.info.encoding.lower().replace("-", "") == "utf8",
        "active_utf8_snapshot_required",
    )
    settings = db.execute(
        "SELECT current_setting('transaction_read_only'),current_setting('transaction_isolation')"
    ).fetchone()
    require(
        settings[0] == "on" and settings[1] in ("repeatable read", "serializable"),
        "readonly_repeatable_snapshot_required",
    )
    table, traces, has_original = ordered.TABLES[target]

    def checked():
        require(db.info.transaction_status.name == "INTRANS", "snapshot_ended")
        check()

    def payload_rows(name, statement, params):
        # A 400-row fetch can allocate 400 * 16 MiB before Python's row guard.
        # Named cursors keep SQL output bounded; wide payloads arrive one at a time.
        with db.cursor(name=name) as cursor:
            cursor.execute(statement, params)
            while True:
                checked()
                batch = cursor.fetchmany(1)
                if not batch:
                    break
                yield batch[0]

    def rows():
        for (series,) in ordered._cursor_rows(
            db, "delta_series_" + target, ordered._series_statement(table), (), checked
        ):
            yield from payload_rows(
                "delta_rows_" + target,
                "SELECT series,time,digest,CASE WHEN octet_length(payload)<=%s "
                "THEN payload ELSE NULL END FROM "
                + table
                + ' WHERE series=%s ORDER BY time COLLATE "C"',
                (limits.max_record_bytes, series),
            )

    def originals():
        yield from payload_rows(
            "delta_traces_" + target,
            "SELECT digest,byte_count,CASE WHEN octet_length(payload)<=%s "
            "THEN payload ELSE NULL END FROM "
            + traces
            + (" WHERE original" if has_original else "")
            + ' ORDER BY digest COLLATE "C"',
            (limits.max_record_bytes,),
        )

    return Snapshot(rows, originals, LimitedLookup(db, traces, checked, limits.max_record_bytes))


class Scan:
    def __init__(self, snapshot, expected, guard):
        self.snapshot, self.expected, self.guard = snapshot, expected, guard
        self.rows_count = self.series = self.traces_count = 0
        self.row_hash, self.trace_hash = hashlib.sha256(), hashlib.sha256()

    def rows(self):
        previous = None
        for raw in self.snapshot.rows():
            self.guard.check()
            codec.validate_series(raw)
            require(
                len(raw[3].encode("utf8")) <= self.guard.limits.max_record_bytes, "row_size_limit"
            )
            key = tuple(v.encode("utf8") for v in raw[:2])
            require(previous is None or key > previous, "duplicate_or_unordered_row")
            body = identity(raw, self.snapshot.records, self.guard.limits.max_record_bytes)
            require(len(body) <= self.guard.limits.max_record_bytes, "logical_row_size_limit")
            self.row_hash.update(body)
            self.series += previous is None or previous[0] != key[0]
            self.rows_count += 1
            require(self.rows_count <= self.expected["rows"], "row_count_exceeded")
            previous = key
            # Compare canonical framing directly. Unchanged rows must not be
            # JSON-decoded and serialized AGAIN merely to discard that result.
            yield key, body
        require(
            self.rows_count == self.expected["rows"]
            and self.series == self.expected["series"]
            and self.row_hash.hexdigest() == self.expected["logical_sha256"],
            "full_row_report_mismatch",
        )

    def traces(self):
        previous = None
        for raw in self.snapshot.traces():
            self.guard.check()
            require(isinstance(raw, (list, tuple)) and len(raw) == 3, "original_trace_shape")
            require(
                isinstance(raw[2], str)
                and len(raw[2].encode("utf8")) <= self.guard.limits.max_record_bytes,
                "trace_size_limit",
            )
            validated_trace(raw)
            key = raw[0]
            require(previous is None or key > previous, "duplicate_or_unordered_trace")
            require(
                self.traces_count < self.expected["original_traces"]
                and self.expected["original_trace_digests"][self.traces_count] == key,
                "original_trace_inventory_mismatch",
            )
            self.trace_hash.update(frame(list(raw)))
            self.traces_count += 1
            previous = key
            yield key, list(raw)
        require(
            self.traces_count == self.expected["original_traces"]
            and self.trace_hash.hexdigest() == self.expected["original_traces_sha256"],
            "full_trace_report_mismatch",
        )


def merge(old, new):
    """One lookahead from each fully consumed, independently verified stream."""
    old, new = iter(old), iter(new)
    a, b = next(old, None), next(new, None)
    while a is not None or b is not None:
        if b is None or a is not None and a[0] < b[0]:
            yield a[0], a[1], None
            a = next(old, None)
        elif a is None or b[0] < a[0]:
            yield b[0], None, b[1]
            b = next(new, None)
        else:
            yield a[0], a[1], b[1]
            a, b = next(old, None), next(new, None)


def inline_row(body):
    if body is None:
        return None
    value = json.loads(body[8:])
    return [*value[:3], codec.encode_json(value[3])]


def build(destination, evidence, base, current, *, limits=DEFAULT_LIMITS, check=lambda: None):
    """Atomically publish NEW JSONL only after both full input scans match receipts.

    On interruption a uniquely named partial is preserved; it is not resumable
    or accepted as a completed archive. No existing file is replaced.
    """
    guard = Guard(limits, check)
    header = evidence.load(guard)
    destination = Path(destination)
    files._plain_parent(destination)
    require(not destination.exists() and not destination.is_symlink(), "destination_exists")
    partial = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".partial")
    old, new = Scan(base, header["base"], guard), Scan(current, header["current"], guard)
    counts = {"trace_additions": 0, "inserted": 0, "updated": 0, "deleted": 0}
    digest, size = hashlib.sha256(), 0
    with partial.open("xb") as stream:

        def emit(value):
            nonlocal size
            guard.check()
            body = codec.encode_json(value).encode("utf8") + b"\n"
            require(len(body) <= limits.max_record_bytes, "record_size_limit")
            require(size + len(body) <= limits.max_archive_bytes, "archive_size_limit")
            require(
                files._disk_free(destination.parent) >= limits.min_free_bytes + len(body),
                "disk_floor",
            )
            stream.write(body)
            digest.update(body)
            size += len(body)

        emit({"kind": "header", "value": header})
        for _, before, after in merge(old.traces(), new.traces()):
            require(after is not None, "original_trace_deleted")
            require(before is None or before == after, "original_trace_changed")
            if before is None:
                emit({"kind": "trace", "after": after})
                counts["trace_additions"] += 1
        emit({"kind": "rows"})
        for _, before, after in merge(old.rows(), new.rows()):
            if before != after:
                emit({"kind": "row", "before": inline_row(before), "after": inline_row(after)})
                counts[
                    "inserted" if before is None else "deleted" if after is None else "updated"
                ] += 1
        # Recheck every receipt and the original archive before publication.
        require(evidence.load(guard) == header, "evidence_changed")
        emit({"kind": "complete", "counts": counts, "prefix_sha256": digest.hexdigest()})
        stream.flush()
        os.fsync(stream.fileno())
        written = os.fstat(stream.fileno())
        guard.check()
    files._plain_parent(destination)
    require(
        plain(partial)[1] == (written.st_dev, written.st_ino, written.st_size, written.st_mtime_ns)
        and written.st_size == size,
        "partial_changed_before_publication",
    )
    files._rename_exclusive(partial, destination)
    files._sync_directory(destination.parent)
    return {
        "schema": SCHEMA,
        "state": "complete",
        "archive_sha256": digest.hexdigest(),
        "bytes": size,
        **counts,
        "production_proof": False,
        "permission_to_execute": False,
        "fixture_non_production": header["fixture_non_production"],
    }


def events(stream, limits, guard, expected_sha256):
    size, digest = 0, hashlib.sha256()
    completed = False
    while raw := stream.readline(limits.max_record_bytes + 1):
        guard.check()
        size += len(raw)
        require(
            not completed
            and raw.endswith(b"\n")
            and len(raw) <= limits.max_record_bytes
            and size <= limits.max_archive_bytes,
            "archive_framing_or_size",
        )
        try:
            value = json.loads(raw)
            require(codec.encode_json(value).encode("utf8") + b"\n" == raw, "noncanonical_record")
        except (ValueError, TypeError, RecursionError) as error:
            raise Refused("invalid_archive_record") from error
        require(isinstance(value, dict), "archive_record_object_required")
        if value.get("kind") == "complete":
            require(value.get("prefix_sha256") == digest.hexdigest(), "completion_hash_mismatch")
            completed = True
        digest.update(raw)
        yield value
    require(completed, "completion_marker_missing")
    require(digest.hexdigest() == expected_sha256, "archive_changed_during_apply")


def apply(archive, expected_sha256, evidence, sink, *, limits=DEFAULT_LIMITS, check=lambda: None):
    """Apply only inside caller's isolated atomic transaction, then full-verify.

    sink.transaction() must rollback every trace/row change on any exception.
    sink.snapshot() returns stable, sorted Snapshot readers for its current data.
    sink.add_original_trace(row) must preserve same-digest bytes, including any
    pre-existing compact-only record, and mark that exact record original.
    sink.change_row(before, after) must compare-and-set the exact logical before
    state; null before means absent and null after means exact-PK deletion.
    This core never connects to PG, modifies an old checkpoint, or issues DDL.
    The caller's check callback must include target-volume growth/free-space
    guards. The sink must separately bound blocked I/O/COMMIT and record any
    ambiguous post-COMMIT failure; Python cannot interrupt those operations.
    """
    guard = Guard(limits, check)
    header = evidence.load(guard)
    signature = hash_file(archive, expected_sha256, limits.max_archive_bytes, guard)
    counts = {"trace_additions": 0, "inserted": 0, "updated": 0, "deleted": 0}
    original_keys = frozenset(header["base"]["original_trace_digests"])
    with Path(archive).open("rb") as stream, sink.transaction():
        values = events(stream, limits, guard, expected_sha256)
        require(
            next(values, None) == {"kind": "header", "value": header}, "archive_evidence_mismatch"
        )
        baseline = Scan(sink.snapshot(), header["base"], guard)
        for _ in baseline.traces():
            pass
        for _ in baseline.rows():
            pass
        section, previous_trace, previous_key = "trace", None, None
        complete = False
        for value in values:
            kind = value.get("kind")
            if kind == "rows":
                require(section == "trace" and set(value) == {"kind"}, "invalid_section_order")
                section = "row"
            elif kind == "trace":
                require(
                    section == "trace" and set(value) == {"kind", "after"}, "invalid_trace_event"
                )
                row = value["after"]
                record = validated_trace(row)
                require(
                    previous_trace is None or record.digest > previous_trace,
                    "duplicate_trace_event",
                )
                require(
                    record.digest not in original_keys,
                    "trace_addition_already_original",
                )
                sink.add_original_trace(row)
                previous_trace = record.digest
                counts["trace_additions"] += 1
                require(
                    counts["trace_additions"]
                    <= header["current"]["original_traces"] - header["base"]["original_traces"],
                    "trace_additions_exceeded",
                )
            elif kind == "row":
                require(
                    section == "row" and set(value) == {"kind", "before", "after"},
                    "invalid_row_event",
                )
                before, after = value["before"], value["after"]
                require(
                    before != after and (before is not None or after is not None), "empty_change"
                )
                for row in (before, after):
                    if row is not None:
                        codec.validate_series(row)
                        require(
                            codec.FORMAT_KEY not in json.loads(row[3]), "delta_rows_must_be_inline"
                        )
                        identity(row, {}, limits.max_record_bytes)
                require(
                    before is None or after is None or before[:2] == after[:2], "row_key_changed"
                )
                key = tuple(s.encode("utf8") for s in (after or before)[:2])
                require(previous_key is None or key > previous_key, "duplicate_or_unordered_change")
                sink.change_row(before, after)
                previous_key = key
                counts[
                    "inserted" if before is None else "deleted" if after is None else "updated"
                ] += 1
            elif kind == "complete":
                require(
                    section == "row"
                    and set(value) == {"kind", "counts", "prefix_sha256"}
                    and isinstance(value["counts"], dict)
                    and all(type(n) is int and n >= 0 for n in value["counts"].values())
                    and value["counts"] == counts,
                    "completion_counts_mismatch",
                )
                complete = True
            else:
                raise Refused("unknown_archive_event")
        require(complete, "completion_marker_missing")
        restored = Scan(sink.snapshot(), header["current"], guard)
        for _ in restored.traces():
            pass
        for _ in restored.rows():
            pass
        require(plain(archive)[1] == signature and evidence.load(guard) == header, "input_changed")
        guard.check()
    # A post-COMMIT guard error never produces a success receipt. Caller owns
    # durable attempt/transaction diagnostics, just as the existing coordinator.
    guard.check()
    return {
        "schema": SCHEMA,
        "state": "independently_applied",
        "data_equal": True,
        "archive_sha256": expected_sha256,
        "base_evidence_sha256": header["evidence_sha256"],
        "current": {k: header["current"][k] for k in FIELDS},
        **counts,
        "production_proof": False,
        "current_fence_valid": False,
        "permission_to_execute": False,
        "indexes_verified": False,
        "fixture_non_production": header["fixture_non_production"],
    }
