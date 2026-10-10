"""Apply a verified delta ONLY to a separately provisioned local candidate DB.

No connect, cluster start, clone, DDL, checkpoint, production fence or deletion
permission. The caller copies the two completed-restore tables to a NEW owned
candidate first. delta.apply verifies that entire copy against the dated proof
before any change, then verifies all current rows/original traces before COMMIT.
An interrupted/ambiguous COMMIT requires inspection; this module never retries.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import delta_archive as delta

ROOT = Path(__file__).resolve().parents[3]
RUNS = ROOT / "work/pg-delta-candidate"
PORT = 54435
ROWS = "market_compact_restore.rows"
TRACES = "market_compact_restore.traces"
require = delta.require
Refused = delta.Refused


@dataclass(frozen=True)
class Candidate:
    run_id: str
    database_oid: int
    system_identifier: str
    base_result_sha256: str
    owner: str = "delta_owner"

    def __post_init__(self):
        require(
            re.fullmatch(r"[0-9a-f]{32}", self.run_id or "") is not None, "candidate_uuid_required"
        )
        require(
            type(self.database_oid) is int
            and self.database_oid > 0
            and re.fullmatch(r"[0-9]{10,20}", self.system_identifier or "") is not None
            and delta.sha(self.base_result_sha256)
            and self.owner == "delta_owner",
            "candidate_identity_required",
        )

    @property
    def database(self):
        return "market_delta_" + self.run_id

    @property
    def data_directory(self):
        return RUNS / self.run_id / "cluster-data"


class Budget:
    """Bounded local guard, NOT an OS quota; blocked I/O is bounded by PG timeouts.

    File/WAL logical growth and drive free space are checked before each write,
    at least each second, and before/after commit. Incoming reservations cover
    gaps between directory samples. They are conservative, not physical maxima.
    """

    def __init__(self, candidate, *, max_growth_bytes, max_total_bytes, max_seconds):
        require(
            all(type(v) is int and v > 0 for v in (max_growth_bytes, max_total_bytes, max_seconds))
            and max_seconds <= 5400,
            "invalid_candidate_budget",
        )
        self.path = candidate.data_directory
        delta.files._plain_parent(self.path / "PG_VERSION")
        require(self.path.is_dir(), "candidate_directory_missing")
        self.growth, self.total, self.seconds = max_growth_bytes, max_total_bytes, max_seconds
        self.started = time.monotonic()
        self.initial_free = self.free()
        self.initial_size = self.size()
        self.last_size, self.sampled_at, self.reserved = self.initial_size, self.started, 0
        require(
            self.initial_free >= delta.checkpoint.MIN_FREE + self.growth
            and self.initial_size <= self.total,
            "candidate_initial_space",
        )

    def free(self):
        return shutil.disk_usage(self.path).free

    def size(self):
        total, pending = 0, [self.path]
        while pending:
            path = pending.pop()
            with os.scandir(path) as entries:
                for entry in entries:
                    st = entry.stat(follow_symlinks=False)
                    require(
                        not entry.is_symlink() and not getattr(st, "st_file_attributes", 0) & 0x400,
                        "candidate_link_refused",
                    )
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        total += st.st_size
                    else:
                        raise Refused("candidate_nonregular_file")
            require(time.monotonic() - self.started < self.seconds, "candidate_deadline")
        return total

    def check(self, incoming=0, *, force=False):
        now = time.monotonic()
        remaining = self.seconds - (now - self.started)
        require(remaining > 0, "candidate_deadline")
        require(type(incoming) is int and incoming >= 0, "candidate_incoming_budget")
        if force or now - self.sampled_at >= 1:
            self.last_size, self.sampled_at, self.reserved = self.size(), now, 0
        free = self.free()
        self.reserved += incoming
        require(
            free >= delta.checkpoint.MIN_FREE + incoming
            and self.initial_free - free + incoming <= self.growth
            and self.last_size - self.initial_size + self.reserved <= self.growth
            and self.last_size + self.reserved <= self.total,
            "candidate_space_exhausted",
        )
        return max(1, int(remaining * 1000))


class PgDeltaSink:
    """Dedicated, idle, autocommit PG17 connection; fixed candidate tables only.

    Use with delta.apply or apply_to_candidate, never as a general SQL adapter.
    A candidate retains all originals and compact-only records; no trace DELETE.
    The original completed restore and its checkpoint must remain elsewhere.
    """

    def __init__(self, db, candidate, budget, *, limits=delta.DEFAULT_LIMITS):
        self.db, self.candidate, self.budget, self.limits = db, candidate, budget, limits
        self.active = False
        self.state = "not_started"
        self.transaction_committed = False

    def _identity(self):
        info, c = self.db.info, self.candidate
        require(
            self.db.autocommit is True
            and info.transaction_status.name == "IDLE"
            and info.host == "127.0.0.1"
            and info.port == PORT
            and info.dbname == c.database
            and info.user == c.owner,
            "dedicated_candidate_connection_required",
        )
        require(self.budget.path.resolve() == c.data_directory.resolve(), "candidate_budget_path")
        self.budget.check(force=True)
        actual = self.db.execute(
            "SELECT current_database(),host(inet_server_addr()),inet_server_port(),"
            "current_setting('data_directory'),current_user,d.oid,pg_get_userbyid(d.datdba),"
            "(SELECT system_identifier::text FROM pg_control_system()),"
            "current_setting('server_version_num')::int FROM pg_database d "
            "WHERE datname=current_database()"
        ).fetchone()
        require(
            actual[:3] == (c.database, "127.0.0.1", PORT)
            and Path(actual[3]).resolve() == c.data_directory.resolve()
            and actual[4:8] == (c.owner, c.database_oid, c.owner, c.system_identifier)
            and 170000 <= actual[8] < 180000,
            "actual_candidate_identity_differs",
        )

    def _catalogue(self):
        db = self.db
        # No copied checkpoint/public source/foreign tables are accepted. Only
        # the explicitly provisioned two-table candidate is writable here.
        relations = db.execute(
            "SELECT n.nspname||'.'||c.relname,c.relkind,pg_get_userbyid(c.relowner),"
            "c.relrowsecurity,c.relhasrules,c.relhassubclass FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT LIKE 'pg_%' "
            "AND n.nspname<>'information_schema' AND c.relkind IN ('r','p','f','v','m','S') "
            "ORDER BY 1"
        ).fetchall()
        require(
            relations
            == [
                (ROWS, "r", self.candidate.owner, False, False, False),
                (TRACES, "r", self.candidate.owner, False, False, False),
            ],
            "candidate_two_tables_without_checkpoint_required",
        )
        columns = db.execute(
            "SELECT a.attrelid::regclass::text,a.attname,format_type(a.atttypid,a.atttypmod),"
            "a.attnotnull FROM pg_attribute a WHERE a.attrelid IN (%s::regclass,%s::regclass) "
            "AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attrelid::regclass::text,a.attnum",
            (ROWS, TRACES),
        ).fetchall()
        expected = [
            (ROWS, k, t, True)
            for k, t in (
                ("series", "character varying(128)"),
                ("time", "character varying(40)"),
                ("digest", "character varying(64)"),
                ("payload", "text"),
            )
        ]
        expected += [
            (TRACES, k, t, True)
            for k, t in (
                ("digest", "character varying(64)"),
                ("byte_count", "integer"),
                ("payload", "text"),
                ("original", "boolean"),
            )
        ]
        require(columns == expected, "candidate_columns_differ")
        require(
            db.execute(
                "SELECT count(*) FROM pg_trigger WHERE tgrelid IN (%s::regclass,%s::regclass)",
                (ROWS, TRACES),
            ).fetchone()
            == (0,),
            "candidate_triggers_refused",
        )
        require(
            db.execute(
                "SELECT count(*) FROM pg_constraint WHERE conrelid IN "
                "(%s::regclass,%s::regclass) AND contype<>'p'",
                (ROWS, TRACES),
            ).fetchone()
            == (0,),
            "candidate_custom_constraints_refused",
        )
        indexes = db.execute(
            "SELECT i.indrelid::regclass::text,i.indisvalid,i.indisready,i.indisprimary,"
            "array(SELECT a.attname FROM unnest(i.indkey) WITH ORDINALITY k(num,n) "
            "JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.num ORDER BY k.n) "
            "FROM pg_index i WHERE i.indrelid IN (%s::regclass,%s::regclass) ORDER BY 1",
            (ROWS, TRACES),
        ).fetchall()
        require(
            indexes
            == [
                (ROWS, True, True, True, ["series", "time"]),
                (TRACES, True, True, True, ["digest"]),
            ],
            "candidate_indexes_differ",
        )

    @contextmanager
    def transaction(self):
        require(
            not self.active and self.state == "not_started", "single_candidate_attempt_required"
        )
        self._identity()
        db = self.db
        self.state = "begin_unconfirmed"
        try:
            db.execute("BEGIN ISOLATION LEVEL SERIALIZABLE READ WRITE")
            self.state = "applying"
            milliseconds = self.budget.check()
            for setting, value in (
                ("statement_timeout", min(milliseconds, 30000)),
                ("lock_timeout", 1000),
                ("transaction_timeout", milliseconds),
            ):
                db.execute("SELECT set_config(%s,%s,true)", (setting, str(value)))
            require(
                db.execute("SELECT pg_try_advisory_xact_lock(18431,123)").fetchone() == (True,),
                "candidate_already_owned",
            )
            db.execute(
                "LOCK TABLE market_compact_restore.rows,market_compact_restore.traces "
                "IN ACCESS EXCLUSIVE MODE NOWAIT"
            )
            self._catalogue()
            self.active = True
            yield
            self.budget.check(force=True)
        except BaseException:
            self.state = "rollback_unconfirmed"
            try:
                db.execute("ROLLBACK")
                self.state = "rolled_back"
            except BaseException:
                db.close()
            raise
        else:
            self.state = "commit_unknown"
            try:
                db.execute("COMMIT")
            except BaseException as error:
                db.close()
                raise Refused("candidate_commit_outcome_unknown_no_retry") from error
            self.state, self.transaction_committed = "committed", True
            self.budget.check(force=True)
        finally:
            self.active = False

    def _check(self, incoming=0):
        require(
            self.active and self.db.info.transaction_status.name == "INTRANS",
            "active_owned_candidate_required",
        )
        self.budget.check(incoming)

    def snapshot(self):
        self._check()

        def stream(name, statement, params=()):
            with self.db.cursor(name=name) as cur:
                cur.execute(statement, params)
                while True:
                    self._check()
                    rows = cur.fetchmany(1)
                    if not rows:
                        break
                    yield rows[0]

        def rows():
            for (series,) in stream(
                "delta_candidate_series", delta.ordered._series_statement(ROWS)
            ):
                yield from stream(
                    "delta_candidate_rows",
                    "SELECT series,time,digest,"
                    "CASE WHEN octet_length(payload)<=%s THEN payload ELSE NULL END "
                    "FROM market_compact_restore.rows WHERE series=%s "
                    'ORDER BY time COLLATE "C"',
                    (self.limits.max_record_bytes, series),
                )

        def traces():
            yield from stream(
                "delta_candidate_traces",
                "SELECT digest,byte_count,"
                "CASE WHEN octet_length(payload)<=%s THEN payload ELSE NULL END "
                'FROM market_compact_restore.traces WHERE original ORDER BY digest COLLATE "C"',
                (self.limits.max_record_bytes,),
            )

        return delta.Snapshot(
            rows,
            traces,
            delta.LimitedLookup(self.db, TRACES, self._check, self.limits.max_record_bytes),
        )

    def add_original_trace(self, row):
        self._check(len(row[2].encode("utf8")) * 4 + 4096)
        record = delta.validated_trace(row)
        require(record.byte_count <= self.limits.max_record_bytes, "trace_size_limit")
        old = self.db.execute(
            "SELECT digest,byte_count,payload FROM market_compact_restore.traces "
            "WHERE digest=%s FOR UPDATE",
            (record.digest,),
        ).fetchone()
        if old is None:
            self.db.execute("INSERT INTO market_compact_restore.traces VALUES(%s,%s,%s,true)", row)
        else:
            require(tuple(row) == old, "candidate_trace_conflict")
            self.db.execute(
                "UPDATE market_compact_restore.traces SET original=true WHERE digest=%s",
                (record.digest,),
            )

    def change_row(self, before, after):
        self._check(sum(len(r[3].encode("utf8")) for r in (before, after) if r) * 4 + 4096)
        require(before is not None or after is not None, "empty_candidate_change")
        require(before is None or after is None or before[:2] == after[:2], "candidate_key_changed")
        key = (after or before)[:2]
        found = self.db.execute(
            "SELECT series,time,digest,payload FROM market_compact_restore.rows "
            "WHERE series=%s AND time=%s FOR UPDATE",
            key,
        ).fetchone()
        require(
            found is None
            if before is None
            else found is not None
            and delta.identity(found, self.snapshot().records, self.limits.max_record_bytes)
            == delta.identity(before, {}, self.limits.max_record_bytes),
            "candidate_before_differs",
        )
        if after is None:
            self.db.execute(
                "DELETE FROM market_compact_restore.rows WHERE series=%s AND time=%s", key
            )
        else:
            delta.identity(after, {}, self.limits.max_record_bytes)
            if before is None:
                self.db.execute(
                    "INSERT INTO market_compact_restore.rows VALUES(%s,%s,%s,%s)", after
                )
            else:
                self.db.execute(
                    "UPDATE market_compact_restore.rows SET digest=%s,payload=%s "
                    "WHERE series=%s AND time=%s",
                    (*after[2:], *key),
                )


class ApplyFailure(Refused):
    """Fixed diagnostic only; original exception remains the explicit cause."""

    def __init__(self, sink):
        super().__init__("candidate_apply_failed_" + sink.state)
        self.transaction_state = sink.state
        self.transaction_committed = sink.transaction_committed


def apply_to_candidate(
    db, candidate, budget, archive, expected_sha256, evidence, *, limits=delta.DEFAULT_LIMITS
):
    """Local application evidence only. Caller persists attempt/error diagnostics.

    Provision/clone happens separately, without copying the completed restore's
    checkpoint. Neither this result nor any fixture permits production cutover.
    """
    require(
        candidate.base_result_sha256 == evidence.expected_sha256.get("base_result"),
        "candidate_base_binding_differs",
    )
    sink = PgDeltaSink(db, candidate, budget, limits=limits)
    try:
        result = delta.apply(
            archive, expected_sha256, evidence, sink, limits=limits, check=budget.check
        )
    except Exception as error:
        raise ApplyFailure(sink) from error
    return {
        **result,
        "candidate_database": candidate.database,
        "candidate_database_oid": candidate.database_oid,
        "transaction_committed": sink.transaction_committed,
        "transaction_state": sink.state,
        "production_proof": False,
        "permission_to_execute": False,
        "current_fence_valid": False,
    }
