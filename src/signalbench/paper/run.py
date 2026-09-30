"""`signalbench paper run` (spec 07, Commands): step every portfolio to the last complete session.

Each stepped session is one transaction: the portfolio's saved state, the session's events,
and its equity row. Orders decided at a close are written that night, before the next open;
their fills the next night carry the order event's id. Rows written after the next session's
open (a missed night, or a late run) are marked `catch_up`.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlmodel import Session, col, select

from signalbench.backtest.preregistration import RunRefusedError
from signalbench.backtest.provenance import code_version, uncommitted_code
from signalbench.backtest.runner import (
    MarketInputs,
    check_series_current,
    last_complete_session,
    load_market_inputs,
    setups_for_run,
)
from signalbench.backtest.sim_state import state_from_json, state_to_json
from signalbench.backtest.simulator import SimState, StepResult, step
from signalbench.db.models import (
    PaperEquity,
    PaperEvent,
    PaperPortfolio,
    PaperRun,
    Price,
    Ticker,
)
from signalbench.ingest.cdr import CdrEntry
from signalbench.ingest.prices import Split, SplitFetcher
from signalbench.market.calendar import HISTORY_START, Sessions
from signalbench.paper.portfolios import paper_setup
from signalbench.paper.report import report_due, write_weekly_report
from signalbench.paper.splits import Mark, adjust_for_splits, references
from signalbench.paper.start import PaperRefusedError
from signalbench.strategy.config import (
    StrategyConfig,
    config_sha256,
    load_strategy_config,
)
from signalbench.strategy.market_view import MarketView
from signalbench.strategy.readings import NullReadingsView

NEW_YORK = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)  # New York
FILLS = {"exit": "fill_exit", "entry": "fill_entry"}  # simulator event -> paper event kind
RunStatus = Literal["locked", "ok", "failed"]
ABANDONED_AFTER = timedelta(hours=2)  # a `running` row this old was killed or crashed
ABANDONED = "abandoned (killed or crashed)"


@dataclass(frozen=True)
class RunOutcome:
    status: RunStatus
    run_id: int | None = None
    target: date | None = None
    stepped: dict[str, int] = field(default_factory=dict)  # sessions stepped per portfolio
    error: str | None = None
    report: Path | None = None  # the weekly report, when this run wrote one


def run_paper(
    session: Session,
    *,
    lock: AbstractContextManager[bool],
    repo: Path,
    universe: list[CdrEntry],
    calendar: Sessions,
    clock: Callable[[], datetime],
    ingest: Callable[[Session], list[str]],
    ingest_earnings: Callable[[Session, date], list[str]],
    splits: SplitFetcher,
    reports_dir: Path,
    echo: Callable[[str], None],
) -> RunOutcome:
    """The nightly job. `lock` yields False when another run holds it: nothing is done.
    `ingest` refreshes prices (and the liquidity flags) and returns what failed.
    `ingest_earnings` refreshes the Finnhub earnings calendar from a date (the oldest last
    session of a portfolio that is behind, so a catch-up keeps the dates it passes) and returns
    what failed; it is not critical: a failure is printed and the stored dates are used. Both
    kinds of failure are also kept as the run row's `warnings`. `splits` lists a symbol's
    splits after a date, for the symbols a portfolio holds (paper/splits.py); a failed lookup,
    or stored prices on another scale than the saved state, refuses that portfolio for the
    night (the next run catches up). `clock` must return timezone-aware times. The first run of
    an ISO week writes the weekly report into `reports_dir`, after stepping (a refused
    portfolio does not stop the others or it); a refused portfolio fails the run at the end.
    Every run records the commit of `repo` it ran; uncommitted changes to tracked code or data
    there refuse the run before anything is ingested or stepped. Runs left `running` for over
    ABANDONED_AFTER are marked failed first."""
    with lock as held:
        if not held:
            echo("Another paper run holds the lock; nothing to do.")
            return RunOutcome(status="locked")
        _abandon_stale_runs(session, clock())
        run = PaperRun(started_at=clock(), status="running")
        session.add(run)
        session.commit()
        session.refresh(run)
        stepped: dict[str, int] = {}
        warnings: list[str] = []
        report: Path | None = None
        try:
            refused = _step_all(
                session, run, repo, universe, calendar, clock, (ingest, ingest_earnings),
                _cached(splits, echo), echo, stepped, warnings,
            )
            today = clock().astimezone(NEW_YORK).date()
            if report_due(reports_dir, today):
                report = write_weekly_report(session, reports_dir, today)
                echo(f"weekly report: {report}")
            if refused:
                raise PaperRefusedError("; ".join(refused))
        except Exception as error:  # noqa: BLE001  # spec 07: any error fails the run, recorded
            session.rollback()
            message = f"{type(error).__name__}: {error}"
            _finish(session, run, "failed", clock(), stepped, warnings, message)
            return RunOutcome("failed", run.id, run.target_session, stepped, message, report)
        _finish(session, run, "ok", clock(), stepped, warnings, None)
        return RunOutcome("ok", run.id, run.target_session, stepped, None, report)


def _abandon_stale_runs(session: Session, now: datetime) -> None:
    """A run killed or crashed mid-way leaves its row `running`; the lock is held here, so an
    old one is not in progress. It counts as a failed run (the weekly report counts it)."""
    rows = session.exec(select(PaperRun).where(PaperRun.status == "running")).all()
    for row in rows:
        if now - row.started_at > ABANDONED_AFTER:
            row.status = "failed"
            row.error = ABANDONED
            session.add(row)
    session.commit()


def _step_all(
    session: Session,
    run: PaperRun,
    repo: Path,
    universe: list[CdrEntry],
    calendar: Sessions,
    clock: Callable[[], datetime],
    ingests: tuple[Callable[[Session], list[str]], Callable[[Session, date], list[str]]],
    splits: SplitFetcher,
    echo: Callable[[str], None],
    stepped: dict[str, int],
    warnings: list[str],
) -> list[str]:
    """Step every portfolio that is behind the target. Returns why portfolios were refused (a
    changed config, a failed split lookup, a held symbol without a current bar, or prices on
    another scale than the saved state); they are not stepped, and the others still are.
    Ingest and calendar failures are appended to `warnings`."""
    portfolios = session.exec(select(PaperPortfolio).order_by(col(PaperPortfolio.id))).all()
    if not portfolios:
        raise PaperRefusedError("No paper portfolios. Run `signalbench paper start` first.")
    version = code_version(repo)  # recorded on every run; a new commit is allowed (bug fixes)
    run.git_sha, run.git_dirty = version.sha, version.dirty
    target = last_complete_session(calendar, clock())
    run.target_session = target
    session.add(run)
    session.commit()
    if version.dirty:
        raise PaperRefusedError(uncommitted_code(version))
    behind = [p for p in portfolios if p.last_session is None or p.last_session < target]
    if not behind:
        echo(f"Every portfolio has stepped {target.isoformat()}; nothing to do.")
        return []
    ingest, ingest_earnings = ingests
    failed = ingest(session)
    if failed:
        echo(f"ingest: {len(failed)} failed ({', '.join(failed)}); stepping on the stored prices")
        warnings.append(f"prices: {len(failed)} failed ({', '.join(failed)})")
    since = min(p.started_on if p.last_session is None else p.last_session for p in behind)
    warning = _refresh_earnings(session, ingest_earnings, since, echo)
    if warning is not None:
        warnings.append(warning)
    refused: list[str] = []
    inputs: dict[str, MarketInputs] = {}
    for portfolio in behind:
        path = repo / portfolio.config_path
        if not path.exists() or config_sha256(path.read_bytes()) != portfolio.config_sha256:
            refused.append(
                f"{portfolio.name}: {portfolio.config_path} no longer matches the config_sha256 "
                "stored at its start, so it was not stepped (spec 07: configs are never edited)"
            )
            continue
        config = load_strategy_config(path)[0].with_setups(
            setups_for_run(paper_setup(portfolio.setup, portfolio.name), "off")
        )
        symbol = config.regime_symbol
        if symbol not in inputs:
            inputs[symbol] = load_market_inputs(
                session, universe, symbol, target, calendar_earnings=True
            )
            _check_current(inputs[symbol], symbol, target, set())  # the benchmark
        market = _market(inputs[symbol], config, calendar, target)
        try:
            _check_held_current(portfolio, config, inputs[symbol], target)
            _step_portfolio(
                session, portfolio, config, market, calendar, target, clock, splits, stepped
            )
        except PaperRefusedError as error:  # prices or splits: this portfolio only, like a config
            session.rollback()
            refused.append(str(error))
            echo(f"{portfolio.name}: not stepped ({error})")
            continue
        echo(f"{portfolio.name}: {stepped.get(portfolio.name, 0)} sessions to {target.isoformat()}")
    return refused


def _refresh_earnings(
    session: Session,
    ingest_earnings: Callable[[Session, date], list[str]],
    since: date,
    echo: Callable[[str], None],
) -> str | None:
    """The Finnhub calendar, stored tonight, is read from tonight's sessions on; sessions
    already stepped are never re-decided. Not critical: a failure keeps the stored dates.
    Returns the warning for the run row, or None."""
    try:
        failed = ingest_earnings(session, since)
    except Exception as error:  # noqa: BLE001  # the stored dates still serve
        session.rollback()
        warning = f"earnings calendar FAILED ({type(error).__name__}: {error})"
        echo(f"{warning}; stepping on the stored earnings dates")
        return warning
    if not failed:
        return None
    warning = f"earnings calendar: {len(failed)} failed ({', '.join(failed)})"
    echo(f"{warning}; stepping on the stored earnings dates")
    return warning


def _cached(fetch: SplitFetcher, echo: Callable[[str], None]) -> SplitFetcher:
    """One lookup per symbol and date in a run. A failed lookup is printed once and raised
    again for every portfolio that needs it: those portfolios are refused tonight."""
    seen: dict[tuple[str, date], list[Split] | Exception] = {}

    def splits(symbol: str, since: date) -> list[Split]:
        if (symbol, since) not in seen:
            try:
                seen[symbol, since] = fetch(symbol, since)
            except Exception as error:  # noqa: BLE001  # raised again below, per portfolio
                echo(f"splits {symbol}: {type(error).__name__}: {error}")
                seen[symbol, since] = error
        found = seen[symbol, since]
        if isinstance(found, Exception):
            raise found
        return found

    return splits


def _check_current(
    inputs: MarketInputs, benchmark: str, target: date, symbols: set[str]
) -> None:
    """The backtest's `check_series_current` on the benchmark and `symbols` only. A paper run
    needs a bar on the target for the benchmark and for what a portfolio holds or has pending;
    any other universe name without one is just not tradable that night."""
    wanted = [item for item in inputs.symbols if item.symbol in symbols]
    check_series_current(MarketInputs(wanted, inputs.benchmark), benchmark, target)


def _check_held_current(
    portfolio: PaperPortfolio, config: StrategyConfig, inputs: MarketInputs, target: date
) -> None:
    """Refuses the portfolio (not the run) when a symbol it holds or has pending has no bar on
    the target; the next run catches up once the bar is stored."""
    vehicle = None if config.cash_vehicle is None else config.cash_vehicle.symbol
    held = {ref.symbol for ref in references(state_from_json(portfolio.state), vehicle)}
    try:
        _check_current(inputs, config.regime_symbol, target, held)
    except RunRefusedError as error:
        raise PaperRefusedError(f"{portfolio.name}: {error}") from error


def _market(
    inputs: MarketInputs, config: StrategyConfig, calendar: Sessions, target: date
) -> MarketView:
    """Bars up to the target; the calendar runs on past it, as the backtest's does, for the
    earnings look-ahead (the session calendar is known in advance)."""
    lookahead = max(config.earnings_blackout_sessions, config.earnings_exit_sessions)
    sessions = calendar.sessions_between(HISTORY_START, target)
    sessions += calendar.next_sessions(target, lookahead)
    return MarketView(inputs.symbols, inputs.benchmark, sessions, config)


def _step_portfolio(
    session: Session,
    portfolio: PaperPortfolio,
    config: StrategyConfig,
    market: MarketView,
    calendar: Sessions,
    target: date,
    clock: Callable[[], datetime],
    splits: SplitFetcher,
    stepped: dict[str, int],
) -> None:
    """Every session after the portfolio's last one, up to the target, in order: each is one
    transaction (state, raw-close marks, events, and equity row together). Splits since the
    last session rescale the saved state first, logged in the first session's transaction."""
    assert portfolio.id is not None
    state = state_from_json(portfolio.state)
    last = portfolio.last_session
    days = [d for d in calendar.sessions_between(portfolio.started_on, target) if last is None or d > last]
    if not days:
        return
    state, adjusted = _adjust_for_splits(session, portfolio, state, config, market, target, splits)
    vehicle = None if config.cash_vehicle is None else config.cash_vehicle.symbol
    for day in days:
        exit_orders, entry_orders = _order_ids(session, portfolio.id, state)
        result = step(state, market, NullReadingsView(), config, day)
        recorded_at = clock()
        catch_up = recorded_at >= _next_open(calendar, day)
        for payload in adjusted:
            session.add(PaperEvent(portfolio_id=portfolio.id, session=day, kind="split_adjust",
                                   payload=payload, recorded_at=recorded_at, catch_up=catch_up))
        adjusted = []
        _record(session, portfolio.id, day, result, exit_orders, entry_orders, recorded_at, catch_up)
        portfolio.state = state_to_json(result.state)
        portfolio.marks = _marks(session, result.state, vehicle, day)
        portfolio.last_session = day
        session.add(portfolio)
        session.commit()
        state = result.state
        stepped[portfolio.name] = stepped.get(portfolio.name, 0) + 1


