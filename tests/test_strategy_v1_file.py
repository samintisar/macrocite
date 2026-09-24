"""The pre-registered config must carry the cost computed from the committed spread survey."""

from pathlib import Path

import pytest

from signalbench.strategy.config import load_strategy_config
from signalbench.strategy.spread import load_spread_survey

DATA = Path(__file__).resolve().parents[1] / "data"
STRATEGY_V1 = DATA / "strategy_v1.yaml"
SURVEY = DATA / "cdr_spread_survey.yaml"


def test_strategy_v1_cost_matches_the_spread_survey() -> None:
    if not STRATEGY_V1.exists():
        pytest.skip(
            "data/strategy_v1.yaml is not committed yet; it is created after the owner "
            "fills and approves data/cdr_spread_survey.yaml (spec 02 pre-registration)"
        )
    config, _ = load_strategy_config(STRATEGY_V1)
    assert config.version == "v1"
    assert config.cost_per_side == load_spread_survey(SURVEY).cost_per_side
