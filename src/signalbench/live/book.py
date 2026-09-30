"""Fills, cash, voids, signals, and exit alerts: the ledger's first layer (spec 04).

Nothing is typed in as a total: cash and each CDR's units and ACB are replayed from fills,
cash movements, and CDR splits. Fills are never deleted (a mistake is voided). Money is CAD
and Decimal throughout.

Ids are integers: the owner types `/void 12`, and Telegram button data is capped at 64 bytes.
"""


import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal
from uuid import UUID

from sqlmodel import Session, col, select

from signalbench.db.models import (
    CashMovement,
    CorporateAction,
    ExitAlert,
    Fill,
    Ticker,
    TickerKind,
    TradeSignal,
)
from signalbench.live.acb import (
    AcbBook,
    AcbError,
    Side,
    SplitEvent,
    Trade,
    ordered,
    replay,
)
from signalbench.market.calendar import Sessions

SignalStatus = Literal["sent", "taken", "skipped", "expired", "withdrawn"]
SignalSkip = Literal["disagree", "no_time", "price_moved", "wide_spread", "other"]
AlertReason = Literal["stop", "earnings"]
AlertStatus = Literal["sent", "done", "ignored"]
SplitKind = Literal["us_split", "cdr_split"]
SplitSource = Literal["yfinance", "owner"]
OrderType = Literal["limit", "market"]

QUANTITY_STEP = Decimal("0.000001")  # units: 6 decimals, as stored
PRICE_STEP = Decimal("0.0001")  # prices, fees, and stops: 4 decimals, as stored
CENT = Decimal("0.01")
ZERO = Decimal(0)

log = logging.getLogger(__name__)


class LedgerError(ValueError):
    """A ledger write or read was refused. The message says why, for the owner."""


def q4(value: Decimal) -> Decimal:
    """A price or stop on the stored 4-decimal scale."""
    return value.quantize(PRICE_STEP, rounding=ROUND_HALF_UP)


def cad(value: Decimal) -> str:
    return f"C${value.quantize(CENT, rounding=ROUND_HALF_UP)}"


def check_places(value: Decimal, places: int, name: str) -> None:
    if value != value.quantize(Decimal(1).scaleb(-places)):
        raise LedgerError(f"{name} {value} has more than {places} decimals")


def check_choice(value: str, choices: tuple[str, ...], name: str) -> None:
    if value not in choices:
        raise LedgerError(f"{name} must be one of {', '.join(choices)}, not {value!r}")


@dataclass(frozen=True)
class Episode:
    """One position in one CDR: from the buy that took the units above 0 to the sale that took
    them back to 0. Managed when that opening buy is linked to a signal."""

    cdr_ticker_id: UUID
    fills: tuple[Fill, ...]  # non-voided, in replay order
    closed_on: date | None  # None while open

    @property
    def opening(self) -> Fill:
        return self.fills[0]

    @property
    def signal_id(self) -> int | None:
        return self.opening.signal_id

    @property
    def managed(self) -> bool:
        return self.signal_id is not None

    @property
    def key(self) -> str:
        """The position id decide() sees: the signal id, or manual-<opening fill id>."""
        return str(self.signal_id) if self.managed else f"manual-{self.opening.id}"

    def pnl(self) -> Decimal:
        """Cash in minus cash out over the episode, fees included (the realized P&L once
        closed). Splits move no cash, so they never change it."""
        return sum((_cash_effect(fill) for fill in self.fills), ZERO)


def episodes(fills: Sequence[Fill], splits: Sequence[CorporateAction]) -> list[Episode]:
    """One CDR's fills cut into positions: each starts at a buy from 0 units and ends at the
    sale back to 0. `fills` are non-voided; `splits` non-voided CDR splits."""
    by_id = {fill.id: fill for fill in fills}
    found: list[Episode] = []
    current: list[Fill] = []
    units = ZERO
    for event in ordered([_trade(f) for f in fills], [_split_event(a) for a in splits]):
        if isinstance(event, SplitEvent):
            units *= event.ratio
            continue
        fill = by_id[event.id]
        current.append(fill)
        units += fill.quantity if fill.side == "buy" else -fill.quantity
        if units <= 0:
            found.append(Episode(fill.cdr_ticker_id, tuple(current), fill.trade_date))
            current, units = [], ZERO
    if current:
        found.append(Episode(current[0].cdr_ticker_id, tuple(current), None))
    return found


