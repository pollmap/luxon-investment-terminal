from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from packages.core.source_trace import (
    POINT_IN_TIME_REQUIRED_FIELDS,
    STORAGE_REQUIRED_FIELDS,
    SourceTrace,
)

REQUIRED_SOURCE_TRACE_KEYS = set(STORAGE_REQUIRED_FIELDS) | set(
    POINT_IN_TIME_REQUIRED_FIELDS
) | {"quality_status"}


@dataclass(frozen=True)
class QualityCheckResult:
    status: str
    flags: list[str]


def validate_source_trace(trace: dict[str, Any] | None) -> QualityCheckResult:
    if not trace:
        return QualityCheckResult(status="failed", flags=["missing_source_trace"])
    try:
        source_trace = SourceTrace.model_validate(trace)
    except ValidationError as exc:
        fields = sorted(
            {
                str(error.get("loc", ("source_trace",))[0])
                for error in exc.errors()
                if error.get("loc")
            }
        )
        return QualityCheckResult(
            status="warning",
            flags=["invalid_source_trace"]
            + [f"invalid_source_trace:{field}" for field in fields],
        )
    missing = sorted(
        {
            *source_trace.missing_storage_fields(),
            *source_trace.missing_point_in_time_fields(),
            *[
                key
                for key in REQUIRED_SOURCE_TRACE_KEYS
                if not getattr(source_trace, key, None)
            ],
        }
    )
    if missing:
        return QualityCheckResult(
            status="warning",
            flags=[f"missing_source_trace:{key}" for key in missing],
        )
    return QualityCheckResult(status="passed", flags=[])


def validate_valuation_row(row: dict[str, Any]) -> QualityCheckResult:
    flags: list[str] = []
    for key in ["metric", "price", "normal_multiple", "fair_multiple", "fair_value_price"]:
        if key not in row or row[key] in {None, ""}:
            flags.append(f"missing_value:{key}")
    trace_result = validate_source_trace(row.get("source_trace"))
    flags.extend(trace_result.flags)
    if flags:
        return QualityCheckResult(status="warning", flags=flags)
    return QualityCheckResult(status="passed", flags=[])
