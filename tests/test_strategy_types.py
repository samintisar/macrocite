from datetime import date
from typing import get_args

from signalbench.strategy.decision import Decision, SkipReason
from signalbench.strategy.portfolio import PendingEntry, PortfolioState, Position
from signalbench.strategy.readings import NullReadingsView, ReadingsView


def _position(pid: str, symbol: str, sector: str) -> Position:
    return Position(
        id=pid,
        symbol=symbol,
        setup="pullback",
        sector=sector,
        units=1.0,
        entry_price=100.0,
        entry_date=date(2024, 1, 2),
        stop=95.0,
        target=110.0,
        time_limit=10,
        sessions_held=1,
        highest_close=100.0,
    )


def test_null_readings_never_block_tag_or_trigger() -> None:
    readings: ReadingsView = NullReadingsView()
    day = date(2024, 1, 2)
    assert readings.blocked("AAPL", day) is False
    assert readings.catalyst("AAPL", day) is False
    assert readings.sentiment_trigger("AAPL", day) is False


def test_portfolio_counts_positions_and_pending_entries() -> None:
    state = PortfolioState(
        cash=50.0,
        positions=(_position("P1", "AAPL", "Information Technology"),),
        pending=(PendingEntry("MSFT", "breakout", "Information Technology", 20.0),),
        equity=150.0,
        peak=150.0,
        paused=False,
        paused_since=None,
    )
    assert state.holds("AAPL") and state.holds("MSFT") and not state.holds("NVDA")
    assert state.slots_used() == 2
    assert state.sector_count("Information Technology") == 2
    assert state.sector_count("Health Care") == 0
    assert state.uncommitted_cash() == 30.0


def test_decision_defaults_to_empty_lists() -> None:
    assert Decision() == Decision(entries=[], exits=[], stop_updates=[], skips=[])


def test_skip_reasons_cover_the_spec_list() -> None:
    spec = {
        "gap_up",
        "gap_below_stop",
        "no_slot",
        "sector_cap",
        "blocked",
        "paused",
        "earnings_blackout",
        "regime",
    }
    assert spec <= set(get_args(SkipReason))
