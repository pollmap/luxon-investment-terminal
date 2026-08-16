"""add adjusted earnings schema

Revision ID: 20260531_0001
Revises:
Create Date: 2026-05-31
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260531_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False, server_default="US"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "securities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=True),
        sa.Column("ticker", sa.String(length=32), nullable=False, unique=True),
        sa.Column("exchange", sa.String(length=32), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="USD"),
    )
    op.create_table(
        "source_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("security_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("securities.id"), nullable=True),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("accession_number", sa.String(length=64), nullable=True),
        sa.Column("form_type", sa.String(length=32), nullable=True),
        sa.Column("filing_url", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(length=128), nullable=True),
        sa.Column("local_path", sa.Text(), nullable=True),
        sa.Column("filed_at", sa.DateTime(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.create_table(
        "adjusted_earnings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("security_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("securities.id"), nullable=False),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("source_documents.id"), nullable=True),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("fiscal_period", sa.String(length=16), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("filed_at", sa.DateTime(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.Column("accession_number", sa.String(length=64), nullable=True),
        sa.Column("form_type", sa.String(length=32), nullable=True),
        sa.Column("gaap_ni", sa.Numeric(), nullable=True),
        sa.Column("gaap_eps_diluted", sa.Numeric(), nullable=True),
        sa.Column("adjusted_ni", sa.Numeric(), nullable=True),
        sa.Column("adjusted_eps", sa.Numeric(), nullable=True),
        sa.Column("company_adjusted_eps", sa.Numeric(), nullable=True),
        sa.Column("diluted_shares", sa.Numeric(), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("scale", sa.Numeric(), nullable=True),
        sa.Column("method", sa.String(length=64), nullable=False),
        sa.Column("policy", sa.String(length=255), nullable=False),
        sa.Column("exclude_sbc", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("exclude_acquired_intangible_amortization", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sector_policy", sa.String(length=32), nullable=False, server_default="default"),
        sa.Column("confidence", sa.Numeric(), nullable=False),
        sa.Column("quality_status", sa.String(length=32), nullable=False),
        sa.Column("flags", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("warnings", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("formula", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("filing_url", sa.Text(), nullable=True),
        sa.Column("computed_at", sa.DateTime(), nullable=False),
        sa.Column("parser_version", sa.String(length=64), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("security_id", "fiscal_year", "fiscal_period", "policy", "method", "accession_number", name="uq_adjusted_earnings_version"),
    )
    op.create_index("ix_adjusted_earnings_security_period_policy", "adjusted_earnings", ["security_id", "fiscal_year", "fiscal_period", "policy"])
    op.create_index("ix_adjusted_earnings_security_method", "adjusted_earnings", ["security_id", "method"])
    op.create_table(
        "adjustments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("security_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("securities.id"), nullable=False),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("source_documents.id"), nullable=True),
        sa.Column("adjusted_earnings_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("adjusted_earnings.id"), nullable=True),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("fiscal_period", sa.String(length=16), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("filed_at", sa.DateTime(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.Column("accession_number", sa.String(length=64), nullable=True),
        sa.Column("form_type", sa.String(length=32), nullable=True),
        sa.Column("item_label", sa.Text(), nullable=False),
        sa.Column("normalized_label", sa.Text(), nullable=True),
        sa.Column("canonical_category", sa.String(length=64), nullable=False),
        sa.Column("gaap_tag", sa.String(length=128), nullable=True),
        sa.Column("raw_value", sa.Text(), nullable=True),
        sa.Column("raw_unit", sa.String(length=64), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("scale", sa.Numeric(), nullable=True),
        sa.Column("amount_basis", sa.String(length=32), nullable=False),
        sa.Column("pretax_amount", sa.Numeric(), nullable=True),
        sa.Column("tax_effect", sa.Numeric(), nullable=True),
        sa.Column("after_tax_impact", sa.Numeric(), nullable=True),
        sa.Column("sign", sa.Integer(), nullable=False),
        sa.Column("recurring_flag", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("asymmetric_flag", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("tax_flag", sa.String(length=64), nullable=True),
        sa.Column("policy_included", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("filing_url", sa.Text(), nullable=True),
        sa.Column("table_hash", sa.String(length=128), nullable=True),
        sa.Column("row_hash", sa.String(length=128), nullable=True),
        sa.Column("parser_version", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Numeric(), nullable=False),
        sa.Column("warnings", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_adjustments_security_period", "adjustments", ["security_id", "fiscal_year", "fiscal_period"])
    op.create_index("ix_adjustments_category", "adjustments", ["canonical_category"])
    op.create_index("ix_adjustments_source_document", "adjustments", ["source_document_id"])


def downgrade() -> None:
    op.drop_index("ix_adjustments_source_document", table_name="adjustments")
    op.drop_index("ix_adjustments_category", table_name="adjustments")
    op.drop_index("ix_adjustments_security_period", table_name="adjustments")
    op.drop_table("adjustments")
    op.drop_index("ix_adjusted_earnings_security_method", table_name="adjusted_earnings")
    op.drop_index("ix_adjusted_earnings_security_period_policy", table_name="adjusted_earnings")
    op.drop_table("adjusted_earnings")
    op.drop_table("source_documents")
    op.drop_table("securities")
    op.drop_table("companies")

