"""The six spec 06 variants: each is data/strategy_v1.yaml with only `version`, the Breakout
`time_limit`, and `cash_vehicle` changed, under a post-hoc header (spec 06, Variants)."""

from dataclasses import replace
from pathlib import Path

import pytest

from signalbench.strategy.config import CashVehicle, load_strategy_config
from signalbench.strategy.spread import load_spread_survey

DATA = Path(__file__).resolve().parents[1] / "data"
QQQ = CashVehicle(symbol="QQQ", cost_per_side=0.002)
VARIANTS: dict[str, tuple[int | None, CashVehicle | None]] = {
    "v2-t30-cash": (30, None),
    "v2-t30-qqq": (30, QQQ),
    "v2-t60-cash": (60, None),
    "v2-t60-qqq": (60, QQQ),
    "v2-none-cash": (None, None),
    "v2-none-qqq": (None, QQQ),
}


def _variant(version: str) -> Path:
    path = DATA / f"strategy_{version}.yaml"
    if not path.exists():
        pytest.skip(f"{path.name} is written by spec 06 Task 10 and committed after review")
    return path


def _body(path: Path) -> str:
    """The file without its header comment lines."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return "".join(f"{line}\n" for line in lines if not line.startswith("#"))


def _replace_once(text: str, old: str, new: str) -> str:
    assert text.count(old) == 1, old
    return text.replace(old, new)


@pytest.mark.parametrize("version", list(VARIANTS))
def test_each_variant_is_v1_with_only_the_declared_fields_changed(version: str) -> None:
    path = _variant(version)
    time_limit, vehicle = VARIANTS[version]
    expected = _replace_once(_body(DATA / "strategy_v1.yaml"), "version: v1\n", f"version: {version}\n")
    written = "null" if time_limit is None else str(time_limit)
    expected = _replace_once(expected, "    time_limit: 30\n", f"    time_limit: {written}\n")
    if vehicle is not None:
        expected = _replace_once(
            expected,
            "cost_per_side: 0.002\nsetup_priority",
            "cost_per_side: 0.002\ncash_vehicle:\n  symbol: QQQ\n  cost_per_side: 0.002\nsetup_priority",
        )
    assert _body(path) == expected
    first = path.read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("# Post-hoc v2 variant (spec 06):")


@pytest.mark.parametrize("version", list(VARIANTS))
def test_each_variant_parses_to_v1_plus_its_changes(version: str) -> None:
    path = _variant(version)
    time_limit, vehicle = VARIANTS[version]
    v1, v1_sha = load_strategy_config(DATA / "strategy_v1.yaml")
    config, sha = load_strategy_config(path)
    assert config == replace(
        v1, version=version, breakout=replace(v1.breakout, time_limit=time_limit),
        cash_vehicle=vehicle,
    )
    assert config.cost_per_side == load_spread_survey(DATA / "cdr_spread_survey.yaml").cost_per_side
    assert sha != v1_sha
