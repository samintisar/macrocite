"""What the bot does with a button press, a command, or a reply (spec 05, Commands).

`BotBrain` turns each update into replies (new messages or edits) and ledger writes, and never
touches Telegram itself: live/telegram_bot.py carries updates in and replies out. Only the
owner's chat (`TELEGRAM_CHAT_ID`) is answered; anything else is logged and ignored.

Button data (live/messages.py): `b:<signal>` I bought, `s:<signal>` skip,
`k:<signal>:<reason>` a skip reason, `x:<alert>` sold, `i:<alert>` ignore, `rc` confirm
/resume, and `ok:<n>` / `no:<n>` confirm or cancel a fill that needs a second look.

Nothing is assumed about a fill: after ✅ on a whole-unit (limit) entry the bot asks for the
average fill price from Wealthsimple's confirmation (the suggested units are pre-filled, and
`UNITS PRICE` overrides them); a fractional order needs both. The date defaults to the
signal's entry session and must fall after its as_of and on or before its expiry session
unless the reply ends in `force`. A fill priced more than 10% from its reference (the
signal's CDR reference for a signal-linked buy, the latest CDR mark otherwise), a signal-linked
buy of more than 2x the suggested units, and a price alone after ✅ Sold ("sell ALL?") are
shown back with [✅ Confirm] and [Cancel] before anything is written.

Each write carries the Telegram update id (`bot_updates`, in the same transaction), so an
update redelivered after a crash is not recorded twice. Symbols are CDR symbols; a US symbol
with one CDR is resolved.
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal, cast, get_args

from sqlmodel import Session, col, select

from signalbench.db.models import (
    BotUpdate,
    ExitAlert,
    Fill,
    LiveConfig,
    Ticker,
    TickerKind,
    TradeSignal,
)
from signalbench.live.acb import Side
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

PRICE_BAND = Decimal("0.10")  # a fill priced more than 10% from its reference asks first
UNITS_BAND = 2  # a signal-linked buy of more than 2x the suggested units asks first
DATE_LIKE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}")  # a reply's date token

HELP = """SignalBench commands (symbols are CDR symbols, like ZNVD):
/signals: today's open signals
/portfolio: positions with their stops, cash, equity, drawdown
/pnl: realized and unrealized P&L, win rate, live R vs the backtest
/buy SYMBOL QTY PRICE [YYYY-MM-DD] [force]: a manual or unsignalled buy
/sell SYMBOL QTY|all PRICE [YYYY-MM-DD]: a sale (linked to an open exit alert)
/void FILL_ID REASON: void a fill
/deposit AMOUNT, /withdraw AMOUNT [force]: cash movements
A price more than 10% from the signal or the latest mark asks you to confirm first.
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


@dataclass(frozen=True)
class FillRequest:
    """A fill the owner reported, not written yet."""

    side: Side
    cdr_symbol: str
    quantity: Decimal
    price: Decimal
    trade_date: date
    force: bool = False
    signal_id: int | None = None
    exit_alert_id: int | None = None
    message_id: int | None = None  # the entry or exit message to edit once it is recorded
    message_text: str = ""


@dataclass(frozen=True)
class Confirming:
    """A fill waiting for [✅ Confirm] (`ok:<token>`) or [Cancel] (`no:<token>`)."""

    token: int
    request: FillRequest


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


def _split_reply(tokens: list[str]) -> tuple[list[str], date | None, bool]:
    """A reply's number tokens, its date token (YYYY-MM-DD-like, after them), and `force`."""
    force = bool(tokens) and tokens[-1].lower() == "force"
    tokens = tokens[:-1] if force else tokens
    day = None
    if tokens and DATE_LIKE.fullmatch(tokens[-1]):
        day, tokens = _day(tokens[-1]), tokens[:-1]
    return tokens, day, force


