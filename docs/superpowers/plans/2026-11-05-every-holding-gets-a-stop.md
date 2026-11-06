# Every Holding Gets a Stop: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

## In plain words (for the owner)

**The problem.** The bot only watches a holding when its buy is linked to one of its signals, because the signal is where the stop comes from. A buy recorded with `/buy` has no link, so it gets no stop and no sell alerts. Today that leaves two of the three holdings unwatched: ZAMZ and ZUNH.

**What changes:**

1. **Every new buy gets watched.** When you record a buy with `/buy`, the bot first looks for one of its own signals for that stock from the last 5 trading days and links it. If there isn't one, it sets the stop the strategy would have set: the US close on the trading day before your buy, minus 2 × the stock's average daily range (ATR, the same rule every Breakout signal uses). Either way, the holding then gets the nightly stop check, trailing stop raises, and sell alerts, like any other.
2. **The two holdings you already have get fixed** with a new command, run once on the server:
   - **ZAMZ** is linked to its saved Oct 19 signal (stop C$11.24).
   - **ZUNH** gets the strategy's stop worked out from the Oct 14 prices. Its saved buy date is also corrected from Oct 7 to **Oct 15**, as Wealthsimple shows.

   For each one, the command says whether the stock has already closed below its stop since you bought it. If it has, the rules say sell.
3. **The scale-up check stays honest.** A stop the bot set itself isn't a signal you were sent, so those buys don't count toward the "took 80% of signals" and "bought close to the signal price" checks. Sell alerts on them still count toward "never missed a sell alert".

**What doesn't change:** how signals are found, how big buys are, the 15% pause, and the crypto paper trading (it has its own folder and database).

**Before it goes live,** you'll be asked to OK three things: pushing the new code, running it on ubuntuworker (the bot is stopped for about a minute), and running the fix for ZAMZ and ZUNH.

**Separate from this plan:** the ZVST sell alert from Oct 22 is still open, and nothing here changes it.

---

**Goal:** Every opening buy in the live ledger is linked to a signal, either the scan's own or an "adopted" one with the Breakout stop rule, so every holding gets stops, raises, and exit alerts.

**Architecture:** Add `trade_signals.source` (`scan` | `adopted`). An adopted signal is an ordinary `TradeSignal` row, so `stop_in_force`, `review_session`, exit alerts, and raises all work unchanged. A new module, `live/adopt.py`, finds a matching scan signal or builds the adopted one: the US close and `close − stop_atr_mult × ATR` at the last NYSE session before the buy, from the same `MarketView` the scan uses. The bot's `/buy` and a new `signalbench ledger adopt FILL_ID` command call it. The outbox never sends adopted rows, and the scale-up check leaves them out of checks 1, 2, and 4.

**Tech Stack:** Python 3.12, SQLModel/SQLAlchemy, Alembic, Typer, pytest (in-memory SQLite), uv.

**Facts this plan relies on (checked 2026-11-05):**
- `src/signalbench/live/` is identical on `main` and on the live tag `live-v2` (9ae1ba3b). `db/models.py` on `main` has only the crypto paper tables on top.
- The live DB is at migration `0016`. `main` has `0017_crypto_ticker_kind` and `0018_paper_crypto`, both additive. Upgrading the live DB applies them too, which is harmless.
- Live rows: signal 2 = AMZN/ZAMZ, as_of 2026-10-19, `expired`, us_stop 249.4420, cdr_stop 11.2437. Fill 3 = ZUNH buy 3 @ 11.60 dated **2026-10-07** (Wealthsimple: 2026-10-15), no signal. Fill 4 = ZAMZ buy 3 @ 11.89 dated 2026-10-19, no signal.
- Breakout stop rule: `src/signalbench/strategy/setups.py:41`, `snap.close - b.stop_atr_mult * snap.atr` (stop_atr_mult 2.0, atr_period 14).
- `send_unsent` (`src/signalbench/live/outbox.py:244`) sends every `TradeSignal` with no `telegram_message_id`, so adopted rows **must** be filtered out, or they'd go out as new buy signals.

## File structure

