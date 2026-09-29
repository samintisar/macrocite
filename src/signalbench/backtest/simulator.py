"""Day-by-day simulation of decide() over NYSE sessions (spec 02, Simulator).

For each session t: at the open, fill the exits and then the entries decided at t-1;
at the close, mark to market, update the peak and pause state, and call decide(t).

With a cash vehicle (spec 06), idle cash waits in the regime symbol (QQQ): on a session with
fills, QQQ is sold at the open just enough to pay for the entries, or the cash left after the
fills buys QQQ at the open. The start equity is parked at the first open. Configs without a
cash vehicle never touch QQQ and behave exactly as before.
"""

from dataclasses import dataclass, field, replace
from datetime import date

from signalbench.strategy.config import CashVehicle, SetupName, StrategyConfig
from signalbench.strategy.decide import decide
from signalbench.strategy.decision import EntryOrder, ExitOrder, ExitReason, SkipReason
from signalbench.strategy.entries import MIN_POSITION_FRACTION
from signalbench.strategy.market_view import AsOfView, MarketView
from signalbench.strategy.portfolio import PortfolioState, Position
from signalbench.strategy.readings import ReadingsView

Event = dict[str, object]
DUST = 1e-9  # cash this close to zero after the day's fills is float residue, not a switch


@dataclass(frozen=True)
class TradeRecord:
    position_id: str
    symbol: str
    setup: SetupName
    sector: str
    signal_date: date
    entry_date: date
    exit_signal_date: date
    exit_date: date
    signal_close: float
    entry_price: float
    exit_price: float
    initial_stop: float
    units: float
    r: float  # (exit fill - entry fill) / (signal close - initial stop): planned risk, costs in
    pnl: float
    reason: ExitReason
    sessions_held: int


@dataclass(frozen=True)
class EquityPoint:
    date: date
    equity: float
    cash: float
    open_positions: int
    vehicle_value: float = 0.0  # the cash vehicle at the close (spec 06); 0 without one


@dataclass(frozen=True)
class OpenPositionRecord:
    """A position still open when the run ends. It is not a trade and has no R."""

    position_id: str
    symbol: str
    setup: SetupName
    sector: str
    signal_date: date
    entry_date: date
    entry_price: float
    units: float
    stop: float
    sessions_held: int
    last_close: float
    unrealized_pnl: float  # units * (last close - entry fill), before any exit cost


@dataclass(frozen=True)
class SimulationResult:
    start: date
    end: date
    equity_curve: list[EquityPoint]
    trades: list[TradeRecord]
    events: list[Event]
    open_positions: list[Position]
    open_at_end: list[OpenPositionRecord] = field(default_factory=list)


@dataclass(frozen=True)
class RiskState:
    peak: float
    paused: bool
    paused_since: date | None
    paused_at: int | None  # session index of the pause, for the auto-resume count


def step_risk(
    state: RiskState, equity: float, index: int, day: date, config: StrategyConfig
) -> tuple[RiskState, str | None]:
    """Close-of-session pause logic.

    While paused: after `auto_resume_sessions` sessions, resume and reset peak := equity
    (the backtest stand-in for the owner's /resume). Otherwise the peak tracks the highest
    equity, and equity below (1 - pause_drawdown) x peak pauses new entries.
    """
    if state.paused and state.paused_at is not None:
        if index - state.paused_at >= config.auto_resume_sessions:
            return RiskState(peak=equity, paused=False, paused_since=None, paused_at=None), "resume"
        return replace(state, peak=max(state.peak, equity)), None
    peak = max(state.peak, equity)
    if equity < (1.0 - config.pause_drawdown) * peak:
        return RiskState(peak=peak, paused=True, paused_since=day, paused_at=index), "pause"
    return replace(state, peak=peak), None


@dataclass(frozen=True)
class _Opened:
    signal_date: date
    signal_close: float
    initial_stop: float

    @property
    def planned_risk(self) -> float:
        """Per-unit risk planned at the signal: signal close - stop (the unit of R)."""
        return self.signal_close - self.initial_stop


@dataclass(frozen=True)
class _Orders:
    """What decide() returned at a close, to fill at the next open."""

    decided_on: date
    exits: list[ExitOrder]
    entries: list[EntryOrder]


def entry_skip(entry: EntryOrder, open_price: float, cash: float, config: StrategyConfig) -> SkipReason | None:
    """Open-time skip rules: gap_up, gap_below_stop, and no cash left.

    `cash` is what the entry can spend: cash, plus the cash vehicle at the open net of its
    selling cost (spec 06).
    """
    if open_price > entry.signal_close * (1.0 + config.gap_up_limit):
        return "gap_up"
    if open_price <= entry.stop:
        return "gap_below_stop"
    if cash <= 0.0:
        return "no_cash"
    return None


