from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import bindparam, text

from packages.core.universe import top_market_cap_rank_coverage_meta
from packages.valuation.engine import ValuationPoint, build_valuation_map
from packages.valuation.portfolio import (
    PortfolioTransaction,
    build_portfolio_summary,
    parse_transactions_csv,
)
from services.api.database import get_engine, postgres_enabled
from services.api.source_coverage import (
    build_source_coverage_report,
    normalize_coverage_tickers,
)

METRIC_LABELS = {
    "adjusted_operating": "Adjusted Operating EPS",
    "smart_metric": "Smart Metric",
    "basic_eps": "Basic EPS",
    "diluted_eps": "Diluted EPS",
    "gaap_diluted_eps": "GAAP Diluted EPS",
    "operating_cash_flow_share": "Operating Cash Flow (OCF/FFO)",
    "revenue_share": "Revenue/share",
    "sales_share": "Sales/share",
    "fcf_share": "Free Cash Flow to Equity (FCFE/AFFO)",
    "ebitda_share": "EBITDA/share",
    "ebit_share": "EBIT/share",
    "ffo_affo": "FFO/AFFO",
}

METRIC_VALUE_ALIASES = {
    "smart_metric": ("smart_metric",),
    "basic_eps": ("basic_eps", "reported_eps_basic"),
    "diluted_eps": ("diluted_eps", "reported_eps_diluted", "gaap_diluted_eps"),
    "gaap_diluted_eps": ("gaap_diluted_eps", "diluted_eps", "reported_eps_diluted"),
    "operating_cash_flow_share": ("operating_cash_flow_share", "ocf_share"),
    "revenue_share": ("revenue_share", "sales_share"),
    "sales_share": ("sales_share", "revenue_share"),
    "fcf_share": ("fcf_share", "fcfe_share"),
    "ebitda_share": ("ebitda_share",),
    "ebit_share": ("ebit_share",),
    "ffo_affo": ("ffo_affo", "ffo_share", "affo_share"),
}

DEFAULT_POLICY_KEY = "street_comparable|sbc_company|amort_company|default"

FRED_USD_PER_CURRENCY_SERIES = {
    "KRW": "DEXKOUS",
    "JPY": "DEXJPUS",
}

RESEARCH_METADATA_SOURCES = (
    "naver_search_research",
    "hankyung_consensus_metadata",
)

PRODUCTION_CONSENSUS_QUALITY_STATUSES = (
    "source_backed_consensus_snapshot",
    "source_backed_consensus_snapshots",
    "user_provided_consensus_snapshot",
)


def company_snapshot_from_postgres(ticker: str) -> dict[str, Any] | None:
    if not postgres_enabled():
        return None
    ticker = ticker.upper()
    with get_engine().connect() as connection:
        security = _security_row(connection, ticker)
        if not security:
            return None
        adjusted_rows = _adjusted_rows_for_security(connection, security["id"])
        if not adjusted_rows:
            return None
        latest_adjusted = adjusted_rows[-1]
        latest_price = connection.execute(
            text(
                """
                SELECT fiscal_year, close_price, currency, source_trace
                FROM price_bars
                WHERE security_id = :security_id
                ORDER BY trade_date DESC
                LIMIT 1
                """
            ),
            {"security_id": security["id"]},
        ).mappings().first()
        if not latest_price:
            return None
        dividend = connection.execute(
            text(
                """
                SELECT COALESCE(SUM(amount), 0) AS amount
                FROM dividends
                WHERE security_id = :security_id
                  AND fiscal_year = :fiscal_year
                """
            ),
            {
                "security_id": security["id"],
                "fiscal_year": latest_price["fiscal_year"],
            },
        ).mappings().first()
        metrics = _latest_metric_lookup(connection, security["id"])
    price = Decimal(str(latest_price["close_price"]))
    eps = Decimal(str(latest_adjusted["adjusted_eps"]))
    annual_dividend = Decimal(str(dividend["amount"] if dividend else "0"))
    price_source_trace = dict(latest_price["source_trace"] or {})
    market_structure = _market_structure_from_price_trace(
        price_source_trace,
        security["currency"],
    )
    source_trace = dict(latest_adjusted["source_trace"] or {})
    source_trace.setdefault("source_type", "postgres")
    source_trace.setdefault("quality_status", latest_adjusted["quality_status"])
    source_trace["price_source_trace"] = price_source_trace
    if market_structure["market_cap_source_trace"]:
        source_trace["market_cap_source_trace"] = market_structure["market_cap_source_trace"]
    if market_structure["listed_shares_source_trace"]:
        source_trace["listed_shares_source_trace"] = market_structure[
            "listed_shares_source_trace"
        ]
    return {
        "ticker": ticker,
        "name": security["name"] or ticker,
        "market": security["market"],
        "country": security["country"],
        "currency": security["currency"],
        "sector_policy": latest_adjusted["sector_policy"] or "default",
        "current_price": _decimal_str(price),
        "market_cap": market_structure["market_cap"],
        "listed_shares": market_structure["listed_shares"],
        "per": _ratio(price, eps),
        "dividend_yield": _percent_ratio(annual_dividend, price),
        "eps": _decimal_str(eps),
        "eps_cagr": _eps_cagr(adjusted_rows),
        "roe": _metric_value(metrics, "roe"),
        "roic": _metric_value(metrics, "roic"),
        "debt_ratio": _metric_value(metrics, "debt_to_equity")
        or _metric_value(metrics, "debt_ratio"),
        "eps_method": latest_adjusted["method"],
        "confidence": _decimal_str(latest_adjusted["confidence"]),
        "source_note": "source_backed",
        "source_trace": source_trace,
    }


def financials_from_postgres(ticker: str) -> list[dict[str, Any]] | None:
    if not postgres_enabled():
        return None
    ticker = ticker.upper()
    with get_engine().connect() as connection:
        security = _security_row(connection, ticker)
        if not security:
            return None
        adjusted_rows = _adjusted_rows_for_security(connection, security["id"])
        metric_rows = connection.execute(
            text(
                """
                SELECT DISTINCT ON (metric_key, fiscal_year)
                       metric_key, fiscal_year, value, method, quality_status, source_trace
                FROM metric_values
                WHERE security_id = :security_id
                ORDER BY metric_key, fiscal_year, created_at DESC
                """
            ),
            {"security_id": security["id"]},
        ).mappings().all()
    if not adjusted_rows and not metric_rows:
        return None
    adjusted_by_year = {int(row["fiscal_year"]): row for row in adjusted_rows}
    metrics_by_year: dict[int, dict[str, Any]] = {}
    for row in metric_rows:
        metrics_by_year.setdefault(int(row["fiscal_year"]), {})[row["metric_key"]] = row
    years = sorted(set(adjusted_by_year) | set(metrics_by_year))
    rows: list[dict[str, Any]] = []
    for year in years:
        adjusted = adjusted_by_year.get(year)
        metrics = metrics_by_year.get(year, {})
        trace = _financial_trace(adjusted, metrics)
        metric_traces = {
            metric_key: _metric_trace(metric_row)
            for metric_key, metric_row in metrics.items()
        }
        if adjusted:
            metric_traces["eps"] = dict(adjusted["source_trace"] or {})
        rows.append(
            {
                "fiscal_year": year,
                "revenue": _metric_value(metrics, "revenue"),
                "eps": _decimal_str(adjusted["adjusted_eps"]) if adjusted else None,
                "fcf": _metric_value(metrics, "fcf"),
                "gross_margin": _metric_value(metrics, "gross_margin"),
                "operating_margin": _metric_value(metrics, "operating_margin"),
                "net_margin": _metric_value(metrics, "net_margin"),
                "roe": _metric_value(metrics, "roe"),
                "roic": _metric_value(metrics, "roic"),
                "debt_to_equity": _metric_value(metrics, "debt_to_equity")
                or _metric_value(metrics, "debt_ratio"),
                "method": adjusted["method"] if adjusted else trace.get("method", "postgres"),
                "confidence": _decimal_str(adjusted["confidence"]) if adjusted else None,
                "source_trace": trace,
                "metric_traces": metric_traces,
            }
        )
    return rows


def financial_facts_from_postgres(ticker: str, limit: int = 250) -> list[dict[str, Any]] | None:
    if not postgres_enabled():
        return None
    ticker = ticker.upper()
    with get_engine().connect() as connection:
        security = _security_row(connection, ticker)
        if not security:
            return None
        rows = connection.execute(
            text(
                """
                SELECT taxonomy, tag, label, fiscal_year, fiscal_period,
                       period_start, period_end, filed_at, accession_number,
                       form_type, frame, unit, currency, value, source,
                       source_url, quality_status, source_trace,
                       source_document_id, metadata
                FROM financial_facts
                WHERE security_id = :security_id
                ORDER BY fiscal_year DESC, filed_at DESC NULLS LAST, tag
                LIMIT :limit
                """
            ),
            {"security_id": security["id"], "limit": limit},
        ).mappings().all()
    if not rows:
        return None
    return [_financial_fact_row(ticker, row) for row in rows]


def use_of_cash_inputs_from_postgres(
    ticker: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str] | None:
    if not postgres_enabled():
        return None
    ticker = ticker.upper()
    financial_rows = financials_from_postgres(ticker)
    if financial_rows is None:
        return None
    with get_engine().connect() as connection:
        security = _security_row(connection, ticker)
        if not security:
            return None
        adjusted_rows = _adjusted_rows_for_security(connection, security["id"])
        dividend_rows = connection.execute(
            text(
                """
                SELECT fiscal_year, SUM(amount) AS amount,
                       jsonb_agg(source_trace) AS source_traces
                FROM dividends
                WHERE security_id = :security_id
                GROUP BY fiscal_year
                """
            ),
            {"security_id": security["id"]},
        ).mappings().all()
    dividends_by_year = {int(row["fiscal_year"]): row for row in dividend_rows}
    valuation_rows: list[dict[str, Any]] = []
    for adjusted in adjusted_rows:
        year = int(adjusted["fiscal_year"])
        dividend = dividends_by_year.get(year)
        trace = dict(adjusted["source_trace"] or {})
        trace.setdefault("source_type", "postgres")
        trace.setdefault("filing_id", trace.get("accession_number") or f"{ticker}-{year}-postgres")
        trace.setdefault("period", f"FY{year}")
        trace.setdefault("unit", "per_share")
        trace.setdefault("currency", security["currency"])
        trace.setdefault("formula", adjusted["formula"] or "adjusted_eps")
        trace.setdefault("quality_status", adjusted["quality_status"] or "warning")
        if dividend is not None:
            trace["dividend_source_traces"] = dividend["source_traces"] or []
        valuation_rows.append(
            {
                "fiscal_year": year,
                "metric": adjusted["adjusted_eps"],
                "dividend": dividend["amount"] if dividend is not None else None,
                "forecast_flag": False,
                "source_trace": trace,
            }
        )
    return financial_rows, valuation_rows, security["currency"]


def portfolio_from_postgres(owner_key: str = "default") -> dict[str, Any] | None:
    if not postgres_enabled():
        return None
    transactions = _portfolio_transactions_from_postgres(owner_key)
    if not transactions:
        return _empty_portfolio("source_backed_empty")
    latest_prices = _latest_prices_for_portfolio(
        {transaction.ticker for transaction in transactions}
    )
    summary = build_portfolio_summary(transactions, latest_prices)
    return summary | {
        "source_trace": {
            "source_type": "postgres",
            "source_document_id": f"portfolio-{owner_key}",
            "filing_id": f"portfolio-{owner_key}",
            "period": "current",
            "unit": "portfolio",
            "currency": "mixed",
            "formula": "holdings from persisted CSV transactions and latest price_bars",
            "quality_status": "source_backed",
        }
    }


