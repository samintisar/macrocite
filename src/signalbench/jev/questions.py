"""Question set q1 and the request body (spec 03).

Changing any wording creates q2, and documents must be re-read under q2 before any q2 reading
is used. Only public text is sent: SEC filings and news headlines and summaries.
"""

from datetime import date
from typing import Any, Literal

MODEL = "typesafe/jev-1.13"
JEV_RELEASE = date(2026, 9, 15)  # trades and documents from here on are out of sample
QUESTION_SET = "q1"
MAX_STATE_CHARS = 100_000  # Jev takes 32K tokens of state; spec 01 caps 8-K text at 96K chars
IMPACT_OPTIONS = ("negative", "neutral", "positive")
EVENT_TYPES = ("earnings", "guidance", "leadership", "legal", "product", "macro", "other")
Source = Literal["filings", "news"]

QUESTIONS: dict[str, dict[str, Any]] = {
    "impact": {
        "type": "choice",
        "instructions": "What does this document mean for the company's common shareholders?",
        "criteria": {
            "negative": (
                "Clearly bad news: weak results or lowered guidance, lawsuits or investigations "
                "with real exposure, forced executive departures, impairments, restatements, "
                "liquidity problems, loss of a major customer or product."
            ),
            "neutral": (
                "Routine, procedural, or mixed news with no clear effect on the business: "
                "meeting and voting results, routine appointments, ordinary debt issuance, "
                "exhibit-only filings, commentary without new facts."
            ),
            "positive": (
                "Clearly good news: strong results or raised guidance, major contracts or "
                "approvals, new or larger buybacks or dividends, favourable legal outcomes, "
                "value-adding deals."
            ),
        },
    },
    "event_type": {
        "type": "choice",
        "instructions": "What kind of event is this document mainly about?",
        "criteria": {
            "earnings": "Reported financial results.",
            "guidance": "Forecasts or outlook changes.",
            "leadership": "Executive or board changes.",
            "legal": "Lawsuits, investigations, regulatory actions, settlements.",
            "product": "Products, contracts, customers, partnerships, approvals.",
            "macro": "Economy-wide or industry-wide conditions.",
            "other": "Anything else, including administrative filings.",
        },
    },
    "routine": {
        "type": "noul",
        "instructions": (
            "Is this a routine administrative document with no new business information?"
        ),
        "criteria": {
            "true": (
                "Procedural or boilerplate: meeting results, standard exhibits, scheduled notices."
            ),
            "false": "Contains new information about the business, its results, or its prospects.",
        },
    },
}


def build_state(
    company_name: str, symbol: str, source: Source, items: str | None, text: str
) -> str:
    """`Company: {name} ({symbol})`, `Source: SEC 8-K, items {items}` or `Source: News`, then
    the cleaned text. No dates are added (dates are a documented Jev weak spot)."""
    if source == "filings":
        listed = ", ".join(part.strip() for part in (items or "").split(",") if part.strip())
        label = f"SEC 8-K, items {listed}" if listed else "SEC 8-K"
    else:
        label = "News"
    return f"Company: {company_name} ({symbol})\nSource: {label}\n\n{text.strip()}"


def request_body(state: str, model: str = MODEL) -> dict[str, Any]:
    """The documented body: model, state, questions. No session_id or user is sent."""
    return {"model": model, "state": state, "questions": QUESTIONS}
