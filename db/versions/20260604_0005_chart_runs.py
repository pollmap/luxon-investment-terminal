"""add chart run manifests

Revision ID: 20260604_0005
Revises: 20260604_0004
Create Date: 2026-06-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260604_0005"
down_revision = "20260604_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chart_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "security_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("securities.id"),
            nullable=True,
        ),
        sa.Column("ticker", sa.String(length=32), nullable=False),
        sa.Column("metric", sa.String(length=64), nullable=False),
        sa.Column("request_params", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("line_visibility", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("data_mode", sa.String(length=64), nullable=True),
        sa.Column("data_backend", sa.String(length=64), nullable=True),
        sa.Column("svg_cache_key", sa.String(length=128), nullable=False),
        sa.Column("png_cache_key", sa.String(length=128), nullable=False),
        sa.Column("svg_blob_key", sa.Text(), nullable=True),
        sa.Column("png_blob_key", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_chart_runs_ticker_created", "chart_runs", ["ticker", "created_at"])
    op.create_index("ix_chart_runs_security_created", "chart_runs", ["security_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_chart_runs_security_created", table_name="chart_runs")
    op.drop_index("ix_chart_runs_ticker_created", table_name="chart_runs")
    op.drop_table("chart_runs")
