"""The weekly summary, `crypto paper status`, and the 12-month review (spec 11).

The review gives on track or off track from three checks fixed in the spec: every day
recorded with at most `max_late_days` late, no replay mismatch, and the worst drawdown under
`max_drawdown`. Profit and the BTC comparison are information only.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlmodel import Session, col, select

from signalbench.backtest.crypto_runner import BTC
from signalbench.db.models import PaperDay, PaperEvent, PaperPortfolio, PaperRun
from signalbench.ingest.kraken import find_crypto_ticker
from signalbench.market.bars import adjusted_bars
from signalbench.paper.paper_file import PaperError, PaperFile

FAILED = ("failed", "mismatch")


@dataclass(frozen=True)
class PaperSummary:
    start_equity: float
    last_day: date | None  # the last saved day; None before the first
    equity: float  # at the last saved day
    btc_equity: float | None  # the start equity in BTC bought at the start day's close
    open_positions: list[str]
    closed_trades: int
    worst_drawdown: float
    late_days: int
    failed_runs_7d: int
    days_to_review: int
    last_ok_run: datetime | None


@dataclass(frozen=True)
class Review:
    on_track: bool
    lines: list[str]  # one per check, then ON TRACK or OFF TRACK


def close_deadline(day: date) -> datetime:
    """After this, `day`'s row is late: 24 hours after its candle closed (00:00 UTC, day + 1)."""
    return datetime.combine(day + timedelta(days=2), time(0, 0), tzinfo=UTC)


def worst_drawdown(equities: Sequence[float]) -> float:
    peak = worst = 0.0
    for equity in equities:
        peak = max(peak, equity)
        if peak > 0.0:
            worst = max(worst, 1.0 - equity / peak)
    return worst


def paper_summary(
    session: Session,
    portfolio: PaperPortfolio,
    paper: PaperFile,
    start_equity: float,
    now: datetime,
) -> PaperSummary:
    days = _days(session, portfolio)
    last = days[-1] if days else None
    closed = session.exec(
        select(PaperEvent.id).where(
            PaperEvent.portfolio_id == portfolio.id, PaperEvent.kind == "exit"
        )
    ).all()
    failed = session.exec(
        select(PaperRun.id).where(
            col(PaperRun.started_at) >= now - timedelta(days=7),
            col(PaperRun.status).in_(FAILED),
        )
    ).all()
    last_ok = session.exec(
        select(PaperRun.finished_at)
        .where(PaperRun.status == "ok")
        .order_by(col(PaperRun.id).desc())
    ).first()
    return PaperSummary(
        start_equity=start_equity,
        last_day=None if last is None else last.day,
        equity=start_equity if last is None else last.equity,
        btc_equity=None if last is None else _btc_equity(session, paper.start, last.day, start_equity),
        open_positions=[] if last is None else _positions(last.state),
        closed_trades=len(closed),
        worst_drawdown=worst_drawdown([start_equity, *(day.equity for day in days)]),
        late_days=sum(_late(day) for day in days),
        failed_runs_7d=len(failed),
        days_to_review=(paper.review_date - now.astimezone(UTC).date()).days,
        last_ok_run=last_ok,
    )


def summary_text(paper: PaperFile, summary: PaperSummary) -> str:
    to = "" if summary.last_day is None else f" to {summary.last_day.isoformat()}"
    btc = "n/a" if summary.btc_equity is None else f"{summary.btc_equity:,.2f}"
    held = "; ".join(summary.open_positions) or "none"
    return "\n".join(
        [
            f"PAPER {paper.name}: weekly summary{to}",
            (
                f"Equity {summary.equity:,.2f} (start {summary.start_equity:,.2f}); "
                f"BTC buy-and-hold {btc}"
            ),
            f"Open: {held}",
            f"Closed trades: {summary.closed_trades}",
            (
                f"Worst drawdown: {summary.worst_drawdown:.1%} "
                f"(limit {paper.review.max_drawdown:.0%})"
            ),
            f"Late days: {summary.late_days} (limit {paper.review.max_late_days})",
            f"Failed runs in the last 7 days: {summary.failed_runs_7d}",
            f"Review on {paper.review_date.isoformat()} ({summary.days_to_review} days)",
        ]
    )


def paper_review(
    session: Session,
    portfolio: PaperPortfolio,
    paper: PaperFile,
    start_equity: float,
    today: date,
) -> Review:
    """The three checks over every day from the start to the day before the review date."""
    if today < paper.review_date:
        raise PaperError(f"The review is on {paper.review_date.isoformat()}; today is {today}.")
    end = paper.review_date - timedelta(days=1)
    days = [day for day in _days(session, portfolio) if paper.start <= day.day <= end]
    have = {day.day for day in days}
    missing = [
        paper.start + timedelta(days=i)
        for i in range((end - paper.start).days + 1)
        if paper.start + timedelta(days=i) not in have
    ]
    late = sum(_late(day) for day in days)
    mismatches = session.exec(
        select(PaperRun.id).where(
            PaperRun.status == "mismatch", col(PaperRun.started_at) <= close_deadline(end)
        )
    ).all()
    drawdown = worst_drawdown([start_equity, *(day.equity for day in days)])
    rules = paper.review
    on_time = not missing and late <= rules.max_late_days
    matches = not mismatches
    shallow = drawdown < rules.max_drawdown
    first_missing = f", first {missing[0].isoformat()}" if missing else ""
    lines = [
        (
            f"1. On time: {_verdict(on_time)}: {len(missing)} missing days{first_missing}, "
            f"{late} late (limit {rules.max_late_days})"
        ),
        f"2. Matches the backtest code: {_verdict(matches)}: {len(mismatches)} replay mismatches",
        f"3. Drawdown: {_verdict(shallow)}: worst {drawdown:.1%} (limit {rules.max_drawdown:.0%})",
    ]
    on_track = on_time and matches and shallow
    return Review(on_track, [*lines, "ON TRACK" if on_track else "OFF TRACK"])


def _days(session: Session, portfolio: PaperPortfolio) -> list[PaperDay]:
    return list(
        session.exec(
            select(PaperDay)
            .where(PaperDay.portfolio_id == portfolio.id)
            .order_by(col(PaperDay.day))
        ).all()
    )


def _late(day: PaperDay) -> bool:
    return day.recorded_at > close_deadline(day.day)


def _verdict(passed: bool) -> str:
    return "pass" if passed else "FAIL"


def _btc_equity(session: Session, start: date, last: date, start_equity: float) -> float | None:
    ticker = find_crypto_ticker(session, BTC)
    if ticker is None:
        return None
    closes = {bar.date: bar.close for bar in adjusted_bars(session, ticker.id, start=start, end=last)}
    if start not in closes or last not in closes:
        return None
    return start_equity * closes[last] / closes[start]


def _positions(state: dict[str, Any]) -> list[str]:
    return [
        f"{p['symbol']} {p['units']:.6f} since {p['entry_date']} at {p['entry_price']:,.2f}, "
        f"stop {p['stop']:,.2f}"
        for p in state.get("positions", [])
    ]
