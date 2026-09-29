"""`signalbench paper start|run|status` wiring (spec 07). The logic is tested in test_paper_*."""

from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlmodel import Session
from typer.testing import CliRunner

from signalbench import cli
from signalbench.cli import app
from signalbench.db.models import PaperPortfolio

runner = CliRunner()


def _use(monkeypatch: pytest.MonkeyPatch, session: Session, held: bool = True) -> list[int]:
    """The test session, a lock that is `held` (or not), and an ingest that counts its calls."""
    calls: list[int] = []
    monkeypatch.setattr(cli, "get_session", lambda: session)
    monkeypatch.setattr(cli, "advisory_lock", lambda _engine: nullcontext(held))
    monkeypatch.setattr(cli, "_ingest_prices", lambda _session: calls.append(1) or [])
    return calls


def test_paper_help_lists_start_run_and_status() -> None:
    result = runner.invoke(app, ["paper", "--help"])
    assert result.exit_code == 0
    for command in ("start", "run", "status"):
        assert command in result.stdout


def test_paper_run_without_portfolios_fails_with_one_error_line(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _use(monkeypatch, session)
    monkeypatch.setattr(cli, "PAPER_REPORTS_DIR", tmp_path / "paper")
    result = runner.invoke(app, ["paper", "run"])
    assert result.exit_code == 1
    assert result.stderr == (
        "ERROR: PaperRefusedError: No paper portfolios. Run `signalbench paper start` first.\n"
    )
    assert calls == []


def test_paper_run_exits_0_when_another_run_holds_the_lock(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _use(monkeypatch, session, held=False)
    result = runner.invoke(app, ["paper", "run"])
    assert (result.exit_code, result.stdout) == (0, "Another paper run holds the lock; nothing to do.\n")
    assert calls == []


def test_paper_run_reports_the_first_line_when_the_database_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def down() -> Session:
        raise RuntimeError('connection failed: port 1 refused\nIs the server running on "127.0.0.1"?')

    monkeypatch.setattr(cli, "get_session", down)
    result = runner.invoke(app, ["paper", "run"])
    assert result.exit_code == 1
    assert result.stderr == "ERROR: RuntimeError: connection failed: port 1 refused\n"


def test_paper_status_reports_the_first_line_when_the_database_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def down() -> Session:
        raise RuntimeError("connection failed: port 1 refused\nmore detail")

    monkeypatch.setattr(cli, "get_session", down)
    result = runner.invoke(app, ["paper", "status", "--stale-after-days", "3"])
    assert result.exit_code == 1
    assert result.stderr == "ERROR: RuntimeError: connection failed: port 1 refused\n"


def test_paper_start_refuses_a_missing_paper_file(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _use(monkeypatch, session)
    monkeypatch.setattr(cli, "PAPER_V1_PATH", tmp_path / "data" / "paper_v1.yaml")
    result = runner.invoke(app, ["paper", "start"])
    assert (result.exit_code, result.stderr) == (1, "paper_v1.yaml not found.\n")


def test_paper_status_exits_3_when_runs_are_stale(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(monkeypatch, session)
    session.add(
        PaperPortfolio(name="p1", config_path="data/strategy_test.yaml", config_sha256="a" * 64,
                       setup="breakout", started_on=date(2026, 10, 12), state={},
                       created_at=datetime.now(UTC) - timedelta(days=5))
    )
    session.commit()
    plain = runner.invoke(app, ["paper", "status"])
    assert plain.exit_code == 0
    assert plain.stdout.splitlines()[0] == "last ok run: none"
    assert plain.stdout.splitlines()[1].startswith("p1: starts 2026-10-12, not stepped yet")
    checked = runner.invoke(app, ["paper", "status", "--stale-after-days", "3"])
    assert checked.exit_code == 3
    assert checked.stderr == "STALE: the last ok paper run was never, more than 3 days ago.\n"
