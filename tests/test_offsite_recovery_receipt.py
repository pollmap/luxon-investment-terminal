"""fixture_non_production: bind PC artifacts to bounded, private restore evidence."""

import datetime as dt
import hashlib
import json

import pytest

from ops.market.backup import backup_health, offsite_recovery_status, verified_recovery
from scripts import publish_offsite_recovery_receipt as publisher
from scripts.publish_offsite_recovery_receipt import build_receipt

NOW = dt.datetime(2026, 10, 9, 15, tzinfo=dt.UTC)


@pytest.fixture
def evidence(tmp_path):
    dump = tmp_path / "market-20261008T153201Z.dump"
    cipher = tmp_path / (dump.name + ".aes256gcm")
    dump.write_bytes(b"fixture database dump")
    cipher.write_bytes(b"fixture encrypted bytes")
    checksum = hashlib.sha256(dump.read_bytes()).hexdigest()
    size = dump.stat().st_size
    decryption = {
        "status": "verified",
        "independent_ciphertext_hash_match": True,
        "encryption": {
            "source_file": dump.name,
            "ciphertext_file": cipher.name,
            "plaintext_sha256": checksum,
            "plaintext_bytes": size,
            "ciphertext_sha256": hashlib.sha256(cipher.read_bytes()).hexdigest(),
            "ciphertext_bytes": cipher.stat().st_size,
        },
        "full_decryption": {
            "authenticated": True,
            "plaintext_sha256": checksum,
            "plaintext_bytes": size,
            "verified_at": "2026-10-08T16:53:52Z",
        },
    }
    restore = {
        "status": "verified",
        "passed": True,
        "pg_restore_succeeded": True,
        "source_content_equality_verified": False,
        "dump_sha256": checksum,
        "dump_bytes": size,
        "started_at": "2026-10-08T16:38:05Z",
        "elapsed_seconds": 1100.75,
        "restore": {"passed": True, "missing_required_tables": [], "invalid_indexes": []},
    }
    decrypt_report = tmp_path / "decryption.json"
    restore_report = tmp_path / "postgres.json"
    decrypt_report.write_text(json.dumps(decryption))
    restore_report.write_text(json.dumps(restore))
    return dump, cipher, decrypt_report, restore_report


def receipt(evidence):
    return build_receipt(*evidence, now=NOW)


def test_actual_file_hashes_bind_to_both_reports_without_rewriting(evidence):
    before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in evidence]
    proof = receipt(evidence)
    assert before == [(p.read_bytes(), p.stat().st_mtime_ns) for p in evidence]
    assert proof["verification"]["plaintext"]["sha256"] == proof["sha256"]
    assert proof["verification"]["postgres_restore"]["source_content_equality_verified"] is False
    assert proof["latest_server_dump_verified"] is False
    assert str(evidence[0].parent) not in json.dumps(proof)


@pytest.mark.parametrize(
    "index,action", [(0, "delete"), (0, "change"), (1, "delete"), (1, "change")]
)
def test_missing_or_changed_artifact_cannot_publish_new_proof(evidence, index, action):
    if action == "delete":
        evidence[index].unlink()
    else:
        evidence[index].write_bytes(b"changed artifact")
    with pytest.raises(ValueError):
        receipt(evidence)


@pytest.mark.parametrize(
    "field,value",
    [
        ("dump_sha256", "0" * 64),
        ("dump_bytes", 1),
        ("passed", False),
        ("pg_restore_succeeded", False),
        ("elapsed_seconds", -1),
        ("started_at", "2027-01-01T00:00:00Z"),
    ],
)
def test_restore_mismatch_or_future_evidence_rejected(evidence, field, value):
    data = json.loads(evidence[3].read_text())
    data[field] = value
    evidence[3].write_text(json.dumps(data))
    with pytest.raises(ValueError):
        receipt(evidence)


@pytest.mark.parametrize(
    "field,value",
    [
        ("authenticated", False),
        ("plaintext_sha256", "0" * 64),
        ("verified_at", "2027-01-01T00:00:00Z"),
    ],
)
def test_decryption_proof_must_match_same_actual_dump(evidence, field, value):
    data = json.loads(evidence[2].read_text())
    data["full_decryption"][field] = value
    evidence[2].write_text(json.dumps(data))
    with pytest.raises(ValueError):
        receipt(evidence)


@pytest.mark.parametrize(
    "days,state",
    [
        (0, "verified"),
        (6, "verified"),
        (7, "recheck_due"),
        (29, "recheck_due"),
        (30, "verification_expired"),
        (90, "verification_expired"),
    ],
)
def test_recheck_expiry_preserves_evidence_and_never_claims_restore_failure(evidence, days, state):
    result = offsite_recovery_status(receipt(evidence), NOW + dt.timedelta(days=days))
    assert result["state"] == state
    assert result["evidence_status"] == "verified"
    assert result["retention"] == "preserve_no_auto_delete"
    assert result["availability"] == "present_at_check"
    assert result["latest_server_dump_verified"] is False
    assert evidence[0].is_file()


