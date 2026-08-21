import pytest

from signalbench.eval.metrics import direction_accuracy, evaluate, event_type_report
from signalbench.extraction.schema import EventTypeName


def test_direction_accuracy_eight_of_ten() -> None:
    humans = [0.8, -0.6, 0.7, -0.8, -0.4, -0.3, 0.0, 0.9, 0.2, 0.1]
    preds = [0.5, -0.2, 0.1, -0.1, 0.2, -0.4, 0.0, 0.3, -0.1, 0.2]
    # mismatches: index 4 (human -0.4 vs pred +0.2), index 8 (human +0.2 vs pred -0.1)
    assert direction_accuracy(humans, preds) == 0.8


def test_direction_accuracy_zero_is_own_class() -> None:
    assert direction_accuracy([0.0], [0.1]) == 0.0
    assert direction_accuracy([0.1], [0.0]) == 0.0


def test_event_type_report_perfect_on_two() -> None:
    y_true = [EventTypeName.earnings, EventTypeName.legal]
    y_pred = [EventTypeName.earnings, EventTypeName.legal]
    report = event_type_report(y_true, y_pred)
    assert report["earnings"]["precision"] == 1.0
    assert report["earnings"]["recall"] == 1.0
    assert report["legal"]["precision"] == 1.0
    assert report["legal"]["recall"] == 1.0


def test_evaluate_returns_accuracy_and_report() -> None:
    humans_s = [1.0, -1.0]
    preds_s = [0.5, -0.2]
    humans_e = [EventTypeName.earnings, EventTypeName.legal]
    preds_e = [EventTypeName.earnings, EventTypeName.other]
    out = evaluate(
        human_sentiment=humans_s,
        pred_sentiment=preds_s,
        human_event=humans_e,
        pred_event=preds_e,
        pred_confidence=[0.9, 0.9],
        correct_direction=[True, True],
    )
    assert out.sentiment_direction_accuracy == 1.0
    assert out.event_type_metrics["legal"]["recall"] == 0.0
    assert out.event_type_metrics["other"]["precision"] == 0.0
    assert out.confidence_calibration is not None
    assert out.confidence_calibration["0.9-1.0"] == 1.0


def test_evaluate_raises_on_mismatched_lengths() -> None:
    with pytest.raises(ValueError, match="same non-empty length"):
        evaluate(
            human_sentiment=[1.0, -1.0],
            pred_sentiment=[0.5, -0.2],
            human_event=[EventTypeName.earnings],
            pred_event=[EventTypeName.earnings],
            pred_confidence=[0.9, 0.9],
        )
