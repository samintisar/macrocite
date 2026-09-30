"""Texts built from the ledger (spec 05): the evening summary, the pause review, and the
bot's /portfolio and /pnl.

R is always R on planned risk (spec 02, spec 04): a trade's P&L after fees over the risk
planned at the signal. Live mean R is compared with the backtest's, read from the stored
backtest run of the live config (v2-none-cash: 0.505).
"""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlmodel import Session, col, select

from signalbench.db.models import (
    BacktestRun,
    CashMovement,
    EquitySnapshot,
    ExitAlert,
    Ticker,
    TradeSignal,
)
from signalbench.live.book import ZERO
from signalbench.live.ledger import Ledger, LivePosition
from signalbench.live.messages import MINUS, cad, pct, signed_cad, units_text, usd

HUNDREDTH = Decimal("0.01")


@dataclass(frozen=True)
class BacktestR:
    """The mean R of the stored backtest run of the live config."""

    mean_r: Decimal
    version: str
    run_id: str


def backtest_r(session: Session, config_sha256: str) -> BacktestR | None:
    """The latest stored Breakout run (Jev off) of the config with this sha256, or None."""
    run = session.exec(
        select(BacktestRun)
        .where(
            BacktestRun.config_sha256 == config_sha256, BacktestRun.setup == "breakout",
            BacktestRun.jev_mode == "off",
        )
        .order_by(col(BacktestRun.run_at).desc())
    ).first()
    if run is None:
        return None
    mean = Decimal(repr(float(run.metrics["mean_r"]))).quantize(Decimal("0.001"))
    return BacktestR(mean_r=mean, version=run.strategy_version, run_id=str(run.id))


def signed(value: Decimal) -> str:
    """+0.51, −1.88: two decimals, rounded half up."""
    q = value.quantize(HUNDREDTH, rounding=ROUND_HALF_UP)
    return f"{MINUS if q < 0 else '+'}{abs(q)}"


def r_text(r: Decimal) -> str:
    """A trade's R: +0.51R, −1.88R."""
    return f"{signed(r)}R"


def _live_r(ledger: Ledger) -> list[Decimal]:
    return [t.r for t in ledger.closed_trades() if t.r is not None]


def r_comparison(ledger: Ledger, backtest: BacktestR | None) -> str:
    """Live mean R over the closed managed trades next to the backtest's."""
    rs = _live_r(ledger)
    live = "No closed managed trades yet" if not rs else (
        f"Live mean R {signed(sum(rs, ZERO) / len(rs))} over {len(rs)} closed managed trades"
    )
    if backtest is None:
        return f"{live} (no stored backtest run of the live config to compare)"
    return (
        f"{live} vs the backtest's {backtest.mean_r} ({backtest.version}, run "
        f"{backtest.run_id[:8]}), both R on planned risk"
    )


def position_line(ledger: Ledger, position: LivePosition) -> str:
    """One position: units, ACB per unit, last, P&L %, the current stop, and sessions held."""
    per_unit = position.acb / position.units
    last = "no price" if position.mark is None else cad(position.mark)
    change = pct(position.value / position.acb - 1) if position.acb else pct(ZERO)
    held = "1 session" if position.sessions_held == 1 else f"{position.sessions_held} sessions"
    if position.episode.signal_id is None:
        stop = "manual (no stop)"
    else:
        current = ledger.current_stop(position.episode.signal_id)
        stop = f"stop {cad(current.cdr)} / {usd(current.us)}"
    return (
        f"• {position.cdr_symbol} {units_text(position.units)} · ACB {cad(per_unit)}/unit · "
        f"last {last} · {change} · {stop} · {held}"
    )


def money_line(ledger: Ledger, snapshot: EquitySnapshot) -> str:
    risk = ledger.risk_state()
    drawdown = snapshot.equity / snapshot.peak - 1 if snapshot.peak else ZERO
    state = f"PAUSED since {risk.paused_at} (/resume)" if risk.paused else "not paused"
    return (
        f"Cash {cad(snapshot.cash)} · equity {cad(snapshot.equity)} · peak {cad(snapshot.peak)} "
        f"· drawdown {pct(drawdown)} · new entries {state}"
    )


def portfolio_text(ledger: Ledger, as_of: date) -> str:
    """/portfolio: the positions with their current stops, then cash, equity, and drawdown."""
    positions = ledger.positions(as_of)
    lines = [f"💼 Portfolio at the close of {as_of}"]
    lines += [position_line(ledger, p) for p in positions] or ["No open positions."]
    lines.append(money_line(ledger, ledger.equity(as_of)))
    return "\n".join(lines)


@dataclass(frozen=True)
class Tonight:
    """What the evening scan did, for its summary."""

    as_of: date
    regime: str  # "QQQ above its 200-session average: new entries allowed", or below
    signals: int  # entry signals sent for this session
    skips: Mapping[str, int]  # decide() and sizing skips by reason
    exits: int  # exit alerts written tonight (catch-up included)
    raises: int  # trailing-stop raises written tonight (catch-up included)
    caught_up: tuple[date, ...]  # missed sessions evaluated for exits and raises only
    warnings: tuple[str, ...]
    scale_up: str | None  # the scale-up result, when it was due


