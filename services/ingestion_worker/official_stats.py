from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from packages.connectors.base import ConnectorDocument


@dataclass(frozen=True)
class OfficialStatObservation:
    series_id: str
    observation_date: date
    value: Decimal
    unit: str | None
    frequency: str | None
    source_trace: dict[str, Any]


def normalize_official_stat_document(document: ConnectorDocument) -> list[OfficialStatObservation]:
    payload = json.loads(document.payload.decode("utf-8"))
    if document.source == "ecos":
        return _ecos_observations(document, payload)
    if document.source == "kosis":
        return _kosis_observations(document, payload)
    if document.source == "estat":
        return _estat_observations(document, payload)
    return []


def _ecos_observations(
    document: ConnectorDocument,
    payload: dict[str, Any],
) -> list[OfficialStatObservation]:
    container = payload.get("StatisticSearch") if isinstance(payload, dict) else None
    rows = container.get("row") if isinstance(container, dict) else []
    if not isinstance(rows, list):
        return []
    observations: list[OfficialStatObservation] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = _decimal_or_none(row.get("DATA_VALUE"))
        period = row.get("TIME")
        if value is None or period in {None, ""}:
            continue
        stat_code = str(row.get("STAT_CODE") or document.metadata.get("stat_code") or "")
        item_code = str(row.get("ITEM_CODE1") or document.metadata.get("item_code") or "ALL")
        item_name = _optional_str(row.get("ITEM_NAME1"))
        series_id = _series_id("ECOS", stat_code or document.ticker, item_code, item_name)
        observation_date = _period_to_date(str(period))
        unit = _optional_str(row.get("UNIT_NAME"))
        frequency = _ecos_frequency(str(row.get("CYCLE") or document.metadata.get("cycle") or ""))
        observations.append(
            OfficialStatObservation(
                series_id=series_id,
                observation_date=observation_date,
                value=value,
                unit=unit,
                frequency=frequency,
                source_trace={
                    "source_type": "ecos_official_api",
                    "source_url": document.url,
                    "series_id": series_id,
                    "stat_code": stat_code,
                    "stat_name": row.get("STAT_NAME"),
                    "item_code": item_code,
                    "item_name": item_name,
                    "period": str(period),
                    "unit": unit,
                    "formula": "ECOS reported observation value",
                    "method": "ECOS_STATISTIC_SEARCH",
                    "quality_status": "source_backed_macro",
                    "raw_row": row,
                },
            )
        )
    return observations


def _kosis_observations(
    document: ConnectorDocument,
    payload: Any,
) -> list[OfficialStatObservation]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("data")
    else:
        rows = []
    if not isinstance(rows, list):
        return []
    observations: list[OfficialStatObservation] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = _decimal_or_none(row.get("DT"))
        period = row.get("PRD_DE")
        if value is None or period in {None, ""}:
            continue
        dimensions = _kosis_dimensions(row)
        org_id = str(row.get("ORG_ID") or document.metadata.get("org_id") or "")
        tbl_id = str(row.get("TBL_ID") or document.metadata.get("tbl_id") or "")
        user_stats_id = str(document.metadata.get("user_stats_id") or "")
        base = ":".join(part for part in (org_id, tbl_id, user_stats_id) if part)
        series_id = _series_id("KOSIS", base or document.ticker, _dimension_digest(dimensions))
        unit = _optional_str(row.get("UNIT_NM"))
        observation_date = _period_to_date(str(period))
        observations.append(
            OfficialStatObservation(
                series_id=series_id,
                observation_date=observation_date,
                value=value,
                unit=unit,
                frequency=_kosis_frequency(row.get("PRD_SE")),
                source_trace={
                    "source_type": "kosis_official_api",
                    "source_url": document.url,
                    "series_id": series_id,
                    "org_id": org_id or None,
                    "tbl_id": tbl_id or None,
                    "user_stats_id": user_stats_id or None,
                    "period": str(period),
                    "dimensions": dimensions,
                    "unit": unit,
                    "formula": "KOSIS reported observation value",
                    "method": "KOSIS_STATISTICS_DATA",
                    "quality_status": "source_backed_macro",
                    "raw_row": row,
                },
            )
        )
    return observations


