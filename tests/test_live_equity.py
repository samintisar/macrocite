"""Spec 04: positions (managed and manual), equity, the peak, the pause, and what decide() sees.
The gate's rebuild of positions and cash from fills and corporate actions alone is here too."""

from datetime import date
from decimal import Decimal

import pytest
from sqlmodel import Session, select

from live_helpers import add_pair, add_prices, load_example, make_ledger, send_signal
from signalbench.db.models import EquitySnapshot, LiveRiskState
from signalbench.live.ledger import Ledger, LedgerError
from signalbench.live.levels import StopLevels
from strategy_helpers import weekdays

DAYS = weekdays(date(2026, 6, 1), 16)  # 2026-06-01 (Monday) to 2026-06-22
D, E, F, G, H = DAYS[:5]
US = [200, 201, 204, 202, 160, 204] + [205] * 10  # a fall to 160 on H, then a recovery
CDR = [40, 40.2, 40.8, 40.4] + [40.4] * 12
CDR_VOLUME = [100, 100, 100] + [0] * 13  # traded up to F: the ratio stays 40.8 / 204 = 0.2
DRAWDOWN = Decimal("0.15")


@pytest.fixture
def ledger(session: Session) -> Ledger:
    stock, receipt = add_pair(session)
    add_prices(session, stock, DAYS, US)
    add_prices(session, receipt, DAYS, CDR, volumes=CDR_VOLUME)
    other, other_cdr = add_pair(session, "XOM", "ZXOM", "Energy")
    add_prices(session, other, DAYS, [100] * 16)
    add_prices(session, other_cdr, DAYS, [10] * 16)
    ledger = make_ledger(session, today=DAYS[-1])
    ledger.record_cash(Decimal("100.00"), D, "deposit")
    return ledger


def _managed(ledger: Ledger) -> int:
    signal = send_signal(ledger, D)  # US 200 / stop 188, CDR 40 / stop 37.60
    assert signal.id is not None
    ledger.record_fill(cdr_symbol="ZNVD", side="buy", quantity=Decimal(2),
                       price_cad=Decimal("40.20"), trade_date=E, signal_id=signal.id)
    return signal.id


def test_a_managed_position_carries_its_levels_and_history(ledger: Ledger) -> None:
    signal_id = _managed(ledger)
    [position] = ledger.positions(G)
    assert (position.cdr_symbol, position.us_symbol, position.units, position.acb) == (
        "ZNVD", "NVDA", Decimal(2), Decimal("80.40")
    )
    assert position.mark == Decimal("40.40")  # 202 x 0.2
    assert (position.entry_session, position.sessions_held) == (E, 3)
    assert (position.stop, position.entry_us, position.highest_close) == (
        StopLevels(Decimal(188), Decimal("37.60")), Decimal("201.00"), 204.0
    )
    strategy = position.strategy_position()
    assert (strategy.id, strategy.symbol, strategy.setup, strategy.stop, strategy.entry_price) == (
        str(signal_id), "NVDA", "breakout", 188.0, 201.0
    )
    assert (strategy.target, strategy.time_limit, strategy.sessions_held) == (None, None, 3)
    assert ledger.positions(D) == []  # bought on E


def test_a_manual_position_has_no_stop_but_holds_a_slot_and_its_sector(ledger: Ledger) -> None:
    fill = ledger.record_fill(cdr_symbol="ZXOM", side="buy", quantity=Decimal(3),
                              price_cad=Decimal(10), trade_date=date(2026, 6, 6))  # a Saturday
    [position] = ledger.positions(DAYS[5])
    assert (position.managed, position.stop, position.entry_us) == (False, None, None)
    assert position.entry_session == date(2026, 6, 8)  # the first NYSE session on or after it
    strategy = position.strategy_position()
    assert (strategy.id, strategy.stop, strategy.entry_price, strategy.highest_close) == (
        f"manual-{fill.id}", 0.0, 0.0, 0.0
    )
    state = ledger.portfolio_state(DAYS[5])
    assert (state.slots_used(), state.sector_count("Energy")) == (1, 1)


def test_equity_is_cash_plus_units_at_the_cdr_mark(ledger: Ledger) -> None:
    _managed(ledger)
    snapshot = ledger.equity(G)
    assert (snapshot.cash, snapshot.positions_value, snapshot.equity, snapshot.peak) == (
        Decimal("19.60"), Decimal("80.80"), Decimal("100.40"), Decimal("100.40")
    )