def open_alert_lines(session: Session) -> list[str]:
    """The exit alerts still `sent`: repeated each evening until sold or ignored."""
    alerts = session.exec(
        select(ExitAlert).where(ExitAlert.status == "sent").order_by(col(ExitAlert.id))
    ).all()
    lines = []
    for alert in alerts:
        cdr = session.get(Ticker, alert.cdr_ticker_id)
        symbol = "?" if cdr is None else cdr.symbol
        lines.append(f"• {symbol} {alert.reason} exit since {alert.as_of}: sell, or tap Ignore")
    return lines


def evening_summary(ledger: Ledger, session: Session, tonight: Tonight) -> str:
    skips = ", ".join(f"{reason} {n}" for reason, n in sorted(tonight.skips.items())) or "none"
    lines = [
        f"📊 Evening summary · {tonight.as_of} ({tonight.as_of:%a})",
        tonight.regime,
        (
            f"Signals sent {tonight.signals} · skipped: {skips} · exits {tonight.exits} · "
            f"stop raises {tonight.raises}"
        ),
    ]
    if tonight.caught_up:
        days = ", ".join(str(day) for day in tonight.caught_up)
        lines.append(f"Caught up (exits and stop raises only, sent marked late): {days}")
    positions = ledger.positions(tonight.as_of)
    lines.append("Positions:" if positions else "Positions: none")
    lines += [position_line(ledger, p) for p in positions]
    alerts = open_alert_lines(session)
    if alerts:
        lines.append("Open exit alerts:")
        lines += alerts
    lines.append(money_line(ledger, ledger.equity(tonight.as_of)))
    lines += [f"⚠️ {warning}" for warning in tonight.warnings]
    if tonight.scale_up is not None:
        lines.append(tonight.scale_up)
    return "\n".join(lines)


def _peak_day(session: Session, since: date | None) -> date | None:
    query = select(EquitySnapshot).order_by(col(EquitySnapshot.date))
    if since is not None:
        query = query.where(col(EquitySnapshot.date) >= since)
    rows = session.exec(query).all()
    if not rows:
        return None
    top = max(row.equity for row in rows)
    return next(row.date for row in rows if row.equity == top)


def pause_review(ledger: Ledger, session: Session, backtest: BacktestR | None) -> str:
    """The review sent when new entries pause, and again with /resume."""
    risk = ledger.risk_state()
    lines = [f"⏸ New entries are paused since the close of {risk.paused_at}: {risk.paused_reason}."]
    trades = [t for t in ledger.closed_trades() if t.r is not None][-10:]
    lines.append("Last 10 managed trades (R on planned risk):" if trades else "No closed trades.")
    lines += [
        f"• {t.cdr_symbol} {t.opened_on} → {t.closed_on} · {signed_cad(t.pnl)} · "
        f"{r_text(t.r)}"
        for t in trades
        if t.r is not None
    ]
    lines.append(r_comparison(ledger, backtest))
    skipped = Counter(
        s.skip_reason
        for s in session.exec(select(TradeSignal).where(TradeSignal.status == "skipped")).all()
    )
    misses = len(ledger.scale_up_check().misses)
    skips = ", ".join(f"{reason} {n}" for reason, n in sorted(skipped.items()) if reason) or "none"
    lines.append(f"Your skips: {skips} · missed exit alerts: {misses}")
    peak_day = _peak_day(session, risk.peak_reset_on)
    if peak_day is not None:
        withdrawn = -sum(
            (m.amount_cad for m in session.exec(select(CashMovement)).all()
             if m.amount_cad < 0 and m.occurred_on > peak_day),
            ZERO,
        )
        if withdrawn > 0:
            lines.append(
                f"Withdrawals since the peak on {peak_day}: {cad(withdrawn)}. A withdrawal lowers "
                "equity like a loss (spec 04), so part of this drawdown is your own cash."
            )
    lines.append("/resume to continue (resets peak)")
    return "\n".join(lines)


def pnl_text(ledger: Ledger, today: date, backtest: BacktestR | None) -> str:
    """/pnl: realized and unrealized P&L, all time and this month, and live R."""
    realized = month = ZERO
    for book in ledger.books().values():
        for sale in book.dispositions:
            realized += sale.gain
            if (sale.trade.day.year, sale.trade.day.month) == (today.year, today.month):
                month += sale.gain
    positions = ledger.positions(today)
    unrealized = sum((p.value - p.acb for p in positions), ZERO)
    closed = [t for t in ledger.closed_trades() if t.r is not None]
    wins = sum(1 for t in closed if t.pnl > 0)
    rate = "n/a" if not closed else f"{Decimal(wins * 100) / len(closed):.0f}%"
    return "\n".join([
        f"💰 P&L to {today}",
        f"Realized: all time {signed_cad(realized)} · {today:%B %Y} {signed_cad(month)}",
        f"Unrealized: {signed_cad(unrealized)} on {len(positions)} open positions",
        f"Closed managed trades: {len(closed)} · win rate {rate}",
        r_comparison(ledger, backtest),
    ])
