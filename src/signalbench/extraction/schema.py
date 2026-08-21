from enum import Enum

from pydantic import BaseModel, Field


class EventTypeName(str, Enum):
    earnings = "earnings"
    guidance = "guidance"
    leadership = "leadership"
    legal = "legal"
    product = "product"
    macro = "macro"
    other = "other"


class ExtractedClaim(BaseModel):
    ticker: str = Field(min_length=1)
    sentiment: float = Field(ge=-1.0, le=1.0)
    event_type: EventTypeName
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)


class ExtractionResult(BaseModel):
    claims: list[ExtractedClaim]
