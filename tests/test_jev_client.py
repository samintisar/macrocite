import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from signalbench.jev.client import (
    ENDPOINT,
    JevClient,
    JevFatalError,
    JevRejectedError,
    JevUnavailableError,
    OpenRouterJevClient,
)
from signalbench.jev.fake import FakeJevClient, fake_result
from signalbench.jev.questions import request_body

STATE = "Company: Apple (AAPL)\nSource: News\n\nApple raises its dividend"
OK_BODY: dict[str, Any] = {
    "answers": {
        "impact": {
            "type": "choice", "choice": "positive", "confidence": 0.7,
            "probabilities": {"negative": 0.05, "neutral": 0.15, "positive": 0.8},
        },
        "event_type": {
            "type": "choice", "choice": "product", "confidence": 0.5,
            "probabilities": {
                "earnings": 0.1, "guidance": 0.1, "leadership": 0.0, "legal": 0.0,
                "product": 0.6, "macro": 0.1, "other": 0.1,
            },
        },
        "routine": {"type": "noul", "noul": 0.1},
    },
    "id": "gen-dec-1",
    "model": "typesafe/jev-1.13-20260917",
    "provider": "TypeSafe",
    "usage": {"cost": 0.00002, "input_tokens": 480, "output_tokens": 70},
}

Step = int | Exception  # an HTTP status to answer with, or an exception to raise


def _client(steps: list[Step]) -> tuple[OpenRouterJevClient, list[httpx.Request], list[float]]:
    requests: list[httpx.Request] = []
    sleeps: list[float] = []
    queue = list(steps)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        step = queue.pop(0)
        if isinstance(step, Exception):
            raise step
        if step == 200:
            return httpx.Response(200, json=OK_BODY)
        return httpx.Response(step, json={"error": {"code": step, "message": f"status {step}"}})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    ticks = iter(float(i) * 0.25 for i in range(100))  # each clock read advances 0.25 s
    client = OpenRouterJevClient(
        "test-key", http, sleep=sleeps.append, clock=lambda: next(ticks)
    )
    return client, requests, sleeps


def test_the_request_matches_the_documented_schema() -> None:
    client, requests, _ = _client([200])
    client.read(STATE)
    (request,) = requests
    assert str(request.url) == ENDPOINT == "https://openrouter.ai/api/alpha/decisions"
    assert request.method == "POST"
    assert request.headers["Authorization"] == "Bearer test-key"
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.content) == request_body(STATE)


def test_a_200_is_parsed_with_its_latency() -> None:
    client, _, _ = _client([200])
    result = client.read(STATE)
    assert result.p_positive == 0.8 and result.event_type == "product"
    assert result.model_resolved == "typesafe/jev-1.13-20260917"
    assert result.latency_ms == 250  # one clock tick between send and receive


@pytest.mark.parametrize("status", [429, 500, 502, 503, 524, 529])
def test_retryable_statuses_are_retried_with_exponential_backoff(status: int) -> None:
    client, requests, sleeps = _client([status, status, 200])
    assert client.read(STATE).p_positive == 0.8
    assert len(requests) == 3
    assert sleeps == [1.0, 2.0]


def test_timeouts_and_connection_errors_are_retried() -> None:
    client, requests, sleeps = _client(
        [httpx.ReadTimeout("slow"), httpx.ConnectError("down"), 200]
    )
    assert client.read(STATE).p_positive == 0.8
    assert len(requests) == 3 and sleeps == [1.0, 2.0]


def test_three_retries_then_unavailable() -> None:
    client, requests, sleeps = _client([503, 503, 503, 503])
    with pytest.raises(JevUnavailableError, match="after 4 attempts: HTTP 503"):
        client.read(STATE)
    assert len(requests) == 4
    assert sleeps == [1.0, 2.0, 4.0]


@pytest.mark.parametrize("status", [400, 403, 413])
def test_document_level_rejections_are_not_retried(status: int) -> None:
    client, requests, sleeps = _client([status])
    with pytest.raises(JevRejectedError, match=f"HTTP {status}: status {status}") as raised:
        client.read(STATE)
    assert raised.value.status == status
    assert len(requests) == 1 and sleeps == []


@pytest.mark.parametrize("status", [401, 402, 404])
def test_account_level_failures_are_fatal_and_not_retried(status: int) -> None:
    client, requests, _ = _client([status])
    with pytest.raises(JevFatalError, match=f"HTTP {status}"):
        client.read(STATE)
    assert len(requests) == 1


def test_the_fake_client_satisfies_the_protocol_and_records_states() -> None:
    fake = FakeJevClient()
    client: JevClient = fake
    result = client.read(STATE)
    assert fake.calls == [STATE]
    assert result == fake_result()


def test_the_fake_client_can_answer_per_state_or_raise() -> None:
    def respond(state: str) -> Any:
        if "lawsuit" in state:
            return fake_result(p_negative=0.9, p_neutral=0.05, p_positive=0.05)
        return JevRejectedError(413, "too large")

    answer: Callable[[str], Any] = respond
    fake = FakeJevClient(answer)
    assert fake.read("a lawsuit").p_negative == 0.9
    with pytest.raises(JevRejectedError):
        fake.read("anything else")
    assert fake.calls == ["a lawsuit", "anything else"]
