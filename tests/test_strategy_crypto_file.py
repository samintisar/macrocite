"""Spec 09: data/strategy_crypto-v1.yaml is data/strategy_v2-none-cash.yaml with only the
declared fields changed, and its cost is Kraken's top published fee."""

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from signalbench.backtest.preregistration import KRAKEN_TOP_FEE
from signalbench.strategy.config import load_strategy_config

DATA = Path(__file__).resolve().parents[1] / "data"
CHANGES = (
    ("version: v2-none-cash\n", "version: crypto-v1\n"),
    ("cost_per_side: 0.002\n", "cost_per_side: 0.004\n"),
    ("  symbol: QQQ\n", "  symbol: XBTUSD\n"),
    ("  min_median_traded_value_usd: 50000000\n", "  min_median_traded_value_usd: 0\n"),
    ("  start: 2012-01-01\n", "  start: 2015-01-01\n"),
    ("  h1_end: 2018-12-31\n", "  h1_end: 2020-12-31\n"),
    ("  h2_start: 2019-01-01\n", "  h2_start: 2021-01-01\n"),
    ("  recent_since: 2026-09-15\n", "  recent_since: 2026-07-01\n"),
)


def _crypto() -> Path:
    path = DATA / "strategy_crypto-v1.yaml"
    if not path.exists():
        pytest.skip(f"{path.name} is written by spec 09 Task 9 and committed on its own")
    return path


def _body(path: Path) -> str:
    """The file without its header comment lines."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return "".join(f"{line}\n" for line in lines if not line.startswith("#"))


def test_crypto_v1_is_v2_none_cash_with_only_the_declared_fields_changed() -> None:
    path = _crypto()
    expected = _body(DATA / "strategy_v2-none-cash.yaml")
    for old, new in CHANGES:
        assert expected.count(old) == 1, old
        expected = expected.replace(old, new)
    assert _body(path) == expected
    first = path.read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("# Spec 09: Breakout on BTC and ETH daily candles from Kraken.")


def test_crypto_v1_parses_to_v2_none_cash_plus_its_changes() -> None:
    v2, _sha = load_strategy_config(DATA / "strategy_v2-none-cash.yaml")
    config, _sha = load_strategy_config(_crypto())
    assert config == replace(
        v2,
        version="crypto-v1",
        cost_per_side=KRAKEN_TOP_FEE,
        regime_symbol="XBTUSD",
        liquidity_min_traded_value=0.0,
        backtest=replace(
            v2.backtest,
            start=date(2015, 1, 1),
            h1_end=date(2020, 12, 31),
            h2_start=date(2021, 1, 1),
            recent_since=date(2026, 7, 1),
        ),
    )
