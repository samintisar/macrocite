import re
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlmodel import Session, select
from typer.testing import CliRunner

from signalbench import cli
from signalbench.cli import app
from signalbench.db.models import (
    BacktestRun,
    DocType,
    DocumentTicker,
    JevReading,
    Price,
    RawDocument,
    Ticker,
    TickerKind,
)
from signalbench.ingest.cdr import CdrEntry
from signalbench.jev.client import JevFatalError
from signalbench.jev.fake import FakeJevClient, fake_result
from signalbench.jev.fixture import FIXTURE_TEXT, fixture_state
from strategy_helpers import WeekdaySessions

runner = CliRunner()
NEW_YORK = ZoneInfo("America/New_York")
UNIVERSE = [
    CdrEntry("AAA", "ZAAA", "ZAAA.NE", "Aaa", "Information Technology"),
    CdrEntry("BBB", "ZBBB", "ZBBB.NE", "Bbb", "Energy"),
]


def test_jev_help_lists_every_command() -> None:
    result = runner.invoke(app, ["jev", "--help"])
    assert result.exit_code == 0
    for command in ("test", "backfill", "fit-filter", "calibration"):
        assert command in result.stdout


def test_the_fixture_is_a_made_up_8k_without_dates() -> None:
    assert fixture_state().startswith(
        "Company: Example Holdings (EXMP)\nSource: SEC 8-K, items 2.02, 9.01\n\n"
    )
    assert not re.search(r"\b(19|20)\d{2}\b", FIXTURE_TEXT)


@pytest.mark.parametrize("command", [["test"], ["backfill"]])
def test_commands_that_call_jev_need_the_key(
    monkeypatch: pytest.MonkeyPatch, command: list[str]
) -> None:
    monkeypatch.setattr(cli.settings, "openrouter_api_key", None)
    result = runner.invoke(app, ["jev", *command])
    assert result.exit_code == 1
    assert "OPENROUTER_API_KEY is not set in .env" in result.stderr


