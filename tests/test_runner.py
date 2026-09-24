from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlmodel import Session, select

from signalbench.backtest.fingerprint import data_fingerprint
from signalbench.backtest.preregistration import RunRefusedError
from signalbench.backtest.runner import (
    RequiresSpec03Error,
    load_market_inputs,
    run_backtest,
    setups_for_run,
)
from signalbench.db.models import BacktestRun, EarningsEvent, Price, Ticker, TickerKind
from signalbench.ingest.cdr import CdrEntry
from signalbench.market.bars import AdjustedBar
from strategy_helpers import (
    WeekdaySessions,
    load_test_config,
    pullback_closes,
    series,
    trend_bars,
    weekdays,
)

DAYS = weekdays(date(2011, 1, 3), 300)  # DAYS[260] is 2012-01-02
DIP = 265
NEW_YORK = ZoneInfo("America/New_York")
LATER = datetime(2026, 9, 24, 20, 0, tzinfo=NEW_YORK)  # long after the last fixture bar
UNIVERSE = [
    CdrEntry("AAA", "ZAAA", "ZAAA.NE", "Aaa", "Information Technology"),
    CdrEntry("BBB", "ZBBB", "ZBBB.NE", "Bbb", "Energy"),
]


def _store(session: Session, symbol: str, kind: TickerKind, bars: list[AdjustedBar]) -> None:
    ticker = Ticker(symbol=symbol, company_name=symbol, kind=kind)
    session.add(ticker)
    session.commit()
    session.refresh(ticker)
    for bar in bars:
        session.add(
            Price(
                ticker_id=ticker.id,
                date=bar.date,
                open=Decimal(str(round(bar.open, 4))),
                high=Decimal(str(round(bar.high, 4))),
                low=Decimal(str(round(bar.low, 4))),
                close=Decimal(str(round(bar.close, 4))),
                adj_close=Decimal(str(round(bar.close, 4))),
                volume=bar.volume,
            )
        )
    session.commit()


@pytest.fixture
def seeded(session: Session) -> Session:
    _store(session, "AAA", TickerKind.us_stock, series(DAYS, pullback_closes(len(DAYS), dip=DIP)))
    _store(session, "BBB", TickerKind.us_stock, trend_bars(DAYS, 50.0, 0.1))
    _store(session, "QQQ", TickerKind.benchmark, trend_bars(DAYS, 300.0, 0.5))
    return session


def _run(
    session: Session, tmp_path: Path, setup: str = "pullback", now: datetime = LATER
) -> tuple[BacktestRun, Path]:
    return run_backtest(
        session,
        setup=setup,  # type: ignore[arg-type]
        jev_mode="off",
        config=load_test_config(),
        config_sha256="f" * 64,
        universe=UNIVERSE,
        calendar=WeekdaySessions(),
        git_sha="abc123",
        run_date=date(2026, 9, 24),
        reports_dir=tmp_path,
        now=now,
    )


def test_run_is_stored_with_provenance_and_report(seeded: Session, tmp_path: Path) -> None:
    run, path = _run(seeded, tmp_path)
    stored = seeded.exec(select(BacktestRun)).one()
    assert stored.id == run.id
    assert (run.setup, run.jev_mode, run.strategy_version) == ("pullback", "off", "test")
    assert (run.config_sha256, run.git_sha) == ("f" * 64, "abc123")
    assert run.start_date == date(2012, 1, 2)  # first session on or after backtest.start
    assert run.end_date == DAYS[-1]  # the last QQQ bar
    assert run.metrics["trades"] >= 1
    assert run.trade_log["trades"][0]["signal_date"] == DAYS[DIP].isoformat()
    assert [b["name"] for b in run.metrics["benchmarks"]] == [
        "QQQ buy-and-hold",
        "Survivor benchmark (equal weight, not rebalanced)",
    ]
    assert set(run.pass_bar) == {"trades", "mean_r", "mean_r_halves", "sharpe"}
    assert run.passed is False  # one trade is far below 30
    assert path == tmp_path / "2026-09-24-pullback-off.md"
    assert path.read_text(encoding="utf-8").startswith("# Backtest: pullback (Jev off)")


def test_data_fingerprint_covers_every_input_series_and_earnings_date(
    seeded: Session, tmp_path: Path
) -> None:
    run, _ = _run(seeded, tmp_path)
    stored = {
        "AAA": series(DAYS, pullback_closes(len(DAYS), dip=DIP)),
        "BBB": trend_bars(DAYS, 50.0, 0.1),
        "QQQ": trend_bars(DAYS, 300.0, 0.5),
    }
    assert run.data_fingerprint == data_fingerprint(stored.items(), [])
    bbb = seeded.exec(select(Ticker).where(Ticker.symbol == "BBB")).one()
    seeded.add(EarningsEvent(ticker_id=bbb.id, event_date=date(2011, 3, 1), source="sec_2.02"))
    seeded.commit()
    again, _ = _run(seeded, tmp_path)
    assert again.data_fingerprint == data_fingerprint(
        stored.items(), [("BBB", date(2011, 3, 1))]
    )
    assert again.data_fingerprint != run.data_fingerprint


def test_second_report_on_the_same_day_gets_a_suffix(seeded: Session, tmp_path: Path) -> None:
    _, first = _run(seeded, tmp_path)
    second_run, second = _run(seeded, tmp_path)
    assert first != second
    assert second.name == f"2026-09-24-pullback-off-{str(second_run.id)[:8]}.md"


def test_sentiment_and_jev_filter_need_spec_03() -> None:
    with pytest.raises(RequiresSpec03Error, match="spec 03"):
        setups_for_run("sentiment", "off")
    with pytest.raises(RequiresSpec03Error, match="spec 03"):
        setups_for_run("pullback", "filter")
    assert setups_for_run("combined", "off") == ("pullback", "breakout")


