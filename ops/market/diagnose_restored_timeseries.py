"""One read-only local scan AFTER the separate restore has completed.

Never starts/stops a server. The caller owns cluster lifecycle. Connections close
on success/error; statement and session timeouts bound this isolated diagnostic.
No production address, DDL, provider client, migration or automatic retry.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[2]
RESTORE = ROOT / "work/restore-daily-20261010-v106"
DATA = RESTORE / "cluster-data"
REPORT = RESTORE / "verification.json"
PASSWORD = RESTORE / "local-password.txt"
HOST, PORT, USER, DATABASE = "127.0.0.1", 54420, "daily_restore", "stocks_restore"
OUTPUT = ROOT / "work/storage-v106/local-whole-history.json"

# One full scan enriches the earlier prefix-only count with endpoint and storage
# classification. Do NOT add another SELECT count(*) or group scan afterwards.
GROUP_SQL = """
SELECT
 CASE WHEN series LIKE 'toss:%' THEN 'toss:' || split_part(series, ':', 2)
      ELSE split_part(series, ':', 1) END AS family,
 split_part(series, ':', 1) AS prefix,
 CASE WHEN payload::json ->> '_market_storage' IS NULL THEN 'inline'
      WHEN payload::json ->> '_market_storage' = 'source-trace-sha256-v1'
        THEN 'source-trace-reference'
      ELSE 'unknown-envelope' END AS encoding,
 count(*) AS rows,
 sum(pg_column_size(payload))::bigint AS payload_physical_bytes,
 max(pg_column_size(payload))::bigint AS payload_max_physical_bytes
FROM public.market_time_series
GROUP BY 1,2,3 ORDER BY 1,3
"""


def verified_restore_report() -> tuple[dict, str]:
    raw = REPORT.read_bytes()
    result = json.loads(raw)
    if result.get("passed") is not True or result.get("pg_restore_succeeded") is not True:
        raise ValueError("restore_not_verified_complete")
    if result.get("status") == "running" or result.get("port") != PORT:
        raise ValueError("restore_running_or_wrong_port")
    rows = result.get("restored", {}).get("table_rows", {})
    if rows.get("public.market_time_series", 0) <= 0:
        raise ValueError("restore_table_count_missing")
    return result, hashlib.sha256(raw).hexdigest()


def collect() -> dict:
    restore, report_hash = verified_restore_report()
    password = PASSWORD.read_text(encoding="utf8").rstrip("\r\n")
    if len(password) < 12 or "\x00" in password:
        raise ValueError("invalid_local_password_file")
    started = time.monotonic()
    report = {
        "observed_at": datetime.now(UTC).isoformat(),
        "scope": "one read-only full scan of the completed isolated local restore",
        "production_contacted": False,
        "server_started_or_stopped": False,
        "source_restore_report_sha256": report_hash,
        "source_dump_sha256": restore.get("sha256"),
        "previous_series_groups": restore["restored"].get("series_groups"),
    }
    # Startup options make READ ONLY active before even the identity SELECT.
    with psycopg.connect(
        host=HOST,
        port=PORT,
        user=USER,
        password=password,
        dbname=DATABASE,
        connect_timeout=5,
        autocommit=False,
        row_factory=dict_row,
        application_name="local-v106-readonly-history-diagnostic",
        options=(
            "-c default_transaction_read_only=on -c statement_timeout=900000 "
            "-c lock_timeout=1000 -c idle_in_transaction_session_timeout=30000 "
            "-c work_mem=16MB -c temp_file_limit=131072 "
            "-c max_parallel_workers_per_gather=0 -c transaction_timeout=960000"
        ),
    ) as db:
        identity = db.execute("""SELECT current_setting('data_directory') AS path,
            current_setting('server_version_num')::int AS version,
            inet_server_port() AS port, host(inet_server_addr()) AS address,
            current_setting('transaction_read_only') AS readonly,
            current_database() AS database, current_user AS username""").fetchone()
        if (
            Path(identity["path"]).resolve() != DATA.resolve()
            or identity["port"] != PORT
            or identity["address"] != HOST
            or identity["readonly"] != "on"
            or identity["database"] != DATABASE
            or identity["username"] != USER
            or not 170000 <= identity["version"] < 180000
        ):
            raise ValueError("local_cluster_identity_mismatch")
        report["identity"] = identity
        report["relations"] = db.execute("""SELECT c.relname AS name,
            pg_relation_size(c.oid) AS heap_bytes,
            pg_indexes_size(c.oid) AS index_bytes,
            pg_total_relation_size(c.reltoastrelid) AS toast_bytes,
            pg_total_relation_size(c.oid) AS total_bytes,
            c.reltuples AS estimated_rows
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname IN
                ('market_time_series','market_source_traces') ORDER BY 1""").fetchall()
        report["collation"] = db.execute("""SELECT datcollate,datctype
            FROM pg_database WHERE datname=current_database()""").fetchone()
        scan_start = time.monotonic()
        groups = db.execute(GROUP_SQL).fetchall()
        report["full_scan_seconds"] = round(time.monotonic() - scan_start, 3)
        report["groups"] = groups
        expected_rows = restore["restored"]["table_rows"]["public.market_time_series"]
        actual_rows = sum(g["rows"] for g in groups)
        families = {}
        for group in groups:
            family = families.setdefault(group["family"], {"rows": 0, "encodings": {}})
            family["rows"] += group["rows"]
            family["encodings"][group["encoding"]] = group["rows"]
        report["family_distribution"] = [
            {
                "family": name,
                **family,
                "whole_table_row_fraction": family["rows"] / actual_rows,
                "encoding_fractions": {
                    key: value / family["rows"] for key, value in family["encodings"].items()
                },
            }
            for name, family in sorted(families.items())
        ]
        report["rows_match_restore_count"] = actual_rows == expected_rows
        earlier = {r["kind"]: r["rows"] for r in report["previous_series_groups"] or []}
        prefixes = {}
        for group in groups:
            prefixes[group["prefix"]] = prefixes.get(group["prefix"], 0) + group["rows"]
        report["prefix_counts_match_previous_scan"] = earlier == prefixes if earlier else None
        report["passed"] = report["rows_match_restore_count"] and earlier == prefixes
        db.rollback()
    report["connection_closed"] = True
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.parent != OUTPUT.parent.resolve() or output.exists():
        raise SystemExit("New report in work/storage-v106 required; existing file preserved")
    try:
        result = collect()
    except Exception as error:
        # Do not print a DSN, password, server exception contents or input rows.
        result = {
            "passed": False,
            "error_type": type(error).__name__,
            "production_contacted": False,
            "server_started_or_stopped": False,
            "automatic_retry": False,
        }
        with output.open("x", encoding="utf8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
        raise SystemExit("Local diagnostic failed; sanitized evidence preserved") from None
    with output.open("x", encoding="utf8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, default=str)
    print(
        json.dumps(
            {
                "passed": result["passed"],
                "report": str(output),
                "seconds": result["elapsed_seconds"],
            }
        )
    )


if __name__ == "__main__":
    main()