def _fake(monkeypatch: pytest.MonkeyPatch, client: FakeJevClient) -> None:
    monkeypatch.setattr(cli.settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(cli, "OpenRouterJevClient", lambda _key, _http: client)
    monkeypatch.setattr(cli, "JEV_CONCURRENCY", 1)  # one call at a time: exact budget stops


def test_jev_test_prints_answers_latency_cost_and_build(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeJevClient()
    _fake(monkeypatch, client)
    result = runner.invoke(app, ["jev", "test"])
    assert result.exit_code == 0, result.stderr
    assert client.calls == [fixture_state()]
    assert "model: typesafe/jev-1.13-20260917" in result.stdout
    assert "impact: negative 0.100 | neutral 0.200 | positive 0.700" in result.stdout
    assert "event_type: earnings | routine 0.100" in result.stdout
    assert "latency 5 ms | input tokens 480 | cost $0.000020" in result.stdout


def test_jev_test_reports_a_failed_call(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake(monkeypatch, FakeJevClient(lambda _state: JevFatalError("HTTP 401: bad key")))
    result = runner.invoke(app, ["jev", "test"])
    assert result.exit_code == 1
    assert "Jev call failed: HTTP 401: bad key" in result.stderr


def _news(session: Session, ticker: Ticker, external_id: str, published: datetime) -> RawDocument:
    document = RawDocument(
        source="finnhub", external_id=external_id, doc_type=DocType.news, raw_text="x",
        text=f"Headline {external_id}", published_at=published,
    )
    session.add(document)
    session.flush()
    session.add(DocumentTicker(document_id=document.id, ticker_id=ticker.id))
    session.commit()
    return document


@pytest.fixture
def news_seeded(session: Session, monkeypatch: pytest.MonkeyPatch) -> Session:
    aaa = Ticker(symbol="AAA", company_name="Aaa Corp", kind=TickerKind.us_stock)
    session.add(aaa)
    session.commit()
    for index in range(3):
        _news(session, aaa, f"n{index}", datetime(2026, 9, 21, 14, index, tzinfo=UTC))
    monkeypatch.setattr(cli, "get_session", lambda: session)
    monkeypatch.setattr(cli, "load_universe", lambda _path: UNIVERSE)
    return session


def test_backfill_reads_once_and_reports_counts(
    news_seeded: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeJevClient()
    _fake(monkeypatch, client)
    first = runner.invoke(app, ["jev", "backfill", "--source", "news"])
    assert first.exit_code == 0, first.stderr
    assert "to read: 3 (already read 0, no text 0, too long 0)" in first.stdout
    assert "read 3 | cost $0.0001 | input tokens 1440" in first.stdout
    assert "model typesafe/jev-1.13-20260917: 3" in first.stdout
    second = runner.invoke(app, ["jev", "backfill", "--source", "news"])
    assert "to read: 0 (already read 3" in second.stdout
    assert len(client.calls) == 3


def test_backfill_since_and_budget(news_seeded: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake(monkeypatch, FakeJevClient(lambda _state: fake_result(cost_usd=0.6)))
    none_left = runner.invoke(app, ["jev", "backfill", "--since", "2026-09-22"])
    assert "to read: 0" in none_left.stdout
    result = runner.invoke(app, ["jev", "backfill", "--max-cost-usd", "1.00"])
    assert result.exit_code == 0, result.stderr  # a budget stop is clean
    assert "budget reached: $1.2000 of $1.00" in result.stdout
    assert "Run again to continue." in result.stdout
    assert len(news_seeded.exec(select(JevReading)).all()) == 2


def test_backfill_stops_with_exit_1_on_a_fatal_error(
    news_seeded: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake(monkeypatch, FakeJevClient(lambda _state: JevFatalError("HTTP 402: no credits")))
    result = runner.invoke(app, ["jev", "backfill"])
    assert result.exit_code == 1
    assert "stopped: HTTP 402: no credits" in result.stderr


def _stored_run(session: Session, setup: str, trades: list[tuple[str, str, float]]) -> None:
    session.add(
        BacktestRun(
            strategy_version="v1", config_sha256="c" * 64, git_sha="abc123", setup=setup,
            jev_mode="off", start_date=date(2012, 1, 3), end_date=date(2026, 9, 23),
            data_fingerprint="d" * 64, metrics={}, pass_bar={}, passed=False,
            trade_log={
                "trades": [{"symbol": s, "signal_date": d, "r": r} for s, d, r in trades],
                "events": [],
            },
        )
    )
    session.commit()


def _negative_reading(session: Session, ticker: Ticker, day: date) -> None:
    document = _news(
        session, ticker, f"neg-{day}", datetime.combine(day, time(10, 0), tzinfo=NEW_YORK)
    )
    session.add(
        JevReading(
            document_id=document.id, ticker_id=ticker.id, model_requested="typesafe/jev-1.13",
            model_resolved="typesafe/jev-1.13-20260917", question_set="q1", response_id="r",
            p_negative=0.9, p_neutral=0.08, p_positive=0.02, event_type="legal", p_routine=0.1,
            answers={}, input_tokens=100, cost_usd=0.0, latency_ms=1,
        )
    )
    session.commit()


@pytest.fixture
def filter_seeded(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[Session, Path]:
    """AAA has a negative reading 1 session before its trades; BBB has none. Fit: 10 blocked
    at -1R vs 20 kept at +0.5R. Confirmation: 10 blocked at -0.5R vs 5 kept at +0.3R -> ON."""
    aaa = Ticker(symbol="AAA", company_name="Aaa Corp", kind=TickerKind.us_stock)
    bbb = Ticker(symbol="BBB", company_name="Bbb Corp", kind=TickerKind.us_stock)
    session.add_all([aaa, bbb])
    session.commit()
    _negative_reading(session, aaa, date(2019, 5, 31))
    _negative_reading(session, aaa, date(2024, 5, 31))
    _stored_run(
        session, "pullback",
        [("AAA", "2019-06-03", -1.0)] * 10 + [("AAA", "2024-06-03", -0.5)] * 10,
    )
    _stored_run(
        session, "breakout",
        [("BBB", "2019-06-03", 0.5)] * 20 + [("BBB", "2024-06-03", 0.3)] * 5,
    )
    monkeypatch.setattr(cli, "get_session", lambda: session)
    monkeypatch.setattr(cli, "load_universe", lambda _path: UNIVERSE)
    monkeypatch.setattr(cli, "NyseSessions", WeekdaySessions)
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cli, "JEV_FILTER_PATH", tmp_path / "data" / "jev_filter_v1.yaml")
    monkeypatch.setattr(cli, "JEV_REPORTS_DIR", tmp_path / "reports" / "jev")
    return session, tmp_path


def test_fit_filter_prints_the_decision_and_writes_nothing_by_default(
    filter_seeded: tuple[Session, Path],
) -> None:
    _, root = filter_seeded
    result = runner.invoke(app, ["jev", "fit-filter"])
    assert result.exit_code == 0, result.stderr
    assert "decision: on, theta 0.5" in result.stdout
    assert "fit theta 0.5: blocked 10, kept 20" in result.stdout
    assert "confirm theta 0.5: blocked 10, kept 5" in result.stdout
    assert "Nothing written. Run with --write" in result.stdout
    assert not (root / "data").exists() and not (root / "reports").exists()


def test_fit_filter_write_records_the_file_and_report_once(
    filter_seeded: tuple[Session, Path],
) -> None:
    _, root = filter_seeded
    result = runner.invoke(app, ["jev", "fit-filter", "--write"])
    assert result.exit_code == 0, result.stderr
    text = (root / "data" / "jev_filter_v1.yaml").read_text(encoding="utf-8")
    assert "mode: 'on'" in text and "theta_block: 0.5" in text
    [report] = list((root / "reports" / "jev").glob("*-filter-decision.md"))
    assert f"report: reports/jev/{report.name}" in text
    assert report.read_text(encoding="utf-8").startswith("# Jev filter decision (spec 03)")
    report.write_text("kept", encoding="utf-8")
    again = runner.invoke(app, ["jev", "fit-filter", "--write"])
    assert again.exit_code == 1
    assert "already exists" in again.stderr
    assert report.read_text(encoding="utf-8") == "kept"  # refused before the report is rewritten


def test_fit_filter_needs_the_stored_runs(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "get_session", lambda: session)
    result = runner.invoke(app, ["jev", "fit-filter"])
    assert result.exit_code == 1
    assert "No stored v1 jev-off run for pullback, breakout" in result.stderr


def _price(session: Session, ticker: Ticker, day: date, close: float) -> None:
    value = Decimal(str(close))
    session.add(
        Price(ticker_id=ticker.id, date=day, open=value, high=value, low=value, close=value,
              adj_close=value, volume=1_000)
    )


def test_calibration_writes_the_report(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    aaa = Ticker(symbol="AAA", company_name="Aaa Corp", kind=TickerKind.us_stock)
    qqq = Ticker(symbol="QQQ", company_name="QQQ", kind=TickerKind.benchmark)
    session.add_all([aaa, qqq])
    session.commit()
    days = WeekdaySessions().sessions_between(date(2024, 5, 27), date(2024, 6, 14))
    for day in days:
        _price(session, aaa, day, 90.0 if day > date(2024, 5, 31) else 100.0)  # -10% after
        _price(session, qqq, day, 100.0)
    session.commit()
    _negative_reading(session, aaa, date(2024, 5, 31))
    monkeypatch.setattr(cli, "get_session", lambda: session)
    monkeypatch.setattr(cli, "load_universe", lambda _path: UNIVERSE)
    monkeypatch.setattr(cli, "NyseSessions", WeekdaySessions)
    monkeypatch.setattr(cli, "JEV_REPORTS_DIR", tmp_path)
    result = runner.invoke(app, ["jev", "calibration"])
    assert result.exit_code == 0, result.stderr
    [report] = list(tmp_path.glob("*-calibration.md"))
    text = report.read_text(encoding="utf-8")
    assert "| Labeled | 1 |" in text
    assert "Labels: down 1, flat 0, up 0." in text
    assert "labeled 1 | not labeled 0" in result.stdout


def test_calibration_without_readings_is_refused(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "get_session", lambda: session)
    monkeypatch.setattr(cli, "load_universe", lambda _path: UNIVERSE)
    monkeypatch.setattr(cli, "NyseSessions", WeekdaySessions)
    result = runner.invoke(app, ["jev", "calibration"])
    assert result.exit_code == 1
    assert "No Jev readings" in result.stderr
