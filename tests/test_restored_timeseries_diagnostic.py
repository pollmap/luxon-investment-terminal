"""Mock-only guards; importing these tests never contacts any PostgreSQL server."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "work/storage-v106/diagnose_restored_timeseries.py"
SPEC = importlib.util.spec_from_file_location("local_timeseries_diagnostic", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def prepare(monkeypatch, tmp_path):
    report = tmp_path / "verification.json"
    report.write_text(
        json.dumps(
            {
                "passed": True,
                "pg_restore_succeeded": True,
                "port": module.PORT,
                "status": "completed",
                "restored": {
                    "table_rows": {"public.market_time_series": 2},
                    "series_groups": [{"kind": "prices", "rows": 2}],
                },
            }
        ),
        encoding="utf8",
    )
    password = tmp_path / "local-password.txt"
    password.write_text("fixture_not_a_real_secret", encoding="utf8")
    monkeypatch.setattr(module, "REPORT", report)
    monkeypatch.setattr(module, "PASSWORD", password)
    return report


class FakeConnection:
    def __init__(self, wrong_path=False, fail_scan=False):
        self.closed = False
        self.queries = []
        self.wrong_path = wrong_path
        self.fail_scan = fail_scan

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def execute(self, query):
        assert query.lstrip().startswith("SELECT")
        self.queries.append(query)
        if query == module.GROUP_SQL and self.fail_scan:
            raise RuntimeError("fixture failure")
        return self

    def fetchone(self):
        if "data_directory" in self.queries[-1]:
            return {
                "path": str(module.DATA if not self.wrong_path else Path("C:/other")),
                "version": 170011,
                "port": module.PORT,
                "address": module.HOST,
                "readonly": "on",
                "database": module.DATABASE,
                "username": module.USER,
            }
        return {"datcollate": "C", "datctype": "C"}

    def fetchall(self):
        if self.queries[-1] == module.GROUP_SQL:
            return [
                {
                    "family": "prices",
                    "prefix": "prices",
                    "encoding": encoding,
                    "rows": 1,
                    "payload_physical_bytes": 200,
                    "payload_max_physical_bytes": 200,
                }
                for encoding in ("inline", "source-trace-reference")
            ]
        return []

    def rollback(self):
        pass


def test_refuses_incomplete_restore_before_connect(monkeypatch, tmp_path):
    report = prepare(monkeypatch, tmp_path)
    report.write_text('{"passed": false}', encoding="utf8")
    monkeypatch.setattr(module.psycopg, "connect", lambda **_: pytest.fail("must not connect"))
    with pytest.raises(ValueError):
        module.collect()


def test_wrong_cluster_is_closed_before_any_data_query(monkeypatch, tmp_path):
    prepare(monkeypatch, tmp_path)
    db = FakeConnection(wrong_path=True)
    monkeypatch.setattr(module.psycopg, "connect", lambda **_: db)
    with pytest.raises(ValueError, match="identity_mismatch"):
        module.collect()
    assert db.closed and len(db.queries) == 1


def test_one_group_scan_reuses_existing_counts_and_readonly_options(monkeypatch, tmp_path):
    prepare(monkeypatch, tmp_path)
    db = FakeConnection()
    seen = {}

    def connect(**args):
        seen.update(args)
        return db

    monkeypatch.setattr(module.psycopg, "connect", connect)
    result = module.collect()
    assert db.closed and db.queries.count(module.GROUP_SQL) == 1
    assert result["passed"] is True
    assert result["family_distribution"][0]["encoding_fractions"] == {
        "inline": 0.5,
        "source-trace-reference": 0.5,
    }
    assert seen["host"] == "127.0.0.1" and seen["port"] == 54420
    assert "default_transaction_read_only=on" in seen["options"]
    assert "statement_timeout=900000" in seen["options"]
    assert "transaction_timeout=960000" in seen["options"]
    assert "fixture_not_a_real_secret" not in json.dumps(result)


def test_scan_failure_closes_connection_without_retry(monkeypatch, tmp_path):
    prepare(monkeypatch, tmp_path)
    db = FakeConnection(fail_scan=True)
    monkeypatch.setattr(module.psycopg, "connect", lambda **_: db)
    with pytest.raises(RuntimeError):
        module.collect()
    assert db.closed and db.queries.count(module.GROUP_SQL) == 1
