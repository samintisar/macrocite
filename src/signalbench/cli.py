import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

import httpx
import typer
from sqlmodel import Session, col, select

from signalbench.backtest.preregistration import (
    RunRefusedError,
    check_config_path,
    check_cost_matches_survey,
)
from signalbench.backtest.provenance import committed_unchanged, git_sha
from signalbench.backtest.report import render_report
from signalbench.backtest.runner import (
    JevMode,
    RequiresSpec03Error,
    RunSetup,
    run_backtest,
    setups_for_run,
)
from signalbench.config import settings
from signalbench.db.models import BacktestRun, Ticker, TickerKind
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
    ingest_finnhub_calendar_for_ticker,
    sync_sec_earnings_events,
)
from signalbench.ingest.edgar import backfill_filing_text, ingest_eight_ks_for_symbol
from signalbench.ingest.finnhub import FinnhubClient
from signalbench.ingest.liquidity import update_liquidity_flags
from signalbench.ingest.news import ingest_company_news, last_news_date, news_windows
from signalbench.ingest.prices import fetch_yfinance_daily, ingest_daily_prices
from signalbench.ingest.seed import BENCHMARKS, seed_universe
from signalbench.ingest.stats import collect_stats
from signalbench.market.calendar import NyseSessions
from signalbench.strategy.config import load_strategy_config
from signalbench.strategy.spread import SpreadSurveyError, load_spread_survey

REPO_ROOT = Path(__file__).resolve().parents[2]
UNIVERSE_PATH = REPO_ROOT / "data" / "cdr_universe.yaml"
STRATEGY_V1_PATH = REPO_ROOT / "data" / "strategy_v1.yaml"
SPREAD_SURVEY_PATH = REPO_ROOT / "data" / "cdr_spread_survey.yaml"
REPORTS_DIR = REPO_ROOT / "reports" / "backtests"
NEW_YORK = ZoneInfo("America/New_York")

app = typer.Typer(help="SignalBench swing assistant.")
ingest_app = typer.Typer(help="Load research data into Postgres.")
universe_app = typer.Typer(help="Manage the checked-in CDR universe.")
app.add_typer(ingest_app, name="ingest")
app.add_typer(universe_app, name="universe")

backtest_app = typer.Typer(help="Pre-registered strategy backtests (spec 02).")
app.add_typer(backtest_app, name="backtest")


class SetupChoice(str, Enum):
    pullback = "pullback"
    breakout = "breakout"
    sentiment = "sentiment"
    combined = "combined"


class JevChoice(str, Enum):
    off = "off"
    filter = "filter"


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
    with get_session() as session, httpx.Client(timeout=30.0) as client:
        failed = _ingest_earnings(session, _finnhub(client))
    _exit_on_failures({"earnings": failed})


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
        finnhub = _finnhub(client)
        failures["earnings"] = _ingest_earnings(session, finnhub)
        if finnhub is None:
            typer.echo("FINNHUB_API_KEY is not set; skipping news", err=True)
        else:
            failures["news"] = _ingest_news(session, finnhub)
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
    try:
        liquidity = update_liquidity_flags(session, load_universe(UNIVERSE_PATH))
    except Exception as error:  # noqa: BLE001  # keep flags as they were; later steps still run
        session.rollback()
        typer.echo(f"liquidity FAILED: {type(error).__name__}: {error}", err=True)
        return [*failed, "liquidity"]
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


def _ingest_earnings(session: Session, finnhub: FinnhubClient | None) -> list[str]:
    typer.echo(f"earnings from SEC 2.02: +{sync_sec_earnings_events(session)}")
    if finnhub is None:
        typer.echo("FINNHUB_API_KEY is not set; skipping the upcoming earnings calendar", err=True)
        return []
    today = datetime.now(UTC).date()

    def work(ticker: Ticker) -> str:
        return f"+{ingest_finnhub_calendar_for_ticker(session, finnhub, ticker, today)}"

    tickers = _universe_tickers(session, {TickerKind.us_stock})
    return _for_each_ticker(session, "earnings", tickers, work)


