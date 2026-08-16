from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


def build_performance_table(
    ticker: str,
    valuation_rows: list[dict[str, Any]],
    *,
    currency: str,
    initial_investment: Decimal = Decimal("10000"),
    data_mode: str,
) -> dict[str, Any]:
    historical = sorted(
        [row for row in valuation_rows if not row.get("forecast_flag")],
        key=lambda row: int(row["fiscal_year"]),
    )
    if len(historical) < 2:
        return {
            "ticker": ticker,
            "currency": currency,
            "initial_investment": initial_investment,
            "rows": [],
            "summary": {},
            "quality_status": _quality_status(data_mode, ["insufficient_history"]),
            "flags": ["insufficient_history"],
            "source_trace": _performance_trace(
                ticker,
                None,
                currency,
                data_mode,
                [],
                ["insufficient_history"],
            ),
        }

    ending = historical[-1]
    flags = _row_flags(historical)
    rows = [
        _performance_row(ticker, start, ending, historical, initial_investment, currency, data_mode)
        for start in historical[:-1]
    ]
    quality_status = _quality_status(data_mode, flags)
    return {
        "ticker": ticker,
        "currency": currency,
        "initial_investment": initial_investment,
        "rows": rows,
        "summary": _summary(rows),
        "quality_status": quality_status,
        "flags": flags,
        "source_trace": _performance_trace(
            ticker,
            int(ending["fiscal_year"]),
            currency,
            data_mode,
            [row.get("source_trace") or {} for row in historical],
            flags,
        ),
    }


def _performance_row(
    ticker: str,
    start: dict[str, Any],
    ending: dict[str, Any],
    historical: list[dict[str, Any]],
    initial_investment: Decimal,
    currency: str,
    data_mode: str,
) -> dict[str, Any]:
    start_year = int(start["fiscal_year"])
    end_year = int(ending["fiscal_year"])
    years = max(end_year - start_year, 1)
    start_price = _decimal(start.get("price"))
    end_price = _decimal(ending.get("price"))
    shares = _safe_div(initial_investment, start_price)
    dividend_rows = [row for row in historical if start_year < int(row["fiscal_year"]) <= end_year]
    dividend_cash = sum(
        (_decimal(row.get("dividend")) * shares for row in dividend_rows), Decimal("0")
    )
    reinvested_shares, reinvested_dividends = _reinvest_dividends(shares, dividend_rows)
    ending_value = shares * end_price
    reinvested_ending_value = reinvested_shares * end_price
    capital_gain = ending_value - initial_investment
    total_value = ending_value + dividend_cash
    total_gain = total_value - initial_investment
    reinvested_total_gain = reinvested_ending_value - initial_investment
    row_flags = []
    if any(row.get("dividend") is None for row in dividend_rows):
        row_flags.append("missing_dividend_source")
    return {
        "start_year": start_year,
        "end_year": end_year,
        "years": years,
        "start_price": start_price,
        "end_price": end_price,
        "shares_purchased": shares,
        "initial_investment": initial_investment,
        "ending_value": ending_value,
        "dividends_received": dividend_cash,
        "reinvested_shares": reinvested_shares,
        "reinvested_dividends": reinvested_dividends,
        "reinvested_ending_value": reinvested_ending_value,
        "capital_gain": capital_gain,
        "total_gain": total_gain,
        "reinvested_total_gain": reinvested_total_gain,
        "price_return_pct": _percent(capital_gain, initial_investment),
        "dividend_return_pct": _percent(dividend_cash, initial_investment),
        "total_return_pct": _percent(total_gain, initial_investment),
        "reinvested_total_return_pct": _percent(reinvested_total_gain, initial_investment),
        "annualized_price_return_pct": _cagr(start_price, end_price, years),
        "annualized_total_return_pct": _cagr(initial_investment, total_value, years),
        "reinvested_annualized_total_return_pct": _cagr(
            initial_investment,
            reinvested_ending_value,
            years,
        ),
        "quality_status": _quality_status(data_mode, row_flags),
        "flags": row_flags,
        "source_trace": _performance_trace(
            ticker,
            end_year,
            currency,
            data_mode,
            [start.get("source_trace") or {}, ending.get("source_trace") or {}],
            row_flags,
            start_year=start_year,
        ),
    }


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    latest = rows[-1]
    best = max(rows, key=lambda row: row["annualized_total_return_pct"])
    worst = min(rows, key=lambda row: row["annualized_total_return_pct"])
    return {
        "latest_start_year": latest["start_year"],
        "latest_annualized_total_return_pct": latest["annualized_total_return_pct"],
        "latest_reinvested_annualized_total_return_pct": latest[
            "reinvested_annualized_total_return_pct"
        ],
        "best_start_year": best["start_year"],
        "best_annualized_total_return_pct": best["annualized_total_return_pct"],
        "worst_start_year": worst["start_year"],
        "worst_annualized_total_return_pct": worst["annualized_total_return_pct"],
    }


