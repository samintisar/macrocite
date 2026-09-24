from datetime import date
from pathlib import Path

import pytest
import yaml

from signalbench.strategy.config import (
    ConfigError,
    config_sha256,
    load_strategy_config,
    parse_strategy_config,
)

FIXTURE = Path(__file__).parent / "fixtures" / "strategy_test.yaml"


def _raw() -> dict[str, object]:
    loaded = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_fixture_loads_every_spec_parameter() -> None:
    config, sha = load_strategy_config(FIXTURE)
    assert config.version == "test"
    assert (config.start_equity, config.risk_pct) == (100.0, 0.02)
    assert (config.max_positions, config.max_per_sector) == (3, 2)
    assert (config.pause_drawdown, config.auto_resume_sessions) == (0.15, 10)
    assert (config.gap_up_limit, config.cost_per_side) == (0.01, 0.002)
    assert config.setup_priority == ("pullback", "breakout", "sentiment")
    assert config.enabled_setups == config.setup_priority
    assert (config.regime_symbol, config.regime_sma) == ("QQQ", 200)
    assert (config.atr_period, config.rsi_period) == (14, 2)
    assert (config.liquidity_min_traded_value, config.liquidity_sessions) == (50_000_000.0, 20)
    assert (config.earnings_blackout_sessions, config.earnings_exit_sessions) == (3, 2)
    assert config.pullback.trend_sma_fast == 50 and config.pullback.trend_sma_slow == 200
    assert config.pullback.rsi_max == 10.0 and config.pullback.stop_atr_mult == 0.5
    assert config.pullback.target_r == 2.0 and config.pullback.time_limit == 10
    assert config.breakout.lookback == 20 and config.breakout.volume_lookback == 50
    assert config.breakout.volume_mult == 1.5 and config.breakout.trail_atr_mult == 3.0
    assert config.breakout.time_limit == 30
    assert config.sentiment.trend_sma == 20 and config.sentiment.time_limit == 10
    assert config.backtest.start == date(2012, 1, 1)
    assert (config.backtest.h1_end, config.backtest.h2_start) == (date(2018, 12, 31), date(2019, 1, 1))
    assert config.backtest.recent_since == date(2026, 9, 15)
    assert (config.backtest.min_trades, config.backtest.min_mean_r) == (30, 0.10)
    assert len(sha) == 64


def test_sha_ignores_line_endings() -> None:
    assert config_sha256(b"a: 1\r\nb: 2\r\n") == config_sha256(b"a: 1\nb: 2\n")
    assert config_sha256(b"a: 1\n") != config_sha256(b"a: 2\n")


def test_unknown_key_is_rejected() -> None:
    raw = _raw()
    raw["risk_pct_typo"] = 0.03
    with pytest.raises(ConfigError, match="unknown keys \\['risk_pct_typo'\\]"):
        parse_strategy_config(raw)


def test_missing_nested_key_is_rejected() -> None:
    raw = _raw()
    setups = raw["setups"]
    assert isinstance(setups, dict)
    del setups["pullback"]["rsi_max"]
    with pytest.raises(ConfigError, match="config.setups.pullback: missing key 'rsi_max'"):
        parse_strategy_config(raw)


def test_cost_below_the_floor_is_rejected() -> None:
    raw = _raw()
    raw["cost_per_side"] = 0.001
    with pytest.raises(ConfigError, match="cost_per_side"):
        parse_strategy_config(raw)


def test_setup_priority_must_name_each_setup_once() -> None:
    raw = _raw()
    raw["setup_priority"] = ["pullback", "pullback", "breakout"]
    with pytest.raises(ConfigError, match="setup_priority"):
        parse_strategy_config(raw)


def test_with_setups_keeps_priority_order() -> None:
    config, _ = load_strategy_config(FIXTURE)
    assert config.with_setups(("breakout", "pullback")).enabled_setups == ("pullback", "breakout")
    assert config.with_setups(("breakout",)).enabled_setups == ("breakout",)
    assert config.sma_periods() == frozenset({20, 50, 200})
