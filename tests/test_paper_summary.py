import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from pytest import approx
from sqlmodel import Session

from signalbench.db.models import PaperDay, PaperEvent, PaperPortfolio, PaperRun, Price
from signalbench.ingest.kraken import crypto_ticker
from signalbench.paper.paper_file import PaperError, PaperFile, ReviewRules
from signalbench.paper.summary import (
    close_deadline,
    paper_review,
    paper_summary,
    summary_text,
    worst_drawdown,
)

START = date(2026, 10, 13)
REVIEW = date(2027, 10, 13)
AFTER = REVIEW + timedelta(days=1)  # the first day the review may run
PAPER = PaperFile(
    name="crypto-v1-paper", strategy="data/strategy_crypto-v1.yaml", strategy_sha256="f" * 64,
    setup="breakout", start=START, review=ReviewRules(months=12, max_late_days=7, max_drawdown=0.35),
)
POSITION = {
    "symbol": "XBTUSD", "units": 0.000527, "entry_date": "2026-10-14",
    "entry_price": 62661.65, "stop": 58900.0,
}


def _setup(session: Session) -> tuple[PaperPortfolio, PaperRun]:
    run = PaperRun(
        started_at=datetime(2026, 10, 14, 0, 30, tzinfo=UTC), status="ok", git_sha="abc",
        finished_at=datetime(2026, 10, 14, 0, 31, tzinfo=UTC),
    )
    portfolio = PaperPortfolio(
        name=PAPER.name, paper_file="paper_crypto-v1.yaml", strategy_sha256="f" * 64,
        start=START,
    )
    session.add(run)
    session.add(portfolio)
    session.commit()
    session.refresh(run)
    session.refresh(portfolio)
    return portfolio, run


def _days(
    session: Session,
    portfolio: PaperPortfolio,
    run: PaperRun,
    count: int,
    equity: Callable[[int], float] = lambda _i: 100.0,
    late: set[int] | None = None,
    skip: set[int] | None = None,
) -> None:
    assert portfolio.id is not None and run.id is not None
    for i in range(count):
        if skip and i in skip:
            continue
        day = START + timedelta(days=i)
        on_time = close_deadline(day) - timedelta(hours=23)
        recorded = close_deadline(day) + timedelta(hours=1) if late and i in late else on_time
        session.add(
            PaperDay(
                portfolio_id=portfolio.id, day=day, equity=equity(i), cash=equity(i),
                state={"positions": [POSITION] if i == count - 1 else []},
                recorded_at=recorded, run_id=run.id,
            )
        )
    session.commit()


def test_close_deadline_is_24_hours_after_the_candle_closes() -> None:
    assert close_deadline(START) == datetime(2026, 10, 15, 0, 0, tzinfo=UTC)


def test_worst_drawdown() -> None:
    assert worst_drawdown([100.0, 120.0, 90.0, 130.0]) == approx(0.25)
    assert worst_drawdown([100.0]) == 0.0


def test_summary_of_a_few_days(session: Session) -> None:
    portfolio, run = _setup(session)
    _days(session, portfolio, run, 3, equity=lambda i: [100.0, 110.0, 99.0][i], late={1})
    assert portfolio.id is not None and run.id is not None
    session.add(
        PaperEvent(
            portfolio_id=portfolio.id, day=START, kind="exit", payload={"event": "exit"},
            recorded_at=run.started_at, run_id=run.id,
        )
    )
    session.add(
        PaperRun(
            started_at=datetime(2026, 10, 15, 6, 30, tzinfo=UTC), status="failed",
            git_sha="abc", error="KrakenError: down",
        )
    )
    session.commit()
    now = datetime(2026, 10, 16, 0, 40, tzinfo=UTC)
    summary = paper_summary(session, portfolio, PAPER, 100.0, now)
    assert summary.last_day == START + timedelta(days=2)
    assert summary.equity == 99.0
    assert summary.btc_equity is None  # no BTC prices stored
    assert summary.open_positions == ["XBTUSD 0.000527 since 2026-10-14 at 62,661.65, stop 58,900.00"]
    assert (summary.closed_trades, summary.late_days, summary.failed_runs_7d) == (1, 1, 1)
    assert summary.worst_drawdown == approx(0.1)
    assert summary.days_to_review == (REVIEW - now.date()).days
    assert summary.last_ok_run == datetime(2026, 10, 14, 0, 31, tzinfo=UTC)
    assert summary_text(PAPER, summary) == "\n".join(
        [
            "PAPER crypto-v1-paper: weekly summary to 2026-10-15",
            "Equity 99.00 (start 100.00); BTC buy-and-hold n/a",
            "Open: XBTUSD 0.000527 since 2026-10-14 at 62,661.65, stop 58,900.00",
            "Closed trades: 1",
            "Worst drawdown: 10.0% (limit 35%)",
            "Late days: 1 (limit 7)",
            "Failed runs in the last 7 days: 1",
            f"Review on 2027-10-13 ({(REVIEW - now.date()).days} days)",
        ]
    )


def test_summary_compares_with_btc_bought_at_the_start_close(session: Session) -> None:
    portfolio, run = _setup(session)
    _days(session, portfolio, run, 3)
    ticker = crypto_ticker(session, "XBTUSD")
    for day, close in ((START, "60000"), (START + timedelta(days=2), "66000")):
        price = Decimal(close)
        session.add(
            Price(
                ticker_id=ticker.id, date=day, open=price, high=price, low=price, close=price,
                adj_close=price, volume=1,
            )
        )
    session.commit()
    summary = paper_summary(session, portfolio, PAPER, 100.0, datetime(2026, 10, 16, tzinfo=UTC))
    assert summary.btc_equity == approx(110.0)


