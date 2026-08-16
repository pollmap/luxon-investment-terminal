"""add industry series storage

Revision ID: 20260610_0010
Revises: 20260609_0009
Create Date: 2026-06-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260610_0010"
down_revision = "20260609_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "industry_series",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("series_id", sa.String(length=96), nullable=False),
        sa.Column("observation_date", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(), nullable=False),
        sa.Column("unit", sa.String(length=64), nullable=True),
        sa.Column("frequency", sa.String(length=64), nullable=True),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("region", sa.String(length=128), nullable=True),
        sa.Column("industry", sa.String(length=128), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column(
            "source_document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("source_documents.id"),
            nullable=True,
        ),
        sa.Column("dimensions", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("source_trace", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "series_id",
            "observation_date",
            "source",
            name="uq_industry_series_observation_source",
        ),
    )
    op.create_index(
        "ix_industry_series_market_category_date",
        "industry_series",
        ["market", "category", "observation_date"],
    )
    op.create_index(
        "ix_industry_series_series_date",
        "industry_series",
        ["series_id", "observation_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_industry_series_series_date", table_name="industry_series")
    op.drop_index("ix_industry_series_market_category_date", table_name="industry_series")
    op.drop_table("industry_series")
