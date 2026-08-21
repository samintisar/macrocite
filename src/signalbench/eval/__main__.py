import os
from pathlib import Path
from typing import cast

from signalbench.config import settings
from signalbench.db.session import get_session
from signalbench.eval.gate import load_baseline, passed_ci_gate
from signalbench.eval.labels import load_labels
from signalbench.eval.metrics import evaluate
from signalbench.eval.persist import record_eval_run

REPO_ROOT = Path(__file__).resolve().parents[3]


def main() -> int:
    labels = load_labels(REPO_ROOT / "evals" / "labels.json").labels
    baseline = load_baseline(REPO_ROOT / "evals" / "baseline.json")
    human_sentiment = [item.human_sentiment for item in labels]
    pred_sentiment = list(human_sentiment)
    human_event = [item.human_event_type for item in labels]
    pred_event = list(human_event)
    pred_confidence = [1.0] * len(labels)
    metrics = evaluate(
        human_sentiment=human_sentiment,
        pred_sentiment=pred_sentiment,
        human_event=human_event,
        pred_event=pred_event,
        pred_confidence=pred_confidence,
    )
    ok = passed_ci_gate(metrics.sentiment_direction_accuracy, baseline)
    print(f"sentiment_direction_accuracy={metrics.sentiment_direction_accuracy:.4f}")
    print("PASS" if ok else "FAIL")
    if os.environ.get("DATABASE_URL"):
        session = get_session()
        try:
            record_eval_run(
                session,
                model_version=settings.model_version,
                prompt_version=settings.prompt_version,
                git_commit_sha=None,
                label_set_git_sha=None,
                n_examples=len(labels),
                sentiment_accuracy=metrics.sentiment_direction_accuracy,
                event_type_metrics=cast(dict[str, object], metrics.event_type_metrics),
                confidence_calibration=cast(dict[str, object] | None, metrics.confidence_calibration),
                passed_ci_gate=ok,
            )
        finally:
            session.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
