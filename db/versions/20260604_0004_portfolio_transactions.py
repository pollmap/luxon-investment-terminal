"""add source-backed portfolio transactions

Revision ID: 20260604_0004
Revises: 20260602_0003
Create Date: 2026-06-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260604_0004"
down_revision = "20260602_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portfolio_transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_key", sa.String(length=128), nullable=False, server_default="default"),
        sa.Column(
            "security_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("securities.id"),
            nullable=True,
        ),
        sa.Column("ticker", sa.String(length=32), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("quantity", sa.Numeric(), nullable=False),
        sa.Column("price", sa.Numeric(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("sector", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_trace", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "owner_key",
            "ticker",
            "trade_date",
            "side",
            "quantity",
            "price",
            "source",
            name="uq_portfolio_transactions_owner_trade",
        ),
    )
    op.create_index(
        "ix_portfolio_transactions_owner_ticker",
        "portfolio_transactions",
        ["owner_key", "ticker", "trade_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_portfolio_transactions_owner_ticker", table_name="portfolio_transactions")
    op.drop_table("portfolio_transactions")
