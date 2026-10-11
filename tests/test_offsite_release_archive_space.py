"""fixture_non_production: bounded local writes, no SSH or provider access."""

import hashlib
import io
import json
import subprocess
import sys
import tarfile
from types import SimpleNamespace

import pytest

from scripts import offsite_release_archive as archive


class BoundedInput(io.BytesIO):
    def read(self, size=-1):
        assert 0 < size <= archive.CHUNK_BYTES
        return super().read(size)


class DownloadChild:
    def __init__(self, data=b"abcdefgh", *, ignores_terminate=False, result=0):
        self.stdout = BoundedInput(data)
        self.ignores_terminate = ignores_terminate
        self.result = result
        self.finished = False
        self.terminated = self.killed = 0

    def poll(self):
        return self.result if self.finished else None

    def terminate(self):
        self.terminated += 1

    def kill(self):
        self.killed += 1

    def wait(self, timeout=None):
        if self.ignores_terminate and self.terminated and not self.killed:
            assert timeout == 5
            raise subprocess.TimeoutExpired("fixture_non_production", timeout)
        self.finished = True
        return self.result


@pytest.fixture(autouse=True)
def no_remote(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected subprocess in local disk fixture")

    monkeypatch.setattr(archive.subprocess, "Popen", forbidden)
    monkeypatch.setattr(archive.subprocess, "check_output", forbidden)
    monkeypatch.setattr(archive, "CHUNK_BYTES", 4)


def free_values(monkeypatch, values):
    readings = iter(values)
    monkeypatch.setattr(
        archive.shutil, "disk_usage", lambda path: SimpleNamespace(free=next(readings))
    )


def test_download_low_space_does_not_start_child_or_create_partial(tmp_path, monkeypatch):
    pending = tmp_path / "archive.partial"
    previous = tmp_path / "previous.tar.gz"
    previous.write_bytes(b"keep")
    free_values(monkeypatch, [13])
    with pytest.raises(archive.InsufficientLocalSpace):
        archive.download_archive(pending, ["fixture_non_production"], minimum=10)
    assert not pending.exists()
    assert previous.read_bytes() == b"keep"


@pytest.mark.parametrize("ignores_terminate", [False, True])
def test_mid_download_stops_only_own_child_and_preserves_partial(
    tmp_path, monkeypatch, ignores_terminate
):
    child = DownloadChild(ignores_terminate=ignores_terminate)
    unrelated = DownloadChild()
    monkeypatch.setattr(archive.subprocess, "Popen", lambda *a, **k: child)
    # Preflight, first write, then reserve would be crossed by the next chunk.
    free_values(monkeypatch, [100, 100, 13])
    pending = tmp_path / "archive.partial"
    with pytest.raises(archive.InsufficientLocalSpace):
        archive.download_archive(pending, ["fixture_non_production"], minimum=10)
    assert pending.read_bytes() == b"abcd"
    assert child.terminated == 1 and child.killed == int(ignores_terminate)
    assert child.finished and child.stdout.closed
    assert unrelated.terminated == unrelated.killed == 0


def test_complete_download_is_bounded_and_does_not_terminate_child(tmp_path, monkeypatch):
    child = DownloadChild()
    monkeypatch.setattr(archive.subprocess, "Popen", lambda *a, **k: child)
    free_values(monkeypatch, [100, 100, 100])
    pending = tmp_path / "archive.partial"
    archive.download_archive(pending, ["fixture_non_production"], minimum=10)
    assert pending.read_bytes() == b"abcdefgh"
    assert child.finished and child.stdout.closed
    assert child.terminated == child.killed == 0


def test_failed_child_preserves_partial_and_propagates_failure(tmp_path, monkeypatch):
    child = DownloadChild(result=7)
    monkeypatch.setattr(archive.subprocess, "Popen", lambda *a, **k: child)
    free_values(monkeypatch, [100, 100, 100])
    pending = tmp_path / "archive.partial"
    with pytest.raises(subprocess.CalledProcessError):
        archive.download_archive(pending, ["fixture_non_production"], minimum=10)
    assert pending.read_bytes() == b"abcdefgh"


def restore_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["archive", "--date", "20261010", "--resume"])
    out, _ = archive.archive_paths("20261010")
    out.mkdir(parents=True)
    pending = out / "inactive-releases-20261010.tar.partial"
    selected = "20261010-fixture_non_production"
    files = {"one.txt": b"abcdefgh", "two.txt": b"ijkl"}
    signature = hashlib.sha256()
    with tarfile.open(pending, "w:gz") as tar:
        for name, data in files.items():
            item = tarfile.TarInfo(selected + "/" + name)
            item.size = len(data)
            tar.addfile(item, io.BytesIO(data))
            signature.update(json.dumps(
                [name, len(data), hashlib.sha256(data).hexdigest()], separators=(",", ":")
            ).encode() + b"\n")
    manifest = {"inventory_version": 2, "archive_sha256": archive.file_digest(pending).hex(),
                "candidate_bytes": 12, "candidates": [{"name": selected, "files": 2,
                    "bytes": 12, "content_sha256": signature.hexdigest()}]}
    monkeypatch.setattr(archive.subprocess, "check_output",
                        lambda *a, **k: json.dumps(manifest).encode())
    previous = out / "previous.tar.gz"
    previous.write_bytes(b"previous verified archive")
    return out, pending, out / "release-restore-verified" / selected, files


