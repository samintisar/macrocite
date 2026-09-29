"""`paper run` (spec 07) on synthetic prices that arrive one night at a time.

The pullback fixture: AAA signals on DAYS[251], fills at the DAYS[252] open, and exits for time
at the DAYS[262] open. The portfolios start on DAYS[240], the Monday after `paper start`.
"""

from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session, col, func, select

from paper_helpers import PAPER_FILE, evening, make_repo
from signalbench.backtest.runner import load_market_inputs
from signalbench.backtest.simulator import SimulationResult, simulate
from signalbench.db.models import (
    PaperEquity,
    PaperEvent,
    PaperPortfolio,
    PaperRun,
    Price,
    Ticker,
    TickerKind,
)
from signalbench.ingest.cdr import CdrEntry
from signalbench.market.bars import AdjustedBar
from signalbench.market.calendar import HISTORY_START
from signalbench.paper.run import RunOutcome, run_paper
from signalbench.paper.start import start_portfolios
from signalbench.strategy.config import load_strategy_config
from signalbench.strategy.market_view import MarketView
from signalbench.strategy.readings import NullReadingsView
from strategy_helpers import (
    WeekdaySessions,
    pullback_closes,
    series,
    trend_bars,
    weekdays,
)

DAYS = weekdays(date(2023, 1, 2), 280)
FIRST, LAST = 240, 270
UNIVERSE = [
    CdrEntry("AAA", "ZAAA", "ZAAA.NE", "Aaa", "Information Technology"),
    CdrEntry("BBB", "ZBBB", "ZBBB.NE", "Bbb", "Energy"),
]
BARS = {
    "AAA": series(DAYS, pullback_closes(len(DAYS))),
    "BBB": trend_bars(DAYS, 50.0, 0.1),
    "QQQ": trend_bars(DAYS, 300.0, 0.5),
}
KINDS = {"AAA": TickerKind.us_stock, "BBB": TickerKind.us_stock, "QQQ": TickerKind.benchmark}


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _price(ticker: Ticker, bar: AdjustedBar) -> Price:
    value = Decimal(str(round(bar.close, 4)))
    return Price(ticker_id=ticker.id, date=bar.date, open=Decimal(str(round(bar.open, 4))),
                 high=Decimal(str(round(bar.high, 4))), low=Decimal(str(round(bar.low, 4))),
                 close=value, adj_close=value, volume=bar.volume)


