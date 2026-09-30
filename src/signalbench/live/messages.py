"""The evening scan's Telegram messages (spec 05, Messages): pure text templates.

Money is shown to the cent, rounded half up: C$ for the CDR, US$ for the US levels that
decide. A minus sign is U+2212, as in the spec. Button data is short (`b:12`) because Telegram
caps it at 64 bytes; live/bot.py reads it back.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

from signalbench.live.book import AlertReason, OrderType
from signalbench.live.messenger import Button, Buttons

MINUS = "−"
CENT = Decimal("0.01")
TENTH = Decimal("0.1")
SPREAD_LIMIT = Decimal("0.005")  # the survey's live spread limit (spec 05 changelog: 0.5%)
SKIP_REASONS = (
    ("disagree", "Disagree"),
    ("no_time", "No time"),
    ("price_moved", "Price moved >1%"),
    ("wide_spread", "Spread too wide"),
    ("other", "Other"),
)


def _money(value: Decimal, currency: str, sign: bool = False) -> str:
    q = value.quantize(CENT, rounding=ROUND_HALF_UP)
    prefix = MINUS if q < 0 else ("+" if sign and q > 0 else "")
    return f"{prefix}{currency}{abs(q):,}"


def cad(value: Decimal) -> str:
    """C$38.40, −C$2.60."""
    return _money(value, "C$")


def signed_cad(value: Decimal) -> str:
    """+C$1.20, −C$2.60, C$0.00."""
    return _money(value, "C$", sign=True)


def usd(value: Decimal) -> str:
    """US$1,830.00."""
    return _money(value, "US$")


def pct(fraction: Decimal) -> str:
    """A signed percentage to one decimal: −6.0%, +2.1%, 0.0%."""
    q = (fraction * 100).quantize(TENTH, rounding=ROUND_HALF_UP)
    prefix = MINUS if q < 0 else ("+" if q > 0 else "")
    return f"{prefix}{abs(q)}%"


def units_text(units: Decimal) -> str:
    """1 unit, 3 units, 0.868055 units."""
    shown = f"{units.normalize():f}"
    return f"{shown} unit" if units == 1 else f"{shown} units"


def ratio_text(ratio: Decimal) -> str:
    """New units per old unit as a split is named: 10-for-1, 1-for-10, 3-for-2."""
    fraction = Fraction(ratio).limit_denominator(1000)
    return f"{fraction.numerator}-for-{fraction.denominator}"


@dataclass(frozen=True)
class EntryText:
    us_symbol: str
    cdr_symbol: str
    company: str
    cdr_close: Decimal
    cdr_stop: Decimal
    stop_pct: Decimal
    units: Decimal
    order_type: OrderType
    limit: Decimal
    risk: Decimal
    why: str
    note: str | None  # the entry moved by a Cboe Canada holiday


def entry_message(e: EntryText) -> str:
    stop = cad(e.cdr_stop)
    if e.order_type == "limit":
        size = (
            f"Size {units_text(e.units)} (~{cad(e.units * e.cdr_close)}) · risk {cad(e.risk)} · "
            f"LIMIT {cad(e.limit)}"
        )
    else:
        size = (
            f"Size {cad(e.units * e.cdr_close)} ({units_text(e.units)}) · risk {cad(e.risk)} · "
            f"MARKET (fractional): only place it if the price is ≤ {cad(e.limit)}"
        )
    spread = (SPREAD_LIMIT * 100).quantize(TENTH)
    lines = [
        f"🟢 BUY {e.us_symbol} (CDR {e.cdr_symbol}) — Breakout · {e.company}",
        (
            f"Signal {cad(e.cdr_close)} · Stop {stop} ({pct(-e.stop_pct)}) · Trailing stop, "
            "no target, no time limit"
        ),
        size,
        f"Skip if price > {cad(e.limit)}, price ≤ the stop {stop}, or bid/ask spread > {spread}%",
    ]
    if e.note is not None:
        lines.append(e.note)
    lines.append(f"Why: {e.why}")
    return "\n".join(lines)


def entry_buttons(signal_id: int) -> Buttons:
    return ((Button("✅ I bought", f"b:{signal_id}"), Button("⏭ Skip", f"s:{signal_id}")),)


def skip_buttons(signal_id: int) -> Buttons:
    """The skip reasons (spec 04's skip_reason values)."""
    buttons = [Button(text, f"k:{signal_id}:{reason}") for reason, text in SKIP_REASONS]
    return (tuple(buttons[:3]), tuple(buttons[3:]))


@dataclass(frozen=True)
class RaiseText:
    us_symbol: str
    cdr_symbol: str
    old_cdr: Decimal
    new_cdr: Decimal
    old_us: Decimal
    new_us: Decimal
    late_for: date | None  # the session a catch-up raise was decided at


def raise_message(r: RaiseText) -> str:
    text = (
        f"⬆️ {r.us_symbol} (CDR {r.cdr_symbol}) stop raised {cad(r.old_cdr)} → {cad(r.new_cdr)} "
        f"({usd(r.old_us)} → {usd(r.new_us)}) · still holding, no action"
    )
    return text if r.late_for is None else f"{text} (late — for {r.late_for})"


@dataclass(frozen=True)
class SplitStopText:
    symbol: str  # the US symbol for a US split, the CDR for a CDR split
    ratio: Decimal
    ex_date: date
    old: Decimal
    new: Decimal
    currency: str  # US$ or C$


def split_stop_message(s: SplitStopText) -> str:
    return (
        f"ℹ️ {s.symbol} split {ratio_text(s.ratio)} (ex-date {s.ex_date}): stop "
        f"{_money(s.old, s.currency)} → {_money(s.new, s.currency)} · no action"
    )


@dataclass(frozen=True)
class CdrSplitText:
    cdr_symbol: str
    ratio: Decimal
    ex_date: date
    units_before: Decimal
    units_after: Decimal
    per_unit_before: Decimal | None
    per_unit_after: Decimal | None
    withdrawn: tuple[int, ...]  # sent signals whose prices no longer apply


def cdr_split_message(s: CdrSplitText) -> str:
    head = f"ℹ️ {s.cdr_symbol} split {ratio_text(s.ratio)} (ex-date {s.ex_date})"
    if s.per_unit_before is not None and s.per_unit_after is not None:
        after = f"{s.units_after.normalize():f}"
        head += (
            f": {units_text(s.units_before)} → {after}, ACB per unit "
            f"{cad(s.per_unit_before)} → {cad(s.per_unit_after)} (the total ACB is unchanged) · "
            f"check that Wealthsimple shows {after} units"
        )
    lines = [f"{head} · no action"]
    lines += [f"Signal {i} withdrawn: its prices no longer apply." for i in s.withdrawn]
    return "\n".join(lines)


@dataclass(frozen=True)
class ExitText:
    us_symbol: str
    cdr_symbol: str
    reason: AlertReason
    us_close: Decimal
    us_stop: Decimal  # the stop in force during the session
    cdr_mark: Decimal | None
    cdr_stop: Decimal
    earnings_on: date | None
    sessions_held: int
    unrealized: Decimal
    unrealized_pct: Decimal
    late_after: date | None  # a catch-up or late alert: the session it was due after


def exit_message(x: ExitText) -> str:
    head = f"🔴 SELL {x.us_symbol} (CDR {x.cdr_symbol}) — "
    if x.reason == "stop":
        mark = "n/a" if x.cdr_mark is None else f"~{cad(x.cdr_mark)}"
        head += (
            f"stop hit (US close {usd(x.us_close)} ≤ stop {usd(x.us_stop)} · CDR {mark} vs "
            f"stop {cad(x.cdr_stop)})"
        )
    else:
        head += f"earnings on {x.earnings_on}, sell before them"
    if x.late_after is not None:
        head += f" (late — should have been sent after {x.late_after})"
    held = "1 session" if x.sessions_held == 1 else f"{x.sessions_held} sessions"
    return "\n".join([
        head,
        f"Held {held} · unrealized {signed_cad(x.unrealized)} ({pct(x.unrealized_pct)})",
        "Sell at the open.",
    ])


def exit_buttons(alert_id: int) -> Buttons:
    return ((Button("✅ Sold", f"x:{alert_id}"), Button("Ignore", f"i:{alert_id}")),)


def failure_message(step: int, name: str, error: str) -> str:
    return f"⚠️ Scan failed at step {step} ({name}): {error}"
