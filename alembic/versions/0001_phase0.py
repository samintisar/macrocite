"""Phase 0 schema

Revision ID: 0001_phase0
Revises:
Create Date: 2026-08-20 16:00:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0001_phase0"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    doctype = sa.Enum("news", "eight_k", "ten_k", "ten_q", name="doctype")
    doctype.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "raw_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("doc_type", doctype, nullable=False),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source",
            "external_id",
            name="uq_raw_documents_source_external_id",
        ),
    )
    op.create_table(
        "tickers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("company_name", sa.String(), nullable=False),
        sa.Column("sector", sa.String(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tickers_symbol"), "tickers", ["symbol"], unique=True)
    op.create_table(
        "document_tickers",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("ticker_id", sa.Uuid(), nullable=False),
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
        sa.PrimaryKeyConstraint("document_id", "ticker_id"),
    )
    op.create_table(
        "prices",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ticker_id", sa.Uuid(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(12, 4), nullable=False),
        sa.Column("high", sa.Numeric(12, 4), nullable=False),
        sa.Column("low", sa.Numeric(12, 4), nullable=False),
        sa.Column("close", sa.Numeric(12, 4), nullable=False),
        sa.Column("adj_close", sa.Numeric(12, 4), nullable=False),
        sa.Column("volume", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["ticker_id"],
            ["tickers.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticker_id", "date", name="uq_prices_ticker_date"),
    )


def downgrade() -> None:
    op.drop_table("prices")
    op.drop_table("document_tickers")
    op.drop_index(op.f("ix_tickers_symbol"), table_name="tickers")
    op.drop_table("tickers")
    op.drop_table("raw_documents")
    sa.Enum("news", "eight_k", "ten_k", "ten_q", name="doctype").drop(
        op.get_bind(),
        checkfirst=True,
    )
