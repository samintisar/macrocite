from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlmodel import Session, select

from signalbench.backtest.fingerprint import data_fingerprint
from signalbench.backtest.runner import (
    RequiresSpec03Error,
    run_backtest,
    setups_for_run,
)
from signalbench.db.models import BacktestRun, Price, Ticker, TickerKind
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


def _run(session: Session, tmp_path: Path, setup: str = "pullback") -> tuple[BacktestRun, Path]:
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


def test_data_fingerprint_covers_every_input_series(seeded: Session, tmp_path: Path) -> None:
    run, _ = _run(seeded, tmp_path)
    stored = {
        "AAA": series(DAYS, pullback_closes(len(DAYS), dip=DIP)),
        "BBB": trend_bars(DAYS, 50.0, 0.1),
        "QQQ": trend_bars(DAYS, 300.0, 0.5),
    }
    assert run.data_fingerprint == data_fingerprint(stored.items())


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
