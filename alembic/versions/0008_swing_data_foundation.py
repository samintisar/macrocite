"""Ticker kinds and links, document acceptance/items/text, earnings events

Revision ID: 0008_swing_data_foundation
Revises: 0007_drop_sentiment_pipeline
Create Date: 2026-09-22 12:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_swing_data_foundation"
down_revision: str | None = "0007_drop_sentiment_pipeline"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    tickerkind = postgresql.ENUM(
        "us_stock",
        "cdr",
        "benchmark",
        name="tickerkind",
        create_type=False,
    )
    tickerkind.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "tickers",
        sa.Column("kind", tickerkind, nullable=False, server_default="us_stock"),
    )
    op.add_column("tickers", sa.Column("price_symbol", sa.String(), nullable=True))
    op.add_column("tickers", sa.Column("us_ticker_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_tickers_us_ticker_id",
        "tickers",
        "tickers",
        ["us_ticker_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.add_column(
        "raw_documents",
        sa.Column("acceptance_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("raw_documents", sa.Column("items", sa.String(), nullable=True))
    op.add_column("raw_documents", sa.Column("text", sa.Text(), nullable=True))

    op.create_table(
        "earnings_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ticker_id", sa.Uuid(), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["ticker_id"], ["tickers.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ticker_id",
            "event_date",
            "source",
            name="uq_earnings_events_ticker_date_source",
        ),
    )


def downgrade() -> None:
    op.drop_table("earnings_events")
    op.drop_column("raw_documents", "text")
    op.drop_column("raw_documents", "items")
    op.drop_column("raw_documents", "acceptance_at")
    op.drop_constraint("fk_tickers_us_ticker_id", "tickers", type_="foreignkey")
    op.drop_column("tickers", "us_ticker_id")
    op.drop_column("tickers", "price_symbol")
    op.drop_column("tickers", "kind")
    postgresql.ENUM(
        "us_stock",
        "cdr",
        "benchmark",
        name="tickerkind",
        create_type=False,
    ).drop(op.get_bind(), checkfirst=True)
