from pathlib import Path
from typing import Annotated, Literal

import httpx
import typer
from sqlmodel import Session, select

from signalbench.config import settings
from signalbench.db.models import RawDocument, Ticker
from signalbench.db.session import get_session
from signalbench.extraction.extract import LLMClient, extract_document
from signalbench.extraction.schema import ExtractionResult
from signalbench.ingest.edgar import ingest_eight_ks_for_symbol
from signalbench.ingest.prices import fetch_yfinance_daily, ingest_daily_prices
from signalbench.ingest.seed import seed_watchlist as seed_watchlist_from_yaml

WATCHLIST_PATH = Path(__file__).resolve().parents[2] / "data" / "watchlist.yaml"

app = typer.Typer()
ingest_app = typer.Typer()
app.add_typer(ingest_app, name="ingest")


@app.command()
def seed_watchlist() -> None:
    with get_session() as session:
        seed_watchlist_from_yaml(session, WATCHLIST_PATH)


@ingest_app.command()
def filings() -> None:
    with get_session() as session, httpx.Client(timeout=30.0) as client:
        for ticker in _active_tickers(session):
            ingest_eight_ks_for_symbol(
                session,
                ticker.symbol,
                client,
                settings.sec_user_agent,
            )


@ingest_app.command()
def prices() -> None:
    with get_session() as session:
        for ticker in _active_tickers(session):
            ingest_daily_prices(session, ticker, fetch=fetch_yfinance_daily)


@app.command()
def extract(
    llm: Annotated[
        Literal["fake", "together"] | None,
        typer.Option("--llm", help="LLM backend: fake (CI) or together (production)."),
    ] = None,
) -> None:
    client = _llm_client(llm)
    with get_session() as session:
        for document in session.exec(select(RawDocument)).all():
            extract_document(
                session,
                document,
                client,
                settings.model_version,
                settings.prompt_version,
            )


class _FakeLLM:
    def complete(self, prompt: str, raw_text: str) -> ExtractionResult:
        return ExtractionResult(claims=[])


def _llm_client(llm: Literal["fake", "together"] | None) -> LLMClient:
    if llm == "fake":
        return _FakeLLM()
    if llm == "together" or settings.together_api_key:
        from signalbench.extraction.together_llm import TogetherLLM

        return TogetherLLM(
            model_version=settings.model_version,
            api_key=settings.together_api_key,
        )
    return _FakeLLM()


def _active_tickers(session: Session) -> list[Ticker]:
    return list(session.exec(select(Ticker).where(Ticker.active)))
