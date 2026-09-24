from datetime import date

from pytest import approx

from signalbench.strategy.decision import Skip
from signalbench.strategy.entries import Candidate, Gates, allocate, gate_reason, size
from signalbench.strategy.portfolio import PendingEntry, PortfolioState, Position
from signalbench.strategy.setups import Signal
from strategy_helpers import load_test_config

CONFIG = load_test_config()
TECH = "Information Technology"
OPEN = Gates(held=False, regime_on=True, paused=False, earnings_soon=False, blocked=False)


def _state(cash: float = 100.0, equity: float = 100.0, **kwargs: object) -> PortfolioState:
    fields: dict[str, object] = {
        "cash": cash,
        "positions": (),
        "pending": (),
        "equity": equity,
        "peak": equity,
        "paused": False,
        "paused_since": None,
    }
    fields.update(kwargs)
    return PortfolioState(**fields)


def _candidate(symbol: str, sector: str = TECH, value: float = 1e8, catalyst: bool = False) -> Candidate:
    signal = Signal("pullback", close=100.0, stop=90.0, target_r=2.0, time_limit=10)
    return Candidate(symbol, sector, signal, median_traded_value=value, catalyst=catalyst)


def _held(symbol: str, sector: str) -> Position:
    return Position(
        id=f"P-{symbol}", symbol=symbol, setup="pullback", sector=sector, units=0.1,
        entry_price=100.0, entry_date=date(2024, 1, 2), stop=90.0, target=120.0,
        time_limit=10, sessions_held=1, highest_close=100.0,
    )


def test_each_gate_in_order() -> None:
    assert gate_reason(OPEN) is None
    assert gate_reason(Gates(True, False, True, True, True)) == "held"
    assert gate_reason(Gates(False, False, True, True, True)) == "regime"
    assert gate_reason(Gates(False, True, True, True, True)) == "paused"
    assert gate_reason(Gates(False, True, False, True, True)) == "earnings_blackout"
    assert gate_reason(Gates(False, True, False, False, True)) == "blocked"


def test_size_is_two_percent_risk() -> None:
    # 0.02 * 100 / (100 - 90) = 0.2 units, worth 20 (< 100 / 3)
    assert size(100.0, 90.0, equity=100.0, uncommitted_cash=100.0, config=CONFIG) == approx(0.2)


def test_size_caps_at_a_third_of_equity() -> None:
    # risk alone gives 2 units (worth 200); the cap is 100 / 3 / 100 units
    assert size(100.0, 99.0, equity=100.0, uncommitted_cash=100.0, config=CONFIG) == approx(1 / 3)


def test_size_caps_at_uncommitted_cash() -> None:
    assert size(100.0, 90.0, equity=100.0, uncommitted_cash=10.0, config=CONFIG) == approx(0.1)
    assert size(100.0, 90.0, equity=100.0, uncommitted_cash=-5.0, config=CONFIG) == 0.0


def test_ranking_catalyst_then_traded_value_then_symbol() -> None:
    candidates = [
        _candidate("CCC", "Energy", value=5e8),
        _candidate("BBB", "Health Care", value=1e8),
        _candidate("AAA", "Financials", value=1e8),
        _candidate("ZZZ", "Utilities", value=1e7, catalyst=True),
    ]
    entries, skips = allocate(candidates, _state(), CONFIG)
    assert [(e.symbol, e.rank) for e in entries] == [("ZZZ", 1), ("CCC", 2), ("AAA", 3)]
    assert skips == [Skip("BBB", "pullback", "no_slot")]


def test_sector_cap_counts_held_positions() -> None:
    state = _state(positions=(_held("HELD", TECH),))
    entries, skips = allocate([_candidate("AAA"), _candidate("BBB")], state, CONFIG)
    assert [e.symbol for e in entries] == ["AAA"]
    assert skips == [Skip("BBB", "pullback", "sector_cap")]


def test_pending_entries_take_slots_and_cash() -> None:
    pending = (
        PendingEntry("P1", "pullback", "Energy", planned_cost=30.0),
        PendingEntry("P2", "pullback", "Utilities", planned_cost=60.0),
    )
    state = _state(pending=pending)
    entries, skips = allocate([_candidate("AAA"), _candidate("BBB")], state, CONFIG)
    assert [e.symbol for e in entries] == ["AAA"]
    assert entries[0].units == approx(0.1)  # 100 - 90 committed = 10 cash left
    assert entries[0].risk_amount == approx(0.1 * 10.0)
    assert skips == [Skip("BBB", "pullback", "no_slot")]


def test_accepted_entries_reduce_cash_for_the_next() -> None:
    entries, _ = allocate(
        [_candidate("AAA", "Energy"), _candidate("BBB", "Utilities")], _state(cash=25.0), CONFIG
    )
    assert [e.units for e in entries] == [approx(0.2), approx(0.05)]  # 25 - 20 = 5 left


def test_no_cash_is_a_skip() -> None:
    entries, skips = allocate([_candidate("AAA")], _state(cash=0.0), CONFIG)
    assert entries == []
    assert skips == [Skip("AAA", "pullback", "no_cash")]


def test_a_dust_entry_below_one_percent_of_equity_is_a_no_cash_skip() -> None:
    # 0.99 of cash left buys 0.0099 units at 100: a position worth 0.99% of equity 100.
    entries, skips = allocate([_candidate("AAA")], _state(cash=0.99), CONFIG)
    assert entries == []
    assert skips == [Skip("AAA", "pullback", "no_cash")]
    # Exactly 1% of equity is still an entry.
    entries, skips = allocate([_candidate("AAA")], _state(cash=1.0), CONFIG)
    assert [e.units * e.signal_close for e in entries] == [approx(1.0)]
    assert skips == []
