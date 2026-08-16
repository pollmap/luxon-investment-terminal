from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from backend.normalize.enums import NormalizationMethod, QualityStatus, SectorPolicy
from backend.normalize.schemas import AdjustedEarningsRecord, NormalizationPolicy, SourceTrace
from packages.connectors.base import ConnectorDocument


@dataclass(frozen=True)
class MetricValueRecord:
    metric_key: str
    fiscal_year: int
    value: Decimal
    unit: str
    currency: str
    formula: str
    method: str
    quality_status: str
    source_trace: dict[str, Any]


@dataclass(frozen=True)
class MarketStandardResult:
    adjusted_record: AdjustedEarningsRecord | None
    metrics: list[MetricValueRecord]


def normalize_market_standard_document(
    document: ConnectorDocument,
    security_id: str,
    currency: str,
) -> MarketStandardResult:
    if document.source == "opendart":
        return _normalize_opendart(document, security_id, currency)
    if document.source == "jquants":
        return _normalize_jquants(document, security_id, currency)
    return MarketStandardResult(adjusted_record=None, metrics=[])


def _normalize_opendart(
    document: ConnectorDocument,
    security_id: str,
    currency: str,
) -> MarketStandardResult:
    payload = _json_payload(document)
    rows = payload.get("list") or []
    fiscal_year = _int_or_none(document.metadata.get("bsns_year"))
    if fiscal_year is None:
        return MarketStandardResult(adjusted_record=None, metrics=[])

    values: dict[str, Decimal] = {}
    for row in rows:
        amount = _decimal_or_none(row.get("thstrm_amount") or row.get("amount"))
        if amount is None:
            continue
        metric_key = _opendart_metric_key_from_row(row)
        if metric_key and metric_key not in values:
            values[metric_key] = amount

    trace = _trace(
        document,
        fiscal_year,
        currency,
        "OpenDART fnlttSinglAcntAll market-standard line item mapping",
        NormalizationMethod.S3_MARKET_STANDARD_KR.value,
    )
    metrics = [
        _metric(
            key,
            fiscal_year,
            value,
            currency if key not in {"eps", "gaap_diluted_eps"} else "per_share",
            currency,
            "OpenDART reported K-IFRS financial statement line item",
            NormalizationMethod.S3_MARKET_STANDARD_KR.value,
            trace,
        )
        for key, value in values.items()
        if key not in {"gaap_diluted_eps"}
    ]
    eps = values.get("gaap_diluted_eps") or values.get("eps")
    adjusted = None
    if eps is not None:
        adjusted = _adjusted_record(
            document=document,
            security_id=security_id,
            fiscal_year=fiscal_year,
            currency=currency,
            eps=eps,
            gaap_ni=values.get("net_income_parent") or values.get("net_income"),
            method=NormalizationMethod.S3_MARKET_STANDARD_KR,
            confidence=Decimal("0.85"),
            formula="K-IFRS reported EPS from OpenDART; market-standard S3 mapping",
            trace=trace,
        )
    return MarketStandardResult(adjusted_record=adjusted, metrics=metrics)


def _normalize_jquants(
    document: ConnectorDocument,
    security_id: str,
    currency: str,
) -> MarketStandardResult:
    payload = _json_payload(document)
    statements = payload.get("statements") or []
    metrics: list[MetricValueRecord] = []
    adjusted_records: list[AdjustedEarningsRecord] = []
    for statement in statements:
        fiscal_year = _jquants_fiscal_year(statement, document)
        if fiscal_year is None:
            continue
        trace = _trace(
            document,
            fiscal_year,
            currency,
            "J-Quants fins/statements market-standard line item mapping",
            NormalizationMethod.S3_MARKET_STANDARD_JP.value,
        )
        mapped = _jquants_values(statement)
        for key, value in mapped.items():
            if key in {"gaap_diluted_eps", "eps"}:
                continue
            metrics.append(
                _metric(
                    key,
                    fiscal_year,
                    value,
                    currency,
                    currency,
                    "J-Quants reported financial statement line item",
                    NormalizationMethod.S3_MARKET_STANDARD_JP.value,
                    trace,
                )
            )
        eps = mapped.get("gaap_diluted_eps") or mapped.get("eps")
        if eps is not None:
            adjusted_records.append(
                _adjusted_record(
                    document=document,
                    security_id=security_id,
                    fiscal_year=fiscal_year,
                    currency=currency,
                    eps=eps,
                    gaap_ni=mapped.get("net_income"),
                    method=NormalizationMethod.S3_MARKET_STANDARD_JP,
                    confidence=Decimal("0.90"),
                    formula="J-Quants reported EPS; market-standard S3 mapping",
                    trace=trace,
                )
            )
    return MarketStandardResult(
        adjusted_record=adjusted_records[-1] if adjusted_records else None,
        metrics=metrics,
    )


