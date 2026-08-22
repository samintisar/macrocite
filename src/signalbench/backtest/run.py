from __future__ import annotations

from datetime import date
from decimal import Decimal

from signalbench.backtest.metrics import compute_metrics
from signalbench.backtest.strategy import Trade

_METRIC_KEYS = (
    "sharpe_ratio",
    "max_drawdown",
    "win_rate",
    "total_return",
    "benchmark_return",
)


def metrics_as_json(
    prices: list[tuple[date, Decimal]],
    trades: list[Trade],
) -> dict[str, float]:
    metrics = compute_metrics(prices, trades)
    return {key: round(float(getattr(metrics, key)), 10) for key in _METRIC_KEYS}
