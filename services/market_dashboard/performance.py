"""Returns on raw prices with a dated share/cash ledger; never double-count dividends."""

from bisect import bisect_right
from decimal import Decimal

from .analysis import numeric
from .bars import normalized_prices


def performance(snapshot, events, *, code, start=None, end=None, reinvest=True):
    prices = [
        p
        for p in normalized_prices(snapshot.get("rows", []))
        if (not start or p["date"] >= start) and (not end or p["date"] <= end)
    ]
    result = {
        "status": "insufficient_history",
        "price_return": None,
        "total_return": None,
        "cagr": None,
        "ledger": [],
        "formula_version": "share-cash-ledger-v2",
        "reinvestment": "first_close_strictly_after_payment",
    }
    if len(prices) < 2:
        return result
    start, end = prices[0]["date"], prices[-1]["date"]
    result.update(start=start, end=end)
    coverage = events.get("coverage") or {}
    if snapshot.get("adjustment") != "raw" or snapshot.get("venue") != events.get("venue"):
        return {**result, "status": "raw_price_basis_required"}
    if (
        not coverage.get("verified")
        or coverage.get("start", "9999") > start
        or coverage.get("end", "") < end
    ):
        return {**result, "status": "corporate_actions_coverage_required"}
    actions, ids, dividend_rights = [], set(), set()
    for event in events.get("rows", []):
        if event.get('superseded_by'):
            continue
        if event.get("security_code") != code:
            return {**result, "status": "security_mismatch"}
        day = (
            event.get("ex_date") if event.get("kind") == "dividend" else event.get("effective_date")
        )
        if not day:
            return {**result, "status": "event_date_required"}
        if not start < day <= end:
            continue
        if not event.get("verified") or not event.get("source_trace") or not event.get("id"):
            return {**result, "status": "event_evidence_required"}
        if event["id"] in ids:
            continue
        ids.add(event["id"])
        if event.get("kind") not in ("split", "dividend"):
            return {**result, "status": "unsupported_corporate_action"}
        if event["kind"] == "split" and (
            numeric(event.get("ratio")) is None or numeric(event["ratio"]) <= 0
        ):
            return {**result, "status": "invalid_split"}
        if event["kind"] == "dividend" and (
            numeric(event.get("amount")) is None
            or numeric(event["amount"]) < 0
            or not event.get("payment_date")
            or event["payment_date"] < day
        ):
            return {**result, "status": "dividend_evidence_required"}
        # Declared/scheduled dates are useful calendar data, but not proof of
        # cash received. Missing status in older records also requires review.
        if event['kind']=='dividend' and event.get('payment_status')!='confirmed':
            return {**result,'status':'payment_confirmation_required'}
        if event['kind']=='dividend':
            # Two filings/sources can describe the same entitlement. Separate
            # special/regular components must be reconciled before calculation.
            right=(event.get('share_class'),event.get('record_date') or day)
            if right in dividend_rights:
                return {**result,'status':'duplicate_dividend_right_requires_review'}
            dividend_rights.add(right)
        actions.append({**event, "day": day})
    # Same-day split/dividend ordering requires an explicit per-share event basis.
    for event in actions:
        if event["kind"] == "dividend" and any(
            a["kind"] == "split" and a["day"] == event["day"] for a in actions
        ):
            return {**result, "status": "same_day_event_basis_required"}
    actions.sort(key=lambda e: e["day"])
    dates = [p["date"] for p in prices]
    principal = Decimal(str(prices[0]["close"]))
    shares = original_shares = Decimal(1)
    cash, receivable = Decimal(0), Decimal(0)
    obligations = []
    index = 0
    for p in prices:
        while index < len(actions) and actions[index]["day"] <= p["date"]:
            event = actions[index]
            if event["kind"] == "split":
                ratio = numeric(event["ratio"])
                shares *= ratio
                original_shares *= ratio
                result["ledger"].append(
                    {
                        "date": event["day"],
                        "kind": "split",
                        "ratio": str(ratio),
                        "event_id": event["id"],
                    }
                )
            else:
                amount = shares * numeric(event["amount"])
                receivable += amount
                pos = bisect_right(dates, event["payment_date"])
                obligations.append(
                    {
                        "payment": event["payment_date"],
                        "reinvest": dates[pos] if pos < len(dates) else None,
                        "amount": amount,
                        "paid": False,
                        "done": False,
                    }
                )
                result["ledger"].append(
                    {
                        "date": event["day"],
                        "kind": "dividend_right",
                        "amount": str(amount),
                        "event_id": event["id"],
                    }
                )
            index += 1
        for obligation in obligations:
            if not obligation["paid"] and obligation["payment"] <= p["date"]:
                cash += obligation["amount"]
                receivable -= obligation["amount"]
                obligation["paid"] = True
            if reinvest and not obligation["done"] and obligation["reinvest"] == p["date"]:
                acquired = obligation["amount"] / Decimal(str(p["close"]))
                shares += acquired
                cash -= obligation["amount"]
                obligation["done"] = True
                result["ledger"].append(
                    {
                        "date": p["date"],
                        "kind": "reinvest",
                        "shares": str(acquired),
                        "price": p["close"],
                    }
                )
    last = Decimal(str(prices[-1]["close"]))
    total = (shares * last + cash + receivable) / principal - 1
    price_return = original_shares * last / principal - 1
    from datetime import date

    duration = (date.fromisoformat(end) - date.fromisoformat(start)).days
    wealth = 1 + total
    return {
        **result,
        "status": "ready",
        "price_return": float(price_return * 100),
        "total_return": float(total * 100),
        "cagr": float((wealth ** (Decimal("365.2425") / duration) - 1) * 100)
        if duration >= 365 and wealth > 0
        else None,
        "receivable": str(receivable),
        "cash": str(cash),
        "shares": str(shares),
        "taxes_and_fees": "excluded",
    }
