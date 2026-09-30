import logging
import subprocess  # schtasks, for the next scheduled scan in /status
import sys
import uuid
from collections.abc import Callable
from contextlib import nullcontext
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

import httpx
import typer
import yaml
from sqlmodel import Session, col, select

from signalbench.backtest.preregistration import (
    check_config_path,
    check_cost_matches_survey,
)
from signalbench.backtest.provenance import committed_unchanged, git_sha
from signalbench.backtest.report import render_report, result_label
from signalbench.backtest.runner import (
    JevInputs,
    JevMode,
    RunSetup,
    UnsupportedRunError,
    last_complete_session,
    run_backtest,
    setups_for_run,
)
from signalbench.config import settings
from signalbench.db.models import BacktestRun, Ticker, TickerKind
from signalbench.db.session import engine, get_session
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
from signalbench.ingest.prices import (
    fetch_yfinance_daily,
    fetch_yfinance_splits,
    ingest_daily_prices,
)
from signalbench.ingest.seed import BENCHMARKS, seed_universe
from signalbench.ingest.stats import collect_stats
from signalbench.jev.backfill import (
    NEWS_DAILY_CAP,
    BackfillSource,
    pending_work,
    run_backfill,
)
from signalbench.jev.calibration import (
    BENCHMARK_SYMBOL,
    calibration_sections,
    render_calibration_report,
)
from signalbench.jev.client import JevError, OpenRouterJevClient
from signalbench.jev.filter import decide_filter, score_trades
from signalbench.jev.filter_record import (
    FilterFileError,
    FilterInputError,
    FilterRecord,
    load_filter_setting,
    load_v1_trades,
    render_filter_report,
    write_filter_file,
)
from signalbench.jev.fixture import fixture_state
from signalbench.jev.store import (
    calibration_samples,
    load_document_readings,
    reading_builds,
    resolved_builds,
)
from signalbench.live.book import LedgerError, SplitKind
from signalbench.live.bot import BotBrain
from signalbench.live.heartbeat import write_heartbeat
from signalbench.live.ledger import Ledger
from signalbench.live.messenger import ConsoleMessenger, TelegramMessenger, redact
from signalbench.live.scan import LIVE_SCAN_LOCK, ScanOutcome, dry_run_session, run_scan
from signalbench.live.start import LIVE_CONFIG, start_live
from signalbench.live.status import scan_stale_message, scan_status_lines
from signalbench.live.tax import tax_csv, tax_text
from signalbench.live.telegram_bot import build_application, redact_logs
from signalbench.market.calendar import HISTORY_START, CboeCanadaSessions, NyseSessions
from signalbench.market.legal_close import LegalCloses
from signalbench.paper.lock import advisory_lock
from signalbench.paper.run import run_paper
from signalbench.paper.start import start_portfolios
from signalbench.paper.status import stale_message, status_lines
from signalbench.strategy.config import ConfigError, load_strategy_config
from signalbench.strategy.readings import JevReadingsView
from signalbench.strategy.spread import SpreadSurveyError, load_spread_survey

REPO_ROOT = Path(__file__).resolve().parents[2]
UNIVERSE_PATH = REPO_ROOT / "data" / "cdr_universe.yaml"
STRATEGY_V1_PATH = REPO_ROOT / "data" / "strategy_v1.yaml"
SPREAD_SURVEY_PATH = REPO_ROOT / "data" / "cdr_spread_survey.yaml"
JEV_FILTER_PATH = REPO_ROOT / "data" / "jev_filter_v1.yaml"
REPORTS_DIR = REPO_ROOT / "reports" / "backtests"
JEV_REPORTS_DIR = REPO_ROOT / "reports" / "jev"
PAPER_V1_PATH = REPO_ROOT / "data" / "paper_v1.yaml"
PAPER_REPORTS_DIR = REPO_ROOT / "reports" / "paper"
LIVE_CONFIG_PATH = REPO_ROOT / LIVE_CONFIG
STALE_EXIT = 3  # `paper status` and `scan status --stale-after-days`: no ok run for too long
SCAN_TASK = "SignalBench live scan"  # the Task Scheduler task (scripts/windows/register-tasks.ps1)
JEV_CONCURRENCY = 4
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


