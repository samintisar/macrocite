from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlmodel import Session, select
from typer.testing import CliRunner

from signalbench import cli
from signalbench.cli import app
from signalbench.db.models import Price
from signalbench.ingest.kraken import crypto_ticker

runner = CliRunner()
FIXTURE = Path(__file__).parent / "fixtures" / "kraken" / "XBTUSD_1440.csv"
CONFIG = Path(__file__).parent / "fixtures" / "strategy_crypto_test.yaml"
PAIR_KEYS = {"XBTUSD": "XXBTZUSD", "ETHUSD": "XETHZUSD"}


def _seed_history(session: Session, *symbols: str) -> None:
    """A crypto ticker with one stored price (2014-12-31) per symbol."""
    for symbol in symbols:
        ticker = crypto_ticker(session, symbol)
        one = Decimal(1)
        session.add(
            Price(
                ticker_id=ticker.id,
                date=date(2014, 12, 31),
                open=one,
                high=one,
                low=one,
                close=one,
                adj_close=one,
                volume=1,
            )
        )
    session.commit()


def test_crypto_help_lists_its_commands() -> None:
    assert "crypto" in runner.invoke(app, ["--help"]).stdout
    result = runner.invoke(app, ["crypto", "--help"])
    assert result.exit_code == 0
    for command in ("ingest-file", "ingest-recent", "backtest"):
        assert command in result.stdout


def test_ingest_file_loads_a_kraken_daily_file(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "get_session", lambda: session)
    result = runner.invoke(app, ["crypto", "ingest-file", str(FIXTURE), "--symbol", "XBTUSD"])
    assert result.exit_code == 0, result.stderr
    assert "XBTUSD: +2 ~0 x1 (2015-01-01 to 2015-01-03)" in result.stdout
    assert len(session.exec(select(Price)).all()) == 2


def test_ingest_file_refuses_another_pairs_file() -> None:
    result = runner.invoke(app, ["crypto", "ingest-file", str(FIXTURE), "--symbol", "ETHUSD"])
    assert result.exit_code == 1
    assert "XBTUSD_1440.csv does not hold ETHUSD" in result.stderr


def test_ingest_recent_needs_the_file_history_first(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "get_session", lambda: session)
    result = runner.invoke(app, ["crypto", "ingest-recent"])
    assert result.exit_code == 1
    assert "ETHUSD FAILED: KrakenError: ETHUSD has no history yet" in result.stderr
    assert "XBTUSD FAILED: KrakenError: XBTUSD has no history yet" in result.stderr


def test_ingest_recent_upserts_both_pairs_from_the_public_api(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_history(session, "XBTUSD", "ETHUSD")
    monkeypatch.setattr(cli, "get_session", lambda: session)

    def handler(request: httpx.Request) -> httpx.Response:
        rows = [[1420070400, "10", "11", "9", "10.5", "10.2", "3.4", 7]]  # 2015-01-01
        key = PAIR_KEYS[request.url.params["pair"]]
        return httpx.Response(200, json={"error": [], "result": {key: rows, "last": 1420070400}})

    real_client = httpx.Client
    monkeypatch.setattr(
        cli.httpx, "Client", lambda **_kwargs: real_client(transport=httpx.MockTransport(handler))
    )
    result = runner.invoke(app, ["crypto", "ingest-recent"])
    assert result.exit_code == 0, result.stderr
    assert "ETHUSD: +1 ~0 x0 (to 2015-01-01)" in result.stdout
    assert "XBTUSD: +1 ~0 x0 (to 2015-01-01)" in result.stdout


def test_ingest_recent_continues_past_a_pair_that_fails(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_history(session, "XBTUSD", "ETHUSD")
    monkeypatch.setattr(cli, "get_session", lambda: session)

    def handler(request: httpx.Request) -> httpx.Response:
        pair = request.url.params["pair"]
        if pair == "ETHUSD":
            return httpx.Response(500, text="boom")
        rows = [[1420070400, "10", "11", "9", "10.5", "10.2", "3.4", 7]]  # 2015-01-01
        return httpx.Response(
            200, json={"error": [], "result": {PAIR_KEYS[pair]: rows, "last": 1420070400}}
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        cli.httpx, "Client", lambda **_kwargs: real_client(transport=httpx.MockTransport(handler))
    )
    result = runner.invoke(app, ["crypto", "ingest-recent"])
    assert result.exit_code == 1
    assert "XBTUSD: +1 ~0 x0 (to 2015-01-01)" in result.stdout
    assert "ETHUSD FAILED: HTTPStatusError:" in result.stderr
    stored = session.exec(select(Price).where(Price.date == date(2015, 1, 1))).all()
    assert len(stored) == 1


def test_ingest_recent_refuses_a_ticker_with_no_prices(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    crypto_ticker(session, "XBTUSD")
    crypto_ticker(session, "ETHUSD")
    monkeypatch.setattr(cli, "get_session", lambda: session)
    result = runner.invoke(app, ["crypto", "ingest-recent"])
    assert result.exit_code == 1
    assert "XBTUSD FAILED: KrakenError: XBTUSD has no history yet" in result.stderr


def test_ingest_file_reports_a_bad_row_without_a_traceback(
    tmp_path: Path, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "get_session", lambda: session)
    path = tmp_path / "XBTUSD_1440.csv"
    path.write_text("1420070400,abc,1,1,1,1,1\n", encoding="utf-8")
    result = runner.invoke(app, ["crypto", "ingest-file", str(path), "--symbol", "XBTUSD"])
    assert result.exit_code == 1
    assert "line 1:" in result.stderr
    assert isinstance(result.exception, SystemExit)


def test_backtest_needs_the_config_file(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["crypto", "backtest", "--config", str(tmp_path / "strategy_crypto-v1.yaml")]
    )
    assert result.exit_code == 1
    assert "not found" in result.stderr


def test_backtest_needs_a_committed_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "committed_unchanged", lambda _repo, _path: False)
    result = runner.invoke(app, ["crypto", "backtest", "--config", str(CONFIG)])
    assert result.exit_code == 1
    assert "must be committed, unchanged" in result.stderr


def test_backtest_refuses_a_config_outside_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "committed_unchanged", lambda _repo, _path: True)
    result = runner.invoke(app, ["crypto", "backtest", "--config", str(CONFIG)])
    assert result.exit_code == 1
    assert "must be data/strategy_crypto-test.yaml" in result.stderr
