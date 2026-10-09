"""Bounded PC offsite transfer. No remote writes, retention deletes or PG restore.

Daily output is transfer/encryption evidence, never PostgreSQL restore evidence.
Existing KSTOCKS1 files and recovery receipts are left untouched. New copies use
the independently verified KSDGCM01 codec and keep their plaintext originals.
"""

import argparse
import base64
import contextlib
import datetime as dt
import hashlib
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REGULAR = re.compile(r"market-\d{8}T\d{6}Z\.dump$")
GIB = 1024**3
REMOTE_ROOT = "/srv/services/korea-stocks/backups"
SSH = [
    "ssh",
    "-i",
    str(Path.home() / ".ssh/openclaw_vmi3138243_ed25519"),
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=10",
    "-o",
    "StrictHostKeyChecking=yes",
    "-o",
    "ServerAliveInterval=15",
    "-o",
    "ServerAliveCountMax=2",
    "root@62.171.141.206",
]


class AlreadyRunningError(RuntimeError):
    pass


class CapacityError(RuntimeError):
    pass


def utcnow():
    return dt.datetime.now(dt.UTC).isoformat()


def unlinked(path):
    """Reject symlinks and Windows junctions, including parent directories."""
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError("Linked backup paths are not supported")
    if os.name == "nt" and not str(path).startswith("\\\\?\\"):
        path = (
            Path("\\\\?\\UNC\\" + str(path)[2:])
            if str(path).startswith("\\\\")
            else Path("\\\\?\\" + str(path))
        )
    return path


@contextlib.contextmanager
def exclusive_lock(root):
    path = unlinked(root / ".offsite.lock")
    with path.open("a+b") as lock:
        if lock.seek(0, os.SEEK_END) == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise AlreadyRunningError("Offsite backup already running") from error
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def write_json(path, value):
    path = unlinked(path)
    pending = path.with_name(path.name + "." + uuid.uuid4().hex + ".partial")
    with pending.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    pending.replace(path)


def checked_json(path):
    path = unlinked(path)
    if not path.is_file() or not 0 < path.stat().st_size <= 64 * 1024:
        raise ValueError("Invalid backup metadata")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Invalid backup metadata type")
    return value


def remote_descriptor(root, requested=None, max_bytes=8 * 1024**3):
    """Runs read-only on the VPS; only producer-completed manifest pairs qualify."""
    root = Path(root)
    if requested is not None and not REGULAR.fullmatch(requested):
        raise ValueError("Invalid dump name")
    candidates = []
    for path in root.iterdir():
        if (
            REGULAR.fullmatch(path.name)
            and not path.is_symlink()
            and path.is_file()
            and path.stat().st_size > 0
        ):
            dt.datetime.strptime(path.name[7:-5], "%Y%m%dT%H%M%SZ")
            candidates.append(path)
    if not candidates:
        raise FileNotFoundError("No completed backup")
    target = root / requested if requested else max(candidates, key=lambda p: p.name)
    manifest = target.with_name(target.name + ".manifest.json")
    if (
        target not in candidates
        or manifest.is_symlink()
        or not manifest.is_file()
        or not 0 < manifest.stat().st_size <= 16384
    ):
        raise ValueError("Latest dump completion manifest unavailable")
    proof = json.loads(manifest.read_text(encoding="utf-8"))
    before = target.stat()
    if (
        not isinstance(proof, dict)
        or proof.get("state") != "completed"
        or proof.get("source") != target.name
        or proof.get("bytes") != before.st_size
        or proof.get("source_mtime_ns") != before.st_mtime_ns
        or proof.get("archive_list_verified") is not True
        or not re.fullmatch("[0-9a-f]{64}", str(proof.get("sha256", "")))
        or not 0 < before.st_size <= max_bytes
    ):
        raise ValueError("Completed backup metadata does not match source")
    with target.open("rb") as stream:
        if stream.read(5) != b"PGDMP":
            raise ValueError("Expected PostgreSQL custom dump")
    return {
        "source": target.name,
        "bytes": before.st_size,
        "sha256": proof["sha256"],
        "source_mtime_ns": before.st_mtime_ns,
        "archive_list_verified": True,
    }


def remote_copy(root, record):
    """Bound byte count and verify source hash while streaming over SSH."""
    if not REGULAR.fullmatch(record["source"]):
        raise ValueError("Invalid dump name")
    path = Path(root) / record["source"]
    if path.is_symlink():
        raise ValueError("Linked dump")
    with path.open("rb") as source:
        before = os.fstat(source.fileno())
        if before.st_size != record["bytes"] or before.st_mtime_ns != record["source_mtime_ns"]:
            raise ValueError("Source changed before transfer")
        remaining, checksum = record["bytes"], hashlib.sha256()
        while remaining:
            chunk = source.read(min(1024**2, remaining))
            if not chunk:
                raise ValueError("Truncated source")
            checksum.update(chunk)
            sys.stdout.buffer.write(chunk)
            remaining -= len(chunk)
        after = os.fstat(source.fileno())
        if (
            source.read(1)
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_size != before.st_size
            or checksum.hexdigest() != record["sha256"]
        ):
            raise ValueError("Source changed during transfer")
    sys.stdout.buffer.flush()


