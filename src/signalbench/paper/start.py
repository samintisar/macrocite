"""`signalbench paper start` (spec 07, Commands): create the pre-registered portfolios."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlmodel import Session, col, select

from signalbench.backtest.preregistration import check_config_path
from signalbench.backtest.provenance import committed_unchanged
from signalbench.backtest.sim_state import state_to_json
from signalbench.backtest.simulator import initial_state
from signalbench.db.models import PaperPortfolio
from signalbench.market.calendar import Sessions
from signalbench.paper.portfolios import load_paper_file
from signalbench.strategy.config import load_strategy_config

NEW_YORK = ZoneInfo("America/New_York")


class PaperRefusedError(ValueError):
    """A paper command was refused. The message says why."""


def start_portfolios(
    session: Session, *, paper_file: Path, repo: Path, calendar: Sessions, now: datetime
) -> list[PaperPortfolio]:
    """Create every portfolio in `paper_file`, all starting on the first session after today
    (New York). All or nothing: refuses when the file or any config is not committed and
    unchanged, or when any of the portfolios already exists."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError(f"now must be timezone-aware, got naive {now.isoformat()}")
    if not paper_file.exists():
        raise PaperRefusedError(f"{paper_file.name} not found.")
    if not committed_unchanged(repo, paper_file):
        raise PaperRefusedError(
            f"{paper_file.name} must be committed, unchanged, before `paper start` (spec 07)."
        )
    specs = load_paper_file(paper_file)
    taken = session.exec(
        select(PaperPortfolio.name).where(col(PaperPortfolio.name).in_([s.name for s in specs]))
    ).all()
    if taken:
        raise PaperRefusedError(
            f"Paper portfolios already exist: {', '.join(sorted(taken))}. A new idea is a new "
            "portfolio in a new data/paper_<n>.yaml (spec 07)."
        )
    started_on = calendar.next_sessions(now.astimezone(NEW_YORK).date(), 1)[0]
    portfolios: list[PaperPortfolio] = []
    for spec in specs:
        path = repo / spec.config
        if not path.exists() or not committed_unchanged(repo, path):
            raise PaperRefusedError(
                f"{spec.name}: {spec.config} must exist and be committed, unchanged (spec 07)."
            )
        config, sha = load_strategy_config(path)
        check_config_path(repo, path, config.version)
        portfolios.append(
            PaperPortfolio(
                name=spec.name,
                config_path=spec.config,
                config_sha256=sha,
                setup=spec.setup,
                started_on=started_on,
                state=state_to_json(initial_state(config)),
            )
        )
    session.add_all(portfolios)
    session.commit()
    for portfolio in portfolios:
        session.refresh(portfolio)
    return portfolios
