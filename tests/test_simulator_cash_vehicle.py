"""Idle cash in QQQ (spec 06), worked by hand on the pullback fixture of test_simulator.py.

QQQ opens and closes at 300 + 0.5 i on DAYS[i]. AAA dips on SIGNAL (close 146), fills at the
ENTRY open of 146, and exits for time at the EXIT open (10 sessions, flat at 146).
"""

from dataclasses import replace
from datetime import date

from pytest import approx

from signalbench.backtest.simulator import DUST, SimulationResult, simulate
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


def _events_on(result: SimulationResult, i: int) -> list[dict[str, object]]:
    return [e for e in result.events if e["date"] == DAYS[i].isoformat()]


def _vehicle_events_on(result: SimulationResult, i: int) -> list[dict[str, object]]:
    return [e for e in _events_on(result, i) if str(e["event"]).startswith("vehicle_")]


def _two_stocks(
    exit_open: float | None = None,
) -> tuple[dict[str, list[AdjustedBar]], dict[str, str]]:
    """AAA runs as in the fixture (enters at ENTRY, exits at the EXIT open). BBB signals on the
    session AAA's exit is decided, so it enters on the very session AAA exits. `exit_open`
    moves AAA's open on EXIT, which sets the size of the exit proceeds."""
    aaa = series(DAYS, pullback_closes(len(DAYS)))
    if exit_open is not None:
        aaa = with_bar(aaa, EXIT, open=exit_open, high=exit_open + 1.0)
    bbb = series(DAYS, pullback_closes(len(DAYS), dip=EXIT - 1))
    return {"AAA": aaa, "BBB": bbb}, {"AAA": "Energy", "BBB": "Utilities"}


def _exit_and_entry_day(exit_open: float | None) -> tuple[SimulationResult, float, float]:
    """The run, AAA's exit proceeds, and BBB's entry cost, both on DAYS[EXIT]."""
    bars, sectors = _two_stocks(exit_open)
    result = _run(bars=bars, sectors=sectors)
    exits = [e for e in _events_on(result, EXIT) if e["event"] == "exit"]
    entries = [e for e in _events_on(result, EXIT) if e["event"] == "entry"]
    assert [(e["symbol"], e["reason"]) for e in exits] == [("AAA", "time")]
    assert [e["symbol"] for e in entries] == ["BBB"]
    [trade] = result.trades
    proceeds = trade.units * trade.exit_price
    [entry] = entries
    cost = float(str(entry["units"])) * float(str(entry["price"]))
    return result, proceeds, cost


def test_an_exit_and_an_entry_on_one_session_make_one_net_vehicle_sell() -> None:
    result, proceeds, cost = _exit_and_entry_day(None)
    assert cost > proceeds  # BBB costs more than AAA returns: QQQ pays the difference
    [event] = _vehicle_events_on(result, EXIT)
    assert (event["event"], event["price"]) == ("vehicle_sell", _qqq(EXIT))
    assert float(str(event["amount"])) * (1 - COST) == approx(cost - proceeds)
    assert event["cost"] == approx(float(str(event["amount"])) * COST)
    assert _point(result, EXIT)[1] == 0.0


def test_an_exit_and_an_entry_on_one_session_make_one_net_vehicle_buy() -> None:
    result, proceeds, cost = _exit_and_entry_day(170.0)
    assert proceeds > cost  # AAA returns more than BBB costs: the difference is parked
    [event] = _vehicle_events_on(result, EXIT)
    assert (event["event"], event["price"]) == ("vehicle_buy", _qqq(EXIT))
    assert float(str(event["amount"])) * (1 + COST) == approx(proceeds - cost)
    assert event["cost"] == approx(float(str(event["amount"])) * COST)
    assert _point(result, EXIT)[1] == 0.0


def test_a_skipped_order_is_not_a_fill_and_does_not_switch() -> None:
    # AAA opens 2% above its signal close on ENTRY: gap_up. It signals again and fills a
    # session later, so the only QQQ trades are the parking, that fill, and the exit.
    bars = {"AAA": with_bar(series(DAYS, pullback_closes(len(DAYS))), ENTRY, open=146.0 * 1.02)}
    result = _run(bars=bars)
    skips = [e["reason"] for e in _events_on(result, ENTRY) if e["event"] == "skip"]
    assert "gap_up" in skips
    assert [e for e in _events_on(result, ENTRY) if e["event"] in ("entry", "exit")] == []
    assert _vehicle_events_on(result, ENTRY) == []
    assert [(e["date"], e["event"]) for e in _vehicle_events(result)] == [
        (DAYS[START].isoformat(), "vehicle_buy"),
        (DAYS[ENTRY + 1].isoformat(), "vehicle_sell"),
        (DAYS[EXIT + 1].isoformat(), "vehicle_buy"),
    ]
    equity = PARKED * _qqq(ENTRY)  # still all in QQQ at the close of the skipped session
    assert _point(result, ENTRY) == approx((equity, 0.0, equity))


