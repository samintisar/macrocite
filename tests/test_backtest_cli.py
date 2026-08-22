from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlmodel import Session, select
from typer.testing import CliRunner

from signalbench.backtest.run import persist_run
from signalbench.cli import app
from signalbench.db.models import (
    BacktestConfig,
    EventType,
    Price,
    RawDocument,
    Signal,
    Ticker,
)


def test_backtest_help() -> None:
    result = CliRunner().invoke(app, ["backtest", "--help"])
    assert result.exit_code == 0


def test_second_run_matches_fingerprint(session: Session) -> None:
    ticker = Ticker(symbol="AAPL", company_name="Apple Inc.", active=True)
    session.add(ticker)
    session.commit()
    session.refresh(ticker)
    doc = RawDocument(
        source="sec_edgar",
        external_id="cli-bt",
        doc_type="eight_k",
        raw_text="beat",
        published_at=datetime(2022, 6, 1, tzinfo=UTC),
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    session.add(
        Signal(
            document_id=doc.id,
            ticker_id=ticker.id,
            model_version="deepseek-ai/DeepSeek-V4-Flash-0731",
            prompt_version="v1",
            sentiment=0.9,
            event_type=EventType.earnings,
            confidence=0.9,
            rationale="beat",
        )
    )
    session.add(
        Price(
            ticker_id=ticker.id,
            date=date(2022, 6, 1),
            open=Decimal(10),
            high=Decimal(10),
            low=Decimal(10),
            close=Decimal(10),
            adj_close=Decimal(20),
            volume=1,
        )
    )
    session.add(
        Price(
            ticker_id=ticker.id,
            date=date(2022, 6, 8),
            open=Decimal(11),
            high=Decimal(11),
            low=Decimal(11),
            close=Decimal(11),
            adj_close=Decimal(22),
            volume=1,
        )
    )
    session.commit()
    params = {"sentiment_threshold": 0.5, "holding_days": 1, "name": "mvp"}
    r1 = persist_run(
        session,
        ticker_id=ticker.id,
        params=params,
        model_version="deepseek-ai/DeepSeek-V4-Flash-0731",
        prompt_version="v1",
    )
    r2 = persist_run(
        session,
        ticker_id=ticker.id,
        params=params,
        model_version="deepseek-ai/DeepSeek-V4-Flash-0731",
        prompt_version="v1",
    )
    assert r1.signal_set_fingerprint == r2.signal_set_fingerprint
    assert r1.sharpe_ratio == r2.sharpe_ratio
    assert r1.config_id == r2.config_id
    assert r1.trade_log is not None
    assert len(r1.trade_log) == 1
    trade = r1.trade_log[0]
    assert isinstance(trade, dict)
    assert trade["entry_date"] == "2022-06-01"
    assert trade["exit_date"] == "2022-06-08"
    assert Decimal(str(trade["entry_price"])) == Decimal(20)
    assert Decimal(str(trade["exit_price"])) == Decimal(22)
    configs = session.exec(
        select(BacktestConfig).where(BacktestConfig.name == "mvp")
    ).all()
    assert len(configs) == 1


def test_persist_run_rejects_name_with_different_params(session: Session) -> None:
    ticker = Ticker(symbol="AAPL", company_name="Apple Inc.", active=True)
    session.add(ticker)
    session.commit()
    session.refresh(ticker)
    doc = RawDocument(
        source="sec_edgar",
        external_id="cli-bt-mismatch",
        doc_type="eight_k",
        raw_text="beat",
        published_at=datetime(2022, 6, 1, tzinfo=UTC),
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    session.add(
        Signal(
            document_id=doc.id,
            ticker_id=ticker.id,
            model_version="deepseek-ai/DeepSeek-V4-Flash-0731",
            prompt_version="v1",
            sentiment=0.9,
            event_type=EventType.earnings,
            confidence=0.9,
            rationale="beat",
        )
    )
    session.add(
        Price(
            ticker_id=ticker.id,
            date=date(2022, 6, 1),
            open=Decimal(10),
            high=Decimal(10),
            low=Decimal(10),
            close=Decimal(10),
            adj_close=Decimal(10),
            volume=1,
        )
    )
    session.add(
        Price(
            ticker_id=ticker.id,
            date=date(2022, 6, 8),
            open=Decimal(11),
            high=Decimal(11),
            low=Decimal(11),
            close=Decimal(11),
            adj_close=Decimal(11),
            volume=1,
        )
    )
    session.commit()
    persist_run(
        session,
        ticker_id=ticker.id,
        params={"sentiment_threshold": 0.5, "holding_days": 1, "name": "mvp"},
        model_version="deepseek-ai/DeepSeek-V4-Flash-0731",
        prompt_version="v1",
    )
    with pytest.raises(ValueError):
        persist_run(
            session,
            ticker_id=ticker.id,
            params={"sentiment_threshold": 0.5, "holding_days": 5, "name": "mvp"},
            model_version="deepseek-ai/DeepSeek-V4-Flash-0731",
            prompt_version="v1",
        )
