from datetime import date
from decimal import Decimal

from signalbench.backtest.metrics import compute_metrics
from signalbench.backtest.strategy import Trade


def test_golden_metrics() -> None:
    prices = [
        (date(2024, 1, 2), Decimal(100)),
        (date(2024, 1, 3), Decimal(100)),
        (date(2024, 1, 4), Decimal(110)),
        (date(2024, 1, 5), Decimal(110)),
    ]
    trades = [
        Trade(
            entry_date=date(2024, 1, 2),
            exit_date=date(2024, 1, 4),
            entry_price=Decimal(100),
            exit_price=Decimal(110),
        )
    ]
    m = compute_metrics(prices, trades)
    assert m.total_return == 0.10
    assert m.win_rate == 1.0
    assert m.benchmark_return == 0.10
    assert m.max_drawdown == 0.0
    assert isinstance(m.sharpe_ratio, float)


def test_max_drawdown_through_intraday_dip() -> None:
    prices = [
        (date(2024, 1, 2), Decimal(100)),
        (date(2024, 1, 3), Decimal(90)),
        (date(2024, 1, 4), Decimal(110)),
        (date(2024, 1, 5), Decimal(110)),
    ]
    trades = [
        Trade(
            entry_date=date(2024, 1, 2),
            exit_date=date(2024, 1, 5),
            entry_price=Decimal(100),
            exit_price=Decimal(110),
        )
    ]
    m = compute_metrics(prices, trades)
    assert m.max_drawdown == 0.10


def test_sharpe_ratio_zero_with_single_daily_return() -> None:
    prices = [
        (date(2024, 1, 2), Decimal(100)),
        (date(2024, 1, 3), Decimal(110)),
    ]
    trades = [
        Trade(
            entry_date=date(2024, 1, 2),
            exit_date=date(2024, 1, 3),
            entry_price=Decimal(100),
            exit_price=Decimal(110),
        )
    ]
    m = compute_metrics(prices, trades)
    assert m.sharpe_ratio == 0.0
    assert isinstance(m.sharpe_ratio, float)
