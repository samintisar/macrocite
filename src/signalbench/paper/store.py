"""Saved paper days (spec 11): read them back, compare them with a replay, append new ones.

Rows are never updated or deleted. Values are compared after the same JSON round-trip they
went through when saved, so a float or a date string reads back exactly.
"""

import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlmodel import Session, col, select

from signalbench.backtest.sim_state import state_to_json
from signalbench.db.models import PaperDay, PaperEvent, PaperPortfolio
from signalbench.paper.replay import ReplayDay

MAX_SHOWN = 200  # characters of each value in a mismatch message


@dataclass(frozen=True)
class SavedDay:
    day: date
    equity: float
    cash: float
    state: dict[str, Any]
    events: list[dict[str, Any]]


def as_json(value: Any) -> Any:
    """`value` as it reads back from a JSON column."""
    return json.loads(json.dumps(value))


def row_values(day: ReplayDay) -> SavedDay:
    """A replayed day as it is saved."""
    return SavedDay(
        day=day.day,
        equity=day.point.equity,
        cash=day.point.cash,
        state=as_json(state_to_json(day.state)),
        events=[as_json(event) for event in day.events],
    )


def find_portfolio(session: Session, name: str) -> PaperPortfolio | None:
    return session.exec(select(PaperPortfolio).where(PaperPortfolio.name == name)).first()


def load_saved(session: Session, portfolio_id: int) -> list[SavedDay]:
    days = session.exec(
        select(PaperDay)
        .where(PaperDay.portfolio_id == portfolio_id)
        .order_by(col(PaperDay.day))
    ).all()
    events = session.exec(
        select(PaperEvent)
        .where(PaperEvent.portfolio_id == portfolio_id)
        .order_by(col(PaperEvent.id))
    ).all()
    by_day: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_day[event.day].append(event.payload)
    return [
        SavedDay(day.day, day.equity, day.cash, day.state, by_day.get(day.day, []))
        for day in days
    ]


def first_difference(saved: Sequence[SavedDay], replayed: Sequence[ReplayDay]) -> str | None:
    """None when every saved day comes out of the replay exactly as saved; otherwise the first
    difference, starting with its day."""
    for old, day in zip(saved, replayed, strict=False):
        new = row_values(day)
        if old.day != new.day:
            return f"{old.day.isoformat()}: replays as {new.day.isoformat()}"
        for name in ("equity", "cash", "state", "events"):
            before, after = getattr(old, name), getattr(new, name)
            if before != after:
                return (
                    f"{old.day.isoformat()}: {name} differs "
                    f"(saved {_shown(before)}, replayed {_shown(after)})"
                )
    if len(saved) > len(replayed):
        return f"{saved[len(replayed)].day.isoformat()}: saved but not replayed"
    return None


def append_days(
    session: Session,
    *,
    portfolio_id: int,
    run_id: int,
    days: Sequence[ReplayDay],
    now: datetime,
) -> None:
    """Add the rows of `days`; the caller commits them in one transaction."""
    for day in days:
        values = row_values(day)
        session.add(
            PaperDay(
                portfolio_id=portfolio_id, day=day.day, equity=values.equity,
                cash=values.cash, state=values.state, recorded_at=now, run_id=run_id,
            )
        )
        for event in values.events:
            session.add(
                PaperEvent(
                    portfolio_id=portfolio_id, day=day.day, kind=str(event["event"]),
                    payload=event, recorded_at=now, run_id=run_id,
                )
            )


def _shown(value: Any) -> str:
    text = json.dumps(value, default=str)
    return text if len(text) <= MAX_SHOWN else f"{text[: MAX_SHOWN - 3]}..."
