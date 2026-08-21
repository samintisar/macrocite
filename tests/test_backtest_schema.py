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
