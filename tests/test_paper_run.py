"""`paper run` (spec 07) on synthetic prices that arrive one night at a time.

The pullback fixture: AAA signals on DAYS[251], fills at the DAYS[252] open, and exits for time
at the DAYS[262] open. The portfolios start on DAYS[240], the Monday after `paper start`.
"""

from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import asdict, replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session, col, func, select

from paper_helpers import PAPER_FILE, evening, git, make_repo
from signalbench.backtest.runner import load_market_inputs
from signalbench.backtest.simulator import SimulationResult, simulate
from signalbench.db.models import (
    EarningsEvent,
    PaperEquity,
    PaperEvent,
    PaperPortfolio,
    PaperRun,
    Price,
    Ticker,
    TickerKind,
)
from signalbench.ingest.cdr import CdrEntry
from signalbench.ingest.earnings import FINNHUB_EARNINGS_SOURCE, SEC_EARNINGS_SOURCE
from signalbench.ingest.prices import Split
from signalbench.market.bars import AdjustedBar
from signalbench.market.calendar import HISTORY_START
from signalbench.paper.run import RunOutcome, run_paper
from signalbench.paper.splits import SplitFetcher
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

    def __init__(self, clock: Clock, bars: dict[str, list[AdjustedBar]] = BARS) -> None:
        self.clock = clock
        self.bars = bars
        self.calls = 0

    def __call__(self, session: Session) -> list[str]:
        self.calls += 1
        today = self.clock().date()
        for symbol, bars in self.bars.items():
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


def _no_splits(_symbol: str, _since: date) -> list[Split]:
    return []


def _run(
    session: Session, repo: Path, clock: Clock, ingest: Callable[[Session], list[str]] | None = None,
    held: bool = True, *, splits: SplitFetcher = _no_splits,
    earnings: Callable[[Session], list[str]] = lambda _session: [],
    echo: Callable[[str], None] = lambda _line: None,
) -> RunOutcome:
    return run_paper(
        session, lock=nullcontext(held), repo=repo, universe=UNIVERSE, calendar=WeekdaySessions(),
        clock=clock, ingest=ingest or Feed(clock), ingest_earnings=earnings, splits=splits,
        reports_dir=repo / "reports" / "paper", echo=echo,
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


def _edit_config(repo: Path) -> None:
    """A committed edit to the QQQ config: the working tree is clean, the sha has changed."""
    with (repo / "data" / "strategy_test-qqq.yaml").open("a", encoding="utf-8") as handle:
        handle.write("# edited\n")
    git(repo, "commit", "-q", "-am", "edit a config")


def test_a_changed_config_fails_that_portfolio_and_the_others_still_step(
    session: Session, repo: Path
) -> None:
    _edit_config(repo)
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


def _commit_src(repo: Path, text: str) -> str:
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "rules.py").write_text(text, encoding="utf-8")
    git(repo, "add", "src")
    git(repo, "commit", "-q", "-m", "code")
    return git(repo, "rev-parse", "HEAD")


def test_each_run_records_the_commit_it_ran_and_a_new_commit_does_not_stop_it(
    session: Session, repo: Path
) -> None:
    first = git(repo, "rev-parse", "HEAD")
    assert _run(session, repo, Clock(evening(DAYS[FIRST]))).status == "ok"
    second = _commit_src(repo, "FIXED = True\n")  # a bug fix between two nights
    assert _run(session, repo, Clock(evening(DAYS[FIRST + 1]))).status == "ok"
    runs = session.exec(select(PaperRun).order_by(col(PaperRun.id))).all()
    assert [(r.status, r.git_sha, r.git_dirty) for r in runs] == [
        ("ok", first, False), ("ok", second, False),
    ]


