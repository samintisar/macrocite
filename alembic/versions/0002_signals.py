"""Signals table

Revision ID: 0002_signals
Revises: 0001_phase0
Create Date: 2026-08-20 17:31:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_signals"
down_revision: str | None = "0001_phase0"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    eventtype = postgresql.ENUM(
        "earnings",
        "guidance",
        "leadership",
        "legal",
        "product",
        "macro",
        "other",
        name="eventtype",
        create_type=False,
    )
    eventtype.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "signals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("ticker_id", sa.Uuid(), nullable=False),
        sa.Column("model_version", sa.String(), nullable=False),
        sa.Column("prompt_version", sa.String(), nullable=False),
        sa.Column("sentiment", sa.Float(), nullable=False),
        sa.Column("event_type", eventtype, nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("rationale", sa.String(), nullable=True),
        sa.Column("raw_llm_response", sa.JSON(), nullable=True),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["raw_documents.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ticker_id"],
            ["tickers.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "ticker_id",
            "model_version",
            "prompt_version",
            name="uq_signals_doc_ticker_model_prompt",
        ),
    )


def downgrade() -> None:
    op.drop_table("signals")
    postgresql.ENUM(
        "earnings",
        "guidance",
        "leadership",
        "legal",
        "product",
        "macro",
        "other",
        name="eventtype",
        create_type=False,
    ).drop(op.get_bind(), checkfirst=True)
