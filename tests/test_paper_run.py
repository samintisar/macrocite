import json
from collections.abc import Callable
from contextlib import nullcontext
from datetime import UTC, date, datetime, time
from pathlib import Path

import pytest
from sqlmodel import Session, col, select

from paper_helpers import (
    BREAKOUT,
    CRASH,
    DAYS,
    REPO,
    START,
    Fetch,
    all_bars,
    config,
    fake_fetch,
    night,
    write_paper_file,
)
from signalbench.backtest.crypto_runner import crypto_market
from signalbench.backtest.simulator import simulate
from signalbench.db.models import PaperDay, PaperEvent, PaperPortfolio, PaperRun, Price
from signalbench.ingest.kraken import KrakenError
from signalbench.ingest.prices import DailyBar
from signalbench.live.messenger import FakeMessenger
from signalbench.market.calendar import DailySessions
from signalbench.paper.paper_file import PaperError
from signalbench.paper.replay import replay
from signalbench.paper.run import PaperOutcome, run_paper, start_paper
from signalbench.paper.store import load_saved, row_values
from signalbench.strategy.readings import NullReadingsView

SUMMARY = "PAPER crypto-test-paper: weekly summary"


def _yes(_path: Path) -> bool:
    return True


def _start(session: Session, paper: Path) -> PaperPortfolio:
    return start_paper(
        session, repo=REPO, paper_path=paper,
        now=datetime.combine(DAYS[START], time(12, 0), tzinfo=UTC),
        fetch=fake_fetch(all_bars()), committed=_yes,
    )


def _run(
    session: Session,
    paper: Path,
    day: date,
    *,
    messenger: FakeMessenger | None = None,
    fetch: Fetch | None = None,
    committed: Callable[[Path], bool] = _yes,
    held: bool = True,
    echo: Callable[[str], None] = lambda _text: None,
) -> PaperOutcome:
    return run_paper(
        session, lock=nullcontext(held), repo=REPO, paper_path=paper,
        clock=lambda: night(day), fetch=fetch or fake_fetch(all_bars()), committed=committed,
        git_sha="abc123", messenger=messenger or FakeMessenger(), echo=echo,
    )


@pytest.fixture
def paper(session: Session, tmp_path: Path) -> Path:
    path = write_paper_file(tmp_path)
    _start(session, path)
    return path


def test_start_creates_the_portfolio_and_loads_the_history(
    session: Session, paper: Path
) -> None:
    portfolio = session.exec(select(PaperPortfolio)).one()
    assert (portfolio.name, portfolio.start, portfolio.paper_file) == (
        "crypto-test-paper", DAYS[START], "paper_crypto-test.yaml",
    )
    assert max(price.date for price in session.exec(select(Price)).all()) == DAYS[START - 1]


def test_start_refuses_a_second_portfolio(session: Session, paper: Path) -> None:
    with pytest.raises(PaperError, match="already exists"):
        _start(session, paper)


def test_start_refuses_a_start_day_that_has_closed(session: Session, tmp_path: Path) -> None:
    with pytest.raises(PaperError, match="has already closed"):
        start_paper(
            session, repo=REPO, paper_path=write_paper_file(tmp_path), now=night(DAYS[START]),
            fetch=fake_fetch(all_bars()), committed=_yes,
        )


def test_start_refuses_an_uncommitted_paper_file(session: Session, tmp_path: Path) -> None:
    with pytest.raises(PaperError, match="must be committed"):
        start_paper(
            session, repo=REPO, paper_path=write_paper_file(tmp_path),
            now=datetime.combine(DAYS[START], time(12, 0), tzinfo=UTC),
            fetch=fake_fetch(all_bars()), committed=lambda _path: False,
        )


def test_start_refuses_a_changed_strategy(session: Session, tmp_path: Path) -> None:
    with pytest.raises(PaperError, match="the strategy changed"):
        _start(session, write_paper_file(tmp_path, sha="0" * 64))


