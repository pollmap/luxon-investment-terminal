"""Deployment failure tests use fake Docker operations and isolated files only."""

import hashlib
import importlib.util
import io
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "market_release", Path(__file__).resolve().parents[1] / "ops/market/activate_release.py"
)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def archive(path, extra=None):
    files = {
        "compose.market.yml": b"name: hannun-public-market",
        "services/market_dashboard/app.py": b"# fixture",
        "apps/web/package.json": b"{}",
        **(extra or {}),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as tar:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    "name",
    [
        "../outside.txt",
        "/absolute.txt",
        "storage/runtime.env",
        "apps/web/.env.local",
        "apps/web/.next/server.js",
    ],
)
def test_source_archive_rejects_escape_and_private_runtime(tmp_path, name):
    path = tmp_path / "source.tgz"
    digest = archive(path, {name: b"fixture_non_production"})
    with pytest.raises(ValueError):
        release.unpack(path, tmp_path / "source", digest)
    assert not (tmp_path / "source").exists()


def test_source_hash_and_identifier_are_verified(tmp_path):
    path = tmp_path / "source.tgz"
    digest = archive(path)
    with pytest.raises(ValueError, match="hash"):
        release.unpack(path, tmp_path / "source", "0" * 64)
    with pytest.raises(ValueError):
        release.validate_release("release; rm -rf /")
    release.unpack(path, tmp_path / "source", digest)
    assert (tmp_path / "source/compose.market.yml").is_file()


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "ROOT", tmp_path)
    # Avoid changing the test process's real umask.
    monkeypatch.setattr(release.os, "umask", lambda _: None)
    services = {
        key: {"id": "old-" + key, "image": "sha256:" + key, "state": "running"}
        for key in (*release.APPS, "market-db", "market-redis")
    }
    monkeypatch.setattr(release, "preflight", lambda: services)
    monkeypatch.setattr(release, "inspect_services", lambda: services)
    monkeypatch.setattr(release.shutil, "disk_usage", lambda _: SimpleNamespace(free=10 * 1024**3))
    calls = []
    monkeypatch.setattr(release, "run", lambda args, **kwargs: calls.append(args) or b"")

    def backup(args, **kwargs):
        kwargs["stdout"].write(b"fixture_non_production_dump")
        calls.append(args)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(release.subprocess, "run", backup)
    name = "20260926-fixture"
    digest = archive(tmp_path / "incoming" / (name + ".tgz"))
    return tmp_path, name, digest, calls


def test_activation_changes_only_apps_after_verified_backup(deployment, monkeypatch):
    root, name, digest, calls = deployment
    monkeypatch.setattr(release, "wait_ready", lambda **kwargs: None)
    report = release.activate(name, digest)
    assert report["status"] == "activated_local_checks_passed"
    assert report["backup"]["bytes"] > 0
    restore = next(i for i, cmd in enumerate(calls) if "pg_restore" in cmd)
    build = next(i for i, cmd in enumerate(calls) if "build" in cmd)
    assert restore < build
    up = next(cmd for cmd in calls if "up" in cmd)
    assert up[-3:] == list(release.APPS) and "--no-deps" in up
    assert all(not any(word in cmd for word in ("down", "prune", "rm")) for cmd in calls)
    saved = json.loads((root / "releases" / name / "rollback.json").read_text())
    assert set(saved["services"]) == set(release.APPS)


def test_failed_readiness_restores_old_images_and_reports_rollback(deployment, monkeypatch):
    root, name, digest, calls = deployment
    checks = []

    def ready(new_features=True):
        checks.append(new_features)
        if new_features:
            raise RuntimeError("fixture readiness failure")

    monkeypatch.setattr(release, "wait_ready", ready)
    with pytest.raises(RuntimeError, match="previous application images restored"):
        release.activate(name, digest)
    report = json.loads((root / "releases" / name / "deployment.json").read_text())
    assert report["status"] == "rolled_back" and checks == [True, False]
    ups = [cmd for cmd in calls if "up" in cmd]
    assert len(ups) == 2 and any("rollback.json" in arg for arg in ups[1])


def test_failed_build_does_not_replace_running_containers(deployment, monkeypatch):
    _, name, digest, calls = deployment

    def run(args, **kwargs):
        calls.append(args)
        if "build" in args:
            raise RuntimeError("fixture build failure")
        return b""

    monkeypatch.setattr(release, "run", run)
    with pytest.raises(RuntimeError, match="build failure"):
        release.activate(name, digest)
    assert not any("up" in cmd for cmd in calls)
