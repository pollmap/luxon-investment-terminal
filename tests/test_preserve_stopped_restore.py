"""Fixture-only preservation checks; no PostgreSQL or production connection."""

import gzip
import hashlib
import json
import os
import shutil
import tarfile

import pytest

from ops.market import preserve_stopped_restore as mod


@pytest.fixture
def copy(tmp_path, monkeypatch):
    source = tmp_path / "fixture_non_production/cluster-data"
    source.mkdir(parents=True)
    (source / "PG_VERSION").write_bytes(b"17\n")
    (source / "base").mkdir()
    (source / "base/1").write_bytes(b"fixture_non_production\x00" * 400)
    monkeypatch.setattr(mod, "TARGET", source)
    monkeypatch.setattr(mod, "DESTINATION", tmp_path / "archives")
    monkeypatch.setattr(mod, "owner", lambda: {"fixture_non_production": True})
    monkeypatch.setattr(mod, "stopped", lambda: None)
    return source


def test_archive_preserves_every_file_and_is_deterministic(copy):
    before = {
        p.relative_to(copy).as_posix(): p.read_bytes() for p in copy.rglob("*") if p.is_file()
    }
    one = mod.preserve("1" * 32, 1024**2)
    two = mod.preserve("2" * 32, 1024**2)
    assert one["verified"] and one["file_bytes_verified"]
    assert one["archive_sha256"] == two["archive_sha256"]
    assert one["source_deleted"] is False and one["database_restore_verified"] is False
    with tarfile.open(mod.DESTINATION / ("1" * 32) / "cluster.tar.gz", "r:gz") as archive:
        restored = {member.name: archive.extractfile(member).read() for member in archive}
    assert restored == before
    assert {
        p.relative_to(copy).as_posix(): p.read_bytes() for p in copy.rglob("*") if p.is_file()
    } == before


def test_source_change_interrupts_and_preserves_partial(copy, monkeypatch):
    original = mod.identity
    seen = 0

    def changed(path):
        nonlocal seen
        result = original(path)
        if path.name == "1":
            seen += 1
            if seen == 3:
                result["mtime_ns"] += 1
        return result

    monkeypatch.setattr(mod, "identity", changed)
    with pytest.raises(mod.Refused, match="source_changed_during_copy"):
        mod.preserve("3" * 32, 1024**2)
    out = mod.DESTINATION / ("3" * 32)
    record = json.loads((out / "checkpoint.json").read_text())
    assert record["verified"] is False and record["source_deleted"] is False
    assert (out / "cluster.tar.gz.partial").exists()
    assert not (out / "manifest.json").exists()
    assert (copy / "base/1").exists()


def test_existing_run_is_never_overwritten(copy):
    mod.preserve("4" * 32, 1024**2)
    archive = mod.DESTINATION / ("4" * 32) / "cluster.tar.gz"
    before = archive.read_bytes()
    with pytest.raises(FileExistsError):
        mod.preserve("4" * 32, 1024**2)
    assert archive.read_bytes() == before


def test_invalid_run_id_cannot_escape_destination(copy):
    with pytest.raises(mod.Refused, match="invalid_explicit_run_id"):
        mod.preserve("../other", 1024**2)
    assert not mod.DESTINATION.exists()


def test_hardlinked_source_is_refused(copy):
    os.link(copy / "base/1", copy / "base/duplicate")
    with pytest.raises(mod.Refused, match="nonregular_or_hardlinked_source"):
        mod.inventory()


def test_inventory_must_not_silently_skip_unreadable_directory(copy, monkeypatch):
    def unreadable(*args, **kwargs):
        kwargs["onerror"](PermissionError("fixture_non_production"))
        yield None

    monkeypatch.setattr(mod.os, "walk", unreadable)
    with pytest.raises(mod.Refused, match="source_inventory_unreadable"):
        mod.inventory()


def test_owner_hash_change_is_refused(tmp_path, monkeypatch):
    proof = tmp_path / "verification.json"
    proof.write_text('{"fixture_non_production":true}')
    monkeypatch.setattr(mod, "OWNER", proof)
    with pytest.raises(mod.Refused, match="owner_proof_changed"):
        # Call original owner; no credential, archive or database access occurs.
        mod.owner()


@pytest.mark.parametrize("estimate", [0, -1, mod.MAX_BYTES + 1])
def test_invalid_size_budget(tmp_path, estimate):
    with pytest.raises(mod.Refused, match="invalid_archive_size_estimate"):
        mod.Budget(tmp_path, estimate)


def test_free_space_and_growth_budgets(tmp_path):
    usage = shutil._ntuple_diskusage(20 * 1024**3, 10 * 1024**3, mod.MIN_FREE)
    with pytest.raises(mod.Refused, match="archive_budget_and_reserve_unavailable"):
        mod.Budget(tmp_path, 1, disk=lambda _: usage)
    frees = [20 * 1024**3, mod.MIN_FREE - 1]
    budget = mod.Budget(
        tmp_path, 1024, disk=lambda _: shutil._ntuple_diskusage(30 * 1024**3, 0, frees.pop(0))
    )
    with pytest.raises(mod.Refused, match="drive_space_budget_exhausted"):
        budget.check()


def test_time_and_own_output_budgets(tmp_path):
    times = [0, mod.MAX_SECONDS + 1]
    budget = mod.Budget(tmp_path, 1, clock=lambda: times.pop(0))
    with pytest.raises(mod.Refused, match="archive_time_budget_exhausted"):
        budget.check()
    budget = mod.Budget(tmp_path, 1)
    with pytest.raises(mod.Refused, match="archive_output_budget_exhausted"):
        budget.check(mod.MAX_BYTES + 1)


def test_corrupt_stream_and_unexpected_file_are_rejected(copy):
    record = mod.preserve("5" * 32, 1024**2)
    archive = mod.DESTINATION / ("5" * 32) / "cluster.tar.gz"
    corrupt = archive.with_name("corrupt.gz")
    value = bytearray(archive.read_bytes())
    value[-5] ^= 1
    corrupt.write_bytes(value)
    with pytest.raises((mod.Refused, gzip.BadGzipFile, EOFError)):
        mod.verify_archive(corrupt, record, mod.Budget(archive.parent, 1))
    with pytest.raises(mod.Refused, match="unexpected_archive_entry"):
        mod.verify_archive(
            archive, {**record, "files": record["files"][:-1]}, mod.Budget(archive.parent, 1)
        )


def test_wrong_per_file_digest_is_rejected(copy):
    record = mod.preserve("6" * 32, 1024**2)
    record["files"][0]["sha256"] = hashlib.sha256(b"different_fixture").hexdigest()
    with pytest.raises(mod.Refused, match="archive_file_bytes_mismatch"):
        mod.verify_archive(
            mod.DESTINATION / ("6" * 32) / "cluster.tar.gz", record, mod.Budget(mod.DESTINATION, 1)
        )
