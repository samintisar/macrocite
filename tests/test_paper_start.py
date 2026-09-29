"""`paper start` (spec 07): every portfolio or none, from committed files only, once."""

from datetime import date
from pathlib import Path

import pytest
from sqlmodel import Session, select

from paper_helpers import PAPER_FILE, PORTFOLIOS, evening, make_repo
from signalbench.backtest.sim_state import state_from_json
from signalbench.backtest.simulator import initial_state
from signalbench.db.models import PaperPortfolio
from signalbench.paper.start import PaperRefusedError, start_portfolios
from signalbench.strategy.config import load_strategy_config
from strategy_helpers import WeekdaySessions, weekdays

DAYS = weekdays(date(2023, 1, 2), 280)
FRIDAY = evening(DAYS[239])  # 2023-12-01; DAYS[240] is the Monday after


def _start(session: Session, repo: Path) -> list[PaperPortfolio]:
    return start_portfolios(
        session, paper_file=repo / PAPER_FILE, repo=repo, calendar=WeekdaySessions(), now=FRIDAY
    )


def test_start_creates_every_portfolio_on_the_next_session(session: Session, tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    created = _start(session, repo)
    assert [p.name for p in created] == list(PORTFOLIOS)
    for portfolio in created:
        config, sha = load_strategy_config(repo / portfolio.config_path)
        assert (portfolio.config_path, portfolio.setup) == PORTFOLIOS[portfolio.name]
        assert portfolio.config_sha256 == sha
        assert portfolio.started_on == DAYS[240]
        assert portfolio.last_session is None
        assert state_from_json(portfolio.state) == initial_state(config)


def test_start_refuses_to_run_twice(session: Session, tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    _start(session, repo)
    with pytest.raises(PaperRefusedError, match="already exist: p-plain, p-qqq, p-twin"):
        _start(session, repo)
    assert len(session.exec(select(PaperPortfolio)).all()) == 3


def test_start_refuses_an_uncommitted_paper_file(session: Session, tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    with (repo / PAPER_FILE).open("a", encoding="utf-8") as handle:
        handle.write("# edited\n")
    with pytest.raises(PaperRefusedError, match="paper_test.yaml must be committed, unchanged"):
        _start(session, repo)
    assert session.exec(select(PaperPortfolio)).all() == []


def test_start_refuses_an_uncommitted_config_and_creates_nothing(
    session: Session, tmp_path: Path
) -> None:
    repo = make_repo(tmp_path)
    with (repo / "data" / "strategy_test-qqq.yaml").open("a", encoding="utf-8") as handle:
        handle.write("# edited\n")
    with pytest.raises(PaperRefusedError, match="p-qqq: data/strategy_test-qqq.yaml must exist"):
        _start(session, repo)
    assert session.exec(select(PaperPortfolio)).all() == []
