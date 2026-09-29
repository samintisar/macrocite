"""The weekly paper report, `reports/paper/<date>-weekly.md` (spec 07, Weekly report).

Written on the first run of each ISO week. Every number is since the portfolio's start, next
to QQQ bought at the close of its first session and held over the same sessions, measured as
the backtest measures a run (from the first close), and next to QQQ's price-only return (raw
closes), since a paper position earns the price return only after entry. It also lists the
commits the week's runs used and their warnings. Information only until the judging rule is
met.
"""

import re
import uuid
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from statistics import fmean
from zoneinfo import ZoneInfo

from sqlmodel import Session, col, select

from signalbench.backtest.benchmarks import (
    BenchmarkStats,
    benchmark_stats,
    buy_and_hold,
)
from signalbench.backtest.metrics import max_drawdown, sharpe
from signalbench.db.models import (
    PaperEquity,
    PaperEvent,
    PaperPortfolio,
    PaperRun,
    Price,
    Ticker,
)
from signalbench.market.bars import adjust, adjusted_bars

NEW_YORK = ZoneInfo("America/New_York")
BENCHMARK = "QQQ"  # spec 07: QQQ buy-and-hold is the benchmark
REPORT_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})-weekly\.md$")
PRICE_ONLY = (
    "Paper portfolios earn the price return only after entry (no dividends); compare with the "
    "price-only QQQ line."
)
JUDGING = (
    "Information only. A portfolio is judged once it has 12 months since its start and at "
    "least 30 closed trades, whichever is later (spec 07, Judging); until then these numbers "
    "decide nothing."
)


@dataclass(frozen=True)
class PortfolioSummary:
    name: str
    started_on: date
    last_session: date | None
    sessions: int
    equity: float | None  # None (and the three below) before the first stepped session
    total_return: float | None
    sharpe: float | None
    max_drawdown: float | None
    closed_trades: int
    mean_r: float | None
    win_rate: float | None
    average_hold: float | None  # sessions, the entry session included
    open_positions: int
    vehicle_share: float | None  # average share of equity in QQQ; None without a vehicle
    catch_up_sessions: int  # in the week the report covers
    benchmark: BenchmarkStats | None  # total return: adjusted closes, dividends included
    price_only: BenchmarkStats | None  # raw closes: the price return a paper position earns


def previous_week(today: date) -> tuple[date, date]:
    """Monday and Sunday of the ISO week before `today`'s."""
    monday = today - timedelta(days=today.weekday())
    return monday - timedelta(days=7), monday - timedelta(days=1)


def report_due(reports_dir: Path, today: date) -> bool:
    """True unless a weekly report is already dated in `today`'s ISO week."""
    monday = today - timedelta(days=today.weekday())
    for path in reports_dir.glob("*-weekly.md") if reports_dir.exists() else []:
        match = REPORT_NAME.match(path.name)
        if match and monday <= date.fromisoformat(match.group(1)) <= monday + timedelta(days=6):
            return False
    return True


def summarize(session: Session, portfolio: PaperPortfolio, week: tuple[date, date]) -> PortfolioSummary:
    rows = session.exec(
        select(PaperEquity).where(PaperEquity.portfolio_id == portfolio.id)
        .order_by(col(PaperEquity.session))
    ).all()
    fills = session.exec(
        select(PaperEvent).where(PaperEvent.portfolio_id == portfolio.id, PaperEvent.kind == "fill_exit")
    ).all()
    rs = [float(fill.payload["trade"]["r"]) for fill in fills]
    holds = [int(fill.payload["trade"]["sessions_held"]) for fill in fills]
    values = [row.equity for row in rows]
    sessions = [row.session for row in rows]
    shares = [row.vehicle_value / row.equity for row in rows if row.equity > 0.0]
    uses_vehicle = any(row.vehicle_value > 0.0 for row in rows)
    return PortfolioSummary(
        name=portfolio.name,
        started_on=portfolio.started_on,
        last_session=portfolio.last_session,
        sessions=len(rows),
        equity=values[-1] if values else None,
        total_return=values[-1] / values[0] - 1.0 if values else None,
        sharpe=sharpe(values) if values else None,
        max_drawdown=max_drawdown(values) if values else None,
        closed_trades=len(rs),
        mean_r=fmean(rs) if rs else None,
        win_rate=sum(1 for r in rs if r > 0.0) / len(rs) if rs else None,
        average_hold=fmean(holds) if holds else None,
        open_positions=rows[-1].open_positions if rows else 0,
        vehicle_share=fmean(shares) if uses_vehicle and shares else None,
        catch_up_sessions=sum(1 for row in rows if row.catch_up and week[0] <= row.session <= week[1]),
        benchmark=_benchmark(session, sessions),
        price_only=_benchmark(session, sessions, price_only=True),
    )


def _benchmark(
    session: Session, sessions: list[date], price_only: bool = False
) -> BenchmarkStats | None:
    """QQQ buy-and-hold over `sessions`: on adjusted closes (total return), or on raw closes
    (split-adjusted only: the price return, as a paper position earns after entry)."""
    if not sessions:
        return None
    ticker = session.exec(select(Ticker).where(Ticker.symbol == BENCHMARK)).first()
    if ticker is None:
        return None
    if price_only:
        bars = [replace(adjust(row), close=float(row.close))
                for row in _prices(session, ticker.id, sessions[-1])]
    else:
        bars = adjusted_bars(session, ticker.id, end=sessions[-1])
    name = f"{BENCHMARK} buy-and-hold{' (price only)' if price_only else ''}"
    return benchmark_stats(name, buy_and_hold(bars, sessions), sessions)