def _adjusted_record(
    document: ConnectorDocument,
    security_id: str,
    fiscal_year: int,
    currency: str,
    eps: Decimal,
    gaap_ni: Decimal | None,
    method: NormalizationMethod,
    confidence: Decimal,
    formula: str,
    trace: dict[str, Any],
) -> AdjustedEarningsRecord:
    return AdjustedEarningsRecord(
        security_id=security_id,
        ticker=document.ticker,
        fiscal_year=fiscal_year,
        fiscal_period="FY",
        gaap_ni=gaap_ni,
        gaap_eps_diluted=eps,
        adjusted_eps=eps,
        currency=currency,
        method=method,
        policy=NormalizationPolicy().key,
        confidence=confidence,
        quality_status=QualityStatus.PASSED,
        sector_policy=SectorPolicy.DEFAULT,
        formula=formula,
        source_trace=SourceTrace(**trace),
        metadata={"source": document.source, "market": document.market},
    )


def _metric(
    metric_key: str,
    fiscal_year: int,
    value: Decimal,
    unit: str,
    currency: str,
    formula: str,
    method: str,
    source_trace: dict[str, Any],
) -> MetricValueRecord:
    return MetricValueRecord(
        metric_key=metric_key,
        fiscal_year=fiscal_year,
        value=value,
        unit=unit,
        currency=currency,
        formula=formula,
        method=method,
        quality_status=QualityStatus.PASSED.value,
        source_trace=source_trace | {"metric_key": metric_key},
    )


def _trace(
    document: ConnectorDocument,
    fiscal_year: int,
    currency: str,
    formula: str,
    method: str,
) -> dict[str, Any]:
    return {
        "source_document_id": document.identifier,
        "source_type": document.source,
        "filing_id": document.identifier,
        "source_url": document.url,
        "period": f"FY{fiscal_year}",
        "unit": "reported",
        "currency": currency,
        "method": method,
        "formula": formula,
        "quality_status": QualityStatus.PASSED.value,
        "market": document.market,
    }


def _opendart_metric_key(label: str) -> str | None:
    normalized = _compact(label)
    if _contains_any(
        normalized,
        "희석주당이익",
        "희석주당순이익",
        "보통주희석주당이익",
        "보통주희석주당순이익",
        "dilutedearningspershare",
        "dilutedeps",
        "dilutednetincomepercommonshare",
    ):
        return "gaap_diluted_eps"
    if _contains_any(
        normalized,
        "기본주당이익",
        "기본주당순이익",
        "보통주기본주당이익",
        "보통주기본주당순이익",
        "basicearningspershare",
        "basiceps",
        "basicnetincomepercommonshare",
    ):
        return "eps"
    if not _contains_any(
        normalized,
        "매출원가",
        "costofsales",
        "costofrevenue",
    ) and _contains_any(
        normalized,
        "매출액",
        "영업수익",
        "revenue",
        "netsales",
        "sales",
    ):
        return "revenue"
    if _contains_any(normalized, "영업이익", "operatingprofit", "operatingincome"):
        return "operating_income"
    if _contains_any(
        normalized,
        "지배기업소유주",
        "지배기업의소유주",
        "지배주주",
        "controlling",
        "ownersofparent",
        "profitattributabletoownersofparent",
    ):
        return "net_income_parent"
    if _contains_any(
        normalized,
        "당기순이익",
        "분기순이익",
        "반기순이익",
        "연결당기순이익",
        "netincome",
        "profit",
    ):
        return "net_income"
    return None


