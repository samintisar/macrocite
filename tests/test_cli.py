from datetime import date, datetime
from pathlib import Path

import httpx
import pytest
from sqlmodel import Session, select
from typer.testing import CliRunner

from signalbench import cli
from signalbench.backtest.runner import JevInputs
from signalbench.cli import app
from signalbench.db.models import BacktestRun, EarningsEvent, Ticker, TickerKind
from signalbench.ingest.cdr import CdrEntry
from signalbench.ingest.finnhub import FinnhubClient
from signalbench.ingest.prices import PriceIngestResult
from signalbench.ingest.ratelimit import RateLimiter
from signalbench.strategy.config import (
    CashVehicle,
    StrategyConfig,
    config_sha256,
    load_strategy_config,
)

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


def test_backtest_run_refuses_sentiment_with_the_jev_filter() -> None:
    result = runner.invoke(app, ["backtest", "run", "--setup", "sentiment", "--jev", "filter"])
    assert result.exit_code == 2
    assert "--setup sentiment runs with --jev off" in result.stderr


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


def _survey_rows(bid: float, ask: float) -> str:
    return "readings:\n" + "".join(
        f"  - {{cdr_symbol: Z{i}, bid: {bid}, ask: {ask}}}\n" for i in range(5)
    )


def _registered_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, version: str = "test"
) -> Path:
    """The fixture config at <repo>/data/strategy_<version>.yaml with REPO_ROOT = tmp_path, and a
    survey whose 0.2% median spread gives the fixture's cost_per_side of 0.002."""
    (tmp_path / "data").mkdir(exist_ok=True)
    config = tmp_path / "data" / f"strategy_{version}.yaml"
    body = FIXTURE_CONFIG.read_bytes()
    if version != "test":
        body = body.replace(b"version: test", f"version: {version}".encode())
    config.write_bytes(body)
    survey = tmp_path / "data" / "cdr_spread_survey.yaml"
    survey.write_text(_survey_rows(99.9, 100.1), encoding="utf-8")
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cli, "SPREAD_SURVEY_PATH", survey)
    monkeypatch.setattr(cli, "committed_unchanged", lambda _repo, _path: True)
    return config


