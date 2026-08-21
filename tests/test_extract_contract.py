import pytest
from pydantic import ValidationError

from signalbench.extraction.schema import ExtractionResult


def test_rejects_sentiment_out_of_range() -> None:
    with pytest.raises(ValidationError):
        ExtractionResult.model_validate(
            {
                "claims": [
                    {
                        "ticker": "AAPL",
                        "sentiment": 2.0,
                        "event_type": "earnings",
                        "confidence": 0.5,
                        "rationale": "nope",
                    }
                ]
            }
        )


def test_rejects_unknown_event_type() -> None:
    with pytest.raises(ValidationError):
        ExtractionResult.model_validate(
            {
                "claims": [
                    {
                        "ticker": "AAPL",
                        "sentiment": 0.1,
                        "event_type": "merger",
                        "confidence": 0.5,
                        "rationale": "nope",
                    }
                ]
            }
        )


def test_accepts_two_claims() -> None:
    result = ExtractionResult.model_validate(
        {
            "claims": [
                {
                    "ticker": "AAPL",
                    "sentiment": 0.6,
                    "event_type": "earnings",
                    "confidence": 0.9,
                    "rationale": "Beat EPS.",
                },
                {
                    "ticker": "MSFT",
                    "sentiment": -0.2,
                    "event_type": "legal",
                    "confidence": 0.4,
                    "rationale": "Mentioned in passing.",
                },
            ]
        }
    )
    assert [c.ticker for c in result.claims] == ["AAPL", "MSFT"]
