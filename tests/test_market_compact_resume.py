"""fixture_non_production: no PostgreSQL, subprocess, socket or production I/O."""

import hashlib
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from ops.market.compact_restore import resume_restore as m


def save(path, value):
    path.write_text(json.dumps(value), encoding="utf8")


@pytest.fixture
def owned(tmp_path, monkeypatch):
    runs = tmp_path / "target-restore"
    work = runs / ("a" * 32)
    data = work / "cluster-data"
    data.mkdir(parents=True)
    (data / "PG_VERSION").write_text("17")
    (data / "fixture.dat").write_bytes(b"fixture_non_production")
    archive = tmp_path / "fixture.dump"
    archive.write_bytes(b"PGDMPfixture_non_production")
    value = {
        "archive": archive, "fixture_non_production": True,
        "receipt": {"sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                    "bytes": archive.stat().st_size},
        "source_sha256": "b" * 64, "receipt_sha256": "c" * 64,
        "source": {"rows": 3, "original_traces": 1},
    }
    manifest = m.cp.Manifest(value["receipt"]["sha256"], value["receipt"]["bytes"],
                             value["source_sha256"], m.cp.codec_identity(), 1, 3)
    save(work / "manifest.json", json.loads(manifest.identity()))
    save(work / "ownership.json", {"format": "target-restore-owner-v1", "run_id": work.name,
                                   "data_directory": str(data), "port": m.local.PORT})
    save(work / "RESULT.json", {
        "state": "interrupted", "executed": True, "cluster_stopped": True,
        "restore_verified": False, "production_contacted": False,
        "fixture_non_production": True, "work_directory": str(work),
        "database": m.local.DATABASE, "port": m.local.PORT,
        "archive_sha256": value["receipt"]["sha256"],
        "archive_receipt_sha256": value["receipt_sha256"],
        "source_report_sha256": value["source_sha256"],
    })
    (work / "local-password.txt").write_text("fixture_non_production_secret_123456789\n")
    monkeypatch.setattr(m.local, "RUNS", runs)
    monkeypatch.setattr(m.cp.shutil, "disk_usage", lambda _path: SimpleNamespace(free=20*1024**3))
    return work, value


def runner(owned):
    work, value = owned
    return m.ResumeRestore(value, work, max_growth_bytes=1024**3,
                           max_total_cluster_bytes=2*1024**3, max_seconds=60)


def test_existing_prefix_manifest_validated_without_process_or_port_use(owned, monkeypatch):
    monkeypatch.setattr(m.socket, "socket", lambda *a: pytest.fail("no socket at construction"))
    item = runner(owned)
    assert item.saved_manifest.codec_sha256 == m.cp.codec_identity()
    assert item.saved_manifest.series_rows == 3
    assert item.attempt.parent == owned[0] and not item.attempt.exists()
    assert item.report["current_fence_valid"] is False
    assert item.report["production_proof"] is False


@pytest.mark.parametrize("filename,field,value", [
    ("ownership.json", "run_id", "0"*32),
    ("ownership.json", "port", 54420),
    ("RESULT.json", "cluster_stopped", False),
    ("RESULT.json", "state", "verified"),
    ("RESULT.json", "archive_sha256", "e"*64),
    ("RESULT.json", "archive_receipt_sha256", "e"*64),
    ("RESULT.json", "source_report_sha256", "e"*64),
    ("RESULT.json", "fixture_non_production", False),
    ("manifest.json", "codec_sha256", "e"*64),
    ("manifest.json", "series_rows", 4),
])
def test_altered_binding_is_refused_before_start(owned, filename, field, value):
    work, _ = owned
    path = work / filename
    saved = json.loads(path.read_text())
    saved[field] = value
    save(path, saved)
    with pytest.raises(m.local.Refused):
        runner(owned)


@pytest.mark.parametrize("name,value", [("postmaster.pid", "123"), ("PG_VERSION", "16")])
def test_active_pid_or_other_pg_is_refused(owned, name, value):
    (owned[0] / "cluster-data" / name).write_text(value)
    with pytest.raises(m.local.Refused):
        runner(owned)


def test_different_run_parent_is_refused(owned, monkeypatch, tmp_path):
    monkeypatch.setattr(m.local, "RUNS", tmp_path / "other")
    with pytest.raises(m.local.Refused, match="exact_owned"):
        runner(owned)


def test_second_attempt_lock_is_refused_without_deleting_file(owned):
    with m.run_lock(owned[0]), pytest.raises(OSError), m.run_lock(owned[0]):
        pytest.fail("second owner entered")
    assert (owned[0] / "resume.lock").exists()
    with m.run_lock(owned[0]):
        pass


def test_initialization_and_missing_checkpoint_ddl_are_forbidden(owned):
    item = runner(owned)
    with pytest.raises(m.local.Refused, match="initialization_forbidden"):
        item.initialize()
    with pytest.raises(m.local.Refused, match="initialization_forbidden"):
        item.tool("initdb")
    db = SimpleNamespace(execute=lambda *a, **k: pytest.fail("must not execute DDL"))
    proxy = m.ExistingConnection(db)
    for sql in (*m.cp.DDL, "INSERT INTO market_compact_restore.checkpoint VALUES(1,%s,%s)"):
        with pytest.raises(m.local.Refused, match="missing_existing_checkpoint"):
            proxy.execute(sql)


def test_existing_logical_total_and_volume_growth_remain_independent(owned):
    data = owned[0] / "cluster-data"
    initial = sum(p.stat().st_size for p in data.iterdir())
    free = [m.cp.MIN_FREE + 10000]
    budget = m.ResumeBudget(data, max_growth_bytes=1000, max_total_cluster_bytes=initial+1000,
                            max_seconds=60, free_for_data=lambda: free[0])
    assert budget.initial_cluster_bytes == initial
    (data / "fixture.dat").write_bytes(b"x"*1100)
    with pytest.raises(m.local.Refused):
        budget.check(force=True)
    assert budget.failure_diagnostic["code"] == "logical_inventory_refused"
    (data / "fixture.dat").write_bytes(b"fixture_non_production")
    free[0] -= 1001  # External consumers do not count as this directory's growth.
    budget.check(force=True)
    assert budget.diagnostic("observed")["drive_growth_bytes"] == 1001


def test_logical_sweep_is_bounded_and_incoming_reservations_cannot_hide_growth(owned, monkeypatch):
    data = owned[0] / "cluster-data"
    clock = [0]
    original = m.cluster_bytes
    scans = []
    def measure(*args):
        scans.append(clock[0])
        return original(*args)
    monkeypatch.setattr(m, "cluster_bytes", measure)
    budget = m.ResumeBudget(data, max_growth_bytes=1000, max_total_cluster_bytes=2000,
                            max_seconds=10, monotonic=lambda: clock[0])
    for _ in range(20):
        budget.check(10)
    assert scans == [0]
    assert budget.reserved_bytes == 200
    clock[0] = 1
    budget.check()
    assert scans == [0, 1] and budget.reserved_bytes == 0
    (data/"fixture.dat").write_bytes(b"x"*1050)
    clock[0] = 2
    with pytest.raises(m.local.Refused):
        budget.check(1)
    assert budget.failure_diagnostic["code"] == "logical_growth_exhausted"
    assert budget.failure_diagnostic["incoming_bytes"] == 1


@pytest.mark.parametrize("failure", ["deadline", "reserve"])
def test_budget_retains_numeric_failure_cause(owned, failure):
    clock, free = [0], [m.MIN_FREE+10000]
    budget = m.ResumeBudget(owned[0]/"cluster-data", max_growth_bytes=1000,
                            max_total_cluster_bytes=2000, max_seconds=10,
                            monotonic=lambda: clock[0], free_for_data=lambda: free[0])
    if failure == "deadline":
        clock[0] = 10
    else:
        free[0] -= 10001
    with pytest.raises(m.local.codec.Refused):
        budget.check()
    assert budget.failure_diagnostic["code"] == {
        "deadline": "deadline_exhausted",
        "reserve": "disk_reserve_exhausted",
    }[failure]
    assert budget.failure_diagnostic["free_bytes"] == free[0]
    assert budget.failure_diagnostic["elapsed_seconds"] == clock[0]


def test_resume_preflight_does_not_hash_archive_start_cluster_or_write(owned, monkeypatch, capsys):
    item = runner(owned)
    monkeypatch.setattr(m.local, "validate_inputs", lambda *a: item.inputs)
    monkeypatch.setattr(m, "ResumeRestore", lambda *a, **k: item)
    monkeypatch.setattr(item, "run", lambda: pytest.fail("preflight must not run"))
    monkeypatch.setattr(m.cp, "verify_archive", lambda *a: pytest.fail("no full hash"))
    assert m.main(["--preflight", "--work-directory", str(owned[0]), "--archive", "unused",
                   "--archive-receipt", "unused", "--source-report", "unused",
                   "--max-growth-bytes", "1000", "--max-total-cluster-bytes", "2000"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["state"] == "preflight_passed" and value["executed"] is False
    assert value["checkpoint_reload_required"] and not value["archive_content_verified"]
    assert not item.attempt.exists()


@pytest.mark.parametrize("operation,message,code", [
    ("open", "Resume archive/codec/manifest/batch settings differ", "checkpoint_identity_differs"),
    ("check_counts", "Stored prefix row count differs from atomic checkpoint",
     "checkpoint_count_differs"),
    ("consume", "Previously committed logical row differs", "prefix_row_differs"),
    ("consume", "SQL text and fixture secret must not be recorded", "validation_refused"),
])
def test_sink_retains_known_reason_before_coordinator_swallows_it(
    owned, monkeypatch, operation, message, code,
):
    item = runner(owned)
    def failed(*_args, **_kwargs):
        raise m.local.codec.Refused(message)
    monkeypatch.setattr(m.cp.PgBatchSink, operation, failed)
    sink = m.ResumeSink(object(), item.saved_manifest, item.budget, item.report)
    with pytest.raises(m.local.codec.Refused):
        if operation == "consume":
            sink.consume(None, m.cp.Checkpoint(), m.cp.Checkpoint(), replay=True)
        elif operation == "check_counts":
            sink.check_counts(m.cp.Checkpoint())
        else:
            sink.open()
    assert item.report["failure_diagnostic"]["code"] == code
    assert "fixture secret" not in json.dumps(item.report)


def test_stop_does_not_claim_an_unstarted_active_cluster_was_stopped(owned):
    item = runner(owned)
    (item.data/"postmaster.pid").write_text("fixture_non_production")
    assert item.stop() is False


def test_occupied_port_is_checked_only_when_execution_preflight_runs(owned, monkeypatch):
    item = runner(owned)
    calls = []
    def command(name, args, **kwargs):
        if name in ("postgres", "pg_restore"):
            assert kwargs["seconds"] == 30
        calls.append((name, args))
        return (3, b"") if name == "pg_ctl" else (0, b"fixture (PostgreSQL) 17.11\n")
    item.command = command
    class OccupiedPort:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def setsockopt(self, *args):
            pass
        def bind(self, address):
            assert address == ("127.0.0.1", 54433)
            raise OSError("fixture occupied port")
    monkeypatch.setattr(m.socket, "socket", lambda *a: OccupiedPort())
    with pytest.raises(OSError):
        item.preflight()
    assert not item.attempt.exists() and not item.start_attempted
    assert calls[-1][0] == "pg_ctl" and calls[-1][1][-1] == "status"
    assert item.report["preflight_step"] == "port_binding"


def test_start_existing_never_initializes_or_creates_database(owned):
    item = runner(owned)
    calls = []
    item.command = lambda *a, **k: calls.append((a, k))
    item.connect = lambda *a, **k: pytest.fail("start must not create a database")
    item.start_existing()
    assert len(calls) == 1
    (name, args), _ = calls[0]
    assert name == "pg_ctl" and args[-1] == "start"
    assert args[1] == str(item.data)
    assert str(item.attempt/"postgres.log") in args


def test_cluster_file_with_multiple_hardlinks_is_refused(owned, tmp_path):
    target = owned[0]/"cluster-data"/"fixture.dat"
    m.os.link(target, tmp_path/"fixture-other-link")
    with pytest.raises(m.local.Refused, match="nonordinary_cluster_entry"):
        runner(owned)


def test_total_space_time_and_reserve_rejected(owned):
    data = owned[0] / "cluster-data"
    for limits in (dict(max_growth_bytes=1000, max_total_cluster_bytes=100),
                   dict(max_growth_bytes=1000, max_total_cluster_bytes=2000, max_seconds=21601)):
        with pytest.raises((m.local.Refused, m.local.codec.Refused)):
            m.ResumeBudget(data, max_seconds=limits.pop("max_seconds", 60), **limits)
    with pytest.raises(m.local.codec.Refused):
        m.ResumeBudget(data, max_growth_bytes=1000, max_total_cluster_bytes=2000,
                       max_seconds=60, free_for_data=lambda: m.MIN_FREE+999)


def mocked_run(item, monkeypatch, *, failure=None):
    events = []
    item.guard = SimpleNamespace(start=lambda: None, close=lambda: None, check=lambda: 1000)
    def preflight():
        item.attempt.mkdir()
        item.created = True
        events.append("preflight")
    item.preflight = preflight
    item.start_existing = lambda: events.append("start_existing")
    def restore(_manifest):
        events.append("restore")
        if failure == "restore":
            raise RuntimeError("fixture_secret_never_emit")
    item.restore = restore
    item.final_verify = lambda _manifest: events.append("full_ordered_and_trace_verify")
    item.stop = lambda: events.append("stop") or (failure != "stop")
    monkeypatch.setattr(m.local, "check_input_metadata", lambda _inputs: None)
    return events


@pytest.mark.parametrize("fixture", [True, False])
def test_success_is_only_dated_archive_proof_and_old_reports_are_immutable(
    owned, monkeypatch, fixture,
):
    work, inputs = owned
    inputs["fixture_non_production"] = fixture
    path = work / "RESULT.json"
    old = json.loads(path.read_text())
    old["fixture_non_production"] = fixture
    save(path, old)
    before = {p.name: p.read_bytes() for p in work.iterdir() if p.is_file()}
    item = runner(owned)
    events = mocked_run(item, monkeypatch)
    result = item.run()
    assert events == [
        "preflight", "start_existing", "restore", "full_ordered_and_trace_verify", "stop",
    ]
    assert result["data_equal"] and result["restore_verified"] is (not fixture)
    assert result["fixture_restore_verified"] is fixture
    assert result["current_fence_valid"] is False and result["production_proof"] is False
    assert all((work/name).read_bytes() == body for name, body in before.items())
    assert (item.attempt / "RESULT.json").exists()


@pytest.mark.parametrize("failure", ["restore", "stop"])
def test_failure_still_stops_and_saves_only_fixed_diagnostics(owned, monkeypatch, failure):
    item = runner(owned)
    events = mocked_run(item, monkeypatch, failure=failure)
    result = item.run()
    assert events[-1] == "stop"
    assert result["restore_verified"] is False and result["data_equal"] is False
    assert "fixture_secret_never_emit" not in json.dumps(result)
    assert json.loads((item.attempt / "RESULT.json").read_text()) == result


def test_safe_control_failure_diagnostic_does_not_publish_exception_text(owned, monkeypatch):
    item = runner(owned)
    mocked_run(item, monkeypatch)
    error = OSError(12, "fixture_secret_never_emit")
    error.winerror = 1455

    def failed_preflight():
        item.report["preflight_step"] = "postgres_version"
        raise error

    item.preflight = failed_preflight
    result = item.run()
    assert result["error_detail"] == {"type": "OSError", "errno": 12, "winerror": 1455}
    assert result["preflight_step"] == "postgres_version"
    assert result["archive_content_verified"] is True
    assert result["restore_verified"] is False
    assert "fixture_secret_never_emit" not in json.dumps(result)
    assert m.failure_code(m.local.Refused("command_deadline_exceeded")) == "control_command_timeout"


def test_failed_prefix_is_saved_before_child_exit_and_safe_status_after_exit(owned, monkeypatch):
    item = runner(owned)
    item.attempt.mkdir()
    item.tool = lambda name: item.attempt / name
    item.command = lambda *a, **k: (0, b"1; 0 1 TABLE DATA public market_source_traces market\n"
                                  b"2; 0 2 TABLE DATA public market_time_series market\n")
    events = []
    @contextmanager
    def connect():
        yield object()
    item.connect = connect
    class Child:
        stream = object()
        wait_exit = None
        def __enter__(self):
            return self
        def __exit__(self, *args):
            assert item.report["prefix"]["state"] == "interrupted"
            assert (item.attempt / "prefix-result.json").exists()
            events.append("exit")
        def safe_status(self):
            assert events == ["exit"]
            events.append("safe_status")
            return {"exit_code": 1, "exit_waited": False, "threads_stopped": True}
    child = Child()
    monkeypatch.setattr(m.supervisor, "open_pg_restore", lambda *a, **k: child)
    monkeypatch.setattr(m.cp, "PgBatchSink", lambda *a: object())
    def restore(*args, evidence_path, **kwargs):
        result = {"state": "interrupted", "stream_complete": False}
        save(evidence_path, result)
        return result
    monkeypatch.setattr(m.cp, "restore_prefix", restore)
    with pytest.raises(m.local.Refused, match="prefix_restore_incomplete"):
        item.restore(item.saved_manifest)
    assert events == ["exit", "safe_status"]
    assert item.report["pipe"]["exit_code"] == 1


def test_default_cli_is_offline(monkeypatch, capsys):
    monkeypatch.setattr(m.local, "validate_inputs", lambda *a: pytest.fail("no input read"))
    assert m.main([]) == 0
    assert json.loads(capsys.readouterr().out)["executed"] is False