def _opendart_metric_key_from_row(row: dict[str, Any]) -> str | None:
    tag = _compact(
        str(
            row.get("account_id")
            or row.get("accountId")
            or row.get("xbrl_tag")
            or row.get("gaap_tag")
            or ""
        )
    )
    if tag:
        if _contains_any(
            tag,
            "dilutedearningslosspershare",
            "dilutedearningslossespershare",
            "dilutedearningspershare",
            "dilutedincomelosspercommonshare",
            "dilutednetincomepercommonshare",
        ):
            return "gaap_diluted_eps"
        if _contains_any(
            tag,
            "basicearningslosspershare",
            "basicearningslossespershare",
            "basicearningspershare",
            "basicincomelosspercommonshare",
            "basicnetincomepercommonshare",
        ):
            return "eps"
        if _contains_any(tag, "costofsales", "costofrevenue"):
            return None
        if _contains_any(
            tag,
            "revenue",
            "salesrevenue",
            "operatingrevenue",
        ):
            return "revenue"
        if _contains_any(tag, "operatingincomeloss", "operatingincome", "operatingprofit"):
            return "operating_income"
        if _contains_any(
            tag,
            "profitlossattributabletoownersofparent",
            "netincomelossattributabletoparent",
            "profitattributabletoownersofparent",
        ):
            return "net_income_parent"
        if _contains_any(tag, "profitloss", "netincomeloss", "netincome"):
            return "net_income"

    labels = " ".join(
        str(row.get(key) or "")
        for key in (
            "account_nm",
            "account_name",
            "account_detail",
            "account_detail_nm",
            "label",
        )
    )
    label_key = _opendart_metric_key(labels)
    if label_key:
        return label_key
    return _opendart_eps_metric_key_from_korean_label(labels)


def _opendart_eps_metric_key_from_korean_label(label: str) -> str | None:
    normalized = _compact(label)
    if "주당" not in normalized:
        return None
    if "희석" in normalized:
        return "gaap_diluted_eps"
    if any(token in normalized for token in ("기본", "계속영업", "이익", "손실")):
        return "eps"
    return None


def _jquants_values(statement: dict[str, Any]) -> dict[str, Decimal]:
    mapping = {
        "gaap_diluted_eps": ("DilutedEarningsPerShare", "DilutedEPS"),
        "eps": ("EarningsPerShare", "BasicEarningsPerShare"),
        "revenue": ("NetSales", "OperatingRevenue", "Revenue"),
        "operating_income": ("OperatingProfit", "OperatingIncome"),
        "recurring_income": ("OrdinaryProfit", "RecurringProfit"),
        "net_income": ("Profit", "ProfitAttributableToOwnersOfParent"),
    }
    values: dict[str, Decimal] = {}
    for metric_key, keys in mapping.items():
        for key in keys:
            value = _decimal_or_none(statement.get(key))
            if value is not None:
                values[metric_key] = value
                break
    return values


def _jquants_fiscal_year(
    statement: dict[str, Any],
    document: ConnectorDocument,
) -> int | None:
    for key in ("FiscalYear", "CurrentFiscalYear", "CurrentFiscalYearEndDate", "DisclosedDate"):
        raw = statement.get(key)
        if raw is None:
            continue
        fiscal_year = _year_from_value(raw)
        if fiscal_year is not None:
            return fiscal_year
    return _int_or_none(document.metadata.get("start_year")) or _int_or_none(
        document.metadata.get("end_year")
    )


def _json_payload(document: ConnectorDocument) -> dict[str, Any]:
    return json.loads(document.payload.decode("utf-8"))


def _decimal_or_none(value: Any) -> Decimal | None:
    if value in {None, "", "-", "N/A"}:
        return None
    clean = str(value).strip()
    if clean.startswith("(") and clean.endswith(")"):
        clean = f"-{clean[1:-1]}"
    clean = re.sub(r"[^0-9.\-]", "", clean)
    if clean in {"", "-", "."}:
        return None
    try:
        return Decimal(clean)
    except InvalidOperation:
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _year_from_value(value: Any) -> int | None:
    if isinstance(value, date):
        return value.year
    match = re.search(r"(19|20)\d{2}", str(value))
    return int(match.group(0)) if match else None


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def _contains_any(value: str, *needles: str) -> bool:
    return any(needle.lower() in value for needle in needles)
