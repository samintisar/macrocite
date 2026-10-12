from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

from signalbench.paper.paper_file import (
    PaperError,
    PaperFile,
    ReviewRules,
    add_months,
    load_paper_file,
    parse_paper_file,
)

SHA = "a" * 64
VALID = f"""name: crypto-v1-paper
strategy: data/strategy_crypto-v1.yaml
strategy_sha256: {SHA}
setup: breakout
start: 2026-10-13
review:
  months: 12
  max_late_days: 7
  max_drawdown: 0.35
"""


def _raw(**changes: Any) -> dict[str, Any]:
    raw: dict[str, Any] = yaml.safe_load(VALID)
    raw.update(changes)
    return raw


def test_loads_a_valid_file(tmp_path: Path) -> None:
    path = tmp_path / "paper.yaml"
    path.write_text(VALID, encoding="utf-8")
    paper = load_paper_file(path)
    assert paper == PaperFile(
        name="crypto-v1-paper",
        strategy="data/strategy_crypto-v1.yaml",
        strategy_sha256=SHA,
        setup="breakout",
        start=date(2026, 10, 13),
        review=ReviewRules(months=12, max_late_days=7, max_drawdown=0.35),
    )
    assert paper.review_date == date(2027, 10, 13)


def test_add_months_keeps_the_day_or_clamps_to_the_month_end() -> None:
    assert add_months(date(2026, 10, 13), 12) == date(2027, 10, 13)
    assert add_months(date(2027, 1, 31), 1) == date(2027, 2, 28)
    assert add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"extra": 1}, "exactly the keys"),
        ({"name": ""}, "name must be"),
        ({"strategy": "data/strategy.txt"}, "strategy must be"),
        ({"strategy_sha256": "abc"}, "64 lowercase hex"),
        ({"setup": "scalp"}, "unknown setup"),
        ({"start": "2026-10-13"}, "start must be a date"),
        ({"review": {"months": 12, "max_late_days": 7}}, "review needs exactly"),
        ({"review": {"months": 0, "max_late_days": 7, "max_drawdown": 0.35}}, "review.months"),
        (
            {"review": {"months": 12, "max_late_days": -1, "max_drawdown": 0.35}},
            "review.max_late_days",
        ),
        (
            {"review": {"months": 12, "max_late_days": 7, "max_drawdown": 1.5}},
            "review.max_drawdown",
        ),
    ],
)
def test_refuses_a_malformed_file(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(PaperError, match=message):
        parse_paper_file(_raw(**changes), "paper.yaml")


def test_a_missing_file_is_a_paper_error(tmp_path: Path) -> None:
    with pytest.raises(PaperError, match="missing.yaml"):
        load_paper_file(tmp_path / "missing.yaml")
