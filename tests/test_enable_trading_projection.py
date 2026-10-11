"""Activation guards only; no production changes or provider calls."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def helper(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "fcntl", SimpleNamespace(LOCK_EX=1, LOCK_NB=2, flock=lambda *a: None)
    )
    path = Path(__file__).resolve().parents[1] / "ops/market/enable_trading_projection.py"
    spec = importlib.util.spec_from_file_location("enable_trading", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_compose_changes_only_exact_common_flag(helper):
    original = (
        "environment: &common\n      MARKET_RELEASE: ${RELEASE}\n      KEEP: yes\n<<: *common\n"
    )
    updated = helper.enable_compose(original)
    assert updated.replace(f"      {helper.ENV}: '1'\n", "") == original
    assert helper.enable_compose(updated) == updated
    assert helper.enable_compose(updated.replace("PROJECTIONS: '1'", "PROJECTIONS: '0'")) == updated


@pytest.mark.parametrize(
    "value",
    [
        "environment: &common\n<<: *common\n",
        "      MARKET_COMPACT_TRADING_PROJECTIONS: ${UNREVIEWED}\n",
        "      MARKET_COMPACT_TRADING_PROJECTIONS: '0'\n"
        "      MARKET_COMPACT_TRADING_PROJECTIONS: '1'\n",
    ],
)
def test_ambiguous_environment_refused(helper, value):
    with pytest.raises(RuntimeError):
        helper.enable_compose(value)


def test_additive_reader_requirement_persists_and_is_idempotent(helper, tmp_path):
    path = tmp_path / "contract.json"
    original = {
        "version": 1,
        "required_read_formats": ["source-trace-sha256-v1"],
        "rollback_floor": "reader-v97",
        "activated_at": "fixture timestamp",
    }
    path.write_text(json.dumps(original))
    first = helper.require_reader(path, "reader-v104")
    assert first["required_read_formats"] == [*original["required_read_formats"], helper.FORMAT]
    assert first["rollback_floor"] == "reader-v104"
    assert first["previous_rollback_floor"] == "reader-v97"
    assert first["activated_at"] == original["activated_at"]
    assert helper.require_reader(path, "reader-v105") == first


def test_invalid_contract_or_pending_file_preserves_original(helper, tmp_path):
    path = tmp_path / "contract.json"
    with pytest.raises(RuntimeError):
        helper.require_reader(path, "reader-v104")
    path.write_text('{"version":2}')
    with pytest.raises(RuntimeError):
        helper.require_reader(path, "reader-v104")
    path.with_name(path.name + ".next").write_text("unfinished")
    with pytest.raises(RuntimeError):
        helper.atomic(path, b"changed")
    assert path.read_text() == '{"version":2}'


def test_compose_pins_and_verifies_release_without_exposing_secrets(helper, tmp_path, monkeypatch):
    monkeypatch.setattr(helper, "ROOT", tmp_path)
    folder = tmp_path / "shared/secrets"
    folder.mkdir(parents=True)
    (folder / "release.env").write_text("RELEASE=reader-v104\nSECRET=fixture-only\n")
    monkeypatch.setenv("RELEASE", "wrong-shell-release")
    rendered = {
        "services": {
            name: {
                "image": "korea-stocks-api:reader-v104",
                "environment": {"MARKET_RELEASE": "reader-v104", "SECRET": "fixture-only"},
            }
            for name in ("market-api", "market-worker")
        }
    }
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(rendered))

    monkeypatch.setattr(helper.subprocess, "run", fake_run)
    command, environment = helper.compose_command(tmp_path / "compose.yml", "reader-v104")
    assert environment["RELEASE"] == "reader-v104"
    assert calls[0][1]["env"]["RELEASE"] == "reader-v104"
    assert command[2:4] == ["--project-name", "korea-stocks"]
    rendered["services"]["market-worker"]["image"] = "korea-stocks-api:wrong"
    with pytest.raises(RuntimeError, match="Rendered reader"):
        helper.compose_command(tmp_path / "compose.yml", "reader-v104")
    (folder / "release.env").write_text("RELEASE=wrong-file-release\n")
    with pytest.raises(RuntimeError, match="Persistent release"):
        helper.compose_command(tmp_path / "compose.yml", "reader-v104")
    assert len(calls) == 2
