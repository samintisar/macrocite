from sqlmodel import Session, select

from signalbench.db.models import EvalRun
from signalbench.eval.persist import record_eval_run


def test_failed_run_still_inserts(session: Session) -> None:
    record_eval_run(
        session,
        model_version="deepseek-ai/DeepSeek-V4-Flash-0731",
        prompt_version="v1",
        git_commit_sha="abc",
        label_set_git_sha="def",
        n_examples=10,
        sentiment_accuracy=0.50,
        event_type_metrics={"earnings": {"f1": 0.0}},
        confidence_calibration={"0.9-1.0": 0.0},
        passed_ci_gate=False,
    )
    row = session.exec(select(EvalRun)).one()
    assert row.passed_ci_gate is False
    assert row.sentiment_accuracy == 0.50
    assert row.label_set_git_sha == "def"
