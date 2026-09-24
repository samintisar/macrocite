from datetime import date

from pytest import approx

from signalbench.backtest.simulator import (
    RiskState,
    SimulationResult,
    simulate,
    step_risk,
)
from signalbench.market.bars import AdjustedBar
from signalbench.strategy.readings import NullReadingsView
from strategy_helpers import (
    load_test_config,
    make_market,
    pullback_closes,
    series,
    trend_bars,
    weekdays,
    with_bar,
)

CONFIG = load_test_config().with_setups(("pullback",))
DAYS = weekdays(date(2023, 1, 2), 280)
SIGNAL, ENTRY = 251, 252
STOP = 145.0 - 0.5 * (30 / 14)  # from the pullback fixture (see test_decide.py)
COST = 0.002


def _run(bars: list[AdjustedBar], start: int = 240, end: int = 270) -> SimulationResult:
    market = make_market({"AAA": bars}, trend_bars(DAYS, 300.0, 0.5), DAYS, CONFIG)
    return simulate(market, NullReadingsView(), CONFIG, DAYS[start], DAYS[end])


def _events(result: SimulationResult, kind: str) -> list[dict[str, object]]:
    return [event for event in result.events if event["event"] == kind]


def test_entry_fills_at_next_open_with_cost_and_target_from_the_planned_risk() -> None:
    result = _run(series(DAYS, pullback_closes(len(DAYS))), end=260)  # before the time exit
    [entry] = _events(result, "entry")
    assert entry["date"] == DAYS[ENTRY].isoformat()
    assert entry["price"] == approx(146.0 * (1 + COST))
    assert entry["units"] == approx(100.0 / 3 / 146.0)
    [position] = result.open_positions
    fill = 146.0 * (1 + COST)
    assert position.entry_price == approx(fill)
    assert position.stop == approx(STOP)
    assert position.target == approx(fill + 2 * (146.0 - STOP))  # fill + 2R, R planned at the signal
    assert position.sessions_held == 260 - ENTRY + 1  # the entry session counts as 1
    assert result.equity_curve[-1].cash == approx(100.0 - position.units * fill)


def test_gap_up_above_one_percent_skips() -> None:
    bars = with_bar(series(DAYS, pullback_closes(len(DAYS))), ENTRY, open=146.0 * 1.01 + 0.01)
    result = _run(bars)
    assert {"date": DAYS[ENTRY].isoformat(), "event": "skip", "symbol": "AAA",
            "setup": "pullback", "reason": "gap_up"} in result.events
    assert all(e["date"] != DAYS[ENTRY].isoformat() for e in _events(result, "entry"))


def test_open_exactly_one_percent_up_still_fills() -> None:
    bars = with_bar(series(DAYS, pullback_closes(len(DAYS))), ENTRY, open=146.0 * (1.0 + 0.01))
    assert _events(_run(bars), "entry")[0]["date"] == DAYS[ENTRY].isoformat()


def test_open_at_or_below_the_stop_skips() -> None:
    bars = with_bar(series(DAYS, pullback_closes(len(DAYS))), ENTRY, open=STOP)
    result = _run(bars)
    skips = [e for e in _events(result, "skip") if e["date"] == DAYS[ENTRY].isoformat()]
    assert [e["reason"] for e in skips] == ["gap_below_stop"]


def test_crash_pauses_then_auto_resumes_with_peak_reset() -> None:
    closes = pullback_closes(len(DAYS))
    closes[ENTRY + 1 :] = [60.0] * (len(DAYS) - ENTRY - 1)
    result = _run(series(DAYS, closes))
    [trade] = result.trades
    assert (trade.reason, trade.signal_date, trade.entry_date) == ("stop", DAYS[SIGNAL], DAYS[ENTRY])
    assert (trade.exit_signal_date, trade.exit_date) == (DAYS[ENTRY + 1], DAYS[ENTRY + 2])
    assert trade.exit_price == approx(60.0 * (1 - COST))
    assert trade.r == approx((60.0 * (1 - COST) - 146.0 * (1 + COST)) / (146.0 - STOP))  # about -41.7
    assert trade.sessions_held == 2
    [pause] = _events(result, "pause")
    [resume] = _events(result, "resume")
    assert pause["date"] == DAYS[ENTRY + 1].isoformat()
    assert pause["peak"] == approx(100.0)
    assert resume["date"] == DAYS[ENTRY + 1 + 10].isoformat()
    assert resume["peak"] == approx(resume["equity"])  # peak := equity at resume
    assert resume["equity"] == approx(100.0 + trade.pnl)


