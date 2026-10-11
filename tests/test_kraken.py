from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlmodel import Session, col, select

from signalbench.db.models import Price, Ticker, TickerKind
from signalbench.ingest.kraken import (
    KRAKEN_OHLC_URL,
    KrakenError,
    crypto_ticker,
    fetch_kraken_daily,
    find_crypto_ticker,
    ingest_kraken_bars,
    parse_ohlcvt_file,
)

FIXTURE = Path(__file__).parent / "fixtures" / "kraken" / "XBTUSD_1440.csv"
HISTORY_START = date(2010, 1, 1)
TODAY = date(2026, 10, 11)  # the third OHLC row below is today's candle, still trading
OHLC: dict[str, Any] = {
    "error": [],
    "result": {
        "XXBTZUSD": [
            [1791504000, "82000.0", "83000.0", "81000.0", "82557.1", "82500.0", "700.4", 50000],
            [1791590400, "82557.1", "83089.7", "82474.8", "82912.7", "82803.8", "490.31", 54517],
            [1791676800, "82912.7", "82999.6", "82853.3", "82971.3", "82913.3", "52.61", 3608],
        ],
        "last": 1791590400,
    },
}


def _client(
    body: dict[str, Any], status: int = 200, seen: list[httpx.Request] | None = None
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_parse_dates_each_candle_by_its_utc_day_and_rounds_volume() -> None:
    bars = parse_ohlcvt_file(FIXTURE)
    assert [bar.date for bar in bars] == [date(2015, 1, 1), date(2015, 1, 2), date(2015, 1, 3)]
    first = bars[0]
    assert (first.open, first.high, first.low, first.close) == (
        Decimal("314.0"),
        Decimal("320.5"),
        Decimal("310.2"),
        Decimal("318.7"),
    )
    assert first.adj_close == first.close
    assert (first.volume, bars[2].volume) == (1235, 2000)


def test_parse_refuses_a_file_that_is_not_daily(tmp_path: Path) -> None:
    path = tmp_path / "XBTUSD_1.csv"
    path.write_text("1420070400,1,1,1,1,1,1\n1420070460,1,1,1,1,1,1\n", encoding="utf-8")
    with pytest.raises(KrakenError, match="line 2: not a daily"):
        parse_ohlcvt_file(path)


def test_parse_refuses_a_row_with_the_wrong_field_count(tmp_path: Path) -> None:
    path = tmp_path / "XBTUSD_1440.csv"
    path.write_text("1420070400,1,1,1,1,1\n", encoding="utf-8")
    with pytest.raises(KrakenError, match="line 1: expected 7 fields, got 6"):
        parse_ohlcvt_file(path)


def test_ingest_creates_the_ticker_rejects_bad_bars_and_is_idempotent(session: Session) -> None:
    ticker = crypto_ticker(session, "XBTUSD")
    bars = parse_ohlcvt_file(FIXTURE)
    first = ingest_kraken_bars(session, ticker, bars, HISTORY_START)
    second = ingest_kraken_bars(session, ticker, bars, HISTORY_START)
    assert (first.created, first.updated, first.rejected) == (2, 0, 1)  # low 0 on 2015-01-02
    assert (second.created, second.updated, second.rejected) == (0, 0, 1)
    stored = session.exec(select(Ticker)).one()
    assert (stored.kind, stored.sector) == (TickerKind.crypto, "crypto")
    rows = session.exec(select(Price).order_by(col(Price.date))).all()
    assert [row.date for row in rows] == [date(2015, 1, 1), date(2015, 1, 3)]
    assert rows[0].adj_close == rows[0].close


def test_crypto_ticker_is_reused_and_refuses_unknown_pairs_and_other_kinds(
    session: Session,
) -> None:
    assert find_crypto_ticker(session, "ETHUSD") is None
    assert crypto_ticker(session, "ETHUSD").id == crypto_ticker(session, "ETHUSD").id
    with pytest.raises(KrakenError, match="Unknown pair DOGEUSD"):
        crypto_ticker(session, "DOGEUSD")
    session.add(Ticker(symbol="XBTUSD", company_name="x", kind=TickerKind.us_stock))
    session.commit()
    with pytest.raises(KrakenError, match="already a us_stock ticker"):
        crypto_ticker(session, "XBTUSD")


def test_fetch_drops_the_day_in_progress_and_reads_volume_not_vwap() -> None:
    seen: list[httpx.Request] = []
    bars = fetch_kraken_daily(_client(OHLC, seen=seen), "XBTUSD", today=TODAY)
    assert [bar.date for bar in bars] == [date(2026, 10, 9), date(2026, 10, 10)]
    assert bars[-1].close == Decimal("82912.7")
    assert bars[-1].volume == 490
    assert str(seen[0].url).startswith(KRAKEN_OHLC_URL)
    assert (seen[0].url.params["pair"], seen[0].url.params["interval"]) == ("XBTUSD", "1440")


def test_fetch_raises_on_a_kraken_error() -> None:
    body = {"error": ["EQuery:Unknown asset pair"]}
    with pytest.raises(KrakenError, match="EQuery:Unknown asset pair"):
        fetch_kraken_daily(_client(body), "XBTUSD", today=TODAY)


def test_fetch_raises_on_an_http_error() -> None:
    with pytest.raises(httpx.HTTPStatusError):
        fetch_kraken_daily(_client({}, status=502), "XBTUSD", today=TODAY)


def test_recent_candles_are_stored_once_without_today(session: Session) -> None:
    ticker = crypto_ticker(session, "XBTUSD")
    bars = fetch_kraken_daily(_client(OHLC), "XBTUSD", today=TODAY)
    first = ingest_kraken_bars(session, ticker, bars, HISTORY_START)
    again = ingest_kraken_bars(session, ticker, bars, HISTORY_START)
    assert (first.created, again.created, again.updated) == (2, 0, 0)
    assert TODAY not in {row.date for row in session.exec(select(Price)).all()}