def _adjust_for_splits(
    session: Session,
    portfolio: PaperPortfolio,
    state: SimState,
    config: StrategyConfig,
    market: MarketView,
    target: date,
    splits: SplitFetcher,
) -> tuple[SimState, list[dict[str, Any]]]:
    """Raises PaperRefusedError when a lookup fails: a split it would have listed must not be
    taken for no split. The next run looks again and catches up."""
    if state.last_session is None:
        return state, []
    vehicle = None if config.cash_vehicle is None else config.cash_vehicle.symbol
    symbols = sorted({ref.symbol for ref in references(state, vehicle)})
    known: dict[str, list[Split]] = {}
    for symbol in symbols:
        try:
            known[symbol] = splits(symbol, state.last_session)
        except Exception as error:  # any lookup failure refuses the portfolio
            raise PaperRefusedError(
                f"{portfolio.name}: the split lookup for {symbol} failed "
                f"({type(error).__name__}: {error}), so it was not stepped; the next run catches up"
            ) from error
    logged = session.exec(
        select(PaperEvent).where(
            PaperEvent.portfolio_id == portfolio.id, PaperEvent.kind == "split_adjust"
        )
    ).all()
    applied = {(str(e.payload["symbol"]), date.fromisoformat(str(e.payload["ex_date"]))) for e in logged}
    marks = []
    for symbol, (day, close) in sorted(portfolio.marks.items()):
        on = date.fromisoformat(str(day))
        stored = _raw_close(session, symbol, on, exact=True)
        marks.append(Mark(symbol, on, float(close), None if stored is None else stored[1]))
    return adjust_for_splits(state, market=market, known=known, applied=applied, target=target,
                             vehicle=vehicle, where=portfolio.name, marks=marks)