| File | Change |
| --- | --- |
| `alembic/versions/0019_signal_source.py` | Create: add `trade_signals.source`, default `scan` |
| `src/signalbench/db/models.py` | Modify: `TradeSignal.source` |
| `src/signalbench/live/book.py` | Modify: `record_signal(..., source=)`; `matching_signal()` |
| `src/signalbench/live/adopt.py` | Create: `Adopter`, the stop for an unsignalled buy, and `adopt_fill()` |
| `src/signalbench/live/outbox.py` | Modify: skip adopted signals |
| `src/signalbench/live/ledger.py` | Modify: scale-up leaves out adopted signals and their buys |
| `src/signalbench/live/bot.py` | Modify: `/buy` links or adopts |
| `src/signalbench/cli.py` | Modify: pass an `Adopter` to the bot; add `ledger adopt` |
| `tests/test_live_adopt.py` | Create |
| `tests/test_live_bot.py`, `tests/test_live_scaleup.py`, `tests/test_live_outbox.py` | Modify |
| `docs/superpowers/specs/2026-09-22-swing-assistant-04-ledger-design.md`, `...-05-bot-scan-design.md` | Modify: a dated entry under Changes |

Work on a branch: `git switch -c claude/every-holding-gets-a-stop`.

---

### Task 1: The `source` column

**Files:**
- Create: `alembic/versions/0019_signal_source.py`
- Modify: `src/signalbench/db/models.py` (class `TradeSignal`)
- Test: `tests/test_live_schema.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_live_schema.py`)

```python
def test_a_signal_is_from_the_scan_unless_adopted(session: Session) -> None:
    add_pair(session)
    ledger = make_ledger(session)
    signal = send_signal(ledger, date(2026, 10, 5))
    assert signal.source == "scan"
```

Add the imports the file lacks: `from datetime import date`, `from live_helpers import add_pair, make_ledger, send_signal`.

- [ ] **Step 2: Run it and check that it fails**

Run: `uv run pytest tests/test_live_schema.py::test_a_signal_is_from_the_scan_unless_adopted -v`
Expected: FAIL with `AttributeError: 'TradeSignal' object has no attribute 'source'`

- [ ] **Step 3: Add the field** in `TradeSignal`, after `skip_reason`:

```python
    source: str = Field(default="scan", sa_column_kwargs={"server_default": "scan"})
    # scan | adopted: an adopted signal is the Breakout stop the ledger set for a buy with no
    # signal; it is never sent and the scale-up check leaves it out
```

- [ ] **Step 4: Write the migration** `alembic/versions/0019_signal_source.py`:

```python
"""Every holding gets a stop: trade_signals.source (scan | adopted)

Revision ID: 0019_signal_source
Revises: 0018_paper_crypto
Create Date: 2026-11-05 22:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_signal_source"
down_revision: str | None = "0018_paper_crypto"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "trade_signals",
        sa.Column("source", sa.String(), nullable=False, server_default="scan"),
    )


def downgrade() -> None:
    op.drop_column("trade_signals", "source")
```

Check that `0018_paper_crypto` is the real revision id: `grep -n "^revision" alembic/versions/0018_paper_crypto.py`.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_live_schema.py tests/test_alembic_enums.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/0019_signal_source.py src/signalbench/db/models.py tests/test_live_schema.py
git commit -m "feat: trade_signals.source marks adopted signals"
```

---

### Task 2: Record an adopted signal, and find a matching scan signal

**Files:**
- Modify: `src/signalbench/live/book.py` (`record_signal`, new `matching_signal`)
- Test: `tests/test_live_signals.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_live_signals.py`)

```python
def test_an_adopted_signal_is_recorded_with_its_source(session: Session) -> None:
    add_pair(session)
    ledger = make_ledger(session)
    signal = ledger.record_signal(
        as_of=date(2026, 10, 5), us_symbol="NVDA", cdr_symbol="ZNVD",
        us_signal_close=Decimal("200"), us_stop=Decimal("188"),
        cdr_signal_close=Decimal("40"), suggested_units=Decimal("3"), order_type="market",
        risk_amount_cad=Decimal("7.20"), explanation="Adopted.", expires_at=evening(
            date(2026, 10, 6), 16), source="adopted",
    )
    assert (signal.source, signal.cdr_stop) == ("adopted", Decimal("37.6000"))


def test_a_buy_matches_a_sent_or_expired_scan_signal_from_the_last_5_sessions(
    session: Session,
) -> None:
    add_pair(session)
    ledger = make_ledger(session)
    signal = send_signal(ledger, date(2026, 10, 5))  # a Monday
    ledger.mark_signal(signal.id, "expired")
    assert ledger.matching_signal("ZNVD", date(2026, 10, 5)).id == signal.id  # same day
    assert ledger.matching_signal("ZNVD", date(2026, 10, 12)).id == signal.id  # 5th session
    assert ledger.matching_signal("ZNVD", date(2026, 10, 13)) is None  # 6th
    assert ledger.matching_signal("ZNVD", date(2026, 10, 2)) is None  # before it