def store_portfolio_csv_to_postgres(
    csv_text: str,
    owner_key: str = "default",
    replace_existing: bool = True,
) -> dict[str, Any] | None:
    if not postgres_enabled():
        return None
    transactions = parse_transactions_csv(csv_text)
    import_id = f"portfolio-import-{uuid.uuid4()}"
    now = datetime.now(UTC)
    with get_engine().begin() as connection:
        if replace_existing:
            connection.execute(
                text("DELETE FROM portfolio_transactions WHERE owner_key = :owner_key"),
                {"owner_key": owner_key},
            )
        securities = {
            row["ticker"]: row["id"]
            for row in connection.execute(
                text("SELECT id, ticker FROM securities WHERE ticker = ANY(:tickers)"),
                {"tickers": [transaction.ticker for transaction in transactions]},
            ).mappings()
        }
        for transaction in transactions:
            trace = {
                "source_type": "user_csv",
                "source_document_id": import_id,
                "filing_id": import_id,
                "period": transaction.trade_date.isoformat(),
                "unit": "shares",
                "currency": transaction.currency,
                "formula": "explicit user-entered CSV transaction",
                "quality_status": "user_provided",
                "ticker": transaction.ticker,
            }
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio_transactions (
                      id, owner_key, security_id, ticker, trade_date, side, quantity,
                      price, currency, sector, source, source_trace, created_at
                    )
                    VALUES (
                      :id, :owner_key, :security_id, :ticker, :trade_date, :side,
                      :quantity, :price, :currency, :sector, 'user_csv',
                      CAST(:source_trace AS jsonb), :created_at
                    )
                    ON CONFLICT ON CONSTRAINT uq_portfolio_transactions_owner_trade DO UPDATE SET
                      sector = EXCLUDED.sector,
                      currency = EXCLUDED.currency,
                      source_trace = EXCLUDED.source_trace
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "owner_key": owner_key,
                    "security_id": securities.get(transaction.ticker),
                    "ticker": transaction.ticker,
                    "trade_date": transaction.trade_date,
                    "side": transaction.side,
                    "quantity": transaction.quantity,
                    "price": transaction.price,
                    "currency": transaction.currency,
                    "sector": transaction.sector,
                    "source_trace": _json(trace),
                    "created_at": now,
                },
            )
    payload = portfolio_from_postgres(owner_key)
    return (payload or _empty_portfolio("source_backed_empty")) | {
        "import_trace": {
            "source_type": "user_csv",
            "source_document_id": import_id,
            "rows": len(transactions),
            "replace_existing": replace_existing,
        }
    }


def watchlist_from_postgres(
    owner_key: str = "default",
    name: str = "Default",
) -> dict[str, Any] | None:
    if not postgres_enabled():
        return None
    watchlist = _ensure_watchlist(owner_key, name)
    with get_engine().connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT wi.ticker, wi.note, wi.source_trace, wi.created_at,
                       s.currency, s.exchange, c.name AS company_name, c.country
                FROM watchlist_items wi
                LEFT JOIN securities s ON s.id = wi.security_id OR s.ticker = wi.ticker
                LEFT JOIN companies c ON c.id = s.company_id
                WHERE wi.watchlist_id = :watchlist_id
                ORDER BY wi.created_at DESC, wi.ticker
                """
            ),
            {"watchlist_id": watchlist["id"]},
        ).mappings().all()
    items = []
    for row in rows:
        snapshot = company_snapshot_from_postgres(row["ticker"])
        items.append(_watchlist_item(row, snapshot))
    return {
        "id": str(watchlist["id"]),
        "name": watchlist["name"],
        "owner_key": owner_key,
        "items": items,
        "source_trace": {
            "source_type": "postgres",
            "source_document_id": f"watchlist-{owner_key}-{name}",
            "filing_id": f"watchlist-{owner_key}-{name}",
            "period": "current",
            "unit": "watchlist",
            "currency": "mixed",
            "formula": "persisted watchlist items joined to latest source-backed snapshot metrics",
            "quality_status": "source_backed",
        },
    }


def add_watchlist_item_to_postgres(
    ticker: str,
    note: str | None = None,
    owner_key: str = "default",
    name: str = "Default",
) -> dict[str, Any] | None:
    if not postgres_enabled():
        return None
    ticker = ticker.upper()
    watchlist = _ensure_watchlist(owner_key, name)
    with get_engine().begin() as connection:
        security = connection.execute(
            text("SELECT id FROM securities WHERE ticker = :ticker"),
            {"ticker": ticker},
        ).mappings().first()
        trace = {
            "source_type": "user_watchlist",
            "source_document_id": f"watchlist-{owner_key}-{name}",
            "filing_id": f"watchlist-{owner_key}-{name}",
            "period": "current",
            "unit": "ticker",
            "currency": "mixed",
            "formula": "explicit user watchlist entry",
            "quality_status": "user_provided",
        }
        connection.execute(
            text(
                """
                INSERT INTO watchlist_items (
                  id, watchlist_id, security_id, ticker, note, source_trace, created_at
                )
                VALUES (
                  :id, :watchlist_id, :security_id, :ticker, :note,
                  CAST(:source_trace AS jsonb), :created_at
                )
                ON CONFLICT ON CONSTRAINT uq_watchlist_items_ticker DO UPDATE SET
                  note = EXCLUDED.note,
                  security_id = EXCLUDED.security_id,
                  source_trace = EXCLUDED.source_trace
                """
            ),
            {
                "id": uuid.uuid4(),
                "watchlist_id": watchlist["id"],
                "security_id": security["id"] if security else None,
                "ticker": ticker,
                "note": note,
                "source_trace": _json(trace),
                "created_at": datetime.now(UTC),
            },
        )
    return watchlist_from_postgres(owner_key, name)


def remove_watchlist_item_from_postgres(
    ticker: str,
    owner_key: str = "default",
    name: str = "Default",
) -> dict[str, Any] | None:
    if not postgres_enabled():
        return None
    watchlist = _ensure_watchlist(owner_key, name)
    with get_engine().begin() as connection:
        connection.execute(
            text(
                """
                DELETE FROM watchlist_items
                WHERE watchlist_id = :watchlist_id AND ticker = :ticker
                """
            ),
            {"watchlist_id": watchlist["id"], "ticker": ticker.upper()},
        )
    return watchlist_from_postgres(owner_key, name)


def screener_rows_from_postgres() -> list[dict[str, Any]] | None:
    if not postgres_enabled():
        return None
    with get_engine().connect() as connection:
        tickers = [
            row["ticker"]
            for row in connection.execute(
                text("SELECT ticker FROM securities ORDER BY ticker LIMIT 500")
            ).mappings()
        ]
        fx_rates = _latest_usd_fx_rates(connection)
    rows: list[dict[str, Any]] = []
    for ticker in tickers:
        snapshot = company_snapshot_from_postgres(ticker)
        if snapshot is None:
            continue
        normal_pe = _normal_multiple_from_postgres(ticker)
        per = _decimal_or_none(snapshot["per"])
        roe = _decimal_or_none(snapshot["roe"])
        roic = _decimal_or_none(snapshot["roic"])
        market_cap = _decimal_or_none(snapshot.get("market_cap"))
        market_cap_usd = _market_cap_usd_from_snapshot(snapshot, fx_rates)
        rows.append(
            {
                "ticker": ticker,
                "name": snapshot["name"],
                "market": snapshot["market"],
                "currency": snapshot["currency"],
                "market_cap": snapshot.get("market_cap"),
                "market_cap_usd": market_cap_usd["market_cap_usd"],
                "listed_shares": snapshot.get("listed_shares"),
                "per": snapshot["per"],
                "normal_pe": _decimal_str(normal_pe),
                "roe": snapshot["roe"],
                "roic": snapshot["roic"],
                "eps_cagr": snapshot["eps_cagr"],
                "debt_to_equity": snapshot["debt_ratio"],
                "filters": {
                    "metric_to_value": per is not None and per < Decimal("25"),
                    "metric_to_metric": (
                        roe is not None and roic is not None and roe > roic
                    ),
                    "company_relative": (
                        per is not None and normal_pe is not None and per < normal_pe
                    ),
                    "market_cap_available": market_cap is not None,
                    "market_cap_usd_available": market_cap_usd["market_cap_usd"] is not None,
                },
                "source_trace": _screener_snapshot_trace(snapshot["source_trace"], market_cap_usd),
            }
        )
    return rows or None


def search_securities_from_postgres(q: str = "") -> list[dict[str, Any]] | None:
    if not postgres_enabled():
        return None
    query = f"%{q.upper()}%"
    with get_engine().connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT s.ticker, c.name, c.country, s.currency,
                       COALESCE(s.exchange, c.country) AS market
                FROM securities s
                LEFT JOIN companies c ON c.id = s.company_id
                WHERE :q = '%%' OR UPPER(s.ticker) LIKE :q OR UPPER(COALESCE(c.name, '')) LIKE :q
                ORDER BY s.ticker
                LIMIT 25
                """
            ),
            {"q": query},
        ).mappings().all()
    return [
        {
            "ticker": row["ticker"],
            "name": row["name"] or row["ticker"],
            "market": row["market"],
            "country": row["country"],
            "currency": row["currency"],
        }
        for row in rows
    ]


def _metric_value_rows_for_security(connection, security_id: Any, metric: str):
    metric_keys = METRIC_VALUE_ALIASES.get(metric, (metric,))
    return connection.execute(
        text(
            """
            SELECT DISTINCT ON (fiscal_year)
                   fiscal_year, value, source_trace, method, quality_status, formula
            FROM metric_values
            WHERE security_id = :security_id AND metric_key IN :metric_keys
            ORDER BY fiscal_year,
                     CASE WHEN metric_key = :primary_metric THEN 0 ELSE 1 END,
                     created_at DESC
            """
        ).bindparams(bindparam("metric_keys", expanding=True)),
        {
            "security_id": security_id,
            "metric_keys": list(metric_keys),
            "primary_metric": metric_keys[0],
        },
    ).mappings().all()


def valuation_points_from_postgres(
    ticker: str,
    metric: str,
) -> tuple[list[ValuationPoint], str, dict[str, Any]] | None:
    if not postgres_enabled():
        return None
    ticker = ticker.upper()
    with get_engine().connect() as connection:
        security = connection.execute(
            text(
                """
                SELECT s.id, s.currency, COALESCE(c.country, 'US') AS country
                FROM securities s
                LEFT JOIN companies c ON c.id = s.company_id
                WHERE s.ticker = :ticker
                """
            ),
            {"ticker": ticker},
        ).mappings().first()
        if not security:
            return None
        security_id = security["id"]
        if metric in {"adjusted_operating", "gaap_diluted_eps", "diluted_eps"}:
            column = "adjusted_eps" if metric == "adjusted_operating" else "gaap_eps_diluted"
            metric_rows = connection.execute(
                text(
                    f"""
                    SELECT DISTINCT ON (fiscal_year)
                           fiscal_year, {column} AS value, source_trace,
                           method, quality_status, formula
                    FROM adjusted_earnings
                    WHERE security_id = :security_id
                      AND {column} IS NOT NULL
                      AND policy = :policy
                    ORDER BY fiscal_year, accepted_at DESC NULLS LAST,
                             computed_at DESC NULLS LAST
                    """
                ),
                {"security_id": security_id, "policy": DEFAULT_POLICY_KEY},
            ).mappings().all()
            if not metric_rows and metric in {"gaap_diluted_eps", "diluted_eps"}:
                metric_rows = _metric_value_rows_for_security(
                    connection,
                    security_id,
                    metric,
                )
        else:
            metric_rows = _metric_value_rows_for_security(connection, security_id, metric)
        if not metric_rows:
            return None
        price_rows = list(
            connection.execute(
                text(
                    """
                    SELECT DISTINCT ON (fiscal_year)
                           fiscal_year, close_price, source_trace
                    FROM price_bars
                    WHERE security_id = :security_id
                    ORDER BY fiscal_year, trade_date DESC
                    """
                ),
                {"security_id": security_id},
            ).mappings()
        )
        prices = {
            row["fiscal_year"]: Decimal(str(row["close_price"])) for row in price_rows
        }
        price_traces = {
            row["fiscal_year"]: row["source_trace"] or {} for row in price_rows
        }
        dividend_rows = list(
            connection.execute(
                text(
                    """
                    SELECT fiscal_year, SUM(amount) AS amount,
                           jsonb_agg(source_trace) FILTER (
                             WHERE source_trace IS NOT NULL
                           ) AS source_traces
                    FROM dividends
                    WHERE security_id = :security_id
                    GROUP BY fiscal_year
                    """
                ),
                {"security_id": security_id},
            ).mappings()
        )
        dividends = {
            row["fiscal_year"]: Decimal(str(row["amount"])) for row in dividend_rows
        }
        dividend_traces = {
            row["fiscal_year"]: row["source_traces"] or [] for row in dividend_rows
        }
    points: list[ValuationPoint] = []
    for row in metric_rows:
        year = int(row["fiscal_year"])
        if year not in prices:
            continue
        trace = dict(row["source_trace"] or {})
        trace.setdefault("source_type", "postgres")
        trace.setdefault("filing_id", trace.get("accession_number") or f"{ticker}-{year}-postgres")
        trace.setdefault("period", f"FY{year}")
        trace.setdefault("unit", "per_share")
        trace.setdefault("currency", security["currency"])
        trace.setdefault("formula", row["formula"] or metric)
        trace.setdefault("quality_status", row["quality_status"] or "warning")
        trace["price_source_trace"] = price_traces.get(year, {})
        if dividend_traces.get(year):
            trace["dividend_source_traces"] = dividend_traces[year]
        points.append(
            ValuationPoint(
                fiscal_year=year,
                metric=Decimal(str(row["value"])),
                price=prices[year],
                dividend=dividends.get(year, Decimal("0")),
                source_trace=trace,
            )
        )
    if not points:
        return None
    return (
        points,
        METRIC_LABELS.get(metric, metric),
        {
            "data_backend": "postgres",
            "currency": security["currency"],
            "country": security["country"],
        },
    )


