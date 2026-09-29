"""`signalbench paper run` (spec 07, Commands): step every portfolio to the last complete session.

Each stepped session is one transaction: the portfolio's saved state, the session's events,
and its equity row. Orders decided at a close are written that night, before the next open;
their fills the next night carry the order event's id. Rows written after the next session's
open (a missed night, or a late run) are marked `catch_up`.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlmodel import Session, col, select

from signalbench.backtest.runner import (
    MarketInputs,
    check_series_current,
    last_complete_session,
    load_market_inputs,
    setups_for_run,
)
from signalbench.backtest.sim_state import state_from_json, state_to_json
from signalbench.backtest.simulator import SimState, StepResult, step
from signalbench.db.models import PaperEquity, PaperEvent, PaperPortfolio, PaperRun
from signalbench.ingest.cdr import CdrEntry
from signalbench.ingest.prices import Split
from signalbench.market.calendar import HISTORY_START, Sessions
from signalbench.paper.portfolios import paper_setup
from signalbench.paper.report import report_due, write_weekly_report
from signalbench.paper.splits import SplitFetcher, adjust_for_splits, references
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
    splits: SplitFetcher,
    reports_dir: Path,
    echo: Callable[[str], None],
) -> RunOutcome:
    """The nightly job. `lock` yields False when another run holds it: nothing is done.
    `ingest` refreshes prices (and the liquidity flags) and returns what failed. `splits` lists
    a symbol's splits after a date, for the symbols a portfolio holds (paper/splits.py); a
    failed lookup is printed and the stored prices alone are checked. `clock` must
    return timezone-aware times. The first run of an ISO week writes the weekly report into
    `reports_dir`, after stepping (a portfolio refused for its config does not stop it)."""
    with lock as held:
        if not held:
            echo("Another paper run holds the lock; nothing to do.")
            return RunOutcome(status="locked")
        run = PaperRun(started_at=clock(), status="running")
        session.add(run)
        session.commit()
        session.refresh(run)
        stepped: dict[str, int] = {}
        report: Path | None = None
        try:
            refused = _step_all(
                session, run, repo, universe, calendar, clock, ingest, _cached(splits, echo), echo,
                stepped,
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
            _finish(session, run, "failed", clock(), stepped, message)
            return RunOutcome("failed", run.id, run.target_session, stepped, message, report)
        _finish(session, run, "ok", clock(), stepped, None)
        return RunOutcome("ok", run.id, run.target_session, stepped, None, report)


def _step_all(
    session: Session,
    run: PaperRun,
    repo: Path,
    universe: list[CdrEntry],
    calendar: Sessions,
    clock: Callable[[], datetime],
    ingest: Callable[[Session], list[str]],
    splits: SplitFetcher,
    echo: Callable[[str], None],
    stepped: dict[str, int],
) -> list[str]:
    """Step every portfolio that is behind the target. Returns the portfolios refused for a
    changed config; they are not stepped, and the others still are."""
    portfolios = session.exec(select(PaperPortfolio).order_by(col(PaperPortfolio.id))).all()
    if not portfolios:
        raise PaperRefusedError("No paper portfolios. Run `signalbench paper start` first.")
    target = last_complete_session(calendar, clock())
    run.target_session = target
    session.add(run)
    session.commit()
    behind = [p for p in portfolios if p.last_session is None or p.last_session < target]
    if not behind:
        echo(f"Every portfolio has stepped {target.isoformat()}; nothing to do.")
        return []
    failed = ingest(session)
    if failed:
        echo(f"ingest: {len(failed)} failed ({', '.join(failed)}); stepping on the stored prices")
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
            inputs[symbol] = load_market_inputs(session, universe, symbol, target)
            check_series_current(inputs[symbol], symbol, target)
        market = _market(inputs[symbol], config, calendar, target)
        _step_portfolio(session, portfolio, config, market, calendar, target, clock, splits, stepped)
        echo(f"{portfolio.name}: {stepped.get(portfolio.name, 0)} sessions to {target.isoformat()}")
    return refused


def _cached(fetch: SplitFetcher, echo: Callable[[str], None]) -> SplitFetcher:
    """One lookup per symbol and date in a run. A failed lookup is printed and counts as no
    known split: the stored-price check still refuses a state on another scale."""
    seen: dict[tuple[str, date], list[Split]] = {}

    def splits(symbol: str, since: date) -> list[Split]:
        if (symbol, since) not in seen:
            try:
                seen[symbol, since] = fetch(symbol, since)
            except Exception as error:  # noqa: BLE001  # the price check below still guards
                echo(f"splits {symbol}: {type(error).__name__}: {error}; "
                     "checking the stored prices only")
                seen[symbol, since] = []
        return seen[symbol, since]

    return splits


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
    transaction (state, events, and equity row together). Splits since the last session
    rescale the saved state first, logged in the first session's transaction."""
    assert portfolio.id is not None
    state = state_from_json(portfolio.state)
    last = portfolio.last_session
    days = [d for d in calendar.sessions_between(portfolio.started_on, target) if last is None or d > last]
    if not days:
        return
    state, adjusted = _adjust_for_splits(session, portfolio, state, config, market, target, splits)
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
    if state.last_session is None:
        return state, []
    vehicle = None if config.cash_vehicle is None else config.cash_vehicle.symbol
    symbols = sorted({ref.symbol for ref in references(state, vehicle)})
    known = {symbol: splits(symbol, state.last_session) for symbol in symbols}
    logged = session.exec(
        select(PaperEvent).where(
            PaperEvent.portfolio_id == portfolio.id, PaperEvent.kind == "split_adjust"
        )
    ).all()
    applied = {(str(e.payload["symbol"]), date.fromisoformat(str(e.payload["ex_date"]))) for e in logged}
    return adjust_for_splits(state, market=market, known=known, applied=applied, target=target,
                             vehicle=vehicle, where=portfolio.name)


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
    error: str | None,
) -> None:
    run.status = status
    run.finished_at = finished_at
    run.sessions_stepped = sum(stepped.values())
    run.error = error
    session.add(run)
    session.commit()