def _raw_close(
    session: Session, symbol: str, day: date, *, exact: bool
) -> tuple[date, float] | None:
    """The stored raw close (split-adjusted, not dividend-adjusted) of `symbol` on `day`, or,
    unless `exact`, on the latest session before it."""
    query = select(Price).join(Ticker, col(Ticker.id) == Price.ticker_id).where(
        Ticker.symbol == symbol
    )
    query = query.where(Price.date == day) if exact else query.where(col(Price.date) <= day)
    row = session.exec(query.order_by(col(Price.date).desc()).limit(1)).first()
    return None if row is None else (row.date, float(row.close))


def _marks(
    session: Session, state: SimState, vehicle: str | None, day: date
) -> dict[str, Any]:
    """The raw close on `day` of every symbol the state holds prices for, to check the next
    night that the stored history has not been rescaled without a known split."""
    marks: dict[str, Any] = {}
    for symbol in sorted({ref.symbol for ref in references(state, vehicle)}):
        found = _raw_close(session, symbol, day, exact=False)
        if found is not None:
            marks[symbol] = [found[0].isoformat(), found[1]]
    return marks


def _next_open(calendar: Sessions, day: date) -> datetime:
    return datetime.combine(calendar.next_sessions(day, 1)[0], MARKET_OPEN, tzinfo=NEW_YORK)


