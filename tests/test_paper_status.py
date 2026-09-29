"""`paper status` (spec 07): one line per portfolio, days until judgeable, and the stale check."""

from datetime import UTC, date, datetime

from sqlmodel import Session

from signalbench.db.models import PaperEquity, PaperEvent, PaperPortfolio, PaperRun
from signalbench.paper.status import judge_date, judgeable, stale_message, status_lines

START = date(2026, 10, 12)
NOW = datetime(2026, 10, 20, 22, 0, tzinfo=UTC)


def _portfolio(session: Session, name: str, created_at: datetime = NOW) -> int:
    portfolio = PaperPortfolio(
        name=name, config_path="data/strategy_test.yaml", config_sha256="a" * 64,
        setup="breakout", started_on=START, state={}, created_at=created_at,
    )
    session.add(portfolio)
    session.commit()
    session.refresh(portfolio)
    assert portfolio.id is not None
    return portfolio.id


def test_one_line_per_portfolio(session: Session) -> None:
    stepped = _portfolio(session, "p-stepped")
    _portfolio(session, "p-new")
    for day, equity, held in ((START, 100.0, 0), (date(2026, 10, 13), 101.5, 1)):
        session.add(PaperEquity(portfolio_id=stepped, session=day, equity=equity, cash=0.0,
                                vehicle_value=0.0, open_positions=held, catch_up=False))
    session.add(PaperEvent(portfolio_id=stepped, session=START, kind="fill_exit", payload={},
                           recorded_at=NOW, catch_up=False))
    session.add(PaperRun(started_at=datetime(2026, 10, 13, 22, 0, tzinfo=UTC), status="ok"))
    session.commit()
    assert status_lines(session, date(2026, 10, 20)) == [
        "last ok run: 2026-10-13T22:00+00:00",
        (
            "p-stepped: last 2026-10-13 | equity 101.50 | return +1.50% | open 1 | closed 1 "
            "| judgeable after 357 days and 29 more closed trades"
        ),
        "p-new: starts 2026-10-12, not stepped yet | judgeable after 357 days and 30 more closed trades",
    ]


def test_judgeable_counts_twelve_months_and_thirty_closed_trades() -> None:
    assert judge_date(date(2026, 10, 12)) == date(2027, 10, 12)
    assert judge_date(date(2028, 2, 29)) == date(2029, 2, 28)
    assert judgeable(date(2025, 1, 1), 30, date(2026, 1, 2)) == "judgeable now"
    assert judgeable(date(2025, 1, 1), 12, date(2026, 1, 2)) == "judgeable after 18 more closed trades"
    assert judgeable(date(2025, 1, 1), 45, date(2025, 12, 30)) == "judgeable after 2 days"


def test_stale_when_no_run_has_succeeded_for_more_than_three_days(session: Session) -> None:
    assert stale_message(session, NOW, 3) is None  # no portfolios: nothing to watch
    _portfolio(session, "p1", created_at=datetime(2026, 10, 18, 22, 0, tzinfo=UTC))
    assert stale_message(session, NOW, 3) is None  # created two days ago, no run yet
    assert stale_message(session, datetime(2026, 10, 22, 23, 0, tzinfo=UTC), 3) == (
        "STALE: the last ok paper run was never, more than 3 days ago."
    )
    session.add(PaperRun(started_at=datetime(2026, 10, 16, 22, 0, tzinfo=UTC), status="ok"))
    session.add(PaperRun(started_at=datetime(2026, 10, 19, 22, 0, tzinfo=UTC), status="failed"))
    session.commit()
    assert stale_message(session, NOW, 3) == (
        "STALE: the last ok paper run was on 2026-10-16T22:00+00:00, more than 3 days ago."
    )
    session.add(PaperRun(started_at=datetime(2026, 10, 19, 23, 0, tzinfo=UTC), status="ok"))
    session.commit()
    assert stale_message(session, NOW, 3) is None