def _finnhub(client: httpx.Client) -> FinnhubClient | None:
    if settings.finnhub_api_key is None:
        return None
    return FinnhubClient(settings.finnhub_api_key, client)


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


@backtest_app.command("run")
def backtest_run(
    setup: Annotated[SetupChoice, typer.Option("--setup", help="Which setup to simulate.")],
    jev: Annotated[JevChoice, typer.Option("--jev", help="Jev filter mode.")] = JevChoice.off,
    config: Annotated[
        Path, typer.Option("--config", help="Pre-registered strategy parameters.")
    ] = STRATEGY_V1_PATH,
) -> None:
    run_setup: RunSetup = setup.value
    jev_mode: JevMode = jev.value
    try:
        setups_for_run(run_setup, jev_mode)
    except RequiresSpec03Error as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(2) from None
    if not config.exists():
        typer.echo(
            f"{config} not found. It is written after the spread survey is approved "
            "(spec 02 pre-registration).",
            err=True,
        )
        raise typer.Exit(1)
    if not committed_unchanged(REPO_ROOT, config):
        typer.echo(
            f"{config.name} must be committed, unchanged, before a backtest on real data "
            "(spec 02 pre-registration).",
            err=True,
        )
        raise typer.Exit(1)
    if not SPREAD_SURVEY_PATH.exists() or not committed_unchanged(REPO_ROOT, SPREAD_SURVEY_PATH):
        typer.echo(
            f"{SPREAD_SURVEY_PATH.name} must be committed, unchanged, before a backtest on real "
            "data: it sets cost_per_side (spec 02 pre-registration).",
            err=True,
        )
        raise typer.Exit(1)
    strategy, sha = load_strategy_config(config)
    try:
        now = datetime.now(NEW_YORK)
        check_config_path(REPO_ROOT, config, strategy.version)
        check_cost_matches_survey(
            strategy.cost_per_side, load_spread_survey(SPREAD_SURVEY_PATH).cost_per_side
        )
        with get_session() as session:
            run, path = run_backtest(
                session,
                setup=run_setup,
                jev_mode=jev_mode,
                config=strategy,
                config_sha256=sha,
                universe=load_universe(UNIVERSE_PATH),
                calendar=NyseSessions(),
                git_sha=git_sha(REPO_ROOT),
                run_date=now.date(),
                reports_dir=REPORTS_DIR,
                now=now,
            )
            _print_run(run)
    except (RunRefusedError, SpreadSurveyError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None
    typer.echo(f"report: {path}")


def _print_run(run: BacktestRun) -> None:
    typer.echo(f"run {run.id}: {'PASS' if run.passed else 'FAIL'}")
    for name, row in run.pass_bar.items():
        mark = "ok" if row["passed"] else "--"
        typer.echo(f"  {mark} {name}: {row['value']:.3f} vs {row['threshold']:.3f}")


@backtest_app.command("show")
def backtest_show(
    run_id: Annotated[str, typer.Argument(help="Run id printed by `backtest run`.")],
) -> None:
    try:
        key = uuid.UUID(run_id)
    except ValueError:
        typer.echo(f"Not a run id: {run_id}", err=True)
        raise typer.Exit(1) from None
    with get_session() as session:
        run = session.get(BacktestRun, key)
        if run is None:
            typer.echo(f"No backtest run {run_id}", err=True)
            raise typer.Exit(1)
        typer.echo(render_report(run), nl=False)


@backtest_app.command("cost")
def backtest_cost(
    survey: Annotated[
        Path, typer.Option("--survey", help="The CDR bid/ask survey.")
    ] = SPREAD_SURVEY_PATH,
) -> None:
    try:
        result = load_spread_survey(survey)
    except (SpreadSurveyError, FileNotFoundError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None
    for reading in result.readings:
        typer.echo(
            f"{reading.cdr_symbol}: bid {reading.bid} ask {reading.ask} "
            f"spread {reading.spread_pct:.3%}"
        )
    typer.echo(f"complete readings: {len(result.readings)} (incomplete: {result.incomplete})")
    typer.echo(f"median spread: {result.median_spread:.3%}")
    typer.echo(f"cost_per_side: {result.cost_per_side}")
