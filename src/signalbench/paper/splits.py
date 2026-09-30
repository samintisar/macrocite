"""Stock splits and a paper portfolio's saved state (spec 07 changelog, 2026-09-29).

A saved state holds prices (entry price, stop, target, highest close, a pending entry's signal
close and stop, the cash vehicle's mark) on the scale of the night it was saved. yfinance
divides a stock's whole history by each split, and `ingest prices` refetches the whole history
when older closes change, so after a split the stored bars are all on the new scale while the
saved state is not. Before stepping, the runner rescales the state by every split since its last
session: prices divided by the ratio, units multiplied by it, so values and R are unchanged.

Each saved price is also checked against the stored close it came from: once the known splits
are applied, the two must agree up to later dividends (which lower older adjusted closes a
little). Anything else, such as a split yfinance does not list or one the stored prices do not
show yet, refuses the portfolio instead of stepping on two scales.

That check allows for years of dividends, which a 5-for-4 split fits in, so each stepped session
also saves the raw close (split-adjusted, not dividend-adjusted) of every held or pending symbol
(`Mark`), and the next run compares it with the stored raw close for the same session: a
dividend leaves it unchanged, and any move beyond MARK_TOLERANCE that the known splits do not
explain refuses the portfolio.
"""

import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from signalbench.backtest.simulator import Opened, Orders, SimState
from signalbench.ingest.prices import Split
from signalbench.paper.start import PaperRefusedError
from signalbench.strategy.market_view import MarketView

# Stored close / (saved price x the known splits' factor). Dividends after the save lower the
# stored adjusted close by a few percent; a missed 3-for-2 split is 1.5 or 0.67.
SCALE_BAND = (0.75, 1.02)
# Stored raw close / saved raw close, for the same session, once the known splits are applied.
# A split changes it by 1/ratio (5-for-4: 0.8); a dividend does not change it at all.
MARK_TOLERANCE = 0.03


@dataclass(frozen=True)
class Reference:
    """A price in the saved state, and the stored close it was read from."""

    symbol: str
    day: date
    price: float
    vehicle: bool  # the cash vehicle, marked on the benchmark series


@dataclass(frozen=True)
class Mark:
    """A symbol's raw close on `day`, saved with the state that night, and the stored raw close
    for that session tonight (None when none is stored)."""

    symbol: str
    day: date
    saved: float
    stored: float | None


def references(state: SimState, vehicle: str | None) -> list[Reference]:
    """Every symbol whose prices the state holds: open positions (their signal close), pending
    entries (theirs), and the cash vehicle while it holds units (its last mark)."""
    refs = [
        Reference(p.symbol, state.opened[p.id].signal_date, state.opened[p.id].signal_close, False)
        for p in state.positions
    ]
    if state.orders is not None:
        refs += [
            Reference(e.symbol, state.orders.decided_on, e.signal_close, False)
            for e in state.orders.entries
        ]
    if vehicle is not None and state.vehicle_units > 0.0 and state.last_session is not None:
        refs.append(Reference(vehicle, state.last_session, state.vehicle_mark, True))
    return refs


def rescale(state: SimState, symbol: str, ratio: float, vehicle: str | None) -> SimState:
    """The state on the scale after a `ratio`-for-1 split of `symbol`."""
    positions = tuple(
        replace(p, units=p.units * ratio, entry_price=p.entry_price / ratio, stop=p.stop / ratio,
                target=None if p.target is None else p.target / ratio,
                highest_close=p.highest_close / ratio)
        if p.symbol == symbol else p
        for p in state.positions
    )
    changed = {p.id for p in state.positions if p.symbol == symbol}
    opened = {
        key: Opened(meta.signal_date, meta.signal_close / ratio, meta.initial_stop / ratio)
        if key in changed else meta
        for key, meta in state.opened.items()
    }
    orders = state.orders
    if orders is not None:
        orders = Orders(orders.decided_on, orders.exits, [
            replace(e, signal_close=e.signal_close / ratio, stop=e.stop / ratio,
                    units=e.units * ratio)  # risk_amount = units x (close - stop): unchanged
            if e.symbol == symbol else e
            for e in orders.entries
        ])
    units, mark = state.vehicle_units, state.vehicle_mark
    if symbol == vehicle:
        units, mark = units * ratio, mark / ratio
    return replace(state, positions=positions, opened=opened, orders=orders,
                   vehicle_units=units, vehicle_mark=mark)