def remote_command(function, arguments):
    code = (
        "import datetime as dt,hashlib,json,os,re,sys\nfrom pathlib import Path\n"
        + "REGULAR=re.compile("
        + repr(REGULAR.pattern)
        + ")\n"
        + inspect.getsource(function)
        + "\ntry:\n result="
        + function.__name__
        + "(*"
        + repr(arguments)
        + ")\n"
        + " if result is not None: print(json.dumps(result))\n"
        + "except Exception as error:\n print(type(error).__name__,file=sys.stderr)\n"
        + " sys.exit(1)\n"
    )
    encoded = base64.b64encode(code.encode()).decode()
    return [*SSH, "python3", "-c", "\"import base64;exec(base64.b64decode('" + encoded + "'))\""]


def describe_remote(name=None, max_bytes=8 * GIB):
    result = subprocess.run(
        remote_command(remote_descriptor, [REMOTE_ROOT, name, max_bytes]),
        capture_output=True,
        stdin=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        timeout=45,
        check=True,
    )
    if len(result.stdout) > 16384:
        raise ValueError("Unexpected remote metadata size")
    return validate_record(json.loads(result.stdout), max_bytes)


def validate_record(record, max_bytes):
    if (
        not isinstance(record, dict)
        or not REGULAR.fullmatch(str(record.get("source", "")))
        or type(record.get("bytes")) is not int
        or not 0 < record["bytes"] <= max_bytes
        or not re.fullmatch("[0-9a-f]{64}", str(record.get("sha256", "")))
        or type(record.get("source_mtime_ns")) is not int
        or record["source_mtime_ns"] <= 0
        or record.get("archive_list_verified") is not True
    ):
        raise ValueError("Invalid remote backup descriptor")
    return record


def download(record, pending, timeout=5400):
    with pending.open("xb") as destination:
        subprocess.run(
            remote_command(remote_copy, [REMOTE_ROOT, record]),
            stdout=destination,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            timeout=timeout,
            check=True,
        )
        destination.flush()
        os.fsync(destination.fileno())


def directory_bytes(root):
    total = 0
    for path in root.rglob("*"):
        unlinked(path)
        if path.is_file():
            total += path.stat().st_size
    return total


def capacity(root, incoming, max_total, min_free):
    current, usage = directory_bytes(root), shutil.disk_usage(root)
    required = incoming * 2 + 1024**2  # Original + ciphertext + bounded metadata.
    return {
        "state": "ready"
        if current + required <= max_total and usage.free >= min_free + required
        else "blocked_capacity",
        "managed_bytes": current,
        "max_total_bytes": max_total,
        "required_bytes": required,
        "disk_free_bytes": usage.free,
        "min_free_bytes": min_free,
        "automatic_deletion": False,
    }


def codec():
    from ops.market import backup_crypto

    if os.name != "nt":
        raise RuntimeError("Current-user Windows DPAPI required")
    return backup_crypto


def verify_complete(folder, record, crypto):
    report = checked_json(folder / "transfer.json")
    if (
        report.get("state") != "transferred_encrypted_verified"
        or report.get("postgres_restore_verified") is not False
        or any(report.get(field) != record[field] for field in ("source", "bytes", "sha256"))
    ):
        raise ValueError("Existing transfer does not match source")
    plain = unlinked(folder / record["source"])
    encrypted = unlinked(folder / (record["source"] + ".aes256gcm"))
    key = unlinked(folder / "key.dpapi")
    if crypto.digest_file(plain) != {"bytes": record["bytes"], "sha256": record["sha256"]}:
        raise ValueError("Preserved original changed")
    ciphertext = crypto.digest_file(encrypted)
    proof = report.get("encryption", {})
    if ciphertext["bytes"] != proof.get("ciphertext_bytes") or ciphertext["sha256"] != proof.get(
        "ciphertext_sha256"
    ):
        raise ValueError("Preserved ciphertext changed")
    restored = crypto.decrypt(encrypted, key)
    if (
        restored.get("authenticated") is not True
        or restored.get("plaintext_bytes") != record["bytes"]
        or restored.get("plaintext_sha256") != record["sha256"]
    ):
        raise ValueError("Preserved encryption did not authenticate")
    return report