def test_uncommitted_code_changes_refuse_the_run(session: Session, repo: Path) -> None:
    head = _commit_src(repo, "LIMIT = 1\n")
    (repo / "src" / "rules.py").write_text("LIMIT = 2\n", encoding="utf-8")
    (repo / "reports").mkdir()
    (repo / "reports" / "notes.md").write_text("not code\n", encoding="utf-8")
    clock = Clock(evening(DAYS[FIRST]))
    feed = Feed(clock)
    outcome = _run(session, repo, clock, feed)
    assert (outcome.status, outcome.stepped, feed.calls) == ("failed", {}, 0)
    assert outcome.error == (
        "PaperRefusedError: uncommitted changes to tracked code or data (src/rules.py); "
        "commit them or check out a clean tag, then rerun"
    )
    [run] = session.exec(select(PaperRun)).all()
    assert (run.status, run.error, run.git_sha, run.git_dirty) == (
        "failed", outcome.error, head, True,
    )
    assert _equity(session, "p-plain") == []


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


def test_a_run_without_portfolios_fails(session: Session, tmp_path: Path) -> None:
    outcome = _run(session, tmp_path, Clock(evening(DAYS[FIRST])))
    assert (outcome.status, outcome.error) == (
        "failed", "PaperRefusedError: No paper portfolios. Run `signalbench paper start` first.",
    )


def test_the_first_run_of_each_iso_week_writes_the_weekly_report(
    session: Session, repo: Path
) -> None:
    reports = repo / "reports" / "paper"
    monday = _run(session, repo, Clock(evening(DAYS[FIRST])))
    assert monday.report == reports / f"{DAYS[FIRST].isoformat()}-weekly.md"
    assert "| p-qqq | 2023-12-04 | 2023-12-04 | 1 |" in monday.report.read_text(encoding="utf-8")
    for i in range(FIRST + 1, FIRST + 5):  # Tuesday to Friday: no new report
        assert _run(session, repo, Clock(evening(DAYS[i]))).report is None
    next_monday = _run(session, repo, Clock(evening(DAYS[FIRST + 5])))
    assert next_monday.report == reports / f"{DAYS[FIRST + 5].isoformat()}-weekly.md"
    assert sorted(path.name for path in reports.iterdir()) == [
        "2023-12-04-weekly.md", "2023-12-11-weekly.md",
    ]


def test_a_refused_portfolio_does_not_stop_the_weekly_report(session: Session, repo: Path) -> None:
    _edit_config(repo)
    outcome = _run(session, repo, Clock(evening(DAYS[FIRST])))
    assert outcome.status == "failed"
    assert outcome.report is not None
    assert "| p-qqq | 2023-12-04 | not stepped yet | 0 |" in outcome.report.read_text(encoding="utf-8")


def _split_bars(bars: list[AdjustedBar], ratio: float) -> list[AdjustedBar]:
    """The history a price source serves after a `ratio`-for-1 split: every price divided by
    the ratio and every volume multiplied by it, on every date."""
    return [
        replace(bar, open=bar.open / ratio, high=bar.high / ratio, low=bar.low / ratio,
                close=bar.close / ratio, volume=round(bar.volume * ratio))
        for bar in bars
    ]


def _split_stored(session: Session, symbol: str, ratio: int) -> None:
    """What tonight's `ingest prices` does after a split: it sees older closes change and
    refetches the whole, rescaled history."""
    ticker = session.exec(select(Ticker).where(Ticker.symbol == symbol)).one()
    for row in session.exec(select(Price).where(Price.ticker_id == ticker.id)).all():
        row.open, row.high, row.low = row.open / ratio, row.high / ratio, row.low / ratio
        row.close, row.adj_close = row.close / ratio, row.adj_close / ratio
        row.volume *= ratio
        session.add(row)
    session.commit()


SPLIT_BARS = {**BARS, "AAA": _split_bars(BARS["AAA"], 2.0)}


def _through_split(session: Session, repo: Path, ex_date: date) -> SplitFetcher:
    """Nightly runs over DAYS[FIRST..LAST], with AAA splitting 2-for-1 on `ex_date`: yfinance
    lists the split from the start, and the stored history is rescaled on its ex-date night."""
    def splits(symbol: str, since: date) -> list[Split]:
        return [Split(ex_date, 2.0)] if symbol == "AAA" and ex_date > since else []

    for i in range(FIRST, LAST + 1):
        clock = Clock(evening(DAYS[i]))
        if DAYS[i] == ex_date:
            _split_stored(session, "AAA", 2)
        feed = Feed(clock, SPLIT_BARS if DAYS[i] >= ex_date else BARS)
        assert _run(session, repo, clock, feed, splits=splits).status == "ok"
    return splits