def price_points_from_postgres(
    ticker: str,
    *,
    start_year: int,
    end_year: int,
) -> list[dict[str, Any]] | None:
    if not postgres_enabled():
        return None
    ticker = ticker.upper()
    with get_engine().connect() as connection:
        security = _security_row(connection, ticker)
        if not security:
            return None
        rows = connection.execute(
            text(
                """
                WITH monthly AS (
                    SELECT
                        DATE_TRUNC('month', trade_date)::date AS month_start,
                        trade_date,
                        fiscal_year,
                        close_price,
                        currency,
                        source,
                        source_trace,
                        ROW_NUMBER() OVER (
                            PARTITION BY DATE_TRUNC('month', trade_date)
                            ORDER BY trade_date DESC, source
                        ) AS row_number
                    FROM price_bars
                    WHERE security_id = :security_id
                      AND fiscal_year >= :start_year
                      AND fiscal_year <= :end_year
                )
                SELECT trade_date, fiscal_year, close_price, currency, source, source_trace
                FROM monthly
                WHERE row_number = 1
                ORDER BY trade_date
                """
            ),
            {
                "security_id": security["id"],
                "start_year": start_year,
                "end_year": end_year,
            },
        ).mappings().all()
    if not rows:
        return []
    price_points: list[dict[str, Any]] = []
    for row in rows:
        trace = dict(row["source_trace"] or {})
        trade_date = row["trade_date"]
        trace.setdefault("source_type", row["source"] or "postgres_price_bars")
        trace.setdefault("source_document_id", f"{ticker.lower()}-{trade_date}-price")
        trace.setdefault("filing_id", f"{ticker.lower()}-{trade_date}-price")
        trace.setdefault("period", _date_str(trade_date))
        trace.setdefault("unit", "per_share")
        trace.setdefault("currency", row["currency"] or security["currency"])
        trace.setdefault(
            "formula",
            "monthly last available close price from source-backed price_bars",
        )
        trace.setdefault("quality_status", "source_backed_price")
        trace["frequency"] = "monthly"
        price_points.append(
            {
                "date": _date_str(trade_date),
                "fiscal_year": int(row["fiscal_year"]),
                "close_price": str(row["close_price"]),
                "currency": row["currency"] or security["currency"],
                "frequency": "monthly",
                "source_trace": trace,
            }
        )
    return price_points


def recession_periods_from_postgres(
    *,
    start_year: int,
    end_year: int,
    series_id: str = "USREC",
) -> list[dict[str, Any]] | None:
    if not postgres_enabled():
        return None
    range_start = date(start_year, 1, 1)
    range_end = date(end_year, 12, 31)
    with get_engine().connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT series_id, start_date, end_date, source, source_trace
                FROM recession_periods
                WHERE series_id = :series_id
                  AND start_date <= :range_end
                  AND COALESCE(end_date, :range_end) >= :range_start
                ORDER BY start_date
                """
            ),
            {
                "series_id": series_id,
                "range_start": range_start,
                "range_end": range_end,
            },
        ).mappings().all()
    return [_recession_period_row(row) for row in rows]


def adjusted_series_from_postgres(
    ticker: str,
    policy_key: str,
    start_year: int | None = None,
    end_year: int | None = None,
) -> dict[str, Any] | None:
    if not postgres_enabled():
        return None
    ticker = ticker.upper()
    policy_base = policy_key.split("|", 1)[0]
    allow_policy_prefix = "|" not in policy_key
    with get_engine().connect() as connection:
        security = connection.execute(
            text("SELECT id FROM securities WHERE ticker = :ticker"),
            {"ticker": ticker},
        ).mappings().first()
        if not security:
            return None
        params: dict[str, Any] = {
            "security_id": security["id"],
            "policy_key": policy_key,
            "policy_prefix": f"{policy_base}|%",
            "allow_policy_prefix": allow_policy_prefix,
            "start_year": start_year,
            "end_year": end_year,
        }
        rows = connection.execute(
            text(
                """
                SELECT DISTINCT ON (fiscal_year, fiscal_period)
                       *
                FROM adjusted_earnings
                WHERE security_id = :security_id
                  AND (
                    policy = :policy_key
                    OR (:allow_policy_prefix AND policy LIKE :policy_prefix)
                  )
                  AND (:start_year IS NULL OR fiscal_year >= :start_year)
                  AND (:end_year IS NULL OR fiscal_year <= :end_year)
                ORDER BY fiscal_year, fiscal_period,
                         accepted_at DESC NULLS LAST,
                         computed_at DESC NULLS LAST
                """
            ),
            params,
        ).mappings().all()
        if not rows:
            return None
        waterfall_by_year = _waterfall_by_year(
            connection,
            security["id"],
            policy_key,
            policy_base,
            allow_policy_prefix,
        )
    return {
        "ticker": ticker,
        "policy": {
            "base_policy": policy_base,
            "key": policy_key,
        },
        "series": [
            _adjusted_row(
                row,
                waterfall_by_year.get((row["id"], row["fiscal_year"], row["fiscal_period"]), []),
            )
            for row in rows
        ],
        "failed_strategies": [],
        "warnings": [],
        "meta": {"source": "postgres"},
    }


def adjusted_waterfall_from_postgres(
    ticker: str,
    fiscal_year: int,
    fiscal_period: str,
    policy_key: str,
) -> dict[str, Any] | None:
    series = adjusted_series_from_postgres(ticker, policy_key, fiscal_year, fiscal_year)
    if not series:
        return None
    for row in series["series"]:
        if row["fiscal_year"] == fiscal_year and row["fiscal_period"] == fiscal_period:
            return {
                "ticker": ticker.upper(),
                "fiscal_year": fiscal_year,
                "waterfall": row["waterfall"],
                "meta": {"source": "postgres"},
            }
    return None


def forecast_evidence_from_postgres(ticker: str) -> dict[str, Any] | None:
    if not postgres_enabled():
        return None
    ticker = ticker.upper()
    with get_engine().connect() as connection:
        security = connection.execute(
            text("SELECT id, currency FROM securities WHERE ticker = :ticker"),
            {"ticker": ticker},
        ).mappings().first()
        if not security:
            return None
        rows = connection.execute(
            text(
                """
                SELECT *
                FROM consensus_estimate_snapshots
                WHERE security_id = :security_id
                  AND metric_key = 'adjusted_operating_eps'
                ORDER BY fiscal_year, snapshot_date, estimate_case
                """
            ),
            {"security_id": security["id"]},
        ).mappings().all()
        if not rows:
            return None
        actual_rows = connection.execute(
            text(
                """
                SELECT DISTINCT ON (fiscal_year)
                       fiscal_year, adjusted_eps, gaap_eps_diluted
                FROM adjusted_earnings
                WHERE security_id = :security_id
                  AND policy = :policy
                ORDER BY fiscal_year, accepted_at DESC NULLS LAST,
                         computed_at DESC NULLS LAST
                """
            ),
            {"security_id": security["id"], "policy": DEFAULT_POLICY_KEY},
        ).mappings().all()
    latest_year = max(int(row["fiscal_year"]) for row in rows)
    year_rows = [row for row in rows if int(row["fiscal_year"]) == latest_year]
    latest_snapshot_date = max(row["snapshot_date"] for row in year_rows)
    latest_rows = [row for row in year_rows if row["snapshot_date"] == latest_snapshot_date]
    cases = _postgres_forecast_cases(latest_rows, security["currency"])
    revisions = _postgres_forecast_revisions(year_rows, security["currency"])
    sentiment = _postgres_forecast_sentiment(revisions)
    scorecard = _postgres_scorecard(ticker, rows, actual_rows, security["currency"])
    trace = _forecast_trace_from_row(latest_rows[0], security["currency"])
    return {
        "ticker": ticker,
        "forecast_year": latest_year,
        "metric_name": "Adjusted Operating EPS",
        "cases": cases,
        "revisions": revisions,
        "sentiment": sentiment,
        "scorecard": scorecard,
        "source_trace": trace,
        "meta": {
            "data_mode": "source_backed",
            "quality_status": trace["quality_status"],
            "source_note": "point-in-time consensus estimate snapshots loaded from Postgres",
        },
    }


def consensus_projection_from_postgres(
    ticker: str,
    forecast_case: str,
    start_year: int,
    years: int,
    start_metric: Decimal | None = None,
) -> dict[str, Any] | None:
    if not postgres_enabled():
        return None
    normalized_case = _normalize_consensus_case(forecast_case)
    bounded_years = max(1, min(int(years), 5))
    end_year = int(start_year) + bounded_years
    ticker = ticker.upper()
    with get_engine().connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT c.*, s.currency
                FROM consensus_estimate_snapshots c
                JOIN securities s ON s.id = c.security_id
                WHERE s.ticker = :ticker
                  AND c.metric_key = 'adjusted_operating_eps'
                  AND c.fiscal_year > :start_year
                  AND c.fiscal_year <= :end_year
                ORDER BY c.fiscal_year, c.snapshot_date DESC, c.created_at DESC
                """
            ),
            {"ticker": ticker, "start_year": start_year, "end_year": end_year},
        ).mappings().all()
    if not rows:
        return None

    selected_rows: list[Any] = []
    metric_values: list[str | None] = []
    missing_years: list[int] = []
    traces_by_year: dict[str, dict[str, Any]] = {}
    currency = rows[0]["currency"]
    for offset in range(1, bounded_years + 1):
        fiscal_year = int(start_year) + offset
        year_rows = [row for row in rows if int(row["fiscal_year"]) == fiscal_year]
        selected = _select_consensus_projection_row(year_rows, normalized_case)
        if selected is None:
            metric_values.append(None)
            missing_years.append(fiscal_year)
            continue
        selected_rows.append(selected)
        metric_values.append(_decimal_str(selected["estimate_value"]))
        traces_by_year[str(fiscal_year)] = _forecast_trace_from_row(selected, currency)

    if not selected_rows:
        return None

    growth_rate = _projection_growth_rate(selected_rows, start_year, start_metric)
    latest_row = max(
        selected_rows,
        key=lambda row: (int(row["fiscal_year"]), row["snapshot_date"], row["created_at"]),
    )
    analyst_count = latest_row["analyst_count"] or 0
    quality_status = (
        "source_backed_consensus_snapshots"
        if not missing_years
        else "partial_source_backed_consensus_snapshots"
    )
    source_trace = _forecast_trace_from_row(latest_row, currency)
    source_trace["quality_status"] = quality_status
    source_trace["forecast_case"] = normalized_case
    source_trace["missing_consensus_years"] = missing_years
    source_trace["formula"] = (
        "point-in-time consensus EPS snapshots by fiscal year; missing years use "
        "deterministic growth fallback when valuation-map projection requires continuity"
    )
    return {
        "case": normalized_case,
        "metric_values": metric_values,
        "growth_rate_pct": _decimal_str(growth_rate),
        "analyst_count": analyst_count,
        "quality_status": quality_status,
        "missing_years": missing_years,
        "source_trace": source_trace,
        "source_traces_by_year": traces_by_year,
        "source_note": "point-in-time consensus estimate snapshots loaded from Postgres",
    }