def test_backtest_run_needs_a_committed_survey(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _registered_config(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "committed_unchanged", lambda _repo, path: path == config)
    monkeypatch.setattr(cli, "get_session", lambda: session)
    result = runner.invoke(app, ["backtest", "run", "--setup", "pullback", "--config", str(config)])
    assert result.exit_code == 1
    assert "cdr_spread_survey.yaml must be committed, unchanged" in result.stderr


def test_backtest_run_refuses_an_unfilled_survey(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _registered_config(monkeypatch, tmp_path)
    (tmp_path / "data" / "cdr_spread_survey.yaml").write_text(
        "readings:\n  - {cdr_symbol: ZNVD, bid: , ask: }\n", encoding="utf-8"
    )
    monkeypatch.setattr(cli, "get_session", lambda: session)
    result = runner.invoke(app, ["backtest", "run", "--setup", "pullback", "--config", str(config)])
    assert result.exit_code == 1
    assert "readings with both bid and ask" in result.stderr


def test_backtest_run_refuses_a_cost_that_differs_from_the_survey(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _registered_config(monkeypatch, tmp_path)
    # A 0.4% median spread gives 0.003; the config says 0.002.
    (tmp_path / "data" / "cdr_spread_survey.yaml").write_text(
        _survey_rows(99.8, 100.2), encoding="utf-8"
    )
    monkeypatch.setattr(cli, "get_session", lambda: session)
    result = runner.invoke(app, ["backtest", "run", "--setup", "pullback", "--config", str(config)])
    assert result.exit_code == 1
    assert "cost_per_side 0.002" in result.stderr
    assert "0.003" in result.stderr


def test_backtest_run_refuses_a_config_outside_data_strategy_version(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Committed and unchanged, but not data/strategy_<version>.yaml: refused before any data.
    survey = tmp_path / "cdr_spread_survey.yaml"
    survey.write_text(_survey_rows(99.9, 100.1), encoding="utf-8")
    monkeypatch.setattr(cli, "SPREAD_SURVEY_PATH", survey)
    monkeypatch.setattr(cli, "get_session", lambda: session)
    monkeypatch.setattr(cli, "committed_unchanged", lambda _repo, _path: True)
    result = runner.invoke(
        app, ["backtest", "run", "--setup", "pullback", "--config", str(FIXTURE_CONFIG)]
    )
    assert result.exit_code == 1
    assert "data/strategy_test.yaml" in result.stderr


def test_backtest_run_refuses_a_version_whose_file_name_differs(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _registered_config(monkeypatch, tmp_path)
    renamed = config.with_name("strategy_v1.yaml")  # holds `version: test`
    config.rename(renamed)
    monkeypatch.setattr(cli, "get_session", lambda: session)
    result = runner.invoke(app, ["backtest", "run", "--setup", "pullback", "--config", str(renamed)])
    assert result.exit_code == 1
    assert "data/strategy_test.yaml" in result.stderr


def test_backtest_run_refuses_a_changed_config_under_a_used_version(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _registered_config(monkeypatch, tmp_path)
    _stored_run(session)  # version "test" with config_sha256 "c" * 64
    monkeypatch.setattr(cli, "get_session", lambda: session)
    monkeypatch.setattr(cli, "git_sha", lambda _repo: "abc123")
    monkeypatch.setattr(cli, "REPORTS_DIR", tmp_path / "reports")
    result = runner.invoke(app, ["backtest", "run", "--setup", "pullback", "--config", str(config)])
    assert result.exit_code == 1
    assert "must be a new version" in result.stderr
    assert not (tmp_path / "reports").exists()


def test_backtest_run_wires_config_calendar_and_git(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: dict[str, object] = {}

    def fake_run(_session: Session, **kwargs: object) -> tuple[BacktestRun, Path]:
        calls.update(kwargs)
        return _stored_run(session), tmp_path / "2026-09-24-breakout-off.md"

    config = _registered_config(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "get_session", lambda: session)
    monkeypatch.setattr(cli, "git_sha", lambda _repo: "abc123")
    monkeypatch.setattr(cli, "NyseSessions", lambda: "calendar")
    monkeypatch.setattr(cli, "run_backtest", fake_run)
    result = runner.invoke(app, ["backtest", "run", "--setup", "breakout", "--config", str(config)])
    assert result.exit_code == 0, result.stderr
    assert (calls["setup"], calls["jev_mode"], calls["git_sha"]) == ("breakout", "off", "abc123")
    assert calls["calendar"] == "calendar"
    now = calls["now"]
    assert isinstance(now, datetime) and now.utcoffset() is not None  # an aware clock
    assert calls["run_date"] == now.date()
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


def _assert_clean_exit(result: object, needle: str) -> None:
    """Exit 1 through typer.Exit (no uncaught exception), with one error line on stderr."""
    exception = getattr(result, "exception", None)
    assert getattr(result, "exit_code", None) == 1
    assert isinstance(exception, SystemExit), exception
    stderr = str(getattr(result, "stderr", ""))
    assert needle in stderr
    assert "Traceback" not in stderr
    assert len(stderr.strip().splitlines()) == 1


def test_backtest_run_prints_a_config_error_cleanly(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _registered_config(monkeypatch, tmp_path)
    config.write_text(FIXTURE_CONFIG.read_text(encoding="utf-8") + "surprise: 1\n", encoding="utf-8")
    monkeypatch.setattr(cli, "get_session", lambda: session)
    result = runner.invoke(app, ["backtest", "run", "--setup", "pullback", "--config", str(config)])
    _assert_clean_exit(result, "unknown keys ['surprise']")


def test_backtest_run_prints_an_unseeded_universe_cleanly(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _registered_config(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "get_session", lambda: session)
    monkeypatch.setattr(cli, "git_sha", lambda _repo: "abc123")
    monkeypatch.setattr(cli, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(cli, "load_universe", lambda _path: [
        CdrEntry("AAA", "ZAAA", "ZAAA.NE", "Aaa", "Information Technology"),
    ])
    result = runner.invoke(app, ["backtest", "run", "--setup", "pullback", "--config", str(config)])
    _assert_clean_exit(result, "Not seeded: AAA, QQQ")


def test_backtest_run_prints_missing_benchmark_prices_cleanly(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _registered_config(monkeypatch, tmp_path)
    _stocks(session, ["AAA"])
    session.add(Ticker(symbol="QQQ", company_name="QQQ", kind=TickerKind.benchmark))
    session.commit()
    monkeypatch.setattr(cli, "get_session", lambda: session)
    monkeypatch.setattr(cli, "git_sha", lambda _repo: "abc123")
    monkeypatch.setattr(cli, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(cli, "load_universe", lambda _path: [
        CdrEntry("AAA", "ZAAA", "ZAAA.NE", "Aaa", "Information Technology"),
    ])
    result = runner.invoke(app, ["backtest", "run", "--setup", "pullback", "--config", str(config)])
    _assert_clean_exit(result, "No QQQ prices")


def _capture_run(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, passed: bool = False
) -> dict[str, object]:
    calls: dict[str, object] = {}

    def fake_run(_session: Session, **kwargs: object) -> tuple[BacktestRun, Path]:
        calls.update(kwargs)
        run = _stored_run(session)
        run.jev_mode = str(kwargs["jev_mode"])
        run.passed = passed
        return run, tmp_path / "report.md"

    monkeypatch.setattr(cli, "get_session", lambda: session)
    monkeypatch.setattr(cli, "git_sha", lambda _repo: "abc123")
    monkeypatch.setattr(cli, "NyseSessions", lambda: "calendar")
    monkeypatch.setattr(cli, "run_backtest", fake_run)
    return calls


def test_backtest_run_sentiment_reads_jev_without_a_filter(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _registered_config(monkeypatch, tmp_path)
    calls = _capture_run(session, monkeypatch, tmp_path)
    result = runner.invoke(app, ["backtest", "run", "--setup", "sentiment", "--config", str(config)])
    assert result.exit_code == 0, result.stderr
    assert (calls["setup"], calls["jev_mode"]) == ("sentiment", "off")
    assert calls["jev"] == JevInputs(theta_block=None)


def _filter_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str | None) -> Path:
    path = tmp_path / "data" / "jev_filter_v1.yaml"
    if body is not None:
        path.write_text(body, encoding="utf-8")
    monkeypatch.setattr(cli, "JEV_FILTER_PATH", path)
    return path


# The v1-version config filter tests run against: same bytes `_registered_config(version="v1")`
# writes, so this sha256 is the one `backtest run --jev filter` computes from that file.
V1_CONFIG_SHA256 = config_sha256(
    FIXTURE_CONFIG.read_bytes().replace(b"version: test", b"version: v1")
)
FILTER_ON = (
    "mode: 'on'\ntheta_fit: 0.7\ntheta_block: 0.7\nquestion_set: q1\n"
    "model_requested: typesafe/jev-1.13\n"
    f"strategy_config_sha256: {V1_CONFIG_SHA256}\n"
)
FILTER_INFO = (
    "mode: information_only\ntheta_block: null\nquestion_set: q1\n"
    "model_requested: typesafe/jev-1.13\n"
    f"strategy_config_sha256: {'a' * 64}\n"
)


def test_backtest_run_filter_uses_the_committed_theta(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _registered_config(monkeypatch, tmp_path, version="v1")
    _filter_file(monkeypatch, tmp_path, FILTER_ON)
    calls = _capture_run(session, monkeypatch, tmp_path)
    result = runner.invoke(
        app, ["backtest", "run", "--setup", "breakout", "--jev", "filter", "--config", str(config)]
    )
    assert result.exit_code == 0, result.stderr
    assert calls["jev_mode"] == "filter"
    assert calls["jev"] == JevInputs(theta_block=0.7)
    assert "FAIL (information only: the Jev-off v1 result stands)" in result.stdout


def test_backtest_run_filter_pass_stays_information_only(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _registered_config(monkeypatch, tmp_path, version="v1")
    _filter_file(monkeypatch, tmp_path, FILTER_ON)
    _capture_run(session, monkeypatch, tmp_path, passed=True)
    result = runner.invoke(
        app, ["backtest", "run", "--setup", "breakout", "--jev", "filter", "--config", str(config)]
    )
    assert result.exit_code == 0, result.stderr
    assert "PASS (information only: the Jev-off v1 result stands)" in result.stdout


@pytest.mark.parametrize(
    ("body", "committed", "message"),
    [
        (None, True, "jev_filter_v1.yaml not found"),
        (FILTER_ON, False, "jev_filter_v1.yaml must be committed, unchanged"),
        (FILTER_INFO, True, "information-only"),
        ("mode: maybe\n", True, "mode must be"),
    ],
)
def test_backtest_run_filter_refusals(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    body: str | None,
    committed: bool,
    message: str,
) -> None:
    config = _registered_config(monkeypatch, tmp_path)
    path = _filter_file(monkeypatch, tmp_path, body)
    monkeypatch.setattr(
        cli, "committed_unchanged", lambda _repo, target: committed or target != path
    )
    calls = _capture_run(session, monkeypatch, tmp_path)
    result = runner.invoke(
        app, ["backtest", "run", "--setup", "pullback", "--jev", "filter", "--config", str(config)]
    )
    assert result.exit_code == 1
    assert message in result.stderr
    assert calls == {}


def test_backtest_run_filter_refuses_a_config_the_filter_was_not_fitted_on(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The filter file records the sha256 of the strategy_v1.yaml it was fitted on; a run
    against a different config (even one that also claims version v1) must be refused (spec 03),
    since applying a theta fitted on other trades would silently change the result."""
    config = _registered_config(monkeypatch, tmp_path, version="v1")
    stale = FILTER_ON.replace(V1_CONFIG_SHA256, "f" * 64)
    _filter_file(monkeypatch, tmp_path, stale)
    calls = _capture_run(session, monkeypatch, tmp_path)
    result = runner.invoke(
        app, ["backtest", "run", "--setup", "breakout", "--jev", "filter", "--config", str(config)]
    )
    assert result.exit_code == 1
    assert "was fitted on a different" in result.stderr
    assert calls == {}


def test_backtest_run_filter_refuses_a_config_version_other_than_v1(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """jev_filter_v1.yaml is fitted from stored v1 runs only; a --config outside version v1 (even
    with a matching sha, which can't really happen since the sha is content-derived) is refused."""
    config = _registered_config(monkeypatch, tmp_path, version="test")
    body = FILTER_ON.replace(V1_CONFIG_SHA256, config_sha256(config.read_bytes()))
    _filter_file(monkeypatch, tmp_path, body)
    calls = _capture_run(session, monkeypatch, tmp_path)
    result = runner.invoke(
        app, ["backtest", "run", "--setup", "breakout", "--jev", "filter", "--config", str(config)]
    )
    assert result.exit_code == 1
    assert "was fitted on a different" in result.stderr
    assert calls == {}


def test_backtest_run_passes_a_qqq_variant_config_through(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Spec 06 needs no new CLI option: --config carries `time_limit: null` and `cash_vehicle`."""
    calls: dict[str, object] = {}

    def fake_run(_session: Session, **kwargs: object) -> tuple[BacktestRun, Path]:
        calls.update(kwargs)
        return _stored_run(session), tmp_path / "2026-09-29-v2-none-qqq-breakout-off.md"

    config = _registered_config(monkeypatch, tmp_path, version="v2-none-qqq")
    body = config.read_text(encoding="utf-8").replace("    time_limit: 30\n", "    time_limit: null\n")
    body = body.replace(
        "cost_per_side: 0.002\n",
        "cost_per_side: 0.002\ncash_vehicle:\n  symbol: QQQ\n  cost_per_side: 0.002\n",
        1,
    )
    config.write_text(body, encoding="utf-8")
    monkeypatch.setattr(cli, "get_session", lambda: session)
    monkeypatch.setattr(cli, "git_sha", lambda _repo: "abc123")
    monkeypatch.setattr(cli, "NyseSessions", lambda: "calendar")
    monkeypatch.setattr(cli, "run_backtest", fake_run)
    result = runner.invoke(app, ["backtest", "run", "--setup", "breakout", "--config", str(config)])
    assert result.exit_code == 0, result.stderr
    strategy = calls["config"]
    assert isinstance(strategy, StrategyConfig)
    assert strategy.version == "v2-none-qqq"
    assert strategy.breakout.time_limit is None
    assert strategy.cash_vehicle == CashVehicle(symbol="QQQ", cost_per_side=0.002)
    assert calls["config_sha256"] == config_sha256(config.read_bytes())
