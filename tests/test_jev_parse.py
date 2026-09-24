import copy
from typing import Any

import pytest
from pytest import approx

from signalbench.jev.client import (
    PRICE_PER_INPUT_TOKEN_USD,
    JevResponseError,
    parse_response,
)


def _body() -> dict[str, Any]:
    """A response in the documented shape for question set q1."""
    return {
        "answers": {
            "impact": {
                "type": "choice",
                "choice": "positive",
                "confidence": 0.7,
                "probabilities": {"negative": 0.05, "neutral": 0.15, "positive": 0.8},
            },
            "event_type": {
                "type": "choice",
                "choice": "earnings",
                "confidence": 0.9,
                "probabilities": {
                    "earnings": 0.9, "guidance": 0.05, "leadership": 0, "legal": 0,
                    "product": 0.05, "macro": 0, "other": 0,
                },
            },
            "routine": {"type": "noul", "noul": 0.04},
        },
        "id": "gen-dec-1789738314-X5e5eKGQdvR9rblyX250",
        "model": "typesafe/jev-1.13-20260917",
        "provider": "TypeSafe",
        "usage": {"cost": 0.000019992, "input_tokens": 476, "output_tokens": 70},
    }


def test_choice_and_noul_answers_are_parsed() -> None:
    result = parse_response(_body(), latency_ms=812)
    assert (result.p_negative, result.p_neutral, result.p_positive) == (0.05, 0.15, 0.8)
    assert result.event_type == "earnings"
    assert result.p_routine == 0.04
    assert result.model_resolved == "typesafe/jev-1.13-20260917"
    assert result.response_id == "gen-dec-1789738314-X5e5eKGQdvR9rblyX250"
    assert (result.input_tokens, result.cost_usd, result.latency_ms) == (476, 0.000019992, 812)
    assert result.answers == _body()["answers"]  # stored in full


def test_integer_probabilities_are_accepted() -> None:
    body = _body()
    body["answers"]["impact"]["probabilities"] = {"negative": 0, "neutral": 0, "positive": 1}
    assert parse_response(body, latency_ms=1).p_positive == 1.0


@pytest.mark.parametrize("total", [0.98, 1.02])
def test_probabilities_that_do_not_sum_to_one_are_rejected(total: float) -> None:
    body = _body()
    body["answers"]["impact"]["probabilities"] = {
        "negative": 0.1, "neutral": 0.1, "positive": total - 0.2,
    }
    with pytest.raises(JevResponseError, match="impact probabilities sum to"):
        parse_response(body, latency_ms=1)


def test_a_sum_inside_the_tolerance_is_accepted() -> None:
    body = _body()
    body["answers"]["impact"]["probabilities"] = {"negative": 0.1, "neutral": 0.1, "positive": 0.805}
    assert parse_response(body, latency_ms=1).p_positive == approx(0.805)


def test_event_type_probabilities_are_checked_too() -> None:
    body = _body()
    body["answers"]["event_type"]["probabilities"]["other"] = 0.5
    with pytest.raises(JevResponseError, match="event_type probabilities sum to"):
        parse_response(body, latency_ms=1)


@pytest.mark.parametrize("missing", ["impact", "event_type", "routine"])
def test_every_answer_must_be_present(missing: str) -> None:
    body = _body()
    del body["answers"][missing]
    with pytest.raises(JevResponseError, match=f"missing answer {missing!r}"):
        parse_response(body, latency_ms=1)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("routine", "noul"), 1.2, "routine: noul must be a number in"),
        (("routine", "noul"), True, "routine: noul must be a number in"),
        (("routine", "type"), "choice", "routine: expected type 'noul'"),
        (("impact", "type"), "noul", "impact: expected type 'choice'"),
        (("event_type", "choice"), "weather", "event_type: choice 'weather' is not"),
        (("impact", "probabilities"), {"bad": 0.5, "good": 0.5}, "impact: probabilities must cover"),
    ],
)
def test_malformed_answers_are_rejected(path: tuple[str, str], value: object, message: str) -> None:
    body = _body()
    body["answers"][path[0]][path[1]] = value
    with pytest.raises(JevResponseError, match=message):
        parse_response(body, latency_ms=1)


def test_missing_cost_falls_back_to_the_token_price() -> None:
    body = _body()
    del body["usage"]["cost"]
    result = parse_response(body, latency_ms=1)
    assert result.cost_usd == approx(476 * PRICE_PER_INPUT_TOKEN_USD)
    assert PRICE_PER_INPUT_TOKEN_USD == approx(0.042 / 1_000_000)


@pytest.mark.parametrize("key", ["model", "usage", "answers", "id"])
def test_missing_top_level_fields_are_rejected(key: str) -> None:
    body = copy.deepcopy(_body())
    del body[key]
    with pytest.raises(JevResponseError):
        parse_response(body, latency_ms=1)


def test_a_non_object_body_is_rejected() -> None:
    with pytest.raises(JevResponseError, match="expected a JSON object"):
        parse_response(["not", "an", "object"], latency_ms=1)
