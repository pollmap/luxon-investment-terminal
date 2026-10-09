"""Durable, source-by-source collection status for every listed security."""

from __future__ import annotations

import math
import os
from datetime import datetime, timezone

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


def update_collection(store, code, updates=None, *, attempted=False, error=None):
    """Merge durable source results without erasing last-good timestamps on failure."""
    current = store.get("collection:" + code, {})
    sources = {**PENDING, **current.get("sources", {})}
    stamp = now()
    if updates:
        for key, value in updates.items():
            previous = sources.get(key, {"status": "pending"})
            entry = {**previous, **value}
            if entry.get("status") in ("error", "rate_limited"):
                entry["failed_at"] = stamp
            else:
                entry.pop("failed_at", None)
            sources[key] = entry
    result = {
        **current,
        "code": code,
        "sources": sources,
        "attempts": int(current.get("attempts", 0)) + int(attempted),
        "last_attempt_at": stamp if attempted else current.get("last_attempt_at"),
        "last_success_at": stamp if updates and any(
            value.get("status") in ("ready", "partial", "no_data", "unavailable")
            for value in updates.values()
        ) else current.get("last_success_at"),
        "updated_at": stamp,
    }
    if error:
        result["last_error"] = error
    elif updates:
        result.pop("last_error", None)
    store.put("collection:" + code, result)
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
