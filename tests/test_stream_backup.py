"""Non-production byte fixtures. Never contact SSH or a real database."""
import hashlib
import io
import json
from types import SimpleNamespace

import pytest

from ops.market import stream_backup as m


def guard(tmp_path, **kwargs):
    return m.Guard(tmp_path, 1024, 60, free=lambda: 20 * m.GIB, **kwargs)


class Process:
    def __init__(self, body, code=0):
        self.stdout, self.code, self.killed = io.BytesIO(body), code, False

    def wait(self, **kwargs):
        return self.code

    def poll(self):
        return self.code

    def kill(self):
        self.killed = True


def test_receive_uses_stdout_and_no_remote_dump_file(tmp_path):
    body = b"PGDMP_fixture_non_production"
    calls = []

    def start(args, **kwargs):
        calls.append(args)
        return Process(body)

    path = tmp_path / "new.partial"
    result = m.receive(path, guard(tmp_path), popen=start)
    assert path.read_bytes() == body
    assert result == {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
    assert "--format=custom" in calls[0] and "flock" in calls[0]
    assert not any("--file" in value or ".dump" in value for value in calls[0])


@pytest.mark.parametrize("code,body,error", [
    (1, b"PGDMP_partial", "producer_or_transport_failed"),
    (0, b"", "empty_dump"), (0, b"x" * 1025, "dump_size_budget_exhausted"),
])
def test_failed_stream_preserves_only_partial(tmp_path, code, body, error):
    target = tmp_path / "new.partial"
    with pytest.raises(m.Refused, match=error):
        m.receive(target, guard(tmp_path), popen=lambda *a, **k: Process(body, code))
    assert target.exists() and not target.with_suffix(".dump").exists()


def test_verify_checks_custom_magic_hash_and_archive_list(tmp_path):
    target = tmp_path / "new.partial"
    target.write_bytes(b"PGDMP_fixture_non_production")
    expected = {"bytes": target.stat().st_size,
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
    calls = []
    m.verify(target, expected, guard(tmp_path), run=lambda *a, **k: calls.append(a))
    assert "--list" in calls[0][0]
    expected["sha256"] = "0" * 64
    with pytest.raises(m.Refused, match="changed"):
        m.verify(target, expected, guard(tmp_path), run=lambda *a, **k: pytest.fail("not listed"))


def test_guard_distinguishes_time_capacity_and_actual_reserve(tmp_path):
    with pytest.raises(m.Refused, match="unavailable"):
        m.Guard(tmp_path, 1024, 60, free=lambda: m.MIN_FREE)
    now, free = [0], [m.MIN_FREE + 2000]
    item = m.Guard(tmp_path, 1024, 60, free=lambda: free[0], clock=lambda: now[0])
    free[0] = m.MIN_FREE - 1
    with pytest.raises(m.Refused, match="reserve"):
        item.check()
    free[0], now[0] = m.MIN_FREE + 2000, 60
    with pytest.raises(m.Refused, match="time"):
        item.check()


def test_completed_manifest_never_claims_restoration(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "PG_RESTORE", tmp_path / "fixture-pg_restore")
    m.PG_RESTORE.write_bytes(b"fixture")
    monkeypatch.setattr(m, "private_directory", lambda p: p.mkdir(parents=True))
    monkeypatch.setattr(m.shutil, "disk_usage", lambda p: SimpleNamespace(free=20 * m.GIB))

    def receive(path, _guard):
        path.write_bytes(b"PGDMP_fixture")
        return {"bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    result = m.backup(destination=tmp_path / "backups", receiver=receive,
                      verifier=lambda *a: None, max_bytes=1024)
    assert result["state"] == "completed" and not result["restore_verified"]
    assert not result["source_content_equality_verified"]
    directory = m.Path(result["directory"])
    assert not list(directory.glob("*.partial"))
    assert (directory / result["source"]).is_file()
    saved = json.loads((directory / "RESULT.json").read_text())
    assert saved["archive_list_verified"] and saved["remote_dump_file_created"] is False


def test_cli_defaults_to_plan(capsys):
    assert m.main([]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["executed"] is False and result["restore_verified"] is False
