"""Spec 04: `live start` freezes the live config once; the scan's check refuses a changed file."""

from datetime import date
from pathlib import Path

import pytest
from sqlmodel import Session

from repo_helpers import git
from signalbench.backtest.preregistration import RunRefusedError
from signalbench.db.models import LiveConfig, LiveRiskState
from signalbench.live.start import (
    LIVE_CONFIG,
    LiveRefusedError,
    start_live,
    verify_live_config,
)
from signalbench.strategy.config import config_sha256

REPO = Path(__file__).resolve().parents[1]
SURVEY = "data/cdr_spread_survey.yaml"
DAY = date(2026, 10, 1)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repo holding the committed live config and spread survey, and a tracked src file."""
    for name in (LIVE_CONFIG, SURVEY):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((REPO / name).read_bytes())
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "code.py").write_text("x = 1\n", encoding="utf-8")
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "data", "src")
    git(tmp_path, "commit", "-q", "-m", "live")
    return tmp_path


def _start(session: Session, repo: Path, config: str = LIVE_CONFIG) -> LiveConfig:
    return start_live(session, repo=repo, config_path=repo / config, survey_path=repo / SURVEY,
                      today=DAY)


def test_live_start_records_the_config_once(session: Session, repo: Path) -> None:
    row = _start(session, repo)
    sha = config_sha256((repo / LIVE_CONFIG).read_bytes())
    head = git(repo, "rev-parse", "HEAD")
    assert (row.config_path, row.config_sha256, row.started_on, row.start_git_sha) == (
        LIVE_CONFIG, sha, DAY, head
    )
    risk = session.get(LiveRiskState, 1)
    assert risk is not None and not risk.paused
    with pytest.raises(LiveRefusedError, match="already recorded"):
        _start(session, repo)


def test_live_start_refuses_an_edited_or_uncommitted_config(session: Session, repo: Path) -> None:
    path = repo / LIVE_CONFIG
    path.write_text(path.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")
    with pytest.raises(LiveRefusedError, match="must exist and be committed, unchanged"):
        _start(session, repo)
    git(repo, "checkout", "--", LIVE_CONFIG)
    (repo / "data" / "strategy_v9.yaml").write_bytes(path.read_bytes())  # never committed
    with pytest.raises(LiveRefusedError, match="strategy_v9.yaml must exist and be committed"):
        _start(session, repo, "data/strategy_v9.yaml")
    assert session.get(LiveConfig, 1) is None


def test_live_start_runs_the_backtest_guard(session: Session, repo: Path) -> None:
    text = (repo / LIVE_CONFIG).read_text(encoding="utf-8")
    (repo / "data" / "strategy_other.yaml").write_text(text, encoding="utf-8")  # wrong name
    (repo / "data" / "strategy_v2-cost.yaml").write_text(
        text.replace("version: v2-none-cash", "version: v2-cost").replace(
            "cost_per_side: 0.002", "cost_per_side: 0.003"
        ),
        encoding="utf-8",
    )
    git(repo, "add", "data")
    git(repo, "commit", "-q", "-m", "more")
    with pytest.raises(RunRefusedError, match="must be data/strategy_v2-none-cash.yaml"):
        _start(session, repo, "data/strategy_other.yaml")
    with pytest.raises(RunRefusedError, match="cost_per_side 0.003"):
        _start(session, repo, "data/strategy_v2-cost.yaml")


def test_live_start_refuses_uncommitted_code(session: Session, repo: Path) -> None:
    (repo / "src" / "code.py").write_text("x = 2\n", encoding="utf-8")
    with pytest.raises(LiveRefusedError, match=r"tracked code or data \(src/code.py\)"):
        _start(session, repo)


def test_the_scan_check_refuses_no_row_and_a_changed_config(session: Session, repo: Path) -> None:
    with pytest.raises(LiveRefusedError, match="Run `signalbench live start` first"):
        verify_live_config(session, repo)
    _start(session, repo)
    row, config = verify_live_config(session, repo)
    assert (row.config_path, config.version, config.enabled_setups) == (
        LIVE_CONFIG, "v2-none-cash", ("breakout",)
    )
    assert (config.breakout.time_limit, config.cash_vehicle) == (None, None)
    path = repo / LIVE_CONFIG
    path.write_text(path.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")
    with pytest.raises(LiveRefusedError, match="changed: sha256"):
        verify_live_config(session, repo)
