"""`signalbench scan`, `scan status`, and `bot run` wiring (spec 05), on the Breakout world:
no real database, Telegram, or network."""

import logging
import re
from contextlib import nullcontext
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session, select
from typer.testing import CliRunner

from repo_helpers import evening, git
from scan_helpers import DAYS, UNIVERSE, B, World, make_world, savepoint_engine
from signalbench import cli
from signalbench.cli import app
from signalbench.db.models import ScanRun, TradeSignal
from signalbench.live.heartbeat import write_heartbeat
from signalbench.live.messenger import FakeMessenger
from strategy_helpers import WeekdaySessions

runner = CliRunner()
# Rich forces colour on GitHub Actions, which splits "--dry-run" with escape codes.
ANSI = re.compile(r"\x1b\[[0-9;]*m")


class ClosableFake(FakeMessenger):
    closed = False

    def close(self) -> None:
        self.closed = True


def _wire(monkeypatch: pytest.MonkeyPatch, world: World, now: datetime) -> ClosableFake:
    messenger = ClosableFake()
    monkeypatch.setattr(cli, "get_session", lambda: world.session)
    monkeypatch.setattr(cli, "advisory_lock", lambda _engine, _key: nullcontext(True))
    monkeypatch.setattr(cli, "REPO_ROOT", world.repo)
    monkeypatch.setattr(cli, "load_universe", lambda _path: list(UNIVERSE))
    monkeypatch.setattr(cli, "NyseSessions", WeekdaySessions)
    monkeypatch.setattr(cli, "CboeCanadaSessions", WeekdaySessions)
    monkeypatch.setattr(cli, "_now", lambda: now)
    monkeypatch.setattr(cli, "_ingest_prices", lambda _session: [])
    monkeypatch.setattr(cli, "_ingest_calendar_earnings", lambda _session, _since: [])
    monkeypatch.setattr(cli, "fetch_yfinance_splits", lambda _symbol, _since: [])
    monkeypatch.setattr(cli, "_next_scan_run", lambda: "2026-10-06 15:00:00 (local time)")
    monkeypatch.setattr(cli, "TelegramMessenger", lambda _token, _chat: messenger)
    monkeypatch.setattr(cli.settings, "telegram_bot_token", "123456:TEST-TOKEN")
    monkeypatch.setattr(cli.settings, "telegram_chat_id", 4242)
    return messenger


@pytest.fixture
def world(session: Session, tmp_path: Path) -> World:
    world = make_world(session, tmp_path)
    write_heartbeat(session, evening(DAYS[B]))
    return world


def test_help_lists_scan_status_and_bot_run() -> None:
    assert "status" in runner.invoke(app, ["scan", "--help"]).stdout
    assert "--dry-run" in ANSI.sub("", runner.invoke(app, ["scan", "--help"]).stdout)
    assert "run" in runner.invoke(app, ["bot", "--help"]).stdout


