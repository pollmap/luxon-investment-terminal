"""Read-only local snapshot audit; existence does not imply complete/current coverage."""

import argparse
import csv
import json
import sqlite3
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path

from services.market_dashboard.analysis import trailing_twelve_months


def audit(database, output):
    today = date.today()
    connection = sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)
    universe = json.loads(
        connection.execute("SELECT payload FROM market_snapshots WHERE key='universe'").fetchone()[
            0
        ]
    )
    securities = {r["code"]: r for r in universe["rows"]}
    result = {
        code: {"code": code, "name": r["name"], "market": r["market"]}
        for code, r in securities.items()
    }
    required_years = {str(y) for y in range(today.year - 5, today.year)}
    fields = [
        "price_rows",
        "price_first",
        "price_last",
        "ten_year_span_observed",
        "financial_years",
        "five_years_observed",
        "flows_rows",
        "brokers_rows",
        "filings_rows",
        "filings_total",
        "filings_complete_response",
        "dated_ttm_facts",
        "verified_duration_facts",
        "xbrl_facts",
        "ttm_calculated_accounts",
        "verified_per_share_facts",
        "raw_daily_ohlc_rows",
        "krx_raw_daily_ohlc_rows",
        "five_minute_ohlc_rows",
        "events_rows",
        "events_coverage_verified",
        "short_trades_rows",
        "short_balance_rows",
        "lending_rows",
        "credit_rows",
        "stock_loan_rows",
    ]
    for row in result.values():
        row.update({key: None for key in fields})

    def count(value):
        return len(value.get("rows", [])) if isinstance(value, dict) else len(value or [])

    prefixes = (
        "prices",
        "detail",
        "official",
        "filings",
        "short-selling",
        "financing",
        "bars",
        "krx-bars",
        "events",
    )
    for key, payload in connection.execute(
        "SELECT key,payload FROM market_snapshots WHERE key LIKE '%:%'"
    ):
        prefix, code = key.split(":", 1)
        suffix = code.split(":")[1:]
        code = code.split(":")[0]
        if prefix not in prefixes or code not in result:
            continue
        value = json.loads(payload)
        row = result[code]
        if prefix == "prices":
            dates = sorted(r["date"] for r in value.get("rows", []) if r.get("date"))
            row.update(
                price_rows=len(dates),
                price_first=dates[0] if dates else None,
                price_last=dates[-1] if dates else None,
                ten_year_span_observed=bool(
                    dates and dates[0] <= f"{today.year - 10}-{today.month:02}-{today.day:02}"
                ),
            )
        elif prefix == "detail":
            years = sorted(
                {
                    str(f.get("period", ""))[:4]
                    for f in value.get("financials", [])
                    if f.get("frequency") == "annual"
                    and not f.get("estimate")
                    and f.get("value") is not None
                }
            )
            row.update(
                financial_years="|".join(years),
                five_years_observed=required_years.issubset(years),
                flows_rows=count(value.get("flows")),
                brokers_rows=count(value.get("brokers")),
            )
        elif prefix == "official":
            dated = value.get("period_facts", [])
            xbrl = value.get("xbrl_facts", [])
            row.update(
                dated_ttm_facts=len(dated),
                xbrl_facts=len(xbrl),
                verified_duration_facts=sum(
                    f.get("period_verified") is True and f.get("kind") == "duration"
                    for f in dated + xbrl
                ),
                ttm_calculated_accounts=len(trailing_twelve_months(dated + xbrl)["rows"]),
                verified_per_share_facts=sum(
                    f.get("period_verified") is True
                    and f.get("share_class_verified") is True
                    and f.get("security_code") == code
                    and bool(f.get("per_share_basis"))
                    for f in value.get("per_share_facts", [])
                ),
            )
        elif prefix in ("bars", "krx-bars") and len(suffix) == 2:
            venue, kind = suffix
            if venue == "KRX" and kind in ("raw", "5min"):
                field = "raw_daily_ohlc_rows" if kind == "raw" else "five_minute_ohlc_rows"
                if prefix == "krx-bars":
                    field = "krx_raw_daily_ohlc_rows"
                row[field] = sum(
                    all(
                        isinstance(b.get(k), (int, float)) and b[k] > 0
                        for k in ("open", "high", "low", "close")
                    )
                    for b in value.get("rows", [])
                )
        elif prefix == "events":
            row.update(
                events_rows=count(value),
                events_coverage_verified=value.get("coverage", {}).get("verified") is True,
            )
        elif prefix == "filings":
            received = len(value.get("rows", []))
            total = value.get("total")
            row.update(
                filings_rows=received,
                filings_total=total,
                filings_complete_response=received >= total if isinstance(total, int) else None,
            )
        elif prefix == "short-selling":
            row.update(
                short_trades_rows=count(value.get("trades")),
                short_balance_rows=count(value.get("balance")),
            )
        elif prefix == "financing":
            row.update(
                {k + "_rows": count(value.get(k)) for k in ("lending", "credit", "stock_loan")}
            )
    connection.close()
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.with_suffix(".csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["code", "name", "market", *fields])
        writer.writeheader()
        writer.writerows(result.values())
    summary = {
        "audited_at": datetime.now(UTC).isoformat(),
        "universe_received_at": universe.get("received_at"),
        "securities": len(result),
        "markets": dict(Counter(r["market"] for r in result.values())),
        "with_nonempty_rows": {
            f: sum(isinstance(r[f], (int, float)) and r[f] > 0 for r in result.values())
            for f in fields
            if f.endswith(("_rows", "_facts", "_accounts"))
        },
        "total_rows": {
            f: sum(r[f] or 0 for r in result.values())
            for f in fields
            if f.endswith(("_rows", "_facts", "_accounts"))
        },
        "events_coverage_verified": sum(
            r["events_coverage_verified"] is True for r in result.values()
        ),
        "ten_year_price_span_observed": sum(
            r["ten_year_span_observed"] is True for r in result.values()
        ),
        "five_financial_years_observed": sum(
            r["five_years_observed"] is True for r in result.values()
        ),
        "filings_complete_response": sum(
            r["filings_complete_response"] is True for r in result.values()
        ),
        "limits": [
            "현재 상장목록과의 대조 아님",
            "관측 행 수는 정확성 또는 최신성 검증 아님",
            "dated_ttm_facts는 기간이 있는 입력 행이며 TTM 계산 성공 수가 아님",
            "ttm_calculated_accounts는 기간 검증 결과입니다. "
            "과거 시점 공개 여부는 별도 검사합니다.",
            "재무 연도 존재는 모든 계정 확보를 뜻하지 않음",
            "신규상장·미제공 예외는 별도 대조 필요",
        ],
    }
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="storage/public-market/market.db")
    parser.add_argument("--output", default="docs/coverage-20260927.json")
    args = parser.parse_args()
    audit(args.database, args.output)
