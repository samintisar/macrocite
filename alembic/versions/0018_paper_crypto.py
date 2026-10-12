"""Spec 11: paper trading of crypto-v1, an append-only record of every replayed day

Revision ID: 0018_paper_crypto
Revises: 0017_crypto_ticker_kind
Create Date: 2026-10-11 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_paper_crypto"
down_revision: str | None = "0017_crypto_ticker_kind"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paper_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("git_sha", sa.String(), nullable=False),
        sa.Column("days_added", sa.Integer(), nullable=False),
        sa.Column("messages_failed", sa.Integer(), nullable=False),
        sa.Column("summary_sent", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "paper_portfolios",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("paper_file", sa.String(), nullable=False),
        sa.Column("strategy_sha256", sa.String(), nullable=False),
        sa.Column("start", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_paper_portfolios_name"),
    )
    op.create_table(
        "paper_days",
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("equity", sa.Float(), nullable=False),
        sa.Column("cash", sa.Float(), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["portfolio_id"], ["paper_portfolios.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["paper_runs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("portfolio_id", "day"),
    )
    op.create_table(
        "paper_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["portfolio_id"], ["paper_portfolios.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["paper_runs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_paper_events_portfolio_day", "paper_events", ["portfolio_id", "day"]
    )


def downgrade() -> None:
    op.drop_index("ix_paper_events_portfolio_day", table_name="paper_events")
    op.drop_table("paper_events")
    op.drop_table("paper_days")
    op.drop_table("paper_portfolios")
    op.drop_table("paper_runs")
