"""fixture_non_production: pure tests; explicit --native starts ONLY fresh 54435.

Native run: <=180s/512MiB growth/6GiB floor. Preserve all fixture files and stop
only its own cluster. Never connects to the running 54433 completed-restore DB.
"""

import hashlib
import importlib.util
import json
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ops.market.compact_restore import pg_delta_sink as p


def fixture_module():
    spec = importlib.util.spec_from_file_location(
        "delta_fixture_helpers", Path(__file__).with_name("test_market_compact_delta_archive.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def candidate():
    return p.Candidate("1" * 32, 123, "1234567890123456789", "a" * 64)


@pytest.mark.parametrize(
    "changes",
    [
        dict(run_id="../x"),
        dict(database_oid=True),
        dict(system_identifier="unknown"),
        dict(owner="market"),
        dict(base_result_sha256="not-sha"),
    ],
)
def test_candidate_identity_is_not_an_arbitrary_database(changes):
    with pytest.raises(p.Refused):
        replace(candidate(), **changes)


@pytest.mark.parametrize(
    "field,value",
    [
        ("host", "remote"),
        ("port", 54433),
        ("dbname", "market_compact_restore_candidate"),
        ("user", "market"),
    ],
)
def test_original_restore_or_remote_connection_refused_before_sql(field, value):
    c = candidate()
    info = SimpleNamespace(
        host="127.0.0.1",
        port=p.PORT,
        dbname=c.database,
        user=c.owner,
        transaction_status=SimpleNamespace(name="IDLE"),
    )
    setattr(info, field, value)

    def forbidden(*_):
        raise AssertionError("wrong connection executed SQL")

    sink = p.PgDeltaSink(SimpleNamespace(info=info, autocommit=True, execute=forbidden), c, None)
    with pytest.raises(p.Refused, match="dedicated_candidate_connection"):
        sink._identity()


def test_budget_floor_growth_total_deadline_and_reservation(tmp_path, monkeypatch):
    c = candidate()
    monkeypatch.setattr(p, "RUNS", tmp_path)
    c.data_directory.mkdir(parents=True)
    (c.data_directory / "fixture").write_bytes(b"x" * 100)
    free = [10 * 1024**3]
    monkeypatch.setattr(p.Budget, "free", lambda _: free[0])
    budget = p.Budget(c, max_growth_bytes=1000, max_total_bytes=1500, max_seconds=30)
    budget.check(400)
    with pytest.raises(p.Refused, match="space_exhausted"):
        budget.check(700)
    budget.check(force=True)
    free[0] = p.delta.checkpoint.MIN_FREE - 1
    with pytest.raises(p.Refused, match="space_exhausted"):
        budget.check()
    free[0] = 10 * 1024**3
    monkeypatch.setattr(p.time, "monotonic", lambda: budget.started + 31)
    with pytest.raises(p.Refused, match="deadline"):
        budget.check()


class FakeDB:
    autocommit = True

    def __init__(self, commit_failure=False, rollback_failure=False, begin_failure=False):
        self.info = SimpleNamespace(transaction_status=SimpleNamespace(name="IDLE"))
        self.calls = []
        self.commit_failure, self.rollback_failure = commit_failure, rollback_failure
        self.begin_failure = begin_failure
        self.closed = False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if sql.startswith("BEGIN"):
            self.info.transaction_status.name = "INTRANS"
            if self.begin_failure:
                raise RuntimeError("fixture-begin-response-lost")
        if sql == "COMMIT" and self.commit_failure:
            raise RuntimeError("fixture-private-text-not-for-report")
        if sql == "ROLLBACK" and self.rollback_failure:
            raise RuntimeError("fixture-private-text-not-for-report")
        if sql in ("COMMIT", "ROLLBACK"):
            self.info.transaction_status.name = "IDLE"
        return SimpleNamespace(fetchone=lambda: (True,))

    def close(self):
        self.closed = True


def fake_sink(monkeypatch, db):
    budget = SimpleNamespace(check=lambda *_, **__: 2000)
    sink = p.PgDeltaSink(db, candidate(), budget)
    monkeypatch.setattr(sink, "_identity", lambda: None)
    monkeypatch.setattr(sink, "_catalogue", lambda: None)
    return sink


def test_transaction_failure_rolls_back_and_never_retries(monkeypatch):
    db = FakeDB()
    sink = fake_sink(monkeypatch, db)
    with pytest.raises(p.Refused, match="injected"):
        with sink.transaction():
            raise p.Refused("injected")
    assert sink.state == "rolled_back" and not sink.transaction_committed and not sink.active
    assert db.calls[-1][0] == "ROLLBACK"
    assert ("SELECT set_config(%s,%s,true)", ("transaction_timeout", "2000")) in db.calls
    with pytest.raises(p.Refused, match="single_candidate_attempt"):
        with sink.transaction():
            pass


def test_commit_failure_is_unknown_not_false_rollback_or_success(monkeypatch):
    db = FakeDB(commit_failure=True)
    sink = fake_sink(monkeypatch, db)
    with pytest.raises(p.Refused, match="candidate_commit_outcome_unknown_no_retry"):
        with sink.transaction():
            pass
    assert sink.state == "commit_unknown" and not sink.transaction_committed and db.closed
    assert not any(sql == "ROLLBACK" for sql, _ in db.calls)


@pytest.mark.parametrize("rollback_failure", [False, True])
def test_begin_transport_error_after_server_begin_always_cleans_up(monkeypatch, rollback_failure):
    db = FakeDB(begin_failure=True, rollback_failure=rollback_failure)
    sink = fake_sink(monkeypatch, db)
    with pytest.raises(RuntimeError, match="fixture-begin-response-lost"):
        with sink.transaction():
            raise AssertionError("body must never run after unconfirmed BEGIN")
    assert sink.state == ("rollback_unconfirmed" if rollback_failure else "rolled_back")
    assert db.closed is rollback_failure
    assert any(sql == "ROLLBACK" for sql, _ in db.calls)
    assert not sink.active and not sink.transaction_committed


def test_rollback_transport_failure_is_not_reported_rolled_back(monkeypatch):
    db = FakeDB(rollback_failure=True)
    sink = fake_sink(monkeypatch, db)
    with pytest.raises(p.Refused, match="injected"):
        with sink.transaction():
            raise p.Refused("injected")
    assert sink.state == "rollback_unconfirmed" and db.closed


def test_changed_base_receipt_rejected_before_candidate_io():
    with pytest.raises(p.Refused, match="candidate_base_binding"):
        p.apply_to_candidate(
            None,
            candidate(),
            None,
            None,
            None,
            SimpleNamespace(expected_sha256={"base_result": "b" * 64}),
        )


def test_post_commit_guard_failure_keeps_actual_commit_state(monkeypatch):
    db = FakeDB()
    sink = fake_sink(monkeypatch, db)

    def check(*_, **__):
        if sink.transaction_committed:
            raise p.Refused("candidate_deadline")
        return 2000

    sink.budget.check = check
    with pytest.raises(p.Refused, match="candidate_deadline"):
        with sink.transaction():
            pass
    assert sink.transaction_committed and sink.state == "committed"
    assert not any(sql == "ROLLBACK" for sql, _ in db.calls)


def test_apply_failure_preserves_state_without_exposing_exception_text(monkeypatch):
    sink = SimpleNamespace(state="commit_unknown", transaction_committed=False)
    monkeypatch.setattr(p, "PgDeltaSink", lambda *_, **__: sink)

    def failed(*_, **__):
        raise RuntimeError("fixture-secret-value")

    monkeypatch.setattr(p.delta, "apply", failed)
    with pytest.raises(p.ApplyFailure) as caught:
        p.apply_to_candidate(
            None,
            candidate(),
            SimpleNamespace(check=lambda: None),
            None,
            None,
            SimpleNamespace(expected_sha256={"base_result": "a" * 64}),
        )
    assert str(caught.value) == "candidate_apply_failed_commit_unknown"
    assert "fixture-secret" not in str(caught.value)
    assert caught.value.transaction_state == "commit_unknown"
    assert caught.value.transaction_committed is False


def native():
    """One real PG transaction run, tiny synthetic evidence; no production proof."""
    import psycopg
    from psycopg import sql

    f = fixture_module()
    run_id = uuid.uuid4().hex
    work = p.RUNS / run_id
    data = work / "cluster-data"
    tools = p.ROOT / "work/tools/postgresql-restore/pgsql/bin"
    report = {
        "fixture_non_production": True,
        "production_proof": False,
        "cases": [],
        "port": p.PORT,
        "data_directory": str(data),
        "cluster_stopped": False,
    }
    started = time.monotonic()
    launched = False
    connections = []

    def guard():
        p.require(time.monotonic() - started < 180, "native_fixture_deadline")

    def command(name, args, *, cleanup=False):
        if not cleanup:
            guard()
        destination = work / (name + "-" + uuid.uuid4().hex + ".log")
        with destination.open("xb") as output:
            result = subprocess.run(
                [str(tools / (name + ".exe")), *map(str, args)],
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=output,
                timeout=30 if cleanup else min(30, max(1, 180 - (time.monotonic() - started))),
            )
        p.require(result.returncode == 0, "native_process_failed_" + name)

    def connect(name):
        guard()
        db = psycopg.connect(
            host="127.0.0.1",
            port=p.PORT,
            user="delta_owner",
            dbname=name,
            connect_timeout=3,
            autocommit=True,
            options="-c statement_timeout=10000 -c lock_timeout=1000",
        )
        connections.append(db)
        return db

    def capture(db):
        return (
            db.execute(
                f'SELECT * FROM {p.ROWS} ORDER BY series COLLATE "C",time COLLATE "C"'
            ).fetchall(),
            db.execute(f'SELECT * FROM {p.TRACES} ORDER BY digest COLLATE "C"').fetchall(),
        )

    def seed(db, view, *, checkpoint):
        for statement in p.delta.checkpoint.DDL:
            if ".checkpoint " not in statement or checkpoint:
                db.execute(statement)
        originals = {r[0] for r in view.traces()}
        with db.cursor() as cur:
            cur.executemany(f"INSERT INTO {p.ROWS} VALUES(%s,%s,%s,%s)", view.rows())
            cur.executemany(
                f"INSERT INTO {p.TRACES} VALUES(%s,%s,%s,%s)",
                [
                    (r.digest, r.byte_count, r.payload, r.digest in originals)
                    for r in view.records.values()
                ],
            )

    with socket.socket() as probe:
        p.require(probe.connect_ex(("127.0.0.1", p.PORT)) != 0, "fixture_port_in_use")
    p.require(
        p.shutil.disk_usage(p.ROOT).free >= p.delta.checkpoint.MIN_FREE + 512 * 1024**2,
        "fixture_initial_space",
    )
    work.mkdir(parents=True, exist_ok=False)
    try:
        command(
            "initdb",
            ["-D", data, "-U", "delta_owner", "-A", "trust", "--encoding=UTF8", "--no-locale"],
        )
        with (data / "postgresql.conf").open("a", encoding="utf8") as output:
            output.write(
                f"\nport={p.PORT}\nlisten_addresses='127.0.0.1'\nshared_buffers='16MB'\n"
                "max_connections=10\nmax_wal_size='128MB'\nmin_wal_size='32MB'\n"
            )
        launched = True
        command("pg_ctl", ["-D", data, "-l", work / "postgres.log", "-w", "-t", "20", "start"])
        admin = connect("postgres")
        database = "market_delta_" + run_id
        admin.execute("CREATE DATABASE dated_base_fixture")
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        base_db, db = connect("dated_base_fixture"), connect(database)
        t1, t2, financial = f.trace("base" * 200), f.trace("new" * 200), f.trace("financial-only")
        base = f.snapshot(
            [f.row("A", 1, t1), f.row("가", 2, t1), f.row("삭제", 3, t1)],
            [t1, financial],
            compact=True,
        )
        current = f.snapshot(
            [f.row("A", 4, t2), f.row("가", 2, t1), f.row("추가", 5, t2)], [t1, t2, financial]
        )
        seed(base_db, base, checkpoint=True)
        base_state = capture(base_db)
        seed(db, base, checkpoint=False)
        evidence = f.write_evidence(work, base, current)
        artifact = work / "changes.jsonl"
        archive = p.delta.build(artifact, evidence, base, current, check=guard)
        oid, system = admin.execute(
            "SELECT oid,(SELECT system_identifier::text FROM pg_control_system()) "
            "FROM pg_database WHERE datname=%s",
            (database,),
        ).fetchone()
        binding = p.Candidate(run_id, oid, system, evidence.expected_sha256["base_result"])

        def sink(connection=db):
            budget = p.Budget(
                binding,
                max_growth_bytes=128 * 1024**2,
                max_total_bytes=512 * 1024**2,
                max_seconds=60,
            )
            return p.PgDeltaSink(connection, binding, budget)

        before = capture(db)
        failing = sink()
        try:
            with failing.transaction():
                failing.add_original_trace(t2)
                failing.change_row(f.row("A", 999, t1), f.row("A", 4, t2))
        except p.Refused as error:
            p.require(str(error) == "candidate_before_differs", "unexpected_CAS_failure")
        else:
            raise AssertionError("bad CAS accepted")
        p.require(capture(db) == before and failing.state == "rolled_back", "CAS_rollback_failed")
        report["cases"].append("actual_CAS_provenance_and_trace_rollback")

        owner = sink()
        second_db = connect(database)
        second = sink(second_db)
        with owner.transaction():
            try:
                with second.transaction():
                    raise AssertionError("duplicate ownership accepted")
            except p.Refused as error:
                p.require(str(error) == "candidate_already_owned", "wrong_owner_refusal")
        p.require(capture(db) == before, "owner_test_changed_rows")
        report["cases"].append("actual_concurrent_owner_refused")

        exhausted = sink()
        try:
            with exhausted.transaction():
                exhausted.add_original_trace(t2)
                exhausted.budget.reserved = exhausted.budget.growth + 1
                exhausted.budget.check()
        except p.Refused as error:
            p.require(str(error) == "candidate_space_exhausted", "wrong_budget_refusal")
        else:
            raise AssertionError("budget exceeded but committed")
        p.require(
            capture(db) == before and exhausted.state == "rolled_back", "budget_rollback_failed"
        )
        report["cases"].append("actual_write_rollback_with_injected_budget_sample")

        budget = p.Budget(
            binding, max_growth_bytes=128 * 1024**2, max_total_bytes=512 * 1024**2, max_seconds=60
        )
        applied = p.apply_to_candidate(
            db, binding, budget, artifact, archive["archive_sha256"], evidence
        )
        p.require(
            applied["data_equal"]
            and applied["transaction_committed"]
            and applied["production_proof"] is False,
            "native_apply_failed",
        )
        p.require(capture(base_db) == base_state, "base_fixture_changed")
        with db.transaction():
            db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            final = p.delta.ordered.verify_snapshot(
                db, "restored", check=guard, max_rows=3, max_traces=10
            )
        expected = f.report(current, "2026-10-10T02:00:00+00:00")
        p.delta.ordered.equal_reports(expected, final)
        report["cases"].append("actual_delta_apply_final_C_order_ALL_original_trace_hash")
        report.update(data_equal=True, base_unchanged=True, result=applied, verification=final)
    except BaseException as error:
        report.update(
            passed=False,
            error_type=type(error).__name__,
            error_code=str(error) if isinstance(error, p.Refused) else "native_fixture_failure",
        )
        raise
    finally:
        for db in connections:
            db.close()
        try:
            if launched and (data / "postmaster.pid").exists():
                command(
                    "pg_ctl", ["-D", data, "-m", "fast", "-w", "-t", "20", "stop"], cleanup=True
                )
            report["cluster_stopped"] = not (data / "postmaster.pid").exists()
            with socket.socket() as probe:
                report["port_closed"] = probe.connect_ex(("127.0.0.1", p.PORT)) != 0
            report["elapsed_seconds"] = round(time.monotonic() - started, 3)
            report["passed"] = bool(
                report.get("data_equal") and report["cluster_stopped"] and report["port_closed"]
            )
        finally:
            report["module_sha256"] = hashlib.sha256(Path(p.__file__).read_bytes()).hexdigest()
            (work / "RESULT.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8"
            )
            print(
                json.dumps(
                    {
                        "result": str(work / "RESULT.json"),
                        "passed": report.get("passed", False),
                        "cluster_stopped": report.get("cluster_stopped", False),
                    }
                )
            )
    return report


if __name__ == "__main__":
    if sys.argv[1:] != ["--native"]:
        print(
            json.dumps(
                {"executed": False, "fixture_non_production": True, "production_proof": False}
            )
        )
    else:
        native()
