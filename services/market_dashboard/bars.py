"""OHLC aggregation uses whole observed periods before clipping the viewport."""

from datetime import date, timedelta

from .analysis import numeric


def normalized_prices(rows):
    result = {}
    for row in rows:
        try:
            day = date.fromisoformat(row["date"])
        except (ValueError, TypeError, KeyError):
            continue
        close = numeric(row.get("close"))
        if close is None or close <= 0:
            continue
        o, h, low, volume = (numeric(row.get(k)) for k in ("open", "high", "low", "volume"))
        valid = (
            all(v is not None and v > 0 for v in (o, h, low))
            and low <= min(o, close) <= max(o, close) <= h
        )
        result[day.isoformat()] = {
            "date": day.isoformat(),
            "close": float(close),
            "open": float(o) if valid else None,
            "high": float(h) if valid else None,
            "low": float(low) if valid else None,
            "volume": float(volume) if volume is not None and volume >= 0 else None,
            "ohlc_valid": bool(valid),
            "confirmed": row.get("confirmed"),
            "source_trace": row.get("source_trace"),
        }
    return sorted(result.values(), key=lambda r: r["date"])


def aggregate_bars(rows, *, interval="day", start=None, end=None, warmup=20):
    if interval not in ("day", "week", "month"):
        raise ValueError("Unsupported bar interval")
    for bound in (start, end):
        if bound:
            date.fromisoformat(bound)
    if start and end and start > end:
        raise ValueError("Start must precede end")
    groups = {}
    for row in normalized_prices(rows):
        day = date.fromisoformat(row["date"])
        key = (
            row["date"]
            if interval == "day"
            else day.replace(day=1).isoformat()
            if interval == "month"
            else (day - timedelta(days=day.weekday())).isoformat()
        )
        groups.setdefault(key, []).append(row)
    result = []
    for key, items in groups.items():
        valid = all(p["ohlc_valid"] for p in items)
        result.append(
            {
                "date": items[-1]["date"],
                "period_start": key,
                "first_observed": items[0]["date"],
                "open": items[0]["open"] if valid else None,
                "high": max(p["high"] for p in items) if valid else None,
                "low": min(p["low"] for p in items) if valid else None,
                "close": items[-1]["close"],
                "volume": sum(p["volume"] for p in items)
                if all(p["volume"] is not None for p in items)
                else None,
                "ohlc_valid": valid,
                "observations": len(items),
                "confirmed": all(p.get("confirmed") is True for p in items),
            }
        )
    # Compute moving average on the full history, including pre-viewport bars.
    for i, row in enumerate(result):
        row["ma20"] = sum(p["close"] for p in result[i - 19 : i + 1]) / 20 if i >= 19 else None
    indices = [
        i
        for i, r in enumerate(result)
        if (not start or r["date"] >= start) and (not end or r["date"] <= end)
    ]
    visible = [result[i] for i in indices]
    preceding = result[max(0, indices[0] - warmup) : indices[0]] if indices else []
    return {
        "rows": visible,
        "warmup": preceding,
        "interval": interval,
        "availability": {
            "prices": bool(visible),
            "candles": bool(visible) and all(r["ohlc_valid"] for r in visible),
        },
        "aggregation": "full_observed_period_before_viewport",
        "status": "ready" if visible else "not_collected",
    }