jev_app = typer.Typer(help="Jev reads filings and news (spec 03).")
app.add_typer(jev_app, name="jev")

paper_app = typer.Typer(help="Forward paper trading of the pre-registered portfolios (spec 07).")
app.add_typer(paper_app, name="paper")


class SourceChoice(str, Enum):
    filings = "filings"
    news = "news"
    all = "all"


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


def _ingest_earnings(
    session: Session, finnhub: FinnhubClient | None, since: date | None = None
) -> list[str]:
    typer.echo(f"earnings from SEC 2.02: +{sync_sec_earnings_events(session)}")
    if finnhub is None:
        typer.echo("FINNHUB_API_KEY is not set; skipping the upcoming earnings calendar", err=True)
        return []
    today = datetime.now(UTC).date()

    def work(ticker: Ticker) -> str:
        return f"+{ingest_finnhub_calendar_for_ticker(session, finnhub, ticker, today, since)}"

    tickers = _universe_tickers(session, {TickerKind.us_stock})
    return _for_each_ticker(session, "earnings", tickers, work)


def _ingest_paper_earnings(session: Session, since: date) -> list[str]:
    """`ingest earnings` for `paper run`: the Finnhub calendar from `since` (the oldest last
    session of a portfolio that is behind) on, skipped with a warning when FINNHUB_API_KEY is
    not set."""
    with httpx.Client(timeout=30.0) as client:
        return _ingest_earnings(session, _finnhub(client), since)


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
    except UnsupportedRunError as error:
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
    try:
        strategy, sha = load_strategy_config(config)
    except (ConfigError, yaml.YAMLError) as error:
        message = " ".join(f"{config.name}: {error}".split())  # YAML errors span lines
        typer.echo(message, err=True)
        raise typer.Exit(1) from None
    jev_inputs: JevInputs | None = None
    if jev_mode == "filter":
        jev_inputs = _jev_filter_inputs(strategy.version, sha)
    elif run_setup == "sentiment":
        jev_inputs = JevInputs(theta_block=None)
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
                jev=jev_inputs,
            )
            _print_run(run)
    except ValueError as error:  # RunRefusedError, SpreadSurveyError, and data/calendar gaps
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None
    typer.echo(f"report: {path}")


def _jev_filter_inputs(version: str, config_sha256: str) -> JevInputs:
    """The committed filter decision; --jev filter runs only when it is ON (spec 03), and only
    against the v1 config it was fitted on."""
    if not JEV_FILTER_PATH.exists():
        typer.echo(
            f"{JEV_FILTER_PATH.name} not found. Run `signalbench jev fit-filter --write` and "
            "commit its output first (spec 03).",
            err=True,
        )
        raise typer.Exit(1)
    if not committed_unchanged(REPO_ROOT, JEV_FILTER_PATH):
        typer.echo(
            f"{JEV_FILTER_PATH.name} must be committed, unchanged, before a --jev filter run "
            "(spec 03).",
            err=True,
        )
        raise typer.Exit(1)
    try:
        setting = load_filter_setting(JEV_FILTER_PATH)
    except (FilterFileError, yaml.YAMLError) as error:
        typer.echo(" ".join(str(error).split()), err=True)
        raise typer.Exit(1) from None
    if setting.mode != "on":
        typer.echo(
            "The Jev filter is information-only (data/jev_filter_v1.yaml), so --jev filter runs "
            "are refused (spec 03).",
            err=True,
        )
        raise typer.Exit(1)
    if version != "v1" or config_sha256 != setting.strategy_config_sha256:
        typer.echo(
            f"{JEV_FILTER_PATH.name} was fitted on a different data/strategy_v1.yaml "
            f"(config_sha256 {setting.strategy_config_sha256}) than the running config "
            f"(version {version!r}, config_sha256 {config_sha256}). Refit the filter against "
            "the current strategy_v1.yaml, or run against the config it was fitted on (spec 03).",
            err=True,
        )
        raise typer.Exit(1)
    return JevInputs(
        theta_block=setting.theta_block,
        model_requested=setting.model_requested,
        question_set=setting.question_set,
    )


def _print_run(run: BacktestRun) -> None:
    result = result_label(run)
    if run.jev_mode == "filter":
        result += " (information only: the Jev-off v1 result stands)"
    typer.echo(f"run {run.id}: {result}")
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


