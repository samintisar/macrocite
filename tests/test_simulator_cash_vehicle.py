"""Idle cash in QQQ (spec 06), worked by hand on the pullback fixture of test_simulator.py.

QQQ opens and closes at 300 + 0.5 i on DAYS[i]. AAA dips on SIGNAL (close 146), fills at the
ENTRY open of 146, and exits for time at the EXIT open (10 sessions, flat at 146).
"""

from dataclasses import replace
from datetime import date

from pytest import approx

from signalbench.backtest.simulator import SimulationResult, simulate
from signalbench.market.bars import AdjustedBar
from signalbench.strategy.config import CashVehicle, StrategyConfig
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

PLAIN = load_test_config().with_setups(("pullback",))
QQQ = replace(PLAIN, cash_vehicle=CashVehicle(symbol="QQQ", cost_per_side=0.002))
DAYS = weekdays(date(2023, 1, 2), 280)
START, SIGNAL, ENTRY, EXIT = 240, 251, 252, 262
COST = 0.002


def _qqq(i: int) -> float:
    return 300.0 + 0.5 * i


def _run(
    config: StrategyConfig = QQQ,
    bars: dict[str, list[AdjustedBar]] | None = None,
    benchmark: list[AdjustedBar] | None = None,
    sectors: dict[str, str] | None = None,
    end: int = 270,
) -> SimulationResult:
    bars = bars or {"AAA": series(DAYS, pullback_closes(len(DAYS)))}
    benchmark = benchmark or trend_bars(DAYS, 300.0, 0.5)
    market = make_market(bars, benchmark, DAYS, config, sectors=sectors)
    return simulate(market, NullReadingsView(), config, DAYS[START], DAYS[end])


def _vehicle_events(result: SimulationResult) -> list[dict[str, object]]:
    return [e for e in result.events if str(e["event"]).startswith("vehicle_")]


def _point(result: SimulationResult, i: int) -> tuple[float, float, float]:
    point = result.equity_curve[i - START]
    assert point.date == DAYS[i]
    return point.equity, point.cash, point.vehicle_value


PARKED = 100.0 / (_qqq(START) * (1 + COST))  # QQQ units bought with the start equity


def test_the_start_equity_is_parked_at_the_first_open_and_quiet_days_do_not_switch() -> None:
    events = _vehicle_events(_run())
    assert [(e["date"], e["event"]) for e in events] == [
        (DAYS[START].isoformat(), "vehicle_buy"),
        (DAYS[ENTRY].isoformat(), "vehicle_sell"),
        (DAYS[EXIT].isoformat(), "vehicle_buy"),
    ]
    first = events[0]
    assert (first["symbol"], first["price"]) == ("QQQ", _qqq(START))
    assert first["units"] == approx(PARKED)
    assert first["amount"] == approx(100.0 / (1 + COST))  # the market value bought
    assert first["cost"] == approx(100.0 - 100.0 / (1 + COST))


def test_sizing_uses_total_equity_and_sell_to_fund_charges_only_the_amount_sold() -> None:
    result = _run()
    equity = PARKED * _qqq(SIGNAL)  # all in QQQ at the signal close
    assert _point(result, SIGNAL) == approx((equity, 0.0, equity))
    [entry] = [e for e in result.events if e["event"] == "entry"]
    units = equity / 3 / 146.0  # the equity/3 cap, on total equity (v1 would use 100)
    assert entry["units"] == approx(units)
    need = units * 146.0 * (1 + COST)
    [sell] = [e for e in _vehicle_events(result) if e["event"] == "vehicle_sell"]
    assert sell["price"] == _qqq(ENTRY)
    assert sell["amount"] == approx(need / (1 - COST))  # just enough to cover the entry
    assert sell["cost"] == approx(need / (1 - COST) * COST)
    left = PARKED - need / (1 - COST) / _qqq(ENTRY)
    assert sell["units"] == approx(PARKED - left)
    # The close of ENTRY: no cash, the stock at 146, the rest of QQQ at its close.
    assert _point(result, ENTRY) == approx(
        (units * 146.0 + left * _qqq(ENTRY), 0.0, left * _qqq(ENTRY)), abs=1e-12
    )


def test_exit_proceeds_are_parked_at_the_open() -> None:
    result = _run()
    [trade] = result.trades
    assert (trade.reason, trade.exit_date) == ("time", DAYS[EXIT])
    proceeds = trade.units * 146.0 * (1 - COST)
    [_, _, park] = _vehicle_events(result)
    assert park["amount"] == approx(proceeds / (1 + COST))
    assert park["cost"] == approx(proceeds - proceeds / (1 + COST))
    held = PARKED - (trade.units * 146.0 * (1 + COST)) / (1 - COST) / _qqq(ENTRY)
    held += proceeds / (_qqq(EXIT) * (1 + COST))
    assert _point(result, EXIT) == approx(
        (held * _qqq(EXIT), 0.0, held * _qqq(EXIT)), abs=1e-12
    )
    assert _point(result, 270) == approx((held * _qqq(270), 0.0, held * _qqq(270)), abs=1e-12)


def test_the_cash_cap_counts_qqq_at_the_open_net_of_the_switching_cost() -> None:
    # Three identical signals open 1% up: A and B fill in full, C gets what QQQ can still pay
    # for after its 0.2% selling cost, and every QQQ unit is sold.
    bars = with_bar(series(DAYS, pullback_closes(len(DAYS))), ENTRY, open=146.0 * 1.01)
    sectors = {"AAA": "Energy", "BBB": "Utilities", "CCC": "Financials"}
    result = _run(bars={symbol: bars for symbol in sectors}, sectors=sectors, end=260)
    entries = [e for e in result.events if e["event"] == "entry"]
    assert [(e["symbol"], e["trimmed"]) for e in entries] == [
        ("AAA", False), ("BBB", False), ("CCC", True),
    ]
    fill = 146.0 * 1.01 * (1 + COST)
    available = PARKED * _qqq(ENTRY) * (1 - COST)
    spent = sum(float(str(e["units"])) * fill for e in entries)
    assert spent == approx(available)
    [sell] = [e for e in _vehicle_events(result) if e["event"] == "vehicle_sell"]
    assert sell["units"] == approx(PARKED)
    _, cash, vehicle = _point(result, ENTRY)
    assert (cash, vehicle) == (approx(0.0, abs=1e-9), approx(0.0, abs=1e-9))


def test_the_drawdown_pause_sees_the_qqq_value() -> None:
    # No stock ever signals; QQQ falls 20% on DAYS[250]. Only the QQQ variant pauses.
    closes = [_qqq(i) for i in range(250)] + [_qqq(249) * 0.8] * (len(DAYS) - 250)
    benchmark = series(DAYS, closes)
    flat = {"AAA": trend_bars(DAYS, 50.0, 0.0)}
    with_qqq = _run(bars=flat, benchmark=benchmark)
    [pause] = [e for e in with_qqq.events if e["event"] == "pause"]
    assert pause["date"] == DAYS[250].isoformat()
    assert pause["equity"] == approx(PARKED * _qqq(249) * 0.8)
    plain = _run(config=PLAIN, bars=flat, benchmark=benchmark)
    assert [e for e in plain.events if e["event"] == "pause"] == []
    assert _vehicle_events(plain) == []
    assert {point.vehicle_value for point in plain.equity_curve} == {0.0}
