"""Synthetic bars, sessions, and views shared by the strategy and backtest tests."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from signalbench.market.bars import AdjustedBar
from signalbench.strategy.config import SetupName, StrategyConfig, load_strategy_config
from signalbench.strategy.market_view import MarketView, Snapshot, SymbolInput
from signalbench.strategy.portfolio import PortfolioState, Position

FIXTURE_CONFIG = Path(__file__).parent / "fixtures" / "strategy_test.yaml"
NEW_YORK = ZoneInfo("America/New_York")
VOLUME = 1_000_000


def load_test_config() -> StrategyConfig:
    return load_strategy_config(FIXTURE_CONFIG)[0]


def weekdays(start: date, count: int) -> list[date]:
    """`count` Monday-to-Friday dates from `start` (the fixture calendar; no holidays)."""
    days: list[date] = []
    day = start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def make_bar(
    day: date,
    close: float,
    *,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
    volume: int = VOLUME,
) -> AdjustedBar:
    """Defaults: open = close, high = close + 1, low = close - 1."""
    return AdjustedBar(
        date=day,
        open=close if open_ is None else open_,
        high=close + 1.0 if high is None else high,
        low=close - 1.0 if low is None else low,
        close=close,
        volume=volume,
        traded_value=close * volume,
    )


def trend_bars(days: Sequence[date], start: float, step: float) -> list[AdjustedBar]:
    """A straight line: close_i = start + step * i."""
    return [make_bar(day, start + step * i) for i, day in enumerate(days)]


def series(days: Sequence[date], closes: Sequence[float]) -> list[AdjustedBar]:
    return [make_bar(day, close) for day, close in zip(days, closes, strict=True)]


def pullback_closes(count: int, dip: int = 251) -> list[float]:
    """Uptrend 100 + 0.2 i, then -0.8 on dip - 1 and -3.0 on `dip` (RSI(2) about 2.9),
    then flat at the dip close. The pullback setup fires on `dip` and not before."""
    closes = [100.0 + 0.2 * i for i in range(dip - 1)]
    closes.append(closes[-1] - 0.8)
    closes.append(closes[-1] - 3.0)
    closes.extend([closes[-1]] * (count - len(closes)))
    return closes


def with_bar(bars: list[AdjustedBar], index: int, **changes: float) -> list[AdjustedBar]:
    """Copy of `bars` with bar `index` changed (keeps traded_value = close * volume)."""
    out = list(bars)
    bar = replace(out[index], **changes)
    out[index] = replace(bar, traded_value=bar.close * bar.volume)
    return out


def make_market(
    bars: Mapping[str, list[AdjustedBar]],
    benchmark: list[AdjustedBar],
    sessions: Sequence[date],
    config: StrategyConfig,
    *,
    sectors: Mapping[str, str] | None = None,
    earnings: Mapping[str, list[date]] | None = None,
) -> MarketView:
    sectors = sectors or {}
    earnings = earnings or {}
    return MarketView(
        [
            SymbolInput(
                symbol=symbol,
                sector=sectors.get(symbol, "Information Technology"),
                bars=symbol_bars,
                earnings=earnings.get(symbol, []),
            )
            for symbol, symbol_bars in bars.items()
        ],
        benchmark,
        sessions,
        config,
    )


def make_snapshot(**changes: object) -> Snapshot:
    """A snapshot where no setup fires; override fields to build a case."""
    base = Snapshot(
        date=date(2024, 1, 2),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=float(VOLUME),
        prev_high=101.0,
        sma={20: 100.0, 50: 100.0, 200: 100.0},
        rsi=50.0,
        atr=2.0,
        prior_max_close=100.0,
        prior_mean_volume=float(VOLUME),
        min_low=99.0,
        median_traded_value=100_000_000.0,
    )
    return replace(base, **changes)


def make_position(
    position_id: str,
    symbol: str,
    *,
    setup: SetupName = "pullback",
    sector: str = "Information Technology",
    units: float = 0.1,
    entry_price: float = 100.0,
    entry_date: date = date(2023, 1, 2),
    stop: float = 90.0,
    target: float | None = 200.0,
    time_limit: int = 10,
    sessions_held: int = 1,
    highest_close: float = 100.0,
) -> Position:
    return Position(
        id=position_id,
        symbol=symbol,
        setup=setup,
        sector=sector,
        units=units,
        entry_price=entry_price,
        entry_date=entry_date,
        stop=stop,
        target=target,
        time_limit=time_limit,
        sessions_held=sessions_held,
        highest_close=highest_close,
    )


def empty_portfolio(cash: float) -> PortfolioState:
    return PortfolioState(
        cash=cash,
        positions=(),
        pending=(),
        equity=cash,
        peak=cash,
        paused=False,
        paused_since=None,
    )


class WeekdaySessions:
    """A `Sessions` calendar where every Monday-to-Friday is a session."""

    def sessions_between(self, start: date, end: date) -> list[date]:
        days: list[date] = []
        day = start
        while day <= end:
            if day.weekday() < 5:
                days.append(day)
            day += timedelta(days=1)
        return days

    def next_sessions(self, day: date, count: int) -> list[date]:
        return weekdays(day + timedelta(days=1), count)

    def is_session(self, day: date) -> bool:
        return day.weekday() < 5

    def session_closes(self, start: date, end: date) -> list[tuple[date, datetime]]:
        """Every fixture session closes at 16:00 New York time."""
        return [
            (day, datetime.combine(day, time(16, 0), tzinfo=NEW_YORK))
            for day in self.sessions_between(start, end)
        ]
