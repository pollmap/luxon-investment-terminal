"""fixture_non_production: failed backup cleanup must not weaken recovery retention."""

import datetime as dt
import hashlib
import json
import os
import sys
from types import SimpleNamespace

import pytest

from ops.market import backup as module

NOW = dt.datetime(2026, 10, 10, tzinfo=dt.UTC)


@pytest.fixture
def backup_tree(tmp_path, monkeypatch):
    name = "market-20261009T153042Z.dump"
    source = tmp_path / name
    source.write_bytes(b"fixture_non_production_verified_restore")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    (tmp_path / (name + ".manifest.json")).write_text(
        json.dumps(
            {
                "state": "completed",
                "source": name,
                "bytes": source.stat().st_size,
                "source_mtime_ns": source.stat().st_mtime_ns,
                "sha256": digest,
                "archive_list_verified": True,
            }
        )
    )
    (tmp_path / "database-recovery-verified.json").write_text(
        json.dumps(
            {
                "source": name,
                "sha256": digest,
                "restore_verified": True,
                "decryption_hash_verified": True,
            }
        )
    )
    monkeypatch.setattr(module, "open_backup_files", lambda identities: set())
    return tmp_path


def partial(root, name="market-20261003T033900Z.partial", *, age=7):
    target = root / name
    target.write_bytes(b"incomplete_backup")
    stamp = (NOW - dt.timedelta(days=age)).timestamp()
    os.utime(target, (stamp, stamp))
    return target


def test_only_failed_partial_removed_after_verified_replacement(backup_tree):
    failed = partial(backup_tree)
    current = partial(backup_tree, "market-20261010T000000Z.partial", age=0)
    predeploy = partial(backup_tree, "market-predeploy-partial.partial")
    completed = backup_tree / "market-20261002T000000Z.dump"
    completed.write_bytes(b"older daily backup")
    plan = module.cleanup_partials_locked(backup_tree, now=NOW)
    assert plan["applied"] is False and failed.exists()
    assert [r["name"] for r in plan["candidates"]] == [failed.name]
    result = module.cleanup_partials_locked(backup_tree, apply=True, now=NOW)
    assert result["applied"] and not failed.exists()
    assert current.exists() and predeploy.exists() and completed.exists()
    assert (backup_tree / result["recovery_source"]).exists()
    assert json.loads((backup_tree / result["audit"]).read_text())["state"] == "planned"
    assert (backup_tree / result["audit"]).with_suffix(".completed.json").is_file()


@pytest.mark.parametrize("damage", ["receipt", "manifest", "content"])
def test_missing_or_changed_recovery_preserves_partial(backup_tree, damage):
    failed = partial(backup_tree)
    if damage in ("receipt", "manifest"):
        if damage == "receipt":
            (backup_tree / "database-recovery-verified.json").unlink()
        else:
            (backup_tree / "market-20261009T153042Z.dump.manifest.json").write_text("{}")
        result = module.cleanup_partials_locked(backup_tree, apply=True, now=NOW)
        assert result["state"] == "blocked_recovery"
    else:
        source = backup_tree / "market-20261009T153042Z.dump"
        original = source.stat()
        source.write_bytes(b"x" * original.st_size)
        os.utime(source, ns=(original.st_atime_ns, original.st_mtime_ns))
        with pytest.raises(ValueError):
            module.cleanup_partials_locked(backup_tree, apply=True, now=NOW)
    assert failed.exists()


def test_partial_open_by_any_process_is_preserved(backup_tree, monkeypatch):
    failed = partial(backup_tree)
    monkeypatch.setattr(module, "open_backup_files", lambda identities: {failed.name})
    result = module.cleanup_partials_locked(backup_tree, apply=True, now=NOW)
    assert result["applied"] is False and result["active_preserved"] == [failed.name]
    assert failed.exists()


def test_failed_process_inspection_blocks_cleanup(backup_tree, monkeypatch):
    failed = partial(backup_tree)

    def inaccessible(_):
        raise PermissionError("fixture process disappeared with protected fd")

    monkeypatch.setattr(module, "open_backup_files", inaccessible)
    with pytest.raises(PermissionError):
        module.cleanup_partials_locked(backup_tree, apply=True, now=NOW)
    assert failed.exists()


@pytest.mark.parametrize("change", ["opened", "mutated"])
def test_changed_partial_after_plan_blocks_unlink(backup_tree, monkeypatch, change):
    failed = partial(backup_tree)
    calls = []

    def inspect(identities):
        calls.append(identities)
        if len(calls) == 2:
            if change == "opened":
                return {failed.name}
            failed.write_bytes(b"different partial bytes")
        return set()

    monkeypatch.setattr(module, "open_backup_files", inspect)
    with pytest.raises(ValueError):
        module.cleanup_partials_locked(backup_tree, apply=True, now=NOW)
    assert failed.exists()