def test_nightly_runs_store_exactly_what_one_backtest_gives(session: Session, paper: Path) -> None:
    for day in DAYS[START:]:
        assert _run(session, paper, day).status == "ok"
    last = DAYS[-1]
    market = crypto_market(all_bars(), config(), DailySessions(), DAYS[START], last)
    result = simulate(market, NullReadingsView(), config(), DAYS[START], last)
    days = session.exec(select(PaperDay).order_by(col(PaperDay.day))).all()
    assert [(d.day, d.equity, d.cash) for d in days] == [
        (p.date, p.equity, p.cash) for p in result.equity_curve
    ]
    events = session.exec(select(PaperEvent).order_by(col(PaperEvent.id))).all()
    simulated = [e.payload for e in events if not e.kind.startswith("order_")]
    assert simulated == json.loads(json.dumps(result.events))
    assert [e.payload["symbol"] for e in events if e.kind == "exit"] == ["XBTUSD"]
    # every saved day (day, equity, cash, state, events) is exactly what one replay gives
    portfolio = session.exec(select(PaperPortfolio)).one()
    assert portfolio.id is not None
    replayed = replay(market, config(), DAYS[START], last)
    assert load_saved(session, portfolio.id) == [row_values(d) for d in replayed]


def test_the_trade_is_messaged_as_order_fill_and_close(session: Session, paper: Path) -> None:
    messenger = FakeMessenger()
    for day in DAYS[START:]:
        _run(session, paper, day, messenger=messenger)
    texts = messenger.texts()
    assert any(
        t.startswith("PAPER crypto-test: BUY XBTUSD at the next open (breakout). Signal close 110.00")
        for t in texts
    )
    assert any(t.startswith("PAPER filled: BUY XBTUSD") and "(open 110.00 + 0.4%)" in t for t in texts)
    assert any(t.startswith("PAPER crypto-test: SELL XBTUSD at the next open (stop)") for t in texts)
    assert any(t.startswith("PAPER closed: XBTUSD") for t in texts)


def test_a_second_run_the_same_night_adds_nothing_and_sends_nothing(
    session: Session, paper: Path
) -> None:
    messenger = FakeMessenger()
    assert _run(session, paper, DAYS[START], messenger=messenger).days_added == 1
    sent = len(messenger.sent)
    again = _run(session, paper, DAYS[START], messenger=messenger)
    assert (again.status, again.days_added) == ("ok", 0)
    assert len(messenger.sent) == sent


def test_before_the_start_day_closes_nothing_is_added(session: Session, paper: Path) -> None:
    outcome = _run(session, paper, DAYS[START - 1])
    assert (outcome.status, outcome.days_added) == ("ok", 0)


def test_an_edited_saved_row_is_a_replay_mismatch(session: Session, paper: Path) -> None:
    for day in DAYS[START : START + 3]:
        _run(session, paper, day)
    row = session.exec(select(PaperDay).order_by(col(PaperDay.day))).first()
    assert row is not None
    row.equity += 0.01
    session.add(row)
    session.commit()
    messenger = FakeMessenger()
    outcome = _run(session, paper, DAYS[START + 3], messenger=messenger)
    assert outcome.status == "mismatch"
    assert outcome.error is not None
    assert outcome.error.startswith(f"replay mismatch on {DAYS[START].isoformat()}: equity differs")
    assert messenger.texts() == [f"PAPER ALERT: {outcome.error}"]
    assert len(session.exec(select(PaperDay)).all()) == 3
    assert _run(session, paper, DAYS[START + 4]).status == "mismatch"
    run = session.get(PaperRun, outcome.run_id)
    assert run is not None and (run.status, run.days_added) == ("mismatch", 0)


def test_a_changed_strategy_fails_the_run(session: Session, paper: Path) -> None:
    write_paper_file(paper.parent, sha="0" * 64)  # the same path, now wrong
    messenger = FakeMessenger()
    outcome = _run(session, paper, DAYS[START], messenger=messenger)
    assert outcome.status == "failed"
    assert outcome.error is not None and "the strategy changed" in outcome.error
    assert messenger.texts()[0].startswith("PAPER ALERT:")


def test_an_uncommitted_paper_file_fails_the_run(session: Session, paper: Path) -> None:
    outcome = _run(session, paper, DAYS[START], committed=lambda _path: False)
    assert outcome.status == "failed"
    assert outcome.error is not None and "must be committed" in outcome.error


