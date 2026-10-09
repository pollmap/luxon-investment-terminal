"""fixture_non_production: scheduled monitor observes static delivery and retention."""

import datetime as dt
import json
from types import SimpleNamespace

import pytest

from ops.market import monitor


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    (tmp_path / "backups").mkdir()
    (tmp_path / "shared").mkdir()
    monkeypatch.setattr(monitor, "ROOT", tmp_path)
    monkeypatch.setattr(
        monitor.shutil, "disk_usage", lambda _: SimpleNamespace(total=1000, used=600, free=400)
    )
    monkeypatch.setattr(monitor.retain_releases, "static_web_reference", lambda: None)
    monkeypatch.setattr(
        monitor, "retention_status", lambda _: {"state": "protected", "automatic_removal": False}
    )
    containers = [{"service": s, "state": "running"} for s in monitor.retain_releases.SERVICES]
    now = dt.datetime(2026, 10, 9, 14, tzinfo=dt.UTC)
    (tmp_path / "backups/market-20261009T100000Z.dump").write_bytes(b"fixture")
    return tmp_path, containers, now


def test_stopped_node_only_valid_with_verified_static(fixture, monkeypatch):
    _, containers, now = fixture
    web = next(p for p in containers if p["service"] == "market-web")
    web["state"] = "exited"
    assert (
        "market-web_stopped" in monitor.collect_report({}, containers=containers, now=now)["issues"]
    )
    monkeypatch.setattr(
        monitor.retain_releases,
        "static_web_reference",
        lambda: {"release": "v98", "public_root": "/verified"},
    )
    report = monitor.collect_report({}, containers=containers, now=now)
    assert "market-web_stopped" not in report["issues"]
    web["state"] = "dead"
    assert (
        "market-web_stopped" in monitor.collect_report({}, containers=containers, now=now)["issues"]
    )


def test_invalid_static_marker_is_not_a_healthy_fallback(fixture, monkeypatch):
    _, containers, now = fixture
    containers[0]["state"] = "exited"

    def invalid():
        raise ValueError("bad manifest")

    monkeypatch.setattr(monitor.retain_releases, "static_web_reference", invalid)
    report = monitor.collect_report({}, containers=containers, now=now)
    assert "static_web_activation_invalid" in report["issues"]
    assert "market-api_stopped" in report["issues"]


@pytest.mark.parametrize(
    "fraction,expected",
    [(0.799, None), (0.8, "disk_warning"), (0.899, "disk_warning"), (0.9, "disk_critical")],
)
def test_disk_thresholds_work_even_when_api_unavailable(fixture, monkeypatch, fraction, expected):
    _, containers, now = fixture
    monkeypatch.setattr(
        monitor.shutil,
        "disk_usage",
        lambda _: SimpleNamespace(total=1000, used=fraction * 1000, free=(1 - fraction) * 1000),
    )
    report = monitor.collect_report({}, ["api_TimeoutError"], containers=containers, now=now)
    assert "api_TimeoutError" in report["issues"]
    assert [p for p in report["issues"] if p.startswith("disk_")] == (
        [expected] if expected else []
    )


def test_metadata_only_retention_status_reports_blocked_not_deletion(monkeypatch):
    def inventory(**kwargs):
        assert kwargs["measure_bytes"] is False
        return {
            "candidates": [{"name": "old-v1"}],
            "protected_reasons": {"current": ["current"]},
            "blocked": [],
            "inspection": "metadata_only",
        }

    monkeypatch.setattr(monitor.retain_releases, "inventory", inventory)
    report = monitor.retention_status([])
    assert report["state"] == "blocked_offsite_verification"
    assert report["automatic_removal"] is False


def test_repeated_issues_quiet_and_existing_partial_preserved(fixture, capsys):
    root, containers, now = fixture
    pending = root / "shared/monitor.partial"
    pending.write_text("unfinished prior process")
    report = monitor.collect_report({}, containers=containers, now=now)
    monitor.save_report(report)
    assert capsys.readouterr().out
    monitor.save_report(report)
    assert capsys.readouterr().out == ""
    assert pending.read_text() == "unfinished prior process"
    assert (
        json.loads((root / "shared/monitor.json").read_text())["backup"]["offsite_recovery"][
            "state"
        ]
        == "blocked"
    )


def test_backup_capacity_block_is_actionable(fixture):
    root, containers, now = fixture
    (root / "backups/backup-status.json").write_text(json.dumps({"state": "blocked_capacity"}))
    assert (
        "backup_capacity_blocked"
        in monitor.collect_report({}, containers=containers, now=now)["issues"]
    )


def test_recent_backup_does_not_mask_new_job_failure(fixture):
    root, containers, now = fixture
    (root / "backups/backup-status.json").write_text(json.dumps({"state": "failed"}))
    report = monitor.collect_report({}, containers=containers, now=now)
    assert "backup_failed" in report["issues"]
    assert "backup_overdue" not in report["issues"]


@pytest.mark.parametrize(
    "state,issue",
    [
        ("verified", None),
        ("recheck_due", "backup_offsite_recheck_due"),
        ("verification_expired", "backup_offsite_reverification_required"),
    ],
)
def test_offsite_expiry_is_recheck_not_restore_failure(fixture, monkeypatch, state, issue):
    _, containers, now = fixture
    monkeypatch.setattr(
        monitor.backup,
        "backup_health",
        lambda *a: {
            "state": "ready",
            "offsite_recovery": {"state": state},
            "last_job_state": "completed",
        },
    )
    result = monitor.collect_report({}, containers=containers, now=now)
    assert "backup_failed" not in result["issues"]
    assert "backup_offsite_restore_unverified" not in result["issues"]
    assert [p for p in result["issues"] if p.startswith("backup_offsite")] == (
        [issue] if issue else []
    )
