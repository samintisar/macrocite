"""The output of decide(): orders for the next open, stop updates, and skips."""

from dataclasses import dataclass, field
from typing import Literal

from signalbench.strategy.config import SetupName

ExitReason = Literal["stop", "earnings", "target", "time"]
SkipReason = Literal[
    "gap_up",
    "gap_below_stop",
    "no_slot",
    "sector_cap",
    "blocked",
    "paused",
    "earnings_blackout",
    "regime",
    "held",
    "no_cash",
    "no_bar",
]


@dataclass(frozen=True)
class EntryOrder:
    symbol: str
    setup: SetupName
    sector: str
    signal_close: float
    stop: float
    target_r: float | None  # None: no target (Breakout)
    time_limit: int
    units: float
    risk_amount: float  # units * (signal_close - stop), after the caps
    catalyst: bool
    rank: int  # 1 = first in the evening's ranking


@dataclass(frozen=True)
class ExitOrder:
    position_id: str
    symbol: str
    reason: ExitReason


@dataclass(frozen=True)
class StopUpdate:
    position_id: str
    old_stop: float
    new_stop: float


@dataclass(frozen=True)
class Skip:
    symbol: str
    setup: SetupName
    reason: SkipReason


@dataclass(frozen=True)
class Decision:
    entries: list[EntryOrder] = field(default_factory=list)
    exits: list[ExitOrder] = field(default_factory=list)
    stop_updates: list[StopUpdate] = field(default_factory=list)
    skips: list[Skip] = field(default_factory=list)
