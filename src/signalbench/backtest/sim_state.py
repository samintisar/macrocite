"""`SimState` to JSON and back, exactly (spec 07).

Floats are written as Python writes them (the shortest repr that reads back to the same
float), dates as ISO strings. `state_from_json(json.loads(json.dumps(state_to_json(s))))`
equals `s`.
"""

from collections.abc import Mapping
from datetime import date
from typing import Any, cast, get_args

from signalbench.backtest.simulator import Opened, Orders, RiskState, SimState
from signalbench.strategy.config import SetupName
from signalbench.strategy.decision import EntryOrder, ExitOrder, ExitReason
from signalbench.strategy.portfolio import Position

FORMAT = 1  # bump when SimState changes shape; old saved states are then refused


def state_to_json(state: SimState) -> dict[str, Any]:
    orders = state.orders
    return {
        "format": FORMAT,
        "cash": state.cash,
        "vehicle_units": state.vehicle_units,
        "vehicle_mark": state.vehicle_mark,
        "positions": [_position_json(position) for position in state.positions],
        "opened": {key: _opened_json(meta) for key, meta in state.opened.items()},
        "orders": None if orders is None else {
            "decided_on": orders.decided_on.isoformat(),
            "exits": [
                {"position_id": o.position_id, "symbol": o.symbol, "reason": o.reason}
                for o in orders.exits
            ],
            "entries": [_entry_json(entry) for entry in orders.entries],
        },
        "risk": {
            "peak": state.risk.peak,
            "paused": state.risk.paused,
            "paused_since": _day_json(state.risk.paused_since),
            "paused_at": state.risk.paused_at,
        },
        "next_id": state.next_id,
        "sessions": state.sessions,
        "last_session": _day_json(state.last_session),
    }


def state_from_json(data: Mapping[str, Any]) -> SimState:
    if data.get("format") != FORMAT:
        raise ValueError(f"saved state has format {data.get('format')}; this code reads {FORMAT}")
    orders = data["orders"]
    risk = data["risk"]
    return SimState(
        cash=data["cash"],
        vehicle_units=data["vehicle_units"],
        vehicle_mark=data["vehicle_mark"],
        positions=tuple(_position(raw) for raw in data["positions"]),
        opened={key: _opened(raw) for key, raw in data["opened"].items()},
        orders=None if orders is None else Orders(
            decided_on=date.fromisoformat(orders["decided_on"]),
            exits=[
                ExitOrder(position_id=o["position_id"], symbol=o["symbol"], reason=_reason(o["reason"]))
                for o in orders["exits"]
            ],
            entries=[_entry(raw) for raw in orders["entries"]],
        ),
        risk=RiskState(
            peak=risk["peak"],
            paused=risk["paused"],
            paused_since=_day(risk["paused_since"]),
            paused_at=risk["paused_at"],
        ),
        next_id=data["next_id"],
        sessions=data["sessions"],
        last_session=_day(data["last_session"]),
    )


def _day_json(day: date | None) -> str | None:
    return None if day is None else day.isoformat()


def _day(text: str | None) -> date | None:
    return None if text is None else date.fromisoformat(text)


def _setup(value: str) -> SetupName:
    if value not in get_args(SetupName):
        raise ValueError(f"unknown setup {value!r} in saved state")
    return cast(SetupName, value)


def _reason(value: str) -> ExitReason:
    if value not in get_args(ExitReason):
        raise ValueError(f"unknown exit reason {value!r} in saved state")
    return cast(ExitReason, value)


def _position_json(position: Position) -> dict[str, Any]:
    return {
        "id": position.id,
        "symbol": position.symbol,
        "setup": position.setup,
        "sector": position.sector,
        "units": position.units,
        "entry_price": position.entry_price,
        "entry_date": position.entry_date.isoformat(),
        "stop": position.stop,
        "target": position.target,
        "time_limit": position.time_limit,
        "sessions_held": position.sessions_held,
        "highest_close": position.highest_close,
    }


def _position(raw: Mapping[str, Any]) -> Position:
    return Position(
        id=raw["id"],
        symbol=raw["symbol"],
        setup=_setup(raw["setup"]),
        sector=raw["sector"],
        units=raw["units"],
        entry_price=raw["entry_price"],
        entry_date=date.fromisoformat(raw["entry_date"]),
        stop=raw["stop"],
        target=raw["target"],
        time_limit=raw["time_limit"],
        sessions_held=raw["sessions_held"],
        highest_close=raw["highest_close"],
    )


def _opened_json(meta: Opened) -> dict[str, Any]:
    return {
        "signal_date": meta.signal_date.isoformat(),
        "signal_close": meta.signal_close,
        "initial_stop": meta.initial_stop,
    }


def _opened(raw: Mapping[str, Any]) -> Opened:
    return Opened(
        signal_date=date.fromisoformat(raw["signal_date"]),
        signal_close=raw["signal_close"],
        initial_stop=raw["initial_stop"],
    )


def _entry_json(entry: EntryOrder) -> dict[str, Any]:
    return {
        "symbol": entry.symbol,
        "setup": entry.setup,
        "sector": entry.sector,
        "signal_close": entry.signal_close,
        "stop": entry.stop,
        "target_r": entry.target_r,
        "time_limit": entry.time_limit,
        "units": entry.units,
        "risk_amount": entry.risk_amount,
        "catalyst": entry.catalyst,
        "rank": entry.rank,
    }


def _entry(raw: Mapping[str, Any]) -> EntryOrder:
    return EntryOrder(
        symbol=raw["symbol"],
        setup=_setup(raw["setup"]),
        sector=raw["sector"],
        signal_close=raw["signal_close"],
        stop=raw["stop"],
        target_r=raw["target_r"],
        time_limit=raw["time_limit"],
        units=raw["units"],
        risk_amount=raw["risk_amount"],
        catalyst=raw["catalyst"],
        rank=raw["rank"],
    )
