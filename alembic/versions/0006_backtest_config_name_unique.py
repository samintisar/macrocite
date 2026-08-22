"""Unique backtest config names

Revision ID: 0006_backtest_config_name_unique
Revises: 0005_backtests
Create Date: 2026-08-21 17:36:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_backtest_config_name_unique"
down_revision: str | None = "0005_backtests"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_backtest_configs_name",
        "backtest_configs",
        ["name"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_backtest_configs_name",
        "backtest_configs",
        type_="unique",
    )
