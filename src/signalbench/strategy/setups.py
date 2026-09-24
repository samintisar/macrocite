"""The three setup rules (spec 02 table), each a pure function of one Snapshot."""

from dataclasses import dataclass

from signalbench.strategy.config import SetupName, StrategyConfig
from signalbench.strategy.market_view import Snapshot


@dataclass(frozen=True)
class Signal:
    setup: SetupName
    close: float
    stop: float
    target_r: float | None
    time_limit: int


def pullback(snap: Snapshot, config: StrategyConfig) -> Signal | None:
    """close > SMA50 > SMA200 and RSI(2) < 10; stop = min(low, 3 sessions) - 0.5 ATR."""
    p = config.pullback
    fast = snap.sma.get(p.trend_sma_fast)
    slow = snap.sma.get(p.trend_sma_slow)
    if fast is None or slow is None or snap.rsi is None or snap.min_low is None:
        return None
    if snap.atr is None or not (snap.close > fast > slow) or snap.rsi >= p.rsi_max:
        return None
    stop = snap.min_low - p.stop_atr_mult * snap.atr
    return Signal("pullback", snap.close, stop, p.target_r, p.time_limit)


def breakout(snap: Snapshot, config: StrategyConfig) -> Signal | None:
    """close > SMA50, close > prior 20-session max close, volume >= 1.5 x prior 50-session mean."""
    b = config.breakout
    trend = snap.sma.get(b.trend_sma)
    if trend is None or snap.prior_max_close is None or snap.prior_mean_volume is None:
        return None
    if snap.atr is None or snap.close <= trend or snap.close <= snap.prior_max_close:
        return None
    if snap.volume < b.volume_mult * snap.prior_mean_volume:
        return None
    return Signal("breakout", snap.close, snap.close - b.stop_atr_mult * snap.atr, None, b.time_limit)


def sentiment(snap: Snapshot, config: StrategyConfig, triggered: bool) -> Signal | None:
    """A positive reading in the last 3 sessions (spec 03), close > SMA20 and > prior high."""
    s = config.sentiment
    trend = snap.sma.get(s.trend_sma)
    if not triggered or trend is None or snap.prev_high is None or snap.atr is None:
        return None
    if snap.close <= trend or snap.close <= snap.prev_high:
        return None
    return Signal(
        "sentiment", snap.close, snap.close - s.stop_atr_mult * snap.atr, s.target_r, s.time_limit
    )


def first_signal(
    snap: Snapshot, config: StrategyConfig, sentiment_triggered: bool
) -> Signal | None:
    """The first enabled setup, in `setup_priority` order, that fires with a stop below close."""
    for name in config.setup_priority:
        if name not in config.enabled_setups:
            continue
        if name == "pullback":
            signal = pullback(snap, config)
        elif name == "breakout":
            signal = breakout(snap, config)
        else:
            signal = sentiment(snap, config, sentiment_triggered)
        if signal is not None and signal.stop < signal.close:
            return signal
    return None
