from pathlib import Path

from pydantic import BaseModel


class Baseline(BaseModel):
    sentiment_direction_accuracy: float
    max_drop: float = 0.05


def load_baseline(path: Path) -> Baseline:
    return Baseline.model_validate_json(path.read_text(encoding="utf-8"))


def passed_ci_gate(accuracy: float, baseline: Baseline) -> bool:
    return accuracy >= baseline.sentiment_direction_accuracy - baseline.max_drop
