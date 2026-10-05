"""Send what the scan wrote (spec 05, step 9).

Every message-bearing row keeps the id of its Telegram message, and a row without one has not
been sent yet: exit alerts, trailing-stop raises, split notices (`split` stop rows and CDR
splits), and entry signals. They go out in that order, oldest first within each kind, each id
saved as soon as its message is sent, so a failure part way leaves the rest for the next scan
and nothing is sent twice. Exits come first because they matter most: the scan sends them and
the raises on their own (`send_exits_and_raises`) before it sizes tonight's entries.
"""

from datetime import date, datetime

from sqlmodel import Session, col, select

from signalbench.db.models import (
    CorporateAction,
    ExitAlert,
    Price,
    StopUpdateRow,
    Ticker,
    TickerKind,
    TradeSignal,
)
from signalbench.ingest.earnings import calendar_earnings_dates
from signalbench.live.book import MARKET_OPEN, NEW_YORK, ZERO
from signalbench.live.ledger import Ledger
from signalbench.live.messages import (
    CdrSplitText,
    EntryText,
    ExitText,
    RaiseText,
    SplitStopText,
    cdr_split_message,
    entry_buttons,
    entry_message,
    exit_buttons,
    exit_message,
    raise_message,
    split_stop_message,
)
from signalbench.live.messenger import Buttons, Messenger
from signalbench.live.sizing import entry_window, limit_price
from signalbench.market.calendar import Sessions


def _ticker(session: Session, ticker_id: object) -> Ticker:
    ticker = session.get(Ticker, ticker_id)
    assert ticker is not None
    return ticker


def _us(session: Session, symbol: str) -> Ticker:
    return session.exec(
        select(Ticker).where(Ticker.symbol == symbol, Ticker.kind == TickerKind.us_stock)
    ).one()


def split_row_text(session: Session, row: StopUpdateRow) -> str:
    signal = session.get(TradeSignal, row.signal_id)
    action = session.get(CorporateAction, row.corporate_action_id)
    assert signal is not None and action is not None
    if action.kind == "us_split":
        text = SplitStopText(symbol=signal.us_symbol, ratio=action.ratio, ex_date=action.ex_date,
                             old=row.old_us_stop, new=row.new_us_stop, currency="US$")
    else:
        cdr = _ticker(session, signal.cdr_ticker_id)
        text = SplitStopText(symbol=cdr.symbol, ratio=action.ratio, ex_date=action.ex_date,
                             old=row.old_cdr_stop, new=row.new_cdr_stop, currency="C$")
    message = split_stop_message(text)
    return message.replace(" (ex-date", " voided (ex-date", 1) if action.voided else message


def cdr_split_text(session: Session, ledger: Ledger, action: CorporateAction) -> str:
    cdr = _ticker(session, action.cdr_ticker_id)
    book = ledger.books().get(cdr.symbol)
    record = None if book is None else next(
        (r for r in book.splits if r.split.id == action.id), None
    )
    withdrawn = session.exec(
        select(TradeSignal.id).where(
            TradeSignal.cdr_ticker_id == cdr.id, TradeSignal.status == "withdrawn",
            col(TradeSignal.as_of) < action.ex_date,
        ).order_by(col(TradeSignal.id))
    ).all()
    return cdr_split_message(
        CdrSplitText(
            cdr_symbol=cdr.symbol, ratio=action.ratio, ex_date=action.ex_date,
            units_before=ZERO if record is None else record.units_before,
            units_after=ZERO if record is None else record.units_after,
            per_unit_before=None if record is None else record.per_unit_before,
            per_unit_after=None if record is None else record.per_unit_after,
            withdrawn=tuple(i for i in withdrawn if i is not None),
        )
    )


def entry_text(
    session: Session, signal: TradeSignal, nyse: Sessions, cboe: Sessions, now: datetime
) -> tuple[str, Buttons]:
    cdr = _ticker(session, signal.cdr_ticker_id)
    late = f"ℹ️ The {signal.us_symbol} (CDR {cdr.symbol}) entry of {signal.as_of} was not sent in time"
    if signal.status == "withdrawn":  # a CDR split since it was written
        return f"{late} and is withdrawn: do not place it.", ()
    if signal.status == "expired" or signal.expires_at <= now:
        expired = signal.expires_at.astimezone(NEW_YORK)
        return f"{late} and expired at {expired:%Y-%m-%d %H:%M} New York time: do not place it.", ()
    window = entry_window(signal.as_of, nyse, cboe)
    entry = EntryText(
        us_symbol=signal.us_symbol, cdr_symbol=cdr.symbol, company=cdr.company_name,
        cdr_close=signal.cdr_signal_close, cdr_stop=signal.cdr_stop, stop_pct=signal.stop_pct,
        units=signal.suggested_units,
        order_type="limit" if signal.order_type == "limit" else "market",
        limit=limit_price(signal.cdr_signal_close),
        risk=signal.risk_amount_cad, why=signal.explanation, note=window.note,
        late=now >= datetime.combine(window.session, MARKET_OPEN, tzinfo=NEW_YORK),
    )
    assert signal.id is not None
    return entry_message(entry), entry_buttons(signal.id)


