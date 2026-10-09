"""Small, reusable inputs for analysis endpoints; never load a lifetime price blob."""

from calendar import monthrange
from datetime import date, timedelta

from .fundamental_metrics import security_fundamentals
from .official_views import read_view
from .per_share import security_eps


def security_facts(store, code):
    official = read_view(store, code, "analysis") or {}
    corporation = store.get("dart-corporations", {}).get("codes", {}).get(code)
    detail = store.get("detail:" + code, {})
    rows = detail.get("financials", []) + official.get("per_share_facts", [])
    rows += [
        f
        for b in ("CFS", "OFS")
        for f in security_eps(official.get("eps", {}), code, corporation, basis=b)
    ]
    rows += security_fundamentals(official.get("fundamental_metrics", {}), code, corporation)
    readiness = [
        r
        for r in official.get("fundamental_metrics", {}).get("readiness", [])
        if corporation and r.get("company_id") == corporation
    ]
    return rows, {**official, "metric_input_readiness": readiness}


def analysis_prices(store, code, venue, years, start=None, end=None):
    keys = (
        [f"bars:{code}:통합:day", f"bars:{code}:KRX:day", "prices:" + code]
        if venue == "auto"
        else [f"bars:{code}:{venue}:day"] + (["prices:" + code] if venue == "KRX" else [])
    )
    evidence = store.get("share-basis:" + code, {})
    from .share_basis import aligned_prices, valid_evidence

    if valid_evidence(evidence) and venue in ("auto", "통합"):
        keys.insert(0, f"bars:{code}:통합:raw")
    snapshot = {}
    for key in keys:
        candidate = store.metadata(key, {})
        if candidate.get("row_count") or candidate.get("rows"):
            snapshot = candidate
            break
    selected_venue = snapshot.get("venue") or ("KRX" if venue == "auto" else venue)
    reference = snapshot.get("_series_ref")
    if not reference:
        return aligned_prices(snapshot, evidence, code), selected_venue
    _, last = store.time_series.bounds(reference)
    finish = end or (last[:10] if last else None)
    begin = start
    if not begin and years and finish:
        d = date.fromisoformat(finish)
        begin = d.replace(
            year=d.year - years, day=min(d.day, monthrange(d.year - years, d.month)[1])
        ).isoformat()
    # Include pre-window observations so a previous publication becomes effective
    # before the display window, and the last completed month remains identifiable.
    warmup = (date.fromisoformat(begin) - timedelta(days=600)).isoformat() if begin else None
    rows = store.time_series.rows(reference, start=warmup, end=finish + "T99" if finish else None)
    return aligned_prices({**snapshot, "rows": rows}, evidence, code), selected_venue


def metric_availability(facts, price, security_code=None, *, input_readiness=()):
    from .analysis import numeric
    from .analysis_metrics import definitions
    from .financial_dates import publication_date
    from .valuation_series import comparison_fact_eligible

    result = definitions()
    for definition in result["metrics"]:
        code = definition["code"]
        rows = [
            f
            for f in facts
            if (f.get("metric") or f.get("name")) == code
            and not f.get("estimate")
            and f.get("source_trace")
        ]
        dates = [f["period_end"] for f in rows if f.get("period_end")]
        verified = [
            f
            for f in rows
            if numeric(f.get("value")) is not None
            and f.get("frequency", "annual") in ("annual", "ttm")
            and comparison_fact_eligible(f, code, security_code, price, price.get("venue"))
            and publication_date(f)[0]
        ]
        price_available = bool(price.get("row_count") or price.get("rows"))
        if not price_available:
            verified = []
        comparison_dates = [f["period_end"] for f in verified if f.get("period_end")]
        partial = [
            r
            for r in input_readiness
            if r.get("metric") == code
            and r.get("input_periods", 0) > 0
            and r.get("status") in ("partial_period_inputs", "split_basis_required")
        ]
        partial_status = (
            "split_basis_required"
            if any(r["status"] == "split_basis_required" for r in partial)
            else "partial_period_inputs"
        )
        status = (
            "ready"
            if verified
            else "inputs_not_collected"
            if not rows
            else "prices_not_collected"
            if not price_available
            else "split_basis_required"
            if not price.get("per_share_basis")
            else "comparison_evidence_required"
        )
        if not rows and partial:
            status = partial_status
        reasons = {
            "inputs_not_collected": "계산에 필요한 자료 없음",
            "prices_not_collected": "가격 자료 없음",
            "split_basis_required": "주당 기준 확인 중",
            "comparison_evidence_required": "기간·통화·종목 기준 확인 중",
            "partial_period_inputs": "연간·최근 12개월 자료가 부족해요",
        }
        definition.update(
            available=bool(rows),
            comparison_available=bool(verified),
            start=min(dates) if dates else None,
            end=max(dates) if dates else None,
            frequencies=sorted({f.get("frequency", "annual") for f in rows}),
            comparison_frequencies=sorted({f.get("frequency", "annual") for f in verified}),
            comparison_start=min(comparison_dates) if comparison_dates else None,
            comparison_end=max(comparison_dates) if comparison_dates else None,
            comparison_periods=len(
                {
                    (f.get("basis"), f.get("period_start"), f.get("period_end"), f.get("frequency"))
                    for f in verified
                }
            ),
            status=status,
            reason=reasons.get(status),
            input_readiness=partial,
        )
    return result
