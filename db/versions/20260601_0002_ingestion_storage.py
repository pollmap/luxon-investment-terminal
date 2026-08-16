"""add ingestion storage and source-backed metrics

Revision ID: 20260601_0002
Revises: 20260531_0001
Create Date: 2026-06-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260601_0002"
down_revision = "20260531_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "adjusted_earnings",
        sa.Column("source_trace", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "adjustments",
        sa.Column("source_trace", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.create_table(
        "ingestion_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("ticker", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_ingestion_runs_ticker_source", "ingestion_runs", ["ticker", "source", "started_at"])
    op.create_table(
        "raw_objects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("ingestion_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ingestion_runs.id"), nullable=True),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("source_documents.id"), nullable=True),
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("ticker", sa.String(length=32), nullable=False),
        sa.Column("identifier", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("blob_key", sa.Text(), nullable=True),
        sa.Column("blob_url", sa.Text(), nullable=True),
        sa.Column("local_path", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("content_hash", "source", "identifier", name="uq_raw_objects_content_source_identifier"),
    )
    op.create_index("ix_raw_objects_ticker_source", "raw_objects", ["ticker", "source"])
    op.create_table(
        "metric_values",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("security_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("securities.id"), nullable=False),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("source_documents.id"), nullable=True),
        sa.Column("metric_key", sa.String(length=64), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("fiscal_period", sa.String(length=16), nullable=False, server_default="FY"),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("value", sa.Numeric(), nullable=False),
        sa.Column("unit", sa.String(length=64), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("formula", sa.Text(), nullable=False),
        sa.Column("method", sa.String(length=64), nullable=False),
        sa.Column("quality_status", sa.String(length=32), nullable=False),
        sa.Column("source_trace", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("security_id", "metric_key", "fiscal_year", "fiscal_period", "method", name="uq_metric_values_version"),
    )
    op.create_index("ix_metric_values_security_metric_year", "metric_values", ["security_id", "metric_key", "fiscal_year"])
    op.create_table(
        "price_bars",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("security_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("securities.id"), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("close_price", sa.Numeric(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_trace", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("security_id", "trade_date", "source", name="uq_price_bars_security_date_source"),
    )
    op.create_index("ix_price_bars_security_year", "price_bars", ["security_id", "fiscal_year"])
    op.create_table(
        "dividends",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("security_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("securities.id"), nullable=False),
        sa.Column("ex_date", sa.Date(), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_trace", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("security_id", "ex_date", "source", name="uq_dividends_security_date_source"),
    )
    op.create_index("ix_dividends_security_year", "dividends", ["security_id", "fiscal_year"])


def downgrade() -> None:
    op.drop_index("ix_dividends_security_year", table_name="dividends")
    op.drop_table("dividends")
    op.drop_index("ix_price_bars_security_year", table_name="price_bars")
    op.drop_table("price_bars")
    op.drop_index("ix_metric_values_security_metric_year", table_name="metric_values")
    op.drop_table("metric_values")
    op.drop_index("ix_raw_objects_ticker_source", table_name="raw_objects")
    op.drop_table("raw_objects")
    op.drop_index("ix_ingestion_runs_ticker_source", table_name="ingestion_runs")
    op.drop_table("ingestion_runs")
    op.drop_column("adjustments", "source_trace")
    op.drop_column("adjusted_earnings", "source_trace")
