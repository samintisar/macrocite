"""Where and how big an entry is placed on the CDR (spec 05, Live CDR sizing).

Pure: Decimal in, Decimal out, no database. The entry session is the next Cboe Canada session
after the signal (the XTSE calendar as the proxy), and the signal expires at its close.

The CDR close is the CDR reference: the US signal close x the CDR/US ratio of the last date
the CDR traded (the scan's; the same basis as the equity mark), so a stale zero-volume close
never sizes an order. The size risks `risk_pct` of equity between the CDR close and the CDR
stop (the US stop's distance, applied to the CDR close), capped at equity / max_positions and
at the cash not already promised to pending signals. Whole units are preferred:
- floor: the whole units below the target that fit the cap at the limit price, when at least 1
  and at least 75% of the target;
- ceil: else the whole units above it, when they risk at most 2.5% of equity and fit the cap
  at the limit price;
- fractional: else the target itself, as a market order for a dollar amount.
Whole units are a limit order at the CDR close + 1%, so the cap is checked at that price; a
fractional order is placed only when the price is at or below that same level. A pending
signal holds units x the limit of the cash (`committed`).
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, Decimal
from typing import Literal

from signalbench.live.book import OrderType, signal_stop
from signalbench.market.calendar import Sessions

SizeRule = Literal["floor", "ceil", "fractional"]
WHOLE_SHARE = Decimal("0.75")  # floor is used when it is at least 75% of the target
CEIL_RISK = Decimal("0.025")  # ceil is used when it risks at most 2.5% of equity
LIMIT_FACTOR = Decimal("1.01")  # the limit (and the fractional price bound): 1% over the close
CENT = Decimal("0.01")
UNIT_STEP = Decimal("0.000001")  # units are stored at 6 decimals
ZERO = Decimal(0)


@dataclass(frozen=True)
class EntryWindow:
    session: date  # the Cboe Canada session to place the order in
    expires_at: datetime  # that session's close
    note: str | None  # set when Cboe Canada is closed on the next NYSE session


@dataclass(frozen=True)
class CdrSize:
    rule: SizeRule
    order_type: OrderType  # limit for whole units, market for a fractional order
    units: Decimal
    cdr_close: Decimal
    cdr_stop: Decimal  # cdr_close x (1 - stop_pct)
    stop_pct: Decimal  # (us_signal_close - us_stop) / us_signal_close
    limit: Decimal  # cdr_close x 1.01, rounded down to the cent

    @property
    def risk(self) -> Decimal:
        """The planned risk: units x (CDR close - CDR stop)."""
        return self.units * (self.cdr_close - self.cdr_stop)

    @property
    def cost(self) -> Decimal:
        return self.units * self.cdr_close

    @property
    def committed(self) -> Decimal:
        """The cash the pending signal holds: units x the limit."""
        return committed_cash(self.units, self.cdr_close)


def limit_price(cdr_close: Decimal) -> Decimal:
    """The limit (and the fractional price bound): the CDR close + 1%, rounded down to the cent."""
    return (cdr_close * LIMIT_FACTOR).quantize(CENT, rounding=ROUND_DOWN)


def committed_cash(units: Decimal, cdr_close: Decimal) -> Decimal:
    """The cash a pending signal holds: its units at the limit price."""
    return units * limit_price(cdr_close)


def _day(day: date) -> str:
    return f"{day:%a} {day.day:02d} {day:%b}"


def entry_window(as_of: date, nyse: Sessions, cboe: Sessions) -> EntryWindow:
    """The next Cboe Canada session after the signal session `as_of`. When Cboe Canada is
    closed on the next NYSE session, the entry and the expiry move to its next session."""
    session = cboe.next_sessions(as_of, 1)[0]
    [(_, close)] = cboe.session_closes(session, session)
    us_next = nyse.next_sessions(as_of, 1)[0]
    note = None
    if session > us_next:
        note = f"Cboe Canada is closed on {_day(us_next)}: place it on {_day(session)}."
    return EntryWindow(session=session, expires_at=close, note=note)


def size_cdr(
    *,
    us_signal_close: Decimal,
    us_stop: Decimal,
    cdr_close: Decimal,
    equity: Decimal,
    uncommitted_cash: Decimal,
    risk_pct: Decimal,
    max_positions: int,
) -> CdrSize | None:
    """The entry's CDR order, or None when nothing fits (no uncommitted cash)."""
    stop_pct, cdr_stop = signal_stop(us_signal_close, us_stop, cdr_close)
    per_unit = cdr_close - cdr_stop
    cap = min(equity / max_positions, max(uncommitted_cash, ZERO))
    target = min(risk_pct * equity / per_unit, cap / cdr_close)
    if target <= 0:
        return None
    limit = limit_price(cdr_close)

    def order(rule: SizeRule, units: Decimal) -> CdrSize:
        return CdrSize(
            rule=rule, order_type="market" if rule == "fractional" else "limit", units=units,
            cdr_close=cdr_close, cdr_stop=cdr_stop, stop_pct=stop_pct, limit=limit,
        )

    whole = min(
        target.to_integral_value(rounding=ROUND_FLOOR),
        (cap / limit).to_integral_value(rounding=ROUND_FLOOR),  # whole units fit at the limit
    )
    if whole >= 1 and whole >= WHOLE_SHARE * target:
        return order("floor", whole)
    up = target.to_integral_value(rounding=ROUND_CEILING)
    if up * per_unit <= CEIL_RISK * equity and up * limit <= cap:
        return order("ceil", up)
    units = target.quantize(UNIT_STEP, rounding=ROUND_DOWN)
    return order("fractional", units) if units > 0 else None
