# Swing Assistant 09 — Crypto daily trend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Load Kraken's BTC and ETH daily candles, then run the live Breakout rules (`v2-none-cash`) on them once, pre-registered, against BTC buy-and-hold, and record the result whatever it is. Research only: nothing connects to Kraken's trading API.

**Architecture:** A new `crypto` ticker kind stores `XBTUSD` and `ETHUSD` daily candles in the existing `prices` table, loaded from Kraken's downloadable OHLCVT files and kept current from Kraken's public OHLC endpoint through the existing price upsert. A new `DailySessions` calendar makes every calendar day a session. A separate `backtest/crypto_runner.py` reuses the shared engine unchanged (`decide()`, `simulate()`, `run_metrics()`, the pass bar, `BacktestRun`, the report) with BTC as the regime symbol and benchmark and a Kraken-fee pre-registration check. The shared report switches its label, benchmark name, caveats, and a missing-days section on `metrics["market"] == "crypto"`. The stock runner, the simulator, and the live scan are not touched.

**Tech Stack:** Python 3.11+, SQLModel (in-memory SQLite in tests), httpx (`MockTransport` in tests), Typer, pytest, ruff 0.16, mypy strict. One Alembic migration (adds an enum value). No new dependencies.

**Spec:** [`docs/superpowers/specs/2026-10-10-swing-assistant-09-crypto-daily-trend-design.md`](../specs/2026-10-10-swing-assistant-09-crypto-daily-trend-design.md). **Branch:** `claude/day-trading-bot-strategy-186677` (cut from `main` at `cbaaa1c`).

---

## Conventions for every task

- Run commands from the repo root with the Bash tool (Git Bash syntax). Use `uv run …` for every Python tool. Stay on the branch above; never push without asking the owner.
- **Never edit an existing `data/strategy_*.yaml`** (their `config_sha256` values are in stored runs) **or any `.env*` file.** Stage explicit paths only; never `git add -A` or `git add .`.
- **No real data before Task 10.** Tasks 1–9 use only fixtures and the in-memory SQLite `session` fixture from `tests/conftest.py`. Do not run any `signalbench crypto` command against a real database, and do not open one, until `data/strategy_crypto-v1.yaml` is committed (Task 9) and the controller gives the go-ahead in Task 10: the run is pre-registered, and seeing a crypto result before the config is committed breaks that.
- **A local database, never the live one.** Task 10 runs against a fresh Postgres on the owner's PC (`docker compose up -d`, the default `DATABASE_URL`). The crypto run needs only its own prices and the `backtest_runs` table. The live scan and bot run on the **botbox** server, and `ubuntuworker` is their stopped fallback: never connect to either database.
- mypy runs strict on `src/` only. `tests/strategy_helpers.py` is imported as `from strategy_helpers import …`.
- ruff 0.16 sorts imports (`I001`). Run `uv run ruff check --fix .` only **after** the module a test imports exists: before that, ruff files the missing `signalbench.*` name as third-party and moves the import.
- After each task: `uv run pytest -q`, `uv run ruff check .`, and `uv run mypy src` all pass before committing.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Every code block below was replayed, task by task, in a scratch worktree of this branch before the plan was committed. The suite starts at **732 passed** and ends at **768 passed** after Task 9, with ruff and mypy clean. If a count disagrees, look for a transcription slip before touching a test.

## Data facts (checked 2026-10-09 on the owner's download)

| Fact | Value |
| --- | --- |
| Download | `Kraken_OHLCVT_Full_2026Q2.zip`, SHA-256 `fc81b54c…eaa4`, matches Kraken's published checksum |
| Files | `$HOME/kraken-ohlcvt/XBTUSD_1440.csv` (4,639 rows, 2013-10-06 to 2026-06-30) and `ETHUSD_1440.csv` (3,975 rows, 2015-08-07 to 2026-06-30); also `_1.csv` minute files for spec 10 |
| Row format | No header: `timestamp, open, high, low, close, volume, trades`; `timestamp` is the candle start, Unix seconds UTC; daily candles start at 00:00 UTC |
| Missing days since 2015 | BTC none; ETH 5 short gaps (4 in Aug–Oct 2015, and 2018-01-12), none longer than 2 days |
| Parse check | Both daily files parse with `parse_ohlcvt_file` (Task 2) with no rejected bars; the largest close has 9 characters, inside `Numeric(12, 4)` |
| Public OHLC endpoint | `GET https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=1440`: `{"error": [], "result": {"XXBTZUSD": [[time, open, high, low, close, vwap, volume, count], …], "last": …}}`, 721 rows, prices as strings; the last row is today's candle, still trading. ETH's key is `XETHZUSD`; an unknown pair returns `{"error": ["EQuery:Unknown asset pair"]}` |

---

### Task 1: The `crypto` ticker kind

**Files:**
- Modify: `src/signalbench/db/models.py` (`TickerKind`)
- Create: `alembic/versions/0017_crypto_ticker_kind.py`
- Test: `tests/test_migrations.py`

- [ ] **Step 1: Write the failing test.** In `tests/test_migrations.py`, change the head assertion and append a test:

```python
def test_migrations_have_single_head() -> None:
    assert _script().get_heads() == ["0017_crypto_ticker_kind"]
```

```python


def test_0017_adds_crypto_to_the_ticker_kind_enum() -> None:
    text = (VERSIONS / "0017_crypto_ticker_kind.py").read_text(encoding="utf-8")
    assert 'down_revision: str | None = "0016_bot_updates"' in text
    assert "ALTER TYPE tickerkind ADD VALUE IF NOT EXISTS 'crypto'" in text
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest -q tests/test_migrations.py`
Expected: 2 failed (the head is still `0016_bot_updates`; `0017_crypto_ticker_kind.py` does not exist), 6 passed.

- [ ] **Step 3: Add the enum value and the migration.** In `src/signalbench/db/models.py`, add the last member of `TickerKind`:

```python
class TickerKind(str, Enum):
    us_stock = "us_stock"
    cdr = "cdr"
    benchmark = "benchmark"
    crypto = "crypto"  # spec 09: a Kraken USD pair, research only
```

Create `alembic/versions/0017_crypto_ticker_kind.py`:

```python
"""Spec 09: a crypto ticker kind for Kraken's BTC and ETH USD pairs

Revision ID: 0017_crypto_ticker_kind
Revises: 0016_bot_updates
Create Date: 2026-10-11 12:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017_crypto_ticker_kind"
down_revision: str | None = "0016_bot_updates"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE tickerkind ADD VALUE IF NOT EXISTS 'crypto'")


def downgrade() -> None:
    # Postgres cannot drop one value from an enum type. Leaving 'crypto' in place is harmless:
    # the downgraded code never writes it, and no crypto rows may remain (delete them first).
    pass
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q tests/test_migrations.py` → 8 passed. Then the full checks → **733 passed**.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/db/models.py alembic/versions/0017_crypto_ticker_kind.py tests/test_migrations.py
git commit -m "feat: a crypto ticker kind for Kraken pairs (spec 09)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Kraken daily candles (file and public API)

The file parser and the API fetcher both produce the existing `DailyBar`, so both go through the existing `ingest_daily_prices` upsert (`full=True`: Kraken closes are never rescaled, so the dividend/split refetch check does not apply). The tickers in `CRYPTO_NAMES` are never in `data/cdr_universe.yaml`, so the stock price ingest, liquidity flags, and the live scan, which all select by universe symbol, never see them.

**Files:**
- Create: `tests/fixtures/kraken/XBTUSD_1440.csv`
- Create: `src/signalbench/ingest/kraken.py`
- Test: `tests/test_kraken.py`

- [ ] **Step 1: Write the fixture and the failing tests.** `tests/fixtures/kraken/XBTUSD_1440.csv` (three days from 2015-01-01; the second has a low of 0 and must be rejected; volumes 1234.56789 and 2000.4 round to 1235 and 2000):

```text
1420070400,314.0,320.5,310.2,318.7,1234.56789,900
1420156800,318.7,319.0,0,315.1,1000.4,800
1420243200,315.1,330.0,314.0,329.95,2000.4,1200
```

`tests/test_kraken.py`:

```python
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlmodel import Session, col, select

from signalbench.db.models import Price, Ticker, TickerKind
from signalbench.ingest.kraken import (
    KRAKEN_OHLC_URL,
    KrakenError,
    crypto_ticker,
    fetch_kraken_daily,
    find_crypto_ticker,
    ingest_kraken_bars,
    parse_ohlcvt_file,
)

FIXTURE = Path(__file__).parent / "fixtures" / "kraken" / "XBTUSD_1440.csv"
HISTORY_START = date(2010, 1, 1)
TODAY = date(2026, 10, 11)  # the third OHLC row below is today's candle, still trading
OHLC: dict[str, Any] = {
    "error": [],
    "result": {
        "XXBTZUSD": [
            [1791504000, "82000.0", "83000.0", "81000.0", "82557.1", "82500.0", "700.4", 50000],
            [1791590400, "82557.1", "83089.7", "82474.8", "82912.7", "82803.8", "490.31", 54517],
            [1791676800, "82912.7", "82999.6", "82853.3", "82971.3", "82913.3", "52.61", 3608],
        ],
        "last": 1791590400,
    },
}


def _client(
    body: dict[str, Any], status: int = 200, seen: list[httpx.Request] | None = None
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_parse_dates_each_candle_by_its_utc_day_and_rounds_volume() -> None:
    bars = parse_ohlcvt_file(FIXTURE)
    assert [bar.date for bar in bars] == [date(2015, 1, 1), date(2015, 1, 2), date(2015, 1, 3)]
    first = bars[0]
    assert (first.open, first.high, first.low, first.close) == (
        Decimal("314.0"),
        Decimal("320.5"),
        Decimal("310.2"),
        Decimal("318.7"),
    )
    assert first.adj_close == first.close
    assert (first.volume, bars[2].volume) == (1235, 2000)


def test_parse_refuses_a_file_that_is_not_daily(tmp_path: Path) -> None:
    path = tmp_path / "XBTUSD_1.csv"
    path.write_text("1420070400,1,1,1,1,1,1\n1420070460,1,1,1,1,1,1\n", encoding="utf-8")
    with pytest.raises(KrakenError, match="line 2: not a daily"):
        parse_ohlcvt_file(path)


def test_parse_refuses_a_row_with_the_wrong_field_count(tmp_path: Path) -> None:
    path = tmp_path / "XBTUSD_1440.csv"
    path.write_text("1420070400,1,1,1,1,1\n", encoding="utf-8")
    with pytest.raises(KrakenError, match="line 1: expected 7 fields, got 6"):
        parse_ohlcvt_file(path)


def test_ingest_creates_the_ticker_rejects_bad_bars_and_is_idempotent(session: Session) -> None:
    ticker = crypto_ticker(session, "XBTUSD")
    bars = parse_ohlcvt_file(FIXTURE)
    first = ingest_kraken_bars(session, ticker, bars, HISTORY_START)
    second = ingest_kraken_bars(session, ticker, bars, HISTORY_START)
    assert (first.created, first.updated, first.rejected) == (2, 0, 1)  # low 0 on 2015-01-02
    assert (second.created, second.updated, second.rejected) == (0, 0, 1)
    stored = session.exec(select(Ticker)).one()
    assert (stored.kind, stored.sector) == (TickerKind.crypto, "crypto")
    rows = session.exec(select(Price).order_by(col(Price.date))).all()
    assert [row.date for row in rows] == [date(2015, 1, 1), date(2015, 1, 3)]
    assert rows[0].adj_close == rows[0].close


def test_crypto_ticker_is_reused_and_refuses_unknown_pairs_and_other_kinds(
    session: Session,
) -> None:
    assert find_crypto_ticker(session, "ETHUSD") is None
    assert crypto_ticker(session, "ETHUSD").id == crypto_ticker(session, "ETHUSD").id
    with pytest.raises(KrakenError, match="Unknown pair DOGEUSD"):
        crypto_ticker(session, "DOGEUSD")
    session.add(Ticker(symbol="XBTUSD", company_name="x", kind=TickerKind.us_stock))
    session.commit()
    with pytest.raises(KrakenError, match="already a us_stock ticker"):
        crypto_ticker(session, "XBTUSD")


def test_fetch_drops_the_day_in_progress_and_reads_volume_not_vwap() -> None:
    seen: list[httpx.Request] = []
    bars = fetch_kraken_daily(_client(OHLC, seen=seen), "XBTUSD", today=TODAY)
    assert [bar.date for bar in bars] == [date(2026, 10, 9), date(2026, 10, 10)]
    assert bars[-1].close == Decimal("82912.7")
    assert bars[-1].volume == 490
    assert str(seen[0].url).startswith(KRAKEN_OHLC_URL)
    assert (seen[0].url.params["pair"], seen[0].url.params["interval"]) == ("XBTUSD", "1440")


def test_fetch_raises_on_a_kraken_error() -> None:
    body = {"error": ["EQuery:Unknown asset pair"]}
    with pytest.raises(KrakenError, match="EQuery:Unknown asset pair"):
        fetch_kraken_daily(_client(body), "XBTUSD", today=TODAY)


def test_fetch_raises_on_an_http_error() -> None:
    with pytest.raises(httpx.HTTPStatusError):
        fetch_kraken_daily(_client({}, status=502), "XBTUSD", today=TODAY)


def test_recent_candles_are_stored_once_without_today(session: Session) -> None:
    ticker = crypto_ticker(session, "XBTUSD")
    bars = fetch_kraken_daily(_client(OHLC), "XBTUSD", today=TODAY)
    first = ingest_kraken_bars(session, ticker, bars, HISTORY_START)
    again = ingest_kraken_bars(session, ticker, bars, HISTORY_START)
    assert (first.created, again.created, again.updated) == (2, 0, 0)
    assert TODAY not in {row.date for row in session.exec(select(Price)).all()}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest -q tests/test_kraken.py`
Expected: collection error, `ModuleNotFoundError: No module named 'signalbench.ingest.kraken'`.

- [ ] **Step 3: Write `src/signalbench/ingest/kraken.py`**

```python
"""Kraken daily candles for BTC and ETH (spec 09): the downloadable OHLCVT files and the
public OHLC endpoint. Research only: nothing here trades or needs an API key."""

import csv
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from sqlmodel import Session, select

from signalbench.db.models import Ticker, TickerKind
from signalbench.ingest.prices import DailyBar, PriceIngestResult, ingest_daily_prices

KRAKEN_OHLC_URL = "https://api.kraken.com/0/public/OHLC"
DAY_SECONDS = 86_400
DAILY_INTERVAL = 1440  # minutes per candle
CRYPTO_SECTOR = "crypto"
CRYPTO_NAMES = {"ETHUSD": "Ether (Kraken ETH/USD)", "XBTUSD": "Bitcoin (Kraken XBT/USD)"}


class KrakenError(ValueError):
    """A Kraken file row could not be read, or Kraken's API answered with an error."""


def utc_day(timestamp: int) -> date:
    """The UTC day a candle starting at `timestamp` (Unix seconds) covers."""
    return datetime.fromtimestamp(timestamp, tz=UTC).date()


def _price(text: str) -> Decimal:
    return Decimal(str(round(float(text), 4)))


def _bar(timestamp: int, open_: str, high: str, low: str, close: str, volume: str) -> DailyBar:
    """Crypto has no splits or dividends, so adj_close = close. Volume is in coins, rounded to
    a whole coin to fit `prices.volume`."""
    return DailyBar(
        date=utc_day(timestamp),
        open=_price(open_),
        high=_price(high),
        low=_price(low),
        close=_price(close),
        adj_close=_price(close),
        volume=round(float(volume)),
    )


def parse_ohlcvt_file(path: Path) -> list[DailyBar]:
    """One Kraken OHLCVT daily file: no header; timestamp, open, high, low, close, volume,
    trades. Refuses a row with another field count, or a candle that does not start at
    00:00 UTC (a 1-minute or hourly file passed by mistake)."""
    bars: list[DailyBar] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for number, row in enumerate(csv.reader(handle), start=1):
            if len(row) != 7:
                raise KrakenError(f"{path.name} line {number}: expected 7 fields, got {len(row)}")
            timestamp = int(row[0])
            if timestamp % DAY_SECONDS != 0:
                started = datetime.fromtimestamp(timestamp, tz=UTC).isoformat()
                raise KrakenError(
                    f"{path.name} line {number}: not a daily (1440) file, a candle starts at "
                    f"{started}"
                )
            bars.append(_bar(timestamp, row[1], row[2], row[3], row[4], row[5]))
    return bars


def fetch_kraken_daily(client: httpx.Client, pair: str, today: date) -> list[DailyBar]:
    """The daily candles Kraken's public OHLC endpoint returns (about the last 720 days),
    without any dated `today` (UTC) or later: that day is still trading.

    Each row is [time, open, high, low, close, vwap, volume, count]; volume, not vwap, is kept.
    """
    response = client.get(
        KRAKEN_OHLC_URL, params={"pair": pair, "interval": str(DAILY_INTERVAL)}
    )
    response.raise_for_status()
    body: dict[str, Any] = response.json()
    if body.get("error"):
        raise KrakenError(f"Kraken OHLC {pair}: {', '.join(body['error'])}")
    result: dict[str, Any] = body["result"]
    rows: list[list[Any]] = next(value for key, value in result.items() if key != "last")
    bars = [_bar(int(row[0]), row[1], row[2], row[3], row[4], row[6]) for row in rows]
    return [bar for bar in bars if bar.date < today]


def find_crypto_ticker(session: Session, symbol: str) -> Ticker | None:
    """The stored crypto ticker for a Kraken pair, or None before its first ingest."""
    if symbol not in CRYPTO_NAMES:
        raise KrakenError(f"Unknown pair {symbol}; spec 09 covers {', '.join(CRYPTO_NAMES)}")
    ticker = session.exec(select(Ticker).where(Ticker.symbol == symbol)).first()
    if ticker is not None and ticker.kind != TickerKind.crypto:
        raise KrakenError(f"{symbol} is already a {ticker.kind.value} ticker")
    return ticker


def crypto_ticker(session: Session, symbol: str) -> Ticker:
    """The crypto ticker for a Kraken pair, created on first use."""
    ticker = find_crypto_ticker(session, symbol)
    if ticker is None:
        ticker = Ticker(
            symbol=symbol,
            company_name=CRYPTO_NAMES[symbol],
            sector=CRYPTO_SECTOR,
            kind=TickerKind.crypto,
        )
        session.add(ticker)
        session.commit()
        session.refresh(ticker)
    return ticker


def ingest_kraken_bars(
    session: Session, ticker: Ticker, bars: list[DailyBar], history_start: date
) -> PriceIngestResult:
    """Upsert `bars` with the stock price upsert: bad bars are rejected and unchanged rows
    are not rewritten. `full=True`: Kraken closes are never rescaled, so the dividend/split
    refetch check does not apply."""
    return ingest_daily_prices(
        session,
        ticker,
        fetch=lambda _symbol, _start: bars,
        history_start=history_start,
        full=True,
    )
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q tests/test_kraken.py` → 9 passed. Then `uv run ruff check --fix tests/test_kraken.py` and the full checks → **742 passed**.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/kraken/XBTUSD_1440.csv src/signalbench/ingest/kraken.py tests/test_kraken.py
git commit -m "feat: Kraken daily candles from the OHLCVT files and the public API (spec 09)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `DailySessions`, every calendar day