def test_recently_touched_or_newer_partial_never_deleted(backup_tree):
    touched = partial(backup_tree, age=0)
    newer = partial(backup_tree, "market-20261010T010000Z.partial")
    assert module.partial_cleanup_plan(backup_tree, NOW)["candidates"] == []
    assert touched.exists() and newer.exists()


def test_hardlinked_partial_preserved(backup_tree):
    failed = partial(backup_tree)
    os.link(failed, backup_tree / "keep-linked.partial")
    assert module.partial_cleanup_plan(backup_tree, NOW)["candidates"] == []
    assert failed.exists()


def test_missing_proc_root_is_not_proof_of_inactivity(tmp_path):
    with pytest.raises(OSError):
        module.open_backup_files({}, proc_root=tmp_path / "missing")


def test_proc_scan_follows_open_descriptor_identity(tmp_path):
    """Plain test files substitute for /proc symlinks, using the same stat contract."""
    fd = tmp_path / "100" / "fd"
    fd.mkdir(parents=True)
    target = fd / "3"
    target.write_bytes(b"open")
    identities = {"market-fixture.partial": module.file_identity(target)}
    assert module.open_backup_files(identities, proc_root=tmp_path) == {"market-fixture.partial"}


def test_path_directory_not_a_candidate(backup_tree):
    (backup_tree / "market-20261003T033900Z.partial").mkdir()
    assert module.partial_cleanup_plan(backup_tree, NOW)["candidates"] == []


def test_safe_cleanup_runs_before_capacity_guard(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "BACKUP_ROOTS", (tmp_path,))
    monkeypatch.setitem(
        sys.modules, "fcntl", SimpleNamespace(LOCK_EX=1, LOCK_NB=2, flock=lambda *args: None)
    )
    calls = []

    def clean(root, *, apply):
        calls.append((root, apply))
        return {"state": "ready", "applied": True, "candidates": []}

    monkeypatch.setattr(module, "cleanup_partials_locked", clean)
    monkeypatch.setattr(
        module.shutil,
        "disk_usage",
        lambda _: SimpleNamespace(total=100 * 1024**3, used=95 * 1024**3, free=5 * 1024**3),
    )
    with pytest.raises(module.BackupCapacityError):
        module.backup(tmp_path, "fixture_no_process")
    assert calls == [(tmp_path, True)]
    status = json.loads((tmp_path / "backup-status.json").read_text())
    assert status["state"] == "blocked_capacity" and status["partial_cleanup"]["applied"]


def test_cleanup_inspection_failure_does_not_prevent_new_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "BACKUP_ROOTS", (tmp_path,))
    monkeypatch.setitem(
        sys.modules, "fcntl", SimpleNamespace(LOCK_EX=1, LOCK_NB=2, flock=lambda *args: None)
    )

    def denied(*args, **kwargs):
        raise PermissionError("fixture no process inspection")

    def run(*args, partial_cleanup):
        assert partial_cleanup == {
            "state": "blocked",
            "error_type": "PermissionError",
            "applied": False,
        }
        return "backup can proceed"

    monkeypatch.setattr(module, "cleanup_partials_locked", denied)
    monkeypatch.setattr(module, "_backup_locked", run)
    assert module.backup(tmp_path, "fixture_no_process") == "backup can proceed"


def test_absent_partials_do_not_require_local_recovery_file(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "verified_recovery", lambda *a: pytest.fail("no candidates"))
    assert module.partial_cleanup_plan(tmp_path, NOW) == {"state": "ready", "candidates": []}


def test_offsite_only_receipt_does_not_attempt_missing_local_dump(backup_tree, monkeypatch):
    failed = partial(backup_tree)
    monkeypatch.setattr(module, "verified_recovery", lambda *a: {
        "state": "verified", "source_available_on_server": False,
        "source": "market-20261009T153042Z.dump"})
    result = module.cleanup_partials_locked(backup_tree, apply=True, now=NOW)
    assert result["state"] == "blocked_recovery" and failed.exists()
    assert result["reason"] == "offsite_only_recovery"


@pytest.mark.parametrize("phase", ["inventory", "recheck", "unlink"])
def test_partial_disappearance_is_idempotent_not_false_removed(backup_tree, monkeypatch, phase):
    failed = partial(backup_tree)
    original, calls = module.file_identity, []

    def identity(path):
        if path == failed:
            calls.append(path)
            threshold = {"inventory": 1, "recheck": 2, "unlink": 3}[phase]
            if len(calls) == threshold:
                failed.unlink()
        return original(path)

    monkeypatch.setattr(module, "file_identity", identity)
    result = module.cleanup_partials_locked(backup_tree, apply=True, now=NOW)
    assert not failed.exists()
    assert result.get("allocated_bytes_removed", 0) == 0
    if phase != "inventory":
        assert result["already_absent"] == [failed.name]