def source_readiness_from_postgres() -> dict[str, Any] | None:
    if not postgres_enabled():
        return None
    try:
        with get_engine().connect() as connection:
            counts = {
                "securities": _count_table(connection, "securities"),
                "adjusted_earnings": _count_table(connection, "adjusted_earnings"),
                "financial_facts": _count_table(connection, "financial_facts"),
                "price_bars": _count_table(connection, "price_bars"),
                "dividends": _count_table(connection, "dividends"),
                "consensus_estimate_snapshots": _count_table(
                    connection,
                    "consensus_estimate_snapshots",
                ),
                "macro_series": _count_table(connection, "macro_series"),
                "industry_series": _count_table(connection, "industry_series"),
                "recession_periods": _count_table(connection, "recession_periods"),
                "raw_objects": _count_table(connection, "raw_objects"),
                "ingestion_runs": _count_table(connection, "ingestion_runs"),
                "watchlists": _count_table(connection, "watchlists"),
                "watchlist_items": _count_table(connection, "watchlist_items"),
            }
        return {"reachable": True, "counts": counts, "error": None}
    except Exception as exc:
        return {"reachable": False, "counts": {}, "error": exc.__class__.__name__}


def top_market_cap_universe_from_postgres(
    market: str,
    *,
    limit: int = 10,
) -> dict[str, Any] | None:
    if not postgres_enabled():
        return None
    market_key = market.upper()
    if market_key not in {"KR", "US", "JP"}:
        return None
    try:
        with get_engine().connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT DISTINCT ON (s.ticker)
                           s.ticker,
                           c.name,
                           c.country,
                           s.currency AS security_currency,
                           COALESCE(s.exchange, c.country) AS exchange,
                           p.trade_date,
                           p.close_price,
                           p.currency AS price_currency,
                           p.source,
                           p.source_trace
                    FROM price_bars p
                    JOIN securities s ON s.id = p.security_id
                    LEFT JOIN companies c ON c.id = s.company_id
                    WHERE (
                        (:market = 'KR' AND (
                            s.ticker LIKE '%.KS'
                            OR c.country = 'KR'
                            OR s.currency = 'KRW'
                        ))
                        OR (:market = 'JP' AND (
                            s.ticker LIKE '%.T'
                            OR c.country = 'JP'
                            OR s.currency = 'JPY'
                        ))
                        OR (:market = 'US' AND (
                            s.ticker NOT LIKE '%.KS'
                            AND s.ticker NOT LIKE '%.T'
                            AND (c.country = 'US' OR s.currency = 'USD')
                        ))
                    )
                      AND (
                        (
                          NULLIF(BTRIM(p.source_trace ->> 'market_cap_krw_millions'), '')
                            IS NOT NULL
                          AND LOWER(BTRIM(p.source_trace ->> 'market_cap_krw_millions'))
                            NOT IN ('unknown', 'n/a', 'na', 'none')
                        )
                        OR (
                          NULLIF(BTRIM(p.source_trace ->> 'market_cap'), '') IS NOT NULL
                          AND LOWER(BTRIM(p.source_trace ->> 'market_cap'))
                            NOT IN ('unknown', 'n/a', 'na', 'none')
                        )
                      )
                    ORDER BY s.ticker, p.trade_date DESC, p.created_at DESC
                    """
                ),
                {"market": market_key},
            ).mappings().all()
    except Exception:
        return None

    ranked_rows: list[dict[str, Any]] = []
    for row in rows:
        trace = dict(row["source_trace"] or {})
        market_structure = _market_structure_from_price_trace(
            trace,
            str(row["price_currency"] or row["security_currency"] or ""),
        )
        market_cap = _decimal_from_trace(market_structure["market_cap"])
        if market_cap is None or market_cap <= 0:
            continue
        market_cap_trace = dict(market_structure["market_cap_source_trace"] or trace)
        market_cap_trace["fact_name"] = "priority_universe.market_cap_rank"
        market_cap_trace["method"] = (
            market_cap_trace.get("method") or "SOURCE_BACKED_LATEST_MARKET_CAP_RANK"
        )
        market_cap_trace["formula"] = (
            "market_cap_rank = descending latest source-backed market_cap from price_bars"
        )
        ranked_rows.append(
            {
                "ticker": row["ticker"],
                "name": row["name"] or row["ticker"],
                "market": market_key,
                "currency": (
                    (market_structure.get("market_cap_source_trace") or {}).get("currency")
                    or row["price_currency"]
                    or row["security_currency"]
                ),
                "market_cap": _decimal_str(market_cap),
                "market_cap_rank_input_date": _date_str(row["trade_date"]),
                "rank_policy": "source_backed_latest_market_cap",
                "source": row["source"],
                "source_trace": market_cap_trace,
            }
        )

    ranked_rows.sort(key=lambda item: Decimal(str(item["market_cap"])), reverse=True)
    ranked_rows = ranked_rows[:limit]
    if not ranked_rows:
        return None

    for index, row in enumerate(ranked_rows, start=1):
        row["market_cap_rank"] = index
        row["coverage_priority_order"] = index

    coverage_meta = top_market_cap_rank_coverage_meta(
        rank_count=len(ranked_rows),
        rank_limit=limit,
    )

    return {
        "universe_id": f"{market_key.lower()}-top-market-cap-source-backed-v1",
        "label": f"{market_key} source-backed top market-cap universe",
        "market": market_key,
        "currency": ranked_rows[0]["currency"],
        "data_mode": "source_backed",
        "rank_policy": "source_backed_latest_market_cap",
        "rank_coverage_status": coverage_meta["rank_coverage_status"],
        "rank_count": coverage_meta["rank_count"],
        "rank_limit": coverage_meta["rank_limit"],
        "missing_rank_slots": coverage_meta["missing_rank_slots"],
        "note": (
            "Top market-cap universe computed from latest source-backed market-cap "
            "evidence already persisted in price_bars."
        ),
        "source_trace": {
            "source": "postgres_price_bars",
            "source_type": "source_backed_market_cap_rank",
            "source_document_id": f"postgres-price-bars-latest-market-cap-{market_key.lower()}",
            "filing_id": f"POSTGRES-{market_key}-LATEST-MARKET-CAP",
            "period": ranked_rows[0]["market_cap_rank_input_date"],
            "unit": "market_cap",
            "currency": ranked_rows[0]["currency"],
            "method": "source_backed_latest_market_cap_rank",
            "formula": (
                "Select the latest source-backed market_cap evidence per ticker "
                "from price_bars, then sort descending and keep the top rows."
            ),
            "quality_status": coverage_meta["quality_status"],
            "quality_flags": coverage_meta["quality_flags"],
            "input_rows": len(rows),
            "rank_coverage_status": coverage_meta["rank_coverage_status"],
            "rank_count": coverage_meta["rank_count"],
            "rank_limit": coverage_meta["rank_limit"],
            "missing_rank_slots": coverage_meta["missing_rank_slots"],
        },
        "tickers": ranked_rows,
    }


def macro_series_from_postgres(
    *,
    source: str | None = None,
    series_id: str | None = None,
    frequency: str | None = None,
    start_date: Any | None = None,
    end_date: Any | None = None,
    limit: int = 250,
) -> list[dict[str, Any]] | None:
    if not postgres_enabled():
        return None
    bounded_limit = max(1, min(limit, 1000))
    with get_engine().connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT series_id, observation_date, value, unit, frequency, source,
                       source_url, source_document_id, source_trace
                FROM macro_series
                WHERE (:source IS NULL OR source = :source)
                  AND (:series_id IS NULL OR series_id = :series_id)
                  AND (:frequency IS NULL OR frequency = :frequency)
                  AND (:start_date IS NULL OR observation_date >= :start_date)
                  AND (:end_date IS NULL OR observation_date <= :end_date)
                ORDER BY observation_date DESC, source, series_id
                LIMIT :limit
                """
            ),
            {
                "source": source.lower() if source else None,
                "series_id": series_id.upper() if series_id else None,
                "frequency": frequency,
                "start_date": start_date,
                "end_date": end_date,
                "limit": bounded_limit,
            },
        ).mappings().all()
    return [_macro_series_row(row) for row in rows]


def industry_series_from_postgres(
    *,
    market: str | None = None,
    source: str | None = None,
    category: str | None = None,
    series_id: str | None = None,
    limit: int = 250,
) -> list[dict[str, Any]] | None:
    if not postgres_enabled():
        return None
    bounded_limit = max(1, min(limit, 1000))
    with get_engine().connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT market, series_id, observation_date, value, unit, frequency,
                       category, region, industry, source, source_url,
                       source_document_id, dimensions, source_trace
                FROM industry_series
                WHERE (:market IS NULL OR market = :market)
                  AND (:source IS NULL OR source = :source)
                  AND (:category IS NULL OR category = :category)
                  AND (:series_id IS NULL OR series_id = :series_id)
                ORDER BY observation_date DESC, market, category, series_id
                LIMIT :limit
                """
            ),
            {
                "market": market.upper() if market else None,
                "source": source.lower() if source else None,
                "category": category,
                "series_id": series_id.upper() if series_id else None,
                "limit": bounded_limit,
            },
        ).mappings().all()
    return [_industry_series_row(row) for row in rows]


def research_metadata_from_postgres(
    ticker: str,
    *,
    limit: int = 25,
) -> dict[str, Any] | None:
    if not postgres_enabled():
        return None
    bounded_limit = max(1, min(limit, 100))
    normalized_ticker = ticker.upper()
    with get_engine().connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT r.source, r.ticker, r.identifier, r.source_url, r.blob_url,
                       r.content_hash, r.content_type, r.local_path, r.metadata,
                       r.created_at, r.source_document_id,
                       d.accession_number, d.form_type, d.source_url AS document_source_url,
                       d.metadata AS document_metadata
                FROM raw_objects r
                LEFT JOIN source_documents d ON d.id = r.source_document_id
                WHERE r.ticker = :ticker
                  AND r.source IN ('naver_search_research', 'hankyung_consensus_metadata')
                ORDER BY r.created_at DESC, r.source
                LIMIT :limit
                """
            ),
            {"ticker": normalized_ticker, "limit": bounded_limit},
        ).mappings().all()

    items: list[dict[str, Any]] = []
    for row in rows:
        items.extend(_research_metadata_items_from_row(row))

    source_trace = _research_metadata_collection_trace(normalized_ticker, items)
    quality_status = (
        "source_backed_research_metadata"
        if items
        else "source_backed_empty_research_metadata"
    )
    source_trace["quality_status"] = quality_status
    source_trace["item_count"] = len(items)
    return {
        "ticker": normalized_ticker,
        "data_mode": "source_backed",
        "policy": "metadata_only_no_financial_numbers",
        "quality_status": quality_status,
        "items": items,
        "source_trace": source_trace,
        "meta": {
            "source": "postgres",
            "sources": list(RESEARCH_METADATA_SOURCES),
            "financial_numbers_allowed": False,
            "row_count": len(rows),
            "item_count": len(items),
        },
    }


