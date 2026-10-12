"""The paper job's Telegram texts (spec 11). Every one starts with PAPER, so none can be read
as a live trade alert."""

from signalbench.backtest.simulator import Event
from signalbench.paper.replay import ReplayDay


def order_texts(version: str, day: ReplayDay) -> list[str]:
    """The orders decided at this day's close, to fill at the next open."""
    orders = day.state.orders
    if orders is None:
        return []
    exits = [
        f"PAPER {version}: SELL {order.symbol} at the next open ({order.reason})."
        for order in orders.exits
    ]
    entries = [
        f"PAPER {version}: BUY {entry.symbol} at the next open ({entry.setup}). "
        f"Signal close {entry.signal_close:,.2f}, stop {entry.stop:,.2f}."
        for entry in orders.entries
    ]
    return [*exits, *entries]


def fill_texts(day: ReplayDay, cost_per_side: float) -> list[str]:
    """Each entry ordered at the previous close: filled at this day's open, or why not."""
    if day.filling is None:
        return []
    texts: list[str] = []
    for entry in day.filling.entries:
        filled = _first(day.events, "entry", entry.symbol)
        if filled is None:
            skipped = _first(day.events, "skip", entry.symbol)
            reason = "no fill" if skipped is None else str(skipped["reason"])
            texts.append(f"PAPER not filled: BUY {entry.symbol} ({reason}).")
            continue
        price, units = _number(filled, "price"), _number(filled, "units")
        texts.append(
            f"PAPER filled: BUY {entry.symbol} {units:.6f} at {price:,.2f} "
            f"(open {price / (1.0 + cost_per_side):,.2f} + {cost_per_side:.1%}), "
            f"{units * price:,.2f} spent."
        )
    return texts


def closed_texts(day: ReplayDay) -> list[str]:
    return [
        f"PAPER closed: {trade.symbol} {trade.r:+.2f}R "
        f"({trade.exit_price / trade.entry_price - 1.0:+.1%}), "
        f"held {trade.sessions_held} days ({trade.reason})."
        for trade in day.trades
    ]


def day_texts(version: str, day: ReplayDay, cost_per_side: float) -> list[str]:
    """In the order they happened: exits and entries at the open, then the close's orders."""
    return [*closed_texts(day), *fill_texts(day, cost_per_side), *order_texts(version, day)]


def alert_text(error: str) -> str:
    return f"PAPER ALERT: {error}"


def _first(events: list[Event], kind: str, symbol: str) -> Event | None:
    """The first `kind` event for `symbol`: open-time events come before the close's."""
    return next((e for e in events if e["event"] == kind and e.get("symbol") == symbol), None)


def _number(event: Event, key: str) -> float:
    value = event[key]
    if not isinstance(value, int | float):
        raise TypeError(f"{event['event']} event has a non-numeric {key}: {value!r}")
    return float(value)