def assert_uncompleted(out, pending):
    assert pending.is_file()
    assert not (out / "inactive-releases-20261010.tar.gz").exists()
    assert not (out / "release-archive-verified.json").exists()
    assert (out / "previous.tar.gz").read_bytes() == b"previous verified archive"


def test_restore_preflight_budgets_whole_release(tmp_path, monkeypatch):
    out, pending, directory, _ = restore_fixture(tmp_path, monkeypatch)
    free_values(monkeypatch, [archive.MIN_FREE_BYTES + 11])
    with pytest.raises(archive.InsufficientLocalSpace):
        archive.main()
    assert not directory.parent.exists()
    assert_uncompleted(out, pending)


def test_restore_mid_write_preserves_existing_files_without_completion(tmp_path, monkeypatch):
    out, pending, directory, _ = restore_fixture(tmp_path, monkeypatch)
    minimum = archive.MIN_FREE_BYTES
    free_values(monkeypatch, [minimum + 100, minimum + 100, minimum + 100, minimum + 3])
    with pytest.raises(archive.InsufficientLocalSpace):
        archive.main()
    assert (directory / "one.txt").read_bytes() == b"abcd"
    assert not (directory / "two.txt").exists()
    assert_uncompleted(out, pending)


def test_resume_existing_verified_restore_does_not_budget_duplicate_copy(tmp_path, monkeypatch):
    out, pending, directory, files = restore_fixture(tmp_path, monkeypatch)
    directory.mkdir(parents=True)
    for name, data in files.items():
        (directory / name).write_bytes(data)
    times = {p.name: p.stat().st_mtime_ns for p in directory.iterdir()}
    # No restore allocation: only the final reserve check is needed.
    free_values(monkeypatch, [archive.MIN_FREE_BYTES + 4096])
    archive.main()
    assert not pending.exists()
    assert times == {p.name: p.stat().st_mtime_ns for p in directory.iterdir()}
    assert json.loads((out / "release-archive-verified.json").read_text())["restore_verified"]


def test_verified_archive_not_promoted_when_report_would_spend_reserve(tmp_path, monkeypatch):
    out, pending, directory, files = restore_fixture(tmp_path, monkeypatch)
    directory.mkdir(parents=True)
    for name, data in files.items():
        (directory / name).write_bytes(data)
    free_values(monkeypatch, [archive.MIN_FREE_BYTES])
    with pytest.raises(archive.InsufficientLocalSpace):
        archive.main()
    assert_uncompleted(out, pending)


def test_full_restore_still_checks_file_and_tree_hashes(tmp_path, monkeypatch):
    out, pending, directory, files = restore_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        archive.shutil, "disk_usage",
        lambda path: SimpleNamespace(free=archive.MIN_FREE_BYTES + 4096),
    )
    archive.main()
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == files
    assert not pending.exists()
    proof = json.loads((out / "release-archive-verified.json").read_text())
    assert proof["restored_bytes"] == 12 and proof["all_release_hashes_verified"] is True


@pytest.mark.parametrize("minimum", ["0", "-1"])
def test_cli_cannot_disable_reserve(tmp_path, monkeypatch, minimum):
    monkeypatch.setattr(archive, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["archive", "--min-free-gib", minimum])
    with pytest.raises(SystemExit) as failure:
        archive.main()
    assert failure.value.code == 2
    assert not (tmp_path / "work").exists()
