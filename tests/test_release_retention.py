"""fixture_non_production: retain references and require content/restore proof."""

import hashlib
import io
import json
import os
import tarfile
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from ops.market import retain_releases as retention
from scripts.offsite_release_archive import payload_inventory, restored_tree_hash


def document(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def link(path, target, monkeypatch):
    try:
        path.symlink_to(target, target_is_directory=True)
    except OSError:
        # Windows without SeCreateSymbolicLinkPrivilege: model only the pointer
        # resolution, while all target payloads and removal checks remain real.
        original = Path.resolve

        def resolve(self, *args, **kwargs):
            return original(target if self == path else self, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", resolve)


@pytest.fixture
def releases(tmp_path, monkeypatch):
    root = tmp_path / "service"
    public = tmp_path / "public"
    (root / "backups").mkdir(parents=True)
    (root / "shared/data").mkdir(parents=True)
    for n in range(1, 9):
        name = f"20261009-test-v{n}"
        path = root / "releases" / name
        document(path / "deployment.json", {"release": name, "state": "active"})
        os.utime(path / "deployment.json", (100 + n, 100 + n))
        (path / "file.txt").write_text("same payload", encoding="utf-8")
    link(root / "current", root / "releases/20261009-test-v8", monkeypatch)
    monkeypatch.setattr(retention, "ROOT", root)
    monkeypatch.setattr(retention, "PUBLIC_ROOT", public)
    containers = [
        {
            "service": s,
            "image": "korea-stocks-api:20261009-test-v8",
            "state": "running",
            "mounts": [],
        }
        for s in retention.SERVICES
    ]
    return root, public, containers


def test_current_recent_two_openalgo_and_rollback_floor_are_protected(releases):
    root, _, containers = releases
    containers[-1]["image"] = "korea-stocks-openalgo:20261009-test-v2"
    containers[-1]["mounts"] = [{"Source": str(root / "releases/20261009-test-v3/services")}]
    document(
        root / "shared/data/storage-format.json",
        {"version": 1, "rollback_floor": "20261009-test-v1"},
    )
    state = retention.inventory(containers=containers)
    assert {p["name"] for p in state["candidates"]} == {"20261009-test-v4", "20261009-test-v5"}
    assert "container_mount" in state["protected_reasons"]["20261009-test-v3"]
    assert "storage_rollback_floor" in state["protected_reasons"]["20261009-test-v1"]
    assert state["keep"] == [f"20261009-test-v{i}" for i in (1, 2, 3, 6, 7, 8)]


@pytest.mark.parametrize("measure_bytes", [True, False])
def test_financial_and_dart_readers_survive_later_floor_advance(
    tmp_path, monkeypatch, measure_bytes
):
    root = tmp_path / "fixture_non_production"
    names = {n: f"20261010-fixture-v{n}" for n in range(108, 115)}
    for number, name in names.items():
        path = root / "releases" / name
        document(path / "deployment.json", {"release": name, "state": "active"})
        os.utime(path / "deployment.json", (number, number))
    link(root / "current", root / "releases" / names[114], monkeypatch)
    monkeypatch.setattr(retention, "ROOT", root)
    monkeypatch.setattr(retention, "PUBLIC_ROOT", tmp_path / "public")
    containers = [
        {"service": service, "image": "korea-stocks-api:" + names[114], "mounts": []}
        for service in retention.SERVICES
    ]
    document(
        root / "shared/data/storage-format.json",
        {
            "version": 1,
            "rollback_floor": names[112],
            "previous_rollback_floor": names[109],
            "financial_reader_release": names[110],
            "financial_rollback_release": names[109],
            "raw_dart": {"activated_release": names[110], "rollback_release": names[109]},
        },
    )
    state = retention.inventory(measure_bytes=measure_bytes, containers=containers)
    assert {row["name"] for row in state["candidates"]} == {names[108], names[111]}
    assert set(state["keep"]) == {names[n] for n in (109, 110, 112, 113, 114)}
    assert "storage_financial_reader_release" in state["protected_reasons"][names[110]]
    assert "storage_financial_rollback_release" in state["protected_reasons"][names[109]]
    assert "storage_raw_dart_activated_release" in state["protected_reasons"][names[110]]
    assert "storage_raw_dart_rollback_release" in state["protected_reasons"][names[109]]
    assert len(list((root / "releases").iterdir())) == 7


@pytest.mark.parametrize(
    "field",
    ["rollback_floor", "financial_reader_release", "financial_rollback_release",
     "raw_dart.activated_release", "raw_dart.rollback_release", "previous_rollback_floor"],
)
@pytest.mark.parametrize("value", ["../outside", "20261009-missing", "", 12])
def test_invalid_storage_release_reference_stops_inventory(releases, field, value):
    root, _, containers = releases
    contract = {"version": 1, "rollback_floor": "20261009-test-v1"}
    if field.startswith("raw_dart."):
        contract["raw_dart"] = {field.split(".")[1]: value}
    else:
        contract[field] = value
    document(root / "shared/data/storage-format.json", contract)
    with pytest.raises(ValueError, match="Storage release reference"):
        retention.inventory(containers=containers)
    assert len(list((root / "releases").iterdir())) == 8


def test_historical_floor_is_preserved_with_explicit_reason(releases):
    root, _, containers = releases
    document(root / "shared/data/storage-format.json", {
        "version": 1, "rollback_floor": "20261009-test-v7",
        "previous_rollback_floor": "20261009-test-v1",
    })
    state = retention.inventory(containers=containers)
    assert state["protected_reasons"]["20261009-test-v1"] == [
        "storage_previous_rollback_floor_historical"
    ]


def test_null_historical_floor_is_allowed_but_null_active_reader_is_not(releases):
    root, _, containers = releases
    path = root / "shared/data/storage-format.json"
    contract = {"version": 1, "rollback_floor": "20261009-test-v7",
                "previous_rollback_floor": None}
    document(path, contract)
    assert len(retention.inventory(containers=containers)["candidates"]) == 5
    document(path, {**contract, "financial_reader_release": None})
    with pytest.raises(ValueError, match="Storage release reference"):
        retention.inventory(containers=containers)


def test_linked_storage_reader_is_rejected(releases, monkeypatch):
    root, _, containers = releases
    reader = root / "releases/20261009-linked-reader"
    link(reader, root / "releases/20261009-test-v1", monkeypatch)
    document(root / "shared/data/storage-format.json", {
        "version": 1, "rollback_floor": "20261009-test-v7",
        "financial_reader_release": reader.name,
    })
    with pytest.raises(ValueError, match="Storage release reference directory"):
        retention.inventory(containers=containers)


@pytest.mark.parametrize("raw_dart", [None, [], "invalid"])
def test_malformed_nested_reader_contract_fails_closed(releases, raw_dart):
    root, _, containers = releases
    document(root / "shared/data/storage-format.json", {
        "version": 1, "rollback_floor": "20261009-test-v7", "raw_dart": raw_dart,
    })
    with pytest.raises(ValueError, match="Invalid storage DART retention contract"):
        retention.inventory(containers=containers)


def test_failed_new_release_does_not_replace_good_rollback(releases):
    root, _, containers = releases
    path = root / "releases/20261009-test-v9/deployment.json"
    document(path, {"release": "20261009-test-v9", "state": "failed"})
    state = retention.inventory(containers=containers)
    assert all(f"20261009-test-v{i}" in state["keep"] for i in (6, 7, 8, 9))
    assert state["blocked"] == [
        {"name": "20261009-test-v9", "reason": "unconfirmed_or_pending_release"}
    ]


def test_partials_protected_but_hidden_source_and_next_build_are_normal(releases):
    root, _, containers = releases
    first = root / "releases/20261009-test-v1"
    (first / "writing.partial").write_text("pending")
    second = root / "releases/20261009-test-v2"
    (second / ".env.example").write_text("NAME=example")
    (second / ".next").mkdir()
    (second / ".next/app.js").write_text("compiled")
    state = retention.inventory(containers=containers)
    assert first.name in state["keep"]
    assert second.name in {p["name"] for p in state["candidates"]}


def test_periodic_inventory_never_reads_payloads(releases, monkeypatch):
    _, _, containers = releases
    monkeypatch.setattr(retention, "fingerprint_files", lambda *a: pytest.fail("payload scan"))
    monkeypatch.setattr(retention, "release_files", lambda *a: pytest.fail("tree scan"))
    state = retention.inventory(measure_bytes=False, containers=containers)
    assert state["inspection"] == "metadata_only" and state["candidate_bytes"] is None


def activate_static(root, public, monkeypatch, release="20261009-test-v3"):
    current = (root / "current").resolve()
    output = public / "releases" / f"{release}-published"
    document(output / "build-manifest.json", {"profile": "static", "release": release})
    document(current / "static-web/build-manifest.json", {"profile": "static", "release": release})
    checksum = retention.digest(output / "build-manifest.json")
    document(
        current / "static-web-deployment.json",
        {
            "state": "active",
            "release": release,
            "manifest_sha256": checksum,
            "public_root": str(output),
        },
    )
    link(public / "current", output, monkeypatch)
    return output


def test_static_pointer_and_hash_checked_and_frontend_version_protected(releases, monkeypatch):
    root, public, containers = releases
    output = activate_static(root, public, monkeypatch)
    assert retention.static_web_reference()["release"] == "20261009-test-v3"
    assert "20261009-test-v3" in retention.inventory(containers=containers)["keep"]
    (output / "build-manifest.json").write_text("{}")
    with pytest.raises(ValueError, match="does not match"):
        retention.static_web_reference()


def evidence(state):
    saved = {**state, "archive_sha256": "a" * 64, "offsite_stream": True}
    first = state["candidates"][0]
    report = {
        **saved,
        "verification_version": 2,
        "local_archive": "C:/verified/archive.tar.gz",
        "restore_verified": True,
        "all_release_hashes_verified": True,
        "compressed_bytes": 50,
        "verified_files": sum(p["files"] for p in state["candidates"]),
        "restored_release": first["name"],
        "restored_bytes": first["bytes"],
        "restored_files": first["files"],
        "restored_tree_sha256": first["content_sha256"],
    }
    return saved, report


def test_new_reader_reference_invalidates_prior_removal_evidence(releases):
    root, _, containers = releases
    saved, report = evidence(retention.inventory(containers=containers))
    document(root / "shared/data/storage-format.json", {
        "version": 1, "rollback_floor": "20261009-test-v7",
        "financial_reader_release": "20261009-test-v1",
        "financial_rollback_release": "20261009-test-v2",
    })
    fresh = retention.inventory(containers=containers)
    with pytest.raises(ValueError, match="Release inventory changed"):
        retention.apply_verified(saved, report, "a" * 64, fresh)
    assert len(list((root / "releases").iterdir())) == 8


@pytest.mark.parametrize(
    "changed",
    [
        "restore_verified",
        "all_release_hashes_verified",
        "archive_sha256",
        "restored_tree_sha256",
        "candidates",
        "verified_files",
    ],
)
def test_missing_or_mismatched_offsite_evidence_never_deletes(releases, changed):
    root, _, containers = releases
    state = retention.inventory(containers=containers)
    saved, report = evidence(state)
    report[changed] = None
    with pytest.raises(ValueError, match="evidence"):
        retention.apply_verified(saved, report, "a" * 64, state)
    assert len(list((root / "releases").iterdir())) == 8


def test_same_length_source_change_stops_all_removal(releases):
    root, _, containers = releases
    state = retention.inventory(containers=containers)
    saved, report = evidence(state)
    (root / "releases/20261009-test-v5/file.txt").write_text("new! payload")
    with pytest.raises(ValueError, match="changed"):
        retention.apply_verified(saved, report, "a" * 64, state)
    assert len(list((root / "releases").iterdir())) == 8


def test_proof_bound_apply_only_removes_exact_candidates(releases):
    root, _, containers = releases
    state = retention.inventory(containers=containers)
    saved, report = evidence(state)
    result = retention.apply_verified(saved, report, "a" * 64, state)
    assert result["removed"] == 5
    assert {p.name for p in (root / "releases").iterdir()} == set(state["keep"])
    assert (root / "backups" / ("inactive-releases-verified-" + "a" * 64 + ".json")).is_file()


def test_existing_pending_archive_not_overwritten(releases, monkeypatch):
    root, _, containers = releases
    monkeypatch.setattr(retention, "inspect_containers", lambda: containers)
    archive = root / "backups/inactive-releases-20261009.tar.gz"
    archive.with_suffix(".partial").write_bytes(b"preserve")
    with pytest.raises(ValueError, match="pending"):
        retention.archive_or_apply(SimpleNamespace(archive=archive, mode="archive", stream=False))
    assert archive.with_suffix(".partial").read_bytes() == b"preserve"


def test_archive_payload_hashes_include_deduplicated_hardlinks(releases, tmp_path):
    _, _, containers = releases
    state = retention.inventory(containers=containers)
    archive = tmp_path / "fixture.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        retention.add_releases(tar, state)
    assert payload_inventory(archive, state["candidates"]) == sum(
        p["files"] for p in state["candidates"]
    )
    assert (
        restored_tree_hash(retention.ROOT / "releases" / state["candidates"][0]["name"])
        == state["candidates"][0]["content_sha256"]
    )
    damaged = deepcopy(state["candidates"])
    damaged[0]["content_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="content differs"):
        payload_inventory(archive, damaged)


def test_unsafe_archive_member_rejected(tmp_path):
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        member = tarfile.TarInfo("safe-release/../escape")
        member.size = 1
        tar.addfile(member, io.BytesIO(b"x"))
    with pytest.raises(ValueError, match="Unexpected"):
        payload_inventory(archive, [{"name": "safe-release", "bytes": 1}])


def test_component_order_matches_linux_with_nested_prefix_and_case(tmp_path):
    expected = ["B/x.txt", "Upper.txt", "a/x.txt", "a.b.txt", "b-other.txt", "lower.txt"]
    for name in reversed(expected):
        file = tmp_path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(name.encode())
    actual = [p.relative_to(tmp_path).as_posix() for p in retention.release_files(tmp_path)]
    assert actual == expected
    assert actual != sorted(expected)
    assert actual != sorted(expected, key=str.casefold)


def test_nested_case_archive_matches_original_linux_signature(tmp_path, monkeypatch):
    root = tmp_path / "service"
    name = "20261009-prefix-case"
    release = root / "releases" / name
    expected = ["B/x.txt", "Upper.txt", "a/x.txt", "a.b.txt", "b-other.txt", "lower.txt"]
    signature = hashlib.sha256()
    for path in expected:
        destination = release / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"identical")
        row = [path, 9, hashlib.sha256(b"identical").hexdigest()]
        signature.update(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
        )
    candidate = {"name": name, "bytes": 54, "files": 6, "content_sha256": signature.hexdigest()}
    measured = retention.fingerprint_files(release, retention.release_files(release))
    assert measured == {k: candidate[k] for k in ("bytes", "files", "content_sha256")}
    monkeypatch.setattr(retention, "ROOT", root)
    archive = tmp_path / "original-linux-order.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        retention.add_releases(tar, {"candidates": [candidate]})
    assert payload_inventory(archive, [candidate]) == 6
    assert restored_tree_hash(release) == candidate["content_sha256"]