def _estat_observations(
    document: ConnectorDocument,
    payload: dict[str, Any],
) -> list[OfficialStatObservation]:
    container = payload.get("GET_STATS_DATA") if isinstance(payload, dict) else None
    values = (
        container.get("STATISTICAL_DATA", {}).get("DATA_INF", {}).get("VALUE", [])
        if isinstance(container, dict)
        else []
    )
    if isinstance(values, dict):
        values = [values]
    if not isinstance(values, list):
        return []
    observations: list[OfficialStatObservation] = []
    for row in values:
        if not isinstance(row, dict):
            continue
        value = _decimal_or_none(row.get("$") or row.get("value"))
        period = row.get("@time") or row.get("time")
        if value is None or period in {None, ""}:
            continue
        dimensions = {
            key.removeprefix("@"): value
            for key, value in row.items()
            if key.startswith("@") and key not in {"@time", "@unit"}
        }
        stats_data_id = str(document.metadata.get("stats_data_id") or document.ticker)
        series_id = _series_id("ESTAT", stats_data_id, _dimension_digest(dimensions))
        unit = _optional_str(row.get("@unit") or row.get("unit"))
        observation_date = _period_to_date(str(period))
        observations.append(
            OfficialStatObservation(
                series_id=series_id,
                observation_date=observation_date,
                value=value,
                unit=unit,
                frequency=None,
                source_trace={
                    "source_type": "estat_official_api",
                    "source_url": document.url,
                    "series_id": series_id,
                    "stats_data_id": stats_data_id,
                    "period": str(period),
                    "dimensions": dimensions,
                    "unit": unit,
                    "formula": "e-Stat reported observation value",
                    "method": "ESTAT_GET_STATS_DATA",
                    "quality_status": "source_backed_macro",
                    "raw_row": row,
                },
            )
        )
    return observations


def _period_to_date(value: str) -> date:
    compact = value.strip()
    quarter = re.fullmatch(r"(\d{4})Q([1-4])", compact, re.IGNORECASE)
    if quarter:
        year = int(quarter.group(1))
        month = (int(quarter.group(2)) - 1) * 3 + 1
        return date(year, month, 1)
    digits = re.sub(r"\D", "", compact)
    if len(digits) >= 8:
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    if len(digits) == 6:
        return date(int(digits[:4]), int(digits[4:6]), 1)
    if len(digits) == 4:
        return date(int(digits), 1, 1)
    raise ValueError(f"unsupported official statistics period: {value!r}")


def _decimal_or_none(value: Any) -> Decimal | None:
    if value in {None, "", "-", ".", "NaN", "NA", "N/A"}:
        return None
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _optional_str(value: Any) -> str | None:
    if value in {None, ""}:
        return None
    return str(value).strip() or None


def _kosis_dimensions(row: dict[str, Any]) -> dict[str, str]:
    dimensions: dict[str, str] = {}
    for key, value in row.items():
        if not key.endswith("_NM") or key in {"ORG_NM", "TBL_NM", "UNIT_NM"}:
            continue
        if value not in {None, ""}:
            dimensions[key] = str(value)
    return dimensions


def _dimension_digest(dimensions: dict[str, Any]) -> str:
    if not dimensions:
        return "ALL"
    payload = json.dumps(dimensions, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]


def _series_id(*parts: str | None) -> str:
    raw = ":".join(str(part) for part in parts if part)
    normalized = re.sub(r"[^0-9A-Za-z:_-]+", "_", raw).strip("_").upper()
    if len(normalized) <= 64:
        return normalized
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:10].upper()
    return f"{normalized[:53]}:{digest}"


def _ecos_frequency(cycle: str) -> str | None:
    return {"A": "annual", "Q": "quarterly", "M": "monthly", "D": "daily"}.get(cycle.upper())


def _kosis_frequency(value: Any) -> str | None:
    raw = str(value or "").upper()
    return {"A": "annual", "Y": "annual", "Q": "quarterly", "M": "monthly"}.get(raw)