@pytest.mark.parametrize(
    "field,value",
    [
        ("checked_at", "2027-01-01T00:00:00Z"),
        ("checked_at", "2026-10-09T15:00:00"),
        ("expires_at", "2030-01-01T00:00:00Z"),
        ("recheck_after", "2026-10-09T14:00:00Z"),
        ("latest_server_dump_verified", True),
        ("bytes", True),
        ("verification", []),
    ],
)
def test_receiver_rejects_invalid_or_unbounded_contract(evidence, field, value):
    proof = receipt(evidence)
    proof[field] = value
    with pytest.raises(ValueError):
        offsite_recovery_status(proof, NOW)


def test_source_hash_link_cannot_be_broken_in_receipt(evidence):
    proof = receipt(evidence)
    proof["verification"]["postgres_restore"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="mismatch"):
        offsite_recovery_status(proof, NOW)


def test_server_does_not_require_duplicate_dump_and_keeps_latest_independent(evidence, tmp_path):
    server = tmp_path / "server"
    server.mkdir()
    proof = receipt(evidence)
    # An unknown field must never be copied into monitor/public status.
    proof["pc_private_path"] = "C:/Users/private-person/backup.dump"
    (server / "database-recovery-verified.json").write_text(json.dumps(proof))
    latest = server / "market-20261009T040000Z.dump"
    latest.write_bytes(b"newer and independently unverified")
    result = backup_health(server, NOW)
    assert result["state"] == "ready" and result["latest"] == latest.name
    assert result["offsite_recovery"]["state"] == "verified"
    assert result["offsite_recovery"]["source"] == evidence[0].name
    assert not (server / evidence[0].name).exists()
    assert result["offsite_recovery"]["source_available_on_server"] is False
    assert result["offsite_recovery"]["latest_server_dump_verified"] is False
    assert "private-person" not in json.dumps(result)


def test_invalid_imported_receipt_is_blocked_without_touching_original(evidence, tmp_path):
    proof = receipt(evidence)
    proof["verification"]["decryption"]["bytes"] += 1
    server = tmp_path / "server"
    server.mkdir()
    (server / "database-recovery-verified.json").write_text(json.dumps(proof))
    assert verified_recovery(server, NOW)["state"] == "blocked"
    assert evidence[0].read_bytes() == b"fixture database dump"


def test_original_changed_after_hash_cannot_refresh_receipt(evidence, monkeypatch):
    original = publisher.checked_report

    def changed(path):
        result = original(path)
        evidence[0].write_bytes(b"changed after source hash")
        return result

    monkeypatch.setattr(publisher, "checked_report", changed)
    with pytest.raises(ValueError, match="changed before receipt"):
        receipt(evidence)


def test_evidence_cannot_precede_source_creation(evidence):
    proof = receipt(evidence)
    proof["verification"]["decryption"]["verified_at"] = "2026-10-01T00:00:00Z"
    with pytest.raises(ValueError):
        offsite_recovery_status(proof, NOW)


def daily_evidence(evidence):
    report = json.loads(evidence[3].read_text())
    report.update(
        restored=report.pop("restore"), sha256=report.pop("dump_sha256"),
        dump=str(evidence[0]), stage="finished", production_contacted=False,
        existing_cluster_modified=False, dump_unchanged=True,
        local_cluster_stopped=True,
    )
    report.pop("dump_bytes")
    evidence[3].write_text(json.dumps(report))
    return report


def test_daily_restore_binds_hash_without_claiming_live_equality(evidence):
    daily_evidence(evidence)
    proof = receipt(evidence)
    assert proof["verification"]["postgres_restore"]["passed"] is True
    assert proof["latest_server_dump_verified"] is False
    assert str(evidence[0].parent) not in json.dumps(proof)


@pytest.mark.parametrize("field,value", [
    ("stage", "checking_database"), ("production_contacted", True),
    ("existing_cluster_modified", True), ("dump_unchanged", False),
    ("local_cluster_stopped", False), ("dump", "another.dump"),
    ("sha256", "0" * 64), ("pg_restore_succeeded", False),
])
def test_daily_restore_cannot_publish_unfinished_or_unbound_proof(evidence, field, value):
    report = daily_evidence(evidence)
    report[field] = value
    evidence[3].write_text(json.dumps(report))
    with pytest.raises(ValueError):
        receipt(evidence)
