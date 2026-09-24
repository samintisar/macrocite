from pytest import approx

from signalbench.strategy.setups import breakout, first_signal, pullback, sentiment
from strategy_helpers import load_test_config, make_snapshot

CONFIG = load_test_config()
UPTREND = {20: 98.0, 50: 95.0, 200: 90.0}


def test_pullback_fires_with_stop_below_the_three_session_low() -> None:
    snap = make_snapshot(close=100.0, sma=UPTREND, rsi=5.0, min_low=97.0, atr=2.0)
    signal = pullback(snap, CONFIG)
    assert signal is not None
    assert signal.setup == "pullback"
    assert signal.stop == approx(97.0 - 0.5 * 2.0)
    assert (signal.target_r, signal.time_limit) == (2.0, 10)


def test_pullback_needs_rsi_strictly_below_10() -> None:
    assert pullback(make_snapshot(sma=UPTREND, rsi=10.0), CONFIG) is None
    assert pullback(make_snapshot(sma=UPTREND, rsi=9.99), CONFIG) is not None


def test_pullback_needs_close_above_sma50_above_sma200() -> None:
    assert pullback(make_snapshot(sma={20: 1.0, 50: 95.0, 200: 96.0}, rsi=5.0), CONFIG) is None
    assert pullback(make_snapshot(sma={20: 1.0, 50: 100.0, 200: 90.0}, rsi=5.0), CONFIG) is None
    assert pullback(make_snapshot(sma={20: 1.0, 50: None, 200: 90.0}, rsi=5.0), CONFIG) is None


def test_breakout_fires_on_new_high_with_volume() -> None:
    snap = make_snapshot(
        close=105.0, sma=UPTREND, prior_max_close=104.0, volume=1_500_000.0,
        prior_mean_volume=1_000_000.0, atr=2.5,
    )
    signal = breakout(snap, CONFIG)
    assert signal is not None
    assert signal.stop == approx(105.0 - 2 * 2.5)
    assert (signal.target_r, signal.time_limit) == (None, 30)


def test_breakout_rejects_equal_high_or_thin_volume() -> None:
    base = {"close": 105.0, "sma": UPTREND, "prior_mean_volume": 1_000_000.0}
    assert breakout(make_snapshot(**base, prior_max_close=105.0, volume=2e6), CONFIG) is None
    assert breakout(make_snapshot(**base, prior_max_close=104.0, volume=1_499_999.0), CONFIG) is None


def test_sentiment_needs_a_trigger_trend_and_close_above_prior_high() -> None:
    snap = make_snapshot(close=100.0, sma=UPTREND, prev_high=99.5, atr=2.0)
    assert sentiment(snap, CONFIG, triggered=False) is None
    signal = sentiment(snap, CONFIG, triggered=True)
    assert signal is not None
    assert signal.stop == approx(96.0) and signal.target_r == 2.0 and signal.time_limit == 10
    assert sentiment(make_snapshot(close=100.0, sma=UPTREND, prev_high=100.0), CONFIG, True) is None


def test_first_signal_keeps_setup_priority_and_enabled_setups() -> None:
    both = make_snapshot(
        close=105.0, sma=UPTREND, rsi=5.0, min_low=103.0, prior_max_close=104.0,
        volume=2_000_000.0, prior_mean_volume=1_000_000.0,
    )
    first = first_signal(both, CONFIG, sentiment_triggered=False)
    assert first is not None and first.setup == "pullback"
    only_breakout = CONFIG.with_setups(("breakout",))
    second = first_signal(both, only_breakout, sentiment_triggered=False)
    assert second is not None and second.setup == "breakout"
    assert first_signal(make_snapshot(), CONFIG, sentiment_triggered=False) is None
