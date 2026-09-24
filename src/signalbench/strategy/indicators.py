"""Causal daily indicators on plain float lists.

Every function returns a list the same length as its input. Entry `i` uses only
inputs `0..i`; it is `None` until there is enough history, and `None` means "no signal".
"""

from collections.abc import Sequence
from statistics import median


def sma(values: Sequence[float], period: int) -> list[float | None]:
    """Mean of values[i - period + 1 .. i]; first defined at i = period - 1."""
    out: list[float | None] = [None] * len(values)
    total = 0.0
    for i, value in enumerate(values):
        total += value
        if i >= period:
            total -= values[i - period]
        if i >= period - 1:
            out[i] = total / period
    return out


def wilder_rsi(closes: Sequence[float], period: int) -> list[float | None]:
    """Wilder RSI.

    change_i = close_i - close_(i-1); gain = max(change, 0); loss = max(-change, 0).
    Seed at i = period: avg_gain / avg_loss = simple mean of changes 1..period.
    After that: avg = (avg_prev * (period - 1) + current) / period.
    RSI = 100 - 100 / (1 + avg_gain / avg_loss); 100 when avg_loss is 0 (50 if both are 0).
    """
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = [0.0] + [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [0.0] + [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    avg_gain = sum(gains[1 : period + 1]) / period
    avg_loss = sum(losses[1 : period + 1]) / period
    for i in range(period, len(closes)):
        if i > period:
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0.0:
            out[i] = 50.0 if avg_gain == 0.0 else 100.0
        else:
            out[i] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return out


def _ranges(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]) -> list[float]:
    """True ranges for bars 1..n-1 (bar 0 has no previous close)."""
    return [
        max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        for i in range(1, len(closes))
    ]


def true_range(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]
) -> list[float | None]:
    """max(high - low, |high - prev close|, |low - prev close|); None at i = 0 (no prev close)."""
    return [None, *_ranges(highs, lows, closes)] if closes else []


def wilder_atr(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int
) -> list[float | None]:
    """Wilder ATR: seed at i = period with the mean of TR_1..TR_period, then
    ATR_i = (ATR_(i-1) * (period - 1) + TR_i) / period."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return out
    ranges = _ranges(highs, lows, closes)  # ranges[k] is TR of bar k + 1
    atr = sum(ranges[:period]) / period
    out[period] = atr
    for i in range(period + 1, len(closes)):
        atr = (atr * (period - 1) + ranges[i - 1]) / period
        out[i] = atr
    return out


def prior_max(values: Sequence[float], lookback: int) -> list[float | None]:
    """max(values[i - lookback .. i - 1]): the prior `lookback` sessions, excluding i."""
    return [
        max(values[i - lookback : i]) if i >= lookback else None for i in range(len(values))
    ]


def prior_mean(values: Sequence[float], lookback: int) -> list[float | None]:
    """mean(values[i - lookback .. i - 1]): the prior `lookback` sessions, excluding i."""
    out: list[float | None] = [None] * len(values)
    total = 0.0
    for i in range(len(values)):
        if i >= lookback:
            out[i] = total / lookback
        total += values[i]
        if i >= lookback:
            total -= values[i - lookback]
    return out


def rolling_min(values: Sequence[float], window: int) -> list[float | None]:
    """min(values[i - window + 1 .. i]), including i."""
    return [
        min(values[i - window + 1 : i + 1]) if i >= window - 1 else None
        for i in range(len(values))
    ]


def rolling_median(values: Sequence[float], window: int) -> list[float | None]:
    """median(values[i - window + 1 .. i]), including i."""
    return [
        float(median(values[i - window + 1 : i + 1])) if i >= window - 1 else None
        for i in range(len(values))
    ]