def test_a_skipped_signal_is_never_matched(session: Session) -> None:
    add_pair(session)
    ledger = make_ledger(session)
    signal = send_signal(ledger, date(2026, 10, 5))
    ledger.mark_signal(signal.id, "skipped", "disagree")
    assert ledger.matching_signal("ZNVD", date(2026, 10, 6)) is None
```

`evening` comes from wherever `live_helpers.py` imports it; import it the same way.

- [ ] **Step 2: Run them and check that they fail**

Run: `uv run pytest tests/test_live_signals.py -k "adopted or matches or never_matched" -v`
Expected: FAIL (`unexpected keyword argument 'source'`, `no attribute 'matching_signal'`)

- [ ] **Step 3: Implement.** In `record_signal`, add the parameter `source: SignalSource = "scan"` after `expires_at`, check it with `check_choice(source, get_args(SignalSource), "source")`, and pass `source=source` to `TradeSignal(...)`. Next to the other `Literal` aliases at the top of `book.py`, add:

```python
SignalSource = Literal["scan", "adopted"]
MATCH_SESSIONS = 5  # a buy links to a scan signal from up to this many sessions before it
```

Add this method after `pending_signals`:

```python
    def matching_signal(self, cdr_symbol: str, trade_date: date) -> TradeSignal | None:
        """The newest sent or expired scan signal for this CDR whose session is on or before
        `trade_date` and at most MATCH_SESSIONS NYSE sessions before it: the signal a buy
        recorded with /buy belongs to. Skipped and withdrawn signals are never matched."""
        cdr = self._cdr(cdr_symbol)
        earliest = self._calendar.previous_sessions(trade_date, MATCH_SESSIONS)[0]
        return self._session.exec(
            select(TradeSignal)
            .where(
                TradeSignal.cdr_ticker_id == cdr.id, TradeSignal.source == "scan",
                col(TradeSignal.status).in_(("sent", "expired")),
                col(TradeSignal.as_of) >= earliest, col(TradeSignal.as_of) <= trade_date,
            )
            .order_by(col(TradeSignal.as_of).desc())
        ).first()
```

Check that the calendar has `previous_sessions(day, n)`, returning the n sessions before `day`, oldest first: `grep -n "def " src/signalbench/market/calendar.py`. If it doesn't, add it to `Sessions` next to `next_sessions`, mirroring that method, with its own test in `tests/test_calendar.py`. The window above treats `trade_date` itself as session 0, so 5 sessions back from Monday 10-12 is Monday 10-05.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_live_signals.py tests/test_live_ledger.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/live/book.py tests/test_live_signals.py
git commit -m "feat: record adopted signals and match a buy to a recent scan signal"
```

---

### Task 3: `live/adopt.py`, which links or adopts an opening buy

**Files:**
- Create: `src/signalbench/live/adopt.py`
- Test: `tests/test_live_adopt.py`

The Breakout stop for an unsignalled buy uses the same `MarketView` the scan uses (`live_market`), at the last NYSE session **before** the buy's trade date: that session plays the role of the signal session. The CDR reference is that session's US close × `traded_ratio` (`scan.py:627`, `size`).

- [ ] **Step 1: Write the failing tests** `tests/test_live_adopt.py`

Build the world with `scan_helpers.make_world`, which already stores NVDA/ZNVD bars and QQQ. Read `tests/scan_helpers.py` for `DAYS`, `make_world`, and the stored closes, then pick a session `D` inside `DAYS` with at least 15 bars before it, so ATR(14) exists.

