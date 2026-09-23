from datetime import date
from decimal import Decimal

from sqlmodel import Session, select

from signalbench.db.models import Price, Ticker, TickerKind
from signalbench.ingest.prices import DailyBar, fetch_start, ingest_daily_prices

HISTORY_START = date(2010, 1, 1)


def _bar(day: date, adj_close: str = "185.2000", high: str = "186.0000", close: str = "185.5000") -> DailyBar:
    return DailyBar(
        date=day,
        open=Decimal("185.0000"),
        high=Decimal(high),
        low=Decimal("184.0000"),
        close=Decimal(close),
        adj_close=Decimal(adj_close),
        volume=50_000_000,
    )


def _ticker(session: Session, symbol: str = "AAPL", price_symbol: str | None = None) -> Ticker:
    ticker = Ticker(symbol=symbol, company_name=symbol, price_symbol=price_symbol)
    session.add(ticker)
    session.commit()
    session.refresh(ticker)
    return ticker


def test_ingest_writes_adj_close_and_is_idempotent(session: Session) -> None:
    ticker = _ticker(session)

    def fetch(_symbol: str, _start: date) -> list[DailyBar]:
        return [_bar(date(2024, 1, 2))]

    first = ingest_daily_prices(session, ticker, fetch=fetch, history_start=HISTORY_START)
    second = ingest_daily_prices(session, ticker, fetch=fetch, history_start=HISTORY_START)
    assert (first.created, first.updated, first.rejected) == (1, 0, 0)
    assert (second.created, second.updated, second.rejected) == (0, 0, 0)
    row = session.exec(select(Price)).one()
    assert row.adj_close == Decimal("185.2000")
    assert row.ticker_id == ticker.id


def test_fetch_start_uses_history_start_then_refetches_ten_days(session: Session) -> None:
    ticker = _ticker(session)
    assert fetch_start(session, ticker, HISTORY_START) == HISTORY_START
    ingest_daily_prices(
        session,
        ticker,
        fetch=lambda _s, _d: [_bar(date(2010, 1, 4)), _bar(date(2024, 1, 12))],
        history_start=HISTORY_START,
    )
    assert fetch_start(session, ticker, HISTORY_START) == date(2024, 1, 2)


def test_fetch_start_backfills_history_that_starts_late(session: Session) -> None:
    ticker = _ticker(session)
    ingest_daily_prices(
        session, ticker, fetch=lambda _s, _d: [_bar(date(2024, 1, 12))], history_start=HISTORY_START
    )
    assert fetch_start(session, ticker, HISTORY_START) == HISTORY_START


def test_rescaled_history_triggers_full_refetch(session: Session) -> None:
    ticker = _ticker(session)
    old = [_bar(date(2010, 1, 4)), _bar(date(2024, 1, 10)), _bar(date(2024, 1, 12))]
    ingest_daily_prices(session, ticker, fetch=lambda _s, _d: old, history_start=HISTORY_START)
    requested: list[date] = []

    def fetch(_symbol: str, start: date) -> list[DailyBar]:
        requested.append(start)
        # A dividend lowered every earlier adj_close, including the 2024-01-10 overlap row.
        return [
            _bar(day, adj_close="180.0000")
            for day in (date(2010, 1, 4), date(2024, 1, 10), date(2024, 1, 12))
            if day >= start
        ]

    result = ingest_daily_prices(session, ticker, fetch=fetch, history_start=HISTORY_START)
    assert requested == [date(2024, 1, 2), HISTORY_START]
    assert result.updated == 3
    assert {row.adj_close for row in session.exec(select(Price)).all()} == {Decimal("180.0000")}


def test_change_to_newest_row_alone_is_not_a_rescale(session: Session) -> None:
    ticker = _ticker(session)
    old = [_bar(date(2010, 1, 4)), _bar(date(2024, 1, 12))]
    ingest_daily_prices(session, ticker, fetch=lambda _s, _d: old, history_start=HISTORY_START)
    requested: list[date] = []

    def fetch(_symbol: str, start: date) -> list[DailyBar]:
        requested.append(start)
        return [_bar(date(2024, 1, 12), close="186.0000")]

    ingest_daily_prices(session, ticker, fetch=fetch, history_start=HISTORY_START)
    assert requested == [date(2024, 1, 2)]


def test_refetch_updates_changed_values(session: Session) -> None:
    ticker = _ticker(session)
    ingest_daily_prices(
        session, ticker, fetch=lambda _s, _d: [_bar(date(2024, 1, 2))], history_start=HISTORY_START
    )
    result = ingest_daily_prices(
        session,
        ticker,
        fetch=lambda _s, _d: [_bar(date(2024, 1, 2), adj_close="180.0000")],
        history_start=HISTORY_START,
    )
    assert (result.created, result.updated) == (0, 1)
    assert session.exec(select(Price)).one().adj_close == Decimal("180.0000")


def test_invalid_bars_are_rejected(session: Session) -> None:
    ticker = _ticker(session)
    bars = [
        _bar(date(2024, 1, 2), high="100.0000"),
        _bar(date(2024, 1, 3), close="0"),
    ]
    result = ingest_daily_prices(session, ticker, fetch=lambda _s, _d: bars, history_start=HISTORY_START)
    assert (result.created, result.rejected) == (0, 2)
    assert session.exec(select(Price)).all() == []


def test_fetch_uses_price_symbol(session: Session) -> None:
    ticker = _ticker(session, symbol="ZNVD", price_symbol="ZNVD.NE")
    ticker.kind = TickerKind.cdr
    requested: list[tuple[str, date]] = []

    def fetch(symbol: str, start: date) -> list[DailyBar]:
        requested.append((symbol, start))
        return []

    ingest_daily_prices(session, ticker, fetch=fetch, history_start=HISTORY_START)
    assert requested == [("ZNVD.NE", HISTORY_START)]


def test_full_refetch_asks_for_history_start_despite_stored_rows(session: Session) -> None:
    ticker = _ticker(session)
    ingest_daily_prices(
        session, ticker, fetch=lambda _s, _d: [_bar(date(2024, 1, 12))], history_start=HISTORY_START
    )
    requested: list[date] = []

    def fetch(_symbol: str, start: date) -> list[DailyBar]:
        requested.append(start)
        return [_bar(date(2010, 1, 4)), _bar(date(2024, 1, 12))]

    result = ingest_daily_prices(
        session, ticker, fetch=fetch, history_start=HISTORY_START, full=True
    )
    assert requested == [HISTORY_START]
    assert (result.created, result.updated) == (1, 0)
    assert len(session.exec(select(Price)).all()) == 2
