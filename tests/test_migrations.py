from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

REPO = Path(__file__).resolve().parents[1]
VERSIONS = REPO / "alembic" / "versions"


def _script() -> ScriptDirectory:
    config = Config(str(REPO / "alembic.ini"))
    config.set_main_option("script_location", str(REPO / "alembic"))
    return ScriptDirectory.from_config(config)


def test_migrations_have_single_head() -> None:
    assert _script().get_heads() == ["0016_bot_updates"]


def test_0007_drops_sentiment_tables_and_enum() -> None:
    text = (VERSIONS / "0007_drop_sentiment_pipeline.py").read_text(encoding="utf-8")
    for table in ("signals", "eval_runs", "backtest_runs", "backtest_configs"):
        assert f'op.drop_table("{table}")' in text
    assert 'name="eventtype"' in text
    assert "checkfirst=True" in text


def test_0008_creates_ticker_kind_enum_idempotently() -> None:
    text = (VERSIONS / "0008_swing_data_foundation.py").read_text(encoding="utf-8")
    assert 'name="tickerkind"' in text
    assert "checkfirst=True" in text
    assert 'op.create_table(\n        "earnings_events"' in text


def test_0010_creates_backtest_runs_with_json_payloads() -> None:
    text = (VERSIONS / "0010_backtest_runs.py").read_text(encoding="utf-8")
    assert 'down_revision: str | None = "0009_document_form"' in text
    assert 'op.create_table(\n        "backtest_runs"' in text
    for column in ("metrics", "pass_bar", "trade_log"):
        assert f'sa.Column("{column}", sa.JSON(), nullable=False)' in text


def test_0011_creates_jev_readings_unique_per_document_ticker_model_and_questions() -> None:
    text = (VERSIONS / "0011_jev_readings.py").read_text(encoding="utf-8")
    assert 'down_revision: str | None = "0010_backtest_runs"' in text
    assert 'op.create_table(\n        "jev_readings"' in text
    assert 'sa.Column("answers", sa.JSON(), nullable=False)' in text
    assert '["document_id"], ["raw_documents.id"], ondelete="CASCADE"' in text
    assert '"uq_jev_readings_document_ticker_model_questions"' in text


def test_0013_creates_the_nine_ledger_tables() -> None:
    text = (VERSIONS / "0013_live_ledger.py").read_text(encoding="utf-8")
    assert 'down_revision: str | None = "0011_jev_readings"' in text
    for table in (
        "live_config", "cash_movements", "trade_signals", "exit_alerts", "corporate_actions",
        "stop_updates", "fills", "equity_snapshots", "risk_state",
    ):
        assert f'op.create_table(\n        "{table}"' in text
    assert "postgresql_where=sa.text(\"reason = 'trail'\")" in text
    assert 'sa.Column("amount_cad", sa.Numeric(12, 2), nullable=False)' in text


def test_0014_creates_scan_runs_and_the_bot_heartbeat() -> None:
    text = (VERSIONS / "0014_scan_runs.py").read_text(encoding="utf-8")
    assert 'down_revision: str | None = "0013_live_ledger"' in text
    for table in ("scan_runs", "bot_heartbeat"):
        assert f'op.create_table(\n        "{table}"' in text
