"""Kraken daily candles for BTC and ETH (spec 09): the downloadable OHLCVT files and the
public OHLC endpoint. Research only: nothing here trades or needs an API key."""

import csv
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from sqlmodel import Session, select

from signalbench.db.models import Ticker, TickerKind
from signalbench.ingest.prices import DailyBar, PriceIngestResult, ingest_daily_prices

KRAKEN_OHLC_URL = "https://api.kraken.com/0/public/OHLC"
DAY_SECONDS = 86_400
DAILY_INTERVAL = 1440  # minutes per candle
CRYPTO_SECTOR = "crypto"
CRYPTO_NAMES = {"ETHUSD": "Ether (Kraken ETH/USD)", "XBTUSD": "Bitcoin (Kraken XBT/USD)"}


class KrakenError(ValueError):
    """A Kraken file row could not be read, or Kraken's API answered with an error."""


def utc_day(timestamp: int) -> date:
    """The UTC day a candle starting at `timestamp` (Unix seconds) covers."""
    return datetime.fromtimestamp(timestamp, tz=UTC).date()


def _price(text: str) -> Decimal:
    return Decimal(str(round(float(text), 4)))


def _bar(timestamp: int, open_: str, high: str, low: str, close: str, volume: str) -> DailyBar:
    """Crypto has no splits or dividends, so adj_close = close. Volume is in coins, rounded to
    a whole coin to fit `prices.volume`."""
    return DailyBar(
        date=utc_day(timestamp),
        open=_price(open_),
        high=_price(high),
        low=_price(low),
        close=_price(close),
        adj_close=_price(close),
        volume=round(float(volume)),
    )


def parse_ohlcvt_file(path: Path) -> list[DailyBar]:
    """One Kraken OHLCVT daily file: no header; timestamp, open, high, low, close, volume,
    trades. Refuses a row with another field count, or a candle that does not start at
    00:00 UTC (a 1-minute or hourly file passed by mistake)."""
    bars: list[DailyBar] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for number, row in enumerate(csv.reader(handle), start=1):
            if len(row) != 7:
                raise KrakenError(f"{path.name} line {number}: expected 7 fields, got {len(row)}")
            timestamp = int(row[0])
            if timestamp % DAY_SECONDS != 0:
                started = datetime.fromtimestamp(timestamp, tz=UTC).isoformat()
                raise KrakenError(
                    f"{path.name} line {number}: not a daily (1440) file, a candle starts at "
                    f"{started}"
                )
            bars.append(_bar(timestamp, row[1], row[2], row[3], row[4], row[5]))
    return bars


def fetch_kraken_daily(client: httpx.Client, pair: str, today: date) -> list[DailyBar]:
    """The daily candles Kraken's public OHLC endpoint returns (about the last 720 days),
    without any dated `today` (UTC) or later: that day is still trading.

    Each row is [time, open, high, low, close, vwap, volume, count]; volume, not vwap, is kept.
    """
    response = client.get(
        KRAKEN_OHLC_URL, params={"pair": pair, "interval": str(DAILY_INTERVAL)}
    )
    response.raise_for_status()
    body: dict[str, Any] = response.json()
    if body.get("error"):
        raise KrakenError(f"Kraken OHLC {pair}: {', '.join(body['error'])}")
    result: dict[str, Any] = body["result"]
    rows: list[list[Any]] = next(value for key, value in result.items() if key != "last")
    bars = [_bar(int(row[0]), row[1], row[2], row[3], row[4], row[6]) for row in rows]
    return [bar for bar in bars if bar.date < today]


def find_crypto_ticker(session: Session, symbol: str) -> Ticker | None:
    """The stored crypto ticker for a Kraken pair, or None before its first ingest."""
    if symbol not in CRYPTO_NAMES:
        raise KrakenError(f"Unknown pair {symbol}; spec 09 covers {', '.join(CRYPTO_NAMES)}")
    ticker = session.exec(select(Ticker).where(Ticker.symbol == symbol)).first()
    if ticker is not None and ticker.kind != TickerKind.crypto:
        raise KrakenError(f"{symbol} is already a {ticker.kind.value} ticker")
    return ticker


def crypto_ticker(session: Session, symbol: str) -> Ticker:
    """The crypto ticker for a Kraken pair, created on first use."""
    ticker = find_crypto_ticker(session, symbol)
    if ticker is None:
        ticker = Ticker(
            symbol=symbol,
            company_name=CRYPTO_NAMES[symbol],
            sector=CRYPTO_SECTOR,
            kind=TickerKind.crypto,
        )
        session.add(ticker)
        session.commit()
        session.refresh(ticker)
    return ticker


def ingest_kraken_bars(
    session: Session, ticker: Ticker, bars: list[DailyBar], history_start: date
) -> PriceIngestResult:
    """Upsert `bars` with the stock price upsert: bad bars are rejected and unchanged rows
    are not rewritten. `full=True`: Kraken closes are never rescaled, so the dividend/split
    refetch check does not apply."""
    return ingest_daily_prices(
        session,
        ticker,
        fetch=lambda _symbol, _start: bars,
        history_start=history_start,
        full=True,
    )
