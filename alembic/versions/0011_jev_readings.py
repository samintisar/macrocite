"""Spec 03 Jev readings

Revision ID: 0011_jev_readings
Revises: 0010_backtest_runs
Create Date: 2026-09-24 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_jev_readings"
down_revision: str | None = "0010_backtest_runs"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jev_readings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("ticker_id", sa.Uuid(), nullable=False),
        sa.Column("model_requested", sa.String(), nullable=False),
        sa.Column("model_resolved", sa.String(), nullable=False),
        sa.Column("question_set", sa.String(), nullable=False),
        sa.Column("response_id", sa.String(), nullable=False),
        sa.Column("p_negative", sa.Float(), nullable=False),
        sa.Column("p_neutral", sa.Float(), nullable=False),
        sa.Column("p_positive", sa.Float(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("p_routine", sa.Float(), nullable=False),
        sa.Column("answers", sa.JSON(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["raw_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ticker_id"], ["tickers.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "ticker_id",
            "model_requested",
            "question_set",
            name="uq_jev_readings_document_ticker_model_questions",
        ),
    )
    op.create_index("ix_jev_readings_document_id", "jev_readings", ["document_id"])


def downgrade() -> None:
    op.drop_index("ix_jev_readings_document_id", table_name="jev_readings")
    op.drop_table("jev_readings")
