from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

STORAGE_REQUIRED_FIELDS = (
    "source",
    "source_document_id",
    "filing_id",
    "period",
    "unit",
    "currency",
    "method",
    "formula",
)

POINT_IN_TIME_REQUIRED_FIELDS = ("available_at",)


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        normalized = value.strip().lower()
        return normalized in {"", "unknown", "n/a", "na", "none"}
    return False


class SourceTrace(BaseModel):
    """Canonical provenance contract for stored financial datapoints.

    The model accepts older field names used by the current API fixtures, but
    storage paths should call assert_storage_ready() before persistence.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    source: str = "UNKNOWN"
    source_type: str | None = None
    source_document_id: str | None = None
    filing_id: str | None = None
    accession_number: str | None = None
    form: str | None = None
    form_type: str | None = None
    period: str = "unknown"
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    available_at: datetime | None = None
    unit: str = "unknown"
    currency: str = "unknown"
    method: str = "unknown"
    formula: str = "unknown"
    input_fact_ids: list[str] = Field(default_factory=list)
    adjustments: list[str] = Field(default_factory=list)
    confidence: Decimal = Decimal("0")
    quality_flags: list[str] = Field(default_factory=list)
    quality_status: str | None = None
    filing_url: str | None = None
    source_url: str | None = None
    filed_at: datetime | None = None
    accepted_at: datetime | None = None
    ingested_at: datetime | None = None
    table_hash: str | None = None
    row_hash: str | None = None
    version: int = 1
    supersedes: int | None = None

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, value: Any) -> Decimal:
        if value is None:
            return Decimal("0")
        return Decimal(str(value))

    @model_validator(mode="after")
    def normalize_legacy_aliases(self) -> SourceTrace:
        if _is_missing(self.source) and self.source_type:
            self.source = self.source_type
        if _is_missing(self.source_type) and self.source:
            self.source_type = self.source
        if _is_missing(self.filing_id) and self.accession_number:
            self.filing_id = self.accession_number
        if _is_missing(self.accession_number) and self.filing_id:
            self.accession_number = self.filing_id
        if _is_missing(self.form) and self.form_type:
            self.form = self.form_type
        if _is_missing(self.form_type) and self.form:
            self.form_type = self.form
        if self.available_at is None:
            self.available_at = self.accepted_at or self.filed_at or self.ingested_at
        if self.quality_status and self.quality_status not in self.quality_flags:
            self.quality_flags.append(self.quality_status)
        return self

    def missing_storage_fields(self) -> list[str]:
        return [
            field_name
            for field_name in STORAGE_REQUIRED_FIELDS
            if _is_missing(getattr(self, field_name))
        ]

    def assert_storage_ready(self) -> None:
        missing = self.missing_storage_fields()
        if missing:
            joined = ", ".join(missing)
            raise ValueError(f"source_trace is not storage-ready; missing: {joined}")

    def missing_point_in_time_fields(self) -> list[str]:
        return [
            field_name
            for field_name in POINT_IN_TIME_REQUIRED_FIELDS
            if _is_missing(getattr(self, field_name))
        ]

    def assert_point_in_time_ready(self) -> None:
        missing = self.missing_point_in_time_fields()
        if missing:
            joined = ", ".join(missing)
            raise ValueError(f"source_trace is not point-in-time-ready; missing: {joined}")


class EntityIdentifier(BaseModel):
    model_config = ConfigDict(extra="allow")

    entity_id: str
    ticker: str
    market: str
    name: str
    cik: str | None = None
    corp_code: str | None = None
    edinet_code: str | None = None
    jquants_code: str | None = None
    currency: str = "USD"


class RawFilingManifest(BaseModel):
    model_config = ConfigDict(extra="allow")

    raw_id: str
    entity_id: str
    source: str
    filing_id: str
    form: str
    period: str
    content_hash: str
    raw_path: str
    append_only: bool = True
    version: int = 1
    supersedes: int | None = None
    source_trace: SourceTrace


class NormalizedFact(BaseModel):
    model_config = ConfigDict(extra="allow")

    fact_id: str
    entity_id: str
    metric: str
    period: str
    value: Decimal
    unit: str
    currency: str
    source_trace: SourceTrace
    version: int = 1
    supersedes: int | None = None


class DerivedMetric(BaseModel):
    model_config = ConfigDict(extra="allow")

    metric_id: str
    entity_id: str
    metric: str
    period: str
    value: Decimal
    unit: str
    currency: str
    formula: str
    input_fact_ids: list[str]
    source_trace: SourceTrace
    version: int = 1
    supersedes: int | None = None


def build_aapl_e2e_stub() -> dict[str, Any]:
    """Return a clearly labeled non-production AAPL contract stub for tests."""

    fixture_available_at = datetime(2024, 11, 1, 12, 0, tzinfo=UTC)

    entity = EntityIdentifier(
        entity_id="sec:aapl",
        ticker="AAPL",
        market="US",
        name="Apple Inc.",
        cik="0000320193",
        currency="USD",
    )
    raw_trace = SourceTrace(
        source="TEST_FIXTURE",
        source_type="fixture_non_production",
        source_document_id="tests/fixtures/terminal/aapl_contract_stub.json",
        filing_id="fixture-aapl-fy2024",
        accession_number="fixture-aapl-fy2024",
        form="10-K",
        form_type="10-K",
        period="FY2024",
        fiscal_year=2024,
        fiscal_period="FY",
        available_at=fixture_available_at,
        unit="document",
        currency="USD",
        method="company_reported",
        formula="raw filing fixture manifest only; not production financial data",
        confidence=Decimal("1"),
        quality_flags=["fixture_non_production"],
        version=1,
    )
    raw_manifest = RawFilingManifest(
        raw_id="raw:sec:aapl:fy2024:contract-stub",
        entity_id=entity.entity_id,
        source="TEST_FIXTURE",
        filing_id="fixture-aapl-fy2024",
        form="10-K",
        period="FY2024",
        content_hash="fixture-non-production-aapl-contract-stub",
        raw_path="tests/fixtures/terminal/aapl_contract_stub.json",
        source_trace=raw_trace,
    )
    eps_trace = SourceTrace(
        source="TEST_FIXTURE",
        source_type="fixture_non_production",
        source_document_id=raw_trace.source_document_id,
        filing_id=raw_trace.filing_id,
        accession_number=raw_trace.accession_number,
        form="10-K",
        form_type="10-K",
        period="FY2024",
        fiscal_year=2024,
        fiscal_period="FY",
        available_at=fixture_available_at,
        unit="USD/share",
        currency="USD",
        method="company_reported",
        formula="reported diluted EPS from fixture-labeled source row",
        confidence=Decimal("1"),
        quality_flags=["fixture_non_production"],
        version=1,
    )
    dividend_trace = eps_trace.model_copy(
        update={
            "unit": "USD/share",
            "formula": "reported dividend per share from fixture-labeled source row",
        }
    )
    eps_fact = NormalizedFact(
        fact_id="fact:sec:aapl:fy2024:eps_diluted",
        entity_id=entity.entity_id,
        metric="gaap_eps_diluted",
        period="FY2024",
        value=Decimal("0"),
        unit="USD/share",
        currency="USD",
        source_trace=eps_trace,
    )
    dividend_fact = NormalizedFact(
        fact_id="fact:sec:aapl:fy2024:dividend_per_share",
        entity_id=entity.entity_id,
        metric="dividend_per_share",
        period="FY2024",
        value=Decimal("0"),
        unit="USD/share",
        currency="USD",
        source_trace=dividend_trace,
    )
    adjusted_trace = eps_trace.model_copy(
        update={
            "method": "gaap_fallback",
            "formula": "adjusted_operating_eps = gaap_eps_diluted when no S1/S2 adjustment exists",
            "input_fact_ids": [eps_fact.fact_id],
            "quality_flags": ["fixture_non_production", "gaap_fallback"],
        }
    )
    adjusted_metric = DerivedMetric(
        metric_id="metric:sec:aapl:fy2024:adjusted_operating_eps",
        entity_id=entity.entity_id,
        metric="adjusted_operating_eps",
        period="FY2024",
        value=eps_fact.value,
        unit="USD/share",
        currency="USD",
        formula=adjusted_trace.formula,
        input_fact_ids=[eps_fact.fact_id],
        source_trace=adjusted_trace,
    )
    return {
        "entity": entity,
        "raw_filings": [raw_manifest],
        "normalized_facts": [eps_fact, dividend_fact],
        "derived_metrics": [adjusted_metric],
    }
