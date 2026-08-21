from signalbench.eval.gate import Baseline, passed_ci_gate


def test_fails_when_drop_exceeds_five_points() -> None:
    baseline = Baseline(sentiment_direction_accuracy=0.80, max_drop=0.05)
    assert passed_ci_gate(0.75, baseline) is True
    assert passed_ci_gate(0.74, baseline) is False


def test_passes_at_or_above_baseline() -> None:
    baseline = Baseline(sentiment_direction_accuracy=0.80, max_drop=0.05)
    assert passed_ci_gate(0.80, baseline) is True
    assert passed_ci_gate(0.90, baseline) is True
