"""fixture_non_production. No process, socket, PostgreSQL or network is started."""

import hashlib
import json
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest

from ops.market.compact_restore import local_restore as m


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inputs(tmp_path):
    archive = tmp_path / "fixture.dump"
    archive.write_bytes(b"PGDMPfixture_non_production")
    columns = []
    for name, fields in m.EXPECTED_COLUMNS.items():
        columns.extend([["public." + name, index, field, kind, True, 100, "", "", None]
                        for index, (field, kind) in enumerate(fields, 1)])
    catalogue = {
        "identity": ["fixture_non_production", "market", "/fixture", 170005, "UTF8", "C"],
        "relations": [[100 + i, name, 100 + i, "p", 0, "r", 10, "market", None, False, False, 1000]
                      for i, name in enumerate(m.EXPECTED_COLUMNS)],
        "columns": columns, "triggers": [], "views": [],
        "constraints": [[name, "-", "p", name + "_pkey", True, "fixture primary key"]
                        for name in m.EXPECTED_COLUMNS],
        "indexes": [[name, "fixture index", True, True, True, m.EXPECTED_KEYS[name]]
                    for name in m.EXPECTED_COLUMNS],
    }
    catalogue["catalogue_sha256"] = m._canonical_hash(catalogue)
    catalogue.update(snapshot="00000003-00000001-1", trace_rows=2, series_rows=5,
                     snapshot_created_at="2026-10-10T00:00:00Z")
    source = {"protocol": m.verify.PROTOCOL, "target": "source", "rows": 5, "series": 3,
              "logical_sha256": "a" * 64, "original_traces": 2,
              "original_traces_sha256": "b" * 64, "validated_total_traces": 2,
              "indexes_valid": True, "restore_verified": False,
              "original_trace_digests": ["c" * 64, "d" * 64],
              "original_trace_inventory_sha256": m._canonical_hash(["c" * 64, "d" * 64]),
              "source_catalogue": catalogue, "checked_at": "2026-10-10T00:00:00Z",
              "fixture_non_production": True}
    source_path = tmp_path / "source.json"
    source_sha = save(source_path, source)
    receipt = {"received": True, "published": True, "exit_waited": True, "exit_code": 0,
               "bytes": archive.stat().st_size,
               "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
               "source_report_sha256": source_sha, "fixture_non_production": True}
    receipt_path = tmp_path / "receipt.json"
    save(receipt_path, receipt)
    return archive, receipt_path, source_path


def alter_source(paths, mutate, *, rehash_catalogue=False):
    _, receipt_path, source_path = paths
    source = json.loads(source_path.read_text())
    mutate(source)
    if rehash_catalogue:
        catalogue = source["source_catalogue"]
        original = {k: v for k, v in catalogue.items() if k not in (
            "catalogue_sha256", "snapshot", "trace_rows", "series_rows", "snapshot_created_at",
        )}
        catalogue["catalogue_sha256"] = m._canonical_hash(original)
    receipt = json.loads(receipt_path.read_text())
    receipt["source_report_sha256"] = save(source_path, source)
    save(receipt_path, receipt)


def test_bound_actual_metadata_builds_manifest_without_running_pg(tmp_path, monkeypatch):
    paths = inputs(tmp_path)
    value = m.validate_inputs(*paths)
    monkeypatch.setattr(m.shutil, "disk_usage", lambda path: SimpleNamespace(free=100 * 1024**3))
    runner = m.LocalRestore(value, max_growth_bytes=1024**3, max_seconds=60)
    manifest = runner.manifest()
    assert manifest.trace_rows == 2 and manifest.series_rows == 5
    assert manifest.archive_sha256 == value["receipt"]["sha256"]
    assert manifest.source_manifest_sha256 == hashlib.sha256(paths[2].read_bytes()).hexdigest()
    assert runner.work.parent == m.RUNS and len(runner.work.name) == 32
    assert not runner.work.exists() and not runner.created
    assert m.PORT == m.cp.PORT == 54433 and m.DATABASE == m.cp.DATABASE