class Feed:
    """Stands in for `ingest prices`: stores every fixture bar dated on or before the clock's
    New York date that is not stored yet."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.calls = 0

    def __call__(self, session: Session) -> list[str]:
        self.calls += 1
        today = self.clock().date()
        for symbol, bars in BARS.items():
            ticker = session.exec(select(Ticker).where(Ticker.symbol == symbol)).one()
            last = session.exec(select(func.max(Price.date)).where(Price.ticker_id == ticker.id)).one()
            for bar in bars:
                if (last is None or bar.date > last) and bar.date <= today:
                    session.add(_price(ticker, bar))
        session.commit()
        return []


@pytest.fixture
def repo(session: Session, tmp_path: Path) -> Path:
    """Prices up to DAYS[FIRST - 1], and the three test portfolios started that evening."""
    for symbol, bars in BARS.items():
        ticker = Ticker(symbol=symbol, company_name=symbol, kind=KINDS[symbol])
        session.add(ticker)
        session.commit()
        session.refresh(ticker)
        session.add_all(_price(ticker, bar) for bar in bars[:FIRST])
        session.commit()
    root = make_repo(tmp_path / "repo")
    start_portfolios(session, paper_file=root / PAPER_FILE, repo=root, calendar=WeekdaySessions(),
                     now=evening(DAYS[FIRST - 1]))
    return root


def _run(
    session: Session, repo: Path, clock: Clock, ingest: Callable[[Session], list[str]] | None = None,
    held: bool = True,
) -> RunOutcome:
    return run_paper(
        session, lock=nullcontext(held), repo=repo, universe=UNIVERSE, calendar=WeekdaySessions(),
        clock=clock, ingest=ingest or Feed(clock), echo=lambda _line: None,
    )


def _portfolio(session: Session, name: str) -> PaperPortfolio:
    return session.exec(select(PaperPortfolio).where(PaperPortfolio.name == name)).one()


def _equity(session: Session, name: str) -> list[PaperEquity]:
    portfolio = _portfolio(session, name)
    return list(session.exec(
        select(PaperEquity).where(PaperEquity.portfolio_id == portfolio.id)
        .order_by(col(PaperEquity.session))
    ).all())


def _events(session: Session, name: str) -> list[PaperEvent]:
    portfolio = _portfolio(session, name)
    return list(session.exec(
        select(PaperEvent).where(PaperEvent.portfolio_id == portfolio.id).order_by(col(PaperEvent.id))
    ).all())


def test_a_run_steps_every_session_since_the_last_one_and_flags_catch_up(
    session: Session, repo: Path
) -> None:
    outcome = _run(session, repo, Clock(evening(DAYS[FIRST + 2])))
    assert (outcome.status, outcome.target) == ("ok", DAYS[FIRST + 2])
    assert outcome.stepped == {"p-plain": 3, "p-twin": 3, "p-qqq": 3}
    rows = _equity(session, "p-plain")
    assert [(r.session, r.catch_up) for r in rows] == [
        (DAYS[FIRST], True), (DAYS[FIRST + 1], True), (DAYS[FIRST + 2], False),
    ]
    assert _portfolio(session, "p-qqq").last_session == DAYS[FIRST + 2]
    [run] = session.exec(select(PaperRun)).all()
    assert (run.status, run.sessions_stepped, run.target_session, run.error) == (
        "ok", 9, DAYS[FIRST + 2], None,
    )
    assert run.finished_at is not None
    later = _run(session, repo, Clock(evening(DAYS[FIRST + 4])))
    assert later.stepped == {"p-plain": 2, "p-twin": 2, "p-qqq": 2}
    assert [r.catch_up for r in _equity(session, "p-plain")[3:]] == [True, False]


def test_a_second_run_the_same_night_steps_nothing(session: Session, repo: Path) -> None:
    clock = Clock(evening(DAYS[FIRST]))
    _run(session, repo, clock)
    events = len(session.exec(select(PaperEvent)).all())
    feed = Feed(clock)
    again = _run(session, repo, clock, feed)
    assert (again.status, again.stepped, feed.calls) == ("ok", {}, 0)
    assert len(session.exec(select(PaperEvent)).all()) == events
    assert len(_equity(session, "p-plain")) == 1
    assert [r.status for r in session.exec(select(PaperRun)).all()] == ["ok", "ok"]


def test_a_run_before_the_close_is_complete_targets_the_previous_session(
    session: Session, repo: Path
) -> None:
    outcome = _run(session, repo, Clock(evening(DAYS[FIRST + 1], hour=15)))
    assert (outcome.target, outcome.stepped["p-plain"]) == (DAYS[FIRST], 1)


def _simulated(session: Session, repo: Path, config_path: str) -> SimulationResult:
    config = load_strategy_config(repo / config_path)[0].with_setups(("pullback",))
    inputs = load_market_inputs(session, UNIVERSE, "QQQ", DAYS[LAST])
    calendar = WeekdaySessions()
    sessions = calendar.sessions_between(HISTORY_START, DAYS[LAST]) + calendar.next_sessions(DAYS[LAST], 3)
    market = MarketView(inputs.symbols, inputs.benchmark, sessions, config)
    return simulate(market, NullReadingsView(), config, DAYS[FIRST], DAYS[LAST])


def _without(payload: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in keys}


@pytest.fixture
def nightly(session: Session, repo: Path) -> Path:
    """One run every evening from DAYS[FIRST] to DAYS[LAST]."""
    for i in range(FIRST, LAST + 1):
        assert _run(session, repo, Clock(evening(DAYS[i]))).status == "ok"
    return repo


@pytest.mark.parametrize(
    ("name", "config_path"),
    [("p-plain", "data/strategy_test.yaml"), ("p-qqq", "data/strategy_test-qqq.yaml")],
)
def test_nightly_runs_store_exactly_what_one_simulate_gives(
    session: Session, nightly: Path, name: str, config_path: str
) -> None:
    whole = _simulated(session, nightly, config_path)
    assert [(r.session, r.equity, r.cash, r.vehicle_value, r.open_positions)
            for r in _equity(session, name)] == [
        (p.date, p.equity, p.cash, p.vehicle_value, p.open_positions) for p in whole.equity_curve
    ]
    stored = [e for e in _events(session, name) if not e.kind.startswith("order_")]
    kinds = {"exit": "fill_exit", "entry": "fill_entry"}
    assert [(e.session.isoformat(), e.kind, _without(e.payload, "order_event_id", "trade"))
            for e in stored] == [
        (str(e["date"]), kinds.get(str(e["event"]), str(e["event"])), _without(e, "date", "event"))
        for e in whole.events
    ]
    [trade] = whole.trades
    [fill] = [e for e in stored if e.kind == "fill_exit"]
    expected = {k: v.isoformat() if isinstance(v, date) else v for k, v in asdict(trade).items()}
    assert fill.payload["trade"] == expected
    assert not any(e.catch_up for e in _events(session, name))


def test_the_twin_portfolios_match_every_night(session: Session, nightly: Path) -> None:
    def rows(name: str) -> list[tuple[date, str, dict[str, Any]]]:
        return [(e.session, e.kind, _without(e.payload, "order_event_id")) for e in _events(session, name)]

    assert rows("p-plain") == rows("p-twin")
    assert len(rows("p-plain")) > 10


def test_order_events_are_recorded_the_night_before_their_fills(
    session: Session, nightly: Path
) -> None:
    fills = [e for e in _events(session, "p-qqq") if e.kind in ("fill_exit", "fill_entry")]
    assert [e.kind for e in fills] == ["fill_entry", "fill_exit"]
    for fill in fills:
        order = session.get(PaperEvent, fill.payload["order_event_id"])
        assert order is not None and order.id is not None and fill.id is not None
        assert order.kind == fill.kind.replace("fill_", "order_")
        assert order.payload["symbol"] == fill.payload["symbol"] == "AAA"
        assert (order.id < fill.id, order.session < fill.session) == (True, True)
        assert order.recorded_at < fill.recorded_at
        assert order.recorded_at < evening(fill.session, hour=9)  # before the fill's open


def test_a_changed_config_fails_that_portfolio_and_the_others_still_step(
    session: Session, repo: Path
) -> None:
    with (repo / "data" / "strategy_test-qqq.yaml").open("a", encoding="utf-8") as handle:
        handle.write("# edited\n")
    outcome = _run(session, repo, Clock(evening(DAYS[FIRST])))
    assert outcome.status == "failed"
    assert outcome.stepped == {"p-plain": 1, "p-twin": 1}
    assert outcome.error is not None
    assert outcome.error.startswith(
        "PaperRefusedError: p-qqq: data/strategy_test-qqq.yaml no longer matches the config_sha256"
    )
    assert _portfolio(session, "p-qqq").last_session is None
    [run] = session.exec(select(PaperRun)).all()
    assert (run.status, run.error, run.sessions_stepped) == ("failed", outcome.error, 2)


def test_the_lock_refuses_a_concurrent_run(session: Session, repo: Path) -> None:
    clock = Clock(evening(DAYS[FIRST]))
    feed = Feed(clock)
    outcome = _run(session, repo, clock, feed, held=False)
    assert (outcome.status, outcome.run_id, feed.calls) == ("locked", None, 0)
    assert session.exec(select(PaperRun)).all() == []


def test_an_error_fails_the_run_and_is_recorded(session: Session, repo: Path) -> None:
    def broken(_session: Session) -> list[str]:
        raise RuntimeError("price feed down")

    outcome = _run(session, repo, Clock(evening(DAYS[FIRST])), broken)
    assert (outcome.status, outcome.error, outcome.stepped) == (
        "failed", "RuntimeError: price feed down", {},
    )
    [run] = session.exec(select(PaperRun)).all()
    assert (run.status, run.error, run.target_session) == (
        "failed", "RuntimeError: price feed down", DAYS[FIRST],
    )
    assert run.finished_at is not None


def test_prices_that_stop_before_the_target_fail_the_run(session: Session, repo: Path) -> None:
    outcome = _run(session, repo, Clock(evening(DAYS[FIRST])), lambda _session: [])
    assert outcome.status == "failed"
    assert outcome.error is not None and "do not end on the run's last session" in outcome.error
    assert _equity(session, "p-plain") == []


def test_a_run_without_portfolios_fails(session: Session) -> None:
    outcome = _run(session, Path("."), Clock(evening(DAYS[FIRST])))
    assert (outcome.status, outcome.error) == (
        "failed", "PaperRefusedError: No paper portfolios. Run `signalbench paper start` first.",
    )
