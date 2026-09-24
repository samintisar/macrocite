"""Load real data, run one backtest, store it, and write its report (spec 02)."""

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from sqlmodel import Session, col, select

from signalbench.backtest.benchmarks import benchmark_stats, buy_and_hold, equal_weight
from signalbench.backtest.fingerprint import data_fingerprint
from signalbench.backtest.metrics import run_metrics
from signalbench.backtest.passbar import evaluate_pass_bar, passes
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
    end: date | None = None,
) -> tuple[BacktestRun, Path]:
    """Run one setup (or the combined set) with Jev off, store it, and write the report."""
    config = config.with_setups(setups_for_run(setup, jev_mode))
    inputs = load_market_inputs(session, universe, config.regime_symbol, end)
    last = inputs.benchmark[-1].date if end is None else end
    start = config.backtest.start
    lookahead = max(config.earnings_blackout_sessions, config.earnings_exit_sessions)
    sessions = calendar.sessions_between(start, last) + calendar.next_sessions(last, lookahead)
    market = MarketView(inputs.symbols, inputs.benchmark, sessions, config)
    result = simulate(market, NullReadingsView(), config, start, last)
    metrics = run_metrics(result, config.backtest)
    run_sessions = market.sessions_between(result.start, result.end)
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
