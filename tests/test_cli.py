from datetime import date

import pytest
from sqlmodel import Session
from typer.testing import CliRunner

from signalbench import cli
from signalbench.cli import app
from signalbench.db.models import Ticker, TickerKind

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
