from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

SEC_COMPANYFACTS_URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
SEC_SUBMISSIONS_URL = "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"


@dataclass(frozen=True)
class SecSubmissionCompany:
    cik: str
    name: str
    tickers: tuple[str, ...]
    exchanges: tuple[str, ...]


@dataclass(frozen=True)
class SecBulkFactRow:
    ticker: str
    cik: str
    entity_name: str
    exchange: str | None
    taxonomy: str
    tag: str
    label: str | None
    metric_key: str
    fiscal_year: int
    fiscal_period: str
    period_start: date | None
    period_end: date | None
    filed_at: datetime | None
    accession_number: str | None
    form_type: str | None
    frame: str | None
    unit: str
    currency: str
    value: Decimal
    source_url: str
    quality_status: str
    source_trace: dict[str, Any]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SecBulkDerivedMetricRow:
    ticker: str
    metric_key: str
    fiscal_year: int
    fiscal_period: str
    value: Decimal
    unit: str
    currency: str
    formula: str
    method: str
    quality_status: str
    source_trace: dict[str, Any]
    source_url: str
    metadata: dict[str, Any]


SEC_COMPANYFACTS_TAGS: dict[str, dict[str, Any]] = {
    "RevenueFromContractWithCustomerExcludingAssessedTax": {
        "metric_key": "revenue",
        "priority": 1,
    },
    "Revenues": {"metric_key": "revenue", "priority": 2},
    "SalesRevenueNet": {"metric_key": "revenue", "priority": 3},
    "EarningsPerShareDiluted": {"metric_key": "reported_eps_diluted", "priority": 1},
    "EarningsPerShareBasic": {"metric_key": "reported_eps_basic", "priority": 1},
    "NetIncomeLoss": {"metric_key": "net_income", "priority": 2},
    "NetIncomeLossAvailableToCommonStockholdersBasic": {
        "metric_key": "net_income_to_common",
        "priority": 1,
    },
    "OperatingIncomeLoss": {"metric_key": "operating_income", "priority": 1},
    "DepreciationDepletionAndAmortization": {
        "metric_key": "depreciation_depletion_amortization",
        "priority": 1,
    },
    "DepreciationDepletionAndAmortizationExpense": {
        "metric_key": "depreciation_depletion_amortization",
        "priority": 2,
    },
    "DepreciationAndAmortization": {
        "metric_key": "depreciation_depletion_amortization",
        "priority": 3,
    },
    "NetCashProvidedByUsedInOperatingActivities": {
        "metric_key": "operating_cash_flow",
        "priority": 1,
    },
    "PaymentsToAcquirePropertyPlantAndEquipment": {"metric_key": "capex", "priority": 1},
    "WeightedAverageNumberOfDilutedSharesOutstanding": {
        "metric_key": "diluted_shares",
        "priority": 1,
    },
    "Assets": {"metric_key": "assets", "priority": 1},
    "Liabilities": {"metric_key": "liabilities", "priority": 1},
    "StockholdersEquity": {"metric_key": "equity", "priority": 1},
}


def parse_submissions_zip(path: Path) -> dict[str, SecSubmissionCompany]:
    companies: dict[str, SecSubmissionCompany] = {}
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.lower().endswith(".json"):
                continue
            payload = json.loads(archive.read(name))
            cik = _normalize_cik(payload.get("cik") or payload.get("cik_str") or name)
            tickers = tuple(str(item).upper() for item in payload.get("tickers", []) if item)
            exchanges = tuple(str(item) for item in payload.get("exchanges", []) if item)
            companies[cik] = SecSubmissionCompany(
                cik=cik,
                name=str(payload.get("name") or payload.get("entityName") or cik),
                tickers=tickers,
                exchanges=exchanges,
            )
    return companies


def parse_companyfacts_zip(
    path: Path,
    *,
    submissions: dict[str, SecSubmissionCompany] | None = None,
    tickers: list[str] | tuple[str, ...] | set[str] | None = None,
    max_companies: int | None = None,
) -> list[SecBulkFactRow]:
    submissions = submissions or {}
    requested_tickers = {ticker.upper() for ticker in tickers or []}
    rows: list[SecBulkFactRow] = []
    parsed_companies = 0
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.lower().endswith(".json"):
                continue
            payload = json.loads(archive.read(name))
            cik = _normalize_cik(payload.get("cik") or name)
            company = submissions.get(cik)
            ticker = _choose_ticker(company, requested_tickers)
            if ticker is None:
                if requested_tickers:
                    continue
                ticker = cik
            if requested_tickers and ticker not in requested_tickers:
                continue
            parsed_companies += 1
            rows.extend(_company_fact_rows(payload, cik, ticker, company))
            if max_companies and parsed_companies >= max_companies:
                break
    return rows


