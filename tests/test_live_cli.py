"""`signalbench live start` and `signalbench ledger tax|split|void-action` wiring (spec 04)."""

from contextlib import nullcontext
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlmodel import Session
from typer.testing import CliRunner

from live_helpers import add_pair, load_example, make_ledger, record_example
from repo_helpers import NEW_YORK, git
from scan_helpers import DAYS, LIVE, NVDA, UNIVERSE, B, make_world
from signalbench import cli
from signalbench.cli import app
from signalbench.db.models import LiveConfig
from signalbench.live.adopt import Adopter
from signalbench.live.start import LIVE_CONFIG
from signalbench.strategy.config import load_strategy_config
from strategy_helpers import WeekdaySessions

runner = CliRunner()
REPO = Path(__file__).resolve().parents[1]
SURVEY = "data/cdr_spread_survey.yaml"


@pytest.fixture
def ledger_cli(session: Session, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setattr(cli, "get_session", lambda: session)
    monkeypatch.setattr(cli, "_now", lambda: datetime(2026, 12, 31, 18, 0, tzinfo=NEW_YORK))
    add_pair(session, "TST", "ZTST")
    return session


def test_help_lists_the_live_and_ledger_commands() -> None:
    assert "start" in runner.invoke(app, ["live", "--help"]).stdout
    listed = runner.invoke(app, ["ledger", "--help"]).stdout
    for command in ("tax", "split", "void-action", "adopt"):
        assert command in listed


def test_live_start_writes_the_row_from_a_clean_repo(
    ledger_cli: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in (LIVE_CONFIG, SURVEY):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes((REPO / name).read_bytes())
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "data")
    git(tmp_path, "commit", "-q", "-m", "live")
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cli, "LIVE_CONFIG_PATH", tmp_path / LIVE_CONFIG)
    monkeypatch.setattr(cli, "SPREAD_SURVEY_PATH", tmp_path / SURVEY)
    result = runner.invoke(app, ["live", "start"])
    assert result.exit_code == 0, result.stderr
    row = ledger_cli.get(LiveConfig, 1)
    assert row is not None
    assert result.stdout == (
        f"live config: {LIVE_CONFIG} | config_sha256 {row.config_sha256[:12]} | started "
        f"2026-12-31 | code {row.start_git_sha[:12]}\n"
    )
    second = runner.invoke(app, ["live", "start"])
    assert second.exit_code == 1
    assert "already recorded" in second.stderr


def test_ledger_tax_prints_the_report_and_writes_the_csv(
    ledger_cli: Session, tmp_path: Path
) -> None:
    record_example(make_ledger(ledger_cli), load_example("acb_rebuy"))
    path = tmp_path / "tax-2026.csv"
    result = runner.invoke(app, ["ledger", "tax", "2026", "--csv", str(path)])
    assert result.exit_code == 0, result.stderr
    assert result.stdout.startswith("ACB report 2026: 2 dispositions\n")
    assert f"csv: {path}\n" in result.stdout
    assert path.read_text(encoding="utf-8").splitlines()[-1] == (
        "total,,,198.00,187.00,0.00,11.00,2.10,13.10,"
    )


def test_ledger_split_records_a_cdr_or_a_us_split_and_void_action_voids_it(
    ledger_cli: Session,
) -> None:
    ledger = make_ledger(ledger_cli)
    ledger.record_cash(Decimal("100.00"), date(2026, 3, 2), "deposit")
    ledger.record_fill(cdr_symbol="ZTST", side="buy", quantity=Decimal(3),
                       price_cad=Decimal(30), trade_date=date(2026, 3, 2))
    result = runner.invoke(app, ["ledger", "split", "ZTST", "2", "2026-03-09"])
    assert (result.exit_code, result.stdout) == (
        0, "corporate action 1: ZTST cdr_split 2-for-1, ex-date 2026-03-09\n"
    )
    result = runner.invoke(app, ["ledger", "split", "TST", "0.5", "2026-03-10"])
    assert result.stdout == "corporate action 2: TST us_split 0.5-for-1, ex-date 2026-03-10\n"
    result = runner.invoke(app, ["ledger", "void-action", "1", "yfinance was right after all"])
    assert (result.exit_code, result.stdout) == (
        0, "corporate action 1 voided: yfinance was right after all\n"
    )
    assert make_ledger(ledger_cli).books()["ZTST"].units == Decimal(3)


def test_ledger_split_and_void_action_refuse_with_one_line(ledger_cli: Session) -> None:
    bad = runner.invoke(app, ["ledger", "split", "ZTST", "two", "2026-03-09"])
    assert (bad.exit_code, bad.stderr) == (
        2, "RATIO must be a number and EX_DATE a YYYY-MM-DD date.\n"
    )
    refused = runner.invoke(app, ["ledger", "split", "ZTST", "2", "2026-03-09"])
    assert refused.exit_code == 1
    assert refused.stderr.startswith("ZTST had no open position or sent signal on 2026-03-09")
    missing = runner.invoke(app, ["ledger", "void-action", "9", "no such row"])
    assert (missing.exit_code, missing.stderr) == (1, "no corporate action 9 to void\n")


def _adopt_world(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, closes: dict | None = None
) -> int:
    """The scan world (XOM flat at US$100, ATR 2: an adopted stop of US$96 / C$9.60) with a
    manual 3-unit ZXOM buy on DAYS[B - 3]; the CLI on its weekday calendar. Returns the fill id."""
    world = make_world(session, tmp_path, closes=closes)
    monkeypatch.setattr(cli, "get_session", lambda: nullcontext(session))
    monkeypatch.setattr(cli, "_ledger", lambda s: make_ledger(s, today=DAYS[B + 1]))
    monkeypatch.setattr(cli, "_adopter", lambda: Adopter(
        config=load_strategy_config(world.repo / LIVE)[0], universe=UNIVERSE,
        calendar=WeekdaySessions(),
    ))
    fill = world.ledger(B + 1).record_fill(
        cdr_symbol="ZXOM", side="buy", quantity=Decimal(3), price_cad=Decimal("10.20"),
        trade_date=DAYS[B - 3],
    )
    assert fill.id is not None
    return fill.id


def test_ledger_adopt_relinks_a_manual_buy_and_prints_its_stop(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fill_id = _adopt_world(session, monkeypatch, tmp_path)
    result = runner.invoke(app, ["ledger", "adopt", str(fill_id), "--date", str(DAYS[B - 2])])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [(
        f"adopted fill {fill_id} → fill {fill_id + 1} on {DAYS[B - 2]}: signal 1 (adopted), "
        "stop C$9.60 / US$96.00"
    )]


def test_ledger_adopt_warns_when_the_stop_was_already_hit(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fill_id = _adopt_world(session, monkeypatch, tmp_path,
                           closes={"NVDA": NVDA, "XOM": {B: 90.0}})
    result = runner.invoke(app, ["ledger", "adopt", str(fill_id)])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[1] == (
        f"⚠️ closed at or below the stop on {DAYS[B]}, {DAYS[B + 1]}: by the rules it should "
        "have been sold"
    )


def test_ledger_adopt_refuses_with_one_line(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _adopt_world(session, monkeypatch, tmp_path)
    result = runner.invoke(app, ["ledger", "adopt", "99"])
    assert result.exit_code == 1
    assert result.stderr == "⚠️ fill 99 is not a manual buy\n"
    bad = runner.invoke(app, ["ledger", "adopt", "1", "--date", "2026-13-01"])
    assert bad.exit_code == 1
    assert bad.stderr.startswith("⚠️ ")
