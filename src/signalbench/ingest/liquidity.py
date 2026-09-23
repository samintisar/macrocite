from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from statistics import median

from sqlmodel import Session, col, select

from signalbench.db.models import Price, Ticker
from signalbench.ingest.cdr import CdrEntry

US_MIN_MEDIAN_TRADED_VALUE_USD = Decimal(50_000_000)
TRADED_VALUE_SESSIONS = 20
CDR_RECENT_SESSIONS = 5
SESSION_CALENDAR_SYMBOL = "QQQ"


@dataclass(frozen=True)
class LiquidityResult:
    active: list[str]
    inactive: dict[str, str]


def median_traded_value(rows: list[Price]) -> Decimal | None:
    if len(rows) < TRADED_VALUE_SESSIONS:
        return None
    recent = sorted(rows, key=lambda row: row.date)[-TRADED_VALUE_SESSIONS:]
    return median([row.close * row.volume for row in recent])


def recent_sessions(session: Session, count: int) -> list[date]:
    """The last `count` session dates, taken from the QQQ price series."""
    calendar = session.exec(select(Ticker).where(Ticker.symbol == SESSION_CALENDAR_SYMBOL)).first()
    if calendar is None:
        return []
    dates = session.exec(
        select(col(Price.date))
        .where(Price.ticker_id == calendar.id)
        .order_by(col(Price.date).desc())
        .limit(count)
    ).all()
    return sorted(dates)


def update_liquidity_flags(session: Session, entries: list[CdrEntry]) -> LiquidityResult:
    sessions = recent_sessions(session, CDR_RECENT_SESSIONS)
    if len(sessions) < CDR_RECENT_SESSIONS:
        raise RuntimeError(
            f"Need {CDR_RECENT_SESSIONS} {SESSION_CALENDAR_SYMBOL} price rows to define recent "
            "sessions; run `signalbench ingest prices` first."
        )
    oldest_recent = sessions[0]
    by_symbol = {ticker.symbol: ticker for ticker in session.exec(select(Ticker)).all()}
    active: list[str] = []
    inactive: dict[str, str] = {}
    for entry in entries:
        us = by_symbol.get(entry.us_symbol)
        cdr = by_symbol.get(entry.cdr_symbol)
        if us is None or cdr is None:
            inactive[entry.us_symbol] = "not_seeded"
            continue
        us_rows = list(
            session.exec(
                select(Price)
                .where(Price.ticker_id == us.id)
                .order_by(col(Price.date).desc())
                .limit(TRADED_VALUE_SESSIONS)
            ).all()
        )
        traded_value = median_traded_value(us_rows)
        cdr_recent = session.exec(
            select(Price.id).where(Price.ticker_id == cdr.id, col(Price.date) >= oldest_recent)
        ).first()
        reason: str | None
        if traded_value is None:
            reason = "us_history_short"
        elif traded_value < US_MIN_MEDIAN_TRADED_VALUE_USD:
            reason = "us_illiquid"
        elif cdr_recent is None:
            reason = "cdr_no_recent_price"
        else:
            reason = None
        us.active = reason is None
        cdr.active = reason is None
        session.add(us)
        session.add(cdr)
        if reason is None:
            active.append(entry.us_symbol)
        else:
            inactive[entry.us_symbol] = reason
    session.commit()
    return LiquidityResult(active=sorted(active), inactive=inactive)
