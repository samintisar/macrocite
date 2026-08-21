from collections.abc import Sequence

from signalbench.eval.labels import Label
from signalbench.extraction.extract import LLMClient
from signalbench.extraction.schema import EventTypeName, ExtractionResult


def claim_prediction(result: ExtractionResult) -> tuple[float, EventTypeName, float]:
    if not result.claims:
        return 0.0, EventTypeName.other, 0.0
    claim = result.claims[0]
    return claim.sentiment, claim.event_type, claim.confidence


def predict_labels(
    labels: Sequence[Label],
    llm: LLMClient,
    prompt: str,
) -> tuple[list[float], list[EventTypeName], list[float]]:
    sentiments: list[float] = []
    events: list[EventTypeName] = []
    confidences: list[float] = []
    for item in labels:
        sentiment, event, confidence = claim_prediction(llm.complete(prompt, item.raw_text))
        sentiments.append(sentiment)
        events.append(event)
        confidences.append(confidence)
    return sentiments, events, confidences
