"""Spec 05 bot: the Telegram updates it recorded, so a redelivered one is not recorded twice

Revision ID: 0016_bot_updates
Revises: 0015_scan_holds
Create Date: 2026-09-30 22:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_bot_updates"
down_revision: str | None = "0015_scan_holds"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bot_updates",
        sa.Column("update_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("update_id"),
    )


def downgrade() -> None:
    op.drop_table("bot_updates")
