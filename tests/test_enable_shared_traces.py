"""Deployment guard unit tests; no production storage mutations."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest


@pytest.fixture
def helper(monkeypatch):
    monkeypatch.setitem(sys.modules, 'fcntl', SimpleNamespace(LOCK_EX=1, LOCK_NB=2, flock=lambda *a: None))
    path = Path(__file__).resolve().parents[1]/'ops/market/enable_shared_traces.py'
    spec = importlib.util.spec_from_file_location('enable_traces', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_environment_edit_is_exact_and_idempotent(helper):
    value = 'environment: &common\n      MARKET_RELEASE: ${RELEASE}\n      SAFE: yes\n<<: *common\n'
    result = helper.enable_compose(value)
    assert result.replace("      MARKET_SHARED_SOURCE_TRACES: '1'\n", '') == value
    assert helper.enable_compose(result) == result
    assert helper.enable_compose(result.replace("TRACES: '1'", "TRACES: '0'")) == result


@pytest.mark.parametrize('value', [
    'environment: &common\n<<: *common\n',
    "      MARKET_SHARED_SOURCE_TRACES: ${UNREVIEWED}\n",
    "      MARKET_SHARED_SOURCE_TRACES: '0'\n      MARKET_SHARED_SOURCE_TRACES: '1'\n",
])
def test_unrecognized_configuration_is_not_changed(helper, value):
    with pytest.raises(RuntimeError):
        helper.enable_compose(value)


def test_existing_floor_is_preserved_not_moved_to_every_new_version(helper, tmp_path):
    path = tmp_path/'contract.json'
    first = helper.require_reader(path, 'reader-v97')
    second = helper.require_reader(path, 'reader-v98')
    assert first == second and second['rollback_floor'] == 'reader-v97'
    assert json.loads(path.read_text())['required_read_formats'] == [helper.FORMAT]


def test_unknown_contract_and_pending_write_are_fail_closed(helper, tmp_path):
    path = tmp_path/'contract.json'
    path.write_text('{"version":2}')
    with pytest.raises(RuntimeError):
        helper.require_reader(path, 'reader-v97')
    path.with_name(path.name+'.next').write_text('pending')
    with pytest.raises(RuntimeError):
        helper.atomic(path, b'new')
    assert path.read_text() == '{"version":2}'
