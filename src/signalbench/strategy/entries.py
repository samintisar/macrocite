"""From signals to entry orders: gates, ranking, slots, sector cap, and sizing."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from signalbench.strategy.config import StrategyConfig
from signalbench.strategy.decision import EntryOrder, Skip, SkipReason
from signalbench.strategy.portfolio import PortfolioState
from signalbench.strategy.setups import Signal

MIN_POSITION_FRACTION = 0.01  # a position worth less than 1% of equity is dust: skip it


@dataclass(frozen=True)
class Candidate:
    symbol: str
    sector: str
    signal: Signal
    median_traded_value: float
    catalyst: bool


@dataclass(frozen=True)
class Gates:
    """Facts that can stop a signal before ranking, checked in this field order."""

    held: bool
    regime_on: bool
    paused: bool
    earnings_soon: bool  # an earnings event on D+1..D+3
    blocked: bool


def gate_reason(gates: Gates) -> SkipReason | None:
    if gates.held:
        return "held"
    if not gates.regime_on:
        return "regime"
    if gates.paused:
        return "paused"
    if gates.earnings_soon:
        return "earnings_blackout"
    if gates.blocked:
        return "blocked"
    return None


def size(
    signal_close: float,
    stop: float,
    equity: float,
    uncommitted_cash: float,
    config: StrategyConfig,
) -> float:
    """Risk-based units, capped at equity / max_positions and at uncommitted cash (by value)."""
    risk_units = config.risk_pct * equity / (signal_close - stop)
    value_cap = equity / config.max_positions / signal_close
    cash_cap = max(uncommitted_cash, 0.0) / signal_close
    return min(risk_units, value_cap, cash_cap)


def allocate(
    candidates: Sequence[Candidate],
    portfolio: PortfolioState,
    config: StrategyConfig,
) -> tuple[list[EntryOrder], list[Skip]]:
    """Rank (catalyst first, higher median traded value, symbol A-Z) and fill free slots."""
    ranked = sorted(candidates, key=lambda c: (not c.catalyst, -c.median_traded_value, c.symbol))
    free = config.max_positions - portfolio.slots_used()
    sectors: Counter[str] = Counter()
    for position in portfolio.positions:
        sectors[position.sector] += 1
    for pending in portfolio.pending:
        sectors[pending.sector] += 1
    cash = portfolio.uncommitted_cash()
    entries: list[EntryOrder] = []
    skips: list[Skip] = []
    for rank, candidate in enumerate(ranked, start=1):
        signal = candidate.signal
        if free <= 0:
            skips.append(Skip(candidate.symbol, signal.setup, "no_slot"))
            continue
        if sectors[candidate.sector] >= config.max_per_sector:
            skips.append(Skip(candidate.symbol, signal.setup, "sector_cap"))
            continue
        units = size(signal.close, signal.stop, portfolio.equity, cash, config)
        if units <= 0.0 or units * signal.close < MIN_POSITION_FRACTION * portfolio.equity:
            skips.append(Skip(candidate.symbol, signal.setup, "no_cash"))
            continue
        entries.append(
            EntryOrder(
                symbol=candidate.symbol,
                setup=signal.setup,
                sector=candidate.sector,
                signal_close=signal.close,
                stop=signal.stop,
                target_r=signal.target_r,
                time_limit=signal.time_limit,
                units=units,
                risk_amount=units * (signal.close - signal.stop),
                catalyst=candidate.catalyst,
                rank=rank,
            )
        )
        free -= 1
        sectors[candidate.sector] += 1
        cash -= units * signal.close
    return entries, skips
