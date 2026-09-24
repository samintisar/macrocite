from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from statistics import fmean, median, stdev

import numpy as np

from signalbench.backtest.simulator import SimulationResult, TradeRecord
from signalbench.strategy.config import BacktestParams


@dataclass(frozen=True)
class Trade:
    entry_date: date
    exit_date: date
    entry_price: Decimal
    exit_price: Decimal


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
    if len(daily_returns) < 2:
        return 0.0

    std = float(np.std(daily_returns, ddof=1))
    if not math.isfinite(std) or std == 0.0:
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


# --- Spec 02 run metrics (plain floats; the functions above serve older callers) ---


@dataclass(frozen=True)
class TradeStats:
    trades: int
    win_rate: float
    mean_r: float
    median_r: float
    average_hold: float  # sessions held, entry session included


@dataclass(frozen=True)
class RunMetrics:
    trades: int
    win_rate: float
    mean_r: float
    median_r: float
    average_hold: float
    trades_h1: int
    mean_r_h1: float
    trades_h2: int
    mean_r_h2: float
    total_return: float
    cagr: float
    sharpe: float
    max_drawdown: float
    exposure_pct: float
    pauses: int
    skips_by_reason: dict[str, int]
    open_positions_at_end: int
    recent: TradeStats


def trade_stats(trades: Sequence[TradeRecord]) -> TradeStats:
    if not trades:
        return TradeStats(trades=0, win_rate=0.0, mean_r=0.0, median_r=0.0, average_hold=0.0)
    rs = [trade.r for trade in trades]
    return TradeStats(
        trades=len(trades),
        win_rate=sum(1 for r in rs if r > 0) / len(rs),
        mean_r=fmean(rs),
        median_r=float(median(rs)),
        average_hold=fmean(trade.sessions_held for trade in trades),
    )


def daily_returns(values: Sequence[float]) -> list[float]:
    return [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]


def sharpe(values: Sequence[float]) -> float:
    """Annualised Sharpe of daily returns: mean / sample stdev x sqrt(252), rf = 0."""
    returns = daily_returns(values)
    if len(returns) < 2:
        return 0.0
    deviation = stdev(returns)
    if deviation == 0.0:
        return 0.0
    return fmean(returns) / deviation * math.sqrt(252.0)


def max_drawdown(values: Sequence[float]) -> float:
    """Largest fall from a running peak, as a fraction of that peak."""
    peak = 0.0
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0.0:
            worst = max(worst, (peak - value) / peak)
    return worst


def cagr(first: float, last: float, start: date, end: date) -> float:
    years = (end - start).days / 365.25
    if years <= 0.0 or first <= 0.0 or last <= 0.0:
        return 0.0
    return float((last / first) ** (1.0 / years) - 1.0)


def run_metrics(result: SimulationResult, params: BacktestParams) -> RunMetrics:
    """Spec 02 "Computed per run". Halves and the recent sample go by entry date."""
    values = [point.equity for point in result.equity_curve]
    everything = trade_stats(result.trades)
    first_half = trade_stats([t for t in result.trades if t.entry_date <= params.h1_end])
    second_half = trade_stats([t for t in result.trades if t.entry_date >= params.h2_start])
    recent = trade_stats([t for t in result.trades if t.entry_date >= params.recent_since])
    skips = Counter(str(e["reason"]) for e in result.events if e["event"] == "skip")
    exposed = sum(1 for point in result.equity_curve if point.open_positions > 0)
    return RunMetrics(
        trades=everything.trades,
        win_rate=everything.win_rate,
        mean_r=everything.mean_r,
        median_r=everything.median_r,
        average_hold=everything.average_hold,
        trades_h1=first_half.trades,
        mean_r_h1=first_half.mean_r,
        trades_h2=second_half.trades,
        mean_r_h2=second_half.mean_r,
        total_return=values[-1] / values[0] - 1.0 if values else 0.0,
        cagr=cagr(values[0], values[-1], result.start, result.end) if values else 0.0,
        sharpe=sharpe(values),
        max_drawdown=max_drawdown(values),
        exposure_pct=exposed / len(values) if values else 0.0,
        pauses=sum(1 for e in result.events if e["event"] == "pause"),
        skips_by_reason=dict(sorted(skips.items())),
        open_positions_at_end=len(result.open_positions),
        recent=recent,
    )
