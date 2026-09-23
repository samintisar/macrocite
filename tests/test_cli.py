from datetime import date

import httpx
import pytest
from sqlmodel import Session, select
from typer.testing import CliRunner

from signalbench import cli
from signalbench.cli import app
from signalbench.db.models import EarningsEvent, Ticker, TickerKind
from signalbench.ingest.finnhub import FinnhubClient
from signalbench.ingest.prices import PriceIngestResult
from signalbench.ingest.ratelimit import RateLimiter

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
