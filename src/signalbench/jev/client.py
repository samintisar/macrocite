"""Jev through OpenRouter's decisions endpoint (spec 03): response parsing and the HTTP client."""

import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast

import httpx

from signalbench.jev.questions import EVENT_TYPES, IMPACT_OPTIONS, MODEL, request_body

ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
TIMEOUT_SECONDS = 10.0
MAX_RETRIES = 3
BACKOFF_SECONDS = 1.0  # waits 1, 2, 4 s between attempts
FATAL_STATUSES = frozenset({401, 402, 404})  # bad key, no credits, unknown model: stop the run
PRICE_PER_INPUT_TOKEN_USD = 0.042 / 1_000_000  # output tokens are free (checked 2026-09-24)
PROBABILITY_TOLERANCE = 0.01


class JevError(Exception):
    """Base class for every Jev failure."""


class JevResponseError(JevError):
    """A 200 response that does not match question set q1. The document is skipped."""


class JevRejectedError(JevError):
    """A non-retryable status about this request (400, 403, 413, ...). The document is skipped."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class JevFatalError(JevError):
    """401 (key), 402 (credits), or 404 (model): every later request would fail too."""


class JevUnavailableError(JevError):
    """429, 5xx, or a transport error on every attempt. The document is skipped."""


@dataclass(frozen=True)
class JevResult:
    response_id: str
    model_resolved: str
    p_negative: float
    p_neutral: float
    p_positive: float
    event_type: str
    p_routine: float
    answers: dict[str, Any]
    input_tokens: int
    cost_usd: float
    latency_ms: int


def _mapping(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise JevResponseError(f"{where}: expected a JSON object")
    return cast(dict[str, Any], value)


def _unit(value: object, where: str) -> float:
    """A probability: a finite number in [0, 1] (bools are not numbers here)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise JevResponseError(f"{where} must be a number in [0, 1], got {value!r}")
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise JevResponseError(f"{where} must be a number in [0, 1], got {value!r}")
    return number


def _answer(answers: dict[str, Any], name: str, kind: str) -> dict[str, Any]:
    if name not in answers:
        raise JevResponseError(f"missing answer {name!r}")
    answer = _mapping(answers[name], name)
    if answer.get("type") != kind:
        raise JevResponseError(f"{name}: expected type {kind!r}, got {answer.get('type')!r}")
    return answer


def _choice(
    answers: dict[str, Any], name: str, options: tuple[str, ...]
) -> tuple[str, dict[str, float]]:
    answer = _answer(answers, name, "choice")
    raw = _mapping(answer.get("probabilities"), f"{name}: probabilities")
    if set(raw) != set(options):
        raise JevResponseError(
            f"{name}: probabilities must cover {list(options)}, got {sorted(raw)}"
        )
    probabilities = {option: _unit(raw[option], f"{name}: {option}") for option in options}
    total = math.fsum(probabilities.values())
    if abs(total - 1.0) > PROBABILITY_TOLERANCE:
        raise JevResponseError(
            f"{name} probabilities sum to {total:.4f}, not 1 ± {PROBABILITY_TOLERANCE}"
        )
    choice = answer.get("choice")
    if choice not in options:
        raise JevResponseError(f"{name}: choice {choice!r} is not one of {list(options)}")
    return str(choice), probabilities


def parse_response(body: object, latency_ms: int) -> JevResult:
    """Validate a 200 body against question set q1. `confidence` is never used (spec 03)."""
    top = _mapping(body, "response")
    answers = _mapping(top.get("answers"), "answers")
    _, impact = _choice(answers, "impact", IMPACT_OPTIONS)
    event_type, _ = _choice(answers, "event_type", EVENT_TYPES)
    routine = _unit(_answer(answers, "routine", "noul").get("noul"), "routine: noul")
    model = top.get("model")
    response_id = top.get("id")
    if not isinstance(model, str) or not model or not isinstance(response_id, str) or not response_id:
        raise JevResponseError("response: expected string `model` and `id`")
    usage = _mapping(top.get("usage"), "usage")
    tokens = usage.get("input_tokens")
    if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
        raise JevResponseError(f"usage.input_tokens must be a whole number, got {tokens!r}")
    cost = usage.get("cost")
    if (
        isinstance(cost, bool)
        or not isinstance(cost, int | float)
        or cost < 0
        or not math.isfinite(cost)
    ):
        cost = tokens * PRICE_PER_INPUT_TOKEN_USD  # not reported: price it ourselves
    try:
        json.dumps(answers, allow_nan=False)
    except ValueError:
        raise JevResponseError("answers: non-finite number") from None
    return JevResult(
        response_id=response_id,
        model_resolved=model,
        p_negative=impact["negative"],
        p_neutral=impact["neutral"],
        p_positive=impact["positive"],
        event_type=event_type,
        p_routine=routine,
        answers=answers,
        input_tokens=tokens,
        cost_usd=float(cost),
        latency_ms=latency_ms,
    )


class JevClient(Protocol):
    def read(self, state: str) -> JevResult:
        """Ask question set q1 about one document state."""
        ...


def _error_message(response: httpx.Response) -> str:
    """OpenRouter errors look like {"error": {"code": 402, "message": "..."}}."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:200] or response.reason_phrase
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return str(body["error"].get("message", body["error"]))
    return str(body)[:200]


class OpenRouterJevClient:
    """Sync client. Retries 429, 5xx, and transport errors with exponential backoff; never retries
    other 4xx. Safe to share across the backfill's worker threads (httpx.Client is thread-safe)."""

    def __init__(
        self,
        api_key: str,
        http: httpx.Client,
        *,
        model: str = MODEL,
        max_retries: int = MAX_RETRIES,
        backoff_seconds: float = BACKOFF_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self._http = http
        self._model = model
        self._max_retries = max_retries
        self._backoff = backoff_seconds
        self._sleep = sleep
        self._clock = clock

    def read(self, state: str) -> JevResult:
        body = request_body(state, self._model)
        failure = ""
        for attempt in range(self._max_retries + 1):
            if attempt:
                self._sleep(self._backoff * 2 ** (attempt - 1))
            started = self._clock()
            try:
                response = self._http.post(
                    ENDPOINT, json=body, headers=self._headers, timeout=TIMEOUT_SECONDS
                )
            except httpx.RequestError as error:  # timeouts, connection failures, undecodable bodies
                failure = f"{type(error).__name__}: {error}"
                continue
            latency_ms = round((self._clock() - started) * 1000)
            status = response.status_code
            if status == 200:
                try:
                    payload = response.json()
                except ValueError as error:
                    raise JevResponseError(f"200 with a body that is not JSON: {error}") from None
                return parse_response(payload, latency_ms)
            if status in FATAL_STATUSES:
                raise JevFatalError(f"HTTP {status}: {_error_message(response)}")
            if status != 429 and status < 500:
                raise JevRejectedError(status, _error_message(response))
            failure = f"HTTP {status}"
        raise JevUnavailableError(f"gave up after {self._max_retries + 1} attempts: {failure}")