def test_a_deferred_exit_is_not_a_fill_and_does_not_switch() -> None:
    # AAA has no bar on EXIT: its time exit is deferred a session, and QQQ does not trade.
    bars = series(DAYS, pullback_closes(len(DAYS)))
    result = _run(bars={"AAA": bars[:EXIT] + bars[EXIT + 1 :]})
    assert [e["event"] for e in _events_on(result, EXIT) if e["event"].startswith("exit")] == [
        "exit_deferred"
    ]
    assert _vehicle_events_on(result, EXIT) == []
    [trade] = result.trades
    assert trade.exit_date == DAYS[EXIT + 1]
    [_, _, park] = _vehicle_events(result)
    assert park["date"] == DAYS[EXIT + 1].isoformat()


def test_an_entry_needing_qqq_money_is_skipped_when_qqq_has_no_bar() -> None:
    # QQQ has no bar on ENTRY, when AAA would fill. All the money is in QQQ and QQQ cannot be
    # sold, so the entry is skipped for want of cash; QQQ stays at its last close (SIGNAL's).
    benchmark = trend_bars(DAYS, 300.0, 0.5)
    result = _run(benchmark=benchmark[:ENTRY] + benchmark[ENTRY + 1 :])
    day = _events_on(result, ENTRY)
    assert _vehicle_events_on(result, ENTRY) == []
    assert ("AAA", "no_cash") in [(e["symbol"], e["reason"]) for e in day if e["event"] == "skip"]
    assert [e for e in day if e["event"] == "entry"] == []
    held = PARKED * _qqq(SIGNAL)
    assert _point(result, ENTRY) == approx((held, 0.0, held))
    # QQQ trades again at the next fill, and the day after ENTRY it is marked at its own close.
    assert _point(result, ENTRY + 1)[2] == approx(PARKED * _qqq(ENTRY + 1))


def test_leftover_cash_waits_for_the_next_session_with_a_fill() -> None:
    # AAA exits on EXIT but QQQ has no bar that day: the proceeds stay cash, QQQ is marked at
    # its last close, and with no later fill the cash is never parked.
    benchmark = trend_bars(DAYS, 300.0, 0.5)
    result = _run(benchmark=benchmark[:EXIT] + benchmark[EXIT + 1 :])
    [trade] = result.trades
    proceeds = trade.units * trade.exit_price
    assert [e["event"] for e in _vehicle_events(result)] == ["vehicle_buy", "vehicle_sell"]
    left = PARKED - float(str(_vehicle_events(result)[1]["units"]))
    for i in (EXIT, EXIT + 1, 270):
        equity, cash, vehicle = _point(result, i)
        mark = _qqq(EXIT - 1) if i == EXIT else _qqq(i)  # EXIT has no bar: its last close
        assert cash == approx(proceeds)
        assert vehicle == approx(left * mark)
        assert equity == approx(proceeds + left * mark)


def _replay(result: SimulationResult, qqq_close: dict[date, float]) -> None:
    """Rebuild cash and QQQ units from the events, from `start_equity`, and compare them with
    the equity curve at every session. Exits pay units x fill, entries cost units x fill, a
    vehicle_buy costs amount + cost, a vehicle_sell pays amount - cost."""
    units_by_trade = {trade.position_id: trade.units for trade in result.trades}
    by_day: dict[str, list[dict[str, object]]] = {}
    for event in result.events:
        by_day.setdefault(str(event["date"]), []).append(event)
    cash, qqq_units, mark = QQQ.start_equity, 0.0, 0.0
    for point in result.equity_curve:
        for event in by_day.get(point.date.isoformat(), []):
            kind = event["event"]
            if kind == "exit":
                cash += units_by_trade[str(event["position_id"])] * float(str(event["price"]))
            elif kind == "entry":
                cash -= float(str(event["units"])) * float(str(event["price"]))
            elif kind == "vehicle_buy":
                cash -= float(str(event["amount"])) + float(str(event["cost"]))
                qqq_units += float(str(event["units"]))
            elif kind == "vehicle_sell":
                cash += float(str(event["amount"])) - float(str(event["cost"]))
                qqq_units -= float(str(event["units"]))
            assert qqq_units >= 0.0
        assert cash >= -DUST  # entries may overdraw only until the day's vehicle_sell
        mark = qqq_close.get(point.date, mark)  # a session with no QQQ bar keeps the last close
        assert point.cash == approx(cash, abs=1e-9, rel=0)
        assert point.vehicle_value == approx(qqq_units * mark, abs=1e-9, rel=0)


