"""Measure one live worker interval without reading credentials or API params.

Run inside the market worker container. Reports provider counters, durable queue
changes, PostgreSQL tuple/statement deltas and cgroup resource snapshots.
"""
import argparse
import json
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, text

from services.market_dashboard.store import Store
from services.market_dashboard.toss_backfill import KINDS
from services.market_dashboard.toss_contract import PUBLIC_GET_PATHS


def timestamp():
    return datetime.now(UTC).isoformat()


def request_snapshot(store):
    result = {}
    for path in PUBLIC_GET_PATHS:
        value = store.get("toss-api:" + path, {}, use_redis=False)
        metrics = value.get("metrics") or {}
        if metrics:
            result[path] = metrics
    return result


def progress_snapshot(store):
    value = store.get("toss-backfill-progress", {}, use_redis=False)
    return {key: value.get(key) for key in (
        "attempts", "cycles", "next_code", "last_code", "last_kind",
        "last_result", "status", "updated_at",
    )}


def jobs_snapshot(store, codes):
    with store.read_connection() as db:
        rows = db.execute(
            select(store.snapshots.c.key, store.snapshots.c.payload)
            .where(store.snapshots.c.key.like("toss-backfill:%"))
        ).all()
    counts = {kind: Counter() for kind in KINDS}
    reported_rows = Counter()
    complete = set()
    errors = 0
    for key, payload in rows:
        parts = key.split(":", 2)
        if len(parts) != 3 or parts[1] not in codes or parts[2] not in counts:
            continue
        kind = parts[2]
        state = json.loads(payload)
        status = state.get("status", "missing")
        counts[kind][status] += 1
        try:
            reported_rows[kind] += max(0, int(state.get("rows", 0) or 0))
        except (TypeError, ValueError):
            pass
        cursor = state.get("next_before") if kind in ("history", "raw-history") else state.get("next_until")
        if state.get("backfill_complete") and not cursor:
            complete.add((parts[1], kind))
        errors += status == "error"
    return {
        "registered": sum(sum(kind.values()) for kind in counts.values()),
        "complete_pairs": len(complete),
        "errors": errors,
        "by_kind": {kind: dict(value) for kind, value in counts.items()},
        "reported_rows_by_kind": dict(reported_rows),
    }


def database_snapshot(store):
    result = {"dialect": store.engine.dialect.name}
    if store.engine.dialect.name != "postgresql":
        return result
    with store.engine.connect() as db:
        result["table"] = db.execute(text(
            "SELECT n_tup_ins, n_tup_upd, n_tup_del "
            "FROM pg_stat_user_tables WHERE relname='market_time_series' "
            "AND schemaname=current_schema()"
        )).mappings().first()
        reset = db.execute(text(
            "SELECT stats_reset FROM pg_stat_database WHERE datname=current_database()"
        )).scalar()
        result["database_stats_reset"] = reset.isoformat() if reset else None
        try:
            exists = db.execute(text("SELECT to_regclass('pg_stat_statements')")).scalar()
            if exists:
                row = db.execute(text(
                    "SELECT COALESCE(sum(calls),0) AS calls, "
                    "COALESCE(sum(total_exec_time),0) AS total_exec_time_ms "
                    "FROM pg_stat_statements "
                    "WHERE dbid=(SELECT oid FROM pg_database WHERE datname=current_database()) "
                    "AND (query ILIKE '%market_snapshots%' "
                    "OR query ILIKE '%market_time_series%' "
                    "OR query ILIKE '%market_jobs%')"
                )).mappings().first()
                result["stock_table_statements"] = dict(row)
        except Exception as exc:
            result["statement_metrics_unavailable"] = type(exc).__name__
    return result


def resource_snapshot():
    result = {}
    for name, path in (("memory_current_bytes", "/sys/fs/cgroup/memory.current"),
                       ("cpu_usage_usec", "/sys/fs/cgroup/cpu.stat")):
        try:
            content = Path(path).read_text().strip()
            result[name] = (int(content) if name == "memory_current_bytes"
                            else int(dict(line.split() for line in content.splitlines()).get("usage_usec", 0)))
        except (OSError, ValueError):
            result[name] = None
    return result


def sample(store):
    return {
        "at": timestamp(),
        "requests": request_snapshot(store),
        "backfill": progress_snapshot(store),
        "jobs": jobs_snapshot(store, set(store.listed_codes())),
        "database": database_snapshot(store),
        "resources": resource_snapshot(),
    }


