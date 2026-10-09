"""Mocked activation only: no Docker, collection, production DB or network."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def helper(monkeypatch):
    monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace(
        LOCK_EX=1, LOCK_NB=2, flock=lambda *args: None,
    ))
    path = Path(__file__).resolve().parents[1] / "ops/market/enable_financial_traces.py"
    spec = importlib.util.spec_from_file_location("enable_financial", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def probe(helper, *, enabled=False, formats=None):
    return {
        "capabilities": {
            "inline_financial_facts": True, "read_formats": [helper.FORMAT],
            "reference_write_env": helper.ENV, "reference_writes_default": False,
        },
        "manifest": {"version": 1, "read_formats": formats or [helper.FORMAT]},
        "enabled": enabled,
    }


def compose_text(helper):
    return (
        "services:\n  market-api:\n    environment: &common\n"
        "      MARKET_RELEASE: ${RELEASE}\n      KEEP: yes\n"
        "  market-worker:\n    environment:\n      <<: *common\n"
    )


def test_compose_only_changes_common_financial_flag(helper):
    original = compose_text(helper)
    expected = helper.enable_compose(original)
    assert expected.replace(f"      {helper.ENV}: '1'\n", "") == original
    assert helper.enable_compose(expected) == expected
    assert helper.enable_compose(expected.replace("TRACES: '1'", "TRACES: '0'")) == expected
    for invalid in (
        original + f"      {helper.ENV}: ${{UNREVIEWED}}\n",
        expected + f"      {helper.ENV}: '0'\n",
        original + f"        {helper.ENV}: '0'\n",
        original + f"      {helper.ENV}: '0'\n",  # Worker override, not common.
        original.replace("environment: &common", "environment:"),
    ):
        with pytest.raises(RuntimeError):
            helper.enable_compose(invalid)


def test_requirement_preserves_existing_and_separate_rollback_floor(helper, tmp_path):
    marker = tmp_path / "storage-format.json"
    original = {"version": 1, "required_read_formats": ["existing-format"],
                "rollback_floor": "older-v108", "unrelated": {"preserve": True}}
    marker.write_text(json.dumps(original))
    state = helper.require_reader(marker, original, "active-v110", "reader-v109")
    assert state["required_read_formats"] == ["existing-format", helper.FORMAT]
    assert state["rollback_floor"] == "reader-v109"
    assert state["financial_reader_release"] == "active-v110"
    assert state["previous_rollback_floor"] == "older-v108"
    assert state["unrelated"] == original["unrelated"]
    assert helper.require_reader(marker, state, "active-v111", "reader-v110") == state
    with pytest.raises(RuntimeError, match="changed"):
        helper.require_reader(marker, original, "active-v111", "reader-v110")
    assert json.loads(marker.read_text()) == state


def test_rejects_pending_and_nonexistent_files_without_overwrite(helper, tmp_path):
    marker = tmp_path / "marker"
    with pytest.raises(RuntimeError):
        helper.atomic(marker, b"changed")
    marker.write_bytes(b"original")
    marker.with_name("marker.next").write_bytes(b"unfinished")
    with pytest.raises(RuntimeError, match="Pending"):
        helper.atomic(marker, b"changed")
    assert marker.read_bytes() == b"original"


def test_image_digest_and_all_prior_reader_formats_are_verified(helper, monkeypatch):
    digest = "sha256:" + "a" * 64
    state = probe(helper, formats=[helper.FORMAT, "existing"])
    calls = []
    def output(command):
        calls.append(command)
        if command[1] == "inspect":
            return "|".join(["api-id", "running", "korea-stocks-api:reader-v110",
                             digest, "korea-stocks", "market-api"])
        return json.dumps(state)
    monkeypatch.setattr(helper, "checked_output", output)
    result = helper.reader_state("market-api", "reader-v110", digest, [helper.FORMAT, "existing"])
    assert result == {"id": "api-id", "enabled": False}
    with pytest.raises(RuntimeError, match="image"):
        helper.reader_state("market-api", "reader-v110", "sha256:" + "b" * 64, [helper.FORMAT])
    assert len(calls) == 3  # Reject mismatched image before executing its probe.
    state["manifest"]["read_formats"].remove("existing")
    with pytest.raises(RuntimeError, match="capabilities"):
        helper.reader_state("market-api", "reader-v110", digest, [helper.FORMAT, "existing"])


def test_rollback_probe_uses_exact_offline_image_with_no_data_mounts(helper, tmp_path, monkeypatch):
    monkeypatch.setattr(helper, "ROOT", tmp_path)
    state = probe(helper, formats=[helper.FORMAT, "existing"])
    path = tmp_path / "releases/reader-v109/services/market_dashboard/storage_capabilities.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(state["manifest"]))
    digest = "sha256:" + "c" * 64
    monkeypatch.setattr(helper, "image_id", lambda release: digest)
    calls = []
    def output(command):
        calls.append(command)
        return json.dumps(state)
    monkeypatch.setattr(helper, "checked_output", output)
    assert helper.verify_rollback("reader-v109", [helper.FORMAT, "existing"]) == digest
    command = calls[0]
    assert command[command.index("--network") + 1] == "none"
    assert "--read-only" in command and "--rm" in command
    assert "--env-file" not in command and "-v" not in command and "--mount" not in command
    assert command[-3] == digest
    assert "FactStore(" not in helper.PROBE and "Store(" not in helper.PROBE
    state["enabled"] = True
    with pytest.raises(RuntimeError, match="default-off"):
        helper.verify_rollback("reader-v109", [helper.FORMAT])


def test_old_time_series_only_rollback_is_rejected(helper, tmp_path, monkeypatch):
    monkeypatch.setattr(helper, "ROOT", tmp_path)
    state = probe(helper, formats=["source-trace-sha256-v1"])
    state["capabilities"]["read_formats"] = ["source-trace-sha256-v1"]
    path = tmp_path / "releases/older-v108/services/market_dashboard/storage_capabilities.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(state["manifest"]))
    monkeypatch.setattr(helper, "image_id", lambda release: "sha256:" + "c" * 64)
    monkeypatch.setattr(helper, "checked_output", lambda command: json.dumps(state))
    with pytest.raises(RuntimeError, match="capabilities"):
        helper.verify_rollback("older-v108", ["source-trace-sha256-v1", helper.FORMAT])


def test_rendered_common_flag_and_release_are_checked_without_secret_output(
    helper, tmp_path, monkeypatch,
):
    monkeypatch.setattr(helper, "ROOT", tmp_path)
    path = tmp_path / "shared/secrets/release.env"
    path.parent.mkdir(parents=True)
    path.write_text("RELEASE=active-v110\nSECRET=fixture-only\n")
    monkeypatch.setenv("RELEASE", "wrong-shell")
    rendered = {"services": {name: {
        "image": "korea-stocks-api:active-v110", "environment": {
            "MARKET_RELEASE": "active-v110", helper.ENV: "1", "SECRET": "fixture-only",
        },
    } for name in ("market-api", "market-worker")}}
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(rendered))
    monkeypatch.setattr(helper.subprocess, "run", run)
    helper.compose_command(tmp_path / "compose.yml", "active-v110", enabled=True)
    assert calls[0][1]["env"]["RELEASE"] == "active-v110"
    rendered["services"]["market-worker"]["environment"][helper.ENV] = "0"
    with pytest.raises(RuntimeError, match="flag") as error:
        helper.compose_command(tmp_path / "compose.yml", "active-v110", enabled=True)
    assert "fixture-only" not in str(error.value)


@pytest.fixture
def activation(helper, tmp_path, monkeypatch):
    monkeypatch.setattr(helper, "ROOT", tmp_path)
    current = tmp_path / "current"
    release = tmp_path / "releases/active-v110"
    original_resolve = Path.resolve
    monkeypatch.setattr(Path, "resolve", lambda path, *a, **kw:
                        release if path == current else original_resolve(path, *a, **kw))
    manifest = release / "services/market_dashboard/storage_capabilities.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"version": 1, "read_formats": [helper.FORMAT, "existing"]}))
    config = release / "ops/market/compose.desktop.yml"
    config.parent.mkdir(parents=True)
    original = compose_text(helper).encode()
    config.write_bytes(original)
    marker = tmp_path / "shared/data/storage-format.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"version": 1, "required_read_formats": ["existing"],
                                 "rollback_floor": "older-v108"}))
    state = {"calls": [], "config": config, "marker": marker, "original": original, "fail": False}
    monkeypatch.setattr(helper, "image_id", lambda release: "sha256:" + "a" * 64)
    def reader(service, expected, identity, required):
        state["calls"].append(("reader", service))
        return {"id": service, "enabled": service == "market-worker"
                and f"{helper.ENV}: '1'" in config.read_text()}
    monkeypatch.setattr(helper, "reader_state", reader)
    def rollback(release, required):
        state["calls"].append(("rollback", release))
        assert json.loads(marker.read_text())["required_read_formats"] == ["existing"]
        return "sha256:" + "b" * 64
    monkeypatch.setattr(helper, "verify_rollback", rollback)
    monkeypatch.setattr(helper, "compose_command", lambda *a, **kw: (["docker", "compose"], {}))
    def restart(command, environment):
        state["calls"].append(("restart", "market-worker"))
        assert helper.FORMAT in json.loads(marker.read_text())["required_read_formats"]
        if state["fail"] and state["calls"].count(("restart", "market-worker")) == 1:
            raise RuntimeError("fixture restart failure")
    monkeypatch.setattr(helper, "restart_worker", restart)
    return state


def test_activation_verifies_before_writing_and_restarts_only_worker(helper, activation):
    state = helper.enable("active-v110", "reader-v109")
    assert state["status"] == "enabled" and state["api_unchanged"]
    assert state["existing_rows_rewritten"] == 0
    assert state["canary"] == "awaiting_actual_collection_and_read"
    calls = activation["calls"]
    assert calls[:3] == [("reader", "market-api"), ("reader", "market-worker"),
                        ("rollback", "reader-v109")]
    assert calls.count(("restart", "market-worker")) == 1
    assert Path(state["backup"]).read_bytes() == activation["original"]
    assert state["rollback_floor"] == "reader-v109"


def test_failed_worker_restores_config_but_keeps_durable_reader_floor(helper, activation):
    activation["fail"] = True
    with pytest.raises(RuntimeError, match="fixture restart"):
        helper.enable("active-v110", "reader-v109")
    assert activation["config"].read_bytes() == activation["original"]
    assert activation["calls"].count(("restart", "market-worker")) == 2
    state = json.loads(activation["marker"].read_text())
    assert state["required_read_formats"] == ["existing", helper.FORMAT]
    assert state["rollback_floor"] == "reader-v109"


def test_rejected_rollback_never_changes_contract_or_configuration(helper, activation, monkeypatch):
    original_marker = activation["marker"].read_bytes()
    def missing_reader(*args):
        raise RuntimeError("missing rollback reader")
    monkeypatch.setattr(helper, "verify_rollback", missing_reader)
    with pytest.raises(RuntimeError, match="missing rollback reader"):
        helper.enable("active-v110", "reader-v109")
    assert activation["config"].read_bytes() == activation["original"]
    assert activation["marker"].read_bytes() == original_marker
    assert not any(call[0] == "restart" for call in activation["calls"])


def test_changed_image_restores_configuration_but_never_restarts_unverified_image(
    helper, activation, monkeypatch,
):
    identities = iter(["sha256:" + "a" * 64, "sha256:" + "b" * 64, "sha256:" + "b" * 64])
    monkeypatch.setattr(helper, "image_id", lambda release: next(identities))
    with pytest.raises(RuntimeError, match="changed image prevents"):
        helper.enable("active-v110", "reader-v109")
    assert activation["config"].read_bytes() == activation["original"]
    assert not any(call[0] == "restart" for call in activation["calls"])
    assert helper.FORMAT in json.loads(activation["marker"].read_text())["required_read_formats"]


@pytest.mark.parametrize("expected,rollback", [
    ("same-v109", "same-v109"), ("../invalid", "safe-v109"), ("active-v110", "../invalid"),
])
def test_invalid_or_same_rollback_rejected_before_mutation(helper, expected, rollback):
    with pytest.raises(ValueError):
        helper.enable(expected, rollback)
