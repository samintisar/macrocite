"""Spec 05 evening scan runs and the bot heartbeat

Revision ID: 0014_scan_runs
Revises: 0013_live_ledger
Create Date: 2026-09-30 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_scan_runs"
down_revision: str | None = "0013_live_ledger"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scan_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("failed_step", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("counts", sa.JSON(), nullable=False),
        sa.Column("git_sha", sa.String(), nullable=True),
        sa.Column("git_dirty", sa.Boolean(), nullable=True),
        sa.Column("config_sha256", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "bot_heartbeat",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("beat_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("bot_heartbeat")
    op.drop_table("scan_runs")
