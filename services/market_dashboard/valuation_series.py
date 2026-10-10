"""Daily point-in-time comparisons, with explicit input and adjustment gates."""

import hashlib
import json
from bisect import bisect_left, bisect_right
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal

from .analysis import numeric
from .analysis_metrics import metric_definition
from .bars import normalized_prices
from .financial_dates import publication_date

VERSION = "valuation-series-v5"


def display_periods(rows):
    """Keep revisions in the fact store, but show one verified observation per period.

    Legacy year labels must not compete with a dated, validated filing. Distinct
    actual period starts remain distinct (including changed financial years).
    """
    verified_ends = {(r['period_end'], r['frequency']) for r in rows if not r['reason']}
    selected = {}
    for row in rows:
        end_key = (row['period_end'], row['frequency'])
        if row['reason'] and end_key in verified_ends:
            continue
        key = (row.get('period_start'), *end_key)
        previous = selected.get(key)
        if previous is None or (not row['reason'], row.get('available_at') or '') > (not previous['reason'], previous.get('available_at') or ''):
            selected[key] = row
    return sorted(selected.values(), key=lambda r: (r['period_end'], r.get('period_start') or ''))


def _end(f):
    try:
        if f.get("period_end"):
            return date.fromisoformat(f["period_end"]).isoformat()
        year, month = map(int, f["period"].split("/"))
        return date(year, month, monthrange(year, month)[1]).isoformat()
    except (KeyError, TypeError, ValueError):
        return None


def comparison_period_valid(f, metric):
    if f.get('period_verified') is not True:
        return False
    if metric == 'BPS':
        return bool(_end(f)) and f.get('frequency') != 'ttm'
    try:
        length = (date.fromisoformat(_end(f)) - date.fromisoformat(f['period_start'])).days + 1
        return 350 <= length <= 380
    except (ValueError, TypeError, KeyError):
        return False


def comparison_fact_eligible(f, metric, code, price, venue):
    """Shared structural gate for metric selection and price comparisons."""
    return bool(comparison_period_valid(f, metric)
                and f.get('security_code') == code
                and f.get('currency') == 'KRW'
                and f.get('share_class_verified') is True
                and price.get('per_share_basis')
                and f.get('per_share_basis') == price['per_share_basis']
                and price.get('venue') == venue and venue in ('KRX','NXT','통합'))


def effective_date(f, days, mode, session_closes=None):
    """The same observed session rule serves frequency choice and plotted facts."""
    period_end = _end(f)
    if mode == "latest":
        return period_end
    published, date_only = publication_date(f)
    if not published:
        return None
    day = published[:10]
    close = (session_closes or {}).get(day)
    before_close = bool(not date_only and close and published < close)
    pos = bisect_left(days, day) if before_close else bisect_right(days, day)
    return max(period_end, days[pos]) if pos < len(days) else None


def comparison_date_status(f, price, days, *, mode="as_known", start=None, end=None,
                           session_closes=None):
    """True/False for observed sessions; None means metadata cannot prove overlap.

    Metadata can disprove overlap but never attest an observed trading session.
    Zero and negative metrics remain usable facts; this gate does not value them.
    """
    period_end = _end(f)
    lower = [period_end]
    upper = [(date.fromisoformat(period_end) + timedelta(days=550)).isoformat()]
    for raw, limits in ((start, lower), (end, upper),
                        (price.get('per_share_basis_start'), lower),
                        (price.get('per_share_basis_end'), upper)):
        if raw:
            limits.append(raw)
    if days is None:
        # Bounds are not a calendar. Keep possible intervals unverified without
        # loading the lifetime price series into the metric-menu response.
        for key, limits in (("first_time", lower), ("last_time", upper)):
            try:
                limits.append(date.fromisoformat(price[key][:10]).isoformat())
            except (KeyError, TypeError, ValueError):
                pass
        if mode == "as_known":
            published, date_only = publication_date(f)
            if not published:
                return False
            day = date.fromisoformat(published[:10])
            close = (session_closes or {}).get(day.isoformat())
            # With no session calendar, a timed same-day publication remains
            # unknown; a date-only filing can only affect a later trading day.
            if date_only or (close and published >= close):
                day += timedelta(days=1)
            lower.append(day.isoformat())
        return False if max(lower) > min(upper) else None
    effective = effective_date(f, days, mode, session_closes)
    if effective is None:
        return False
    lower.append(effective)
    pos = bisect_left(days, max(lower))
    return pos < len(days) and days[pos] <= min(upper)