def test_unseeded_universe_is_an_error(session: Session, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Not seeded: AAA, BBB, QQQ"):
        _run(session, tmp_path)


def test_a_changed_config_under_a_used_version_is_refused(seeded: Session, tmp_path: Path) -> None:
    _run(seeded, tmp_path)  # stores version "test" with config_sha256 "f" * 64
    with pytest.raises(RunRefusedError, match="must be a new version"):
        run_backtest(
            seeded, setup="pullback", jev_mode="off", config=load_test_config(),
            config_sha256="e" * 64, universe=UNIVERSE, calendar=WeekdaySessions(),
            git_sha="abc123", run_date=date(2026, 9, 24), reports_dir=tmp_path / "other",
            now=LATER,
        )
    assert len(seeded.exec(select(BacktestRun)).all()) == 1
    assert not (tmp_path / "other").exists()


def test_a_series_that_stops_before_the_last_session_is_refused(
    session: Session, tmp_path: Path
) -> None:
    _store(session, "AAA", TickerKind.us_stock, series(DAYS, pullback_closes(len(DAYS), dip=DIP)))
    _store(session, "BBB", TickerKind.us_stock, trend_bars(DAYS, 50.0, 0.1)[:-3])  # 3 stale
    _store(session, "QQQ", TickerKind.benchmark, trend_bars(DAYS, 300.0, 0.5))
    with pytest.raises(RunRefusedError) as refused:
        _run(session, tmp_path)
    message = str(refused.value)
    assert f"BBB (last bar {DAYS[-4].isoformat()})" in message
    assert "AAA" not in message
    assert DAYS[-1].isoformat() in message  # the run's last session
    assert "signalbench ingest prices" in message
    assert session.exec(select(BacktestRun)).all() == []


def test_a_symbol_with_no_bars_is_refused(session: Session, tmp_path: Path) -> None:
    _store(session, "AAA", TickerKind.us_stock, series(DAYS, pullback_closes(len(DAYS), dip=DIP)))
    _store(session, "BBB", TickerKind.us_stock, [])
    _store(session, "QQQ", TickerKind.benchmark, trend_bars(DAYS, 300.0, 0.5))
    with pytest.raises(RunRefusedError, match=r"BBB \(no bars\)"):
        _run(session, tmp_path)


def test_todays_partial_bar_is_dropped_before_16_15_new_york(
    seeded: Session, tmp_path: Path
) -> None:
    today = DAYS[-1]  # the fixture's last bar is "today"
    before = datetime(today.year, today.month, today.day, 16, 14, tzinfo=NEW_YORK)
    run, _ = _run(seeded, tmp_path, now=before)
    assert run.end_date == DAYS[-2]
    stored = {
        "AAA": series(DAYS, pullback_closes(len(DAYS), dip=DIP))[:-1],
        "BBB": trend_bars(DAYS, 50.0, 0.1)[:-1],
        "QQQ": trend_bars(DAYS, 300.0, 0.5)[:-1],
    }
    assert run.data_fingerprint == data_fingerprint(stored.items(), [])
    # The same instant in UTC is still before 16:15 in New York.
    utc = before.astimezone(ZoneInfo("UTC"))
    assert _run(seeded, tmp_path, now=utc)[0].end_date == DAYS[-2]


def test_todays_bar_is_kept_from_16_15_new_york(seeded: Session, tmp_path: Path) -> None:
    today = DAYS[-1]
    at = datetime(today.year, today.month, today.day, 16, 15, tzinfo=NEW_YORK)
    assert _run(seeded, tmp_path, now=at)[0].end_date == today


def test_backtest_earnings_are_every_sec_2_02_date_unclustered(seeded: Session) -> None:
    aaa = seeded.exec(select(Ticker).where(Ticker.symbol == "AAA")).one()
    for day, source in (
        (date(2015, 1, 5), "sec_2.02"),
        (date(2015, 1, 7), "sec_2.02"),  # within 3 days: clustered away by earnings_dates()
        (date(2015, 1, 5), "finnhub"),
        (date(2015, 4, 20), "finnhub"),  # calendar-only dates are not used by the backtest
    ):
        seeded.add(EarningsEvent(ticker_id=aaa.id, event_date=day, source=source))
    seeded.commit()
    inputs = load_market_inputs(seeded, UNIVERSE, "QQQ", None)
    earnings = {item.symbol: item.earnings for item in inputs.symbols}
    assert earnings == {"AAA": [date(2015, 1, 5), date(2015, 1, 7)], "BBB": []}


def test_a_stale_benchmark_is_named_in_the_refusal(session: Session, tmp_path: Path) -> None:
    _store(session, "AAA", TickerKind.us_stock, series(DAYS, pullback_closes(len(DAYS), dip=DIP)))
    _store(session, "BBB", TickerKind.us_stock, trend_bars(DAYS, 50.0, 0.1))
    _store(session, "QQQ", TickerKind.benchmark, trend_bars(DAYS, 300.0, 0.5)[:-2])  # 2 stale
    with pytest.raises(RunRefusedError) as refused:
        _run(session, tmp_path)
    message = str(refused.value)
    assert f"QQQ (last bar {DAYS[-3].isoformat()})" in message
    assert DAYS[-1].isoformat() in message  # the latest session in the data
    assert "AAA" not in message and "BBB" not in message


def test_a_naive_clock_is_rejected(seeded: Session, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _run(seeded, tmp_path, now=datetime(2026, 9, 24, 12, 0))  # noqa: DTZ001  # naive on purpose
