"""Load real data, run one backtest, store it, and write its report (specs 02, 03, and 06)."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from sqlmodel import Session, col, select

from signalbench.backtest.benchmarks import benchmark_stats, buy_and_hold, equal_weight
from signalbench.backtest.fingerprint import data_fingerprint
from signalbench.backtest.metrics import run_metrics, trade_stats, vehicle_stats
from signalbench.backtest.passbar import evaluate_pass_bar, passes
from signalbench.backtest.preregistration import (
    RunRefusedError,
    check_version_unchanged,
)
from signalbench.backtest.report import (
    cash_vehicle_payload,
    jev_payload,
    metrics_payload,
    pass_bar_payload,
    render_report,
    report_path,
    trade_log_payload,
)
from signalbench.backtest.simulator import simulate
from signalbench.db.models import BacktestRun, Ticker
from signalbench.ingest.cdr import CdrEntry
from signalbench.ingest.earnings import sec_earnings_dates
from signalbench.jev.questions import JEV_RELEASE, MODEL, QUESTION_SET
from signalbench.jev.store import load_document_readings, resolved_builds
from signalbench.market.bars import AdjustedBar, adjusted_bars
from signalbench.market.calendar import HISTORY_START, Sessions
from signalbench.market.legal_close import LegalCloses
from signalbench.strategy.config import BacktestParams, SetupName, StrategyConfig
from signalbench.strategy.market_view import MarketView, SymbolInput
from signalbench.strategy.readings import (
    DocumentReading,
    JevReadingsView,
    NullReadingsView,
    ReadingsView,
)

RunSetup = Literal["pullback", "breakout", "sentiment", "combined"]
JevMode = Literal["off", "filter"]
SETUPS_BY_RUN: dict[str, tuple[SetupName, ...]] = {
    "pullback": ("pullback",),
    "breakout": ("breakout",),
    "combined": ("pullback", "breakout"),
}
NEW_YORK = ZoneInfo("America/New_York")
DAILY_BAR_FINAL = time(16, 15)  # a bar dated today (New York) is partial before this time
QQQ_BENCHMARK = "QQQ buy-and-hold"
SURVIVOR_BENCHMARK = "Survivor benchmark (equal weight, not rebalanced)"
SENTIMENT_START = date(2016, 1, 1)  # spec 03: filings are backfilled from 2016
SENSITIVITY_COST = 0.0005  # spec 06: cash-vehicle switching cost of the information-only rerun


class UnsupportedRunError(ValueError):
    """A setup and Jev mode that never run together. Refused before any data is read."""


def setups_for_run(setup: RunSetup, jev_mode: JevMode) -> tuple[SetupName, ...]:
    if setup == "sentiment":
        if jev_mode == "filter":
            raise UnsupportedRunError(
                "--setup sentiment runs with --jev off: Sentiment uses readings as its trigger, "
                "and the filter decision covers Pullback and Breakout only (spec 03)."
            )
        return ("sentiment",)
    return SETUPS_BY_RUN[setup]


def sentiment_params(params: BacktestParams, last: date) -> BacktestParams:
    """Spec 03: the Sentiment backtest runs from 2016-01-01 to the end, with the halves split at
    the calendar midpoint of that period. The pass bar itself is unchanged."""
    midpoint = SENTIMENT_START + timedelta(days=(last - SENTIMENT_START).days // 2)
    return replace(
        params, start=SENTIMENT_START, h1_end=midpoint, h2_start=midpoint + timedelta(days=1)
    )


@dataclass(frozen=True)
class JevInputs:
    """What a Sentiment run or a --jev filter run needs from spec 03."""

    theta_block: float | None  # the committed filter theta for --jev filter runs, else None
    model_requested: str = MODEL
    question_set: str = QUESTION_SET


@dataclass(frozen=True)
class MarketInputs:
    symbols: list[SymbolInput]
    benchmark: list[AdjustedBar]


def load_market_inputs(
    session: Session, universe: list[CdrEntry], benchmark_symbol: str, end: date | None
) -> MarketInputs:
    """Adjusted US bars, every SEC Item 2.02 date (unclustered), and sectors per universe name."""
    wanted = [entry.us_symbol for entry in universe] + [benchmark_symbol]
    rows = session.exec(select(Ticker).where(col(Ticker.symbol).in_(wanted))).all()
    tickers = {ticker.symbol: ticker for ticker in rows}
    missing = sorted(set(wanted) - set(tickers))
    if missing:
        raise RunRefusedError(f"Not seeded: {', '.join(missing)}. Run `signalbench seed` first.")
    symbols = [
        SymbolInput(
            symbol=entry.us_symbol,
            sector=entry.sector,
            bars=adjusted_bars(session, tickers[entry.us_symbol].id, end=end),
            earnings=sec_earnings_dates(session, tickers[entry.us_symbol].id),
        )
        for entry in sorted(universe, key=lambda e: e.us_symbol)
    ]
    benchmark = adjusted_bars(session, tickers[benchmark_symbol].id, end=end)
    if not benchmark:
        raise RunRefusedError(f"No {benchmark_symbol} prices. Run `signalbench ingest prices` first.")
    return MarketInputs(symbols=symbols, benchmark=benchmark)


def drop_partial_session(inputs: MarketInputs, now: datetime) -> MarketInputs:
    """Drop today's bars when the latest benchmark bar is today (New York) and it is before 16:15.

    `now` must be timezone-aware: a naive clock would be read in the machine's local zone.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError(f"now must be timezone-aware, got naive {now.isoformat()}")
    local = now.astimezone(NEW_YORK)
    today = local.date()
    if inputs.benchmark[-1].date != today or local.time() >= DAILY_BAR_FINAL:
        return inputs
    return MarketInputs(
        symbols=[
            replace(item, bars=[bar for bar in item.bars if bar.date < today])
            for item in inputs.symbols
        ],
        benchmark=[bar for bar in inputs.benchmark if bar.date < today],
    )


