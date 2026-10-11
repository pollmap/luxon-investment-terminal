"""Bounded VPS pg_dump -> private PC file, without a VPS dump file.

Opt-in CLI; no retention deletion, migration or restore. A received and listed
archive proves transfer integrity, never full database recovery or equality.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from . import offsite_backup as offsite

GIB = 1024**3
MIN_FREE = 4 * GIB
ROOT = Path(__file__).resolve().parents[2]
DESTINATION = ROOT / "work/backups/streaming"
PG_RESTORE = ROOT / "work/tools/postgresql-restore/pgsql/bin/pg_restore.exe"
CONTAINER = "hannun-public-market-market-db-1"


class Refused(RuntimeError):
    """Fixed non-secret status code."""


def hidden():
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def private_directory(path):
    offsite.unlinked(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=False)
    if os.name == "nt":
        script = r'''& { param($directory)
        $sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User
        $acl=Get-Acl -LiteralPath $directory
        $acl.SetAccessRuleProtection($true,$false)
        foreach($who in @($sid,[System.Security.Principal.SecurityIdentifier]'S-1-5-18')) {
          $rule=[System.Security.AccessControl.FileSystemAccessRule]::new(
            $who,'FullControl','ContainerInherit,ObjectInherit','None','Allow')
          $acl.AddAccessRule($rule)
        }
        Set-Acl -LiteralPath $directory -AclObject $acl
        }'''
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                        script, str(path)], check=True, timeout=30, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, **hidden())


class Guard:
    def __init__(self, directory, max_bytes, max_seconds, *, free=None, clock=time.monotonic):
        if (type(max_bytes) is not int or not 1 <= max_bytes <= 32 * GIB
                or type(max_seconds) is not int or not 1 <= max_seconds <= 14400):
            raise Refused("invalid_explicit_budget")
        self.max_bytes, self.max_seconds = max_bytes, max_seconds
        self.free = free or (lambda: shutil.disk_usage(directory).free)
        self.clock, self.started = clock, clock()
        if self.free() < MIN_FREE + max_bytes:
            raise Refused("destination_budget_unavailable")

    def check(self, received=0, incoming=0):
        if received + incoming > self.max_bytes:
            raise Refused("dump_size_budget_exhausted")
        remaining = self.max_seconds - (self.clock() - self.started)
        if remaining <= 0:
            raise Refused("dump_time_budget_exhausted")
        if self.free() < MIN_FREE + incoming:
            raise Refused("destination_disk_reserve_exhausted")
        return remaining


def command(max_seconds):
    # Fixed server, DB and lock; no SQL, credentials or operator text interpolation.
    return [*offsite.SSH, "timeout", "--signal=TERM", "--kill-after=10s", str(max_seconds),
            "flock", "-n", "/srv/services/korea-stocks/backups/.backup.lock",
            "docker", "exec", CONTAINER, "pg_dump", "-U", "market", "-d", "market",
            "--format=custom", "--compress=6", "--no-owner", "--no-acl",
            "--lock-wait-timeout=5000"]


def receive(pending, guard, *, popen=subprocess.Popen):
    process = popen(command(guard.max_seconds), stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, **hidden())
    stopped, faults = threading.Event(), []

    def watch():
        while not stopped.wait(0.25):
            try:
                guard.check()
            except Refused as error:
                faults.append(error)
                process.kill()
                return

    thread = threading.Thread(target=watch, name="pg-dump-budget", daemon=True)
    thread.start()
    size, digest = 0, hashlib.sha256()
    try:
        with pending.open("xb") as output:
            while part := process.stdout.read(1024**2):
                guard.check(size, len(part))
                output.write(part)
                digest.update(part)
                size += len(part)
            output.flush()
            os.fsync(output.fileno())
        code = process.wait(timeout=max(1, guard.check(size)))
        if faults:
            raise faults[0]
        if code != 0:
            raise Refused("producer_or_transport_failed")
        if not size:
            raise Refused("empty_dump")
        return {"bytes": size, "sha256": digest.hexdigest()}
    finally:
        stopped.set()
        thread.join(timeout=2)
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        process.stdout.close()


def verify(pending, expected, guard, *, run=subprocess.run, pg_restore=PG_RESTORE):
    if pending.is_symlink() or pending.stat().st_nlink != 1:
        raise Refused("linked_dump_refused")
    before = pending.stat()
    digest = hashlib.sha256()
    with pending.open("rb") as source:
        if source.read(5) != b"PGDMP":
            raise Refused("not_a_custom_dump")
        source.seek(0)
        while part := source.read(1024**2):
            guard.check(expected["bytes"])
            digest.update(part)
    after = pending.stat()
    if (before.st_size != expected["bytes"] or digest.hexdigest() != expected["sha256"]
            or (before.st_size, before.st_mtime_ns, before.st_ino)
            != (after.st_size, after.st_mtime_ns, after.st_ino)):
        raise Refused("received_dump_changed")
    run([str(pg_restore), "--list", str(pending)], check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=min(120, guard.check(expected["bytes"])), **hidden())
    guard.check(expected["bytes"])


def backup(*, destination=DESTINATION, max_bytes=6 * GIB, max_seconds=7200,
           receiver=receive, verifier=verify):
    destination = Path(destination).absolute()
    offsite.unlinked(destination)
    if not PG_RESTORE.is_file():
        raise Refused("existing_pg_restore_required")
    parent = destination
    while not parent.exists():
        parent = parent.parent
    guard = Guard(parent, max_bytes, max_seconds)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    directory = destination / (stamp + "-" + uuid.uuid4().hex)
    private_directory(directory)
    pending = directory / ("market-" + stamp + ".partial")
    target = pending.with_suffix(".dump")
    report = {"state": "receiving", "source": target.name, "restore_verified": False,
              "source_content_equality_verified": False, "remote_dump_file_created": False,
              "minimum_free_bytes": MIN_FREE, "max_bytes": max_bytes,
              "max_seconds": max_seconds, "started_at": dt.datetime.now(dt.UTC).isoformat()}
    try:
        expected = receiver(pending, guard)
        report.update(expected, state="verifying")
        verifier(pending, expected, guard)
        # No existing file is overwritten. Every run has a unique private path.
        if target.exists():
            raise Refused("destination_already_exists")
        pending.rename(target)
        report.update(state="completed", archive_list_verified=True,
                      source_mtime_ns=target.stat().st_mtime_ns,
                      completed_at=dt.datetime.now(dt.UTC).isoformat())
        offsite.write_json(directory / (target.name + ".manifest.json"), report)
    except BaseException as error:
        report.update(state="failed", error_code=str(error) if isinstance(error, Refused)
                      else type(error).__name__, partial_preserved=pending.exists())
        offsite.write_json(directory / "RESULT.json", report)
        raise
    offsite.write_json(directory / "RESULT.json", report)
    return {**report, "directory": str(directory)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--max-bytes", type=int, default=6 * GIB)
    parser.add_argument("--max-seconds", type=int, default=7200)
    args = parser.parse_args(argv)
    if not args.run:
        print(json.dumps({"executed": False, "minimum_free_bytes": MIN_FREE,
                          "required_free_bytes": MIN_FREE + args.max_bytes,
                          "restore_verified": False, "remote_dump_file_created": False}))
        return 0
    try:
        print(json.dumps(backup(max_bytes=args.max_bytes, max_seconds=args.max_seconds)))
        return 0
    except (Exception, KeyboardInterrupt) as error:
        print(json.dumps({"state": "failed", "restore_verified": False,
                          "error_code": str(error) if isinstance(error, Refused)
                          else type(error).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