def simulate(
    market: MarketView,
    readings: ReadingsView,
    config: StrategyConfig,
    start: date,
    end: date,
) -> SimulationResult:
    sessions = market.sessions_between(start, end)
    if not sessions:
        raise ValueError(f"no sessions between {start} and {end}")
    cash = config.start_equity
    vehicle = config.cash_vehicle
    vehicle_units = 0.0
    positions: dict[str, Position] = {}
    opened: dict[str, _Opened] = {}
    risk = RiskState(peak=config.start_equity, paused=False, paused_since=None, paused_at=None)
    orders: _Orders | None = None
    trades: list[TradeRecord] = []
    events: list[Event] = []
    curve: list[EquityPoint] = []
    next_id = 1

    for index, day in enumerate(sessions):
        view = market.at(day)
        vehicle_open, vehicle_mark = _vehicle_prices(view, day, vehicle)
        sleeve = 0.0  # what the vehicle would pay today, net of its selling cost
        if vehicle is not None and vehicle_open is not None:
            sleeve = vehicle_units * vehicle_open * (1.0 - vehicle.cost_per_side)
        filled = index == 0  # the first open parks the start equity

        # Open: exits first, then entries, both decided at the previous close.
        deferred: list[ExitOrder] = []  # exits with no bar today, retried at the next open
        if orders is not None:
            for order in orders.exits:
                position = positions[order.position_id]
                snap = view.snapshot(position.symbol)
                if snap is None or snap.date != day:
                    events.append(_event(day, "exit_deferred", position_id=position.id))
                    deferred.append(order)
                    continue
                fill = snap.open * (1.0 - config.cost_per_side)
                cash += position.units * fill
                filled = True
                meta = opened.pop(position.id)
                del positions[position.id]
                trade = TradeRecord(
                    position_id=position.id,
                    symbol=position.symbol,
                    setup=position.setup,
                    sector=position.sector,
                    signal_date=meta.signal_date,
                    entry_date=position.entry_date,
                    exit_signal_date=orders.decided_on,
                    exit_date=day,
                    signal_close=meta.signal_close,
                    entry_price=position.entry_price,
                    exit_price=fill,
                    initial_stop=meta.initial_stop,
                    units=position.units,
                    r=(fill - position.entry_price) / meta.planned_risk,
                    pnl=position.units * (fill - position.entry_price),
                    reason=order.reason,
                    sessions_held=position.sessions_held,
                )
                trades.append(trade)
                events.append(
                    _event(day, "exit", position_id=position.id, symbol=position.symbol,
                           reason=order.reason, price=fill, r=trade.r)
                )
            open_equity = (
                cash + _held_value_at_open(view, day, positions) + vehicle_units * vehicle_mark
            )
            for entry in orders.entries:
                snap = view.snapshot(entry.symbol)
                skip = "no_bar" if snap is None or snap.date != day else None
                if snap is not None and skip is None:
                    skip = entry_skip(entry, snap.open, cash + sleeve, config)
                fill = 0.0 if snap is None else snap.open * (1.0 + config.cost_per_side)
                units = 0.0 if skip is not None else min(entry.units, (cash + sleeve) / fill)
                if skip is None and units * fill < MIN_POSITION_FRACTION * open_equity:
                    skip = "no_cash"  # trimmed to dust by earlier fills in this batch
                if snap is None or skip is not None:
                    events.append(
                        _event(day, "skip", symbol=entry.symbol, setup=entry.setup, reason=skip)
                    )
                    continue
                cash -= units * fill  # below zero only until the vehicle is sold, below
                filled = True
                position_id = f"P{next_id:05d}"
                next_id += 1
                meta = _Opened(orders.decided_on, entry.signal_close, entry.stop)
                target = None
                if entry.target_r is not None:
                    target = fill + entry.target_r * meta.planned_risk
                positions[position_id] = Position(
                    id=position_id,
                    symbol=entry.symbol,
                    setup=entry.setup,
                    sector=entry.sector,
                    units=units,
                    entry_price=fill,
                    entry_date=day,
                    stop=entry.stop,
                    target=target,
                    time_limit=entry.time_limit,
                    sessions_held=0,
                    highest_close=0.0,
                )
                opened[position_id] = meta
                events.append(
                    _event(day, "entry", position_id=position_id, symbol=entry.symbol,
                           setup=entry.setup, units=units, price=fill, trimmed=units < entry.units)
                )

        # Open, after the fills: sell the vehicle to cover the entries, or park the cash left.
        if vehicle is not None and vehicle_open is not None and filled:
            cash, vehicle_units = _switch(day, cash, vehicle_units, vehicle_open, vehicle, events)

        # Close: mark to market, count the session, update the peak and pause state.
        value = 0.0
        for position_id, position in list(positions.items()):
            snap = view.snapshot(position.symbol)
            close = position.entry_price if snap is None else snap.close
            value += position.units * close
            positions[position_id] = replace(
                position,
                sessions_held=position.sessions_held + 1,
                highest_close=max(position.highest_close, close),
            )
        vehicle_value = 0.0
        spendable = cash  # what decide() may size against: cash, plus the vehicle net of cost
        if vehicle is not None:
            benchmark = view.benchmark()
            vehicle_value = 0.0 if benchmark is None else vehicle_units * benchmark.close
            spendable = cash + vehicle_value * (1.0 - vehicle.cost_per_side)
        equity = cash + value + vehicle_value
        risk, change = step_risk(risk, equity, index, day, config)
        if change is not None:
            events.append(_event(day, change, equity=equity, peak=risk.peak))
        curve.append(EquityPoint(day, equity, cash, len(positions), vehicle_value))

        state = PortfolioState(
            cash=spendable,
            positions=tuple(positions.values()),
            pending=(),
            equity=equity,
            peak=risk.peak,
            paused=risk.paused,
            paused_since=risk.paused_since,
        )
        decision = decide(day, market, readings, state, config)
        for update in decision.stop_updates:
            positions[update.position_id] = replace(
                positions[update.position_id], stop=update.new_stop
            )
            events.append(
                _event(day, "stop_update", position_id=update.position_id,
                       old_stop=update.old_stop, new_stop=update.new_stop)
            )
        for skipped in decision.skips:
            events.append(
                _event(day, "skip", symbol=skipped.symbol, setup=skipped.setup, reason=skipped.reason)
            )
        carried = {order.position_id for order in deferred}
        exits = deferred + [e for e in decision.exits if e.position_id not in carried]
        orders = _Orders(day, sorted(exits, key=lambda e: e.position_id), decision.entries)

    return SimulationResult(
        start=sessions[0],
        end=sessions[-1],
        equity_curve=curve,
        trades=trades,
        events=events,
        open_positions=list(positions.values()),
        open_at_end=_open_at_end(market, sessions[-1], positions, opened),
    )