@pytest.mark.parametrize("field,value", [
    ("received", False), ("published", False), ("exit_waited", False), ("exit_code", 1),
    ("exit_code", False), ("bytes", 1), ("sha256", "missing"), ("source_report_sha256", "a" * 64),
])
def test_receipt_mismatch_rejected_before_native_work(tmp_path, field, value):
    paths = inputs(tmp_path)
    receipt = json.loads(paths[1].read_text())
    receipt[field] = value
    save(paths[1], receipt)
    with pytest.raises(m.Refused, match="archive_receipt_binding_invalid"):
        m.validate_inputs(*paths)


@pytest.mark.parametrize("field,value", [
    ("protocol", "wrong"), ("rows", -1), ("indexes_valid", False), ("target", "restored"),
    ("original_trace_digests", ["c" * 64, "c" * 64]),
    ("original_trace_inventory_sha256", "0" * 64), ("fixture_non_production", "true"),
])
def test_invalid_source_evidence_rejected(tmp_path, field, value):
    paths = inputs(tmp_path)
    alter_source(paths, lambda source: source.__setitem__(field, value))
    with pytest.raises((m.Refused, m.codec.Refused)):
        m.validate_inputs(*paths)


def test_source_columns_and_dependencies_are_not_accepted_from_arbitrary_sql(tmp_path):
    paths = inputs(tmp_path)
    alter_source(paths, lambda s: s["source_catalogue"]["columns"][0].__setitem__(3, "jsonb"),
                 rehash_catalogue=True)
    with pytest.raises(m.Refused, match="source_columns_unsupported"):
        m.validate_inputs(*paths)
    paths = inputs(tmp_path)
    alter_source(paths, lambda s: s["source_catalogue"].__setitem__("views", [["fixture_view"]]),
                 rehash_catalogue=True)
    with pytest.raises(m.Refused, match="source_dependencies_unsupported"):
        m.validate_inputs(*paths)


def test_input_file_change_and_same_size_receipt_replacement_are_rejected(tmp_path):
    paths = inputs(tmp_path)
    value = m.validate_inputs(*paths)
    paths[2].write_text(paths[2].read_text().replace("2026-10-10", "2026-10-11"))
    with pytest.raises(m.Refused, match="input_metadata_changed"):
        m.check_input_metadata(value)


def test_archive_body_hash_is_not_inferred_from_receipt(tmp_path, monkeypatch):
    paths = inputs(tmp_path)
    value = m.validate_inputs(*paths)
    monkeypatch.setattr(m.shutil, "disk_usage", lambda path: SimpleNamespace(free=100 * 1024**3))
    runner = m.LocalRestore(value, max_growth_bytes=1024**3, max_seconds=60)
    paths[0].write_bytes(b"PGDMPfixture_non_productioX")
    with pytest.raises(m.codec.Refused):
        m.cp.verify_archive(paths[0], runner.manifest(), runner.budget)


def test_toc_selects_only_market_target_data_and_never_copies_ddl():
    raw = (b"; fixture_non_production\n"
           b"1; 0 1 TABLE public market_time_series market\n"
           b"2; 0 2 TABLE DATA public market_time_series market\n"
           b"3; 0 3 TABLE DATA public market_source_traces market\n"
           b"4; 0 4 CONSTRAINT public fixture key market\n")
    assert m.ordered_toc(raw) == (
        b"3; 0 3 TABLE DATA public market_source_traces market\n"
        b"2; 0 2 TABLE DATA public market_time_series market\n")


@pytest.mark.parametrize("extra", [
    b"4; 0 4 TABLE DATA public other_data market\n", b"SELECT fixture;\n",
    b"4; 0 4 TABLE DATA public market_time_series other_owner\n",
    b"3; 0 4 TABLE DATA public market_time_series market\n",
])
def test_unexpected_or_duplicate_toc_data_is_refused(extra):
    raw = (b"2; 0 2 TABLE DATA public market_time_series market\n"
           b"3; 0 3 TABLE DATA public market_source_traces market\n")
    with pytest.raises(m.Refused):
        m.ordered_toc(raw + extra)