**Files:**
- Modify: `src/signalbench/market/calendar.py`
- Test: `tests/test_daily_sessions.py`

- [ ] **Step 1: Write the failing tests** in `tests/test_daily_sessions.py`:

```python
from datetime import UTC, date, datetime

from signalbench.market.calendar import DailySessions, Sessions


def test_every_calendar_day_is_a_session() -> None:
    calendar: Sessions = DailySessions()
    assert calendar.sessions_between(date(2024, 2, 27), date(2024, 3, 2)) == [
        date(2024, 2, 27),
        date(2024, 2, 28),
        date(2024, 2, 29),
        date(2024, 3, 1),
        date(2024, 3, 2),
    ]
    assert calendar.sessions_between(date(2024, 3, 2), date(2024, 3, 1)) == []
    assert calendar.is_session(date(2024, 12, 25)) is True


def test_next_sessions_are_the_following_days() -> None:
    assert DailySessions().next_sessions(date(2024, 12, 30), 3) == [
        date(2024, 12, 31),
        date(2025, 1, 1),
        date(2025, 1, 2),
    ]


def test_sessions_start_at_the_first_day_and_close_at_utc_midnight() -> None:
    calendar = DailySessions(start=date(2010, 1, 1))
    assert calendar.sessions_between(date(2009, 12, 30), date(2010, 1, 2)) == [
        date(2010, 1, 1),
        date(2010, 1, 2),
    ]
    assert calendar.is_session(date(2009, 12, 31)) is False
    assert calendar.session_closes(date(2024, 3, 1), date(2024, 3, 1)) == [
        (date(2024, 3, 1), datetime(2024, 3, 2, 0, 0, tzinfo=UTC))
    ]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest -q tests/test_daily_sessions.py`
Expected: collection error, `ImportError: cannot import name 'DailySessions'`.

- [ ] **Step 3: Implement.** In `src/signalbench/market/calendar.py`, replace the module docstring and the `datetime` import:

```python
"""Sessions as plain dates: NYSE and Cboe Canada (the only module that imports
exchange_calendars), and every calendar day for crypto (spec 09)."""

from datetime import UTC, date, datetime, time, timedelta
```

and append, after `CboeCanadaSessions`:

```python


class DailySessions:
    """Every calendar day is a session (spec 09: crypto trades all week). A session closes at
    00:00 UTC the next day, when Kraken's daily candle for it is final."""

    def __init__(self, start: date = HISTORY_START) -> None:
        self._first = start

    def sessions_between(self, start: date, end: date) -> list[date]:
        start = max(start, self._first)
        return [start + timedelta(days=i) for i in range((end - start).days + 1)]

    def next_sessions(self, day: date, count: int) -> list[date]:
        return [day + timedelta(days=i) for i in range(1, count + 1)]

    def is_session(self, day: date) -> bool:
        return day >= self._first

    def session_closes(self, start: date, end: date) -> list[tuple[date, datetime]]:
        return [
            (day, datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=UTC))
            for day in self.sessions_between(start, end)
        ]
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q tests/test_daily_sessions.py tests/test_calendar.py` → all pass. Full checks → **745 passed**.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/market/calendar.py tests/test_daily_sessions.py
git commit -m "feat: DailySessions, where every calendar day is a session (spec 09)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The Kraken fee check

**Files:**
- Modify: `src/signalbench/backtest/preregistration.py`
- Test: `tests/test_preregistration.py`