def adjust_for_splits(
    state: SimState,
    *,
    market: MarketView,
    known: Mapping[str, list[Split]],
    applied: Collection[tuple[str, date]],
    target: date,
    vehicle: str | None,
    where: str,
    marks: Sequence[Mark] = (),
) -> tuple[SimState, list[dict[str, Any]]]:
    """Apply every known split with an ex-date after the state's last session, up to `target`,
    that is not in `applied` (symbol, ex-date: logged by an earlier run). A split dated after
    the target is applied too when the stored prices already show it (a run during its
    ex-date's session, whose target is the session before). Returns the new state and one
    `split_adjust` payload per split. Raises PaperRefusedError when a saved price or a saved
    raw close (`marks`) and the stored prices disagree after the splits."""
    last = state.last_session
    events: list[dict[str, Any]] = []
    if last is None:
        return state, events
    refs = references(state, vehicle)
    for symbol in sorted({ref.symbol for ref in refs}):
        pending = sorted(
            (s for s in known.get(symbol, [])
             if last < s.ex_date and (symbol, s.ex_date) not in applied),
            key=lambda s: s.ex_date,
        )
        due = [s for s in pending if s.ex_date <= target]
        problem = _disagreement(symbol, refs, marks, due, market, last, where)
        if (
            problem is not None
            and len(due) < len(pending)
            and _disagreement(symbol, refs, marks, pending, market, last, where) is None
        ):
            due, problem = pending, None  # the stored prices already show the later split
        if problem is not None:
            raise PaperRefusedError(problem)
        for split in due:
            state = rescale(state, symbol, split.ratio, vehicle)
            events.append(
                {"symbol": symbol, "ex_date": split.ex_date.isoformat(), "ratio": split.ratio}
            )
    return state, events


def _disagreement(
    symbol: str,
    refs: Sequence[Reference],
    marks: Sequence[Mark],
    due: Sequence[Split],
    market: MarketView,
    last: date,
    where: str,
) -> str | None:
    """Why `symbol`'s stored prices and saved state are on different scales once `due` is
    applied, or None when they agree."""
    factor = math.prod(1.0 / s.ratio for s in due)
    splits = ", ".join(f"{s.ratio:g}-for-1 on {s.ex_date}" for s in due) or "none"
    not_stepped = ("Not stepped: its prices and saved state are on different scales (check the "
                   "split in the stored prices and rerun).")
    for ref in (r for r in refs if r.symbol == symbol):
        view = market.at(ref.day)
        snap = view.benchmark() if ref.vehicle else view.snapshot(symbol)
        if snap is None or ref.price <= 0.0:
            continue
        observed = snap.close / ref.price
        if not SCALE_BAND[0] <= observed / factor <= SCALE_BAND[1]:
            return (
                f"{where}: {symbol} stored close on {ref.day} is {observed:.4g}x the "
                f"{ref.price:.4f} in the saved state, and the splits since {last} ({splits}) "
                f"explain {factor:.4g}x. {not_stepped}"
            )
    for mark in (m for m in marks if m.symbol == symbol and m.saved > 0.0):
        if mark.stored is None:
            return f"{where}: {symbol} has no stored close on {mark.day} any more. {not_stepped}"
        observed = mark.stored / mark.saved
        if abs(observed / factor - 1.0) > MARK_TOLERANCE:
            return (
                f"{where}: {symbol} stored raw close on {mark.day} is {observed:.4g}x the "
                f"{mark.saved:.4f} close saved with the state that night, and the splits since "
                f"{last} ({splits}) explain {factor:.4g}x. {not_stepped}"
            )
    return None
