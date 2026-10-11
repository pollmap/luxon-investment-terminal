"""Independent chart queue: active viewers first, durable full-universe backfill."""

import asyncio
import logging
import os
import shutil
from datetime import UTC, datetime

from .collection import update_collection
from .store import now
from .storage_budget import collection_capacity

log = logging.getLogger("market-history")


def elapsed(stamp):
    try:
        return (datetime.now(UTC) - datetime.fromisoformat(stamp)).total_seconds()
    except (TypeError, ValueError):
        return float("inf")


async def refresh_history(store, provider, code):
    state = store.get("history-state:" + code, {})
    if elapsed(state.get("failed_at")) < min(3600, 60 * 2 ** min(state.get("failures", 0), 5)):
        return "backoff"
    previous = store.get("prices:" + code, {})
    ttl = max(3600, float(os.getenv("MARKET_HISTORY_INTERVAL", "86400")))
    if previous.get("rows") and previous.get('requested_count',0)>=20000 and elapsed(previous.get("received_at")) < ttl:
        return "current"
    try:
        prices = await provider.prices(code)
        if previous.get("rows") != prices["rows"]:
            try:
                await asyncio.to_thread(store.archive, "prices-" + code, prices["rows"])
            except Exception as exc:
                # Raw source and current snapshot already exist; archive failure must not
                # turn a successfully retrieved chart into a failed chart.
                log.warning("History archive %s: %s", code, type(exc).__name__)
        update_collection(
            store,
            code,
            {
                "history": {
                    "status": "ready",
                    "source": "네이버 증권 일봉",
                    "received_at": prices["received_at"],
                    "as_of": prices["rows"][-1]["date"],
                }
            },
        )
        store.put("history-state:" + code, {"failures": 0, "received_at": now()})
        return "ready"
    except Exception as exc:
        store.put(
            "history-state:" + code,
            {
                "failed_at": now(),
                "failures": state.get("failures", 0) + 1,
                "error": type(exc).__name__,
            },
        )
        update_collection(
            store,
            code,
            {
                "history": {
                    "status": "stale" if previous.get("rows") else "error",
                    "source": "네이버 증권 일봉",
                    "received_at": previous.get("received_at"),
                }
            },
        )
        log.warning("History %s: %s", code, type(exc).__name__)
        return "error"


async def history_loop(store, provider):
    while True:
        try:
            codes = list(store.listed_codes())
            known = set(codes)
            cursor = int(store.get("history-progress", {}).get("cursor", 0)) % max(1, len(codes))
            active = [c for c in store.active() if c in known]
            # Do not exhaust the shared VPS while filling years of history.
            capacity = collection_capacity(store, 'price-history')
            free = capacity['free_bytes']
            reserve = max(256, int(os.getenv("MARKET_HISTORY_RESERVE_MB", "768"))) * 1024**2
            targets = list(
                dict.fromkeys(active + (codes[cursor : cursor + 1] if free > reserve else []))
            )
            allowed = not capacity['limited'] and free > reserve
            status = "ready" if allowed else "capacity_limited"
            if allowed:
                for code in targets:
                    await refresh_history(store, provider, code)
            else:
                status = "capacity_limited"
            store.put(
                "history-progress",
                {
                    "cursor": (cursor + 1) % max(1, len(codes)) if allowed else cursor,
                    "universe_total": len(codes),
                    "active": len(active),
                    "status": status,
                    "updated_at": now(),
                },
            )
        except Exception as exc:
            log.warning("History queue: %s", type(exc).__name__)
        await asyncio.sleep(2)
