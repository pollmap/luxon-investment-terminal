"""add saved chart layouts

Revision ID: 20260608_0007
Revises: 20260604_0006
Create Date: 2026-06-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260608_0007"
down_revision = "20260604_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chart_layouts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_key", sa.String(length=128), nullable=False, server_default="default"),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("ticker", sa.String(length=32), nullable=False),
        sa.Column("metric", sa.String(length=64), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("source_trace", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_key", "name", name="uq_chart_layouts_owner_name"),
    )
    op.create_index("ix_chart_layouts_owner_updated", "chart_layouts", ["owner_key", "updated_at"])
    op.create_index("ix_chart_layouts_ticker", "chart_layouts", ["ticker"])


def downgrade() -> None:
    op.drop_index("ix_chart_layouts_ticker", table_name="chart_layouts")
    op.drop_index("ix_chart_layouts_owner_updated", table_name="chart_layouts")
    op.drop_table("chart_layouts")
