from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import cast

from sqlalchemy import ColumnElement
from sqlmodel import Session, col, select

from signalbench.backtest.metrics import compute_metrics
from signalbench.backtest.strategy import Trade, build_trades
from signalbench.db.models import Price, RawDocument, Signal

_METRIC_KEYS = (
    "sharpe_ratio",
    "max_drawdown",
    "win_rate",
    "total_return",
    "benchmark_return",
)


def metrics_as_json(
    prices: list[tuple[date, Decimal]],
    trades: list[Trade],
) -> dict[str, float]:
    metrics = compute_metrics(prices, trades)
    return {key: round(float(getattr(metrics, key)), 10) for key in _METRIC_KEYS}


def run_backtest_for_ticker(
    session: Session,
    ticker_id: uuid.UUID,
    sentiment_threshold: float,
    holding_days: int,
    model_version: str,
    prompt_version: str,
    *,
    stop_loss: float | None = None,
    take_profit: float | None = None,
) -> list[Trade]:
    price_rows = session.exec(
        select(Price).where(Price.ticker_id == ticker_id).order_by(col(Price.date))
    ).all()
    prices: list[tuple[date, Decimal]] = [
        (row.date, row.adj_close) for row in price_rows
    ]
    signal_rows = session.exec(
        select(Signal, RawDocument)
        .join(
            RawDocument,
            cast(ColumnElement[bool], Signal.document_id == RawDocument.id),
        )
        .where(Signal.ticker_id == ticker_id)
        .where(Signal.model_version == model_version)
        .where(Signal.prompt_version == prompt_version)
    ).all()
    signals: list[dict[str, object]] = [
        {"sentiment": signal.sentiment, "published_at": document.published_at}
        for signal, document in signal_rows
    ]
    return build_trades(
        prices=prices,
        signals=signals,
        sentiment_threshold=sentiment_threshold,
        holding_days=holding_days,
        stop_loss=stop_loss,
        take_profit=take_profit,
    )