def run_backup(
    root,
    *,
    name=None,
    max_total=32 * GIB,
    min_free=10 * GIB,
    max_dump=8 * GIB,
    transfer_timeout=5400,
    describe=None,
    fetch=None,
    crypto=None,
):
    """Only the managed root is written. Existing backup/receipt paths are not touched."""
    if name is not None and not REGULAR.fullmatch(name):
        raise ValueError("Invalid dump name")
    if min(max_total, min_free, max_dump, transfer_timeout) <= 0:
        raise ValueError("Positive limits required")
    root = unlinked(root)
    root.mkdir(parents=True, exist_ok=True)
    describe, fetch = describe or describe_remote, fetch or download
    with exclusive_lock(root):
        status_path = root / "last-attempt.json"
        try:
            write_json(status_path, {"state": "running", "started_at": utcnow()})
            crypto = crypto or codec()  # Check crypto availability before downloading.
            record = validate_record(describe(name, max_dump), max_dump)
            destination = unlinked(root / record["source"])
            if destination.exists():
                completed = verify_complete(destination, record, crypto)
                write_json(root / "last-transfer.json", completed)
                result = {
                    "state": "already_transferred",
                    "source": record["source"],
                    "bytes": record["bytes"],
                    "sha256": record["sha256"],
                    "checked_at": utcnow(),
                    "postgres_restore_verified": False,
                }
            else:
                space = capacity(root, record["bytes"], max_total, min_free)
                if space["state"] != "ready":
                    write_json(status_path, {**space, "checked_at": utcnow()})
                    raise CapacityError("Insufficient PC backup capacity")
                staging = root / ("." + record["source"] + "." + uuid.uuid4().hex + ".partial")
                staging.mkdir()
                temporary = staging / (record["source"] + ".partial")
                fetch(record, temporary, transfer_timeout)
                if crypto.digest_file(temporary) != {
                    "bytes": record["bytes"],
                    "sha256": record["sha256"],
                }:
                    raise ValueError("Downloaded bytes differ from completed source")
                with temporary.open("rb") as stream:
                    if stream.read(5) != b"PGDMP":
                        raise ValueError("Expected PostgreSQL custom dump")
                original = staging / record["source"]
                temporary.rename(original)
                encrypted = staging / (record["source"] + ".aes256gcm")
                key = staging / "key.dpapi"
                encryption = crypto.encrypt(
                    original,
                    encrypted,
                    key,
                    expected_sha256=record["sha256"],
                    expected_bytes=record["bytes"],
                )
                decrypted = crypto.decrypt(encrypted, key)
                cipher_hash = crypto.digest_file(encrypted)
                if (
                    decrypted.get("authenticated") is not True
                    or decrypted.get("plaintext_sha256") != record["sha256"]
                    or decrypted.get("plaintext_bytes") != record["bytes"]
                    or cipher_hash
                    != {
                        "sha256": encryption["ciphertext_sha256"],
                        "bytes": encryption["ciphertext_bytes"],
                    }
                ):
                    raise ValueError("Encrypted transfer verification failed")
                write_json(
                    staging / "backup-verification.json",
                    {
                        "status": "verified",
                        "encryption": encryption,
                        "full_decryption": decrypted,
                        "independent_ciphertext_hash_match": True,
                        "original_dump_preserved": True,
                        "database_restore_not_performed_by_this_tool": True,
                        "portable_recovery_key_issued": False,
                    },
                )
                result = {
                    "version": 1,
                    "state": "transferred_encrypted_verified",
                    **record,
                    "checked_at": utcnow(),
                    "encryption": encryption,
                    "postgres_restore_verified": False,
                    "original_dump_preserved": True,
                    "automatic_deletion": False,
                }
                write_json(staging / "transfer.json", result)
                if destination.exists():
                    raise FileExistsError("Existing backup preserved")
                staging.rename(destination)
                write_json(root / "last-transfer.json", result)
            write_json(status_path, result)
            return result
        except CapacityError:
            raise
        except Exception as error:
            write_json(
                status_path,
                {
                    "state": "failed",
                    "error_type": type(error).__name__,
                    "checked_at": utcnow(),
                    "previous_copies_preserved": True,
                },
            )
            raise


def emit_result(result):
    # pythonw has no standard streams. Durable JSON status files and the process
    # exit code remain authoritative; a console is only an optional convenience.
    if sys.stdout is not None:
        print(json.dumps(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", nargs="?", help="Exact completed name; omit with --latest")
    parser.add_argument("--latest", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT / "work/backups/daily")
    parser.add_argument("--max-total-gib", type=int, default=32)
    parser.add_argument("--min-free-gib", type=int, default=10)
    parser.add_argument("--max-dump-gib", type=int, default=8)
    args = parser.parse_args()
    if bool(args.name) == args.latest:
        parser.error("Specify an exact dump name or --latest")
    try:
        result = run_backup(
            args.root,
            name=args.name,
            max_total=args.max_total_gib * GIB,
            min_free=args.min_free_gib * GIB,
            max_dump=args.max_dump_gib * GIB,
        )
    except AlreadyRunningError:
        emit_result({"state": "already_running"})
        return 0
    except Exception as error:
        emit_result({"state": "failed", "error_type": type(error).__name__})
        return 1
    emit_result({key: value for key, value in result.items() if key != "encryption"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