def primary_metric_rows(rows: list[SecBulkFactRow]) -> list[SecBulkFactRow]:
    selected: dict[tuple[str, str, int], SecBulkFactRow] = {}
    for row in rows:
        if row.fiscal_period != "FY":
            continue
        key = (row.ticker, row.metric_key, row.fiscal_year)
        current = selected.get(key)
        if current is None or _metric_sort_key(row) > _metric_sort_key(current):
            selected[key] = row
    return sorted(selected.values(), key=lambda row: (row.ticker, row.metric_key, row.fiscal_year))


def derived_metric_rows(rows: list[SecBulkFactRow]) -> list[SecBulkDerivedMetricRow]:
    grouped: dict[tuple[str, int], dict[str, SecBulkFactRow]] = {}
    for row in rows:
        if row.fiscal_period != "FY":
            continue
        grouped.setdefault((row.ticker, row.fiscal_year), {})[row.metric_key] = row

    derived: list[SecBulkDerivedMetricRow] = []
    for _, metrics in sorted(grouped.items()):
        shares = metrics.get("diluted_shares")
        if shares is None or shares.value <= 0:
            continue
        if basic := metrics.get("reported_eps_basic"):
            derived.append(
                _direct_eps_metric(
                    basic,
                    metric_key="basic_eps",
                    formula="reported_eps_basic from SEC companyfacts EPS fact",
                )
            )
        if diluted := metrics.get("reported_eps_diluted"):
            derived.append(
                _direct_eps_metric(
                    diluted,
                    metric_key="diluted_eps",
                    formula="reported_eps_diluted from SEC companyfacts EPS fact",
                )
            )
        if revenue := metrics.get("revenue"):
            derived.append(
                _per_share_metric(
                    revenue,
                    shares,
                    "sales_share",
                    "revenue_reported / diluted_shares",
                )
            )
            derived.append(
                _per_share_metric(
                    revenue,
                    shares,
                    "revenue_share",
                    "revenue_reported / diluted_shares",
                )
            )
        if operating_cash_flow := metrics.get("operating_cash_flow"):
            derived.append(
                _per_share_metric(
                    operating_cash_flow,
                    shares,
                    "operating_cash_flow_share",
                    "operating_cash_flow_reported / diluted_shares",
                )
            )
        if operating_income := metrics.get("operating_income"):
            derived.append(
                _per_share_metric(
                    operating_income,
                    shares,
                    "ebit_share",
                    "operating_income_reported / diluted_shares",
                )
            )
            if dda := metrics.get("depreciation_depletion_amortization"):
                derived.append(
                    _combined_per_share_metric(
                        (operating_income, dda),
                        shares,
                        "ebitda_share",
                        (
                            "EBITDA proxy = (operating_income_reported + "
                            "depreciation_depletion_amortization_reported) / "
                            "diluted_shares"
                        ),
                        quality_flags=["ebitda_xbrl_reconstructed_from_operating_income_plus_dda"],
                    )
                )
        if operating_cash_flow and (capex := metrics.get("capex")):
            derived.append(
                _combined_per_share_metric(
                    (operating_cash_flow, capex),
                    shares,
                    "fcf_share",
                    (
                        "free_cash_flow = (operating_cash_flow_reported - "
                        "abs(capex_reported)) / diluted_shares"
                    ),
                    combine=lambda first, second: first - abs(second),
                )
            )
    return sorted(derived, key=lambda row: (row.ticker, row.metric_key, row.fiscal_year))


def _company_fact_rows(
    payload: dict[str, Any],
    cik: str,
    ticker: str,
    company: SecSubmissionCompany | None,
) -> list[SecBulkFactRow]:
    entity_name = str(payload.get("entityName") or (company.name if company else cik))
    exchange = company.exchanges[0] if company and company.exchanges else None
    rows: list[SecBulkFactRow] = []
    for taxonomy, taxonomy_facts in (payload.get("facts") or {}).items():
        if not isinstance(taxonomy_facts, dict):
            continue
        for tag, definition in taxonomy_facts.items():
            tag_config = SEC_COMPANYFACTS_TAGS.get(tag)
            if tag_config is None or not isinstance(definition, dict):
                continue
            for unit, facts in (definition.get("units") or {}).items():
                if not isinstance(facts, list):
                    continue
                for fact in facts:
                    row = _fact_row(
                        fact,
                        cik=cik,
                        ticker=ticker,
                        entity_name=entity_name,
                        exchange=exchange,
                        taxonomy=taxonomy,
                        tag=tag,
                        label=definition.get("label"),
                        unit=unit,
                        metric_key=str(tag_config["metric_key"]),
                        tag_priority=int(tag_config["priority"]),
                    )
                    if row is not None:
                        rows.append(row)
    return rows


