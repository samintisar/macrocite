from contextlib import nullcontext
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlmodel import Session
from typer.testing import CliRunner

from paper_helpers import DAYS, START, strategy_sha, write_paper_file
from signalbench import cli
from signalbench.cli import app
from signalbench.db.models import PaperPortfolio
from signalbench.live.messenger import FakeMessenger
from signalbench.paper.run import PaperOutcome

runner = CliRunner()


def _engine_named(name: str) -> SimpleNamespace:
    return SimpleNamespace(url=SimpleNamespace(database=name))


@pytest.fixture
def paper_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "engine", _engine_named("signalbench_paper"))


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
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, paper_database: None
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


@pytest.mark.parametrize("command", ["start", "run"])
def test_the_write_commands_refuse_the_live_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, command: str
) -> None:
    monkeypatch.setattr(cli, "engine", _engine_named("signalbench"))
    monkeypatch.setattr(cli.settings, "telegram_bot_token", None)  # run would stop here
    result = runner.invoke(
        app, ["crypto", "paper", command, "--file", str(write_paper_file(tmp_path))]
    )
    assert result.exit_code == 1
    assert (
        "ERROR: the paper commands need a database whose name ends in _paper (spec 11); "
        "DATABASE_URL points at 'signalbench'."
    ) in result.stderr
    assert "TELEGRAM_BOT_TOKEN" not in result.stderr


def test_start_goes_past_the_guard_on_the_paper_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, paper_database: None
) -> None:
    portfolio = PaperPortfolio(
        name="crypto-test-paper", paper_file="paper_crypto-test.yaml",
        strategy_sha256=strategy_sha(), start=DAYS[START],
    )
    monkeypatch.setattr(cli, "get_session", lambda: nullcontext(None))
    monkeypatch.setattr(cli, "start_paper", lambda *_args, **_kwargs: portfolio)
    result = runner.invoke(
        app, ["crypto", "paper", "start", "--file", str(write_paper_file(tmp_path))]
    )
    assert result.exit_code == 0, result.stderr
    assert "crypto-test-paper: starts" in result.stdout


class _ClosingMessenger(FakeMessenger):
    def __init__(self, *_args: Any) -> None:
        super().__init__()
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_a_failure_outside_run_paper_is_alerted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, paper_database: None
) -> None:
    messengers: list[_ClosingMessenger] = []

    def make(*args: Any) -> _ClosingMessenger:
        messengers.append(_ClosingMessenger(*args))
        return messengers[-1]

    def down(*_args: object, **_kwargs: object) -> PaperOutcome:
        raise RuntimeError("db down")

    monkeypatch.setattr(cli.settings, "telegram_bot_token", "123:abc")
    monkeypatch.setattr(cli.settings, "telegram_chat_id", 42)
    monkeypatch.setattr(cli, "TelegramMessenger", make)
    monkeypatch.setattr(cli, "get_session", lambda: nullcontext(None))
    monkeypatch.setattr(cli, "run_paper", down)
    monkeypatch.setattr(cli, "git_sha", lambda _repo: "abc123")
    result = runner.invoke(
        app, ["crypto", "paper", "run", "--file", str(write_paper_file(tmp_path))]
    )
    assert result.exit_code == 1
    assert "ERROR: RuntimeError: db down" in result.stderr
    assert messengers[0].texts() == ["PAPER ALERT: RuntimeError: db down"]
    assert messengers[0].closed


def test_the_paper_run_clock_is_utc(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, paper_database: None
) -> None:
    clocks: list[datetime] = []

    def fake_run(*_args: object, clock: Any, **_kwargs: object) -> PaperOutcome:
        clocks.append(clock())
        return PaperOutcome("ok", 1, 0)

    monkeypatch.setattr(cli.settings, "telegram_bot_token", "123:abc")
    monkeypatch.setattr(cli.settings, "telegram_chat_id", 42)
    monkeypatch.setattr(cli, "TelegramMessenger", _ClosingMessenger)
    monkeypatch.setattr(cli, "get_session", lambda: nullcontext(None))
    monkeypatch.setattr(cli, "run_paper", fake_run)
    monkeypatch.setattr(cli, "git_sha", lambda _repo: "abc123")
    result = runner.invoke(
        app, ["crypto", "paper", "run", "--file", str(write_paper_file(tmp_path))]
    )
    assert result.exit_code == 0, result.stderr
    assert clocks[0].utcoffset() == UTC.utcoffset(None)