```python
"""Every holding gets a stop: a buy with no signal is linked to a recent scan signal or
gets the Breakout stop the strategy would have set (adopted)."""

from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlmodel import Session

from scan_helpers import DAYS, World, make_world
from signalbench.live.adopt import Adopter, adopt_fill


def adopter(world: World) -> Adopter:
    return Adopter(config=world.config, universe=world.universe, calendar=world.ledger._calendar)


def test_an_unsignalled_buy_gets_close_minus_2_atr_at_the_session_before(
    session: Session, tmp_path: Path
) -> None:
    world = make_world(session, tmp_path)
    buy_day = DAYS[-1]
    signal = adopter(world).signal_for_buy(session, world.ledger, "ZXOM", buy_day, Decimal("3"))
    snap = world.market(DAYS[-2]).at(DAYS[-2]).snapshot("XOM")
    expected_stop = Decimal(repr(snap.close - 2.0 * snap.atr)).quantize(Decimal("0.0001"))
    assert (signal.source, signal.status, signal.as_of) == ("adopted", "sent", DAYS[-2])
    assert signal.us_stop == expected_stop
    assert signal.telegram_message_id is None


def test_a_buy_with_a_recent_scan_signal_links_to_it(session: Session, tmp_path: Path) -> None:
    world = make_world(session, tmp_path)
    world.scan(world.B)  # the NVDA entry
    scan_signal = world.signals()[0]
    entry_day = world.ledger._calendar.next_sessions(scan_signal.as_of, 1)[0]
    found = adopter(world).signal_for_buy(session, world.ledger, "ZNVD", entry_day, Decimal("3"))
    assert found.id == scan_signal.id


def test_adopt_fill_voids_a_manual_opening_buy_and_records_it_linked(
    session: Session, tmp_path: Path
) -> None:
    world = make_world(session, tmp_path)
    ledger = world.ledger
    manual = ledger.record_fill(cdr_symbol="ZXOM", side="buy", quantity=Decimal("3"),
                                price_cad=Decimal("10.20"), trade_date=DAYS[-3])
    result = adopt_fill(session, ledger, adopter(world), manual.id, trade_date=DAYS[-2])
    assert result.old_fill_id == manual.id
    assert result.fill.signal_id == result.signal.id
    assert result.fill.trade_date == DAYS[-2]
    assert [p.managed for p in ledger.positions(DAYS[-1])] == [True]
```

The helper names `world.config`, `world.universe`, `world.market(day)`, `world.B`, and `world.signals()` are **assumptions**. Check `tests/scan_helpers.py` and use what `World` actually exposes. If `World` has no market helper, build one with `live_market(session, world.universe, world.config, calendar, day)` from `signalbench.live.review`. Change only the test's access paths, never the expected values.

- [ ] **Step 2: Run them and check that they fail**

Run: `uv run pytest tests/test_live_adopt.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'signalbench.live.adopt'`

- [ ] **Step 3: Write `src/signalbench/live/adopt.py`**

```python
"""Every holding gets a stop: an opening buy with no signal is linked to the scan's own
signal for that CDR from the last few sessions, or to an adopted signal with the Breakout
stop the strategy would have set at the session before the buy (close - stop_atr_mult x ATR).

An adopted signal is an ordinary trade_signals row with source `adopted`, so stops, raises,
and exit alerts work on it unchanged. It is never sent, and the scale-up check leaves it out."""

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

from sqlmodel import Session

from signalbench.db.models import Fill, TradeSignal
from signalbench.ingest.cdr import CdrEntry
from signalbench.live.book import LedgerError, q4
from signalbench.live.ledger import Ledger
from signalbench.live.review import live_market
from signalbench.market.calendar import NEW_YORK, Sessions
from signalbench.strategy.config import StrategyConfig


@dataclass(frozen=True)
class Adopter:
    """What it takes to work out a Breakout stop: the live config, the universe, the NYSE
    calendar."""

    config: StrategyConfig
    universe: list[CdrEntry]
    calendar: Sessions

    def signal_for_buy(
        self, session: Session, ledger: Ledger, cdr_symbol: str, trade_date: date,
        quantity: Decimal,
    ) -> TradeSignal:
        """The scan signal this buy belongs to, or a new adopted one."""
        found = ledger.matching_signal(cdr_symbol, trade_date)
        if found is not None:
            return found
        return self._adopt(session, ledger, cdr_symbol, trade_date, quantity)

    def _adopt(
        self, session: Session, ledger: Ledger, cdr_symbol: str, trade_date: date,
        quantity: Decimal,
    ) -> TradeSignal:
        entry = next((e for e in self.universe if e.cdr_symbol == cdr_symbol), None)
        if entry is None:
            raise LedgerError(f"{cdr_symbol} is not in the universe: no stop can be worked out")
        day = self.calendar.previous_sessions(trade_date, 1)[0]
        market = live_market(session, self.universe, self.config, self.calendar, day)
        snap = market.at(day).snapshot(entry.us_symbol)
        if snap is None or snap.atr is None or snap.date != day:
            raise LedgerError(f"no {entry.us_symbol} bar with an ATR on {day}: no stop")
        us_close = q4(Decimal(repr(snap.close)))
        us_stop = q4(Decimal(repr(snap.close - self.config.breakout.stop_atr_mult * snap.atr)))
        cdr = ledger.cdr_ticker(cdr_symbol)
        ratio = ledger.traded_ratio(cdr, day)
        if ratio is None:
            raise LedgerError(f"{cdr_symbol} has no traded CDR price by {day}: no stop")
        cdr_close = q4(us_close * ratio)
        risk = q4(quantity * cdr_close * (us_close - us_stop) / us_close)
        expires = datetime.combine(trade_date, time(16), NEW_YORK)
        return ledger.record_signal(
            as_of=day, us_symbol=entry.us_symbol, cdr_symbol=cdr_symbol,
            us_signal_close=us_close, us_stop=us_stop, cdr_signal_close=cdr_close,
            suggested_units=quantity, order_type="market", risk_amount_cad=risk,
            explanation=(
                f"Adopted: a buy with no signal. Breakout stop = the {day} close − "
                f"{self.config.breakout.stop_atr_mult:g} × ATR({self.config.atr_period})."
            ),
            expires_at=expires, source="adopted",
        )


@dataclass(frozen=True)
class Adopted:
    old_fill_id: int
    fill: Fill
    signal: TradeSignal
    breaches: list[date]  # sessions since the entry whose US close was at or below the stop


def adopt_fill(
    session: Session, ledger: Ledger, adopter: Adopter, fill_id: int,
    *, trade_date: date | None = None,
) -> Adopted:
    """Give an existing manual opening buy a stop: void it and record it again, linked
    (optionally with a corrected trade date). Fills are never edited, only voided."""
    old = session.get(Fill, fill_id)
    if old is None or old.voided or old.side != "buy" or old.signal_id is not None:
        raise LedgerError(f"fill {fill_id} is not a manual buy")
    cdr = ledger.cdr_by_id(old.cdr_ticker_id)
    day = trade_date or old.trade_date
    signal = adopter.signal_for_buy(session, ledger, cdr.symbol, day, old.quantity)
    ledger.void_fill(fill_id, f"adopted: re-recorded linked to signal {signal.id}")
    fill = ledger.record_fill(
        cdr_symbol=cdr.symbol, side="buy", quantity=old.quantity, price_cad=old.price_cad,
        trade_date=day, signal_id=signal.id, fee_cad=old.fee_cad, force=True,
    )
    assert signal.id is not None
    stop = ledger.stop_in_force(signal.id, day).us
    us = ledger.us_ticker_of(cdr)
    breaches = [
        p.date for p in ledger.us_prices(us, ledger.today)
        if p.date >= day and Decimal(p.adj_close) <= stop
    ]
    return Adopted(fill_id, fill, signal, breaches)
```

