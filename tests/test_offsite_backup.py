"""Small non-production fixtures; no SSH, real backup or scheduler mutations."""

import base64
import hashlib
import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from ops.market import backup_crypto as crypto
from ops.market import offsite_backup as backup


@pytest.fixture
def source(tmp_path):
    remote = tmp_path / "vps-fixture"
    remote.mkdir()
    path = remote / "market-20261009T192000Z.dump"
    path.write_bytes(b"PGDMP_fixture_non_production" * 400)
    record = {
        "state": "completed",
        "source": path.name,
        **crypto.digest_file(path),
        "source_mtime_ns": path.stat().st_mtime_ns,
        "archive_list_verified": True,
        "restore_verified": False,
    }
    (remote / (path.name + ".manifest.json")).write_text(json.dumps(record))
    return path, backup.remote_descriptor(remote)


@pytest.fixture
def local(tmp_path, monkeypatch):
    root = tmp_path / "daily"
    monkeypatch.setattr(backup.shutil, "disk_usage", lambda _: SimpleNamespace(free=2**40))
    return root


def copy_source(path):
    def fetch(record, pending, timeout):
        assert record["source"] == path.name and timeout == 5400
        pending.write_bytes(path.read_bytes())

    return fetch


def run(local, source, **kwargs):
    path, record = source
    return backup.run_backup(
        local,
        describe=lambda *_: record,
        fetch=kwargs.pop("fetch", copy_source(path)),
        crypto=crypto,
        min_free=1,
        **kwargs,
    )


def test_remote_selects_only_latest_complete_nonempty_regular_pair(source):
    path, record = source
    for name in (
        "market-20261010T192000Z.partial",
        "predeploy-20261010T192000Z.dump",
        "market-20261011T192000Z.dump",
    ):
        (path.parent / name).write_bytes(b"" if name.endswith("dump") else b"partial")
    assert backup.remote_descriptor(path.parent) == record


@pytest.mark.parametrize(
    "field,value",
    [
        ("bytes", 1),
        ("source_mtime_ns", 1),
        ("state", "running"),
        ("sha256", "invalid"),
        ("archive_list_verified", False),
    ],
)
def test_rejects_inconsistent_producer_completion(source, field, value):
    path, _ = source
    manifest = path.with_name(path.name + ".manifest.json")
    record = json.loads(manifest.read_text())
    record[field] = value
    manifest.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        backup.remote_descriptor(path.parent)


def test_newest_unfinished_manifest_is_not_mislabeled_as_latest(source):
    path, _ = source
    (path.parent / "market-20261010T192000Z.dump").write_bytes(b"PGDMP_pending_manifest")
    with pytest.raises(ValueError, match="completion manifest"):
        backup.remote_descriptor(path.parent)


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows DPAPI")
def test_daily_transfer_atomic_complete_original_preserved_no_restore(source, local):
    path, record = source
    old_recovery = local.parent / "database-recovery-verified.json"
    old_recovery.write_bytes(b"existing_verified_receipt_fixture")
    result = run(local, source)
    assert result["state"] == "transferred_encrypted_verified"
    assert result["postgres_restore_verified"] is False
    folder = local / path.name
    assert (folder / path.name).read_bytes() == path.read_bytes()
    assert not list(local.glob("*.partial"))
    proof = json.loads((folder / "backup-verification.json").read_text())
    assert proof["full_decryption"]["authenticated"] is True
    assert proof["database_restore_not_performed_by_this_tool"] is True
    assert old_recovery.read_bytes() == b"existing_verified_receipt_fixture"
    assert backup.verify_complete(folder, record, crypto) == result
    # Restart: never transfer or encrypt the same completed source again.
    calls = []
    repeat = run(local, source, fetch=lambda *_: calls.append(True))
    assert repeat["state"] == "already_transferred" and not calls
    assert len(list(local.glob("market-*.dump"))) == 1


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows DPAPI")
@pytest.mark.parametrize("artifact", ["original", "ciphertext", "key"])
def test_changed_existing_copy_is_preserved_and_rejected(source, local, artifact):
    path, _ = source
    run(local, source)
    file = (
        local
        / path.name
        / {"original": path.name, "ciphertext": path.name + ".aes256gcm", "key": "key.dpapi"}[
            artifact
        ]
    )
    file.write_bytes(b"modified_fixture")
    previous = (local / "last-transfer.json").read_bytes()
    with pytest.raises(ValueError):
        run(local, source)
    assert file.read_bytes() == b"modified_fixture"
    assert (local / "last-transfer.json").read_bytes() == previous
    assert json.loads((local / "last-attempt.json").read_text())["state"] == "failed"


def test_capacity_counts_existing_partials_and_blocks_before_network(source, local):
    local.mkdir()
    existing = local / "old-transfer.partial"
    existing.write_bytes(b"preserve_incomplete_evidence")
    called = []
    with pytest.raises(backup.CapacityError):
        run(local, source, max_total=1, fetch=lambda *_: called.append(True))
    assert not called and existing.read_bytes() == b"preserve_incomplete_evidence"
    assert json.loads((local / "last-attempt.json").read_text())["state"] == "blocked_capacity"


def test_disk_free_reserve_blocks_download(source, local, monkeypatch):
    monkeypatch.setattr(backup.shutil, "disk_usage", lambda _: SimpleNamespace(free=10))
    with pytest.raises(backup.CapacityError):
        run(local, source, fetch=lambda *_: pytest.fail("Must not transfer"))


