"""Every holding gets a stop: trade_signals.source (scan | adopted)

Revision ID: 0019_signal_source
Revises: 0018_paper_crypto
Create Date: 2026-11-05 22:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_signal_source"
down_revision: str | None = "0018_paper_crypto"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "trade_signals",
        sa.Column("source", sa.String(), nullable=False, server_default="scan"),
    )


def downgrade() -> None:
    with op.batch_alter_table("trade_signals") as batch:  # SQLite needs a batch to drop
        batch.drop_column("source")