@pytest.mark.parametrize(
    ("name", "config_path"),
    [("p-plain", "data/strategy_test.yaml"), ("p-qqq", "data/strategy_test-qqq.yaml")],
)
def test_a_held_position_keeps_its_value_through_a_2_for_1_split(
    session: Session, repo: Path, name: str, config_path: str
) -> None:
    ex_date = DAYS[256]  # AAA is held from DAYS[252] to DAYS[262]
    _through_split(session, repo, ex_date)
    whole = _simulated(session, repo, config_path)  # the backtest on the rescaled history
    assert [(r.session, r.open_positions) for r in _equity(session, name)] == [
        (p.date, p.open_positions) for p in whole.equity_curve
    ]
    assert [r.equity for r in _equity(session, name)] == pytest.approx(
        [p.equity for p in whole.equity_curve], rel=1e-9
    )
    [trade] = whole.trades
    [fill] = [e for e in _events(session, name) if e.kind == "fill_exit"]
    assert (fill.session, fill.payload["reason"]) == (DAYS[262], "time")  # not the stop
    assert fill.payload["r"] == pytest.approx(trade.r, rel=1e-9)
    assert fill.payload["trade"]["units"] == pytest.approx(trade.units, rel=1e-9)
    adjusts = [e for e in _events(session, name) if e.kind == "split_adjust"]
    assert [(e.session, e.payload) for e in adjusts] == [
        (ex_date, {"symbol": "AAA", "ex_date": ex_date.isoformat(), "ratio": 2.0}),
    ]


def test_a_pending_entry_is_rescaled_across_a_split_on_its_fill_day(
    session: Session, repo: Path
) -> None:
    ex_date = DAYS[252]  # the order is decided at the DAYS[251] close, before the split
    _through_split(session, repo, ex_date)
    [order] = [e for e in _events(session, "p-plain") if e.kind == "order_entry"]
    [fill] = [e for e in _events(session, "p-plain") if e.kind == "fill_entry"]
    assert fill.session == ex_date
    assert fill.payload["units"] == pytest.approx(2 * order.payload["units"], rel=1e-9)
    whole = _simulated(session, repo, "data/strategy_test.yaml")
    assert [r.equity for r in _equity(session, "p-plain")] == pytest.approx(
        [p.equity for p in whole.equity_curve], rel=1e-9
    )
    assert [e.kind for e in _events(session, "p-plain") if e.session == ex_date][:2] == [
        "split_adjust", "fill_entry",
    ]


def test_stored_prices_rescaled_without_a_known_split_fail_the_run(
    session: Session, repo: Path
) -> None:
    for i in range(FIRST, 256):
        assert _run(session, repo, Clock(evening(DAYS[i]))).status == "ok"
    _split_stored(session, "AAA", 2)
    clock = Clock(evening(DAYS[256]))
    outcome = _run(session, repo, clock, Feed(clock, SPLIT_BARS))
    assert outcome.status == "failed"
    assert outcome.error is not None
    assert outcome.error.startswith("PaperRefusedError: p-plain: AAA")
    assert _portfolio(session, "p-plain").last_session == DAYS[255]


def test_a_failed_split_lookup_warns_and_the_prices_decide(session: Session, repo: Path) -> None:
    def down(_symbol: str, _since: date) -> list[Split]:
        raise RuntimeError("yfinance down")

    for i in range(FIRST, 256):
        assert _run(session, repo, Clock(evening(DAYS[i])), splits=down).status == "ok"
    lines: list[str] = []
    clock = Clock(evening(DAYS[256]))
    assert _run(session, repo, clock, splits=down, echo=lines.append).status == "ok"
    assert "splits AAA: RuntimeError: yfinance down; checking the stored prices only" in lines


def _event(session: Session, symbol: str, day: date, source: str) -> None:
    ticker = session.exec(select(Ticker).where(Ticker.symbol == symbol)).one()
    session.add(EarningsEvent(ticker_id=ticker.id, event_date=day, source=source))
    session.commit()


