"""Verify existing PC artifacts and write an internal receipt for operator import.

No SSH, download, decryption, PostgreSQL restore or file deletion is performed.
The source reports must already describe full authenticated decryption and an
isolated PostgreSQL restore of the exact same plaintext dump.
"""

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from ops.market.backup import REGULAR, evidence_time, offsite_recovery_status  # noqa: E402


def checked_file(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Required recovery artifact is missing or linked")
    before = path.stat()
    if before.st_size <= 0:
        raise ValueError("Recovery artifact is empty")
    checksum, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024**2), b""):
            checksum.update(chunk)
            size += len(chunk)
    after = path.stat()
    if (
        before.st_size != size
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_size != after.st_size
        or before.st_ino != after.st_ino
    ):
        raise ValueError("Recovery artifact changed during verification")
    return {"sha256": checksum.hexdigest(), "bytes": size}


def checked_report(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size < 1024**2:
        raise ValueError("Invalid recovery report file")
    raw = path.read_bytes()
    report = json.loads(raw)
    if not isinstance(report, dict):
        raise ValueError("Invalid recovery report")
    return report, hashlib.sha256(raw).hexdigest()


def build_receipt(dump, ciphertext, decryption_report, restore_report, *, now=None):
    dump, ciphertext = Path(dump), Path(ciphertext)
    if not REGULAR.fullmatch(dump.name):
        raise ValueError("Expected a named regular database dump")

    def identity(path):
        if path.is_symlink() or not path.is_file():
            raise ValueError("Recovery artifact is missing or linked")
        state = path.stat()
        return state.st_size, state.st_mtime_ns, state.st_ino

    before = {path: identity(path) for path in (dump, ciphertext)}
    plaintext = checked_file(dump)
    encrypted = checked_file(ciphertext)
    decrypt, decrypt_hash = checked_report(decryption_report)
    restore, restore_hash = checked_report(restore_report)
    encryption = decrypt.get("encryption", {})
    full = decrypt.get("full_decryption", {})
    if (
        decrypt.get("status") != "verified"
        or decrypt.get("independent_ciphertext_hash_match") is not True
        or full.get("authenticated") is not True
        or encryption.get("source_file") != dump.name
        or encryption.get("ciphertext_file") != ciphertext.name
        or encryption.get("ciphertext_sha256") != encrypted["sha256"]
        or encryption.get("ciphertext_bytes") != encrypted["bytes"]
        or any(
            value.get("plaintext_sha256") != plaintext["sha256"]
            or value.get("plaintext_bytes") != plaintext["bytes"]
            for value in (encryption, full)
        )
    ):
        raise ValueError("Actual files do not match authenticated decryption evidence")
    # Daily restores use a fresh cluster rather than the older reserved cluster.
    # Accept that report only after its isolation, unchanged-input and stop gates
    # all pass; do not manufacture a successful legacy report from it.
    if "restored" in restore:
        if (
            restore.get("stage") != "finished"
            or restore.get("production_contacted") is not False
            or restore.get("existing_cluster_modified") is not False
            or restore.get("dump_unchanged") is not True
            or restore.get("local_cluster_stopped") is not True
            or Path(restore.get("dump", "")).resolve() != dump.resolve()
            or restore.get("sha256") != plaintext["sha256"]
        ):
            raise ValueError("Daily restore isolation or unchanged-input evidence failed")
        restored = restore["restored"]
        restore_checksum = restore["sha256"]
        # The hash binds the whole unchanged file; size is independently read.
        restore_size = plaintext["bytes"]
    else:
        restored = restore.get("restore", {})
        restore_checksum = restore.get("dump_sha256")
        restore_size = restore.get("dump_bytes")
    elapsed = restore.get("elapsed_seconds")
    if (
        restore.get("status") != "verified"
        or restore.get("passed") is not True
        or restore.get("pg_restore_succeeded") is not True
        or restored.get("passed") is not True
        or restored.get("missing_required_tables") != []
        or restored.get("invalid_indexes") != []
        or restore_checksum != plaintext["sha256"]
        or restore_size != plaintext["bytes"]
        or type(restore.get("source_content_equality_verified")) is not bool
        or type(elapsed) not in (int, float)
        or not math.isfinite(elapsed)
        or elapsed < 0
    ):
        raise ValueError("Actual dump does not match successful isolated PostgreSQL restore")
    if any(identity(path) != original for path, original in before.items()):
        raise ValueError("Recovery artifact changed before receipt creation")
    checked = now or dt.datetime.now(dt.UTC)
    receipt = {
        "version": 2,
        "kind": "offsite_only",
        "location": "operator_managed_pc",
        "source": dump.name,
        **plaintext,
        "checked_at": checked.isoformat(),
        "recheck_after": (checked + dt.timedelta(days=7)).isoformat(),
        "expires_at": (checked + dt.timedelta(days=30)).isoformat(),
        "availability": "present_at_check",
        "retention": "preserve_no_auto_delete",
        "latest_server_dump_verified": False,
        "verification": {
            "plaintext": plaintext,
            "ciphertext": encrypted,
            "decryption": {
                **plaintext,
                "report_sha256": decrypt_hash,
                "verified_at": full.get("verified_at"),
                "authenticated": True,
            },
            "postgres_restore": {
                **plaintext,
                "report_sha256": restore_hash,
                "verified_at": (
                    evidence_time(restore.get("started_at")) + dt.timedelta(seconds=elapsed)
                ).isoformat(),
                "timestamp_basis": "started_at_plus_elapsed_seconds",
                "passed": True,
                "pg_restore_succeeded": True,
                "scope": "isolated_dump_restore_schema_and_counts",
                "source_content_equality_verified": restore["source_content_equality_verified"],
            },
        },
    }
    # Use the same strict receiver contract before writing a receipt.
    offsite_recovery_status(receipt, checked)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump", type=Path, required=True)
    parser.add_argument("--ciphertext", type=Path, required=True)
    parser.add_argument("--decryption-report", type=Path, required=True)
    parser.add_argument("--restore-report", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists() or args.out.is_symlink():
        raise ValueError("Receipt already exists; use a new evidence filename")
    receipt = build_receipt(args.dump, args.ciphertext, args.decryption_report, args.restore_report)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    print(
        json.dumps(
            {k: receipt[k] for k in ("version", "kind", "source", "sha256", "bytes", "checked_at")}
        )
    )


if __name__ == "__main__":
    main()
