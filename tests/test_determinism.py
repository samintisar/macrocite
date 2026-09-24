from datetime import date
from decimal import Decimal

from signalbench.backtest.fingerprint import (
    data_fingerprint,
    series_summary,
    signal_set_fingerprint,
)
from signalbench.backtest.metrics import RunMetrics, Trade, compute_metrics, run_metrics
from signalbench.backtest.simulator import simulate
from signalbench.strategy.readings import NullReadingsView
from strategy_helpers import (
    load_test_config,
    make_bar,
    make_market,
    pullback_closes,
    series,
    trend_bars,
    weekdays,
)


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


def test_data_fingerprint_is_order_free_and_sensitive_to_every_field() -> None:
    days = [date(2024, 1, 2), date(2024, 1, 3)]
    aaa = [make_bar(days[0], 10.0), make_bar(days[1], 11.0)]
    qqq = [make_bar(days[0], 300.0), make_bar(days[1], 301.0)]
    assert series_summary("AAA", aaa) == "AAA|2024-01-02|2024-01-03|2|21.0000"
    base = data_fingerprint([("AAA", aaa), ("QQQ", qqq)], [])
    assert base == data_fingerprint([("QQQ", qqq), ("AAA", aaa)], [])
    assert len(base) == 64
    assert base != data_fingerprint([("AAA", aaa[:1]), ("QQQ", qqq)], [])  # row count, last date
    assert base != data_fingerprint(
        [("AAA", [aaa[0], make_bar(days[1], 11.0001)]), ("QQQ", qqq)], []
    )
    assert base != data_fingerprint([("AAB", aaa), ("QQQ", qqq)], [])


def test_data_fingerprint_covers_the_earnings_dates() -> None:
    bars = [("AAA", [make_bar(date(2024, 1, 2), 10.0)])]
    one = [("AAA", date(2024, 1, 25)), ("BBB", date(2024, 2, 1))]
    base = data_fingerprint(bars, one)
    assert base == data_fingerprint(bars, list(reversed(one)))  # order free
    assert base == data_fingerprint(bars, [*one, one[0]])  # a repeated date is the same set
    assert base != data_fingerprint(bars, [])
    assert base != data_fingerprint(bars, one[:1])  # a date removed
    assert base != data_fingerprint(bars, [("AAA", date(2024, 1, 26)), one[1]])  # a date moved
    assert base != data_fingerprint(bars, [("BBB", date(2024, 1, 25)), one[1]])  # another symbol


def test_two_simulations_give_identical_metrics_and_fingerprints() -> None:
    config = load_test_config().with_setups(("pullback", "breakout"))
    days = weekdays(date(2023, 1, 2), 300)
    bars = {"AAA": series(days, pullback_closes(len(days))), "BBB": trend_bars(days, 50.0, 0.1)}
    qqq = trend_bars(days, 300.0, 0.5)

    def run() -> tuple[RunMetrics, str]:
        market = make_market(bars, qqq, days, config)
        result = simulate(market, NullReadingsView(), config, days[220], days[290])
        fingerprint = data_fingerprint([*bars.items(), ("QQQ", qqq)], [])
        return run_metrics(result, config.backtest), fingerprint

    first, second = run(), run()
    assert first[0].trades >= 1  # the fixture trades, so the comparison means something
    assert first == second
