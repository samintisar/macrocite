import uuid
from typing import Protocol, cast

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from signalbench.db.models import EventType, RawDocument, Signal, Ticker
from signalbench.extraction.prompt import load_prompt
from signalbench.extraction.schema import ExtractionResult


class LLMClient(Protocol):
    def complete(self, prompt: str, raw_text: str) -> ExtractionResult: ...


def extract_document(
    session: Session,
    document: RawDocument,
    llm: LLMClient,
    model_version: str,
    prompt_version: str,
) -> int:
    prompt = load_prompt(prompt_version)
    result = llm.complete(prompt, document.raw_text)
    payload = cast(dict[str, object], result.model_dump(mode="json"))

    created = 0
    for claim in result.claims:
        ticker = _resolve_ticker(session, claim.ticker)
        if _signal_exists(session, document.id, ticker.id, model_version, prompt_version):
            continue
        session.add(
            Signal(
                document_id=document.id,
                ticker_id=ticker.id,
                model_version=model_version,
                prompt_version=prompt_version,
                sentiment=claim.sentiment,
                event_type=EventType(claim.event_type.value),
                confidence=claim.confidence,
                rationale=claim.rationale,
                raw_llm_response=payload,
            )
        )
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            continue
        created += 1

    return created


def _resolve_ticker(session: Session, symbol: str) -> Ticker:
    ticker = session.exec(select(Ticker).where(Ticker.symbol == symbol)).first()
    if ticker is not None:
        return ticker
    ticker = Ticker(symbol=symbol, company_name=symbol, active=False)
    session.add(ticker)
    session.flush()
    return ticker


def _signal_exists(
    session: Session,
    document_id: uuid.UUID,
    ticker_id: uuid.UUID,
    model_version: str,
    prompt_version: str,
) -> bool:
    existing = session.exec(
        select(Signal).where(
            Signal.document_id == document_id,
            Signal.ticker_id == ticker_id,
            Signal.model_version == model_version,
            Signal.prompt_version == prompt_version,
        )
    ).first()
    return existing is not None
