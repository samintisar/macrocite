"""One session's exits and stop raises for the live ledger (spec 04, Strategy levels).

The evening scan (spec 05) calls `review_session()` for each session in order (catch-up
sessions first) and writes what it returns: an exit alert per `ExitCall`
(`Ledger.record_exit_alert`) and a `trail` row per `Raise` (`Ledger.record_stop_update`).
decide() sees every open position, managed and manual, and the pending signals; what it
returns for a manual position, for a position with a `sent` exit alert, or for a symbol the
scale check holds is discarded. Entries and skips are passed through for the scan to size.
"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlmodel import Session

from signalbench.backtest.runner import load_market_inputs
from signalbench.ingest.cdr import CdrEntry
from signalbench.live.book import AlertReason, q4
from signalbench.live.ledger import Ledger
from signalbench.market.calendar import HISTORY_START, Sessions
from signalbench.strategy.config import StrategyConfig
from signalbench.strategy.decide import decide
from signalbench.strategy.decision import Decision
from signalbench.strategy.market_view import MarketView
from signalbench.strategy.portfolio import PortfolioState
from signalbench.strategy.readings import NullReadingsView


@dataclass(frozen=True)
class ExitCall:
    """Sell a managed position at the next open."""

    signal_id: int
    us_symbol: str
    reason: AlertReason


@dataclass(frozen=True)
class Raise:
    """A trailing-stop raise decided at `session`'s close, in force from the next session."""

    signal_id: int
    session: date
    old_us_stop: Decimal
    new_us_stop: Decimal


@dataclass(frozen=True)
class SessionReview:
    as_of: date
    state: PortfolioState  # what decide() saw
    decision: Decision  # as decide() returned it: the scan sizes its entries
    exits: tuple[ExitCall, ...]
    raises: tuple[Raise, ...]
    discarded: tuple[str, ...]  # position ids whose exit or raise was dropped


def live_market(
    session: Session,
    universe: list[CdrEntry],
    config: StrategyConfig,
    calendar: Sessions,
    as_of: date,
) -> MarketView:
    """Stored US bars up to `as_of`, earnings dates from SEC 2.02 plus the Finnhub calendar
    (upcoming ones included), and sessions running past `as_of` for the earnings look-ahead."""
    inputs = load_market_inputs(
        session, universe, config.regime_symbol, as_of, calendar_earnings=True
    )
    lookahead = max(config.earnings_blackout_sessions, config.earnings_exit_sessions)
    sessions = calendar.sessions_between(HISTORY_START, as_of)
    sessions += calendar.next_sessions(as_of, lookahead)
    return MarketView(inputs.symbols, inputs.benchmark, sessions, config)


def _signal_id(position_id: str) -> int | None:
    """The signal id of a managed position; None for a manual one (manual-<fill id>)."""
    return int(position_id) if position_id.isdigit() else None


def review_session(
    ledger: Ledger,
    market: MarketView,
    config: StrategyConfig,
    as_of: date,
    *,
    hold: Collection[str] = (),
    same_day: bool = False,
) -> SessionReview:
    """decide() at `as_of`'s close on the ledger's portfolio, Breakout only and without Jev
    readings (spec 04, Live config). `hold` names US symbols that get no decision tonight.
    `same_day`: signals already written for `as_of` (by a failed run) hold slots and cash."""
    live = config.with_setups(("breakout",))
    state = ledger.portfolio_state(as_of, same_day=same_day)
    decision = decide(as_of, market, NullReadingsView(), state, live)
    symbols = {p.id: p.symbol for p in state.positions}
    exits: list[ExitCall] = []
    raises: list[Raise] = []
    discarded: list[str] = []

    def managed(position_id: str) -> int | None:
        signal_id = _signal_id(position_id)
        if (
            signal_id is None
            or symbols[position_id] in hold
            or ledger.open_exit_alert(signal_id) is not None
        ):
            discarded.append(position_id)
            return None
        return signal_id

    for order in decision.exits:
        signal_id = managed(order.position_id)
        if signal_id is None:
            continue
        if order.reason not in ("stop", "earnings"):
            raise ValueError(f"the live config has no {order.reason} exit ({order.symbol})")
        reason: AlertReason = "stop" if order.reason == "stop" else "earnings"
        exits.append(ExitCall(signal_id, order.symbol, reason))
    for update in decision.stop_updates:
        signal_id = managed(update.position_id)
        if signal_id is None:
            continue
        old = ledger.stop_in_force(signal_id, as_of).us
        new = q4(Decimal(repr(update.new_stop)))
        if new > old:
            raises.append(Raise(signal_id, as_of, old, new))
    return SessionReview(
        as_of=as_of, state=state, decision=decision, exits=tuple(exits),
        raises=tuple(raises), discarded=tuple(discarded),
    )