def _fact_row(
    fact: dict[str, Any],
    *,
    cik: str,
    ticker: str,
    entity_name: str,
    exchange: str | None,
    taxonomy: str,
    tag: str,
    label: str | None,
    unit: str,
    metric_key: str,
    tag_priority: int,
) -> SecBulkFactRow | None:
    fiscal_year = _int_or_none(fact.get("fy"))
    fiscal_period = str(fact.get("fp") or "").upper()
    value = _decimal_or_none(fact.get("val"))
    if fiscal_year is None or not fiscal_period or value is None:
        return None
    period_start = _date_or_none(fact.get("start"))
    period_end = _date_or_none(fact.get("end"))
    filed_at = _datetime_or_none(fact.get("filed"))
    accession_number = _optional_str(fact.get("accn"))
    form_type = _optional_str(fact.get("form"))
    frame = _optional_str(fact.get("frame"))
    currency = _currency_for_unit(unit)
    quality_status = "source_backed_sec_companyfacts"
    trace = {
        "source_type": "sec_companyfacts_bulk",
        "source_url": SEC_COMPANYFACTS_URL,
        "cik": cik,
        "ticker": ticker,
        "taxonomy": taxonomy,
        "tag": tag,
        "filing_id": accession_number,
        "accession_number": accession_number,
        "form_type": form_type,
        "period": f"{fiscal_year}{fiscal_period}",
        "period_start": period_start.isoformat() if period_start else None,
        "period_end": period_end.isoformat() if period_end else None,
        "unit": unit,
        "currency": currency,
        "formula": f"SEC companyfacts {taxonomy}:{tag} reported XBRL fact",
        "method": "SEC_COMPANYFACTS_BULK",
        "quality_status": quality_status,
        "frame": frame,
    }
    return SecBulkFactRow(
        ticker=ticker,
        cik=cik,
        entity_name=entity_name,
        exchange=exchange,
        taxonomy=taxonomy,
        tag=tag,
        label=str(label) if label else None,
        metric_key=metric_key,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        period_start=period_start,
        period_end=period_end,
        filed_at=filed_at,
        accession_number=accession_number,
        form_type=form_type,
        frame=frame,
        unit=unit,
        currency=currency,
        value=value,
        source_url=SEC_COMPANYFACTS_URL,
        quality_status=quality_status,
        source_trace=trace,
        metadata={"tag_priority": tag_priority, "raw_fact": fact},
    )


def _choose_ticker(
    company: SecSubmissionCompany | None,
    requested_tickers: set[str],
) -> str | None:
    if company is None or not company.tickers:
        return None
    if not requested_tickers:
        return company.tickers[0]
    for ticker in company.tickers:
        if ticker in requested_tickers:
            return ticker
    return None


def _metric_sort_key(row: SecBulkFactRow) -> tuple[str, int, str, str]:
    filed = row.filed_at.isoformat() if row.filed_at else ""
    end = row.period_end.isoformat() if row.period_end else ""
    priority = -int(row.metadata.get("tag_priority") or 99)
    form_rank = "2" if row.form_type in {"10-K", "10-K/A"} else "1"
    return (filed, priority, end, form_rank)


def _normalize_cik(value: Any) -> str:
    digits = "".join(re.findall(r"\d+", str(value)))
    if not digits:
        raise ValueError(f"CIK is not parseable: {value!r}")
    return digits[-10:].zfill(10)


def _int_or_none(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _decimal_or_none(value: Any) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _date_or_none(value: Any) -> date | None:
    if value in {None, ""}:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _datetime_or_none(value: Any) -> datetime | None:
    parsed = _date_or_none(value)
    if parsed is None:
        return None
    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=UTC)


def _optional_str(value: Any) -> str | None:
    if value in {None, ""}:
        return None
    return str(value)


