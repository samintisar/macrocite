from datetime import date

import pytest

from signalbench.config import Settings


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("FINNHUB_API_KEY", "PRICE_HISTORY_START", "FILINGS_BACKFILL_START"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)
    assert settings.finnhub_api_key is None
    assert settings.price_history_start == date(2010, 1, 1)
    assert settings.filings_backfill_start == date(2016, 1, 1)


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRICE_HISTORY_START", "2012-01-03")
    monkeypatch.setenv("FILINGS_BACKFILL_START", "2018-06-01")
    monkeypatch.setenv("FINNHUB_API_KEY", "test-key")
    settings = Settings(_env_file=None)
    assert settings.price_history_start == date(2012, 1, 3)
    assert settings.filings_backfill_start == date(2018, 6, 1)
    assert settings.finnhub_api_key == "test-key"
