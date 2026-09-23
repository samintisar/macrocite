import httpx
import pytest

from signalbench.ingest.finnhub import FinnhubClient, finnhub_symbol
from signalbench.ingest.ratelimit import RateLimiter

FAST = RateLimiter(calls=1_000_000, period=1.0)


def _finnhub(handler: object, sleeps: list[float] | None = None) -> FinnhubClient:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    record = sleeps if sleeps is not None else []
    return FinnhubClient(
        "secret-key",
        httpx.Client(transport=transport),
        limiter=FAST,
        sleep=record.append,
    )


def test_sends_token_in_header_not_url() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[{"id": 1}])

    result = _finnhub(handler).get("/company-news", {"symbol": "NVDA"})
    assert result == [{"id": 1}]
    assert seen[0].headers["X-Finnhub-Token"] == "secret-key"
    assert "secret-key" not in str(seen[0].url)
    assert str(seen[0].url) == "https://finnhub.io/api/v1/company-news?symbol=NVDA"


def test_retries_429_with_backoff() -> None:
    calls = {"n": 0}
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429)
        return httpx.Response(200, json={"ok": True})

    assert _finnhub(handler, sleeps).get("/x", {}) == {"ok": True}
    assert sleeps == [1.0, 2.0]


def test_gives_up_after_retries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    with pytest.raises(httpx.HTTPStatusError, match="429"):
        _finnhub(handler).get("/x", {})


def test_finnhub_symbol_uses_dots_for_share_classes() -> None:
    assert finnhub_symbol("BRK-B") == "BRK.B"
    assert finnhub_symbol("GOOG") == "GOOG"