def _closes(bars: list[AdjustedBar]) -> dict[date, float]:
    return {bar.date: bar.close for bar in bars}


def test_money_is_conserved_across_several_trades() -> None:
    # Three stocks dip a few sessions apart: entries and exits overlap, some sessions are
    # exit-only, entry-only or both, and the vehicle is sold and re-bought around them.
    sectors = {"AAA": "Energy", "BBB": "Utilities", "CCC": "Financials"}
    bars = {
        "AAA": series(DAYS, pullback_closes(len(DAYS))),
        "BBB": series(DAYS, pullback_closes(len(DAYS), dip=EXIT - 1)),
        "CCC": series(DAYS, pullback_closes(len(DAYS), dip=SIGNAL + 3)),
    }
    result = _run(bars=bars, sectors=sectors, end=279)
    assert len(result.trades) == 3  # the scenario really has several round trips
    sold_and_bought = {str(e["event"]) for e in _vehicle_events(result)}
    assert sold_and_bought == {"vehicle_buy", "vehicle_sell"}
    _replay(result, _closes(trend_bars(DAYS, 300.0, 0.5)))


def test_money_is_conserved_when_qqq_is_sold_out_and_has_a_missing_bar() -> None:
    # Three signals open 1% up (the last is trimmed and every QQQ unit is sold), then QQQ
    # misses the exit session: cash waits and the vehicle keeps its last close.
    bars = with_bar(series(DAYS, pullback_closes(len(DAYS))), ENTRY, open=146.0 * 1.01)
    sectors = {"AAA": "Energy", "BBB": "Utilities", "CCC": "Financials"}
    benchmark = trend_bars(DAYS, 300.0, 0.5)
    thin = benchmark[:EXIT] + benchmark[EXIT + 1 :]
    result = _run(bars={s: bars for s in sectors}, sectors=sectors, benchmark=thin, end=270)
    assert len(result.trades) == 3
    _replay(result, _closes(thin))


def _idle_cash_run(bbb: list[AdjustedBar]) -> SimulationResult:
    """AAA exits on EXIT while QQQ has no bar, so its proceeds sit idle as cash; BBB is the
    stock whose skipped or deferred order falls on a later session that has a QQQ bar."""
    benchmark = trend_bars(DAYS, 300.0, 0.5)
    return _run(
        bars={"AAA": series(DAYS, pullback_closes(len(DAYS))), "BBB": bbb},
        sectors={"AAA": "Energy", "BBB": "Utilities"},
        benchmark=benchmark[:EXIT] + benchmark[EXIT + 1 :],
        end=272,
    )


def test_a_skipped_order_does_not_sweep_idle_cash_into_qqq() -> None:
    # BBB's only order on DAYS[265] is a gap_up skip. The idle cash stays cash that session
    # and goes to work on the next one that has a fill (BBB's entry on 266).
    closes = pullback_closes(len(DAYS), dip=264)
    bbb = with_bar(series(DAYS, closes), 265, open=closes[264] * 1.02)
    result = _idle_cash_run(bbb)
    idle = _point(result, EXIT)[1]
    assert idle > 1.0
    assert [e["reason"] for e in _events_on(result, 265) if e["event"] == "skip"] == ["gap_up"]
    for i in range(EXIT, 266):
        assert _vehicle_events_on(result, i) == []
        assert _point(result, i)[1] == approx(idle)
    assert [e["event"] for e in _vehicle_events_on(result, 266)] == ["vehicle_sell"]
    assert _point(result, 266)[1] == 0.0


def test_a_deferred_exit_does_not_sweep_idle_cash_into_qqq() -> None:
    # BBB has no bar on DAYS[265], when its time exit is due: it is deferred, and the idle
    # cash stays cash until the exit fills on 266 and one purchase parks it all.
    bbb = series(DAYS, pullback_closes(len(DAYS), dip=254))
    result = _idle_cash_run(bbb[:265] + bbb[266:])
    idle = _point(result, EXIT)[1]
    assert idle > 1.0
    assert [e["event"] for e in _events_on(result, 265) if e["event"].startswith("exit")] == [
        "exit_deferred"
    ]
    for i in range(EXIT, 266):
        assert _vehicle_events_on(result, i) == []
        assert _point(result, i)[1] == approx(idle)
    assert [e["event"] for e in _vehicle_events_on(result, 266)] == ["vehicle_buy"]
    assert _point(result, 266)[1] == 0.0
