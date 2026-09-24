"""Buy-and-hold benchmarks over the same sessions as a run (spec 02)."""

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from statistics import fmean

from signalbench.backtest.metrics import cagr, max_drawdown, sharpe
from signalbench.market.bars import AdjustedBar


@dataclass(frozen=True)
class BenchmarkStats:
    name: str
    total_return: float
    cagr: float
    sharpe: float
    max_drawdown: float


def closes_on(bars: Sequence[AdjustedBar], sessions: Sequence[date]) -> list[float | None]:
    """The latest adjusted close on or before each session; None before the first bar."""
    dates = [bar.date for bar in bars]
    out: list[float | None] = []
    for day in sessions:
        index = bisect_right(dates, day) - 1
        out.append(bars[index].close if index >= 0 else None)
    return out


def buy_and_hold(bars: Sequence[AdjustedBar], sessions: Sequence[date]) -> list[float]:
    """Value of 1.0 bought at the first session's close, held to the end."""
    closes = closes_on(bars, sessions)
    first = closes[0]
    if first is None:
        raise ValueError(f"benchmark has no bar on or before {sessions[0]}")
    return [(first if close is None else close) / first for close in closes]


def equal_weight(series: Sequence[Sequence[AdjustedBar]], sessions: Sequence[date]) -> list[float]:
    """Survivor benchmark: equal money in every name with a bar on the first session,
    bought at that close and never rebalanced."""
    holdings: list[list[float]] = []
    for bars in series:
        if not any(bar.date == sessions[0] for bar in bars):
            continue
        closes = closes_on(bars, sessions)
        first = closes[0]
        if first is None:
            continue
        holdings.append([1.0 if close is None else close / first for close in closes])
    if not holdings:
        raise ValueError(f"no universe name has a bar on {sessions[0]}")
    return [fmean(values) for values in zip(*holdings, strict=True)]


def benchmark_stats(name: str, curve: Sequence[float], sessions: Sequence[date]) -> BenchmarkStats:
    return BenchmarkStats(
        name=name,
        total_return=curve[-1] / curve[0] - 1.0,
        cagr=cagr(curve[0], curve[-1], sessions[0], sessions[-1]),
        sharpe=sharpe(curve),
        max_drawdown=max_drawdown(curve),
    )
