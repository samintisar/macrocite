import time
from collections.abc import Callable
from typing import Any

import httpx

from signalbench.ingest.ratelimit import RateLimiter

FINNHUB_BASE_URL = "https://finnhub.io/api/v1"
# The free tier allows 60 calls/minute; stay under it.
FINNHUB_CALLS_PER_MINUTE = 50


class FinnhubClient:
    def __init__(
        self,
        api_key: str,
        client: httpx.Client,
        limiter: RateLimiter | None = None,
        retries: int = 5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_key = api_key
        self._client = client
        self._limiter = limiter or RateLimiter(calls=FINNHUB_CALLS_PER_MINUTE, period=60.0)
        self._retries = retries
        self._sleep = sleep

    def get(self, path: str, params: dict[str, str]) -> Any:
        last: httpx.Response | None = None
        for attempt in range(self._retries):
            self._limiter.wait()
            response = self._client.get(
                f"{FINNHUB_BASE_URL}{path}",
                params=params,
                headers={"X-Finnhub-Token": self._api_key},
            )
            if response.status_code == 429:
                last = response
                self._sleep(min(60.0, 2.0**attempt))
                continue
            response.raise_for_status()
            return response.json()
        assert last is not None
        raise httpx.HTTPStatusError(
            f"429 Too Many Requests after {self._retries} attempts: {path}",
            request=last.request,
            response=last,
        )
