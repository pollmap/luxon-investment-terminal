"""add private watchlists

Revision ID: 20260604_0006
Revises: 20260604_0005
Create Date: 2026-06-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260604_0006"
down_revision = "20260604_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "watchlists",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_key", sa.String(length=128), nullable=False, server_default="default"),
        sa.Column("name", sa.String(length=128), nullable=False, server_default="Default"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_key", "name", name="uq_watchlists_owner_name"),
    )
    op.create_table(
        "watchlist_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "watchlist_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("watchlists.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "security_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("securities.id"),
            nullable=True,
        ),
        sa.Column("ticker", sa.String(length=32), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source_trace", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("watchlist_id", "ticker", name="uq_watchlist_items_ticker"),
    )
    op.create_index("ix_watchlists_owner", "watchlists", ["owner_key"])
    op.create_index("ix_watchlist_items_ticker", "watchlist_items", ["ticker"])


def downgrade() -> None:
    op.drop_index("ix_watchlist_items_ticker", table_name="watchlist_items")
    op.drop_index("ix_watchlists_owner", table_name="watchlists")
    op.drop_table("watchlist_items")
    op.drop_table("watchlists")
