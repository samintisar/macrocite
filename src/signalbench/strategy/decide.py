"""The one decision function shared by the backtest and the live scan (spec 02).

Pure: no database, no clock, no network. Same inputs, same Decision.
"""

from datetime import date

from signalbench.strategy.config import StrategyConfig
from signalbench.strategy.decision import Decision, ExitOrder, Skip, StopUpdate
from signalbench.strategy.entries import Candidate, Gates, allocate, gate_reason
from signalbench.strategy.exits import exit_reason, trailed_stop
from signalbench.strategy.market_view import AsOfView, MarketView
from signalbench.strategy.portfolio import PortfolioState
from signalbench.strategy.readings import ReadingsView
from signalbench.strategy.setups import first_signal


def _exits(
    view: AsOfView, portfolio: PortfolioState, config: StrategyConfig
) -> tuple[list[ExitOrder], list[StopUpdate]]:
    exits: list[ExitOrder] = []
    updates: list[StopUpdate] = []
    for position in sorted(portfolio.positions, key=lambda p: p.id):
        snap = view.snapshot(position.symbol)
        if snap is None:
            continue
        soon = view.earnings_within(position.symbol, config.earnings_exit_sessions)
        reason = exit_reason(position, snap, soon)
        if reason is not None:
            exits.append(ExitOrder(position.id, position.symbol, reason))
            continue
        new_stop = trailed_stop(position, snap, config)
        if new_stop is not None:
            updates.append(StopUpdate(position.id, position.stop, new_stop))
    return exits, updates


def _candidates(
    view: AsOfView,
    readings: ReadingsView,
    portfolio: PortfolioState,
    config: StrategyConfig,
) -> tuple[list[Candidate], list[Skip]]:
    as_of = view.as_of
    regime_on = view.regime_on()
    candidates: list[Candidate] = []
    skips: list[Skip] = []
    for symbol in view.symbols:
        snap = view.snapshot(symbol)
        if snap is None or snap.date != as_of or snap.median_traded_value is None:
            continue
        if not view.is_active(symbol):
            continue
        triggered = "sentiment" in config.enabled_setups and readings.sentiment_trigger(
            symbol, as_of
        )
        signal = first_signal(snap, config, triggered)
        if signal is None:
            continue
        reason = gate_reason(
            Gates(
                held=portfolio.holds(symbol),
                regime_on=regime_on,
                paused=portfolio.paused,
                earnings_soon=view.earnings_within(symbol, config.earnings_blackout_sessions),
                blocked=readings.blocked(symbol, as_of),
            )
        )
        if reason is not None:
            skips.append(Skip(symbol, signal.setup, reason))
            continue
        candidates.append(
            Candidate(
                symbol=symbol,
                sector=view.sector(symbol),
                signal=signal,
                median_traded_value=snap.median_traded_value,
                catalyst=readings.catalyst(symbol, as_of),
            )
        )
    return candidates, skips


def decide(
    as_of: date,
    market: MarketView,
    readings: ReadingsView,
    portfolio: PortfolioState,
    config: StrategyConfig,
) -> Decision:
    view = market.at(as_of)
    exits, stop_updates = _exits(view, portfolio, config)
    candidates, gate_skips = _candidates(view, readings, portfolio, config)
    entries, slot_skips = allocate(candidates, portfolio, config)
    return Decision(
        entries=entries,
        exits=exits,
        stop_updates=stop_updates,
        skips=gate_skips + slot_skips,
    )
