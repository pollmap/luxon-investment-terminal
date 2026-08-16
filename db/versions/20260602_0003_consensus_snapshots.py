"""add point-in-time consensus estimate snapshots

Revision ID: 20260602_0003
Revises: 20260601_0002
Create Date: 2026-06-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260602_0003"
down_revision = "20260601_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "consensus_estimate_snapshots",
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
        sa.Column("metric_key", sa.String(length=64), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("fiscal_period", sa.String(length=16), nullable=False, server_default="FY"),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("estimate_case", sa.String(length=16), nullable=False),
        sa.Column("estimate_value", sa.Numeric(), nullable=False),
        sa.Column("growth_rate_pct", sa.Numeric(), nullable=True),
        sa.Column("analyst_count", sa.Integer(), nullable=True),
        sa.Column("unit", sa.String(length=64), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("quality_status", sa.String(length=64), nullable=False),
        sa.Column("source_trace", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "security_id",
            "metric_key",
            "fiscal_year",
            "fiscal_period",
            "snapshot_date",
            "estimate_case",
            "source",
            name="uq_consensus_snapshot_version",
        ),
    )
    op.create_index(
        "ix_consensus_snapshots_security_metric_year",
        "consensus_estimate_snapshots",
        ["security_id", "metric_key", "fiscal_year"],
    )
    op.create_index(
        "ix_consensus_snapshots_snapshot_date",
        "consensus_estimate_snapshots",
        ["security_id", "snapshot_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_consensus_snapshots_snapshot_date", table_name="consensus_estimate_snapshots")
    op.drop_index(
        "ix_consensus_snapshots_security_metric_year",
        table_name="consensus_estimate_snapshots",
    )
    op.drop_table("consensus_estimate_snapshots")
