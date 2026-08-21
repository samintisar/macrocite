"""Eval runs table

Revision ID: 0004_eval_runs
Revises: 0003_prices_volume_bigint
Create Date: 2026-08-21 06:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_eval_runs"
down_revision: str | None = "0003_prices_volume_bigint"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("model_version", sa.String(), nullable=False),
        sa.Column("prompt_version", sa.String(), nullable=False),
        sa.Column("git_commit_sha", sa.String(), nullable=True),
        sa.Column("label_set_git_sha", sa.String(), nullable=True),
        sa.Column("n_examples", sa.Integer(), nullable=False),
        sa.Column("sentiment_accuracy", sa.Float(), nullable=False),
        sa.Column("event_type_metrics", sa.JSON(), nullable=False),
        sa.Column("confidence_calibration", sa.JSON(), nullable=True),
        sa.Column("passed_ci_gate", sa.Boolean(), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("eval_runs")
