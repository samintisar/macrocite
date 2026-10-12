from datetime import UTC, date, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from signalbench.db.models import PaperDay, PaperEvent, PaperPortfolio, PaperRun

T = datetime(2026, 10, 14, 0, 30, tzinfo=UTC)
DAY = date(2026, 10, 13)


def _run(session: Session) -> PaperRun:
    run = PaperRun(started_at=T, status="running", git_sha="abc123")
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _portfolio(session: Session, name: str = "crypto-v1-paper") -> PaperPortfolio:
    portfolio = PaperPortfolio(
        name=name, paper_file="paper_crypto-v1.yaml", strategy_sha256="f" * 64, start=DAY
    )
    session.add(portfolio)
    session.commit()
    session.refresh(portfolio)
    return portfolio


def _day(portfolio: PaperPortfolio, run: PaperRun, state: dict[str, object]) -> PaperDay:
    assert portfolio.id is not None and run.id is not None
    return PaperDay(
        portfolio_id=portfolio.id, day=DAY, equity=100.0, cash=100.0, state=state,
        recorded_at=T, run_id=run.id,
    )


def test_a_run_starts_with_zero_counts(session: Session) -> None:
    run = _run(session)
    assert (run.days_added, run.messages_failed, run.summary_sent) == (0, 0, False)
    assert run.finished_at is None and run.error is None


def test_a_paper_day_reads_its_state_back_exactly(session: Session) -> None:
    state: dict[str, object] = {"cash": 0.1 + 0.2, "positions": [{"units": 0.000527}]}
    session.add(_day(_portfolio(session), _run(session), state))
    session.commit()
    session.expire_all()
    saved = session.get(PaperDay, (1, DAY))
    assert saved is not None
    assert saved.state == state
    assert saved.recorded_at == T


def test_a_day_is_saved_once_per_portfolio(session: Session) -> None:
    portfolio, run = _portfolio(session), _run(session)
    session.add(_day(portfolio, run, {}))
    again = _day(portfolio, run, {})  # built before expunge: it reads the (then loaded) ids
    session.commit()
    session.expunge_all()
    session.add(again)
    with pytest.raises(IntegrityError):
        session.commit()


def test_portfolio_names_are_unique(session: Session) -> None:
    _portfolio(session)
    session.expunge_all()
    with pytest.raises(IntegrityError):
        _portfolio(session)


def test_an_event_keeps_its_payload(session: Session) -> None:
    portfolio, run = _portfolio(session), _run(session)
    assert portfolio.id is not None and run.id is not None
    payload = {"date": DAY.isoformat(), "event": "entry", "price": 62661.648, "trimmed": False}
    session.add(
        PaperEvent(
            portfolio_id=portfolio.id, day=DAY, kind="entry", payload=payload,
            recorded_at=T, run_id=run.id,
        )
    )
    session.commit()
    session.expire_all()
    event = session.get(PaperEvent, 1)
    assert event is not None and event.payload == payload
