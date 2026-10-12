from datetime import date
from pathlib import Path

import pytest
from sqlmodel import Session
from typer.testing import CliRunner

from paper_helpers import DAYS, START, strategy_sha, write_paper_file
from signalbench import cli
from signalbench.cli import app
from signalbench.db.models import PaperPortfolio

runner = CliRunner()


def _portfolio(session: Session, start: date) -> None:
    session.add(
        PaperPortfolio(
            name="crypto-test-paper", paper_file="paper_crypto-test.yaml",
            strategy_sha256=strategy_sha(), start=start,
        )
    )
    session.commit()


def test_paper_help_lists_its_commands() -> None:
    result = runner.invoke(app, ["crypto", "paper", "--help"])
    assert result.exit_code == 0
    for command in ("start", "run", "status", "review"):
        assert command in result.stdout


def test_status_needs_a_started_portfolio(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "get_session", lambda: session)
    paper = write_paper_file(tmp_path)
    result = runner.invoke(app, ["crypto", "paper", "status", "--file", str(paper)])
    assert result.exit_code == 1
    assert "run `signalbench crypto paper start` first" in result.stderr


def test_status_prints_the_summary(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "get_session", lambda: session)
    paper = write_paper_file(tmp_path)
    _portfolio(session, DAYS[START])
    result = runner.invoke(app, ["crypto", "paper", "status", "--file", str(paper)])
    assert result.exit_code == 0, result.stderr
    assert "PAPER crypto-test-paper: weekly summary" in result.stdout
    assert "Equity 100.00 (start 100.00)" in result.stdout
    assert "Last ok run: never" in result.stdout


def test_review_refuses_before_the_review_date(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "get_session", lambda: session)
    paper = write_paper_file(tmp_path, start=date(2099, 1, 1))
    _portfolio(session, date(2099, 1, 1))
    result = runner.invoke(app, ["crypto", "paper", "review", "--file", str(paper)])
    assert result.exit_code == 1
    assert "The review is on 2100-01-01" in result.stderr


def test_run_needs_the_telegram_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli.settings, "telegram_bot_token", None)
    result = runner.invoke(
        app, ["crypto", "paper", "run", "--file", str(write_paper_file(tmp_path))]
    )
    assert result.exit_code == 1
    assert "TELEGRAM_BOT_TOKEN" in result.stderr


def test_a_malformed_paper_file_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "paper.yaml"
    bad.write_text("name: x\n", encoding="utf-8")
    result = runner.invoke(app, ["crypto", "paper", "status", "--file", str(bad)])
    assert result.exit_code == 1
    assert "needs exactly the keys" in result.stderr
