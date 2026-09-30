from datetime import date
from pathlib import Path

import pytest

from signalbench.config import Settings


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "FINNHUB_API_KEY", "OPENROUTER_API_KEY", "PRICE_HISTORY_START", "FILINGS_BACKFILL_START"
    ):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)
    assert settings.finnhub_api_key is None
    assert settings.openrouter_api_key is None
    assert settings.price_history_start == date(2010, 1, 1)
    assert settings.filings_backfill_start == date(2016, 1, 1)


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRICE_HISTORY_START", "2012-01-03")
    monkeypatch.setenv("FILINGS_BACKFILL_START", "2018-06-01")
    monkeypatch.setenv("FINNHUB_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")
    settings = Settings(_env_file=None)
    assert settings.price_history_start == date(2012, 1, 3)
    assert settings.filings_backfill_start == date(2018, 6, 1)
    assert settings.finnhub_api_key == "test-key"
    assert settings.openrouter_api_key == "or-test-key"


def test_environment_variables_win_over_the_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The nightly script loads the main checkout's .env into the process without overriding
    what is already set; a run's own .env (none in the live worktree) must not win either."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=postgresql+psycopg://file:file@filehost.invalid:5432/file\n"
        "FINNHUB_API_KEY=from-file\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://env:env@envhost.invalid:5432/env")
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    settings = Settings(_env_file=env_file)
    assert settings.database_url == "postgresql+psycopg://env:env@envhost.invalid:5432/env"
    assert settings.finnhub_api_key == "from-file"
