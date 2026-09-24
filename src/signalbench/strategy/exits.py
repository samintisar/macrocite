"""Exit checks and the Breakout trailing stop, evaluated at each close."""

from signalbench.strategy.config import StrategyConfig
from signalbench.strategy.decision import ExitReason
from signalbench.strategy.market_view import Snapshot
from signalbench.strategy.portfolio import Position


def exit_reason(position: Position, snap: Snapshot, earnings_soon: bool) -> ExitReason | None:
    """First match in spec order: stop, earnings, target, time. It fills at the next open.

    `earnings_soon` is whether an earnings event falls on D+1 or D+2.
    """
    if snap.close <= position.stop:
        return "stop"
    if earnings_soon:
        return "earnings"
    if position.target is not None and snap.close >= position.target:
        return "target"
    if position.sessions_held >= position.time_limit:
        return "time"
    return None


def trailed_stop(position: Position, snap: Snapshot, config: StrategyConfig) -> float | None:
    """Breakout only: max(stop, highest close since entry - 3 x ATR today).

    Returns the new stop when it moves up, otherwise None. The stop never loosens.
    """
    if position.setup != "breakout" or snap.atr is None:
        return None
    candidate = position.highest_close - config.breakout.trail_atr_mult * snap.atr
    return candidate if candidate > position.stop else None