def test_scan_sends_through_the_messenger_and_prints_one_line(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    messenger = _wire(monkeypatch, world, evening(DAYS[B]))
    result = runner.invoke(app, ["scan"])
    assert result.exit_code == 0, result.stderr
    assert result.stdout.splitlines()[-1] == "scan 1: ok for 2026-10-05 (sent 2, signals 1)"
    assert [m.text.split()[0] for m in messenger.sent] == ["🟢", "📊"]
    assert messenger.closed
    again = runner.invoke(app, ["scan"])
    assert again.stdout.splitlines()[-1] == "scan 2: nothing for 2026-10-05 (none)"


def test_a_failed_scan_exits_1_after_its_error_line(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    messenger = _wire(monkeypatch, world, evening(DAYS[B]))
    messenger.fail = True
    result = runner.invoke(app, ["scan"])
    assert result.exit_code == 1
    assert result.stdout.splitlines()[-1].startswith("ERROR: Scan failed at step 9 (send):")


def test_scan_refuses_without_telegram_settings_and_as_of_without_dry_run(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, world, evening(DAYS[B]))
    monkeypatch.setattr(cli.settings, "telegram_chat_id", None)
    result = runner.invoke(app, ["scan"])
    assert result.exit_code == 1
    assert result.stderr == (
        "ERROR: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set (the main checkout's .env)\n"
    )
    assert world.session.exec(select(ScanRun)).all() == []
    result = runner.invoke(app, ["scan", "--as-of", "2026-10-05"])
    assert (result.exit_code, result.stderr) == (2, "--as-of needs --dry-run\n")


def test_a_dry_run_prints_the_messages_and_leaves_the_database_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engine = savepoint_engine()
    with Session(engine) as seed:
        world = make_world(seed, tmp_path)
        _wire(monkeypatch, world, evening(DAYS[B + 3]))
    monkeypatch.setattr(cli, "engine", engine)
    monkeypatch.setattr(cli, "TelegramMessenger", None)  # a dry run never builds one
    result = runner.invoke(app, ["scan", "--dry-run", "--as-of", "2026-10-05"])
    assert result.exit_code == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[lines.index("--- message 1 ---") + 1] == "🟢 BUY NVDA (CDR ZNVD) — Breakout · NVDA"
    assert lines[-2:] == [
        "scan 1: ok for 2026-10-05 (sent 2, signals 1)", "(dry run: nothing was written or sent)"
    ]
    with Session(engine) as after:
        assert (after.exec(select(ScanRun)).all(), after.exec(select(TradeSignal)).all()) == (
            [], []
        )


def test_scan_status_and_the_stale_exit(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, world, evening(DAYS[B]))
    runner.invoke(app, ["scan"])
    result = runner.invoke(app, ["scan", "status", "--stale-after-days", "3"])
    assert result.exit_code == 0, result.stderr
    assert result.stdout.splitlines()[0] == (
        "Last scan: 2026-10-05 ok, started 2026-10-05 18:00 New York"
    )
    assert result.stdout.splitlines()[1] == "Next scheduled scan: 2026-10-06 15:00:00 (local time)"
    monkeypatch.setattr(cli, "_now", lambda: evening(DAYS[B]) + timedelta(days=4))
    stale = runner.invoke(app, ["scan", "status", "--stale-after-days", "3"])
    assert (stale.exit_code, stale.stderr) == (
        3, "STALE: the last ok scan was on 2026-10-05 18:00 New York, more than 3 days ago.\n"
    )
    monkeypatch.setattr(cli, "_now", lambda: evening(DAYS[B]) + timedelta(minutes=1))
    always = runner.invoke(app, ["scan", "status", "--stale-after-days", "0"])  # the toast check
    assert always.exit_code == 3


def test_bot_run_polls_with_one_handler_and_keeps_the_token_out_of_the_log(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, world, evening(DAYS[B]))
    polled: list[dict[str, Any]] = []

    class App:
        def run_polling(self, **kwargs: Any) -> None:
            polled.append(kwargs)

    built: list[tuple[str, int]] = []

    def build(token: str, brain: Any, beat: Any) -> App:
        built.append((token, brain.chat_id))
        return App()

    monkeypatch.setattr(cli, "build_application", build)
    result = runner.invoke(app, ["bot", "run"])
    assert result.exit_code == 0, result.stderr
    assert "TEST-TOKEN" not in result.stdout and "4242" not in result.stdout
    assert built == [("123456:TEST-TOKEN", 4242)]
    assert polled == [{"allowed_updates": ["message", "callback_query"]}]
    assert logging.getLogger("httpx").level == logging.WARNING
    monkeypatch.setattr(cli.settings, "telegram_bot_token", None)
    assert runner.invoke(app, ["bot", "run"]).exit_code == 1


def test_scan_refuses_an_unpinned_commit_unless_allowed(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, world, evening(DAYS[B]))
    (world.repo / "src" / "app.py").write_text("VERSION = 2\n", encoding="utf-8")
    git(world.repo, "commit", "-q", "-am", "an update")
    refused = runner.invoke(app, ["scan"])
    assert refused.exit_code == 1
    assert "is neither the live start commit nor a live-v* tag" in refused.stdout
    allowed = runner.invoke(app, ["scan", "--allow-any-commit"])
    assert allowed.exit_code == 0, allowed.stderr
    assert allowed.stdout.splitlines()[-1] == "scan 2: ok for 2026-10-05 (sent 2, signals 1)"
