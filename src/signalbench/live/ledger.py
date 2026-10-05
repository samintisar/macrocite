"""The ledger (spec 04, Interface): the one class the scan, the bot, and the CLI use.

Built in layers: live/book.py (fills, cash, voids, signals, exit alerts), then live/levels.py
(prices, stops, splits), then this module (positions, equity, the pause, the tax report, and
the scale-up check).
"""


from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlmodel import col, select

from signalbench.db.models import (
    EquitySnapshot,
    ExitAlert,
    LiveRiskState,
    TickerKind,
    TradeSignal,
    utcnow,
)
from signalbench.live.book import NEW_YORK, ZERO, Episode, LedgerError, cad, q4
from signalbench.live.levels import LedgerLevels, StopLevels
from signalbench.live.scaleup import AlertOutcome, ScaleUpResult, SignalBuy, scale_up
from signalbench.live.sizing import committed_cash
from signalbench.live.tax import TaxReport, build_tax_report
from signalbench.strategy.portfolio import PendingEntry, PortfolioState, Position


@dataclass(frozen=True)
class LivePosition:
    """An open position at one session's close."""

    episode: Episode
    cdr_symbol: str
    us_symbol: str
    sector: str
    units: Decimal  # open CDR units, post-split
    acb: Decimal  # total ACB
    mark: Decimal | None  # the CDR mark; None when no CDR price is stored
    entry_session: date  # the first NYSE session on or after the opening buy's trade date
    sessions_held: int  # the entry session counts as 1
    stop: StopLevels | None  # managed: the stop in force during this session; manual: None
    entry_us: Decimal | None  # managed: the US-equivalent entry (display only)
    highest_close: float  # managed: highest stored adjusted US close since the entry session

    @property
    def managed(self) -> bool:
        return self.episode.managed

    @property
    def value(self) -> Decimal:
        """Units x the CDR mark, or the ACB when no CDR price is stored."""
        return self.acb if self.mark is None else self.units * self.mark

    def strategy_position(self) -> Position:
        """What decide() sees. A manual position has stop 0: it holds a slot and counts toward
        its sector, and whatever decide() returns for it is discarded (live/review.py)."""
        entry = stop = highest = 0.0
        if self.stop is not None and self.entry_us is not None:
            entry, stop, highest = float(self.entry_us), float(self.stop.us), self.highest_close
        return Position(
            id=self.episode.key,
            symbol=self.us_symbol,
            setup="breakout",
            sector=self.sector,
            units=float(self.units),
            entry_price=entry,
            entry_date=self.entry_session,
            stop=stop,
            target=None,
            time_limit=None,
            sessions_held=self.sessions_held,
            highest_close=highest,
        )