def _openrouter_key() -> str:
    if settings.openrouter_api_key is None:
        typer.echo("OPENROUTER_API_KEY is not set in .env", err=True)
        raise typer.Exit(1)
    return settings.openrouter_api_key


def _universe_symbols() -> list[str]:
    return [entry.us_symbol for entry in load_universe(UNIVERSE_PATH)]


def _now() -> datetime:
    return datetime.now(NEW_YORK)


@jev_app.command("test")
def jev_test() -> None:
    """One real call on a small made-up 8-K: prints the answers, latency, cost, and build."""
    key = _openrouter_key()
    with httpx.Client() as http:
        try:
            result = OpenRouterJevClient(key, http).read(fixture_state())
        except JevError as error:
            typer.echo(f"Jev call failed: {error}", err=True)
            raise typer.Exit(1) from None
    typer.echo(f"model: {result.model_resolved}")
    typer.echo(
        f"impact: negative {result.p_negative:.3f} | neutral {result.p_neutral:.3f} "
        f"| positive {result.p_positive:.3f}"
    )
    typer.echo(f"event_type: {result.event_type} | routine {result.p_routine:.3f}")
    typer.echo(
        f"latency {result.latency_ms} ms | input tokens {result.input_tokens} "
        f"| cost ${result.cost_usd:.6f}"
    )


@jev_app.command("backfill")
def jev_backfill(
    source: Annotated[
        SourceChoice, typer.Option("--source", help="Which documents to read.")
    ] = SourceChoice.all,
    since: Annotated[
        datetime | None,
        typer.Option("--since", formats=["%Y-%m-%d"], help="Only documents published from this day."),
    ] = None,
    max_cost_usd: Annotated[
        float, typer.Option("--max-cost-usd", min=0.0, help="Stop once this much is spent.")
    ] = 10.0,
) -> None:
    """Read every unread 8-K and news item once per universe ticker. Safe to rerun."""
    key = _openrouter_key()
    backfill_source: BackfillSource = source.value
    with get_session() as session, httpx.Client() as http:
        work = pending_work(
            session, _universe_symbols(), backfill_source, None if since is None else since.date()
        )
        typer.echo(
            f"to read: {len(work.jobs)} (already read {work.already_read}, "
            f"no text {work.no_text}, too long {work.too_long})"
        )
        if work.capped:
            typer.echo(
                f"news cap: {work.capped} items over {NEWS_DAILY_CAP} per symbol per New York "
                f"day not read ({work.capped_days} symbol-days)"
            )
        summary = run_backfill(
            session,
            work.jobs,
            OpenRouterJevClient(key, http),
            max_cost_usd=max_cost_usd,
            concurrency=JEV_CONCURRENCY,
            echo=typer.echo,
        )
    typer.echo(
        f"read {summary.read} | cost ${summary.cost_usd:.4f} | input tokens {summary.input_tokens}"
    )
    for build, count in sorted(summary.builds.items()):
        typer.echo(f"model {build}: {count}")
    if summary.skipped:
        typer.echo(f"skipped {len(summary.skipped)} documents (listed above)")
    if summary.budget_reached:
        typer.echo(f"{summary.stopped}; {summary.not_started} not started. Run again to continue.")
    elif summary.stopped is not None:
        typer.echo(f"stopped: {summary.stopped}; {summary.not_started} not started.", err=True)
        raise typer.Exit(1)


