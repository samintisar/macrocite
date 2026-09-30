"""What the bot does with a button press, a command, or a reply (spec 05, Commands).

`BotBrain` turns each update into replies (new messages or edits) and ledger writes, and never
touches Telegram itself: live/telegram_bot.py carries updates in and replies out. Only the
owner's chat (`TELEGRAM_CHAT_ID`) is answered; anything else is logged and ignored.

Button data (live/messages.py): `b:<signal>` I bought, `u:<signal>` use the suggested size,
`s:<signal>` skip, `k:<signal>:<reason>` a skip reason, `x:<alert>` sold, `i:<alert>` ignore,
`rc` confirm /resume. After ✅ the bot waits for a reply such as `3 10.45` (units and price; a
third token sets the date). Symbols are CDR symbols; a US symbol with one CDR is resolved.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal, cast, get_args

from sqlmodel import Session, col, select

from signalbench.db.models import ExitAlert, LiveConfig, Ticker, TickerKind, TradeSignal
from signalbench.live.book import NEW_YORK, LedgerError, SignalSkip
from signalbench.live.ledger import Ledger
from signalbench.live.messages import cad, signed_cad, skip_buttons, units_text, usd
from signalbench.live.messenger import Button, Buttons
from signalbench.live.sizing import limit_price
from signalbench.live.status import scan_status_lines
from signalbench.live.summary import (
    BacktestR,
    backtest_r,
    pause_review,
    pnl_text,
    portfolio_text,
    r_text,
)
from signalbench.live.tax import tax_text
from signalbench.market.calendar import Sessions

log = logging.getLogger(__name__)

HELP = """SignalBench commands (symbols are CDR symbols, like ZNVD):
/signals: today's open signals
/portfolio: positions with their stops, cash, equity, drawdown
/pnl: realized and unrealized P&L, win rate, live R vs the backtest
/buy SYMBOL QTY PRICE [YYYY-MM-DD] [force]: a manual or unsignalled buy
/sell SYMBOL QTY|all PRICE [YYYY-MM-DD]: a sale (linked to an open exit alert)
/void FILL_ID REASON: void a fill
/deposit AMOUNT, /withdraw AMOUNT: cash movements
/resume: end a pause (shows the review first)
/status: the last scan, the next one, the live config and code
/tax YEAR: the ACB report summary (the CSV: signalbench ledger tax YEAR --csv PATH)
/help: this list"""


class UsageError(ValueError):
    """A command the bot could not read. The message is the reply."""


@dataclass(frozen=True)
class Reply:
    text: str
    buttons: Buttons = ()
    edit: int | None = None  # the message to edit instead of sending a new one


@dataclass(frozen=True)
class Awaiting:
    """What the owner's next plain-text reply answers: after ✅ on an entry or an exit."""

    kind: Literal["buy", "sell"]
    row_id: int  # the signal or the exit alert
    message_id: int
    message_text: str


def _number(token: str, what: str) -> Decimal:
    try:
        value = Decimal(token.replace(",", ""))
    except InvalidOperation:
        raise UsageError(f"⚠️ {what} must be a number, not {token!r}") from None
    if not value.is_finite():
        raise UsageError(f"⚠️ {what} must be a number, not {token!r}")
    return value


def _day(token: str) -> date:
    try:
        return date.fromisoformat(token)
    except ValueError:
        raise UsageError(f"⚠️ the date must be YYYY-MM-DD, not {token!r}") from None


def _cdr(session: Session, token: str) -> Ticker:
    """A CDR symbol, or a US symbol with exactly one CDR."""
    symbol = token.upper()
    found = session.exec(
        select(Ticker).where(Ticker.symbol == symbol, Ticker.kind == TickerKind.cdr)
    ).first()
    if found is not None:
        return found
    us = session.exec(
        select(Ticker).where(Ticker.symbol == symbol, Ticker.kind == TickerKind.us_stock)
    ).first()
    if us is not None:
        cdrs = session.exec(
            select(Ticker).where(Ticker.us_ticker_id == us.id, Ticker.kind == TickerKind.cdr)
        ).all()
        if len(cdrs) == 1:
            return cdrs[0]
        if cdrs:
            names = ", ".join(sorted(c.symbol for c in cdrs))
            raise UsageError(f"⚠️ {symbol} has {len(cdrs)} CDRs ({names}): use the CDR symbol")
    raise UsageError(f"⚠️ unknown symbol {token!r}: use a CDR symbol, like ZNVD")


