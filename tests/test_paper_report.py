"""The weekly paper report (spec 07), rendered from a hand-built portfolio."""

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from sqlmodel import Session

from signalbench.db.models import (
    PaperEquity,
    PaperEvent,
    PaperPortfolio,
    PaperRun,
    Price,
    Ticker,
    TickerKind,
)
from signalbench.paper.report import previous_week, report_due, write_weekly_report

WEEK = [date(2026, 10, 12 + i) for i in range(5)]  # Monday to Friday
MONDAY_AFTER = date(2026, 10, 19)
EQUITY = [100.0, 101.0, 99.0, 102.0, 103.0]
QQQ_CLOSES = [500.0, 505.0, 495.0, 510.0, 515.0]  # the same moves as EQUITY: the same Sharpe
QQQ_RAW_CLOSES = [510.0, 515.0, 505.0, 520.0, 525.0]  # before the dividend adjustment
WARNINGS = {14: "earnings calendar: 1 failed (AAA)", 16: "earnings calendar FAILED (RuntimeError: "
            "finnhub down)", 20: "prices: 1 failed (BBB)"}
EXPECTED = """\
# Paper trading: weekly report, 2026-10-19

Information only. A portfolio is judged once it has 12 months since its start and at least 30 \
closed trades, whichever is later (spec 07, Judging); until then these numbers decide nothing.

Failed runs from 2026-10-12 to 2026-10-18: 1.

Code used from 2026-10-12 to 2026-10-18: `aaaaaaaaaaaa`, `bbbbbbbbbbbb`. CHANGED: the last run \
before used `cccccccccccc`.

Run warnings from 2026-10-12 to 2026-10-18: 2.

- 2026-10-14: earnings calendar: 1 failed (AAA)
- 2026-10-16: earnings calendar FAILED (RuntimeError: finnhub down)

## Equity since the start

Paper portfolios earn the price return only after entry (no dividends); compare with the \
price-only QQQ line.

| Portfolio | Start | Last session | Sessions | Equity | Total return | Sharpe | Max drawdown \
| QQQ total return | QQQ price-only return | QQQ Sharpe | QQQ max drawdown |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| demo-qqq | 2026-10-12 | 2026-10-16 | 5 | 103.00 | +3.00% | 5.83 | 2.0% | +3.00% | +2.94% | 5.83 \
| 2.0% |
| demo-cash | 2026-10-12 | not stepped yet | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |

## Trades since the start

| Portfolio | Closed trades | Mean R | Win rate | Average hold (sessions) | Open positions \
| Average share in QQQ | Catch-up sessions last week |
| --- | --- | --- | --- | --- | --- | --- | --- |
| demo-qqq | 2 | +0.125 | 50.0% | 5.0 | 1 | 50.0% | 2 |
| demo-cash | 0 | n/a | n/a | n/a | 0 | n/a | 0 |
"""


def _portfolio(session: Session, name: str) -> int:
    portfolio = PaperPortfolio(
        name=name, config_path="data/strategy_test.yaml", config_sha256="a" * 64,
        setup="breakout", started_on=WEEK[0], state={},
    )
    session.add(portfolio)
    session.commit()
    session.refresh(portfolio)
    assert portfolio.id is not None
    return portfolio.id


def _fixture(session: Session) -> None:
    demo = _portfolio(session, "demo-qqq")
    _portfolio(session, "demo-cash")
    for i, (day, equity) in enumerate(zip(WEEK, EQUITY, strict=True)):
        session.add(
            PaperEquity(portfolio_id=demo, session=day, equity=equity, cash=0.0,
                        vehicle_value=equity / 2, open_positions=1 if i == 4 else 0,
                        catch_up=i in (1, 2))
        )
    for day, r, held in ((WEEK[2], 0.5, 4), (WEEK[4], -0.25, 6)):
        session.add(
            PaperEvent(portfolio_id=demo, session=day, kind="fill_exit",
                       payload={"trade": {"r": r, "sessions_held": held}},
                       recorded_at=datetime(2026, 10, 20, tzinfo=UTC), catch_up=False)
        )
    demo_row = session.get(PaperPortfolio, demo)
    assert demo_row is not None
    demo_row.last_session = WEEK[-1]
    session.add(demo_row)
    for day, status, sha in ((5, "ok", "c"), (14, "failed", "a"), (20, "failed", "d"),
                             (15, "ok", "b"), (16, "ok", "a")):
        session.add(PaperRun(started_at=datetime(2026, 10, day, 22, tzinfo=UTC), status=status,
                             git_sha=sha * 40, git_dirty=False, warnings=WARNINGS.get(day)))
    qqq = Ticker(symbol="QQQ", company_name="QQQ", kind=TickerKind.benchmark)
    session.add(qqq)
    session.commit()
    session.refresh(qqq)
    for day, close, raw in zip(WEEK, QQQ_CLOSES, QQQ_RAW_CLOSES, strict=True):
        value = Decimal(str(raw))
        session.add(Price(ticker_id=qqq.id, date=day, open=value, high=value, low=value,
                          close=value, adj_close=Decimal(str(close)), volume=1_000_000))
    session.commit()


def test_the_weekly_report_on_a_hand_built_portfolio(session: Session, tmp_path: Path) -> None:
    _fixture(session)
    path = write_weekly_report(session, tmp_path / "paper", MONDAY_AFTER)
    assert path == tmp_path / "paper" / "2026-10-19-weekly.md"
    assert path.read_text(encoding="utf-8") == EXPECTED


def test_the_report_covers_the_iso_week_before_the_one_it_is_written_in() -> None:
    assert previous_week(date(2026, 10, 19)) == (date(2026, 10, 12), date(2026, 10, 18))
    assert previous_week(date(2026, 10, 25)) == (date(2026, 10, 12), date(2026, 10, 18))


def test_a_report_is_due_once_per_iso_week(tmp_path: Path) -> None:
    reports = tmp_path / "paper"
    assert report_due(reports, date(2026, 10, 21))  # no folder yet
    reports.mkdir()
    (reports / "2026-10-12-weekly.md").write_text("last week\n", encoding="utf-8")
    (reports / "notes.md").write_text("not a report\n", encoding="utf-8")
    assert report_due(reports, date(2026, 10, 21))
    (reports / "2026-10-20-weekly.md").write_text("this week\n", encoding="utf-8")
    assert not report_due(reports, date(2026, 10, 21))
    assert not report_due(reports, date(2026, 10, 25))  # Sunday, same ISO week
    assert report_due(reports, date(2026, 10, 26))


def test_the_code_line_says_when_one_commit_ran_all_week(session: Session, tmp_path: Path) -> None:
    for day in (9, 13, 14):
        session.add(PaperRun(started_at=datetime(2026, 10, day, 22, tzinfo=UTC), status="ok",
                             git_sha="e" * 40, git_dirty=False))
    session.commit()
    text = write_weekly_report(session, tmp_path, MONDAY_AFTER).read_text(encoding="utf-8")
    assert "Code used from 2026-10-12 to 2026-10-18: `eeeeeeeeeeee`.\n" in text
    assert "CHANGED" not in text