def test_a_kraken_error_fails_the_run_and_saves_nothing(session: Session, paper: Path) -> None:
    def broken(_pair: str, _today: date) -> list[DailyBar]:
        raise KrakenError("Kraken OHLC XBTUSD: EService:Unavailable")

    outcome = _run(session, paper, DAYS[START], fetch=broken)
    assert (outcome.status, outcome.error) == (
        "failed", "KrakenError: Kraken OHLC XBTUSD: EService:Unavailable",
    )
    assert session.exec(select(PaperDay)).all() == []
    run = session.get(PaperRun, outcome.run_id)
    assert run is not None and run.finished_at is not None


def test_a_missing_candle_fails_the_run(session: Session, paper: Path) -> None:
    series = all_bars()
    series["ETHUSD"] = series["ETHUSD"][: START + 1]  # ETH stops at the start day
    outcome = _run(session, paper, DAYS[START + 1], fetch=fake_fetch(series))
    assert outcome.status == "failed"
    assert outcome.error is not None and "do not end on" in outcome.error


def test_telegram_down_still_saves_the_days(session: Session, paper: Path) -> None:
    outcome = _run(session, paper, DAYS[START], messenger=FakeMessenger(fail=True))
    assert (outcome.status, outcome.days_added) == ("ok", 1)
    run = session.get(PaperRun, outcome.run_id)
    assert run is not None
    assert (run.messages_failed, run.summary_sent) == (1, False)  # the week's summary


def test_a_held_lock_does_nothing(session: Session, paper: Path) -> None:
    assert _run(session, paper, DAYS[START], held=False) == PaperOutcome("locked")
    assert session.exec(select(PaperRun)).all() == []


def test_the_weekly_summary_is_sent_once_per_iso_week(session: Session, paper: Path) -> None:
    messenger = FakeMessenger()
    nights = DAYS[START : START + 15]
    for day in nights:
        _run(session, paper, day, messenger=messenger)
    summaries = [t for t in messenger.texts() if t.startswith(SUMMARY)]
    assert len(summaries) == len({night(day).isocalendar()[:2] for day in nights})


def test_crash_and_breakout_days_are_inside_the_fixture() -> None:
    assert START < BREAKOUT < CRASH < len(DAYS) - 1


def test_a_failure_after_the_days_are_saved_still_finishes_and_alerts(
    session: Session, paper: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr("signalbench.paper.run.summary_text", boom)
    messenger = FakeMessenger()
    outcome = _run(session, paper, DAYS[START], messenger=messenger)
    assert (outcome.status, outcome.days_added, outcome.error) == (
        "failed", 1, "RuntimeError: boom",
    )
    assert len(session.exec(select(PaperDay)).all()) == 1
    assert messenger.texts()[-1] == "PAPER ALERT: RuntimeError: boom"
    run = session.get(PaperRun, outcome.run_id)
    assert run is not None and run.finished_at is not None
    assert (run.status, run.days_added, run.error) == ("failed", 1, "RuntimeError: boom")


def test_a_portfolio_that_no_longer_matches_the_paper_file_fails_the_run(
    session: Session, paper: Path
) -> None:
    portfolio = session.exec(select(PaperPortfolio)).one()
    portfolio.start = DAYS[START + 1]
    session.add(portfolio)
    session.commit()
    outcome = _run(session, paper, DAYS[START])
    assert outcome.status == "failed"
    assert outcome.error is not None and "no longer matches" in outcome.error


def test_the_weekly_summary_is_sent_again_after_a_failed_send(
    session: Session, paper: Path
) -> None:
    first, second = DAYS[START], DAYS[START + 1]
    assert night(first).isocalendar()[:2] == night(second).isocalendar()[:2]
    assert _run(session, paper, first, messenger=FakeMessenger(fail=True)).status == "ok"
    messenger = FakeMessenger()
    _run(session, paper, second, messenger=messenger)
    assert sum(t.startswith(SUMMARY) for t in messenger.texts()) == 1
    # now it is sent, so a third run the same week does not repeat it
    again = FakeMessenger()
    _run(session, paper, DAYS[START + 2], messenger=again)
    assert not any(t.startswith(SUMMARY) for t in again.texts())


def test_a_failed_send_is_echoed(session: Session, paper: Path) -> None:
    lines: list[str] = []
    _run(session, paper, DAYS[START], messenger=FakeMessenger(fail=True), echo=lines.append)
    assert any(line.startswith("Telegram send failed: ConnectionError") for line in lines)
