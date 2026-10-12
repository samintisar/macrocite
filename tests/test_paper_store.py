from datetime import UTC, date, datetime

from sqlmodel import Session, select

from paper_helpers import BREAKOUT, DAYS, START, all_bars, config, up_to
from signalbench.backtest.crypto_runner import crypto_market
from signalbench.db.models import PaperDay, PaperEvent, PaperPortfolio, PaperRun
from signalbench.market.calendar import DailySessions
from signalbench.paper.replay import ReplayDay, replay
from signalbench.paper.store import (
    append_days,
    find_portfolio,
    first_difference,
    load_saved,
)

T = datetime(2014, 9, 9, 0, 30, tzinfo=UTC)


def _ids(session: Session) -> tuple[int, int]:
    run = PaperRun(started_at=T, status="running", git_sha="abc123")
    portfolio = PaperPortfolio(
        name="crypto-test-paper", paper_file="paper.yaml", strategy_sha256="f" * 64,
        start=DAYS[START],
    )
    session.add(run)
    session.add(portfolio)
    session.commit()
    session.refresh(run)
    session.refresh(portfolio)
    assert run.id is not None and portfolio.id is not None
    return portfolio.id, run.id


def _replay(last: date) -> list[ReplayDay]:
    market = crypto_market(up_to(all_bars(), last), config(), DailySessions(), DAYS[START], last)
    return replay(market, config(), DAYS[START], last)


def _save(session: Session, days: list[ReplayDay]) -> int:
    portfolio_id, run_id = _ids(session)
    append_days(session, portfolio_id=portfolio_id, run_id=run_id, days=days, now=T)
    session.commit()
    session.expire_all()
    return portfolio_id


def test_saved_days_read_back_exactly_as_replayed(session: Session) -> None:
    days = _replay(DAYS[-1])
    portfolio_id = _save(session, days)
    assert first_difference(load_saved(session, portfolio_id), days) is None


def test_a_later_replay_extends_the_saved_days_unchanged(session: Session) -> None:
    portfolio_id = _save(session, _replay(DAYS[BREAKOUT + 5]))
    later = _replay(DAYS[-1])
    saved = load_saved(session, portfolio_id)
    assert first_difference(saved, later) is None
    assert [day.day for day in later[len(saved):]] == DAYS[BREAKOUT + 6 :]


def test_an_edited_equity_is_reported(session: Session) -> None:
    days = _replay(DAYS[START + 5])
    portfolio_id = _save(session, days)
    row = session.get(PaperDay, (portfolio_id, DAYS[START + 2]))
    assert row is not None
    row.equity += 0.01
    session.add(row)
    session.commit()
    message = first_difference(load_saved(session, portfolio_id), days)
    assert message is not None
    assert message.startswith(f"{DAYS[START + 2].isoformat()}: equity differs")


def test_a_lost_event_is_reported(session: Session) -> None:
    days = _replay(DAYS[BREAKOUT + 1])
    portfolio_id = _save(session, days)
    entry = session.exec(select(PaperEvent).where(PaperEvent.kind == "entry")).one()
    session.delete(entry)
    session.commit()
    message = first_difference(load_saved(session, portfolio_id), days)
    assert message is not None
    assert message.startswith(f"{DAYS[BREAKOUT + 1].isoformat()}: events differs")


def test_a_saved_day_the_replay_does_not_reach_is_reported(session: Session) -> None:
    portfolio_id = _save(session, _replay(DAYS[START + 5]))
    shorter = _replay(DAYS[START + 3])
    assert first_difference(load_saved(session, portfolio_id), shorter) == (
        f"{DAYS[START + 4].isoformat()}: saved but not replayed"
    )


def test_events_are_saved_with_their_kind_run_and_time(session: Session) -> None:
    _save(session, _replay(DAYS[BREAKOUT + 1]))
    entry = session.exec(select(PaperEvent).where(PaperEvent.kind == "entry")).one()
    assert (entry.day, entry.payload["symbol"], entry.recorded_at) == (
        DAYS[BREAKOUT + 1], "XBTUSD", T,
    )


def test_find_portfolio_by_name(session: Session) -> None:
    _ids(session)
    found = find_portfolio(session, "crypto-test-paper")
    assert found is not None and found.start == DAYS[START]
    assert find_portfolio(session, "other") is None
