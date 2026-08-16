from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False, default="US")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class Security(Base):
    __tablename__ = "securities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("companies.id"), nullable=True)
    ticker: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    exchange: Mapped[str | None] = mapped_column(String(32), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    company: Mapped[Company | None] = relationship()


class SourceDocument(Base):
    __tablename__ = "source_documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    security_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("securities.id"),
        nullable=True,
    )
    source_type: Mapped[str] = mapped_column(String(64), nullable=False, default="sec_filing")
    accession_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    form_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    filing_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    local_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    filed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)


class AdjustedEarnings(Base):
    __tablename__ = "adjusted_earnings"
    __table_args__ = (
        UniqueConstraint(
            "security_id",
            "fiscal_year",
            "fiscal_period",
            "policy",
            "method",
            "accession_number",
            name="uq_adjusted_earnings_version",
        ),
        Index(
            "ix_adjusted_earnings_security_period_policy",
            "security_id",
            "fiscal_year",
            "fiscal_period",
            "policy",
        ),
        Index("ix_adjusted_earnings_security_method", "security_id", "method"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    security_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("securities.id"), nullable=False)
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_documents.id"),
        nullable=True,
    )
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    fiscal_period: Mapped[str] = mapped_column(String(16), nullable=False)
    period_start = mapped_column(Date, nullable=True)
    period_end = mapped_column(Date, nullable=True)
    filed_at = mapped_column(DateTime, nullable=True)
    accepted_at = mapped_column(DateTime, nullable=True)
    accession_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    form_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    gaap_ni = mapped_column(Numeric, nullable=True)
    gaap_eps_diluted = mapped_column(Numeric, nullable=True)
    adjusted_ni = mapped_column(Numeric, nullable=True)
    adjusted_eps = mapped_column(Numeric, nullable=True)
    company_adjusted_eps = mapped_column(Numeric, nullable=True)
    diluted_shares = mapped_column(Numeric, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    scale = mapped_column(Numeric, nullable=True)
    method: Mapped[str] = mapped_column(String(64), nullable=False)
    policy: Mapped[str] = mapped_column(String(255), nullable=False)
    exclude_sbc: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    exclude_acquired_intangible_amortization: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    sector_policy: Mapped[str] = mapped_column(String(32), nullable=False, default="default")
    confidence = mapped_column(Numeric, nullable=False)
    quality_status: Mapped[str] = mapped_column(String(32), nullable=False)
    flags: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    warnings: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    formula: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    filing_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    computed_at = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    source_trace: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


class Adjustment(Base):
    __tablename__ = "adjustments"
    __table_args__ = (
        Index("ix_adjustments_security_period", "security_id", "fiscal_year", "fiscal_period"),
        Index("ix_adjustments_category", "canonical_category"),
        Index("ix_adjustments_source_document", "source_document_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    security_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("securities.id"), nullable=False)
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_documents.id"),
        nullable=True,
    )
    adjusted_earnings_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("adjusted_earnings.id"),
        nullable=True,
    )
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    fiscal_period: Mapped[str] = mapped_column(String(16), nullable=False)
    period_start = mapped_column(Date, nullable=True)
    period_end = mapped_column(Date, nullable=True)
    filed_at = mapped_column(DateTime, nullable=True)
    accepted_at = mapped_column(DateTime, nullable=True)
    accession_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    form_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    item_label: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    canonical_category: Mapped[str] = mapped_column(String(64), nullable=False)
    gaap_tag: Mapped[str | None] = mapped_column(String(128), nullable=True)
    raw_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    scale = mapped_column(Numeric, nullable=True)
    amount_basis: Mapped[str] = mapped_column(String(32), nullable=False)
    pretax_amount = mapped_column(Numeric, nullable=True)
    tax_effect = mapped_column(Numeric, nullable=True)
    after_tax_impact = mapped_column(Numeric, nullable=True)
    sign: Mapped[int] = mapped_column(Integer, nullable=False)
    recurring_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    asymmetric_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tax_flag: Mapped[str | None] = mapped_column(String(64), nullable=True)
    policy_included: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    filing_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    table_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    row_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence = mapped_column(Numeric, nullable=False)
    warnings: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    source_trace: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"
    __table_args__ = (Index("ix_ingestion_runs_ticker_source", "ticker", "source", "started_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    market: Mapped[str] = mapped_column(String(8), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    ticker: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at = mapped_column(DateTime(timezone=True), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)


class RawObject(Base):
    __tablename__ = "raw_objects"
    __table_args__ = (
        UniqueConstraint(
            "content_hash",
            "source",
            "identifier",
            name="uq_raw_objects_content_source_identifier",
        ),
        Index("ix_raw_objects_ticker_source", "ticker", "source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ingestion_runs.id"),
        nullable=True,
    )
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_documents.id"),
        nullable=True,
    )
    market: Mapped[str] = mapped_column(String(8), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    ticker: Mapped[str] = mapped_column(String(32), nullable=False)
    identifier: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    blob_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    blob_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    local_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class MetricValue(Base):
    __tablename__ = "metric_values"
    __table_args__ = (
        UniqueConstraint(
            "security_id",
            "metric_key",
            "fiscal_year",
            "fiscal_period",
            "method",
            name="uq_metric_values_version",
        ),
        Index("ix_metric_values_security_metric_year", "security_id", "metric_key", "fiscal_year"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    security_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("securities.id"), nullable=False)
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_documents.id"),
        nullable=True,
    )
    metric_key: Mapped[str] = mapped_column(String(64), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    fiscal_period: Mapped[str] = mapped_column(String(16), nullable=False, default="FY")
    period_end = mapped_column(Date, nullable=True)
    value = mapped_column(Numeric, nullable=False)
    unit: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    formula: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str] = mapped_column(String(64), nullable=False)
    quality_status: Mapped[str] = mapped_column(String(32), nullable=False)
    source_trace: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class FinancialFact(Base):
    __tablename__ = "financial_facts"
    __table_args__ = (
        UniqueConstraint(
            "security_id",
            "taxonomy",
            "tag",
            "fiscal_year",
            "fiscal_period",
            "unit",
            "accession_number",
            "source",
            name="uq_financial_facts_version",
        ),
        Index("ix_financial_facts_security_tag_year", "security_id", "tag", "fiscal_year"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    security_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("securities.id"), nullable=False)
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_documents.id"),
        nullable=True,
    )
    taxonomy: Mapped[str] = mapped_column(String(64), nullable=False)
    tag: Mapped[str] = mapped_column(String(128), nullable=False)
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    fiscal_period: Mapped[str] = mapped_column(String(16), nullable=False)
    period_start = mapped_column(Date, nullable=True)
    period_end = mapped_column(Date, nullable=True)
    filed_at = mapped_column(DateTime(timezone=True), nullable=True)
    accession_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    form_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    frame: Mapped[str | None] = mapped_column(String(64), nullable=True)
    unit: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    value = mapped_column(Numeric, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality_status: Mapped[str] = mapped_column(String(64), nullable=False)
    source_trace: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class PriceBar(Base):
    __tablename__ = "price_bars"
    __table_args__ = (
        UniqueConstraint(
            "security_id",
            "trade_date",
            "source",
            name="uq_price_bars_security_date_source",
        ),
        Index("ix_price_bars_security_year", "security_id", "fiscal_year"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    security_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("securities.id"), nullable=False)
    trade_date = mapped_column(Date, nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    close_price = mapped_column(Numeric, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_trace: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class Dividend(Base):
    __tablename__ = "dividends"
    __table_args__ = (
        UniqueConstraint(
            "security_id",
            "ex_date",
            "source",
            name="uq_dividends_security_date_source",
        ),
        Index("ix_dividends_security_year", "security_id", "fiscal_year"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    security_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("securities.id"), nullable=False)
    ex_date = mapped_column(Date, nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    amount = mapped_column(Numeric, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_trace: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class PortfolioTransactionModel(Base):
    __tablename__ = "portfolio_transactions"
    __table_args__ = (
        UniqueConstraint(
            "owner_key",
            "ticker",
            "trade_date",
            "side",
            "quantity",
            "price",
            "source",
            name="uq_portfolio_transactions_owner_trade",
        ),
        Index("ix_portfolio_transactions_owner_ticker", "owner_key", "ticker", "trade_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_key: Mapped[str] = mapped_column(String(128), nullable=False, default="default")
    security_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("securities.id"),
        nullable=True,
    )
    ticker: Mapped[str] = mapped_column(String(32), nullable=False)
    trade_date = mapped_column(Date, nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity = mapped_column(Numeric, nullable=False)
    price = mapped_column(Numeric, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    sector: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_trace: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class ChartRunModel(Base):
    __tablename__ = "chart_runs"
    __table_args__ = (
        Index("ix_chart_runs_ticker_created", "ticker", "created_at"),
        Index("ix_chart_runs_security_created", "security_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    security_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("securities.id"),
        nullable=True,
    )
    ticker: Mapped[str] = mapped_column(String(32), nullable=False)
    metric: Mapped[str] = mapped_column(String(64), nullable=False)
    request_params: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    line_visibility: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    data_mode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    data_backend: Mapped[str | None] = mapped_column(String(64), nullable=True)
    svg_cache_key: Mapped[str] = mapped_column(String(128), nullable=False)
    png_cache_key: Mapped[str] = mapped_column(String(128), nullable=False)
    svg_blob_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    png_blob_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class WatchlistModel(Base):
    __tablename__ = "watchlists"
    __table_args__ = (
        UniqueConstraint("owner_key", "name", name="uq_watchlists_owner_name"),
        Index("ix_watchlists_owner", "owner_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_key: Mapped[str] = mapped_column(String(128), nullable=False, default="default")
    name: Mapped[str] = mapped_column(String(128), nullable=False, default="Default")
    created_at = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class WatchlistItemModel(Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (
        UniqueConstraint("watchlist_id", "ticker", name="uq_watchlist_items_ticker"),
        Index("ix_watchlist_items_ticker", "ticker"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    watchlist_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("watchlists.id"), nullable=False)
    security_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("securities.id"),
        nullable=True,
    )
    ticker: Mapped[str] = mapped_column(String(32), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_trace: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class ConsensusEstimateSnapshot(Base):
    __tablename__ = "consensus_estimate_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "security_id",
            "metric_key",
            "fiscal_year",
            "fiscal_period",
            "snapshot_date",
            "estimate_case",
            "source",
            name="uq_consensus_snapshot_version",
        ),
        Index(
            "ix_consensus_snapshots_security_metric_year",
            "security_id",
            "metric_key",
            "fiscal_year",
        ),
        Index("ix_consensus_snapshots_snapshot_date", "security_id", "snapshot_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    security_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("securities.id"), nullable=False)
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_documents.id"),
        nullable=True,
    )
    metric_key: Mapped[str] = mapped_column(String(64), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    fiscal_period: Mapped[str] = mapped_column(String(16), nullable=False, default="FY")
    period_end = mapped_column(Date, nullable=True)
    snapshot_date = mapped_column(Date, nullable=False)
    estimate_case: Mapped[str] = mapped_column(String(16), nullable=False)
    estimate_value = mapped_column(Numeric, nullable=False)
    growth_rate_pct = mapped_column(Numeric, nullable=True)
    analyst_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unit: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality_status: Mapped[str] = mapped_column(String(64), nullable=False)
    source_trace: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
