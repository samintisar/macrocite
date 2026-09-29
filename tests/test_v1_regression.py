"""Pins what a config without `cash_vehicle` produces, recorded before spec 06.

Spec 06 adds idle cash in QQQ and an optional Breakout time limit. A config without
`cash_vehicle` (v1 and the `-cash` variants) must keep producing these results exactly.
"""

import hashlib
import json
import random
from dataclasses import asdict
from datetime import date
from pathlib import Path

from pytest import approx
from sqlmodel import Session

from signalbench.backtest.runner import run_backtest
from signalbench.backtest.simulator import SimulationResult, simulate
from signalbench.db.models import TickerKind
from signalbench.market.bars import AdjustedBar
from signalbench.strategy.readings import NullReadingsView
from strategy_helpers import (
    WeekdaySessions,
    load_test_config,
    make_bar,
    make_market,
    pullback_closes,
    series,
    trend_bars,
    weekdays,
)
from test_runner import DAYS as RUN_DAYS
from test_runner import DIP, LATER, UNIVERSE, _store

CONFIG = load_test_config().with_setups(("pullback", "breakout"))
METRIC_KEYS = [
    "average_hold", "benchmarks", "cagr", "exposure_pct", "h1_end", "h2_start", "max_drawdown",
    "mean_r", "mean_r_h1", "mean_r_h2", "median_r", "open_positions_at_end", "pauses", "recent",
    "recent_since", "sharpe", "skips_by_reason", "total_return", "trades", "trades_h1",
    "trades_h2", "win_rate",
]
TRADES, TOTAL_RETURN, SHARPE = 1, -0.0013333333333332975, -3.643123346373737
DAYS = weekdays(date(2021, 1, 4), 520)
SECTORS = {"AAA": "Energy", "BBB": "Energy", "CCC": "Energy", "DDD": "Utilities", "EEE": "Financials"}
EARNINGS = {"AAA": [DAYS[300], DAYS[380]], "CCC": [DAYS[333], DAYS[450]], "EEE": [DAYS[410]]}


def _walk(rng: random.Random) -> list[AdjustedBar]:
    bars: list[AdjustedBar] = []
    close = 100.0
    for day in DAYS:
        # Uniform draws only: gauss() calls libm (log, cos), whose last digit can vary by
        # platform, and these bars feed exact digests.
        close = max(5.0, close * (1.0008 + 0.07 * (rng.random() - 0.5)))
        volume = int(1_000_000 * (3.0 if rng.random() < 0.05 else 1.0))
        spread = close * 0.01
        bars.append(make_bar(day, close, open_=close * (1 + 0.017 * (rng.random() - 0.5)),
                             high=close + spread, low=close - spread, volume=volume))
    return bars


def _digest(rows: object) -> str:
    """SHA-256 of the rows as JSON; floats keep every digit (repr), dates become ISO strings."""
    text = json.dumps(rows, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _simulation_rows(result: SimulationResult) -> dict[str, object]:
    return {
        "trades": [asdict(trade) for trade in result.trades],
        "open_at_end": [asdict(position) for position in result.open_at_end],
        "events": result.events,
        "curve": [(p.date, p.equity, p.cash, p.open_positions) for p in result.equity_curve],
    }


def test_a_config_without_a_cash_vehicle_simulates_exactly_as_before() -> None:
    rng = random.Random(20260928)
    bars = {name: _walk(rng) for name in SECTORS}
    market = make_market(
        bars, trend_bars(DAYS, 300.0, 0.3), DAYS, CONFIG, sectors=SECTORS, earnings=EARNINGS
    )
    result = simulate(market, NullReadingsView(), CONFIG, DAYS[210], DAYS[510])
    reasons = sorted({trade.reason for trade in result.trades})
    setups = sorted({trade.setup for trade in result.trades})
    assert (len(result.trades), reasons, setups) == (13, ["stop", "target", "time"], ["breakout", "pullback"])
    assert _digest(_simulation_rows(result)) == "d3788232e641390e7770aa1da118b6113beefff74174614ef537d1a8daa2899a"


def test_a_v1_style_run_stores_the_same_metrics_trade_log_and_fingerprint(
    session: Session, tmp_path: Path
) -> None:
    _store(session, "AAA", TickerKind.us_stock, series(RUN_DAYS, pullback_closes(len(RUN_DAYS), dip=DIP)))
    _store(session, "BBB", TickerKind.us_stock, trend_bars(RUN_DAYS, 50.0, 0.1))
    _store(session, "QQQ", TickerKind.benchmark, trend_bars(RUN_DAYS, 300.0, 0.5))
    run, _ = run_backtest(
        session, setup="combined", jev_mode="off", config=load_test_config(),
        config_sha256="f" * 64, universe=UNIVERSE, calendar=WeekdaySessions(), git_sha="abc123",
        run_date=date(2026, 9, 24), reports_dir=tmp_path, now=LATER,
    )
    assert run.data_fingerprint == "f4fdd15ec3c05e0c0f97a765285e8b8f79aefbdcfc7a0d5974f3716f549bce64"
    assert _digest(run.trade_log) == "68c093c7285d91e5d248d80fa7ed7932dfcf179108996df19b4d7ad19301ef29"
    assert sorted(run.metrics) == METRIC_KEYS
    assert (run.metrics["trades"], run.metrics["total_return"], run.metrics["sharpe"]) == (
        TRADES, approx(TOTAL_RETURN, rel=1e-12), approx(SHARPE, rel=1e-12),
    )
