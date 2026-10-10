"""fixture_non_production: actual ASGI routes and SQLite target-write observation.

No production URL, provider credentials, workers, network or PostgreSQL are used.
Requests may enqueue work or cache projections but must not mutate collected facts.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import socket
import threading
from collections import Counter, OrderedDict
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.exc import DBAPIError

from services.market_dashboard import app as market
from services.market_dashboard.response_cache import RevisionResponseCache
from services.market_dashboard.store import Store
from services.market_dashboard.time_series import put_series

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TARGETS = ("market_time_series", "market_source_traces")
RESULTS = []
TRACE = {
    "source": "fixture_non_production",
    "available_at": "2026-03-03",
    "source_document_id": "a" * 64,
    "unit": "KRW",
    "fixture_note": "evidence " * 200,
}
ROWS = [
    {
        "date": "2026-06-01",
        "open": 100,
        "high": 110,
        "low": 95,
        "close": 105,
        "volume": 10,
        "source_trace": TRACE,
    },
    {
        "date": "2026-06-02",
        "open": 105,
        "high": 115,
        "low": 101,
        "close": 112,
        "volume": 12,
        "source_trace": TRACE,
    },
]
BASE = market.BASE + "/securities/005930"
ROUTES = {
    "search": (market.BASE + "/search?q=fixture", True),
    "summary": (BASE + "?view=summary", True),
    "prices": (BASE + "/prices?period=all", False),
    "bars": (BASE + "/bars?interval=day&venue=KRX", True),
    "bars-integrated": (BASE + "/bars?interval=day&venue=%ED%86%B5%ED%95%A9", True),
    "trading": (BASE + "/trading", True),
    "trading-history": (BASE + "/trading/history?kind=investor-trading", True),
    "financial-index": (BASE + "/financials", False),
    "financial-period": (BASE + "/financials?year=2025&report_code=11011&basis=CFS", False),
    "valuation": (BASE + "/analysis/valuation?metric=EPS&years=5", True),
    "openalgo-history": ("/internal/market-data/005930/history?interval=D", False),
    "openalgo-quotes": ("/internal/market-data/005930/quotes", False),
    "openalgo-depth": ("/internal/market-data/005930/depth", False),
}


def fingerprint(store):
    with store.engine.connect() as db:
        return {
            table: hashlib.sha256(
                json.dumps(
                    [
                        tuple(r)
                        for r in db.exec_driver_sql("SELECT * FROM " + table + " ORDER BY 1, 2")
                    ],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            for table in TARGETS
        }


def fence(store):
    with store.engine.begin() as db:
        for table in TARGETS:
            for operation in ("INSERT", "UPDATE", "DELETE"):
                db.exec_driver_sql(
                    f"CREATE TRIGGER audit_{table}_{operation} BEFORE {operation} ON {table} "
                    "BEGIN SELECT RAISE(ABORT, 'fixture_target_fenced'); END"
                )


@contextmanager
def observe(store):
    mutations = []

    def before(_connection, _cursor, statement, _parameters, _context, _executemany):
        match = re.match(
            r'\s*(INSERT(?:\s+OR\s+\w+)?\s+INTO|UPDATE|DELETE\s+FROM|REPLACE\s+INTO)\s+["`]?([a-z_][a-z_0-9]*)',
            statement,
            re.I,
        )
        if match:
            mutations.append({"operation": match[1].upper(), "table": match[2].lower()})

    event.listen(store.engine, "before_cursor_execute", before)
    try:
        yield mutations
    finally:
        event.remove(store.engine, "before_cursor_execute", before)


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    for key in (
        "MARKET_REDIS_URL",
        "MARKET_OPENALGO_URL",
        "MARKET_PUBLIC_ORIGIN",
        "MARKET_DATABASE_URL",
        "MARKET_STORAGE_DIR",
        "MARKET_INTERNAL_KEY_FILE",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MARKET_SHARED_SOURCE_TRACES", "1")

    def no_network(*_args, **_kwargs):
        pytest.fail("Network access is forbidden in fixture target audit")

    original_connect = socket.socket.connect
    original_pair = socket.socketpair
    local = threading.local()

    def socketpair(*args, **kwargs):
        # Windows asyncio creates its own loopback self-pipe. Permit ONLY that
        # exact socketpair call stack, never arbitrary loopback/PG connections.
        local.self_pipe = True
        try:
            return original_pair(*args, **kwargs)
        finally:
            local.self_pipe = False

    def connect(sock, address):
        if getattr(local, "self_pipe", False) and address[0] in ("127.0.0.1", "::1"):
            return original_connect(sock, address)
        no_network()

    monkeypatch.setattr(socket, "socketpair", socketpair)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "create_connection", no_network)


@pytest.fixture
def store(tmp_path, monkeypatch):
    value = Store("sqlite:///" + (tmp_path / "fixture.sqlite").as_posix(), tmp_path / "data")
    value.put(
        "universe",
        {
            "rows": [
                {
                    "code": "005930",
                    "name": "fixture_non_production",
                    "market": "KOSPI",
                    "price": 112,
                    "change": 2,
                    "as_of": "2026-06-02",
                    "source_trace": TRACE,
                }
            ]
        },
    )
    key = tmp_path / "internal-fixture.key"
    key.write_text("fixture_non_production_internal_key")
    monkeypatch.setenv("MARKET_INTERNAL_KEY_FILE", str(key))
    monkeypatch.setattr(market, "get_store", lambda: value)
    monkeypatch.setattr(market, "_body_cache", {})
    monkeypatch.setattr(market, "_rows_cache", {})
    monkeypatch.setattr(market, "_security_rows", OrderedDict())
    monkeypatch.setattr(market, "_search_responses", RevisionResponseCache())
    monkeypatch.setattr(market, "_detail_responses", RevisionResponseCache())
    yield value
    value.engine.dispose()


def seed(store, storage):
    if storage == "empty":
        return

    def write(key, payload):
        put_series(store, key, payload) if storage == "normalized" else store.put(key, payload)

    for key in ("prices:005930", "bars:005930:KRX:day", "bars:005930:통합:day"):
        write(
            key,
            {
                "rows": ROWS,
                "status": "ready",
                "adjustment": "source",
                "per_share_basis": "fixture_non_production_aligned",
                "venue": "통합" if "통합" in key else "KRX",
                "source_trace": TRACE,
            },
        )
    write(
        "toss:investor-trading:005930",
        {
            "rows": [
                {
                    "date": "2026-06-02",
                    "individual": {"netBuyVolume": 8},
                    "foreigner": {"netBuyVolume": 0},
                    "source_trace": TRACE,
                }
            ],
            "status": "ready",
        },
    )
    store.put(
        "quotes",
        {"005930": {"price": 112, "as_of": "2026-06-02", "venue": "통합", "quote_trace": TRACE}},
    )
    store.put(
        "depth:005930",
        {
            "book": [{"price": 112, "ask": 5, "bid": 0}],
            "trades": [],
            "venue": "통합",
            "book_status": "ready",
            "trades_status": "pending",
        },
    )
    store.put(
        "detail:005930", {"company": {"description": "fixture_non_production"}, "financials": []}
    )
    store.put(
        "trading:005930",
        {
            "brokers": [{"buy_name": "fixture broker", "buy_volume": 2}],
            "program": {"net": 0},
            "brokers_status": "ready",
            "program_status": "ready",
        },
    )
    store.put(
        "company-report-fixture",
        {
            "status": "ready",
            "rows": [
                {
                    "name": "매출액",
                    "current": 100,
                    "unit": "KRW",
                    "statement": "IS",
                    "account_id": "ifrs-full_Revenue",
                }
            ],
        },
    )
    store.put(
        "official:005930",
        {
            "financial_reports": {
                "2025:11011:CFS": {
                    "year": "2025",
                    "report_code": "11011",
                    "basis": "CFS",
                    "status": "ready",
                    "snapshot_key": "company-report-fixture",
                    "row_count": 1,
                }
            },
            "xbrl_facts": [],
            "per_share_facts": [
                {
                    "name": "EPS",
                    "value": 10,
                    "basis": "CFS",
                    "frequency": "annual",
                    "period_start": "2025-01-01",
                    "period_end": "2025-12-31",
                    "period_verified": True,
                    "security_code": "005930",
                    "currency": "KRW",
                    "share_class_verified": True,
                    "per_share_basis": "fixture_non_production_aligned",
                    "published_at": "2026-03-03",
                    "source_trace": TRACE,
                }
            ],
        },
    )
    if storage == "legacy-inline":
        # Exercise read-time projection construction, not only the eager views
        # that Store.put('official:...') creates for current collectors.
        with store.engine.begin() as db:
            db.execute(
                store.snapshots.delete().where(store.snapshots.c.key.like("official-view:%"))
            )


def headers(path):
    return (
        {"X-Market-Key": "fixture_non_production_internal_key"}
        if path.startswith("/internal/")
        else {}
    )


def record(name, storage, fenced, observations, before, after, statuses, extra=None):
    item = {
        "scenario": name,
        "storage": storage,
        "target_fence": fenced,
        "status_codes": statuses,
        "target_fingerprint_unchanged": before == after,
        "mutation_attempts_by_table": dict(Counter(r["table"] for r in observations)),
        "target_write_attempts": sum(r["table"] in TARGETS for r in observations),
        "evidence": "fixture_non_production_actual_fastapi_sqlite",
        **(extra or {}),
    }
    RESULTS.append(item)
    assert before == after
    assert item["target_write_attempts"] == 0


@pytest.mark.parametrize("storage", ["normalized", "legacy-inline", "empty"])
@pytest.mark.parametrize("fenced", [False, True])
@pytest.mark.parametrize("name", list(ROUTES))
def test_route_matrix(store, monkeypatch, storage, fenced, name):
    seed(store, storage)
    if fenced:
        fence(store)
    before = fingerprint(store)
    builds = []
    for cache in (market._search_responses, market._detail_responses):
        original = cache.response

        def measured(store, key, dependencies, build, *, initial_revision=None, original=original):
            def work():
                builds.append(str(key))
                return build()

            return original(store, key, dependencies, work, initial_revision=initial_revision)

        monkeypatch.setattr(cache, "response", measured)
    path, revision_cached = ROUTES[name]
    with observe(store) as observed:
        with TestClient(market.app) as client:
            first = client.get(path, headers=headers(path))
            first_builds = len(builds)
            second = client.get(path, headers=headers(path))
    expected = 404 if storage == "empty" and name in ("openalgo-quotes", "openalgo-depth") else 200
    assert first.status_code == second.status_code == expected, first.text
    assert first.json() == second.json()
    if revision_cached:
        assert first_builds == 1 and len(builds) == 1
    if name in ("prices", "bars", "bars-integrated", "openalgo-history") and storage != "empty":
        assert len(first.json()["rows"]) == 2
    if name == "trading-history" and storage != "empty":
        assert first.json()["rows"][0]["foreigner"] == 0
    if name == "valuation" and storage != "empty":
        assert first.json()["availability"]["comparison"] is True
        assert any(row["reference_price"] == 150 for row in first.json()["series"])
    record(
        name,
        storage,
        fenced,
        observed,
        before,
        fingerprint(store),
        [first.status_code, second.status_code],
        {
            "response_builds": len(builds),
            "second_request": "verified_revision_cache_hit"
            if revision_cached
            else "repeat_read_not_asserted_cache_hit",
        },
    )


def test_refresh_jobs_and_missing_financial_period_only_write_intent_tables(store):
    seed(store, "normalized")
    fence(store)
    before = fingerprint(store)
    paths = [
        BASE + "?view=summary",
        BASE + "/prices",
        BASE + "/financials?year=2024&report_code=11011&basis=CFS",
    ]
    with observe(store) as observed, TestClient(market.app) as client:
        responses = [client.get(p) for p in paths]
    assert all(r.status_code == 200 for r in responses)
    assert store.active() == ["005930"]
    assert store.get("trading-history-request:005930:history")["kind"] == "history"
    assert store.get("financial-request:005930")["query_key"] == "statement:2024:11011:CFS"
    assert {"market_jobs", "market_snapshots"} <= {r["table"] for r in observed}
    record(
        "refresh-enqueue",
        "normalized",
        True,
        observed,
        before,
        fingerprint(store),
        [r.status_code for r in responses],
    )


def test_financial_read_model_cache_write_failure_keeps_read_and_target_unchanged(store):
    seed(store, "normalized")

    def projection_failure():
        raise RuntimeError("fixture_non_production cache persistence failure")

    def register(dbapi, _record):
        dbapi.create_function("fixture_projection_failure", 0, projection_failure)

    event.listen(store.engine, "connect", register)
    with store.engine.begin() as db:
        db.execute(store.snapshots.delete().where(store.snapshots.c.key.like("official-view:%")))
        db.connection.driver_connection.create_function(
            "fixture_projection_failure", 0, projection_failure
        )
        db.exec_driver_sql(
            "CREATE TRIGGER reject_projection BEFORE INSERT ON market_snapshots "
            "WHEN NEW.key LIKE 'official-view:%' BEGIN SELECT fixture_projection_failure(); END"
        )
    # The SQLite engine really rejects this INSERT with OperationalError. This
    # is an injected database failure, not a production disk-failure simulation.
    fence(store)
    before = fingerprint(store)
    with (
        observe(store) as observed,
        TestClient(market.app, raise_server_exceptions=False) as client,
    ):
        failed = client.get(BASE + "/financials")
        unaffected = client.get(BASE + "?view=summary")
    assert failed.status_code == 200 and unaffected.status_code == 200
    assert failed.json()["reports"][0]["row_count"] == 1
    record(
        "projection-insert-operational-failure-isolated",
        "normalized",
        True,
        observed,
        before,
        fingerprint(store),
        [failed.status_code, unaffected.status_code],
        {
            "failure": (
                "actual SQLite OperationalError from injected trigger function; "
                "not a production disk failure."
            )
        },
    )


def test_missing_financial_snapshot_returns_partial_failure_without_target_writes(store):
    seed(store, "normalized")
    with store.engine.begin() as db:
        db.execute(
            store.snapshots.delete().where(store.snapshots.c.key == "company-report-fixture")
        )
    store.cache.clear()
    fence(store)
    before = fingerprint(store)
    with observe(store) as observed, TestClient(market.app) as client:
        failed = client.get(BASE + "/financials?year=2025&report_code=11011&basis=CFS")
        unaffected = client.get(BASE + "?view=summary")
    assert failed.status_code == 503 and unaffected.status_code == 200
    record(
        "financial-source-missing",
        "normalized",
        True,
        observed,
        before,
        fingerprint(store),
        [503, 200],
    )


def test_openalgo_sidecar_timeout_uses_existing_stored_series(store, monkeypatch):
    seed(store, "normalized")
    monkeypatch.setenv("MARKET_OPENALGO_URL", "http://fixture.invalid")

    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("fixture_non_production transport failure")

    monkeypatch.setattr(httpx, "post", timeout)
    fence(store)
    before = fingerprint(store)
    with observe(store) as observed, TestClient(market.app) as client:
        result = client.get(BASE + "/bars?venue=%ED%86%B5%ED%95%A9")
    assert result.status_code == 200
    assert result.json()["transport"] == "stored_fallback"
    assert len(result.json()["rows"]) == 2
    record(
        "openalgo-timeout-fallback",
        "normalized",
        True,
        observed,
        before,
        fingerprint(store),
        [200],
        {"transport_failure": "injected_fixture_exception"},
    )


def test_missing_shared_trace_is_not_silently_repaired_by_api(store):
    seed(store, "normalized")
    with store.engine.begin() as db:
        db.execute(store.time_series.source_traces.table.delete())
    fence(store)
    before = fingerprint(store)
    with observe(store) as observed, TestClient(market.app) as client:
        failed = client.get(BASE + "/bars?venue=KRX")
        intact = client.get(BASE + "?view=summary")
    assert failed.status_code == 422 and intact.status_code == 200
    record(
        "missing-trace-no-read-repair",
        "normalized",
        True,
        observed,
        before,
        fingerprint(store),
        [422, 200],
    )


@pytest.mark.parametrize("operation", ["history", "quotes", "depth"])
def test_real_openalgo_adapter_to_actual_canonical_route(store, monkeypatch, operation):
    seed(store, "normalized")
    fence(store)
    spec = importlib.util.spec_from_file_location(
        "fixture_toss_data", ROOT / "ops/openalgo/toss_data.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("MARKET_CANONICAL_URL", "http://fixture.invalid")
    before = fingerprint(store)
    with observe(store) as observed, TestClient(market.app) as client:

        def bridge(url, *, headers, params, timeout):
            assert url.startswith("http://fixture.invalid/internal/market-data/") and timeout == 5
            return client.get(
                url.removeprefix("http://fixture.invalid"), params=params, headers=headers
            )

        monkeypatch.setattr(httpx, "get", bridge)
        adapter = module.BrokerData("fixture_non_production_internal_key")
        if operation == "history":
            result = adapter.get_history("005930", "TOSS_KR", "D", "2026-06-01", "2026-06-02")
            assert result["close"].tolist() == [105, 112]
        elif operation == "quotes":
            assert adapter.get_quotes("005930", "TOSS_KR")["ltp"] == 112
        else:
            assert adapter.get_depth("005930", "TOSS_KR")["book"][0]["bid"] == 0
    record(
        "adapter-" + operation,
        "normalized",
        True,
        observed,
        before,
        fingerprint(store),
        [200],
        {"transport": "in_process_http_bridge", "upstream_openalgo_flask": "not_executed"},
    )


def test_target_observer_and_database_fence_positive_controls(store):
    with observe(store) as observed:
        put_series(store, "fixture-control", {"rows": ROWS})
    assert set(TARGETS) <= {r["table"] for r in observed}
    before = fingerprint(store)
    fence(store)
    with observe(store) as blocked:
        with pytest.raises(DBAPIError, match="fixture_target_fenced"):
            put_series(
                store,
                "fixture-denied-trace",
                {"rows": [{**ROWS[0], "source_trace": {**TRACE, "unit": "fixture_new"}}]},
            )
        store.time_series.shared_source_traces = False
        with pytest.raises(DBAPIError, match="fixture_target_fenced"):
            put_series(store, "fixture-denied-series", {"rows": ROWS})
        for table in TARGETS:
            for sql in (
                f"UPDATE {table} SET payload=payload",
                f"DELETE FROM {table}",
            ):
                with pytest.raises(DBAPIError, match="fixture_target_fenced"):
                    with store.engine.begin() as db:
                        db.exec_driver_sql(sql)
    assert set(TARGETS) <= {r["table"] for r in blocked}
    assert before == fingerprint(store)
    RESULTS.append(
        {
            "scenario": "positive-controls",
            "target_writes_observed": dict(Counter(r["table"] for r in observed)),
            "target_writes_rejected": dict(Counter(r["table"] for r in blocked)),
            "atomic_rollback_verified": True,
        }
    )
