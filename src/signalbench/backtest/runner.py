"""Load real data, run one backtest, store it, and write its report (spec 02)."""

from dataclasses import dataclass, replace
from datetime import date, datetime, time
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from sqlmodel import Session, col, select

from signalbench.backtest.benchmarks import benchmark_stats, buy_and_hold, equal_weight
from signalbench.backtest.fingerprint import data_fingerprint
from signalbench.backtest.metrics import run_metrics
from signalbench.backtest.passbar import evaluate_pass_bar, passes
from signalbench.backtest.preregistration import (
    RunRefusedError,
    check_version_unchanged,
)
from signalbench.backtest.report import (
    metrics_payload,
    pass_bar_payload,
    render_report,
    report_path,
    trade_log_payload,
)
from signalbench.backtest.simulator import simulate
from signalbench.db.models import BacktestRun, Ticker
from signalbench.ingest.cdr import CdrEntry
from signalbench.ingest.earnings import earnings_dates
from signalbench.market.bars import AdjustedBar, adjusted_bars
from signalbench.market.calendar import Sessions
from signalbench.strategy.config import SetupName, StrategyConfig
from signalbench.strategy.market_view import MarketView, SymbolInput
from signalbench.strategy.readings import NullReadingsView

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


class RequiresSpec03Error(ValueError):
    """The Sentiment setup and the Jev filter need Jev readings, which arrive in spec 03."""


def setups_for_run(setup: RunSetup, jev_mode: JevMode) -> tuple[SetupName, ...]:
    if setup == "sentiment":
        raise RequiresSpec03Error("--setup sentiment requires Jev readings (spec 03).")
    if jev_mode == "filter":
        raise RequiresSpec03Error("--jev filter requires Jev readings (spec 03).")
    return SETUPS_BY_RUN[setup]


@dataclass(frozen=True)
class MarketInputs:
    symbols: list[SymbolInput]
    benchmark: list[AdjustedBar]


def load_market_inputs(
    session: Session, universe: list[CdrEntry], benchmark_symbol: str, end: date | None
) -> MarketInputs:
    """Adjusted US bars, clustered earnings dates, and sectors for every universe name."""
    wanted = [entry.us_symbol for entry in universe] + [benchmark_symbol]
    rows = session.exec(select(Ticker).where(col(Ticker.symbol).in_(wanted))).all()
    tickers = {ticker.symbol: ticker for ticker in rows}
    missing = sorted(set(wanted) - set(tickers))
    if missing:
        raise ValueError(f"Not seeded: {', '.join(missing)}. Run `signalbench seed` first.")
    symbols = [
        SymbolInput(
            symbol=entry.us_symbol,
            sector=entry.sector,
            bars=adjusted_bars(session, tickers[entry.us_symbol].id, end=end),
            earnings=earnings_dates(session, tickers[entry.us_symbol].id),
        )
        for entry in sorted(universe, key=lambda e: e.us_symbol)
    ]
    benchmark = adjusted_bars(session, tickers[benchmark_symbol].id, end=end)
    if not benchmark:
        raise ValueError(f"No {benchmark_symbol} prices. Run `signalbench ingest prices` first.")
    return MarketInputs(symbols=symbols, benchmark=benchmark)


def drop_partial_session(inputs: MarketInputs, now: datetime) -> MarketInputs:
    """Drop today's bars when the latest benchmark bar is today (New York) and it is before 16:15."""
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


def _series_end(item: SymbolInput) -> str:
    return f"last bar {item.bars[-1].date.isoformat()}" if item.bars else "no bars"


def check_series_current(symbols: list[SymbolInput], last_session: date) -> None:
    """Every universe series must end on the run's last session (no stale or truncated data)."""
    behind = [
        f"{item.symbol} ({_series_end(item)})"
        for item in symbols
        if not item.bars or item.bars[-1].date != last_session
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
) -> tuple[BacktestRun, Path]:
    """Run one setup (or the combined set) with Jev off, store it, and write the report.

    Refuses (RunRefusedError) when stored runs of this strategy version used another config, or
    when a universe series does not end on the run's last session. `now` is the clock: today's
    bars are dropped as partial before 16:15 New York time.
    """
    config = config.with_setups(setups_for_run(setup, jev_mode))
    check_version_unchanged(session, config.version, config_sha256)
    inputs = drop_partial_session(
        load_market_inputs(session, universe, config.regime_symbol, end), now
    )
    if not inputs.benchmark:
        raise RunRefusedError(f"No complete {config.regime_symbol} bars yet.")
    last = inputs.benchmark[-1].date if end is None else end
    start = config.backtest.start
    run_sessions = calendar.sessions_between(start, last)
    if not run_sessions:
        raise RunRefusedError(f"No sessions between {start} and {last}.")
    check_series_current(inputs.symbols, run_sessions[-1])
    lookahead = max(config.earnings_blackout_sessions, config.earnings_exit_sessions)
    sessions = run_sessions + calendar.next_sessions(last, lookahead)
    market = MarketView(inputs.symbols, inputs.benchmark, sessions, config)
    result = simulate(market, NullReadingsView(), config, start, last)
    metrics = run_metrics(result, config.backtest)
    run_sessions = market.sessions_between(result.start, result.end)  # the simulated sessions
    qqq = benchmark_stats(QQQ_BENCHMARK, buy_and_hold(inputs.benchmark, run_sessions), run_sessions)
    survivor = benchmark_stats(
        SURVIVOR_BENCHMARK,
        equal_weight([item.bars for item in inputs.symbols], run_sessions),
        run_sessions,
    )
    bar = evaluate_pass_bar(metrics, qqq.sharpe, config.backtest)
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
            + [(config.regime_symbol, inputs.benchmark)]
        ),
        metrics=metrics_payload(metrics, [qqq, survivor], config.backtest),
        pass_bar=pass_bar_payload(bar),
        passed=passes(bar),
        trade_log=trade_log_payload(result),
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    path = report_path(reports_dir, run_date, setup, jev_mode)
    if path.exists():
        path = path.with_name(f"{path.stem}-{str(run.id)[:8]}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(run), encoding="utf-8")
    return run, path
