"""What decide() knows about holdings. Built by the simulator here and by the ledger (spec 04)."""

from dataclasses import dataclass
from datetime import date

from signalbench.strategy.config import SetupName


@dataclass(frozen=True)
class Position:
    id: str
    symbol: str
    setup: SetupName
    sector: str
    units: float
    entry_price: float  # the fill, cost included
    entry_date: date
    stop: float
    target: float | None
    time_limit: int
    sessions_held: int  # the entry session counts as 1
    highest_close: float  # highest close since entry, entry session included


@dataclass(frozen=True)
class PendingEntry:
    """An accepted entry that has not filled yet. It holds a slot and its planned cash."""

    symbol: str
    setup: SetupName
    sector: str
    planned_cost: float


@dataclass(frozen=True)
class PortfolioState:
    cash: float
    positions: tuple[Position, ...]
    pending: tuple[PendingEntry, ...]
    equity: float  # cash + sum(units * close) at the as-of close
    peak: float
    paused: bool
    paused_since: date | None

    def holds(self, symbol: str) -> bool:
        return any(p.symbol == symbol for p in self.positions) or any(
            p.symbol == symbol for p in self.pending
        )

    def slots_used(self) -> int:
        return len(self.positions) + len(self.pending)

    def sector_count(self, sector: str) -> int:
        return sum(1 for p in self.positions if p.sector == sector) + sum(
            1 for p in self.pending if p.sector == sector
        )

    def uncommitted_cash(self) -> float:
        return self.cash - sum(p.planned_cost for p in self.pending)