`ledger.cdr_ticker`, `ledger.cdr_by_id`, `ledger.us_ticker_of`, and `ledger.us_prices` are thin public wrappers to add to `LedgerBook`/`LedgerLevels` over the existing private `_cdr`, `_by_id`, `_us_of`, and `_prices`. Add each as a one-liner with a docstring. `ledger.today` already exists on `LedgerBook` (`book.py:180`). `force=True` on the re-record only waives the cash check: cash is unchanged by void + re-record, but replaying an earlier date can dip it below zero in between. `record_fill` marks a `sent`/`expired` signal `taken`, so adopted signals end up `taken` too.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_live_adopt.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/live/adopt.py src/signalbench/live/book.py src/signalbench/live/levels.py tests/test_live_adopt.py
git commit -m "feat: link or adopt an unsignalled opening buy with the Breakout stop"
```

---

### Task 4: Never send adopted signals; leave them out of the scale-up check

**Files:**
- Modify: `src/signalbench/live/outbox.py:244-247`, `src/signalbench/live/ledger.py:230-246`
- Test: `tests/test_live_outbox.py`, `tests/test_live_scaleup.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_live_outbox.py`, add a test that records an adopted signal (`record_signal(..., source="adopted")`, as in Task 2), calls `send_unsent(...)` the way the file's other tests do, and asserts that nothing was sent and its `telegram_message_id` is still `None`.

In `tests/test_live_scaleup.py`, add:

```python
def test_adopted_signals_and_their_buys_are_left_out_of_the_scale_up_check(
    session: Session,
) -> None:
    add_pair(session)
    ledger = make_ledger(session)
    ledger.record_cash(Decimal("100"), date(2026, 10, 1), "deposit")
    adopted = ledger.record_signal(
        as_of=date(2026, 10, 5), us_symbol="NVDA", cdr_symbol="ZNVD",
        us_signal_close=Decimal("200"), us_stop=Decimal("188"),
        cdr_signal_close=Decimal("40"), suggested_units=Decimal("1"), order_type="market",
        risk_amount_cad=Decimal("2.40"), explanation="Adopted.",
        expires_at=evening(date(2026, 10, 6), 16), source="adopted",
    )
    ledger.record_fill(cdr_symbol="ZNVD", side="buy", quantity=Decimal("1"),
                       price_cad=Decimal("45"), trade_date=date(2026, 10, 6),
                       signal_id=adopted.id)  # 12.5% above its reference
    result = ledger.scale_up_check()
    assert result.take_rate is None
    assert result.mean_slippage is None