class Ledger(LedgerLevels):
    """Everything derived from what the owner did on Wealthsimple."""

    # --- Positions -------------------------------------------------------------------------

    def positions(self, as_of: date) -> list[LivePosition]:
        """Every position open at the close of `as_of` (fills and splits dated on or before it),
        managed and manual, by CDR symbol."""
        found: list[LivePosition] = []
        for cdr_id in self._held_cdr_ids():
            episodes_ = self._episodes(cdr_id, as_of)
            if not episodes_ or episodes_[-1].closed_on is not None:
                continue
            found.append(self._position(episodes_[-1], as_of))
        return sorted(found, key=lambda p: p.cdr_symbol)

    def _position(self, episode: Episode, as_of: date) -> LivePosition:
        cdr = self._by_id(episode.cdr_ticker_id)
        us = self._us_of(cdr)
        fills = self._fills(cdr.id, as_of)
        splits = [s for s in self._actions("cdr_split", cdr_id=cdr.id) if s.ex_date <= as_of]
        book = self._book(fills, splits)
        entry = self._entry_session(episode.opening.trade_date)
        stop = entry_us = None
        highest = 0.0
        if episode.signal_id is not None:
            stop = self.stop_in_force(episode.signal_id, as_of)
            entry_us = self.entry_us(episode.signal_id)
            closes = [
                float(p.adj_close) for p in self._prices(us.id, as_of) if p.date >= entry
            ]
            highest = max(closes, default=0.0)
        return LivePosition(
            episode=episode, cdr_symbol=cdr.symbol, us_symbol=us.symbol,
            sector=us.sector or "Unknown", units=book.units, acb=book.acb,
            mark=self.cdr_mark(cdr, as_of), entry_session=entry,
            sessions_held=len(self._calendar.sessions_between(entry, as_of)),
            stop=stop, entry_us=entry_us, highest_close=highest,
        )

    def _entry_session(self, trade_date: date) -> date:
        """The first NYSE session on or after the opening buy's trade date."""
        if self._calendar.is_session(trade_date):
            return trade_date
        return self._calendar.next_sessions(trade_date, 1)[0]

    # --- Equity, peak, and the pause -------------------------------------------------------

    def risk_state(self) -> LiveRiskState:
        risk = self._session.get(LiveRiskState, 1)
        if risk is None:
            risk = LiveRiskState()
            self._session.add(risk)
            self._session.commit()
            self._session.refresh(risk)
        return risk

    def equity(self, as_of: date) -> EquitySnapshot:
        """Equity at the close of `as_of`, not saved. Peak = the max equity since
        risk_state.peak_reset_on, this one included, each earlier snapshot adjusted by the cash
        movements after its date through `as_of` (+ deposits, - withdrawals): moving cash in
        or out is never a gain or a drawdown."""
        cash = self.cash(as_of)
        value = sum((p.value for p in self.positions(as_of)), ZERO)
        equity = q4(cash + value)
        reset = self.risk_state().peak_reset_on
        query = select(col(EquitySnapshot.date), col(EquitySnapshot.equity)).where(
            col(EquitySnapshot.date) < as_of
        )
        if reset is not None:
            query = query.where(col(EquitySnapshot.date) >= reset)
        movements = self._movements(as_of)
        earlier = [
            then + sum((m.amount_cad for m in movements if m.occurred_on > day), ZERO)
            for day, then in self._session.exec(query).all()
        ]
        peak = q4(max([equity, *earlier]))
        return EquitySnapshot(
            date=as_of, cash=q4(cash), positions_value=q4(value), equity=equity, peak=peak
        )

    def record_equity(
        self, as_of: date, *, pause_drawdown: Decimal
    ) -> tuple[EquitySnapshot, bool]:
        """Write the night's snapshot (replacing one for the same date) and pause new entries
        when equity < (1 - pause_drawdown) x peak. Returns the snapshot and whether this call
        paused. Only resume() clears a pause; the backtest's auto_resume_sessions is ignored."""
        snapshot = self.equity(as_of)
        self._session.merge(snapshot)
        risk = self.risk_state()
        paused_now = not risk.paused and snapshot.equity < (1 - pause_drawdown) * snapshot.peak
        if paused_now:
            risk.paused = True
            risk.paused_at = as_of
            risk.paused_reason = (
                f"equity {cad(snapshot.equity)} is below {1 - pause_drawdown} x the peak "
                f"{cad(snapshot.peak)}"
            )
            self._session.add(risk)
        self._session.commit()
        return snapshot, paused_now

    def resume(self) -> LiveRiskState:
        """/resume: clear the pause; the peak counts again from today."""
        risk = self.risk_state()
        if not risk.paused:
            raise LedgerError("new entries are not paused")
        risk.paused = False
        risk.resumed_at = utcnow()
        risk.peak_reset_on = self.today
        self._session.add(risk)
        self._session.commit()
        self._session.refresh(risk)
        return risk

    def portfolio_state(self, as_of: date, *, same_day: bool = False) -> PortfolioState:
        """What decide() needs at `as_of`'s close: every open position (managed and manual),
        the pending signals holding slots and their cash at the limit price, cash, equity,
        peak, and the pause. `same_day`: as_of's own signals are pending too (a retried scan)."""
        snapshot = self.equity(as_of)
        risk = self.risk_state()
        pending = []
        for signal in self.pending_signals(as_of, same_day=same_day):
            us = self._ticker(signal.us_symbol, TickerKind.us_stock)
            pending.append(
                PendingEntry(
                    symbol=signal.us_symbol, setup="breakout", sector=us.sector or "Unknown",
                    planned_cost=float(
                        committed_cash(signal.suggested_units, signal.cdr_signal_close)
                    ),
                )
            )
        return PortfolioState(
            cash=float(snapshot.cash),
            positions=tuple(p.strategy_position() for p in self.positions(as_of)),
            pending=tuple(pending),
            equity=float(snapshot.equity),
            peak=float(snapshot.peak),
            paused=risk.paused,
            paused_since=risk.paused_at if risk.paused else None,
        )

    # --- The tax report --------------------------------------------------------------------

    def tax_report(self, year: int) -> TaxReport:
        """The year's dispositions, superficial losses, and CDR splits (spec 04). A loss within
        30 days of today is provisional. Not tax advice."""
        return build_tax_report(self.books(), year)

    # --- The scale-up check ----------------------------------------------------------------

    def scale_up_check(self) -> ScaleUpResult:
        """The four checks over every signal, signal-linked buy, and exit alert (spec 04)."""
        closed = sum(1 for trade in self.closed_trades() if trade.episode.managed)
        signals = {s.id: s for s in self._session.exec(select(TradeSignal)).all()}
        buys = [
            SignalBuy(f.id, f.price_cad, signals[f.signal_id].cdr_signal_close)
            for f in self._fills()
            if f.side == "buy" and f.signal_id is not None and f.id is not None
        ]
        alerts = self._session.exec(select(ExitAlert).order_by(col(ExitAlert.id))).all()
        return scale_up(
            closed_managed=closed,
            statuses=[(s.status, s.skip_reason) for s in signals.values()],
            buys=buys,
            alerts=[self._alert_outcome(alert) for alert in alerts],
            today=self.today,
        )

    def _alert_outcome(self, alert: ExitAlert) -> AlertOutcome:
        """The sale window: through the first session after the alert's session, or after the
        New York date it was sent when it was late."""
        assert alert.id is not None
        sent_on = alert.created_at.astimezone(NEW_YORK).date() if alert.late else alert.as_of
        sales = [
            f.trade_date for f in self._fills(alert.cdr_ticker_id)
            if f.side == "sell" and f.trade_date > alert.as_of
        ]
        return AlertOutcome(
            alert_id=alert.id, status=alert.status,
            deadline=self._calendar.next_sessions(sent_on, 1)[0], sold_on=min(sales, default=None),
        )

    def record_scale_up(self, result: ScaleUpResult) -> None:
        """Append the result and its inputs to risk_state's history, for the record."""
        risk = self.risk_state()
        risk.scale_up_history = [*risk.scale_up_history, result.record(utcnow())]
        self._session.add(risk)
        self._session.commit()