def _trade(fill: Fill) -> Trade:
    assert fill.id is not None
    side: Side = "buy" if fill.side == "buy" else "sell"
    return Trade(fill.id, fill.trade_date, side, fill.quantity, fill.price_cad, fill.fee_cad)


def _split_event(action: CorporateAction) -> SplitEvent:
    assert action.id is not None
    return SplitEvent(action.id, action.ex_date, action.ratio)


def _cash_effect(fill: Fill) -> Decimal:
    amount = fill.quantity * fill.price_cad
    return amount - fill.fee_cad if fill.side == "sell" else -(amount + fill.fee_cad)


class LedgerBook:
    """Fills, cash, voids, signals, and exit alerts. `today` is the owner's date (New York).
    `Ledger` (live/ledger.py) is the class to use: it adds stops, splits, and equity."""

    def __init__(self, session: Session, *, calendar: Sessions, today: date) -> None:
        self._session = session
        self._calendar = calendar
        self.today = today

    # --- Lookups ---------------------------------------------------------------------------

    def _ticker(self, symbol: str, kind: TickerKind) -> Ticker:
        ticker = self._session.exec(
            select(Ticker).where(Ticker.symbol == symbol, Ticker.kind == kind)
        ).first()
        if ticker is None:
            what = "CDR" if kind == TickerKind.cdr else "US stock"
            raise LedgerError(f"{symbol} is not a known {what}")
        return ticker

    def _cdr(self, symbol: str) -> Ticker:
        return self._ticker(symbol, TickerKind.cdr)

    def _us_of(self, cdr: Ticker) -> Ticker:
        us = None if cdr.us_ticker_id is None else self._session.get(Ticker, cdr.us_ticker_id)
        if us is None:
            raise LedgerError(f"{cdr.symbol} has no US stock in the universe")
        return us

    def _by_id(self, ticker_id: UUID) -> Ticker:
        ticker = self._session.get(Ticker, ticker_id)
        if ticker is None:
            raise LedgerError(f"no ticker {ticker_id}")
        return ticker

    def signal(self, signal_id: int) -> TradeSignal:
        signal = self._session.get(TradeSignal, signal_id)
        if signal is None:
            raise LedgerError(f"no signal {signal_id}")
        return signal

    def exit_alert(self, alert_id: int) -> ExitAlert:
        alert = self._session.get(ExitAlert, alert_id)
        if alert is None:
            raise LedgerError(f"no exit alert {alert_id}")
        return alert

    def _fills(self, cdr_id: UUID | None = None, through: date | None = None) -> list[Fill]:
        """Non-voided fills in replay order (trade date, then id)."""
        query = select(Fill).where(col(Fill.voided).is_(False))
        if cdr_id is not None:
            query = query.where(Fill.cdr_ticker_id == cdr_id)
        if through is not None:
            query = query.where(col(Fill.trade_date) <= through)
        return list(self._session.exec(query.order_by(col(Fill.trade_date), col(Fill.id))).all())

    def _actions(
        self, kind: SplitKind, *, us_symbol: str | None = None, cdr_id: UUID | None = None
    ) -> list[CorporateAction]:
        """Non-voided splits of one symbol, oldest ex-date first."""
        query = select(CorporateAction).where(
            CorporateAction.kind == kind, col(CorporateAction.voided).is_(False)
        )
        if us_symbol is not None:
            query = query.where(CorporateAction.us_symbol == us_symbol)
        if cdr_id is not None:
            query = query.where(CorporateAction.cdr_ticker_id == cdr_id)
        return list(
            self._session.exec(
                query.order_by(col(CorporateAction.ex_date), col(CorporateAction.id))
            ).all()
        )

    # --- Cash ------------------------------------------------------------------------------

    def record_cash(self, amount_cad: Decimal, occurred_on: date, note: str) -> CashMovement:
        """A deposit (+) or a withdrawal (-)."""
        if amount_cad == 0:
            raise LedgerError("a cash movement cannot be 0")
        check_places(amount_cad, 2, "amount")
        if occurred_on > self.today:
            raise LedgerError(f"{occurred_on} is after today ({self.today})")
        movement = CashMovement(amount_cad=amount_cad, occurred_on=occurred_on, note=note)
        self._session.add(movement)
        self._session.commit()
        self._session.refresh(movement)
        return movement

    def _movements(self, through: date | None = None) -> list[CashMovement]:
        query = select(CashMovement)
        if through is not None:
            query = query.where(col(CashMovement.occurred_on) <= through)
        return list(
            self._session.exec(
                query.order_by(col(CashMovement.occurred_on), col(CashMovement.id))
            ).all()
        )

    def cash(self, as_of: date | None = None) -> Decimal:
        """Deposits - withdrawals + sell proceeds - buy costs - fees, through `as_of`."""
        total = sum((m.amount_cad for m in self._movements(as_of)), ZERO)
        return total + sum((_cash_effect(f) for f in self._fills(through=as_of)), ZERO)

    def _lowest_cash_from(self, fill: Fill) -> Decimal:
        """The lowest running cash from `fill` on, over every movement and fill in date order
        (a movement before the fills of its date)."""
        events: list[tuple[date, int, int, Decimal]] = [
            (m.occurred_on, 0, m.id or 0, m.amount_cad) for m in self._movements()
        ]
        events += [(f.trade_date, 1, f.id or 0, _cash_effect(f)) for f in self._fills()]
        running, lowest, seen = ZERO, None, False
        for day, kind, key, amount in sorted(events):
            running += amount
            seen = seen or (kind == 1 and key == fill.id)
            if seen:
                lowest = running if lowest is None else min(lowest, running)
        return ZERO if lowest is None else lowest

    # --- Fills -----------------------------------------------------------------------------

    def record_fill(
        self,
        *,
        cdr_symbol: str,
        side: Side,
        quantity: Decimal,
        price_cad: Decimal,
        trade_date: date,
        signal_id: int | None = None,
        exit_alert_id: int | None = None,
        fee_cad: Decimal = ZERO,
        force: bool = False,
    ) -> Fill:
        """One buy or sell, validated (spec 04, Validation). A buy linked to a signal marks it
        taken; a sale linked to an exit alert marks it done. `force` records a buy the cash on
        hand does not cover, because Wealthsimple is the source of truth; it is logged."""
        cdr = self._cdr(cdr_symbol)
        check_choice(side, ("buy", "sell"), "side")
        for value, places, name in (
            (quantity, 6, "quantity"), (price_cad, 4, "price"),
        ):
            if value <= 0:
                raise LedgerError(f"{name} must be above 0, not {value}")
            check_places(value, places, name)
        if fee_cad < 0:
            raise LedgerError(f"fee cannot be negative, not {fee_cad}")
        check_places(fee_cad, 4, "fee")
        if trade_date > self.today:
            raise LedgerError(f"trade date {trade_date} is after today ({self.today})")
        movements = self._movements()
        if not movements:
            raise LedgerError("record the first deposit before any fill")
        if trade_date < movements[0].occurred_on:
            raise LedgerError(
                f"trade date {trade_date} is before the first cash movement "
                f"({movements[0].occurred_on})"
            )
        signal = None if signal_id is None else self._linkable_signal(signal_id, cdr, side)
        alert = None if exit_alert_id is None else self._linkable_alert(exit_alert_id, cdr, side)
        fill = Fill(
            cdr_ticker_id=cdr.id, side=side, quantity=quantity, price_cad=price_cad,
            fee_cad=fee_cad, trade_date=trade_date, signal_id=signal_id,
            exit_alert_id=exit_alert_id,
        )
        self._session.add(fill)
        self._session.flush()
        try:
            self._check_book(cdr)
            if side == "buy":
                lowest = self._lowest_cash_from(fill)
                if lowest < 0 and not force:
                    raise LedgerError(
                        f"a buy of {cad(quantity * price_cad + fee_cad)} on {trade_date} "
                        f"takes cash to {cad(lowest)}; there is no margin. If Wealthsimple "
                        "shows the fill, record it with force"
                    )
                if lowest < 0:
                    fill.forced = True
                    log.warning(
                        "forced buy: %s %s x %s on %s takes cash to %s",
                        cdr.symbol, quantity, price_cad, trade_date, cad(lowest),
                    )
        except LedgerError:
            self._session.rollback()
            raise
        if signal is not None and signal.status in ("sent", "expired"):
            signal.status = "taken"
            self._session.add(signal)
        if alert is not None:
            alert.status = "done"
            self._session.add(alert)
        self._session.commit()
        self._session.refresh(fill)
        return fill

    def _linkable_signal(self, signal_id: int, cdr: Ticker, side: str) -> TradeSignal:
        signal = self.signal(signal_id)
        if side != "buy":
            raise LedgerError("only a buy can be linked to a signal")
        if signal.cdr_ticker_id != cdr.id:
            raise LedgerError(f"signal {signal_id} is for another CDR, not {cdr.symbol}")
        if signal.status in ("skipped", "withdrawn"):
            raise LedgerError(
                f"signal {signal_id} is {signal.status}; record the buy without the signal"
            )
        return signal

    def _linkable_alert(self, alert_id: int, cdr: Ticker, side: str) -> ExitAlert:
        alert = self.exit_alert(alert_id)
        if side != "sell":
            raise LedgerError("only a sale can be linked to an exit alert")
        if alert.cdr_ticker_id != cdr.id:
            raise LedgerError(f"exit alert {alert_id} is for another CDR, not {cdr.symbol}")
        return alert

    def void_fill(self, fill_id: int, reason: str) -> None:
        """Void a fill: its cash and position effects are gone from every derivation."""
        fill = self._session.get(Fill, fill_id)
        if fill is None or fill.voided:
            raise LedgerError(f"no fill {fill_id} to void")
        if not reason.strip():
            raise LedgerError("a void needs a reason")
        fill.voided = True
        fill.void_reason = reason.strip()
        self._session.add(fill)
        self._session.flush()
        try:
            self._check_book(self._by_id(fill.cdr_ticker_id))
        except LedgerError as error:
            self._session.rollback()
            raise LedgerError(
                f"voiding fill {fill_id} breaks the ledger ({error}); record the corrected "
                "fill first, then void this one"
            ) from None
        self._session.commit()

    def _check_book(self, cdr: Ticker) -> None:
        """No sale of more than is held, and every signal-linked buy opens its signal's position
        or adds to it (a signal opens at most one position)."""
        fills = self._fills(cdr.id)
        splits = self._actions("cdr_split", cdr_id=cdr.id)
        try:
            self._book(fills, splits)
        except AcbError as error:
            raise LedgerError(f"{cdr.symbol}: {error}") from None
        opened: set[int] = set()
        for episode in episodes(fills, splits):
            for fill in episode.fills:
                if fill.signal_id is not None and fill.signal_id != episode.signal_id:
                    raise LedgerError(
                        f"{cdr.symbol}: the buy linked to signal {fill.signal_id} adds to a "
                        f"position it did not open ({episode.key}); record it without the signal"
                    )
            if episode.signal_id is not None:
                if episode.signal_id in opened:
                    raise LedgerError(
                        f"{cdr.symbol}: signal {episode.signal_id} already opened a position "
                        "that has closed; record the buy without the signal"
                    )
                opened.add(episode.signal_id)

    def _book(self, fills: Sequence[Fill], splits: Sequence[CorporateAction]) -> AcbBook:
        return replay([_trade(f) for f in fills], [_split_event(a) for a in splits], self.today)

    def _held_cdr_ids(self) -> list[UUID]:
        return sorted({f.cdr_ticker_id for f in self._fills()}, key=str)

    def books(self) -> dict[str, AcbBook]:
        """Each CDR's whole ACB replay, by CDR symbol."""
        return {
            self._by_id(cdr_id).symbol: self._book(
                self._fills(cdr_id), self._actions("cdr_split", cdr_id=cdr_id)
            )
            for cdr_id in self._held_cdr_ids()
        }
