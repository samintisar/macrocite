"""Stock splits in a paper portfolio's saved state (spec 07 changelog, 2026-09-29)."""

from dataclasses import replace
from datetime import date

import pytest

from signalbench.backtest.simulator import Opened, Orders, SimState, initial_state
from signalbench.ingest.prices import Split
from signalbench.paper.splits import adjust_for_splits, rescale
from signalbench.paper.start import PaperRefusedError
from signalbench.strategy.decision import EntryOrder, ExitOrder
from signalbench.strategy.market_view import MarketView
from strategy_helpers import (
    load_test_config,
    make_market,
    make_position,
    trend_bars,
    weekdays,
)

DAYS = weekdays(date(2024, 1, 1), 260)
LAST = DAYS[250]  # the portfolio's last stepped session
TARGET = DAYS[252]
EX_DATE = DAYS[251]


def _entry(symbol: str) -> EntryOrder:
    return EntryOrder(symbol=symbol, setup="pullback", sector="Energy", signal_close=80.0, stop=76.0,
                      target_r=2.0, time_limit=10, units=5.0, risk_amount=20.0, catalyst=False,
                      rank=1)


def _state() -> SimState:
    """AAA held (opened at DAYS[240]), an exit order for it, BBB about to be bought, QQQ held."""
    position = make_position("P00001", "AAA", units=3.0, entry_price=100.0,
                             entry_date=DAYS[241], stop=90.0, target=120.0, highest_close=104.0)
    return replace(
        initial_state(load_test_config()),
        cash=10.0,
        vehicle_units=4.0,
        vehicle_mark=300.0,
        positions=(position,),
        opened={"P00001": Opened(signal_date=DAYS[240], signal_close=98.0, initial_stop=90.0)},
        orders=Orders(LAST, [ExitOrder("P00001", "AAA", "time")], [_entry("BBB")]),
        next_id=2,
        sessions=11,
        last_session=LAST,
    )


def test_rescale_multiplies_units_and_divides_prices_by_the_ratio() -> None:
    state = _state()
    after = rescale(state, "AAA", 2.0, vehicle="QQQ")
    [position] = after.positions
    assert (position.units, position.entry_price, position.stop, position.target,
            position.highest_close) == (6.0, 50.0, 45.0, 60.0, 52.0)
    assert after.opened["P00001"] == Opened(signal_date=DAYS[240], signal_close=49.0,
                                            initial_stop=45.0)
    assert position.units * position.entry_price == 300.0  # same value
    assert after.orders == state.orders  # an exit order has no prices; BBB is untouched
    assert (after.cash, after.vehicle_units, after.vehicle_mark) == (10.0, 4.0, 300.0)


def test_rescale_changes_a_pending_entry_and_keeps_its_risk() -> None:
    after = rescale(_state(), "BBB", 4.0, vehicle="QQQ")
    assert after.orders is not None
    [entry] = after.orders.entries
    assert (entry.signal_close, entry.stop, entry.units, entry.risk_amount, entry.target_r) == (
        20.0, 19.0, 20.0, 20.0, 2.0,
    )
    assert after.positions == _state().positions


def test_rescale_changes_the_cash_vehicle() -> None:
    after = rescale(_state(), "QQQ", 2.0, vehicle="QQQ")
    assert (after.vehicle_units, after.vehicle_mark) == (8.0, 150.0)
    assert after.positions == _state().positions


def _market(scales: dict[str, float]) -> MarketView:
    """Stored prices after tonight's ingest: AAA near 100, BBB near 80, QQQ near 300, each
    divided by its `scales` entry on every date (a split rescales the whole history)."""
    bars = {
        "AAA": trend_bars(DAYS, 98.0 / scales.get("AAA", 1.0), 0.0),
        "BBB": trend_bars(DAYS, 80.0 / scales.get("BBB", 1.0), 0.0),
    }
    benchmark = trend_bars(DAYS, 300.0 / scales.get("QQQ", 1.0), 0.0)
    return make_market(bars, benchmark, DAYS, load_test_config())


def _adjust(
    scales: dict[str, float], known: dict[str, list[Split]],
    applied: frozenset[tuple[str, date]] = frozenset(),
) -> tuple[SimState, list[dict[str, object]]]:
    return adjust_for_splits(_state(), market=_market(scales), known=known,
                             applied=applied, target=TARGET, vehicle="QQQ", where="p-test")


def test_a_split_since_the_last_session_rescales_the_state_and_is_reported() -> None:
    state, events = _adjust({"AAA": 2.0}, {"AAA": [Split(EX_DATE, 2.0)]})
    assert state == rescale(_state(), "AAA", 2.0, vehicle="QQQ")
    assert events == [{"symbol": "AAA", "ex_date": EX_DATE.isoformat(), "ratio": 2.0}]


def test_no_split_changes_nothing() -> None:
    assert _adjust({}, {}) == (_state(), [])


def test_splits_outside_the_window_or_already_applied_are_ignored() -> None:
    known = {"AAA": [Split(DAYS[200], 3.0), Split(LAST, 5.0), Split(DAYS[253], 7.0)]}
    assert _adjust({}, known) == (_state(), [])
    # Applied by an earlier run whose later sessions failed: the state is already rescaled.
    assert _adjust({}, {"AAA": [Split(EX_DATE, 2.0)]}, frozenset({("AAA", EX_DATE)})) == (_state(), [])


def test_a_dividend_sized_difference_is_not_a_split() -> None:
    assert _adjust({"AAA": 1.02}, {}) == (_state(), [])


def test_stored_prices_on_another_scale_with_no_known_split_are_refused() -> None:
    with pytest.raises(PaperRefusedError, match=r"p-test: AAA .* 0\.5"):
        _adjust({"AAA": 2.0}, {})


def test_a_known_split_the_stored_prices_do_not_show_yet_is_refused() -> None:
    with pytest.raises(PaperRefusedError, match="p-test: BBB"):
        _adjust({}, {"BBB": [Split(EX_DATE, 2.0)]})


def test_a_vehicle_split_is_checked_against_the_benchmark() -> None:
    state, events = _adjust({"QQQ": 2.0}, {"QQQ": [Split(EX_DATE, 2.0)]})
    assert (state.vehicle_units, state.vehicle_mark) == (8.0, 150.0)
    assert [e["symbol"] for e in events] == ["QQQ"]
    with pytest.raises(PaperRefusedError, match="p-test: QQQ"):
        _adjust({"QQQ": 2.0}, {})