def _earnings_after(session: Session, us: Ticker, day: date) -> date | None:
    return next((d for d in calendar_earnings_dates(session, us.id) if d > day), None)


def exit_text(session: Session, ledger: Ledger, alert: ExitAlert) -> str:
    signal = session.get(TradeSignal, alert.signal_id)
    assert signal is not None
    cdr = _ticker(session, alert.cdr_ticker_id)
    us = _us(session, signal.us_symbol)
    position = next(
        (p for p in ledger.positions(alert.as_of) if p.episode.signal_id == alert.signal_id),
        None,
    )
    bar = session.exec(
        select(Price).where(Price.ticker_id == us.id, Price.date == alert.as_of)
    ).first()
    stop = ledger.stop_in_force(alert.signal_id, alert.as_of)
    value = acb = ZERO
    held = 0
    mark = None
    if position is not None:
        value, acb, held, mark = position.value, position.acb, position.sessions_held, position.mark
    return exit_message(
        ExitText(
            us_symbol=signal.us_symbol, cdr_symbol=cdr.symbol,
            reason="stop" if alert.reason == "stop" else "earnings",
            us_close=ZERO if bar is None else bar.adj_close, us_stop=stop.us, cdr_mark=mark,
            cdr_stop=stop.cdr, earnings_on=_earnings_after(session, us, alert.as_of),
            sessions_held=held, unrealized=value - acb,
            unrealized_pct=value / acb - 1 if acb else ZERO,
            late_after=alert.as_of if alert.late else None,
        )
    )


def raise_text(session: Session, row: StopUpdateRow) -> str:
    signal = session.get(TradeSignal, row.signal_id)
    assert signal is not None
    cdr = _ticker(session, signal.cdr_ticker_id)
    return raise_message(
        RaiseText(
            us_symbol=signal.us_symbol, cdr_symbol=cdr.symbol, old_cdr=row.old_cdr_stop,
            new_cdr=row.new_cdr_stop, old_us=row.old_us_stop, new_us=row.new_us_stop,
            late_for=row.session if row.late else None,
        )
    )


def _unsent_stop_rows(session: Session, reason: str) -> list[StopUpdateRow]:
    return list(
        session.exec(
            select(StopUpdateRow)
            .where(StopUpdateRow.reason == reason, col(StopUpdateRow.telegram_message_id).is_(None))
            .order_by(col(StopUpdateRow.id))
        ).all()
    )


class _Sender:
    """Sends a row's message and saves its id at once, counting what was sent."""

    def __init__(self, session: Session, messenger: Messenger) -> None:
        self.session = session
        self.messenger = messenger
        self.sent = 0

    def __call__(
        self, row: StopUpdateRow | CorporateAction | TradeSignal | ExitAlert, text: str,
        buttons: Buttons = (),
    ) -> None:
        row.telegram_message_id = self.messenger.send(text, buttons)
        self.session.add(row)
        self.session.commit()
        self.sent += 1


def _send_exits_and_raises(session: Session, ledger: Ledger, mark: _Sender) -> None:
    alerts = session.exec(
        select(ExitAlert)
        .where(col(ExitAlert.telegram_message_id).is_(None))
        .order_by(col(ExitAlert.id))
    ).all()
    for alert in alerts:
        assert alert.id is not None
        mark(alert, exit_text(session, ledger, alert), exit_buttons(alert.id))
    for row in _unsent_stop_rows(session, "trail"):
        mark(row, raise_text(session, row))


def send_exits_and_raises(session: Session, ledger: Ledger, messenger: Messenger) -> int:
    """Send every unsent exit alert, then every unsent stop raise. Returns how many messages
    were sent; a send that raises stops here, and what is left is sent by the next scan."""
    mark = _Sender(session, messenger)
    _send_exits_and_raises(session, ledger, mark)
    return mark.sent


def send_unsent(
    session: Session,
    ledger: Ledger,
    messenger: Messenger,
    *,
    nyse: Sessions,
    cboe: Sessions,
    now: datetime,
) -> int:
    """Send every unsent row: exit alerts, stop raises, split notices, then entries. Returns
    how many messages were sent. A send that raises stops here; what is left is sent by the
    next scan."""
    mark = _Sender(session, messenger)
    _send_exits_and_raises(session, ledger, mark)
    for row in _unsent_stop_rows(session, "split"):
        mark(row, split_row_text(session, row))
    actions = session.exec(
        select(CorporateAction)
        .where(
            CorporateAction.kind == "cdr_split", col(CorporateAction.voided).is_(False),
            col(CorporateAction.telegram_message_id).is_(None),
        )
        .order_by(col(CorporateAction.id))
    ).all()
    for action in actions:
        mark(action, cdr_split_text(session, ledger, action))
    signals = session.exec(
        select(TradeSignal)
        .where(col(TradeSignal.telegram_message_id).is_(None))
        .order_by(col(TradeSignal.id))
    ).all()
    for signal in signals:
        text, buttons = entry_text(session, signal, nyse, cboe, now)
        mark(signal, text, buttons)
    return mark.sent
