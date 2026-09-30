"""Average cost (ACB), superficial losses, and CDR splits for one CDR symbol (spec 04).

Pure: Decimal in, Decimal out, no database and no clock. `replay()` walks one symbol's fills
and CDR splits in order (by date; on a date, a split before the fills, which are already in
post-split units; fills in the order they were recorded) and keeps the CRA average cost:

- buy: acb += quantity x price + fee; units += quantity
- sell: acb_sold = acb x quantity / units; gain = quantity x price - fee - acb_sold
- split with ratio r: units x= r; the total ACB is unchanged, so the ACB per unit is / r

A sale at a loss is a superficial loss in part when the same CDR symbol is bought from 30 days
before to 30 days after it and still held at the end of the 30th day:
denied = loss x min(quantity sold, bought in the window, held at day 30) / quantity sold, all
counted on the sale's scale. The denied amount is added to the ACB of the units held (when the
sale leaves none, it waits in the ACB for the re-buy). Nothing is rounded here.

Not tax advice: a record-keeping aid that follows the CRA's published method.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

Side = Literal["buy", "sell"]
WINDOW = timedelta(days=30)
ZERO = Decimal(0)


class AcbError(ValueError):
    """The fills cannot be replayed (a sale of more units than are held)."""


@dataclass(frozen=True)
class Trade:
    """A fill as the ACB sees it. `id` orders fills recorded on the same date."""

    id: int
    day: date
    side: Side
    quantity: Decimal
    price: Decimal
    fee: Decimal = ZERO


@dataclass(frozen=True)
class SplitEvent:
    """A CDR split: units held at the start of `day` (the ex-date) are multiplied by `ratio`."""

    id: int
    day: date
    ratio: Decimal


@dataclass(frozen=True)
class Holding:
    """Units and total ACB after one event."""

    event: Trade | SplitEvent
    units: Decimal
    acb: Decimal


@dataclass(frozen=True)
class Disposition:
    trade: Trade
    proceeds: Decimal  # quantity x price
    acb: Decimal  # the ACB of the units sold
    gain: Decimal  # proceeds - fee - acb, before any superficial-loss adjustment
    denied: Decimal  # the superficial loss added to ACB (0 when none)
    provisional: bool  # a loss whose 30-day window had not passed on `today`

    @property
    def allowed(self) -> Decimal:
        """The gain, or the loss that remains once the superficial part is denied."""
        return self.gain + self.denied


@dataclass(frozen=True)
class SplitRecord:
    split: SplitEvent
    units_before: Decimal
    acb: Decimal  # unchanged by the split
    units_after: Decimal

    @property
    def per_unit_before(self) -> Decimal | None:
        return self.acb / self.units_before if self.units_before else None

    @property
    def per_unit_after(self) -> Decimal | None:
        return self.acb / self.units_after if self.units_after else None


@dataclass(frozen=True)
class AcbBook:
    units: Decimal
    acb: Decimal
    steps: tuple[Holding, ...]  # one per event, in replay order
    dispositions: tuple[Disposition, ...]
    splits: tuple[SplitRecord, ...]

    @property
    def per_unit(self) -> Decimal | None:
        return self.acb / self.units if self.units else None


def ordered(trades: Sequence[Trade], splits: Sequence[SplitEvent]) -> list[Trade | SplitEvent]:
    """Replay order: by date; on a date, splits first (a fill on the ex-date is post-split),
    then fills by id."""
    events: list[Trade | SplitEvent] = [*trades, *splits]
    return sorted(events, key=lambda e: (e.day, isinstance(e, Trade), e.id))


def units_through(events: Sequence[Trade | SplitEvent], day: date) -> Decimal:
    """Units held at the end of `day`, on that day's scale."""
    units = ZERO
    for event in events:
        if event.day > day:
            break
        if isinstance(event, SplitEvent):
            units *= event.ratio
        elif event.side == "buy":
            units += event.quantity
        else:
            units -= event.quantity
    return units


def split_factor(splits: Sequence[SplitEvent], start: date, end: date) -> Decimal:
    """Units on `start`'s scale times this are on `end`'s scale (start <= end): the product of
    the ratios with an ex-date after `start`, on or before `end`."""
    factor = Decimal(1)
    for split in splits:
        if start < split.day <= end:
            factor *= split.ratio
    return factor


def _on_scale_of(quantity: Decimal, day: date, target: date, splits: Sequence[SplitEvent]) -> Decimal:
    if day <= target:
        return quantity * split_factor(splits, day, target)
    return quantity / split_factor(splits, target, day)


def denied_loss(
    sale: Trade, loss: Decimal, events: Sequence[Trade | SplitEvent], splits: Sequence[SplitEvent]
) -> Decimal:
    """The superficial part of `loss` (> 0) on `sale` (spec 04, ACB and superficial losses)."""
    first, last = sale.day - WINDOW, sale.day + WINDOW
    bought = sum(
        (
            _on_scale_of(e.quantity, e.day, sale.day, splits)
            for e in events
            if isinstance(e, Trade) and e.side == "buy" and first <= e.day <= last
        ),
        ZERO,
    )
    held = units_through(events, last) / split_factor(splits, sale.day, last)
    counted = min(sale.quantity, bought, held)
    return loss * counted / sale.quantity if counted > 0 else ZERO


def replay(trades: Sequence[Trade], splits: Sequence[SplitEvent], today: date) -> AcbBook:
    """Every event in order, with the ACB, the dispositions, and the splits' ACB per unit.
    Raises AcbError when a sale is larger than the units held."""
    events = ordered(trades, splits)
    units = acb = ZERO
    steps: list[Holding] = []
    dispositions: list[Disposition] = []
    records: list[SplitRecord] = []
    for event in events:
        if isinstance(event, SplitEvent):
            records.append(SplitRecord(event, units, acb, units * event.ratio))
            units *= event.ratio
        elif event.side == "buy":
            acb += event.quantity * event.price + event.fee
            units += event.quantity
        else:
            if event.quantity > units:
                raise AcbError(
                    f"a sale of {event.quantity.normalize():f} on {event.day} is more than the "
                    f"{units.normalize():f} units held"
                )
            sold = acb * event.quantity / units
            proceeds = event.quantity * event.price
            gain = proceeds - event.fee - sold
            acb -= sold
            units -= event.quantity
            denied = denied_loss(event, -gain, events, splits) if gain < 0 else ZERO
            acb += denied
            provisional = gain < 0 and today <= event.day + WINDOW
            dispositions.append(Disposition(event, proceeds, sold, gain, denied, provisional))
        steps.append(Holding(event, units, acb))
    return AcbBook(units, acb, tuple(steps), tuple(dispositions), tuple(records))
