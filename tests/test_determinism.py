from datetime import date
from decimal import Decimal

from signalbench.backtest.fingerprint import signal_set_fingerprint
from signalbench.backtest.metrics import Trade, compute_metrics


def test_fingerprint_stable() -> None:
    a = signal_set_fingerprint(["b", "a"])
    b = signal_set_fingerprint(["a", "b"])
    assert a == b
    assert len(a) == 64


def test_two_run_metrics_equal() -> None:
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
    assert compute_metrics(prices, trades) == compute_metrics(prices, trades)