def test_step_risk_pauses_below_85_percent_of_peak() -> None:
    state = RiskState(peak=100.0, paused=False, paused_since=None, paused_at=None)
    same, change = step_risk(state, 85.0, 5, DAYS[5], CONFIG)
    assert (same.paused, change) == (False, None)  # exactly -15% is not below
    paused, change = step_risk(state, 84.99, 5, DAYS[5], CONFIG)
    assert (paused.paused, paused.paused_since, paused.paused_at, change) == (
        True, DAYS[5], 5, "pause",
    )
    higher, _ = step_risk(state, 120.0, 5, DAYS[5], CONFIG)
    assert higher.peak == 120.0


def test_step_risk_resumes_after_ten_sessions_and_resets_the_peak() -> None:
    paused = RiskState(peak=100.0, paused=True, paused_since=DAYS[5], paused_at=5)
    still, change = step_risk(paused, 80.0, 14, DAYS[14], CONFIG)
    assert (still.paused, change) == (True, None)
    resumed, change = step_risk(paused, 80.0, 15, DAYS[15], CONFIG)
    assert (resumed.paused, resumed.peak, change) == (False, 80.0, "resume")


def test_last_entry_is_trimmed_to_the_cash_left() -> None:
    # Three identical signals fill the three slots; each is sized at equity / 3 on the close.
    # All open 1% higher, so the third can only buy what the first two left.
    bars = with_bar(series(DAYS, pullback_closes(len(DAYS))), ENTRY, open=146.0 * 1.01)
    sectors = {"AAA": "Energy", "BBB": "Utilities", "CCC": "Financials"}
    market = make_market(
        {symbol: bars for symbol in sectors}, trend_bars(DAYS, 300.0, 0.5), DAYS, CONFIG,
        sectors=sectors,
    )
    result = simulate(market, NullReadingsView(), CONFIG, DAYS[240], DAYS[260])
    entries = _events(result, "entry")
    assert [(e["symbol"], e["trimmed"]) for e in entries] == [
        ("AAA", False), ("BBB", False), ("CCC", True),
    ]
    assert result.equity_curve[ENTRY - 240].cash == approx(0.0, abs=1e-9)


def test_r_and_target_use_the_risk_planned_at_the_signal_not_the_fill() -> None:
    # Reviewer's case: the open lands just above the stop, so fill - stop is tiny. R and the
    # target must still use the planned risk, signal close 146 - stop (about 2.071).
    closes = pullback_closes(len(DAYS))
    closes[ENTRY + 2 :] = [148.5] * (len(DAYS) - ENTRY - 2)
    bars = with_bar(series(DAYS, closes), ENTRY, open=STOP + 0.05)
    result = _run(bars)
    [trade] = result.trades
    fill = (STOP + 0.05) * (1 + COST)  # about 144.267
    planned = 146.0 - STOP  # about 2.071
    assert trade.entry_price == approx(fill)
    assert trade.signal_close == approx(146.0)
    # Target = fill + 2 x planned, about 148.41: close 146 on 252-253 is below it, 148.5 on
    # 254 is above it, so the exit fills at the 255 open (under the old fill-based rule the
    # target was about 144.94 and it exited on 253 at +4.27R).
    assert trade.reason == "target"
    assert (trade.exit_signal_date, trade.exit_date) == (DAYS[ENTRY + 2], DAYS[ENTRY + 3])
    assert trade.r == approx((148.5 * (1 - COST) - fill) / planned)  # about +1.90
    assert trade.r == approx(trade.pnl / (trade.units * planned))
    assert 1.8 < trade.r < 2.0
