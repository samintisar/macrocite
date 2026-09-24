from datetime import date
from pathlib import Path

import httpx
import pytest
from sqlmodel import Session, select
from typer.testing import CliRunner

from signalbench import cli
from signalbench.cli import app
from signalbench.db.models import BacktestRun, EarningsEvent, Ticker, TickerKind
from signalbench.ingest.finnhub import FinnhubClient
from signalbench.ingest.prices import PriceIngestResult
from signalbench.ingest.ratelimit import RateLimiter
from signalbench.strategy.config import load_strategy_config

runner = CliRunner()


def test_top_level_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("universe", "seed", "ingest"):
        assert command in result.stdout
    assert "seed-watchlist" not in result.stdout


def test_ingest_help_lists_every_source() -> None:
    result = runner.invoke(app, ["ingest", "--help"])
    assert result.exit_code == 0
    for command in ("prices", "filings", "earnings", "news", "all", "stats"):
        assert command in result.stdout


def test_universe_help_lists_refresh() -> None:
    result = runner.invoke(app, ["universe", "--help"])
    assert result.exit_code == 0
    assert "refresh" in result.stdout


def _stocks(session: Session, symbols: list[str]) -> list[Ticker]:
    tickers = [Ticker(symbol=symbol, company_name=symbol, kind=TickerKind.us_stock) for symbol in symbols]
    session.add_all(tickers)
    session.commit()
    return tickers


def _use_session(monkeypatch: pytest.MonkeyPatch, session: Session, tickers: list[Ticker]) -> None:
    monkeypatch.setattr(cli, "get_session", lambda: session)
    monkeypatch.setattr(cli, "_universe_tickers", lambda _session, _kinds=None: tickers)


