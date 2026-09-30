"""Shared by the spec 04 ledger tests: the hand-checked examples, tickers, prices, a ledger."""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from sqlmodel import Session

from paper_helpers import evening
from signalbench.db.models import Ticker, TickerKind, TradeSignal
from signalbench.live.acb import SplitEvent, Trade
from signalbench.live.ledger import Ledger
from strategy_helpers import WeekdaySessions

EXAMPLES = Path(__file__).parent / "fixtures" / "live_ledger_examples.yaml"


def load_example(name: str) -> dict[str, Any]:
    examples: dict[str, Any] = yaml.safe_load(EXAMPLES.read_text(encoding="utf-8"))
    return dict(examples[name])


def example_events(example: dict[str, Any]) -> tuple[list[Trade], list[SplitEvent]]:
    """An example's fills and splits, numbered in file order."""
    trades: list[Trade] = []
    splits: list[SplitEvent] = []
    for number, event in enumerate(example["events"], start=1):
        day: date = event["date"]
        if "split" in event:
            splits.append(SplitEvent(number, day, Decimal(event["split"])))
        else:
            trades.append(
                Trade(number, day, event["side"], Decimal(event["quantity"]),
                      Decimal(event["price"]), Decimal(event.get("fee", "0")))
            )
    return trades, splits


TODAY = date(2026, 12, 31)  # the owner's date in these tests, after every fill


def add_pair(
    session: Session, us: str = "NVDA", cdr: str = "ZNVD", sector: str = "Information Technology"
) -> tuple[Ticker, Ticker]:
    """A US stock and its CDR, as `signalbench seed` stores them."""
    stock = Ticker(symbol=us, company_name=us, sector=sector, kind=TickerKind.us_stock,
                   price_symbol=us)
    session.add(stock)
    session.flush()
    receipt = Ticker(symbol=cdr, company_name=us, sector=sector, kind=TickerKind.cdr,
                     price_symbol=f"{cdr}.NE", us_ticker_id=stock.id)
    session.add(receipt)
    session.commit()
    return stock, receipt


def make_ledger(session: Session, today: date = TODAY) -> Ledger:
    """A ledger on the weekday calendar of strategy_helpers (no holidays)."""
    return Ledger(session, calendar=WeekdaySessions(), today=today)


def record_example(ledger: Ledger, example: dict[str, Any], symbol: str = "ZTST") -> None:
    """An example's deposit and fills (splits are recorded by the caller)."""
    deposit = example["deposit"]
    ledger.record_cash(Decimal(deposit["amount"]), deposit["date"], "deposit")
    for event in example["events"]:
        if "split" in event:
            continue
        ledger.record_fill(
            cdr_symbol=symbol, side=event["side"], quantity=Decimal(event["quantity"]),
            price_cad=Decimal(event["price"]), trade_date=event["date"],
            fee_cad=Decimal(event.get("fee", "0")),
        )


def send_signal(
    ledger: Ledger,
    as_of: date,
    *,
    us: str = "NVDA",
    cdr: str = "ZNVD",
    us_close: str = "200",
    us_stop: str = "188",
    cdr_close: str = "40",
    units: str = "1",
) -> TradeSignal:
    """A sent signal: stop_pct 6%, so cdr_stop 37.60 with the defaults. It expires at 16:00
    New York on the next weekday (its entry session)."""
    entry = WeekdaySessions().next_sessions(as_of, 1)[0]
    return ledger.record_signal(
        as_of=as_of, us_symbol=us, cdr_symbol=cdr, us_signal_close=Decimal(us_close),
        us_stop=Decimal(us_stop), cdr_signal_close=Decimal(cdr_close),
        suggested_units=Decimal(units), order_type="limit", risk_amount_cad=Decimal("2.40"),
        explanation="Closed at a 20-session high.", expires_at=evening(entry, 16),
    )
