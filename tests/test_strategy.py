from datetime import UTC, date, datetime
from decimal import Decimal

from signalbench.backtest.strategy import Trade, build_trades


def test_known_trade_list() -> None:
    prices = [
        (date(2024, 1, 2), Decimal(100)),
        (date(2024, 1, 3), Decimal(101)),
        (date(2024, 1, 4), Decimal(102)),
        (date(2024, 1, 5), Decimal(103)),
        (date(2024, 1, 8), Decimal(104)),
    ]
    signals = [
        {
            "sentiment": 0.8,
            "published_at": datetime(2024, 1, 2, 15, 0, tzinfo=UTC),
        }
    ]
    trades = build_trades(
        prices=prices,
        signals=signals,
        sentiment_threshold=0.5,
        holding_days=3,
        stop_loss=None,
        take_profit=None,
    )
    assert trades == [
        Trade(
            entry_date=date(2024, 1, 2),
            exit_date=date(2024, 1, 5),
            entry_price=Decimal(100),
            exit_price=Decimal(103),
        )
    ]


def test_stop_loss_exits_before_holding_days() -> None:
    prices = [
        (date(2024, 1, 2), Decimal(100)),
        (date(2024, 1, 3), Decimal(96)),
        (date(2024, 1, 4), Decimal(95)),
        (date(2024, 1, 5), Decimal(94)),
    ]
    signals = [
        {
            "sentiment": 0.8,
            "published_at": datetime(2024, 1, 2, 10, 0, tzinfo=UTC),
        }
    ]
    trades = build_trades(
        prices=prices,
        signals=signals,
        sentiment_threshold=0.5,
        holding_days=3,
        stop_loss=-0.03,
        take_profit=None,
    )
    assert trades == [
        Trade(
            entry_date=date(2024, 1, 2),
            exit_date=date(2024, 1, 3),
            entry_price=Decimal(100),
            exit_price=Decimal(96),
        )
    ]


def test_no_overlapping_trades() -> None:
    prices = [
        (date(2024, 1, 2), Decimal(100)),
        (date(2024, 1, 3), Decimal(101)),
        (date(2024, 1, 4), Decimal(102)),
        (date(2024, 1, 5), Decimal(103)),
        (date(2024, 1, 8), Decimal(104)),
        (date(2024, 1, 9), Decimal(105)),
        (date(2024, 1, 10), Decimal(106)),
        (date(2024, 1, 11), Decimal(107)),
    ]
    signals = [
        {
            "sentiment": 0.8,
            "published_at": datetime(2024, 1, 2, 10, 0, tzinfo=UTC),
        },
        {
            "sentiment": 0.9,
            "published_at": datetime(2024, 1, 3, 10, 0, tzinfo=UTC),
        },
        {
            "sentiment": 0.7,
            "published_at": datetime(2024, 1, 8, 10, 0, tzinfo=UTC),
        },
    ]
    trades = build_trades(
        prices=prices,
        signals=signals,
        sentiment_threshold=0.5,
        holding_days=3,
        stop_loss=None,
        take_profit=None,
    )
    assert len(trades) == 2
    assert trades[0].entry_date == date(2024, 1, 2)
    assert trades[0].exit_date == date(2024, 1, 5)
    assert trades[1].entry_date == date(2024, 1, 8)
    assert trades[1].exit_date == date(2024, 1, 11)
