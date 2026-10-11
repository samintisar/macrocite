"""Load Kraken daily candles, run one crypto backtest, store it, and write its report (spec 09).

The stock runner (runner.py) is neither used nor changed: the live strategy depends on it.
This runner shares the engine behind it: decide(), the simulator, metrics, the pass bar, the
stored run, and the report.
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlmodel import Session, col, select

from signalbench.backtest.benchmarks import benchmark_stats, buy_and_hold
from signalbench.backtest.fingerprint import data_fingerprint
from signalbench.backtest.metrics import run_metrics
from signalbench.backtest.passbar import evaluate_pass_bar, passes
from signalbench.backtest.preregistration import (
    RunRefusedError,
    check_cost_matches_kraken,
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
from signalbench.db.models import BacktestRun, Ticker, TickerKind
from signalbench.ingest.kraken import CRYPTO_SECTOR
from signalbench.market.bars import AdjustedBar, adjusted_bars
from signalbench.market.calendar import Sessions
from signalbench.strategy.config import StrategyConfig
from signalbench.strategy.market_view import MarketView, SymbolInput
from signalbench.strategy.readings import NullReadingsView

CRYPTO_SYMBOLS = ("ETHUSD", "XBTUSD")
BTC = "XBTUSD"  # a universe symbol, the regime symbol, and the benchmark
BTC_BENCHMARK = "BTC buy-and-hold"
MAX_GAP_DAYS = 3  # a longer run of missing days inside the run is broken data


def last_complete_day(now: datetime) -> date:
    """Yesterday in UTC: today's Kraken candle is final only at 00:00 UTC tomorrow."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError(f"now must be timezone-aware, got naive {now.isoformat()}")
    return now.astimezone(UTC).date() - timedelta(days=1)


def missing_days(bars: Sequence[AdjustedBar], start: date, end: date) -> list[date]:
    """Days from max(start, the first bar) to `end` with no candle."""
    if not bars:
        return []
    have = {bar.date for bar in bars}
    first = max(start, bars[0].date)
    return [
        first + timedelta(days=i)
        for i in range((end - first).days + 1)
        if first + timedelta(days=i) not in have
    ]


def longest_gap(days: Sequence[date]) -> int:
    """The longest run of consecutive days in a sorted list."""
    longest = run = 0
    previous: date | None = None
    for day in days:
        run = run + 1 if previous is not None and day == previous + timedelta(days=1) else 1
        longest = max(longest, run)
        previous = day
    return longest


def load_crypto_bars(session: Session, end: date) -> dict[str, list[AdjustedBar]]:
    """Each crypto symbol's bars up to `end`, in CRYPTO_SYMBOLS order."""
    rows = session.exec(
        select(Ticker).where(
            col(Ticker.symbol).in_(CRYPTO_SYMBOLS), Ticker.kind == TickerKind.crypto
        )
    ).all()
    tickers = {ticker.symbol: ticker for ticker in rows}
    missing = [symbol for symbol in CRYPTO_SYMBOLS if symbol not in tickers]
    if missing:
        raise RunRefusedError(
            f"Not loaded: {', '.join(missing)}. Run `signalbench crypto ingest-file` first."
        )
    bars = {
        symbol: adjusted_bars(session, tickers[symbol].id, end=end) for symbol in CRYPTO_SYMBOLS
    }
    empty = [symbol for symbol, series in bars.items() if not series]
    if empty:
        raise RunRefusedError(
            f"No prices for {', '.join(empty)}. Run `signalbench crypto ingest-file` first."
        )
    return bars


def _check_series(
    bars: dict[str, list[AdjustedBar]], start: date, last: date
) -> dict[str, list[date]]:
    """Every series ends on `last` and has no gap longer than MAX_GAP_DAYS inside the run.
    Returns each symbol's missing days, for the report."""
    behind = [
        f"{symbol} (last bar {series[-1].date.isoformat()})"
        for symbol, series in bars.items()
        if series[-1].date != last
    ]
    if behind:
        raise RunRefusedError(
            f"These series do not end on {last.isoformat()}, the last complete UTC day: "
            f"{', '.join(behind)}. Run `signalbench crypto ingest-recent` and try again."
        )
    gaps = {symbol: missing_days(series, start, last) for symbol, series in bars.items()}
    broken = [
        f"{symbol} ({longest_gap(days)} days in a row)"
        for symbol, days in gaps.items()
        if longest_gap(days) > MAX_GAP_DAYS
    ]
    if broken:
        raise RunRefusedError(
            f"Gaps longer than {MAX_GAP_DAYS} days inside the run: {', '.join(broken)}. "
            "Check the data before running (spec 09)."
        )
    return gaps


def run_crypto_backtest(
    session: Session,
    *,
    config: StrategyConfig,
    config_sha256: str,
    calendar: Sessions,
    git_sha: str,
    run_date: date,
    reports_dir: Path,
    now: datetime,
) -> tuple[BacktestRun, Path]:
    """Breakout on BTC and ETH from `backtest.start` to yesterday (UTC), judged against BTC
    buy-and-hold; store the run and write its report.

    Refuses (RunRefusedError) a cost other than Kraken's top fee, a changed config for a
    stored version, an unloaded symbol, a series that does not end yesterday, or a gap longer
    than MAX_GAP_DAYS inside the run. `now` must be timezone-aware.
    """
    check_cost_matches_kraken(config.cost_per_side)
    config = config.with_setups(("breakout",))
    check_version_unchanged(session, config.version, config_sha256)
    last = last_complete_day(now)
    start = config.backtest.start
    bars = load_crypto_bars(session, last)
    gaps = _check_series(bars, start, last)
    run_sessions = calendar.sessions_between(start, last)
    if not run_sessions:
        raise RunRefusedError(f"No sessions between {start} and {last}.")
    lookahead = max(config.earnings_blackout_sessions, config.earnings_exit_sessions)
    market = MarketView(
        [
            SymbolInput(symbol=symbol, sector=CRYPTO_SECTOR, bars=series, earnings=[])
            for symbol, series in bars.items()
        ],
        bars[BTC],
        run_sessions + calendar.next_sessions(last, lookahead),
        config,
    )
    result = simulate(market, NullReadingsView(), config, start, last)
    metrics = run_metrics(result, config.backtest)
    simulated = market.sessions_between(result.start, result.end)
    btc = benchmark_stats(BTC_BENCHMARK, buy_and_hold(bars[BTC], simulated), simulated)
    bar = evaluate_pass_bar(metrics, btc.sharpe, config.backtest)
    payload = metrics_payload(metrics, [btc], config.backtest)
    payload["market"] = "crypto"
    payload["benchmark"] = BTC_BENCHMARK
    payload["missing_days"] = {
        symbol: [day.isoformat() for day in days] for symbol, days in gaps.items()
    }
    run = BacktestRun(
        strategy_version=config.version,
        config_sha256=config_sha256,
        git_sha=git_sha,
        setup="breakout",
        jev_mode="off",
        start_date=result.start,
        end_date=result.end,
        data_fingerprint=data_fingerprint(list(bars.items()), []),
        metrics=payload,
        pass_bar=pass_bar_payload(bar),
        passed=passes(bar),
        trade_log=trade_log_payload(result),
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    path = report_path(reports_dir, run_date, "breakout", "off", config.version)
    if path.exists():
        path = path.with_name(f"{path.stem}-{str(run.id)[:8]}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(run), encoding="utf-8")
    return run, path
