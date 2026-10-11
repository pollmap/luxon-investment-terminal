"""Durable, source-by-source collection status for every listed security."""

from __future__ import annotations

import json
import math
import os
from datetime import date, datetime, timezone

from sqlalchemy import select

from .store import now

SOURCES = {
    "quote": "개별 종목 시세 스냅샷",
    "fundamentals": "재무·밸류에이션",
    "flows": "투자자별 수급",
    "history": "과거 주가",
    "filings": "공시",
    "brokers": "증권사별 거래원",
    "official": "DART 기업정보·재무",
    "short_trades": "공매도 거래",
    "short_balance": "공매도 순보유잔고",
    "lending": "대차잔고",
    "credit": "신용융자",
    "stock_loan": "신용대주",
}
PENDING = {key: {"status": "pending", "source": None} for key in SOURCES}

TRADING_SECTIONS = {
    "investor-trading": {"flows": (None, ("individual", "foreign", "institution"))},
    "short-selling": {"short_trades": ("trades", ("volume", "value"))},
    "credit-trades": {"credit": ("credit", ("balance",)),
                      "stock_loan": ("stock_loan", ("balance",))},
    "securities-lending": {"lending": ("lending", ("balance",))},
}


def _locked_collection(store, code, db):
    """Reserve an absent row, then read under the same cross-process write lock.

    SQLite's INSERT starts the write transaction even when the row exists.
    PostgreSQL's reservation covers first-writer races; FOR UPDATE serializes
    merges of an existing row. Never merge through Redis or Store.get caches.
    """
    if store.engine.dialect.name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    elif store.engine.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        raise ValueError("Collection receipts require SQLite or PostgreSQL")
    key = "collection:" + code
    db.execute(insert(store.snapshots).values(key=key, payload="{}", updated_at=now())
               .on_conflict_do_nothing(index_elements=[store.snapshots.c.key]))
    query = select(store.snapshots.c.payload).where(store.snapshots.c.key == key)
    if store.engine.dialect.name == "postgresql":
        query = query.with_for_update()
    return json.loads(db.execute(query).scalar_one())


