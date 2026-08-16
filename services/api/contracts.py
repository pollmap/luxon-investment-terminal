from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.core.source_trace import SourceTrace


class DataStatus(StrEnum):
    READY = "ready"
    PARTIAL = "partial"
    STALE = "stale"
    CONFIGURED = "configured"
    FIXTURE_NON_PRODUCTION = "fixture_non_production"
    MISSING_SOURCE = "missing_source"
    MISSING_CONTRACT = "missing_contract"
    MISSING_KEY = "missing_key"
    RATE_LIMITED = "rate_limited"
    UPSTREAM_ERROR = "upstream_error"


class DataMode(StrEnum):
    SOURCE_BACKED = "source_backed"
    FIXTURE_NON_PRODUCTION = "fixture_non_production"
    CONFIGURATION_ONLY = "configuration_only"
    UNAVAILABLE = "unavailable"


_UNAVAILABLE_STATUSES = {
    DataStatus.MISSING_SOURCE,
    DataStatus.MISSING_CONTRACT,
    DataStatus.MISSING_KEY,
    DataStatus.RATE_LIMITED,
    DataStatus.UPSTREAM_ERROR,
}

_EXPECTED_MODES = {
    DataStatus.READY: DataMode.SOURCE_BACKED,
    DataStatus.PARTIAL: DataMode.SOURCE_BACKED,
    DataStatus.STALE: DataMode.SOURCE_BACKED,
    DataStatus.CONFIGURED: DataMode.CONFIGURATION_ONLY,
    DataStatus.FIXTURE_NON_PRODUCTION: DataMode.FIXTURE_NON_PRODUCTION,
    DataStatus.MISSING_SOURCE: DataMode.UNAVAILABLE,
    DataStatus.MISSING_CONTRACT: DataMode.UNAVAILABLE,
    DataStatus.MISSING_KEY: DataMode.UNAVAILABLE,
    DataStatus.RATE_LIMITED: DataMode.UNAVAILABLE,
    DataStatus.UPSTREAM_ERROR: DataMode.UNAVAILABLE,
}


class DataState(BaseModel):
    """Machine-readable availability state shared by beta API responses."""

    model_config = ConfigDict(extra="forbid")

    status: DataStatus
    available: bool
    data_mode: DataMode
    reason: str | None = None

    @model_validator(mode="after")
    def validate_availability(self) -> DataState:
        is_unavailable = self.status in _UNAVAILABLE_STATUSES
        if is_unavailable == self.available:
            raise ValueError("data-state availability conflicts with status")
        expected_mode = _EXPECTED_MODES[self.status]
        if self.data_mode != expected_mode:
            raise ValueError(
                f"status={self.status.value} requires data_mode={expected_mode.value}"
            )
        if (is_unavailable or self.status in {DataStatus.PARTIAL, DataStatus.STALE}) and not (
            self.reason or ""
        ).strip():
            raise ValueError(f"status={self.status.value} requires a reason")
        return self


class ApiEnvelope[PayloadT](BaseModel):
    """Stable response shell that never disguises unavailable data as an empty dataset."""

    model_config = ConfigDict(extra="forbid")

    data: PayloadT | None = None
    state: DataState
    meta: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_data_state(self) -> ApiEnvelope[PayloadT]:
        if self.state.status in _UNAVAILABLE_STATUSES and self.data is not None:
            raise ValueError("unavailable data states require data=null")
        if self.state.status not in _UNAVAILABLE_STATUSES and self.data is None:
            raise ValueError("available data states require a non-null data payload")
        return self


class FactValue(BaseModel):
    """A numeric fact coupled to its storage-ready provenance contract."""

    model_config = ConfigDict(extra="forbid")

    metric: str = Field(min_length=1)
    value: Decimal | None = None
    period: str | None = None
    unit: str | None = None
    currency: str | None = None
    source_trace: SourceTrace | None = None

    @model_validator(mode="after")
    def validate_sourced_value(self) -> FactValue:
        if self.value is None:
            return self
        if not self.value.is_finite():
            raise ValueError("fact value must be finite")
        missing_fields = [
            field_name
            for field_name in ("period", "unit", "currency")
            if not (getattr(self, field_name) or "").strip()
        ]
        if missing_fields:
            raise ValueError(
                "non-null fact values require " + ", ".join(missing_fields)
            )
        if self.source_trace is None:
            raise ValueError("non-null fact values require source_trace")
        self.source_trace.assert_storage_ready()
        return self


__all__ = [
    "ApiEnvelope",
    "DataMode",
    "DataState",
    "DataStatus",
    "FactValue",
    "SourceTrace",
]
