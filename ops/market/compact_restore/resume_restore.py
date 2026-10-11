"""Explicit replay of one owned, stopped failed restore; default is offline.

No initdb, database creation, deletion or production contact. Every stored prefix
row is compared by checkpoint.restore_prefix. Success proves only this dated
archive's independent restoration, never a current production writer fence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import checkpoint as cp
from . import local_restore as local
from . import restore_supervisor as supervisor
from .replay_batch import ReplayBatchSink

MIN_FREE = 4 * 1024**3


def cluster_bytes(data, check, maximum):
    """Count logical regular-file bytes, including compressed files and WAL."""
    pending, total, entries = [data], 0, 0
    while pending:
        check()
        with os.scandir(pending.pop()) as directory:
            for entry in directory:
                entries += 1
                if entries > 100000:
                    raise local.Refused("cluster_inventory_limit")
                if entries % 128 == 0:
                    check()
                try:
                    info = entry.stat(follow_symlinks=False)
                    if os.name == "nt":
                        # Windows DirEntry caches st_nlink=0; fetch real link metadata.
                        info = Path(entry.path).lstat()
                except FileNotFoundError:
                    continue  # A PG temporary/WAL file may disappear during a stat sweep.
                if (stat.S_ISLNK(info.st_mode)
                        or getattr(info, "st_file_attributes", 0) & 0x400):
                    raise local.Refused("linked_cluster_refused")
                if stat.S_ISDIR(info.st_mode):
                    pending.append(Path(entry.path))
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    total += info.st_size
                    if total > maximum:
                        raise local.Refused("total_cluster_budget_exhausted", total)
                else:
                    raise local.Refused("nonordinary_cluster_entry")
    check()
    return total


class ResumeBudget(cp.Budget):
    """Volume guard on every call; bounded logical sweeps with write reservations.

    This is a fail-stop budget, not an OS allocation quota. PG's own allocations
    are observed by the watchdog at most one second apart. Incoming batch budgets
    are accumulated between sweeps, never silently reset on a failed write.
    """

    def __init__(self, data, *, max_total_cluster_bytes, max_growth_bytes, max_seconds,
                 free_for_data=None, monotonic=time.monotonic):
        if type(max_total_cluster_bytes) is not int or max_total_cluster_bytes <= 0:
            raise local.Refused("explicit_total_cluster_budget_required")
        # checkpoint.py is part of the frozen codec identity. Keep its existing
        # contract intact; this resume coordinator owns the revised budget.
        if (type(max_growth_bytes) is not int or max_growth_bytes <= 0
                or type(max_seconds) is not int or not 1 <= max_seconds <= 21600):
            raise cp.codec.Refused("Explicit positive growth/time budgets required")
        self.clock, self.started = monotonic, monotonic()
        self.max_seconds, self.max_growth = max_seconds, max_growth_bytes
        self.data_directory = Path(data).resolve()
        self.free = free_for_data or (lambda: shutil.disk_usage(self.data_directory).free)
        self.initial_free = self.free()
        if self.initial_free < MIN_FREE + self.max_growth:
            raise cp.codec.Refused("Full compact restore growth budget plus reserve unavailable")
        self.max_total = max_total_cluster_bytes
        self.measure_lock = threading.RLock()
        self.failure_diagnostic = None
        self.last_cluster_bytes = 0
        self.reserved_bytes = 0
        self.last_measured_at = float("-inf")
        self.initial_cluster_bytes = self._measure()
        if self.initial_cluster_bytes + self.max_growth > self.max_total:
            raise local.Refused("remaining_total_cluster_budget_insufficient")

    def _measure(self):
        try:
            self.last_cluster_bytes = cluster_bytes(
                self.data_directory, self._base_check, self.max_total,
            )
        except local.Refused as error:
            if (len(error.args) == 2 and error.args[0] == "total_cluster_budget_exhausted"
                    and type(error.args[1]) is int):
                self.last_cluster_bytes = error.args[1]
            raise
        self.last_measured_at = self.clock()
        self.reserved_bytes = 0
        return self.last_cluster_bytes

    def diagnostic(self, code, incoming=0):
        try:
            free = self.free()
        except OSError:
            free = None
        return {"code": code, "free_bytes": free, "initial_free_bytes": self.initial_free,
                "drive_growth_bytes": self.initial_free-free if free is not None else None,
                "own_directory_growth_bytes": max(
                    0, self.last_cluster_bytes-getattr(self, "initial_cluster_bytes", 0)),
                "minimum_free_bytes": MIN_FREE,
                "last_cluster_bytes": self.last_cluster_bytes,
                "initial_cluster_bytes": getattr(self, "initial_cluster_bytes", None),
                "reserved_bytes": self.reserved_bytes, "incoming_bytes": incoming,
                "elapsed_seconds": round(self.clock()-self.started, 3),
                "max_growth_bytes": self.max_growth, "max_total_cluster_bytes": self.max_total}

    def _base_check(self, incoming=0):
        try:
            if type(incoming) is not int or incoming < 0:
                raise cp.codec.Refused("Invalid next-write budget")
            remaining = self.max_seconds - (self.clock() - self.started)
            if remaining <= 0:
                raise cp.codec.Refused("Restore elapsed-time budget exhausted")
            if self.free() < MIN_FREE + incoming:
                raise cp.codec.Refused("Restore disk reserve exhausted; committed prefix preserved")
            return max(1, min(30000, int(remaining * 1000)))
        except Exception:
            if self.failure_diagnostic is None:
                values = self.diagnostic("volume_guard_refused", incoming)
                if values["elapsed_seconds"] >= self.max_seconds:
                    values["code"] = "deadline_exhausted"
                elif (values["free_bytes"] is not None
                      and values["free_bytes"] < MIN_FREE+incoming):
                    values["code"] = "disk_reserve_exhausted"
                self.failure_diagnostic = values
            raise

    def check(self, incoming=0, *, force=False):
        self._base_check(incoming)
        with self.measure_lock:
            projected = self.last_cluster_bytes + self.reserved_bytes + incoming
            if (force or self.clock()-self.last_measured_at >= 1
                    or projected > self.max_total
                    or projected-self.initial_cluster_bytes > self.max_growth):
                try:
                    self._measure()
                except Exception:
                    if self.failure_diagnostic is None:
                        self.failure_diagnostic = self.diagnostic(
                            "logical_inventory_refused", incoming,
                        )
                    raise
            projected = self.last_cluster_bytes + self.reserved_bytes + incoming
            if (projected > self.max_total
                    or projected-self.initial_cluster_bytes > self.max_growth):
                self.failure_diagnostic = self.diagnostic("logical_growth_exhausted", incoming)
                raise local.Refused("logical_cluster_growth_exhausted")
            self.reserved_bytes += incoming
        return self._base_check()


KNOWN_FAILURES = {
    "command_deadline_exceeded": "control_command_timeout",
    "Previously committed logical row differs": "prefix_row_differs",
    "Previously committed original trace differs": "prefix_original_trace_differs",
    "Stored prefix row count differs from atomic checkpoint": "checkpoint_count_differs",
    "Resume archive/codec/manifest/batch settings differ": "checkpoint_identity_differs",
    "Another restore owns this target": "checkpoint_owned_elsewhere",
    "Original/new trace bytes conflict": "trace_bytes_conflict",
    "Checkpoint CAS failed": "checkpoint_cas_failed",
    "missing_existing_checkpoint": "missing_existing_checkpoint",
    "prefix_restore_incomplete": "prefix_restore_incomplete",
}


def failure_code(error):
    if isinstance(error, (local.Refused, local.codec.Refused)):
        message = error.args[0] if error.args and isinstance(error.args[0], str) else None
        return KNOWN_FAILURES.get(message, "validation_refused")
    if isinstance(error, supervisor.SupervisorError):
        return "restore_child_failed"
    return "cancelled" if isinstance(error, KeyboardInterrupt) else "restore_operation_failed"


def safe_error_detail(error):
    """No exception messages, SQL, tool output or credential-bearing arguments."""
    result = {"type": type(error).__name__}
    for field in ("errno", "winerror"):
        value = getattr(error, field, None)
        if type(value) is int:
            result[field] = value
    return result


class ResumeSink(ReplayBatchSink):
    """Capture fixed failure reasons before the frozen coordinator handles them."""

    def __init__(self, db, manifest, budget, report):
        super().__init__(ExistingConnection(db), manifest, budget)
        self.report = report
        self.report.update(self.replay_stats())
        self.report.setdefault("row_progress", {
            "processed": {"trace_rows": 0, "series_rows": 0},
            "committed": None, "verified_prefix": {"trace_rows": 0, "series_rows": 0},
            "ordered_hash_verified": False, "checkpoint_reload_required": True,
        })

    def _record(self, error, phase, state=None):
        value = {"code": failure_code(error), "phase": phase}
        if state is not None:
            value.update(sequence=state.sequence, trace_rows=state.trace_rows,
                         series_rows=state.series_rows)
        sqlstate = getattr(error, "sqlstate", None)
        if isinstance(sqlstate, str) and re.fullmatch(r"[A-Z0-9]{5}", sqlstate):
            value["sqlstate"] = sqlstate
        self.report.setdefault("failure_diagnostic", value)

    def open(self):
        try:
            state = super().open()
            self.report["row_progress"].update(
                committed={"trace_rows": state.trace_rows, "series_rows": state.series_rows},
                checkpoint_reload_required=False,
            )
            return state
        except BaseException as error:
            self._record(error, "checkpoint_open")
            raise

    def consume(self, event, before, after, *, replay):
        progress = self.report["row_progress"]
        progress["processed"] = {"trace_rows": after.trace_rows,
                                 "series_rows": after.series_rows}
        try:
            result = super().consume(event, before, after, replay=replay)
            self.report.update(self.replay_stats())
            if replay:
                progress["verified_prefix"] = dict(progress["processed"])
            else:
                progress["committed"] = dict(progress["processed"])
            return result
        except BaseException as error:
            # COMMIT may have completed immediately before a deadline. Require
            # an authoritative DB reload rather than claiming an acknowledged row.
            progress["checkpoint_reload_required"] = True
            self._record(error, "prefix_replay" if replay else "append_batch", before)
            raise

    def check_counts(self, checkpoint):
        try:
            return super().check_counts(checkpoint)
        except BaseException as error:
            self._record(error, "checkpoint_counts", checkpoint)
            raise


def validate_existing(work, inputs):
    work = local._plain(work, directory=True)
    if work.parent != local.RUNS or not re.fullmatch(r"[0-9a-f]{32}", work.name):
        raise local.Refused("exact_owned_run_required")
    data = local._plain(work / "cluster-data", directory=True)
    if (data / "postmaster.pid").exists():
        raise local.Refused("existing_postmaster_refused")
    if local._plain(data / "PG_VERSION").read_text().strip() != "17":
        raise local.Refused("existing_pg17_required")
    owner, owner_sha, _ = local._json_file(work / "ownership.json", 4096)
    if owner != {"format": "target-restore-owner-v1", "run_id": work.name,
                 "data_directory": str(data), "port": local.PORT}:
        raise local.Refused("existing_owner_mismatch")
    previous, result_sha, _ = local._json_file(work / "RESULT.json", 65536)
    if (previous.get("state") != "interrupted" or previous.get("executed") is not True
            or previous.get("cluster_stopped") is not True
            or previous.get("restore_verified") is not False
            or previous.get("production_contacted") is not False
            or previous.get("work_directory") != str(work)
            or previous.get("database") != local.DATABASE or previous.get("port") != local.PORT
            or previous.get("archive_sha256") != inputs["receipt"]["sha256"]
            or previous.get("archive_receipt_sha256") != inputs["receipt_sha256"]
            or previous.get("source_report_sha256") != inputs["source_sha256"]
            or previous.get("fixture_non_production") is not inputs["fixture_non_production"]):
        raise local.Refused("failed_restore_binding_mismatch")
    saved, manifest_sha, _ = local._json_file(work / "manifest.json", 8192)
    if saved.get("format") != cp.FORMAT:
        raise local.Refused("saved_manifest_format_invalid")
    manifest = cp.Manifest(**{k: v for k, v in saved.items() if k != "format"})
    if (manifest.identity() != local.codec.encode_json(saved)
            or manifest.codec_sha256 != cp.codec_identity()
            or manifest.archive_sha256 != inputs["receipt"]["sha256"]
            or manifest.archive_bytes != inputs["receipt"]["bytes"]
            or manifest.source_manifest_sha256 != inputs["source_sha256"]
            or manifest.trace_rows != inputs["source"]["original_traces"]
            or manifest.series_rows != inputs["source"]["rows"]):
        raise local.Refused("saved_manifest_binding_mismatch")
    password = local._plain(work / "local-password.txt")
    if not 32 <= password.stat().st_size <= 256:
        raise local.Refused("saved_password_invalid")
    secret = password.read_text().strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", secret):
        raise local.Refused("saved_password_invalid")
    return manifest, secret, {"ownership.json": owner_sha, "RESULT.json": result_sha,
                              "manifest.json": manifest_sha}


@contextmanager
def run_lock(work):
    """Persistent lock file; release OS lock without deleting any file."""
    path = work / "resume.lock"
    if path.exists():
        local._plain(path)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    locked = False
    try:
        local._plain(path)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or (info.st_dev, info.st_ino) != (path.stat().st_dev, path.stat().st_ino)):
            raise local.Refused("resume_lock_identity_changed")
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = True
        yield
    finally:
        try:
            if locked:
                if os.name == "nt":
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


class ExistingConnection:
    """The frozen coordinator may never initialize a missing resume schema."""

    def __init__(self, db):
        self.db = db

    def __getattr__(self, name):
        return getattr(self.db, name)

    def execute(self, statement, *args, **kwargs):
        if statement in cp.DDL or statement.startswith(
            "INSERT INTO market_compact_restore.checkpoint",
        ):
            raise local.Refused("missing_existing_checkpoint")
        return self.db.execute(statement, *args, **kwargs)


class ResumeRestore(local.LocalRestore):
    def __init__(self, inputs, work, *, max_growth_bytes, max_total_cluster_bytes, max_seconds):
        self.inputs = inputs
        self.work = Path(work)
        self.saved_manifest, self.secret, self.evidence_hashes = validate_existing(
            self.work, inputs,
        )
        self.data = self.work / "cluster-data"
        self.attempt = self.work / ("resume-" + uuid.uuid4().hex)
        self.created = self.start_attempted = False
        self.budget = ResumeBudget(self.data, max_growth_bytes=max_growth_bytes,
                                   max_total_cluster_bytes=max_total_cluster_bytes,
                                   max_seconds=max_seconds)
        self.guard = local.Watchdog(self.budget)
        self.report = {
            "executed": True, "state": "starting", "restore_verified": False,
            "production_proof": False, "current_fence_valid": False,
            "proof_scope": "dated_archive_independent_restore_only",
            "fixture_non_production": inputs["fixture_non_production"],
            "work_directory": str(self.work), "attempt_directory": str(self.attempt),
            "port": local.PORT, "database": local.DATABASE,
            "archive_sha256": inputs["receipt"]["sha256"],
            "source_report_sha256": inputs["source_sha256"],
            "archive_receipt_sha256": inputs["receipt_sha256"],
            "previous_evidence_sha256": self.evidence_hashes,
            "initial_cluster_bytes": self.budget.initial_cluster_bytes,
            "max_total_cluster_bytes": max_total_cluster_bytes,
            "max_growth_bytes": max_growth_bytes, "max_seconds": max_seconds,
            "minimum_free_bytes": MIN_FREE,
            "archive_content_verified": False,
            "space_preflight": self.budget.diagnostic("ready"),
            "cluster_stopped": False, "production_contacted": False,
            "input_paths_modified": False, "processes": [],
        }

    def tool(self, name):
        if name == "initdb":
            raise local.Refused("initialization_forbidden")
        return super().tool(name)

    def preflight(self):
        self.report["preflight_step"] = "target_contract"
        if local.PORT != cp.PORT or local.DATABASE != cp.DATABASE:
            raise local.Refused("local_target_contract_changed")
        _, secret, hashes = validate_existing(self.work, self.inputs)
        if hashes != self.evidence_hashes or secret != self.secret:
            raise local.Refused("existing_evidence_changed")
        for name in ("postgres", "pg_restore"):
            self.report["preflight_step"] = name + "_version"
            # The control deadline includes the directory-budget check. On a
            # busy Windows volume that check alone can exceed five seconds.
            # The overall restore deadline and all space limits still apply.
            _, version = self.command(name, ["--version"], seconds=30)
            if re.fullmatch(rb"[^\r\n]+ \(PostgreSQL\) 17(?:\.[0-9]+)*\s*", version) is None:
                raise local.Refused("local_pg17_required")
        self.report["preflight_step"] = "cluster_status"
        status, _ = self.command("pg_ctl", ["-D", str(self.data), "status"], accepted=(0, 3))
        if status != 3:
            raise local.Refused("existing_cluster_active")
        self.report["preflight_step"] = "port_binding"
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if os.name == "nt":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(("127.0.0.1", local.PORT))
        self.report["preflight_step"] = "attempt_directory"
        self.attempt.mkdir(mode=0o700, exist_ok=False)
        self.created = True
        local._report_file(
            self.attempt / "manifest.json", json.loads(self.saved_manifest.identity()),
        )

    def initialize(self):
        raise local.Refused("initialization_forbidden")

    def stop(self):
        if not self.start_attempted:
            return not (self.data / "postmaster.pid").exists()
        return super().stop()

    def start_existing(self):
        self.budget.check(force=True)
        self.start_attempted = True
        options = (f"-p {local.PORT} -h 127.0.0.1 -c shared_buffers=128MB -c max_connections=8 "
                   "-c work_mem=16MB -c maintenance_work_mem=128MB -c max_wal_size=512MB "
                   "-c min_wal_size=80MB -c logging_collector=off -c log_statement=none "
                   "-c log_min_error_statement=panic -c log_error_verbosity=terse "
                   "-c fsync=on -c full_page_writes=on -c synchronous_commit=on "
                   "-c statement_timeout=30000 -c lock_timeout=2000 -c temp_file_limit=256MB")
        self.command("pg_ctl", ["-D", str(self.data), "-w", "-t", "30", "-l",
                                str(self.attempt / "postgres.log"), "-o", options, "start"],
                     seconds=40)

    def restore(self, manifest):
        self.budget.check(force=True)
        archive = self.inputs["archive"]
        before = archive.stat()
        sha = cp.verify_archive(archive, manifest, self.budget)
        proof = supervisor.VerifiedArchive.from_verified(
            archive, sha, manifest.archive_bytes, before=before, after=archive.stat(),
        )
        _, raw_toc = self.command("pg_restore", ["--list", str(archive)], seconds=30)
        toc = local.ordered_toc(raw_toc)
        toc_path = self.attempt / "ordered.list"
        local._write_new(toc_path, toc)
        child = None
        try:
            with self.connect() as db:
                remaining = self.budget.max_seconds - (self.budget.clock() - self.budget.started)
                with supervisor.open_pg_restore(
                    self.tool("pg_restore"), proof, ordered_toc=toc_path,
                    toc_sha256=hashlib.sha256(toc).hexdigest(),
                    limits=supervisor.Limits(max_seconds=remaining),
                ) as child:
                    # File reports are observations. DB checkpoint + table counts
                    # and every archived prefix row remain authoritative.
                    try:
                        result = cp.restore_prefix(
                            child.stream,
                            ResumeSink(db, manifest, self.budget, self.report),
                            manifest, archive=archive, budget=self.budget,
                            child_exit=child.wait_exit,
                            evidence_path=self.attempt / "prefix-result.json",
                        )
                        self.report["prefix"] = result
                        if (result["state"] != "awaiting_ordered_verification"
                                or not result["stream_complete"]):
                            raise local.Refused("prefix_restore_incomplete")
                    except (Exception, KeyboardInterrupt):
                        self.report["stream_error_code"] = "prefix_restore_interrupted"
                        try:
                            local._report_file(self.attempt / "stream-interrupted.json", {
                                "error_code": "prefix_restore_interrupted",
                                "restore_verified": False, "checkpoint_reload_required": True,
                            })
                        except Exception:
                            self.report["stream_evidence_error_code"] = "evidence_write_failed"
                        raise
        finally:
            if child is not None:
                self.report["pipe"] = child.safe_status()
        status = self.report.get("pipe", {})
        if (status.get("exit_code") != 0 or not status.get("exit_waited")
                or not status.get("threads_stopped")):
            raise local.Refused("restore_stream_incomplete")
        self.budget.check(force=True)

    def run(self):
        completed = False
        with run_lock(self.work):
            self.guard.start()
            try:
                manifest = self.saved_manifest
                self.report["phase"] = "archive_verification"
                cp.verify_archive(self.inputs["archive"], manifest, self.budget)
                self.report["archive_content_verified"] = True
                local.check_input_metadata(self.inputs)
                self.report["phase"] = "preflight"
                self.preflight()
                self.report["phase"] = "cluster_start"
                self.start_existing()
                self.report["phase"] = "prefix_restore"
                self.restore(manifest)
                self.report["phase"] = "final_ordered_verification"
                self.final_verify(manifest)
                self.report.setdefault("row_progress", {})["ordered_hash_verified"] = True
                self.budget.check(force=True)
                self.guard.check()
                completed = True
            except (Exception, KeyboardInterrupt) as error:
                self.report.update(state="interrupted", error_code=failure_code(error),
                                   error_detail=safe_error_detail(error))
            finally:
                try:
                    self.guard.close()
                except Exception:
                    self.report["watchdog_error_code"] = "watchdog_cleanup_failed"
                try:
                    self.report["cluster_stopped"] = self.stop()
                except Exception:
                    self.report.update(cleanup_error_code="cluster_cleanup_failed",
                                       cluster_stopped=False)
                if self.report["cluster_stopped"]:
                    try:
                        _, secret, hashes = validate_existing(self.work, self.inputs)
                        if secret != self.secret or hashes != self.evidence_hashes:
                            raise local.Refused("existing_evidence_changed")
                        self.budget.check(force=True)
                        self.guard.check()
                    except Exception:
                        self.report["final_guard_error_code"] = "final_evidence_or_budget_failed"
                elapsed = self.budget.clock() - self.budget.started
                passed = (completed and self.report["cluster_stopped"]
                          and "watchdog_error_code" not in self.report
                          and "final_guard_error_code" not in self.report
                          and elapsed <= self.budget.max_seconds)
                fixture = self.inputs["fixture_non_production"]
                if self.budget.failure_diagnostic is not None:
                    self.report["budget_failure"] = self.budget.failure_diagnostic
                self.report.update(state="verified" if passed else "interrupted", data_equal=passed,
                                   restore_verified=passed and not fixture,
                                   fixture_restore_verified=passed and fixture,
                                   last_observed_cluster_bytes=self.budget.last_cluster_bytes,
                                   elapsed_seconds=round(elapsed, 3))
                if self.created:
                    local._report_file(self.attempt / "RESULT.json", self.report)
        return self.report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--preflight", action="store_true",
                      help="Read metadata and bounded directory sizes only; no process or writes")
    for name in ("work-directory", "archive", "archive-receipt", "source-report"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--max-total-cluster-bytes", type=int)
    parser.add_argument("--max-growth-bytes", type=int)
    parser.add_argument("--max-seconds", type=int, default=14400)
    args = parser.parse_args(argv)
    if not args.run and not args.preflight:
        print(json.dumps({"executed": False, "restore_verified": False,
                          "current_fence_valid": False, "production_proof": False,
                          "port": local.PORT, "database": local.DATABASE,
                          "minimum_free_bytes": MIN_FREE, "maximum_seconds": 21600,
                          "default_seconds": 14400}))
        return 0
    if not all((args.work_directory, args.archive, args.archive_receipt, args.source_report,
                args.max_total_cluster_bytes, args.max_growth_bytes)):
        parser.error("Explicit owned run, archive bindings and both space limits are required")
    runner = None
    try:
        inputs = local.validate_inputs(args.archive, args.archive_receipt, args.source_report)
        runner = ResumeRestore(inputs, args.work_directory, max_growth_bytes=args.max_growth_bytes,
                               max_total_cluster_bytes=args.max_total_cluster_bytes,
                               max_seconds=args.max_seconds)
        if args.preflight:
            print(json.dumps({"executed": False, "state": "preflight_passed",
                              "archive_sha256": runner.saved_manifest.archive_sha256,
                              "expected_rows": runner.saved_manifest.counts(),
                              "space": runner.budget.diagnostic("ready"),
                              "checkpoint_reload_required": True,
                              "archive_content_verified": False,
                              "restore_verified": False, "production_contacted": False}))
            return 0
        report = runner.run()
    except (Exception, KeyboardInterrupt):
        print(json.dumps({"executed": runner is not None and args.run, "restore_verified": False,
                          "current_fence_valid": False, "production_proof": False,
                          "error_code": "resume_refused_or_evidence_failed"}))
        return 1
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["restore_verified"] or report["fixture_restore_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
