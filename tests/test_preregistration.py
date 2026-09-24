"""Pre-registration checks that `backtest run` applies before a run on real data."""

from datetime import date
from pathlib import Path

import pytest
from sqlmodel import Session

from signalbench.backtest.preregistration import (
    RunRefusedError,
    check_config_path,
    check_cost_matches_survey,
    check_version_unchanged,
)
from signalbench.db.models import BacktestRun


def test_config_path_must_be_data_strategy_version(tmp_path: Path) -> None:
    check_config_path(tmp_path, tmp_path / "data" / "strategy_v1.yaml", "v1")
    check_config_path(tmp_path, tmp_path / "data" / ".." / "data" / "strategy_v1.yaml", "v1")


@pytest.mark.parametrize(
    ("relative", "version"),
    [
        ("tests/fixtures/strategy_test.yaml", "test"),  # not under data/
        ("data/strategy_v1.yaml", "v2"),  # version does not match the file name
        ("data/strategy_v2.yaml", "v1"),
        ("data/other.yaml", "v1"),
        ("data/sub/strategy_v1.yaml", "v1"),
    ],
)
def test_other_config_paths_are_refused(tmp_path: Path, relative: str, version: str) -> None:
    with pytest.raises(RunRefusedError, match=rf"data/strategy_{version}\.yaml"):
        check_config_path(tmp_path, tmp_path / relative, version)


def _stored(session: Session, version: str, sha: str) -> None:
    session.add(
        BacktestRun(
            strategy_version=version, config_sha256=sha, git_sha="abc", setup="pullback",
            jev_mode="off", start_date=date(2012, 1, 3), end_date=date(2026, 9, 23),
            data_fingerprint="d" * 64, metrics={}, pass_bar={}, passed=False,
            trade_log={"trades": [], "events": []},
        )
    )
    session.commit()


def test_a_version_may_run_again_with_the_same_config(session: Session) -> None:
    check_version_unchanged(session, "v1", "a" * 64)  # no runs yet
    _stored(session, "v1", "a" * 64)
    _stored(session, "v2", "b" * 64)  # other versions do not matter
    check_version_unchanged(session, "v1", "a" * 64)


def test_a_changed_config_under_the_same_version_is_refused(session: Session) -> None:
    _stored(session, "v1", "a" * 64)
    with pytest.raises(RunRefusedError, match="new version"):
        check_version_unchanged(session, "v1", "c" * 64)


def test_config_cost_must_equal_the_survey_cost() -> None:
    check_cost_matches_survey(0.003, 0.003)
    check_cost_matches_survey(0.0025, round(0.0025000001, 6))  # the survey rounds to 6 decimals
    with pytest.raises(RunRefusedError, match=r"0.002.*0.003"):
        check_cost_matches_survey(0.002, 0.003)
    with pytest.raises(RunRefusedError):
        check_cost_matches_survey(0.003001, 0.003)
