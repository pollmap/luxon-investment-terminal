"""add financial facts table

Revision ID: 20260609_0009
Revises: 20260608_0008
Create Date: 2026-06-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260609_0009"
down_revision = "20260608_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "financial_facts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "security_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("securities.id"),
            nullable=False,
        ),
        sa.Column(
            "source_document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("source_documents.id"),
            nullable=True,
        ),
        sa.Column("taxonomy", sa.String(length=64), nullable=False),
        sa.Column("tag", sa.String(length=128), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("fiscal_period", sa.String(length=16), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("filed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accession_number", sa.String(length=64), nullable=True),
        sa.Column("form_type", sa.String(length=32), nullable=True),
        sa.Column("frame", sa.String(length=64), nullable=True),
        sa.Column("unit", sa.String(length=64), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("value", sa.Numeric(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("quality_status", sa.String(length=64), nullable=False),
        sa.Column("source_trace", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
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
    )
    op.create_index(
        "ix_financial_facts_security_tag_year",
        "financial_facts",
        ["security_id", "tag", "fiscal_year"],
    )


def downgrade() -> None:
    op.drop_index("ix_financial_facts_security_tag_year", table_name="financial_facts")
    op.drop_table("financial_facts")
