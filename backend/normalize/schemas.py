from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.normalize.enums import (
    AmountBasis,
    BasePolicy,
    NormalizationMethod,
    QualityStatus,
    SectorPolicy,
)
from packages.core import SourceTrace


class NormalizationPolicy(BaseModel):
    base_policy: BasePolicy = BasePolicy.STREET_COMPARABLE
    exclude_sbc: bool = False
    exclude_acquired_intangible_amortization: bool = False
    sector_policy: SectorPolicy = SectorPolicy.DEFAULT
    use_company_reported_when_available: bool = True

    @property
    def key(self) -> str:
        switches = [
            self.base_policy.value,
            f"sbc_{'exclude' if self.exclude_sbc else 'company'}",
            f"amort_{'exclude' if self.exclude_acquired_intangible_amortization else 'company'}",
            self.sector_policy.value,
        ]
        return "|".join(switches)


class AdjustmentRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    security_id: str
    fiscal_year: int
    fiscal_period: str
    period_start: date | None = None
    period_end: date | None = None
    item_label: str
    normalized_label: str | None = None
    canonical_category: str
    gaap_tag: str | None = None
    raw_value: str | None = None
    raw_unit: str | None = None
    currency: str | None = "USD"
    scale: Decimal | None = Decimal("1")
    amount_basis: AmountBasis = AmountBasis.UNKNOWN
    pretax_amount: Decimal | None = None
    tax_effect: Decimal | None = None
    after_tax_impact: Decimal | None = None
    sign: int = 0
    recurring_flag: bool = False
    asymmetric_flag: bool = False
    tax_flag: str | None = None
    policy_included: bool = True
    source: NormalizationMethod
    source_trace: SourceTrace = Field(default_factory=SourceTrace)
    parser_version: str = "normalize-v0.1"
    confidence: Decimal = Decimal("0.5")
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("sign")
    @classmethod
    def validate_sign(cls, value: int) -> int:
        if value not in {-1, 0, 1}:
            raise ValueError("sign must be -1, 0, or 1")
        return value


class WaterfallStep(BaseModel):
    label: str
    category: str
    pretax_amount: Decimal | None = None
    tax_effect: Decimal | None = None
    after_tax_impact: Decimal | None = None
    eps_impact: Decimal | None = None
    included_by_policy: bool = True
    recurring: bool = False
    source_trace: SourceTrace = Field(default_factory=SourceTrace)


class AdjustedEarningsRecord(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    id: UUID = Field(default_factory=uuid4)
    security_id: str
    ticker: str | None = None
    fiscal_year: int
    fiscal_period: str
    period_start: date | None = None
    period_end: date | None = None
    gaap_ni: Decimal | None = None
    gaap_eps_diluted: Decimal | None = None
    adjusted_ni: Decimal | None = None
    adjusted_eps: Decimal | None = None
    company_adjusted_eps: Decimal | None = None
    diluted_shares: Decimal | None = None
    currency: str | None = "USD"
    scale: Decimal | None = Decimal("1")
    method: NormalizationMethod
    policy: str
    exclude_sbc: bool = False
    exclude_acquired_intangible_amortization: bool = False
    sector_policy: SectorPolicy = SectorPolicy.DEFAULT
    confidence: Decimal = Decimal("0.5")
    quality_status: QualityStatus = QualityStatus.WARNING
    flags: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    formula: str | None = None
    source_trace: SourceTrace = Field(default_factory=SourceTrace)
    computed_at: datetime = Field(default_factory=datetime.utcnow)
    parser_version: str = "normalize-v0.1"
    metadata: dict[str, Any] = Field(default_factory=dict)
    waterfall: list[WaterfallStep] = Field(default_factory=list)
    adjustments: list[AdjustmentRecord] = Field(default_factory=list)


class NormalizationResult(BaseModel):
    ticker: str
    policy: NormalizationPolicy
    series: list[AdjustedEarningsRecord]
    failed_strategies: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SourceDocument(BaseModel):
    id: str
    ticker: str | None = None
    accession_number: str | None = None
    form_type: str | None = None
    filing_url: str | None = None
    source_url: str | None = None
    description: str | None = None
    document_type: str | None = None
    content: str | None = None
    local_path: str | None = None
    content_hash: str | None = None
    filed_at: datetime | None = None
    accepted_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
