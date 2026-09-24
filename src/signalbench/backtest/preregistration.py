"""Pre-registration checks applied before a backtest on real data (spec 02, Pre-registration)."""

from pathlib import Path

from sqlmodel import Session, col, select

from signalbench.db.models import BacktestRun


class RunRefusedError(ValueError):
    """A backtest run was refused before it touched real data. The message says why."""


def check_config_path(repo: Path, path: Path, version: str) -> None:
    """A pre-registered config lives at data/strategy_<version>.yaml, named after its version."""
    expected = repo.resolve() / "data" / f"strategy_{version}.yaml"
    if path.resolve() != expected:
        raise RunRefusedError(
            f"{path} holds `version: {version}`, so it must be data/strategy_{version}.yaml. "
            "Only a pre-registered config at data/strategy_<version>.yaml can run on real data "
            "(spec 02 pre-registration)."
        )


def check_version_unchanged(session: Session, version: str, config_sha256: str) -> None:
    """Every stored run of `version` used this exact config; a changed config is a new version."""
    stored = session.exec(
        select(BacktestRun.config_sha256).where(col(BacktestRun.strategy_version) == version)
    ).all()
    others = sorted(set(stored) - {config_sha256})
    if others:
        raise RunRefusedError(
            f"Strategy {version} already has runs with config_sha256 {others[0][:12]}..., and this "
            f"config is {config_sha256[:12]}.... A changed {version} config must be a new version "
            "(e.g. data/strategy_v2.yaml), reported as post-hoc (spec 02 pre-registration)."
        )


def check_cost_matches_survey(config_cost: float, survey_cost: float) -> None:
    """The config's cost_per_side must be the committed survey's (already rounded to 6 dp)."""
    if config_cost != survey_cost:
        raise RunRefusedError(
            f"The config has cost_per_side {config_cost}, but the committed spread survey gives "
            f"{survey_cost}. Set cost_per_side from `signalbench backtest cost` "
            "(spec 02 pre-registration)."
        )
