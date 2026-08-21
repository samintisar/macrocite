from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import numpy as np

from signalbench.backtest.strategy import Trade


@dataclass(frozen=True)
class Metrics:
    total_return: float
    win_rate: float
    benchmark_return: float
    max_drawdown: float
    sharpe_ratio: float


def _empty_metrics() -> Metrics:
    return Metrics(0.0, 0.0, 0.0, 0.0, 0.0)


def _build_equity_curve(
    prices: list[tuple[date, Decimal]],
    trades: list[Trade],
) -> list[Decimal]:
    equity_curve: list[Decimal] = []

    sorted_trades = sorted(trades, key=lambda trade: trade.entry_date)
    trade_index = 0
    active_trade: Trade | None = None
    base_at_entry = Decimal(1)
    realized_equity = Decimal(1)

    for trading_date, price in prices:
        while active_trade is not None and trading_date > active_trade.exit_date:
            realized_equity = base_at_entry * (
                active_trade.exit_price / active_trade.entry_price
            )
            active_trade = None

        while (
            trade_index < len(sorted_trades)
            and sorted_trades[trade_index].entry_date == trading_date
        ):
            if active_trade is None:
                active_trade = sorted_trades[trade_index]
                base_at_entry = realized_equity
            trade_index += 1

        if active_trade is not None and trading_date <= active_trade.exit_date:
            equity = base_at_entry * (price / active_trade.entry_price)
            equity_curve.append(equity)
            if trading_date == active_trade.exit_date:
                realized_equity = base_at_entry * (
                    active_trade.exit_price / active_trade.entry_price
                )
                active_trade = None
        else:
            equity_curve.append(realized_equity)

    return equity_curve


def _sharpe_ratio(equity_curve: np.ndarray) -> float:
    if len(equity_curve) < 2:
        return 0.0

    daily_returns = np.diff(equity_curve) / equity_curve[:-1]
    std = float(np.std(daily_returns, ddof=1))
    if std == 0.0:
        return 0.0

    mean = float(np.mean(daily_returns))
    return float(np.sqrt(252.0) * mean / std)


def _max_drawdown(equity_curve: list[Decimal]) -> float:
    if not equity_curve:
        return 0.0

    peak = Decimal(0)
    max_drawdown = Decimal(0)

    for value in equity_curve:
        peak = max(peak, value)
        if peak > 0:
            drawdown = (peak - value) / peak
            max_drawdown = max(max_drawdown, drawdown)

    return float(max_drawdown)


def compute_metrics(
    prices: list[tuple[date, Decimal]],
    trades: list[Trade],
) -> Metrics:
    if not prices:
        return _empty_metrics()

    first_price = prices[0][1]
    last_price = prices[-1][1]
    benchmark_return = float(last_price / first_price - Decimal(1))

    if trades:
        wins = sum(1 for trade in trades if trade.exit_price > trade.entry_price)
        win_rate = float(wins / len(trades))
    else:
        win_rate = 0.0

    equity_curve_decimal = _build_equity_curve(prices, trades)
    total_return = float(equity_curve_decimal[-1] - Decimal(1))

    equity_curve = np.array(
        [float(value) for value in equity_curve_decimal],
        dtype=np.float64,
    )

    return Metrics(
        total_return=total_return,
        win_rate=win_rate,
        benchmark_return=benchmark_return,
        max_drawdown=_max_drawdown(equity_curve_decimal),
        sharpe_ratio=_sharpe_ratio(equity_curve),
    )