def test_filings_failure_for_one_ticker_does_not_stop_the_others(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_session(monkeypatch, session, _stocks(session, ["AAPL", "BAD", "MSFT"]))
    seen: list[str] = []

    def ingest(_session: Session, symbol: str, *_args: object, **_kwargs: object) -> int:
        seen.append(symbol)
        if symbol == "BAD":
            raise ValueError("Ticker not found in SEC company_tickers.json: BAD")
        return 1

    monkeypatch.setattr(cli, "ingest_eight_ks_for_symbol", ingest)
    result = runner.invoke(app, ["ingest", "filings"])
    assert seen == ["AAPL", "BAD", "MSFT"]
    assert result.exit_code == 1
    assert "filings 3/3 MSFT +1" in result.stdout
    assert (
        "filings 2/3 BAD FAILED: ValueError: Ticker not found in SEC company_tickers.json: BAD"
        in result.stderr
    )
    assert "filings: 1 failed: BAD" in result.stderr


def test_ingest_all_runs_every_step_then_exits_1_on_ticker_failures(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_session(monkeypatch, session, _stocks(session, ["AAPL", "BAD"]))
    monkeypatch.setattr(cli, "_ingest_prices", lambda _session, full=False: [])
    monkeypatch.setattr(cli, "_ingest_earnings", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli.settings, "finnhub_api_key", "k")

    def ingest_filings(_session: Session, symbol: str, *_args: object, **_kwargs: object) -> int:
        if symbol == "BAD":
            raise RuntimeError("boom")
        return 0

    news: list[str] = []

    def ingest_news(_session: Session, _finnhub: object, ticker: Ticker, _start: date, _end: date) -> int:
        news.append(ticker.symbol)
        return 0

    monkeypatch.setattr(cli, "ingest_eight_ks_for_symbol", ingest_filings)
    monkeypatch.setattr(cli, "ingest_company_news", ingest_news)
    result = runner.invoke(app, ["ingest", "all"])
    assert result.exit_code == 1
    assert "filings 2/2 BAD FAILED: RuntimeError: boom" in result.stderr
    assert sorted(set(news)) == ["AAPL", "BAD"]
    assert "filings: 1 failed: BAD" in result.stderr


def test_ingest_all_reports_a_failed_earnings_calendar_after_news(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    aapl, bad = _stocks(session, ["AAPL", "BAD"])
    _use_session(monkeypatch, session, [aapl, bad])
    session.add(EarningsEvent(ticker_id=bad.id, event_date=date(2099, 1, 5), source="finnhub"))
    session.commit()
    monkeypatch.setattr(cli, "_ingest_prices", lambda _session, full=False: [])
    monkeypatch.setattr(cli, "ingest_eight_ks_for_symbol", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(cli.settings, "finnhub_api_key", "k")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["symbol"] == "BAD":
            return httpx.Response(500)
        return httpx.Response(200, json={"earningsCalendar": [{"symbol": "AAPL", "date": "2099-01-02"}]})

    real_client = httpx.Client
    monkeypatch.setattr(
        cli.httpx, "Client", lambda **_kwargs: real_client(transport=httpx.MockTransport(handler))
    )
    fast = RateLimiter(calls=1_000_000, period=1.0)
    monkeypatch.setattr(cli, "FinnhubClient", lambda key, client: FinnhubClient(key, client, limiter=fast))
    news: list[str] = []

    def ingest_news(_session: Session, _finnhub: object, ticker: Ticker, _start: date, _end: date) -> int:
        news.append(ticker.symbol)
        return 0

    monkeypatch.setattr(cli, "ingest_company_news", ingest_news)
    result = runner.invoke(app, ["ingest", "all"])
    assert result.exit_code == 1
    assert "earnings 1/2 AAPL +1" in result.stdout
    assert "earnings 2/2 BAD FAILED: HTTPStatusError:" in result.stderr
    assert sorted(set(news)) == ["AAPL", "BAD"]
    assert "earnings: 1 failed: BAD" in result.stderr
    stored = {(row.ticker_id, row.event_date) for row in session.exec(select(EarningsEvent)).all()}
    assert stored == {(aapl.id, date(2099, 1, 2)), (bad.id, date(2099, 1, 5))}


def test_liquidity_failure_is_reported_and_later_steps_still_run(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_session(monkeypatch, session, _stocks(session, ["AAPL"]))
    monkeypatch.setattr(cli, "ingest_daily_prices", lambda *_args, **_kwargs: PriceIngestResult(0, 0, 0))
    monkeypatch.setattr(cli, "_ingest_earnings", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(cli.settings, "finnhub_api_key", None)
    filings: list[str] = []

    def ingest_filings(_session: Session, symbol: str, *_args: object, **_kwargs: object) -> int:
        filings.append(symbol)
        return 0

    monkeypatch.setattr(cli, "ingest_eight_ks_for_symbol", ingest_filings)
    result = runner.invoke(app, ["ingest", "all"])  # no QQQ rows, so liquidity cannot run
    assert result.exit_code == 1
    assert "liquidity FAILED: RuntimeError:" in result.stderr
    assert filings == ["AAPL"]
    assert "prices: 1 failed: liquidity" in result.stderr


FIXTURE_CONFIG = Path(__file__).parent / "fixtures" / "strategy_test.yaml"


def test_backtest_help_lists_run_and_show() -> None:
    result = runner.invoke(app, ["backtest", "--help"])
    assert result.exit_code == 0
    for command in ("run", "show"):
        assert command in result.stdout


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--setup", "sentiment"], "--setup sentiment requires Jev readings (spec 03)."),
        (["--setup", "pullback", "--jev", "filter"], "--jev filter requires Jev readings (spec 03)."),
    ],
)
def test_backtest_run_refuses_spec_03_modes(args: list[str], message: str) -> None:
    result = runner.invoke(app, ["backtest", "run", *args])
    assert result.exit_code == 2
    assert message in result.stderr


def test_backtest_run_needs_the_config_file(tmp_path: Path) -> None:
    missing = tmp_path / "strategy_v1.yaml"
    result = runner.invoke(app, ["backtest", "run", "--setup", "pullback", "--config", str(missing)])
    assert result.exit_code == 1
    assert "not found" in result.stderr


def test_backtest_run_needs_a_committed_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "committed_unchanged", lambda _repo, _path: False)
    result = runner.invoke(
        app, ["backtest", "run", "--setup", "pullback", "--config", str(FIXTURE_CONFIG)]
    )
    assert result.exit_code == 1
    assert "must be committed, unchanged" in result.stderr


def _stored_run(session: Session) -> BacktestRun:
    run = BacktestRun(
        strategy_version="test", config_sha256="c" * 64, git_sha="abc123", setup="breakout",
        jev_mode="off", start_date=date(2012, 1, 3), end_date=date(2026, 9, 23),
        data_fingerprint="d" * 64, metrics={},
        pass_bar={"trades": {"value": 12.0, "threshold": 30.0, "passed": False}},
        passed=False, trade_log={"trades": [], "events": []},
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def test_backtest_run_wires_config_calendar_and_git(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: dict[str, object] = {}

    def fake_run(_session: Session, **kwargs: object) -> tuple[BacktestRun, Path]:
        calls.update(kwargs)
        return _stored_run(session), tmp_path / "2026-09-24-breakout-off.md"

    monkeypatch.setattr(cli, "get_session", lambda: session)
    monkeypatch.setattr(cli, "committed_unchanged", lambda _repo, _path: True)
    monkeypatch.setattr(cli, "git_sha", lambda _repo: "abc123")
    monkeypatch.setattr(cli, "NyseSessions", lambda: "calendar")
    monkeypatch.setattr(cli, "run_backtest", fake_run)
    result = runner.invoke(
        app, ["backtest", "run", "--setup", "breakout", "--config", str(FIXTURE_CONFIG)]
    )
    assert result.exit_code == 0, result.stderr
    assert (calls["setup"], calls["jev_mode"], calls["git_sha"]) == ("breakout", "off", "abc123")
    assert calls["calendar"] == "calendar"
    assert calls["config_sha256"] == load_strategy_config(FIXTURE_CONFIG)[1]
    assert "FAIL" in result.stdout
    assert "-- trades: 12.000 vs 30.000" in result.stdout
    assert "report: " in result.stdout


def test_backtest_show_prints_the_stored_report(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _stored_run(session)
    monkeypatch.setattr(cli, "get_session", lambda: session)
    monkeypatch.setattr(cli, "render_report", lambda stored: f"report for {stored.id}\n")
    result = runner.invoke(app, ["backtest", "show", str(run.id)])
    assert result.exit_code == 0
    assert result.stdout == f"report for {run.id}\n"


def test_backtest_show_unknown_or_bad_id(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "get_session", lambda: session)
    unknown = runner.invoke(app, ["backtest", "show", "00000000-0000-0000-0000-000000000000"])
    assert unknown.exit_code == 1
    assert "No backtest run" in unknown.stderr
    bad = runner.invoke(app, ["backtest", "show", "not-a-uuid"])
    assert bad.exit_code == 1
    assert "Not a run id" in bad.stderr


def test_backtest_cost_prints_the_cost_per_side(tmp_path: Path) -> None:
    survey = tmp_path / "survey.yaml"
    rows = "".join(
        f"  - {{cdr_symbol: Z{i}, bid: 99.8, ask: 100.2}}\n" for i in range(5)
    )
    survey.write_text("readings:\n" + rows + "  - {cdr_symbol: ZMET, bid: , ask: }\n", encoding="utf-8")
    result = runner.invoke(app, ["backtest", "cost", "--survey", str(survey)])
    assert result.exit_code == 0, result.stderr
    assert "complete readings: 5 (incomplete: 1)" in result.stdout
    assert "median spread: 0.400%" in result.stdout
    assert "cost_per_side: 0.003" in result.stdout


def test_backtest_cost_refuses_an_unfilled_survey(tmp_path: Path) -> None:
    survey = tmp_path / "survey.yaml"
    survey.write_text("readings:\n  - {cdr_symbol: ZNVD, bid: , ask: }\n", encoding="utf-8")
    result = runner.invoke(app, ["backtest", "cost", "--survey", str(survey)])
    assert result.exit_code == 1
    assert "has 0 readings with both bid and ask" in result.stderr
