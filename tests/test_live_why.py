"""Spec 05, the "Why" line: a fixed template filled from the Snapshot that fired Breakout, with
the lookbacks from the config. Pure: no network, no model."""

from dataclasses import replace
from pathlib import Path

import pytest

from signalbench.live.why import why_line
from signalbench.strategy.config import load_strategy_config
from strategy_helpers import make_snapshot

CONFIG = load_strategy_config(Path(__file__).parents[1] / "data" / "strategy_v2-none-cash.yaml")[0]


def test_the_spec_example() -> None:
    snap = make_snapshot(close=181.515, volume=2_300_000.0, prior_mean_volume=1_000_000.0)
    assert why_line(snap, CONFIG.breakout) == (
        "Closed at a 20-session high (US$181.52) on 2.3× its 50-session average volume, "
        "above its 50-session average."
    )


def test_rounding_is_half_up_on_the_printed_value() -> None:
    snap = make_snapshot(close=1830.125, volume=2_250_000.0, prior_mean_volume=1_000_000.0)
    assert why_line(snap, CONFIG.breakout) == (
        "Closed at a 20-session high (US$1,830.13) on 2.3× its 50-session average volume, "
        "above its 50-session average."
    )
    snap = make_snapshot(close=99.994, volume=1_549_999.0, prior_mean_volume=1_000_000.0)
    assert "(US$99.99) on 1.5×" in why_line(snap, CONFIG.breakout)


def test_the_lookbacks_come_from_the_config() -> None:
    params = replace(CONFIG.breakout, lookback=55, volume_lookback=30, trend_sma=100)
    snap = make_snapshot(close=50.0, volume=3_000_000.0, prior_mean_volume=1_000_000.0)
    assert why_line(snap, params) == (
        "Closed at a 55-session high (US$50.00) on 3.0× its 30-session average volume, "
        "above its 100-session average."
    )


def test_a_snapshot_without_a_volume_average_is_refused() -> None:
    with pytest.raises(ValueError, match="volume average"):
        why_line(make_snapshot(prior_mean_volume=None), CONFIG.breakout)
