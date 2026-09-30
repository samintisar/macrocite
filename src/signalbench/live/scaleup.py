"""The scale-up check (spec 04): did the owner follow the signals closely enough to add C$900?

Active once at least 10 managed positions are fully closed. Profitability is not a criterion.
1. Take rate = taken / (taken + expired + skipped for disagree, no_time, or other) >= 0.80.
   `price_moved` and `wide_spread` skips (the rules require them) and withdrawn signals are out.
2. Mean |fill - cdr_signal_close| / cdr_signal_close over signal-linked buys <= 0.5%.
3. Every exit alert followed by a sale of that CDR within 1 session (a late alert: within 1
   session after it was sent). `ignored` is a miss. An alert still inside its window is not.
4. No signal-linked buy above cdr_signal_close x 1.01.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

MIN_CLOSED = 10
TAKE_RATE_MIN = Decimal("0.80")
SLIPPAGE_MAX = Decimal("0.005")
LIMIT_FACTOR = Decimal("1.01")
COUNTED_SKIPS = ("disagree", "no_time", "other")


@dataclass(frozen=True)
class SignalBuy:
    fill_id: int
    price: Decimal  # the CDR fill price
    signal_close: Decimal  # the signal's cdr_signal_close


@dataclass(frozen=True)
class AlertOutcome:
    alert_id: int
    status: str  # sent | done | ignored
    deadline: date  # the first NYSE session after the alert's session (or, late, its send date)
    sold_on: date | None  # the first sale of the CDR after the alert's session, if any


@dataclass(frozen=True)
class ScaleUpResult:
    closed_managed: int
    taken: int
    declined: int  # expired + skips for disagree, no_time, other
    buys: tuple[SignalBuy, ...]
    misses: tuple[int, ...]  # exit alert ids
    violations: tuple[int, ...]  # fill ids above cdr_signal_close x 1.01

    @property
    def active(self) -> bool:
        return self.closed_managed >= MIN_CLOSED

    @property
    def take_rate(self) -> Decimal | None:
        counted = self.taken + self.declined
        return None if counted == 0 else Decimal(self.taken) / counted

    @property
    def mean_slippage(self) -> Decimal | None:
        if not self.buys:
            return None
        gaps = [abs(b.price - b.signal_close) / b.signal_close for b in self.buys]
        return sum(gaps, Decimal(0)) / len(gaps)

    @property
    def checks(self) -> dict[str, bool]:
        rate, slippage = self.take_rate, self.mean_slippage
        return {
            "take_rate": rate is not None and rate >= TAKE_RATE_MIN,
            "slippage": slippage is not None and slippage <= SLIPPAGE_MAX,
            "exit_misses": not self.misses,
            "limit_violations": not self.violations,
        }

    @property
    def passed(self) -> bool:
        return self.active and all(self.checks.values())

    def record(self, checked_at: datetime) -> dict[str, Any]:
        """The result and its inputs, JSON-safe, for risk_state.scale_up_history."""
        rate, slippage = self.take_rate, self.mean_slippage
        return {
            "checked_at": checked_at.isoformat(),
            "closed_managed": self.closed_managed,
            "active": self.active,
            "passed": self.passed,
            "checks": self.checks,
            "taken": self.taken,
            "declined": self.declined,
            "take_rate": None if rate is None else str(rate),
            "signal_buys": len(self.buys),
            "mean_slippage": None if slippage is None else str(slippage),
            "misses": list(self.misses),
            "violations": list(self.violations),
        }


def is_miss(alert: AlertOutcome, today: date) -> bool:
    """Ignored, or its window has passed without a sale in it."""
    if alert.status == "ignored":
        return True
    sold_in_time = alert.sold_on is not None and alert.sold_on <= alert.deadline
    return not sold_in_time and today > alert.deadline


def scale_up(
    *,
    closed_managed: int,
    statuses: Sequence[tuple[str, str | None]],
    buys: Sequence[SignalBuy],
    alerts: Sequence[AlertOutcome],
    today: date,
) -> ScaleUpResult:
    """`statuses`: every signal's (status, skip_reason)."""
    taken = sum(1 for status, _ in statuses if status == "taken")
    declined = sum(
        1 for status, reason in statuses
        if status == "expired" or (status == "skipped" and reason in COUNTED_SKIPS)
    )
    return ScaleUpResult(
        closed_managed=closed_managed,
        taken=taken,
        declined=declined,
        buys=tuple(buys),
        misses=tuple(a.alert_id for a in alerts if is_miss(a, today)),
        violations=tuple(b.fill_id for b in buys if b.price > b.signal_close * LIMIT_FACTOR),
    )
