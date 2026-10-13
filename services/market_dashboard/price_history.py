"""Choose one compatible price archive without joining markets or adjustments.

Manifests and indexed edge reads are sufficient for selection. A long unscoped
legacy archive never displaces a scoped OHLC archive, and selection does not
attest that every historical page or every trading session was validated.
"""

import re
from datetime import date

from .bars import normalized_prices

VENUES = ("통합", "KRX", "NXT")
ADJUSTMENTS = ("source", "raw", "split")


def _day(value):
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except (ValueError, TypeError):
        return None


def price_bounds(store, snapshot):
    reference = snapshot.get("_series_ref")
    if reference:
        first, last = store.time_series.bounds(reference)
        return _day(first), _day(last)
    days = [_day(row.get("date") or row.get("time")) for row in snapshot.get("rows", [])]
    days = [day for day in days if day]
    return (min(days), max(days)) if days else (None, None)


def read_prices(store, snapshot, *, start=None, end=None, limit=None, reverse=False):
    """One archive only; include full timestamp observations on the end date."""
    reference = snapshot.get("_series_ref")
    if reference:
        return store.time_series.rows(
            reference, reverse, start=start, end=end + "T99" if end else None, limit=limit
        )
    rows = sorted(snapshot.get("rows", []), key=lambda row: row.get("time") or row.get("date", ""))
    rows = [
        row
        for row in rows
        if (not start or (_day(row.get("date") or row.get("time")) or "") >= start)
        and (not end or (_day(row.get("date") or row.get("time")) or "9999") <= end)
    ]
    if reverse:
        rows.reverse()
    return rows[:limit] if limit is not None else rows


def select_price_history(store, code, *, venue="auto", adjustment="source", prefer_raw=False):
    if venue not in ("auto", *VENUES) or adjustment not in ("auto", *ADJUSTMENTS):
        raise ValueError("Invalid price source selection")
    keys = []
    if prefer_raw and venue in ("auto", "통합"):
        keys.append(f"bars:{code}:통합:raw")
    period = "raw" if adjustment == "raw" else "day"
    for scope in VENUES if venue == "auto" else (venue,):
        keys.append(f"bars:{code}:{scope}:{period}")
    if venue in ("auto", "KRX") and adjustment in ("auto", "source"):
        keys.append("prices:" + code)
    candidates = []
    for position, key in enumerate(dict.fromkeys(keys)):
        snapshot = store.metadata(key, {}) or {}
        if not (snapshot.get("row_count") or snapshot.get("rows")):
            continue
        if any(
            snapshot.get(field) not in (None, code) for field in ("code", "security_code", "symbol")
        ):
            continue
        trace = snapshot.get("source_trace") or {}
        if snapshot.get("currency", trace.get("currency")) not in (None, "KRW"):
            continue
        first, last = price_bounds(store, snapshot)
        if not first or not last:
            continue
        scope, basis = snapshot.get("venue"), snapshot.get("adjustment")
        # A conflicting explicit scope is never silently inferred from its key.
        if venue != "auto" and scope not in (None, venue):
            continue
        expected = "raw" if key.endswith(":raw") else adjustment
        if adjustment in ("raw", "split") and basis != adjustment:
            continue
        if adjustment == "auto" and basis == "raw":
            continue  # An unspecified legacy request is still an adjusted/day read.
        if adjustment != "auto" and basis is not None and basis != expected:
            continue
        edges = read_prices(store, snapshot, limit=1) + read_prices(
            store, snapshot, limit=1, reverse=True
        )
        if any(
            row.get("currency") not in (None, "KRW")
            or row.get("security_code", row.get("symbol")) not in (None, code)
            for row in edges
        ):
            continue
        valid_edges = normalized_prices(edges)
        if not valid_edges:
            continue
        candles = all(row["ohlc_valid"] for row in valid_edges)
        scoped = scope in VENUES and basis in ADJUSTMENTS
        candidates.append(
            {
                "snapshot": snapshot,
                "position": position,
                "first": first,
                "last": last,
                "scoped": scoped,
                "candles": candles,
                "group": (scope, basis),
                "span": (date.fromisoformat(last) - date.fromisoformat(first)).days,
            }
        )
    if not candidates:
        return {}, {"reason": "not_collected", "venue": None, "adjustment": None}
    # Scope/OHLC quality first, then the established source priority. Widen only
    # inside that exact market/adjustment group; never create a mixed series.
    primary = min(
        candidates, key=lambda item: (not item["scoped"], not item["candles"], item["position"])
    )
    compatible = [
        item
        for item in candidates
        if item["group"] == primary["group"]
        and item["scoped"] == primary["scoped"]
        and item["candles"] == primary["candles"]
    ]
    chosen = max(compatible, key=lambda item: (item["span"], item["last"], -item["position"]))
    snapshot = chosen["snapshot"]
    return snapshot, {
        "reason": "compatible_widest" if chosen["scoped"] else "unverified_fallback",
        "venue": snapshot.get("venue"),
        "adjustment": snapshot.get("adjustment"),
        "ohlc_edges_available": chosen["candles"],
    }


def history_coverage(
    store, snapshot, *, requested_start=None, requested_end=None, displayed=(), selection=None
):
    first, last = price_bounds(store, snapshot)
    days = [_day(row.get("date") or row.get("time")) for row in displayed]
    days = [day for day in days if day]
    verification = snapshot.get("history_verification", {})
    count = snapshot.get("row_count", len(snapshot.get("rows", [])))
    verified = (
        isinstance(verification, dict)
        and verification.get("status") == "verified"
        and verification.get("series_revision") == snapshot.get("series_revision")
        and snapshot.get("series_revision") is not None
        and verification.get("rows") == count
        and verification.get("start") == first
        and verification.get("end") == last
        and verification.get("source_references_verified") is True
        and verification.get("calendar_checked") is True
        and bool(re.fullmatch(r"[0-9a-f]{64}", str(verification.get("ordered_sha256", ""))))
    )
    trace = snapshot.get("source_trace") or {}
    verified = verified and bool(trace.get("sha256") or trace.get("source_document_id"))
    terminal = snapshot.get("history_terminal") or {}
    terminal_evidenced = terminal.get("explicit_next_before_null") is True and bool(
        terminal.get("sha256") or terminal.get("source_document_id")
    )
    windows = snapshot.get("gap_windows", [])
    pending = any(window.get("status", "collecting") == "collecting" for window in windows)
    return {
        "requested": {"start": requested_start, "end": requested_end},
        "displayed": {
            "start": min(days) if days else None,
            "end": max(days) if days else None,
            "rows": len(displayed),
        },
        "stored": {"start": first, "end": last, "rows": count},
        "selection": selection
        or {"venue": snapshot.get("venue"), "adjustment": snapshot.get("adjustment")},
        "collection": {
            "status": snapshot.get("status", "not_collected"),
            "history_complete": bool(
                snapshot.get("history_complete", snapshot.get("backfill_complete"))
            ),
            "backfill_complete": bool(snapshot.get("backfill_complete")),
            "terminal_receipt_available": terminal_evidenced,
            "older_pages_pending": bool(snapshot.get("next_before")),
            "gap_pending": pending,
            "gap_status": snapshot.get("gap_status"),
            "head_received_at": snapshot.get("head_received_at"),
        },
        "verification": {
            "status": "verified" if verified else "unverified",
            "reason": None if verified else "period_integrity_not_verified",
        },
    }