def _r(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


@jev_app.command("fit-filter")
def jev_fit_filter(
    write: Annotated[
        bool, typer.Option("--write", help="Write data/jev_filter_v1.yaml and the report.")
    ] = False,
) -> None:
    """The pre-registered filter decision (spec 03, steps 1-3). Writes nothing without --write."""
    calendar = NyseSessions()
    with get_session() as session:
        try:
            trades, runs = load_v1_trades(session)
        except FilterInputError as error:
            typer.echo(str(error), err=True)
            raise typer.Exit(1) from None
        last = max(run.end_date for run in runs)
        symbols = sorted(set(_universe_symbols()) | {trade.symbol for trade in trades})
        documents = load_document_readings(
            session, symbols, LegalCloses(calendar.session_closes(HISTORY_START, last))
        )
        builds = resolved_builds(documents)
    readings = sum(len(rows) for rows in documents.values())
    if readings == 0:
        typer.echo("No Jev readings. Run `signalbench jev backfill` first (spec 03).", err=True)
        raise typer.Exit(1)
    view = JevReadingsView(documents, calendar.sessions_between(HISTORY_START, last), None)
    decision = decide_filter(score_trades(trades, view.max_p_negative))
    typer.echo(f"decision: {decision.mode}, theta {decision.theta}")
    typer.echo(f"why: {decision.reason}")
    for row in decision.fit:
        typer.echo(
            f"fit theta {row.theta}: blocked {row.blocked}, kept {row.kept}, "
            f"kept - blocked {_r(row.difference)}"
        )
    check = decision.confirm
    if check is not None:
        typer.echo(
            f"confirm theta {check.theta}: blocked {check.blocked}, kept {check.kept}, "
            f"mean R blocked {_r(check.mean_r_blocked)}, kept {_r(check.mean_r_kept)}"
        )
    if not write:
        typer.echo("Nothing written. Run with --write to record the decision.")
        return
    if JEV_FILTER_PATH.exists():  # refuse before the report is written, so nothing is replaced
        typer.echo(
            f"{JEV_FILTER_PATH.name} already exists. The filter decision is made once; a new "
            "decision needs a new question set (spec 03).",
            err=True,
        )
        raise typer.Exit(1)
    today = datetime.now(NEW_YORK).date()
    report = JEV_REPORTS_DIR / f"{today.isoformat()}-filter-decision.md"
    record = FilterRecord(
        decision=decision,
        source_runs={run.setup: run.run_id for run in runs},
        strategy_config_sha256=runs[0].config_sha256,
        confirm_end=last,
        readings=readings,
        builds=builds,
        decided_on=today,
        report_path=report.relative_to(REPO_ROOT).as_posix(),
    )
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_filter_report(record), encoding="utf-8")
    try:
        write_filter_file(JEV_FILTER_PATH, record)
    except FilterFileError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None
    typer.echo(f"wrote {JEV_FILTER_PATH}")
    typer.echo(f"report: {report}")


@jev_app.command("calibration")
def jev_calibration() -> None:
    """Write reports/jev/<date>-calibration.md (information only; nothing is adjusted)."""
    calendar = NyseSessions()
    now = _now()
    today = now.date()
    last = last_complete_session(calendar, now)
    with get_session() as session:
        samples, unlabeled = calibration_samples(
            session,
            _universe_symbols(),
            BENCHMARK_SYMBOL,
            LegalCloses(calendar.session_closes(HISTORY_START, last)),
            calendar.sessions_between(HISTORY_START, last),
        )
        builds = reading_builds(session)
    if not samples and not unlabeled:
        typer.echo("No Jev readings. Run `signalbench jev backfill` first (spec 03).", err=True)
        raise typer.Exit(1)
    sections = calibration_sections(samples)
    path = JEV_REPORTS_DIR / f"{today.isoformat()}-calibration.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_calibration_report(
            sections,
            readings=len(samples) + unlabeled,
            unlabeled=unlabeled,
            builds=builds,
            written_on=today,
        ),
        encoding="utf-8",
    )
    typer.echo(f"labeled {len(samples)} | not labeled {unlabeled}")
    for section in sections:
        typer.echo(
            f"{section.name}: {section.samples} documents | ECE positive "
            f"{_r(section.positive_ece)} | ECE negative {_r(section.negative_ece)} | "
            f"accuracy {_r(section.accuracy)} vs baseline {_r(section.majority_rate)}"
        )
    typer.echo(f"report: {path}")


def _first_line(error: BaseException) -> str:
    lines = str(error).strip().splitlines()
    return lines[0] if lines else ""


@paper_app.command("start")
def paper_start() -> None:
    """Create the portfolios in data/paper_v1.yaml; they start on the next NYSE session."""
    try:
        with get_session() as session:
            created = start_portfolios(
                session, paper_file=PAPER_V1_PATH, repo=REPO_ROOT, calendar=NyseSessions(), now=_now()
            )
            lines = [
                f"{p.name}: starts {p.started_on.isoformat()} | {p.config_path} "
                f"| config_sha256 {p.config_sha256[:12]}"
                for p in created
            ]
    except (ValueError, yaml.YAMLError) as error:  # PaperRefusedError, PaperFileError, ConfigError
        typer.echo(" ".join(str(error).split()), err=True)
        raise typer.Exit(1) from None
    for line in lines:
        typer.echo(line)


