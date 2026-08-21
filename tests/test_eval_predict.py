from signalbench.eval.labels import Label
from signalbench.eval.predict import claim_prediction, predict_labels
from signalbench.extraction.schema import (
    EventTypeName,
    ExtractedClaim,
    ExtractionResult,
)


class _ScriptedLLM:
    def __init__(self, result: ExtractionResult) -> None:
        self.result = result
        self.prompts: list[str] = []
        self.texts: list[str] = []

    def complete(self, prompt: str, raw_text: str) -> ExtractionResult:
        self.prompts.append(prompt)
        self.texts.append(raw_text)
        return self.result


def test_claim_prediction_uses_first_claim() -> None:
    result = ExtractionResult(
        claims=[
            ExtractedClaim(
                ticker="AAPL",
                sentiment=-0.5,
                event_type=EventTypeName.legal,
                confidence=0.8,
                rationale="suit",
            ),
            ExtractedClaim(
                ticker="MSFT",
                sentiment=0.9,
                event_type=EventTypeName.earnings,
                confidence=0.99,
                rationale="beat",
            ),
        ]
    )
    sentiment, event, confidence = claim_prediction(result)
    assert sentiment == -0.5
    assert event is EventTypeName.legal
    assert confidence == 0.8


def test_claim_prediction_empty_is_neutral_other() -> None:
    sentiment, event, confidence = claim_prediction(ExtractionResult(claims=[]))
    assert sentiment == 0.0
    assert event is EventTypeName.other
    assert confidence == 0.0


def test_predict_labels_calls_complete_with_each_raw_text() -> None:
    labels = [
        Label(
            id="a",
            raw_text="Company beats EPS estimates.",
            human_sentiment=0.8,
            human_event_type=EventTypeName.earnings,
        ),
        Label(
            id="b",
            raw_text="CEO resigns effective immediately.",
            human_sentiment=-0.6,
            human_event_type=EventTypeName.leadership,
        ),
    ]
    llm = _ScriptedLLM(
        ExtractionResult(
            claims=[
                ExtractedClaim(
                    ticker="X",
                    sentiment=0.1,
                    event_type=EventTypeName.macro,
                    confidence=0.7,
                    rationale="scripted",
                )
            ]
        )
    )
    sentiments, events, confidences = predict_labels(labels, llm, prompt="PROMPT")
    assert llm.prompts == ["PROMPT", "PROMPT"]
    assert llm.texts == [labels[0].raw_text, labels[1].raw_text]
    assert sentiments == [0.1, 0.1]
    assert events == [EventTypeName.macro, EventTypeName.macro]
    assert confidences == [0.7, 0.7]