def last_complete_session(calendar: Sessions, now: datetime) -> date:
    """The most recent NYSE session complete as of `now` (spec 03), on the same cutoff as
    `drop_partial_session`: a session dated today is not yet complete before 16:15 New York, so
    it is not used as a legal close or a calibration label input either.

    `now` must be timezone-aware, like `drop_partial_session`.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError(f"now must be timezone-aware, got naive {now.isoformat()}")
    local = now.astimezone(NEW_YORK)
    today = local.date()
    end = today if local.time() >= DAILY_BAR_FINAL else today - timedelta(days=1)
    sessions = calendar.sessions_between(HISTORY_START, end)
    if not sessions:
        raise ValueError(f"No NYSE sessions on or before {end.isoformat()}")
    return sessions[-1]


def _series_end(bars: Sequence[AdjustedBar]) -> str:
    return f"last bar {bars[-1].date.isoformat()}" if bars else "no bars"


def latest_bar_date(inputs: MarketInputs) -> date:
    """The latest bar date in any input series, the benchmark included."""
    every = [inputs.benchmark, *(item.bars for item in inputs.symbols)]
    return max(bars[-1].date for bars in every if bars)


def check_series_current(inputs: MarketInputs, benchmark_symbol: str, last_session: date) -> None:
    """Every universe series and the benchmark must end on the run's last session (no stale or
    truncated data)."""
    series = [(item.symbol, item.bars) for item in inputs.symbols]
    series.append((benchmark_symbol, inputs.benchmark))
    behind = [
        f"{symbol} ({_series_end(bars)})"
        for symbol, bars in series
        if not bars or bars[-1].date != last_session
    ]
    if behind:
        raise RunRefusedError(
            f"These price series do not end on the run's last session "
            f"{last_session.isoformat()}: {', '.join(behind)}. "
            "Run `signalbench ingest prices` and try again."
        )


def run_backtest(
    session: Session,
    *,
    setup: RunSetup,
    jev_mode: JevMode,
    config: StrategyConfig,
    config_sha256: str,
    universe: list[CdrEntry],
    calendar: Sessions,
    git_sha: str,
    run_date: date,
    reports_dir: Path,
    now: datetime,
    end: date | None = None,
    jev: JevInputs | None = None,
) -> tuple[BacktestRun, Path]:
    """Run one setup (or the combined set), store it, and write the report.

    A config with a cash vehicle (spec 06) is simulated a second time at SENSITIVITY_COST per
    switch; only its total return, CAGR, Sharpe, and drawdown are kept, in the stored metrics.

    Refuses (RunRefusedError) when stored runs of this strategy version used another config,
    when a universe series does not end on the run's last session, when a Sentiment or
    --jev filter run finds no Jev readings, or when --jev filter has no theta (the filter is
    information-only). `now` is the clock: today's bars are dropped as partial before 16:15
    New York time. `jev` is required for Sentiment and --jev filter runs.
    """
    config = config.with_setups(setups_for_run(setup, jev_mode))
    reads_jev = setup == "sentiment" or jev_mode == "filter"
    if reads_jev and jev is None:
        raise ValueError(f"--setup {setup} --jev {jev_mode} needs JevInputs (spec 03)")
    if jev_mode == "filter" and (jev is None or jev.theta_block is None):
        raise RunRefusedError(
            "The Jev filter is information-only (data/jev_filter_v1.yaml), so --jev filter runs "
            "are refused (spec 03)."
        )
    check_version_unchanged(session, config.version, config_sha256)
    inputs = drop_partial_session(
        load_market_inputs(session, universe, config.regime_symbol, end), now
    )
    if not inputs.benchmark:
        raise RunRefusedError(f"No complete {config.regime_symbol} bars yet.")
    last = latest_bar_date(inputs) if end is None else end
    if setup == "sentiment":
        config = replace(config, backtest=sentiment_params(config.backtest, last))
    start = config.backtest.start
    run_sessions = calendar.sessions_between(start, last)
    if not run_sessions:
        raise RunRefusedError(f"No sessions between {start} and {last}.")
    check_series_current(inputs, config.regime_symbol, run_sessions[-1])
    lookahead = max(config.earnings_blackout_sessions, config.earnings_exit_sessions)
    sessions = run_sessions + calendar.next_sessions(last, lookahead)
    market = MarketView(inputs.symbols, inputs.benchmark, sessions, config)
    readings: ReadingsView = NullReadingsView()
    documents: dict[str, list[DocumentReading]] | None = None
    if reads_jev and jev is not None:
        documents = load_document_readings(
            session,
            [item.symbol for item in inputs.symbols],
            LegalCloses(calendar.session_closes(HISTORY_START, last)),
            model=jev.model_requested,
            question_set=jev.question_set,
        )
        if not any(documents.values()):
            raise RunRefusedError(
                f"No Jev readings ({jev.model_requested}, {jev.question_set}) with a legal close "
                f"by {last.isoformat()}. Run `signalbench jev backfill` first (spec 03)."
            )
        readings = JevReadingsView(
            documents,
            calendar.sessions_between(HISTORY_START, last),
            block_theta=jev.theta_block if jev_mode == "filter" else None,
        )
    result = simulate(market, readings, config, start, last)
    metrics = run_metrics(result, config.backtest)
    run_sessions = market.sessions_between(result.start, result.end)  # the simulated sessions
    qqq = benchmark_stats(QQQ_BENCHMARK, buy_and_hold(inputs.benchmark, run_sessions), run_sessions)
    survivor = benchmark_stats(
        SURVIVOR_BENCHMARK,
        equal_weight([item.bars for item in inputs.symbols], run_sessions),
        run_sessions,
    )
    bar = evaluate_pass_bar(metrics, qqq.sharpe, config.backtest)
    payload = metrics_payload(metrics, [qqq, survivor], config.backtest)
    vehicle = config.cash_vehicle
    if vehicle is not None:
        # Spec 06: the same config again with cheaper switching, reported and never stored.
        cheap = replace(config, cash_vehicle=replace(vehicle, cost_per_side=SENSITIVITY_COST))
        rerun = simulate(market, readings, cheap, start, last)
        sensitivity = benchmark_stats(
            f"{vehicle.symbol} switching at {SENSITIVITY_COST:.2%}",
            [point.equity for point in rerun.equity_curve],
            run_sessions,
        )
        payload["cash_vehicle"] = cash_vehicle_payload(
            vehicle_stats(result, vehicle), sensitivity, SENSITIVITY_COST
        )
    if documents is not None and jev is not None:
        payload["jev"] = jev_payload(
            model_requested=jev.model_requested,
            question_set=jev.question_set,
            readings=sum(len(rows) for rows in documents.values()),
            builds=resolved_builds(documents),
            theta_block=jev.theta_block if jev_mode == "filter" else None,
            information_only=jev_mode == "filter" or metrics.trades < config.backtest.min_trades,
            out_of_sample=trade_stats([t for t in result.trades if t.signal_date >= JEV_RELEASE]),
        )
    run = BacktestRun(
        strategy_version=config.version,
        config_sha256=config_sha256,
        git_sha=git_sha,
        setup=setup,
        jev_mode=jev_mode,
        start_date=result.start,
        end_date=result.end,
        data_fingerprint=data_fingerprint(
            [(item.symbol, item.bars) for item in inputs.symbols]
            + [(config.regime_symbol, inputs.benchmark)],
            [(item.symbol, day) for item in inputs.symbols for day in item.earnings],
            None
            if documents is None
            else [(symbol, row) for symbol, rows in documents.items() for row in rows],
        ),
        metrics=payload,
        pass_bar=pass_bar_payload(bar),
        passed=setup != "combined" and passes(bar),  # a combined run is information only
        trade_log=trade_log_payload(result),
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    path = report_path(reports_dir, run_date, setup, jev_mode, config.version)
    if path.exists():
        path = path.with_name(f"{path.stem}-{str(run.id)[:8]}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(run), encoding="utf-8")
    return run, path
