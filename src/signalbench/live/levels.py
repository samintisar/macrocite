"""Prices, US-equivalent levels, stops, and splits: the ledger's second layer (spec 04).

Decisions use the US levels, as in the backtest; the CDR levels are for display. A managed
position's stop is its signal's `us_stop` until `stop_updates` rows move it: a `trail` row only
raises it, and a `split` row rescales it. The tool never rescales the owner's fills or ACB: a
US split rescales only its own levels, and a CDR split multiplies the units held.
"""


from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlmodel import col, select

from signalbench.db.models import (
    CorporateAction,
    Price,
    StopUpdateRow,
    Ticker,
    TradeSignal,
)
from signalbench.live.book import LedgerBook, LedgerError, q4


@dataclass(frozen=True)
class StopLevels:
    """A managed position's stop: the US level decides, the CDR level is for display."""

    us: Decimal
    cdr: Decimal


@dataclass(frozen=True)
class SignalLevels:
    """A signal's prices on today's scale: US prices / the recorded US splits after its as_of,
    CDR prices / the recorded CDR splits after it. The stored row is never rewritten."""

    us_signal_close: Decimal
    us_stop: Decimal
    cdr_signal_close: Decimal
    cdr_stop: Decimal


def _split_step(ratio: Decimal, old: Decimal, new: Decimal) -> Decimal:
    """The factor a `split` row applies: 1/ratio, or ratio for the row that undoes a voided
    split (the row moves the level the other way)."""
    applies = (new < old) == (ratio > 1)
    return 1 / ratio if applies else ratio


