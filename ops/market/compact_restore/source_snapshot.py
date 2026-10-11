"""Fixed-table snapshot for target backup; caller owns the process writer fence.

No connection, deployment, worker stop or file operation is performed here.
The guardian holds SHARE locks while pg_dump and the independent verifier import
its snapshot. Releasing it does not assert that an external writer fence exists.
"""

from __future__ import annotations

import hashlib
import re
from contextlib import contextmanager
from datetime import UTC, datetime

from psycopg import sql

from .copy_stream import Refused, encode_json
from .ordered_verify import verify_snapshot

TARGETS = ("public.market_source_traces", "public.market_time_series")
TABLE_SQL = "'public.market_source_traces'::regclass,'public.market_time_series'::regclass"
EXPECTED_COLUMNS = {
    "market_source_traces": (
        ("digest", "character varying(64)"),
        ("byte_count", "integer"),
        ("payload", "text"),
    ),
    "market_time_series": (
        ("series", "character varying(128)"),
        ("time", "character varying(40)"),
        ("digest", "character varying(64)"),
        ("payload", "text"),
    ),
}
EXPECTED_KEYS = {"market_source_traces": ["digest"], "market_time_series": ["series", "time"]}


def digest(value):
    return hashlib.sha256(encode_json(value).encode("utf8")).hexdigest()


def catalogue(db):
    """Capture both schemas, privileges and external dependencies without payloads."""
    identity = db.execute(
        "SELECT current_database(),current_user,current_setting('data_directory'),"
        "current_setting('server_version_num')::int,current_setting('server_encoding'),"
        "(SELECT datcollate FROM pg_database WHERE datname=current_database())"
    ).fetchone()
    if identity[4] != "UTF8" or not 170000 <= identity[3] < 180000:
        raise Refused("Expected UTF8 PostgreSQL 17 source")
    queries = {
        "relations": "SELECT c.oid,c.relname,c.relfilenode,c.relpersistence,c.reltablespace,"
        "c.relkind,c.relowner,pg_get_userbyid(c.relowner),c.relacl::text,c.relrowsecurity,"
        "c.relforcerowsecurity,pg_total_relation_size(c.oid) FROM pg_class c WHERE c.oid IN ("
        + TABLE_SQL
        + ") ORDER BY c.relname",
        "columns": "SELECT a.attrelid::regclass::text,a.attnum,a.attname,"
        "format_type(a.atttypid,a.atttypmod),a.attnotnull,a.attcollation,a.attidentity,"
        "a.attgenerated,pg_get_expr(d.adbin,d.adrelid) FROM pg_attribute a "
        "LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum "
        "WHERE a.attrelid IN (" + TABLE_SQL + ") AND a.attnum>0 AND NOT a.attisdropped "
        "ORDER BY a.attrelid,a.attnum",
        "indexes": "SELECT indrelid::regclass::text,pg_get_indexdef(indexrelid),"
        "indisvalid,indisready,indisprimary,"
        "array(SELECT a.attname FROM unnest(indkey) WITH ORDINALITY k(attnum,n) "
        "JOIN pg_attribute a ON a.attrelid=indrelid AND a.attnum=k.attnum ORDER BY k.n) "
        "FROM pg_index WHERE indrelid IN (" + TABLE_SQL + ") ORDER BY indrelid,indexrelid",
        "constraints": "SELECT conrelid::regclass::text,confrelid::regclass::text,contype,"
        "conname,convalidated,pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid IN ("
        + TABLE_SQL
        + ") OR confrelid IN ("
        + TABLE_SQL
        + ") ORDER BY oid",
        "triggers": "SELECT tgrelid::regclass::text,tgname,pg_get_triggerdef(oid) FROM "
        "pg_trigger WHERE NOT tgisinternal AND tgrelid IN (" + TABLE_SQL + ") ORDER BY oid",
        "views": "SELECT DISTINCT r.ev_class::regclass::text,d.refobjid::regclass::text "
        "FROM pg_depend d JOIN pg_rewrite r ON d.classid='pg_rewrite'::regclass "
        "AND d.objid=r.oid WHERE d.refclassid='pg_class'::regclass AND d.refobjid IN ("
        + TABLE_SQL
        + ")",
    }
    result = {"identity": list(identity)}
    for name, query in queries.items():
        rows = db.execute(query + " LIMIT 1025").fetchall()
        if len(rows) > 1024:
            raise Refused("Unreviewed source catalogue population")
        result[name] = [list(row) for row in rows]
    if len(result["relations"]) != 2 or any(
        r[3] != "p" or r[4] != 0 or r[5] != "r" or r[9] or r[10] for r in result["relations"]
    ):
        raise Refused("Only logged regular default-tablespace targets are supported")
    if {r[1] for r in result["relations"]} != set(EXPECTED_COLUMNS) or any(
        r[7] != identity[1] for r in result["relations"]
    ):
        raise Refused("Caller must be the exact owner of both targets")
    for name, expected in EXPECTED_COLUMNS.items():
        aliases = (name, "public." + name)
        columns = [row for row in result["columns"] if row[0] in aliases]
        primary = [row for row in result["indexes"] if row[0] in aliases and row[4]]
        if (
            [(r[1], r[2], r[3], r[4]) for r in columns]
            != [(i, field, kind, True) for i, (field, kind) in enumerate(expected, 1)]
            or len(primary) != 1
            or list(primary[0][5]) != EXPECTED_KEYS[name]
        ):
            raise Refused("Exact target columns and primary keys required")
    if len(result["columns"]) != 7 or len(result["constraints"]) != 2:
        raise Refused("Unexpected target columns or constraints")
    if (
        result["triggers"]
        or result["views"]
        or any(c[2] != "p" or not c[4] for c in result["constraints"])
        or any(not r[2] or not r[3] for r in result["indexes"])
        or any(c[6] or c[7] or c[8] is not None for c in result["columns"])
    ):
        raise Refused("Source dependencies require a separate restore contract")
    result["catalogue_sha256"] = digest(result)
    return result