def _performance_trace(
    ticker: str,
    fiscal_year: int | None,
    currency: str,
    data_mode: str,
    input_traces: list[dict[str, Any]],
    flags: list[str],
    *,
    start_year: int | None = None,
) -> dict[str, Any]:
    period = (
        f"FY{start_year}-FY{fiscal_year}"
        if start_year is not None and fiscal_year is not None
        else f"FY{fiscal_year}"
        if fiscal_year is not None
        else "historical"
    )
    return {
        "source_document_id": f"{ticker.lower()}-{period.lower()}-performance",
        "source_type": "performance_derived",
        "filing_id": f"{ticker}-{period}-performance",
        "period": period,
        "unit": "return",
        "currency": currency,
        "formula": (
            "shares=initial_investment/start_price; total_value=end_price*shares+"
            "sum(dividend_per_share*shares); reinvested_shares recursively add "
            "dividend_cash/year_end_price; CAGR=(total_value/initial)^(1/years)-1"
        ),
        "quality_status": _quality_status(data_mode, flags),
        "input_trace_summary": {
            "input_trace_count": len(input_traces),
            "source_types": sorted(
                {
                    str(trace.get("source_type"))
                    for trace in input_traces
                    if trace.get("source_type")
                }
            ),
            "quality_statuses": sorted(
                {
                    str(trace.get("quality_status"))
                    for trace in input_traces
                    if trace.get("quality_status")
                }
            ),
        },
    }


def _reinvest_dividends(
    starting_shares: Decimal,
    dividend_rows: list[dict[str, Any]],
) -> tuple[Decimal, Decimal]:
    shares = starting_shares
    dividend_cash_total = Decimal("0")
    for row in dividend_rows:
        dividend_per_share = _decimal(row.get("dividend"))
        price = _decimal(row.get("price"))
        dividend_cash = dividend_per_share * shares
        dividend_cash_total += dividend_cash
        if price > 0:
            shares += dividend_cash / price
    return shares, dividend_cash_total


def _row_flags(rows: list[dict[str, Any]]) -> list[str]:
    flags = []
    if any(row.get("dividend") is None for row in rows):
        flags.append("missing_dividend_source")
    if any(not row.get("source_trace") for row in rows):
        flags.append("missing_source_trace")
    return sorted(flags)


def _quality_status(data_mode: str, flags: list[str]) -> str:
    if data_mode != "source_backed":
        return "fixture_non_production_performance"
    if flags:
        return "source_backed_partial"
    return "source_backed_derived"


def _decimal(value: Any) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")
    return parsed if parsed.is_finite() else Decimal("0")


def _safe_div(numerator: Decimal, denominator: Decimal) -> Decimal:
    if denominator == 0:
        return Decimal("0")
    return numerator / denominator


def _percent(numerator: Decimal, denominator: Decimal) -> Decimal:
    if denominator == 0:
        return Decimal("0.00")
    return ((numerator / denominator) * Decimal("100")).quantize(Decimal("0.01"))


def _cagr(start_value: Decimal, end_value: Decimal, years: int) -> Decimal:
    if start_value <= 0 or end_value <= 0 or years <= 0:
        return Decimal("0.00")
    value = (float(end_value / start_value) ** (1 / years) - 1) * 100
    return Decimal(str(value)).quantize(Decimal("0.01"))