def subtract_counters(before, after):
    names = (
        "request_count", "successful_responses", "http_errors", "network_errors",
        "protocol_errors", "rate_limit_responses", "auth_retries", "rows_received",
        "response_bytes", "upstream_ms_total", "budget_wait_ms_total", "raw_archive_ms_total",
        "time_series_write_ms_total", "time_series_inserted_rows", "time_series_updated_rows",
    )
    result = {}
    invalid = []
    for path in sorted(set(before) | set(after)):
        left, right = before.get(path), after.get(path)
        if not right:
            continue
        if not left:
            if right.get("request_count", 0):
                invalid.append(path)
            continue
        if left.get("session_id") != right.get("session_id"):
            invalid.append(path)
            continue
        result[path] = {key: round(float(right.get(key, 0) or 0) - float(left.get(key, 0) or 0), 3)
                        for key in names}
    return result, invalid


def table_counter_delta(start, end, key):
    left = (start.get("table") or {}).get(key) or 0
    right = (end.get("table") or {}).get(key) or 0
    return max(0, right - left)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=600)
    args = parser.parse_args()
    if not 60 <= args.seconds <= 3600:
        raise SystemExit("Measurement must be between 60 and 3600 seconds")
    store = Store()
    start = sample(store)
    time.sleep(args.seconds)
    end = sample(store)
    elapsed = (datetime.fromisoformat(end["at"]) - datetime.fromisoformat(start["at"])).total_seconds()
    requests, invalid_sessions = subtract_counters(start["requests"], end["requests"])
    totals = {key: round(sum(value[key] for value in requests.values()), 3)
              for key in next(iter(requests.values()), {})}
    progress_delta = {
        key: (end["backfill"].get(key) or 0) - (start["backfill"].get(key) or 0)
        for key in ("attempts", "cycles")
    }
    db_start, db_end = start["database"].get("table") or {}, end["database"].get("table") or {}
    statement_start = start["database"].get("stock_table_statements") or {}
    statement_end = end["database"].get("stock_table_statements") or {}
    output = {
        "started_at": start["at"], "ended_at": end["at"], "elapsed_seconds": round(elapsed, 3),
        "rates_per_minute": {
            "successful_provider_requests": round(totals.get("successful_responses", 0) / elapsed * 60, 3),
            "provider_response_rows": round(totals.get("rows_received", 0) / elapsed * 60, 3),
            "instrumented_time_series_inserts": round(totals.get("time_series_inserted_rows", 0) / elapsed * 60, 3),
            "time_series_write_path_ms": round(totals.get("time_series_write_ms_total", 0) / elapsed * 60, 3),
            "new_time_series_rows": round(table_counter_delta(
                start["database"], end["database"], "n_tup_ins") / elapsed * 60, 3),
            "updated_time_series_rows": round(table_counter_delta(
                start["database"], end["database"], "n_tup_upd") / elapsed * 60, 3),
            "completed_job_pairs": round((end["jobs"]["complete_pairs"]
                - start["jobs"]["complete_pairs"]) / elapsed * 60, 3),
        },
        "universe": len(store.listed_codes()),
        "request_delta_by_endpoint": requests,
        "request_totals": totals,
        "incomplete_or_restarted_metric_endpoints": invalid_sessions,
        "backfill_progress_delta": progress_delta,
        "backfill_state_start": start["backfill"], "backfill_state_end": end["backfill"],
        "job_state_start": start["jobs"], "job_state_end": end["jobs"],
        "completed_job_pair_delta": end["jobs"]["complete_pairs"] - start["jobs"]["complete_pairs"],
        "reported_job_rows_delta_by_kind": {
            kind: end["jobs"]["reported_rows_by_kind"].get(kind, 0)
            - start["jobs"]["reported_rows_by_kind"].get(kind, 0)
            for kind in KINDS
        },
        "series_tuple_delta": {key: (db_end.get(key) or 0) - (db_start.get(key) or 0)
                               for key in ("n_tup_ins", "n_tup_upd", "n_tup_del")},
        "postgres_statement_delta": {
            "calls": (statement_end.get("calls", 0) or 0) - (statement_start.get("calls", 0) or 0),
            "total_exec_time_ms": round((statement_end.get("total_exec_time_ms", 0) or 0)
                                         - (statement_start.get("total_exec_time_ms", 0) or 0), 3),
            "scope": "shared database queries mentioning market tables; includes concurrent service queries and this audit",
        } if statement_start and statement_end else None,
        "resource_start": start["resources"], "resource_end": end["resources"],
        "stats_reset_unchanged": start["database"].get("database_stats_reset") == end["database"].get("database_stats_reset"),
        "interpretation": [
            "rows_received counts rows in successful Toss responses, not newly stored rows",
            "PostgreSQL tuple inserts/updates are service-table writes and may include concurrent users",
            "an endpoint with a restarted or previously absent metrics session is excluded from exact request deltas",
            "attempts are queue visits and are not presented as successful throughput",
        ],
    }
    print(json.dumps(output, ensure_ascii=False, default=str))
    store.engine.dispose()


if __name__ == "__main__":
    main()