def test_summary_before_the_first_day(session: Session) -> None:
    portfolio, _run = _setup(session)
    summary = paper_summary(session, portfolio, PAPER, 100.0, datetime(2026, 10, 13, tzinfo=UTC))
    assert (summary.last_day, summary.equity, summary.open_positions) == (None, 100.0, [])
    assert summary_text(PAPER, summary).startswith("PAPER crypto-v1-paper: weekly summary\n")


def test_review_refuses_before_the_review_date(session: Session) -> None:
    portfolio, _run = _setup(session)
    with pytest.raises(PaperError, match="The review is on 2027-10-13"):
        paper_review(session, portfolio, PAPER, 100.0, REVIEW - timedelta(days=1))


def test_review_refuses_on_the_review_date_itself(session: Session) -> None:
    portfolio, _run = _setup(session)
    message = (
        "The review is on 2027-10-13; run it from 2027-10-14, once the last day is "
        "recorded (today is 2027-10-13)."
    )
    with pytest.raises(PaperError, match=re.escape(message)):
        paper_review(session, portfolio, PAPER, 100.0, REVIEW)


def _exit(portfolio: PaperPortfolio, run: PaperRun, r: float, day: date) -> PaperEvent:
    assert portfolio.id is not None and run.id is not None
    return PaperEvent(
        portfolio_id=portfolio.id, day=day, kind="exit",
        payload={"event": "exit", "symbol": "XBTUSD", "r": r},
        recorded_at=run.started_at, run_id=run.id,
    )


def _info_line(lines: list[str]) -> str:
    return next(line for line in lines if line.startswith("For information:"))


def test_review_reports_profit_trades_and_btc_for_information(session: Session) -> None:
    portfolio, run = _setup(session)
    _days(session, portfolio, run, 365, equity=lambda i: 100.0 + i * 10.0 / 364)
    session.add(_exit(portfolio, run, 2.0, START + timedelta(days=10)))
    session.add(_exit(portfolio, run, -1.0, START + timedelta(days=20)))
    session.add(_exit(portfolio, run, 5.0, REVIEW))  # outside the period: not counted
    ticker = crypto_ticker(session, "XBTUSD")
    for day, close in ((START, "60000"), (REVIEW - timedelta(days=1), "90000")):
        price = Decimal(close)
        session.add(
            Price(
                ticker_id=ticker.id, date=day, open=price, high=price, low=price, close=price,
                adj_close=price, volume=1,
            )
        )
    session.commit()
    review = paper_review(session, portfolio, PAPER, 100.0, AFTER)
    assert _info_line(review.lines) == (
        "For information: profit +10.0%, 2 closed trades, win rate 50%, mean R +0.50, "
        "BTC buy-and-hold 150.00"
    )
    assert review.lines[-1] in ("ON TRACK", "OFF TRACK")
    assert review.lines.index(_info_line(review.lines)) == len(review.lines) - 2


def test_review_information_line_with_no_trades(session: Session) -> None:
    portfolio, run = _setup(session)
    _days(session, portfolio, run, 365)
    review = paper_review(session, portfolio, PAPER, 100.0, AFTER)
    assert _info_line(review.lines) == (
        "For information: profit +0.0%, 0 closed trades, win rate n/a, mean R n/a, "
        "BTC buy-and-hold n/a"
    )


def test_review_on_track(session: Session) -> None:
    portfolio, run = _setup(session)
    _days(session, portfolio, run, 365, late=set(range(7)))
    review = paper_review(session, portfolio, PAPER, 100.0, AFTER)
    assert review.on_track
    assert review.lines[-1] == "ON TRACK"


@pytest.mark.parametrize(
    ("late", "skip", "equity", "failing"),
    [
        (set(range(8)), set(), lambda _i: 100.0, "1. On time: FAIL"),
        (set(), {100}, lambda _i: 100.0, "1. On time: FAIL"),
        (set(), set(), lambda i: 64.0 if i == 50 else 100.0, "3. Drawdown: FAIL"),
    ],
)
def test_review_off_track(
    session: Session,
    late: set[int],
    skip: set[int],
    equity: Callable[[int], float],
    failing: str,
) -> None:
    portfolio, run = _setup(session)
    _days(session, portfolio, run, 365, equity=equity, late=late, skip=skip)
    review = paper_review(session, portfolio, PAPER, 100.0, AFTER)
    assert not review.on_track
    assert any(line.startswith(failing) for line in review.lines)
    assert review.lines[-1] == "OFF TRACK"


def test_review_off_track_after_a_replay_mismatch(session: Session) -> None:
    portfolio, run = _setup(session)
    _days(session, portfolio, run, 365)
    session.add(
        PaperRun(
            started_at=datetime(2027, 3, 1, 0, 30, tzinfo=UTC), status="mismatch",
            git_sha="abc", error="replay mismatch on 2027-02-01: equity differs",
        )
    )
    session.commit()
    review = paper_review(session, portfolio, PAPER, 100.0, AFTER)
    assert not review.on_track
    assert any(line.startswith("2. Matches the backtest code: FAIL") for line in review.lines)
