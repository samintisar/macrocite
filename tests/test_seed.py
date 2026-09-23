import pytest
from sqlmodel import Session, select

from signalbench.db.models import Ticker, TickerKind
from signalbench.ingest.cdr import CdrEntry
from signalbench.ingest.seed import seed_universe

ENTRIES = [
    CdrEntry(
        us_symbol="NVDA",
        cdr_symbol="ZNVD",
        price_symbol="ZNVD.NE",
        company_name="Nvidia",
        sector="Information Technology",
    ),
    CdrEntry(
        us_symbol="JPM",
        cdr_symbol="ZJPM",
        price_symbol="ZJPM.NE",
        company_name="Jpmorgan",
        sector="Financials",
    ),
]


def _by_symbol(session: Session) -> dict[str, Ticker]:
    return {ticker.symbol: ticker for ticker in session.exec(select(Ticker)).all()}


def test_seed_creates_pairs_and_benchmarks(session: Session) -> None:
    result = seed_universe(session, ENTRIES)
    rows = _by_symbol(session)
    assert set(rows) == {"NVDA", "ZNVD", "JPM", "ZJPM", "QQQ", "SPY"}
    assert rows["NVDA"].kind is TickerKind.us_stock
    assert rows["NVDA"].price_symbol == "NVDA"
    assert rows["NVDA"].sector == "Information Technology"
    assert rows["ZNVD"].kind is TickerKind.cdr
    assert rows["ZNVD"].price_symbol == "ZNVD.NE"
    assert rows["ZNVD"].us_ticker_id == rows["NVDA"].id
    assert rows["QQQ"].kind is TickerKind.benchmark
    assert all(row.active for row in rows.values())
    assert result.deactivated == []


def test_seed_is_idempotent(session: Session) -> None:
    seed_universe(session, ENTRIES)
    seed_universe(session, ENTRIES)
    assert len(_by_symbol(session)) == 6


def test_seed_deactivates_names_dropped_from_universe(session: Session) -> None:
    seed_universe(session, ENTRIES)
    result = seed_universe(session, ENTRIES[:1])
    rows = _by_symbol(session)
    assert result.deactivated == ["JPM", "ZJPM"]
    assert rows["JPM"].active is False
    assert rows["ZJPM"].active is False
    assert rows["NVDA"].active is True


def test_reseed_keeps_liquidity_flags(session: Session) -> None:
    seed_universe(session, ENTRIES)
    rows = _by_symbol(session)
    rows["JPM"].active = False
    rows["ZJPM"].active = False
    session.commit()
    seed_universe(session, ENTRIES)
    rows = _by_symbol(session)
    assert rows["JPM"].active is False
    assert rows["ZJPM"].active is False
    assert rows["NVDA"].active is True


def test_seed_refuses_to_change_a_ticker_kind(session: Session) -> None:
    session.add(Ticker(symbol="ZNVD", company_name="Not a CDR", kind=TickerKind.us_stock))
    session.commit()
    with pytest.raises(ValueError, match="ZNVD"):
        seed_universe(session, ENTRIES)
