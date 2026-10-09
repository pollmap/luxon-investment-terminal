"""Project-scoped PostgreSQL backups; predeploy files never participate in retention."""

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

REGULAR = re.compile(r"market-(\d{8}T\d{6}Z)\.dump$")
PARTIAL = re.compile(r"market-(\d{8}T\d{6}Z)\.partial$")
BACKUP_ROOTS = (Path("/srv/hannun/backups"), Path("/srv/services/korea-stocks/backups"))


def regular_backups(root: Path, *, nonempty=False):
    dated = []
    for path in root.iterdir():
        match = REGULAR.fullmatch(path.name)
        if (
            match
            and path.is_file()
            and not path.is_symlink()
            and (not nonempty or path.stat().st_size)
        ):
            try:
                dated.append(
                    (dt.datetime.strptime(match[1], "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.UTC), path)
                )
            except ValueError:
                continue
    return sorted(dated, reverse=True)


def capacity_check(usage, previous_dump_bytes=0):
    """Reserve 10% disk space plus room for a dump twice the previous size."""
    reserve = max(768 * 1024**2, int(usage.total * 0.1))
    estimate = max(256 * 1024**2, previous_dump_bytes * 2)
    fraction = usage.used / usage.total
    return {
        "state": "blocked_capacity"
        if fraction >= 0.9 or usage.free < reserve + estimate
        else "ready",
        "disk_used_fraction": fraction,
        "disk_free_bytes": usage.free,
        "required_free_bytes": reserve + estimate,
        "estimated_dump_bytes": estimate,
    }


def write_status(root, status):
    destination = root / "backup-status.json"
    if destination.is_symlink():
        raise ValueError("Unexpected backup status path")
    descriptor, name = tempfile.mkstemp(prefix=".backup-status-", suffix=".partial", dir=root)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"checked_at": dt.datetime.now(dt.UTC).isoformat(), **status}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        Path(name).replace(destination)
    finally:
        Path(name).unlink(missing_ok=True)


def metadata(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16384:
        raise ValueError("Unexpected backup metadata")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Unexpected backup metadata type")
    return value


def evidence_time(value):
    if not isinstance(value, str):
        raise ValueError("Missing evidence timestamp")
    stamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Evidence timestamp must include timezone")
    return stamp.astimezone(dt.UTC)


def offsite_recovery_status(proof, now=None):
    """Validate an operator-imported PC receipt without claiming live PC access.

    The publisher checks actual bytes. This receiver accepts only bounded,
    hash-bound evidence and emits an allowlist without PC paths or usernames.
    """
    now = now or dt.datetime.now(dt.UTC)
    checksum, size = proof.get("sha256"), proof.get("bytes")
    source = proof.get("source", "")
    if (
        proof.get("version") != 2
        or proof.get("kind") != "offsite_only"
        or proof.get("location") != "operator_managed_pc"
        or not isinstance(source, str)
        or not REGULAR.fullmatch(source)
        or not isinstance(checksum, str)
        or not re.fullmatch("[0-9a-f]{64}", checksum)
        or type(size) is not int
        or size <= 0
        or proof.get("retention") != "preserve_no_auto_delete"
        or proof.get("availability") != "present_at_check"
        or proof.get("latest_server_dump_verified") is not False
    ):
        raise ValueError("Invalid offsite recovery contract")
    checked = evidence_time(proof.get("checked_at"))
    source_created = dt.datetime.strptime(REGULAR.fullmatch(source)[1], "%Y%m%dT%H%M%SZ").replace(
        tzinfo=dt.UTC
    )
    recheck = evidence_time(proof.get("recheck_after"))
    expiry = evidence_time(proof.get("expires_at"))
    if (
        checked > now
        or source_created > checked
        or recheck != checked + dt.timedelta(days=7)
        or expiry != checked + dt.timedelta(days=30)
    ):
        raise ValueError("Invalid offsite verification window")
    verification = proof.get("verification", {})
    if not isinstance(verification, dict):
        raise ValueError("Invalid offsite verification records")
    for kind in ("plaintext", "ciphertext", "decryption", "postgres_restore"):
        if not isinstance(verification.get(kind), dict):
            raise ValueError("Missing offsite verification record")
    for kind in ("plaintext", "decryption", "postgres_restore"):
        item = verification.get(kind, {})
        if (
            item.get("sha256") != checksum
            or type(item.get("bytes")) is not int
            or item.get("bytes") != size
        ):
            raise ValueError("Offsite source evidence mismatch")
    for kind in ("decryption", "postgres_restore"):
        item = verification[kind]
        if (
            not re.fullmatch("[0-9a-f]{64}", item.get("report_sha256", ""))
            or not source_created <= evidence_time(item.get("verified_at")) <= checked
        ):
            raise ValueError("Invalid offsite evidence record")
    restore = verification["postgres_restore"]
    encrypted = verification.get("ciphertext", {})
    if (
        verification["decryption"].get("authenticated") is not True
        or restore.get("passed") is not True
        or restore.get("pg_restore_succeeded") is not True
        or type(restore.get("source_content_equality_verified")) is not bool
        or restore.get("scope") != "isolated_dump_restore_schema_and_counts"
        or not re.fullmatch("[0-9a-f]{64}", encrypted.get("sha256", ""))
        or type(encrypted.get("bytes")) is not int
        or encrypted["bytes"] <= 0
    ):
        raise ValueError("Incomplete offsite restore evidence")
    state = (
        "verification_expired" if now >= expiry else "recheck_due" if now >= recheck else "verified"
    )
    return {
        "state": state,
        "kind": "offsite_only",
        "source": source,
        "sha256": checksum,
        "bytes": size,
        "checked_at": checked.isoformat(),
        "recheck_after": recheck.isoformat(),
        "expires_at": expiry.isoformat(),
        "evidence_status": "verified",
        "availability": "present_at_check",
        "source_available_on_server": False,
        "latest_server_dump_verified": False,
        "retention": "preserve_no_auto_delete",
        "restore_scope": restore["scope"],
        "source_content_equality_verified": restore["source_content_equality_verified"],
    }


def verified_recovery(root, now=None):
    """Last verified recovery remains valid when the PC is offline on later days."""
    receipt = root / "database-recovery-verified.json"
    if not receipt.exists() and not receipt.is_symlink():
        return {"state": "blocked", "reason": "offsite_restore_evidence_missing"}
    try:
        proof = metadata(receipt)
        if proof.get("version") == 2 or proof.get("kind") == "offsite_only":
            return offsite_recovery_status(proof, now)
        name = proof.get("source", "")
        if not isinstance(name, str) or not REGULAR.fullmatch(name):
            raise ValueError("Unexpected recovery source")
        source = root / name
        saved = metadata(root / (name + ".manifest.json"))
        checksum = proof.get("sha256", "")
        if (
            source.is_symlink()
            or not source.is_file()
            or not source.stat().st_size
            or saved.get("source") != name
            or saved.get("state") != "completed"
            or saved.get("bytes") != source.stat().st_size
            or saved.get("source_mtime_ns") != source.stat().st_mtime_ns
            or not isinstance(checksum, str)
            or not re.fullmatch("[0-9a-f]{64}", checksum)
            or checksum != saved.get("sha256")
            or proof.get("restore_verified") is not True
            or proof.get("decryption_hash_verified") is not True
        ):
            raise ValueError("Recovery evidence does not match completed source")
        return {
            "state": "verified",
            "source": name,
            "sha256": checksum,
            "verified_at": proof.get("verified_at"),
        }
    except (OSError, ValueError, TypeError):
        return {"state": "blocked", "reason": "invalid_offsite_restore_evidence"}


def backup_health(root, now=None):
    now = now or dt.datetime.now(dt.UTC)
    rows = regular_backups(root, nonempty=True)
    latest = rows[0] if rows else None
    age = (now - latest[0]).total_seconds() if latest else None
    report = {
        "state": "ready" if age is not None and 0 <= age <= 36 * 3600 else "overdue",
        "latest": latest[1].name if latest else None,
        "age_seconds": age,
        "completed_regular_count": len(rows),
        "partials_preserved": sum(1 for p in root.glob("market-*.partial")),
        "empty_completed_preserved": sum(not p.stat().st_size for _, p in regular_backups(root)),
        "offsite_recovery": verified_recovery(root, now),
    }
    status_path = root / "backup-status.json"
    try:
        status = {}
        if (
            status_path.is_file()
            and not status_path.is_symlink()
            and status_path.stat().st_size < 16384
        ):
            status = json.loads(status_path.read_text(encoding="utf-8"))
            report["last_job_state"] = status.get("state")
    except (OSError, ValueError, TypeError):
        report["last_job_state"] = "invalid_metadata"
    return report


def retention_candidates(root: Path, current: Path) -> list[Path]:
    """Keep seven daily and four weekly recovery points, using parsed creation dates."""
    dated = regular_backups(root, nonempty=True)
    keep = {current}
    recovery = verified_recovery(root)
    if recovery["state"] == "verified" or recovery.get("evidence_status") == "verified":
        keep.add(root / recovery["source"])
    elif recovery.get("reason") == "invalid_offsite_restore_evidence":
        raise ValueError("Recovery receipt is invalid; inspect before backup retention")
    days, weeks = set(), set()
    for stamp, path in dated:
        day, week = stamp.date(), stamp.isocalendar()[:2]
        if day not in days and len(days) < 7:
            keep.add(path)
            days.add(day)
        if week not in weeks and len(weeks) < 4:
            keep.add(path)
            weeks.add(week)
    return [path for _, path in dated if path not in keep]


def file_identity(path):
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
        raise ValueError("Expected a regular, unlinked backup file")
    return {
        "device": value.st_dev,
        "inode": value.st_ino,
        "bytes": value.st_size,
        "mtime_ns": value.st_mtime_ns,
        "allocated_bytes": getattr(value, "st_blocks", 0) * 512,
    }


def open_backup_files(identities, proc_root=Path("/proc")):
    """Fail closed on inaccessible process descriptors; tolerate process exit races."""
    if not proc_root.is_dir():
        raise OSError("Process descriptors unavailable")
    targets = {(v["device"], v["inode"]): name for name, v in identities.items()}
    opened = set()
    for process in proc_root.iterdir():
        if not process.name.isdigit():
            continue
        try:
            for descriptor in (process / "fd").iterdir():
                try:
                    value = descriptor.stat()
                    name = targets.get((value.st_dev, value.st_ino))
                    if name:
                        opened.add(name)
                except FileNotFoundError:
                    pass
        except FileNotFoundError:
            pass
    return opened


def partial_cleanup_plan(root, now=None):
    """Only failed files older than a verified, still-present recovery dump qualify.

    Caller holds .backup.lock. No complete dump or predeploy file is a candidate.
    """
    now = now or dt.datetime.now(dt.UTC)
    recovery = verified_recovery(root, now)
    if recovery.get("state") != "verified":
        return {"state": "blocked_recovery", "candidates": []}
    source = root / recovery["source"]
    saved = metadata(root / (source.name + ".manifest.json"))
    original = file_identity(source)
    if (
        saved.get("state") != "completed"
        or saved.get("source") != source.name
        or saved.get("archive_list_verified") is not True
        or saved.get("bytes") != original["bytes"]
        or saved.get("source_mtime_ns") != original["mtime_ns"]
        or saved.get("sha256") != recovery["sha256"]
    ):
        raise ValueError("Recovery dump does not match completion evidence")
    source_time = dt.datetime.strptime(REGULAR.fullmatch(source.name)[1], "%Y%m%dT%H%M%SZ").replace(
        tzinfo=dt.UTC
    )
    candidates = []
    for path in sorted(root.iterdir()):
        match = PARTIAL.fullmatch(path.name)
        if not match:
            continue
        try:
            stamp = dt.datetime.strptime(match[1], "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.UTC)
            identity = file_identity(path)
        except ValueError:
            continue
        modified = dt.datetime.fromtimestamp(identity["mtime_ns"] / 1e9, dt.UTC)
        if (
            stamp < source_time
            and modified < source_time
            and now - modified >= dt.timedelta(hours=48)
        ):
            candidates.append({"name": path.name, **identity})
    if not candidates:
        return {"state": "ready", "candidates": []}
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024**2), b""):
            digest.update(chunk)
    if digest.hexdigest() != recovery["sha256"] or file_identity(source) != original:
        raise ValueError("Recovery dump changed or failed its content hash")
    identities = {row["name"]: row for row in candidates}
    opened = open_backup_files(identities)
    return {
        "state": "ready",
        "recovery_source": source.name,
        "recovery_sha256": digest.hexdigest(),
        "candidates": [row for row in candidates if row["name"] not in opened],
        "active_preserved": sorted(opened),
    }


def cleanup_partials_locked(root, *, apply=False, now=None):
    """Called only under the backup lock. Persist intent before scoped unlink."""
    plan = partial_cleanup_plan(root, now)
    if not apply or not plan["candidates"]:
        return {**plan, "applied": False}
    identities = {row["name"]: row for row in plan["candidates"]}
    if open_backup_files(identities):
        raise ValueError("A partial backup became active")
    for row in plan["candidates"]:
        if file_identity(root / row["name"]) != {k: v for k, v in row.items() if k != "name"}:
            raise ValueError("Partial backup changed during cleanup planning")
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S%fZ")
    audit = root / ("partial-cleanup-" + stamp + ".json")
    before = shutil.disk_usage(root).free
    with audit.open("x", encoding="utf-8") as stream:
        json.dump({**plan, "state": "planned", "planned_at": stamp}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    for row in plan["candidates"]:
        path = root / row["name"]
        if file_identity(path) != {k: v for k, v in row.items() if k != "name"}:
            raise ValueError("Partial backup changed before unlink")
        path.unlink()
    result = {
        **plan,
        "applied": True,
        "audit": audit.name,
        "allocated_bytes_removed": sum(row["allocated_bytes"] for row in plan["candidates"]),
        "free_before": before,
        "free_after": shutil.disk_usage(root).free,
    }
    completed = audit.with_suffix(".completed.json")
    with completed.open("x", encoding="utf-8") as stream:
        json.dump(result, stream)
        stream.flush()
        os.fsync(stream.fileno())
    return result


def cleanup_partials(root, *, apply=False):
    import fcntl

    os.umask(0o077)
    root = root.resolve(strict=True)
    if root not in BACKUP_ROOTS:
        raise ValueError("Unexpected backup directory")
    with (root / ".backup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return cleanup_partials_locked(root, apply=apply)


def backup(root: Path, container: str, verify_restore: bool = False):
    import fcntl

    os.umask(0o077)
    root = root.resolve()
    if root not in BACKUP_ROOTS:
        raise ValueError("Unexpected backup directory")
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".backup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            # A prior verified recovery point can retire old failed writes even
            # when the capacity guard will prevent starting today's new dump.
            try:
                cleanup = cleanup_partials_locked(root, apply=True)
            except (OSError, ValueError) as error:
                cleanup = {"state": "blocked", "error_type": type(error).__name__, "applied": False}
            return _backup_locked(root, container, verify_restore, partial_cleanup=cleanup)
        except BackupCapacityError:
            raise
        except Exception as error:
            write_status(root, {"state": "failed", "error_type": type(error).__name__})
            raise


class BackupCapacityError(RuntimeError):
    pass


def _backup_locked(root, container, verify_restore, *, partial_cleanup=None):
    previous = regular_backups(root, nonempty=True)
    capacity = capacity_check(
        shutil.disk_usage(root), previous[0][1].stat().st_size if previous else 0
    )
    if capacity["state"] != "ready":
        write_status(root, {**capacity, "partial_cleanup": partial_cleanup})
        raise BackupCapacityError(
            "Insufficient space for a new database backup; existing backups preserved"
        )
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    target = root / f"market-{stamp}.dump"
    temporary = target.with_suffix(".partial")
    write_status(
        root, {"state": "running", "source": target.name, "partial_cleanup": partial_cleanup}
    )
    with temporary.open("xb") as output:
        subprocess.run(
            ["docker", "exec", container, "pg_dump", "-U", "market", "-d", "market", "-Fc"],
            stdout=output,
            check=True,
        )
        output.flush()
        os.fsync(output.fileno())
    if not temporary.stat().st_size:
        raise ValueError("Empty database dump")
    with temporary.open("rb") as source:
        subprocess.run(
            ["docker", "exec", "-i", container, "pg_restore", "--list"],
            stdin=source,
            stdout=subprocess.DEVNULL,
            check=True,
        )
    if verify_restore:
        isolated = "backup_verify_" + stamp.lower()
        subprocess.run(
            ["docker", "exec", container, "createdb", "-U", "market", isolated], check=True
        )
        try:
            with temporary.open("rb") as source:
                subprocess.run(
                    [
                        "docker",
                        "exec",
                        "-i",
                        container,
                        "pg_restore",
                        "-U",
                        "market",
                        "-d",
                        isolated,
                        "--exit-on-error",
                        "--no-owner",
                    ],
                    stdin=source,
                    check=True,
                )
            subprocess.run(
                [
                    "docker",
                    "exec",
                    container,
                    "psql",
                    "-U",
                    "market",
                    "-d",
                    isolated,
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-c",
                    "SELECT count(*) AS restored_tables FROM information_schema.tables "
                    "WHERE table_schema='public';",
                ],
                check=True,
            )
        finally:
            subprocess.run(
                ["docker", "exec", container, "dropdb", "-U", "market", isolated], check=True
            )
    temporary.replace(target)
    checksum = hashlib.sha256()
    with target.open("rb") as source:
        for chunk in iter(lambda: source.read(1024**2), b""):
            checksum.update(chunk)
    completed = {
        "state": "completed",
        "source": target.name,
        "bytes": target.stat().st_size,
        "sha256": checksum.hexdigest(),
        "archive_list_verified": True,
        "restore_verified": verify_restore,
        "source_mtime_ns": target.stat().st_mtime_ns,
    }
    with (root / (target.name + ".manifest.json")).open("x", encoding="utf-8") as stream:
        json.dump(completed, stream)
        stream.flush()
        os.fsync(stream.fileno())
    write_status(root, completed)
    for path in retention_candidates(root, target):
        path.unlink()
    write_status(root, {**completed, "partial_cleanup": partial_cleanup})
    print(
        "Database backup completed:",
        target.name,
        target.stat().st_size,
        "bytes; restore_verified=",
        verify_restore,
    )
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/srv/hannun/backups"))
    parser.add_argument("--container", default="hannun-public-market-market-db-1")
    parser.add_argument("--verify-restore", action="store_true")
    parser.add_argument("--cleanup-partials", action="store_true")
    parser.add_argument("--apply", action="store_true", help="Apply only partial backup cleanup")
    args = parser.parse_args()
    if args.apply and not args.cleanup_partials:
        parser.error("--apply requires --cleanup-partials")
    if args.cleanup_partials:
        print(json.dumps(cleanup_partials(args.root, apply=args.apply)))
    else:
        backup(args.root, args.container, args.verify_restore)
