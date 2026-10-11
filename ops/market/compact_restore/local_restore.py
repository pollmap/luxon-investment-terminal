"""Explicit new-cluster target-only restore. Default CLI is an offline plan.

Only 127.0.0.1:54433 and a new work/target-restore/<uuid>/cluster-data are used.
Input archives, old clusters and production are never written or deleted. The
source report must come from the dump's exported snapshot; this runner verifies
its bound hashes, not the truth of an operator's unsupported receipt. A fixture
can establish data equality but never produces production restore proof.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import stat
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import checkpoint as cp
from . import copy_stream as codec
from . import ordered_verify as verify
from . import restore_supervisor as supervisor
from .archive_receiver import _rename_exclusive, _sync_directory

ROOT = Path(__file__).resolve().parents[3]
RUNS = ROOT / "work/target-restore"
BIN = ROOT / "work/tools/postgresql-restore/pgsql/bin"
PORT = 54433
DATABASE = "market_compact_restore_candidate"
ADMIN = "compact_restore_admin"
MAX_REPORT = 64 * 1024**2
MAX_RECEIPT = 64 * 1024
EXPECTED_COLUMNS = {
    "market_source_traces": [("digest", "character varying(64)"), ("byte_count", "integer"),
                             ("payload", "text")],
    "market_time_series": [("series", "character varying(128)"), ("time", "character varying(40)"),
                           ("digest", "character varying(64)"), ("payload", "text")],
}
EXPECTED_KEYS = {"market_source_traces": ["digest"], "market_time_series": ["series", "time"]}


class Refused(RuntimeError):
    """Fixed diagnostics only; never SQL, input bodies, passwords or tool output."""


def _signature(path):
    value = path.stat()
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


def _plain(path, *, directory=False):
    path = Path(path)
    if not path.is_absolute():
        raise Refused("absolute_path_required")
    for entry in (path, *path.parents):
        meta = entry.lstat()
        if stat.S_ISLNK(meta.st_mode) or getattr(meta, "st_file_attributes", 0) & 0x400:
            raise Refused("linked_input_refused")
    if not (path.is_dir() if directory else path.is_file()):
        raise Refused("plain_input_required")
    return path


def _json_file(path, maximum):
    path = _plain(path)
    before = _signature(path)
    if not 0 < before[2] <= maximum:
        raise Refused("input_size_limit")
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum or _signature(path) != before:
        raise Refused("input_changed")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise Refused("object_input_required")
    return value, hashlib.sha256(raw).hexdigest(), before


def _canonical_hash(value):
    return hashlib.sha256(codec.encode_json(value).encode("utf8")).hexdigest()


def validate_inputs(archive, receipt_path, source_path):
    """Read bounded metadata only. Full archive hashing follows under Budget."""
    archive = _plain(archive)
    receipt, receipt_sha, receipt_stat = _json_file(receipt_path, MAX_RECEIPT)
    source, source_sha, source_stat = _json_file(source_path, MAX_REPORT)
    if (receipt.get("received") is not True or receipt.get("published") is not True
            or receipt.get("exit_waited") is not True or type(receipt.get("exit_code")) is not int
            or receipt["exit_code"] != 0 or type(receipt.get("bytes")) is not int
            or receipt["bytes"] <= 5 or receipt["bytes"] != archive.stat().st_size
            or not cp.digest_valid(receipt.get("sha256"))
            or receipt.get("source_report_sha256") != source_sha):
        raise Refused("archive_receipt_binding_invalid")
    for value in (receipt, source):
        if "fixture_non_production" in value and type(value["fixture_non_production"]) is not bool:
            raise Refused("fixture_marker_invalid")
    # Reuse the same count/hash/protocol validation without claiming restoration.
    verify.equal_reports(source, {**source, "target": "restored"})
    catalogue = source.get("source_catalogue")
    keys = source.get("original_trace_digests")
    if (source.get("restore_verified") is not False or not isinstance(catalogue, dict)
            or catalogue.get("trace_rows") != source["original_traces"]
            or catalogue.get("series_rows") != source["rows"]
            or not re.fullmatch(r"[0-9A-Fa-f]+-[0-9A-Fa-f]+-[0-9]+",
                                str(catalogue.get("snapshot", "")))
            or not isinstance(keys, list) or len(keys) != source["original_traces"]
            or any(not cp.digest_valid(key) for key in keys)
            or any(left >= right for left, right in zip(keys, keys[1:], strict=False))
            or _canonical_hash(keys) != source.get("original_trace_inventory_sha256")):
        raise Refused("source_inventory_invalid")
    original_catalogue = {key: value for key, value in catalogue.items() if key not in (
        "catalogue_sha256", "snapshot", "trace_rows", "series_rows", "snapshot_created_at",
    )}
    if _canonical_hash(original_catalogue) != catalogue.get("catalogue_sha256"):
        raise Refused("source_catalogue_hash_invalid")
    identity = catalogue.get("identity", [])
    if (len(identity) != 6 or type(identity[3]) is not int or not 170000 <= identity[3] < 180000
            or identity[4] != "UTF8"):
        raise Refused("source_pg17_utf8_required")
    columns = {name: [] for name in EXPECTED_COLUMNS}
    for row in catalogue.get("columns", []):
        if (not isinstance(row, list) or len(row) != 9 or not isinstance(row[0], str)
                or row[0].removeprefix("public.") not in columns or row[4] is not True
                or row[6] or row[7] or row[8] is not None):
            raise Refused("source_columns_unsupported")
        name = row[0].removeprefix("public.")
        if row[1] != len(columns[name]) + 1:
            raise Refused("source_column_order_invalid")
        columns[name].append((row[2], row[3]))
    if columns != EXPECTED_COLUMNS:
        raise Refused("source_columns_unsupported")
    relations = catalogue.get("relations", [])
    if (len(relations) != 2 or any(not isinstance(row, list) or len(row) != 12
                                 for row in relations)
            or {row[1] for row in relations} != set(EXPECTED_COLUMNS)
            or any(row[3] != "p" or row[4] != 0 or row[5] != "r" or row[9] or row[10]
                   or row[7] != identity[1]
                   for row in relations)):
        raise Refused("source_relations_unsupported")
    indexes = catalogue.get("indexes", [])
    constraints = catalogue.get("constraints", [])
    if (catalogue.get("triggers") or catalogue.get("views")
            or len(constraints) != 2
            or any(len(row) != 6 or row[2] != "p" or row[4] is not True for row in constraints)
            or any(len(row) != 6 or row[2] is not True or row[3] is not True for row in indexes)
            or {row[0].removeprefix("public.") for row in indexes if row[4] is True}
            != set(EXPECTED_COLUMNS)):
        raise Refused("source_dependencies_unsupported")
    for name, expected in EXPECTED_KEYS.items():
        primary = [row for row in indexes if row[0].removeprefix("public.") == name
                   and row[4] is True]
        if len(primary) != 1 or primary[0][5] != expected:
            raise Refused("source_primary_key_unsupported")
    with archive.open("rb") as stream:
        if stream.read(5) != b"PGDMP":
            raise Refused("archive_magic_invalid")
    return {
        "archive": archive, "receipt_path": Path(receipt_path), "source_path": Path(source_path),
        "receipt": receipt, "source": source, "receipt_sha256": receipt_sha,
        "source_sha256": source_sha, "receipt_signature": receipt_stat,
        "source_signature": source_stat,
        "fixture_non_production": any(x.get("fixture_non_production", False)
                                      for x in (source, receipt)),
    }


def check_input_metadata(inputs):
    for label, maximum in (("receipt", MAX_RECEIPT), ("source", MAX_REPORT)):
        _, sha, signature = _json_file(inputs[label + "_path"], maximum)
        if sha != inputs[label + "_sha256"] or signature != inputs[label + "_signature"]:
            raise Refused("input_metadata_changed")


def ordered_toc(raw):
    """Select only two TABLE DATA entries; no schema/SQL from the dump executes."""
    if not isinstance(raw, bytes) or len(raw) > 4 * 1024**2:
        raise Refused("toc_size_limit")
    selected, identifiers = {}, set()
    for line in raw.splitlines():
        if not line or line.startswith(b";"):
            continue
        if b"\x00" in line or len(line) > 16384:
            raise Refused("toc_entry_invalid")
        line.decode("utf8", errors="strict")
        match = re.fullmatch(rb"([0-9]+); [0-9]+ [0-9]+ TABLE DATA public "
                            rb"(market_source_traces|market_time_series) market", line)
        if match:
            name, entry = match[2].decode("ascii"), int(match[1])
            if name in selected or entry <= 0 or entry in identifiers:
                raise Refused("duplicate_toc_data")
            selected[name] = line
            identifiers.add(entry)
        elif b"TABLE DATA" in line:
            raise Refused("unexpected_toc_data")
        # Non-data catalogue lines are ignored, never executed or copied to -L.
        elif not re.match(rb"[1-9][0-9]*; [0-9]+ [0-9]+ ", line):
            raise Refused("toc_entry_invalid")
    if set(selected) != set(codec.TABLES):
        raise Refused("missing_target_toc_data")
    return b"\n".join(selected[name] for name in codec.TABLES) + b"\n"


def _write_new(path, body):
    with path.open("xb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    if os.name != "nt":
        path.chmod(0o600)


def _report_file(path, report):
    pending = path.with_name(path.name + ".partial")
    _write_new(pending, (codec.encode_json(report) + "\n").encode("utf8"))
    _rename_exclusive(pending, path)
    _sync_directory(path.parent)


class Watchdog:
    """Cancel caller-owned DB operations on deadline/disk exhaustion."""

    def __init__(self, budget):
        self.budget = budget
        self.reason = None
        self.connections = set()
        self.lock = threading.Lock()
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._watch, daemon=True, name="local-restore-guard")

    def check(self):
        if self.reason:
            raise Refused(self.reason)
        return self.budget.check()

    def _watch(self):
        while not self.done.wait(0.2):
            try:
                self.budget.check()
            except Exception:
                self.reason = "resource_deadline_or_disk_exhausted"
                with self.lock:
                    connections = tuple(self.connections)
                for db in connections:
                    try:
                        db.cancel_safe(timeout=1.0)
                    except Exception:
                        pass
                return

    def add(self, db):
        with self.lock:
            self.check()
            self.connections.add(db)

    def remove(self, db):
        with self.lock:
            self.connections.discard(db)

    def start(self):
        self.thread.start()

    def close(self):
        self.done.set()
        self.thread.join(timeout=3)
        if self.thread.is_alive():
            raise Refused("watchdog_not_stopped")


class LocalRestore:
    """One fresh UUID directory, one fixed port, no resume/delete/remote branch."""

    def __init__(self, inputs, *, max_growth_bytes, max_seconds):
        self.inputs = inputs
        self.work = RUNS / uuid.uuid4().hex
        self.data = self.work / "cluster-data"
        self.budget = cp.Budget(self.data, max_growth_bytes=max_growth_bytes,
                               max_seconds=max_seconds,
                               free_for_data=lambda: shutil.disk_usage(ROOT / "work").free)
        self.guard = Watchdog(self.budget)
        self.secret = None
        self.created = self.start_attempted = False
        self.report = {"executed": True, "restore_verified": False, "production_proof": False,
                       "fixture_non_production": inputs["fixture_non_production"],
                       "work_directory": str(self.work), "port": PORT, "database": DATABASE,
                       "archive_sha256": inputs["receipt"]["sha256"],
                       "source_report_sha256": inputs["source_sha256"],
                       "archive_receipt_sha256": inputs["receipt_sha256"],
                       "cluster_stopped": False, "production_contacted": False,
                       "input_paths_modified": False, "processes": []}

    def tool(self, name):
        if name not in ("initdb", "pg_ctl", "pg_restore", "postgres"):
            raise Refused("unknown_local_tool")
        return _plain(BIN / (name + (".exe" if os.name == "nt" else "")))

    def command(self, name, args, *, seconds=60, cleanup=False, accepted=(0,),
                output_limit=4*1024**2):
        if not cleanup:
            self.guard.check()
            seconds = min(seconds, self.budget.max_seconds -
                          (self.budget.clock() - self.budget.started))
        if seconds < 0.05:
            raise Refused("command_deadline_exceeded")
        # pg_ctl start launches a long-lived server. On Windows its inherited
        # pipe handles can remain open after pg_ctl exits. Do not wait for pipe
        # EOF (or close a pipe under a blocked reader) for these control tools.
        # pg_restore's actual data stream still uses the bounded supervisor.
        environment = {k: v for k, v in os.environ.items() if not k.upper().startswith("PG")}
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        deadline = time.monotonic() + seconds
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            child = subprocess.Popen(
                [str(self.tool(name)), *args], stdin=subprocess.DEVNULL,
                stdout=stdout, stderr=stderr, shell=False, close_fds=True,
                env=environment, **options,
            )

            def check():
                if time.monotonic() >= deadline:
                    raise Refused("command_deadline_exceeded")
                if (os.fstat(stdout.fileno()).st_size > output_limit
                        or os.fstat(stderr.fileno()).st_size > 1024**2):
                    raise Refused("control_output_limit")
                if not cleanup:
                    self.guard.check()

            try:
                while child.poll() is None:
                    check()
                    time.sleep(0.02)
                code = child.wait(timeout=max(0.001, deadline - time.monotonic()))
                check()
                stdout.seek(0)
                output = stdout.read(output_limit + 1)
                if len(output) > output_limit:
                    raise Refused("control_output_limit")
            finally:
                if child.poll() is None:
                    child.kill()  # only this owned control process
                    child.wait(timeout=5)
        self.report["processes"].append({"tool": name, "exit_code": code})
        if code not in accepted:
            raise Refused("local_tool_failed")
        if not cleanup:
            self.guard.check()
        return code, bytes(output)

    def preflight(self):
        if PORT != cp.PORT or DATABASE != cp.DATABASE:
            raise Refused("local_target_contract_changed")
        _plain(ROOT / "work", directory=True)
        _plain(BIN, directory=True)
        for name in ("initdb", "pg_ctl", "pg_restore", "postgres"):
            self.tool(name)
        for name in ("postgres", "pg_restore"):
            _, version = self.command(name, ["--version"], seconds=5)
            if re.fullmatch(rb"[^\r\n]+ \(PostgreSQL\) 17(?:\.[0-9]+)*\s*", version) is None:
                raise Refused("local_pg17_required")
        # Binding checks the port without connecting to or inspecting any PG.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if os.name == "nt":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(("127.0.0.1", PORT))
        if RUNS.exists():
            _plain(RUNS, directory=True)
        else:
            RUNS.mkdir(mode=0o700)
        self.work.mkdir(mode=0o700, exist_ok=False)
        self.created = True
        self.secret = secrets.token_urlsafe(48)
        _write_new(self.work / "local-password.txt", (self.secret + "\n").encode())
        _report_file(self.work / "ownership.json", {
            "format": "target-restore-owner-v1", "run_id": self.work.name,
            "data_directory": str(self.data), "port": PORT,
        })

    def initialize(self):
        self.command("initdb", ["-D", str(self.data), "-U", ADMIN, "--encoding=UTF8", "--locale=C",
                                "--auth-host=scram-sha-256", "--auth-local=scram-sha-256",
                                "--pwfile=" + str(self.work / "local-password.txt")], seconds=90)
        if (self.data / "PG_VERSION").read_text().strip() != "17":
            raise Refused("new_cluster_version_invalid")
        self.start_attempted = True
        options = (f"-p {PORT} -h 127.0.0.1 -c shared_buffers=128MB -c max_connections=8 "
                   "-c work_mem=16MB -c maintenance_work_mem=128MB -c max_wal_size=512MB "
                   "-c min_wal_size=80MB -c logging_collector=off -c log_statement=none "
                   "-c log_min_error_statement=panic -c log_error_verbosity=terse "
                   "-c password_encryption=scram-sha-256 -c fsync=on -c full_page_writes=on "
                   "-c synchronous_commit=on -c statement_timeout=30000 -c lock_timeout=2000 "
                   "-c temp_file_limit=256MB")
        self.command("pg_ctl", ["-D", str(self.data), "-w", "-t", "30", "-l",
                                str(self.work / "postgres.log"), "-o", options, "start"],
                     seconds=40)
        with self.connect("postgres") as db:
            db.execute("CREATE DATABASE market_compact_restore_candidate TEMPLATE template0")

    @contextmanager
    def connect(self, database=DATABASE):
        import psycopg

        if database not in ("postgres", DATABASE) or not self.start_attempted:
            raise Refused("local_database_refused")
        self.guard.check()
        db = psycopg.connect(host="127.0.0.1", port=PORT, dbname=database, user=ADMIN,
                             password=self.secret, autocommit=True, connect_timeout=3,
                             application_name="market_target_restore",
                             options="-c statement_timeout=30000 -c lock_timeout=2000")
        try:
            self.guard.add(db)
            row = db.execute("SELECT current_database(),host(inet_server_addr()),"
                             "inet_server_port(),current_setting('data_directory'),"
                             "current_setting('server_version_num')::int,current_user").fetchone()
            if (row[:3] != (database, "127.0.0.1", PORT)
                    or Path(row[3]).resolve() != self.data.resolve()
                    or not 170000 <= row[4] < 180000 or row[5] != ADMIN):
                raise Refused("local_database_identity_changed")
            yield db
            self.guard.check()
        finally:
            self.guard.remove(db)
            db.close()

    def manifest(self):
        return cp.Manifest(self.inputs["receipt"]["sha256"], self.inputs["receipt"]["bytes"],
                           self.inputs["source_sha256"], cp.codec_identity(),
                           self.inputs["source"]["original_traces"], self.inputs["source"]["rows"])

    def restore(self, manifest):
        archive = self.inputs["archive"]
        before = archive.stat()
        sha = cp.verify_archive(archive, manifest, self.budget)
        proof = supervisor.VerifiedArchive.from_verified(archive, sha, manifest.archive_bytes,
                                                        before=before, after=archive.stat())
        _, raw_toc = self.command("pg_restore", ["--list", str(archive)], seconds=30)
        toc = ordered_toc(raw_toc)
        toc_path = self.work / "ordered.list"
        _write_new(toc_path, toc)
        remaining = self.budget.max_seconds - (self.budget.clock() - self.budget.started)
        with self.connect() as db:
            with supervisor.open_pg_restore(
                self.tool("pg_restore"), proof, ordered_toc=toc_path,
                toc_sha256=hashlib.sha256(toc).hexdigest(),
                limits=supervisor.Limits(max_seconds=remaining),
            ) as child:
                result = cp.restore_prefix(child.stream, cp.PgBatchSink(db, manifest, self.budget),
                                           manifest, archive=archive, budget=self.budget,
                                           child_exit=child.wait_exit,
                                           evidence_path=self.work / "prefix-result.json")
            process = child.safe_status()
        self.report.update(prefix=result, pipe=process)
        if (result["state"] != "awaiting_ordered_verification" or not result["stream_complete"]
                or process["exit_code"] != 0 or not process["exit_waited"]
                or not process["threads_stopped"]):
            raise Refused("restore_stream_incomplete")

    def final_verify(self, manifest):
        source = self.inputs["source"]
        with self.connect() as db:
            with db.transaction():
                db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                total = db.execute(
                    "SELECT count(*) FROM market_compact_restore.traces"
                ).fetchone()[0]
                restored = verify.verify_snapshot(db, "restored", check=self.guard.check,
                                                  max_rows=source["rows"], max_traces=total)
                equality = verify.equal_reports(source, restored)
                # Compare the complete original-key inventory as well as the
                # verifier's exact byte hash. Added compact traces stay separate.
                count = 0
                with db.cursor(name="original_trace_keys") as cursor:
                    cursor.execute("SELECT digest FROM market_compact_restore.traces "
                                   'WHERE original ORDER BY digest COLLATE "C"')
                    while rows := cursor.fetchmany(400):
                        self.guard.check()
                        for (key,) in rows:
                            if count >= len(source["original_trace_digests"]) or key != \
                                    source["original_trace_digests"][count]:
                                raise Refused("original_trace_inventory_differs")
                            count += 1
                if count != source["original_traces"]:
                    raise Refused("original_trace_inventory_incomplete")
                sizes = db.execute("SELECT pg_total_relation_size('market_compact_restore.rows'),"
                                   "pg_total_relation_size('market_compact_restore.traces')").fetchone()
        check_input_metadata(self.inputs)
        cp.verify_archive(self.inputs["archive"], manifest, self.budget)
        self.report.update(verification=restored, comparison=equality, originals_preserved=True,
                           original_trace_inventory_sha256=source["original_trace_inventory_sha256"],
                           candidate_bytes=sizes[0], trace_bytes=sizes[1], archive_unchanged=True)

    def stop(self):
        if not self.start_attempted:
            return True
        _plain(self.work, directory=True)
        _plain(self.data, directory=True)
        owner, _, _ = _json_file(self.work / "ownership.json", 4096)
        if owner != {"format": "target-restore-owner-v1", "run_id": self.work.name,
                     "data_directory": str(self.data), "port": PORT}:
            raise Refused("cluster_ownership_changed")
        pid_path = self.data / "postmaster.pid"
        if pid_path.exists():
            _plain(pid_path)
            lines = pid_path.read_text().splitlines()
            if (len(lines) < 4 or Path(lines[1]).resolve() != self.data.resolve()
                    or lines[3] != str(PORT)):
                raise Refused("postmaster_identity_changed")
        for mode, seconds in (("fast", 20), ("immediate", 10)):
            status, _ = self.command("pg_ctl", ["-D", str(self.data), "status"], cleanup=True,
                                     seconds=5, accepted=(0, 3))
            if status == 3 and not pid_path.exists():
                return True
            try:
                self.command("pg_ctl", ["-D", str(self.data), "-w", "-t", str(seconds),
                                        "-m", mode, "stop"], cleanup=True, seconds=seconds + 2)
            except (Refused, supervisor.SupervisorError):
                pass
        status, _ = self.command("pg_ctl", ["-D", str(self.data), "status"], cleanup=True,
                                 seconds=5, accepted=(0, 3))
        return status == 3 and not pid_path.exists()

    def run(self):
        self.guard.start()
        completed = False
        try:
            manifest = self.manifest()
            cp.verify_archive(self.inputs["archive"], manifest, self.budget)
            check_input_metadata(self.inputs)
            self.preflight()
            _report_file(self.work / "manifest.json", json.loads(manifest.identity()))
            self.initialize()
            self.restore(manifest)
            self.final_verify(manifest)
            self.guard.check()
            completed = True
        except (Exception, KeyboardInterrupt) as error:
            self.report.update(state="interrupted", error_type=type(error).__name__)
        finally:
            try:
                self.guard.close()
            except Exception as error:
                self.report["watchdog_cleanup_error_type"] = type(error).__name__
            try:
                self.report["cluster_stopped"] = self.stop()
            except Exception as error:
                self.report.update(cleanup_error_type=type(error).__name__, cluster_stopped=False)
            elapsed = self.budget.clock() - self.budget.started
            passed = (completed and self.report["cluster_stopped"]
                      and "watchdog_cleanup_error_type" not in self.report
                      and elapsed <= self.budget.max_seconds)
            fixture = self.inputs["fixture_non_production"]
            self.report.update(state="verified" if passed else "interrupted",
                               data_equal=passed, restore_verified=passed and not fixture,
                               production_proof=passed and not fixture,
                               fixture_restore_verified=passed and fixture,
                               elapsed_seconds=round(elapsed, 3))
            if self.created:
                _report_file(self.work / "RESULT.json", self.report)
        return self.report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--archive-receipt", type=Path)
    parser.add_argument("--source-report", type=Path)
    parser.add_argument("--max-growth-bytes", type=int)
    parser.add_argument("--max-seconds", type=int, default=21600)
    args = parser.parse_args(argv)
    if not args.run:
        print(json.dumps({"executed": False, "port": PORT, "database": DATABASE,
                          "minimum_free_bytes": cp.MIN_FREE, "maximum_seconds": 21600,
                          "restore_verified": False}))
        return 0
    if (not all((args.archive, args.archive_receipt, args.source_report, args.max_growth_bytes))
            or not 1 <= args.max_seconds <= 21600 or args.max_growth_bytes <= 0):
        parser.error("Explicit archive, bound receipts and positive space/time limits required")
    runner = None
    try:
        inputs = validate_inputs(args.archive, args.archive_receipt, args.source_report)
        runner = LocalRestore(inputs, max_growth_bytes=args.max_growth_bytes,
                              max_seconds=args.max_seconds)
        report = runner.run()
    except (Exception, KeyboardInterrupt) as error:
        print(json.dumps({"executed": runner is not None, "restore_verified": False,
                          "cluster_stopped": runner.report["cluster_stopped"] if runner else None,
                          "error_type": type(error).__name__}))
        return 1
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["restore_verified"] or report["fixture_restore_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