@paper_app.command("run")
def paper_run() -> None:
    """The nightly job: ingest prices, step every portfolio to the last complete session, and
    write the weekly report on the first run of an ISO week. Safe to rerun."""
    try:
        with get_session() as session:
            outcome = run_paper(
                session,
                lock=advisory_lock(engine),
                repo=REPO_ROOT,
                universe=load_universe(UNIVERSE_PATH),
                calendar=NyseSessions(),
                clock=_now,
                ingest=_ingest_prices,
                ingest_earnings=_ingest_paper_earnings,
                splits=fetch_yfinance_splits,
                reports_dir=PAPER_REPORTS_DIR,
                echo=typer.echo,
            )
    except Exception as error:  # noqa: BLE001  # e.g. the database is down: no run row to mark
        typer.echo(f"ERROR: {type(error).__name__}: {_first_line(error)}", err=True)
        raise typer.Exit(1) from None
    if outcome.status == "failed":
        typer.echo(f"ERROR: {outcome.error}", err=True)
        raise typer.Exit(1)
    if outcome.status == "ok" and outcome.target is not None:
        stepped = sum(outcome.stepped.values())
        typer.echo(f"paper run {outcome.run_id}: ok, {stepped} sessions stepped to {outcome.target}")


@paper_app.command("status")
def paper_status(
    stale_after_days: Annotated[
        int | None,
        typer.Option(
            "--stale-after-days",
            min=1,
            help="Exit 3 when no paper run has succeeded for more than this many days.",
        ),
    ] = None,
) -> None:
    """One line per portfolio: last session, equity, return, positions, and judging."""
    now = _now()
    try:
        with get_session() as session:
            lines = status_lines(session, now.date())
            stale = None if stale_after_days is None else stale_message(session, now, stale_after_days)
    except Exception as error:  # noqa: BLE001  # one line for the nightly log, not a traceback
        typer.echo(f"ERROR: {type(error).__name__}: {_first_line(error)}", err=True)
        raise typer.Exit(1) from None
    for line in lines:
        typer.echo(line)
    if stale is not None:
        typer.echo(stale, err=True)
        raise typer.Exit(STALE_EXIT)


live_app = typer.Typer(help="The one live strategy, v2-none-cash (spec 04).")
app.add_typer(live_app, name="live")
ledger_app = typer.Typer(help="The live ledger: the tax report and split corrections (spec 04).")
app.add_typer(ledger_app, name="ledger")


def _ledger(session: Session) -> Ledger:
    return Ledger(session, calendar=NyseSessions(), today=_now().date())


@live_app.command("start")
def live_start() -> None:
    """Freeze data/strategy_v2-none-cash.yaml as the live config. Runs once."""
    try:
        with get_session() as session:
            row = start_live(
                session, repo=REPO_ROOT, config_path=LIVE_CONFIG_PATH,
                survey_path=SPREAD_SURVEY_PATH, today=_now().date(),
            )
            line = (
                f"live config: {row.config_path} | config_sha256 {row.config_sha256[:12]} | "
                f"started {row.started_on} | code {row.start_git_sha[:12]}"
            )
    except (ValueError, yaml.YAMLError) as error:  # LiveRefusedError, RunRefusedError, ConfigError
        typer.echo(" ".join(str(error).split()), err=True)
        raise typer.Exit(1) from None
    typer.echo(line)


@ledger_app.command("tax")
def ledger_tax(
    year: Annotated[int, typer.Argument(help="The tax year, e.g. 2026.")],
    csv_path: Annotated[
        Path | None, typer.Option("--csv", help="Also write every disposition to this CSV.")
    ] = None,
) -> None:
    """The year's ACB report: dispositions, superficial losses, and CDR splits."""
    with get_session() as session:
        report = _ledger(session).tax_report(year)
    for line in tax_text(report):
        typer.echo(line)
    if csv_path is not None:
        csv_path.write_text(tax_csv(report), encoding="utf-8")
        typer.echo(f"csv: {csv_path}")


