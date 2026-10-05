"""Prices, US-equivalent levels, stops, and splits: the ledger's second layer (spec 04).

Decisions use the US levels, as in the backtest; the CDR levels are for display. A managed
position's stop is its signal's `us_stop` until `stop_updates` rows move it: a `trail` row only
raises it, and a `split` row rescales it. The tool never rescales the owner's fills or ACB: a
US split rescales only its own levels, and a CDR split multiplies the units held.
"""


from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import get_args
from uuid import UUID

from sqlmodel import col, select

from signalbench.db.models import (
    CorporateAction,
    Price,
    StopUpdateRow,
    Ticker,
    TickerKind,
    TradeSignal,
)
from signalbench.live.book import (
    LedgerBook,
    LedgerError,
    SplitKind,
    SplitSource,
    check_choice,
    check_places,
    q4,
)

TOLERANCE = Decimal("0.03")  # a larger gap than 3% between two closes is an unrecorded split


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


def _split_row(
    signal_id: int, action: CorporateAction, old: StopLevels, new: StopLevels
) -> StopUpdateRow:
    return StopUpdateRow(
        signal_id=signal_id, session=action.ex_date, reason="split", old_us_stop=old.us,
        new_us_stop=new.us, old_cdr_stop=old.cdr, new_cdr_stop=new.cdr,
        corporate_action_id=action.id,
    )


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

    def traded_ratio(self, cdr: Ticker, through: date) -> Decimal | None:
        """CDR close / US close on the latest date on or before `through` when the CDR traded
        (volume > 0): CAD per US dollar of the stock. None when it has never traded."""
        cdr_rows, us_closes = self._closes(cdr, through)
        traded = self._traded(cdr_rows, us_closes)
        return None if traded is None else traded.close / us_closes[traded.date]

    def cdr_ratio(self, cdr: Ticker, through: date) -> Decimal | None:
        """The traded ratio (traded_ratio). A CDR that has never traded uses its latest close,
        over the US close on or before that date. None without prices."""
        traded = self.traded_ratio(cdr, through)
        if traded is not None:
            return traded
        cdr_rows, us_closes = self._closes(cdr, through)
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

    # --- Splits ----------------------------------------------------------------------------

    def scale_check(self, signal_id: int) -> str | None:
        """Why a signal's prices and the stored prices of its as_of disagree by more than 3%
        once the recorded splits are applied (an unrecorded split or bad data), or None.
        Compares the raw US close (split-adjusted, not dividend-adjusted), so dividends pass,
        and the CDR mark of its as_of (the basis of the signal's CDR reference, spec 05)."""
        signal = self.signal(signal_id)
        levels = self.signal_levels(signal_id)
        us = self._ticker(signal.us_symbol, TickerKind.us_stock)
        rows = [r for r in self._prices(us.id, signal.as_of) if r.date == signal.as_of]
        if not rows:
            return f"no stored US close for {signal.as_of}"
        mark = self.cdr_mark(self._by_id(signal.cdr_ticker_id), signal.as_of)
        if mark is None:
            return f"no stored CDR close for {signal.as_of}"
        checks = [
            ("US close", rows[0].close, levels.us_signal_close),
            ("CDR mark", mark, levels.cdr_signal_close),
        ]
        for name, stored, expected in checks:
            if abs(stored / expected - 1) > TOLERANCE:
                return (
                    f"the stored {name} {q4(stored)} on {signal.as_of} is "
                    f"{stored / expected:.4f}x the signal's {expected} after recorded splits"
                )
        return None

    def record_split(
        self, *, kind: SplitKind, symbol: str, ex_date: date, ratio: Decimal, source: SplitSource
    ) -> CorporateAction:
        """A US split (the tool's US levels rescale) or a CDR split (units x ratio, the total
        ACB unchanged). Writes a `split` stop row for each open managed position it touches,
        and a CDR split withdraws the CDR's `sent` signals from before its ex-date."""
        check_choice(kind, get_args(SplitKind), "kind")
        check_choice(source, get_args(SplitSource), "source")
        if ratio <= 0 or ratio == 1:
            raise LedgerError(f"a split ratio must be above 0 and not 1, not {ratio}")
        check_places(ratio, 6, "ratio")
        if kind == "us_split":
            us_symbol, cdr_id = self._ticker(symbol, TickerKind.us_stock).symbol, None
            signals = self._session.exec(
                select(TradeSignal).where(TradeSignal.us_symbol == us_symbol)
            ).all()
        else:
            cdr = self._cdr(symbol)
            us_symbol, cdr_id = None, cdr.id
            signals = self._session.exec(
                select(TradeSignal).where(TradeSignal.cdr_ticker_id == cdr.id)
            ).all()
            held = self._book(
                self._fills(cdr.id, ex_date - timedelta(days=1)),
                [s for s in self._actions("cdr_split", cdr_id=cdr.id) if s.ex_date < ex_date],
            ).units
            sent = [s for s in signals if s.status == "sent" and s.as_of < ex_date]
            if held == 0 and not sent:
                raise LedgerError(
                    f"{symbol} had no open position or sent signal on {ex_date}: "
                    "a CDR split only matters for those"
                )
        duplicate = self._session.exec(
            select(CorporateAction).where(
                CorporateAction.kind == kind, CorporateAction.us_symbol == us_symbol,
                CorporateAction.cdr_ticker_id == cdr_id, CorporateAction.ex_date == ex_date,
                col(CorporateAction.voided).is_(False),
            )
        ).first()
        if duplicate is not None:
            raise LedgerError(f"{symbol} {kind} on {ex_date} is already recorded ({duplicate.id})")
        affected = [
            (s, self.current_stop(s.id))
            for s in signals
            if s.id is not None and s.as_of < ex_date and self._open_episode(s) is not None
        ]
        action = CorporateAction(
            kind=kind, us_symbol=us_symbol, cdr_ticker_id=cdr_id, ex_date=ex_date,
            ratio=ratio, source=source,
        )
        self._session.add(action)
        self._session.flush()
        if cdr_id is not None:
            try:
                self._check_book(self._by_id(cdr_id))
            except LedgerError:
                self._session.rollback()
                raise
            for signal in signals:
                if signal.status == "sent" and signal.as_of < ex_date:
                    signal.status = "withdrawn"
                    self._session.add(signal)
        for signal, old in affected:
            assert signal.id is not None
            new = StopLevels(
                q4(old.us / ratio) if kind == "us_split" else old.us,
                q4(old.cdr / ratio) if kind == "cdr_split" else old.cdr,
            )
            self._session.add(_split_row(signal.id, action, old, new))
        self._session.commit()
        self._session.refresh(action)
        return action

    def void_corporate_action(self, action_id: int, reason: str) -> None:
        """Void a wrong split. Its effects are recomputed, and each `split` stop row written
        from it gets a new `split` row that undoes it. Withdrawn signals stay withdrawn."""
        action = self._session.get(CorporateAction, action_id)
        if action is None or action.voided:
            raise LedgerError(f"no corporate action {action_id} to void")
        if not reason.strip():
            raise LedgerError("a void needs a reason")
        undo: list[tuple[int, StopLevels]] = []
        rows = self._session.exec(
            select(StopUpdateRow).where(StopUpdateRow.corporate_action_id == action_id)
        ).all()
        for signal_id in sorted({row.signal_id for row in rows}):
            if sum(1 for row in rows if row.signal_id == signal_id) % 2 == 1:
                undo.append((signal_id, self.current_stop(signal_id)))
        action.voided = True
        action.void_reason = reason.strip()
        self._session.add(action)
        self._session.flush()
        if action.cdr_ticker_id is not None:
            try:
                self._check_book(self._by_id(action.cdr_ticker_id))
            except LedgerError as error:
                self._session.rollback()
                raise LedgerError(f"voiding corporate action {action_id}: {error}") from None
        for signal_id, old in undo:
            new = StopLevels(
                q4(old.us * action.ratio) if action.kind == "us_split" else old.us,
                q4(old.cdr * action.ratio) if action.kind == "cdr_split" else old.cdr,
            )
            self._session.add(_split_row(signal_id, action, old, new))
        self._session.commit()
