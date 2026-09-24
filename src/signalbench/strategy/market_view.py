"""Bars, indicators, earnings dates and sectors, with look-ahead protection.

`MarketView` computes every indicator once for the full history. `MarketView.at(as_of)`
returns an `AsOfView`, which is the only thing `decide()` reads. It never returns a bar
dated after `as_of` and raises `LookAheadError` when asked for one.
"""

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from signalbench.market.bars import AdjustedBar
from signalbench.strategy.config import StrategyConfig
from signalbench.strategy.indicators import (
    prior_max,
    prior_mean,
    rolling_median,
    rolling_min,
    sma,
    wilder_atr,
    wilder_rsi,
)


class LookAheadError(Exception):
    """Raised when strategy code asks for data dated after its as-of session."""


@dataclass(frozen=True)
class SymbolInput:
    symbol: str
    sector: str
    bars: Sequence[AdjustedBar]
    earnings: Sequence[date]


@dataclass(frozen=True)
class Snapshot:
    """One symbol at one session close: the bar plus every indicator the rules read."""

    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    prev_high: float | None
    sma: Mapping[int, float | None]
    rsi: float | None
    atr: float | None
    prior_max_close: float | None
    prior_mean_volume: float | None
    min_low: float | None
    median_traded_value: float | None


class _Series:
    def __init__(self, bars: Sequence[AdjustedBar], config: StrategyConfig) -> None:
        self.bars = list(bars)
        if any(a.date >= b.date for a, b in zip(self.bars, self.bars[1:], strict=False)):
            raise ValueError("bars must be in strictly increasing date order")
        self.dates = [bar.date for bar in self.bars]
        highs = [bar.high for bar in self.bars]
        lows = [bar.low for bar in self.bars]
        closes = [bar.close for bar in self.bars]
        volumes = [float(bar.volume) for bar in self.bars]
        self.sma = {period: sma(closes, period) for period in sorted(config.sma_periods())}
        self.rsi = wilder_rsi(closes, config.rsi_period)
        self.atr = wilder_atr(highs, lows, closes, config.atr_period)
        self.prior_max_close = prior_max(closes, config.breakout.lookback)
        self.prior_mean_volume = prior_mean(volumes, config.breakout.volume_lookback)
        self.min_low = rolling_min(lows, config.pullback.stop_low_sessions)
        self.median_traded_value = rolling_median(
            [bar.traded_value for bar in self.bars], config.liquidity_sessions
        )

    def index_on_or_before(self, day: date) -> int | None:
        index = bisect_right(self.dates, day) - 1
        return index if index >= 0 else None

    def snapshot(self, i: int) -> Snapshot:
        bar = self.bars[i]
        return Snapshot(
            date=bar.date,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=float(bar.volume),
            prev_high=self.bars[i - 1].high if i > 0 else None,
            sma={period: values[i] for period, values in self.sma.items()},
            rsi=self.rsi[i],
            atr=self.atr[i],
            prior_max_close=self.prior_max_close[i],
            prior_mean_volume=self.prior_mean_volume[i],
            min_low=self.min_low[i],
            median_traded_value=self.median_traded_value[i],
        )


class MarketView:
    def __init__(
        self,
        symbols: Sequence[SymbolInput],
        benchmark: Sequence[AdjustedBar],
        sessions: Sequence[date],
        config: StrategyConfig,
    ) -> None:
        self.config = config
        self.sessions: tuple[date, ...] = tuple(sessions)
        if list(self.sessions) != sorted(set(self.sessions)):
            raise ValueError("sessions must be unique and sorted")
        self._series = {item.symbol: _Series(item.bars, config) for item in symbols}
        self._sectors = {item.symbol: item.sector for item in symbols}
        self._earnings = {item.symbol: sorted(set(item.earnings)) for item in symbols}
        self._benchmark = _Series(benchmark, config)
        self.symbols: tuple[str, ...] = tuple(sorted(self._series))

    def at(self, as_of: date) -> "AsOfView":
        return AsOfView(self, as_of)

    def sessions_between(self, start: date, end: date) -> list[date]:
        return [day for day in self.sessions if start <= day <= end]


class AsOfView:
    """Everything `decide()` may know at the close of `as_of`."""

    def __init__(self, market: MarketView, as_of: date) -> None:
        self._market = market
        self.as_of = as_of
        self.symbols = market.symbols

    def _check(self, day: date) -> None:
        if day > self.as_of:
            raise LookAheadError(f"asked for {day} at the close of {self.as_of}")

    def sector(self, symbol: str) -> str:
        return self._market._sectors[symbol]

    def snapshot(self, symbol: str, day: date | None = None) -> Snapshot | None:
        """The latest bar dated on or before `day` (default: as_of), or None."""
        day = self.as_of if day is None else day
        self._check(day)
        series = self._market._series[symbol]
        index = series.index_on_or_before(day)
        return None if index is None else series.snapshot(index)

    def benchmark(self, day: date | None = None) -> Snapshot | None:
        day = self.as_of if day is None else day
        self._check(day)
        index = self._market._benchmark.index_on_or_before(day)
        return None if index is None else self._market._benchmark.snapshot(index)

    def regime_on(self) -> bool:
        """Benchmark close > its regime SMA at as_of. No data means no entries."""
        snap = self.benchmark()
        if snap is None or snap.date != self.as_of:
            return False
        average = snap.sma[self._market.config.regime_sma]
        return average is not None and snap.close > average

    def is_active(self, symbol: str) -> bool:
        """Backtest liquidity: traded a bar today and its 20-session median value clears the bar."""
        snap = self.snapshot(symbol)
        if snap is None or snap.date != self.as_of or snap.median_traded_value is None:
            return False
        return snap.median_traded_value >= self._market.config.liquidity_min_traded_value

    def earnings_within(self, symbol: str, sessions_ahead: int) -> bool:
        """Whether a known earnings date falls after as_of and on or before the Nth next session.

        The session calendar is known in advance, so reading future sessions is not look-ahead.
        In the backtest, realized SEC 2.02 dates stand in for dates announced in advance.
        """
        sessions = self._market.sessions
        first_after = bisect_right(sessions, self.as_of)
        last = first_after + sessions_ahead - 1
        if last >= len(sessions):
            raise ValueError(
                f"session list ends before {sessions_ahead} sessions after {self.as_of}"
            )
        horizon = sessions[last]
        dates = self._market._earnings.get(symbol, [])
        index = bisect_right(dates, self.as_of)  # first date strictly after as_of
        return index < len(dates) and dates[index] <= horizon
