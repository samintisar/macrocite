from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlmodel import Session, select

from signalbench.db.models import Price, Ticker
from signalbench.ingest.cdr import CdrEntry
from signalbench.ingest.liquidity import update_liquidity_flags
from signalbench.ingest.seed import seed_universe

DAY0 = date(2026, 8, 3)


def _entry(us: str, cdr: str) -> CdrEntry:
    return CdrEntry(
        us_symbol=us,
        cdr_symbol=cdr,
        price_symbol=f"{cdr}.NE",
        company_name=us,
        sector="Information Technology",
    )


def _ticker(session: Session, symbol: str) -> Ticker:
    return session.exec(select(Ticker).where(Ticker.symbol == symbol)).one()


def _prices(session: Session, symbol: str, days: list[date], close: str, volume: int) -> None:
    ticker = _ticker(session, symbol)
    for day in days:
        session.add(
            Price(
                ticker_id=ticker.id,
                date=day,
                open=Decimal(close),
                high=Decimal(close),
                low=Decimal(close),
                close=Decimal(close),
                adj_close=Decimal(close),
                volume=volume,
            )
        )
    session.commit()


def _days(count: int) -> list[date]:
    return [DAY0 + timedelta(days=offset) for offset in range(count)]


def test_liquidity_flags(session: Session) -> None:
    entries = [
        _entry("NVDA", "ZNVD"),
        _entry("EDGE", "ZEDG"),
        _entry("THIN", "ZTHN"),
        _entry("NEWB", "ZNEW"),
        _entry("STAL", "ZSTL"),
        _entry("GONE", "ZGON"),
    ]
    seed_universe(session, entries)
    days = _days(20)
    _prices(session, "QQQ", days, "400", 1)
    stale = [DAY0 - timedelta(days=offset) for offset in range(40, 20, -1)]
    _prices(session, "GONE", stale, "100", 1_000_000)  # liquid, but no price in the last 5 sessions
    _prices(session, "ZGON", days[-1:], "12", 5)
    _prices(session, "NVDA", days, "100", 1_000_000)  # median 100M: active
    _prices(session, "EDGE", days, "50", 1_000_000)  # exactly 50M: active
    _prices(session, "THIN", days, "10", 1_000_000)  # 10M: illiquid
    _prices(session, "NEWB", days[:19], "100", 1_000_000)  # 19 rows: too short
    _prices(session, "STAL", days, "100", 1_000_000)
    for cdr in ("ZNVD", "ZEDG", "ZTHN", "ZNEW"):
        _prices(session, cdr, days[-1:], "12", 0)  # zero volume still counts as priced
    _prices(session, "ZSTL", days[:10], "12", 5)  # last CDR price is older than 5 sessions

    result = update_liquidity_flags(session, entries)

    assert result.active == ["EDGE", "NVDA"]
    assert result.inactive == {
        "THIN": "us_illiquid",
        "NEWB": "us_history_short",
        "STAL": "cdr_no_recent_price",
        "GONE": "us_no_recent_price",
    }
    assert _ticker(session, "ZNVD").active is True
    assert _ticker(session, "THIN").active is False
    assert _ticker(session, "ZTHN").active is False


def test_requires_benchmark_sessions(session: Session) -> None:
    seed_universe(session, [_entry("NVDA", "ZNVD")])
    with pytest.raises(RuntimeError, match="QQQ"):
        update_liquidity_flags(session, [_entry("NVDA", "ZNVD")])


def test_unseeded_entry_is_inactive(session: Session) -> None:
    seed_universe(session, [])
    _prices(session, "QQQ", _days(5), "400", 1)
    result = update_liquidity_flags(session, [_entry("NVDA", "ZNVD")])
    assert result.inactive == {"NVDA": "not_seeded"}
