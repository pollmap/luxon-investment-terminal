"""Deterministic, source-backed comparison and full-universe exploration."""

import csv
import io
import math
from calendar import monthrange
from datetime import date

from .metrics import METRICS


def sector_summary(rows):
    groups = {}
    for row in rows:
        name = row.get("sector")
        if not name:
            continue
        group = groups.setdefault(
            name, {"name": name, "count": 0, "covered": 0, "sum": 0, "value": 0, "cap": 0}
        )
        group["count"] += 1
        if row.get("change") is not None:
            group["covered"] += 1
            group["sum"] += row["change"]
        group["value"] += row.get("value") or 0
        group["cap"] += row.get("cap") or 0
    return sorted(
        [
            {
                **{k: v for k, v in g.items() if k != "sum"},
                "change": g["sum"] / g["covered"] if g["covered"] else None,
            }
            for g in groups.values()
        ],
        key=lambda g: (-g["value"], g["name"]),
    )


def compare_prices(stocks, histories, months=12):
    """Align exact common dates; never forward-fill missing prices or synthesize returns."""
    series, pending = [], []
    indexed = {}
    for stock in stocks:
        prices = histories.get(stock["code"]) or {}
        valid = {
            p["date"]: p["close"]
            for p in prices.get("rows", [])
            if isinstance(p.get("close"), (int, float))
            and math.isfinite(p["close"])
            and p["close"] > 0
        }
        if len(valid) < 2:
            pending.append(stock["code"])
        else:
            indexed[stock["code"]] = valid
    dates = sorted(set.intersection(*(set(p) for p in indexed.values()))) if indexed else []
    if dates:
        end = date.fromisoformat(dates[-1])
        start_month = end.year * 12 + end.month - 1 - months
        year, month = divmod(start_month, 12)
        cutoff = date(year, month + 1, min(end.day, monthrange(year, month + 1)[1]))
        dates = [day for day in dates if day >= cutoff.isoformat()]
    if len(dates) >= 2:
        for stock in stocks:
            code = stock["code"]
            if code not in indexed:
                continue
            prices = indexed[code]
            base = prices[dates[0]]
            series.append(
                {
                    "code": code,
                    "name": stock["name"],
                    "logo_url": stock.get("logo_url"),
                    "points": [
                        {"date": day, "value": round((prices[day] / base - 1) * 100, 6)}
                        for day in dates
                    ],
                    "source_trace": {
                        **(histories[code].get("source_trace") or {}),
                        "unit": "%",
                        "period": f"{dates[0]}/{dates[-1]}",
                        "formula": "(close / first_common_close - 1) * 100",
                        "input_fact_ids": [f"prices:{code}:{day}:close" for day in dates],
                    },
                }
            )
    return {
        "series": series,
        "pending": pending,
        "start": dates[0] if dates else None,
        "end": dates[-1] if dates else None,
        "status": "ready"
        if len(series) == len(stocks) and stocks
        else "partial"
        if series
        else "collecting"
        if pending
        else "no_overlap",
    }


def screener_csv(rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(
        ["종목코드", "종목명", "시장", "업종"]
        + [f"{m['label']} ({m['unit']})" for m in METRICS.values()]
    )

    def safe(value):
        # A text field must never become an Excel formula. Leading zeros remain in the file.
        text = str(value or "")
        return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text

    for row in rows:
        writer.writerow(
            [safe(row.get(k)) for k in ("code", "name", "market", "sector")]
            + [row.get(k) if row.get(k) is not None else "" for k in METRICS]
        )
    return ("\ufeff" + stream.getvalue()).encode("utf-8")