```

Check `ScaleUpResult`'s fields (`scaleup.py:41-76`) and adjust the two asserts to how "no inputs" is shown there.

- [ ] **Step 2: Run them and check that they fail**

Run: `uv run pytest tests/test_live_outbox.py tests/test_live_scaleup.py -v`
Expected: the two new tests FAIL

- [ ] **Step 3: Implement.** In `outbox.py`, change the signal query to:

```python
    signals = session.exec(
        select(TradeSignal)
        .where(
            col(TradeSignal.telegram_message_id).is_(None), TradeSignal.source == "scan",
        )
        .order_by(col(TradeSignal.id))
    ).all()
```

In `Ledger.scale_up_check`, keep only scan signals:

```python
        signals = {
            s.id: s for s in self._session.exec(
                select(TradeSignal).where(TradeSignal.source == "scan")
            ).all()
        }
        buys = [
            SignalBuy(f.id, f.price_cad, signals[f.signal_id].cdr_signal_close)
            for f in self._fills()
            if f.side == "buy" and f.signal_id in signals and f.id is not None
        ]
```

Update the docstring to say that adopted signals are left out of checks 1, 2, and 4, while their exit alerts still count in check 3. `closed_managed` still counts adopted positions, because they're managed.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_live_outbox.py tests/test_live_scaleup.py tests/test_live_scan.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/live/outbox.py src/signalbench/live/ledger.py tests/test_live_outbox.py tests/test_live_scaleup.py
git commit -m "feat: adopted signals are never sent and stay out of the scale-up check"
```

---

### Task 5: `/buy` links or adopts

**Files:**
- Modify: `src/signalbench/live/bot.py` (`BotBrain.__init__`, `_manual_buy`, `_record`, `HELP`), `src/signalbench/cli.py:1022-1053` (`bot_run`)
- Test: `tests/test_live_bot.py`, `tests/scan_helpers.py` (`bot_brain`)

- [ ] **Step 1: Update the tests.** In `test_buy_and_sell_commands_and_their_errors`, the forced XOM buy now gets an adopted stop. Replace its expected reply with:

```python
    [bought] = texts(bot.handle_text(OWNER, "/buy XOM 10 10.20 force"))
    assert bought.startswith("✅ Bought 10 units ZXOM @ C$10.20 on 2026-10-06 (fill 1), stop ")
    assert bought.endswith("(the Breakout stop, no signal); the evening scan manages it from here.")
```

Add:

```python
def test_a_buy_command_for_a_signalled_stock_links_the_signal(world: World) -> None:
    bot = bot_brain(world)
    [bought] = texts(bot.handle_text(OWNER, "/buy ZNVD 3 10.10"))
    assert "for signal 1" in bought
    assert world.session.exec(select(Fill)).one().signal_id == 1
```

If adoption fails (no bars, not in the universe), the bot records the buy as manual and says why:

```python
def test_a_buy_with_no_stop_available_is_recorded_manual_and_says_why(world: World) -> None:
    bot = bot_brain(world, adopter=None)
    [bought] = texts(bot.handle_text(OWNER, "/buy XOM 1 10.20"))
    assert bought.endswith("manual: no stop or exit alerts.")
```

In `scan_helpers.bot_brain`, add a keyword `adopter` that defaults to `Adopter(config=..., universe=..., calendar=WeekdaySessions())` built from the world, and pass it to `BotBrain`.

- [ ] **Step 2: Run them and check that they fail**

Run: `uv run pytest tests/test_live_bot.py -v`
Expected: the changed and new tests FAIL

- [ ] **Step 3: Implement.** `BotBrain.__init__` takes `adopter: Adopter | None = None` and stores it as `self._adopter`. An adopted signal is written **only when the buy is recorded**, never while the bot waits for a confirm. A `sent` row left behind by an unanswered confirm would hold a slot and cash in `pending_signals` and show up in `/signals`.

At the end of `_manual_buy`, after `request` is built, link a scan signal, or mark the request for adoption when this buy opens a position:

```python
        held = ledger.books().get(cdr.symbol)
        if held is None or held.units <= 0:
            found = ledger.matching_signal(cdr.symbol, request.trade_date)
            request = (
                replace(request, signal_id=found.id) if found is not None
                else replace(request, adopt=True)
            )
```

