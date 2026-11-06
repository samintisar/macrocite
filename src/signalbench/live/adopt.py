"""Every holding gets a stop: an opening buy with no signal is linked to the scan's own signal
for that CDR from the last few sessions, or to an adopted signal with the Breakout stop the
strategy would have set at the session before the buy (close - stop_atr_mult x ATR).

An adopted signal is an ordinary trade_signals row with source `adopted`, so stops, raises,
and exit alerts work on it unchanged. It is never sent, and the scale-up check leaves it out.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlmodel import Session, col, select

from signalbench.db.models import Fill, Price, Ticker, TickerKind, TradeSignal
from signalbench.ingest.cdr import CdrEntry
from signalbench.live.book import NEW_YORK, LedgerError, q4
from signalbench.live.ledger import Ledger
from signalbench.live.review import live_market
from signalbench.market.calendar import Sessions
from signalbench.strategy.config import StrategyConfig


@dataclass(frozen=True)
class Adopter:
    """What it takes to work out a Breakout stop: the live config, the universe, and the NYSE
    calendar."""

    config: StrategyConfig
    universe: list[CdrEntry]
    calendar: Sessions

    def signal_for_buy(
        self, session: Session, ledger: Ledger, cdr_symbol: str, trade_date: date,
        quantity: Decimal,
    ) -> TradeSignal:
        """The scan signal this buy belongs to (Ledger.matching_signal), or a new adopted one,
        written `sent` until the buy is recorded against it."""
        found = ledger.matching_signal(cdr_symbol, trade_date)
        if found is not None:
            return found
        return self._adopt(session, ledger, cdr_symbol, trade_date, quantity)

    def _session_before(self, day: date) -> date:
        before = self.calendar.sessions_between(day - timedelta(days=10), day - timedelta(days=1))
        if not before:
            raise LedgerError(f"no NYSE session in the 10 days before {day}")
        return before[-1]

    def _adopt(
        self, session: Session, ledger: Ledger, cdr_symbol: str, trade_date: date,
        quantity: Decimal,
    ) -> TradeSignal:
        entry = next((e for e in self.universe if e.cdr_symbol == cdr_symbol), None)
        if entry is None:
            raise LedgerError(f"{cdr_symbol} is not in the universe, so no stop can be set")
        day = self._session_before(trade_date)
        market = live_market(session, [entry], self.config, self.calendar, day)
        snap = market.at(day).snapshot(entry.us_symbol)
        if snap is None or snap.atr is None or snap.date != day:
            raise LedgerError(f"no {entry.us_symbol} bar with an ATR on {day}, so no stop")
        mult = self.config.breakout.stop_atr_mult
        us_close = q4(Decimal(repr(snap.close)))
        us_stop = q4(Decimal(repr(snap.close - mult * snap.atr)))
        cdr = session.exec(
            select(Ticker).where(Ticker.symbol == cdr_symbol, Ticker.kind == TickerKind.cdr)
        ).one()
        ratio = ledger.traded_ratio(cdr, day)
        if ratio is None:
            raise LedgerError(f"{cdr_symbol} has no traded CDR price by {day}, so no stop")
        cdr_close = q4(us_close * ratio)
        return ledger.record_signal(
            as_of=day, us_symbol=entry.us_symbol, cdr_symbol=cdr_symbol,
            us_signal_close=us_close, us_stop=us_stop, cdr_signal_close=cdr_close,
            suggested_units=quantity, order_type="market",
            risk_amount_cad=q4(quantity * cdr_close * (us_close - us_stop) / us_close),
            explanation=(
                f"Adopted: a buy with no signal. Breakout stop = the {day} close − "
                f"{mult:g} × ATR({self.config.atr_period})."
            ),
            expires_at=datetime.combine(trade_date, time(16), tzinfo=NEW_YORK),
            source="adopted",
        )


@dataclass(frozen=True)
class Adopted:
    old_fill_id: int
    fill: Fill
    signal: TradeSignal
    breaches: list[date]  # sessions since the entry whose US close was at or below the stop


def adopt_fill(
    session: Session, ledger: Ledger, adopter: Adopter, fill_id: int,
    *, trade_date: date | None = None,
) -> Adopted:
    """Give an existing manual holding a stop: void its opening buy and record it again, linked
    (optionally on a corrected trade date). Fills are never edited, only voided. If the new
    record fails, the original buy is recorded again unlinked, so the holding never vanishes,
    and an adopted signal made for it is discarded."""
    old = session.get(Fill, fill_id)
    if old is None or old.voided or old.side != "buy" or old.signal_id is not None:
        raise LedgerError(f"fill {fill_id} is not a manual buy")
    episode = next(
        (e for e in ledger.open_episodes() if e.opening.id == fill_id), None
    )
    if episode is None:
        raise LedgerError(f"fill {fill_id} does not open a position that is still held")
    cdr = session.get(Ticker, old.cdr_ticker_id)
    assert cdr is not None and old.id is not None
    original = {
        "cdr_symbol": cdr.symbol, "side": "buy", "quantity": old.quantity,
        "price_cad": old.price_cad, "trade_date": old.trade_date, "fee_cad": old.fee_cad,
    }
    day = old.trade_date if trade_date is None else trade_date
    signal = adopter.signal_for_buy(session, ledger, cdr.symbol, day, old.quantity)
    assert signal.id is not None
    voided = False
    try:
        ledger.void_fill(fill_id, f"adopted: re-recorded linked to signal {signal.id}")
        voided = True
        fill = ledger.record_fill(
            cdr_symbol=cdr.symbol, side="buy", quantity=old.quantity, price_cad=old.price_cad,
            trade_date=day, signal_id=signal.id, fee_cad=old.fee_cad, force=True,
        )
    except LedgerError:
        session.rollback()
        if voided:
            ledger.record_fill(**original, force=True)  # type: ignore[arg-type]
        if signal.source == "adopted":
            ledger.discard_adopted(signal.id)
        raise
    return Adopted(old.id, fill, signal, _breaches(session, ledger, signal.id, day))


def _breaches(session: Session, ledger: Ledger, signal_id: int, trade_date: date) -> list[date]:
    """Sessions from the entry through today whose stored US close was at or below the stop in
    force that session: the strategy's stop exit (strategy/exits.py: close <= stop)."""
    signal = ledger.signal(signal_id)
    us = session.exec(
        select(Ticker).where(Ticker.symbol == signal.us_symbol, Ticker.kind == TickerKind.us_stock)
    ).one()
    prices = session.exec(
        select(Price)
        .where(
            Price.ticker_id == us.id, col(Price.date) >= trade_date,
            col(Price.date) <= ledger.today,
        )
        .order_by(col(Price.date))
    ).all()
    return [
        p.date for p in prices if Decimal(p.adj_close) <= ledger.stop_in_force(signal_id, p.date).us
    ]