def _prices(session: Session, ticker_id: uuid.UUID, end: date) -> list[Price]:
    return list(session.exec(
        select(Price).where(Price.ticker_id == ticker_id, col(Price.date) <= end)
        .order_by(col(Price.date))
    ).all())


def run_warnings(session: Session, week: tuple[date, date]) -> list[tuple[date, str]]:
    """The not-critical failures (price ingest, earnings calendar) of the week's runs."""
    runs = session.exec(
        select(PaperRun).where(col(PaperRun.warnings).is_not(None)).order_by(col(PaperRun.started_at))
    ).all()
    found = [(run.started_at.astimezone(NEW_YORK).date(), run.warnings) for run in runs]
    return [(day, text) for day, text in found if text and week[0] <= day <= week[1]]


def failed_runs(session: Session, week: tuple[date, date]) -> int:
    runs = session.exec(select(PaperRun).where(PaperRun.status == "failed")).all()
    return sum(1 for run in runs if week[0] <= run.started_at.astimezone(NEW_YORK).date() <= week[1])


@dataclass(frozen=True)
class CodeUse:
    """The commits the week's runs used, in order of first use, and the last one used before."""

    week: list[str]
    before: str | None


def code_use(session: Session, week: tuple[date, date]) -> CodeUse:
    runs = session.exec(
        select(PaperRun).where(col(PaperRun.git_sha).is_not(None)).order_by(col(PaperRun.started_at))
    ).all()
    used: list[str] = []
    before: str | None = None
    for run in runs:
        day = run.started_at.astimezone(NEW_YORK).date()
        if day < week[0]:
            before = run.git_sha
        elif day <= week[1] and run.git_sha is not None and run.git_sha not in used:
            used.append(run.git_sha)
    return CodeUse(week=used, before=before)


def _code_line(week: tuple[date, date], code: CodeUse) -> str:
    span = f"Code used from {week[0].isoformat()} to {week[1].isoformat()}"
    if not code.week:
        return f"{span}: no run recorded a commit."
    line = f"{span}: {', '.join(f'`{sha[:12]}`' for sha in code.week)}."
    if code.before is not None and code.before != code.week[0]:
        return f"{line} CHANGED: the last run before used `{code.before[:12]}`."
    if len(code.week) > 1:
        return f"{line} CHANGED during the week."
    return line


def _pct(value: float | None, signed: bool = False) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.2%}" if signed else f"{value:.1%}"


def _number(value: float | None, spec: str) -> str:
    return "n/a" if value is None else format(value, spec)


def render_weekly_report(
    written_on: date,
    week: tuple[date, date],
    failed: int,
    summaries: list[PortfolioSummary],
    code: CodeUse,
    warnings: list[tuple[date, str]],
) -> str:
    lines = [
        f"# Paper trading: weekly report, {written_on.isoformat()}",
        "",
        JUDGING,
        "",
        f"Failed runs from {week[0].isoformat()} to {week[1].isoformat()}: {failed}.",
        "",
        _code_line(week, code),
        "",
        f"Run warnings from {week[0].isoformat()} to {week[1].isoformat()}: {len(warnings)}.",
        "",
    ]
    if warnings:
        lines += [f"- {day.isoformat()}: {text}" for day, text in warnings] + [""]
    lines += [
        "## Equity since the start",
        "",
        PRICE_ONLY,
        "",
        (
            "| Portfolio | Start | Last session | Sessions | Equity | Total return | Sharpe "
            "| Max drawdown | QQQ total return | QQQ price-only return | QQQ Sharpe "
            "| QQQ max drawdown |"
        ),
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for s in summaries:
        last = "not stepped yet" if s.last_session is None else s.last_session.isoformat()
        qqq = s.benchmark
        lines.append(
            f"| {s.name} | {s.started_on.isoformat()} | {last} | {s.sessions} "
            f"| {_number(s.equity, '.2f')} | {_pct(s.total_return, signed=True)} "
            f"| {_number(s.sharpe, '.2f')} | {_pct(s.max_drawdown)} "
            f"| {_pct(None if qqq is None else qqq.total_return, signed=True)} "
            f"| {_pct(None if s.price_only is None else s.price_only.total_return, signed=True)} "
            f"| {_number(None if qqq is None else qqq.sharpe, '.2f')} "
            f"| {_pct(None if qqq is None else qqq.max_drawdown)} |"
        )
    lines += [
        "",
        "## Trades since the start",
        "",
        (
            "| Portfolio | Closed trades | Mean R | Win rate | Average hold (sessions) "
            "| Open positions | Average share in QQQ | Catch-up sessions last week |"
        ),
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for s in summaries:
        lines.append(
            f"| {s.name} | {s.closed_trades} | {_number(s.mean_r, '+.3f')} | {_pct(s.win_rate)} "
            f"| {_number(s.average_hold, '.1f')} | {s.open_positions} | {_pct(s.vehicle_share)} "
            f"| {s.catch_up_sessions} |"
        )
    return "\n".join(lines) + "\n"


def write_weekly_report(session: Session, reports_dir: Path, today: date) -> Path:
    week = previous_week(today)
    portfolios = session.exec(select(PaperPortfolio).order_by(col(PaperPortfolio.id))).all()
    text = render_weekly_report(
        today, week, failed_runs(session, week), [summarize(session, p, week) for p in portfolios],
        code_use(session, week), run_warnings(session, week),
    )
    path = reports_dir / f"{today.isoformat()}-weekly.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
