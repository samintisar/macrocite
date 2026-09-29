"""The paper portfolios file (spec 07, Portfolios) and its loader."""

from dataclasses import replace
from pathlib import Path

import pytest

from signalbench.paper.portfolios import PaperFileError, PortfolioSpec, load_paper_file
from signalbench.strategy.config import load_strategy_config

REPO = Path(__file__).resolve().parents[1]
PAPER_V1 = REPO / "data" / "paper_v1.yaml"
EXPECTED = [
    PortfolioSpec("v1-breakout", "data/strategy_v1.yaml", "breakout"),
    PortfolioSpec("v2-t30-cash", "data/strategy_v2-t30-cash.yaml", "breakout"),
    PortfolioSpec("v2-t30-qqq", "data/strategy_v2-t30-qqq.yaml", "breakout"),
    PortfolioSpec("v2-t60-cash", "data/strategy_v2-t60-cash.yaml", "breakout"),
    PortfolioSpec("v2-t60-qqq", "data/strategy_v2-t60-qqq.yaml", "breakout"),
    PortfolioSpec("v2-none-cash", "data/strategy_v2-none-cash.yaml", "breakout"),
    PortfolioSpec("v2-none-qqq", "data/strategy_v2-none-qqq.yaml", "breakout"),
]
GOOD = "portfolios:\n  - name: p1\n    config: data/strategy_test.yaml\n    setup: pullback\n"


def _paper_v1() -> Path:
    if not PAPER_V1.exists():
        pytest.skip("data/paper_v1.yaml is written by spec 07 Task 3 and committed after review")
    return PAPER_V1


def test_paper_v1_lists_the_seven_pre_registered_portfolios() -> None:
    assert load_paper_file(_paper_v1()) == EXPECTED


def test_every_paper_v1_config_parses_and_the_twins_share_their_rules() -> None:
    configs = {spec.name: load_strategy_config(REPO / spec.config)[0] for spec in load_paper_file(_paper_v1())}
    twin = configs["v2-t30-cash"]
    assert replace(twin, version="v1") == configs["v1-breakout"]
    assert {config.start_equity for config in configs.values()} == {100.0}


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "paper_test.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_well_formed_file_loads(tmp_path: Path) -> None:
    assert load_paper_file(_write(tmp_path, GOOD)) == [
        PortfolioSpec("p1", "data/strategy_test.yaml", "pullback")
    ]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("portfolios: []\n", r"paper_test.yaml.portfolios: expected a non-empty list"),
        (GOOD + "extra: 1\n", "expected exactly one top-level key, 'portfolios'"),
        (GOOD.replace("name: p1", "name: P 1"), r"portfolios\[0\].name: expected lowercase"),
        (GOOD.replace("data/strategy_test.yaml", "strategy_test.yaml"), r"\[0\].config: expected data/strategy_"),
        (GOOD.replace("pullback", "sentiment"), r"\[0\].setup: expected one of pullback, breakout, combined"),
        (GOOD.replace("    setup: pullback\n", ""), r"\[0\]: expected exactly the keys name, config, setup"),
        (GOOD + GOOD.removeprefix("portfolios:\n"), r"duplicate names \['p1'\]"),
    ],
)
def test_a_malformed_file_is_refused(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(PaperFileError, match=message):
        load_paper_file(_write(tmp_path, text))