def _open_at_end(
    market: MarketView, end: date, positions: dict[str, Position], opened: dict[str, _Opened]
) -> list[OpenPositionRecord]:
    """Positions still held at the last close, marked the way the equity curve marks them."""
    view = market.at(end)
    records: list[OpenPositionRecord] = []
    for position in positions.values():
        snap = view.snapshot(position.symbol)
        close = position.entry_price if snap is None else snap.close
        records.append(
            OpenPositionRecord(
                position_id=position.id,
                symbol=position.symbol,
                setup=position.setup,
                sector=position.sector,
                signal_date=opened[position.id].signal_date,
                entry_date=position.entry_date,
                entry_price=position.entry_price,
                units=position.units,
                stop=position.stop,
                sessions_held=position.sessions_held,
                last_close=close,
                unrealized_pnl=position.units * (close - position.entry_price),
            )
        )
    return records


def _vehicle_prices(
    view: AsOfView, day: date, vehicle: CashVehicle | None
) -> tuple[float | None, float]:
    """The vehicle's open today (None when it has no bar today, so it does not trade) and its
    mark at the open (today's open, else the last close; 0 before its first bar)."""
    if vehicle is None:
        return None, 0.0
    snap = view.benchmark()
    if snap is None:
        return None, 0.0
    if snap.date != day:
        return None, snap.close
    return snap.open, snap.open


def _switch(
    day: date,
    cash: float,
    units: float,
    price: float,
    vehicle: CashVehicle,
    events: list[Event],
) -> tuple[float, float]:
    """Settle the day's cash against the vehicle at the open (spec 06).

    Negative cash (entries paid beyond the cash on hand) sells just enough units to cover it;
    positive cash buys units. Fills mirror a stock's: price x (1 - cost) when selling and
    price x (1 + cost) when buying, so the cost is charged only on the amount that moves.
    Returns the new (cash, units).
    """
    cost = vehicle.cost_per_side
    if cash < -DUST:
        sold = min(-cash / (price * (1.0 - cost)), units)
        amount = sold * price
        events.append(
            _event(day, "vehicle_sell", symbol=vehicle.symbol, units=sold, price=price,
                   amount=amount, cost=amount * cost)
        )
        return _settled(cash + amount * (1.0 - cost)), units - sold
    if cash > DUST:
        bought = cash / (price * (1.0 + cost))
        amount = bought * price
        events.append(
            _event(day, "vehicle_buy", symbol=vehicle.symbol, units=bought, price=price,
                   amount=amount, cost=amount * cost)
        )
        return _settled(cash - amount * (1.0 + cost)), units + bought
    return cash, units


def _settled(cash: float) -> float:
    """Cash after a switch: float residue within DUST of zero is zero."""
    return 0.0 if abs(cash) <= DUST else cash


def _held_value_at_open(view: AsOfView, day: date, positions: dict[str, Position]) -> float:
    """Held units marked at today's open (the last close when a symbol has no bar today)."""
    value = 0.0
    for position in positions.values():
        snap = view.snapshot(position.symbol)
        if snap is None:
            mark = position.entry_price
        else:
            mark = snap.open if snap.date == day else snap.close
        value += position.units * mark
    return value


def _event(day: date, kind: str, **fields: object) -> Event:
    return {"date": day.isoformat(), "event": kind, **fields}
