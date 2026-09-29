import uuid
from collections.abc import Callable
from datetime import UTC, datetime
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
from signalbench.market.calendar import HISTORY_START, NyseSessions
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
STALE_EXIT = 3  # `paper status --stale-after-days`: no ok paper run for too long
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