def _away(price: Decimal, reference: Decimal | None, what: str) -> str | None:
    """Why `price` needs a second look: more than 10% from `reference`."""
    if reference is None or reference <= 0:
        return None
    gap = price / reference - 1
    if abs(gap) <= PRICE_BAND:
        return None
    direction = "above" if gap > 0 else "below"
    return f"{cad(price)} is {abs(gap) * 100:.1f}% {direction} {what} {cad(reference)}"


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


def _entry_session(signal: TradeSignal) -> date:
    """The signal's entry session: the New York date of its expiry (that session's close)."""
    return signal.expires_at.astimezone(NEW_YORK).date()


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
        self.confirming: Confirming | None = None
        self._tokens = 0

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

    def handle_text(self, chat_id: int, text: str, update_id: int | None = None) -> list[Reply]:
        """A command or a reply. `update_id` (Telegram's) is recorded with any write, so an
        update Telegram delivers again after a crash is not recorded twice."""
        if not self.allowed(chat_id):
            return []
        text = text.strip()
        try:
            with self._sessions() as session:
                if text.startswith("/"):
                    name, *args = text.split()
                    return self._command(
                        session, name[1:].split("@")[0].lower(), args, update_id
                    )
                if self.awaiting is not None:
                    return self._answer(session, self.awaiting, text.split(), update_id)
        except (UsageError, LedgerError) as error:
            message = str(error)
            return [Reply(message if message.startswith("⚠️") else f"⚠️ {message}")]
        return [Reply("Send /help for the commands.")]

    def handle_callback(
        self, chat_id: int, data: str, message_id: int, message_text: str,
        update_id: int | None = None,
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
                if kind in ("ok", "no"):
                    return self._confirm_button(
                        session, ledger, kind, int(rest), message_id, message_text, update_id
                    )
                if kind in ("b", "s", "k"):
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
            self.awaiting = self.confirming = None
            label = reason.replace("_", " ")
            return [Reply(f"{message_text}\n⏭ Skipped ({label})", edit=message_id)]
        cdr = session.get(Ticker, signal.cdr_ticker_id)
        assert cdr is not None
        self.awaiting = Awaiting("buy", signal_id, message_id, message_text)
        self.confirming = None
        when = (
            f"The date is the entry session, {_entry_session(signal)}; a YYYY-MM-DD token after "
            "the price changes it."
        )
        if signal.order_type == "limit":
            return [Reply(
                f"What was your average fill price for the {units_text(signal.suggested_units)} "
                f"of {cdr.symbol}? Reply with the price from Wealthsimple's confirmation, like "
                f"`{signal.cdr_signal_close:.2f}`, or `UNITS PRICE` if you bought a "
                f"different number of units. {when}"
            )]
        return [Reply(
            f"How many units of {cdr.symbol} did you buy, and at what average price? Reply "
            f"`UNITS PRICE` from Wealthsimple's confirmation, like "
            f"`{signal.suggested_units.normalize():f} {signal.cdr_signal_close:.2f}`. "
            f"{when}"
        )]

    def _alert_button(
        self, ledger: Ledger, kind: str, alert_id: int, message_id: int, message_text: str
    ) -> list[Reply]:
        alert = ledger.exit_alert(alert_id)
        if alert.status != "sent":
            return [Reply(f"Already logged: exit alert {alert_id} is {alert.status}.")]
        if kind == "i":
            ledger.mark_exit_alert(alert_id, "ignored")
            self.awaiting = self.confirming = None
            return [Reply(
                f"{message_text}\nIgnored: this counts as a miss in the scale-up check, and the "
                "position is managed again from the next session.",
                edit=message_id,
            )]
        self.awaiting = Awaiting("sell", alert_id, message_id, message_text)
        self.confirming = None
        return [Reply(
            "How many units did you sell, and at what price? Reply like `2 9.70` or `all 9.70` "
            "(a YYYY-MM-DD token after the price sets the date; a price alone asks to confirm "
            "a sale of every unit)."
        )]

    def _confirm_button(
        self, session: Session, ledger: Ledger, kind: str, token: int, message_id: int,
        message_text: str, update_id: int | None,
    ) -> list[Reply]:
        confirming = self.confirming
        if confirming is None or confirming.token != token:
            return [Reply("⚠️ That button is no longer valid.")]
        self.confirming = None
        if kind == "no":
            return [Reply(f"{message_text}\nCancelled: nothing recorded.", edit=message_id)]
        replies, fill_id = self._record(session, ledger, confirming.request, update_id)
        return [*replies, Reply(f"{message_text}\n✅ Recorded (fill {fill_id}).", edit=message_id)]

    # --- Replies after ✅ ------------------------------------------------------------------

    def _answer(
        self, session: Session, awaiting: Awaiting, tokens: list[str], update_id: int | None
    ) -> list[Reply]:
        ledger = self._ledger(session)
        if awaiting.kind == "buy":
            request, checks = self._signal_buy(session, ledger, awaiting, tokens)
            return self._submit(session, ledger, request, checks, update_id)
        alert = ledger.exit_alert(awaiting.row_id)
        cdr = session.get(Ticker, alert.cdr_ticker_id)
        assert cdr is not None
        numbers, day, _ = _split_reply(tokens)
        if not 1 <= len(numbers) <= 2:
            raise UsageError("⚠️ Reply like `2 9.70`, `all 9.70`, or `all 9.70 2026-10-09`.")
        request, checks = self._sale(
            ledger, cdr, "all" if len(numbers) == 1 else numbers[0], numbers[-1], day, alert.id,
        )
        request = replace(
            request, message_id=awaiting.message_id, message_text=awaiting.message_text
        )
        if len(numbers) == 1:  # a price alone: never silently "all"
            return self._ask(
                request, checks,
                f"Sell ALL {units_text(request.quantity)} {cdr.symbol} @ {cad(request.price)} "
                f"on {request.trade_date}?",
            )
        return self._submit(session, ledger, request, checks, update_id)

    def _signal_buy(
        self, session: Session, ledger: Ledger, awaiting: Awaiting, tokens: list[str]
    ) -> tuple[FillRequest, list[str]]:
        signal = ledger.signal(awaiting.row_id)
        assert signal.id is not None
        cdr = session.get(Ticker, signal.cdr_ticker_id)
        assert cdr is not None
        numbers, day, force = _split_reply(tokens)
        if len(numbers) == 1 and signal.order_type != "limit":
            example = f"{signal.suggested_units.normalize():f} {signal.cdr_signal_close:.2f}"
            raise UsageError(
                f"⚠️ A fractional order needs both: reply `UNITS PRICE`, like `{example}`."
            )
        if len(numbers) not in (1, 2):
            raise UsageError("⚠️ Reply like `10.45`, `3 10.45`, or `3 10.45 2026-10-06`.")
        levels = ledger.signal_levels(signal.id)
        scale = signal.cdr_signal_close / levels.cdr_signal_close  # CDR splits since the signal
        suggested = signal.suggested_units * scale
        quantity = suggested if len(numbers) == 1 else _number(numbers[0], "the units")
        price = _number(numbers[-1], "the price")
        trade_date = _entry_session(signal) if day is None else day
        last = _entry_session(signal)
        if not signal.as_of < trade_date <= last and not force:
            raise UsageError(
                f"⚠️ {trade_date} is outside signal {signal.id}'s entry window (after "
                f"{signal.as_of}, through {last}). If Wealthsimple shows that date, add `force`."
            )
        checks = [
            reason for reason in (
                _away(price, levels.cdr_signal_close, "the signal's CDR reference"),
                (
                    f"{units_text(quantity)} is more than {UNITS_BAND}× the suggested "
                    f"{suggested.normalize():f}"
                    if quantity > UNITS_BAND * suggested else None
                ),
            ) if reason is not None
        ]
        request = FillRequest(
            side="buy", cdr_symbol=cdr.symbol, quantity=quantity, price=price,
            trade_date=trade_date, force=force, signal_id=signal.id,
            message_id=awaiting.message_id, message_text=awaiting.message_text,
        )
        return request, checks

    def _sale(
        self, ledger: Ledger, cdr: Ticker, units: str, price: str, day: date | None,
        alert_id: int | None,
    ) -> tuple[FillRequest, list[str]]:
        """A sale of `units` (or `all`) of `cdr`, linked to `alert_id`, and why it needs a
        second look."""
        book = ledger.books().get(cdr.symbol)
        held = book.units if book is not None else Decimal(0)
        if held <= 0:
            raise UsageError(f"⚠️ no {cdr.symbol} units are held")
        quantity = held if units.lower() == "all" else _number(units, "the units")
        value = _number(price, "the price")
        mark = ledger.cdr_mark(cdr, self._today())
        request = FillRequest(
            side="sell", cdr_symbol=cdr.symbol, quantity=quantity, price=value,
            trade_date=self._today() if day is None else day, exit_alert_id=alert_id,
        )
        checks = [c for c in (_away(value, mark, "the latest CDR mark"),) if c is not None]
        return request, checks

    # --- Recording -------------------------------------------------------------------------

    def _submit(
        self, session: Session, ledger: Ledger, request: FillRequest, checks: list[str],
        update_id: int | None,
    ) -> list[Reply]:
        """Record the fill, or ask first when something about it looks wrong."""
        if checks:
            return self._ask(request, checks)
        replies, _ = self._record(session, ledger, request, update_id)
        return replies

    def _ask(self, request: FillRequest, checks: list[str], question: str = "") -> list[Reply]:
        self._tokens += 1
        self.confirming = Confirming(self._tokens, request)
        if not question:
            linked = "" if request.signal_id is None else f" for signal {request.signal_id}"
            question = (
                f"Record: {request.side} {units_text(request.quantity)} {request.cdr_symbol} @ "
                f"{cad(request.price)} on {request.trade_date}{linked}?"
            )
        lines = ["⚠️ Check this before I record it:", *(f"• {c}" for c in checks)] if checks else []
        buttons = ((Button("✅ Confirm", f"ok:{self._tokens}"),
                    Button("Cancel", f"no:{self._tokens}")),)
        return [Reply("\n".join([*lines, question]), buttons)]

    def _claim(self, session: Session, update_id: int | None) -> None:
        """Written with the ledger row that follows, in one transaction: a redelivered update
        is refused instead of recorded twice."""
        if update_id is None:
            return
        if session.get(BotUpdate, update_id) is not None:
            raise UsageError(f"⚠️ Already recorded: Telegram delivered update {update_id} again.")
        session.add(BotUpdate(update_id=update_id))

    def _record(
        self, session: Session, ledger: Ledger, request: FillRequest, update_id: int | None
    ) -> tuple[list[Reply], int]:
        self._claim(session, update_id)
        fill = ledger.record_fill(
            cdr_symbol=request.cdr_symbol, side=request.side, quantity=request.quantity,
            price_cad=request.price, trade_date=request.trade_date,
            signal_id=request.signal_id, exit_alert_id=request.exit_alert_id,
            force=request.force,
        )
        assert fill.id is not None
        self.awaiting = self.confirming = None
        did = f"{units_text(fill.quantity)} @ {cad(fill.price_cad)} (fill {fill.id})"
        if request.side == "buy" and request.signal_id is not None:
            stop = ledger.current_stop(request.signal_id)
            text = (
                f"✅ Bought {units_text(fill.quantity)} {request.cdr_symbol} @ "
                f"{cad(fill.price_cad)} on {fill.trade_date} (fill {fill.id}), for signal "
                f"{request.signal_id}. Stop {cad(stop.cdr)} / {usd(stop.us)}; the evening scan "
                "manages it from here."
            )
            done = f"Bought {did}"
        elif request.side == "buy":
            text = (
                f"✅ Bought {units_text(fill.quantity)} {request.cdr_symbol} @ "
                f"{cad(fill.price_cad)} on {fill.trade_date} (fill {fill.id}), manual: no stop "
                "or exit alerts."
            )
            done = f"Bought {did}"
        else:
            text = self._sold_text(ledger, request.cdr_symbol, fill)
            done = f"Sold {did}"
        replies = [Reply(text)]
        if request.message_id is not None:
            replies.append(
                Reply(f"{request.message_text}\n✅ {done}", edit=request.message_id)
            )
        return replies, fill.id

    def _sold_text(self, ledger: Ledger, cdr_symbol: str, fill: Fill) -> str:
        text = (
            f"✅ Sold {units_text(fill.quantity)} {cdr_symbol} @ {cad(fill.price_cad)} on "
            f"{fill.trade_date} (fill {fill.id})."
        )
        closed = next(
            (t for t in ledger.closed_trades() if t.episode.fills[-1].id == fill.id), None
        )
        if closed is None:
            left = ledger.books()[cdr_symbol].units
            return f"{text} Still holding {units_text(left)}."
        text += f" Position closed: P&L {signed_cad(closed.pnl)}"
        return text + ("." if closed.r is None else f" · {r_text(closed.r)} on planned risk.")

    # --- Commands --------------------------------------------------------------------------

    def _command(
        self, session: Session, name: str, args: list[str], update_id: int | None
    ) -> list[Reply]:
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
            request, checks = self._manual_buy(session, ledger, args)
            return self._submit(session, ledger, request, checks, update_id)
        if name == "sell":
            if len(args) not in (3, 4):
                raise UsageError("⚠️ Usage: /sell SYMBOL QTY|all PRICE [YYYY-MM-DD]")
            cdr = _cdr(session, args[0])
            alert = session.exec(
                select(ExitAlert).where(
                    ExitAlert.cdr_ticker_id == cdr.id, ExitAlert.status == "sent"
                )
            ).first()
            request, checks = self._sale(
                ledger, cdr, args[1], args[2], _day(args[3]) if len(args) == 4 else None,
                None if alert is None else alert.id,
            )
            return self._submit(session, ledger, request, checks, update_id)
        if name == "void":
            if len(args) < 2 or not args[0].isdigit():
                raise UsageError("⚠️ Usage: /void FILL_ID REASON")
            ledger.void_fill(int(args[0]), " ".join(args[1:]))
            return [Reply(f"Fill {args[0]} voided: {' '.join(args[1:])}. Cash is now "
                          f"{cad(ledger.cash())}.")]
        if name in ("deposit", "withdraw"):
            force = name == "withdraw" and bool(args) and args[-1].lower() == "force"
            args = args[:-1] if force else args
            if len(args) != 1:
                usage = "AMOUNT [force]" if name == "withdraw" else "AMOUNT"
                raise UsageError(f"⚠️ Usage: /{name} {usage}")
            amount = _number(args[0], "the amount")
            if amount <= 0:
                raise UsageError("⚠️ the amount must be above 0")
            self._claim(session, update_id)
            ledger.record_cash(
                amount if name == "deposit" else -amount, today, f"/{name}", force=force
            )
            what = "Deposit" if name == "deposit" else "Withdrawal"
            return [Reply(f"{what} of {cad(amount)} recorded on {today}. Cash is "
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

    def _manual_buy(
        self, session: Session, ledger: Ledger, args: list[str]
    ) -> tuple[FillRequest, list[str]]:
        force = bool(args) and args[-1].lower() == "force"
        args = args[:-1] if force else args
        if len(args) not in (3, 4):
            raise UsageError("⚠️ Usage: /buy SYMBOL QTY PRICE [YYYY-MM-DD] [force]")
        cdr = _cdr(session, args[0])
        request = FillRequest(
            side="buy", cdr_symbol=cdr.symbol, quantity=_number(args[1], "the units"),
            price=_number(args[2], "the price"),
            trade_date=_day(args[3]) if len(args) == 4 else self._today(), force=force,
        )
        mark = ledger.cdr_mark(cdr, self._today())
        checks = [c for c in (_away(request.price, mark, "the latest CDR mark"),) if c is not None]
        return request, checks
