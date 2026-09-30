"""`signalbench live start`: freeze the one live config (spec 04, Live config).

`start_live()` writes the single `live_config` row once, after the backtest's guard: the file is
data/strategy_<version>.yaml, committed and unchanged, with the committed spread survey's
cost_per_side, and the code is committed too (its HEAD is recorded). `verify_live_config()` is
the evening scan's check (spec 05, step 1): the row exists and the file's sha256 still matches.
"""

from datetime import date
from pathlib import Path

from sqlmodel import Session

from signalbench.backtest.preregistration import (
    check_config_path,
    check_cost_matches_survey,
)
from signalbench.backtest.provenance import code_version, committed_unchanged
from signalbench.db.models import LiveConfig, LiveRiskState
from signalbench.strategy.config import StrategyConfig, load_strategy_config
from signalbench.strategy.spread import load_spread_survey

LIVE_CONFIG = "data/strategy_v2-none-cash.yaml"  # owner decision, overview changelog 2026-09-29


class LiveRefusedError(ValueError):
    """`live start` or the scan's config check refused. The message says why."""


def start_live(
    session: Session, *, repo: Path, config_path: Path, survey_path: Path, today: date
) -> LiveConfig:
    """Write the `live_config` row (and an unpaused `risk_state` row). Refuses a second run."""
    if session.get(LiveConfig, 1) is not None:
        raise LiveRefusedError(
            "The live config is already recorded. Trading a different config is a new owner "
            "decision (spec 04)."
        )
    for path, what in ((config_path, "The live config"), (survey_path, "The spread survey")):
        if not path.exists() or not committed_unchanged(repo, path):
            raise LiveRefusedError(
                f"{what} {path.name} must exist and be committed, unchanged (spec 04)."
            )
    config, sha = load_strategy_config(config_path)
    check_config_path(repo, config_path, config.version)
    check_cost_matches_survey(config.cost_per_side, load_spread_survey(survey_path).cost_per_side)
    version = code_version(repo)
    if version.dirty:
        raise LiveRefusedError(
            f"Uncommitted changes to tracked code or data ({', '.join(version.changed)}); commit "
            "them or check out a clean tag, then rerun."
        )
    row = LiveConfig(
        config_path=config_path.resolve().relative_to(repo.resolve()).as_posix(),
        config_sha256=sha,
        started_on=today,
        start_git_sha=version.sha,
    )
    session.add(row)
    if session.get(LiveRiskState, 1) is None:
        session.add(LiveRiskState())
    session.commit()
    session.refresh(row)
    return row


def verify_live_config(session: Session, repo: Path) -> tuple[LiveConfig, StrategyConfig]:
    """The recorded row and the config it froze, Breakout only. Refuses when there is no row or
    the file's sha256 differs from the recorded one."""
    row = session.get(LiveConfig, 1)
    if row is None:
        raise LiveRefusedError("No live config. Run `signalbench live start` first.")
    path = repo / row.config_path
    if not path.exists():
        raise LiveRefusedError(f"{row.config_path} is missing.")
    config, sha = load_strategy_config(path)
    if sha != row.config_sha256:
        raise LiveRefusedError(
            f"{row.config_path} changed: sha256 {sha[:12]} is not the recorded "
            f"{row.config_sha256[:12]}. The live config is frozen (spec 04)."
        )
    return row, config.with_setups(("breakout",))
