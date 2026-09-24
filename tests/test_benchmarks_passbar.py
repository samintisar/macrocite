from dataclasses import replace
from datetime import date

import pytest
from pytest import approx

from signalbench.backtest.benchmarks import benchmark_stats, buy_and_hold, equal_weight
from signalbench.backtest.metrics import RunMetrics, TradeStats
from signalbench.backtest.passbar import Criterion, evaluate_pass_bar, passes
from strategy_helpers import load_test_config, make_bar, weekdays

PARAMS = load_test_config().backtest
DAYS = weekdays(date(2012, 1, 2), 4)


def test_buy_and_hold_normalises_to_the_first_close() -> None:
    bars = [make_bar(DAYS[0], 50.0), make_bar(DAYS[1], 55.0), make_bar(DAYS[3], 45.0)]
    assert buy_and_hold(bars, DAYS) == [1.0, 1.1, 1.1, approx(0.9)]  # DAYS[2] carries 55
    with pytest.raises(ValueError, match="no bar"):
        buy_and_hold(bars[1:], DAYS)


def test_survivor_benchmark_holds_names_with_a_first_day_bar() -> None:
    a = [make_bar(day, close) for day, close in zip(DAYS, [10.0, 12.0, 11.0, 13.0], strict=True)]
    b = [make_bar(day, close) for day, close in zip(DAYS, [20.0, 20.0, 30.0, 10.0], strict=True)]
    late = [make_bar(DAYS[1], 5.0), make_bar(DAYS[2], 50.0)]  # listed after the start: excluded
    curve = equal_weight([a, b, late], DAYS)
    assert curve == [1.0, approx((1.2 + 1.0) / 2), approx((1.1 + 1.5) / 2), approx((1.3 + 0.5) / 2)]


def test_benchmark_stats() -> None:
    stats = benchmark_stats("QQQ buy-and-hold", [1.0, 1.2, 0.9, 1.1], DAYS)
    assert stats.name == "QQQ buy-and-hold"
    assert stats.total_return == approx(0.1)
    assert stats.max_drawdown == approx(0.25)


def _metrics(**changes: object) -> RunMetrics:
    base = RunMetrics(
        trades=30, win_rate=0.5, mean_r=0.2, median_r=0.1, average_hold=6.0,
        trades_h1=15, mean_r_h1=0.1, trades_h2=15, mean_r_h2=0.3,
        total_return=0.5, cagr=0.03, sharpe=0.8, max_drawdown=0.2, exposure_pct=0.4,
        pauses=0, skips_by_reason={}, open_positions_at_end=0,
        recent=TradeStats(0, 0.0, 0.0, 0.0, 0.0),
    )
    return replace(base, **changes)


def test_every_criterion_at_its_exact_threshold() -> None:
    at = evaluate_pass_bar(_metrics(trades=30, mean_r=0.10, mean_r_h1=0.0, sharpe=0.8), 0.8, PARAMS)
    assert at["trades"] == Criterion(30.0, 30.0, True)  # >= 30
    assert at["mean_r"] == Criterion(0.10, 0.10, False)  # > +0.10 is strict
    assert at["mean_r_halves"] == Criterion(0.0, 0.0, False)  # > 0 in both halves
    assert at["sharpe"] == Criterion(0.8, 0.8, True)  # >= QQQ's Sharpe
    assert passes(at) is False


def test_just_inside_and_outside_each_threshold() -> None:
    assert evaluate_pass_bar(_metrics(trades=29), 0.0, PARAMS)["trades"].passed is False
    assert evaluate_pass_bar(_metrics(mean_r=0.1000001), 0.0, PARAMS)["mean_r"].passed is True
    halves = evaluate_pass_bar(_metrics(mean_r_h1=1e-9, mean_r_h2=-1e-9), 0.0, PARAMS)
    assert halves["mean_r_halves"] == Criterion(-1e-9, 0.0, False)
    assert evaluate_pass_bar(_metrics(sharpe=0.79), 0.8, PARAMS)["sharpe"].passed is False


def test_all_four_pass() -> None:
    assert passes(evaluate_pass_bar(_metrics(), 0.5, PARAMS)) is True