def source_coverage_from_postgres(
    tickers: str | list[str] | None = None,
    *,
    min_historical_years: int = 3,
    min_forecast_years: int = 5,
    require_consensus_forecast: bool = False,
) -> dict[str, Any] | None:
    expected_tickers = normalize_coverage_tickers(tickers)
    if not postgres_enabled():
        return None
    try:
        with get_engine().connect() as connection:
            rows = connection.execute(
                text(
                    """
                    WITH requested AS (
                      SELECT unnest(CAST(:tickers AS text[])) AS ticker
                    ),
                    scoped_securities AS (
                      SELECT s.id, s.ticker, c.name, c.country, s.currency,
                             COALESCE(s.exchange, c.country) AS market
                      FROM securities s
                      LEFT JOIN companies c ON c.id = s.company_id
                      WHERE s.ticker = ANY(:tickers)
                    ),
                    adjusted AS (
                      SELECT security_id,
                             COUNT(DISTINCT fiscal_year) FILTER (
                               WHERE adjusted_eps IS NOT NULL AND policy = :policy
                             ) AS adjusted_years,
                             MAX(fiscal_year) FILTER (
                               WHERE adjusted_eps IS NOT NULL AND policy = :policy
                             ) AS latest_adjusted_year,
                             COUNT(*) FILTER (
                               WHERE policy = :policy AND method LIKE 'S1%'
                             ) AS s1_periods,
                             COUNT(*) FILTER (
                               WHERE policy = :policy AND method LIKE 'S2%'
                             ) AS s2_periods,
                             COUNT(*) FILTER (
                               WHERE policy = :policy AND method LIKE 'S4%'
                             ) AS s4_periods
                      FROM adjusted_earnings
                      GROUP BY security_id
                    ),
                    prices AS (
                      SELECT security_id,
                             COUNT(DISTINCT fiscal_year) AS price_years,
                             MAX(fiscal_year) AS latest_price_year,
                             COUNT(DISTINCT fiscal_year) FILTER (
                               WHERE (
                                 NULLIF(
                                   BTRIM(source_trace ->> 'market_cap_krw_millions'),
                                   ''
                                 ) IS NOT NULL
                                 AND LOWER(
                                   BTRIM(source_trace ->> 'market_cap_krw_millions')
                                 ) NOT IN ('unknown', 'n/a', 'na', 'none')
                               )
                                  OR (
                                    NULLIF(BTRIM(source_trace ->> 'market_cap'), '')
                                      IS NOT NULL
                                    AND LOWER(BTRIM(source_trace ->> 'market_cap'))
                                      NOT IN ('unknown', 'n/a', 'na', 'none')
                                  )
                             ) AS market_cap_years,
                             COUNT(DISTINCT fiscal_year) FILTER (
                               WHERE (
                                 NULLIF(BTRIM(source_trace ->> 'listed_shares'), '')
                                   IS NOT NULL
                                 AND LOWER(BTRIM(source_trace ->> 'listed_shares'))
                                   NOT IN ('unknown', 'n/a', 'na', 'none')
                               )
                                  OR (
                                    NULLIF(BTRIM(source_trace ->> 'shares_outstanding'), '')
                                      IS NOT NULL
                                    AND LOWER(BTRIM(source_trace ->> 'shares_outstanding'))
                                      NOT IN ('unknown', 'n/a', 'na', 'none')
                                  )
                             ) AS listed_shares_years
                      FROM price_bars
                      GROUP BY security_id
                    ),
                    facts AS (
                      SELECT security_id,
                             COUNT(DISTINCT fiscal_year) AS financial_fact_years,
                             COUNT(DISTINCT tag) AS financial_fact_tags,
                             MAX(fiscal_year) AS latest_financial_fact_year
                      FROM financial_facts
                      GROUP BY security_id
                    ),
                    metrics AS (
                      SELECT security_id,
                             COUNT(DISTINCT fiscal_year) AS financial_metric_years,
                             COUNT(DISTINCT metric_key) AS financial_metric_keys,
                             ARRAY_AGG(DISTINCT metric_key ORDER BY metric_key) AS
                               available_metric_keys
                      FROM metric_values
                      GROUP BY security_id
                    ),
                    dividend_rows AS (
                      SELECT security_id,
                             COUNT(DISTINCT fiscal_year) AS dividend_years
                      FROM dividends
                      GROUP BY security_id
                    ),
                    consensus AS (
                      SELECT security_id,
                             COUNT(DISTINCT fiscal_year) FILTER (
                               WHERE metric_key = 'adjusted_operating_eps'
                                 AND LOWER(quality_status) IN :production_consensus_quality_statuses
                             ) AS consensus_forecast_years,
                             COUNT(DISTINCT fiscal_year) FILTER (
                               WHERE metric_key = 'adjusted_operating_eps'
                                 AND LOWER(estimate_case) IN ('median', 'current')
                                 AND estimate_value IS NOT NULL
                                 AND LOWER(quality_status) IN :production_consensus_quality_statuses
                             ) AS consensus_valuation_years,
                             COUNT(*) FILTER (
                               WHERE metric_key = 'adjusted_operating_eps'
                                 AND LOWER(quality_status) IN :production_consensus_quality_statuses
                             ) AS consensus_snapshots,
                             COUNT(*) FILTER (
                               WHERE metric_key = 'adjusted_operating_eps'
                                 AND LOWER(estimate_case) IN ('median', 'current')
                                 AND estimate_value IS NOT NULL
                                 AND LOWER(quality_status) IN :production_consensus_quality_statuses
                             ) AS consensus_valuation_snapshots,
                             MAX(fiscal_year) FILTER (
                               WHERE metric_key = 'adjusted_operating_eps'
                                 AND LOWER(quality_status) IN :production_consensus_quality_statuses
                             ) AS latest_consensus_year
                      FROM consensus_estimate_snapshots
                      GROUP BY security_id
                    ),
                    adjustment_rows AS (
                      SELECT security_id, COUNT(*) AS adjustment_rows
                      FROM adjustments
                      GROUP BY security_id
                    ),
                    documents AS (
                      SELECT security_id, COUNT(*) AS source_documents
                      FROM source_documents
                      GROUP BY security_id
                    ),
                    raws AS (
                      SELECT ticker, COUNT(*) AS raw_objects
                      FROM raw_objects
                      GROUP BY ticker
                    )
                    SELECT r.ticker,
                           CASE WHEN s.id IS NULL THEN 0 ELSE 1 END AS security_count,
                           s.name, s.country, s.currency, s.market,
                           COALESCE(a.adjusted_years, 0) AS adjusted_years,
                           COALESCE(a.latest_adjusted_year, NULL) AS latest_adjusted_year,
                           COALESCE(a.s1_periods, 0) AS s1_periods,
                           COALESCE(a.s2_periods, 0) AS s2_periods,
                           COALESCE(a.s4_periods, 0) AS s4_periods,
                           COALESCE(p.price_years, 0) AS price_years,
                           COALESCE(p.latest_price_year, NULL) AS latest_price_year,
                           COALESCE(p.market_cap_years, 0) AS market_cap_years,
                           COALESCE(p.listed_shares_years, 0) AS listed_shares_years,
                           COALESCE(f.financial_fact_years, 0) AS financial_fact_years,
                           COALESCE(f.financial_fact_tags, 0) AS financial_fact_tags,
                           COALESCE(f.latest_financial_fact_year, NULL) AS
                             latest_financial_fact_year,
                           COALESCE(m.financial_metric_years, 0) AS financial_metric_years,
                           COALESCE(m.financial_metric_keys, 0) AS financial_metric_keys,
                           COALESCE(m.available_metric_keys, ARRAY[]::text[]) AS
                             available_metric_keys,
                           COALESCE(d.dividend_years, 0) AS dividend_years,
                           COALESCE(c.consensus_forecast_years, 0) AS consensus_forecast_years,
                           COALESCE(c.consensus_valuation_years, 0) AS
                             consensus_valuation_years,
                           COALESCE(c.consensus_snapshots, 0) AS consensus_snapshots,
                           COALESCE(c.consensus_valuation_snapshots, 0) AS
                             consensus_valuation_snapshots,
                           COALESCE(c.latest_consensus_year, NULL) AS latest_consensus_year,
                           COALESCE(ar.adjustment_rows, 0) AS adjustment_rows,
                           COALESCE(doc.source_documents, 0) AS source_documents,
                           COALESCE(raw.raw_objects, 0) AS raw_objects
                    FROM requested r
                    LEFT JOIN scoped_securities s ON s.ticker = r.ticker
                    LEFT JOIN adjusted a ON a.security_id = s.id
                    LEFT JOIN prices p ON p.security_id = s.id
                    LEFT JOIN facts f ON f.security_id = s.id
                    LEFT JOIN metrics m ON m.security_id = s.id
                    LEFT JOIN dividend_rows d ON d.security_id = s.id
                    LEFT JOIN consensus c ON c.security_id = s.id
                    LEFT JOIN adjustment_rows ar ON ar.security_id = s.id
                    LEFT JOIN documents doc ON doc.security_id = s.id
                    LEFT JOIN raws raw ON raw.ticker = r.ticker
                    ORDER BY array_position(CAST(:tickers AS text[]), r.ticker)
                    """
                ).bindparams(
                    bindparam(
                        "production_consensus_quality_statuses",
                        expanding=True,
                    )
                ),
                {
                    "tickers": expected_tickers,
                    "policy": DEFAULT_POLICY_KEY,
                    "production_consensus_quality_statuses": PRODUCTION_CONSENSUS_QUALITY_STATUSES,
                },
            ).mappings().all()
        return build_source_coverage_report(
            [dict(row) for row in rows],
            expected_tickers,
            min_historical_years=min_historical_years,
            min_forecast_years=min_forecast_years,
            require_consensus_forecast=require_consensus_forecast,
            postgres_reachable=True,
        )
    except Exception as exc:
        return build_source_coverage_report(
            [],
            expected_tickers,
            min_historical_years=min_historical_years,
            min_forecast_years=min_forecast_years,
            require_consensus_forecast=require_consensus_forecast,
            postgres_reachable=False,
            error=exc.__class__.__name__,
        )


def _ensure_watchlist(owner_key: str, name: str) -> dict[str, Any]:
    with get_engine().begin() as connection:
        row = connection.execute(
            text(
                """
                INSERT INTO watchlists (id, owner_key, name, created_at)
                VALUES (:id, :owner_key, :name, :created_at)
                ON CONFLICT ON CONSTRAINT uq_watchlists_owner_name DO UPDATE SET
                  name = EXCLUDED.name
                RETURNING id, owner_key, name
                """
            ),
            {
                "id": uuid.uuid4(),
                "owner_key": owner_key,
                "name": name,
                "created_at": datetime.now(UTC),
            },
        ).mappings().one()
    return {"id": row["id"], "owner_key": row["owner_key"], "name": row["name"]}


def _watchlist_item(row: Any, snapshot: dict[str, Any] | None) -> dict[str, Any]:
    trace = dict(row["source_trace"] or {})
    trace.setdefault("source_type", "postgres")
    trace.setdefault("quality_status", "source_backed")
    if snapshot is not None:
        trace["snapshot_source_trace"] = snapshot.get("source_trace") or {}
    return {
        "ticker": row["ticker"],
        "name": (snapshot or {}).get("name") or row["company_name"] or row["ticker"],
        "market": (snapshot or {}).get("market") or row["exchange"] or row["country"],
        "country": (snapshot or {}).get("country") or row["country"],
        "currency": (snapshot or {}).get("currency") or row["currency"],
        "current_price": (snapshot or {}).get("current_price"),
        "per": (snapshot or {}).get("per"),
        "dividend_yield": (snapshot or {}).get("dividend_yield"),
        "eps_cagr": (snapshot or {}).get("eps_cagr"),
        "quality_status": (
            ((snapshot or {}).get("source_trace") or {}).get("quality_status")
            or trace.get("quality_status")
        ),
        "note": row["note"],
        "source_trace": trace,
    }


def _security_row(connection: Any, ticker: str) -> Any | None:
    return connection.execute(
        text(
            """
            SELECT s.id, s.ticker, s.currency, COALESCE(s.exchange, c.country) AS market,
                   c.name, c.country
            FROM securities s
            LEFT JOIN companies c ON c.id = s.company_id
            WHERE s.ticker = :ticker
            """
        ),
        {"ticker": ticker.upper()},
    ).mappings().first()


def _adjusted_rows_for_security(connection: Any, security_id: Any) -> list[Any]:
    return list(
        connection.execute(
            text(
                """
                SELECT DISTINCT ON (fiscal_year)
                       fiscal_year, adjusted_eps, gaap_eps_diluted, method, confidence,
                       quality_status, formula, source_trace, sector_policy
                FROM adjusted_earnings
                WHERE security_id = :security_id
                  AND adjusted_eps IS NOT NULL
                  AND policy = :policy
                ORDER BY fiscal_year, accepted_at DESC NULLS LAST,
                         computed_at DESC NULLS LAST
                """
            ),
            {"security_id": security_id, "policy": DEFAULT_POLICY_KEY},
        ).mappings()
    )


def _latest_metric_lookup(connection: Any, security_id: Any) -> dict[str, Any]:
    rows = connection.execute(
        text(
            """
            SELECT DISTINCT ON (metric_key)
                   metric_key, fiscal_year, value, method, quality_status, source_trace
            FROM metric_values
            WHERE security_id = :security_id
            ORDER BY metric_key, fiscal_year DESC, created_at DESC
            """
        ),
        {"security_id": security_id},
    ).mappings().all()
    return {row["metric_key"]: row for row in rows}