@ledger_app.command("split")
def ledger_split(
    symbol: Annotated[str, typer.Argument(help="A CDR (ZNVD) or a US symbol (NVDA).")],
    ratio: Annotated[str, typer.Argument(help="New units per old unit: 2 for a 2-for-1.")],
    ex_date: Annotated[str, typer.Argument(help="The ex-date, YYYY-MM-DD.")],
) -> None:
    """Record a split yfinance missed (source owner)."""
    try:
        new_per_old, day = Decimal(ratio), date.fromisoformat(ex_date)
    except (InvalidOperation, ValueError):
        typer.echo("RATIO must be a number and EX_DATE a YYYY-MM-DD date.", err=True)
        raise typer.Exit(2) from None
    try:
        with get_session() as session:
            cdr = session.exec(
                select(Ticker).where(Ticker.symbol == symbol, Ticker.kind == TickerKind.cdr)
            ).first()
            kind: SplitKind = "us_split" if cdr is None else "cdr_split"
            action = _ledger(session).record_split(
                kind=kind, symbol=symbol, ex_date=day, ratio=new_per_old, source="owner"
            )
            line = f"corporate action {action.id}: {symbol} {kind} {ratio}-for-1, ex-date {day}"
    except LedgerError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None
    typer.echo(line)


@ledger_app.command("void-action")
def ledger_void_action(
    action_id: Annotated[int, typer.Argument(help="The corporate action's id.")],
    reason: Annotated[str, typer.Argument(help="Why it is wrong.")],
) -> None:
    """Void a wrong split; its stop rows are undone by new split rows."""
    try:
        with get_session() as session:
            _ledger(session).void_corporate_action(action_id, reason)
    except LedgerError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None
    typer.echo(f"corporate action {action_id} voided: {reason}")


scan_app = typer.Typer(help="The evening scan of the live strategy (spec 05).")
app.add_typer(scan_app, name="scan")
bot_app = typer.Typer(help="The Telegram bot (spec 05).")
app.add_typer(bot_app, name="bot")


def _telegram() -> tuple[str, int]:
    """The bot token and the owner's chat id from the environment (.env). Never printed."""
    token, chat = settings.telegram_bot_token, settings.telegram_chat_id
    if not token or chat is None:
        typer.echo(
            "ERROR: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set (the main checkout's .env)",
            err=True,
        )
        raise typer.Exit(1)
    return token, chat


def _next_scan_run() -> str | None:
    """The next run time Task Scheduler shows for the scan task, or None (not Windows, or the
    task is not registered)."""
    if sys.platform != "win32":
        return None
    try:
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", SCAN_TASK, "/FO", "LIST"],
            capture_output=True, text=True, check=False, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in result.stdout.splitlines() if result.returncode == 0 else []:
        key, _, value = line.partition(":")
        if key.strip() == "Next Run Time":
            return f"{value.strip()} (local time)"
    return None


def _print_scan(outcome: ScanOutcome) -> None:
    counts = ", ".join(f"{key} {value}" for key, value in sorted(outcome.counts.items()))
    typer.echo(f"scan {outcome.run_id}: {outcome.status} for {outcome.as_of} ({counts or 'none'})")


@scan_app.callback(invoke_without_command=True)
def scan(
    ctx: typer.Context,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print every message; write and send nothing.")
    ] = False,
    as_of: Annotated[
        datetime | None,
        typer.Option("--as-of", formats=["%Y-%m-%d"], help="With --dry-run: the session to scan."),
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Rescan a session that was already scanned.")
    ] = False,
    allow_any_commit: Annotated[
        bool,
        typer.Option(
            "--allow-any-commit",
            help="Run code other than the live start commit or a live-v* tag (development).",
        ),
    ] = False,
) -> None:
    """The evening scan: ingest, catch up missed sessions, decide, size, and send (spec 05)."""
    if ctx.invoked_subcommand is not None:
        return
    if as_of is not None and not dry_run:
        typer.echo("--as-of needs --dry-run", err=True)
        raise typer.Exit(2)
    if dry_run:
        _dry_run(as_of, allow_any_commit)
        return
    token, chat = _telegram()
    messenger = TelegramMessenger(token, chat)
    try:
        with get_session() as session:
            outcome = run_scan(
                session, lock=advisory_lock(engine, LIVE_SCAN_LOCK), repo=REPO_ROOT,
                universe=load_universe(UNIVERSE_PATH), nyse=NyseSessions(),
                cboe=CboeCanadaSessions(), clock=_now, ingest=_ingest_prices,
                ingest_earnings=_ingest_paper_earnings, splits=fetch_yfinance_splits,
                messenger=messenger, echo=typer.echo, force=force,
                allow_any_commit=allow_any_commit,
            )
    except Exception as error:  # noqa: BLE001  # e.g. the database is down: no run row to mark
        typer.echo(f"ERROR: {type(error).__name__}: {redact(_first_line(error), token)}", err=True)
        raise typer.Exit(1) from None
    finally:
        messenger.close()
    if outcome.status == "failed":
        raise typer.Exit(1)  # run_scan printed the ERROR line
    if outcome.status != "locked":
        _print_scan(outcome)


