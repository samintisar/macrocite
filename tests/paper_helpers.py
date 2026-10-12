"""Shared fixtures for the spec 11 paper tests: BTC breaks out and later crashes, ETH stays
flat, and every calendar day has a candle."""

from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from signalbench.ingest.prices import DailyBar
from signalbench.market.bars import AdjustedBar
from signalbench.strategy.config import (
    StrategyConfig,
    config_sha256,
    load_strategy_config,
)
from strategy_helpers import make_bar

REPO = Path(__file__).resolve().parents[1]
STRATEGY = "tests/fixtures/strategy_crypto_test.yaml"
DAYS = [date(2014, 1, 1) + timedelta(days=i) for i in range(300)]
START = 250  # 2014-09-08: the paper start day
BREAKOUT = 260  # close 110 on double volume: an entry order at this close
CRASH = 280  # close 100, below the stop: an exit order at this close
Series = dict[str, list[AdjustedBar]]
Fetch = Callable[[str, date], list[DailyBar]]


def btc_bars() -> list[AdjustedBar]:
    bars = []
    for i, day in enumerate(DAYS):
        if i < BREAKOUT:
            bars.append(make_bar(day, 100.0, volume=1000))
        elif i == BREAKOUT:
            bars.append(make_bar(day, 110.0, volume=2000))
        elif i < CRASH:
            bars.append(make_bar(day, 110.0, volume=1000))
        else:
            bars.append(make_bar(day, 100.0, volume=1000))
    return bars


def all_bars() -> Series:
    return {"ETHUSD": [make_bar(day, 50.0, volume=1000) for day in DAYS], "XBTUSD": btc_bars()}


def up_to(series: Series, last: date) -> Series:
    return {symbol: [bar for bar in bars if bar.date <= last] for symbol, bars in series.items()}


def config() -> StrategyConfig:
    return load_strategy_config(REPO / STRATEGY)[0].with_setups(("breakout",))


def strategy_sha() -> str:
    return config_sha256((REPO / STRATEGY).read_bytes())


def write_paper_file(
    directory: Path, *, start: date = DAYS[START], sha: str | None = None
) -> Path:
    path = directory / "paper_crypto-test.yaml"
    path.write_text(
        "name: crypto-test-paper\n"
        f"strategy: {STRATEGY}\n"
        f"strategy_sha256: {sha or strategy_sha()}\n"
        "setup: breakout\n"
        f"start: {start.isoformat()}\n"
        "review:\n  months: 12\n  max_late_days: 7\n  max_drawdown: 0.35\n",
        encoding="utf-8",
    )
    return path


def daily_bar(bar: AdjustedBar) -> DailyBar:
    return DailyBar(
        date=bar.date,
        open=Decimal(str(bar.open)),
        high=Decimal(str(bar.high)),
        low=Decimal(str(bar.low)),
        close=Decimal(str(bar.close)),
        adj_close=Decimal(str(bar.close)),
        volume=bar.volume,
    )


def fake_fetch(series: Series) -> Fetch:
    """Kraken's OHLC endpoint on `series`: every finished candle before `today`."""

    def fetch(pair: str, today: date) -> list[DailyBar]:
        return [daily_bar(bar) for bar in series[pair] if bar.date < today]

    return fetch


def night(day: date) -> datetime:
    """00:30 UTC after `day`'s candle closed: the nightly run that records `day`."""
    return datetime.combine(day + timedelta(days=1), time(0, 30), tzinfo=UTC)
