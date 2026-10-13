"""Strict candle-page contracts and independent resumable gap windows."""

from datetime import UTC, datetime


def candle_clock(value):
    if not isinstance(value, str) or not value:
        raise ValueError("Candle cursor must be an offset timestamp")
    try:
        clock = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("Candle cursor must be an offset timestamp") from None
    if clock.tzinfo is None:
        raise ValueError("Candle cursor timezone is required")
    return clock.astimezone(UTC)


def archive_clock(value):
    """Legacy daily manifests can retain dates; do not infer their market."""
    if isinstance(value, str) and len(value) == 10:
        return candle_clock(value + "T23:59:59+09:00")
    return candle_clock(value)


def validate_page(data, code, before, seen, normalize, *, allow_unknown_cursor=False):
    if not isinstance(data, dict) or not isinstance(data.get("candles"), list):
        raise ValueError("Candle records must be an array")
    if data.get("symbol") and data["symbol"] != code:
        raise ValueError("Candle security mismatch")
    if "nextBefore" not in data and not allow_unknown_cursor:
        raise ValueError("Candle page needs explicit continuation status")
    cursor = data.get("nextBefore")
    if cursor is not None:
        if cursor == before or cursor in seen:
            raise ValueError("Candle pagination did not advance")
        target = candle_clock(cursor)
        if before and target >= candle_clock(before):
            raise ValueError("Candle pagination did not advance")
    rows = normalize(data["candles"])
    if before and any(candle_clock(row["time"]) > candle_clock(before) for row in rows):
        raise ValueError("Candle page exceeds requested upper bound")
    if cursor is not None:
        if not rows:
            raise ValueError("Empty candle page must be terminal")
        if target > min(candle_clock(row["time"]) for row in rows):
            raise ValueError("Candle cursor skips observations inside its page")
    return rows, cursor


def pending_gaps(snapshot):
    return [dict(window) for window in snapshot.get("gap_windows", [])]


def gap_fields(windows):
    active = [window for window in windows if window.get("status", "collecting") == "collecting"]
    blocked = any(window.get("status") == "source_gap" for window in windows)
    return {
        "gap_windows": windows,
        "gap_next_before": active[0].get("next_before") if active else None,
        "gap_status": "collecting" if active else "source_gap" if blocked else "closed",
    }


def terminal_receipt(evidence, *, before, rows, received_at, context=None):
    """An explicit null cursor is a receipt, not a whole-period validation."""
    from .stream_evidence import message_digest
    if evidence.get('source') != 'toss' or not message_digest(evidence):
        return None
    return {
        **(context or {}),
        "explicit_next_before_null": True,
        "received_at": received_at,
        "request_before": before,
        "rows": len(rows),
        "source_document_id": evidence.get("source_document_id"),
        "sha256": evidence.get("sha256"),
        "source": evidence.get("source"),
    }
