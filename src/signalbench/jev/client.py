"""Jev through OpenRouter's decisions endpoint (spec 03): response parsing and the HTTP client."""

import math
from dataclasses import dataclass
from typing import Any, cast

from signalbench.jev.questions import EVENT_TYPES, IMPACT_OPTIONS

PRICE_PER_INPUT_TOKEN_USD = 0.042 / 1_000_000  # output tokens are free (checked 2026-09-24)
PROBABILITY_TOLERANCE = 0.01


class JevError(Exception):
    """Base class for every Jev failure."""


class JevResponseError(JevError):
    """A 200 response that does not match question set q1. The document is skipped."""


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
    if not isinstance(model, str) or not model or not isinstance(response_id, str):
        raise JevResponseError("response: expected string `model` and `id`")
    usage = _mapping(top.get("usage"), "usage")
    tokens = usage.get("input_tokens")
    if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
        raise JevResponseError(f"usage.input_tokens must be a whole number, got {tokens!r}")
    cost = usage.get("cost")
    if isinstance(cost, bool) or not isinstance(cost, int | float) or cost < 0:
        cost = tokens * PRICE_PER_INPUT_TOKEN_USD  # not reported: price it ourselves
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
