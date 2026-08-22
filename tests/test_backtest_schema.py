from datetime import date

import pytest
from pydantic import ValidationError
from sqlmodel import Session

from signalbench.db.models import BacktestConfig, BacktestRun


def test_fingerprint_required(session: Session) -> None:
    cfg = BacktestConfig(
        name="mvp",
        strategy_type="sentiment_threshold_long",
        params={"sentiment_threshold": 0.5, "holding_days": 5},
    )
    session.add(cfg)
    session.commit()
    session.refresh(cfg)
    with pytest.raises((ValidationError, TypeError)):
        BacktestRun(
            config_id=cfg.id,
            ticker_ids=[str(cfg.id)],
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            model_version="deepseek-ai/DeepSeek-V4-Flash-0731",
            prompt_version="v1",
            # fingerprint omitted
        )


def test_fingerprint_null_rejected(session: Session) -> None:
    cfg = BacktestConfig(
        name="mvp",
        strategy_type="sentiment_threshold_long",
        params={"sentiment_threshold": 0.5, "holding_days": 5},
    )
    session.add(cfg)
    session.commit()
    session.refresh(cfg)
    with pytest.raises((ValidationError, TypeError)):
        BacktestRun(
            config_id=cfg.id,
            ticker_ids=[str(cfg.id)],
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            model_version="deepseek-ai/DeepSeek-V4-Flash-0731",
            prompt_version="v1",
            signal_set_fingerprint=None,
        )


def test_backtest_run_persist_and_load(session: Session) -> None:
    cfg = BacktestConfig(
        name="mvp",
        strategy_type="sentiment_threshold_long",
        params={"sentiment_threshold": 0.5, "holding_days": 5},
    )
    session.add(cfg)
    session.commit()
    session.refresh(cfg)

    ticker_ids = [str(cfg.id)]
    run = BacktestRun(
        config_id=cfg.id,
        ticker_ids=ticker_ids,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
        model_version="deepseek-ai/DeepSeek-V4-Flash-0731",
        prompt_version="v1",
        signal_set_fingerprint="abc123fingerprint",
    )
    session.add(run)
    session.commit()
    run_id = run.id
    config_id = cfg.id

    session.expunge_all()
    loaded = session.get(BacktestRun, run_id)
    assert loaded is not None
    assert loaded.signal_set_fingerprint == "abc123fingerprint"
    assert loaded.config_id == config_id
    assert loaded.ticker_ids == ticker_ids

    table = BacktestRun.metadata.tables["backtest_runs"]
    fk = next(iter(table.c.config_id.foreign_keys))
    assert fk.ondelete == "RESTRICT"
