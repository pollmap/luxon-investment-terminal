"""Preparation-only resumable COPY coordinator. Never launches/connects to PG.

The caller owns a NEW isolated database, a deadline-bounded pg_restore stream,
and its process exit status. This module does not claim an operational restore.
The default CLI prints a plan and performs no filesystem/DB/process operation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

from . import copy_stream as codec

HERE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[3]

FORMAT = "compact-restore-checkpoint-v1"
EMPTY_HASH = hashlib.sha256(b"").hexdigest()
MIN_FREE = 6 * 1024**3
SCHEMA = "market_compact_restore"
DATABASE = "market_compact_restore_candidate"
PORT = 54433


def digest_valid(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def codec_identity():
    """Bind checkpoints to the actual parser, codec and coordinator source."""
    checksum = hashlib.sha256()
    for path in (
        HERE / "copy_stream.py",
        ROOT / "services/market_dashboard/source_trace_store.py",
        Path(__file__),
    ):
        body = path.read_bytes()
        checksum.update(len(body).to_bytes(8, "big") + body)
    return checksum.hexdigest()


@dataclass(frozen=True)
class Manifest:
    archive_sha256: str
    archive_bytes: int
    source_manifest_sha256: str
    codec_sha256: str
    trace_rows: int
    series_rows: int
    max_batch_rows: int = 400
    max_batch_bytes: int = 16 * 1024**2
    max_stream_bytes: int = 256 * 1024**3

    def __post_init__(self):
        if any(
            not digest_valid(v)
            for v in (self.archive_sha256, self.source_manifest_sha256, self.codec_sha256)
        ):
            raise codec.Refused("Independent archive/manifest/codec SHA256 required")
        if type(self.archive_bytes) is not int or self.archive_bytes <= 0:
            raise codec.Refused("Positive archive byte count required")
        if any(type(n) is not int or n < 0 for n in (self.trace_rows, self.series_rows)):
            raise codec.Refused("Exact original table counts required")
        self.limits()

    def limits(self):
        return codec.Limits(self.max_batch_rows, self.max_batch_bytes, self.max_stream_bytes)

    def identity(self):
        return codec.encode_json({"format": FORMAT, **asdict(self)})

    def counts(self):
        return {codec.TRACES: self.trace_rows, codec.SERIES: self.series_rows}


class Budget:
    """Cooperative guard; caller MUST separately bound blocked I/O and COMMIT.

    free_for_data is the actual PG data volume, not the archive drive. Max growth
    includes concurrent disk consumers conservatively; no compression assumption.
    """

    def __init__(
        self,
        data_directory,
        *,
        max_growth_bytes,
        max_seconds,
        free_for_data=None,
        monotonic=time.monotonic,
    ):
        if (
            type(max_growth_bytes) is not int
            or max_growth_bytes <= 0
            or type(max_seconds) is not int
            or not 1 <= max_seconds <= 21600
        ):
            raise codec.Refused("Explicit positive growth/time budgets required")
        self.clock = monotonic
        self.started = self.clock()
        self.max_seconds = max_seconds
        self.max_growth = max_growth_bytes
        self.data_directory = Path(data_directory).resolve()
        self.free = free_for_data or (lambda: shutil.disk_usage(self.data_directory).free)
        self.initial_free = self.free()
        if self.initial_free < MIN_FREE + self.max_growth:
            raise codec.Refused("Full compact restore growth budget plus reserve unavailable")

    def check(self, incoming=0):
        if type(incoming) is not int or incoming < 0:
            raise codec.Refused("Invalid next-write budget")
        remaining = self.max_seconds - (self.clock() - self.started)
        if remaining <= 0:
            raise codec.Refused("Restore elapsed-time budget exhausted")
        free = self.free()
        if free < MIN_FREE + incoming or self.initial_free - free + incoming > self.max_growth:
            raise codec.Refused("Restore disk budget exhausted; committed prefix preserved")
        return max(1, min(30000, int(remaining * 1000)))


def verify_archive(path, manifest, budget):
    """Read-only bounded hashing; partial/changed archives never become receipts."""
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size != manifest.archive_bytes:
        raise codec.Refused("Archive missing, linked, or wrong byte count")
    before = path.stat()
    checksum = hashlib.sha256()
    with path.open("rb") as source:
        while part := source.read(1024**2):
            budget.check()
            checksum.update(part)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_ino,
    ) or checksum.hexdigest() != manifest.archive_sha256:
        raise codec.Refused("Archive changed or SHA256 differs")
    return checksum.hexdigest()


@dataclass(frozen=True)
class Checkpoint:
    sequence: int = 0
    prefix_sha256: str = EMPTY_HASH
    trace_rows: int = 0
    series_rows: int = 0
    trace_complete: bool = False
    series_complete: bool = False

    def __post_init__(self):
        if (
            any(
                type(n) is not int or n < 0
                for n in (self.sequence, self.trace_rows, self.series_rows)
            )
            or not digest_valid(self.prefix_sha256)
            or type(self.trace_complete) is not bool
            or type(self.series_complete) is not bool
            or self.series_complete
            and not self.trace_complete
        ):
            raise codec.Refused("Invalid atomic checkpoint")

    def advance(self, event):
        if self.series_complete:
            raise codec.Refused("Input after completed table stream")
        is_batch = isinstance(event, codec.CopyBatch)
        if (
            event.table not in codec.TABLES
            or event.table == codec.SERIES
            and not self.trace_complete
        ):
            raise codec.Refused("Series must follow complete original trace table")
        if event.table == codec.TRACES and self.trace_complete:
            raise codec.Refused("Original trace input already ended")
        material = event.raw if is_batch else str(event.row_count).encode("ascii")
        tag = (event.table + (":batch:" if is_batch else ":end:")).encode("ascii")
        prefix = hashlib.sha256(
            bytes.fromhex(self.prefix_sha256) + tag + len(material).to_bytes(8, "big") + material
        ).hexdigest()
        values = asdict(self)
        values.update(sequence=self.sequence + 1, prefix_sha256=prefix)
        key = "trace_rows" if event.table == codec.TRACES else "series_rows"
        if is_batch:
            values[key] += event.row_count
        else:
            if values[key] != event.row_count:
                raise codec.Refused("COPY end count differs from committed prefix")
            values["trace_complete" if event.table == codec.TRACES else "series_complete"] = True
        return Checkpoint(**values)


DDL = (
    "CREATE SCHEMA market_compact_restore",
    "CREATE TABLE market_compact_restore.checkpoint "
    "(singleton integer PRIMARY KEY CHECK(singleton=1),"
    "identity text NOT NULL,state text NOT NULL)",
    "CREATE TABLE market_compact_restore.traces (digest varchar(64) PRIMARY KEY,"
    "byte_count integer NOT NULL,payload text NOT NULL,original boolean NOT NULL)",
    "CREATE TABLE market_compact_restore.rows "
    "(series varchar(128) NOT NULL,time varchar(40) NOT NULL,"
    "digest varchar(64) NOT NULL,payload text NOT NULL,PRIMARY KEY(series,time))",
)
TRACE_SELECT = "SELECT digest,byte_count,payload FROM market_compact_restore.traces WHERE digest=%s"
ROW_SELECT = (
    "SELECT series,time,digest,payload FROM market_compact_restore.rows WHERE series=%s AND time=%s"
)


class TraceLookup(Mapping):
    """One batch's verified LRU; never retain a full source ledger."""

    def __init__(self, db):
        self.db = db
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self.max_entries = 256
        self.max_bytes = 16 * 1024**2

    def __getitem__(self, key):
        if not digest_valid(key):
            raise KeyError(key)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        row = self.db.execute(TRACE_SELECT, (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        record = codec.validate_trace(row)
        if record.byte_count <= self.max_bytes:
            while self.cache and (
                len(self.cache) >= self.max_entries
                or self.cache_bytes + record.byte_count > self.max_bytes
            ):
                _, old = self.cache.popitem(last=False)
                self.cache_bytes -= old.byte_count
            self.cache[key] = record
            self.cache_bytes += record.byte_count
        return record

    def __iter__(self):
        raise codec.Refused("Full trace ledger materialization is forbidden")

    def __len__(self):
        raise codec.Refused("Full trace ledger materialization is forbidden")


class PgBatchSink:
    """Fixed isolated target schema; caller supplies dedicated idle connection.

    No public table mutation or process/connection creation. session advisory
    ownership serializes an entire attempt. Native staging drops each commit.
    """

    def __init__(self, db, manifest, budget):
        self.db, self.manifest, self.budget = db, manifest, budget
        self.locked = False
        self.checkpoint = None

    def open(self):
        self.budget.check()
        db = self.db
        info = db.info
        if (
            db.autocommit is not True
            or info.transaction_status.name != "IDLE"
            or info.host != "127.0.0.1"
            or info.port != PORT
            or info.dbname != DATABASE
        ):
            raise codec.Refused("A dedicated idle loopback compact-restore connection is required")
        actual = db.execute(
            "SELECT current_database(),host(inet_server_addr()),inet_server_port(),"
            "current_setting('data_directory')"
        ).fetchone()
        if (
            actual[:3] != (DATABASE, "127.0.0.1", PORT)
            or Path(actual[3]).resolve() != self.budget.data_directory
        ):
            raise codec.Refused("Isolated server identity or data volume differs")
        if db.execute("SELECT pg_try_advisory_lock(18431,114)").fetchone() != (True,):
            raise codec.Refused("Another restore owns this target")
        self.locked = True
        try:
            with db.transaction():
                exists = db.execute(
                    "SELECT to_regclass('market_compact_restore.checkpoint')"
                ).fetchone()[0]
                if exists is None:
                    for statement in DDL:
                        db.execute(statement)
                    self.checkpoint = Checkpoint()
                    db.execute(
                        "INSERT INTO market_compact_restore.checkpoint VALUES(1,%s,%s)",
                        (self.manifest.identity(), codec.encode_json(asdict(self.checkpoint))),
                    )
                else:
                    identity, state = db.execute(
                        "SELECT identity,state FROM market_compact_restore.checkpoint "
                        "WHERE singleton=1"
                    ).fetchone()
                    if identity != self.manifest.identity():
                        raise codec.Refused("Resume archive/codec/manifest/batch settings differ")
                    self.checkpoint = Checkpoint(**json.loads(state))
                self.check_counts(self.checkpoint)
            return self.checkpoint
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.locked:
            self.db.execute("SELECT pg_advisory_unlock(18431,114)")
            self.locked = False

    def check_counts(self, checkpoint):
        row = self.db.execute(
            "SELECT (SELECT count(*) FROM market_compact_restore.traces WHERE original),"
            "(SELECT count(*) FROM market_compact_restore.rows)"
        ).fetchone()
        if row != (checkpoint.trace_rows, checkpoint.series_rows):
            raise codec.Refused("Stored prefix row count differs from atomic checkpoint")

    def consume(self, event, before, after, *, replay):
        if not self.locked:
            raise codec.Refused("Restore target is not exclusively owned")
        self.budget.check(
            event.row_count * 128
            + (len(event.raw) * 4 if isinstance(event, codec.CopyBatch) else 0)
        )
        with self.db.transaction():
            db = self.db
            db.execute(
                "SELECT set_config('statement_timeout',%s,true)", (str(self.budget.check()),)
            )
            if isinstance(event, codec.CopyBatch):
                # NativeCopyStage requires explicit non-autocommit mode; a caller
                # connection remains autocommit outside the transaction. A narrow
                # view reports the active transaction without altering its owner.
                stage = codec.NativeCopyStage(TransactionView(db), limits=self.manifest.limits())
                stage.create()
                values = stage.decode(event)
                lookup = TraceLookup(db)
                if event.table == codec.TRACES:
                    if replay:
                        for value in values:
                            record = codec.validate_trace(value)
                            found = db.execute(
                                "SELECT digest,byte_count,payload,original FROM "
                                "market_compact_restore.traces WHERE digest=%s",
                                (record.digest,),
                            ).fetchone()
                            if found != (*value, True):
                                raise codec.Refused("Previously committed original trace differs")
                    else:
                        with db.cursor() as cursor:
                            cursor.executemany(
                                "INSERT INTO market_compact_restore.traces VALUES(%s,%s,%s,true)",
                                values,
                            )
                elif replay:
                    for value in values:
                        found = db.execute(ROW_SELECT, value[:2]).fetchone()
                        if found is None or codec.row_identity(value, lookup) != codec.row_identity(
                            found, lookup
                        ):
                            raise codec.Refused("Previously committed logical row differs")
                else:
                    rows, records = codec.compact_rows(
                        values, lookup, limits=self.manifest.limits()
                    )
                    with db.cursor() as cursor:
                        cursor.executemany(
                            "INSERT INTO market_compact_restore.traces VALUES(%s,%s,%s,false) "
                            "ON CONFLICT(digest) DO NOTHING",
                            [
                                (record.digest, record.byte_count, record.payload)
                                for record in records.values()
                            ],
                        )
                    # Read after INSERT independently of the input lookup cache.
                    lookup = TraceLookup(db)
                    for record in records.values():
                        if lookup[record.digest] != record:
                            raise codec.Refused("Original/new trace bytes conflict")
                    with db.cursor() as cursor:
                        cursor.executemany(
                            "INSERT INTO market_compact_restore.rows VALUES(%s,%s,%s,%s)", rows
                        )
            if not replay:
                changed = db.execute(
                    "UPDATE market_compact_restore.checkpoint SET state=%s WHERE singleton=1 "
                    "AND state=%s",
                    (codec.encode_json(asdict(after)), codec.encode_json(asdict(before))),
                )
                if changed.rowcount != 1:
                    raise codec.Refused("Checkpoint CAS failed")
            self.budget.check()
        # A deadline during COMMIT is never reported as success. Committed state
        # remains authoritative on next open; no unsafe compensating deletion.
        self.budget.check()


class TransactionView:
    """Only expose the existing active transaction to the native staging adapter."""

    autocommit = False

    def __init__(self, db):
        self._db = db

    @property
    def info(self):
        return self._db.info

    def execute(self, *args, **kwargs):
        return self._db.execute(*args, **kwargs)

    def cursor(self, *args, **kwargs):
        return self._db.cursor(*args, **kwargs)


def write_evidence(path, report):
    """Create-only, bounded evidence. A write failure retains its partial bytes."""
    path = Path(path)
    body = (codec.encode_json(report) + "\n").encode("utf-8")
    if len(body) > 32768:
        raise codec.Refused("Unexpected evidence size")
    with path.open("xb") as output:
        output.write(body)
        output.flush()
        os.fsync(output.fileno())


def restore_prefix(stream, sink, manifest, *, archive, budget, child_exit, evidence_path):
    """Replay from byte zero; skip writes only after comparing every saved row.

    child_exit must WAIT with an independent deadline and return real process
    status. Cooperative checks cannot interrupt a hung pipe or COMMIT. Caller
    supervises those; no operational CLI is supplied before that is implemented.
    """
    report = {
        "state": "starting",
        "restore_verified": False,
        "stream_complete": False,
        "archive_sha256": manifest.archive_sha256,
        "checkpoint_last_acknowledged": None,
    }
    try:
        if manifest.codec_sha256 != codec_identity():
            raise codec.Refused("Actual parser/codec/coordinator source differs")
        verify_archive(archive, manifest, budget)
        saved = sink.open()
        report["checkpoint_last_acknowledged"] = asdict(saved)
        current = Checkpoint()
        for event in codec.parse_copy_stream(
            stream, expected_rows=manifest.counts(), limits=manifest.limits()
        ):
            budget.check()
            following = current.advance(event)
            replay = following.sequence <= saved.sequence
            if replay and following.sequence == saved.sequence and following != saved:
                raise codec.Refused("Replay COPY prefix differs from saved checkpoint")
            sink.consume(event, current, following, replay=replay)
            current = following
            if not replay:
                report["checkpoint_last_acknowledged"] = asdict(current)
        if current.sequence < saved.sequence or not current.series_complete:
            raise codec.Refused("Stream ended before saved prefix or final COPY end")
        if child_exit() != 0:
            raise codec.Refused("pg_restore child did not exit successfully")
        verify_archive(archive, manifest, budget)
        sink.check_counts(current)
        report.update(state="awaiting_ordered_verification", stream_complete=True)
    except Exception as error:
        # Do not expose SQL, payloads, DSNs or exception messages in artifacts.
        report.update(
            state="interrupted", error_type=type(error).__name__, checkpoint_reload_required=True
        )
    finally:
        try:
            sink.close()
        except Exception as error:
            report.update(
                state="interrupted", stream_complete=False, cleanup_error=type(error).__name__
            )
    write_evidence(evidence_path, report)
    return report


def main():
    print(
        json.dumps(
            {
                "executed": False,
                "connects_to_database": False,
                "opens_process": False,
                "minimum_free_bytes": MIN_FREE,
                "batch_rows": 400,
                "resume": "archive SHA + atomic checkpoint + every prefix row",
                "remaining": [
                    "operational wrapper with independent connection/lock/deadline guards",
                    "latest fenced archive and independently verified source manifest",
                    "full target-only restore and ordered verification of that archive",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
