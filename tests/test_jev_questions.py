import json
from datetime import date

from signalbench.jev.questions import (
    EVENT_TYPES,
    IMPACT_OPTIONS,
    JEV_RELEASE,
    MAX_STATE_CHARS,
    MODEL,
    QUESTION_SET,
    QUESTIONS,
    build_state,
    request_body,
)


def test_model_and_question_set_ids() -> None:
    assert MODEL == "typesafe/jev-1.13"
    assert QUESTION_SET == "q1"
    assert MAX_STATE_CHARS == 100_000
    assert JEV_RELEASE == date(2026, 9, 15)


def test_question_set_q1_matches_the_spec() -> None:
    assert list(QUESTIONS) == ["impact", "event_type", "routine"]
    impact, event_type, routine = QUESTIONS["impact"], QUESTIONS["event_type"], QUESTIONS["routine"]
    assert impact["type"] == "choice"
    assert impact["instructions"] == (
        "What does this document mean for the company's common shareholders?"
    )
    assert tuple(impact["criteria"]) == IMPACT_OPTIONS == ("negative", "neutral", "positive")
    assert impact["criteria"]["neutral"].startswith("Routine, procedural, or mixed news")
    assert event_type["type"] == "choice"
    assert tuple(event_type["criteria"]) == EVENT_TYPES
    assert EVENT_TYPES == ("earnings", "guidance", "leadership", "legal", "product", "macro", "other")
    assert routine["type"] == "noul"
    assert set(routine["criteria"]) == {"true", "false"}
    assert routine["instructions"] == (
        "Is this a routine administrative document with no new business information?"
    )


def test_filing_state_names_the_company_and_the_items() -> None:
    state = build_state("Apple", "AAPL", "filings", "2.02,9.01", "  Apple reported results.\n")
    assert state == "Company: Apple (AAPL)\nSource: SEC 8-K, items 2.02, 9.01\n\nApple reported results."


def test_filing_without_items_and_news_state() -> None:
    assert build_state("Apple", "AAPL", "filings", None, "Text").startswith(
        "Company: Apple (AAPL)\nSource: SEC 8-K\n\n"
    )
    assert build_state("Apple", "AAPL", "news", None, "Headline\n\nSummary") == (
        "Company: Apple (AAPL)\nSource: News\n\nHeadline\n\nSummary"
    )


def test_request_body_has_exactly_the_documented_fields() -> None:
    body = request_body("Company: Apple (AAPL)\nSource: News\n\nHeadline")
    assert set(body) == {"model", "state", "questions"}
    assert body["model"] == MODEL
    assert body["state"].startswith("Company: Apple")
    assert body["questions"] == QUESTIONS
    for question in body["questions"].values():
        assert set(question) == {"type", "instructions", "criteria"}
        assert question["type"] in {"choice", "noul", "score"}
    assert json.loads(json.dumps(body)) == body


def test_request_body_questions_are_a_copy_not_a_reference() -> None:
    body = request_body("state")
    assert body["questions"] == QUESTIONS
    assert body["questions"] is not QUESTIONS
    body["questions"]["impact"]["instructions"] = "mutated"
    assert QUESTIONS["impact"]["instructions"] != "mutated"
