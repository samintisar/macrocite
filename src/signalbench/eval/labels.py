from pathlib import Path

from pydantic import BaseModel, Field

from signalbench.extraction.schema import EventTypeName


class Label(BaseModel):
    id: str
    raw_text: str = Field(min_length=1)
    human_sentiment: float = Field(ge=-1.0, le=1.0)
    human_event_type: EventTypeName
    notes: str | None = None


class LabelSet(BaseModel):
    labels: list[Label]


def load_labels(path: Path) -> LabelSet:
    return LabelSet.model_validate_json(path.read_text(encoding="utf-8"))