Add `adopt: bool = False`, `adopted: bool = False`, and `note: str = ""` to `FillRequest` (find it with `grep -n "class FillRequest" -A15 src/signalbench/live/bot.py`), and `from dataclasses import replace`.

At the start of `_record`, after `self._claim(...)`, adopt in the same transaction as the fill:

```python
        if request.adopt and self._adopter is not None:
            try:
                signal = self._adopter.signal_for_buy(
                    session, ledger, request.cdr_symbol, request.trade_date, request.quantity
                )
                request = replace(request, signal_id=signal.id, adopted=True)
            except LedgerError as error:
                request = replace(request, note=str(error))
```

Then split the signal-linked buy reply:

```python
        if request.side == "buy" and request.signal_id is not None:
            stop = ledger.current_stop(request.signal_id)
            how = (
                "(the Breakout stop, no signal)" if request.adopted
                else f"for signal {request.signal_id}. Stop"
            )
            text = (
                f"✅ Bought {units_text(fill.quantity)} {request.cdr_symbol} @ "
                f"{cad(fill.price_cad)} on {fill.trade_date} (fill {fill.id}), "
                + (f"stop {cad(stop.cdr)} / {usd(stop.us)} {how}" if request.adopted
                   else f"{how} {cad(stop.cdr)} / {usd(stop.us)}")
                + "; the evening scan manages it from here."
            )
```

The existing linked text (`"..., for signal N. Stop C$x / US$y; the evening scan manages it from here."`) must stay byte-for-byte the same. The test at `tests/test_live_bot.py:59` pins it. For the manual branch, append `f" ({request.note})"` before the final period when `request.note` is set.

Update `HELP`: `/buy SYMBOL QTY PRICE [YYYY-MM-DD] [force]: a buy; it gets a recent signal's stop, or the Breakout stop`.

In `cli.py` `bot_run`, build the adopter from the frozen live config and pass it in:

```python
    strategy, _sha = load_strategy_config(LIVE_CONFIG_PATH)
    adopter = Adopter(config=strategy, universe=load_universe(UNIVERSE_PATH),
                      calendar=NyseSessions())
    brain = BotBrain(chat_id=chat, sessions=get_session, calendar=NyseSessions(), clock=_now,
                     next_run=_next_scan_run, adopter=adopter)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_live_bot.py tests/test_live_e2e.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/live/bot.py src/signalbench/cli.py tests/test_live_bot.py tests/scan_helpers.py
git commit -m "feat: /buy links a recent signal or adopts the Breakout stop"
```

---

### Task 6: `signalbench ledger adopt FILL_ID [--date]`

**Files:**
- Modify: `src/signalbench/cli.py` (next to `ledger_split`, ~line 830)
- Test: `tests/test_live_cli.py`

- [ ] **Step 1: Write the failing test.** Follow the pattern of the existing `ledger split` CLI test in `tests/test_live_cli.py` (a `CliRunner`, `get_session` patched onto the test session). Record a manual opening buy, run `["ledger", "adopt", str(fill_id), "--date", "<a later session>"]`, and assert:
  - exit code 0;
  - the output starts with `adopted fill <id> → fill <new id>`, then `signal <n> (adopted)` or `signal <n> (scan)`, the stop, and the date;
  - if the US close has been at or below the stop since the entry, a line `⚠️ closed at or below the stop on <dates>: by the rules it should have been sold`;
  - the old fill is voided and the new one is linked.

- [ ] **Step 2: Run it and check that it fails**

Run: `uv run pytest tests/test_live_cli.py -k adopt -v`
Expected: FAIL (`No such command 'adopt'`)

- [ ] **Step 3: Implement**

```python
@ledger_app.command("adopt")
def ledger_adopt(
    fill_id: Annotated[int, typer.Argument(help="A manual opening buy's fill id.")],
    day: Annotated[
        str | None, typer.Option("--date", help="Correct the trade date (YYYY-MM-DD).")
    ] = None,
) -> None:
    """Give a manual holding a stop: re-record its buy linked to a recent signal or the
    Breakout stop (the old fill is voided, never edited)."""
    strategy, _sha = load_strategy_config(LIVE_CONFIG_PATH)
    adopter = Adopter(config=strategy, universe=load_universe(UNIVERSE_PATH),
                      calendar=NyseSessions())
    try:
        with get_session() as session:
            result = adopt_fill(
                session, _ledger(session), adopter, fill_id,
                trade_date=None if day is None else date.fromisoformat(day),
            )
            signal = result.signal
            typer.echo(
                f"adopted fill {result.old_fill_id} → fill {result.fill.id} on "
                f"{result.fill.trade_date}: signal {signal.id} ({signal.source}), stop "
                f"C${signal.cdr_stop} / US${signal.us_stop}"
            )
            if result.breaches:
                dates = ", ".join(str(d) for d in result.breaches)
                typer.echo(f"⚠️ closed at or below the stop on {dates}: by the rules it "
                           "should have been sold")
    except (LedgerError, ValueError) as error:
        typer.echo(f"⚠️ {error}", err=True)
        raise typer.Exit(1) from None
```

