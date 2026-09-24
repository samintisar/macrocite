"""The pre-registered Jev filter decision (spec 03, Filter decision steps 1-3). Pure.

Nothing here is tuned after the fact: the theta grid, the windows, and the 10-trade minimum are
fixed by the spec. Fit and confirmation results must never be used to change them.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from statistics import fmean
from typing import Literal

THETA_GRID = (0.5, 0.6, 0.7, 0.8, 0.9)
MIN_BLOCKED = 10
FIT_START = date(2016, 1, 1)
FIT_END = date(2022, 12, 31)
CONFIRM_START = date(2023, 1, 1)
FilterMode = Literal["on", "information_only"]


@dataclass(frozen=True)
class TradeRow:
    """One closed trade from a stored jev-off v1 run."""

    setup: str
    symbol: str
    signal_date: date
    r: float


@dataclass(frozen=True)
class ScoredTrade:
    setup: str
    symbol: str
    signal_date: date
    r: float
    max_p_negative: float | None  # None: no reading in the window, so never blocked


@dataclass(frozen=True)
class Split:
    theta: float
    kept: int
    blocked: int
    mean_r_kept: float | None
    mean_r_blocked: float | None

    @property
    def difference(self) -> float | None:
        """Mean R of kept trades minus mean R of blocked trades."""
        if self.mean_r_kept is None or self.mean_r_blocked is None:
            return None
        return self.mean_r_kept - self.mean_r_blocked


@dataclass(frozen=True)
class FilterDecision:
    mode: FilterMode
    theta: float | None  # the fitted theta (None when no theta was eligible)
    reason: str
    fit_trades: int
    confirm_trades: int
    fit: tuple[Split, ...]  # one row per theta in THETA_GRID
    confirm: Split | None


def score_trades(
    trades: Sequence[TradeRow], max_p_negative: Callable[[str, date], float | None]
) -> list[ScoredTrade]:
    """Step 1: the max p_negative over the symbol's documents whose legal close falls in the
    10 sessions ending on the signal date (`JevReadingsView.max_p_negative`)."""
    return [
        ScoredTrade(t.setup, t.symbol, t.signal_date, t.r, max_p_negative(t.symbol, t.signal_date))
        for t in trades
    ]


def _is_blocked(trade: ScoredTrade, theta: float) -> bool:
    return trade.max_p_negative is not None and trade.max_p_negative >= theta


def split(trades: Sequence[ScoredTrade], theta: float) -> Split:
    blocked = [t.r for t in trades if _is_blocked(t, theta)]
    kept = [t.r for t in trades if not _is_blocked(t, theta)]
    return Split(
        theta=theta,
        kept=len(kept),
        blocked=len(blocked),
        mean_r_kept=fmean(kept) if kept else None,
        mean_r_blocked=fmean(blocked) if blocked else None,
    )


def eligible(row: Split) -> bool:
    """At least MIN_BLOCKED blocked trades, and at least one kept trade to compare against."""
    return row.blocked >= MIN_BLOCKED and row.kept > 0


def _confirmation(check: Split) -> tuple[FilterMode, str]:
    """Step 3: ON only with at least MIN_BLOCKED blocked trades whose mean R is below the kept."""
    if check.blocked < MIN_BLOCKED:
        return "information_only", (
            f"only {check.blocked} confirmation trades blocked (need {MIN_BLOCKED})"
        )
    if check.mean_r_kept is None or check.mean_r_blocked is None:
        return "information_only", "no kept confirmation trades to compare against"
    if check.mean_r_blocked >= check.mean_r_kept:
        return "information_only", (
            f"blocked mean R {check.mean_r_blocked:.3f} is not below kept mean R "
            f"{check.mean_r_kept:.3f} in the confirmation window"
        )
    return "on", (
        f"{check.blocked} confirmation trades blocked with mean R {check.mean_r_blocked:.3f}, "
        f"below the kept mean R {check.mean_r_kept:.3f}"
    )


def decide_filter(trades: Sequence[ScoredTrade]) -> FilterDecision:
    """Step 2 fits theta on 2016-2022 signals; step 3 confirms it on signals from 2023 on."""
    fit_trades = [t for t in trades if FIT_START <= t.signal_date <= FIT_END]
    confirm_trades = [t for t in trades if t.signal_date >= CONFIRM_START]
    fit = tuple(split(fit_trades, theta) for theta in THETA_GRID)
    eligible_rows = [row for row in fit if eligible(row)]
    if not eligible_rows:
        return FilterDecision(
            mode="information_only",
            theta=None,
            reason=f"no theta blocks at least {MIN_BLOCKED} fit-window trades",
            fit_trades=len(fit_trades),
            confirm_trades=len(confirm_trades),
            fit=fit,
            confirm=None,
        )
    # max() keeps the first maximum, so a tie goes to the lower theta.
    best = max(eligible_rows, key=lambda row: row.difference or 0.0)
    check = split(confirm_trades, best.theta)
    mode, reason = _confirmation(check)
    return FilterDecision(
        mode=mode,
        theta=best.theta,
        reason=reason,
        fit_trades=len(fit_trades),
        confirm_trades=len(confirm_trades),
        fit=fit,
        confirm=check,
    )
