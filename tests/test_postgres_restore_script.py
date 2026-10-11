"""Safety/cleanup regression tests; these never start PostgreSQL or restore a DB."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import verify_postgres_restore as restore


@pytest.fixture
def local_fixture(tmp_path, monkeypatch):
    root = tmp_path / "postgresql-restore"
    data, binaries = root / "verify-data", root / "pgsql/bin"
    data.mkdir(parents=True)
    binaries.mkdir(parents=True)
    (data / "PG_VERSION").write_text("17")
    for filename in ("pg_ctl.exe", "pg_restore.exe", "postgres.exe"):
        (binaries / filename).write_bytes(b"fixture")
    password = root / "restore-password.tmp"
    password.write_text("fixture_private_password_not_to_print")
    dump = root / "backup.dump"
    dump.write_bytes(b"PGDMPfixture")
    digest = hashlib.sha256(dump.read_bytes()).hexdigest()
    output = root / "run-1"
    monkeypatch.setattr(restore.shutil, "disk_usage", lambda _: SimpleNamespace(free=60 * 1024 ** 3))

    class Socket:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def bind(self, address): assert address == ("127.0.0.1", 54389)

    monkeypatch.setattr(restore.socket, "socket", lambda *args: Socket())
    return root, data, binaries, dump, digest, output, password


def test_preflight_requires_complete_dump_correct_hash_space_and_reserved_paths(local_fixture):
    root, _, _, dump, digest, output, _ = local_fixture
    result = restore.validate_inputs(dump, digest, output, root=root)
    assert result["sha256"] == digest
    for bad_dump, bad_hash, bad_out, message in (
        (dump, "0" * 64, output, "dump_sha256_mismatch"),
        (dump, digest, root.parent / "outside", "path_outside_reserved_workspace"),
        (dump.with_suffix(".partial"), digest, output, "complete_dump_required"),
    ):
        with pytest.raises(restore.VerificationError, match=message):
            restore.validate_inputs(bad_dump, bad_hash, bad_out, root=root)


def test_preflight_does_not_touch_existing_cluster_or_low_capacity(local_fixture, monkeypatch):
    root, data, _, dump, digest, output, _ = local_fixture
    monkeypatch.setattr(restore.shutil, "disk_usage", lambda _: SimpleNamespace(free=44 * 1024 ** 3))
    with pytest.raises(restore.VerificationError, match="less_than_45_gib_free"):
        restore.validate_inputs(dump, digest, output, root=root)
    (data / "postmaster.pid").write_text("fixture")
    with pytest.raises(restore.VerificationError, match="already_has_pid_file"):
        restore.validate_inputs(dump, digest, output, root=root)


def test_existing_database_is_not_replaced():
    calls = []

    class Connection:
        def execute(self, query, params=None):
            calls.append((query, params))
            return SimpleNamespace(fetchone=lambda: {"exists": 1})

    with pytest.raises(restore.VerificationError, match="already_exists_preserved"):
        restore.create_empty_database(Connection())
    assert len(calls) == 1
    assert not any("DROP" in query or "CREATE" in query for query, _ in calls)


@pytest.mark.parametrize("field,value", [(None,None), ("host","192.0.2.1"),
    ("port",5432), ("username","other"), ("version",160001), ("data","other-cluster")])
def test_server_identity_checks_host_without_inet_netmask(tmp_path, field, value):
    data = tmp_path / "expected-cluster"
    row = {"data":str(data), "host":"127.0.0.1", "port":54389,
           "username":"restore_check", "version":170011}
    if field:
        row[field] = value

    class Connection:
        def execute(self, query):
            # inet::text includes /32; host(inet) returns the actual address.
            assert "host(inet_server_addr()) AS host" in query
            return SimpleNamespace(fetchone=lambda: row)

    if field:
        with pytest.raises(restore.VerificationError, match="identity_mismatch"):
            restore.check_server(Connection(), data)
    else:
        restore.check_server(Connection(), data)


def test_restore_flags_have_no_replacement_or_source_connection():
    command = restore.restore_command(Path("tools"), Path("backup.dump"))
    assert {"--exit-on-error", "--no-owner", "--no-acl", "--no-tablespaces", "--jobs=2"}.issubset(command)
    assert not {"--clean", "--create"}.intersection(command)
    assert command[command.index("--host") + 1] == "127.0.0.1"
    assert command[command.index("--port") + 1] == "54389"
    assert command[command.index("--dbname") + 1] == "stocks_restore"


@pytest.mark.parametrize("fail_stage", [None, "restore", "connect", "inspect"])
def test_private_cluster_stops_after_success_or_failure(local_fixture, monkeypatch, fail_stage):
    root, data, binaries, dump, digest, output, password = local_fixture
    monkeypatch.setattr(restore, "validate_inputs", lambda *args: {
        "root": root, "data": data, "binaries": binaries, "dump": dump,
        "sha256": digest, "output": output, "password": password,
        "free_bytes_before": 60 * 1024 ** 3,
    })
    commands, status = [], iter([3, 0, 3])

    def run(command, log, env, **kwargs):
        commands.append(command)
        if command[-1] == "status":
            return next(status)
        if fail_stage == "restore" and "--jobs=2" in command:
            raise restore.VerificationError("synthetic_restore_failure")
        return 0

    class Connection:
        def __enter__(self):
            if fail_stage == "connect":
                raise RuntimeError(password.read_text())
            return self
        def __exit__(self, *args): pass
        def execute(self, query, params=None):
            return SimpleNamespace(fetchone=lambda: {"rows": 0})

    monkeypatch.setattr(restore, "run_command", run)
    monkeypatch.setattr(restore, "connect", lambda *args: Connection())
    monkeypatch.setattr(restore, "check_server", lambda *args: None)
    monkeypatch.setattr(restore, "create_empty_database", lambda *args: None)
    monkeypatch.setattr(restore, "inspect_database", lambda *args: {"passed": fail_stage != "inspect"})
    report = restore.verify(dump, digest, output)
    assert report["passed"] == (fail_stage is None)
    assert report["local_cluster_stopped"] is True
    assert any(command[-1] == "stop" and "fast" in command for command in commands)
    saved = (output / "verification.json").read_text()
    assert password.read_text() not in saved
    assert json.loads(saved) == report
    assert report["production_contacted"] is False


def test_preflight_failure_reports_without_starting(monkeypatch, capsys):
    def fail(*args):
        raise restore.VerificationError("less_than_45_gib_free")
    monkeypatch.setattr(restore, "validate_inputs", fail)
    monkeypatch.setattr(restore, "run_command", lambda *args: pytest.fail("must not start"))
    report = restore.verify("unused", "unused", "unused")
    assert report["status"] == "failed"
    assert report["failure"] == "less_than_45_gib_free"
    assert report["local_cluster_started"] is False