@pytest.mark.parametrize("failure", ["network", "hash", "encryption"])
def test_failed_new_attempt_preserves_previous_and_own_partial(source, local, failure):
    local.mkdir()
    previous = local / "last-transfer.json"
    previous.write_bytes(b'{"state":"transferred_encrypted_verified","old":true}')
    before = previous.read_bytes()
    path, record = source

    def fetch(record, pending, timeout):
        pending.write_bytes(path.read_bytes() if failure == "encryption" else b"partial")
        if failure == "network":
            raise subprocess.TimeoutExpired("ssh_secret_must_not_be_logged", timeout)

    def failed_encrypt(*args, **kwargs):
        raise OSError("private_internal_error")

    supplied = SimpleNamespace(digest_file=crypto.digest_file, encrypt=failed_encrypt)
    with pytest.raises((ValueError, OSError, subprocess.TimeoutExpired)):
        backup.run_backup(
            local, describe=lambda *_: record, fetch=fetch, crypto=supplied, min_free=1
        )
    assert previous.read_bytes() == before
    assert not (local / path.name).exists()
    assert len(list(local.glob(".*.partial"))) == 1
    status = (local / "last-attempt.json").read_text()
    assert "secret" not in status and "private_internal_error" not in status
    assert json.loads(status)["previous_copies_preserved"] is True


def test_lock_stops_second_run_without_changing_status(local, source):
    local.mkdir()
    status = local / "last-attempt.json"
    status.write_text('{"state":"existing"}')
    with backup.exclusive_lock(local), pytest.raises(backup.AlreadyRunningError):
        run(local, source, fetch=lambda *_: pytest.fail("Must not transfer"))
    assert status.read_text() == '{"state":"existing"}'
    with backup.exclusive_lock(local):
        pass


@pytest.mark.parametrize("name", ["../file", "predeploy-20261009T192000Z.dump", "file; rm"])
def test_name_validation_before_creating_output(local, source, name):
    with pytest.raises(ValueError):
        backup.run_backup(local, name=name, crypto=crypto)
    assert not local.exists()


def test_remote_program_roundtrip_is_readonly_bounded_and_hash_verified(source, tmp_path):
    path, record = source
    command = backup.remote_command(backup.remote_descriptor, [str(path.parent), None, 2**20])
    code = base64.b64decode(command[-1].split("'")[1]).decode()
    result = subprocess.run([os.sys.executable, "-c", code], capture_output=True, check=True)
    assert json.loads(result.stdout) == record
    command = backup.remote_command(backup.remote_copy, [str(path.parent), record])
    code = base64.b64decode(command[-1].split("'")[1]).decode()
    copied = subprocess.run([os.sys.executable, "-c", code], capture_output=True, check=True)
    assert hashlib.sha256(copied.stdout).hexdigest() == record["sha256"]
    assert len(copied.stdout) == record["bytes"]
    # Same-size modification with restored mtime must still fail at hash verification.
    path.write_bytes(b"X" * record["bytes"])
    os.utime(path, ns=(record["source_mtime_ns"], record["source_mtime_ns"]))
    changed = subprocess.run([os.sys.executable, "-c", code], capture_output=True)
    assert changed.returncode == 1 and changed.stderr.strip() == b"ValueError"


def test_download_uses_timeout_fsync_and_bounded_script(source, tmp_path, monkeypatch):
    _, record = source
    calls = []

    def mocked(command, **kwargs):
        calls.append((command, kwargs["timeout"], kwargs["check"]))
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["creationflags"] == (subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        kwargs["stdout"].write(b"bounded_fixture")

    monkeypatch.setattr(backup.subprocess, "run", mocked)
    target = tmp_path / "owned.partial"
    backup.download(record, target, 50)
    assert target.read_bytes() == b"bounded_fixture"
    assert calls[0][1:] == (50, True)
    assert "StrictHostKeyChecking=yes" in calls[0][0]


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows DPAPI and MAX_PATH")
def test_long_windows_root_roundtrip_and_accounting(source, local):
    nested = local / ("a" * 100) / ("b" * 100)
    result = run(nested, source)
    path, record = source
    rooted = backup.unlinked(nested)
    assert backup.verify_complete(rooted / path.name, record, crypto) == result
    assert backup.directory_bytes(rooted) > record["bytes"] * 2
    assert (
        run(nested, source, fetch=lambda *_: pytest.fail("Duplicate transfer"))["state"]
        == "already_transferred"
    )


def test_running_state_is_written_before_network(source, local):
    _, record = source

    def describe(*_):
        assert json.loads((local / "last-attempt.json").read_text())["state"] == "running"
        raise TimeoutError("private_host_details")

    with pytest.raises(TimeoutError):
        backup.run_backup(local, crypto=crypto, describe=describe)
    status = json.loads((local / "last-attempt.json").read_text())
    assert status["state"] == "failed" and status["error_type"] == "TimeoutError"
    assert not (local / record["source"]).exists()


def test_metadata_ssh_never_opens_console_or_reads_interactive_input(source, monkeypatch):
    _, record = source

    def mocked(command, **kwargs):
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["creationflags"] == (subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        assert kwargs["capture_output"] is True
        return SimpleNamespace(stdout=json.dumps(record).encode())

    monkeypatch.setattr(backup.subprocess, "run", mocked)
    assert backup.describe_remote() == record


@pytest.mark.parametrize("failure", [False, True])
def test_pythonw_missing_standard_streams_preserves_exit_code(monkeypatch, local, failure):
    def execute(*args, **kwargs):
        if failure:
            raise RuntimeError("private_fixture")
        return {"state": "already_transferred"}

    monkeypatch.setattr(backup.sys, "argv", ["offsite_backup.py", "--latest", "--root", str(local)])
    monkeypatch.setattr(backup, "run_backup", execute)
    with monkeypatch.context() as streams:
        streams.setattr(backup.sys, "stdout", None)
        streams.setattr(backup.sys, "stderr", None)
        result = backup.main()
    assert result == int(failure)
