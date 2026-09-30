"""Spec 04 live ledger

Revision ID: 0013_live_ledger
Revises: 0012_paper_trading
Create Date: 2026-09-30 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_live_ledger"
down_revision: str | None = "0012_paper_trading"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "live_config",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("config_path", sa.String(), nullable=False),
        sa.Column("config_sha256", sa.String(), nullable=False),
        sa.Column("started_on", sa.Date(), nullable=False),
        sa.Column("start_git_sha", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "cash_movements",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("amount_cad", sa.Numeric(12, 2), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "trade_signals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("us_symbol", sa.String(), nullable=False),
        sa.Column("cdr_ticker_id", sa.Uuid(), nullable=False),
        sa.Column("setup", sa.String(), nullable=False),
        sa.Column("us_signal_close", sa.Numeric(12, 4), nullable=False),
        sa.Column("us_stop", sa.Numeric(12, 4), nullable=False),
        sa.Column("stop_pct", sa.Numeric(12, 8), nullable=False),
        sa.Column("cdr_signal_close", sa.Numeric(12, 4), nullable=False),
        sa.Column("cdr_stop", sa.Numeric(12, 4), nullable=False),
        sa.Column("suggested_units", sa.Numeric(18, 6), nullable=False),
        sa.Column("order_type", sa.String(), nullable=False),
        sa.Column("risk_amount_cad", sa.Numeric(12, 4), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("skip_reason", sa.String(), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cdr_ticker_id"], ["tickers.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "as_of", "us_symbol", "setup", name="uq_trade_signals_as_of_symbol_setup"
        ),
    )
    op.create_table(
        "exit_alerts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("signal_id", sa.Integer(), nullable=False),
        sa.Column("cdr_ticker_id", sa.Uuid(), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("late", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["signal_id"], ["trade_signals.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["cdr_ticker_id"], ["tickers.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_exit_alerts_signal_id", "exit_alerts", ["signal_id"])
    op.create_table(
        "corporate_actions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("us_symbol", sa.String(), nullable=True),
        sa.Column("cdr_ticker_id", sa.Uuid(), nullable=True),
        sa.Column("ex_date", sa.Date(), nullable=False),
        sa.Column("ratio", sa.Numeric(12, 6), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("voided", sa.Boolean(), nullable=False),
        sa.Column("void_reason", sa.Text(), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cdr_ticker_id"], ["tickers.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_corporate_actions_us_split", "corporate_actions", ["us_symbol", "ex_date"],
        unique=True,
        sqlite_where=sa.text("kind = 'us_split' AND NOT voided"),
        postgresql_where=sa.text("kind = 'us_split' AND NOT voided"),
    )
    op.create_index(
        "uq_corporate_actions_cdr_split", "corporate_actions", ["cdr_ticker_id", "ex_date"],
        unique=True,
        sqlite_where=sa.text("kind = 'cdr_split' AND NOT voided"),
        postgresql_where=sa.text("kind = 'cdr_split' AND NOT voided"),
    )
    op.create_table(
        "stop_updates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("signal_id", sa.Integer(), nullable=False),
        sa.Column("session", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("old_us_stop", sa.Numeric(12, 4), nullable=False),
        sa.Column("new_us_stop", sa.Numeric(12, 4), nullable=False),
        sa.Column("old_cdr_stop", sa.Numeric(12, 4), nullable=False),
        sa.Column("new_cdr_stop", sa.Numeric(12, 4), nullable=False),
        sa.Column("late", sa.Boolean(), nullable=False),
        sa.Column("corporate_action_id", sa.Integer(), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["signal_id"], ["trade_signals.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["corporate_action_id"], ["corporate_actions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_stop_updates_signal_id", "stop_updates", ["signal_id"])
    op.create_index(
        "uq_stop_updates_trail_signal_session", "stop_updates", ["signal_id", "session"],
        unique=True,
        sqlite_where=sa.text("reason = 'trail'"),
        postgresql_where=sa.text("reason = 'trail'"),
    )
    op.create_table(
        "fills",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("cdr_ticker_id", sa.Uuid(), nullable=False),
        sa.Column("side", sa.String(), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("price_cad", sa.Numeric(12, 4), nullable=False),
        sa.Column("fee_cad", sa.Numeric(12, 4), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("signal_id", sa.Integer(), nullable=True),
        sa.Column("exit_alert_id", sa.Integer(), nullable=True),
        sa.Column("voided", sa.Boolean(), nullable=False),
        sa.Column("void_reason", sa.Text(), nullable=True),
        sa.Column("forced", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cdr_ticker_id"], ["tickers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["signal_id"], ["trade_signals.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["exit_alert_id"], ["exit_alerts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fills_cdr_ticker_id", "fills", ["cdr_ticker_id"])
    op.create_table(
        "equity_snapshots",
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("cash", sa.Numeric(14, 4), nullable=False),
        sa.Column("positions_value", sa.Numeric(14, 4), nullable=False),
        sa.Column("equity", sa.Numeric(14, 4), nullable=False),
        sa.Column("peak", sa.Numeric(14, 4), nullable=False),
        sa.PrimaryKeyConstraint("date"),
    )
    op.create_table(
        "risk_state",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False),
        sa.Column("paused_at", sa.Date(), nullable=True),
        sa.Column("paused_reason", sa.Text(), nullable=True),
        sa.Column("resumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("peak_reset_on", sa.Date(), nullable=True),
        sa.Column("scale_up_history", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("risk_state")
    op.drop_table("equity_snapshots")
    op.drop_index("ix_fills_cdr_ticker_id", table_name="fills")
    op.drop_table("fills")
    op.drop_index("uq_stop_updates_trail_signal_session", table_name="stop_updates")
    op.drop_index("ix_stop_updates_signal_id", table_name="stop_updates")
    op.drop_table("stop_updates")
    op.drop_index("uq_corporate_actions_cdr_split", table_name="corporate_actions")
    op.drop_index("uq_corporate_actions_us_split", table_name="corporate_actions")
    op.drop_table("corporate_actions")
    op.drop_index("ix_exit_alerts_signal_id", table_name="exit_alerts")
    op.drop_table("exit_alerts")
    op.drop_table("trade_signals")
    op.drop_table("cash_movements")
    op.drop_table("live_config")