class LedgerLevels(LedgerBook):
    """Prices, levels, stops, and splits, on top of the fills and cash of LedgerBook."""

    # --- Prices ----------------------------------------------------------------------------

    def _prices(self, ticker_id: UUID, through: date) -> list[Price]:
        return list(
            self._session.exec(
                select(Price)
                .where(Price.ticker_id == ticker_id, col(Price.date) <= through)
                .order_by(col(Price.date))
            ).all()
        )

    def _closes(self, cdr: Ticker, through: date) -> tuple[list[Price], dict[date, Decimal]]:
        """The CDR's stored bars and its US stock's raw closes by date, through `through`."""
        us = self._us_of(cdr)
        return self._prices(cdr.id, through), {
            p.date: p.close for p in self._prices(us.id, through)
        }

    @staticmethod
    def _traded(cdr_rows: Sequence[Price], us_closes: dict[date, Decimal]) -> Price | None:
        """The latest CDR bar with volume on a date the US stock has a bar too."""
        return next(
            (r for r in reversed(cdr_rows) if r.volume > 0 and r.date in us_closes), None
        )

    def cdr_ratio(self, cdr: Ticker, through: date) -> Decimal | None:
        """CDR close / US close on the latest date on or before `through` when the CDR traded
        (volume > 0): CAD per US dollar of the stock. A CDR that has never traded uses its
        latest close, over the US close on or before that date. None without prices."""
        cdr_rows, us_closes = self._closes(cdr, through)
        traded = self._traded(cdr_rows, us_closes)
        if traded is not None:
            return traded.close / us_closes[traded.date]
        before = [day for day in us_closes if cdr_rows and day <= cdr_rows[-1].date]
        return cdr_rows[-1].close / us_closes[max(before)] if before else None

    def cdr_mark(self, cdr: Ticker, as_of: date) -> Decimal | None:
        """The latest US close x (CDR close / US close) on the last date the CDR traded; the
        latest CDR close when it has never traded; None when no CDR price is stored (spec 04,
        Equity). This avoids stale marks on the many zero-volume days."""
        cdr_rows, us_closes = self._closes(cdr, as_of)
        traded = self._traded(cdr_rows, us_closes)
        if traded is None:
            return cdr_rows[-1].close if cdr_rows else None
        return us_closes[max(us_closes)] * traded.close / us_closes[traded.date]

    # --- Levels and stops ------------------------------------------------------------------

    def _factors(
        self, signal: TradeSignal, skip: Collection[int | None] = ()
    ) -> tuple[Decimal, Decimal]:
        """The products of the recorded US and of the recorded CDR split ratios with an ex-date
        after the signal's as_of, leaving out the actions in `skip`."""
        us = cdr = Decimal(1)
        for action in self._actions("us_split", us_symbol=signal.us_symbol):
            if action.ex_date > signal.as_of and action.id not in skip:
                us *= action.ratio
        for action in self._actions("cdr_split", cdr_id=signal.cdr_ticker_id):
            if action.ex_date > signal.as_of and action.id not in skip:
                cdr *= action.ratio
        return us, cdr

    def signal_levels(self, signal_id: int) -> SignalLevels:
        signal = self.signal(signal_id)
        us, cdr = self._factors(signal)
        return SignalLevels(
            us_signal_close=q4(signal.us_signal_close / us), us_stop=q4(signal.us_stop / us),
            cdr_signal_close=q4(signal.cdr_signal_close / cdr), cdr_stop=q4(signal.cdr_stop / cdr),
        )

    def entry_us(self, signal_id: int) -> Decimal | None:
        """us_signal_close x (the opening buy's price / cdr_signal_close), on today's US scale.
        Display only. None before the signal's position opens."""
        signal = self.signal(signal_id)
        opening = next(
            (e.opening for e in self._episodes(signal.cdr_ticker_id) if e.signal_id == signal.id),
            None,
        )
        if opening is None:
            return None
        ratio = opening.price_cad / signal.cdr_signal_close
        return q4(signal.us_signal_close * ratio / self._factors(signal)[0])

    def _stop_rows(self, signal_id: int) -> list[StopUpdateRow]:
        return list(
            self._session.exec(
                select(StopUpdateRow)
                .where(StopUpdateRow.signal_id == signal_id)
                .order_by(col(StopUpdateRow.id))
            ).all()
        )

    def stop_in_force(self, signal_id: int, session: date) -> StopLevels:
        """The stop in force during `session`, on today's scale: raises from earlier sessions
        count; a raise decided at `session`'s own close applies from the next one.

        The rows are replayed in the order they were written. A `split` row rescales the
        running stop by its split's ratio (or undoes it, for a voided split), and a recorded
        split with no row for this signal (recorded before its position opened) rescales the
        signal's initial stop."""
        signal = self.signal(signal_id)
        rows = self._stop_rows(signal_id)
        us_factor, cdr_factor = self._factors(signal, {row.corporate_action_id for row in rows})
        us, cdr = q4(signal.us_stop / us_factor), q4(signal.cdr_stop / cdr_factor)
        for row in rows:
            if row.reason == "trail":
                if row.session < session:
                    us, cdr = row.new_us_stop, row.new_cdr_stop
                continue
            action = self._session.get(CorporateAction, row.corporate_action_id)
            if action is None:
                continue
            if action.kind == "us_split":
                us = q4(us * _split_step(action.ratio, row.old_us_stop, row.new_us_stop))
            else:
                cdr = q4(cdr * _split_step(action.ratio, row.old_cdr_stop, row.new_cdr_stop))
        return StopLevels(us, cdr)

    def current_stop(self, signal_id: int) -> StopLevels:
        """The stop after every recorded raise and split: in force from the next session."""
        return self.stop_in_force(signal_id, date.max)

    def record_stop_update(
        self, *, signal_id: int, session: date, new_us_stop: Decimal, late: bool = False
    ) -> StopUpdateRow:
        """A `trail` raise decided at `session`'s close, in force from the next session. The
        CDR display stop is new_us_stop x the last traded ratio on or before `session`.
        (`split` rows are written by record_split and void_corporate_action.)"""
        signal = self.signal(signal_id)
        episode = self._open_episode(signal)
        if episode is None or episode.opening.trade_date > session:
            raise LedgerError(f"signal {signal_id} has no open position on {session}")
        if self.open_exit_alert(signal_id) is not None:
            raise LedgerError(f"signal {signal_id} has an open exit alert: no stop raise")
        old = self.stop_in_force(signal_id, session)
        new_us = q4(new_us_stop)
        if new_us <= old.us:
            raise LedgerError(
                f"a trailing stop only rises: {new_us} is not above the stop {old.us}"
            )
        taken = self._session.exec(
            select(StopUpdateRow).where(
                StopUpdateRow.signal_id == signal_id, StopUpdateRow.session == session,
                StopUpdateRow.reason == "trail",
            )
        ).first()
        if taken is not None:
            raise LedgerError(f"signal {signal_id} already has a raise for {session}")
        ratio = self.cdr_ratio(self._by_id(signal.cdr_ticker_id), session)
        new_cdr = old.cdr if ratio is None else q4(new_us * ratio)
        row = StopUpdateRow(
            signal_id=signal_id, session=session, reason="trail", old_us_stop=old.us,
            new_us_stop=new_us, old_cdr_stop=old.cdr, new_cdr_stop=new_cdr, late=late,
        )
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row