def test_default_cli_has_no_io_or_native_execution(monkeypatch, capsys):
    monkeypatch.setattr(m, "validate_inputs", lambda *a: pytest.fail("file read"))
    monkeypatch.setattr(m, "LocalRestore", lambda *a, **k: pytest.fail("native execution"))
    assert m.main([]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["executed"] is False and result["restore_verified"] is False
    with pytest.raises(SystemExit):
        m.main(["--run"])


def fake_runner(tmp_path, monkeypatch):
    monkeypatch.setattr(m.shutil, "disk_usage", lambda path: SimpleNamespace(free=100 * 1024**3))
    value = m.validate_inputs(*inputs(tmp_path))
    runner = m.LocalRestore(value, max_growth_bytes=1024**3, max_seconds=60)
    runner.guard = SimpleNamespace(start=lambda: None, close=lambda: None, check=lambda: 1000)
    return runner


@pytest.mark.parametrize("fixture", [True, False])
def test_success_requires_final_comparison_and_own_cluster_stopped(tmp_path, monkeypatch, fixture):
    runner = fake_runner(tmp_path, monkeypatch)
    runner.inputs["fixture_non_production"] = fixture
    events = []
    runner.preflight = lambda: events.append("preflight")
    runner.initialize = lambda: events.append("initialize")
    runner.restore = lambda manifest: events.append("restore")
    runner.final_verify = lambda manifest: events.append("verify")
    runner.stop = lambda: events.append("stop") or True
    monkeypatch.setattr(m, "_report_file", lambda *args: None)
    result = runner.run()
    assert events == ["preflight", "initialize", "restore", "verify", "stop"]
    assert result["data_equal"] and result["cluster_stopped"]
    assert result["restore_verified"] is (not fixture)
    assert result["production_proof"] is (not fixture)
    assert result["fixture_restore_verified"] is fixture


def test_final_mismatch_or_watchdog_failure_still_stops_only_owned_cluster(tmp_path, monkeypatch):
    runner = fake_runner(tmp_path, monkeypatch)
    stopped = []
    runner.preflight = runner.initialize = lambda: None
    runner.restore = lambda manifest: None
    runner.final_verify = lambda manifest: (_ for _ in ()).throw(m.Refused("fixture mismatch"))
    runner.guard.close = lambda: (_ for _ in ()).throw(RuntimeError("fixture secret"))
    runner.stop = lambda: stopped.append(True) or True
    monkeypatch.setattr(m, "_report_file", lambda *args: None)
    report = runner.run()
    assert stopped == [True] and report["cluster_stopped"]
    assert not report["restore_verified"] and not report["data_equal"]
    assert "fixture secret" not in json.dumps(report)


def test_failed_shutdown_never_becomes_restore_proof(tmp_path, monkeypatch):
    runner = fake_runner(tmp_path, monkeypatch)
    runner.preflight = runner.initialize = lambda: None
    runner.restore = runner.final_verify = lambda manifest: None
    runner.stop = lambda: False
    monkeypatch.setattr(m, "_report_file", lambda *args: None)
    report = runner.run()
    assert not report["restore_verified"] and not report["cluster_stopped"]


def test_watchdog_cancels_registered_connection_without_touching_other_connections():
    events = []
    budget = SimpleNamespace(check=lambda: 1000)
    guard = m.Watchdog(budget)

    class DB:
        def cancel_safe(self, *, timeout):
            events.append(timeout)

    owned = DB()
    guard.add(owned)
    budget.check = lambda: (_ for _ in ()).throw(m.Refused("fixture deadline"))
    guard.start()
    guard.thread.join(timeout=2)
    guard.close()
    assert events == [1.0]
    with pytest.raises(m.Refused, match="resource_deadline_or_disk_exhausted"):
        guard.check()


def test_stop_refuses_foreign_owner_before_any_command(tmp_path, monkeypatch):
    runner = fake_runner(tmp_path, monkeypatch)
    runner.work = tmp_path / "own-new-run"
    runner.data = runner.work / "cluster-data"
    runner.data.mkdir(parents=True)
    runner.start_attempted = True
    save(runner.work / "ownership.json", {"port": 54420})
    runner.command = lambda *a, **k: pytest.fail("foreign cluster command")
    with pytest.raises(m.Refused, match="cluster_ownership_changed"):
        runner.stop()


def test_stop_commands_never_address_other_data_directory_or_port(tmp_path, monkeypatch):
    runner = fake_runner(tmp_path, monkeypatch)
    runner.work = tmp_path / "own-new-run"
    runner.data = runner.work / "cluster-data"
    runner.data.mkdir(parents=True)
    runner.start_attempted = True
    save(runner.work / "ownership.json", {"format": "target-restore-owner-v1",
         "run_id": runner.work.name, "data_directory": str(runner.data), "port": m.PORT})
    calls = []

    def command(name, args, **kwargs):
        calls.append((name, args, kwargs))
        return (3 if len(calls) > 1 else 0), b""

    runner.command = command
    assert runner.stop()
    assert all(name == "pg_ctl" and args[:2] == ["-D", str(runner.data)]
               for name, args, _ in calls)
    assert any("fast" in args for _, args, _ in calls)


def test_final_verifier_compares_original_inventory_and_preserves_inputs(tmp_path, monkeypatch):
    runner = fake_runner(tmp_path, monkeypatch)
    source = runner.inputs["source"]
    rows = [(key,) for key in source["original_trace_digests"]]

    class Cursor:
        def __enter__(self):
            self.done = False
            return self

        def __exit__(self, *args):
            pass

        def execute(self, query):
            assert 'WHERE original ORDER BY digest COLLATE "C"' in query

        def fetchmany(self, count):
            assert count == 400
            if self.done:
                return []
            self.done = True
            return rows

    class DB:
        @contextmanager
        def transaction(self):
            yield

        def execute(self, query):
            if query.startswith("SET TRANSACTION"):
                assert "READ ONLY" in query
                return None
            result = (4,) if "count(*)" in query else (1000, 300)
            return SimpleNamespace(fetchone=lambda: result)

        def cursor(self, *, name):
            return Cursor()

    @contextmanager
    def connect():
        yield DB()

    runner.connect = connect
    restored = {**source, "target": "restored", "validated_total_traces": 4}
    monkeypatch.setattr(m.verify, "verify_snapshot", lambda *a, **k: deepcopy(restored))
    original_hash = hashlib.sha256(runner.inputs["archive"].read_bytes()).hexdigest()
    runner.final_verify(runner.manifest())
    assert runner.report["originals_preserved"] and runner.report["archive_unchanged"]
    assert hashlib.sha256(runner.inputs["archive"].read_bytes()).hexdigest() == original_hash
    rows[0] = ("0" * 64,)
    with pytest.raises(m.Refused, match="original_trace_inventory_differs"):
        runner.final_verify(runner.manifest())


def test_actual_six_field_indexes_require_exact_primary_key_order(tmp_path):
    paths = inputs(tmp_path)
    alter_source(paths, lambda s: s["source_catalogue"]["indexes"][1].__setitem__(
        5, ["time", "series"]), rehash_catalogue=True)
    with pytest.raises(m.Refused, match="source_primary_key_unsupported"):
        m.validate_inputs(*paths)


def test_disk_budget_refuses_before_any_artifact_or_process(tmp_path, monkeypatch):
    value = m.validate_inputs(*inputs(tmp_path))
    monkeypatch.setattr(m.shutil, "disk_usage", lambda path: SimpleNamespace(free=m.cp.MIN_FREE))
    monkeypatch.setattr(m.LocalRestore, "command", lambda *a, **k: pytest.fail("native call"))
    with pytest.raises(m.codec.Refused, match="growth budget plus reserve"):
        m.LocalRestore(value, max_growth_bytes=1024, max_seconds=60)


def test_initialize_uses_scram_and_fixed_loopback_without_password_in_argv(tmp_path, monkeypatch):
    runner = fake_runner(tmp_path, monkeypatch)
    runner.work = tmp_path / "own-new-run"
    runner.data = runner.work / "cluster-data"
    runner.data.mkdir(parents=True)
    (runner.data / "PG_VERSION").write_text("17\n")
    runner.secret = "fixture-password-not-an-argument"
    calls, queries = [], []
    runner.command = lambda name, args, **kwargs: calls.append((name, args, kwargs))

    @contextmanager
    def connect(database):
        assert database == "postgres" and runner.start_attempted
        yield SimpleNamespace(execute=lambda sql: queries.append(sql))

    runner.connect = connect
    runner.initialize()
    assert calls[0][0] == "initdb"
    assert "--auth-host=scram-sha-256" in calls[0][1]
    assert "--auth-local=scram-sha-256" in calls[0][1]
    assert "--pwfile=" + str(runner.work / "local-password.txt") in calls[0][1]
    assert runner.secret not in repr(calls)
    options = calls[1][1][calls[1][1].index("-o") + 1]
    assert "-p 54433 -h 127.0.0.1" in options and "fsync=on" in options
    assert queries == ["CREATE DATABASE market_compact_restore_candidate TEMPLATE template0"]


def test_connect_refuses_foreign_cluster_before_yielding_or_writing(tmp_path, monkeypatch):
    import psycopg

    runner = fake_runner(tmp_path, monkeypatch)
    runner.start_attempted = True
    runner.secret = "fixture-only"
    events, arguments = [], []
    runner.guard.add = lambda db: events.append("register")
    runner.guard.remove = lambda db: events.append("unregister")

    class DB:
        def execute(self, query):
            assert query.startswith("SELECT current_database()")
            return SimpleNamespace(fetchone=lambda: (
                m.DATABASE, "127.0.0.1", 54420, str(runner.data), 170005, m.ADMIN))

        def close(self):
            events.append("close")

    monkeypatch.setattr(psycopg, "connect", lambda **kwargs: arguments.append(kwargs) or DB())
    with pytest.raises(m.Refused, match="local_database_identity_changed"), runner.connect():
        pytest.fail("must not yield a foreign database")
    assert arguments[0]["host"] == "127.0.0.1" and arguments[0]["port"] == 54433
    assert events == ["register", "unregister", "close"]


def test_daemon_control_does_not_wait_for_inherited_pipe_eof(tmp_path, monkeypatch):
    runner = fake_runner(tmp_path, monkeypatch)
    runner.tool = lambda name: tmp_path / name
    seen = []

    class Child:
        def poll(self):
            return 0

        def wait(self, timeout):
            return 0

    def start(argv, **kwargs):
        assert kwargs["stdout"] != m.subprocess.PIPE
        assert kwargs["stderr"] != m.subprocess.PIPE
        assert kwargs["stdin"] == m.subprocess.DEVNULL
        kwargs["stdout"].write(b"server started\n")
        kwargs["stdout"].flush()
        seen.append(argv)
        return Child()

    monkeypatch.setattr(m.subprocess, "Popen", start)
    assert runner.command("pg_ctl", ["start"]) == (0, b"server started\n")
    assert seen == [[str(tmp_path / "pg_ctl"), "start"]]


@pytest.mark.parametrize("failure", ["deadline", "output"])
def test_control_timeout_or_output_limit_reaps_only_owned_child(tmp_path, monkeypatch, failure):
    runner = fake_runner(tmp_path, monkeypatch)
    runner.tool = lambda name: tmp_path / name
    events = []

    class Child:
        def poll(self):
            return None

        def kill(self):
            events.append("kill")

        def wait(self, timeout):
            events.append("wait")
            return -1

    def start(argv, **kwargs):
        if failure == "output":
            kwargs["stdout"].write(b"too much")
            kwargs["stdout"].flush()
        return Child()

    monkeypatch.setattr(m.subprocess, "Popen", start)
    if failure == "deadline":
        clock = iter([100.0, 200.0])
        monkeypatch.setattr(m.time, "monotonic", lambda: next(clock))
    with pytest.raises(m.Refused, match="deadline|output_limit"):
        runner.command("pg_ctl", ["start"], output_limit=2)
    assert events == ["kill", "wait"]
