from signalbench.eval.metrics import direction_accuracy, evaluate, event_type_report
from signalbench.extraction.schema import EventTypeName


def test_direction_accuracy_eight_of_ten() -> None:
    humans = [0.8, -0.6, 0.7, -0.8, -0.4, -0.3, 0.0, 0.9, 0.2, 0.1]
    preds = [0.5, -0.2, 0.1, -0.1, 0.2, -0.4, 0.0, 0.3, -0.1, 0.2]
    # mismatches: index 4 (human -0.4 vs pred +0.2), index 8 (human +0.2 vs pred -0.1)
    assert direction_accuracy(humans, preds) == 0.8


def test_event_type_report_perfect_on_two() -> None:
    y_true = [EventTypeName.earnings, EventTypeName.legal]
    y_pred = [EventTypeName.earnings, EventTypeName.legal]
    report = event_type_report(y_true, y_pred)
    assert report["earnings"]["precision"] == 1.0
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
    assert "earnings" in out.event_type_metrics
    assert out.confidence_calibration is not None