def _order_ids(
    session: Session, portfolio_id: int, state: SimState
) -> tuple[dict[str, int], dict[str, int]]:
    """The order events written at the last close: exits by position id, entries by symbol."""
    if state.orders is None:
        return {}, {}
    rows = session.exec(
        select(PaperEvent).where(
            PaperEvent.portfolio_id == portfolio_id,
            PaperEvent.session == state.orders.decided_on,
            col(PaperEvent.kind).in_(["order_exit", "order_entry"]),
        )
    ).all()
    exits = {str(r.payload["position_id"]): r.id for r in rows if r.kind == "order_exit" and r.id}
    entries = {str(r.payload["symbol"]): r.id for r in rows if r.kind == "order_entry" and r.id}
    return exits, entries


def _record(
    session: Session,
    portfolio_id: int,
    day: date,
    result: StepResult,
    exit_orders: dict[str, int],
    entry_orders: dict[str, int],
    recorded_at: datetime,
    catch_up: bool,
) -> None:
    """The session's events in the simulator's order, then the orders decided at its close,
    then its equity row."""
    trades = {trade.position_id: trade for trade in result.trades}

    def add(kind: str, payload: dict[str, Any]) -> None:
        session.add(
            PaperEvent(portfolio_id=portfolio_id, session=day, kind=kind, payload=payload,
                       recorded_at=recorded_at, catch_up=catch_up)
        )

    for event in result.events:
        kind = str(event["event"])
        payload = {key: value for key, value in event.items() if key not in ("date", "event")}
        if kind == "exit":
            position_id = str(event["position_id"])
            payload["order_event_id"] = exit_orders[position_id]
            payload["trade"] = _plain(asdict(trades[position_id]))
        elif kind == "entry":
            payload["order_event_id"] = entry_orders[str(event["symbol"])]
        add(FILLS.get(kind, kind), payload)
    orders = result.state.orders
    if orders is not None:
        for order in orders.exits:
            add("order_exit", {"position_id": order.position_id, "symbol": order.symbol,
                               "reason": order.reason})
        for entry in orders.entries:
            add("order_entry", _plain(asdict(entry)))
    point = result.point
    session.add(
        PaperEquity(portfolio_id=portfolio_id, session=day, equity=point.equity, cash=point.cash,
                    vehicle_value=point.vehicle_value, open_positions=point.open_positions,
                    catch_up=catch_up)
    )


def _plain(values: dict[str, Any]) -> dict[str, Any]:
    """JSON-ready: dates as ISO strings."""
    return {k: v.isoformat() if isinstance(v, date) else v for k, v in values.items()}


def _finish(
    session: Session,
    run: PaperRun,
    status: str,
    finished_at: datetime,
    stepped: dict[str, int],
    warnings: list[str],
    error: str | None,
) -> None:
    run.status = status
    run.finished_at = finished_at
    run.sessions_stepped = sum(stepped.values())
    run.warnings = "; ".join(warnings) or None
    run.error = error
    session.add(run)
    session.commit()
