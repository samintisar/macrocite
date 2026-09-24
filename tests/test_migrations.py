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
    assert _script().get_heads() == ["0010_backtest_runs"]


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