- [ ] **Step 4: Run the whole suite and the linters**

Run: `uv run pytest -q && uv run ruff check src tests && uv run mypy src`
Expected: all pass (use whichever of ruff/mypy `pyproject.toml` configures).

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/cli.py tests/test_live_cli.py
git commit -m "feat: signalbench ledger adopt gives a manual holding a stop"
```

---

### Task 7: Record the change in specs 04 and 05

**Files:** `docs/superpowers/specs/2026-09-22-swing-assistant-04-ledger-design.md`, `docs/superpowers/specs/2026-09-22-swing-assistant-05-bot-scan-design.md`

- [ ] **Step 1:** Under each spec's Changes list, add a `2026-11-05` entry. Spec 04: every opening buy is linked, to a scan signal from the last 5 sessions or an adopted signal (`trade_signals.source`, migration `0019`, Breakout stop at the session before the buy); `ledger adopt`; scale-up checks 1, 2, and 4 leave out adopted signals, and check 3 counts their alerts. Spec 05: `/buy` links or adopts, adopted signals are never sent, and the reply shows the stop. Give the reason: two of three real holdings had no stop after the botbox data loss.
- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs
git commit -m "docs: specs 04 and 05 record that every holding gets a stop"
```

---

### Task 8: Ship it to ubuntuworker (each step needs the owner's OK)

The live bot runs from `~/macrocite` on ubuntuworker (`ssh signalbench-server`) at tag `live-v2`. It can't `git pull` over its deploy key; HTTPS fetches of the public repo work. Don't touch `~/paper/` or the fplopt bot.

- [ ] **Step 1 (owner OK: push).** Merge the branch into `main`, tag `live-v3`, and push `main` and the tag.
- [ ] **Step 2: back up first.**

```bash
ssh signalbench-server '~/config/backup.sh && ls -t ~/backups | head -1'
```

(Check the backup script's real path with `systemctl --user cat signalbench-backup.service`.)

- [ ] **Step 3 (owner OK: deploy).** Don't do this during a scan (17:00, 19:00, 07:30 New York).

```bash
ssh signalbench-server 'cd ~/macrocite && systemctl --user stop signalbench-bot.service && git fetch https://github.com/samintisar/macrocite.git tag live-v3 && git checkout live-v3 && ~/.local/bin/uv sync --frozen && ~/.local/bin/uv run --frozen alembic upgrade head && systemctl --user start signalbench-bot.service && systemctl --user is-active signalbench-bot.service'
```

Expected: the migrations run `0016 → 0017 → 0018 → 0019`, then `active`.

- [ ] **Step 4 (owner OK: fix the two holdings).** The owner first confirms the ZAMZ fill date in Wealthsimple's activity: the saved date is 2026-10-19, but a market order placed after that day's close fills on 2026-10-20.

```bash
ssh signalbench-server 'cd ~/macrocite && ~/.local/bin/uv run --frozen signalbench ledger adopt 3 --date 2026-10-15 && ~/.local/bin/uv run --frozen signalbench ledger adopt 4'
```

Expected: fill 3 (ZUNH) → adopted signal on 2026-10-14 with its stop. Fill 4 (ZAMZ) → signal 2 (scan), stop C$11.2437 / US$249.4420. Add `--date 2026-10-20` to the second command if Wealthsimple shows that date. Report any `⚠️ closed at or below the stop` lines to the owner.

- [ ] **Step 5: Verify.** Run the bot's read-only `/portfolio` through `BotBrain` (as in the 2026-10-19 check). All three holdings must show a `stop C$… / US$…`, none `manual (no stop)`, and cash must still be C$20.93. After the next evening scan, check `/status` shows `ok` and that no adopted row was sent as a signal.
- [ ] **Step 6:** Log the change and the ZUNH date fix in `docs/research-log.md`, and update the live-setup memory (tag `live-v3`, migration `0019`).
