"""`signalbench paper status` (spec 07, Commands): one line per portfolio, and the stale check
the nightly script uses."""

from datetime import date, datetime, timedelta

from sqlmodel import Session, col, func, select

from signalbench.db.models import PaperEquity, PaperEvent, PaperPortfolio, PaperRun

JUDGE_TRADES = 30  # spec 07, Judging: at least 30 closed trades, and 12 months since the start


def judge_date(started_on: date) -> date:
    """12 months after the start (a 29 February start becomes 28 February)."""
    try:
        return started_on.replace(year=started_on.year + 1)
    except ValueError:
        return started_on.replace(year=started_on.year + 1, day=28)


def judgeable(started_on: date, closed_trades: int, today: date) -> str:
    days = max(0, (judge_date(started_on) - today).days)
    more = max(0, JUDGE_TRADES - closed_trades)
    if days == 0 and more == 0:
        return "judgeable now"
    waits = ([f"{days} days"] if days else []) + ([f"{more} more closed trades"] if more else [])
    return "judgeable after " + " and ".join(waits)


def last_ok_run(session: Session) -> datetime | None:
    return session.exec(select(func.max(PaperRun.started_at)).where(PaperRun.status == "ok")).one()


def stale_message(session: Session, now: datetime, days: int) -> str | None:
    """A warning when no `paper run` has succeeded for more than `days` days (counted from the
    portfolios' creation when none has succeeded yet); None otherwise, or with no portfolios."""
    last = last_ok_run(session)
    created = session.exec(select(func.min(PaperPortfolio.created_at))).one()
    since = last if last is not None else created
    if since is None or now - since <= timedelta(days=days):
        return None
    when = "never" if last is None else f"on {last.isoformat(timespec='minutes')}"
    return f"STALE: the last ok paper run was {when}, more than {days} days ago."


def status_lines(session: Session, today: date) -> list[str]:
    last = last_ok_run(session)
    lines = [f"last ok run: {'none' if last is None else last.isoformat(timespec='minutes')}"]
    portfolios = session.exec(select(PaperPortfolio).order_by(col(PaperPortfolio.id))).all()
    for portfolio in portfolios:
        rows = session.exec(
            select(PaperEquity).where(PaperEquity.portfolio_id == portfolio.id)
            .order_by(col(PaperEquity.session))
        ).all()
        closed = len(session.exec(
            select(PaperEvent.id).where(
                PaperEvent.portfolio_id == portfolio.id, PaperEvent.kind == "fill_exit"
            )
        ).all())
        wait = judgeable(portfolio.started_on, closed, today)
        if not rows:
            lines.append(f"{portfolio.name}: starts {portfolio.started_on.isoformat()}, not stepped yet | {wait}")
            continue
        first, latest = rows[0], rows[-1]
        lines.append(
            f"{portfolio.name}: last {latest.session.isoformat()} | equity {latest.equity:.2f} "
            f"| return {latest.equity / first.equity - 1.0:+.2%} | open {latest.open_positions} "
            f"| closed {closed} | {wait}"
        )
    return lines
