"""Synthetic, offline audit guards; these fixtures are not coverage evidence."""

import ast
import json
import sys
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from ops.market import audit_current_coverage as audit
from services.market_dashboard.service_scope import VERSION, included


@pytest.fixture
def inner():
    namespace = {"__name__": "fixture_non_production"}
    exec(compile(audit.INNER, "<coverage-audit-fixture>", "exec"), namespace)
    return namespace


@pytest.fixture
def no_process(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("offline fixture must not start a child")

    monkeypatch.setattr(audit.subprocess, "run", reject)
    monkeypatch.setattr(audit.subprocess, "check_output", reject)


def test_default_is_offline(no_process, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["audit_current_coverage.py"])
    audit.main()
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "executed": False, "batch_keys": 256, "diagnostic_selects": None,
        "universe_projection_timeout_seconds": 10,
        "statement_timeout_seconds": 3, "snapshot_deadline_seconds": 120,
    }


@pytest.mark.parametrize("output_kind", ["absent", "relative", "existing", "missing_parent"])
def test_output_guard_precedes_ssh(no_process, monkeypatch, tmp_path, output_kind):
    argv = ["audit_current_coverage.py", "--run"]
    if output_kind == "relative":
        argv += ["--output", "relative.json"]
    elif output_kind == "existing":
        target = tmp_path / "existing.json"
        target.write_text("preserve", encoding="utf8")
        argv += ["--output", str(target)]
    elif output_kind == "missing_parent":
        argv += ["--output", str(tmp_path / "missing" / "new.json")]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as error:
        audit.main()
    assert error.value.code == 2
    if output_kind == "existing":
        assert target.read_text(encoding="utf8") == "preserve"


@pytest.mark.parametrize("diagnostic", [False, True])
def test_remote_transport_is_fixed_stdin_not_shell(diagnostic):
    source = audit.remote_source(diagnostic)
    tree = ast.parse(source)
    assignments = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"INNER", "REPORT_KEY", "filters"}
    }
    assert assignments["INNER"] == f"DIAGNOSE_UNIVERSE={diagnostic}\n" + audit.INNER
    assert assignments["REPORT_KEY"] == (
        "universe_diagnostic" if diagnostic else "target_securities"
    )
    assert assignments["filters"] == [
        "docker", "ps", "-q", "--filter", "label=com.docker.compose.project=korea-stocks",
        "--filter", "label=com.docker.compose.service=market-api",
    ]
    assert "['docker','exec','-i','-w','/app',ids[0],'python','-']" in source
    assert "input=INNER.encode()" in source
    assert "shell=" not in source
    assert "MARKET_DATABASE_URL" not in audit.SSH


def test_failure_diagnostic_never_forwards_payload_or_exception_text():
    result = audit.safe_failure({
        "stage": "universe_projection", "error_type": "QueryCanceled", "sqlstate": "57014",
        "query_count": 2, "elapsed_seconds": 3.02, "child_exit_code": 1,
        "complete": 1, "sql": "fixture-secret-sql", "stderr": "fixture-secret-stderr",
        "dsn": "fixture-secret-dsn", "error": "fixture-secret-exception",
    })
    assert result == {
        "audit_failed": 1, "complete": 0, "stage": "universe_projection",
        "error_type": "QueryCanceled", "sqlstate": "57014", "query_count": 2,
        "elapsed_seconds": 3.02, "child_exit_code": 1,
    }
    assert audit.safe_failure({"stage": "secret", "error_type": "SQL text !",
                               "sqlstate": "secret"}) == {"audit_failed": 1, "complete": 0}


