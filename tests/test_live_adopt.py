"""Every holding gets a stop: a buy with no signal is linked to a recent scan signal, or gets the
Breakout stop the strategy would have set at the session before the buy (an adopted signal).

On the scan world every stock is flat with ATR(14) exactly 2, so XOM (flat at US$100, its CDR
at C$10.00) adopts a stop of 100 - 2 x 2 = US$96, C$9.60."""

from decimal import Decimal
from pathlib import Path

import pytest
from sqlmodel import Session, select

from scan_helpers import B, DAYS, LIVE, NVDA, UNIVERSE, World, make_world
from signalbench.db.models import Fill, TradeSignal
from signalbench.live.adopt import Adopter, adopt_fill
from signalbench.live.book import LedgerError
from signalbench.strategy.config import load_strategy_config
from strategy_helpers import WeekdaySessions


def adopter(world: World) -> Adopter:
    config = load_strategy_config(world.repo / LIVE)[0]
    return Adopter(config=config, universe=UNIVERSE, calendar=WeekdaySessions())


def manual_buy(world: World, index: int, cdr: str = "ZXOM", today: int = B + 1) -> Fill:
    return world.ledger(today).record_fill(
        cdr_symbol=cdr, side="buy", quantity=Decimal("3"), price_cad=Decimal("10.20"),
        trade_date=DAYS[index],
    )


def test_an_unsignalled_buy_gets_close_minus_2_atr_at_the_session_before(
    session: Session, tmp_path: Path
) -> None:
    world = make_world(session, tmp_path)
    signal = adopter(world).signal_for_buy(
        session, world.ledger(B + 1), "ZXOM", DAYS[B + 1], Decimal("3")
    )
    assert (signal.source, signal.status, signal.as_of) == ("adopted", "sent", DAYS[B])
    assert (signal.us_signal_close, signal.us_stop) == (Decimal("100.0000"), Decimal("96.0000"))
    assert (signal.cdr_signal_close, signal.cdr_stop) == (Decimal("10.0000"), Decimal("9.6000"))
    assert signal.telegram_message_id is None


def test_a_buy_with_a_recent_scan_signal_links_to_it(session: Session, tmp_path: Path) -> None:
    world = make_world(session, tmp_path)
    world.scan(B)  # the NVDA entry
    scan_signal = session.exec(select(TradeSignal)).one()
    found = adopter(world).signal_for_buy(
        session, world.ledger(B + 1), "ZNVD", DAYS[B + 1], Decimal("3")
    )
    assert found.id == scan_signal.id
    assert len(session.exec(select(TradeSignal)).all()) == 1


def test_a_stock_outside_the_universe_gets_no_stop(session: Session, tmp_path: Path) -> None:
    world = make_world(session, tmp_path)
    small = Adopter(config=adopter(world).config, universe=UNIVERSE[:1], calendar=WeekdaySessions())
    with pytest.raises(LedgerError, match="ZXOM is not in the universe"):
        small.signal_for_buy(session, world.ledger(B + 1), "ZXOM", DAYS[B + 1], Decimal("3"))


def test_adopt_fill_voids_a_manual_opening_buy_and_records_it_linked(
    session: Session, tmp_path: Path
) -> None:
    world = make_world(session, tmp_path)
    manual = manual_buy(world, B - 3)
    ledger = world.ledger(B + 1)
    assert manual.id is not None
    result = adopt_fill(session, ledger, adopter(world), manual.id, trade_date=DAYS[B - 2])
    assert result.old_fill_id == manual.id
    assert (result.fill.signal_id, result.fill.trade_date) == (result.signal.id, DAYS[B - 2])
    assert (result.signal.status, result.signal.as_of) == ("taken", DAYS[B - 3])
    assert result.breaches == []
    assert session.get(Fill, manual.id).voided  # type: ignore[union-attr]
    [position] = ledger.positions(DAYS[B + 1])
    assert position.managed
    assert position.stop is not None and position.stop.cdr == Decimal("9.6000")
    assert ledger.cash() == Decimal("100.00") - Decimal("30.60")


def test_adopt_fill_lists_the_sessions_that_closed_at_or_below_the_stop(
    session: Session, tmp_path: Path
) -> None:
    world = make_world(session, tmp_path, closes={"NVDA": NVDA, "XOM": {B: 90.0}})
    manual = manual_buy(world, B - 5)
    assert manual.id is not None
    result = adopt_fill(session, world.ledger(B + 1), adopter(world), manual.id)
    assert result.breaches == [DAYS[B], DAYS[B + 1]]


def test_adopt_fill_links_a_recent_scan_signal(session: Session, tmp_path: Path) -> None:
    world = make_world(session, tmp_path)
    world.scan(B)
    scan_signal = session.exec(select(TradeSignal)).one()
    manual = manual_buy(world, B + 1, cdr="ZNVD")
    assert manual.id is not None
    result = adopt_fill(session, world.ledger(B + 1), adopter(world), manual.id)
    assert result.signal.id == scan_signal.id
    assert result.signal.source == "scan"


def test_only_a_held_manual_opening_buy_can_be_adopted(session: Session, tmp_path: Path) -> None:
    world = make_world(session, tmp_path)
    ledger = world.ledger(B + 1)
    manual = manual_buy(world, B - 3)
    assert manual.id is not None
    adopt_fill(session, ledger, adopter(world), manual.id)
    with pytest.raises(LedgerError, match=f"fill {manual.id} is not a manual buy"):
        adopt_fill(session, ledger, adopter(world), manual.id)  # now voided
    linked = session.exec(select(Fill).where(Fill.voided == False)).one()  # noqa: E712
    assert linked.id is not None
    with pytest.raises(LedgerError, match="is not a manual buy"):
        adopt_fill(session, ledger, adopter(world), linked.id)


def test_a_failed_adoption_puts_the_original_buy_back_and_withdraws_the_signal(
    session: Session, tmp_path: Path
) -> None:
    world = make_world(session, tmp_path)
    ledger = world.ledger(B + 1)
    manual = manual_buy(world, B - 3)
    assert manual.id is not None
    with pytest.raises(LedgerError, match="after today"):
        adopt_fill(session, ledger, adopter(world), manual.id, trade_date=DAYS[B + 5])
    [position] = ledger.positions(DAYS[B + 1])
    assert (position.managed, position.units) == (False, Decimal("3"))
    assert position.episode.opening.trade_date == DAYS[B - 3]
    assert session.exec(select(TradeSignal)).one().status == "withdrawn"