def check_idle(db):
    if db.autocommit is not True or db.info.transaction_status.name != "IDLE":
        raise Refused("Dedicated idle autocommit connection required")


@contextmanager
def hold_snapshot(db, *, check, expected_database, expected_user):
    """Locks are bounded at acquisition; caller must cancel a stuck guardian."""
    check_idle(db)
    check()
    try:
        db.execute("BEGIN ISOLATION LEVEL REPEATABLE READ")
        db.execute("SET LOCAL lock_timeout='3s'")
        db.execute("SET LOCAL statement_timeout='60s'")
        db.execute("LOCK TABLE public.market_source_traces,public.market_time_series IN SHARE MODE")
        state = catalogue(db)
        if state["identity"][:2] != [expected_database, expected_user]:
            raise Refused("Source database identity differs")
        counts = db.execute(
            "SELECT (SELECT count(*) FROM public.market_source_traces),"
            "(SELECT count(*) FROM public.market_time_series)"
        ).fetchone()
        token = db.execute("SELECT pg_export_snapshot()").fetchone()[0]
        if not re.fullmatch(r"[0-9A-Fa-f]+-[0-9A-Fa-f]+-[0-9]+", token):
            raise Refused("Unexpected PostgreSQL snapshot identifier")
        state.update(
            snapshot=token,
            trace_rows=counts[0],
            series_rows=counts[1],
            snapshot_created_at=datetime.now(UTC).isoformat(),
        )
        check()
        yield state
        check()
    finally:
        # No target writes occur in this transaction; release locks on every exit.
        db.execute("ROLLBACK")


def verify_exported(db, snapshot, *, check):
    check_idle(db)
    token = snapshot.get("snapshot", "")
    if not isinstance(token, str) or not re.fullmatch(r"[0-9A-Fa-f]+-[0-9A-Fa-f]+-[0-9]+", token):
        raise Refused("Actual exported snapshot required")
    if any(
        type(snapshot.get(key)) is not int or snapshot[key] < 0
        for key in ("series_rows", "trace_rows")
    ):
        raise Refused("Exact exported population bounds required")
    check()
    try:
        db.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        db.execute(sql.SQL("SET TRANSACTION SNAPSHOT {}").format(sql.Literal(token)))
        db.execute("SET LOCAL statement_timeout='60s'")
        db.execute("SET LOCAL work_mem='16MB'")
        db.execute("SET LOCAL temp_file_limit='256MB'")
        actual = catalogue(db)
        if actual["identity"] != snapshot.get("identity") or actual[
            "catalogue_sha256"
        ] != snapshot.get("catalogue_sha256"):
            raise Refused("Imported source database or catalogue differs")
        result = verify_snapshot(
            db,
            "source",
            check=check,
            max_rows=snapshot["series_rows"],
            max_traces=snapshot["trace_rows"],
        )
        if (
            result["rows"] != snapshot["series_rows"]
            or result["original_traces"] != snapshot["trace_rows"]
        ):
            raise Refused("Whole source population differs from exported snapshot")
        keys = []
        with db.cursor(name="snapshot_original_digests") as cursor:
            cursor.execute(
                'SELECT digest FROM public.market_source_traces ORDER BY digest COLLATE "C"'
            )
            while True:
                check()
                rows = cursor.fetchmany(400)
                if not rows:
                    break
                for (key,) in rows:
                    if not re.fullmatch(r"[0-9a-f]{64}", key) or keys and key <= keys[-1]:
                        raise Refused("Invalid original trace inventory")
                    keys.append(key)
                    if len(keys) > snapshot["trace_rows"]:
                        raise Refused("Original trace inventory exceeds exported bound")
        if len(keys) != snapshot["trace_rows"]:
            raise Refused("Original trace inventory is incomplete")
        result.update(
            original_trace_digests=keys,
            original_trace_inventory_sha256=digest(keys),
            source_catalogue=snapshot,
            checked_at=datetime.now(UTC).isoformat(),
        )
        check()
        return result
    finally:
        db.execute("ROLLBACK")