def test_identity_projection_preserves_current_scope_without_full_payload(inner):
    rows = [
        {"code": "005930", "market": "KOSPI", "name": "fixture common",
         "security_type": "common", "history": {"secret": "not transferred"}},
        {"code": "005935", "market": "KOSPI", "name": "fixture preferred",
         "security_type": "preferred"},
        {"code": "035900", "market": "KOSDAQ", "name": "fixture kosdaq",
         "security_type": "common"},
        {"code": "123456", "market": "KONEX", "name": "fixture excluded"},
        {"code": "0106J0", "market": "KOSPI", "name": "fixture listed trust"},
        {"code": "0120X0", "market": "KOSDAQ", "name": "fixture listed trust"},
    ]
    fields = ("code", "market", "name", "security_type")
    projected = [{key: row.get(key) for key in fields} for row in rows]
    assert [row["code"] for row in projected if included(row)] == [
        row["code"] for row in rows if included(row)
    ] == ["005930", "005935", "035900"]
    sql = inner["UNIVERSE_SQL"]
    assert "WHERE key=%s" in sql
    assert "n BETWEEN 1 AND 10000" in sql
    assert "octet_length(identities)<=16777216" in sql
    assert "r(code text,market text,name text,security_type text)" in sql
    assert "jsonb" not in sql
    encoded = json.dumps({"rows": projected, "received_at": "2026-01-01T00:00:00Z"})
    observation = ("2026-01-01T00:00:00Z", 40_000_000, len(rows), len(encoded), encoded)

    class DB:
        timeout = 3000

        def execute(self, query, params=()):
            if query in ('SET LOCAL statement_timeout=10000',
                         'SET LOCAL statement_timeout=3000'):
                self.timeout = int(query.rsplit('=', 1)[1])
                return self
            assert query == sql and params == ("universe",)
            assert self.timeout == 10000
            return self

        def fetchone(self):
            return observation

    seen = []

    def controls(db):
        assert db.timeout == 3000
        return {}

    inner["controls"] = controls
    for name in ("collect", "jobs", "projections"):
        inner[name] = lambda db, codes: seen.append(codes) or {}
    result = inner["coverage_report"](
        DB(), (datetime(2026, 1, 1, tzinfo=UTC),), included, VERSION
    )
    assert result["target_securities"] == 3
    assert seen == [["005930", "005935", "035900"]] * 3
    assert result["universe_source_bytes"] == 40_000_000
    assert all(token not in json.dumps(result) for token in ("005930", "fixture", "secret"))


def test_unknown_scope_refuses_before_database_read(inner):
    class DB:
        def execute(self, *args):
            raise AssertionError("scope must be checked before query")

    with pytest.raises(inner["Refused"], match="scope_version_review_required"):
        inner["coverage_report"](DB(), (), included, VERSION + 1)


def test_exact_key_batches_retain_payload_bound(inner):
    calls = []

    class DB:
        def execute(self, query, params):
            calls.append((query, params))
            return []

    keys = [f"collection:{i:06d}" for i in range(257)]
    assert list(inner["snapshots"](DB(), keys)) == []
    assert [len(params[1]) for _, params in calls] == [256, 1]
    assert all("key=ANY(%s)" in query and "LIKE" not in query for query, _ in calls)
    assert all(params[0] == 262144 for _, params in calls)


def test_end_of_history_does_not_assert_latest_ready_or_value_verification(inner):
    fixtures = [
        {"status": "ready", "rows": 10, "backfill_complete": True},
        {"status": "ready", "rows": 8, "backfill_complete": True, "source_status": "ready"},
        {"status": "ready", "rows": 9, "backfill_complete": True,
         "source_status": "stale", "next_before": "2025-01-01", "next_until": "2025-01-01"},
        {"status": "no_data", "rows": 0, "backfill_complete": True},
        {"status": "error", "http_status": 404},
        {"status": "not_in_provider_catalogue"},
    ]
    codes = [f"{i:06d}" for i in range(len(fixtures))]
    inner["snapshots"] = lambda db, keys, maximum: zip(
        keys, [None] * len(keys), fixtures, strict=True
    )
    result = inner["jobs"](None, codes)
    for item in result.values():
        assert item["end_of_history_receipt"] == 3
        assert item["end_of_history_and_current_ready"] == 1
        assert item["completion_cursor_conflict"] == 1
        assert item["cursor_remaining"] == 1
        assert item["provider_404_unresolved"] == 1
        assert item["securities_with_reported_rows"] == 3
        assert item["not_verified"] == 3


def test_cache_numeric_presence_is_not_comparison_validation(inner):
    inner["PROJECTION_VERSION"] = 14

    class Cursor(list):
        def fetchall(self):
            return self

    class DB:
        def execute(self, query, params):
            if query == inner["PROJECTION_SQL"]:
                return Cursor([(
                    "official-view:000001:analysis", None, False, "revision", "basis", "14",
                    [{"metric": "EPS", "frequency": "ttm", "rows": 4,
                      "first": "2025-01-01", "last": "2026-06-30"}],
                )])
            return Cursor([("official:000001", "revision"), ("share-basis:000001", "basis")])

    result = inner["projections"](DB(), ["000001", "000002"])
    assert result["cache_states"] == {"current_cache": 1, "missing_not_verified": 1}
    assert result["stored_metric_rows"]["EPS"]["ttm"]["cached_rows"] == 4
    assert result["comparison_verified"] is None
    assert result["comparison_tested"] == 0
    assert result["comparison_not_verified"] == 2


