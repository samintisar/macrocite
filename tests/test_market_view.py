from datetime import date

import pytest
from pytest import approx

from signalbench.strategy.indicators import sma, wilder_atr
from signalbench.strategy.market_view import LookAheadError, MarketView
from strategy_helpers import (
    load_test_config,
    make_bar,
    make_market,
    trend_bars,
    weekdays,
)

DAYS = weekdays(date(2023, 1, 2), 260)
CONFIG = load_test_config()


def _market(**kwargs: object) -> MarketView:
    bars = {"AAA": trend_bars(DAYS, 100.0, 0.2)}
    return make_market(bars, trend_bars(DAYS, 300.0, 0.5), DAYS, CONFIG, **kwargs)


def test_snapshot_is_the_as_of_bar_with_its_indicators() -> None:
    view = _market().at(DAYS[250])
    snap = view.snapshot("AAA")
    assert snap is not None
    assert snap.date == DAYS[250]
    assert snap.close == approx(150.0)
    closes = [100.0 + 0.2 * i for i in range(260)]
    assert snap.sma[50] == approx(sma(closes, 50)[250])
    assert snap.sma[200] == approx(sma(closes, 200)[250])
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    assert snap.atr == approx(wilder_atr(highs, lows, closes, 14)[250])
    assert snap.prior_max_close == approx(closes[249])
    assert snap.prior_mean_volume == approx(1_000_000.0)
    assert snap.min_low == approx(closes[248] - 1)
    assert snap.prev_high == approx(closes[249] + 1)


def test_future_access_raises() -> None:
    view = _market().at(DAYS[100])
    with pytest.raises(LookAheadError):
        view.snapshot("AAA", DAYS[101])
    with pytest.raises(LookAheadError):
        view.benchmark(DAYS[101])
    assert view.snapshot("AAA", DAYS[99]) is not None


def test_missing_bar_returns_the_latest_earlier_bar() -> None:
    bars = trend_bars(DAYS, 100.0, 0.2)
    del bars[120]
    market = make_market({"AAA": bars}, trend_bars(DAYS, 300.0, 0.5), DAYS, CONFIG)
    snap = market.at(DAYS[120]).snapshot("AAA")
    assert snap is not None and snap.date == DAYS[119]
    assert market.at(DAYS[120]).is_active("AAA") is False  # no bar today


def test_regime_needs_200_sessions_and_close_above_the_average() -> None:
    market = _market()
    assert market.at(DAYS[198]).regime_on() is False  # SMA200 undefined
    assert market.at(DAYS[199]).regime_on() is True
    falling = trend_bars(DAYS, 300.0, -0.5)
    down = make_market({"AAA": trend_bars(DAYS, 100.0, 0.2)}, falling, DAYS, CONFIG)
    assert down.at(DAYS[250]).regime_on() is False


def test_liquidity_is_active_at_exactly_the_threshold() -> None:
    at_threshold = [make_bar(day, 100.0, volume=500_000) for day in DAYS[:30]]
    below = [make_bar(day, 100.0, volume=499_999) for day in DAYS[:30]]
    market = make_market(
        {"AT": at_threshold, "BELOW": below}, trend_bars(DAYS, 300.0, 0.5), DAYS, CONFIG
    )
    view = market.at(DAYS[25])
    assert view.is_active("AT") is True  # median 100 * 500,000 = 50,000,000
    assert view.is_active("BELOW") is False
    assert market.at(DAYS[18]).is_active("AT") is False  # fewer than 20 sessions


def test_earnings_within_counts_sessions_after_as_of() -> None:
    # DAYS[10] is a Monday; the next three sessions are Tue, Wed, Thu.
    market = _market(earnings={"AAA": [DAYS[10], DAYS[13]]})
    view = market.at(DAYS[10])
    assert view.earnings_within("AAA", 3) is True  # DAYS[13] is D+3
    assert view.earnings_within("AAA", 2) is False  # an event on as_of itself does not count
    assert market.at(DAYS[13]).earnings_within("AAA", 3) is False


def test_earnings_on_a_non_session_day_counts_inside_the_window() -> None:
    friday = DAYS[4]
    saturday = date(2023, 1, 7)
    view = _market(earnings={"AAA": [saturday]}).at(friday)
    assert view.earnings_within("AAA", 1) is True  # Sat falls before Monday (D+1)


def test_earnings_window_past_the_session_list_is_an_error() -> None:
    view = _market().at(DAYS[-2])
    with pytest.raises(ValueError, match="session list ends"):
        view.earnings_within("AAA", 3)


def test_bars_out_of_order_are_rejected() -> None:
    bars = trend_bars(DAYS[:5], 100.0, 1.0)
    with pytest.raises(ValueError, match="increasing"):
        make_market({"AAA": bars[::-1]}, trend_bars(DAYS, 300.0, 0.5), DAYS, CONFIG)