def _metric_value(metrics: dict[str, Any], key: str) -> str | None:
    row = metrics.get(key)
    if not row:
        return None
    return _decimal_str(row["value"])


def _market_structure_from_price_trace(
    trace: dict[str, Any],
    currency: str | None,
) -> dict[str, Any]:
    market_cap = _decimal_from_trace(trace.get("market_cap"))
    market_cap_formula = "Source trace market_cap imported as market capitalization"
    market_cap_currency = str(trace.get("currency") or currency or "")
    if market_cap is None:
        marcap_krw_millions = _decimal_from_trace(trace.get("market_cap_krw_millions"))
        if marcap_krw_millions is not None:
            market_cap = marcap_krw_millions * Decimal("1000000")
            market_cap_currency = "KRW"
            market_cap_formula = (
                "FinanceData marcap Marcap column reported in KRW millions "
                "multiplied by 1,000,000"
            )
    listed_shares = _decimal_from_trace(trace.get("listed_shares"))
    return {
        "market_cap": _decimal_str(market_cap),
        "listed_shares": _decimal_str(listed_shares),
        "market_cap_source_trace": _market_structure_trace(
            trace,
            fact_name="snapshot.market_cap",
            formula=market_cap_formula,
            unit="market_cap",
            currency=market_cap_currency,
        )
        if market_cap is not None
        else None,
        "listed_shares_source_trace": _market_structure_trace(
            trace,
            fact_name="snapshot.listed_shares",
            formula="Source trace listed_shares imported as listed shares outstanding",
            unit="shares",
            currency="N/A",
        )
        if listed_shares is not None
        else None,
    }


def _latest_usd_fx_rates(connection: Any) -> dict[str, dict[str, Any]]:
    rows = connection.execute(
        text(
            """
            SELECT DISTINCT ON (series_id)
                   series_id, observation_date, value, unit, frequency, source,
                   source_url, source_document_id, source_trace
            FROM macro_series
            WHERE series_id IN ('DEXKOUS', 'DEXJPUS')
              AND value IS NOT NULL
            ORDER BY series_id, observation_date DESC
            """
        )
    ).mappings().all()
    rates: dict[str, dict[str, Any]] = {}
    for row in rows:
        series_id = str(row["series_id"]).upper()
        currency = next(
            (
                code
                for code, mapped_series_id in FRED_USD_PER_CURRENCY_SERIES.items()
                if mapped_series_id == series_id
            ),
            None,
        )
        value = _decimal_or_none(row["value"])
        if not currency or value is None or value <= 0:
            continue
        rates[currency] = {
            "rate": value,
            "source_trace": _macro_series_row(row)["source_trace"],
        }
    return rates


def _market_cap_usd_from_snapshot(
    snapshot: dict[str, Any],
    fx_rates: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    market_cap = _decimal_or_none(snapshot.get("market_cap"))
    currency = str(snapshot.get("currency") or "").upper()
    snapshot_trace = dict(snapshot.get("source_trace") or {})
    market_cap_trace = dict(
        snapshot_trace.get("market_cap_source_trace")
        if isinstance(snapshot_trace.get("market_cap_source_trace"), dict)
        else snapshot_trace
    )
    if market_cap is None:
        return {
            "market_cap_usd": None,
            "market_cap_usd_source_trace": _missing_market_cap_usd_trace(
                market_cap_trace,
                currency,
                "missing_market_cap",
            ),
        }
    if currency == "USD":
        return {
            "market_cap_usd": _decimal_str(market_cap),
            "market_cap_usd_source_trace": _market_cap_usd_trace(
                market_cap_trace,
                currency,
                market_cap,
                None,
            ),
        }
    fx = fx_rates.get(currency)
    if not fx:
        return {
            "market_cap_usd": None,
            "market_cap_usd_source_trace": _missing_market_cap_usd_trace(
                market_cap_trace,
                currency,
                "missing_fx_rate",
            ),
        }
    rate = fx["rate"]
    market_cap_usd = (market_cap / rate).quantize(Decimal("0.01"))
    return {
        "market_cap_usd": _decimal_str(market_cap_usd),
        "market_cap_usd_source_trace": _market_cap_usd_trace(
            market_cap_trace,
            currency,
            market_cap,
            fx,
        ),
    }


def _market_cap_usd_trace(
    market_cap_trace: dict[str, Any],
    currency: str,
    market_cap: Decimal,
    fx: dict[str, Any] | None,
) -> dict[str, Any]:
    output = dict(market_cap_trace)
    output["fact_name"] = "screener.market_cap_usd"
    output["unit"] = "market_cap"
    output["currency"] = "USD"
    if currency == "USD":
        output["formula"] = "market_cap_usd = market_cap because source currency is USD"
        output["calculation_inputs"] = {
            "market_cap": _decimal_str(market_cap),
            "market_cap_currency": currency,
        }
    else:
        fx_trace = dict((fx or {}).get("source_trace") or {})
        output["formula"] = "market_cap_usd = market_cap / local_currency_per_usd_fx_rate"
        output["calculation_inputs"] = {
            "market_cap": _decimal_str(market_cap),
            "market_cap_currency": currency,
            "fx_rate": _decimal_str((fx or {}).get("rate")),
            "fx_series_id": fx_trace.get("series_id"),
            "fx_source_trace": fx_trace,
        }
    output.setdefault("source_type", market_cap_trace.get("source_type") or "market_data")
    output.setdefault(
        "quality_status",
        market_cap_trace.get("quality_status") or "source_backed_market_data",
    )
    return output


def _missing_market_cap_usd_trace(
    market_cap_trace: dict[str, Any],
    currency: str,
    reason: str,
) -> dict[str, Any]:
    output = dict(market_cap_trace)
    output["fact_name"] = "screener.market_cap_usd"
    output["unit"] = "market_cap"
    output["currency"] = "USD"
    output["formula"] = (
        "market_cap_usd unavailable until market_cap and FX source traces are available"
    )
    output["quality_status"] = "warning"
    output["missing_reason"] = reason
    output["market_cap_currency"] = currency or None
    return output


def _screener_snapshot_trace(
    snapshot_trace: dict[str, Any],
    market_cap_usd: dict[str, Any],
) -> dict[str, Any]:
    trace = dict(snapshot_trace or {})
    if market_cap_usd.get("market_cap_usd_source_trace"):
        trace["market_cap_usd_source_trace"] = market_cap_usd["market_cap_usd_source_trace"]
    return trace


def _market_structure_trace(
    trace: dict[str, Any],
    *,
    fact_name: str,
    formula: str,
    unit: str,
    currency: str,
) -> dict[str, Any]:
    output = dict(trace)
    output["fact_name"] = fact_name
    output["unit"] = unit
    output["currency"] = currency
    output["formula"] = formula
    output.setdefault("source_type", trace.get("source_type") or "market_data")
    output.setdefault(
        "quality_status",
        trace.get("quality_status") or "source_backed_market_data",
    )
    return output


def _decimal_from_trace(value: Any) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        decimal = Decimal(str(value).replace(",", ""))
    except Exception:
        return None
    return decimal if decimal.is_finite() else None


def _metric_trace(row: Any) -> dict[str, Any]:
    trace = dict(row["source_trace"] or {})
    trace.setdefault("source_type", "postgres")
    trace.setdefault("method", row["method"])
    trace.setdefault("quality_status", row["quality_status"])
    return trace


def _financial_fact_row(ticker: str, row: Any) -> dict[str, Any]:
    trace = dict(row["source_trace"] or {})
    period = f"{row['fiscal_year']}{row['fiscal_period']}"
    source_document_id = row["source_document_id"]
    trace.setdefault("source_type", row["source"] or "financial_facts")
    trace.setdefault(
        "source_document_id",
        str(source_document_id) if source_document_id else f"{ticker}-{period}-{row['tag']}",
    )
    trace.setdefault("filing_id", row["accession_number"] or f"{ticker}-{period}-financial-fact")
    trace.setdefault("accession_number", row["accession_number"])
    trace.setdefault("period", period)
    trace.setdefault(
        "period_start",
        row["period_start"].isoformat() if row["period_start"] else None,
    )
    trace.setdefault("period_end", row["period_end"].isoformat() if row["period_end"] else None)
    trace.setdefault("unit", row["unit"])
    trace.setdefault("currency", row["currency"])
    trace.setdefault("formula", "source reported financial fact")
    trace.setdefault("method", row["source"])
    trace.setdefault("quality_status", row["quality_status"])
    trace.setdefault("source_url", row["source_url"])
    trace["taxonomy"] = row["taxonomy"]
    trace["tag"] = row["tag"]
    return {
        "ticker": ticker,
        "taxonomy": row["taxonomy"],
        "tag": row["tag"],
        "label": row["label"],
        "fiscal_year": int(row["fiscal_year"]),
        "fiscal_period": row["fiscal_period"],
        "value": _decimal_str(row["value"]),
        "unit": row["unit"],
        "currency": row["currency"],
        "source": row["source"],
        "quality_status": row["quality_status"],
        "source_trace": trace,
        "metadata": row["metadata"] or {},
    }


def _macro_series_row(row: Any) -> dict[str, Any]:
    source_trace = dict(row["source_trace"] or {})
    source_trace.setdefault("source_type", row["source"])
    source_trace.setdefault("source_document_id", str(row["source_document_id"] or ""))
    source_trace.setdefault("filing_id", f"{row['source']}:{row['series_id']}")
    source_trace.setdefault("period", _date_str(row["observation_date"]))
    source_trace.setdefault("unit", row["unit"] or "reported")
    source_trace.setdefault("currency", "N/A")
    source_trace.setdefault("formula", "Source reported macro observation value")
    source_trace.setdefault("quality_status", "source_backed_macro")
    if row["source_url"]:
        source_trace.setdefault("source_url", row["source_url"])
    return {
        "series_id": row["series_id"],
        "observation_date": _date_str(row["observation_date"]),
        "value": _decimal_str(row["value"]),
        "unit": row["unit"],
        "frequency": row["frequency"],
        "source": row["source"],
        "source_url": row["source_url"],
        "source_document_id": str(row["source_document_id"] or ""),
        "source_trace": source_trace,
    }


def _industry_series_row(row: Any) -> dict[str, Any]:
    source_trace = dict(row["source_trace"] or {})
    source_trace.setdefault("source_type", row["source"])
    source_trace.setdefault("source_document_id", str(row["source_document_id"] or ""))
    source_trace.setdefault("filing_id", f"{row['source']}:{row['series_id']}")
    source_trace.setdefault("period", _date_str(row["observation_date"]))
    source_trace.setdefault("unit", row["unit"] or "reported")
    source_trace.setdefault("currency", "N/A")
    source_trace.setdefault("formula", "Official statistics reported observation value")
    source_trace.setdefault("quality_status", "source_backed_industry")
    if row["source_url"]:
        source_trace.setdefault("source_url", row["source_url"])
    return {
        "market": row["market"],
        "series_id": row["series_id"],
        "observation_date": _date_str(row["observation_date"]),
        "value": _decimal_str(row["value"]),
        "unit": row["unit"],
        "frequency": row["frequency"],
        "category": row["category"],
        "region": row["region"],
        "industry": row["industry"],
        "source": row["source"],
        "source_url": row["source_url"],
        "source_document_id": str(row["source_document_id"] or ""),
        "dimensions": row["dimensions"] or {},
        "source_trace": source_trace,
    }


def _recession_period_row(row: Any) -> dict[str, Any]:
    trace = dict(row["source_trace"] or {})
    trace.setdefault("source_type", "fred")
    trace.setdefault("source_document_id", f"fred-{row['series_id']}-recession-period")
    trace.setdefault("filing_id", f"fred-{row['series_id']}")
    period_end = _date_str(row["end_date"]) or "open"
    trace.setdefault("period", f"{_date_str(row['start_date'])}:{period_end}")
    trace.setdefault("unit", "indicator")
    trace.setdefault("currency", "N/A")
    trace.setdefault("formula", "Contiguous FRED USREC observations equal to 1")
    trace.setdefault("method", "FRED_RECESSION_BAND")
    trace.setdefault("quality_status", "source_backed_macro")
    trace.setdefault("source_url", "https://fred.stlouisfed.org/series/USREC")
    return {
        "series_id": row["series_id"],
        "start_date": _date_str(row["start_date"]),
        "end_date": _date_str(row["end_date"]),
        "source": row["source"],
        "source_trace": trace,
    }


def _financial_trace(adjusted: Any | None, metrics: dict[str, Any]) -> dict[str, Any]:
    if adjusted:
        trace = dict(adjusted["source_trace"] or {})
        trace.setdefault("source_type", "postgres")
        trace.setdefault("method", adjusted["method"])
        trace.setdefault("quality_status", adjusted["quality_status"])
        trace.setdefault("formula", adjusted["formula"])
        return trace
    for row in metrics.values():
        trace = dict(row["source_trace"] or {})
        trace.setdefault("source_type", "postgres")
        trace.setdefault("method", row["method"])
        trace.setdefault("quality_status", row["quality_status"])
        return trace
    return {"source_type": "postgres", "quality_status": "warning"}


def _ratio(numerator: Decimal, denominator: Decimal) -> str | None:
    if denominator == 0:
        return None
    return _decimal_str((numerator / denominator).quantize(Decimal("0.01")))


def _percent_ratio(numerator: Decimal, denominator: Decimal) -> str:
    if denominator == 0:
        return "0.00"
    return _decimal_str(((numerator / denominator) * 100).quantize(Decimal("0.01"))) or "0.00"


def _eps_cagr(rows: list[Any]) -> str | None:
    positive_rows = [
        row
        for row in rows
        if row["adjusted_eps"] is not None and Decimal(str(row["adjusted_eps"])) > 0
    ]
    if len(positive_rows) < 2:
        return None
    start = positive_rows[0]
    end = positive_rows[-1]
    years = int(end["fiscal_year"]) - int(start["fiscal_year"])
    if years <= 0:
        return None
    start_eps = float(Decimal(str(start["adjusted_eps"])))
    end_eps = float(Decimal(str(end["adjusted_eps"])))
    cagr = ((end_eps / start_eps) ** (1 / years) - 1) * 100
    return f"{cagr:.2f}"


def _normal_multiple_from_postgres(ticker: str) -> Decimal | None:
    db_payload = valuation_points_from_postgres(ticker, "adjusted_operating")
    if db_payload is None:
        return None
    points, _, _ = db_payload
    valuation_rows = build_valuation_map(points)
    if not valuation_rows:
        return None
    return valuation_rows[-1].normal_multiple


def _portfolio_transactions_from_postgres(owner_key: str) -> list[PortfolioTransaction]:
    with get_engine().connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT trade_date, ticker, side, quantity, price, currency, sector
                FROM portfolio_transactions
                WHERE owner_key = :owner_key
                ORDER BY trade_date, ticker, created_at
                """
            ),
            {"owner_key": owner_key},
        ).mappings().all()
    return [
        PortfolioTransaction(
            trade_date=row["trade_date"],
            ticker=row["ticker"],
            side=row["side"],
            quantity=Decimal(str(row["quantity"])),
            price=Decimal(str(row["price"])),
            currency=row["currency"],
            sector=row["sector"],
        )
        for row in rows
    ]


def _latest_prices_for_portfolio(tickers: set[str]) -> dict[str, Decimal]:
    if not tickers:
        return {}
    with get_engine().connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT DISTINCT ON (s.ticker) s.ticker, p.close_price
                FROM securities s
                JOIN price_bars p ON p.security_id = s.id
                WHERE s.ticker = ANY(:tickers)
                ORDER BY s.ticker, p.trade_date DESC
                """
            ),
            {"tickers": list(tickers)},
        ).mappings().all()
    return {row["ticker"]: Decimal(str(row["close_price"])) for row in rows}


