"""fixture_non_production: source snapshot boundaries, no real connections."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from ops.market.compact_restore import source_snapshot as m


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0]

    def fetchall(self):
        return self.rows


class Inventory:
    def __init__(self, db):
        self.db = db
        self.offset = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.db.cursor_closed = True

    def execute(self, query):
        assert query == 'SELECT digest FROM public.market_source_traces ORDER BY digest COLLATE "C"'

    def fetchmany(self, size):
        assert size == 400
        rows = [(key,) for key in self.db.keys[self.offset : self.offset + size]]
        self.offset += size
        return rows


class DB:
    def __init__(self):
        self.autocommit = True
        self.info = SimpleNamespace(transaction_status=SimpleNamespace(name="IDLE"))
        self.statements = []
        self.identity = ["fixture", "fixture_owner", "/fixture/pg", 170011, "UTF8", "C"]
        self.keys = ["1" * 64, "2" * 64, "3" * 64]
        self.counts = (3, 1201)
        self.token = "00000003-00000011-1"
        self.fail_on = None
        self.cursor_closed = False
        self.data = {
            key: []
            for key in ("relations", "columns", "indexes", "constraints", "triggers", "views")
        }
        for i, (name, columns) in enumerate(m.EXPECTED_COLUMNS.items(), 1):
            self.data["relations"].append(
                [i, name, i + 10, "p", 0, "r", 10, "fixture_owner", None, False, False, 8192]
            )
            self.data["columns"].extend(
                [name, j, field, kind, True, 0, "", "", None]
                for j, (field, kind) in enumerate(columns, 1)
            )
            self.data["indexes"].append(
                [name, "fixture index", True, True, True, m.EXPECTED_KEYS[name]]
            )
            self.data["constraints"].append(
                [name, "-", "p", name + "_pkey", True, "fixture primary"]
            )

    def execute(self, query):
        query = query if isinstance(query, str) else query.as_string()
        self.statements.append(query)
        if query == self.fail_on:
            raise RuntimeError("fixture failure")
        if query.startswith("BEGIN "):
            self.info.transaction_status.name = "INTRANS"
        elif query == "ROLLBACK":
            self.info.transaction_status.name = "IDLE"
        elif query.startswith("SELECT current_database()"):
            return Result([tuple(self.identity)])
        elif query == "SELECT pg_export_snapshot()":
            return Result([(self.token,)])
        elif query.startswith("SELECT (SELECT count(*)"):
            return Result([self.counts])
        else:
            for marker, key in (
                ("FROM pg_class c", "relations"),
                ("FROM pg_attribute a", "columns"),
                ("FROM pg_index WHERE", "indexes"),
                ("FROM pg_constraint", "constraints"),
                ("pg_trigger WHERE", "triggers"),
                ("FROM pg_depend d", "views"),
            ):
                if marker in query:
                    assert query.endswith(" LIMIT 1025")
                    return Result(deepcopy(self.data[key]))
        return Result([])

    def cursor(self, *, name):
        assert name == "snapshot_original_digests"
        return Inventory(self)


def snapshot():
    db = DB()
    with m.hold_snapshot(
        db, check=lambda: None, expected_database="fixture", expected_user="fixture_owner"
    ) as value:
        return deepcopy(value)


def test_exact_actual_schema_and_catalogue_checksum():
    db = DB()
    result = m.catalogue(db)
    checksum = result.pop("catalogue_sha256")
    assert checksum == m.digest(result)
    assert len(result["columns"]) == 7
    assert len(result["constraints"]) == 2
    assert all(query.startswith("SELECT ") for query in db.statements)


@pytest.mark.parametrize(
    "section,index,field,value",
    [
        ("relations", 0, 7, "another_owner"),
        ("relations", 0, 3, "u"),
        ("relations", 0, 4, 123),
        ("relations", 0, 9, True),
        ("relations", 1, 5, "p"),
        ("columns", 0, 3, "text"),
        ("columns", 0, 4, False),
        ("columns", 0, 6, "a"),
        ("columns", 0, 8, "nextval('fixture')"),
        ("indexes", 0, 2, False),
        ("indexes", 1, 5, ["time", "series"]),
        ("constraints", 0, 2, "f"),
    ],
)
def test_unreviewed_catalogue_refused(section, index, field, value):
    db = DB()
    db.data[section][index][field] = value
    with pytest.raises(m.Refused):
        m.catalogue(db)


@pytest.mark.parametrize("section", ["views", "triggers"])
def test_external_dependencies_refused(section):
    db = DB()
    db.data[section] = [["fixture dependency"]]
    with pytest.raises(m.Refused):
        m.catalogue(db)


def test_population_bound_no_truncation():
    db = DB()
    db.data["indexes"] *= 513
    with pytest.raises(m.Refused, match="catalogue population"):
        m.catalogue(db)


@pytest.mark.parametrize("identity_index,value", [(3, 160010), (4, "LATIN1")])
def test_server_contract(identity_index, value):
    db = DB()
    db.identity[identity_index] = value
    with pytest.raises(m.Refused, match="PostgreSQL 17"):
        m.catalogue(db)


def test_guardian_lock_precedes_snapshot_and_finally_rollback():
    db = DB()
    with pytest.raises(RuntimeError, match="caller fails"):
        with m.hold_snapshot(
            db, check=lambda: None, expected_database="fixture", expected_user="fixture_owner"
        ) as state:
            assert state["trace_rows"] == 3 and state["series_rows"] == 1201
            assert db.info.transaction_status.name == "INTRANS"
            raise RuntimeError("caller fails")
    assert db.info.transaction_status.name == "IDLE"
    assert db.statements[-1] == "ROLLBACK"
    lock = next(i for i, query in enumerate(db.statements) if query.startswith("LOCK TABLE"))
    export = db.statements.index("SELECT pg_export_snapshot()")
    assert lock < export


@pytest.mark.parametrize("failure", ["lock", "identity", "check"])
def test_guardian_setup_failure_releases_transaction(failure):
    db = DB()
    if failure == "lock":
        db.fail_on = (
            "LOCK TABLE public.market_source_traces,public.market_time_series IN SHARE MODE"
        )
    calls = 0

    def check():
        nonlocal calls
        calls += 1
        if failure == "check" and calls == 2:
            raise RuntimeError("fixture expiry")

    with pytest.raises((m.Refused, RuntimeError)):
        with m.hold_snapshot(
            db,
            check=check,
            expected_database="wrong" if failure == "identity" else "fixture",
            expected_user="fixture_owner",
        ):
            pytest.fail("Must not yield")
    assert db.statements[-1] == "ROLLBACK"


@pytest.mark.parametrize("autocommit,status", [(False, "IDLE"), (True, "INTRANS")])
def test_never_rolls_back_callers_transaction(autocommit, status):
    db = DB()
    db.autocommit = autocommit
    db.info.transaction_status.name = status
    with pytest.raises(m.Refused, match="Dedicated idle"):
        with m.hold_snapshot(
            db, check=lambda: None, expected_database="fixture", expected_user="fixture_owner"
        ):
            pytest.fail("Must not yield")
    assert db.statements == []


def install_verifier(monkeypatch, rows=1201, traces=3):
    def verify(db, target, *, check, max_rows, max_traces):
        assert target == "source"
        assert (max_rows, max_traces) == (1201, 3)
        assert db.statements[0] == "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"
        assert db.statements[1].startswith("SET TRANSACTION SNAPSHOT '")
        assert db.info.transaction_status.name == "INTRANS"
        check()
        return {"rows": rows, "original_traces": traces, "fixture_non_production": True}

    monkeypatch.setattr(m, "verify_snapshot", verify)


def test_import_readonly_exact_inventory_including_unreferenced(monkeypatch):
    install_verifier(monkeypatch)
    db = DB()
    report = m.verify_exported(db, snapshot(), check=lambda: None)
    assert report["original_trace_digests"] == db.keys
    assert report["original_trace_inventory_sha256"] == m.digest(db.keys)
    assert db.cursor_closed and db.statements[-1] == "ROLLBACK"


@pytest.mark.parametrize("change", ["database", "owner", "catalogue", "population", "inventory"])
def test_import_mismatch_fails_and_rolls_back(monkeypatch, change):
    install_verifier(monkeypatch, rows=1200 if change == "population" else 1201)
    db, state = DB(), snapshot()
    if change == "database":
        db.identity[0] = "other"
    elif change == "owner":
        db.data["relations"][0][7] = "other"
    elif change == "catalogue":
        db.data["relations"][0][2] = 999
    elif change == "inventory":
        db.keys.pop()
    with pytest.raises(m.Refused):
        m.verify_exported(db, state, check=lambda: None)
    assert db.statements[-1] == "ROLLBACK"


@pytest.mark.parametrize(
    "key,value",
    [("snapshot", "x'; COMMIT"), ("snapshot", None), ("series_rows", True), ("trace_rows", -1)],
)
def test_invalid_export_metadata_rejected_before_begin(key, value):
    db, state = DB(), snapshot()
    state[key] = value
    with pytest.raises(m.Refused):
        m.verify_exported(db, state, check=lambda: None)
    assert db.statements == []