- [ ] **Step 1: Write the failing test.** In `tests/test_preregistration.py`, add `KRAKEN_TOP_FEE` and `check_cost_matches_kraken` to the `signalbench.backtest.preregistration` import (ruff's order: `KRAKEN_TOP_FEE, RunRefusedError, check_config_path, check_cost_matches_kraken, check_cost_matches_survey, check_version_unchanged`), and append:

```python


def test_crypto_cost_must_be_krakens_top_fee() -> None:
    check_cost_matches_kraken(KRAKEN_TOP_FEE)
    with pytest.raises(RunRefusedError, match="Kraken's top published fee, 0.004"):
        check_cost_matches_kraken(0.002)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest -q tests/test_preregistration.py`
Expected: collection error, `ImportError: cannot import name 'KRAKEN_TOP_FEE'`.

- [ ] **Step 3: Implement.** In `src/signalbench/backtest/preregistration.py`, after `from signalbench.db.models import BacktestRun`, add:

```python

# Kraken's published fee per trade ranges from -0.02% to 0.40%, by 30-day volume and maker or
# taker (read 2026-10-08). Spec 09 charges the top of the range: a starting account, taking.
# https://support.kraken.com/ca/articles/201893638-How-trading-fees-work-on-Kraken
KRAKEN_TOP_FEE = 0.004
```

and append:

```python


def check_cost_matches_kraken(config_cost: float) -> None:
    """Spec 09: a crypto config's cost_per_side must be Kraken's top published fee."""
    if config_cost != KRAKEN_TOP_FEE:
        raise RunRefusedError(
            f"The config has cost_per_side {config_cost}, but crypto runs use Kraken's top "
            f"published fee, {KRAKEN_TOP_FEE} (spec 09 pre-registration)."
        )
```

- [ ] **Step 4: Run the tests** → `tests/test_preregistration.py` all pass; full checks → **746 passed**.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/backtest/preregistration.py tests/test_preregistration.py
git commit -m "feat: crypto configs must charge Kraken's top published fee (spec 09)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Crypto sections in the shared report

A stock run has no `market`, `benchmark`, or `missing_days` key in its stored metrics, so it renders exactly as before; the existing `tests/test_report.py` tests prove it.

**Files:**
- Modify: `src/signalbench/backtest/report.py`
- Test: `tests/test_report.py`

- [ ] **Step 1: Write the failing tests.** In `tests/test_report.py`, add `CRYPTO_CAVEATS` and `CRYPTO_LABEL` to the `signalbench.backtest.report` import (after `CAVEATS`), and append:

```python


def test_crypto_run_has_its_label_benchmark_caveats_and_missing_days() -> None:
    run = _run(version="crypto-v1")
    run.metrics = {
        **run.metrics,
        "market": "crypto",
        "benchmark": "BTC buy-and-hold",
        "missing_days": {"ETHUSD": ["2018-01-12"], "XBTUSD": []},
    }
    text = render_report(run)
    assert CRYPTO_LABEL in text
    assert "POST-HOC" not in text
    assert "| 4 | Sharpe vs BTC buy-and-hold |" in text
    assert "| ETHUSD | 1 | 2018-01-12 |" in text
    assert "| XBTUSD | 0 | none |" in text
    for caveat in CRYPTO_CAVEATS:
        assert f"- {caveat}" in text
    for caveat in CAVEATS:
        assert caveat not in text


def test_stock_run_has_no_crypto_sections() -> None:
    text = render_report(_run())
    assert "## Missing days" not in text
    assert CRYPTO_LABEL not in text
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest -q tests/test_report.py`
Expected: collection error, `ImportError: cannot import name 'CRYPTO_CAVEATS'`.

- [ ] **Step 3: Implement.** Five edits in `src/signalbench/backtest/report.py`.

(a) Just before `PASS_BAR_ROWS = (`, add:

```python
CRYPTO_LABEL = (
    "**First test on crypto.** Rules carried over unchanged from v2-none-cash; the owner has "
    "seen crypto price charts, but no rule was chosen on them (spec 09)."
)
CRYPTO_CAVEATS = (
    "Kraken USD candles (XBT/USD, ETH/USD); a day runs from 00:00 to 00:00 UTC.",
    (
        "Orders fill at the next day's 00:00 UTC open. The 0.40% fee per side is the only cost; "
        "no slippage beyond it is modeled."
    ),
    (
        "BTC and ETH were chosen partly because they survived; a universe picked today "
        "flatters the past."
    ),
    (
        "Sharpe annualises with sqrt(252) over every calendar day, so this run's and BTC's are "
        "both understated by about sqrt(365/252), 1.2x; the comparison is unaffected."
    ),
    (
        "Taxes are not modeled; frequent trading can be business income in Canada. "
        "Fractional coins are assumed."
    ),
)
```

(b) In `_header`, replace `    if run.strategy_version != "v1":` with:

```python
    if run.metrics.get("market") == "crypto":
        lines += [CRYPTO_LABEL, ""]
    elif run.strategy_version != "v1":
```

(c) In `_pass_bar`, replace the first two lines of the loop:

```python
    for number, (key, label, comparison) in enumerate(PASS_BAR_ROWS, start=1):
        row = run.pass_bar[key]
```

with:

```python
    benchmark: str | None = run.metrics.get("benchmark")  # spec 09; stock runs compare to QQQ
    for number, (key, label, comparison) in enumerate(PASS_BAR_ROWS, start=1):
        row = run.pass_bar[key]
        if key == "sharpe" and benchmark is not None:
            label = f"Sharpe vs {benchmark}"
```

(d) Add `_missing_days` just before `_skips_and_caveats`, and pick the caveats by market inside it:

```python
def _missing_days(m: dict[str, Any]) -> list[str]:
    missing: dict[str, list[str]] | None = m.get("missing_days")
    if missing is None:
        return []
    return [
        "## Missing days",
        "",
        (
            "Days inside the run with no candle (no trades on Kraken): nothing filled, and "
            "held coins waited."
        ),
        "",
        "| Symbol | Days | Dates |",
        "| --- | --- | --- |",
        *[
            f"| {symbol} | {len(days)} | {', '.join(days) or 'none'} |"
            for symbol, days in missing.items()
        ],
    ]


def _skips_and_caveats(m: dict[str, Any]) -> list[str]:
    skips: dict[str, int] = m["skips_by_reason"]
    caveats = CRYPTO_CAVEATS if m.get("market") == "crypto" else CAVEATS
```

and in the same function replace `        *[f"- {caveat}" for caveat in CAVEATS],` with `        *[f"- {caveat}" for caveat in caveats],`.

(e) In `render_report`, add `_missing_days(run.metrics),` right after `_benchmarks(run.metrics),`.

- [ ] **Step 4: Run the tests** → `tests/test_report.py` 17 passed; full checks → **748 passed**.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/backtest/report.py tests/test_report.py
git commit -m "feat: crypto label, benchmark, caveats, and missing days in the report (spec 09)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The crypto runner

The test data is hand-checkable: BTC is flat at 100 (high 101, low 99, so ATR is 2) for 260 days, closes at 110 on double volume on day 260 (true range 11, so ATR(14) = (13 × 2 + 11) / 14 = 37/14 and the planned risk is 2 × 37/14 = 37/7), holds at 110, then closes at 100 on day 280, below the 2-ATR stop. Entry fills at 110 × 1.004, the exit at the next open, 100 × 0.996. ETH is flat at 50 and never breaks out.

**Files:**
- Create: `tests/fixtures/strategy_crypto_test.yaml`
- Create: `src/signalbench/backtest/crypto_runner.py`
- Test: `tests/test_crypto_runner.py`

- [ ] **Step 1: Write the fixture config and the failing tests.** `tests/fixtures/strategy_crypto_test.yaml`:

```yaml
# Test copy of the crypto parameters (spec 09). Not pre-registered: the real file is
# data/strategy_crypto-v1.yaml. Cost is Kraken's top fee; no liquidity floor; no time limit.
version: crypto-test
start_equity: 100
risk_pct: 0.02
max_positions: 3
max_per_sector: 2
pause_drawdown: 0.15
auto_resume_sessions: 10
gap_up_limit: 0.01
cost_per_side: 0.004
setup_priority: [pullback, breakout, sentiment]
regime:
  symbol: XBTUSD
  sma: 200
indicators:
  atr_period: 14
  rsi_period: 2
liquidity:
  min_median_traded_value_usd: 0
  sessions: 20
earnings:
  blackout_sessions: 3
  exit_sessions: 2
setups:
  pullback:
    trend_sma_fast: 50
    trend_sma_slow: 200
    rsi_max: 10
    stop_low_sessions: 3
    stop_atr_mult: 0.5
    target_r: 2.0
    time_limit: 10
  breakout:
    trend_sma: 50
    lookback: 20
    volume_lookback: 50
    volume_mult: 1.5
    stop_atr_mult: 2.0
    trail_atr_mult: 3.0
    time_limit: null
  sentiment:
    trend_sma: 20
    stop_atr_mult: 2.0
    target_r: 2.0
    time_limit: 10
backtest:
  start: 2014-09-01
  h1_end: 2014-09-30
  h2_start: 2014-10-01
  recent_since: 2014-10-01
  pass_bar:
    min_trades: 30
    min_mean_r: 0.10
```

`tests/test_crypto_runner.py`:

```python
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pytest import approx
from sqlmodel import Session, col, select

from signalbench.backtest.crypto_runner import (
    last_complete_day,
    longest_gap,
    missing_days,
    run_crypto_backtest,
)
from signalbench.backtest.preregistration import RunRefusedError
from signalbench.db.models import BacktestRun, Price, Ticker, TickerKind
from signalbench.market.bars import AdjustedBar
from signalbench.market.calendar import DailySessions
from signalbench.strategy.config import StrategyConfig, load_strategy_config
from strategy_helpers import make_bar

CONFIG_PATH = Path(__file__).parent / "fixtures" / "strategy_crypto_test.yaml"
DAYS = [date(2014, 1, 1) + timedelta(days=i) for i in range(300)]  # every calendar day
BREAKOUT = 260  # 2014-09-18: close 110 on double volume, after 260 days flat at 100
CRASH = 280  # close 100, below the stop: the exit fills at the next open
NOW = datetime(2014, 10, 28, 12, 0, tzinfo=UTC)  # the last complete day is DAYS[-1]


def _config() -> StrategyConfig:
    return load_strategy_config(CONFIG_PATH)[0]


def _btc() -> list[AdjustedBar]:
    bars = []
    for i, day in enumerate(DAYS):
        if i < BREAKOUT:
            bars.append(make_bar(day, 100.0, volume=1000))
        elif i == BREAKOUT:
            bars.append(make_bar(day, 110.0, volume=2000))
        elif i < CRASH:
            bars.append(make_bar(day, 110.0, volume=1000))
        else:
            bars.append(make_bar(day, 100.0, volume=1000))
    return bars


def _eth() -> list[AdjustedBar]:
    return [make_bar(day, 50.0, volume=1000) for day in DAYS]  # flat: never breaks out


def _store(session: Session, symbol: str, bars: list[AdjustedBar]) -> None:
    ticker = Ticker(symbol=symbol, company_name=symbol, sector="crypto", kind=TickerKind.crypto)
    session.add(ticker)
    session.commit()
    session.refresh(ticker)
    for bar in bars:
        price = Decimal(str(bar.close))
        session.add(
            Price(
                ticker_id=ticker.id,
                date=bar.date,
                open=Decimal(str(bar.open)),
                high=Decimal(str(bar.high)),
                low=Decimal(str(bar.low)),
                close=price,
                adj_close=price,
                volume=bar.volume,
            )
        )
    session.commit()


@pytest.fixture
def seeded(session: Session) -> Session:
    _store(session, "XBTUSD", _btc())
    _store(session, "ETHUSD", _eth())
    return session


def _run(
    session: Session,
    tmp_path: Path,
    *,
    config: StrategyConfig | None = None,
    sha: str = "f" * 64,
    now: datetime = NOW,
) -> tuple[BacktestRun, Path]:
    return run_crypto_backtest(
        session,
        config=config or _config(),
        config_sha256=sha,
        calendar=DailySessions(),
        git_sha="abc123",
        run_date=date(2014, 10, 28),
        reports_dir=tmp_path,
        now=now,
    )


def _drop_eth_days(session: Session, days: list[date]) -> None:
    eth = session.exec(select(Ticker).where(Ticker.symbol == "ETHUSD")).one()
    rows = session.exec(
        select(Price).where(Price.ticker_id == eth.id, col(Price.date).in_(days))
    ).all()
    for row in rows:
        session.delete(row)
    session.commit()


def test_one_breakout_gives_one_hand_checked_trade_against_btc(
    seeded: Session, tmp_path: Path
) -> None:
    run, path = _run(seeded, tmp_path)
    assert (run.setup, run.jev_mode, run.strategy_version) == ("breakout", "off", "crypto-test")
    assert (run.start_date, run.end_date) == (date(2014, 9, 1), DAYS[-1])
    [trade] = run.trade_log["trades"]
    assert trade["symbol"] == "XBTUSD"
    assert (trade["signal_date"], trade["entry_date"]) == (
        DAYS[BREAKOUT].isoformat(),
        DAYS[BREAKOUT + 1].isoformat(),
    )
    assert (trade["exit_date"], trade["reason"]) == (DAYS[CRASH + 1].isoformat(), "stop")
    # ATR(14) on the breakout day is (13 x 2 + 11) / 14 = 37/14, so the planned risk is
    # 2 x 37/14 = 37/7. Entry fills at 110 x 1.004, the exit at 100 x 0.996.
    assert trade["r"] == approx((100 * 0.996 - 110 * 1.004) / (37 / 7))
    assert run.metrics["market"] == "crypto"
    assert [b["name"] for b in run.metrics["benchmarks"]] == ["BTC buy-and-hold"]
    assert run.pass_bar["sharpe"]["threshold"] == run.metrics["benchmarks"][0]["sharpe"]
    assert run.metrics["missing_days"] == {"ETHUSD": [], "XBTUSD": []}
    assert run.passed is False  # one trade is far below 30
    assert seeded.exec(select(BacktestRun)).one().id == run.id
    assert path == tmp_path / "2014-10-28-crypto-test-breakout-off.md"
    text = path.read_text(encoding="utf-8")
    assert "**First test on crypto.**" in text
    assert "| 4 | Sharpe vs BTC buy-and-hold |" in text


def test_a_short_gap_is_allowed_and_listed(seeded: Session, tmp_path: Path) -> None:
    gap = [DAYS[270], DAYS[271]]
    _drop_eth_days(seeded, gap)
    run, _path = _run(seeded, tmp_path)
    assert run.metrics["missing_days"]["ETHUSD"] == [day.isoformat() for day in gap]
    assert len(run.trade_log["trades"]) == 1


def test_a_gap_longer_than_three_days_is_refused(seeded: Session, tmp_path: Path) -> None:
    _drop_eth_days(seeded, DAYS[270:274])
    with pytest.raises(RunRefusedError, match="ETHUSD \\(4 days in a row\\)"):
        _run(seeded, tmp_path)


def test_a_gap_before_the_run_starts_is_ignored(seeded: Session, tmp_path: Path) -> None:
    _drop_eth_days(seeded, DAYS[100:110])
    run, _path = _run(seeded, tmp_path)
    assert run.metrics["missing_days"]["ETHUSD"] == []


def test_series_must_end_on_the_last_complete_utc_day(seeded: Session, tmp_path: Path) -> None:
    with pytest.raises(RunRefusedError, match="do not end on 2014-10-28"):
        _run(seeded, tmp_path, now=NOW + timedelta(days=1))


def test_cost_must_be_krakens_top_fee(seeded: Session, tmp_path: Path) -> None:
    with pytest.raises(RunRefusedError, match="Kraken's top published fee"):
        _run(seeded, tmp_path, config=replace(_config(), cost_per_side=0.002))


def test_a_changed_config_is_a_new_version(seeded: Session, tmp_path: Path) -> None:
    _run(seeded, tmp_path)
    with pytest.raises(RunRefusedError, match="already has runs"):
        _run(seeded, tmp_path, sha="e" * 64)


def test_both_symbols_must_be_loaded(session: Session, tmp_path: Path) -> None:
    _store(session, "XBTUSD", _btc())
    with pytest.raises(RunRefusedError, match="Not loaded: ETHUSD"):
        _run(session, tmp_path)


def test_missing_days_and_longest_gap() -> None:
    bars = [make_bar(date(2024, 1, d), 1.0) for d in (2, 3, 6, 7, 9)]
    days = missing_days(bars, date(2024, 1, 1), date(2024, 1, 10))
    assert days == [date(2024, 1, 4), date(2024, 1, 5), date(2024, 1, 8), date(2024, 1, 10)]
    assert longest_gap(days) == 2
    assert longest_gap([]) == 0
    assert missing_days([], date(2024, 1, 1), date(2024, 1, 10)) == []


def test_last_complete_day_is_yesterday_in_utc() -> None:
    assert last_complete_day(datetime(2026, 10, 11, 0, 30, tzinfo=UTC)) == date(2026, 10, 10)
    with pytest.raises(ValueError, match="timezone-aware"):
        last_complete_day(datetime(2026, 10, 11, 0, 30))  # noqa: DTZ001  # naive on purpose
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest -q tests/test_crypto_runner.py`
Expected: collection error, `ModuleNotFoundError: No module named 'signalbench.backtest.crypto_runner'`.

- [ ] **Step 3: Write `src/signalbench/backtest/crypto_runner.py`**

```python
"""Load Kraken daily candles, run one crypto backtest, store it, and write its report (spec 09).

The stock runner (runner.py) is neither used nor changed: the live strategy depends on it.
This runner shares the engine behind it: decide(), the simulator, metrics, the pass bar, the
stored run, and the report.
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlmodel import Session, col, select

from signalbench.backtest.benchmarks import benchmark_stats, buy_and_hold
from signalbench.backtest.fingerprint import data_fingerprint
from signalbench.backtest.metrics import run_metrics
from signalbench.backtest.passbar import evaluate_pass_bar, passes
from signalbench.backtest.preregistration import (
    RunRefusedError,
    check_cost_matches_kraken,
    check_version_unchanged,
)
from signalbench.backtest.report import (
    metrics_payload,
    pass_bar_payload,
    render_report,
    report_path,
    trade_log_payload,
)
from signalbench.backtest.simulator import simulate
from signalbench.db.models import BacktestRun, Ticker, TickerKind
from signalbench.ingest.kraken import CRYPTO_SECTOR
from signalbench.market.bars import AdjustedBar, adjusted_bars
from signalbench.market.calendar import Sessions
from signalbench.strategy.config import StrategyConfig
from signalbench.strategy.market_view import MarketView, SymbolInput
from signalbench.strategy.readings import NullReadingsView

CRYPTO_SYMBOLS = ("ETHUSD", "XBTUSD")
BTC = "XBTUSD"  # a universe symbol, the regime symbol, and the benchmark
BTC_BENCHMARK = "BTC buy-and-hold"
MAX_GAP_DAYS = 3  # a longer run of missing days inside the run is broken data


def last_complete_day(now: datetime) -> date:
    """Yesterday in UTC: today's Kraken candle is final only at 00:00 UTC tomorrow."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError(f"now must be timezone-aware, got naive {now.isoformat()}")
    return now.astimezone(UTC).date() - timedelta(days=1)


def missing_days(bars: Sequence[AdjustedBar], start: date, end: date) -> list[date]:
    """Days from max(start, the first bar) to `end` with no candle."""
    if not bars:
        return []
    have = {bar.date for bar in bars}
    first = max(start, bars[0].date)
    return [
        first + timedelta(days=i)
        for i in range((end - first).days + 1)
        if first + timedelta(days=i) not in have
    ]


def longest_gap(days: Sequence[date]) -> int:
    """The longest run of consecutive days in a sorted list."""
    longest = run = 0
    previous: date | None = None
    for day in days:
        run = run + 1 if previous is not None and day == previous + timedelta(days=1) else 1
        longest = max(longest, run)
        previous = day
    return longest


def load_crypto_bars(session: Session, end: date) -> dict[str, list[AdjustedBar]]:
    """Each crypto symbol's bars up to `end`, in CRYPTO_SYMBOLS order."""
    rows = session.exec(
        select(Ticker).where(
            col(Ticker.symbol).in_(CRYPTO_SYMBOLS), Ticker.kind == TickerKind.crypto
        )
    ).all()
    tickers = {ticker.symbol: ticker for ticker in rows}
    missing = [symbol for symbol in CRYPTO_SYMBOLS if symbol not in tickers]
    if missing:
        raise RunRefusedError(
            f"Not loaded: {', '.join(missing)}. Run `signalbench crypto ingest-file` first."
        )
    bars = {
        symbol: adjusted_bars(session, tickers[symbol].id, end=end) for symbol in CRYPTO_SYMBOLS
    }
    empty = [symbol for symbol, series in bars.items() if not series]
    if empty:
        raise RunRefusedError(
            f"No prices for {', '.join(empty)}. Run `signalbench crypto ingest-file` first."
        )
    return bars


def _check_series(
    bars: dict[str, list[AdjustedBar]], start: date, last: date
) -> dict[str, list[date]]:
    """Every series ends on `last` and has no gap longer than MAX_GAP_DAYS inside the run.
    Returns each symbol's missing days, for the report."""
    behind = [
        f"{symbol} (last bar {series[-1].date.isoformat()})"
        for symbol, series in bars.items()
        if series[-1].date != last
    ]
    if behind:
        raise RunRefusedError(
            f"These series do not end on {last.isoformat()}, the last complete UTC day: "
            f"{', '.join(behind)}. Run `signalbench crypto ingest-recent` and try again."
        )
    gaps = {symbol: missing_days(series, start, last) for symbol, series in bars.items()}
    broken = [
        f"{symbol} ({longest_gap(days)} days in a row)"
        for symbol, days in gaps.items()
        if longest_gap(days) > MAX_GAP_DAYS
    ]
    if broken:
        raise RunRefusedError(
            f"Gaps longer than {MAX_GAP_DAYS} days inside the run: {', '.join(broken)}. "
            "Check the data before running (spec 09)."
        )
    return gaps


def run_crypto_backtest(
    session: Session,
    *,
    config: StrategyConfig,
    config_sha256: str,
    calendar: Sessions,
    git_sha: str,
    run_date: date,
    reports_dir: Path,
    now: datetime,
) -> tuple[BacktestRun, Path]:
    """Breakout on BTC and ETH from `backtest.start` to yesterday (UTC), judged against BTC
    buy-and-hold; store the run and write its report.

    Refuses (RunRefusedError) a cost other than Kraken's top fee, a changed config for a
    stored version, an unloaded symbol, a series that does not end yesterday, or a gap longer
    than MAX_GAP_DAYS inside the run. `now` must be timezone-aware.
    """
    check_cost_matches_kraken(config.cost_per_side)
    config = config.with_setups(("breakout",))
    check_version_unchanged(session, config.version, config_sha256)
    last = last_complete_day(now)
    start = config.backtest.start
    bars = load_crypto_bars(session, last)
    gaps = _check_series(bars, start, last)
    run_sessions = calendar.sessions_between(start, last)
    if not run_sessions:
        raise RunRefusedError(f"No sessions between {start} and {last}.")
    lookahead = max(config.earnings_blackout_sessions, config.earnings_exit_sessions)
    market = MarketView(
        [
            SymbolInput(symbol=symbol, sector=CRYPTO_SECTOR, bars=series, earnings=[])
            for symbol, series in bars.items()
        ],
        bars[BTC],
        run_sessions + calendar.next_sessions(last, lookahead),
        config,
    )
    result = simulate(market, NullReadingsView(), config, start, last)
    metrics = run_metrics(result, config.backtest)
    simulated = market.sessions_between(result.start, result.end)
    btc = benchmark_stats(BTC_BENCHMARK, buy_and_hold(bars[BTC], simulated), simulated)
    bar = evaluate_pass_bar(metrics, btc.sharpe, config.backtest)
    payload = metrics_payload(metrics, [btc], config.backtest)
    payload["market"] = "crypto"
    payload["benchmark"] = BTC_BENCHMARK
    payload["missing_days"] = {
        symbol: [day.isoformat() for day in days] for symbol, days in gaps.items()
    }
    run = BacktestRun(
        strategy_version=config.version,
        config_sha256=config_sha256,
        git_sha=git_sha,
        setup="breakout",
        jev_mode="off",
        start_date=result.start,
        end_date=result.end,
        data_fingerprint=data_fingerprint(list(bars.items()), []),
        metrics=payload,
        pass_bar=pass_bar_payload(bar),
        passed=passes(bar),
        trade_log=trade_log_payload(result),
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    path = report_path(reports_dir, run_date, "breakout", "off", config.version)
    if path.exists():
        path = path.with_name(f"{path.stem}-{str(run.id)[:8]}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(run), encoding="utf-8")
    return run, path
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q tests/test_crypto_runner.py` → 10 passed. Then `uv run ruff check --fix tests/test_crypto_runner.py` and the full checks → **758 passed**. `tests/test_v1_regression.py` must still pass untouched: nothing in the stock path changed.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/strategy_crypto_test.yaml src/signalbench/backtest/crypto_runner.py tests/test_crypto_runner.py
git commit -m "feat: the crypto runner, Breakout on BTC and ETH against BTC (spec 09)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: `signalbench crypto` commands

**Files:**
- Modify: `src/signalbench/cli.py`
- Test: `tests/test_crypto_cli.py`

- [ ] **Step 1: Write the failing tests** in `tests/test_crypto_cli.py`:

```python
from pathlib import Path

import httpx
import pytest
from sqlmodel import Session, select
from typer.testing import CliRunner

from signalbench import cli
from signalbench.cli import app
from signalbench.db.models import Price
from signalbench.ingest.kraken import crypto_ticker

runner = CliRunner()
FIXTURE = Path(__file__).parent / "fixtures" / "kraken" / "XBTUSD_1440.csv"
CONFIG = Path(__file__).parent / "fixtures" / "strategy_crypto_test.yaml"
PAIR_KEYS = {"XBTUSD": "XXBTZUSD", "ETHUSD": "XETHZUSD"}


def test_crypto_help_lists_its_commands() -> None:
    assert "crypto" in runner.invoke(app, ["--help"]).stdout
    result = runner.invoke(app, ["crypto", "--help"])
    assert result.exit_code == 0
    for command in ("ingest-file", "ingest-recent", "backtest"):
        assert command in result.stdout


def test_ingest_file_loads_a_kraken_daily_file(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "get_session", lambda: session)
    result = runner.invoke(app, ["crypto", "ingest-file", str(FIXTURE), "--symbol", "XBTUSD"])
    assert result.exit_code == 0, result.stderr
    assert "XBTUSD: +2 ~0 x1 (2015-01-01 to 2015-01-03)" in result.stdout
    assert len(session.exec(select(Price)).all()) == 2


def test_ingest_file_refuses_another_pairs_file() -> None:
    result = runner.invoke(app, ["crypto", "ingest-file", str(FIXTURE), "--symbol", "ETHUSD"])
    assert result.exit_code == 1
    assert "XBTUSD_1440.csv is not a ETHUSD file" in result.stderr


def test_ingest_recent_needs_the_file_history_first(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "get_session", lambda: session)
    result = runner.invoke(app, ["crypto", "ingest-recent"])
    assert result.exit_code == 1
    assert "ETHUSD FAILED: ETHUSD has no history yet" in result.stderr
    assert "XBTUSD FAILED: XBTUSD has no history yet" in result.stderr


def test_ingest_recent_upserts_both_pairs_from_the_public_api(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    crypto_ticker(session, "XBTUSD")
    crypto_ticker(session, "ETHUSD")
    monkeypatch.setattr(cli, "get_session", lambda: session)

    def handler(request: httpx.Request) -> httpx.Response:
        rows = [[1420070400, "10", "11", "9", "10.5", "10.2", "3.4", 7]]  # 2015-01-01
        key = PAIR_KEYS[request.url.params["pair"]]
        return httpx.Response(200, json={"error": [], "result": {key: rows, "last": 1420070400}})

    real_client = httpx.Client
    monkeypatch.setattr(
        cli.httpx, "Client", lambda **_kwargs: real_client(transport=httpx.MockTransport(handler))
    )
    result = runner.invoke(app, ["crypto", "ingest-recent"])
    assert result.exit_code == 0, result.stderr
    assert "ETHUSD: +1 ~0 x0 (to 2015-01-01)" in result.stdout
    assert "XBTUSD: +1 ~0 x0 (to 2015-01-01)" in result.stdout


def test_backtest_needs_the_config_file(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["crypto", "backtest", "--config", str(tmp_path / "strategy_crypto-v1.yaml")]
    )
    assert result.exit_code == 1
    assert "not found" in result.stderr


def test_backtest_needs_a_committed_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "committed_unchanged", lambda _repo, _path: False)
    result = runner.invoke(app, ["crypto", "backtest", "--config", str(CONFIG)])
    assert result.exit_code == 1
    assert "must be committed, unchanged" in result.stderr


def test_backtest_refuses_a_config_outside_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "committed_unchanged", lambda _repo, _path: True)
    result = runner.invoke(app, ["crypto", "backtest", "--config", str(CONFIG)])
    assert result.exit_code == 1
    assert "must be data/strategy_crypto-test.yaml" in result.stderr
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest -q tests/test_crypto_cli.py`
Expected: 8 failed (no `crypto` command: exit code 2, "No such command 'crypto'").

- [ ] **Step 3: Implement.** In `src/signalbench/cli.py`:

(a) Imports. Add `from signalbench.backtest.crypto_runner import run_crypto_backtest` just before `from signalbench.backtest.preregistration import (`; add, just before `from signalbench.ingest.liquidity import update_liquidity_flags`:

```python
from signalbench.ingest.kraken import (
    CRYPTO_NAMES,
    KrakenError,
    crypto_ticker,
    fetch_kraken_daily,
    find_crypto_ticker,
    ingest_kraken_bars,
    parse_ohlcvt_file,
)
```

and replace `from signalbench.market.calendar import HISTORY_START, CboeCanadaSessions, NyseSessions` with:

```python
from signalbench.market.calendar import (
    HISTORY_START,
    CboeCanadaSessions,
    DailySessions,
    NyseSessions,
)
```

(b) After `STRATEGY_V1_PATH = …`, add:

```python
CRYPTO_V1_PATH = REPO_ROOT / "data" / "strategy_crypto-v1.yaml"
```

(c) Append to the end of the file:

```python
crypto_app = typer.Typer(help="Crypto research on Kraken daily candles (spec 09). Nothing trades.")
app.add_typer(crypto_app, name="crypto")


class CryptoPair(str, Enum):
    XBTUSD = "XBTUSD"
    ETHUSD = "ETHUSD"


@crypto_app.command("ingest-file")
def crypto_ingest_file(
    path: Annotated[Path, typer.Argument(help="A Kraken OHLCVT daily file, e.g. XBTUSD_1440.csv.")],
    symbol: Annotated[CryptoPair, typer.Option("--symbol", help="The Kraken pair in the file.")],
) -> None:
    if not path.exists():
        typer.echo(f"{path} not found.", err=True)
        raise typer.Exit(1)
    if not path.name.upper().startswith(f"{symbol.value}_"):
        typer.echo(
            f"{path.name} is not a {symbol.value} file (Kraken names it {symbol.value}_1440.csv).",
            err=True,
        )
        raise typer.Exit(1)
    try:
        bars = parse_ohlcvt_file(path)
        if not bars:
            raise KrakenError(f"{path.name} has no rows")
        with get_session() as session:
            result = ingest_kraken_bars(
                session, crypto_ticker(session, symbol.value), bars, settings.price_history_start
            )
    except KrakenError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None
    typer.echo(
        f"{symbol.value}: +{result.created} ~{result.updated} x{result.rejected} "
        f"({bars[0].date} to {bars[-1].date})"
    )


@crypto_app.command("ingest-recent")
def crypto_ingest_recent() -> None:
    today = datetime.now(UTC).date()
    failed: list[str] = []
    with get_session() as session, httpx.Client(timeout=30.0) as client:
        for symbol in CRYPTO_NAMES:
            try:
                ticker = find_crypto_ticker(session, symbol)
                if ticker is None:
                    raise KrakenError(
                        f"{symbol} has no history yet. Run `signalbench crypto ingest-file` first."
                    )
                bars = fetch_kraken_daily(client, symbol, today)
                result = ingest_kraken_bars(session, ticker, bars, settings.price_history_start)
            except (KrakenError, httpx.HTTPError) as error:
                session.rollback()
                typer.echo(f"{symbol} FAILED: {error}", err=True)
                failed.append(symbol)
                continue
            last = bars[-1].date.isoformat() if bars else "none"
            typer.echo(
                f"{symbol}: +{result.created} ~{result.updated} x{result.rejected} (to {last})"
            )
    if failed:
        raise typer.Exit(1)


@crypto_app.command("backtest")
def crypto_backtest(
    config: Annotated[
        Path, typer.Option("--config", help="Pre-registered crypto parameters.")
    ] = CRYPTO_V1_PATH,
) -> None:
    if not config.exists():
        typer.echo(f"{config} not found (spec 09 pre-registration).", err=True)
        raise typer.Exit(1)
    if not committed_unchanged(REPO_ROOT, config):
        typer.echo(
            f"{config.name} must be committed, unchanged, before a backtest on real data "
            "(spec 09 pre-registration).",
            err=True,
        )
        raise typer.Exit(1)
    try:
        strategy, sha = load_strategy_config(config)
    except (ConfigError, yaml.YAMLError) as error:
        typer.echo(" ".join(f"{config.name}: {error}".split()), err=True)
        raise typer.Exit(1) from None
    try:
        now = datetime.now(UTC)
        check_config_path(REPO_ROOT, config, strategy.version)
        with get_session() as session:
            run, path = run_crypto_backtest(
                session,
                config=strategy,
                config_sha256=sha,
                calendar=DailySessions(),
                git_sha=git_sha(REPO_ROOT),
                run_date=now.date(),
                reports_dir=REPORTS_DIR,
                now=now,
            )
            _print_run(run)
    except ValueError as error:  # RunRefusedError and data gaps
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None
    typer.echo(f"report: {path}")
```

- [ ] **Step 4: Run the tests** → `tests/test_crypto_cli.py` 8 passed; full checks → **766 passed**.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/cli.py tests/test_crypto_cli.py
git commit -m "feat: signalbench crypto ingest-file, ingest-recent, and backtest (spec 09)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: The config-file test and the README

**Files:**
- Create: `tests/test_strategy_crypto_file.py`
- Modify: `README.md`

- [ ] **Step 1: Write the test** (it skips until Task 9 writes the config) in `tests/test_strategy_crypto_file.py`:

```python
"""Spec 09: data/strategy_crypto-v1.yaml is data/strategy_v2-none-cash.yaml with only the
declared fields changed, and its cost is Kraken's top published fee."""

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from signalbench.backtest.preregistration import KRAKEN_TOP_FEE
from signalbench.strategy.config import load_strategy_config

DATA = Path(__file__).resolve().parents[1] / "data"
CHANGES = (
    ("version: v2-none-cash\n", "version: crypto-v1\n"),
    ("cost_per_side: 0.002\n", "cost_per_side: 0.004\n"),
    ("  symbol: QQQ\n", "  symbol: XBTUSD\n"),
    ("  min_median_traded_value_usd: 50000000\n", "  min_median_traded_value_usd: 0\n"),
    ("  start: 2012-01-01\n", "  start: 2015-01-01\n"),
    ("  h1_end: 2018-12-31\n", "  h1_end: 2020-12-31\n"),
    ("  h2_start: 2019-01-01\n", "  h2_start: 2021-01-01\n"),
    ("  recent_since: 2026-09-15\n", "  recent_since: 2026-07-01\n"),
)


def _crypto() -> Path:
    path = DATA / "strategy_crypto-v1.yaml"
    if not path.exists():
        pytest.skip(f"{path.name} is written by spec 09 Task 9 and committed on its own")
    return path


def _body(path: Path) -> str:
    """The file without its header comment lines."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return "".join(f"{line}\n" for line in lines if not line.startswith("#"))


def test_crypto_v1_is_v2_none_cash_with_only_the_declared_fields_changed() -> None:
    path = _crypto()
    expected = _body(DATA / "strategy_v2-none-cash.yaml")
    for old, new in CHANGES:
        assert expected.count(old) == 1, old
        expected = expected.replace(old, new)
    assert _body(path) == expected
    first = path.read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("# Spec 09: Breakout on BTC and ETH daily candles from Kraken.")


def test_crypto_v1_parses_to_v2_none_cash_plus_its_changes() -> None:
    v2, _sha = load_strategy_config(DATA / "strategy_v2-none-cash.yaml")
    config, _sha = load_strategy_config(_crypto())
    assert config == replace(
        v2,
        version="crypto-v1",
        cost_per_side=KRAKEN_TOP_FEE,
        regime_symbol="XBTUSD",
        liquidity_min_traded_value=0.0,
        backtest=replace(
            v2.backtest,
            start=date(2015, 1, 1),
            h1_end=date(2020, 12, 31),
            h2_start=date(2021, 1, 1),
            recent_since=date(2026, 7, 1),
        ),
    )
```

Run: `uv run pytest -q tests/test_strategy_crypto_file.py -rs` → 2 skipped ("strategy_crypto-v1.yaml is written by spec 09 Task 9 and committed on its own").

- [ ] **Step 2: README.** In `README.md`'s CLI reference, add a **Crypto research (spec 09)** table after the "Live strategy" table:

```markdown
**Crypto research (spec 09)**

| Command | Purpose |
| --- | --- |
| `uv run signalbench crypto ingest-file PATH --symbol {XBTUSD,ETHUSD}` | Load a Kraken OHLCVT daily file (e.g. `XBTUSD_1440.csv`); creates the crypto ticker on first use |
| `uv run signalbench crypto ingest-recent` | Upsert the complete daily candles from Kraken's public OHLC endpoint (about 720 days; today's is never stored) |
| `uv run signalbench crypto backtest [--config PATH]` | The pre-registered Breakout run on BTC and ETH against BTC buy-and-hold (`data/strategy_crypto-v1.yaml` by default) |
```

and in "Roadmap", after the 07 line, add:

```markdown
- 09: crypto daily trend, Breakout on BTC and ETH from Kraken (research only; see the spec)
```

- [ ] **Step 3: Run the full checks** → **766 passed, 2 skipped**.

- [ ] **Step 4: Commit**

```bash
git add tests/test_strategy_crypto_file.py README.md
git commit -m "test: crypto-v1 must be v2-none-cash with only the declared fields changed (spec 09)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Pre-register `data/strategy_crypto-v1.yaml` (committed on its own)

This is the pre-registration. The commit holds this one file and nothing else.

**Files:**
- Create: `data/strategy_crypto-v1.yaml`

- [ ] **Step 1: Write the config**, exactly:

```yaml
# Spec 09: Breakout on BTC and ETH daily candles from Kraken. Pre-registered, one try.
# A copy of data/strategy_v2-none-cash.yaml with only version, cost_per_side, regime.symbol,
# liquidity.min_median_traded_value_usd, and the backtest dates changed, committed on its own
# before any run on real data. Do not edit: any change is a new version, reported as post-hoc.
# cost_per_side is Kraken's top published fee (KRAKEN_TOP_FEE). Every session is a calendar
# day. Crypto has no earnings dates, so the earnings rules never fire.
version: crypto-v1
start_equity: 100
risk_pct: 0.02
max_positions: 3
max_per_sector: 2
pause_drawdown: 0.15
auto_resume_sessions: 10
gap_up_limit: 0.01
cost_per_side: 0.004
setup_priority: [pullback, breakout, sentiment]
regime:
  symbol: XBTUSD
  sma: 200
indicators:
  atr_period: 14
  rsi_period: 2
liquidity:
  min_median_traded_value_usd: 0
  sessions: 20
earnings:
  blackout_sessions: 3
  exit_sessions: 2
setups:
  pullback:
    trend_sma_fast: 50
    trend_sma_slow: 200
    rsi_max: 10
    stop_low_sessions: 3
    stop_atr_mult: 0.5
    target_r: 2.0
    time_limit: 10
  breakout:
    trend_sma: 50
    lookback: 20
    volume_lookback: 50
    volume_mult: 1.5
    stop_atr_mult: 2.0
    trail_atr_mult: 3.0
    time_limit: null
  sentiment:
    trend_sma: 20
    stop_atr_mult: 2.0
    target_r: 2.0
    time_limit: 10
backtest:
  start: 2015-01-01
  h1_end: 2020-12-31
  h2_start: 2021-01-01
  recent_since: 2026-07-01
  pass_bar:
    min_trades: 30
    min_mean_r: 0.10
```

- [ ] **Step 2: Run the full checks** → **768 passed** (the two file tests now run).

- [ ] **Step 3: Commit it alone**

```bash
git add data/strategy_crypto-v1.yaml
git commit -m "data: pre-register crypto-v1, Breakout on BTC and ETH (spec 09)

Committed on its own before any run on real data.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git show --stat HEAD
```

Expected: `git show --stat HEAD` lists only `data/strategy_crypto-v1.yaml`.

---

### Task 10: Load the real data and run once

**Controller only. Stop and ask the owner for a go-ahead before Step 1.** Everything here touches the research database, and Step 4 produces the one result.

- [ ] **Step 1: Start and migrate the local database**

Run: `docker compose up -d`, then `uv run alembic upgrade head`. Check first that `DATABASE_URL` (in `.env`, or the default in `src/signalbench/config.py`) points at `localhost`, not at botbox or ubuntuworker.
Expected: the migrations run up to `0017_crypto_ticker_kind`.

- [ ] **Step 2: Load the history files**

```bash
uv run signalbench crypto ingest-file "$HOME/kraken-ohlcvt/XBTUSD_1440.csv" --symbol XBTUSD
uv run signalbench crypto ingest-file "$HOME/kraken-ohlcvt/ETHUSD_1440.csv" --symbol ETHUSD
```

Expected: `XBTUSD: +4639 ~0 x0 (2013-10-06 to 2026-06-30)` and `ETHUSD: +3975 ~0 x0 (2015-08-07 to 2026-06-30)`.

- [ ] **Step 3: Bring both up to yesterday (UTC)**

Run: `uv run signalbench crypto ingest-recent`
Expected: one line per pair, each ending `(to <yesterday in UTC>)`, with about 100+ new rows (2026-07-01 onward) and few or no updates. If many rows are updated, stop and report: the API and the file disagree.

- [ ] **Step 4: Run the backtest once**

Run: `uv run signalbench crypto backtest`
Expected: `run <id>: PASS` or `FAIL`, the four pass-bar lines, and `report: reports/backtests/<date>-crypto-v1-breakout-off.md`. A refusal (for example a series that does not end yesterday because the run crossed 00:00 UTC) stores nothing; fix the cause (rerun Step 3) and run again. **Do not run it again after a stored result**, and never edit the config: a change is a new version, reported as post-hoc.

- [ ] **Step 5: Commit the report**

```bash
git add reports/backtests/<date>-crypto-v1-breakout-off.md
git commit -m "report: crypto-v1 Breakout on BTC and ETH, <PASS or FAIL> (spec 09)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Record the result

**Files:**
- Modify: `docs/research-log.md`, `docs/superpowers/specs/2026-10-10-swing-assistant-09-crypto-daily-trend-design.md`

- [ ] **Step 1: Research log.** Add an entry before "## Open questions (not yet tested)", filled in from the report:

```markdown
## <run date> — Crypto daily trend: Breakout on BTC and ETH (spec 09)

- **Kind:** pre-registered, one try. `data/strategy_crypto-v1.yaml` committed on its own (commit `<sha>`) before any crypto data was loaded. The rules are v2-none-cash's, none chosen on crypto data; the owner has seen crypto price charts.
- **Test:** Breakout on Kraken XBT/USD and ETH/USD daily candles, every calendar day a session, BTC above its 200-day average gating entries, 0.40% per side, no liquidity floor. Days <start> to <end>.
- **Result:**

  | Series | Trades | Mean R (worse half) | Total return | CAGR | Sharpe | Max drawdown | Result |
  | --- | --- | --- | --- | --- | --- | --- | --- |
  | crypto-v1 Breakout | <n> | <mean R> (<worse half>) | <x>% | <x>% | <x> | <x>% | <PASS or FAIL (which criteria)> |
  | BTC buy-and-hold | | | <x>% | <x>% | <x> | <x>% | |

- **Missing days:** <from the report's Missing days section>.
- **Report:** `reports/backtests/<date>-crypto-v1-breakout-off.md`.
- **Takeaway:** <one or two plain sentences: what this does and does not show>.
```

- [ ] **Step 2: Spec changelog.** Append to the spec's "Changelog": `- <run date>: run once; <PASS or FAIL>; report and research-log entry committed.`

- [ ] **Step 3: Commit**

```bash
git add docs/research-log.md docs/superpowers/specs/2026-10-10-swing-assistant-09-crypto-daily-trend-design.md
git commit -m "docs: log the crypto-v1 result (spec 09)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
