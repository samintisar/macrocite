from pathlib import Path

import pytest
from pydantic import ValidationError

from signalbench.eval.labels import Label, LabelSet, load_labels

REPO = Path(__file__).resolve().parents[1]


def test_repo_labels_validate() -> None:
    loaded = load_labels(REPO / "evals" / "labels.json")
    assert len(loaded.labels) >= 10
    assert all(item.raw_text for item in loaded.labels)
    assert "document_id" not in Label.model_fields


def test_rejects_missing_text() -> None:
    with pytest.raises(ValidationError):
        LabelSet.model_validate({"labels": [{"id": "x", "raw_text": "", "human_sentiment": 0.1, "human_event_type": "earnings"}]})