def _currency_for_unit(unit: str) -> str:
    normalized = unit.upper()
    if normalized.startswith("USD"):
        return "USD"
    if "SHARE" in normalized:
        return "SHARES"
    return "N/A"


def _direct_eps_metric(
    row: SecBulkFactRow,
    *,
    metric_key: str,
    formula: str,
) -> SecBulkDerivedMetricRow:
    return _derived_row(
        ticker=row.ticker,
        fiscal_year=row.fiscal_year,
        fiscal_period=row.fiscal_period,
        metric_key=metric_key,
        value=row.value,
        unit=f"{row.currency}_per_share",
        currency=row.currency,
        formula=formula,
        inputs=(row,),
        quality_flags=[],
    )


def _per_share_metric(
    numerator: SecBulkFactRow,
    shares: SecBulkFactRow,
    metric_key: str,
    formula: str,
) -> SecBulkDerivedMetricRow:
    value = _quantize_per_share(numerator.value / shares.value)
    return _derived_row(
        ticker=numerator.ticker,
        fiscal_year=numerator.fiscal_year,
        fiscal_period=numerator.fiscal_period,
        metric_key=metric_key,
        value=value,
        unit=f"{numerator.currency}_per_share",
        currency=numerator.currency,
        formula=formula,
        inputs=(numerator, shares),
        quality_flags=[],
    )


def _combined_per_share_metric(
    numerators: tuple[SecBulkFactRow, SecBulkFactRow],
    shares: SecBulkFactRow,
    metric_key: str,
    formula: str,
    *,
    combine=None,
    quality_flags: list[str] | None = None,
) -> SecBulkDerivedMetricRow:
    first, second = numerators
    combined = combine(first.value, second.value) if combine else first.value + second.value
    value = _quantize_per_share(combined / shares.value)
    return _derived_row(
        ticker=first.ticker,
        fiscal_year=first.fiscal_year,
        fiscal_period=first.fiscal_period,
        metric_key=metric_key,
        value=value,
        unit=f"{first.currency}_per_share",
        currency=first.currency,
        formula=formula,
        inputs=(first, second, shares),
        quality_flags=quality_flags or [],
    )


def _derived_row(
    *,
    ticker: str,
    fiscal_year: int,
    fiscal_period: str,
    metric_key: str,
    value: Decimal,
    unit: str,
    currency: str,
    formula: str,
    inputs: tuple[SecBulkFactRow, ...],
    quality_flags: list[str],
) -> SecBulkDerivedMetricRow:
    filing_id = next((row.accession_number for row in inputs if row.accession_number), None)
    trace = {
        "source_type": "sec_companyfacts_bulk_derived",
        "source": "SEC_COMPANYFACTS_BULK_DERIVED",
        "source_url": SEC_COMPANYFACTS_URL,
        "ticker": ticker,
        "metric_key": metric_key,
        "filing_id": filing_id,
        "accession_number": filing_id,
        "period": f"{fiscal_year}{fiscal_period}",
        "unit": unit,
        "currency": currency,
        "formula": formula,
        "method": "SEC_COMPANYFACTS_BULK_DERIVED",
        "confidence": "0.80" if quality_flags else "0.85",
        "quality_status": "source_backed_sec_companyfacts_derived",
        "quality_flags": quality_flags,
        "input_fact_ids": [_input_fact_id(row) for row in inputs],
        "calculation_inputs": [
            {
                "metric_key": row.metric_key,
                "taxonomy": row.taxonomy,
                "tag": row.tag,
                "value": str(row.value),
                "unit": row.unit,
                "currency": row.currency,
                "filing_id": row.accession_number,
                "period": f"{row.fiscal_year}{row.fiscal_period}",
            }
            for row in inputs
        ],
    }
    return SecBulkDerivedMetricRow(
        ticker=ticker,
        metric_key=metric_key,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        value=value,
        unit=unit,
        currency=currency,
        formula=formula,
        method="SEC_COMPANYFACTS_BULK_DERIVED",
        quality_status="source_backed_sec_companyfacts_derived",
        source_trace=trace,
        source_url=SEC_COMPANYFACTS_URL,
        metadata={"input_fact_ids": trace["input_fact_ids"], "quality_flags": quality_flags},
    )


def _input_fact_id(row: SecBulkFactRow) -> str:
    accession = row.accession_number or "no-accession"
    return f"{row.ticker}:{accession}:{row.taxonomy}:{row.tag}:{row.fiscal_year}{row.fiscal_period}"


def _quantize_per_share(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"))
