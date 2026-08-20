from pathlib import Path

from sqlmodel import Session, select

from signalbench.db.models import Ticker
from signalbench.ingest.seed import seed_watchlist

WATCHLIST = Path(__file__).resolve().parents[1] / "data" / "watchlist.yaml"


def test_seed_upserts_twenty_active_tickers(session: Session) -> None:
    count = seed_watchlist(session, WATCHLIST)
    assert count == 20
    rows = session.exec(select(Ticker)).all()
    assert len(rows) == 20
    assert all(row.active for row in rows)
    assert {row.symbol for row in rows} >= {"AAPL", "MSFT", "NVDA"}


def test_seed_is_idempotent(session: Session) -> None:
    seed_watchlist(session, WATCHLIST)
    seed_watchlist(session, WATCHLIST)
    rows = session.exec(select(Ticker)).all()
    assert len(rows) == 20
