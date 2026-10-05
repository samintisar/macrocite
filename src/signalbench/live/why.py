"""The "Why" line of an entry (spec 05): a fixed template, no LLM.

Filled from the same Snapshot values that fired the Breakout rule (the signal close, its
volume, and the prior mean volume), with the lookbacks from the config. Pure: no network and
no model. The line is stored in `trade_signals.explanation`.
"""

from decimal import ROUND_HALF_UP, Decimal

from signalbench.strategy.config import BreakoutParams
from signalbench.strategy.market_view import Snapshot


def _half_up(value: float, step: str) -> Decimal:
    """`value` rounded half up on its shortest decimal form (181.515 -> 181.52)."""
    return Decimal(repr(value)).quantize(Decimal(step), rounding=ROUND_HALF_UP)


def why_line(snapshot: Snapshot, breakout: BreakoutParams) -> str:
    if not snapshot.prior_mean_volume:
        raise ValueError(f"no prior volume average on {snapshot.date}: Breakout cannot have fired")
    close = _half_up(snapshot.close, "0.01")
    ratio = _half_up(snapshot.volume / snapshot.prior_mean_volume, "0.1")
    return (
        f"Closed at a {breakout.lookback}-session high (US${close:,}) on {ratio}× its "
        f"{breakout.volume_lookback}-session average volume, above its "
        f"{breakout.trend_sma}-session average."
    )
