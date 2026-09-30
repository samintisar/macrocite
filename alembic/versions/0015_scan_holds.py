"""Spec 05 held sessions: the scan reviews them once the hold clears

Revision ID: 0015_scan_holds
Revises: 0014_scan_runs
Create Date: 2026-09-30 21:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_scan_holds"
down_revision: str | None = "0014_scan_runs"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scan_holds",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("us_symbol", sa.String(), nullable=False),
        sa.Column("session", sa.Date(), nullable=False),
        sa.Column("reviewed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("us_symbol", "session", name="uq_scan_holds_symbol_session"),
    )


def downgrade() -> None:
    op.drop_table("scan_holds")