def _empty_portfolio(quality_status: str) -> dict[str, Any]:
    return {
        "as_of": date.today().isoformat(),
        "holdings": [],
        "total_market_value": Decimal("0.00"),
        "xirr": None,
        "sector_weights": {},
        "source_trace": {
            "source_type": "postgres",
            "source_document_id": "portfolio-empty",
            "filing_id": "portfolio-empty",
            "period": "current",
            "unit": "portfolio",
            "currency": "mixed",
            "formula": "no persisted portfolio transactions",
            "quality_status": quality_status,
        },
    }


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _json(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


def _count_table(connection: Any, table_name: str) -> int:
    return int(connection.execute(text(f"SELECT count(*) FROM {table_name}")).scalar_one())


def _waterfall_by_year(
    connection,
    security_id: Any,
    policy_key: str,
    policy_base: str,
    allow_policy_prefix: bool,
) -> dict[tuple[Any, int, str], list[dict[str, Any]]]:
    rows = connection.execute(
        text(
            """
            SELECT a.adjusted_earnings_id, a.fiscal_year, a.fiscal_period, a.item_label,
                   a.canonical_category, a.pretax_amount, a.tax_effect,
                   a.after_tax_impact, a.policy_included, a.recurring_flag,
                   a.source_trace, ae.diluted_shares
            FROM adjustments a
            LEFT JOIN adjusted_earnings ae ON ae.id = a.adjusted_earnings_id
            WHERE a.security_id = :security_id
              AND a.adjusted_earnings_id IS NOT NULL
              AND (
                ae.policy = :policy_key
                OR (:allow_policy_prefix AND ae.policy LIKE :policy_prefix)
              )
            ORDER BY a.fiscal_year, a.fiscal_period, a.item_label
            """
        ),
        {
            "security_id": security_id,
            "policy_key": policy_key,
            "policy_prefix": f"{policy_base}|%",
            "allow_policy_prefix": allow_policy_prefix,
        },
    ).mappings().all()
    grouped: dict[tuple[Any, int, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (row["adjusted_earnings_id"], int(row["fiscal_year"]), row["fiscal_period"])
        grouped.setdefault(key, []).append(
            {
                "label": row["item_label"],
                "category": row["canonical_category"],
                "pretax_amount": _decimal_str(row["pretax_amount"]),
                "tax_effect": _decimal_str(row["tax_effect"]),
                "after_tax_impact": _decimal_str(row["after_tax_impact"]),
                "eps_impact": _eps_impact(row["after_tax_impact"], row["diluted_shares"]),
                "included_by_policy": row["policy_included"],
                "recurring": row["recurring_flag"],
                "source_trace": row["source_trace"] or {},
            }
        )
    return grouped


def _postgres_forecast_cases(rows: list[Any], currency: str) -> list[dict[str, Any]]:
    by_case = {row["estimate_case"]: row for row in rows}
    return [
        {
            "case": case,
            "growth_rate_pct": _decimal_str(by_case[case]["growth_rate_pct"]),
            "estimate_eps": _decimal_str(by_case[case]["estimate_value"]),
            "source_trace": _forecast_trace_from_row(by_case[case], currency),
        }
        for case in ("low", "median", "high")
        if case in by_case
    ]


def _postgres_forecast_revisions(rows: list[Any], currency: str) -> list[dict[str, Any]]:
    median_rows = [row for row in rows if row["estimate_case"] in {"median", "current"}]
    median_rows = sorted(median_rows, key=lambda row: row["snapshot_date"])[-4:]
    revisions: list[dict[str, Any]] = []
    previous_estimate: Decimal | None = None
    for index, row in enumerate(median_rows):
        estimate = Decimal(str(row["estimate_value"]))
        revision_delta = None
        if previous_estimate and previous_estimate > 0:
            revision_delta = (((estimate / previous_estimate) - 1) * 100).quantize(
                Decimal("0.01")
            )
        label = "current" if index == len(median_rows) - 1 else row["snapshot_date"].isoformat()
        revisions.append(
            {
                "as_of_label": label,
                "age_months": None,
                "estimate_eps": _decimal_str(row["estimate_value"]),
                "analyst_count": row["analyst_count"] or 0,
                "revision_delta_pct": _decimal_str(revision_delta),
                "quality_status": row["quality_status"],
                "source_trace": _forecast_trace_from_row(row, currency),
            }
        )
        previous_estimate = estimate
    return revisions


def _postgres_forecast_sentiment(revisions: list[dict[str, Any]]) -> dict[str, Any]:
    latest_delta = (
        Decimal(str(revisions[-1]["revision_delta_pct"] or "0"))
        if revisions
        else Decimal("0")
    )
    label = "positive" if latest_delta > 0 else "negative" if latest_delta < 0 else "neutral"
    analyst_count = int(revisions[-1]["analyst_count"] or 0) if revisions else 0
    up_revisions = max(0, analyst_count // 3) if latest_delta > 0 else max(0, analyst_count // 6)
    down_revisions = max(0, analyst_count // 6) if latest_delta > 0 else max(0, analyst_count // 3)
    return {
        "label": label,
        "net_revision_score_pct": _decimal_str(latest_delta),
        "up_revisions": up_revisions,
        "down_revisions": down_revisions,
        "unchanged": max(0, analyst_count - up_revisions - down_revisions),
        "quality_status": revisions[-1]["quality_status"] if revisions else "warning",
    }


def _postgres_scorecard(
    ticker: str,
    snapshots: list[Any],
    actual_rows: list[Any],
    currency: str,
) -> dict[str, Any]:
    snapshots_by_year: dict[int, list[Any]] = {}
    for row in snapshots:
        if row["estimate_case"] in {"median", "current"}:
            snapshots_by_year.setdefault(int(row["fiscal_year"]), []).append(row)

    rows: list[dict[str, Any]] = []
    for actual in actual_rows:
        fiscal_year = int(actual["fiscal_year"])
        estimates = snapshots_by_year.get(fiscal_year, [])
        estimate_1y = _snapshot_for_scorecard(estimates, years_prior=1)
        estimate_2y = _snapshot_for_scorecard(estimates, years_prior=2)
        if not estimate_1y and not estimate_2y:
            continue
        actual_eps = Decimal(str(actual["adjusted_eps"] or actual["gaap_eps_diluted"] or "0"))
        estimate_1y_eps = (
            Decimal(str(estimate_1y["estimate_value"])) if estimate_1y else None
        )
        estimate_2y_eps = (
            Decimal(str(estimate_2y["estimate_value"])) if estimate_2y else None
        )
        error_1y = (
            _estimate_error_pct(estimate_1y_eps, actual_eps)
            if estimate_1y_eps is not None
            else None
        )
        error_2y = (
            _estimate_error_pct(estimate_2y_eps, actual_eps)
            if estimate_2y_eps is not None
            else None
        )
        quality_row = estimate_1y or estimate_2y
        rows.append(
            {
                "fiscal_year": fiscal_year,
                "actual_eps": _decimal_str(actual_eps),
                "estimate_1y_prior": _decimal_str(estimate_1y_eps),
                "estimate_2y_prior": _decimal_str(estimate_2y_eps),
                "error_1y_pct": _decimal_str(error_1y),
                "error_2y_pct": _decimal_str(error_2y),
                "result_1y": _scorecard_result(error_1y, Decimal("10")),
                "result_2y": _scorecard_result(error_2y, Decimal("20")),
                "quality_status": quality_row["quality_status"],
                "source_trace": _forecast_trace_from_row(quality_row, currency),
            }
        )
    return {
        "ticker": ticker,
        "status": "source_backed_consensus_snapshots" if rows else "pending_actual_overlap",
        "rows": rows,
        "summary": {
            "hit_rate_1y_pct": _hit_rate(rows, "result_1y"),
            "hit_rate_2y_pct": _hit_rate(rows, "result_2y"),
            "required_source": "point_in_time_consensus_snapshots",
        },
    }


def _snapshot_for_scorecard(rows: list[Any], years_prior: int) -> Any | None:
    if not rows:
        return None
    period_end = _period_end_for_snapshot(rows)
    cutoff = period_end - timedelta(days=365 * years_prior)
    candidates = [row for row in rows if row["snapshot_date"] <= cutoff]
    if not candidates:
        return None
    return max(candidates, key=lambda row: row["snapshot_date"])


def _period_end_for_snapshot(rows: list[Any]) -> date:
    for row in rows:
        if row["period_end"]:
            return row["period_end"]
    return date(int(rows[0]["fiscal_year"]), 12, 31)


def _scorecard_result(error: Decimal | None, tolerance: Decimal) -> str:
    if error is None:
        return "not_available"
    return "hit" if abs(error) <= tolerance else "miss"


def _forecast_trace_from_row(row: Any, currency: str) -> dict[str, Any]:
    trace = dict(row["source_trace"] or {})
    trace.setdefault("source_type", row["source"])
    trace.setdefault("source_url", row["source_url"])
    trace.setdefault("source_document_id", f"consensus-{row['id']}")
    trace.setdefault("filing_id", f"consensus-{row['id']}")
    trace.setdefault("period", f"FY{row['fiscal_year']}E")
    trace.setdefault("unit", row["unit"])
    trace.setdefault("currency", currency)
    trace.setdefault("formula", "point-in-time consensus estimate snapshot")
    trace.setdefault("quality_status", row["quality_status"])
    trace.setdefault("snapshot_id", str(row["id"]))
    trace.setdefault("snapshot_date", row["snapshot_date"].isoformat())
    trace.setdefault("available_at", f"{row['snapshot_date'].isoformat()}T00:00:00+00:00")
    trace.setdefault("estimate_case", row["estimate_case"])
    trace.setdefault("metric_key", row["metric_key"])
    trace.setdefault("fiscal_year", int(row["fiscal_year"]))
    trace.setdefault("fiscal_period", row["fiscal_period"])
    trace.setdefault("source", row["source"])
    trace.setdefault("method", row["source"])
    return trace


def _normalize_consensus_case(raw: str) -> str:
    normalized = raw.lower().replace("-", "_")
    if normalized in {"low", "bear", "pessimistic"}:
        return "low"
    if normalized in {"high", "bull", "optimistic"}:
        return "high"
    return "median"


def _select_consensus_projection_row(rows: list[Any], forecast_case: str) -> Any | None:
    if not rows:
        return None
    desired_cases = [forecast_case]
    if forecast_case == "median":
        desired_cases.append("current")
    else:
        desired_cases.extend(["median", "current"])
    sorted_rows = sorted(
        rows,
        key=lambda row: (row["snapshot_date"], row["created_at"]),
        reverse=True,
    )
    for desired_case in desired_cases:
        for row in sorted_rows:
            row_case = _normalize_consensus_case(str(row["estimate_case"]))
            if row_case == desired_case or (
                desired_case == "median" and str(row["estimate_case"]).lower() == "current"
            ):
                return row
    return sorted_rows[0]


def _projection_growth_rate(
    selected_rows: list[Any],
    start_year: int,
    start_metric: Decimal | None,
) -> Decimal | None:
    for row in reversed(selected_rows):
        if row["growth_rate_pct"] is not None:
            return Decimal(str(row["growth_rate_pct"])).quantize(Decimal("0.01"))
    if start_metric is None or start_metric <= 0:
        return None
    latest_row = max(selected_rows, key=lambda row: int(row["fiscal_year"]))
    horizon = int(latest_row["fiscal_year"]) - int(start_year)
    latest_estimate = Decimal(str(latest_row["estimate_value"]))
    if horizon <= 0 or latest_estimate <= 0:
        return None
    return (
        (
            ((latest_estimate / start_metric) ** (Decimal("1") / Decimal(horizon)))
            - Decimal("1")
        )
        * Decimal("100")
    ).quantize(Decimal("0.01"))


def _estimate_error_pct(estimate: Decimal, actual: Decimal) -> Decimal:
    if actual == 0:
        return Decimal("0")
    return (((estimate / actual) - 1) * 100).quantize(Decimal("0.01"))


def _hit_rate(rows: list[dict[str, Any]], field: str) -> str:
    scored_rows = [row for row in rows if row[field] in {"hit", "miss"}]
    if not scored_rows:
        return "0.00"
    hits = sum(1 for row in scored_rows if row[field] == "hit")
    rate = (Decimal(hits) / Decimal(len(scored_rows)) * 100).quantize(Decimal("0.01"))
    return _decimal_str(rate) or "0.00"


def _research_metadata_items_from_row(row: Any) -> list[dict[str, Any]]:
    metadata = dict(row["metadata"] or {})
    payload_items = _research_metadata_payload_items(row["local_path"])
    item_count = int(metadata.get("item_count") or len(payload_items) or 0)
    if not payload_items:
        return [
            _research_metadata_item(
                row,
                title=_research_source_label(row["source"]),
                link=row["source_url"] or row["document_source_url"] or row["blob_url"],
                description=metadata.get("source_note")
                or "Source-backed research metadata batch. Raw payload may be in Blob storage.",
                item_index=None,
                item_count=item_count,
            )
        ]

    items: list[dict[str, Any]] = []
    for index, payload_item in enumerate(payload_items[:25], start=1):
        items.append(
            _research_metadata_item(
                row,
                title=str(payload_item.get("title") or _research_source_label(row["source"])),
                link=str(
                    payload_item.get("link")
                    or row["source_url"]
                    or row["document_source_url"]
                    or row["blob_url"]
                    or ""
                ),
                description=str(payload_item.get("description") or ""),
                item_index=index,
                item_count=item_count,
            )
        )
    return items


def _research_metadata_item(
    row: Any,
    *,
    title: str,
    link: str | None,
    description: str,
    item_index: int | None,
    item_count: int,
) -> dict[str, Any]:
    metadata = dict(row["metadata"] or {})
    source_document_id = str(row["source_document_id"] or metadata.get("source_document_id") or "")
    trace = _research_metadata_trace(
        row,
        source_document_id=source_document_id,
        item_index=item_index,
        item_count=item_count,
    )
    return {
        "source": row["source"],
        "source_label": _research_source_label(row["source"]),
        "ticker": row["ticker"],
        "identifier": row["identifier"],
        "title": title,
        "link": link or "",
        "description": description,
        "source_url": row["source_url"] or row["document_source_url"] or "",
        "source_document_id": source_document_id,
        "content_hash": row["content_hash"],
        "content_type": row["content_type"],
        "item_count": item_count,
        "financial_numbers_allowed": False,
        "terms_note": metadata.get("terms_note") or "",
        "source_note": metadata.get("source_note") or "",
        "source_trace": trace,
    }


def _research_metadata_payload_items(local_path: str | None) -> list[dict[str, Any]]:
    if not local_path:
        return []
    path = Path(local_path)
    if not path.exists() or not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return []
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _research_metadata_trace(
    row: Any,
    *,
    source_document_id: str,
    item_index: int | None,
    item_count: int,
) -> dict[str, Any]:
    metadata = dict(row["metadata"] or {})
    created_at = row["created_at"]
    period = str(metadata.get("downloaded_date") or _date_str(created_at) or "metadata")
    trace = {
        "source_document_id": source_document_id,
        "source_type": row["source"],
        "source": row["source"],
        "filing_id": row["accession_number"] or row["identifier"],
        "form": row["form_type"] or metadata.get("form_type") or "RESEARCH_LINK_METADATA",
        "period": period,
        "available_at": _date_str(created_at),
        "unit": "research_metadata",
        "currency": "N/A",
        "method": "metadata_only_no_financial_numbers",
        "formula": (
            "raw_objects/source_documents metadata lookup; external research links are "
            "not promoted to financial facts, estimates, or valuation inputs"
        ),
        "quality_status": "source_backed_research_metadata",
        "quality_flags": ["metadata_only_no_financial_numbers"],
        "source_url": row["source_url"] or row["document_source_url"],
        "content_hash": row["content_hash"],
        "item_index": item_index,
        "item_count": item_count,
        "financial_numbers_allowed": False,
    }
    if metadata.get("terms_note"):
        trace["terms_note"] = metadata["terms_note"]
    if metadata.get("source_note"):
        trace["source_note"] = metadata["source_note"]
    return trace


def _research_metadata_collection_trace(
    ticker: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    if not items:
        return {
            "source_document_id": "research_metadata:not_loaded",
            "source_type": "research_metadata",
            "source": "research_metadata",
            "filing_id": f"research_metadata:{ticker}",
            "period": "metadata",
            "available_at": datetime.now(UTC).isoformat(),
            "unit": "research_metadata",
            "currency": "N/A",
            "method": "metadata_only_no_financial_numbers",
            "formula": (
                "No source-backed research metadata rows were found in raw_objects "
                "for this ticker"
            ),
            "quality_status": "source_backed_empty_research_metadata",
            "quality_flags": ["research_metadata_not_loaded"],
            "financial_numbers_allowed": False,
        }
    traces = [dict(item.get("source_trace") or {}) for item in items]
    first = traces[0]
    return {
        "source_document_id": first.get("source_document_id") or "research_metadata:mixed",
        "source_document_ids": sorted(
            {
                str(trace.get("source_document_id"))
                for trace in traces
                if trace.get("source_document_id")
            }
        ),
        "source_type": "research_metadata",
        "source": "research_metadata",
        "sources": sorted({str(item["source"]) for item in items if item.get("source")}),
        "filing_id": f"research_metadata:{ticker}",
        "period": first.get("period") or "metadata",
        "available_at": first.get("available_at") or datetime.now(UTC).isoformat(),
        "unit": "research_metadata",
        "currency": "N/A",
        "method": "metadata_only_no_financial_numbers",
        "formula": (
            "Union of metadata-only external research source rows; no external "
            "research numbers are used in valuation calculations"
        ),
        "quality_status": "source_backed_research_metadata",
        "quality_flags": ["metadata_only_no_financial_numbers"],
        "financial_numbers_allowed": False,
    }


def _research_source_label(source: str) -> str:
    if source == "naver_search_research":
        return "Naver research search metadata"
    if source == "hankyung_consensus_metadata":
        return "Hankyung consensus metadata"
    return source


def _adjusted_row(row: Any, adjustment_steps: list[dict[str, Any]]) -> dict[str, Any]:
    source_trace = row["source_trace"] or {}
    waterfall = [
        {
            "label": "GAAP net income",
            "category": "gaap_ni",
            "pretax_amount": None,
            "tax_effect": None,
            "after_tax_impact": _decimal_str(row["gaap_ni"]),
            "eps_impact": _decimal_str(row["gaap_eps_diluted"]),
            "included_by_policy": True,
            "recurring": False,
            "source_trace": source_trace,
        },
        *adjustment_steps,
        {
            "label": "Adjusted diluted EPS",
            "category": "adjusted_eps",
            "pretax_amount": None,
            "tax_effect": None,
            "after_tax_impact": _decimal_str(row["adjusted_ni"]),
            "eps_impact": _decimal_str(row["adjusted_eps"]),
            "included_by_policy": True,
            "recurring": False,
            "source_trace": source_trace,
        },
    ]
    return {
        "id": str(row["id"]),
        "security_id": str(row["security_id"]),
        "fiscal_year": row["fiscal_year"],
        "fiscal_period": row["fiscal_period"],
        "period_start": _date_str(row["period_start"]),
        "period_end": _date_str(row["period_end"]),
        "gaap_ni": _decimal_str(row["gaap_ni"]),
        "gaap_eps_diluted": _decimal_str(row["gaap_eps_diluted"]),
        "adjusted_ni": _decimal_str(row["adjusted_ni"]),
        "adjusted_eps": _decimal_str(row["adjusted_eps"]),
        "company_adjusted_eps": _decimal_str(row["company_adjusted_eps"]),
        "diluted_shares": _decimal_str(row["diluted_shares"]),
        "currency": row["currency"],
        "scale": _decimal_str(row["scale"]),
        "method": row["method"],
        "policy": row["policy"],
        "exclude_sbc": row["exclude_sbc"],
        "exclude_acquired_intangible_amortization": row["exclude_acquired_intangible_amortization"],
        "sector_policy": row["sector_policy"],
        "confidence": _decimal_str(row["confidence"]),
        "quality_status": row["quality_status"],
        "flags": row["flags"] or [],
        "warnings": row["warnings"] or [],
        "formula": row["formula"],
        "source_trace": source_trace,
        "computed_at": _date_str(row["computed_at"]),
        "parser_version": row["parser_version"],
        "metadata": row["metadata"] or {},
        "waterfall": waterfall,
    }


def _decimal_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _eps_impact(after_tax_impact: Any, diluted_shares: Any) -> str | None:
    if after_tax_impact is None or diluted_shares in {None, 0, "0"}:
        return None
    shares = Decimal(str(diluted_shares))
    if shares == 0:
        return None
    return str((Decimal(str(after_tax_impact)) / shares).quantize(Decimal("0.0001")))


def _date_str(value: Any) -> str | None:
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else value
