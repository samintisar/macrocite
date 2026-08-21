from collections.abc import Sequence
from dataclasses import dataclass

from signalbench.extraction.schema import EventTypeName


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def direction_accuracy(human: Sequence[float], pred: Sequence[float]) -> float:
    if len(human) != len(pred) or not human:
        raise ValueError("human and pred must be same non-empty length")
    matches = sum(_sign(h) == _sign(p) for h, p in zip(human, pred, strict=True))
    return matches / len(human)


def event_type_report(
    y_true: Sequence[EventTypeName],
    y_pred: Sequence[EventTypeName],
) -> dict[str, dict[str, float]]:
    labels = [e.value for e in EventTypeName]
    report: dict[str, dict[str, float]] = {}
    for label in labels:
        tp = sum(
            1
            for t, p in zip(y_true, y_pred, strict=True)
            if t.value == label and p.value == label
        )
        fp = sum(
            1
            for t, p in zip(y_true, y_pred, strict=True)
            if t.value != label and p.value == label
        )
        fn = sum(
            1
            for t, p in zip(y_true, y_pred, strict=True)
            if t.value == label and p.value != label
        )
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        report[label] = {"precision": precision, "recall": recall, "f1": f1, "support": tp + fn}
    return report


@dataclass
class EvalMetrics:
    sentiment_direction_accuracy: float
    event_type_metrics: dict[str, dict[str, float]]
    confidence_calibration: dict[str, float] | None


def evaluate(
    *,
    human_sentiment: Sequence[float],
    pred_sentiment: Sequence[float],
    human_event: Sequence[EventTypeName],
    pred_event: Sequence[EventTypeName],
    pred_confidence: Sequence[float],
    correct_direction: Sequence[bool] | None = None,
) -> EvalMetrics:
    acc = direction_accuracy(human_sentiment, pred_sentiment)
    report = event_type_report(human_event, pred_event)
    if correct_direction is None:
        correct_direction = [
            _sign(h) == _sign(p) for h, p in zip(human_sentiment, pred_sentiment, strict=True)
        ]
    buckets: dict[str, list[bool]] = {"0.0-0.5": [], "0.5-0.9": [], "0.9-1.0": []}
    for conf, ok in zip(pred_confidence, correct_direction, strict=True):
        if conf >= 0.9:
            buckets["0.9-1.0"].append(ok)
        elif conf >= 0.5:
            buckets["0.5-0.9"].append(ok)
        else:
            buckets["0.0-0.5"].append(ok)
    calibration = {
        name: (sum(vals) / len(vals) if vals else 0.0) for name, vals in buckets.items()
    }
    return EvalMetrics(acc, report, calibration)