class BotBrain:
    """One per bot process. `sessions` opens a database session per update; `clock` gives
    timezone-aware times (the owner's date is New York's); `next_run` reads the scheduled
    scan's next run time, or None."""

    def __init__(
        self,
        *,
        chat_id: int,
        sessions: Callable[[], Session],
        calendar: Sessions,
        clock: Callable[[], datetime],
        next_run: Callable[[], str | None],
    ) -> None:
        self.chat_id = chat_id
        self._sessions = sessions
        self._calendar = calendar
        self._clock = clock
        self._next_run = next_run
        self.awaiting: Awaiting | None = None

    def allowed(self, chat_id: int) -> bool:
        if chat_id != self.chat_id:
            log.warning("ignored an update from chat %s (not TELEGRAM_CHAT_ID)", chat_id)
            return False
        return True

    def _today(self) -> date:
        return self._clock().astimezone(NEW_YORK).date()

    def _ledger(self, session: Session) -> Ledger:
        return Ledger(session, calendar=self._calendar, today=self._today())

    # --- Entry points ----------------------------------------------------------------------

    def handle_text(self, chat_id: int, text: str) -> list[Reply]:
        if not self.allowed(chat_id):
            return []
        text = text.strip()
        try:
            with self._sessions() as session:
                if text.startswith("/"):
                    name, *args = text.split()
                    return self._command(session, name[1:].split("@")[0].lower(), args)
                if self.awaiting is not None:
                    return self._answer(session, self.awaiting, text.split())
        except (UsageError, LedgerError) as error:
            message = str(error)
            return [Reply(message if message.startswith("⚠️") else f"⚠️ {message}")]
        return [Reply("Send /help for the commands.")]

    def handle_callback(
        self, chat_id: int, data: str, message_id: int, message_text: str
    ) -> list[Reply]:
        if not self.allowed(chat_id):
            return []
        kind, _, rest = data.partition(":")
        try:
            with self._sessions() as session:
                ledger = self._ledger(session)
                if kind == "rc":
                    risk = ledger.resume()
                    return [Reply(
                        f"▶️ Resumed: new entries are allowed again; the peak counts from "
                        f"{risk.peak_reset_on}."
                    )]
                if kind in ("b", "u", "s", "k"):
                    signal_id, _, reason = rest.partition(":")
                    return self._signal_button(
                        session, ledger, kind, int(signal_id), reason, message_id, message_text
                    )
                if kind in ("x", "i"):
                    return self._alert_button(ledger, kind, int(rest), message_id, message_text)
        except (UsageError, LedgerError) as error:
            message = str(error)
            return [Reply(message if message.startswith("⚠️") else f"⚠️ {message}")]
        except ValueError:  # malformed button data
            pass
        return [Reply("⚠️ That button is no longer valid.")]

    # --- Buttons ---------------------------------------------------------------------------

    def _signal_button(
        self,
        session: Session,
        ledger: Ledger,
        kind: str,
        signal_id: int,
        reason: str,
        message_id: int,
        message_text: str,
    ) -> list[Reply]:
        signal = ledger.signal(signal_id)
        if signal.status not in ("sent", "expired"):
            return [Reply(f"Already logged: signal {signal_id} is {signal.status}.")]
        if kind == "s":
            return [Reply(message_text, skip_buttons(signal_id), edit=message_id)]
        if kind == "k":
            if reason not in get_args(SignalSkip):
                return [Reply("⚠️ That button is no longer valid.")]
            ledger.mark_signal(signal_id, "skipped", cast(SignalSkip, reason))
            self.awaiting = None
            label = reason.replace("_", " ")
            return [Reply(f"{message_text}\n⏭ Skipped ({label})", edit=message_id)]
        cdr = session.get(Ticker, signal.cdr_ticker_id)
        assert cdr is not None
        if kind == "u":
            return self._buy(session, ledger, signal, [f"{signal.suggested_units.normalize():f}",
                                                       f"{signal.cdr_signal_close}"],
                             message_id, message_text)
        self.awaiting = Awaiting("buy", signal_id, message_id, message_text)
        suggested = (
            f"Use suggested: {units_text(signal.suggested_units)} @ {cad(signal.cdr_signal_close)}"
        )
        return [Reply(
            f"How many units of {cdr.symbol} did you buy, and at what price? Reply like "
            f"`{signal.suggested_units.normalize():f} {signal.cdr_signal_close.normalize():f}` "
            "(a third token sets the date, YYYY-MM-DD).",
            ((Button(suggested, f"u:{signal_id}"),),),
        )]

    def _alert_button(
        self, ledger: Ledger, kind: str, alert_id: int, message_id: int, message_text: str
    ) -> list[Reply]:
        alert = ledger.exit_alert(alert_id)
        if alert.status != "sent":
            return [Reply(f"Already logged: exit alert {alert_id} is {alert.status}.")]
        if kind == "i":
            ledger.mark_exit_alert(alert_id, "ignored")
            self.awaiting = None
            return [Reply(
                f"{message_text}\nIgnored: this counts as a miss in the scale-up check, and the "
                "position is managed again from the next session.",
                edit=message_id,
            )]
        self.awaiting = Awaiting("sell", alert_id, message_id, message_text)
        return [Reply(
            "How many units did you sell, and at what price? Reply like `9.70` (all units), "
            "`2 9.70`, or `all 9.70` (a third token sets the date, YYYY-MM-DD)."
        )]

    # --- Replies after ✅ ------------------------------------------------------------------

    def _answer(self, session: Session, awaiting: Awaiting, tokens: list[str]) -> list[Reply]:
        ledger = self._ledger(session)
        if awaiting.kind == "buy":
            signal = ledger.signal(awaiting.row_id)
            return self._buy(session, ledger, signal, tokens, awaiting.message_id,
                             awaiting.message_text)
        alert = ledger.exit_alert(awaiting.row_id)
        cdr = session.get(Ticker, alert.cdr_ticker_id)
        assert cdr is not None
        if not 1 <= len(tokens) <= 3:
            raise UsageError("⚠️ Reply like `9.70`, `2 9.70`, or `all 9.70 2026-10-09`.")
        if len(tokens) == 1:
            tokens = ["all", *tokens]
        text, done = self._sell(session, ledger, cdr, tokens, alert.id)
        self.awaiting = None
        return [Reply(text), Reply(f"{awaiting.message_text}\n✅ {done}", edit=awaiting.message_id)]

    def _buy(
        self,
        session: Session,
        ledger: Ledger,
        signal: TradeSignal,
        tokens: list[str],
        message_id: int,
        message_text: str,
    ) -> list[Reply]:
        force = bool(tokens) and tokens[-1].lower() == "force"
        tokens = tokens[:-1] if force else tokens
        if len(tokens) not in (2, 3):
            raise UsageError("⚠️ Reply like `3 10.45`, or `3 10.45 2026-10-06`.")
        cdr = session.get(Ticker, signal.cdr_ticker_id)
        assert cdr is not None and signal.id is not None
        fill = ledger.record_fill(
            cdr_symbol=cdr.symbol, side="buy", quantity=_number(tokens[0], "the units"),
            price_cad=_number(tokens[1], "the price"),
            trade_date=_day(tokens[2]) if len(tokens) == 3 else self._today(),
            signal_id=signal.id, force=force,
        )
        self.awaiting = None
        stop = ledger.current_stop(signal.id)
        bought = f"Bought {units_text(fill.quantity)} @ {cad(fill.price_cad)} (fill {fill.id})"
        return [
            Reply(
                f"✅ Bought {units_text(fill.quantity)} {cdr.symbol} @ {cad(fill.price_cad)} on "
                f"{fill.trade_date} (fill {fill.id}), for signal {signal.id}. Stop "
                f"{cad(stop.cdr)} / {usd(stop.us)}; the evening scan manages it from here."
            ),
            Reply(f"{message_text}\n✅ {bought}", edit=message_id),
        ]

    def _sell(
        self, session: Session, ledger: Ledger, cdr: Ticker, tokens: list[str],
        alert_id: int | None,
    ) -> tuple[str, str]:
        """Sell `tokens` = [QTY|all, PRICE, (DATE)] of `cdr`, linked to `alert_id`. Returns the
        reply and a short line for the alert's message."""
        book = ledger.books().get(cdr.symbol)
        held = book.units if book is not None else Decimal(0)
        quantity = held if tokens[0].lower() == "all" else _number(tokens[0], "the units")
        if held <= 0:
            raise UsageError(f"⚠️ no {cdr.symbol} units are held")
        fill = ledger.record_fill(
            cdr_symbol=cdr.symbol, side="sell", quantity=quantity,
            price_cad=_number(tokens[1], "the price"),
            trade_date=_day(tokens[2]) if len(tokens) == 3 else self._today(),
            exit_alert_id=alert_id,
        )
        text = (
            f"✅ Sold {units_text(fill.quantity)} {cdr.symbol} @ {cad(fill.price_cad)} on "
            f"{fill.trade_date} (fill {fill.id})."
        )
        closed = next(
            (t for t in ledger.closed_trades() if t.episode.fills[-1].id == fill.id), None
        )
        if closed is None:
            left = ledger.books()[cdr.symbol].units
            text += f" Still holding {units_text(left)}."
        else:
            text += f" Position closed: P&L {signed_cad(closed.pnl)}"
            text += "." if closed.r is None else f" · {r_text(closed.r)} on planned risk."
        return text, f"Sold {units_text(fill.quantity)} @ {cad(fill.price_cad)} (fill {fill.id})"

    # --- Commands --------------------------------------------------------------------------

    def _command(self, session: Session, name: str, args: list[str]) -> list[Reply]:
        ledger = self._ledger(session)
        today = self._today()
        if name in ("help", "start"):
            return [Reply(HELP)]
        if name == "signals":
            return [Reply(self._signals(session))]
        if name == "portfolio":
            return [Reply(portfolio_text(ledger, today))]
        if name == "pnl":
            return [Reply(pnl_text(ledger, today, self._backtest(session)))]
        if name == "buy":
            return [Reply(self._manual_buy(session, ledger, args))]
        if name == "sell":
            if len(args) not in (3, 4):
                raise UsageError("⚠️ Usage: /sell SYMBOL QTY|all PRICE [YYYY-MM-DD]")
            cdr = _cdr(session, args[0])
            alert = session.exec(
                select(ExitAlert).where(
                    ExitAlert.cdr_ticker_id == cdr.id, ExitAlert.status == "sent"
                )
            ).first()
            text, _ = self._sell(session, ledger, cdr, args[1:], None if alert is None else alert.id)
            return [Reply(text)]
        if name == "void":
            if len(args) < 2 or not args[0].isdigit():
                raise UsageError("⚠️ Usage: /void FILL_ID REASON")
            ledger.void_fill(int(args[0]), " ".join(args[1:]))
            return [Reply(f"Fill {args[0]} voided: {' '.join(args[1:])}. Cash is now "
                          f"{cad(ledger.cash())}.")]
        if name in ("deposit", "withdraw"):
            if len(args) != 1:
                raise UsageError(f"⚠️ Usage: /{name} AMOUNT")
            amount = _number(args[0], "the amount")
            if amount <= 0:
                raise UsageError("⚠️ the amount must be above 0")
            ledger.record_cash(amount if name == "deposit" else -amount, today, f"/{name}")
            return [Reply(f"{name.capitalize()} of {cad(amount)} recorded on {today}. Cash is "
                          f"now {cad(ledger.cash())}.")]
        if name == "resume":
            if not ledger.risk_state().paused:
                return [Reply("New entries are not paused.")]
            review = pause_review(ledger, session, self._backtest(session))
            return [Reply(review, ((Button("Confirm /resume", "rc"),),))]
        if name == "status":
            return [Reply("\n".join(scan_status_lines(session, self._next_run)))]
        if name == "tax":
            if len(args) != 1 or not args[0].isdigit():
                raise UsageError("⚠️ Usage: /tax YEAR")
            lines = tax_text(ledger.tax_report(int(args[0])))
            summary = [line for line in lines if not line[:4].isdigit()]  # no per-sale lines
            summary.append(f"Every sale: signalbench ledger tax {args[0]} --csv PATH")
            return [Reply("\n".join(summary))]
        return [Reply(f"⚠️ unknown command /{name}. Send /help for the commands.")]

    def _backtest(self, session: Session) -> BacktestR | None:
        live = session.get(LiveConfig, 1)
        return None if live is None else backtest_r(session, live.config_sha256)

    def _signals(self, session: Session) -> str:
        now = self._clock()
        rows = [
            s for s in session.exec(
                select(TradeSignal).where(TradeSignal.status == "sent")
                .order_by(col(TradeSignal.id))
            ).all()
            if s.expires_at > now
        ]
        if not rows:
            return "No open signals."
        lines = ["Open signals:"]
        for s in rows:
            cdr = session.get(Ticker, s.cdr_ticker_id)
            limit = limit_price(s.cdr_signal_close)
            order = f"LIMIT {cad(limit)}" if s.order_type == "limit" else f"MARKET if ≤ {cad(limit)}"
            expires = s.expires_at.astimezone(NEW_YORK)
            lines.append(
                f"• {s.id} {'?' if cdr is None else cdr.symbol} ({s.us_symbol}) "
                f"{units_text(s.suggested_units)} · {order} · stop {cad(s.cdr_stop)} · until "
                f"{expires:%a %d %b %H:%M} New York"
            )
        return "\n".join(lines)

    def _manual_buy(self, session: Session, ledger: Ledger, args: list[str]) -> str:
        force = bool(args) and args[-1].lower() == "force"
        args = args[:-1] if force else args
        if len(args) not in (3, 4):
            raise UsageError("⚠️ Usage: /buy SYMBOL QTY PRICE [YYYY-MM-DD] [force]")
        cdr = _cdr(session, args[0])
        fill = ledger.record_fill(
            cdr_symbol=cdr.symbol, side="buy", quantity=_number(args[1], "the units"),
            price_cad=_number(args[2], "the price"),
            trade_date=_day(args[3]) if len(args) == 4 else self._today(), force=force,
        )
        return (
            f"✅ Bought {units_text(fill.quantity)} {cdr.symbol} @ {cad(fill.price_cad)} on "
            f"{fill.trade_date} (fill {fill.id}), manual: no stop or exit alerts."
        )