def _dry_run(as_of: datetime | None, allow_any_commit: bool) -> None:
    """The scan as it would run at 18:00 New York on the session, inside a transaction that is
    rolled back: stored prices, calendar, and splits only (nothing is fetched), every message
    printed, nothing written or sent."""
    nyse = NyseSessions()
    day = last_complete_session(nyse, _now()) if as_of is None else as_of.date()
    try:
        with dry_run_session(engine) as session:
            outcome = run_scan(
                session, lock=nullcontext(True), repo=REPO_ROOT,
                universe=load_universe(UNIVERSE_PATH), nyse=nyse, cboe=CboeCanadaSessions(),
                clock=lambda: datetime.combine(day, time(18, 0), tzinfo=NEW_YORK),
                ingest=lambda _session: [], ingest_earnings=lambda _session, _since: [],
                splits=lambda _symbol, _since: [], messenger=ConsoleMessenger(typer.echo),
                echo=typer.echo, as_of=day, force=True, allow_any_commit=allow_any_commit,
            )
    except Exception as error:  # noqa: BLE001  # one line, not a traceback
        typer.echo(f"ERROR: {type(error).__name__}: {_first_line(error)}", err=True)
        raise typer.Exit(1) from None
    _print_scan(outcome)
    typer.echo("(dry run: nothing was written or sent)")
    if outcome.status == "failed":
        raise typer.Exit(1)


@scan_app.command("status")
def scan_status(
    stale_after_days: Annotated[
        int | None,
        typer.Option(
            "--stale-after-days", min=0,
            help="Exit 3 when no scan has succeeded for more than this many days (0: always, "
            "to check the script's toast).",
        ),
    ] = None,
) -> None:
    """The last scan, the next one, the live config and code, and the pause state."""
    try:
        with get_session() as session:
            lines = scan_status_lines(session, _next_scan_run)
            stale = (
                None if stale_after_days is None
                else scan_stale_message(session, _now(), stale_after_days)
            )
    except Exception as error:  # noqa: BLE001  # one line for the scan log, not a traceback
        typer.echo(f"ERROR: {type(error).__name__}: {_first_line(error)}", err=True)
        raise typer.Exit(1) from None
    for line in lines:
        typer.echo(line)
    if stale is not None:
        typer.echo(stale, err=True)
        raise typer.Exit(STALE_EXIT)


@bot_app.command("run")
def bot_run() -> None:
    """Poll Telegram for the owner's commands and button presses until stopped."""
    token, chat = _telegram()
    log_format = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    logging.basicConfig(level=logging.INFO, format=log_format)
    redact_logs(token, log_format)  # the token never reaches the log or the toast
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its request lines show the token

    def beat() -> None:
        with get_session() as session:
            write_heartbeat(session, datetime.now(UTC))

    brain = BotBrain(chat_id=chat, sessions=get_session, calendar=NyseSessions(), clock=_now,
                     next_run=_next_scan_run)
    typer.echo("bot: polling Telegram; only TELEGRAM_CHAT_ID is answered")
    try:
        build_application(token, brain, beat).run_polling(
            allowed_updates=["message", "callback_query"]
        )
    except Exception as error:  # noqa: BLE001  # one redacted line for the log and the toast
        typer.echo(f"ERROR: {type(error).__name__}: {redact(_first_line(error), token)}", err=True)
        raise typer.Exit(1) from None
