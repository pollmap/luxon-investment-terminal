"""Observed close-price context, adapted from Coin Desk to exchange trading dates.

No total-return, calendar-completeness or split-adjustment claims are inferred.
The chart, reading table and CSV consume the same result.
"""

from __future__ import annotations

import calendar
import hashlib
import json
from datetime import date
from decimal import Decimal, InvalidOperation

VERSION = "observed-price-context-v1"


def _day(value):
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError("Expected ISO calendar date")
    return date.fromisoformat(value)


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result > 0 else None
    except InvalidOperation:
        return None


def _change(value, base):
    return float(((value / base - 1) * 100).quantize(Decimal("0.000001")))


def _previous_month(month):
    year, number = map(int, month.split("-"))
    return f"{year - (number == 1):04d}-{12 if number == 1 else number - 1:02d}"


def price_context(snapshot, *, code, years=1, start=None, end=None):
    if years not in (0, 1, 3, 5, 10):
        raise ValueError("Unsupported period")
    for bound in (start, end):
        if bound is not None:
            _day(bound)
    if start and end and start > end:
        raise ValueError("Reversed period")
    result = {
        "code": code,
        "version": VERSION,
        "status": "not_collected",
        "series": [],
        "months": [],
        "summary": {},
        "availability": {
            "prices": False,
            "total_return": False,
            "calendar_verified": False,
            "reason": "no_price_observations",
        },
        "selection": {"years": years, "start": start, "end": end},
        "basis": {
            "venue": snapshot.get("venue"),
            "adjustment": snapshot.get("adjustment"),
            "unit": "KRW",
            "dividends_included": False,
        },
        "source_trace": snapshot.get("source_trace"),
        "formula": {
            "return_pct": "100 * (close / first_observed_close - 1)",
            "drawdown_pct": "100 * (close / max_observed_close_since_start - 1)",
            "month_return_pct": "100 * (last_close_in_month / last_close_in_previous_month - 1)",
        },
        "quality": {"excluded_rows": 0, "conflicting_dates": [], "long_gaps": []},
    }
    by_day, conflicts, bases = {}, set(), set()
    for row in snapshot.get("rows", []):
        try:
            day = _day(row.get("date"))
        except (ValueError, TypeError):
            result["quality"]["excluded_rows"] += 1
            continue
        if end and day.isoformat() > end:
            continue
        close = _number(row.get("close"))
        if close is None or row.get("confirmed") is False:
            result["quality"]["excluded_rows"] += 1
            continue
        identity = (
            row.get("venue", snapshot.get("venue")),
            row.get("adjustment", snapshot.get("adjustment")),
            row.get("currency", "KRW"),
        )
        bases.add(identity)
        if day in by_day and by_day[day] != close:
            conflicts.add(day)
        by_day[day] = close
    if conflicts or len(bases) > 1 or any(b[2] != "KRW" for b in bases):
        result["status"] = "incompatible_data"
        result["availability"]["reason"] = "conflicting_prices_or_basis"
        result["quality"]["conflicting_dates"] = sorted(d.isoformat() for d in conflicts)
        return result
    if not by_day:
        return result
    if bases:
        venue, adjustment, currency = next(iter(bases))
        result["basis"].update(venue=venue, adjustment=adjustment, unit=currency)
    if result["basis"]["adjustment"] == "total_return":
        result["status"] = "incompatible_data"
        result["availability"]["reason"] = "price_only_basis_required"
        return result
    ordered = sorted(by_day.items())
    last = ordered[-1][0]
    cutoff = _day(start) if start else date.min
    if not start and years:
        cutoff = last.replace(
            year=last.year - years,
            day=min(last.day, calendar.monthrange(last.year - years, last.month)[1]),
        )
    visible = [(d, p) for d, p in ordered if d >= cutoff]
    if not visible:
        result["availability"]["reason"] = "no_observations_in_period"
        return result

    high, peak_day = visible[0][1], visible[0][0]
    previous = None
    for day, close in visible:
        if close >= high:
            high, peak_day = close, day
        gap = previous is not None and (day - previous).days > 14
        if gap:
            result["quality"]["long_gaps"].append(
                {"after": previous.isoformat(), "before": day.isoformat()}
            )
        result["series"].append(
            {
                "date": day.isoformat(),
                "close": float(close),
                "peak": float(high),
                "peak_date": peak_day.isoformat(),
                "return_pct": _change(close, visible[0][1]),
                "drawdown_pct": _change(close, high),
                "gap_before": gap,
            }
        )
        previous = day

    month_ends = {day.strftime("%Y-%m"): (day, close) for day, close in ordered}
    first_month = visible[0][0].year * 12 + visible[0][0].month - 1
    last_month = visible[-1][0].year * 12 + visible[-1][0].month - 1
    for month_index in range(first_month, last_month + 1):
        year, month0 = divmod(month_index, 12)
        month = f"{year:04d}-{month0 + 1:02d}"
        current, prior = month_ends.get(month), month_ends.get(_previous_month(month))
        value = _change(current[1], prior[1]) if current and prior else None
        result["months"].append(
            {
                "month": month,
                "return_pct": value,
                "start": prior[0].isoformat() if prior else None,
                "end": current[0].isoformat() if current else None,
                "start_close": float(prior[1]) if prior else None,
                "end_close": float(current[1]) if current else None,
                "status": "observed"
                if value is not None
                else "missing_prior_month"
                if current
                else "missing_month",
                "as_of_month": month == last.strftime("%Y-%m"),
                "calendar_verified": False,
            }
        )
    worst = min(result["series"], key=lambda p: p["drawdown_pct"])
    result["summary"] = {
        "start": visible[0][0].isoformat(),
        "end": last.isoformat(),
        "observations": len(visible),
        "return_pct": result["series"][-1]["return_pct"],
        "drawdown_pct": result["series"][-1]["drawdown_pct"],
        "max_drawdown_pct": worst["drawdown_pct"],
        "trough_date": worst["date"],
        "peak_date": worst["peak_date"],
    }
    result["status"] = "ready"
    result["availability"].update(prices=True, reason=None)
    result["revision"] = hashlib.sha256(
        json.dumps(
            {
                "basis": result["basis"],
                "prices": [(d.isoformat(), str(p)) for d, p in ordered],
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()[:24]
    return result
