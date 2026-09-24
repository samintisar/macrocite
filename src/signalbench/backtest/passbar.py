"""The v1 pass bar (spec 02). A setup run alone must meet every criterion to go live."""

from dataclasses import dataclass

from signalbench.backtest.metrics import RunMetrics
from signalbench.strategy.config import BacktestParams


@dataclass(frozen=True)
class Criterion:
    value: float
    threshold: float
    passed: bool


def evaluate_pass_bar(
    metrics: RunMetrics, qqq_sharpe: float, params: BacktestParams
) -> dict[str, Criterion]:
    """1 trades >= 30; 2 mean R > +0.10; 3 mean R > 0 in both halves; 4 Sharpe >= QQQ's."""
    worst_half = min(metrics.mean_r_h1, metrics.mean_r_h2)
    return {
        "trades": Criterion(
            float(metrics.trades), float(params.min_trades), metrics.trades >= params.min_trades
        ),
        "mean_r": Criterion(metrics.mean_r, params.min_mean_r, metrics.mean_r > params.min_mean_r),
        "mean_r_halves": Criterion(
            worst_half, 0.0, metrics.mean_r_h1 > 0.0 and metrics.mean_r_h2 > 0.0
        ),
        "sharpe": Criterion(metrics.sharpe, qqq_sharpe, metrics.sharpe >= qqq_sharpe),
    }


def passes(bar: dict[str, Criterion]) -> bool:
    return all(criterion.passed for criterion in bar.values())
