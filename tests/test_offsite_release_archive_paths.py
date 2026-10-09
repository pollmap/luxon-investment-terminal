"""fixture_non_production: same-day batches never reuse prior archive paths."""

import hashlib
import io
import json
import sys
import tarfile

import pytest

from scripts import offsite_release_archive as archive


def test_default_date_path_unchanged_and_batch_is_separate(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "ROOT", tmp_path)
    original, remote = archive.archive_paths("20261009")
    second, second_remote = archive.archive_paths("20261009", 2)
    assert original == tmp_path / "work/operations-20261009"
    assert remote.endswith("/inactive-releases-20261009.tar.gz")
    assert second == tmp_path / "work/operations-20261009-2"
    assert second_remote.endswith("/inactive-releases-20261009-2.tar.gz")
    assert original != second and remote != second_remote


@pytest.mark.parametrize("batch", [0, -1, "../2", True, 1.5])
def test_invalid_batch_rejected(batch):
    with pytest.raises(ValueError, match="batch"):
        archive.archive_paths("20261009", batch)


@pytest.mark.parametrize("date", ["2026-10-09", "../20261009", "20261009-2"])
def test_date_contract_unchanged(date):
    with pytest.raises(ValueError, match="YYYYMMDD"):
        archive.archive_paths(date, 2)


@pytest.mark.parametrize("pending", [False, True])
def test_existing_batch_archive_or_partial_never_overwritten(tmp_path, monkeypatch, pending):
    monkeypatch.setattr(archive, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["archive", "--date", "20261009", "--batch", "2"])
    monkeypatch.setattr(archive.subprocess, "Popen", lambda *a, **k: pytest.fail("Unexpected SSH"))
    original, _ = archive.archive_paths("20261009")
    original.mkdir(parents=True)
    previous = original / "inactive-releases-20261009.tar.gz"
    previous.write_bytes(b"preserve first archive")
    second, _ = archive.archive_paths("20261009", 2)
    second.mkdir(parents=True)
    target = second / "inactive-releases-20261009-2.tar.gz"
    collision = target.with_suffix(".partial") if pending else target
    collision.write_bytes(b"preserve existing batch")
    with pytest.raises((ValueError, FileExistsError)):
        archive.main()
    assert previous.read_bytes() == b"preserve first archive"
    assert collision.read_bytes() == b"preserve existing batch"


def test_cli_default_date_still_20261008(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["archive"])
    original, _ = archive.archive_paths("20261008")
    original.mkdir(parents=True)
    target = original / "inactive-releases-20261008.tar.gz"
    target.write_bytes(b"existing default")
    with pytest.raises(ValueError, match="already exists"):
        archive.main()


def test_long_path_inventory_includes_all_descendants(tmp_path):
    root = tmp_path / "restore"
    relative = "/".join(["long-component-" + "x" * 45] * 5 + ["payload.bin"])
    file = archive.filesystem_path(root / relative)
    file.parent.mkdir(parents=True)
    file.write_bytes(b"long path contents")
    signature = hashlib.sha256(
        json.dumps(
            [relative, 18, hashlib.sha256(b"long path contents").hexdigest()],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        + b"\n"
    ).hexdigest()
    assert len(str(root / relative)) > 260
    assert archive.restored_tree_inventory(root) == {
        "files": 1,
        "bytes": 18,
        "content_sha256": signature,
    }


def resume_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["archive", "--date", "20261009", "--batch", "2", "--resume"])
    out, _ = archive.archive_paths("20261009", 2)
    out.mkdir(parents=True)
    pending = out / "inactive-releases-20261009-2.tar.partial"
    selected = "20261009-test-source"
    directory = out / "release-restore-verified" / selected
    directory.mkdir(parents=True)
    signature = hashlib.sha256()
    with tarfile.open(pending, "w:gz") as tar:
        for name, contents in [("one.txt", b"one"), ("two.txt", b"two")]:
            item = tarfile.TarInfo(selected + "/" + name)
            item.size = len(contents)
            tar.addfile(item, io.BytesIO(contents))
            (directory / name).write_bytes(contents)
            signature.update(
                json.dumps(
                    [name, 3, hashlib.sha256(contents).hexdigest()], separators=(",", ":")
                ).encode()
                + b"\n"
            )
    candidate = {"name": selected, "files": 2, "bytes": 6, "content_sha256": signature.hexdigest()}
    manifest = {
        "inventory_version": 2,
        "archive_sha256": hashlib.sha256(pending.read_bytes()).hexdigest(),
        "candidates": [candidate],
        "candidate_bytes": 6,
    }
    monkeypatch.setattr(
        archive.subprocess, "check_output", lambda *a, **k: json.dumps(manifest).encode()
    )
    monkeypatch.setattr(
        archive.subprocess, "Popen", lambda *a, **k: pytest.fail("No new archive download")
    )
    return out, pending, directory, manifest


def test_resume_rechecks_exact_existing_restore_without_rewriting(tmp_path, monkeypatch):
    out, pending, directory, _ = resume_fixture(tmp_path, monkeypatch)
    stamps = {p.name: p.stat().st_mtime_ns for p in directory.iterdir()}
    archive.main()
    assert not pending.exists()
    assert (out / "inactive-releases-20261009-2.tar.gz").is_file()
    assert stamps == {p.name: p.stat().st_mtime_ns for p in directory.iterdir()}
    proof = json.loads((out / "release-archive-verified.json").read_text())
    assert proof["restored_files"] == 2 and proof["restore_verified"] is True


@pytest.mark.parametrize("damage", ["missing", "changed", "extra", "archive_hash"])
def test_resume_keeps_existing_restore_and_rejects_mismatch(tmp_path, monkeypatch, damage):
    out, pending, directory, manifest = resume_fixture(tmp_path, monkeypatch)
    if damage == "missing":
        (directory / "one.txt").unlink()
    elif damage == "changed":
        (directory / "one.txt").write_bytes(b"bad")
    elif damage == "extra":
        (directory / "unexpected.txt").write_bytes(b"extra")
    else:
        manifest["archive_sha256"] = "0" * 64
    original = {p.name: p.read_bytes() for p in directory.iterdir()}
    with pytest.raises(ValueError, match="mismatch"):
        archive.main()
    assert pending.is_file() and not (out / "inactive-releases-20261009-2.tar.gz").exists()
    assert original == {p.name: p.read_bytes() for p in directory.iterdir()}
