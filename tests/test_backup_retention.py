"""fixture_non_production: retention protects new, weekly and predeploy backups."""

import datetime as dt
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from ops.market import backup as module
from ops.market.backup import backup_health, capacity_check, retention_candidates


def test_predeploy_does_not_delete_new_dump(tmp_path):
    for n in range(9):
        (tmp_path / f"market-predeploy-{n}.dump").touch()
    current = tmp_path / "market-20261003T000000Z.dump"
    current.touch()
    assert retention_candidates(tmp_path, current) == []


def test_daily_weekly_and_partial_retention(tmp_path):
    today = dt.datetime(2026, 10, 3)
    for offset in range(45):
        (tmp_path / f"market-{today - dt.timedelta(days=offset):%Y%m%dT%H%M%SZ}.dump").write_bytes(
            b"fixture"
        )
    partial = tmp_path / "market-20200101T000000Z.partial"
    partial.touch()
    current = tmp_path / "market-20261003T000000Z.dump"
    removed = retention_candidates(tmp_path, current)
    assert current not in removed and partial not in removed
    kept = set(tmp_path.glob("*.dump")) - set(removed)
    assert len(kept) == 9
    assert tmp_path / "market-20260927T000000Z.dump" in kept


def test_recent_predeploy_or_empty_regular_cannot_mask_overdue_backup(tmp_path):
    (tmp_path / "market-20261001T000000Z.dump").write_bytes(b"old")
    (tmp_path / "market-20261009T000000Z.dump").touch()
    (tmp_path / "market-predeploy-20261009T000000Z.dump").write_bytes(b"new")
    partial = tmp_path / "market-20261009T100000Z.partial"
    partial.write_bytes(b"in progress")
    report = backup_health(tmp_path, dt.datetime(2026, 10, 9, tzinfo=dt.UTC))
    assert report["state"] == "overdue"
    assert report["latest"] == "market-20261001T000000Z.dump"
    assert report["partials_preserved"] == 1 and partial.read_bytes() == b"in progress"


def test_capacity_guard_preserves_ten_percent_and_next_dump_room():
    gb = 1024**3
    assert (
        capacity_check(SimpleNamespace(total=100 * gb, used=60 * gb, free=40 * gb), 2 * gb)["state"]
        == "ready"
    )
    assert (
        capacity_check(SimpleNamespace(total=100 * gb, used=87 * gb, free=13 * gb), 2 * gb)["state"]
        == "blocked_capacity"
    )
    assert (
        capacity_check(SimpleNamespace(total=100 * gb, used=90 * gb, free=10 * gb))["state"]
        == "blocked_capacity"
    )


def test_offsite_decryption_alone_is_not_database_restore(tmp_path):
    name = "market-20261009T000000Z.dump"
    (tmp_path / name).write_bytes(b"fixture")
    now = dt.datetime(2026, 10, 9, tzinfo=dt.UTC)
    proof = {"source": name, "sha256": "a" * 64, "decryption_hash_verified": True}
    (tmp_path / (name + ".manifest.json")).write_text(
        json.dumps(
            {
                "source": name,
                "sha256": "a" * 64,
                "state": "completed",
                "bytes": 7,
                "source_mtime_ns": (tmp_path / name).stat().st_mtime_ns,
            }
        )
    )
    (tmp_path / "database-recovery-verified.json").write_text(json.dumps(proof))
    assert backup_health(tmp_path, now)["offsite_recovery"]["state"] == "blocked"
    proof["restore_verified"] = True
    (tmp_path / "database-recovery-verified.json").write_text(json.dumps(proof))
    assert backup_health(tmp_path, now)["offsite_recovery"]["state"] == "verified"
    proof["source"] = "market-20261008T000000Z.dump"
    (tmp_path / "database-recovery-verified.json").write_text(json.dumps(proof))
    assert backup_health(tmp_path, now)["offsite_recovery"]["state"] == "blocked"


def recovery_receipt(root, name):
    source = root / name
    source.write_bytes(b"verified")
    (root / (name + ".manifest.json")).write_text(
        json.dumps(
            {
                "source": name,
                "sha256": "a" * 64,
                "state": "completed",
                "bytes": 8,
                "source_mtime_ns": source.stat().st_mtime_ns,
            }
        )
    )
    (root / "database-recovery-verified.json").write_text(
        json.dumps(
            {
                "source": name,
                "sha256": "a" * 64,
                "restore_verified": True,
                "decryption_hash_verified": True,
            }
        )
    )


def test_new_daily_backups_preserve_last_offsite_restore(tmp_path):
    verified = "market-20260801T000000Z.dump"
    recovery_receipt(tmp_path, verified)
    now = dt.datetime(2026, 10, 9, tzinfo=dt.UTC)
    for offset in range(45):
        (tmp_path / f"market-{now - dt.timedelta(days=offset):%Y%m%dT%H%M%SZ}.dump").write_bytes(
            b"new"
        )
    current = tmp_path / "market-20261009T000000Z.dump"
    (tmp_path / "backup-status.json").write_text(
        json.dumps({"source": current.name, "state": "completed"})
    )
    report = backup_health(tmp_path, now)
    assert report["state"] == "ready"
    assert report["offsite_recovery"]["source"] == verified
    assert report["offsite_recovery"]["state"] == "verified"
    removed = retention_candidates(tmp_path, current)
    assert tmp_path / verified not in removed
    assert len(set(tmp_path.glob("*.dump")) - set(removed)) <= 12


def test_empty_same_day_dump_does_not_displace_valid_recovery(tmp_path):
    good = tmp_path / "market-20261001T040000Z.dump"
    empty = tmp_path / "market-20261001T050000Z.dump"
    good.write_bytes(b"valid")
    empty.touch()
    current = tmp_path / "market-20261002T040000Z.dump"
    current.write_bytes(b"valid")
    assert good not in retention_candidates(tmp_path, current)
    assert empty not in retention_candidates(tmp_path, current)
    assert backup_health(tmp_path)["empty_completed_preserved"] == 1


def test_dump_failure_records_safe_state_and_preserves_partial(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "BACKUP_ROOTS", (tmp_path,))
    monkeypatch.setattr(module.os, "umask", lambda _: None)
    monkeypatch.setitem(
        sys.modules, "fcntl", SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=lambda *a: None)
    )
    monkeypatch.setattr(module, "capacity_check", lambda *a: {"state": "ready"})
    (tmp_path / "backup-status.json").write_text(json.dumps({"state": "completed"}))

    def fail(command, **kwargs):
        kwargs["stdout"].write(b"partial dump")
        raise subprocess.CalledProcessError(1, ["sensitive-command"])

    monkeypatch.setattr(module.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        module.backup(tmp_path, "fixture-container")
    saved = json.loads((tmp_path / "backup-status.json").read_text())
    assert saved["state"] == "failed" and saved["error_type"] == "CalledProcessError"
    assert "sensitive" not in json.dumps(saved)
    assert next(tmp_path.glob("market-*.partial")).read_bytes() == b"partial dump"