def valuation_series(
    financials,
    price_snapshot,
    *,
    code,
    metric="EPS",
    basis="auto",
    years=5,
    multiple=None,
    start=None,
    end=None,
    mode="as_known",
    frequency="auto",
    interval="week",
    venue="KRX",
    session_closes=None,
):
    definition = metric_definition(metric)
    metric = definition['code']
    if (
        basis not in ("auto", "CFS", "OFS")
        or mode not in ("as_known", "latest")
    ):
        raise ValueError("Invalid analysis selection")
    if (
        years not in (0, 3, 5, 10)
        or frequency not in ("auto", "annual", "ttm")
        or interval not in ("day", "week")
    ):
        raise ValueError("Invalid analysis period")
    factor = numeric(multiple if multiple is not None else definition['default_multiple'])
    if (multiple is not None and factor is None) or (factor is not None and not 0 < factor <= 100):
        raise ValueError("Multiple must be between zero and 100")
    for bound in (start, end):
        if bound:
            date.fromisoformat(bound)
    if start and end and start > end:
        raise ValueError("Start must precede end")
    prices = normalized_prices(price_snapshot.get("rows", []))
    # Existing unknown venue snapshots remain displayable but cannot be used for
    # verified mixed-source calculations. Explicitly different venues are excluded.
    if price_snapshot.get("venue") not in (None, venue):
        prices = []
    if not end and prices:
        end = prices[-1]["date"]
    if not start and end and years:
        d = date.fromisoformat(end)
        start = d.replace(
            year=d.year - years, day=min(d.day, monthrange(d.year - years, d.month)[1])
        ).isoformat()
    candidates = [
        f
        for f in financials
        if (f.get("metric") or f.get("name")) == metric
        and not f.get("estimate")
        and f.get("source_trace")
        and _end(f)
        and numeric(f.get("value")) is not None
    ]
    if basis == "auto":
        basis = "CFS" if any(f.get("basis") == "CFS" for f in candidates) else "OFS"
    candidates = [f for f in candidates if f.get("basis") == basis]
    def period_valid(f):
        return comparison_period_valid(f, metric)

    def eligible(f):
        # The caller must attest the security class and split basis; a company
        # total, or an ordinary-share EPS, is never copied onto a preferred share.
        return comparison_fact_eligible(f, metric, code, price_snapshot, venue)

    days = [p["date"] for p in prices]
    available_candidates = [f for f in candidates if not end or _end(f) <= end]
    latest_period = max((_end(f) for f in available_candidates
                         if f.get('frequency') in ('annual','ttm') and eligible(f)
                         and comparison_date_status(f, price_snapshot, days, mode=mode,
                             start=start, end=end, session_closes=session_closes)), default=None)
    if frequency == "auto":
        frequency = (
            "ttm"
            if definition['supports_ttm']
            and any(f.get("frequency") == "ttm" and _end(f) == latest_period and eligible(f)
                    and comparison_date_status(f, price_snapshot, days, mode=mode,
                        start=start, end=end, session_closes=session_closes)
                    for f in available_candidates)
            else "annual"
        )
    candidates = [f for f in candidates if f.get("frequency") == frequency]
    usable, excluded = [], set()
    annual_table = []
    for f in candidates:
        period_end = _end(f)
        value = numeric(f["value"])
        reason = None
        if not period_valid(f):
            reason = "period_unverified"
        elif f.get("security_code") != code or not f.get("share_class_verified"):
            reason = "share_class_unverified"
        elif not eligible(f):
            reason = "adjustment_or_venue_unverified"
        published, _ = publication_date(f)
        if mode == "as_known" and not published:
            reason = reason or "publication_unverified"
        annual_table.append(
            {
                "period": f.get("period") or period_end,
                "period_end": period_end,
                "period_start": f.get('period_start'),
                "frequency": f.get('frequency'),
                "value": float(value),
                "available_at": published,
                "reason": reason,
                "source_trace": f["source_trace"],
            }
        )
        if reason:
            excluded.add(reason)
            continue
        effective = effective_date(f, days, mode, session_closes)
        if effective is None:
            continue
        usable.append(
            {
                **f,
                "effective": effective,
                "end": period_end,
                "amount": value,
                "publication": published or "",
                "fact_id": f.get("fact_id")
                or hashlib.sha256(json.dumps(f, sort_keys=True, default=str).encode()).hexdigest(),
            }
        )
    usable.sort(
        key=lambda f: (f["effective"], f["end"], f["publication"], str(f.get("filing_id") or ""))
    )
    daily = []
    active = None
    pos = 0
    for p in prices:
        while pos < len(usable) and usable[pos]["effective"] <= p["date"]:
            incoming = usable[pos]
            if active is None or (
                incoming["end"],
                incoming["publication"],
                str(incoming.get("filing_id") or ""),
            ) >= (active["end"], active["publication"], str(active.get("filing_id") or "")):
                active = incoming
            pos += 1
        # Do not carry an old annual/TTM observation indefinitely.
        current = (
            active
            if active
            and (date.fromisoformat(p["date"]) - date.fromisoformat(active["end"])).days <= 550
            else None
        )
        if (price_snapshot.get('per_share_basis_start') and p['date'] < price_snapshot['per_share_basis_start']) or (price_snapshot.get('per_share_basis_end') and p['date'] > price_snapshot['per_share_basis_end']):
            current = None
        value = current["amount"] if current else None
        valid = value is not None and value > 0
        daily.append(
            {
                "date": p["date"],
                "price": p["close"],
                "metric_value": float(value) if value is not None else None,
                "reference_price": float(value * factor) if valid and factor is not None else None,
                "comparison_eligible": bool(valid),
                "multiple": float(Decimal(str(p["close"])) / value) if valid else None,
                "period_end": current["end"] if current else None,
                "fact_id": current["fact_id"] if current else None,
                "reason": "non_positive_metric"
                if value is not None and value <= 0
                else "no_available_fact"
                if current is None
                else None,
            }
        )
    month_ends = {}
    for row in daily:
        # Last actual observation of each month, including nulls. An earlier
        # positive value must not replace a missing/loss-making month end.
        month_ends[row["date"][:7]] = row
    observations = []
    for month, row in month_ends.items():
        year, number = map(int, month.split("-"))
        calendar_end = date(year, number, monthrange(year, number)[1]).isoformat()
        # A following month in the source confirms the preceding month's last
        # observed close. Never substitute an unfinished month or viewport end.
        if (
            month >= (days[-1][:7] if days else "")
            or (end and calendar_end > end)
            or (start and row["date"] < start)
        ):
            continue
        if row["multiple"] is not None and row["multiple"] > 0:
            observations.append(numeric(row["multiple"]))
    observations.sort()
    daily = [
        p for p in daily if (not start or p["date"] >= start) and (not end or p["date"] <= end)
    ]
    representative = None
    if len(observations) >= 24:
        trim = len(observations) // 10
        values = observations[trim : len(observations) - trim]
        representative = float(sum(values) / len(values))
    multiple_source = 'user' if multiple is not None else 'default' if factor is not None else 'historical'
    if factor is None and representative is not None and 0 < representative <= 100:
        factor = numeric(representative)
    if factor is None:
        multiple_source = 'input_required'
        # A multiple can unlock a line only when positive, aligned observations
        # exist in the selected window. Do not ask users to enter a ratio for a
        # missing preferred-share metric or an entirely negative cash flow.
        if any(p['comparison_eligible'] for p in daily):
            excluded.add('multiple_required')
    if not any(p['comparison_eligible'] for p in daily):
        excluded.update(p['reason'] for p in daily if p.get('reason'))
    for p in daily:
        p['reference_price'] = (float(numeric(p['metric_value']) * factor)
                                if p['comparison_eligible'] and factor is not None else None)
    selected = {}
    for p in daily:
        d = date.fromisoformat(p["date"])
        key = (d.isocalendar().year, d.isocalendar().week) if interval == "week" else p["date"]
        selected[key] = p
    series = list(selected.values())
    for p in series:
        p["historical_reference"] = (
            p["metric_value"] * representative
            if representative is not None
            and p["metric_value"] is not None
            and p["metric_value"] > 0
            else None
        )
    revision = hashlib.sha256(
        json.dumps((VERSION, code, metric, basis, frequency, mode, series), sort_keys=True).encode()
    ).hexdigest()[:20]
    has_comparison = any(p["reference_price"] is not None for p in series)
    return {
        "code": code,
        "metric": metric,
        "metric_definition": definition,
        "basis": basis,
        "frequency": frequency,
        "mode": mode,
        "venue": venue,
        "share_basis": {k:price_snapshot.get(k) for k in ('adjustment','per_share_basis','per_share_basis_start','per_share_basis_end')},
        "unit": "원",
        "multiple": float(factor) if factor is not None else None,
        "multiple_source": multiple_source,
        "interval": interval,
        "series": series,
        "periods": display_periods(annual_table),
        "summary": {
            "start": daily[0]["date"] if daily else None,
            "end": daily[-1]["date"] if daily else None,
            "representative_multiple": representative,
            "monthly_observations": len(observations),
            "representative_is_retrospective": True,
        },
        "availability": {
            "prices": bool(series),
            "comparison": has_comparison,
            "reasons": sorted(excluded),
            "forecast": False,
            "dividends": False,
        },
        "revision": revision,
        "formula_version": VERSION,
        "status": "ready" if has_comparison else "price_only" if series else "not_collected",
    }
