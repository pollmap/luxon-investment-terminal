"""Verify a trusted dump in the reserved local PG17 cluster, then stop it.

Never drops databases, registers a service, modifies the dump, or contacts the VPS.
The target stocks_restore must not exist. A failed/finished database is retained.
Only this repository's work/tools/postgresql-restore directory is allowed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
from datetime import UTC, datetime

ROOT = Path(__file__).resolve().parents[1]
TOOL_ROOT = ROOT / "work/tools/postgresql-restore"
HOST, PORT, USER, DATABASE = "127.0.0.1", 54389, "restore_check", "stocks_restore"
MIN_FREE_BYTES = 45 * 1024 ** 3
REQUIRED_TABLES = {"public.market_snapshots", "public.market_jobs",
                   "public.market_time_series", "public.market_financial_facts"}


class VerificationError(RuntimeError):
    """Messages are fixed codes, safe to include in the public status report."""


def within(root, path):
    root, path = Path(root).resolve(), Path(path).resolve()
    if path == root or not path.is_relative_to(root):
        raise VerificationError("path_outside_reserved_workspace")
    return path


def validate_inputs(dump, sha256, output, *, root=TOOL_ROOT):
    root = Path(root).resolve()
    dump, output = within(root, dump), within(root, output)
    data = within(root, root / "verify-data")
    binaries = within(root, root / "pgsql/bin")
    password = within(root, root / "restore-password.tmp")
    if not dump.is_file() or dump.suffix != ".dump":
        raise VerificationError("complete_dump_required")
    if len(sha256) != 64 or any(c not in "0123456789abcdefABCDEF" for c in sha256):
        raise VerificationError("valid_expected_sha256_required")
    if output.exists() or output.is_relative_to(data) or output.is_relative_to(binaries):
        raise VerificationError("new_separate_output_directory_required")
    if not data.is_dir() or (data / "PG_VERSION").read_text().strip() != "17":
        raise VerificationError("reserved_postgres17_cluster_required")
    if (data / "postmaster.pid").exists():
        raise VerificationError("reserved_cluster_already_has_pid_file")
    for executable in ("pg_ctl.exe", "pg_restore.exe", "postgres.exe"):
        if not (binaries / executable).is_file():
            raise VerificationError("postgres_binary_missing")
    if not password.is_file():
        raise VerificationError("password_file_missing")
    free = shutil.disk_usage(data).free
    if free < MIN_FREE_BYTES:
        raise VerificationError("less_than_45_gib_free")
    with dump.open("rb") as stream:
        if stream.read(5) != b"PGDMP":
            raise VerificationError("custom_format_dump_required")
        stream.seek(0)
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != sha256.lower():
        raise VerificationError("dump_sha256_mismatch")
    # Never attach to an existing server even if it accepts the same credentials.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((HOST, PORT))
        except OSError as exc:
            raise VerificationError("reserved_loopback_port_in_use") from exc
    return {"root": root, "dump": dump, "output": output, "data": data,
            "binaries": binaries, "password": password,
            "sha256": actual, "free_bytes_before": free}


def hidden_options():
    if os.name != "nt":
        return {}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    return {"creationflags": subprocess.CREATE_NO_WINDOW, "startupinfo": startup}


def run_command(command, log, env, *, timeout=120, allowed=(0,)):
    with Path(log).open("ab") as stream:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=stream,
            stderr=subprocess.STDOUT, env=env, timeout=timeout, **hidden_options())
    if result.returncode not in allowed:
        raise VerificationError(f"command_failed_exit_{result.returncode}")
    return result.returncode


def restore_command(binaries, dump):
    # No --clean/--create: the archive cannot select or replace the source DB.
    return [str(binaries / "pg_restore.exe"), "--exit-on-error", "--no-owner",
            "--no-acl", "--no-tablespaces", "--jobs=2", "--no-password",
            "--host", HOST, "--port", str(PORT), "--username", USER,
            "--dbname", DATABASE, str(dump)]


def connect(password, database):
    import psycopg
    from psycopg.rows import dict_row
    return psycopg.connect(host=HOST, port=PORT, user=USER, password=password,
        dbname=database, connect_timeout=15, autocommit=True, row_factory=dict_row,
        options="-c statement_timeout=900000 -c lock_timeout=10000")


def check_server(connection, data):
    row = connection.execute("SELECT current_setting('data_directory') AS data, "
        "host(inet_server_addr()) AS host, inet_server_port() AS port, "
        "current_user AS username, current_setting('server_version_num')::int AS version").fetchone()
    if (Path(row["data"]).resolve() != data.resolve() or row["host"] != HOST
            or row["port"] != PORT or row["username"] != USER
            or not 170000 <= row["version"] < 180000):
        raise VerificationError("connected_server_identity_mismatch")


def create_empty_database(connection):
    if connection.execute("SELECT 1 FROM pg_database WHERE datname=%s", (DATABASE,)).fetchone():
        raise VerificationError("target_database_already_exists_preserved")
    # Fixed identifier and template0; no user input is interpolated into SQL.
    connection.execute("CREATE DATABASE stocks_restore TEMPLATE template0 ENCODING 'UTF8'")


def inspect_database(connection):
    from psycopg import sql
    tables = connection.execute("SELECT n.nspname AS schema, c.relname AS name "
        "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.relkind IN ('r','p') AND n.nspname NOT IN ('pg_catalog','information_schema') "
        "AND n.nspname NOT LIKE 'pg_toast%' ORDER BY 1,2").fetchall()
    counts = {}
    for row in tables:
        query = sql.SQL("SELECT count(*) AS rows FROM {}.{}").format(
            sql.Identifier(row["schema"]), sql.Identifier(row["name"]))
        counts[f"{row['schema']}.{row['name']}"] = connection.execute(query).fetchone()["rows"]
    missing = sorted(REQUIRED_TABLES.difference(counts))
    indexes = connection.execute("SELECT n.nspname AS schema, t.relname AS table, "
        "c.relname AS name, i.indisvalid AS valid, i.indisready AS ready, i.indisprimary AS primary "
        "FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid "
        "JOIN pg_class t ON t.oid=i.indrelid JOIN pg_namespace n ON n.oid=t.relnamespace "
        "WHERE n.nspname NOT IN ('pg_catalog','information_schema') "
        "AND n.nspname NOT LIKE 'pg_toast%' ORDER BY 1,2,3").fetchall()
    invalid_indexes = [row for row in indexes if not row["valid"] or not row["ready"]]
    invalid_constraints = connection.execute("SELECT n.nspname AS schema, c.conname AS name "
        "FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace "
        "WHERE NOT c.convalidated AND n.nspname NOT IN ('pg_catalog','information_schema')").fetchall()
    result = {"table_rows": counts, "missing_required_tables": missing,
        "indexes": indexes, "invalid_indexes": invalid_indexes,
        "unvalidated_constraints": invalid_constraints,
        "database_bytes": connection.execute("SELECT pg_database_size(current_database()) AS bytes").fetchone()["bytes"]}
    if not missing:
        result["snapshot_groups"] = connection.execute(
            "SELECT split_part(key, ':', 1) AS kind, count(*) AS rows "
            "FROM public.market_snapshots GROUP BY 1 ORDER BY 1").fetchall()
        result["series_groups"] = connection.execute(
            "SELECT split_part(series, ':', 1) AS kind, count(*) AS rows, "
            "min(time) AS first, max(time) AS last FROM public.market_time_series GROUP BY 1 ORDER BY 1").fetchall()
        result["universe_snapshot_exists"] = bool(connection.execute(
            "SELECT 1 FROM public.market_snapshots WHERE key='universe'").fetchone())
    nonempty = all(counts.get(name, 0) > 0 for name in REQUIRED_TABLES if name != "public.market_jobs")
    primary = {f"{row['schema']}.{row['table']}" for row in indexes if row["primary"]}
    result["passed"] = (not missing and nonempty and not invalid_indexes and not invalid_constraints
                        and REQUIRED_TABLES.issubset(primary) and result.get("universe_snapshot_exists", False))
    return result


def verify(dump, sha256, output, *, timeout=14400):
    started = time.monotonic()
    report = {"started_at": datetime.now(UTC).isoformat(), "status": "running", "stage": "preflight",
        "passed": False, "host": HOST, "port": PORT, "database": DATABASE,
        "production_contacted": False, "existing_database_deleted": False,
        "source_content_equality_verified": False,
        "scope": "isolated dump restore, exact restored row counts, required tables and index validity"}
    paths, start_attempted, env = None, False, None

    def checkpoint(stage=None):
        if stage:
            report["stage"] = stage
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        if paths and paths["output"].is_dir():
            try:
                temp = paths["output"] / "verification.json.tmp"
                temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                temp.replace(paths["output"] / "verification.json")
            except OSError as exc:
                # A full disk or report permission error must not skip pg_ctl stop.
                report["report_write_failure"] = type(exc).__name__

    try:
        paths = validate_inputs(dump, sha256, output)
        paths["output"].mkdir(parents=True, exist_ok=False)
        report.update(dump_sha256=paths["sha256"], dump_bytes=paths["dump"].stat().st_size,
                      free_bytes_before=paths["free_bytes_before"])
        password = paths["password"].read_text(encoding="utf-8").rstrip("\r\n")
        if not 12 <= len(password) <= 4096 or "\x00" in password:
            raise VerificationError("invalid_password_file")
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith("PG")}
        env.update(PGPASSWORD=password, PGCONNECT_TIMEOUT="15", PGCLIENTENCODING="UTF8")
        log = paths["output"] / "commands.log"
        ctl = [str(paths["binaries"] / "pg_ctl.exe"), "-D", str(paths["data"])]
        if run_command(ctl + ["status"], log, env, allowed=(0, 3)) == 0:
            raise VerificationError("reserved_cluster_already_running")
        checkpoint("starting_local_cluster")
        start_attempted = True
        options = f"-h {HOST} -p {PORT} -c shared_buffers=128MB -c max_connections=8 -c maintenance_work_mem=128MB"
        run_command(ctl + ["-l", str(paths["output"] / "postgres.log"), "-w", "-t", "60",
                           "-o", options, "start"], log, env)
        report["local_cluster_started"] = True
        checkpoint("creating_empty_database")
        with connect(password, "postgres") as connection:
            check_server(connection, paths["data"])
            create_empty_database(connection)
        report["new_database_created"] = True
        with connect(password, DATABASE) as connection:
            check_server(connection, paths["data"])
            count = connection.execute("SELECT count(*) AS rows FROM pg_tables "
                "WHERE schemaname NOT IN ('pg_catalog','information_schema')").fetchone()["rows"]
            if count:
                raise VerificationError("new_target_database_not_empty")
        if shutil.disk_usage(paths["data"]).free < MIN_FREE_BYTES:
            raise VerificationError("less_than_45_gib_free_before_restore")
        checkpoint("restoring")
        run_command(restore_command(paths["binaries"], paths["dump"]),
                    paths["output"] / "restore.log", env, timeout=timeout)
        report["pg_restore_succeeded"] = True
        checkpoint("inspecting_restored_database")
        with connect(password, DATABASE) as connection:
            check_server(connection, paths["data"])
            report["restore"] = inspect_database(connection)
        if not report["restore"]["passed"]:
            raise VerificationError("restored_data_checks_failed")
        report["status"] = "verified"
        report["passed"] = True
    except (Exception, KeyboardInterrupt) as exc:
        report["status"] = "failed"
        report["failure"] = str(exc) if isinstance(exc, VerificationError) else type(exc).__name__
        report["passed"] = False
    finally:
        report["failed_stage"] = report["stage"] if not report["passed"] else None
        if start_attempted:
            checkpoint("stopping_local_cluster")
            try:
                if run_command(ctl + ["status"], log, env, allowed=(0, 3)) == 0:
                    run_command(ctl + ["-m", "fast", "-w", "-t", "60", "stop"], log, env)
                report["local_cluster_stopped"] = run_command(ctl + ["status"], log, env, allowed=(0, 3)) == 3
                if not report["local_cluster_stopped"]:
                    raise VerificationError("local_cluster_still_running")
            except Exception as exc:
                report.update(status="failed", passed=False, local_cluster_stopped=False)
                report["stop_failure"] = str(exc) if isinstance(exc, VerificationError) else type(exc).__name__
        else:
            report["local_cluster_started"] = False
        if env is not None:
            env.pop("PGPASSWORD", None)
        if paths:
            report["free_bytes_after"] = shutil.disk_usage(paths["data"]).free
        if report.get("report_write_failure"):
            report.update(status="failed", passed=False)
        checkpoint("finished")
        if report.get("report_write_failure"):
            report.update(status="failed", passed=False)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump", required=True, type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--out", required=True, type=Path, help="New directory inside reserved tool root")
    parser.add_argument("--timeout", type=int, default=14400)
    args = parser.parse_args()
    if not 60 <= args.timeout <= 43200:
        parser.error("timeout must be between 60 and 43200 seconds")
    report = verify(args.dump, args.sha256, args.out, timeout=args.timeout)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
