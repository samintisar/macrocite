from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import cast

from sqlalchemy import ColumnElement
from sqlmodel import Session, col, select

from signalbench.backtest.fingerprint import signal_set_fingerprint
from signalbench.backtest.metrics import compute_metrics
from signalbench.backtest.strategy import Trade, build_trades
from signalbench.db.models import (
    BacktestConfig,
    BacktestRun,
    Price,
    RawDocument,
    Signal,
)

_METRIC_KEYS = (
    "sharpe_ratio",
    "max_drawdown",
    "win_rate",
    "total_return",
    "benchmark_return",
)
_PRICE_LOOKBACK_DAYS = 365 * 2


def _window_prices(
    prices: list[tuple[date, Decimal]],
) -> list[tuple[date, Decimal]]:
    if not prices:
        return prices
    cutoff = prices[-1][0] - timedelta(days=_PRICE_LOOKBACK_DAYS)
    return [row for row in prices if row[0] >= cutoff]


def _window_signals(
    signals: list[dict[str, object]],
    cutoff: date,
) -> list[dict[str, object]]:
    windowed: list[dict[str, object]] = []
    for signal in signals:
        published_at = signal["published_at"]
        if not isinstance(published_at, datetime):
            continue
        if published_at.date() >= cutoff:
            windowed.append(signal)
    return windowed


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
    prices: list[tuple[date, Decimal]] = _window_prices(
        [(row.date, row.adj_close) for row in price_rows]
    )
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
    if prices:
        cutoff = prices[-1][0] - timedelta(days=_PRICE_LOOKBACK_DAYS)
        signals = _window_signals(signals, cutoff)
    return build_trades(
        prices=prices,
        signals=signals,
        sentiment_threshold=sentiment_threshold,
        holding_days=holding_days,
        stop_loss=stop_loss,
        take_profit=take_profit,
    )


def _as_float(value: object, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        msg = f"{key} must be a number"
        raise TypeError(msg)
    return float(value)


def _as_int(value: object, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"{key} must be an int"
        raise TypeError(msg)
    return value


def _optional_float(params: dict[str, object], key: str) -> float | None:
    if key not in params:
        return None
    return _as_float(params[key], key)


def _serialize_trades(trades: list[Trade]) -> list[dict[str, str]]:
    return [
        {
            "entry_date": trade.entry_date.isoformat(),
            "exit_date": trade.exit_date.isoformat(),
            "entry_price": str(trade.entry_price),
            "exit_price": str(trade.exit_price),
        }
        for trade in trades
    ]


def _config_name(params: dict[str, object]) -> str:
    raw = params.get("name")
    if not isinstance(raw, str) or not raw.strip():
        msg = "backtest config name is required"
        raise ValueError(msg)
    return raw


def persist_run(
    session: Session,
    ticker_id: uuid.UUID,
    params: dict[str, object],
    model_version: str,
    prompt_version: str,
) -> BacktestRun:
    name = _config_name(params)
    config_params = {key: value for key, value in params.items() if key != "name"}
    strategy_type = "sentiment_threshold_long"
    config = session.exec(
        select(BacktestConfig).where(BacktestConfig.name == name)
    ).first()
    if config is None:
        config = BacktestConfig(
            name=name,
            strategy_type=strategy_type,
            params=config_params,
        )
        session.add(config)
    elif config.params != config_params or config.strategy_type != strategy_type:
        msg = f"backtest config name {name!r} is taken"
        raise ValueError(msg)

    trades = run_backtest_for_ticker(
        session,
        ticker_id=ticker_id,
        sentiment_threshold=_as_float(
            config_params["sentiment_threshold"], "sentiment_threshold"
        ),
        holding_days=_as_int(config_params["holding_days"], "holding_days"),
        model_version=model_version,
        prompt_version=prompt_version,
        stop_loss=_optional_float(config_params, "stop_loss"),
        take_profit=_optional_float(config_params, "take_profit"),
    )

    signal_ids = session.exec(
        select(Signal.id)
        .where(Signal.ticker_id == ticker_id)
        .where(Signal.model_version == model_version)
        .where(Signal.prompt_version == prompt_version)
    ).all()
    fingerprint = signal_set_fingerprint([str(signal_id) for signal_id in signal_ids])

    price_rows = session.exec(
        select(Price).where(Price.ticker_id == ticker_id).order_by(col(Price.date))
    ).all()
    if not price_rows:
        msg = f"no prices for ticker {ticker_id}"
        raise ValueError(msg)
    prices: list[tuple[date, Decimal]] = _window_prices(
        [(row.date, row.adj_close) for row in price_rows]
    )
    if not prices:
        msg = f"no prices for ticker {ticker_id}"
        raise ValueError(msg)
    metrics = metrics_as_json(prices, trades)

    run = BacktestRun(
        config_id=config.id,
        ticker_ids=[str(ticker_id)],
        start_date=prices[0][0],
        end_date=prices[-1][0],
        model_version=model_version,
        prompt_version=prompt_version,
        signal_set_fingerprint=fingerprint,
        sharpe_ratio=metrics["sharpe_ratio"],
        max_drawdown=metrics["max_drawdown"],
        win_rate=metrics["win_rate"],
        total_return=metrics["total_return"],
        benchmark_return=metrics["benchmark_return"],
        trade_log=_serialize_trades(trades),
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run
