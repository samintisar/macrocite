from sqlmodel import Session

from signalbench.db.models import EvalRun


def record_eval_run(
    session: Session,
    *,
    model_version: str,
    prompt_version: str,
    git_commit_sha: str | None,
    label_set_git_sha: str | None,
    n_examples: int,
    sentiment_accuracy: float,
    event_type_metrics: dict[str, object],
    confidence_calibration: dict[str, object] | None,
    passed_ci_gate: bool,
) -> EvalRun:
    row = EvalRun(
        model_version=model_version,
        prompt_version=prompt_version,
        git_commit_sha=git_commit_sha,
        label_set_git_sha=label_set_git_sha,
        n_examples=n_examples,
        sentiment_accuracy=sentiment_accuracy,
        event_type_metrics=event_type_metrics,
        confidence_calibration=confidence_calibration,
        passed_ci_gate=passed_ci_gate,
    )
    session.add(row)
    session.commit()
    return row
