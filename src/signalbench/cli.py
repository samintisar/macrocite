from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import httpx
import typer
from sqlmodel import Session, col, select

from signalbench.config import settings
from signalbench.db.models import Ticker, TickerKind
from signalbench.db.session import get_session
from signalbench.ingest.cdr import (
    build_entries,
    diff_universe,
    dump_universe,
    fetch_cboe_directory,
    gics_sector,
    load_universe,
    parse_cboe_directory,
    yfinance_sector,
)
from signalbench.ingest.earnings import (
    ingest_finnhub_calendar,
    sync_sec_earnings_events,
)
from signalbench.ingest.edgar import backfill_filing_text, ingest_eight_ks_for_symbol
from signalbench.ingest.finnhub import FinnhubClient
from signalbench.ingest.liquidity import update_liquidity_flags
from signalbench.ingest.news import ingest_company_news, last_news_date, news_windows
from signalbench.ingest.prices import fetch_yfinance_daily, ingest_daily_prices
from signalbench.ingest.seed import BENCHMARKS, seed_universe
from signalbench.ingest.stats import collect_stats

UNIVERSE_PATH = Path(__file__).resolve().parents[2] / "data" / "cdr_universe.yaml"

app = typer.Typer(help="SignalBench swing assistant.")
ingest_app = typer.Typer(help="Load research data into Postgres.")
universe_app = typer.Typer(help="Manage the checked-in CDR universe.")
app.add_typer(ingest_app, name="ingest")
app.add_typer(universe_app, name="universe")


@universe_app.command("refresh")
def universe_refresh(
    write: Annotated[bool, typer.Option("--write", help="Write data/cdr_universe.yaml.")] = False,
) -> None:
    headers = {"User-Agent": "Mozilla/5.0 (SignalBench universe refresh)"}
    with httpx.Client(timeout=30.0, headers=headers) as client:
        listings = parse_cboe_directory(fetch_cboe_directory(client))
    current = load_universe(UNIVERSE_PATH) if UNIVERSE_PATH.exists() else []
    diff = diff_universe(current, listings)
    typer.echo(f"Cboe lists {len(listings)} US-company CDRs")
    for listing in diff.added:
        typer.echo(f"+ {listing.us_symbol} ({listing.cdr_symbol}) {listing.company_name}")
    for entry in diff.removed:
        typer.echo(f"- {entry.us_symbol} ({entry.cdr_symbol}) {entry.company_name}")
    if write:
        entries = build_entries(
            listings,
            sector_lookup=lambda symbol: gics_sector(yfinance_sector(symbol)),
            existing=current,
        )
        dump_universe(entries, UNIVERSE_PATH)
        typer.echo(f"Wrote {len(entries)} entries to {UNIVERSE_PATH}")
    elif diff.added or diff.removed:
        typer.echo("Run again with --write to update the file.")


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
def prices(
    full: Annotated[
        bool,
        typer.Option("--full", help="Refetch from PRICE_HISTORY_START, not just recent days."),
    ] = False,
) -> None:
    with get_session() as session:
        failed = _ingest_prices(session, full=full)
    _exit_on_failures({"prices": failed})


@ingest_app.command()
def filings(
    backfill_text: Annotated[
        bool,
        typer.Option("--backfill-text", help="Fill text/acceptance/items for older rows first."),
    ] = False,
) -> None:
    with get_session() as session, httpx.Client(timeout=30.0) as client:
        failed = _ingest_filings(session, client, backfill_text=backfill_text)
    _exit_on_failures({"filings": failed})


@ingest_app.command()
def earnings() -> None:
    with get_session() as session:
        _ingest_earnings(session)


@ingest_app.command()
def news() -> None:
    if settings.finnhub_api_key is None:
        typer.echo("FINNHUB_API_KEY is not set in .env", err=True)
        raise typer.Exit(1)
    with get_session() as session, httpx.Client(timeout=30.0) as client:
        failed = _ingest_news(session, FinnhubClient(settings.finnhub_api_key, client))
    _exit_on_failures({"news": failed})


@ingest_app.command("all")
def ingest_all() -> None:
    failures: dict[str, list[str]] = {}
    with get_session() as session, httpx.Client(timeout=30.0) as client:
        failures["prices"] = _ingest_prices(session)
        failures["filings"] = _ingest_filings(session, client, backfill_text=False)
        _ingest_earnings(session)
        if settings.finnhub_api_key is None:
            typer.echo("FINNHUB_API_KEY is not set; skipping news", err=True)
        else:
            failures["news"] = _ingest_news(
                session, FinnhubClient(settings.finnhub_api_key, client)
            )
    _exit_on_failures(failures)


