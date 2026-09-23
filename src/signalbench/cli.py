from pathlib import Path

import httpx
import typer
from sqlmodel import Session, select

from signalbench.config import settings
from signalbench.db.models import Ticker
from signalbench.db.session import get_session
from signalbench.ingest.cdr import load_universe
from signalbench.ingest.edgar import ingest_eight_ks_for_symbol
from signalbench.ingest.prices import fetch_yfinance_daily, ingest_daily_prices
from signalbench.ingest.seed import seed_universe

UNIVERSE_PATH = Path(__file__).resolve().parents[2] / "data" / "cdr_universe.yaml"

app = typer.Typer()
ingest_app = typer.Typer()
app.add_typer(ingest_app, name="ingest")


@app.command()
def seed() -> None:
    entries = load_universe(UNIVERSE_PATH)
    with get_session() as session:
        result = seed_universe(session, entries)
    typer.echo(
        f"Seeded {result.us_stocks} US stocks, {result.cdrs} CDRs, "
        f"{result.benchmarks} benchmarks; deactivated {len(result.deactivated)}"
    )


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
        tickers = _active_tickers(session)
        for index, ticker in enumerate(tickers, start=1):
            result = ingest_daily_prices(
                session,
                ticker,
                fetch=fetch_yfinance_daily,
                history_start=settings.price_history_start,
            )
            typer.echo(
                f"{index}/{len(tickers)} {ticker.symbol} "
                f"+{result.created} ~{result.updated} x{result.rejected}"
            )


def _active_tickers(session: Session) -> list[Ticker]:
    return list(session.exec(select(Ticker).where(Ticker.active)))
