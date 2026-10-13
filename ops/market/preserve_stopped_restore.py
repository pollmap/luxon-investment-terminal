"""Archive one owned stopped test cluster; never delete or restore anything.

The command is intentionally fixed to the v106 daily-restore copy. A complete
archive proves file-byte preservation, not database or financial correctness.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "work/restore-daily-20261010-v106/cluster-data"
DESTINATION = ROOT / "work/stopped-restore-archives"
OWNER = TARGET.parent / "verification.json"
OWNER_SHA = "10a1ee205e660b708882d8ed7f19a1e06a2b8a33e653e79239b166ae63b670ed"
MAX_BYTES = 6 * 1024**3
MIN_FREE = 4 * 1024**3
MAX_SECONDS = 1800


class Refused(RuntimeError):
    pass


def identity(path):
    record = path.lstat()
    if (
        path.is_symlink()
        or getattr(path, "is_junction", lambda: False)()
        or getattr(record, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise Refused("reparse_or_link_refused")
    return {
        "bytes": record.st_size,
        "mtime_ns": record.st_mtime_ns,
        "inode": record.st_ino,
        "device": record.st_dev,
        "nlink": record.st_nlink,
    }


def stopped():
    if os.name != "nt":
        raise Refused("owned_windows_environment_required")
    if (TARGET / "PG_VERSION").read_text().strip() != "17":
        raise Refused("wrong_cluster_version")
    if (TARGET / "postmaster.pid").exists():
        raise Refused("postmaster_present")
    processes = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "@(Get-Process -Name postgres -ErrorAction SilentlyContinue).Count",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    if processes.stdout.strip() != "0":
        raise Refused("postgres_process_present")
    pg_ctl = ROOT / "work/tools/postgresql-restore/pgsql/bin/pg_ctl.exe"
    result = subprocess.run(
        [str(pg_ctl), "-D", str(TARGET), "status"], capture_output=True, timeout=10
    )
    if result.returncode != 3:
        raise Refused("cluster_not_confirmed_stopped")


def owner():
    identity(OWNER)
    raw = OWNER.read_bytes()
    if hashlib.sha256(raw).hexdigest() != OWNER_SHA:
        raise Refused("owner_proof_changed")
    proof = json.loads(raw)
    if (
        not all(
            proof.get(field) is True
            for field in (
                "passed",
                "pg_restore_succeeded",
                "dump_unchanged",
                "local_cluster_stopped",
            )
        )
        or proof.get("production_contacted") is not False
        or proof.get("port") != 54420
    ):
        raise Refused("owner_proof_invalid")
    dump = Path(proof["dump"])
    expected = ROOT / "work/backups/daily/market-20261009T153042Z.dump/market-20261009T153042Z.dump"
    if dump != expected or not dump.is_file() or dump.stat().st_size != 2372779444:
        raise Refused("preserved_daily_dump_missing")
    ciphertext = dump.with_name(dump.name + ".aes256gcm")
    if not ciphertext.is_file() or ciphertext.stat().st_size != 2372779770:
        raise Refused("preserved_encrypted_copy_missing")
    return {
        "verification_sha256": OWNER_SHA,
        "source_content_equality_verified": proof.get("source_content_equality_verified"),
    }


def inventory():
    if TARGET.resolve(strict=True) != TARGET.absolute():
        raise Refused("source_root_identity_changed")
    identity(TARGET)
    files, directories = [], []

    def unavailable(_error):
        raise Refused("source_inventory_unreadable")

    for current, dirs, names in os.walk(TARGET, followlinks=False, onerror=unavailable):
        here = Path(current)
        for name in dirs:
            identity(here / name)
            directories.append((here / name).relative_to(TARGET).as_posix())
        for name in names:
            path = here / name
            record = identity(path)
            if not stat.S_ISREG(path.lstat().st_mode) or record["nlink"] != 1:
                raise Refused("nonregular_or_hardlinked_source")
            files.append({"path": path.relative_to(TARGET).as_posix(), **record})
    files.sort(key=lambda row: row["path"])
    if not files:
        raise Refused("empty_source")
    return {
        "files": files,
        "directories": sorted(directories),
        "logical_bytes": sum(row["bytes"] for row in files),
    }


def publish_json(path, value):
    temporary = path.with_name(path.name + ".partial")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, sort_keys=True)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


class Budget:
    def __init__(self, destination, estimate, clock=time.monotonic, disk=shutil.disk_usage):
        if type(estimate) is not int or not 0 < estimate <= MAX_BYTES:
            raise Refused("invalid_archive_size_estimate")
        self.destination, self.clock, self.disk = destination, clock, disk
        self.started = clock()
        self.initial_free = disk(destination).free
        if self.initial_free < estimate + MIN_FREE:
            raise Refused("archive_budget_and_reserve_unavailable")

    def check(self, own_bytes=0):
        free = self.disk(self.destination).free
        if self.clock() - self.started > MAX_SECONDS:
            raise Refused("archive_time_budget_exhausted")
        if own_bytes > MAX_BYTES:
            raise Refused("archive_output_budget_exhausted")
        if free < MIN_FREE or self.initial_free - free > MAX_BYTES:
            raise Refused("drive_space_budget_exhausted")


class Writer:
    def __init__(self, stream, budget):
        self.stream, self.budget = stream, budget
        self.bytes, self.digest = 0, hashlib.sha256()

    def write(self, value):
        self.budget.check(self.bytes + len(value))
        count = self.stream.write(value)
        if count != len(value):
            raise Refused("short_archive_write")
        self.bytes += count
        self.digest.update(value)
        return count

    def flush(self):
        self.stream.flush()


class Reader:
    def __init__(self, stream, budget):
        self.stream, self.budget, self.digest = stream, budget, hashlib.sha256()
        self.bytes = 0

    def read(self, size=-1):
        self.budget.check()
        value = self.stream.read(size)
        self.bytes += len(value)
        self.digest.update(value)
        return value


def verify_archive(path, manifest, budget):
    expected = manifest["files"]
    observed = 0
    with path.open("rb") as source:
        compressed = Reader(source, budget)
        with gzip.GzipFile(fileobj=compressed, mode="rb") as decoded:
            with tarfile.open(fileobj=decoded, mode="r|") as archive:
                for member in archive:
                    if observed >= len(expected):
                        raise Refused("unexpected_archive_entry")
                    row = expected[observed]
                    if (
                        not member.isfile()
                        or member.name != row["path"]
                        or member.size != row["bytes"]
                    ):
                        raise Refused("archive_file_identity_mismatch")
                    digest = hashlib.sha256()
                    stream = archive.extractfile(member)
                    count = 0
                    while value := stream.read(1024**2):
                        budget.check()
                        digest.update(value)
                        count += len(value)
                    if count != row["bytes"] or digest.hexdigest() != row["sha256"]:
                        raise Refused("archive_file_bytes_mismatch")
                    observed += 1
            # Consume the gzip trailer/CRC; tar padding may only contain zeros.
            remaining = 0
            while value := decoded.read(1024**2):
                budget.check()
                remaining += len(value)
                if any(value) or remaining > 1024**2:
                    raise Refused("unexpected_archive_trailing_data")
        if compressed.bytes != manifest["archive_bytes"]:
            raise Refused("compressed_size_mismatch")
        if compressed.digest.hexdigest() != manifest["archive_sha256"]:
            raise Refused("compressed_hash_mismatch")
    if observed != len(expected):
        raise Refused("archive_file_count_mismatch")


def preserve(run_id, estimate):
    if not re.fullmatch("[0-9a-f]{32}", run_id):
        raise Refused("invalid_explicit_run_id")
    started = time.monotonic()
    ownership = owner()
    stopped()
    baseline = inventory()
    DESTINATION.parent.resolve(strict=True)
    if DESTINATION.exists():
        identity(DESTINATION)
    else:
        DESTINATION.mkdir()
    budget = Budget(DESTINATION, estimate)
    budget.started = started
    destination = DESTINATION / run_id
    destination.mkdir()  # never overwrite/resume an existing archive
    path = destination / "cluster.tar.gz.partial"
    record = {
        "version": 1,
        "scope": "stopped_test_cluster_file_bytes_only",
        "started_at": datetime.now(UTC).isoformat(),
        "source": str(TARGET),
        "owner": ownership,
        "state": "archiving",
        "verified": False,
        "database_restore_verified": False,
        "source_deleted": False,
        "max_seconds": MAX_SECONDS,
        "max_output_bytes": MAX_BYTES,
        "minimum_free_bytes": MIN_FREE,
        "files": [],
        "directories": baseline["directories"],
        "logical_bytes": baseline["logical_bytes"],
    }
    publish_json(destination / "checkpoint.json", record)
    try:
        with path.open("xb") as raw:
            writer = Writer(raw, budget)
            with gzip.GzipFile(
                filename="", fileobj=writer, mode="wb", compresslevel=1, mtime=0
            ) as gz:
                with tarfile.open(
                    fileobj=gz, mode="w|", format=tarfile.PAX_FORMAT, copybufsize=1024**2
                ) as archive:
                    for row in baseline["files"]:
                        budget.check(writer.bytes)
                        source = TARGET / row["path"]
                        expected = {key: value for key, value in row.items() if key != "path"}
                        if identity(source) != expected:
                            raise Refused("source_changed_before_copy")
                        member = tarfile.TarInfo(row["path"])
                        member.size, member.mode = row["bytes"], 0o600
                        member.uid = member.gid = member.mtime = 0
                        with source.open("rb") as stream:
                            reader = Reader(stream, budget)
                            archive.addfile(member, reader)
                        if reader.bytes != row["bytes"] or identity(source) != expected:
                            raise Refused("source_changed_during_copy")
                        record["files"].append({**row, "sha256": reader.digest.hexdigest()})
                        record["processed_files"] = len(record["files"])
                        publish_json(destination / "checkpoint.json", record)
            raw.flush()
            os.fsync(raw.fileno())
        record.update(
            state="verifying", archive_bytes=writer.bytes, archive_sha256=writer.digest.hexdigest()
        )
        publish_json(destination / "checkpoint.json", record)
        verify_archive(path, record, budget)
        stopped()
        if inventory() != baseline:
            raise Refused("source_changed_after_verification")
        budget.check(writer.bytes)
        path.replace(destination / "cluster.tar.gz")
        record.update(
            state="verified",
            verified=True,
            file_bytes_verified=True,
            elapsed_seconds=round(time.monotonic() - budget.started, 3),
        )
        publish_json(destination / "manifest.json", record)
        publish_json(destination / "checkpoint.json", record)
        return record
    except Exception as error:
        record.update(
            state="interrupted",
            verified=False,
            error_type=type(error).__name__,
            error_code=str(error) if isinstance(error, Refused) else "archive_operation_failed",
            source_deleted=False,
            partial_preserved=path.exists(),
            elapsed_seconds=round(time.monotonic() - budget.started, 3),
        )
        publish_json(destination / "checkpoint.json", record)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--estimated-output-bytes", type=int)
    args = parser.parse_args()
    if not args.run:
        ownership = owner()
        stopped()
        info = inventory()
        print(
            json.dumps(
                {
                    "executed": False,
                    "source": str(TARGET),
                    "owner": ownership,
                    "source_files": len(info["files"]),
                    "logical_bytes": info["logical_bytes"],
                    "max_output_bytes": MAX_BYTES,
                    "minimum_free_bytes": MIN_FREE,
                    "archive_verified": False,
                    "source_deleted": False,
                }
            )
        )
        return
    if not args.run_id or args.estimated_output_bytes is None:
        parser.error("An explicit new run ID and output estimate are required")
    result = preserve(args.run_id, args.estimated_output_bytes)
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "state",
                    "verified",
                    "archive_bytes",
                    "archive_sha256",
                    "source_deleted",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