def test_a_drawdown_pauses_until_resume_whatever_auto_resume_sessions_says(
    ledger: Ledger, session: Session
) -> None:
    _managed(ledger)
    paused_on = []
    for day in DAYS:
        snapshot, paused_now = ledger.record_equity(day, pause_drawdown=DRAWDOWN)
        if paused_now:
            paused_on.append((day, snapshot.equity, snapshot.peak))
    # H: 19.60 + 2 x (160 x 0.2) = 83.60 < 0.85 x the peak 101.20 (F: 19.60 + 2 x 40.80)
    assert paused_on == [(H, Decimal("83.60"), Decimal("101.20"))]
    risk = ledger.risk_state()
    assert (risk.paused, risk.paused_at) == (True, H)  # 11 recovered sessions later: still paused
    assert ledger.portfolio_state(DAYS[-1]).paused
    assert len(session.exec(select(EquitySnapshot)).all()) == 16
    ledger.resume()
    risk = ledger.risk_state()
    assert (risk.paused, risk.peak_reset_on) == (False, DAYS[-1])
    snapshot, paused_now = ledger.record_equity(DAYS[-1], pause_drawdown=DRAWDOWN)
    assert (snapshot.peak, paused_now) == (snapshot.equity, False)  # the peak starts again
    with pytest.raises(LedgerError, match="not paused"):
        ledger.resume()


def test_a_rerun_of_a_night_replaces_its_snapshot(ledger: Ledger, session: Session) -> None:
    ledger.record_equity(D, pause_drawdown=DRAWDOWN)
    ledger.record_cash(Decimal("5.00"), D, "a late deposit")
    ledger.record_equity(D, pause_drawdown=DRAWDOWN)
    [snapshot] = session.exec(select(EquitySnapshot)).all()
    assert snapshot.equity == Decimal("105.00")
    assert session.get(LiveRiskState, 1) is not None


def test_decide_sees_positions_pending_signals_cash_and_the_pause(ledger: Ledger) -> None:
    _managed(ledger)
    pending = send_signal(ledger, F, us="XOM", cdr="ZXOM", us_close="100", us_stop="95",
                          cdr_close="10", units="3")
    state = ledger.portfolio_state(G)  # the XOM signal from F expires at G's close
    assert [p.symbol for p in state.positions] == ["NVDA"]
    assert [(p.symbol, p.sector, p.planned_cost) for p in state.pending] == [
        ("XOM", "Energy", 30.3)  # 3 units at the C$10.10 limit
    ]
    assert (state.slots_used(), state.cash, state.equity, state.paused) == (2, 19.6, 100.4, False)
    assert pending.id is not None
    ledger.mark_signal(pending.id, "expired")
    assert ledger.portfolio_state(G).slots_used() == 1


def test_positions_and_cash_rebuild_from_fills_and_corporate_actions_alone(
    session: Session,
) -> None:
    example = load_example("rebuild")
    add_pair(session, "AAA", "ZAAA")
    add_pair(session, "BBB", "ZBBB")
    ledger = make_ledger(session)
    for movement in example["cash"]:
        ledger.record_cash(Decimal(movement["amount"]), movement["date"], "movement")

    def fill(row: dict[str, str]) -> None:
        made = ledger.record_fill(
            cdr_symbol=row["symbol"], side=row["side"], quantity=Decimal(row["quantity"]),
            price_cad=Decimal(row["price"]), trade_date=row["date"],
        )
        if "void" in row:
            assert made.id is not None
            ledger.void_fill(made.id, row["void"])

    for row in example["fills"]:
        fill(row)
    for split in example["splits"]:
        ledger.record_split(kind="cdr_split", symbol=split["symbol"], ex_date=split["date"],
                            ratio=Decimal(split["ratio"]), source="owner")
    for row in example["after_splits"]:
        fill(row)
    expect = example["expect"]
    assert ledger.cash() == Decimal(expect["cash"])
    books = ledger.books()
    assert {symbol: (book.units, book.acb) for symbol, book in books.items()} == {
        symbol: (Decimal(p["units"]), Decimal(p["acb"])) for symbol, p in expect["positions"].items()
    }
    assert [p.cdr_symbol for p in ledger.positions(date(2026, 6, 30))] == ["ZAAA", "ZBBB"]


def test_the_peak_moves_with_deposits_and_withdrawals(ledger: Ledger) -> None:
    ledger.record_equity(D, pause_drawdown=DRAWDOWN)  # C$100, the peak
    ledger.record_cash(Decimal("-30.00"), E, "withdrawal")
    snapshot, paused_now = ledger.record_equity(E, pause_drawdown=DRAWDOWN)
    assert (snapshot.equity, snapshot.peak, paused_now) == (Decimal(70), Decimal(70), False)
    ledger.record_cash(Decimal("50.00"), F, "deposit")  # the peak rises with it, then falls again
    ledger.record_cash(Decimal("-50.00"), G, "withdrawal")
    snapshot, paused_now = ledger.record_equity(G, pause_drawdown=DRAWDOWN)
    assert (snapshot.equity, snapshot.peak, paused_now) == (Decimal(70), Decimal(70), False)