def _received_at(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp if stamp.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def _generic_source_update(name, previous, value):
    """Generic enrichment cannot certify or relabel a checked trading receipt."""
    endpoint = previous.get("endpoint")
    validated = previous.get("validated_rows")
    if (previous.get("source") == "toss"
            and name in TRADING_SECTIONS.get(endpoint, {})
            and previous.get("period_scope") == "checked_normalized_rows"
            and isinstance(validated, int) and not isinstance(validated, bool)
            and validated > 0):
        return None
    before = _received_at(previous.get("received_at"))
    incoming = _received_at(value.get("received_at"))
    if before is not None and incoming is not None and incoming < before:
        return None
    different_source = (previous.get("source") and value.get("source")
                        and previous["source"] != value["source"])
    # A provider switch must not inherit another provider's dates or proof.
    entry = dict(value) if different_source else {**previous, **value}
    if not different_source:
        for field in ("as_of", "received_at"):
            if entry.get(field) is None and previous.get(field) is not None:
                entry[field] = previous[field]
    return entry


def trading_collection_receipt(endpoint, normalized):
    """Small receipt from checked normalized values, never from source row counts.

    A compact summary can prove only its inspected dates, not the full history.
    Zero is a real value; absent/non-finite values cannot certify a section.
    """
    result = {}
    for name, (field, required) in TRADING_SECTIONS.get(endpoint, {}).items():
        section = normalized.get(field, {}) if field else normalized
        rows = section.get("rows", [])
        dates, complete = [], False
        for row in rows:
            try:
                day = date.fromisoformat(row.get("date", "")).isoformat()
            except (TypeError, ValueError):
                continue
            inspected = required + (("new", "repaid", "ratio")
                                    if name in {"credit", "stock_loan", "lending"} else ())
            valid = [isinstance(row.get(key), (int, float))
                     and not isinstance(row[key], bool) and math.isfinite(row[key])
                     for key in inspected]
            if any(valid):
                dates.append(day)
                complete = complete or all(valid[:len(required)])
        status = section.get("status")
        if dates:
            status = "ready" if complete else "partial"
        elif rows:
            status = "error"
        else:
            status = "no_data" if status in {"ready", "no_data"} else "error"
        result[name] = {"status": status, "validated_rows": len(dates),
                        "period_scope": "checked_normalized_rows"}
        if dates:
            result[name].update(as_of=max(dates), period={"start": min(dates), "end": max(dates)})
        elif rows:
            result[name]["reason"] = "normalized_values_missing"
    return result


def record_trading_collection(store, code, endpoint, receipt=None, *, source=None,
                              failure=None, _connection=None):
    """Merge one projection receipt atomically; retained data is never zero-filled.

    Compact callers use their existing transaction. Legacy callers record after
    output validation; the marker receipt lets the next unchanged page repair a
    failed status write without reading the history again.
    """
    if endpoint not in TRADING_SECTIONS:
        return
    if _connection is None:
        with store.lock, store.engine.begin() as db:
            record_trading_collection(store, code, endpoint, receipt, source=source,
                                      failure=failure, _connection=db)
        store.notify_changed("collection:" + code)
        return
    key = "collection:" + code
    current = _locked_collection(store, code, _connection)
    sources = {**PENDING, **current.get("sources", {})}
    source = source or {}
    stamp = now()
    for name in TRADING_SECTIONS[endpoint]:
        previous = sources.get(name, {})
        retained = previous.get("validated_rows", 0) > 0 or (
            previous.get("status") in {"ready", "partial", "stale"}
            and bool(previous.get("as_of")))
        value = (receipt or {}).get(name)
        if failure is None and value is None:
            continue
        entry = {**previous}
        if failure is not None:
            # Preserve dates/last-good receipt, even when the failing request
            # happened before any successful page in this burst.
            entry.update(status="stale" if retained else "error",
                         failed_at=stamp, last_error_at=stamp, error=type(failure).__name__,
                         source=previous.get("source") or "toss", endpoint=endpoint)
        else:
            observed = source.get("status")
            valid = value.get("validated_rows", 0) > 0
            entry.update(value, source="toss", endpoint=endpoint)
            if observed not in {"ready", "no_data"}:
                entry["status"] = "stale" if valid else (
                    "rate_limited" if observed == "rate_limited" else "error")
            # A successful empty section cannot discard an older last-good
            # collection receipt while the original observations are retained.
            if not valid and retained:
                entry = {**previous, "status": "stale", "endpoint": endpoint}
            entry["backfill_complete"] = (
                source.get("backfill_complete") is True and not source.get("next_until"))
            entry["history_status"] = "complete" if entry["backfill_complete"] else "incomplete"
            entry["history_scope"] = "provider_endpoint_pages"
            entry["source_revision"] = source.get("series_revision")
            received = source.get("head_received_at") or source.get("received_at")
            if valid and entry["status"] in {"ready", "partial"}:
                if received:
                    entry["received_at"] = received
                    entry["last_success_at"] = received
                entry.pop("error", None)
                entry.pop("failed_at", None)
                if "reason" not in value:
                    entry.pop("reason", None)
        entry["checked_at"] = stamp
        sources[name] = entry
    # Preserve the existing global attempt/success/error clocks. They belong to
    # several independent collectors and are not this projection's receipt.
    store.put(key, {**current, "code": code, "sources": sources, "updated_at": stamp},
              _connection=_connection)


def worker_snapshot(worker, *, checked_at=None, max_age_seconds=None):
    """Report heartbeat freshness without mutating durable source or worker results.

    A saved ``collecting`` flag is not evidence that a process is still running.
    Conversely a stale heartbeat does not prove that the saved market data is bad.
    """
    result = dict(worker or {})
    reported = result.get("reported_status", result.get("status", "unknown"))
    checked = checked_at or datetime.now(timezone.utc)
    if isinstance(checked, str):
        checked = datetime.fromisoformat(checked.replace("Z", "+00:00"))
    if checked.tzinfo is None:
        raise ValueError("checked_at must include a timezone")
    heartbeat_source = "process_heartbeat_at" if "process_heartbeat_at" in result else "updated_at"
    if max_age_seconds is None and heartbeat_source == "process_heartbeat_at":
        max_age_seconds = 120
    if max_age_seconds is None:
        try:
            interval = float(os.environ.get("MARKET_UNIVERSE_INTERVAL", "300"))
        except ValueError:
            interval = 300
        if not math.isfinite(interval) or interval <= 0:
            interval = 300
        max_age_seconds = max(900, interval * 3)
    if not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
        raise ValueError("max_age_seconds must be positive and finite")
    stamp = result.get(heartbeat_source)
    age = None
    freshness = "missing"
    if stamp:
        try:
            updated = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            if updated.tzinfo is None:
                raise ValueError("heartbeat must include a timezone")
            difference = (checked - updated).total_seconds()
            # Permit small clock drift but never accept a far-future timestamp as live.
            if difference < -60:
                raise ValueError("heartbeat is in the future")
            age = max(0, difference)
            freshness = "current" if age <= max_age_seconds else "stale"
        except (TypeError, ValueError, OverflowError):
            freshness = "invalid"
    result.update({
        "reported_status": reported,
        "heartbeat_status": freshness,
        "heartbeat_source": heartbeat_source,
        "heartbeat_age_seconds": round(age, 3) if age is not None else None,
        "heartbeat_stale_after_seconds": max_age_seconds,
        "health_checked_at": checked.isoformat(),
    })
    if reported in {"ready", "collecting", "running", "starting"}:
        result["status"] = reported if freshness == "current" else "degraded"
        if freshness != "current":
            result["health_reason"] = "heartbeat_" + freshness
        else:
            result.pop("health_reason", None)
    return result


def update_collection(store, code, updates=None, *, attempted=False, error=None,
                      _connection=None):
    """Merge durable source results without erasing last-good timestamps on failure."""
    if _connection is None:
        with store.lock, store.engine.begin() as db:
            result = update_collection(store, code, updates, attempted=attempted,
                                       error=error, _connection=db)
        store.notify_changed("collection:" + code)
        return result
    current = _locked_collection(store, code, _connection)
    sources = {**PENDING, **current.get("sources", {})}
    stamp = now()
    accepted = {}
    if updates:
        for key, value in updates.items():
            previous = sources.get(key, {"status": "pending"})
            entry = _generic_source_update(key, previous, value)
            if entry is None:
                continue
            if entry.get("status") in ("error", "rate_limited"):
                entry["failed_at"] = stamp
            else:
                entry.pop("failed_at", None)
            sources[key] = entry
            accepted[key] = value
    result = {
        **current,
        "code": code,
        "sources": sources,
        "attempts": int(current.get("attempts", 0)) + int(attempted),
        "last_attempt_at": stamp if attempted else current.get("last_attempt_at"),
        "last_success_at": stamp if accepted and any(
            value.get("status") in ("ready", "partial", "no_data", "unavailable")
            for value in accepted.values()
        ) else current.get("last_success_at"),
        "updated_at": stamp,
    }
    if error:
        result["last_error"] = error
    elif accepted:
        result.pop("last_error", None)
    store.put("collection:" + code, result, _connection=_connection)
    return result


def collection_snapshot(store, rows, worker=None):
    """Summarize actual observations, not assumed completeness, over the listed universe."""
    stored = store.list_prefix("collection:")
    by_code = {key.split(":", 1)[1]: value for key, value in stored.items()}
    totals = {
        source: {
            "label": label,
            "ready": 0,
            "partial": 0,
            "stale": 0,
            "no_data": 0,
            "unavailable": 0,
            "credentials_missing": 0,
            "error": 0,
            "rate_limited": 0,
            "pending": 0,
        }
        for source, label in SOURCES.items()
    }
    visited = 0
    attempted = 0
    for row in rows:
        record = by_code.get(row["code"], {})
        if record.get("attempts", 0):
            attempted += 1
        if any(
            state.get("status") != "pending"
            for state in record.get("sources", {}).values()
        ):
            visited += 1
        for source in SOURCES:
            status = record.get("sources", {}).get(source, {}).get("status", "pending")
            if status not in totals[source]:
                status = "pending"
            totals[source][status] += 1
    total = len(rows)
    for values in totals.values():
        values["covered"] = values["ready"] + values["partial"] + values["stale"]
        values["total"] = total
    return {
        "total": total,
        "attempted": attempted,
        "visited": visited,
        "remaining": max(0, total - visited),
        "sources": totals,
        "updated_at": now(),
        "universe_received_at": store.get("universe", {}).get("received_at"),
        "worker": worker_snapshot(worker if worker is not None else store.get("worker", {})),
        "scope": (
            "거래소 상장 보통주·우선주 목록 기준 · ETF/ETN 제외 · "
            "자료별 원천 범위와 갱신주기 상이"
        ),
    }