@ingest_app.command()
def stats() -> None:
    with get_session() as session:
        for label, value in collect_stats(session, since=settings.filings_backfill_start):
            typer.echo(f"{label}: {value}")


def _for_each_ticker(
    session: Session,
    command: str,
    tickers: list[Ticker],
    work: Callable[[Ticker], str],
) -> list[str]:
    """Run `work` per ticker, echoing its result; a failure is reported and skipped."""
    failed: list[str] = []
    for index, ticker in enumerate(tickers, start=1):
        symbol = ticker.symbol
        label = f"{command} {index}/{len(tickers)} {symbol}"
        try:
            message = work(ticker)
        except Exception as error:  # noqa: BLE001  # report any per-ticker failure and move on
            session.rollback()
            typer.echo(f"{label} FAILED: {type(error).__name__}: {error}", err=True)
            failed.append(symbol)
            continue
        typer.echo(f"{label} {message}")
    return failed


def _exit_on_failures(failures: dict[str, list[str]]) -> None:
    failed_steps = {command: symbols for command, symbols in failures.items() if symbols}
    for command, symbols in failed_steps.items():
        typer.echo(f"{command}: {len(symbols)} failed: {', '.join(symbols)}", err=True)
    if failed_steps:
        raise typer.Exit(1)


def _ingest_prices(session: Session, full: bool = False) -> list[str]:
    def work(ticker: Ticker) -> str:
        result = ingest_daily_prices(
            session,
            ticker,
            fetch=fetch_yfinance_daily,
            history_start=settings.price_history_start,
            full=full,
        )
        return f"+{result.created} ~{result.updated} x{result.rejected}"

    failed = _for_each_ticker(session, "prices", _universe_tickers(session), work)
    liquidity = update_liquidity_flags(session, load_universe(UNIVERSE_PATH))
    typer.echo(f"liquidity: {len(liquidity.active)} active, {len(liquidity.inactive)} inactive")
    for symbol, reason in sorted(liquidity.inactive.items()):
        typer.echo(f"  inactive {symbol}: {reason}")
    return failed


def _ingest_filings(session: Session, client: httpx.Client, backfill_text: bool) -> list[str]:
    def work(ticker: Ticker) -> str:
        backfilled = ""
        if backfill_text:
            filled = backfill_filing_text(session, ticker.symbol, client, settings.sec_user_agent)
            backfilled = f" (text backfilled {filled})"
        created = ingest_eight_ks_for_symbol(
            session,
            ticker.symbol,
            client,
            settings.sec_user_agent,
            since=settings.filings_backfill_start,
        )
        return f"+{created}{backfilled}"

    tickers = _universe_tickers(session, {TickerKind.us_stock})
    return _for_each_ticker(session, "filings", tickers, work)


def _ingest_earnings(session: Session) -> None:
    typer.echo(f"earnings from SEC 2.02: +{sync_sec_earnings_events(session)}")
    if settings.finnhub_api_key is None:
        typer.echo("FINNHUB_API_KEY is not set; skipping the upcoming earnings calendar", err=True)
        return
    with httpx.Client(timeout=30.0) as client:
        finnhub = FinnhubClient(settings.finnhub_api_key, client)
        tickers = _universe_tickers(session, {TickerKind.us_stock})
        created = ingest_finnhub_calendar(session, finnhub, tickers, datetime.now(UTC).date())
    typer.echo(f"earnings from Finnhub calendar: {created} upcoming")


def _ingest_news(session: Session, finnhub: FinnhubClient) -> list[str]:
    today = datetime.now(UTC).date()

    def work(ticker: Ticker) -> str:
        created = 0
        for start, end in news_windows(last_news_date(session, ticker), today):
            created += ingest_company_news(session, finnhub, ticker, start, end)
        return f"+{created}"

    tickers = _universe_tickers(session, {TickerKind.us_stock})
    return _for_each_ticker(session, "news", tickers, work)


def _universe_tickers(session: Session, kinds: set[TickerKind] | None = None) -> list[Ticker]:
    """Universe members plus benchmarks, active or not (liquidity needs their prices)."""
    entries = load_universe(UNIVERSE_PATH)
    symbols = (
        {entry.us_symbol for entry in entries}
        | {entry.cdr_symbol for entry in entries}
        | {symbol for symbol, _name in BENCHMARKS}
    )
    rows = session.exec(select(Ticker).where(col(Ticker.symbol).in_(symbols))).all()
    return sorted(
        (ticker for ticker in rows if kinds is None or ticker.kind in kinds),
        key=lambda ticker: ticker.symbol,
    )
