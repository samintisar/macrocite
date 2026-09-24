from pytest import approx

from signalbench.strategy.indicators import (
    prior_max,
    prior_mean,
    rolling_median,
    rolling_min,
    sma,
    true_range,
    wilder_atr,
    wilder_rsi,
)


def test_sma_by_hand() -> None:
    assert sma([1.0, 2.0, 3.0, 4.0, 5.0], 3) == [None, None, 2.0, 3.0, 4.0]
    assert sma([2.0, 4.0], 3) == [None, None]


def test_wilder_rsi2_by_hand() -> None:
    # changes: +1, -0.5, +1.5, -1
    # i=2 seed: gain (1 + 0)/2 = 0.5, loss (0 + 0.5)/2 = 0.25 -> RS 2 -> 66.667
    # i=3: gain (0.5 + 1.5)/2 = 1.0, loss (0.25 + 0)/2 = 0.125 -> RS 8 -> 88.889
    # i=4: gain (1.0 + 0)/2 = 0.5, loss (0.125 + 1)/2 = 0.5625 -> RS 0.8889 -> 47.059
    out = wilder_rsi([10.0, 11.0, 10.5, 12.0, 11.0], 2)
    assert out[:2] == [None, None]
    assert out[2] == approx(100 - 100 / 3)
    assert out[3] == approx(100 - 100 / 9)
    assert out[4] == approx(100 - 100 / (1 + 0.5 / 0.5625))


def test_wilder_rsi_edge_cases() -> None:
    assert wilder_rsi([1.0, 2.0, 3.0], 2)[2] == 100.0  # no losses
    assert wilder_rsi([5.0, 5.0, 5.0], 2)[2] == 50.0  # no movement
    assert wilder_rsi([1.0, 2.0], 2) == [None, None]


def test_true_range_uses_previous_close() -> None:
    highs = [11.0, 12.0, 10.0]
    lows = [9.0, 11.5, 8.0]
    closes = [10.0, 11.8, 9.0]
    # bar 1: max(0.5, |12 - 10|, |11.5 - 10|) = 2.0; bar 2: max(2, |10 - 11.8|, |8 - 11.8|) = 3.8
    assert true_range(highs, lows, closes) == [None, 2.0, approx(3.8)]


def test_wilder_atr_by_hand() -> None:
    # period 3. TRs for bars 1..5: 2, 1, 3, 2, 4 (ranges only; closes flat at 10).
    highs = [11.0, 11.0, 10.5, 11.5, 11.0, 12.0]
    lows = [9.0, 9.0, 9.5, 8.5, 9.0, 8.0]
    closes = [10.0] * 6
    out = wilder_atr(highs, lows, closes, 3)
    assert out[:3] == [None, None, None]
    assert out[3] == approx((2 + 1 + 3) / 3)  # seed = 2.0
    assert out[4] == approx((2.0 * 2 + 2) / 3)  # 2.0
    assert out[5] == approx((2.0 * 2 + 4) / 3)  # 2.6667


def test_wilder_atr14_on_constant_range() -> None:
    n = 20
    out = wilder_atr([11.0] * n, [9.0] * n, [10.0] * n, 14)
    assert out[13] is None
    assert out[14] == approx(2.0)
    assert out[19] == approx(2.0)


def test_prior_max_excludes_today() -> None:
    assert prior_max([1.0, 3.0, 2.0, 5.0, 4.0], 2) == [None, None, 3.0, 3.0, 5.0]


def test_prior_mean_excludes_today() -> None:
    assert prior_mean([1.0, 2.0, 3.0, 10.0], 2) == [None, None, 1.5, 2.5]


def test_rolling_min_and_median_include_today() -> None:
    assert rolling_min([5.0, 3.0, 4.0, 6.0], 3) == [None, None, 3.0, 3.0]
    assert rolling_median([5.0, 1.0, 3.0, 2.0], 3) == [None, None, 3.0, 2.0]
