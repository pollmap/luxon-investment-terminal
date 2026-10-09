"""Deterministic comparisons. Missing evidence never becomes a zero return."""

from __future__ import annotations

import json
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from .financial_dates import publication_date


def numeric(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def valuation_history(financials, prices, *, metric="EPS", basis="CFS", multiple=15, years=10):
    if metric not in ("EPS", "BPS") or basis not in ("CFS", "OFS"):
        raise ValueError("Unsupported valuation basis")
    factor = numeric(multiple)
    if factor is None or not 0 < factor <= 100:
        raise ValueError("Multiple must be greater than zero and at most 100")
    prices = sorted(
        (p for p in prices if numeric(p.get("close")) is not None and numeric(p["close"]) > 0),
        key=lambda p: p["date"],
    )
    facts = {}
    for f in financials:
        if (
            f.get("name") != metric
            or f.get("basis") != basis
            or f.get("frequency") != "annual"
            or f.get("estimate")
        ):
            continue
        if not f.get("source_trace"):
            continue
        try:
            year, month = map(int, f["period"].split("/"))
            end = date(year, month, monthrange(year, month)[1])
        except (KeyError, ValueError, TypeError):
            continue
        previous = facts.get(end)
        if previous is None or str(f.get("published_at") or "") > str(
            previous.get("published_at") or ""
        ):
            facts[end] = f
    rows = []
    if facts:
        cutoff = max(facts).year - years + 1
        for end, f in sorted(facts.items()):
            if end.year < cutoff:
                continue
            value = numeric(f.get("value"))
            close = next(
                (
                    p
                    for p in reversed(prices)
                    if (end - timedelta(days=10)).isoformat() <= p["date"] <= end.isoformat()
                ),
                None,
            )
            price = numeric(close["close"]) if close else None
            valid = value is not None and value > 0
            rows.append(
                {
                    "period": f["period"],
                    "metric_value": float(value) if value is not None else None,
                    "price": float(price) if price is not None else None,
                    "price_date": close["date"] if close else None,
                    "reference_price": float(value * factor) if valid else None,
                    "multiple": float(price / value) if valid and price is not None else None,
                    "source_trace": f["source_trace"],
                }
            )
    multiples = [r["multiple"] for r in rows if r["multiple"] is not None]
    return {
        "metric": metric,
        "basis": basis,
        "unit": "원",
        "multiple": float(factor),
        "rows": rows,
        "mean_multiple": sum(multiples) / len(multiples) if multiples else None,
        "method": "결산월 말 주가와 연간 실적 비교 · 현재 확보한 공시 기준",
        "point_in_time": False,
        "status": "ready" if len(rows) >= 2 else "insufficient_history",
    }


def scenario(*, base, growth, multiple, years):
    values = [numeric(v) for v in (base, growth, multiple)]
    if any(v is None for v in values) or not 1 <= years <= 5:
        raise ValueError("Invalid scenario inputs")
    base, growth, multiple = values
    if not 0 < base <= Decimal("1e9") or not -100 < growth <= 100 or not 0 < multiple <= 100:
        raise ValueError("Scenario inputs outside supported range")
    rows = []
    for year in range(1, years + 1):
        value = base * (1 + growth / 100) ** year
        rows.append(
            {"year": year, "metric_value": float(value), "reference_price": float(value * multiple)}
        )
    return {
        "rows": rows,
        "kind": "user_assumption",
        "formula": "입력 지표 × (1 + 입력 성장률)^연수 × 입력 배수",
    }


def trailing_twelve_months(facts, *, as_of=None):
    """Accept only dated, verified duration facts; never sum instant/per-share/ratio facts.

    Cumulative facts are subtracted only inside the same fiscal year and accounting
    group. Duplicate periods use the latest filing publication, preserving its trace.
    """
    groups = {}
    for f in facts:
        if (
            not f.get("period_verified")
            or f.get("kind") != "duration"
            or f.get("estimate")
            or not f.get("source_trace")
        ):
            continue
        published, date_only = publication_date(f)
        if as_of and (not published or published[:10] >= str(as_of)[:10]):
            # Date-only disclosures cannot be used on their publication date.
            continue
        amount = numeric(f.get("exact_value", f.get("value")))
        try:
            start, end = (date.fromisoformat(f[k]) for k in ("period_start", "period_end"))
            fiscal = date.fromisoformat(f.get("fiscal_start", f["period_start"]))
        except (ValueError, TypeError, KeyError):
            continue
        if amount is None or start > end or fiscal > start:
            continue
        accounting = tuple(str(f.get(k) or "") for k in ("account_id", "basis", "unit", "currency"))
        if not all(accounting):
            continue
        key = (
            *accounting,
            str(f.get("company_id") or f.get("entity_id") or ""),
            json.dumps(f.get("dimensions") or {}, sort_keys=True),
            str(f.get("scope") or ""),
        )
        periods = groups.setdefault(key, {})
        period = (start, end, fiscal)

        def version(item):
            stamp, _ = publication_date(item)
            return (
                stamp or "",
                str(item.get("filing_id") or item.get("source_trace", {}).get("filing_id") or ""),
            )

        if period not in periods or version(f) > version(periods[period]):
            periods[period] = {**f, "_amount": amount}
    output = []
    for key, periods in groups.items():
        quarters = {}
        for (start, end, fiscal), f in sorted(periods.items(), key=lambda item: item[0][1]):
            duration = (end - start).days + 1
            amount, traces = f["_amount"], [f["source_trace"]]
            if duration > 100 and f.get("cumulative") and start == fiscal:
                previous = [
                    (e, v)
                    for (s, e, fy), v in periods.items()
                    if s == start and fy == fiscal and e < end and v.get("cumulative")
                ]
                if previous:
                    prior_end, prior = max(previous, key=lambda pair: pair[0])
                    start = prior_end + timedelta(days=1)
                    amount -= prior["_amount"]
                    traces.append(prior["source_trace"])
                else:
                    # Annual statements plus three dated standalone quarters give Q4.
                    # No assumed January start or calendar-quarter dates are used.
                    for quarter in sorted(quarters.values(), key=lambda q: q["start"]):
                        if quarter["start"] == start and quarter["end"] < end:
                            start = quarter["end"] + timedelta(days=1)
                            amount -= quarter["value"]
                            traces.extend(quarter["trace"])
            if not 70 <= (end - start).days + 1 <= 100:
                continue
            quarters[end] = {"start": start, "end": end, "value": amount, "trace": traces}
        ordered = sorted(quarters.values(), key=lambda q: q["end"])
        chosen = ordered[-4:]
        if len(chosen) != 4 or any(
            a["end"] + timedelta(days=1) != b["start"]
            for a, b in zip(chosen, chosen[1:], strict=False)
        ):
            continue
        if not 350 <= (chosen[-1]["end"] - chosen[0]["start"]).days + 1 <= 380:
            continue
        output.append(
            {
                "account_id": key[0],
                "name": next(iter(periods.values())).get("name", key[0]),
                "basis": key[1],
                "unit": key[2],
                "currency": key[3],
                "company_id": key[4],
                "dimensions": json.loads(key[5]),
                "scope": key[6],
                "period_start": chosen[0]["start"].isoformat(),
                "period_end": chosen[-1]["end"].isoformat(),
                "value": float(sum(q["value"] for q in chosen)),
                "exact_value": str(sum(q["value"] for q in chosen)),
                "frequency": "ttm",
                "source_trace": {
                    "formula": "sum(verified standalone quarters)",
                    "inputs": [t for q in chosen for t in q["trace"]],
                },
            }
        )
    return {"rows": output, "status": "ready" if output else "period_evidence_required"}