def test_the_backtest_loader_ignores_the_calendar_and_paper_reads_it(
    session: Session, repo: Path
) -> None:
    _event(session, "AAA", DAYS[100], SEC_EARNINGS_SOURCE)
    _event(session, "AAA", DAYS[253], FINNHUB_EARNINGS_SOURCE)  # upcoming, from the calendar
    backtest = load_market_inputs(session, UNIVERSE, "QQQ", DAYS[FIRST - 1])
    paper = load_market_inputs(session, UNIVERSE, "QQQ", DAYS[FIRST - 1], calendar_earnings=True)
    assert [(i.symbol, list(i.earnings)) for i in backtest.symbols] == [
        ("AAA", [DAYS[100]]), ("BBB", []),
    ]
    assert [(i.symbol, list(i.earnings)) for i in paper.symbols] == [
        ("AAA", [DAYS[100], DAYS[253]]), ("BBB", []),
    ]


class Calendar:
    """Stands in for the nightly Finnhub calendar ingest: from the night of `known_from`, AAA
    reports on `report_day`."""

    def __init__(self, clock: Clock, known_from: date, report_day: date) -> None:
        self.clock, self.known_from, self.report_day = clock, known_from, report_day
        self.calls = 0

    def __call__(self, session: Session) -> list[str]:
        self.calls += 1
        stored = session.exec(select(EarningsEvent)).all()
        if self.clock().date() >= self.known_from and not stored:
            _event(session, "AAA", self.report_day, FINNHUB_EARNINGS_SOURCE)
        return []


def test_an_upcoming_calendar_date_blocks_an_entry_in_paper(session: Session, repo: Path) -> None:
    for i in range(FIRST, 253):  # AAA signals at the DAYS[251] close; it reports on DAYS[253]
        clock = Clock(evening(DAYS[i]))
        calendar = Calendar(clock, known_from=DAYS[FIRST], report_day=DAYS[253])
        assert _run(session, repo, clock, earnings=calendar).status == "ok"
        assert calendar.calls == 1
    events = [(e.session, e.kind, e.payload.get("reason")) for e in _events(session, "p-plain")]
    assert (DAYS[251], "skip", "earnings_blackout") in events
    assert not [e for e in events if e[1] in ("order_entry", "fill_entry")]


def test_a_calendar_date_counts_from_the_night_it_is_stored(session: Session, repo: Path) -> None:
    """Stored the night after the entry was decided: the entry stands (past sessions are never
    re-decided), and the earnings exit fires at that night's close."""
    for i in range(FIRST, LAST + 1):
        clock = Clock(evening(DAYS[i]))
        calendar = Calendar(clock, known_from=DAYS[252], report_day=DAYS[254])
        assert _run(session, repo, clock, earnings=calendar).status == "ok"
    events = _events(session, "p-plain")
    assert [(e.session, e.kind) for e in events if e.kind in ("order_entry", "fill_entry")][:2] == [
        (DAYS[251], "order_entry"), (DAYS[252], "fill_entry"),
    ]
    fill = next(e for e in events if e.kind == "fill_exit")
    assert (fill.session, fill.payload["reason"]) == (DAYS[253], "earnings")


def test_an_earnings_calendar_failure_warns_and_the_run_goes_on(
    session: Session, repo: Path
) -> None:
    def down(_session: Session) -> list[str]:
        raise RuntimeError("finnhub down")

    lines: list[str] = []
    outcome = _run(session, repo, Clock(evening(DAYS[FIRST])), earnings=down, echo=lines.append)
    assert (outcome.status, outcome.stepped["p-plain"]) == ("ok", 1)
    assert ("earnings calendar FAILED (RuntimeError: finnhub down); "
            "stepping on the stored earnings dates") in lines
    lines.clear()
    later = _run(session, repo, Clock(evening(DAYS[FIRST + 1])), earnings=lambda _s: ["AAA"],
                 echo=lines.append)
    assert later.status == "ok"
    assert "earnings calendar: 1 failed (AAA); stepping on the stored earnings dates" in lines