@pytest.mark.parametrize('fail_at', ['projection', 'reset'])
def test_projection_failure_rolls_back_local_timeout_without_retry_or_leaking_error(
    inner, monkeypatch, capsys, fail_at,
):
    class QueryCanceled(Exception):
        sqlstate = '57014'

    calls = []

    class DB:
        timeout = 3000

        def __enter__(self):
            return self

        def __exit__(self, *args):
            assert self.timeout == 3000

        def execute(self, sql, params=()):
            calls.append(sql)
            if sql.startswith('SELECT transaction_timestamp()'):
                return SimpleNamespace(fetchone=lambda: (
                    datetime(2026, 1, 1, tzinfo=UTC), 'on', 'repeatable read',
                ))
            if sql == 'SET LOCAL statement_timeout=10000':
                assert self.timeout == 3000
                self.timeout = 10000
            elif sql == inner['UNIVERSE_SQL']:
                assert self.timeout == 10000 and params == ('universe',)
                if fail_at == 'projection':
                    raise QueryCanceled('fixture-secret-statement-and-dsn')
                return SimpleNamespace(fetchone=lambda: None)
            elif sql == 'SET LOCAL statement_timeout=3000':
                assert fail_at == 'reset'
                raise QueryCanceled('fixture-secret-reset')
            elif sql == 'ROLLBACK':
                self.timeout = 3000
            else:
                assert sql == 'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY'
            return self

    def connect(url, **kwargs):
        assert url == 'postgresql://fixture_non_production'
        assert kwargs['options'] == (
            '-c statement_timeout=3000 -c lock_timeout=1000 '
            '-c idle_in_transaction_session_timeout=10000'
        )
        return DB()

    monkeypatch.setitem(sys.modules, 'psycopg', SimpleNamespace(connect=connect))
    monkeypatch.setitem(sys.modules, 'services.market_dashboard.official_views',
                        SimpleNamespace(PROJECTION_VERSION=14))
    inner['os'] = SimpleNamespace(environ={
        'MARKET_DATABASE_URL': 'postgresql+psycopg://fixture_non_production',
    })
    alarms = []
    inner['signal'] = SimpleNamespace(SIGALRM=14, signal=lambda *args: None, alarm=alarms.append)
    with pytest.raises(QueryCanceled) as error:
        inner['main']()
    assert error.value.sqlstate == '57014'
    assert inner['stage'] == 'universe_projection'
    assert inner['queries'] == 2  # SELECT count excludes SET LOCAL / transaction control.
    assert alarms == [120]
    assert calls.count(inner['UNIVERSE_SQL']) == 1
    assert calls[-1] == 'ROLLBACK'
    assert not capsys.readouterr().out


def test_snapshot_deadline_still_applies_after_universe_statement(inner):
    calls = []
    checks = 0

    def check():
        nonlocal checks
        checks += 1
        if checks == 3:  # execute() checks elapsed time again after the SELECT.
            raise inner['Refused']('deadline')

    class DB:
        def execute(self, query, params=()):
            calls.append(query)
            return self

        def fetchone(self):
            raise AssertionError('expired snapshot must not continue')

    inner['check'] = check
    with pytest.raises(inner['Refused'], match='deadline'):
        inner['read_universe'](DB())
    assert inner['MAX_SECONDS'] == 120
    assert calls == ['SET LOCAL statement_timeout=10000', inner['UNIVERSE_SQL']]
    # main() owns rollback on any failure; no other query follows this exception.


def test_diagnostic_plan_remains_three_seconds_and_offline(no_process, monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['audit_current_coverage.py', '--diagnose-universe'])
    audit.main()
    report = json.loads(capsys.readouterr().out)
    assert report['executed'] is False
    assert report['diagnostic_selects'] == 2
    assert report['universe_projection_timeout_seconds'] == 3
    assert report['snapshot_deadline_seconds'] == 120
