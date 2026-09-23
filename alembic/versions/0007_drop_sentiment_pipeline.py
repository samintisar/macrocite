"""Drop the retired sentiment pipeline tables

Revision ID: 0007_drop_sentiment_pipeline
Revises: 0006_backtest_config_name_unique
Create Date: 2026-09-22 12:00:00
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_drop_sentiment_pipeline"
down_revision: str | None = "0006_backtest_config_name_unique"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("backtest_runs")
    op.drop_table("backtest_configs")
    op.drop_table("eval_runs")
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


def downgrade() -> None:
    raise NotImplementedError(
        "0007 is one-way: the sentiment pipeline was removed. Restore it from git history."
    )
