# Swing Assistant 03 — Jev Reader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Have Jev read every stored 8-K and news item once per universe ticker; decide by the pre-registered rule whether a negative reading may block entries; backtest the Sentiment setup; and publish a calibration report. Spec 02's v1 result (Pullback and Breakout both FAIL, owner: "no-go on both") stands whatever happens here.

**Architecture:** A new `signalbench.jev` package holds the question set `q1`, an OpenRouter client with strict response validation and retries (`JevClient` protocol, `FakeJevClient` for tests), a resumable backfill that reads (document, ticker) pairs on a 4-thread pool and writes `jev_readings` (migration `0011`) on the main thread, the pure filter decision and its record (`data/jev_filter_v1.yaml` plus a report), and the calibration report. `market/legal_close.py` maps a document's timestamp to its legal close from real NYSE session closes. `strategy/readings.py` gains `JevReadingsView`, a pure `ReadingsView` built once from precomputed readings. The spec 02 runner gains the Sentiment run (2016 → end, calendar-midpoint halves) and the information-only `--jev filter` run, and `signalbench jev test|backfill|fit-filter|calibration` drive it all. `data/strategy_v1.yaml` is never touched.

**Tech Stack:** Python 3.11, httpx (sync client, `MockTransport` in tests), `concurrent.futures.ThreadPoolExecutor`, SQLModel/SQLAlchemy JSON columns, Alembic, PyYAML, Typer, pytest, ruff, mypy strict. No new dependencies.

**Spec:** [`docs/superpowers/specs/2026-09-22-swing-assistant-03-jev-reader-design.md`](../specs/2026-09-22-swing-assistant-03-jev-reader-design.md) (with its 2026-09-24 changelog entry). **Depends on:** branch `feat/swing-02-strategy-backtest` (PR #5, not merged yet); this branch, `feat/swing-03-jev-reader`, is cut from it.

---

## Conventions for every task

- Run commands from the repo root. Use `uv run …` for everything. Stay on branch `feat/swing-03-jev-reader`; never push without asking the owner.
- **Never edit `data/strategy_v1.yaml`.** Its `config_sha256` is recorded in the stored v1 runs, and the pre-registration guard refuses a changed v1. **Never edit `.env.example`** (the owner's permission settings block it); Task 14 tells the owner which line to add to `.env`. Stage explicit paths only; never `git add -A` or `git add .`.
- mypy runs strict on `src/` only. SQLModel's `select()` is typed for at most 4 columns, so queries here select at most 4 columns or whole rows; wrap columns in `col()` for operators such as `.in_()`.
- **Strategy code stays pure.** `JevReadingsView` receives precomputed readings and session lists; it never touches the database, a clock, or the network.
- Tests never touch the network or the real database. They use the in-memory SQLite `session` fixture from `tests/conftest.py`, `FakeJevClient`, and `httpx.MockTransport`. `tests/strategy_helpers.py` is imported as `from strategy_helpers import …`.
- ruff 0.16 sorts imports (`I001`). Run `uv run ruff check --fix .` only **after** the module a test imports exists: before that, ruff files the missing `signalbench.*` module as third-party and moves the import.
- After each task: `uv run pytest -q`, `uv run ruff check .`, and `uv run mypy src` all pass before committing.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Every code block in this plan was replayed, task by task, in a scratch copy of the branch before the plan was committed: each "Expected" failure and pass count below is from that replay. The suite starts at 284 passed and ends at 415 passed after Task 13, with ruff and mypy clean. If a number disagrees, look for a transcription slip in the module before touching the test.
- Beyond the unit tests, the whole pipeline was dry-run on a throwaway copy of the local database (migrated to `0011`), with a fake client giving random readings: the backfill read all 85,318 pairs (5,110 filings, 80,208 news) in about 8 minutes, and `jev fit-filter`, `jev fit-filter --write`, `backtest run --setup sentiment`, a `--jev filter` run, and `jev calibration` each finished in about 10 seconds. The real run's speed is set by the API (about 4 calls at a time).

## Jev API facts (checked live 2026-09-24; they override the 2026-09-22 docs)

| Fact | Value |
| --- | --- |
| Endpoint | `POST https://openrouter.ai/api/alpha/decisions` (not `/api/v1/alpha/decisions`) |
| Headers | `Authorization: Bearer $OPENROUTER_API_KEY`, `Content-Type: application/json` |
| Body | `{"model": "typesafe/jev-1.13", "state": <string>, "questions": {<id>: {"type", "instructions", "criteria"}}}`; `session_id` and `user` are optional and not sent |
| 200 body | `{"answers": {...}, "id": "gen-dec-…", "model": "typesafe/jev-1.13-20260917", "provider": "TypeSafe", "usage": {"cost", "input_tokens", "output_tokens"}}`; a `choice` answer has `choice`, `confidence`, `probabilities`; a `noul` answer has `noul` |
| Retry (backoff 1 s, 2 s, 4 s; 3 retries) | 429, any 5xx (the documented 500, 502, 503, 524, 529), timeouts, connection errors |
| Stop the whole backfill | 401 (key), 402 (insufficient credits), 404 (model) |
| Skip the document, record why | 400, 403, 413 (too large), any other 4xx, a 200 that fails validation, retries exhausted |
| Price | $0.042 per 1M input tokens; output tokens free |
| Limits | 32K tokens of state per request; spec 01 caps 8-K `text` at 96,000 characters (about 24K tokens) |

Measured on the local database on 2026-09-24, for the 40 universe tickers: 5,111 8-K pairs (5,110 with text), 108,503 news pairs, of which 28,295 fall over the 20-per-day cap on 1,363 symbol-days, leaving 80,208 news pairs to read. 15,609 of 88,682 universe documents link to two or more universe tickers. Estimated cost: filings about $1.10, news about $1.80.

## Definitions (the pre-registered ones)

| Term | Definition |
| --- | --- |
| Legal close | The first NYSE session whose close (real close time, so 13:00 ET on early-close days) is strictly after the document's timestamp. 8-K: `acceptance_at`, else `published_at`; news: `published_at` |
| "The last N sessions" at `as_of` | The N sessions ending with `as_of`, inclusive. A document is in the window when its legal close is in those sessions (so it is never visible before its legal close) |
| Positive | `p_positive ≥ 0.70` and `p_routine < 0.50` (fixed; not fitted) |
| Catalyst / sentiment trigger | A positive document in the last 10 / 3 sessions |
| Blocked | Only when the filter is ON: a document in the last 10 sessions with `p_negative ≥ θ_block` |
| Trade score (filter step 1) | Max `p_negative` over the symbol's documents in the 10 sessions ending on the trade's signal date; no reading means never blocked |
| Eligible θ | θ ∈ {0.5, 0.6, 0.7, 0.8, 0.9} that blocks at least 10 fit-window trades (signals 2016-01-01 → 2022-12-31) and keeps at least one |
| Chosen θ | The eligible θ with the largest (kept mean R − blocked mean R); ties go to the lower θ |
| Filter ON | With the chosen θ on confirmation trades (signals 2023-01-01 → end): at least 10 blocked **and** blocked mean R < kept mean R. Otherwise information-only |
| Sentiment period | Sessions from 2016-01-01 to the run's last session; H1 = entries up to the calendar midpoint `2016-01-01 + ⌊days/2⌋`, H2 = entries after it. Same pass bar |
| Sentiment information-only | Fewer than 30 trades (the pass-bar minimum) |
| Out-of-sample trades | Signal date ≥ 2026-09-15 (Jev's release); reported apart from the spec 02 "entered on or after 2026-09-15" stats |
| Calibration label | Symbol adj-close return minus QQQ's, from the legal close to 5 sessions later: > +2% `up`, < −2% `down`, else `flat` (excess rounded to 10 decimals) |
| Deciles / ECE | Bucket `min(⌊10p⌋, 9)`, so 1.0 sits in the top bucket. ECE = Σ (n_b / N) × \|mean predicted_b − observed rate_b\| |
| Argmax accuracy | Argmax of (negative, neutral, positive) mapped to (down, flat, up), ties in that order; the baseline is the most frequent label's share |

## Decisions this plan makes where the spec is silent

- **Readings are per (document, ticker), not per document.** The spec keys `jev_readings` on (`document_id`, `model_requested`, `question_set`), but the state names one company and the question asks what the document means for *that* company's shareholders. 15,609 universe documents (17.6%) are linked to two or more universe tickers, so a per-document reading would be wrong for every ticker but one. The table adds `ticker_id` and is unique on (`document_id`, `ticker_id`, `model_requested`, `question_set`); the backfill is still idempotent and resumable. Extra cost: about 25,000 more news reads (under $0.60). **Confirm with the owner** (Task 14 records it in the spec 03 changelog).
- **Migration** `0011_jev_readings`. Extra columns: `ticker_id` (above) and `response_id` (OpenRouter's `gen-dec-…` id, for support questions). Per-document failures are **not** stored: the table only holds valid readings, so "has a reading" means "read". Failures print one `skip <symbol> <document_id>: <reason>` line each and are counted in the summary; a skipped document is retried on the next run.
- **Universe only.** The backfill reads documents linked to the 40 `data/cdr_universe.yaml` tickers; documents of tickers left over from the old phases are ignored.
- **State size:** a document whose state would exceed 100,000 characters is skipped before any call (`too long`). No stored 8-K reaches that today (the longest `text` is 96,000). 8-Ks without `text` are skipped (`no text`).
- **Error handling** follows the API facts table. 20 failed documents in a row stop the run (a systematic failure should not burn through 85,000 calls).
- **Budget guard** (`--max-cost-usd`, default 10.00, on `jev backfill`; `jev test` makes one call): no new call is submitted once the summed `usage.cost` reaches the limit; at most 3 calls already in flight still finish and are stored. A budget stop exits 0 and says to run again; a fatal error or repeated failures exit 1. When a 200 omits `usage.cost`, the cost is priced as `input_tokens × $0.042 / 1M`.
- **Validation:** both `choice` answers must list exactly their options with probabilities in [0, 1] summing to 1 ± 0.01, and a `choice` among the options; `noul` must be in [0, 1]; `model`, `id`, and `usage.input_tokens` must be present. `confidence` is never read.
- **Concurrency:** Jev calls run on 4 worker threads; the state is built and every reading is committed on the calling thread, one commit per reading, so an interrupted backfill loses at most the calls in flight.
- **Filter inputs:** the latest stored v1, Jev-off run of `pullback` and of `breakout` (by `run_at`), pooled. Refused when either is missing or when those stored runs used more than one `config_sha256`. The confirmation window ends on the later of the two runs' end dates.
- **`data/jev_filter_v1.yaml`** records `theta_fit` (the chosen θ, or null) and `theta_block` (θ only when ON, else null), plus the reason, both windows, the chosen-θ fit row and the confirmation row, the source run ids, the strategy `config_sha256`, the readings count, the resolved builds, and the report path. `mode` is written as `'on'` (quoted) or `information_only`, because a bare `on` is a YAML 1.1 boolean. `jev fit-filter --write` refuses to overwrite an existing file: the decision is made once.
- **`--jev filter` runs** need `data/jev_filter_v1.yaml` committed and unchanged with `mode: 'on'`; otherwise they are refused (exit 1). They use the same readings view the live scan will use: block at `theta_block`, and catalysts rank first. `--setup sentiment --jev filter` is refused before any data is read (exit 2): Sentiment uses readings as its trigger, and the filter decision covers Pullback and Breakout.
- **Sentiment runs** are `jev_mode off` (no blocking) and use the committed `data/strategy_v1.yaml` unchanged; the runner overrides only `backtest.start`, `h1_end`, and `h2_start` (spec 03 rules), so the pre-registration guard (committed config, same `config_sha256` per version, survey cost) still applies.
- **Runs that read Jev** (Sentiment, `--jev filter`) refuse to start when no reading has a legal close by the run's last session. They store `metrics["jev"]` (model, question set, readings used, resolved builds, `theta_block`, `information_only`, and the out-of-sample stats), and their `data_fingerprint` gains one `jev|<count>|<sha>` entry over every reading used. Jev-off fingerprints are unchanged.
- **Calibration** samples are the same (document, ticker) readings. A reading without a price for the symbol or QQQ at its legal close or 5 sessions later is counted as "not labeled". `jev calibration` always writes `reports/jev/<date>-calibration.md` (a same-day rerun overwrites it) and needs no key.
- **A filtered run's `passed` is pass-bar arithmetic only.** Its report and the CLI print `PASS (information only)` / `FAIL (information only)`; which setups go live comes only from the Jev-off v1 runs and the owner's recorded decision. (While this plan was written, a dry run on a copy of the database with *random* fake readings turned Breakout's Sharpe from 0.949 into 0.998 against QQQ's 0.996: noise alone can flip that criterion, so a filtered PASS means little.)
- **`NyseSessions.sessions_between` clamps its start** to the calendar's first session. The readings code asks for sessions from `HISTORY_START` (2010-01-01, a holiday), and `exchange_calendars` raises `DateOutOfBounds` for a start before its first session (2010-01-04); the dry run above found this.
- **`jev test`** sends a small made-up 8-K (`jev/fixture.py`: "Example Holdings (EXMP)", items 2.02 and 9.01, no dates) so nothing real leaves the machine for a smoke test.
- **CLI output is ASCII** (`|` separators), because the Windows console mangles `·`. The markdown reports keep spec 02's style.

## File map

| Path | Action | Responsibility |
| --- | --- | --- |
| `src/signalbench/market/calendar.py` | Modify | `Sessions.session_closes()`; real NYSE closes, including early closes |
| `src/signalbench/market/legal_close.py` | Create | `LegalCloses`: timestamp → legal close session |
| `src/signalbench/config.py` | Modify | `openrouter_api_key` |
| `src/signalbench/db/models.py` | Modify | `JevReading` |
| `alembic/versions/0011_jev_readings.py` | Create | `jev_readings` table |
| `src/signalbench/jev/__init__.py` | Create | Package marker |
| `src/signalbench/jev/questions.py` | Create | Model id, question set `q1`, state and request body, `JEV_RELEASE` |
| `src/signalbench/jev/client.py` | Create | `JevResult`, `parse_response`, errors, `JevClient`, `OpenRouterJevClient` |
| `src/signalbench/jev/fake.py` | Create | `FakeJevClient`, `fake_result` |
| `src/signalbench/jev/backfill.py` | Create | Pending (document, ticker) pairs, news cap, threaded backfill, budget guard |
| `src/signalbench/jev/store.py` | Create | Readings keyed by legal close, resolved builds, calibration samples |
| `src/signalbench/jev/filter.py` | Create | Pure filter decision (steps 1–3) |
| `src/signalbench/jev/filter_record.py` | Create | v1 trade logs in, `data/jev_filter_v1.yaml` and the decision report out |
| `src/signalbench/jev/calibration.py` | Create | Labels, reliability deciles, ECE, accuracy, report |
| `src/signalbench/jev/fixture.py` | Create | The made-up 8-K for `jev test` |
| `src/signalbench/strategy/readings.py` | Modify | `DocumentReading`, `JevReadingsView` |
| `src/signalbench/backtest/fingerprint.py` | Modify | Optional readings entry |
| `src/signalbench/backtest/report.py` | Modify | `jev_payload`, Sentiment status, information-only banner, Jev section and caveats |
| `src/signalbench/backtest/runner.py` | Modify | Sentiment and `--jev filter` runs; `RequiresSpec03Error` removed |
| `src/signalbench/cli.py` | Modify | `backtest run` wiring; `jev test`, `jev backfill`, `jev fit-filter`, `jev calibration` |
| `tests/strategy_helpers.py` | Modify | `WeekdaySessions.session_closes()` |
| `tests/test_*.py` | Create/modify | One test file per module (listed in each task) |
| `README.md`, spec 03 changelog | Modify | CLI reference, `.env` key, implementation choices |
| `data/jev_filter_v1.yaml`, `reports/jev/*.md`, `reports/backtests/*-sentiment-off.md` | Create (gated) | The recorded decisions and reports |

---

### Task 1: Session closes and the legal close

**Files:**
- Modify: `src/signalbench/market/calendar.py`, `tests/strategy_helpers.py`
- Create: `src/signalbench/market/legal_close.py`, `tests/test_legal_close.py`

- [ ] **Step 1: Write the failing test**

`tests/test_legal_close.py`:

```python
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from signalbench.market.calendar import NyseSessions
from signalbench.market.legal_close import LegalCloses
from strategy_helpers import WeekdaySessions

NEW_YORK = ZoneInfo("America/New_York")
TUESDAY = date(2024, 1, 2)
CLOSES = LegalCloses(WeekdaySessions().session_closes(date(2024, 1, 1), date(2024, 1, 31)))


def _ny(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=NEW_YORK)


def test_before_the_close_is_that_sessions_close() -> None:
    assert CLOSES.of(_ny(TUESDAY, 15, 59)) == TUESDAY
    assert CLOSES.of(_ny(TUESDAY, 4, 0)) == TUESDAY  # pre-market


def test_at_or_after_the_close_is_the_next_session() -> None:
    assert CLOSES.of(_ny(TUESDAY, 16, 0)) == date(2024, 1, 3)  # strictly after the close
    assert CLOSES.of(_ny(TUESDAY, 16, 5)) == date(2024, 1, 3)


def test_a_weekend_document_waits_for_monday() -> None:
    assert CLOSES.of(_ny(date(2024, 1, 6), 11, 0)) == date(2024, 1, 8)


def test_utc_timestamps_are_compared_as_instants() -> None:
    # 21:05 UTC is 16:05 EST: after Tuesday's close.
    assert CLOSES.of(datetime(2024, 1, 2, 21, 5, tzinfo=UTC)) == date(2024, 1, 3)
    assert CLOSES.of(datetime(2024, 1, 2, 20, 55, tzinfo=UTC)) == TUESDAY


def test_after_the_last_known_close_is_none() -> None:
    assert CLOSES.of(_ny(date(2024, 1, 31), 16, 30)) is None


def test_naive_timestamps_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        CLOSES.of(datetime(2024, 1, 2, 12, 0))  # noqa: DTZ001  # naive on purpose


def test_an_early_close_moves_the_cutoff() -> None:
    nyse = NyseSessions(start=date(2022, 1, 1))
    closes = LegalCloses(nyse.session_closes(date(2022, 11, 21), date(2022, 11, 30)))
    # 2022-11-25, the day after Thanksgiving, closed at 13:00 ET.
    assert closes.of(_ny(date(2022, 11, 25), 12, 59)) == date(2022, 11, 25)
    assert closes.of(_ny(date(2022, 11, 25), 13, 30)) == date(2022, 11, 28)
    # Thanksgiving itself is not a session.
    assert closes.of(_ny(date(2022, 11, 24), 10, 0)) == date(2022, 11, 25)


def test_nyse_session_closes_are_aware_utc_instants() -> None:
    nyse = NyseSessions(start=date(2022, 1, 1))
    closes = dict(nyse.session_closes(date(2022, 11, 23), date(2022, 11, 28)))
    assert sorted(closes) == [date(2022, 11, 23), date(2022, 11, 25), date(2022, 11, 28)]
    assert closes[date(2022, 11, 23)] == datetime(2022, 11, 23, 21, 0, tzinfo=UTC)
    assert closes[date(2022, 11, 25)] == datetime(2022, 11, 25, 18, 0, tzinfo=UTC)
    assert nyse.session_closes(date(2022, 11, 28), date(2022, 11, 21)) == []


def test_nyse_ranges_that_start_before_the_calendar_are_clamped() -> None:
    nyse = NyseSessions()  # starts 2010-01-01, a holiday; the first session is 2010-01-04
    assert nyse.sessions_between(date(2010, 1, 1), date(2010, 1, 6)) == [
        date(2010, 1, 4), date(2010, 1, 5), date(2010, 1, 6),
    ]
    assert [day for day, _ in nyse.session_closes(date(2010, 1, 1), date(2010, 1, 5))] == [
        date(2010, 1, 4), date(2010, 1, 5),
    ]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_legal_close.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'signalbench.market.legal_close'`.

- [ ] **Step 3: Add session closes to the calendar**

`src/signalbench/market/calendar.py` (complete file; `session_closes` joins the `Sessions` protocol, so every calendar can give legal closes, and `sessions_between` clamps a start before the calendar's first session, because `exchange_calendars` raises `DateOutOfBounds` for `HISTORY_START`, 2010-01-01, a holiday):

```python
"""NYSE sessions as plain dates. The only module that imports exchange_calendars."""

from datetime import date, datetime, timedelta
from typing import Any, Protocol

HISTORY_START = date(2010, 1, 1)


class Sessions(Protocol):
    def sessions_between(self, start: date, end: date) -> list[date]:
        """Sessions with start <= session <= end, in order."""
        ...

    def next_sessions(self, day: date, count: int) -> list[date]:
        """The first `count` sessions strictly after `day`."""
        ...

    def is_session(self, day: date) -> bool: ...

    def session_closes(self, start: date, end: date) -> list[tuple[date, datetime]]:
        """(session, its close as an aware datetime) for sessions with start <= session <= end."""
        ...


class NyseSessions:
    """The XNYS calendar from exchange_calendars, exposed as `datetime.date` values."""

    def __init__(self, start: date = HISTORY_START) -> None:
        import exchange_calendars as xcals

        self._calendar: Any = xcals.get_calendar("XNYS", start=start.isoformat())
        self._first: date = self._calendar.first_session.date()

    def sessions_between(self, start: date, end: date) -> list[date]:
        start = max(start, self._first)  # exchange_calendars rejects dates before its first session
        if end < start:
            return []
        return [stamp.date() for stamp in self._calendar.sessions_in_range(start, end)]

    def next_sessions(self, day: date, count: int) -> list[date]:
        # Weekends plus the longest NYSE closure stay far inside 2 * count + 10 calendar days.
        horizon = day + timedelta(days=2 * count + 10)
        found = self.sessions_between(day + timedelta(days=1), horizon)
        if len(found) < count:
            raise ValueError(f"Only {len(found)} NYSE sessions known after {day}; need {count}")
        return found[:count]

    def is_session(self, day: date) -> bool:
        return bool(self._calendar.is_session(day))

    def session_closes(self, start: date, end: date) -> list[tuple[date, datetime]]:
        """Real closes in UTC, including early closes (13:00 ET on some holiday eves)."""
        if end < start:
            return []
        closes = self._calendar.closes.loc[start.isoformat() : end.isoformat()]
        return [(stamp.date(), close.to_pydatetime()) for stamp, close in closes.items()]
```

In `tests/strategy_helpers.py`, replace:

```python
from datetime import date, timedelta
from pathlib import Path

```

with:

```python
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

```

In `tests/strategy_helpers.py`, replace:

```python
FIXTURE_CONFIG = Path(__file__).parent / "fixtures" / "strategy_test.yaml"
```

with:

```python
FIXTURE_CONFIG = Path(__file__).parent / "fixtures" / "strategy_test.yaml"
NEW_YORK = ZoneInfo("America/New_York")
```

In `tests/strategy_helpers.py`, replace:

```python
    def is_session(self, day: date) -> bool:
        return day.weekday() < 5
```

with:

```python
    def is_session(self, day: date) -> bool:
        return day.weekday() < 5

    def session_closes(self, start: date, end: date) -> list[tuple[date, datetime]]:
        """Every fixture session closes at 16:00 New York time."""
        return [
            (day, datetime.combine(day, time(16, 0), tzinfo=NEW_YORK))
            for day in self.sessions_between(start, end)
        ]
```

- [ ] **Step 4: Write the legal-close map**

`src/signalbench/market/legal_close.py`:

```python
"""Legal close (overview, Shared terms): the first NYSE session close strictly after a moment."""

from bisect import bisect_right
from collections.abc import Sequence
from datetime import date, datetime


class LegalCloses:
    """Maps a document's acceptance or publish time to the session whose close first follows it.

    Built once from `Sessions.session_closes()`, so early closes are honoured and no calendar
    lookup happens per document.
    """

    def __init__(self, closes: Sequence[tuple[date, datetime]]) -> None:
        self._sessions = [session for session, _ in closes]
        self._closes = [close for _, close in closes]
        if any(close.utcoffset() is None for close in self._closes):
            raise ValueError("session closes must be timezone-aware")
        if any(a >= b for a, b in zip(self._closes, self._closes[1:], strict=False)):
            raise ValueError("session closes must strictly increase")

    def of(self, moment: datetime) -> date | None:
        """The first session whose close is strictly after `moment`, or None if that session is
        past the last known close (the document is not visible yet)."""
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError(f"moment must be timezone-aware, got naive {moment.isoformat()}")
        index = bisect_right(self._closes, moment)
        return self._sessions[index] if index < len(self._sessions) else None
```

- [ ] **Step 5: Run the tests and checks**

Run: `uv run pytest tests/test_legal_close.py -v && uv run pytest -q && uv run mypy src && uv run ruff check .`
Expected: 9 passed; the full suite 293 passed; mypy `Success`; ruff `All checks passed!`.

- [ ] **Step 6: Commit**

```bash
git add src/signalbench/market/calendar.py src/signalbench/market/legal_close.py tests/strategy_helpers.py tests/test_legal_close.py
git commit -m "feat: NYSE session closes and the legal-close map

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Settings, `jev_readings`, migration 0011

**Files:**
- Modify: `src/signalbench/config.py`, `src/signalbench/db/models.py`, `tests/test_config.py`, `tests/test_migrations.py`
- Create: `alembic/versions/0011_jev_readings.py`, `tests/test_jev_schema.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_jev_schema.py`:

```python
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from signalbench.db.models import DocType, JevReading, RawDocument, Ticker, TickerKind


def _document_and_ticker(session: Session) -> tuple[RawDocument, Ticker]:
    ticker = Ticker(symbol="AAA", company_name="Aaa Corp", kind=TickerKind.us_stock)
    document = RawDocument(
        source="sec_edgar",
        external_id="0000000000-24-000001",
        doc_type=DocType.eight_k,
        raw_text="<html></html>",
        text="Results.",
        published_at=datetime(2024, 1, 2, tzinfo=UTC),
    )
    session.add_all([ticker, document])
    session.commit()
    return document, ticker


def _reading(document: RawDocument, ticker: Ticker, question_set: str = "q1") -> JevReading:
    return JevReading(
        document_id=document.id,
        ticker_id=ticker.id,
        model_requested="typesafe/jev-1.13",
        model_resolved="typesafe/jev-1.13-20260917",
        question_set=question_set,
        response_id="gen-dec-1",
        p_negative=0.1,
        p_neutral=0.2,
        p_positive=0.7,
        event_type="earnings",
        p_routine=0.05,
        answers={"impact": {"type": "choice", "choice": "positive"}},
        input_tokens=500,
        cost_usd=0.000021,
        latency_ms=850,
    )


def test_a_reading_round_trips(session: Session) -> None:
    document, ticker = _document_and_ticker(session)
    session.add(_reading(document, ticker))
    session.commit()
    stored = session.exec(select(JevReading)).one()
    assert stored.p_positive == 0.7 and stored.event_type == "earnings"
    assert stored.answers == {"impact": {"type": "choice", "choice": "positive"}}
    assert stored.read_at.tzinfo is not None


def test_one_reading_per_document_ticker_model_and_question_set(session: Session) -> None:
    document, ticker = _document_and_ticker(session)
    session.add(_reading(document, ticker))
    session.add(_reading(document, ticker, question_set="q2"))  # a new question set is allowed
    session.commit()
    session.add(_reading(document, ticker))
    with pytest.raises(IntegrityError):
        session.commit()
```

In `tests/test_config.py`, replace:

```python
    for name in ("FINNHUB_API_KEY", "PRICE_HISTORY_START", "FILINGS_BACKFILL_START"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)
    assert settings.finnhub_api_key is None
```

with:

```python
    for name in (
        "FINNHUB_API_KEY", "OPENROUTER_API_KEY", "PRICE_HISTORY_START", "FILINGS_BACKFILL_START"
    ):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)
    assert settings.finnhub_api_key is None
    assert settings.openrouter_api_key is None
```

In `tests/test_config.py`, replace:

```python
    monkeypatch.setenv("FINNHUB_API_KEY", "test-key")
    settings = Settings(_env_file=None)
```

with:

```python
    monkeypatch.setenv("FINNHUB_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")
    settings = Settings(_env_file=None)
```

In `tests/test_config.py`, replace:

```python
    assert settings.finnhub_api_key == "test-key"
```

with:

```python
    assert settings.finnhub_api_key == "test-key"
    assert settings.openrouter_api_key == "or-test-key"
```

In `tests/test_migrations.py`, replace:

```python
    assert _script().get_heads() == ["0010_backtest_runs"]
```

with:

```python
    assert _script().get_heads() == ["0011_jev_readings"]
```

Append to `tests/test_migrations.py`:

```python
def test_0011_creates_jev_readings_unique_per_document_ticker_model_and_questions() -> None:
    text = (VERSIONS / "0011_jev_readings.py").read_text(encoding="utf-8")
    assert 'down_revision: str | None = "0010_backtest_runs"' in text
    assert 'op.create_table(\n        "jev_readings"' in text
    assert 'sa.Column("answers", sa.JSON(), nullable=False)' in text
    assert '["document_id"], ["raw_documents.id"], ondelete="CASCADE"' in text
    assert '"uq_jev_readings_document_ticker_model_questions"' in text
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_jev_schema.py tests/test_config.py tests/test_migrations.py -v`
Expected: FAIL. Collection stops at `tests/test_jev_schema.py` with `ImportError: cannot import name 'JevReading' from 'signalbench.db.models'`. Run alone, `tests/test_config.py` fails with `AttributeError: 'Settings' object has no attribute 'openrouter_api_key'`, and `tests/test_migrations.py` fails on the head (`['0010_backtest_runs']`) and on the missing `0011_jev_readings.py`.

- [ ] **Step 3: Implement**

In `src/signalbench/config.py`, replace:

```python
    finnhub_api_key: str | None = None
```

with:

```python
    finnhub_api_key: str | None = None
    openrouter_api_key: str | None = None
```

Append to `src/signalbench/db/models.py`:

```python
class JevReading(SQLModel, table=True):
    """One Jev reading of one document for one ticker (spec 03). Written by `jev backfill`."""

    __tablename__ = "jev_readings"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "ticker_id",
            "model_requested",
            "question_set",
            name="uq_jev_readings_document_ticker_model_questions",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    document_id: uuid.UUID = Field(
        foreign_key="raw_documents.id", ondelete="CASCADE", index=True
    )
    ticker_id: uuid.UUID = Field(foreign_key="tickers.id", ondelete="RESTRICT")
    model_requested: str
    model_resolved: str
    question_set: str
    response_id: str
    p_negative: float
    p_neutral: float
    p_positive: float
    event_type: str
    p_routine: float
    answers: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    input_tokens: int
    cost_usd: float
    latency_ms: int
    read_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(UTCDateTime(), nullable=False),
    )
```

`alembic/versions/0011_jev_readings.py`:

```python
"""Spec 03 Jev readings

Revision ID: 0011_jev_readings
Revises: 0010_backtest_runs
Create Date: 2026-09-24 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_jev_readings"
down_revision: str | None = "0010_backtest_runs"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jev_readings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("ticker_id", sa.Uuid(), nullable=False),
        sa.Column("model_requested", sa.String(), nullable=False),
        sa.Column("model_resolved", sa.String(), nullable=False),
        sa.Column("question_set", sa.String(), nullable=False),
        sa.Column("response_id", sa.String(), nullable=False),
        sa.Column("p_negative", sa.Float(), nullable=False),
        sa.Column("p_neutral", sa.Float(), nullable=False),
        sa.Column("p_positive", sa.Float(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("p_routine", sa.Float(), nullable=False),
        sa.Column("answers", sa.JSON(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["raw_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ticker_id"], ["tickers.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "ticker_id",
            "model_requested",
            "question_set",
            name="uq_jev_readings_document_ticker_model_questions",
        ),
    )
    op.create_index("ix_jev_readings_document_id", "jev_readings", ["document_id"])


def downgrade() -> None:
    op.drop_index("ix_jev_readings_document_id", table_name="jev_readings")
    op.drop_table("jev_readings")
```

The migration matches the model: while this plan was written it was run with `alembic upgrade head`, `alembic check` ("No new upgrade operations detected"), and a downgrade/upgrade round trip on a throwaway copy of the database. The real database is migrated in Task 16.

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_jev_schema.py tests/test_config.py tests/test_migrations.py -v && uv run mypy src && uv run ruff check .`
Expected: 9 passed; mypy and ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/config.py src/signalbench/db/models.py alembic/versions/0011_jev_readings.py tests/test_jev_schema.py tests/test_config.py tests/test_migrations.py
git commit -m "feat: jev_readings table (migration 0011) and the OpenRouter key setting

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Question set q1 and the request body

**Files:**
- Create: `src/signalbench/jev/__init__.py` (empty), `src/signalbench/jev/questions.py`, `tests/test_jev_questions.py`

- [ ] **Step 1: Write the failing test**

`tests/test_jev_questions.py`:

```python
import json
from datetime import date

from signalbench.jev.questions import (
    EVENT_TYPES,
    IMPACT_OPTIONS,
    JEV_RELEASE,
    MAX_STATE_CHARS,
    MODEL,
    QUESTION_SET,
    QUESTIONS,
    build_state,
    request_body,
)


def test_model_and_question_set_ids() -> None:
    assert MODEL == "typesafe/jev-1.13"
    assert QUESTION_SET == "q1"
    assert MAX_STATE_CHARS == 100_000
    assert JEV_RELEASE == date(2026, 9, 15)


def test_question_set_q1_matches_the_spec() -> None:
    assert list(QUESTIONS) == ["impact", "event_type", "routine"]
    impact, event_type, routine = QUESTIONS["impact"], QUESTIONS["event_type"], QUESTIONS["routine"]
    assert impact["type"] == "choice"
    assert impact["instructions"] == (
        "What does this document mean for the company's common shareholders?"
    )
    assert tuple(impact["criteria"]) == IMPACT_OPTIONS == ("negative", "neutral", "positive")
    assert impact["criteria"]["neutral"].startswith("Routine, procedural, or mixed news")
    assert event_type["type"] == "choice"
    assert tuple(event_type["criteria"]) == EVENT_TYPES
    assert EVENT_TYPES == ("earnings", "guidance", "leadership", "legal", "product", "macro", "other")
    assert routine["type"] == "noul"
    assert set(routine["criteria"]) == {"true", "false"}
    assert routine["instructions"] == (
        "Is this a routine administrative document with no new business information?"
    )


def test_filing_state_names_the_company_and_the_items() -> None:
    state = build_state("Apple", "AAPL", "filings", "2.02,9.01", "  Apple reported results.\n")
    assert state == "Company: Apple (AAPL)\nSource: SEC 8-K, items 2.02, 9.01\n\nApple reported results."


def test_filing_without_items_and_news_state() -> None:
    assert build_state("Apple", "AAPL", "filings", None, "Text").startswith(
        "Company: Apple (AAPL)\nSource: SEC 8-K\n\n"
    )
    assert build_state("Apple", "AAPL", "news", None, "Headline\n\nSummary") == (
        "Company: Apple (AAPL)\nSource: News\n\nHeadline\n\nSummary"
    )


def test_request_body_has_exactly_the_documented_fields() -> None:
    body = request_body("Company: Apple (AAPL)\nSource: News\n\nHeadline")
    assert set(body) == {"model", "state", "questions"}
    assert body["model"] == MODEL
    assert body["state"].startswith("Company: Apple")
    assert body["questions"] == QUESTIONS
    for question in body["questions"].values():
        assert set(question) == {"type", "instructions", "criteria"}
        assert question["type"] in {"choice", "noul", "score"}
    assert json.loads(json.dumps(body)) == body
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_jev_questions.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'signalbench.jev'`.

- [ ] **Step 3: Implement**

`src/signalbench/jev/__init__.py`: an empty file.

`src/signalbench/jev/questions.py` (the wording is the spec's `q1`, character for character):

```python
"""Question set q1 and the request body (spec 03).

Changing any wording creates q2, and documents must be re-read under q2 before any q2 reading
is used. Only public text is sent: SEC filings and news headlines and summaries.
"""

from datetime import date
from typing import Any, Literal

MODEL = "typesafe/jev-1.13"
JEV_RELEASE = date(2026, 9, 15)  # trades and documents from here on are out of sample
QUESTION_SET = "q1"
MAX_STATE_CHARS = 100_000  # Jev takes 32K tokens of state; spec 01 caps 8-K text at 96K chars
IMPACT_OPTIONS = ("negative", "neutral", "positive")
EVENT_TYPES = ("earnings", "guidance", "leadership", "legal", "product", "macro", "other")
Source = Literal["filings", "news"]

QUESTIONS: dict[str, dict[str, Any]] = {
    "impact": {
        "type": "choice",
        "instructions": "What does this document mean for the company's common shareholders?",
        "criteria": {
            "negative": (
                "Clearly bad news: weak results or lowered guidance, lawsuits or investigations "
                "with real exposure, forced executive departures, impairments, restatements, "
                "liquidity problems, loss of a major customer or product."
            ),
            "neutral": (
                "Routine, procedural, or mixed news with no clear effect on the business: "
                "meeting and voting results, routine appointments, ordinary debt issuance, "
                "exhibit-only filings, commentary without new facts."
            ),
            "positive": (
                "Clearly good news: strong results or raised guidance, major contracts or "
                "approvals, new or larger buybacks or dividends, favourable legal outcomes, "
                "value-adding deals."
            ),
        },
    },
    "event_type": {
        "type": "choice",
        "instructions": "What kind of event is this document mainly about?",
        "criteria": {
            "earnings": "Reported financial results.",
            "guidance": "Forecasts or outlook changes.",
            "leadership": "Executive or board changes.",
            "legal": "Lawsuits, investigations, regulatory actions, settlements.",
            "product": "Products, contracts, customers, partnerships, approvals.",
            "macro": "Economy-wide or industry-wide conditions.",
            "other": "Anything else, including administrative filings.",
        },
    },
    "routine": {
        "type": "noul",
        "instructions": (
            "Is this a routine administrative document with no new business information?"
        ),
        "criteria": {
            "true": (
                "Procedural or boilerplate: meeting results, standard exhibits, scheduled notices."
            ),
            "false": "Contains new information about the business, its results, or its prospects.",
        },
    },
}


def build_state(
    company_name: str, symbol: str, source: Source, items: str | None, text: str
) -> str:
    """`Company: {name} ({symbol})`, `Source: SEC 8-K, items {items}` or `Source: News`, then
    the cleaned text. No dates are added (dates are a documented Jev weak spot)."""
    if source == "filings":
        listed = ", ".join(part.strip() for part in (items or "").split(",") if part.strip())
        label = f"SEC 8-K, items {listed}" if listed else "SEC 8-K"
    else:
        label = "News"
    return f"Company: {company_name} ({symbol})\nSource: {label}\n\n{text.strip()}"


def request_body(state: str, model: str = MODEL) -> dict[str, Any]:
    """The documented body: model, state, questions. No session_id or user is sent."""
    return {"model": model, "state": state, "questions": QUESTIONS}
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_jev_questions.py -v && uv run mypy src && uv run ruff check .`
Expected: 5 passed; mypy and ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/jev/__init__.py src/signalbench/jev/questions.py tests/test_jev_questions.py
git commit -m "feat: Jev question set q1, document state, and request body

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Response parsing and validation

**Files:**
- Create: `src/signalbench/jev/client.py`, `tests/test_jev_parse.py`

- [ ] **Step 1: Write the failing test**

`tests/test_jev_parse.py`:

```python
import copy
from typing import Any

import pytest
from pytest import approx

from signalbench.jev.client import (
    PRICE_PER_INPUT_TOKEN_USD,
    JevResponseError,
    parse_response,
)


def _body() -> dict[str, Any]:
    """A response in the documented shape for question set q1."""
    return {
        "answers": {
            "impact": {
                "type": "choice",
                "choice": "positive",
                "confidence": 0.7,
                "probabilities": {"negative": 0.05, "neutral": 0.15, "positive": 0.8},
            },
            "event_type": {
                "type": "choice",
                "choice": "earnings",
                "confidence": 0.9,
                "probabilities": {
                    "earnings": 0.9, "guidance": 0.05, "leadership": 0, "legal": 0,
                    "product": 0.05, "macro": 0, "other": 0,
                },
            },
            "routine": {"type": "noul", "noul": 0.04},
        },
        "id": "gen-dec-1789738314-X5e5eKGQdvR9rblyX250",
        "model": "typesafe/jev-1.13-20260917",
        "provider": "TypeSafe",
        "usage": {"cost": 0.000019992, "input_tokens": 476, "output_tokens": 70},
    }


def test_choice_and_noul_answers_are_parsed() -> None:
    result = parse_response(_body(), latency_ms=812)
    assert (result.p_negative, result.p_neutral, result.p_positive) == (0.05, 0.15, 0.8)
    assert result.event_type == "earnings"
    assert result.p_routine == 0.04
    assert result.model_resolved == "typesafe/jev-1.13-20260917"
    assert result.response_id == "gen-dec-1789738314-X5e5eKGQdvR9rblyX250"
    assert (result.input_tokens, result.cost_usd, result.latency_ms) == (476, 0.000019992, 812)
    assert result.answers == _body()["answers"]  # stored in full


def test_integer_probabilities_are_accepted() -> None:
    body = _body()
    body["answers"]["impact"]["probabilities"] = {"negative": 0, "neutral": 0, "positive": 1}
    assert parse_response(body, latency_ms=1).p_positive == 1.0


@pytest.mark.parametrize("total", [0.98, 1.02])
def test_probabilities_that_do_not_sum_to_one_are_rejected(total: float) -> None:
    body = _body()
    body["answers"]["impact"]["probabilities"] = {
        "negative": 0.1, "neutral": 0.1, "positive": total - 0.2,
    }
    with pytest.raises(JevResponseError, match="impact probabilities sum to"):
        parse_response(body, latency_ms=1)


def test_a_sum_inside_the_tolerance_is_accepted() -> None:
    body = _body()
    body["answers"]["impact"]["probabilities"] = {"negative": 0.1, "neutral": 0.1, "positive": 0.805}
    assert parse_response(body, latency_ms=1).p_positive == approx(0.805)


def test_event_type_probabilities_are_checked_too() -> None:
    body = _body()
    body["answers"]["event_type"]["probabilities"]["other"] = 0.5
    with pytest.raises(JevResponseError, match="event_type probabilities sum to"):
        parse_response(body, latency_ms=1)


@pytest.mark.parametrize("missing", ["impact", "event_type", "routine"])
def test_every_answer_must_be_present(missing: str) -> None:
    body = _body()
    del body["answers"][missing]
    with pytest.raises(JevResponseError, match=f"missing answer {missing!r}"):
        parse_response(body, latency_ms=1)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("routine", "noul"), 1.2, "routine: noul must be a number in"),
        (("routine", "noul"), True, "routine: noul must be a number in"),
        (("routine", "type"), "choice", "routine: expected type 'noul'"),
        (("impact", "type"), "noul", "impact: expected type 'choice'"),
        (("event_type", "choice"), "weather", "event_type: choice 'weather' is not"),
        (("impact", "probabilities"), {"bad": 0.5, "good": 0.5}, "impact: probabilities must cover"),
    ],
)
def test_malformed_answers_are_rejected(path: tuple[str, str], value: object, message: str) -> None:
    body = _body()
    body["answers"][path[0]][path[1]] = value
    with pytest.raises(JevResponseError, match=message):
        parse_response(body, latency_ms=1)


def test_missing_cost_falls_back_to_the_token_price() -> None:
    body = _body()
    del body["usage"]["cost"]
    result = parse_response(body, latency_ms=1)
    assert result.cost_usd == approx(476 * PRICE_PER_INPUT_TOKEN_USD)
    assert PRICE_PER_INPUT_TOKEN_USD == approx(0.042 / 1_000_000)


@pytest.mark.parametrize("key", ["model", "usage", "answers", "id"])
def test_missing_top_level_fields_are_rejected(key: str) -> None:
    body = copy.deepcopy(_body())
    del body[key]
    with pytest.raises(JevResponseError):
        parse_response(body, latency_ms=1)


def test_a_non_object_body_is_rejected() -> None:
    with pytest.raises(JevResponseError, match="expected a JSON object"):
        parse_response(["not", "an", "object"], latency_ms=1)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_jev_parse.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'signalbench.jev.client'`.

- [ ] **Step 3: Implement the parser**

`src/signalbench/jev/client.py` (Task 5 adds the HTTP client to this file):

```python
"""Jev through OpenRouter's decisions endpoint (spec 03): response parsing and the HTTP client."""

import math
from dataclasses import dataclass
from typing import Any, cast

from signalbench.jev.questions import EVENT_TYPES, IMPACT_OPTIONS

PRICE_PER_INPUT_TOKEN_USD = 0.042 / 1_000_000  # output tokens are free (checked 2026-09-24)
PROBABILITY_TOLERANCE = 0.01


class JevError(Exception):
    """Base class for every Jev failure."""


class JevResponseError(JevError):
    """A 200 response that does not match question set q1. The document is skipped."""


@dataclass(frozen=True)
class JevResult:
    response_id: str
    model_resolved: str
    p_negative: float
    p_neutral: float
    p_positive: float
    event_type: str
    p_routine: float
    answers: dict[str, Any]
    input_tokens: int
    cost_usd: float
    latency_ms: int


def _mapping(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise JevResponseError(f"{where}: expected a JSON object")
    return cast(dict[str, Any], value)


def _unit(value: object, where: str) -> float:
    """A probability: a finite number in [0, 1] (bools are not numbers here)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise JevResponseError(f"{where} must be a number in [0, 1], got {value!r}")
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise JevResponseError(f"{where} must be a number in [0, 1], got {value!r}")
    return number


def _answer(answers: dict[str, Any], name: str, kind: str) -> dict[str, Any]:
    if name not in answers:
        raise JevResponseError(f"missing answer {name!r}")
    answer = _mapping(answers[name], name)
    if answer.get("type") != kind:
        raise JevResponseError(f"{name}: expected type {kind!r}, got {answer.get('type')!r}")
    return answer


def _choice(
    answers: dict[str, Any], name: str, options: tuple[str, ...]
) -> tuple[str, dict[str, float]]:
    answer = _answer(answers, name, "choice")
    raw = _mapping(answer.get("probabilities"), f"{name}: probabilities")
    if set(raw) != set(options):
        raise JevResponseError(
            f"{name}: probabilities must cover {list(options)}, got {sorted(raw)}"
        )
    probabilities = {option: _unit(raw[option], f"{name}: {option}") for option in options}
    total = math.fsum(probabilities.values())
    if abs(total - 1.0) > PROBABILITY_TOLERANCE:
        raise JevResponseError(
            f"{name} probabilities sum to {total:.4f}, not 1 ± {PROBABILITY_TOLERANCE}"
        )
    choice = answer.get("choice")
    if choice not in options:
        raise JevResponseError(f"{name}: choice {choice!r} is not one of {list(options)}")
    return str(choice), probabilities


def parse_response(body: object, latency_ms: int) -> JevResult:
    """Validate a 200 body against question set q1. `confidence` is never used (spec 03)."""
    top = _mapping(body, "response")
    answers = _mapping(top.get("answers"), "answers")
    _, impact = _choice(answers, "impact", IMPACT_OPTIONS)
    event_type, _ = _choice(answers, "event_type", EVENT_TYPES)
    routine = _unit(_answer(answers, "routine", "noul").get("noul"), "routine: noul")
    model = top.get("model")
    response_id = top.get("id")
    if not isinstance(model, str) or not model or not isinstance(response_id, str):
        raise JevResponseError("response: expected string `model` and `id`")
    usage = _mapping(top.get("usage"), "usage")
    tokens = usage.get("input_tokens")
    if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
        raise JevResponseError(f"usage.input_tokens must be a whole number, got {tokens!r}")
    cost = usage.get("cost")
    if isinstance(cost, bool) or not isinstance(cost, int | float) or cost < 0:
        cost = tokens * PRICE_PER_INPUT_TOKEN_USD  # not reported: price it ourselves
    return JevResult(
        response_id=response_id,
        model_resolved=model,
        p_negative=impact["negative"],
        p_neutral=impact["neutral"],
        p_positive=impact["positive"],
        event_type=event_type,
        p_routine=routine,
        answers=answers,
        input_tokens=tokens,
        cost_usd=float(cost),
        latency_ms=latency_ms,
    )
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_jev_parse.py -v && uv run mypy src && uv run ruff check .`
Expected: 21 passed; mypy and ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/jev/client.py tests/test_jev_parse.py
git commit -m "feat: validate Jev responses against question set q1

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: HTTP client with retries, and the fake client

**Files:**
- Modify: `src/signalbench/jev/client.py`
- Create: `src/signalbench/jev/fake.py`, `tests/test_jev_client.py`

- [ ] **Step 1: Write the failing test**

`tests/test_jev_client.py`:

```python
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from signalbench.jev.client import (
    ENDPOINT,
    JevClient,
    JevFatalError,
    JevRejectedError,
    JevUnavailableError,
    OpenRouterJevClient,
)
from signalbench.jev.fake import FakeJevClient, fake_result
from signalbench.jev.questions import request_body

STATE = "Company: Apple (AAPL)\nSource: News\n\nApple raises its dividend"
OK_BODY: dict[str, Any] = {
    "answers": {
        "impact": {
            "type": "choice", "choice": "positive", "confidence": 0.7,
            "probabilities": {"negative": 0.05, "neutral": 0.15, "positive": 0.8},
        },
        "event_type": {
            "type": "choice", "choice": "product", "confidence": 0.5,
            "probabilities": {
                "earnings": 0.1, "guidance": 0.1, "leadership": 0.0, "legal": 0.0,
                "product": 0.6, "macro": 0.1, "other": 0.1,
            },
        },
        "routine": {"type": "noul", "noul": 0.1},
    },
    "id": "gen-dec-1",
    "model": "typesafe/jev-1.13-20260917",
    "provider": "TypeSafe",
    "usage": {"cost": 0.00002, "input_tokens": 480, "output_tokens": 70},
}

Step = int | Exception  # an HTTP status to answer with, or an exception to raise


def _client(steps: list[Step]) -> tuple[OpenRouterJevClient, list[httpx.Request], list[float]]:
    requests: list[httpx.Request] = []
    sleeps: list[float] = []
    queue = list(steps)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        step = queue.pop(0)
        if isinstance(step, Exception):
            raise step
        if step == 200:
            return httpx.Response(200, json=OK_BODY)
        return httpx.Response(step, json={"error": {"code": step, "message": f"status {step}"}})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    ticks = iter(float(i) * 0.25 for i in range(100))  # each clock read advances 0.25 s
    client = OpenRouterJevClient(
        "test-key", http, sleep=sleeps.append, clock=lambda: next(ticks)
    )
    return client, requests, sleeps


def test_the_request_matches_the_documented_schema() -> None:
    client, requests, _ = _client([200])
    client.read(STATE)
    (request,) = requests
    assert str(request.url) == ENDPOINT == "https://openrouter.ai/api/alpha/decisions"
    assert request.method == "POST"
    assert request.headers["Authorization"] == "Bearer test-key"
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.content) == request_body(STATE)


def test_a_200_is_parsed_with_its_latency() -> None:
    client, _, _ = _client([200])
    result = client.read(STATE)
    assert result.p_positive == 0.8 and result.event_type == "product"
    assert result.model_resolved == "typesafe/jev-1.13-20260917"
    assert result.latency_ms == 250  # one clock tick between send and receive


@pytest.mark.parametrize("status", [429, 500, 502, 503, 524, 529])
def test_retryable_statuses_are_retried_with_exponential_backoff(status: int) -> None:
    client, requests, sleeps = _client([status, status, 200])
    assert client.read(STATE).p_positive == 0.8
    assert len(requests) == 3
    assert sleeps == [1.0, 2.0]


def test_timeouts_and_connection_errors_are_retried() -> None:
    client, requests, sleeps = _client(
        [httpx.ReadTimeout("slow"), httpx.ConnectError("down"), 200]
    )
    assert client.read(STATE).p_positive == 0.8
    assert len(requests) == 3 and sleeps == [1.0, 2.0]


def test_three_retries_then_unavailable() -> None:
    client, requests, sleeps = _client([503, 503, 503, 503])
    with pytest.raises(JevUnavailableError, match="after 4 attempts: HTTP 503"):
        client.read(STATE)
    assert len(requests) == 4
    assert sleeps == [1.0, 2.0, 4.0]


@pytest.mark.parametrize("status", [400, 403, 413])
def test_document_level_rejections_are_not_retried(status: int) -> None:
    client, requests, sleeps = _client([status])
    with pytest.raises(JevRejectedError, match=f"HTTP {status}: status {status}") as raised:
        client.read(STATE)
    assert raised.value.status == status
    assert len(requests) == 1 and sleeps == []


@pytest.mark.parametrize("status", [401, 402, 404])
def test_account_level_failures_are_fatal_and_not_retried(status: int) -> None:
    client, requests, _ = _client([status])
    with pytest.raises(JevFatalError, match=f"HTTP {status}"):
        client.read(STATE)
    assert len(requests) == 1


def test_the_fake_client_satisfies_the_protocol_and_records_states() -> None:
    fake = FakeJevClient()
    client: JevClient = fake
    result = client.read(STATE)
    assert fake.calls == [STATE]
    assert result == fake_result()


def test_the_fake_client_can_answer_per_state_or_raise() -> None:
    def respond(state: str) -> Any:
        if "lawsuit" in state:
            return fake_result(p_negative=0.9, p_neutral=0.05, p_positive=0.05)
        return JevRejectedError(413, "too large")

    answer: Callable[[str], Any] = respond
    fake = FakeJevClient(answer)
    assert fake.read("a lawsuit").p_negative == 0.9
    with pytest.raises(JevRejectedError):
        fake.read("anything else")
    assert fake.calls == ["a lawsuit", "anything else"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_jev_client.py -v`
Expected: FAIL with `ImportError: cannot import name 'ENDPOINT' from 'signalbench.jev.client'`.

- [ ] **Step 3: Implement the client**

`src/signalbench/jev/client.py` (complete file: Task 4's parser unchanged, plus the error classes, the `JevClient` protocol, and `OpenRouterJevClient`):

```python
"""Jev through OpenRouter's decisions endpoint (spec 03): response parsing and the HTTP client."""

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast

import httpx

from signalbench.jev.questions import EVENT_TYPES, IMPACT_OPTIONS, MODEL, request_body

ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
TIMEOUT_SECONDS = 10.0
MAX_RETRIES = 3
BACKOFF_SECONDS = 1.0  # waits 1, 2, 4 s between attempts
FATAL_STATUSES = frozenset({401, 402, 404})  # bad key, no credits, unknown model: stop the run
PRICE_PER_INPUT_TOKEN_USD = 0.042 / 1_000_000  # output tokens are free (checked 2026-09-24)
PROBABILITY_TOLERANCE = 0.01


class JevError(Exception):
    """Base class for every Jev failure."""


class JevResponseError(JevError):
    """A 200 response that does not match question set q1. The document is skipped."""


class JevRejectedError(JevError):
    """A non-retryable status about this request (400, 403, 413, ...). The document is skipped."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class JevFatalError(JevError):
    """401 (key), 402 (credits), or 404 (model): every later request would fail too."""


class JevUnavailableError(JevError):
    """429, 5xx, or a transport error on every attempt. The document is skipped."""


@dataclass(frozen=True)
class JevResult:
    response_id: str
    model_resolved: str
    p_negative: float
    p_neutral: float
    p_positive: float
    event_type: str
    p_routine: float
    answers: dict[str, Any]
    input_tokens: int
    cost_usd: float
    latency_ms: int


def _mapping(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise JevResponseError(f"{where}: expected a JSON object")
    return cast(dict[str, Any], value)


def _unit(value: object, where: str) -> float:
    """A probability: a finite number in [0, 1] (bools are not numbers here)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise JevResponseError(f"{where} must be a number in [0, 1], got {value!r}")
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise JevResponseError(f"{where} must be a number in [0, 1], got {value!r}")
    return number


def _answer(answers: dict[str, Any], name: str, kind: str) -> dict[str, Any]:
    if name not in answers:
        raise JevResponseError(f"missing answer {name!r}")
    answer = _mapping(answers[name], name)
    if answer.get("type") != kind:
        raise JevResponseError(f"{name}: expected type {kind!r}, got {answer.get('type')!r}")
    return answer


def _choice(
    answers: dict[str, Any], name: str, options: tuple[str, ...]
) -> tuple[str, dict[str, float]]:
    answer = _answer(answers, name, "choice")
    raw = _mapping(answer.get("probabilities"), f"{name}: probabilities")
    if set(raw) != set(options):
        raise JevResponseError(
            f"{name}: probabilities must cover {list(options)}, got {sorted(raw)}"
        )
    probabilities = {option: _unit(raw[option], f"{name}: {option}") for option in options}
    total = math.fsum(probabilities.values())
    if abs(total - 1.0) > PROBABILITY_TOLERANCE:
        raise JevResponseError(
            f"{name} probabilities sum to {total:.4f}, not 1 ± {PROBABILITY_TOLERANCE}"
        )
    choice = answer.get("choice")
    if choice not in options:
        raise JevResponseError(f"{name}: choice {choice!r} is not one of {list(options)}")
    return str(choice), probabilities


def parse_response(body: object, latency_ms: int) -> JevResult:
    """Validate a 200 body against question set q1. `confidence` is never used (spec 03)."""
    top = _mapping(body, "response")
    answers = _mapping(top.get("answers"), "answers")
    _, impact = _choice(answers, "impact", IMPACT_OPTIONS)
    event_type, _ = _choice(answers, "event_type", EVENT_TYPES)
    routine = _unit(_answer(answers, "routine", "noul").get("noul"), "routine: noul")
    model = top.get("model")
    response_id = top.get("id")
    if not isinstance(model, str) or not model or not isinstance(response_id, str):
        raise JevResponseError("response: expected string `model` and `id`")
    usage = _mapping(top.get("usage"), "usage")
    tokens = usage.get("input_tokens")
    if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
        raise JevResponseError(f"usage.input_tokens must be a whole number, got {tokens!r}")
    cost = usage.get("cost")
    if isinstance(cost, bool) or not isinstance(cost, int | float) or cost < 0:
        cost = tokens * PRICE_PER_INPUT_TOKEN_USD  # not reported: price it ourselves
    return JevResult(
        response_id=response_id,
        model_resolved=model,
        p_negative=impact["negative"],
        p_neutral=impact["neutral"],
        p_positive=impact["positive"],
        event_type=event_type,
        p_routine=routine,
        answers=answers,
        input_tokens=tokens,
        cost_usd=float(cost),
        latency_ms=latency_ms,
    )


class JevClient(Protocol):
    def read(self, state: str) -> JevResult:
        """Ask question set q1 about one document state."""
        ...


def _error_message(response: httpx.Response) -> str:
    """OpenRouter errors look like {"error": {"code": 402, "message": "..."}}."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:200] or response.reason_phrase
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return str(body["error"].get("message", body["error"]))
    return str(body)[:200]


class OpenRouterJevClient:
    """Sync client. Retries 429, 5xx, and transport errors with exponential backoff; never retries
    other 4xx. Safe to share across the backfill's worker threads (httpx.Client is thread-safe)."""

    def __init__(
        self,
        api_key: str,
        http: httpx.Client,
        *,
        model: str = MODEL,
        max_retries: int = MAX_RETRIES,
        backoff_seconds: float = BACKOFF_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self._http = http
        self._model = model
        self._max_retries = max_retries
        self._backoff = backoff_seconds
        self._sleep = sleep
        self._clock = clock

    def read(self, state: str) -> JevResult:
        body = request_body(state, self._model)
        failure = ""
        for attempt in range(self._max_retries + 1):
            if attempt:
                self._sleep(self._backoff * 2 ** (attempt - 1))
            started = self._clock()
            try:
                response = self._http.post(
                    ENDPOINT, json=body, headers=self._headers, timeout=TIMEOUT_SECONDS
                )
            except httpx.TransportError as error:  # timeouts and connection failures
                failure = f"{type(error).__name__}: {error}"
                continue
            latency_ms = round((self._clock() - started) * 1000)
            status = response.status_code
            if status == 200:
                try:
                    payload = response.json()
                except ValueError as error:
                    raise JevResponseError(f"200 with a body that is not JSON: {error}") from None
                return parse_response(payload, latency_ms)
            if status in FATAL_STATUSES:
                raise JevFatalError(f"HTTP {status}: {_error_message(response)}")
            if status != 429 and status < 500:
                raise JevRejectedError(status, _error_message(response))
            failure = f"HTTP {status}"
        raise JevUnavailableError(f"gave up after {self._max_retries + 1} attempts: {failure}")
```

`src/signalbench/jev/fake.py`:

```python
"""A JevClient test double (spec 03 testing; spec 05 end-to-end tests reuse it)."""

import threading
from collections.abc import Callable

from signalbench.jev.client import JevResult


def fake_result(
    *,
    p_negative: float = 0.1,
    p_neutral: float = 0.2,
    p_positive: float = 0.7,
    event_type: str = "earnings",
    p_routine: float = 0.1,
    cost_usd: float = 0.00002,
    model_resolved: str = "typesafe/jev-1.13-20260917",
) -> JevResult:
    """A valid q1 result; override the fields a test cares about."""
    return JevResult(
        response_id="gen-dec-fake",
        model_resolved=model_resolved,
        p_negative=p_negative,
        p_neutral=p_neutral,
        p_positive=p_positive,
        event_type=event_type,
        p_routine=p_routine,
        answers={
            "impact": {
                "type": "choice",
                "choice": max(
                    ("negative", p_negative), ("neutral", p_neutral), ("positive", p_positive),
                    key=lambda item: item[1],
                )[0],
                "probabilities": {
                    "negative": p_negative, "neutral": p_neutral, "positive": p_positive,
                },
            },
            "event_type": {"type": "choice", "choice": event_type},
            "routine": {"type": "noul", "noul": p_routine},
        },
        input_tokens=480,
        cost_usd=cost_usd,
        latency_ms=5,
    )


Respond = Callable[[str], JevResult | Exception]


class FakeJevClient:
    """Answers every state with `respond(state)` (default: `fake_result()`), raising it when it
    is an exception. Records each state in `calls`; safe to call from worker threads."""

    def __init__(self, respond: Respond | None = None) -> None:
        self._respond: Respond = respond or (lambda _state: fake_result())
        self._lock = threading.Lock()
        self.calls: list[str] = []
        self.threads: list[int] = []

    def read(self, state: str) -> JevResult:
        with self._lock:
            self.calls.append(state)
            self.threads.append(threading.get_ident())
        answer = self._respond(state)
        if isinstance(answer, Exception):
            raise answer
        return answer
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_jev_client.py tests/test_jev_parse.py -v && uv run mypy src && uv run ruff check .`
Expected: 39 passed (18 new); mypy and ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/jev/client.py src/signalbench/jev/fake.py tests/test_jev_client.py
git commit -m "feat: OpenRouter Jev client with retries, and a fake client for tests

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Backfill — pending pairs, news cap, budget guard

**Files:**
- Create: `src/signalbench/jev/backfill.py`, `tests/test_jev_backfill.py`

- [ ] **Step 1: Write the failing test**

`tests/test_jev_backfill.py`:

```python
import threading
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlmodel import Session, select

from signalbench.db.models import (
    DocType,
    DocumentTicker,
    JevReading,
    RawDocument,
    Ticker,
    TickerKind,
)
from signalbench.jev import backfill
from signalbench.jev.backfill import job_state, pending_work, run_backfill
from signalbench.jev.client import JevFatalError, JevRejectedError
from signalbench.jev.fake import FakeJevClient, fake_result

UNIVERSE = ["AAA", "BBB"]
NEWS_DAY = datetime(2026, 9, 21, 13, 0, tzinfo=UTC)  # 09:00 in New York


def _quiet(_line: str) -> None:
    pass


def _ticker(session: Session, symbol: str) -> Ticker:
    ticker = Ticker(symbol=symbol, company_name=f"{symbol.title()} Corp", kind=TickerKind.us_stock)
    session.add(ticker)
    session.commit()
    return ticker


def _document(
    session: Session,
    tickers: list[Ticker],
    external_id: str,
    doc_type: DocType,
    published_at: datetime,
    text: str | None,
    *,
    acceptance_at: datetime | None = None,
    items: str | None = None,
) -> RawDocument:
    document = RawDocument(
        source="sec_edgar" if doc_type == DocType.eight_k else "finnhub",
        external_id=external_id,
        doc_type=doc_type,
        raw_text=text or "<html></html>",
        text=text,
        published_at=published_at,
        acceptance_at=acceptance_at,
        items=items,
    )
    session.add(document)
    session.flush()
    for ticker in tickers:
        session.add(DocumentTicker(document_id=document.id, ticker_id=ticker.id))
    session.commit()
    return document


@pytest.fixture
def seeded(session: Session) -> Session:
    aaa, bbb, zzz = (_ticker(session, symbol) for symbol in ("AAA", "BBB", "ZZZ"))
    _document(
        session, [aaa], "8k-results", DocType.eight_k, datetime(2024, 1, 2, tzinfo=UTC),
        "Aaa Corp reported record revenue.",
        acceptance_at=datetime(2024, 1, 2, 21, 5, tzinfo=UTC), items="2.02,9.01",
    )
    _document(session, [aaa], "8k-no-text", DocType.eight_k, datetime(2024, 2, 1, tzinfo=UTC), None)
    _document(
        session, [bbb], "8k-huge", DocType.eight_k, datetime(2024, 3, 1, tzinfo=UTC), "x" * 100_000
    )
    for minute in range(22):  # 22 AAA items on one New York day, plus news-both below
        _document(
            session, [aaa], f"news-{minute:02d}", DocType.news,
            NEWS_DAY + timedelta(minutes=minute), f"Headline {minute:02d}",
        )
    _document(
        session, [aaa, bbb], "news-both", DocType.news, NEWS_DAY + timedelta(hours=1), "Both names"
    )
    _document(session, [zzz], "news-other", DocType.news, NEWS_DAY, "Not in the universe")
    return session


def test_pending_work_counts_and_the_news_cap(seeded: Session) -> None:
    work = pending_work(seeded, UNIVERSE, "all", None)
    assert (work.no_text, work.too_long, work.already_read) == (1, 1, 0)
    assert (work.capped, work.capped_days) == (3, 1)  # AAA's 23 items that day keep the newest 20
    sources = [(job.symbol, job.source) for job in work.jobs]
    assert sources.count(("AAA", "filings")) == 1
    assert sources.count(("AAA", "news")) == 20
    assert sources.count(("BBB", "news")) == 1  # the shared item is read once per ticker
    assert "ZZZ" not in {job.symbol for job in work.jobs}
    kept = sorted(
        job.timestamp for job in work.jobs if job.symbol == "AAA" and job.source == "news"
    )
    assert kept[0] == NEWS_DAY + timedelta(minutes=3)  # minutes 0, 1, 2 were capped
    assert kept[-1] == NEWS_DAY + timedelta(hours=1)
    filing = work.jobs[0]  # jobs run oldest first
    assert filing.timestamp == datetime(2024, 1, 2, 21, 5, tzinfo=UTC)  # the acceptance time
    assert (filing.company_name, filing.items) == ("Aaa Corp", "2.02,9.01")


def test_pending_work_by_source_and_since(seeded: Session) -> None:
    filings = pending_work(seeded, UNIVERSE, "filings", None)
    assert [job.source for job in filings.jobs] == ["filings"]
    news = pending_work(seeded, UNIVERSE, "news", date(2026, 9, 21))
    assert {job.source for job in news.jobs} == {"news"} and len(news.jobs) == 21
    assert pending_work(seeded, UNIVERSE, "news", date(2026, 9, 22)).jobs == []


def test_backfill_stores_readings_and_a_second_run_makes_no_calls(seeded: Session) -> None:
    client = FakeJevClient()
    work = pending_work(seeded, UNIVERSE, "filings", None)
    summary = run_backfill(seeded, work.jobs, client, max_cost_usd=10.0, echo=_quiet)
    assert (summary.read, summary.stopped, summary.skipped) == (1, None, [])
    assert client.calls == [
        (
            "Company: Aaa Corp (AAA)\nSource: SEC 8-K, items 2.02, 9.01\n\n"
            "Aaa Corp reported record revenue."
        )
    ]
    stored = seeded.exec(select(JevReading)).one()
    assert (stored.model_requested, stored.question_set) == ("typesafe/jev-1.13", "q1")
    assert stored.model_resolved == "typesafe/jev-1.13-20260917"
    assert (stored.p_positive, stored.p_routine, stored.cost_usd) == (0.7, 0.1, 0.00002)
    assert summary.builds == {"typesafe/jev-1.13-20260917": 1}
    again = pending_work(seeded, UNIVERSE, "filings", None)
    assert (again.jobs, again.already_read) == ([], 1)
    second = FakeJevClient()
    assert run_backfill(seeded, again.jobs, second, max_cost_usd=10.0, echo=_quiet).read == 0
    assert second.calls == []


def test_the_budget_guard_stops_cleanly_and_the_next_run_resumes(seeded: Session) -> None:
    jobs = pending_work(seeded, UNIVERSE, "news", None).jobs[:5]
    client = FakeJevClient(lambda _state: fake_result(cost_usd=0.4))
    summary = run_backfill(seeded, jobs, client, max_cost_usd=1.0, concurrency=1, echo=_quiet)
    assert summary.read == 3  # 0.4, 0.8, then 1.2 reaches the limit
    assert summary.cost_usd == pytest.approx(1.2)
    assert summary.not_started == 2
    assert summary.budget_reached
    assert summary.stopped == "budget reached: $1.2000 of $1.00"
    assert len(pending_work(seeded, UNIVERSE, "news", None).jobs) == 21 - 3


def test_a_fatal_error_stops_the_run(seeded: Session) -> None:
    jobs = pending_work(seeded, UNIVERSE, "news", None).jobs[:4]
    answers: list[Any] = [fake_result(), JevFatalError("HTTP 402: insufficient credits")]
    client = FakeJevClient(lambda _state: answers.pop(0) if answers else fake_result())
    summary = run_backfill(seeded, jobs, client, max_cost_usd=10.0, concurrency=1, echo=_quiet)
    assert summary.read == 1
    assert summary.stopped == "HTTP 402: insufficient credits"
    assert not summary.budget_reached
    assert summary.not_started == 2
    assert len(client.calls) == 2


def test_a_rejected_document_is_skipped_with_its_reason(seeded: Session) -> None:
    jobs = pending_work(seeded, UNIVERSE, "news", None).jobs[:3]
    too_large = job_state(seeded, jobs[1])
    client = FakeJevClient(
        lambda state: JevRejectedError(413, "too large") if state == too_large else fake_result()
    )
    lines: list[str] = []
    summary = run_backfill(
        seeded, jobs, client, max_cost_usd=10.0, concurrency=1, echo=lines.append
    )
    assert summary.read == 2 and summary.stopped is None
    ((skipped_job, reason),) = summary.skipped
    assert skipped_job == jobs[1] and reason == "HTTP 413: too large"
    assert any(line.startswith("skip AAA ") and "HTTP 413" in line for line in lines)


def test_repeated_failures_stop_the_run(seeded: Session) -> None:
    jobs = pending_work(seeded, UNIVERSE, "news", None).jobs
    client = FakeJevClient(lambda _state: JevRejectedError(400, "bad request"))
    summary = run_backfill(seeded, jobs, client, max_cost_usd=10.0, concurrency=1, echo=_quiet)
    assert summary.read == 0 and len(summary.skipped) == 20
    assert summary.stopped is not None and "20 documents in a row failed" in summary.stopped
    assert summary.not_started == 1


def test_workers_call_jev_but_only_the_main_thread_writes(
    seeded: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    writers: list[int] = []
    save = backfill.save_reading

    def spy(*args: Any, **kwargs: Any) -> None:
        writers.append(threading.get_ident())
        save(*args, **kwargs)

    monkeypatch.setattr(backfill, "save_reading", spy)
    jobs = pending_work(seeded, UNIVERSE, "news", None).jobs
    client = FakeJevClient()
    summary = run_backfill(seeded, jobs, client, max_cost_usd=10.0, concurrency=4, echo=_quiet)
    assert summary.read == 21
    assert len(seeded.exec(select(JevReading)).all()) == 21
    assert set(writers) == {threading.get_ident()}
    assert threading.get_ident() not in set(client.threads)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_jev_backfill.py -v`
Expected: FAIL with `ImportError: cannot import name 'backfill' from 'signalbench.jev'`.

- [ ] **Step 3: Implement**

`src/signalbench/jev/backfill.py`:

```python
"""Read every stored 8-K and news item once per ticker (spec 03, backfill).

Idempotent and resumable: a (document, ticker) pair that already has a reading for this model
and question set is never sent again, and each reading is committed as soon as it arrives.
Jev calls run on a small thread pool; every database read and write stays on the calling thread.
"""

import uuid
from collections import Counter, defaultdict
from collections.abc import Callable, Collection, Iterator, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlmodel import Session, col, select

from signalbench.db.models import (
    DocType,
    DocumentTicker,
    JevReading,
    RawDocument,
    Ticker,
)
from signalbench.jev.client import JevClient, JevError, JevFatalError, JevResult
from signalbench.jev.questions import (
    MAX_STATE_CHARS,
    MODEL,
    QUESTION_SET,
    Source,
    build_state,
)

NEW_YORK = ZoneInfo("America/New_York")
NEWS_DAILY_CAP = 20  # per symbol per New York calendar day, most recent first
MAX_CONSECUTIVE_FAILURES = 20
PROGRESS_EVERY = 100
BackfillSource = Literal["filings", "news", "all"]
DOC_TYPES: dict[Source, DocType] = {"filings": DocType.eight_k, "news": DocType.news}


@dataclass(frozen=True)
class ReadJob:
    """One (document, ticker) pair to read."""

    document_id: uuid.UUID
    ticker_id: uuid.UUID
    symbol: str
    company_name: str
    source: Source
    items: str | None
    timestamp: datetime  # 8-K: acceptance time (else published); news: published


@dataclass(frozen=True)
class PendingWork:
    jobs: list[ReadJob]
    already_read: int
    no_text: int
    too_long: int
    capped: int  # news items over the daily cap (read or not)
    capped_days: int  # symbol-days that hit the cap


@dataclass
class BackfillSummary:
    read: int = 0
    cost_usd: float = 0.0
    input_tokens: int = 0
    skipped: list[tuple[ReadJob, str]] = field(default_factory=list)
    builds: Counter[str] = field(default_factory=Counter)
    stopped: str | None = None
    budget_reached: bool = False  # a clean stop: run again to continue
    not_started: int = 0


@dataclass(frozen=True)
class _Row:
    job: ReadJob
    text_chars: int
    read: bool


def _sources(source: BackfillSource) -> list[Source]:
    return ["filings", "news"] if source == "all" else [source]


def _rows(
    session: Session,
    symbols: Collection[str],
    source: Source,
    since: date | None,
    model: str,
    question_set: str,
) -> list[_Row]:
    tickers = {
        ticker.id: ticker
        for ticker in session.exec(select(Ticker).where(col(Ticker.symbol).in_(list(symbols))))
    }
    documents = select(
        RawDocument.id,
        RawDocument.items,
        func.coalesce(RawDocument.acceptance_at, RawDocument.published_at),  # 8-K: acceptance
        func.coalesce(func.length(RawDocument.text), 0),
    ).where(RawDocument.doc_type == DOC_TYPES[source])
    if since is not None:
        start = datetime.combine(since, time(0, 0), tzinfo=NEW_YORK)
        documents = documents.where(col(RawDocument.published_at) >= start)
    found = {
        doc_id: (items, stamp, int(chars))
        for doc_id, items, stamp, chars in session.exec(documents)
    }
    links = session.exec(
        select(DocumentTicker.document_id, DocumentTicker.ticker_id).where(
            col(DocumentTicker.ticker_id).in_(list(tickers))
        )
    ).all()
    read = set(
        session.exec(
            select(JevReading.document_id, JevReading.ticker_id).where(
                JevReading.model_requested == model, JevReading.question_set == question_set
            )
        ).all()
    )
    rows: list[_Row] = []
    for doc_id, ticker_id in links:
        if doc_id not in found:
            continue
        items, stamp, chars = found[doc_id]
        ticker = tickers[ticker_id]
        job = ReadJob(doc_id, ticker_id, ticker.symbol, ticker.company_name, source, items, stamp)
        rows.append(_Row(job, chars, (doc_id, ticker_id) in read))
    return rows


def _cap_news(rows: list[_Row]) -> tuple[list[_Row], int, int]:
    """Keep the newest NEWS_DAILY_CAP items per symbol per New York day."""
    days: dict[tuple[str, date], list[_Row]] = defaultdict(list)
    for row in rows:
        days[(row.job.symbol, row.job.timestamp.astimezone(NEW_YORK).date())].append(row)
    kept: list[_Row] = []
    capped = capped_days = 0
    for day_rows in days.values():
        day_rows.sort(key=lambda row: (row.job.timestamp, str(row.job.document_id)), reverse=True)
        kept.extend(day_rows[:NEWS_DAILY_CAP])
        if len(day_rows) > NEWS_DAILY_CAP:
            capped += len(day_rows) - NEWS_DAILY_CAP
            capped_days += 1
    return kept, capped, capped_days


def pending_work(
    session: Session,
    symbols: Collection[str],
    source: BackfillSource,
    since: date | None,
    *,
    model: str = MODEL,
    question_set: str = QUESTION_SET,
) -> PendingWork:
    """Every unread (document, ticker) pair for the universe `symbols`, oldest first."""
    jobs: list[ReadJob] = []
    already_read = no_text = too_long = capped = capped_days = 0
    for kind in _sources(source):
        rows = _rows(session, symbols, kind, since, model, question_set)
        if kind == "news":
            rows, capped, capped_days = _cap_news(rows)
        for row in rows:
            job = row.job
            header = build_state(job.company_name, job.symbol, job.source, job.items, "")
            if row.read:
                already_read += 1
            elif row.text_chars == 0:
                no_text += 1
            elif len(header) + row.text_chars > MAX_STATE_CHARS:
                too_long += 1
            else:
                jobs.append(job)
    jobs.sort(key=lambda job: (job.timestamp, str(job.document_id), job.symbol))
    return PendingWork(jobs, already_read, no_text, too_long, capped, capped_days)


def job_state(session: Session, job: ReadJob) -> str:
    """The state sent to Jev for one job (loads only the document's text column)."""
    text = session.exec(select(RawDocument.text).where(RawDocument.id == job.document_id)).one()
    return build_state(job.company_name, job.symbol, job.source, job.items, text or "")


def save_reading(
    session: Session, job: ReadJob, result: JevResult, model: str, question_set: str
) -> None:
    session.add(
        JevReading(
            document_id=job.document_id,
            ticker_id=job.ticker_id,
            model_requested=model,
            model_resolved=result.model_resolved,
            question_set=question_set,
            response_id=result.response_id,
            p_negative=result.p_negative,
            p_neutral=result.p_neutral,
            p_positive=result.p_positive,
            event_type=result.event_type,
            p_routine=result.p_routine,
            answers=result.answers,
            input_tokens=result.input_tokens,
            cost_usd=result.cost_usd,
            latency_ms=result.latency_ms,
        )
    )
    session.commit()


def run_backfill(
    session: Session,
    jobs: Sequence[ReadJob],
    client: JevClient,
    *,
    max_cost_usd: float,
    concurrency: int = 4,
    echo: Callable[[str], None] = print,
    model: str = MODEL,
    question_set: str = QUESTION_SET,
) -> BackfillSummary:
    """Read `jobs` until done, a fatal error, repeated failures, or the budget is reached.

    The budget guard stops submitting once the summed `usage.cost` reaches `max_cost_usd`;
    calls already in flight (at most `concurrency - 1`) still finish and are stored.
    """
    summary = BackfillSummary()
    queue: Iterator[ReadJob] = iter(jobs)
    remaining = len(jobs)
    in_flight: dict[Future[JevResult], ReadJob] = {}
    failures_in_a_row = 0
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        while True:
            while summary.stopped is None and len(in_flight) < concurrency:
                if summary.cost_usd >= max_cost_usd:
                    if remaining:
                        summary.budget_reached = True
                        summary.stopped = (
                            f"budget reached: ${summary.cost_usd:.4f} of ${max_cost_usd:.2f}"
                        )
                    break
                job = next(queue, None)
                if job is None:
                    break
                remaining -= 1
                in_flight[pool.submit(client.read, job_state(session, job))] = job
            if not in_flight:
                break
            done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
            for future in done:
                job = in_flight.pop(future)
                try:
                    result = future.result()
                except JevFatalError as error:
                    summary.stopped = str(error)
                    continue
                except JevError as error:
                    summary.skipped.append((job, str(error)))
                    echo(f"skip {job.symbol} {job.document_id}: {error}")
                    failures_in_a_row += 1
                    if failures_in_a_row >= MAX_CONSECUTIVE_FAILURES and summary.stopped is None:
                        summary.stopped = (
                            f"{MAX_CONSECUTIVE_FAILURES} documents in a row failed; "
                            f"the last error was: {error}"
                        )
                    continue
                failures_in_a_row = 0
                save_reading(session, job, result, model, question_set)
                summary.read += 1
                summary.cost_usd += result.cost_usd
                summary.input_tokens += result.input_tokens
                summary.builds[result.model_resolved] += 1
                if summary.read % PROGRESS_EVERY == 0:
                    echo(f"read {summary.read}/{len(jobs)} | ${summary.cost_usd:.4f}")
    summary.not_started = remaining
    return summary
```

The timestamp is `coalesce(acceptance_at, published_at)`: news rows have no acceptance time, so they fall back to `published_at`, and SQLAlchemy keeps the `UTCDateTime` type through `coalesce`, so the values come back timezone-aware on SQLite and Postgres alike. There is deliberately no `IN (…)` over document ids: 85,000 ids would pass Postgres's 65,535 bind-parameter limit.

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_jev_backfill.py -v && uv run mypy src && uv run ruff check .`
Expected: 8 passed; mypy and ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/jev/backfill.py tests/test_jev_backfill.py
git commit -m "feat: resumable Jev backfill with the news cap and the budget guard

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Readings view and the readings loader

**Files:**
- Modify: `src/signalbench/strategy/readings.py`
- Create: `src/signalbench/jev/store.py`, `tests/test_jev_readings.py`

- [ ] **Step 1: Write the failing test**

`tests/test_jev_readings.py`:

```python
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from sqlmodel import Session

from signalbench.db.models import (
    DocType,
    DocumentTicker,
    JevReading,
    RawDocument,
    Ticker,
    TickerKind,
)
from signalbench.jev.store import load_document_readings
from signalbench.market.legal_close import LegalCloses
from signalbench.strategy.readings import DocumentReading, JevReadingsView, ReadingsView
from strategy_helpers import WeekdaySessions, weekdays

NEW_YORK = ZoneInfo("America/New_York")
DAYS = weekdays(date(2024, 1, 1), 40)  # Monday 2024-01-01 onward, no holidays
D = DAYS[10]  # Monday 2024-01-15


def _reading(
    legal_close: date,
    *,
    p_negative: float = 0.1,
    p_positive: float = 0.1,
    p_routine: float = 0.1,
) -> DocumentReading:
    return DocumentReading(
        legal_close=legal_close,
        p_negative=p_negative,
        p_neutral=max(0.0, 1.0 - p_negative - p_positive),
        p_positive=p_positive,
        p_routine=p_routine,
        event_type="earnings",
        document_id="doc",
    )


def _view(*readings: DocumentReading, theta: float | None = None) -> JevReadingsView:
    return JevReadingsView({"AAA": list(readings)}, DAYS, block_theta=theta)


def test_positive_needs_p_positive_at_least_070_and_p_routine_below_050() -> None:
    assert _view(_reading(D, p_positive=0.70, p_routine=0.49)).sentiment_trigger("AAA", D)
    assert not _view(_reading(D, p_positive=0.69, p_routine=0.1)).sentiment_trigger("AAA", D)
    assert not _view(_reading(D, p_positive=0.9, p_routine=0.50)).sentiment_trigger("AAA", D)


def test_the_sentiment_trigger_lasts_3_sessions_from_the_legal_close() -> None:
    view = _view(_reading(D, p_positive=0.8))
    assert [view.sentiment_trigger("AAA", day) for day in DAYS[9:14]] == [
        False,  # the session before the legal close: not visible yet
        True,
        True,
        True,
        False,  # the 4th session
    ]


def test_windows_count_sessions_not_calendar_days() -> None:
    friday = DAYS[4]  # 2024-01-05
    view = _view(_reading(friday, p_positive=0.8))
    assert view.sentiment_trigger("AAA", DAYS[6])  # Tuesday is the 3rd session
    assert not view.sentiment_trigger("AAA", DAYS[7])


def test_a_catalyst_lasts_10_sessions() -> None:
    view = _view(_reading(D, p_positive=0.8))
    assert [view.catalyst("AAA", day) for day in DAYS[9:21]] == [False] + [True] * 10 + [False]


def test_blocking_needs_the_filter_on_and_p_negative_at_least_theta() -> None:
    negative = _reading(D, p_negative=0.7)
    assert not _view(negative).blocked("AAA", D)  # filter off (information only)
    on = _view(negative, theta=0.7)
    assert [on.blocked("AAA", day) for day in DAYS[9:21]] == [False] + [True] * 10 + [False]
    assert not _view(_reading(D, p_negative=0.69), theta=0.7).blocked("AAA", D)


def test_max_p_negative_over_the_10_session_window() -> None:
    view = _view(
        _reading(DAYS[0], p_negative=0.95),  # 21 sessions before DAYS[21]: outside
        _reading(DAYS[12], p_negative=0.4),
        _reading(DAYS[20], p_negative=0.6),
        _reading(DAYS[22], p_negative=0.99),  # after the as-of: not visible
    )
    assert view.max_p_negative("AAA", DAYS[21]) == 0.6
    assert view.max_p_negative("AAA", DAYS[11]) is None
    assert view.max_p_negative("BBB", DAYS[21]) is None


def test_a_symbol_without_readings_is_neither_positive_nor_negative() -> None:
    view: ReadingsView = _view(_reading(D, p_positive=0.9, p_negative=0.9), theta=0.5)
    assert not view.blocked("BBB", D)
    assert not view.catalyst("BBB", D)
    assert not view.sentiment_trigger("BBB", D)


def _seed_filing(
    session: Session, accepted: datetime, *, model: str = "typesafe/jev-1.13"
) -> None:
    ticker = Ticker(symbol="AAA", company_name="Aaa Corp", kind=TickerKind.us_stock)
    document = RawDocument(
        source="sec_edgar",
        external_id="0000000000-24-000001",
        doc_type=DocType.eight_k,
        raw_text="<html></html>",
        text="Record results.",
        published_at=datetime(2024, 1, 15, tzinfo=UTC),  # the filing date; acceptance wins
        acceptance_at=accepted,
    )
    session.add_all([ticker, document])
    session.flush()
    session.add(DocumentTicker(document_id=document.id, ticker_id=ticker.id))
    session.add(
        JevReading(
            document_id=document.id, ticker_id=ticker.id, model_requested=model,
            model_resolved=f"{model}-20260917", question_set="q1", response_id="gen-dec-1",
            p_negative=0.05, p_neutral=0.15, p_positive=0.8, event_type="earnings",
            p_routine=0.1, answers={}, input_tokens=500, cost_usd=0.00002, latency_ms=900,
        )
    )
    session.commit()


def _closes() -> LegalCloses:
    return LegalCloses(WeekdaySessions().session_closes(DAYS[0], DAYS[-1]))


def test_a_filing_accepted_at_1605_is_invisible_at_that_close_and_visible_the_next(
    session: Session,
) -> None:
    _seed_filing(session, datetime.combine(D, time(16, 5), tzinfo=NEW_YORK))
    readings = load_document_readings(session, ["AAA"], _closes())
    assert [r.legal_close for r in readings["AAA"]] == [DAYS[11]]
    view = JevReadingsView(readings, DAYS, block_theta=None)
    assert view.sentiment_trigger("AAA", D) is False
    assert view.sentiment_trigger("AAA", DAYS[11]) is True


def test_a_filing_accepted_before_the_close_is_visible_that_day(session: Session) -> None:
    _seed_filing(session, datetime.combine(D, time(15, 59), tzinfo=NEW_YORK))
    readings = load_document_readings(session, ["AAA"], _closes())
    assert [r.legal_close for r in readings["AAA"]] == [D]
    assert readings["AAA"][0].p_positive == 0.8


def test_readings_of_another_model_or_symbol_are_not_loaded(session: Session) -> None:
    _seed_filing(session, datetime.combine(D, time(9, 0), tzinfo=NEW_YORK), model="other/model")
    assert load_document_readings(session, ["AAA"], _closes()) == {"AAA": []}
    assert load_document_readings(session, ["BBB"], _closes()) == {"BBB": []}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_jev_readings.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'signalbench.jev.store'`.

- [ ] **Step 3: Implement the view**

`src/signalbench/strategy/readings.py` (complete file; the protocol and `NullReadingsView` are unchanged):

```python
"""The port through which Jev readings (spec 03) reach decide()."""

from bisect import bisect_left, bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol


class ReadingsView(Protocol):
    def blocked(self, symbol: str, as_of: date) -> bool:
        """A negative document blocks entry (only when the Jev filter is on)."""
        ...

    def catalyst(self, symbol: str, as_of: date) -> bool:
        """A positive document in the last 10 sessions; ranks the candidate first."""
        ...

    def sentiment_trigger(self, symbol: str, as_of: date) -> bool:
        """A positive document in the last 3 sessions; feeds the Sentiment setup."""
        ...


class NullReadingsView:
    """Spec 02: no blocks, no catalysts, no sentiment triggers."""

    def blocked(self, symbol: str, as_of: date) -> bool:
        return False

    def catalyst(self, symbol: str, as_of: date) -> bool:
        return False

    def sentiment_trigger(self, symbol: str, as_of: date) -> bool:
        return False


POSITIVE_MIN = 0.70  # fixed, not fitted: it triggers a setup (spec 03)
ROUTINE_MAX = 0.50
TRIGGER_SESSIONS = 3
CATALYST_SESSIONS = 10
BLOCK_SESSIONS = 10


@dataclass(frozen=True)
class DocumentReading:
    """One Jev reading of one document for one symbol, keyed by the document's legal close."""

    legal_close: date
    p_negative: float
    p_neutral: float
    p_positive: float
    p_routine: float
    event_type: str
    document_id: str


def is_positive(reading: DocumentReading) -> bool:
    return reading.p_positive >= POSITIVE_MIN and reading.p_routine < ROUTINE_MAX


class JevReadingsView:
    """Spec 03 readings for decide(). Pure: built once from precomputed readings and sessions.

    At `as_of` it sees only documents whose legal close is on or before `as_of`. "The last N
    sessions" are the N sessions ending with `as_of`. `sessions` must reach at least
    BLOCK_SESSIONS sessions before the first as-of date. `block_theta` is None when the
    filter is information-only: then nothing is blocked.
    """

    def __init__(
        self,
        readings: Mapping[str, Sequence[DocumentReading]],
        sessions: Sequence[date],
        block_theta: float | None,
    ) -> None:
        self._sessions = list(sessions)
        self._readings = {
            symbol: sorted(rows, key=lambda r: (r.legal_close, r.document_id))
            for symbol, rows in readings.items()
        }
        self._closes = {
            symbol: [r.legal_close for r in rows] for symbol, rows in self._readings.items()
        }
        self._block_theta = block_theta

    def window(self, symbol: str, as_of: date, sessions: int) -> list[DocumentReading]:
        """Readings whose legal close falls in the `sessions` sessions ending at `as_of`."""
        last = bisect_right(self._sessions, as_of) - 1
        if last < 0 or symbol not in self._readings:
            return []
        first = self._sessions[max(0, last - sessions + 1)]
        closes = self._closes[symbol]
        return self._readings[symbol][bisect_left(closes, first) : bisect_right(closes, as_of)]

    def blocked(self, symbol: str, as_of: date) -> bool:
        theta = self._block_theta
        if theta is None:
            return False
        return any(r.p_negative >= theta for r in self.window(symbol, as_of, BLOCK_SESSIONS))

    def catalyst(self, symbol: str, as_of: date) -> bool:
        return any(is_positive(r) for r in self.window(symbol, as_of, CATALYST_SESSIONS))

    def sentiment_trigger(self, symbol: str, as_of: date) -> bool:
        return any(is_positive(r) for r in self.window(symbol, as_of, TRIGGER_SESSIONS))

    def max_p_negative(self, symbol: str, as_of: date) -> float | None:
        """The filter decision's input: max p_negative over the block window, None if no reading."""
        values = [r.p_negative for r in self.window(symbol, as_of, BLOCK_SESSIONS)]
        return max(values) if values else None
```

- [ ] **Step 4: Implement the loader**

`src/signalbench/jev/store.py` (Task 10 adds the calibration loader):

```python
"""Database reads for spec 03: readings keyed by legal close, and the v1 trade logs."""

from collections.abc import Collection

from sqlalchemy import func
from sqlmodel import Session, col, select

from signalbench.db.models import JevReading, RawDocument, Ticker
from signalbench.jev.questions import MODEL, QUESTION_SET
from signalbench.market.legal_close import LegalCloses
from signalbench.strategy.readings import DocumentReading


def load_document_readings(
    session: Session,
    symbols: Collection[str],
    legal_closes: LegalCloses,
    *,
    model: str = MODEL,
    question_set: str = QUESTION_SET,
) -> dict[str, list[DocumentReading]]:
    """Every reading for `symbols`, keyed by the document's legal close (8-K: acceptance time,
    else publish time; news: publish time). Documents past the last known close are left out."""
    tickers = {
        ticker.id: ticker.symbol
        for ticker in session.exec(select(Ticker).where(col(Ticker.symbol).in_(list(symbols))))
    }
    out: dict[str, list[DocumentReading]] = {symbol: [] for symbol in symbols}
    rows = session.exec(
        select(JevReading, func.coalesce(RawDocument.acceptance_at, RawDocument.published_at))
        .join(RawDocument, col(RawDocument.id) == JevReading.document_id)
        .where(
            col(JevReading.ticker_id).in_(list(tickers)),
            JevReading.model_requested == model,
            JevReading.question_set == question_set,
        )
    ).all()
    for reading, stamp in rows:
        close = legal_closes.of(stamp)
        if close is None:
            continue
        out[tickers[reading.ticker_id]].append(
            DocumentReading(
                legal_close=close,
                p_negative=reading.p_negative,
                p_neutral=reading.p_neutral,
                p_positive=reading.p_positive,
                p_routine=reading.p_routine,
                event_type=reading.event_type,
                document_id=str(reading.document_id),
            )
        )
    for readings in out.values():
        readings.sort(key=lambda r: (r.legal_close, r.document_id))
    return out
```

- [ ] **Step 5: Run the tests and checks**

Run: `uv run pytest tests/test_jev_readings.py tests/test_strategy_types.py tests/test_decide.py -v && uv run mypy src && uv run ruff check .`
Expected: all pass (10 new); mypy and ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/signalbench/strategy/readings.py src/signalbench/jev/store.py tests/test_jev_readings.py
git commit -m "feat: JevReadingsView and the readings loader keyed by legal close

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Filter decision (pure)

**Files:**
- Create: `src/signalbench/jev/filter.py`, `tests/test_jev_filter.py`

- [ ] **Step 1: Write the failing test**

`tests/test_jev_filter.py` (crafted trade lists: no eligible θ, a fit that does not confirm, a fit that confirms, the tie-break, and the window edges):

```python
from datetime import date

from pytest import approx

from signalbench.jev.filter import (
    CONFIRM_START,
    FIT_END,
    FIT_START,
    THETA_GRID,
    ScoredTrade,
    TradeRow,
    decide_filter,
    score_trades,
    split,
)
from signalbench.strategy.readings import DocumentReading, JevReadingsView
from strategy_helpers import weekdays

FIT_DAY = date(2019, 6, 3)
CONFIRM_DAY = date(2024, 6, 3)


def _trades(day: date, count: int, r: float, p: float | None) -> list[ScoredTrade]:
    return [ScoredTrade("breakout", f"S{i}", day, r, p) for i in range(count)]


def _fit() -> list[ScoredTrade]:
    """theta 0.5 blocks 15 (mean R -1/3); 0.6, 0.7, 0.8 block 10 (mean R -1); 0.9 blocks 0."""
    return (
        _trades(FIT_DAY, 10, -1.0, 0.85)
        + _trades(FIT_DAY, 5, 1.0, 0.55)
        + _trades(FIT_DAY, 20, 0.5, None)  # no reading in the window: never blocked
    )


def test_the_pre_registered_grid_and_windows() -> None:
    assert THETA_GRID == (0.5, 0.6, 0.7, 0.8, 0.9)
    assert (FIT_START, FIT_END, CONFIRM_START) == (
        date(2016, 1, 1), date(2022, 12, 31), date(2023, 1, 1),
    )


def test_split_counts_blocked_and_kept_trades() -> None:
    row = split(_fit(), 0.5)
    assert (row.blocked, row.kept) == (15, 20)
    assert row.mean_r_blocked == approx(-1 / 3) and row.mean_r_kept == approx(0.5)
    assert row.difference == approx(0.5 + 1 / 3)
    empty = split(_fit(), 0.9)
    assert (empty.blocked, empty.mean_r_blocked, empty.difference) == (0, None, None)


def test_no_eligible_theta_means_information_only() -> None:
    trades = _trades(FIT_DAY, 9, -1.0, 0.95) + _trades(FIT_DAY, 30, 0.5, 0.1)
    decision = decide_filter(trades)
    assert decision.mode == "information_only"
    assert decision.theta is None and decision.confirm is None
    assert "no theta blocks at least 10 fit-window trades" in decision.reason
    assert [row.blocked for row in decision.fit] == [9, 9, 9, 9, 9]


def test_the_best_eligible_theta_wins_and_ties_go_to_the_lower_theta() -> None:
    confirm = _trades(CONFIRM_DAY, 10, -0.5, 0.65) + _trades(CONFIRM_DAY, 5, 0.3, None)
    decision = decide_filter(_fit() + confirm)
    assert decision.theta == 0.6  # 0.6, 0.7 and 0.8 tie at +1.6; 0.5 gives +0.83
    assert [row.difference for row in decision.fit][:4] == [
        approx(0.5 + 1 / 3), approx(1.6), approx(1.6), approx(1.6),
    ]


def test_a_fit_that_confirms_turns_the_filter_on() -> None:
    confirm = _trades(CONFIRM_DAY, 10, -0.5, 0.65) + _trades(CONFIRM_DAY, 5, 0.3, 0.2)
    decision = decide_filter(_fit() + confirm)
    assert decision.mode == "on"
    assert decision.theta == 0.6
    assert decision.confirm is not None
    assert (decision.confirm.blocked, decision.confirm.kept) == (10, 5)
    assert (decision.fit_trades, decision.confirm_trades) == (35, 15)


def test_a_fit_whose_blocked_trades_do_better_in_confirmation_is_information_only() -> None:
    confirm = _trades(CONFIRM_DAY, 10, 1.0, 0.65) + _trades(CONFIRM_DAY, 5, 0.0, None)
    decision = decide_filter(_fit() + confirm)
    assert decision.mode == "information_only"
    assert decision.theta == 0.6  # still reported, but not applied
    assert "blocked mean R 1.000 is not below kept mean R 0.000" in decision.reason


def test_too_few_blocked_confirmation_trades_is_information_only() -> None:
    confirm = _trades(CONFIRM_DAY, 9, -2.0, 0.95) + _trades(CONFIRM_DAY, 5, 0.3, None)
    decision = decide_filter(_fit() + confirm)
    assert decision.mode == "information_only"
    assert "only 9 confirmation trades blocked" in decision.reason


def test_window_edges() -> None:
    edges = (
        _trades(date(2015, 12, 31), 10, -1.0, 0.95)  # before the fit window: ignored
        + _trades(FIT_START, 10, -1.0, 0.95)
        + _trades(FIT_END, 1, 0.5, None)
        + _trades(CONFIRM_START, 3, 0.1, None)
    )
    decision = decide_filter(edges)
    assert (decision.fit_trades, decision.confirm_trades) == (11, 3)


def test_trades_are_scored_with_the_readings_view_block_window() -> None:
    sessions = weekdays(date(2024, 1, 1), 30)
    view = JevReadingsView(
        {
            "AAA": [
                DocumentReading(sessions[5], 0.7, 0.2, 0.1, 0.1, "legal", "d1"),
                DocumentReading(sessions[20], 0.9, 0.05, 0.05, 0.1, "legal", "d2"),
            ]
        },
        sessions,
        block_theta=None,
    )
    rows = [
        TradeRow("pullback", "AAA", sessions[14], 0.4),  # d1 is 10 sessions back: inside
        TradeRow("pullback", "AAA", sessions[15], 0.4),  # d1 is 11 sessions back: outside
        TradeRow("breakout", "BBB", sessions[14], -1.0),
    ]
    assert [t.max_p_negative for t in score_trades(rows, view.max_p_negative)] == [0.7, None, None]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_jev_filter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'signalbench.jev.filter'`.

- [ ] **Step 3: Implement**

`src/signalbench/jev/filter.py`:

```python
"""The pre-registered Jev filter decision (spec 03, Filter decision steps 1-3). Pure.

Nothing here is tuned after the fact: the theta grid, the windows, and the 10-trade minimum are
fixed by the spec. Fit and confirmation results must never be used to change them.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from statistics import fmean
from typing import Literal

THETA_GRID = (0.5, 0.6, 0.7, 0.8, 0.9)
MIN_BLOCKED = 10
FIT_START = date(2016, 1, 1)
FIT_END = date(2022, 12, 31)
CONFIRM_START = date(2023, 1, 1)
FilterMode = Literal["on", "information_only"]


@dataclass(frozen=True)
class TradeRow:
    """One closed trade from a stored jev-off v1 run."""

    setup: str
    symbol: str
    signal_date: date
    r: float


@dataclass(frozen=True)
class ScoredTrade:
    setup: str
    symbol: str
    signal_date: date
    r: float
    max_p_negative: float | None  # None: no reading in the window, so never blocked


@dataclass(frozen=True)
class Split:
    theta: float
    kept: int
    blocked: int
    mean_r_kept: float | None
    mean_r_blocked: float | None

    @property
    def difference(self) -> float | None:
        """Mean R of kept trades minus mean R of blocked trades."""
        if self.mean_r_kept is None or self.mean_r_blocked is None:
            return None
        return self.mean_r_kept - self.mean_r_blocked


@dataclass(frozen=True)
class FilterDecision:
    mode: FilterMode
    theta: float | None  # the fitted theta (None when no theta was eligible)
    reason: str
    fit_trades: int
    confirm_trades: int
    fit: tuple[Split, ...]  # one row per theta in THETA_GRID
    confirm: Split | None


def score_trades(
    trades: Sequence[TradeRow], max_p_negative: Callable[[str, date], float | None]
) -> list[ScoredTrade]:
    """Step 1: the max p_negative over the symbol's documents whose legal close falls in the
    10 sessions ending on the signal date (`JevReadingsView.max_p_negative`)."""
    return [
        ScoredTrade(t.setup, t.symbol, t.signal_date, t.r, max_p_negative(t.symbol, t.signal_date))
        for t in trades
    ]


def _is_blocked(trade: ScoredTrade, theta: float) -> bool:
    return trade.max_p_negative is not None and trade.max_p_negative >= theta


def split(trades: Sequence[ScoredTrade], theta: float) -> Split:
    blocked = [t.r for t in trades if _is_blocked(t, theta)]
    kept = [t.r for t in trades if not _is_blocked(t, theta)]
    return Split(
        theta=theta,
        kept=len(kept),
        blocked=len(blocked),
        mean_r_kept=fmean(kept) if kept else None,
        mean_r_blocked=fmean(blocked) if blocked else None,
    )


def _eligible(row: Split) -> bool:
    """At least MIN_BLOCKED blocked trades, and at least one kept trade to compare against."""
    return row.blocked >= MIN_BLOCKED and row.kept > 0


def _confirmation(check: Split) -> tuple[FilterMode, str]:
    """Step 3: ON only with at least MIN_BLOCKED blocked trades whose mean R is below the kept."""
    if check.blocked < MIN_BLOCKED:
        return "information_only", (
            f"only {check.blocked} confirmation trades blocked (need {MIN_BLOCKED})"
        )
    if check.mean_r_kept is None or check.mean_r_blocked is None:
        return "information_only", "no kept confirmation trades to compare against"
    if check.mean_r_blocked >= check.mean_r_kept:
        return "information_only", (
            f"blocked mean R {check.mean_r_blocked:.3f} is not below kept mean R "
            f"{check.mean_r_kept:.3f} in the confirmation window"
        )
    return "on", (
        f"{check.blocked} confirmation trades blocked with mean R {check.mean_r_blocked:.3f}, "
        f"below the kept mean R {check.mean_r_kept:.3f}"
    )


def decide_filter(trades: Sequence[ScoredTrade]) -> FilterDecision:
    """Step 2 fits theta on 2016-2022 signals; step 3 confirms it on signals from 2023 on."""
    fit_trades = [t for t in trades if FIT_START <= t.signal_date <= FIT_END]
    confirm_trades = [t for t in trades if t.signal_date >= CONFIRM_START]
    fit = tuple(split(fit_trades, theta) for theta in THETA_GRID)
    eligible = [row for row in fit if _eligible(row)]
    if not eligible:
        return FilterDecision(
            mode="information_only",
            theta=None,
            reason=f"no theta blocks at least {MIN_BLOCKED} fit-window trades",
            fit_trades=len(fit_trades),
            confirm_trades=len(confirm_trades),
            fit=fit,
            confirm=None,
        )
    # max() keeps the first maximum, so a tie goes to the lower theta.
    best = max(eligible, key=lambda row: row.difference or 0.0)
    check = split(confirm_trades, best.theta)
    mode, reason = _confirmation(check)
    return FilterDecision(
        mode=mode,
        theta=best.theta,
        reason=reason,
        fit_trades=len(fit_trades),
        confirm_trades=len(confirm_trades),
        fit=fit,
        confirm=check,
    )
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_jev_filter.py -v && uv run mypy src && uv run ruff check .`
Expected: 9 passed; mypy and ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/jev/filter.py tests/test_jev_filter.py
git commit -m "feat: the pre-registered Jev filter decision

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Filter inputs, `data/jev_filter_v1.yaml`, and the decision report

**Files:**
- Create: `src/signalbench/jev/filter_record.py`, `tests/test_jev_filter_record.py`

- [ ] **Step 1: Write the failing test**

`tests/test_jev_filter_record.py`:

```python
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlmodel import Session

from signalbench.db.models import BacktestRun
from signalbench.jev.filter import ScoredTrade, decide_filter
from signalbench.jev.filter_record import (
    FilterFileError,
    FilterInputError,
    FilterRecord,
    load_filter_setting,
    load_v1_trades,
    render_filter_report,
    write_filter_file,
)

FIT_DAY = date(2019, 6, 3)
CONFIRM_DAY = date(2024, 6, 3)


def _run(
    session: Session,
    setup: str,
    trades: list[tuple[str, str, float]],
    *,
    sha: str = "c" * 64,
    run_at: datetime = datetime(2026, 9, 24, 21, 0, tzinfo=UTC),
    version: str = "v1",
    jev_mode: str = "off",
) -> BacktestRun:
    run = BacktestRun(
        strategy_version=version, config_sha256=sha, git_sha="abc123", setup=setup,
        jev_mode=jev_mode, start_date=date(2012, 1, 3), end_date=date(2026, 9, 24),
        data_fingerprint="d" * 64, metrics={}, pass_bar={}, passed=False,
        trade_log={
            "trades": [
                {"symbol": symbol, "signal_date": day, "r": r} for symbol, day, r in trades
            ],
            "events": [],
        },
        run_at=run_at,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def test_trades_come_from_the_latest_v1_jev_off_run_of_each_setup(session: Session) -> None:
    _run(session, "pullback", [("OLD", "2019-01-02", 9.0)], run_at=datetime(2026, 9, 1, tzinfo=UTC))
    pullback = _run(session, "pullback", [("AAA", "2019-06-03", 0.5)])
    breakout = _run(session, "breakout", [("BBB", "2024-06-03", -1.0), ("CCC", "2020-02-03", 2.0)])
    _run(session, "breakout", [("XXX", "2020-01-02", 5.0)], version="v2")  # post-hoc: ignored
    _run(session, "breakout", [("YYY", "2020-01-02", 5.0)], jev_mode="filter")  # ignored
    trades, runs = load_v1_trades(session)
    assert [(t.setup, t.symbol, t.signal_date, t.r) for t in trades] == [
        ("pullback", "AAA", date(2019, 6, 3), 0.5),
        ("breakout", "BBB", date(2024, 6, 3), -1.0),
        ("breakout", "CCC", date(2020, 2, 3), 2.0),
    ]
    assert [(run.setup, run.run_id) for run in runs] == [
        ("pullback", str(pullback.id)),
        ("breakout", str(breakout.id)),
    ]
    assert runs[0].config_sha256 == "c" * 64


def test_a_missing_setup_run_is_refused(session: Session) -> None:
    _run(session, "pullback", [])
    with pytest.raises(FilterInputError, match="No stored v1 jev-off run for breakout"):
        load_v1_trades(session)


def test_runs_with_two_config_hashes_are_refused(session: Session) -> None:
    _run(session, "pullback", [])
    _run(session, "breakout", [], sha="e" * 64)
    with pytest.raises(FilterInputError, match="more than one config_sha256"):
        load_v1_trades(session)


def _record(mode_on: bool) -> FilterRecord:
    fit = (
        [ScoredTrade("breakout", f"S{i}", FIT_DAY, -1.0, 0.85) for i in range(10)]
        + [ScoredTrade("breakout", f"K{i}", FIT_DAY, 0.5, None) for i in range(20)]
    )
    confirm_r = -0.5 if mode_on else 1.0
    confirm = (
        [ScoredTrade("pullback", f"S{i}", CONFIRM_DAY, confirm_r, 0.9) for i in range(10)]
        + [ScoredTrade("pullback", f"K{i}", CONFIRM_DAY, 0.3, None) for i in range(5)]
    )
    return FilterRecord(
        decision=decide_filter(fit + confirm),
        source_runs={"pullback": "run-p", "breakout": "run-b"},
        strategy_config_sha256="c" * 64,
        confirm_end=date(2026, 9, 24),
        readings=1234,
        builds={"typesafe/jev-1.13-20260917": 1234},
        decided_on=date(2026, 9, 25),
        report_path="reports/jev/2026-09-25-filter-decision.md",
    )


def test_an_on_decision_round_trips_through_the_filter_file(tmp_path: Path) -> None:
    path = tmp_path / "jev_filter_v1.yaml"
    write_filter_file(path, _record(mode_on=True))
    text = path.read_text(encoding="utf-8")
    assert "mode: 'on'\n" in text  # quoted: a bare `on` is a YAML 1.1 boolean
    assert "theta_block: 0.5\n" in text
    assert "report: reports/jev/2026-09-25-filter-decision.md" in text
    setting = load_filter_setting(path)
    assert (setting.mode, setting.theta_block) == ("on", 0.5)
    assert (setting.model_requested, setting.question_set) == ("typesafe/jev-1.13", "q1")


def test_an_information_only_decision_blocks_nothing(tmp_path: Path) -> None:
    path = tmp_path / "jev_filter_v1.yaml"
    write_filter_file(path, _record(mode_on=False))
    text = path.read_text(encoding="utf-8")
    assert "mode: information_only\n" in text
    assert "theta_fit: 0.5\n" in text and "theta_block: null\n" in text
    setting = load_filter_setting(path)
    assert (setting.mode, setting.theta_block) == ("information_only", None)


def test_the_filter_file_is_written_once(tmp_path: Path) -> None:
    path = tmp_path / "jev_filter_v1.yaml"
    write_filter_file(path, _record(mode_on=True))
    with pytest.raises(FilterFileError, match="already exists"):
        write_filter_file(path, _record(mode_on=False))


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("mode: maybe\ntheta_block: 0.5\nquestion_set: q1\nmodel_requested: m\n", "mode"),
        ("mode: on\ntheta_block: 0.5\nquestion_set: q1\nmodel_requested: m\n", "quoted"),
        ("mode: 'on'\ntheta_block: null\nquestion_set: q1\nmodel_requested: m\n", "theta_block"),
        ("mode: 'on'\ntheta_block: 0.55\nquestion_set: q1\nmodel_requested: m\n", "theta_block"),
        ("- not a mapping\n", "mapping"),
    ],
)
def test_a_malformed_filter_file_is_rejected(tmp_path: Path, body: str, message: str) -> None:
    path = tmp_path / "jev_filter_v1.yaml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(FilterFileError, match=message):
        load_filter_setting(path)


def test_the_report_shows_the_rule_both_windows_and_the_caveats() -> None:
    text = render_filter_report(_record(mode_on=True))
    assert text.startswith("# Jev filter decision (spec 03)\n")
    assert "**Result:** ON, theta_block = 0.5" in text
    assert "| 0.5 | 10 | 20 | -1.000 | 0.500 | 1.500 | yes |" in text
    assert "| 0.9 | 0 | 30 | n/a | 0.000 | n/a | no |" in text
    assert "## Confirmation window: signals 2023-01-01 to 2026-09-24 (15 trades)" in text
    assert "| 0.5 | 10 | 5 | -0.500 | 0.300 |" in text
    assert "`run-p`" in text and "`run-b`" in text
    assert "cannot change the spec 02 v1 results" in text
    assert "effectively filings-only" in text
    info = render_filter_report(_record(mode_on=False))
    assert "**Result:** information only" in info
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_jev_filter_record.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'signalbench.jev.filter_record'`.

- [ ] **Step 3: Implement**

`src/signalbench/jev/filter_record.py`:

```python
"""Inputs and outputs of the filter decision: the stored v1 trade logs, `data/jev_filter_v1.yaml`,
and the markdown report (spec 03, Filter decision). `data/strategy_v1.yaml` is never touched."""

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, cast

import yaml
from sqlmodel import Session, col, select

from signalbench.db.models import BacktestRun
from signalbench.jev.filter import (
    CONFIRM_START,
    FIT_END,
    FIT_START,
    MIN_BLOCKED,
    THETA_GRID,
    FilterDecision,
    FilterMode,
    Split,
    TradeRow,
)
from signalbench.jev.questions import MODEL, QUESTION_SET

FILTER_SETUPS = ("pullback", "breakout")
FILE_HEADER = (
    "# Jev filter decision (spec 03, pre-registered rule), written once by\n"
    "# `signalbench jev fit-filter --write`. Do not edit: a different decision needs a new\n"
    "# question set. `signalbench backtest run --jev filter` reads mode and theta_block.\n"
)


class FilterInputError(ValueError):
    """The stored v1 runs cannot feed the filter decision. The message says why."""


class FilterFileError(ValueError):
    """`data/jev_filter_v1.yaml` is missing, malformed, or already written."""


@dataclass(frozen=True)
class SourceRun:
    setup: str
    run_id: str
    config_sha256: str
    end_date: date


def load_v1_trades(session: Session) -> tuple[list[TradeRow], list[SourceRun]]:
    """Closed trades of the latest stored v1 jev-off run of Pullback and of Breakout.

    Refuses when either run is missing, or when the stored v1 jev-off runs of these setups
    used more than one config_sha256.
    """
    runs = session.exec(
        select(BacktestRun)
        .where(
            BacktestRun.strategy_version == "v1",
            BacktestRun.jev_mode == "off",
            col(BacktestRun.setup).in_(FILTER_SETUPS),
        )
        .order_by(col(BacktestRun.run_at))
    ).all()
    latest = {run.setup: run for run in runs}  # later runs overwrite earlier ones
    missing = [setup for setup in FILTER_SETUPS if setup not in latest]
    if missing:
        raise FilterInputError(
            f"No stored v1 jev-off run for {', '.join(missing)}. Run "
            "`signalbench backtest run --setup <setup>` first (spec 02)."
        )
    hashes = sorted({run.config_sha256 for run in runs})
    if len(hashes) > 1:
        raise FilterInputError(
            "The stored v1 jev-off runs used more than one config_sha256 "
            f"({', '.join(sha[:12] for sha in hashes)}); the filter needs one pre-registered config."
        )
    trades: list[TradeRow] = []
    sources: list[SourceRun] = []
    for setup in FILTER_SETUPS:
        run = latest[setup]
        sources.append(SourceRun(setup, str(run.id), run.config_sha256, run.end_date))
        for trade in run.trade_log["trades"]:
            trades.append(
                TradeRow(
                    setup=setup,
                    symbol=str(trade["symbol"]),
                    signal_date=date.fromisoformat(str(trade["signal_date"])),
                    r=float(trade["r"]),
                )
            )
    return trades, sources


@dataclass(frozen=True)
class FilterRecord:
    decision: FilterDecision
    source_runs: dict[str, str]  # setup -> run id
    strategy_config_sha256: str
    confirm_end: date
    readings: int
    builds: dict[str, int]  # resolved model build -> readings
    decided_on: date
    report_path: str  # repo-relative


def _row(split: Split | None, trades: int) -> dict[str, Any] | None:
    if split is None:
        return None
    return {
        "theta": split.theta,
        "trades": trades,
        "blocked": split.blocked,
        "kept": split.kept,
        "mean_r_blocked": None if split.mean_r_blocked is None else round(split.mean_r_blocked, 6),
        "mean_r_kept": None if split.mean_r_kept is None else round(split.mean_r_kept, 6),
    }


def filter_file_payload(record: FilterRecord) -> dict[str, Any]:
    decision = record.decision
    chosen = next((row for row in decision.fit if row.theta == decision.theta), None)
    return {
        "question_set": QUESTION_SET,
        "model_requested": MODEL,
        "mode": decision.mode,
        "theta_fit": decision.theta,
        "theta_block": decision.theta if decision.mode == "on" else None,
        "reason": decision.reason,
        "fit_window": {"start": FIT_START, "end": FIT_END},
        "confirm_window": {"start": CONFIRM_START, "end": record.confirm_end},
        "fit": _row(chosen, decision.fit_trades),
        "confirm": _row(decision.confirm, decision.confirm_trades),
        "source_runs": dict(record.source_runs),
        "strategy_config_sha256": record.strategy_config_sha256,
        "readings": record.readings,
        "builds": dict(record.builds),
        "decided_on": record.decided_on,
        "report": record.report_path,
    }


def write_filter_file(path: Path, record: FilterRecord) -> None:
    """Write the decision once. An existing file is never overwritten."""
    if path.exists():
        raise FilterFileError(
            f"{path.name} already exists. The filter decision is made once; a new decision "
            "needs a new question set (spec 03)."
        )
    body = yaml.safe_dump(filter_file_payload(record), sort_keys=False, allow_unicode=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(FILE_HEADER + body, encoding="utf-8")


@dataclass(frozen=True)
class FilterSetting:
    mode: FilterMode
    theta_block: float | None
    model_requested: str
    question_set: str


def load_filter_setting(path: Path) -> FilterSetting:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise FilterFileError(f"{path.name}: expected a mapping")
    data = cast(dict[str, Any], raw)
    mode = data.get("mode")
    if mode not in ("on", "information_only"):
        raise FilterFileError(
            f"{path.name}: mode must be 'on' (quoted) or information_only, got {mode!r}"
        )
    theta = data.get("theta_block")
    if mode == "on" and theta not in THETA_GRID:
        raise FilterFileError(f"{path.name}: theta_block must be one of {THETA_GRID} when on")
    if mode == "information_only" and theta is not None:
        raise FilterFileError(f"{path.name}: theta_block must be null when information_only")
    model, questions = data.get("model_requested"), data.get("question_set")
    if not isinstance(model, str) or not isinstance(questions, str):
        raise FilterFileError(f"{path.name}: model_requested and question_set must be strings")
    return FilterSetting(
        mode=cast(FilterMode, mode),
        theta_block=None if theta is None else float(theta),
        model_requested=model,
        question_set=questions,
    )


def _r(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def render_filter_report(record: FilterRecord) -> str:
    decision = record.decision
    builds = ", ".join(f"{build} ({count})" for build, count in sorted(record.builds.items()))
    if decision.mode == "on":
        result = f"ON, theta_block = {decision.theta}"
    else:
        result = "information only (nothing is blocked)"
    lines = [
        "# Jev filter decision (spec 03)",
        "",
        f"**Result:** {result}",
        "",
        f"**Why:** {decision.reason}.",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Decided on | {record.decided_on.isoformat()} |",
        f"| Question set / model requested | {QUESTION_SET} / {MODEL} |",
        f"| Readings available | {record.readings} |",
        f"| Resolved builds | {builds} |",
        *[
            f"| Source run ({setup}, v1, Jev off) | `{run}` |"
            for setup, run in record.source_runs.items()
        ],
        f"| strategy_v1 config_sha256 | `{record.strategy_config_sha256}` |",
        "",
        "## Rule (pre-registered)",
        "",
        (
            "Each trade gets the max `p_negative` among the symbol's documents whose legal close "
            "falls in the 10 sessions ending on its signal date (no reading: never blocked). A "
            f"theta is eligible when it blocks at least {MIN_BLOCKED} fit-window trades and keeps "
            "at least one. The eligible theta with the largest (kept mean R - blocked mean R) is "
            "chosen; ties go to the lower theta. The filter is ON only if, in the confirmation "
            f"window, at least {MIN_BLOCKED} trades are blocked and their mean R is below the "
            "kept mean R."
        ),
        "",
        (
            f"## Fit window: signals {FIT_START.isoformat()} to {FIT_END.isoformat()} "
            f"({decision.fit_trades} trades)"
        ),
        "",
        "| theta | Blocked | Kept | Mean R blocked | Mean R kept | Kept - blocked | Eligible |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in decision.fit:
        eligible = "yes" if row.blocked >= MIN_BLOCKED and row.kept > 0 else "no"
        lines.append(
            f"| {row.theta} | {row.blocked} | {row.kept} | {_r(row.mean_r_blocked)} "
            f"| {_r(row.mean_r_kept)} | {_r(row.difference)} | {eligible} |"
        )
    lines += [
        "",
        (
            f"## Confirmation window: signals {CONFIRM_START.isoformat()} to "
            f"{record.confirm_end.isoformat()} ({decision.confirm_trades} trades)"
        ),
        "",
    ]
    check = decision.confirm
    if check is None:
        lines.append("Not run: no theta was eligible.")
    else:
        lines += [
            "| theta | Blocked | Kept | Mean R blocked | Mean R kept |",
            "| --- | --- | --- | --- | --- |",
            (
                f"| {check.theta} | {check.blocked} | {check.kept} "
                f"| {_r(check.mean_r_blocked)} | {_r(check.mean_r_kept)} |"
            ),
        ]
    lines += [
        "",
        "## Notes",
        "",
        (
            "- A run with `--jev filter` is information only: it cannot change the spec 02 v1 "
            "results (Pullback and Breakout both failed their pass bar)."
        ),
        (
            "- Finnhub news covers only about the last year, so the fit window is effectively "
            "filings-only."
        ),
        (
            "- The theta grid, the windows, and the 10-trade minimum were fixed before this run. "
            "These results must not be used to change them."
        ),
    ]
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_jev_filter_record.py -v && uv run mypy src && uv run ruff check .`
Expected: 12 passed; mypy and ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/jev/filter_record.py tests/test_jev_filter_record.py
git commit -m "feat: filter inputs from the stored v1 runs, the filter file, and its report

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Calibration

**Files:**
- Create: `src/signalbench/jev/calibration.py`, `tests/test_jev_calibration.py`
- Modify: `src/signalbench/jev/store.py`

- [ ] **Step 1: Write the failing test**

`tests/test_jev_calibration.py` (the hand-computed example is worked in the comment above `EXAMPLE`):

```python
from datetime import UTC, date, datetime
from decimal import Decimal

from pytest import approx
from sqlmodel import Session

from signalbench.db.models import (
    DocType,
    DocumentTicker,
    JevReading,
    Price,
    RawDocument,
    Ticker,
    TickerKind,
)
from signalbench.jev.calibration import (
    Sample,
    calibrate,
    calibration_sections,
    decile,
    label_for,
    render_calibration_report,
)
from signalbench.jev.store import calibration_samples
from signalbench.market.legal_close import LegalCloses
from strategy_helpers import WeekdaySessions, weekdays

BEFORE = date(2026, 9, 1)
AFTER = date(2026, 9, 16)

# Hand-worked example (p_negative, p_neutral, p_positive, label):
#   p_positive deciles: [0.05] flat | [0.15 up, 0.12 flat] | [0.85 up] | [0.95 up, 0.91 down]
#   ECE(p_positive vs up) = (0.05 + 2 x 0.365 + 0.15 + 2 x 0.43) / 6 = 1.79 / 6
#   p_negative deciles: [0.05, 0.08, 0.05, 0.02, 0.04] one down | [0.15] none
#   ECE(p_negative vs down) = (5 x |0.048 - 0.2| + 0.15) / 6 = 0.91 / 6
#   argmax: neutral, neutral, neutral, positive, positive, positive -> 4 of 6 right
#   labels: up 3, flat 2, down 1 -> majority baseline "up" at 3 / 6
EXAMPLE = [
    Sample(BEFORE, 0.15, 0.80, 0.05, "flat"),
    Sample(BEFORE, 0.05, 0.80, 0.15, "up"),
    Sample(BEFORE, 0.08, 0.80, 0.12, "flat"),
    Sample(BEFORE, 0.05, 0.10, 0.85, "up"),
    Sample(BEFORE, 0.02, 0.03, 0.95, "up"),
    Sample(AFTER, 0.04, 0.05, 0.91, "down"),
]


def test_deciles_include_the_upper_edge_in_the_top_bucket() -> None:
    assert [decile(p) for p in (0.0, 0.0999, 0.1, 0.3, 0.7, 0.95, 1.0)] == [0, 0, 1, 3, 7, 9, 9]


def test_reliability_ece_and_accuracy_match_the_hand_computed_example() -> None:
    section = calibrate("All", EXAMPLE)
    assert section.samples == 6
    by_bucket = {bucket.index: bucket for bucket in section.positive if bucket.count}
    assert sorted(by_bucket) == [0, 1, 8, 9]
    assert by_bucket[1].count == 2
    assert by_bucket[1].mean_predicted == approx(0.135)
    assert by_bucket[1].observed_rate == approx(0.5)
    assert by_bucket[9].mean_predicted == approx(0.93)
    assert section.positive_ece == approx(1.79 / 6)
    negative = {bucket.index: bucket for bucket in section.negative if bucket.count}
    assert negative[0].count == 5 and negative[0].mean_predicted == approx(0.048)
    assert negative[0].observed_rate == approx(0.2)
    assert section.negative_ece == approx(0.91 / 6)
    assert section.accuracy == approx(4 / 6)
    assert (section.majority_label, section.majority_rate) == ("up", approx(0.5))
    assert section.label_counts == {"down": 1, "flat": 2, "up": 3}


def test_sections_split_at_jevs_release() -> None:
    sections = calibration_sections(EXAMPLE)
    assert [(s.name, s.samples) for s in sections] == [
        ("All documents", 6),
        ("Legal close before 2026-09-15", 5),
        ("Legal close on or after 2026-09-15", 1),
    ]


def test_an_empty_section_has_no_ece_or_accuracy() -> None:
    empty = calibrate("Nothing", [])
    assert (empty.samples, empty.positive_ece, empty.accuracy, empty.majority_label) == (
        0, None, None, None,
    )


def test_labels_are_the_5_session_excess_return_over_qqq() -> None:
    sessions = weekdays(date(2024, 1, 1), 10)
    qqq = {day: 100.0 for day in sessions} | {sessions[6]: 101.0}
    stock = {day: 100.0 for day in sessions}
    t = sessions[1]
    assert label_for(t, stock | {sessions[6]: 104.0}, qqq, sessions) == "up"  # 4% - 1% = 3%
    assert label_for(t, stock | {sessions[6]: 103.0}, qqq, sessions) == "flat"  # exactly +2%
    assert label_for(t, stock | {sessions[6]: 98.5}, qqq, sessions) == "down"  # -2.5%
    assert label_for(sessions[5], stock, qqq, sessions) is None  # 5 sessions later is unknown
    missing = {day: price for day, price in stock.items() if day != sessions[6]}
    assert label_for(t, missing, qqq, sessions) is None


def test_the_report_has_every_table_and_the_split() -> None:
    text = render_calibration_report(
        calibration_sections(EXAMPLE),
        readings=7,
        unlabeled=1,
        builds={"typesafe/jev-1.13-20260917": 7},
        written_on=date(2026, 10, 1),
    )
    assert text.startswith("# Jev calibration report (spec 03, information only)\n")
    assert "| 0.1-0.2 | 2 | 0.135 | 0.500 |" in text
    assert f"ECE: {1.79 / 6:.3f}" in text
    assert "Argmax impact accuracy: 66.7% (majority baseline `up`: 50.0%)" in text
    assert "## Legal close on or after 2026-09-15 (1 documents)" in text
    assert "Nothing is adjusted automatically" in text


def _price(session: Session, ticker: Ticker, day: date, close: float) -> None:
    value = Decimal(str(close))
    session.add(
        Price(ticker_id=ticker.id, date=day, open=value, high=value, low=value, close=value,
              adj_close=value, volume=1_000)
    )


def test_samples_are_labeled_from_stored_prices(session: Session) -> None:
    sessions = weekdays(date(2024, 1, 1), 12)
    aaa = Ticker(symbol="AAA", company_name="Aaa Corp", kind=TickerKind.us_stock)
    qqq = Ticker(symbol="QQQ", company_name="QQQ", kind=TickerKind.benchmark)
    session.add_all([aaa, qqq])
    session.commit()
    for day in sessions:
        _price(session, aaa, day, 110.0 if day >= sessions[6] else 100.0)
        _price(session, qqq, day, 100.0)
    for index, published in enumerate(
        [datetime(2024, 1, 2, 15, 0, tzinfo=UTC), datetime(2024, 1, 16, 15, 0, tzinfo=UTC)]
    ):
        document = RawDocument(
            source="finnhub", external_id=f"n{index}", doc_type=DocType.news, raw_text="x",
            text="x", published_at=published,
        )
        session.add(document)
        session.flush()
        session.add(DocumentTicker(document_id=document.id, ticker_id=aaa.id))
        session.add(
            JevReading(
                document_id=document.id, ticker_id=aaa.id, model_requested="typesafe/jev-1.13",
                model_resolved="typesafe/jev-1.13-20260917", question_set="q1",
                response_id="r", p_negative=0.1, p_neutral=0.2, p_positive=0.7,
                event_type="product", p_routine=0.1, answers={}, input_tokens=100,
                cost_usd=0.0, latency_ms=1,
            )
        )
    session.commit()
    closes = LegalCloses(WeekdaySessions().session_closes(sessions[0], sessions[-1]))
    samples, unlabeled = calibration_samples(session, ["AAA"], "QQQ", closes, sessions)
    # 2024-01-02 10:00 New York -> legal close 01-02; five sessions later (01-09) AAA is +10%.
    assert [(s.legal_close, s.label) for s in samples] == [(date(2024, 1, 2), "up")]
    assert unlabeled == 1  # 01-16 has no bar five sessions later
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_jev_calibration.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'signalbench.jev.calibration'`.

- [ ] **Step 3: Implement the calculations**

`src/signalbench/jev/calibration.py`:

```python
"""Calibration report (spec 03, information only). Pure: samples in, tables and markdown out.

Nothing here feeds back into theta or the 0.70 positive threshold. Changing either after reading
the report is an owner decision recorded in the spec 03 changelog.
"""

import math
from bisect import bisect_left
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from signalbench.jev.questions import JEV_RELEASE

BENCHMARK_SYMBOL = "QQQ"
HORIZON_SESSIONS = 5
MOVE_THRESHOLD = 0.02
DECILES = 10
Label = Literal["down", "flat", "up"]
LABELS: tuple[Label, ...] = ("down", "flat", "up")
IMPACT_TO_LABEL: dict[str, Label] = {"negative": "down", "neutral": "flat", "positive": "up"}


@dataclass(frozen=True)
class Sample:
    legal_close: date
    p_negative: float
    p_neutral: float
    p_positive: float
    label: Label


def label_for(
    legal_close: date,
    closes: Mapping[date, float],
    benchmark: Mapping[date, float],
    sessions: Sequence[date],
) -> Label | None:
    """The symbol's adj-close return minus QQQ's from the legal close to 5 sessions later:
    above +2% is up, below -2% is down, else flat. None when a price or session is missing.
    `sessions` is sorted. The excess is rounded to 10 decimals so an exact 2% stays flat."""
    index = bisect_left(sessions, legal_close)
    if index >= len(sessions) or sessions[index] != legal_close:
        return None
    if index + HORIZON_SESSIONS >= len(sessions):
        return None
    later = sessions[index + HORIZON_SESSIONS]
    start, end = closes.get(legal_close), closes.get(later)
    b_start, b_end = benchmark.get(legal_close), benchmark.get(later)
    if start is None or end is None or b_start is None or b_end is None:
        return None
    excess = round((end / start - 1.0) - (b_end / b_start - 1.0), 10)
    if excess > MOVE_THRESHOLD:
        return "up"
    if excess < -MOVE_THRESHOLD:
        return "down"
    return "flat"


def decile(p: float) -> int:
    """Bucket 0 is [0, 0.1), ..., bucket 9 is [0.9, 1.0] (1.0 included)."""
    return min(math.floor(p * DECILES + 1e-9), DECILES - 1)


@dataclass(frozen=True)
class Bucket:
    index: int
    count: int
    mean_predicted: float | None
    observed_rate: float | None

    @property
    def label(self) -> str:
        return f"{self.index / DECILES:.1f}-{(self.index + 1) / DECILES:.1f}"


def reliability(pairs: Sequence[tuple[float, bool]]) -> list[Bucket]:
    """Ten buckets of (predicted probability, outcome happened)."""
    grouped: dict[int, list[tuple[float, bool]]] = {i: [] for i in range(DECILES)}
    for p, happened in pairs:
        grouped[decile(p)].append((p, happened))
    return [
        Bucket(
            index=i,
            count=len(rows),
            mean_predicted=math.fsum(p for p, _ in rows) / len(rows) if rows else None,
            observed_rate=sum(1 for _, hit in rows if hit) / len(rows) if rows else None,
        )
        for i, rows in grouped.items()
    ]


def ece(buckets: Sequence[Bucket]) -> float | None:
    """Expected calibration error: sum over buckets of (count / N) x |mean predicted - observed|."""
    total = sum(bucket.count for bucket in buckets)
    if total == 0:
        return None
    return math.fsum(
        bucket.count / total * abs(bucket.mean_predicted - bucket.observed_rate)
        for bucket in buckets
        if bucket.mean_predicted is not None and bucket.observed_rate is not None
    )


def _argmax_label(sample: Sample) -> Label:
    """Ties resolve in the order negative, neutral, positive."""
    options = (
        ("negative", sample.p_negative),
        ("neutral", sample.p_neutral),
        ("positive", sample.p_positive),
    )
    return IMPACT_TO_LABEL[max(options, key=lambda item: item[1])[0]]


@dataclass(frozen=True)
class Section:
    name: str
    samples: int
    positive: list[Bucket]
    positive_ece: float | None
    negative: list[Bucket]
    negative_ece: float | None
    accuracy: float | None
    majority_label: Label | None
    majority_rate: float | None
    label_counts: dict[str, int]


def calibrate(name: str, samples: Sequence[Sample]) -> Section:
    positive = reliability([(s.p_positive, s.label == "up") for s in samples])
    negative = reliability([(s.p_negative, s.label == "down") for s in samples])
    counts = Counter(s.label for s in samples)
    majority: Label | None = None
    rate: float | None = None
    accuracy: float | None = None
    if samples:
        top = max(LABELS, key=lambda name: counts[name])  # ties: down, flat, up
        majority, rate = top, counts[top] / len(samples)
        accuracy = sum(1 for s in samples if _argmax_label(s) == s.label) / len(samples)
    return Section(
        name=name,
        samples=len(samples),
        positive=positive,
        positive_ece=ece(positive),
        negative=negative,
        negative_ece=ece(negative),
        accuracy=accuracy,
        majority_label=majority,
        majority_rate=rate,
        label_counts={label: counts[label] for label in LABELS},
    )


def calibration_sections(samples: Sequence[Sample]) -> list[Section]:
    """All documents, then split at Jev's release (2026-09-15) by legal close."""
    release = JEV_RELEASE.isoformat()
    return [
        calibrate("All documents", samples),
        calibrate(
            f"Legal close before {release}", [s for s in samples if s.legal_close < JEV_RELEASE]
        ),
        calibrate(
            f"Legal close on or after {release}",
            [s for s in samples if s.legal_close >= JEV_RELEASE],
        ),
    ]


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _table(title: str, buckets: Sequence[Bucket], error: float | None) -> list[str]:
    return [
        f"### {title}",
        "",
        "| Predicted | Count | Mean predicted | Observed rate |",
        "| --- | --- | --- | --- |",
        *[
            f"| {b.label} | {b.count} | {_num(b.mean_predicted)} | {_num(b.observed_rate)} |"
            for b in buckets
        ],
        "",
        f"ECE: {_num(error)}",
    ]


def render_calibration_report(
    sections: Sequence[Section],
    *,
    readings: int,
    unlabeled: int,
    builds: Mapping[str, int],
    written_on: date,
) -> str:
    lines = [
        "# Jev calibration report (spec 03, information only)",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Written on | {written_on.isoformat()} |",
        f"| Readings | {readings} |",
        f"| Labeled | {sections[0].samples if sections else 0} |",
        f"| Not labeled (no price 5 sessions after the legal close) | {unlabeled} |",
        f"| Resolved builds | {', '.join(f'{b} ({n})' for b, n in sorted(builds.items()))} |",
        "",
        (
            "Label: the symbol's adj-close return minus QQQ's, from the document's legal close to "
            "5 sessions later. Above +2% is `up`, below -2% is `down`, otherwise `flat`. Jev's "
            "training cutoff is unpublished, so results before 2026-09-15 may be optimistic."
        ),
        "",
        (
            "Nothing is adjusted automatically. Changing theta or the 0.70 positive threshold "
            "after reading this report is an owner decision recorded in the spec 03 changelog."
        ),
    ]
    for section in sections:
        counts = ", ".join(f"{label} {n}" for label, n in section.label_counts.items())
        lines += [
            "",
            f"## {section.name} ({section.samples} documents)",
            "",
            f"Labels: {counts}.",
            "",
            *_table("p_positive vs up", section.positive, section.positive_ece),
            "",
            *_table("p_negative vs down", section.negative, section.negative_ece),
            "",
            (
                f"Argmax impact accuracy: {_pct(section.accuracy)} (majority baseline "
                f"`{section.majority_label}`: {_pct(section.majority_rate)})"
            ),
        ]
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Add the calibration loader**

`src/signalbench/jev/store.py` (complete file: Task 7's loader unchanged, plus `reading_builds` and `calibration_samples`):

```python
"""Database reads for spec 03: readings keyed by legal close, and calibration samples."""

from collections import Counter
from collections.abc import Collection, Sequence
from datetime import date

from sqlalchemy import func
from sqlmodel import Session, col, select

from signalbench.db.models import JevReading, RawDocument, Ticker
from signalbench.jev.calibration import Sample, label_for
from signalbench.jev.questions import MODEL, QUESTION_SET
from signalbench.market.bars import adjusted_bars
from signalbench.market.legal_close import LegalCloses
from signalbench.strategy.readings import DocumentReading


def load_document_readings(
    session: Session,
    symbols: Collection[str],
    legal_closes: LegalCloses,
    *,
    model: str = MODEL,
    question_set: str = QUESTION_SET,
) -> dict[str, list[DocumentReading]]:
    """Every reading for `symbols`, keyed by the document's legal close (8-K: acceptance time,
    else publish time; news: publish time). Documents past the last known close are left out."""
    tickers = {
        ticker.id: ticker.symbol
        for ticker in session.exec(select(Ticker).where(col(Ticker.symbol).in_(list(symbols))))
    }
    out: dict[str, list[DocumentReading]] = {symbol: [] for symbol in symbols}
    rows = session.exec(
        select(JevReading, func.coalesce(RawDocument.acceptance_at, RawDocument.published_at))
        .join(RawDocument, col(RawDocument.id) == JevReading.document_id)
        .where(
            col(JevReading.ticker_id).in_(list(tickers)),
            JevReading.model_requested == model,
            JevReading.question_set == question_set,
        )
    ).all()
    for reading, stamp in rows:
        close = legal_closes.of(stamp)
        if close is None:
            continue
        out[tickers[reading.ticker_id]].append(
            DocumentReading(
                legal_close=close,
                p_negative=reading.p_negative,
                p_neutral=reading.p_neutral,
                p_positive=reading.p_positive,
                p_routine=reading.p_routine,
                event_type=reading.event_type,
                document_id=str(reading.document_id),
            )
        )
    for readings in out.values():
        readings.sort(key=lambda r: (r.legal_close, r.document_id))
    return out


def reading_builds(
    session: Session, *, model: str = MODEL, question_set: str = QUESTION_SET
) -> dict[str, int]:
    """How many stored readings each resolved model build produced."""
    builds = session.exec(
        select(JevReading.model_resolved).where(
            JevReading.model_requested == model, JevReading.question_set == question_set
        )
    ).all()
    return dict(sorted(Counter(builds).items()))


def _closes_by_date(session: Session, symbol: str) -> dict[date, float]:
    ticker = session.exec(select(Ticker).where(Ticker.symbol == symbol)).first()
    if ticker is None:
        return {}
    return {bar.date: bar.close for bar in adjusted_bars(session, ticker.id)}


def calibration_samples(
    session: Session,
    symbols: Collection[str],
    benchmark_symbol: str,
    legal_closes: LegalCloses,
    sessions: Sequence[date],
    *,
    model: str = MODEL,
    question_set: str = QUESTION_SET,
) -> tuple[list[Sample], int]:
    """One labeled sample per reading, and how many readings could not be labeled yet."""
    benchmark = _closes_by_date(session, benchmark_symbol)
    readings = load_document_readings(
        session, symbols, legal_closes, model=model, question_set=question_set
    )
    samples: list[Sample] = []
    unlabeled = 0
    for symbol, rows in sorted(readings.items()):
        closes = _closes_by_date(session, symbol)
        for row in rows:
            label = label_for(row.legal_close, closes, benchmark, sessions)
            if label is None:
                unlabeled += 1
                continue
            samples.append(
                Sample(row.legal_close, row.p_negative, row.p_neutral, row.p_positive, label)
            )
    return samples, unlabeled
```

- [ ] **Step 5: Run the tests and checks**

Run: `uv run pytest tests/test_jev_calibration.py tests/test_jev_readings.py -v && uv run mypy src && uv run ruff check .`
Expected: 17 passed (7 new); mypy and ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/signalbench/jev/calibration.py src/signalbench/jev/store.py tests/test_jev_calibration.py
git commit -m "feat: Jev calibration labels, reliability deciles, ECE, and report

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Readings in the data fingerprint, and the Jev report sections

**Files:**
- Modify: `src/signalbench/backtest/fingerprint.py`, `src/signalbench/backtest/report.py`, `tests/test_determinism.py`, `tests/test_report.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_determinism.py`, replace:

```python
from signalbench.strategy.readings import NullReadingsView
```

with:

```python
from signalbench.strategy.readings import DocumentReading, NullReadingsView
```

Append to `tests/test_determinism.py`:

```python
def test_data_fingerprint_covers_jev_readings_only_when_given() -> None:
    bars = [("AAA", [make_bar(date(2024, 1, 2), 10.0)])]
    one = DocumentReading(date(2024, 1, 3), 0.1, 0.2, 0.7, 0.05, "earnings", "doc-1")
    two = DocumentReading(date(2024, 1, 4), 0.8, 0.1, 0.1, 0.05, "legal", "doc-2")
    without = data_fingerprint(bars, [])
    assert data_fingerprint(bars, [], None) == without  # Jev-off fingerprints are unchanged
    base = data_fingerprint(bars, [], [("AAA", one), ("AAA", two)])
    assert base != without
    assert base != data_fingerprint(bars, [], [])  # no readings is not the same as Jev off
    assert base == data_fingerprint(bars, [], [("AAA", two), ("AAA", one)])  # order free
    moved = DocumentReading(date(2024, 1, 3), 0.1, 0.2, 0.7, 0.06, "earnings", "doc-1")
    assert base != data_fingerprint(bars, [], [("AAA", moved), ("AAA", two)])
    assert base != data_fingerprint(bars, [], [("BBB", one), ("AAA", two)])
```

In `tests/test_report.py`, replace:

```python
from signalbench.backtest.metrics import run_metrics
```

with:

```python
from signalbench.backtest.metrics import TradeStats, run_metrics
```

In `tests/test_report.py`, replace:

```python
from signalbench.backtest.report import (
    CAVEATS,
    metrics_payload,
```

with:

```python
from signalbench.backtest.report import (
    CAVEATS,
    JEV_CAVEATS,
    jev_payload,
    metrics_payload,
```

Append to `tests/test_report.py`:

```python
def _with_jev(run: BacktestRun, *, theta: float | None, information_only: bool) -> BacktestRun:
    run.metrics["jev"] = jev_payload(
        model_requested="typesafe/jev-1.13",
        question_set="q1",
        readings=42,
        builds={"typesafe/jev-1.13-20260917": 42},
        theta_block=theta,
        information_only=information_only,
        out_of_sample=TradeStats(trades=2, win_rate=0.5, mean_r=0.25, median_r=0.25, average_hold=4.0),
    )
    return run


def test_sentiment_report_shows_status_period_readings_and_out_of_sample_trades() -> None:
    run = _with_jev(_run(setup="sentiment"), theta=None, information_only=True)
    text = render_report(run)
    assert text.startswith("# Backtest: sentiment (Jev off)\n")
    assert "**Sentiment status:** information only (fewer than 30 trades)" in text
    assert "no Sentiment entries are sent" in text
    assert "halves split at the calendar midpoint" in text
    assert "| Readings used | 42 |" in text
    assert "| Resolved builds | typesafe/jev-1.13-20260917 (42) |" in text
    assert "## Trades signalled on or after 2026-09-15 (out of sample for Jev)" in text
    assert "Trades 2 · win rate 50.0% · mean R 0.250" in text
    for caveat in (*CAVEATS, *JEV_CAVEATS):
        assert f"- {caveat}" in text
    failed = render_report(_with_jev(_run(setup="sentiment"), theta=None, information_only=False))
    assert "**Sentiment status:** FAIL" in failed


def test_filtered_report_is_information_only() -> None:
    run = _run(setup="breakout")
    run.jev_mode = "filter"
    text = render_report(_with_jev(run, theta=0.7, information_only=True))
    assert text.startswith("# Backtest: breakout (Jev filter)\n")
    assert "**Result:** FAIL (information only)" in text
    assert "**Information only — cannot change the v1 result.**" in text
    assert "| Filter theta_block | 0.7 |" in text
    assert "Sentiment status" not in text


def test_jev_off_reports_have_no_jev_section() -> None:
    text = render_report(_run())
    assert "## Jev readings" not in text
    for caveat in JEV_CAVEATS:
        assert caveat not in text
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_determinism.py tests/test_report.py -v`
Expected: FAIL. Collection stops at `tests/test_report.py` with `ImportError: cannot import name 'JEV_CAVEATS' from 'signalbench.backtest.report'`. Run alone, `tests/test_determinism.py` fails only `test_data_fingerprint_covers_jev_readings_only_when_given`, with `TypeError: data_fingerprint() takes 2 positional arguments but 3 were given`.

- [ ] **Step 3: Implement the fingerprint entry**

`src/signalbench/backtest/fingerprint.py` (complete file; with `readings=None` every Jev-off fingerprint is unchanged, so the stored v1 runs still match):

```python
import hashlib
import math
from collections.abc import Iterable, Sequence
from datetime import date

from signalbench.market.bars import AdjustedBar
from signalbench.strategy.readings import DocumentReading


def signal_set_fingerprint(signal_ids: list[str]) -> str:
    joined = ",".join(sorted(signal_ids))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def series_summary(symbol: str, bars: Sequence[AdjustedBar]) -> str:
    """symbol|first_date|last_date|row_count|round(sum(adj_close), 4)."""
    if not bars:
        return f"{symbol}|||0|0.0000"
    total = round(math.fsum(bar.close for bar in bars), 4)
    return f"{symbol}|{bars[0].date.isoformat()}|{bars[-1].date.isoformat()}|{len(bars)}|{total:.4f}"


def earnings_summary(earnings: Iterable[tuple[str, date]]) -> str:
    """earnings|count|SHA-256 of the sorted, distinct symbol|YYYY-MM-DD pairs."""
    pairs = sorted({f"{symbol}|{day.isoformat()}" for symbol, day in earnings})
    return f"earnings|{len(pairs)}|{signal_set_fingerprint(pairs)}"


def readings_summary(readings: Iterable[tuple[str, DocumentReading]]) -> str:
    """jev|count|SHA-256 of the sorted symbol|legal close|document|probabilities (6 dp) rows."""
    rows = sorted(
        f"{symbol}|{r.legal_close.isoformat()}|{r.document_id}|{r.p_negative:.6f}|"
        f"{r.p_neutral:.6f}|{r.p_positive:.6f}|{r.p_routine:.6f}"
        for symbol, r in readings
    )
    return f"jev|{len(rows)}|{signal_set_fingerprint(rows)}"


def data_fingerprint(
    series: Iterable[tuple[str, Sequence[AdjustedBar]]],
    earnings: Iterable[tuple[str, date]],
    readings: Iterable[tuple[str, DocumentReading]] | None = None,
) -> str:
    """SHA-256 over the sorted per-series summaries of every input price series, plus a
    summary of every (symbol, earnings date) the run used (spec 02), plus, for runs that read
    Jev (spec 03), a summary of every reading. `readings=None` keeps Jev-off fingerprints as
    they were."""
    summaries = [series_summary(symbol, bars) for symbol, bars in series]
    extra = [] if readings is None else [readings_summary(readings)]
    return signal_set_fingerprint([*summaries, earnings_summary(earnings), *extra])
```

- [ ] **Step 4: Implement the report sections**

In `src/signalbench/backtest/report.py`, replace:

```python
from signalbench.backtest.metrics import RunMetrics
```

with:

```python
from signalbench.backtest.metrics import RunMetrics, TradeStats
```

In `src/signalbench/backtest/report.py`, replace:

```python
from signalbench.db.models import BacktestRun
```

with:

```python
from signalbench.db.models import BacktestRun
from signalbench.jev.questions import JEV_RELEASE
```

In `src/signalbench/backtest/report.py`, replace:

```python
PASS_BAR_ROWS = (
```

with:

```python
JEV_CAVEATS = (
    (
        "Jev's training cutoff is unpublished, so results before 2026-09-15 may be optimistic. "
        "Trades signalled on or after 2026-09-15 are the only fully out-of-sample ones."
    ),
    "Finnhub news covers only about the last year; earlier readings come from 8-K filings only.",
)
PASS_BAR_ROWS = (
```

In `src/signalbench/backtest/report.py`, replace:

```python
def pass_bar_payload(
```

with:

```python
def jev_payload(
    *,
    model_requested: str,
    question_set: str,
    readings: int,
    builds: dict[str, int],
    theta_block: float | None,
    information_only: bool,
    out_of_sample: TradeStats,
) -> dict[str, Any]:
    """Spec 03 facts stored under metrics["jev"] for Sentiment and --jev filter runs."""
    return {
        "model_requested": model_requested,
        "question_set": question_set,
        "readings": readings,
        "builds": dict(sorted(builds.items())),
        "theta_block": theta_block,
        "information_only": information_only,
        "out_of_sample_since": JEV_RELEASE.isoformat(),
        "out_of_sample": asdict(out_of_sample),
    }


def pass_bar_payload(
```

In `src/signalbench/backtest/report.py`, replace:

```python
def _header(run: BacktestRun) -> list[str]:
    lines = [
        f"# Backtest: {run.setup} (Jev {run.jev_mode})",
        "",
        f"**Strategy:** {run.strategy_version} · **Result:** {'PASS' if run.passed else 'FAIL'}",
        "",
    ]
```

with:

```python
def _header(run: BacktestRun) -> list[str]:
    result = "PASS" if run.passed else "FAIL"
    if run.jev_mode == "filter":
        result += " (information only)"
    lines = [
        f"# Backtest: {run.setup} (Jev {run.jev_mode})",
        "",
        f"**Strategy:** {run.strategy_version} · **Result:** {result}",
        "",
    ]
```

In `src/signalbench/backtest/report.py`, replace:

```python
    if run.setup == "combined":
        lines += ["**Information only:** a combined run does not change pass or fail.", ""]
```

with:

```python
    if run.setup == "combined":
        lines += ["**Information only:** a combined run does not change pass or fail.", ""]
    jev: dict[str, Any] | None = run.metrics.get("jev")
    if jev is not None and run.jev_mode == "filter":
        lines += [
            (
                "**Information only — cannot change the v1 result.** The spec 02 Jev-off v1 "
                f"result stands; this run applies the Jev filter at theta {jev['theta_block']} "
                "from `data/jev_filter_v1.yaml` (spec 03)."
            ),
            "",
        ]
    if jev is not None and run.setup == "sentiment":
        lines += [f"**Sentiment status:** {_sentiment_status(run, jev)}", "", _period(run), ""]
```

In `src/signalbench/backtest/report.py`, replace:

```python
def _pass_bar(run: BacktestRun) -> list[str]:
```

with:

```python
def _sentiment_status(run: BacktestRun, jev: dict[str, Any]) -> str:
    if run.passed:
        return "PASS: the Sentiment setup may go live (spec 03)"
    if jev["information_only"]:
        return (
            "information only (fewer than 30 trades): live messages may mention positive "
            "documents, but no Sentiment entries are sent (spec 03)"
        )
    return "FAIL: no Sentiment entries are sent (spec 03)"


def _period(run: BacktestRun) -> str:
    return (
        f"**Period (spec 03):** {run.start_date.isoformat()} to {run.end_date.isoformat()}; the "
        f"halves split at the calendar midpoint (H1 entries to {run.metrics['h1_end']}, H2 "
        f"from {run.metrics['h2_start']})."
    )


def _jev(m: dict[str, Any]) -> list[str]:
    jev: dict[str, Any] | None = m.get("jev")
    if jev is None:
        return []
    builds = ", ".join(f"{build} ({count})" for build, count in jev["builds"].items())
    theta = "off" if jev["theta_block"] is None else jev["theta_block"]
    oos = jev["out_of_sample"]
    return [
        "## Jev readings",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Model requested | {jev['model_requested']} |",
        f"| Question set | {jev['question_set']} |",
        f"| Readings used | {jev['readings']} |",
        f"| Resolved builds | {builds or 'none'} |",
        f"| Filter theta_block | {theta} |",
        "",
        f"## Trades signalled on or after {jev['out_of_sample_since']} (out of sample for Jev)",
        "",
        (
            f"Trades {oos['trades']} · win rate {_pct(oos['win_rate'])} · "
            f"mean R {oos['mean_r']:.3f} · median R {oos['median_r']:.3f}"
        ),
    ]


def _pass_bar(run: BacktestRun) -> list[str]:
```

In `src/signalbench/backtest/report.py`, replace:

```python
        *[f"- {caveat}" for caveat in CAVEATS],
    ]
```

with:

```python
        *[f"- {caveat}" for caveat in CAVEATS],
        *([f"- {caveat}" for caveat in JEV_CAVEATS] if "jev" in m else []),
    ]
```

In `src/signalbench/backtest/report.py`, replace:

```python
        _metrics(run.metrics),
        _benchmarks(run.metrics),
```

with:

```python
        _metrics(run.metrics),
        _jev(run.metrics),
        _benchmarks(run.metrics),
```

In `src/signalbench/backtest/report.py`, replace:

```python
    return "\n\n".join("\n".join(section) for section in sections) + "\n"
```

with:

```python
    return "\n\n".join("\n".join(section) for section in sections if section) + "\n"
```

(`_jev` is empty for Jev-off runs; skipping empty sections keeps those reports byte-identical to spec 02's.)

- [ ] **Step 5: Run the tests and checks**

Run: `uv run pytest tests/test_determinism.py tests/test_report.py -v && uv run mypy src && uv run ruff check .`
Expected: 16 passed (4 new); mypy and ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/signalbench/backtest/fingerprint.py src/signalbench/backtest/report.py tests/test_determinism.py tests/test_report.py
git commit -m "feat: Jev readings in the data fingerprint and the report's Jev sections

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Runner and `backtest run` — Sentiment and `--jev filter`

**Files:**
- Modify: `src/signalbench/backtest/runner.py`, `src/signalbench/cli.py`, `tests/test_runner.py`, `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_runner.py`, replace:

```python
from datetime import date, datetime
```

with:

```python
from datetime import UTC, date, datetime, time
```

In `tests/test_runner.py`, replace:

```python
from signalbench.backtest.runner import (
    RequiresSpec03Error,
    load_market_inputs,
    run_backtest,
    setups_for_run,
)
from signalbench.db.models import BacktestRun, EarningsEvent, Price, Ticker, TickerKind
```

with:

```python
from signalbench.backtest.runner import (
    JevInputs,
    UnsupportedRunError,
    load_market_inputs,
    run_backtest,
    sentiment_params,
    setups_for_run,
)
from signalbench.db.models import (
    BacktestRun,
    DocType,
    DocumentTicker,
    EarningsEvent,
    JevReading,
    Price,
    RawDocument,
    Ticker,
    TickerKind,
)
```

In `tests/test_runner.py`, replace:

```python
def test_sentiment_and_jev_filter_need_spec_03() -> None:
    with pytest.raises(RequiresSpec03Error, match="spec 03"):
        setups_for_run("sentiment", "off")
    with pytest.raises(RequiresSpec03Error, match="spec 03"):
        setups_for_run("pullback", "filter")
    assert setups_for_run("combined", "off") == ("pullback", "breakout")
```

with:

```python
def test_setups_for_each_run() -> None:
    assert setups_for_run("sentiment", "off") == ("sentiment",)
    assert setups_for_run("pullback", "filter") == ("pullback",)
    assert setups_for_run("combined", "off") == ("pullback", "breakout")
    with pytest.raises(UnsupportedRunError, match="--setup sentiment runs with --jev off"):
        setups_for_run("sentiment", "filter")


def test_sentiment_runs_from_2016_with_halves_split_at_the_calendar_midpoint() -> None:
    params = sentiment_params(load_test_config().backtest, date(2026, 9, 24))
    assert (params.start, params.h1_end, params.h2_start) == (
        date(2016, 1, 1), date(2021, 5, 13), date(2021, 5, 14),
    )
    assert params.recent_since == date(2026, 9, 15)
    assert (params.min_trades, params.min_mean_r) == (30, 0.10)  # the same pass bar
```

(3,919 days separate 2016-01-01 and 2026-09-24; half of that, 1,959 days, lands on 2021-05-13.)

Append to `tests/test_runner.py`:

```python
def _reading(
    session: Session, symbol: str, published: datetime, *, p_negative: float, p_positive: float
) -> None:
    ticker = session.exec(select(Ticker).where(Ticker.symbol == symbol)).one()
    document = RawDocument(
        source="finnhub", external_id=f"{symbol}-{published.isoformat()}", doc_type=DocType.news,
        raw_text="x", text="x", published_at=published,
    )
    session.add(document)
    session.flush()
    session.add(DocumentTicker(document_id=document.id, ticker_id=ticker.id))
    session.add(
        JevReading(
            document_id=document.id, ticker_id=ticker.id, model_requested="typesafe/jev-1.13",
            model_resolved="typesafe/jev-1.13-20260917", question_set="q1", response_id="r",
            p_negative=p_negative, p_neutral=1.0 - p_negative - p_positive,
            p_positive=p_positive, event_type="product", p_routine=0.1, answers={},
            input_tokens=100, cost_usd=0.0, latency_ms=1,
        )
    )
    session.commit()


def _morning(day: date) -> datetime:
    return datetime.combine(day, time(10, 0), tzinfo=NEW_YORK)  # legal close: that session


SENTIMENT_DAYS = weekdays(date(2015, 1, 1), 300)  # SENTIMENT_DAYS[261] is Friday 2016-01-01
TRIGGER = 270


@pytest.fixture
def sentiment_seeded(session: Session) -> Session:
    _store(session, "AAA", TickerKind.us_stock, trend_bars(SENTIMENT_DAYS, 100.0, 1.5))
    _store(session, "BBB", TickerKind.us_stock, trend_bars(SENTIMENT_DAYS, 100.0, 0.1))
    _store(session, "QQQ", TickerKind.benchmark, trend_bars(SENTIMENT_DAYS, 300.0, 0.5))
    return session


def _jev_run(
    session: Session, tmp_path: Path, setup: str, jev_mode: str, theta: float | None
) -> tuple[BacktestRun, Path]:
    return run_backtest(
        session,
        setup=setup,  # type: ignore[arg-type]
        jev_mode=jev_mode,  # type: ignore[arg-type]
        config=load_test_config(),
        config_sha256="f" * 64,
        universe=UNIVERSE,
        calendar=WeekdaySessions(),
        git_sha="abc123",
        run_date=date(2026, 9, 24),
        reports_dir=tmp_path,
        now=LATER,
        jev=JevInputs(theta_block=theta),
    )


def test_a_sentiment_run_trades_positive_readings_from_2016(
    sentiment_seeded: Session, tmp_path: Path
) -> None:
    _reading(
        sentiment_seeded, "AAA", _morning(SENTIMENT_DAYS[TRIGGER]), p_negative=0.05, p_positive=0.8
    )
    run, path = _jev_run(sentiment_seeded, tmp_path, "sentiment", "off", None)
    assert (run.setup, run.jev_mode) == ("sentiment", "off")
    assert run.start_date == date(2016, 1, 1)
    first = run.trade_log["trades"][0]
    assert (first["setup"], first["symbol"]) == ("sentiment", "AAA")
    assert first["signal_date"] == SENTIMENT_DAYS[TRIGGER].isoformat()
    midpoint = sentiment_params(load_test_config().backtest, SENTIMENT_DAYS[-1]).h1_end
    assert run.metrics["h1_end"] == midpoint.isoformat()
    jev = run.metrics["jev"]
    assert (jev["readings"], jev["theta_block"], jev["information_only"]) == (1, None, True)
    assert jev["builds"] == {"typesafe/jev-1.13-20260917": 1}
    stored = {
        "AAA": trend_bars(SENTIMENT_DAYS, 100.0, 1.5),
        "BBB": trend_bars(SENTIMENT_DAYS, 100.0, 0.1),
        "QQQ": trend_bars(SENTIMENT_DAYS, 300.0, 0.5),
    }
    assert run.data_fingerprint != data_fingerprint(stored.items(), [])  # readings are covered
    assert path.name == "2026-09-24-sentiment-off.md"
    assert "**Sentiment status:** information only" in path.read_text(encoding="utf-8")


def test_a_sentiment_run_without_readings_is_refused(
    sentiment_seeded: Session, tmp_path: Path
) -> None:
    with pytest.raises(RunRefusedError, match="No Jev readings"):
        _jev_run(sentiment_seeded, tmp_path, "sentiment", "off", None)
    assert sentiment_seeded.exec(select(BacktestRun)).all() == []


def test_a_filtered_run_blocks_a_negative_reading(seeded: Session, tmp_path: Path) -> None:
    _reading(seeded, "AAA", _morning(DAYS[DIP - 2]), p_negative=0.9, p_positive=0.02)
    run, path = _jev_run(seeded, tmp_path, "pullback", "filter", 0.7)
    assert run.jev_mode == "filter"
    assert DAYS[DIP].isoformat() not in [t["signal_date"] for t in run.trade_log["trades"]]
    blocked = [
        e for e in run.trade_log["events"] if e["event"] == "skip" and e["reason"] == "blocked"
    ]
    assert blocked[0]["date"] == DAYS[DIP].isoformat()
    assert run.metrics["jev"]["theta_block"] == 0.7
    assert path.name == "2026-09-24-pullback-filter.md"
    assert "cannot change the v1 result" in path.read_text(encoding="utf-8")


def test_a_filter_run_needs_theta(seeded: Session, tmp_path: Path) -> None:
    _reading(seeded, "AAA", _morning(DAYS[DIP - 2]), p_negative=0.9, p_positive=0.02)
    with pytest.raises(RunRefusedError, match="information-only"):
        _jev_run(seeded, tmp_path, "pullback", "filter", None)


def test_a_reading_after_the_run_is_not_used(seeded: Session, tmp_path: Path) -> None:
    after = datetime.combine(DAYS[-1], time(16, 30), tzinfo=NEW_YORK).astimezone(UTC)
    _reading(seeded, "AAA", after, p_negative=0.9, p_positive=0.02)
    with pytest.raises(RunRefusedError, match="No Jev readings"):
        _jev_run(seeded, tmp_path, "pullback", "filter", 0.7)
```

In `tests/test_cli.py`, replace:

```python
from signalbench.cli import app
```

with:

```python
from signalbench.backtest.runner import JevInputs
from signalbench.cli import app
```

In `tests/test_cli.py`, replace:

```python
@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--setup", "sentiment"], "--setup sentiment requires Jev readings (spec 03)."),
        (["--setup", "pullback", "--jev", "filter"], "--jev filter requires Jev readings (spec 03)."),
    ],
)
def test_backtest_run_refuses_spec_03_modes(args: list[str], message: str) -> None:
    result = runner.invoke(app, ["backtest", "run", *args])
    assert result.exit_code == 2
    assert message in result.stderr
```

with:

```python
def test_backtest_run_refuses_sentiment_with_the_jev_filter() -> None:
    result = runner.invoke(app, ["backtest", "run", "--setup", "sentiment", "--jev", "filter"])
    assert result.exit_code == 2
    assert "--setup sentiment runs with --jev off" in result.stderr
```

Append to `tests/test_cli.py`:

```python
def _capture_run(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> dict[str, object]:
    calls: dict[str, object] = {}

    def fake_run(_session: Session, **kwargs: object) -> tuple[BacktestRun, Path]:
        calls.update(kwargs)
        run = _stored_run(session)
        run.jev_mode = str(kwargs["jev_mode"])
        return run, tmp_path / "report.md"

    monkeypatch.setattr(cli, "get_session", lambda: session)
    monkeypatch.setattr(cli, "git_sha", lambda _repo: "abc123")
    monkeypatch.setattr(cli, "NyseSessions", lambda: "calendar")
    monkeypatch.setattr(cli, "run_backtest", fake_run)
    return calls


def test_backtest_run_sentiment_reads_jev_without_a_filter(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _registered_config(monkeypatch, tmp_path)
    calls = _capture_run(session, monkeypatch, tmp_path)
    result = runner.invoke(app, ["backtest", "run", "--setup", "sentiment", "--config", str(config)])
    assert result.exit_code == 0, result.stderr
    assert (calls["setup"], calls["jev_mode"]) == ("sentiment", "off")
    assert calls["jev"] == JevInputs(theta_block=None)


def _filter_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str | None) -> Path:
    path = tmp_path / "data" / "jev_filter_v1.yaml"
    if body is not None:
        path.write_text(body, encoding="utf-8")
    monkeypatch.setattr(cli, "JEV_FILTER_PATH", path)
    return path


FILTER_ON = "mode: 'on'\ntheta_block: 0.7\nquestion_set: q1\nmodel_requested: typesafe/jev-1.13\n"
FILTER_INFO = (
    "mode: information_only\ntheta_block: null\nquestion_set: q1\n"
    "model_requested: typesafe/jev-1.13\n"
)


def test_backtest_run_filter_uses_the_committed_theta(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _registered_config(monkeypatch, tmp_path)
    _filter_file(monkeypatch, tmp_path, FILTER_ON)
    calls = _capture_run(session, monkeypatch, tmp_path)
    result = runner.invoke(
        app, ["backtest", "run", "--setup", "breakout", "--jev", "filter", "--config", str(config)]
    )
    assert result.exit_code == 0, result.stderr
    assert calls["jev_mode"] == "filter"
    assert calls["jev"] == JevInputs(theta_block=0.7)
    assert "FAIL (information only: the Jev-off v1 result stands)" in result.stdout


@pytest.mark.parametrize(
    ("body", "committed", "message"),
    [
        (None, True, "jev_filter_v1.yaml not found"),
        (FILTER_ON, False, "jev_filter_v1.yaml must be committed, unchanged"),
        (FILTER_INFO, True, "information-only"),
        ("mode: maybe\n", True, "mode must be"),
    ],
)
def test_backtest_run_filter_refusals(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    body: str | None,
    committed: bool,
    message: str,
) -> None:
    config = _registered_config(monkeypatch, tmp_path)
    path = _filter_file(monkeypatch, tmp_path, body)
    monkeypatch.setattr(
        cli, "committed_unchanged", lambda _repo, target: committed or target != path
    )
    calls = _capture_run(session, monkeypatch, tmp_path)
    result = runner.invoke(
        app, ["backtest", "run", "--setup", "pullback", "--jev", "filter", "--config", str(config)]
    )
    assert result.exit_code == 1
    assert message in result.stderr
    assert calls == {}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_runner.py tests/test_cli.py -v`
Expected: FAIL. Collection stops with `ImportError: cannot import name 'JevInputs' from 'signalbench.backtest.runner'` (both files import it).

- [ ] **Step 3: Implement the runner**

In `src/signalbench/backtest/runner.py`, replace:

```python
"""Load real data, run one backtest, store it, and write its report (spec 02)."""
```

with:

```python
"""Load real data, run one backtest, store it, and write its report (specs 02 and 03)."""
```

In `src/signalbench/backtest/runner.py`, replace:

```python
from datetime import date, datetime, time
```

with:

```python
from datetime import date, datetime, time, timedelta
```

In `src/signalbench/backtest/runner.py`, replace:

```python
from signalbench.backtest.metrics import run_metrics
```

with:

```python
from signalbench.backtest.metrics import run_metrics, trade_stats
```

In `src/signalbench/backtest/runner.py`, replace:

```python
from signalbench.backtest.report import (
    metrics_payload,
```

with:

```python
from signalbench.backtest.report import (
    jev_payload,
    metrics_payload,
```

In `src/signalbench/backtest/runner.py`, replace:

```python
from signalbench.market.bars import AdjustedBar, adjusted_bars
from signalbench.market.calendar import Sessions
from signalbench.strategy.config import SetupName, StrategyConfig
from signalbench.strategy.market_view import MarketView, SymbolInput
from signalbench.strategy.readings import NullReadingsView
```

with:

```python
from signalbench.jev.questions import JEV_RELEASE, MODEL, QUESTION_SET
from signalbench.jev.store import load_document_readings, reading_builds
from signalbench.market.bars import AdjustedBar, adjusted_bars
from signalbench.market.calendar import HISTORY_START, Sessions
from signalbench.market.legal_close import LegalCloses
from signalbench.strategy.config import BacktestParams, SetupName, StrategyConfig
from signalbench.strategy.market_view import MarketView, SymbolInput
from signalbench.strategy.readings import (
    DocumentReading,
    JevReadingsView,
    NullReadingsView,
    ReadingsView,
)
```

In `src/signalbench/backtest/runner.py`, replace:

```python
SURVIVOR_BENCHMARK = "Survivor benchmark (equal weight, not rebalanced)"


class RequiresSpec03Error(ValueError):
    """The Sentiment setup and the Jev filter need Jev readings, which arrive in spec 03."""


def setups_for_run(setup: RunSetup, jev_mode: JevMode) -> tuple[SetupName, ...]:
    if setup == "sentiment":
        raise RequiresSpec03Error("--setup sentiment requires Jev readings (spec 03).")
    if jev_mode == "filter":
        raise RequiresSpec03Error("--jev filter requires Jev readings (spec 03).")
    return SETUPS_BY_RUN[setup]
```

with:

```python
SURVIVOR_BENCHMARK = "Survivor benchmark (equal weight, not rebalanced)"
SENTIMENT_START = date(2016, 1, 1)  # spec 03: filings are backfilled from 2016


class UnsupportedRunError(ValueError):
    """A setup and Jev mode that never run together. Refused before any data is read."""


def setups_for_run(setup: RunSetup, jev_mode: JevMode) -> tuple[SetupName, ...]:
    if setup == "sentiment":
        if jev_mode == "filter":
            raise UnsupportedRunError(
                "--setup sentiment runs with --jev off: Sentiment uses readings as its trigger, "
                "and the filter decision covers Pullback and Breakout only (spec 03)."
            )
        return ("sentiment",)
    return SETUPS_BY_RUN[setup]


def sentiment_params(params: BacktestParams, last: date) -> BacktestParams:
    """Spec 03: the Sentiment backtest runs from 2016-01-01 to the end, with the halves split at
    the calendar midpoint of that period. The pass bar itself is unchanged."""
    midpoint = SENTIMENT_START + timedelta(days=(last - SENTIMENT_START).days // 2)
    return replace(
        params, start=SENTIMENT_START, h1_end=midpoint, h2_start=midpoint + timedelta(days=1)
    )


@dataclass(frozen=True)
class JevInputs:
    """What a Sentiment run or a --jev filter run needs from spec 03."""

    theta_block: float | None  # the committed filter theta for --jev filter runs, else None
    model_requested: str = MODEL
    question_set: str = QUESTION_SET
```

In `src/signalbench/backtest/runner.py`, replace:

```python
    now: datetime,
    end: date | None = None,
) -> tuple[BacktestRun, Path]:
    """Run one setup (or the combined set) with Jev off, store it, and write the report.

    Refuses (RunRefusedError) when stored runs of this strategy version used another config, or
    when a universe series does not end on the run's last session. `now` is the clock: today's
    bars are dropped as partial before 16:15 New York time.
    """
    config = config.with_setups(setups_for_run(setup, jev_mode))
    check_version_unchanged(session, config.version, config_sha256)
```

with:

```python
    now: datetime,
    end: date | None = None,
    jev: JevInputs | None = None,
) -> tuple[BacktestRun, Path]:
    """Run one setup (or the combined set), store it, and write the report.

    Refuses (RunRefusedError) when stored runs of this strategy version used another config,
    when a universe series does not end on the run's last session, when a Sentiment or
    --jev filter run finds no Jev readings, or when --jev filter has no theta (the filter is
    information-only). `now` is the clock: today's bars are dropped as partial before 16:15
    New York time. `jev` is required for Sentiment and --jev filter runs.
    """
    config = config.with_setups(setups_for_run(setup, jev_mode))
    reads_jev = setup == "sentiment" or jev_mode == "filter"
    if reads_jev and jev is None:
        raise ValueError(f"--setup {setup} --jev {jev_mode} needs JevInputs (spec 03)")
    if jev_mode == "filter" and (jev is None or jev.theta_block is None):
        raise RunRefusedError(
            "The Jev filter is information-only (data/jev_filter_v1.yaml), so --jev filter runs "
            "are refused (spec 03)."
        )
    check_version_unchanged(session, config.version, config_sha256)
```

In `src/signalbench/backtest/runner.py`, replace:

```python
    last = latest_bar_date(inputs) if end is None else end
    start = config.backtest.start
```

with:

```python
    last = latest_bar_date(inputs) if end is None else end
    if setup == "sentiment":
        config = replace(config, backtest=sentiment_params(config.backtest, last))
    start = config.backtest.start
```

In `src/signalbench/backtest/runner.py`, replace:

```python
    market = MarketView(inputs.symbols, inputs.benchmark, sessions, config)
    result = simulate(market, NullReadingsView(), config, start, last)
    metrics = run_metrics(result, config.backtest)
```

with:

```python
    market = MarketView(inputs.symbols, inputs.benchmark, sessions, config)
    readings: ReadingsView = NullReadingsView()
    documents: dict[str, list[DocumentReading]] | None = None
    if reads_jev and jev is not None:
        documents = load_document_readings(
            session,
            [item.symbol for item in inputs.symbols],
            LegalCloses(calendar.session_closes(HISTORY_START, last)),
            model=jev.model_requested,
            question_set=jev.question_set,
        )
        if not any(documents.values()):
            raise RunRefusedError(
                f"No Jev readings ({jev.model_requested}, {jev.question_set}) with a legal close "
                f"by {last.isoformat()}. Run `signalbench jev backfill` first (spec 03)."
            )
        readings = JevReadingsView(
            documents,
            calendar.sessions_between(HISTORY_START, last),
            block_theta=jev.theta_block if jev_mode == "filter" else None,
        )
    result = simulate(market, readings, config, start, last)
    metrics = run_metrics(result, config.backtest)
```

In `src/signalbench/backtest/runner.py`, replace:

```python
    bar = evaluate_pass_bar(metrics, qqq.sharpe, config.backtest)
    run = BacktestRun(
```

with:

```python
    bar = evaluate_pass_bar(metrics, qqq.sharpe, config.backtest)
    payload = metrics_payload(metrics, [qqq, survivor], config.backtest)
    if documents is not None and jev is not None:
        payload["jev"] = jev_payload(
            model_requested=jev.model_requested,
            question_set=jev.question_set,
            readings=sum(len(rows) for rows in documents.values()),
            builds=reading_builds(session, model=jev.model_requested, question_set=jev.question_set),
            theta_block=jev.theta_block if jev_mode == "filter" else None,
            information_only=jev_mode == "filter" or metrics.trades < config.backtest.min_trades,
            out_of_sample=trade_stats([t for t in result.trades if t.signal_date >= JEV_RELEASE]),
        )
    run = BacktestRun(
```

In `src/signalbench/backtest/runner.py`, replace:

```python
            [(item.symbol, day) for item in inputs.symbols for day in item.earnings],
        ),
        metrics=metrics_payload(metrics, [qqq, survivor], config.backtest),
```

with:

```python
            [(item.symbol, day) for item in inputs.symbols for day in item.earnings],
            None
            if documents is None
            else [(symbol, row) for symbol, rows in documents.items() for row in rows],
        ),
        metrics=payload,
```

The Sentiment override replaces only `backtest.start`, `h1_end`, and `h2_start` on the in-memory config; the file, its `config_sha256`, and every pre-registration check are untouched.

- [ ] **Step 4: Wire `backtest run`**

In `src/signalbench/cli.py`, replace:

```python
from signalbench.backtest.runner import (
    JevMode,
    RequiresSpec03Error,
    RunSetup,
    run_backtest,
    setups_for_run,
)
```

with:

```python
from signalbench.backtest.runner import (
    JevInputs,
    JevMode,
    RunSetup,
    UnsupportedRunError,
    run_backtest,
    setups_for_run,
)
```

In `src/signalbench/cli.py`, replace:

```python
from signalbench.ingest.stats import collect_stats
```

with:

```python
from signalbench.ingest.stats import collect_stats
from signalbench.jev.filter_record import FilterFileError, load_filter_setting
```

In `src/signalbench/cli.py`, replace:

```python
SPREAD_SURVEY_PATH = REPO_ROOT / "data" / "cdr_spread_survey.yaml"
```

with:

```python
SPREAD_SURVEY_PATH = REPO_ROOT / "data" / "cdr_spread_survey.yaml"
JEV_FILTER_PATH = REPO_ROOT / "data" / "jev_filter_v1.yaml"
```

In `src/signalbench/cli.py`, replace:

```python
    except RequiresSpec03Error as error:
```

with:

```python
    except UnsupportedRunError as error:
```

In `src/signalbench/cli.py`, replace:

```python
        message = " ".join(f"{config.name}: {error}".split())  # YAML errors span lines
        typer.echo(message, err=True)
        raise typer.Exit(1) from None
```

with:

```python
        message = " ".join(f"{config.name}: {error}".split())  # YAML errors span lines
        typer.echo(message, err=True)
        raise typer.Exit(1) from None
    jev_inputs: JevInputs | None = None
    if jev_mode == "filter":
        jev_inputs = _jev_filter_inputs()
    elif run_setup == "sentiment":
        jev_inputs = JevInputs(theta_block=None)
```

In `src/signalbench/cli.py`, replace:

```python
                reports_dir=REPORTS_DIR,
                now=now,
            )
```

with:

```python
                reports_dir=REPORTS_DIR,
                now=now,
                jev=jev_inputs,
            )
```

In `src/signalbench/cli.py`, replace:

```python
    typer.echo(f"run {run.id}: {'PASS' if run.passed else 'FAIL'}")
```

with:

```python
    result = "PASS" if run.passed else "FAIL"
    if run.jev_mode == "filter":
        result += " (information only: the Jev-off v1 result stands)"
    typer.echo(f"run {run.id}: {result}")
```

In `src/signalbench/cli.py`, replace:

```python
def _print_run(run: BacktestRun) -> None:
```

with:

```python
def _jev_filter_inputs() -> JevInputs:
    """The committed filter decision; --jev filter runs only when it is ON (spec 03)."""
    if not JEV_FILTER_PATH.exists():
        typer.echo(
            f"{JEV_FILTER_PATH.name} not found. Run `signalbench jev fit-filter --write` and "
            "commit its output first (spec 03).",
            err=True,
        )
        raise typer.Exit(1)
    if not committed_unchanged(REPO_ROOT, JEV_FILTER_PATH):
        typer.echo(
            f"{JEV_FILTER_PATH.name} must be committed, unchanged, before a --jev filter run "
            "(spec 03).",
            err=True,
        )
        raise typer.Exit(1)
    try:
        setting = load_filter_setting(JEV_FILTER_PATH)
    except (FilterFileError, yaml.YAMLError) as error:
        typer.echo(" ".join(str(error).split()), err=True)
        raise typer.Exit(1) from None
    if setting.mode != "on":
        typer.echo(
            "The Jev filter is information-only (data/jev_filter_v1.yaml), so --jev filter runs "
            "are refused (spec 03).",
            err=True,
        )
        raise typer.Exit(1)
    return JevInputs(
        theta_block=setting.theta_block,
        model_requested=setting.model_requested,
        question_set=setting.question_set,
    )


def _print_run(run: BacktestRun) -> None:
```

- [ ] **Step 5: Run the tests and checks**

Run: `uv run pytest tests/test_runner.py tests/test_cli.py -v && uv run pytest -q && uv run mypy src && uv run ruff check .`
Expected: 50 passed (19 runner, 31 CLI); the full suite 401 passed; mypy and ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/signalbench/backtest/runner.py src/signalbench/cli.py tests/test_runner.py tests/test_cli.py
git commit -m "feat: Sentiment backtest and information-only --jev filter runs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: `signalbench jev` commands

**Files:**
- Create: `src/signalbench/jev/fixture.py`, `tests/test_jev_cli.py`
- Modify: `src/signalbench/cli.py`

- [ ] **Step 1: Write the failing test**

`tests/test_jev_cli.py`:

```python
import re
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlmodel import Session, select
from typer.testing import CliRunner

from signalbench import cli
from signalbench.cli import app
from signalbench.db.models import (
    BacktestRun,
    DocType,
    DocumentTicker,
    JevReading,
    Price,
    RawDocument,
    Ticker,
    TickerKind,
)
from signalbench.ingest.cdr import CdrEntry
from signalbench.jev.client import JevFatalError
from signalbench.jev.fake import FakeJevClient, fake_result
from signalbench.jev.fixture import FIXTURE_TEXT, fixture_state
from strategy_helpers import WeekdaySessions

runner = CliRunner()
NEW_YORK = ZoneInfo("America/New_York")
UNIVERSE = [
    CdrEntry("AAA", "ZAAA", "ZAAA.NE", "Aaa", "Information Technology"),
    CdrEntry("BBB", "ZBBB", "ZBBB.NE", "Bbb", "Energy"),
]


def test_jev_help_lists_every_command() -> None:
    result = runner.invoke(app, ["jev", "--help"])
    assert result.exit_code == 0
    for command in ("test", "backfill", "fit-filter", "calibration"):
        assert command in result.stdout


def test_the_fixture_is_a_made_up_8k_without_dates() -> None:
    assert fixture_state().startswith(
        "Company: Example Holdings (EXMP)\nSource: SEC 8-K, items 2.02, 9.01\n\n"
    )
    assert not re.search(r"\b(19|20)\d{2}\b", FIXTURE_TEXT)


@pytest.mark.parametrize("command", [["test"], ["backfill"]])
def test_commands_that_call_jev_need_the_key(
    monkeypatch: pytest.MonkeyPatch, command: list[str]
) -> None:
    monkeypatch.setattr(cli.settings, "openrouter_api_key", None)
    result = runner.invoke(app, ["jev", *command])
    assert result.exit_code == 1
    assert "OPENROUTER_API_KEY is not set in .env" in result.stderr


def _fake(monkeypatch: pytest.MonkeyPatch, client: FakeJevClient) -> None:
    monkeypatch.setattr(cli.settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(cli, "OpenRouterJevClient", lambda _key, _http: client)
    monkeypatch.setattr(cli, "JEV_CONCURRENCY", 1)  # one call at a time: exact budget stops


def test_jev_test_prints_answers_latency_cost_and_build(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeJevClient()
    _fake(monkeypatch, client)
    result = runner.invoke(app, ["jev", "test"])
    assert result.exit_code == 0, result.stderr
    assert client.calls == [fixture_state()]
    assert "model: typesafe/jev-1.13-20260917" in result.stdout
    assert "impact: negative 0.100 | neutral 0.200 | positive 0.700" in result.stdout
    assert "event_type: earnings | routine 0.100" in result.stdout
    assert "latency 5 ms | input tokens 480 | cost $0.000020" in result.stdout


def test_jev_test_reports_a_failed_call(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake(monkeypatch, FakeJevClient(lambda _state: JevFatalError("HTTP 401: bad key")))
    result = runner.invoke(app, ["jev", "test"])
    assert result.exit_code == 1
    assert "Jev call failed: HTTP 401: bad key" in result.stderr


def _news(session: Session, ticker: Ticker, external_id: str, published: datetime) -> RawDocument:
    document = RawDocument(
        source="finnhub", external_id=external_id, doc_type=DocType.news, raw_text="x",
        text=f"Headline {external_id}", published_at=published,
    )
    session.add(document)
    session.flush()
    session.add(DocumentTicker(document_id=document.id, ticker_id=ticker.id))
    session.commit()
    return document


@pytest.fixture
def news_seeded(session: Session, monkeypatch: pytest.MonkeyPatch) -> Session:
    aaa = Ticker(symbol="AAA", company_name="Aaa Corp", kind=TickerKind.us_stock)
    session.add(aaa)
    session.commit()
    for index in range(3):
        _news(session, aaa, f"n{index}", datetime(2026, 9, 21, 14, index, tzinfo=UTC))
    monkeypatch.setattr(cli, "get_session", lambda: session)
    monkeypatch.setattr(cli, "load_universe", lambda _path: UNIVERSE)
    return session


def test_backfill_reads_once_and_reports_counts(
    news_seeded: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeJevClient()
    _fake(monkeypatch, client)
    first = runner.invoke(app, ["jev", "backfill", "--source", "news"])
    assert first.exit_code == 0, first.stderr
    assert "to read: 3 (already read 0, no text 0, too long 0)" in first.stdout
    assert "read 3 | cost $0.0001 | input tokens 1440" in first.stdout
    assert "model typesafe/jev-1.13-20260917: 3" in first.stdout
    second = runner.invoke(app, ["jev", "backfill", "--source", "news"])
    assert "to read: 0 (already read 3" in second.stdout
    assert len(client.calls) == 3


def test_backfill_since_and_budget(news_seeded: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake(monkeypatch, FakeJevClient(lambda _state: fake_result(cost_usd=0.6)))
    none_left = runner.invoke(app, ["jev", "backfill", "--since", "2026-09-22"])
    assert "to read: 0" in none_left.stdout
    result = runner.invoke(app, ["jev", "backfill", "--max-cost-usd", "1.00"])
    assert result.exit_code == 0, result.stderr  # a budget stop is clean
    assert "budget reached: $1.2000 of $1.00" in result.stdout
    assert "Run again to continue." in result.stdout
    assert len(news_seeded.exec(select(JevReading)).all()) == 2


def test_backfill_stops_with_exit_1_on_a_fatal_error(
    news_seeded: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake(monkeypatch, FakeJevClient(lambda _state: JevFatalError("HTTP 402: no credits")))
    result = runner.invoke(app, ["jev", "backfill"])
    assert result.exit_code == 1
    assert "stopped: HTTP 402: no credits" in result.stderr


def _stored_run(session: Session, setup: str, trades: list[tuple[str, str, float]]) -> None:
    session.add(
        BacktestRun(
            strategy_version="v1", config_sha256="c" * 64, git_sha="abc123", setup=setup,
            jev_mode="off", start_date=date(2012, 1, 3), end_date=date(2026, 9, 23),
            data_fingerprint="d" * 64, metrics={}, pass_bar={}, passed=False,
            trade_log={
                "trades": [{"symbol": s, "signal_date": d, "r": r} for s, d, r in trades],
                "events": [],
            },
        )
    )
    session.commit()


def _negative_reading(session: Session, ticker: Ticker, day: date) -> None:
    document = _news(
        session, ticker, f"neg-{day}", datetime.combine(day, time(10, 0), tzinfo=NEW_YORK)
    )
    session.add(
        JevReading(
            document_id=document.id, ticker_id=ticker.id, model_requested="typesafe/jev-1.13",
            model_resolved="typesafe/jev-1.13-20260917", question_set="q1", response_id="r",
            p_negative=0.9, p_neutral=0.08, p_positive=0.02, event_type="legal", p_routine=0.1,
            answers={}, input_tokens=100, cost_usd=0.0, latency_ms=1,
        )
    )
    session.commit()


@pytest.fixture
def filter_seeded(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[Session, Path]:
    """AAA has a negative reading 1 session before its trades; BBB has none. Fit: 10 blocked
    at -1R vs 20 kept at +0.5R. Confirmation: 10 blocked at -0.5R vs 5 kept at +0.3R -> ON."""
    aaa = Ticker(symbol="AAA", company_name="Aaa Corp", kind=TickerKind.us_stock)
    bbb = Ticker(symbol="BBB", company_name="Bbb Corp", kind=TickerKind.us_stock)
    session.add_all([aaa, bbb])
    session.commit()
    _negative_reading(session, aaa, date(2019, 5, 31))
    _negative_reading(session, aaa, date(2024, 5, 31))
    _stored_run(
        session, "pullback",
        [("AAA", "2019-06-03", -1.0)] * 10 + [("AAA", "2024-06-03", -0.5)] * 10,
    )
    _stored_run(
        session, "breakout",
        [("BBB", "2019-06-03", 0.5)] * 20 + [("BBB", "2024-06-03", 0.3)] * 5,
    )
    monkeypatch.setattr(cli, "get_session", lambda: session)
    monkeypatch.setattr(cli, "load_universe", lambda _path: UNIVERSE)
    monkeypatch.setattr(cli, "NyseSessions", WeekdaySessions)
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cli, "JEV_FILTER_PATH", tmp_path / "data" / "jev_filter_v1.yaml")
    monkeypatch.setattr(cli, "JEV_REPORTS_DIR", tmp_path / "reports" / "jev")
    return session, tmp_path


def test_fit_filter_prints_the_decision_and_writes_nothing_by_default(
    filter_seeded: tuple[Session, Path],
) -> None:
    _, root = filter_seeded
    result = runner.invoke(app, ["jev", "fit-filter"])
    assert result.exit_code == 0, result.stderr
    assert "decision: on, theta 0.5" in result.stdout
    assert "fit theta 0.5: blocked 10, kept 20" in result.stdout
    assert "confirm theta 0.5: blocked 10, kept 5" in result.stdout
    assert "Nothing written. Run with --write" in result.stdout
    assert not (root / "data").exists() and not (root / "reports").exists()


def test_fit_filter_write_records_the_file_and_report_once(
    filter_seeded: tuple[Session, Path],
) -> None:
    _, root = filter_seeded
    result = runner.invoke(app, ["jev", "fit-filter", "--write"])
    assert result.exit_code == 0, result.stderr
    text = (root / "data" / "jev_filter_v1.yaml").read_text(encoding="utf-8")
    assert "mode: 'on'" in text and "theta_block: 0.5" in text
    [report] = list((root / "reports" / "jev").glob("*-filter-decision.md"))
    assert f"report: reports/jev/{report.name}" in text
    assert report.read_text(encoding="utf-8").startswith("# Jev filter decision (spec 03)")
    again = runner.invoke(app, ["jev", "fit-filter", "--write"])
    assert again.exit_code == 1
    assert "already exists" in again.stderr


def test_fit_filter_needs_the_stored_runs(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "get_session", lambda: session)
    result = runner.invoke(app, ["jev", "fit-filter"])
    assert result.exit_code == 1
    assert "No stored v1 jev-off run for pullback, breakout" in result.stderr


def _price(session: Session, ticker: Ticker, day: date, close: float) -> None:
    value = Decimal(str(close))
    session.add(
        Price(ticker_id=ticker.id, date=day, open=value, high=value, low=value, close=value,
              adj_close=value, volume=1_000)
    )


def test_calibration_writes_the_report(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    aaa = Ticker(symbol="AAA", company_name="Aaa Corp", kind=TickerKind.us_stock)
    qqq = Ticker(symbol="QQQ", company_name="QQQ", kind=TickerKind.benchmark)
    session.add_all([aaa, qqq])
    session.commit()
    days = WeekdaySessions().sessions_between(date(2024, 5, 27), date(2024, 6, 14))
    for day in days:
        _price(session, aaa, day, 90.0 if day > date(2024, 5, 31) else 100.0)  # -10% after
        _price(session, qqq, day, 100.0)
    session.commit()
    _negative_reading(session, aaa, date(2024, 5, 31))
    monkeypatch.setattr(cli, "get_session", lambda: session)
    monkeypatch.setattr(cli, "load_universe", lambda _path: UNIVERSE)
    monkeypatch.setattr(cli, "NyseSessions", WeekdaySessions)
    monkeypatch.setattr(cli, "JEV_REPORTS_DIR", tmp_path)
    result = runner.invoke(app, ["jev", "calibration"])
    assert result.exit_code == 0, result.stderr
    [report] = list(tmp_path.glob("*-calibration.md"))
    text = report.read_text(encoding="utf-8")
    assert "| Labeled | 1 |" in text
    assert "Labels: down 1, flat 0, up 0." in text
    assert "labeled 1 | not labeled 0" in result.stdout


def test_calibration_without_readings_is_refused(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "get_session", lambda: session)
    monkeypatch.setattr(cli, "load_universe", lambda _path: UNIVERSE)
    monkeypatch.setattr(cli, "NyseSessions", WeekdaySessions)
    result = runner.invoke(app, ["jev", "calibration"])
    assert result.exit_code == 1
    assert "No Jev readings" in result.stderr
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_jev_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'signalbench.jev.fixture'`.

- [ ] **Step 3: Write the fixture 8-K**

`src/signalbench/jev/fixture.py`:

```python
"""A small made-up 8-K for `signalbench jev test`: no real company, no dates, public-style text."""

from signalbench.jev.questions import build_state

FIXTURE_COMPANY = "Example Holdings"
FIXTURE_SYMBOL = "EXMP"
FIXTURE_ITEMS = "2.02,9.01"
FIXTURE_TEXT = (
    "Item 2.02 Results of Operations and Financial Condition.\n"
    "Example Holdings Inc. announced results for its fiscal quarter. Revenue grew 18 percent "
    "from the prior-year quarter to a record level, operating margin widened, and the company "
    "raised its full-year revenue and earnings outlook. The board also approved a new share "
    "repurchase program.\n\n"
    "Item 9.01 Financial Statements and Exhibits.\n"
    "Exhibit 99.1: Press release issued by Example Holdings Inc."
)


def fixture_state() -> str:
    return build_state(FIXTURE_COMPANY, FIXTURE_SYMBOL, "filings", FIXTURE_ITEMS, FIXTURE_TEXT)
```

- [ ] **Step 4: Add the commands**

In `src/signalbench/cli.py`, replace:

```python
from signalbench.jev.filter_record import FilterFileError, load_filter_setting
```

with:

```python
from signalbench.jev.backfill import (
    NEWS_DAILY_CAP,
    BackfillSource,
    pending_work,
    run_backfill,
)
from signalbench.jev.calibration import (
    BENCHMARK_SYMBOL,
    calibration_sections,
    render_calibration_report,
)
from signalbench.jev.client import JevError, OpenRouterJevClient
from signalbench.jev.filter import decide_filter, score_trades
from signalbench.jev.filter_record import (
    FilterFileError,
    FilterInputError,
    FilterRecord,
    load_filter_setting,
    load_v1_trades,
    render_filter_report,
    write_filter_file,
)
from signalbench.jev.fixture import fixture_state
from signalbench.jev.store import (
    calibration_samples,
    load_document_readings,
    reading_builds,
)
```

In `src/signalbench/cli.py`, replace:

```python
from signalbench.market.calendar import NyseSessions
```

with:

```python
from signalbench.market.calendar import HISTORY_START, NyseSessions
from signalbench.market.legal_close import LegalCloses
```

In `src/signalbench/cli.py`, replace:

```python
from signalbench.strategy.config import ConfigError, load_strategy_config
```

with:

```python
from signalbench.strategy.config import ConfigError, load_strategy_config
from signalbench.strategy.readings import JevReadingsView
```

In `src/signalbench/cli.py`, replace:

```python
REPORTS_DIR = REPO_ROOT / "reports" / "backtests"
```

with:

```python
REPORTS_DIR = REPO_ROOT / "reports" / "backtests"
JEV_REPORTS_DIR = REPO_ROOT / "reports" / "jev"
JEV_CONCURRENCY = 4
```

In `src/signalbench/cli.py`, replace:

```python
class JevChoice(str, Enum):
    off = "off"
    filter = "filter"
```

with:

```python
class JevChoice(str, Enum):
    off = "off"
    filter = "filter"


jev_app = typer.Typer(help="Jev reads filings and news (spec 03).")
app.add_typer(jev_app, name="jev")


class SourceChoice(str, Enum):
    filings = "filings"
    news = "news"
    all = "all"
```

Append to `src/signalbench/cli.py`:

```python
def _openrouter_key() -> str:
    if settings.openrouter_api_key is None:
        typer.echo("OPENROUTER_API_KEY is not set in .env", err=True)
        raise typer.Exit(1)
    return settings.openrouter_api_key


def _universe_symbols() -> list[str]:
    return [entry.us_symbol for entry in load_universe(UNIVERSE_PATH)]


@jev_app.command("test")
def jev_test() -> None:
    """One real call on a small made-up 8-K: prints the answers, latency, cost, and build."""
    key = _openrouter_key()
    with httpx.Client() as http:
        try:
            result = OpenRouterJevClient(key, http).read(fixture_state())
        except JevError as error:
            typer.echo(f"Jev call failed: {error}", err=True)
            raise typer.Exit(1) from None
    typer.echo(f"model: {result.model_resolved}")
    typer.echo(
        f"impact: negative {result.p_negative:.3f} | neutral {result.p_neutral:.3f} "
        f"| positive {result.p_positive:.3f}"
    )
    typer.echo(f"event_type: {result.event_type} | routine {result.p_routine:.3f}")
    typer.echo(
        f"latency {result.latency_ms} ms | input tokens {result.input_tokens} "
        f"| cost ${result.cost_usd:.6f}"
    )


@jev_app.command("backfill")
def jev_backfill(
    source: Annotated[
        SourceChoice, typer.Option("--source", help="Which documents to read.")
    ] = SourceChoice.all,
    since: Annotated[
        datetime | None,
        typer.Option("--since", formats=["%Y-%m-%d"], help="Only documents published from this day."),
    ] = None,
    max_cost_usd: Annotated[
        float, typer.Option("--max-cost-usd", min=0.0, help="Stop once this much is spent.")
    ] = 10.0,
) -> None:
    """Read every unread 8-K and news item once per universe ticker. Safe to rerun."""
    key = _openrouter_key()
    backfill_source: BackfillSource = source.value
    with get_session() as session, httpx.Client() as http:
        work = pending_work(
            session, _universe_symbols(), backfill_source, None if since is None else since.date()
        )
        typer.echo(
            f"to read: {len(work.jobs)} (already read {work.already_read}, "
            f"no text {work.no_text}, too long {work.too_long})"
        )
        if work.capped:
            typer.echo(
                f"news cap: {work.capped} items over {NEWS_DAILY_CAP} per symbol per New York "
                f"day not read ({work.capped_days} symbol-days)"
            )
        summary = run_backfill(
            session,
            work.jobs,
            OpenRouterJevClient(key, http),
            max_cost_usd=max_cost_usd,
            concurrency=JEV_CONCURRENCY,
            echo=typer.echo,
        )
    typer.echo(
        f"read {summary.read} | cost ${summary.cost_usd:.4f} | input tokens {summary.input_tokens}"
    )
    for build, count in sorted(summary.builds.items()):
        typer.echo(f"model {build}: {count}")
    if summary.skipped:
        typer.echo(f"skipped {len(summary.skipped)} documents (listed above)")
    if summary.budget_reached:
        typer.echo(f"{summary.stopped}; {summary.not_started} not started. Run again to continue.")
    elif summary.stopped is not None:
        typer.echo(f"stopped: {summary.stopped}; {summary.not_started} not started.", err=True)
        raise typer.Exit(1)


def _r(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


@jev_app.command("fit-filter")
def jev_fit_filter(
    write: Annotated[
        bool, typer.Option("--write", help="Write data/jev_filter_v1.yaml and the report.")
    ] = False,
) -> None:
    """The pre-registered filter decision (spec 03, steps 1-3). Writes nothing without --write."""
    calendar = NyseSessions()
    with get_session() as session:
        try:
            trades, runs = load_v1_trades(session)
        except FilterInputError as error:
            typer.echo(str(error), err=True)
            raise typer.Exit(1) from None
        last = max(run.end_date for run in runs)
        symbols = sorted(set(_universe_symbols()) | {trade.symbol for trade in trades})
        documents = load_document_readings(
            session, symbols, LegalCloses(calendar.session_closes(HISTORY_START, last))
        )
        builds = reading_builds(session)
    readings = sum(len(rows) for rows in documents.values())
    if readings == 0:
        typer.echo("No Jev readings. Run `signalbench jev backfill` first (spec 03).", err=True)
        raise typer.Exit(1)
    view = JevReadingsView(documents, calendar.sessions_between(HISTORY_START, last), None)
    decision = decide_filter(score_trades(trades, view.max_p_negative))
    typer.echo(f"decision: {decision.mode}, theta {decision.theta}")
    typer.echo(f"why: {decision.reason}")
    for row in decision.fit:
        typer.echo(
            f"fit theta {row.theta}: blocked {row.blocked}, kept {row.kept}, "
            f"kept - blocked {_r(row.difference)}"
        )
    check = decision.confirm
    if check is not None:
        typer.echo(
            f"confirm theta {check.theta}: blocked {check.blocked}, kept {check.kept}, "
            f"mean R blocked {_r(check.mean_r_blocked)}, kept {_r(check.mean_r_kept)}"
        )
    if not write:
        typer.echo("Nothing written. Run with --write to record the decision.")
        return
    today = datetime.now(NEW_YORK).date()
    report = JEV_REPORTS_DIR / f"{today.isoformat()}-filter-decision.md"
    record = FilterRecord(
        decision=decision,
        source_runs={run.setup: run.run_id for run in runs},
        strategy_config_sha256=runs[0].config_sha256,
        confirm_end=last,
        readings=readings,
        builds=builds,
        decided_on=today,
        report_path=report.relative_to(REPO_ROOT).as_posix(),
    )
    try:
        write_filter_file(JEV_FILTER_PATH, record)
    except FilterFileError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_filter_report(record), encoding="utf-8")
    typer.echo(f"wrote {JEV_FILTER_PATH}")
    typer.echo(f"report: {report}")


@jev_app.command("calibration")
def jev_calibration() -> None:
    """Write reports/jev/<date>-calibration.md (information only; nothing is adjusted)."""
    calendar = NyseSessions()
    today = datetime.now(NEW_YORK).date()
    with get_session() as session:
        samples, unlabeled = calibration_samples(
            session,
            _universe_symbols(),
            BENCHMARK_SYMBOL,
            LegalCloses(calendar.session_closes(HISTORY_START, today)),
            calendar.sessions_between(HISTORY_START, today),
        )
        builds = reading_builds(session)
    if not samples and not unlabeled:
        typer.echo("No Jev readings. Run `signalbench jev backfill` first (spec 03).", err=True)
        raise typer.Exit(1)
    sections = calibration_sections(samples)
    path = JEV_REPORTS_DIR / f"{today.isoformat()}-calibration.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_calibration_report(
            sections,
            readings=len(samples) + unlabeled,
            unlabeled=unlabeled,
            builds=builds,
            written_on=today,
        ),
        encoding="utf-8",
    )
    typer.echo(f"labeled {len(samples)} | not labeled {unlabeled}")
    for section in sections:
        typer.echo(
            f"{section.name}: {section.samples} documents | ECE positive "
            f"{_r(section.positive_ece)} | ECE negative {_r(section.negative_ece)} | "
            f"accuracy {_r(section.accuracy)} vs baseline {_r(section.majority_rate)}"
        )
    typer.echo(f"report: {path}")
```

`fit-filter` builds the view with `block_theta=None` (it only needs `max_p_negative`), and writes the filter file before the report, so a refused overwrite leaves nothing behind.

- [ ] **Step 5: Run the tests and checks**

Run: `uv run pytest tests/test_jev_cli.py -v && uv run pytest -q && uv run mypy src && uv run ruff check . && uv run signalbench jev --help`
Expected: 14 passed; the full suite 415 passed; mypy and ruff clean; the help lists `test`, `backfill`, `fit-filter`, and `calibration`.

- [ ] **Step 6: Commit**

```bash
git add src/signalbench/jev/fixture.py src/signalbench/cli.py tests/test_jev_cli.py
git commit -m "feat: signalbench jev test, backfill, fit-filter, and calibration

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Docs and the owner's `.env` line

**Files:**
- Modify: `README.md`, `docs/superpowers/specs/2026-09-22-swing-assistant-03-jev-reader-design.md`

- [ ] **Step 1: Update `README.md`**

In `README.md`, replace:

```markdown
Trading setups are rule-based and run as code, not model guesses; a future reader called Jev (spec 03) will read recent SEC filings and news as a supporting signal.
```

with:

```markdown
Trading setups are rule-based and run as code, not model guesses; a reader called Jev (spec 03, TypeSafe's decision model through OpenRouter) reads SEC filings and news as a supporting signal.
```

In `README.md`, replace:

```markdown
- Ingests Finnhub company news per ticker.
```

with:

```markdown
- Ingests Finnhub company news per ticker.
- Has Jev read each stored 8-K and news item once per ticker, decides by a pre-registered rule whether a negative reading may block entries, backtests the Sentiment setup, and writes a calibration report (spec 03).
```

In `README.md`, replace:

````markdown
FINNHUB_API_KEY=your-finnhub-api-key
```
````

with:

````markdown
FINNHUB_API_KEY=your-finnhub-api-key
```

For the Jev reader (spec 03), also add an OpenRouter key. `signalbench jev test` and `jev backfill` stop with a clear message without it:

```dotenv
OPENROUTER_API_KEY=your-openrouter-api-key
```
````

In `README.md`, replace:

```markdown
| `uv run signalbench backtest run --setup {pullback,breakout,combined} [--jev off] [--config PATH]` | Simulate on stored data with the committed `data/strategy_v1.yaml`, store the run, and write `reports/backtests/<date>-<setup>-<jev>.md`. `sentiment` and `--jev filter` need spec 03 |
```

with:

```markdown
| `uv run signalbench backtest run --setup {pullback,breakout,sentiment,combined} [--jev {off,filter}] [--config PATH]` | Simulate on stored data with the committed `data/strategy_v1.yaml`, store the run, and write `reports/backtests/<date>-<setup>-<jev>.md`. `sentiment` needs Jev readings and runs 2016 → end; `--jev filter` is information only and needs a committed `data/jev_filter_v1.yaml` with the filter ON |
```

In `README.md`, replace:

```markdown
| `uv run signalbench backtest show RUN_ID` | Reprint a stored run's report |
```

with:

```markdown
| `uv run signalbench backtest show RUN_ID` | Reprint a stored run's report |
| `uv run signalbench jev test` | One real Jev call on a small made-up 8-K; prints the answers, latency, cost, and resolved build |
| `uv run signalbench jev backfill [--source filings\|news\|all] [--since DATE] [--max-cost-usd N]` | Read every unread 8-K and news item once per universe ticker (news capped at 20 per symbol per day); resumable; stops cleanly at the budget (default $10) |
| `uv run signalbench jev fit-filter [--write]` | The pre-registered filter decision; `--write` records `data/jev_filter_v1.yaml` and `reports/jev/<date>-filter-decision.md` once |
| `uv run signalbench jev calibration` | Write `reports/jev/<date>-calibration.md` (information only) |
```

In `README.md`, replace:

```markdown
├── ingest/         CDR universe, prices, filings, earnings, news, and seeding
```

with:

```markdown
├── ingest/         CDR universe, prices, filings, earnings, news, and seeding
├── jev/            Jev client, backfill, filter decision, and calibration (spec 03)
```

In `README.md`, replace:

```markdown
data/               Checked-in CDR universe, spread survey, and strategy parameters
reports/            Committed backtest reports
```

with:

```markdown
data/               Checked-in CDR universe, spread survey, strategy parameters, and the Jev filter decision
reports/            Committed backtest, filter-decision, and calibration reports
```

In `README.md`, replace:

```markdown
earnings-date clustering, Finnhub news ingestion, CLI wiring, migrations, and API health.
```

with:

```markdown
earnings-date clustering, Finnhub news ingestion, the Jev client and backfill (against a fake client and mocked HTTP), the filter decision, calibration, CLI wiring, migrations, and API health.
```

- [ ] **Step 2: Record the implementation choices in spec 03**

Append to `docs/superpowers/specs/2026-09-22-swing-assistant-03-jev-reader-design.md`:

```markdown
- 2026-09-24: implementation choices (plan `2026-09-24-swing-03-jev-reader.md`):
  - Readings are per (document, ticker): `jev_readings` adds `ticker_id` and `response_id` and is unique on (`document_id`, `ticker_id`, `model_requested`, `question_set`), because 17.6% of universe documents name two or more universe tickers and the state names one company. Only the 40 universe tickers' documents are read.
  - Failed documents are not stored; each prints a `skip` line and is retried on the next run. 401, 402, and 404 stop the backfill; 20 failures in a row stop it too. A state over 100,000 characters is skipped before any call.
  - The budget guard stops submitting once the summed cost reaches `--max-cost-usd` (on `jev backfill`); at most 3 calls in flight still finish. A budget stop exits 0.
  - Legal close uses real NYSE close times, so a document after a 13:00 early close waits for the next session.
  - Filter: the latest stored v1 Jev-off run of each setup, pooled; an eligible θ also needs one kept trade; ties go to the lower θ. `data/jev_filter_v1.yaml` records `theta_fit` and `theta_block` (null unless ON) and is written once. `--jev filter` uses block and catalyst ranking, as live will; `--setup sentiment --jev filter` is refused.
  - Sentiment runs use the committed v1 config with only `start`, `h1_end`, and `h2_start` overridden. Runs that read Jev store `metrics.jev` (readings, builds, `information_only`, and the out-of-sample stats by signal date) and add a readings entry to `data_fingerprint`.
  - A filtered run's result is printed and reported as `PASS (information only)` or `FAIL (information only)`. `NyseSessions.sessions_between` clamps a start before the calendar's first session (2010-01-01 is a holiday).
```

- [ ] **Step 3: Run the checks**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 415 passed; ruff and mypy clean.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/superpowers/specs/2026-09-22-swing-assistant-03-jev-reader-design.md
git commit -m "docs: Jev reader CLI, environment key, and spec 03 implementation notes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 5: Tell the owner the `.env` line**

`.env.example` is off-limits, so ask the owner to add this line to `.env` themselves (their key, never pasted into chat):

```dotenv
OPENROUTER_API_KEY=
```

---

> ## ⛔ Controller: stop here until the owner has added the key and approved a spend cap
>
> Tasks 15–21 call the paid API, write to the real database, and record decisions. Do not start them until the owner says in chat that `OPENROUTER_API_KEY` is in `.env` **and** approves a spend cap for the backfill (the estimate is about $1.10 for filings and $1.80 for news; suggest $2 and $3). Report the Task 1–14 results and wait. The fit and confirmation results in Task 17 must never be used to change the θ grid, the windows, the 10-trade minimum, or the 0.70 / 0.50 thresholds: they are pre-registered in spec 03.

---

### Task 15: ⛔ `jev test` against the live API

**Gated:** start only after the go-ahead above.

- [ ] **Step 1: One real call**

Run: `uv run signalbench jev test`
Expected: exit 0 and four lines: `model: typesafe/jev-1.13-<build date>`, the impact probabilities, the event type and `p_routine`, and `latency … ms · input tokens … · cost $…`. The made-up 8-K is clearly good news, so `positive` should lead; if it does not, report that to the owner before any backfill.

If it exits 1: `HTTP 401` means a bad key, `HTTP 402` means no credits, and `HTTP 404` means the model or endpoint moved. Report it to the owner and stop.

- [ ] **Step 2: Keep the numbers**

Note the resolved build, latency, input tokens, and cost. Task 21 records them.

---

### Task 16: ⛔ Backfill filings, then news

**Gated:** Task 15 passed and the owner approved the caps.

- [ ] **Step 1: Database**

Run: `docker compose up -d && uv run alembic upgrade head`
Expected: upgrades through `0011_jev_readings`.

Do not ingest prices again until Task 19 is done: the Sentiment and filtered runs should share the data the v1 runs used where they overlap.

- [ ] **Step 2: Filings**

Run: `uv run signalbench jev backfill --source filings --max-cost-usd <approved filings cap>`
Expected: `to read: 5110 (already read 0, no text 1, too long 0)` or close to it, progress every 100 reads, then `read … · cost $… · input tokens …` and one `model typesafe/jev-1.13-…: N` line per resolved build. At four calls at a time this takes about an hour (filings are long). It is resumable: a budget stop, a crash, or Ctrl+C loses at most the calls in flight; run the same command again to continue.

- [ ] **Step 3: News**

Run: `uv run signalbench jev backfill --source news --max-cost-usd <approved news cap>`
Expected: `to read: 80208 …` (or close), the `news cap: … items over 20 per symbol per New York day not read (… symbol-days)` line, and the same summary. Expect several hours; run it in the background and rerun it after any stop.

- [ ] **Step 4: Confirm nothing is left**

Run: `uv run signalbench jev backfill --max-cost-usd 0.50`
Expected: `to read: 0` (or only documents that keep failing, each with its `skip` line). If more than one resolved build appears across the runs, tell the owner: readings from different builds are mixed.

Note the counts (read, skipped with reasons, capped), the total cost, and the resolved builds for Task 21.

---

### Task 17: ⛔ The filter decision

**Gated:** Task 16 complete.

- [ ] **Step 1: Dry run**

Run: `uv run signalbench jev fit-filter`
Expected: `decision: on, theta …` or `decision: information_only, theta …`, the reason, one `fit theta …` line per θ, and the confirmation line when a θ was eligible. It reads the stored v1 Jev-off runs of Pullback and Breakout; if it refuses because a run is missing or two `config_sha256` values exist, stop and report.

- [ ] **Step 2: Record it**

Run: `uv run signalbench jev fit-filter --write`
Expected: `wrote …/data/jev_filter_v1.yaml` and `report: …/reports/jev/<date>-filter-decision.md`. Read both. **Do not change anything in them, and do not rerun with other settings.**

- [ ] **Step 3: Commit the decision on its own**

```bash
git add data/jev_filter_v1.yaml reports/jev/<date>-filter-decision.md
git commit -m "data: pre-registered Jev filter decision (spec 03)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 18: ⛔ Sentiment backtest

**Gated:** Task 17 committed (so `git_sha` is clean).

- [ ] **Step 1: Clean tree**

Run: `git status --short --untracked-files=no -- src data alembic pyproject.toml uv.lock`
Expected: no output.

- [ ] **Step 2: Run**

Run: `uv run signalbench backtest run --setup sentiment`
Expected: `run <uuid>: PASS` or `FAIL`, one line per criterion, and `report: …/reports/backtests/<date>-sentiment-off.md`. Open the report: the period starts on the first session of 2016, the status line says PASS, FAIL, or information only (fewer than 30 trades), the Jev readings section and the out-of-sample section (signals on or after 2026-09-15) are present, and the caveats include the two Jev lines. **Do not change any parameter, whatever the result.**

- [ ] **Step 3: Commit**

```bash
git add reports/backtests/<date>-sentiment-off.md
git commit -m "docs: Sentiment setup pass-bar report (spec 03)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 19: ⛔ Information-only filtered runs (only if the filter is ON)

**Gated:** Task 18 committed.

- [ ] **Step 1: If `data/jev_filter_v1.yaml` says `mode: information_only`**

Run: `uv run signalbench backtest run --setup breakout --jev filter`
Expected: exit 1 with `The Jev filter is information-only (data/jev_filter_v1.yaml), so --jev filter runs are refused (spec 03).` That refusal is correct. Skip to Task 20.

- [ ] **Step 2: If it says `mode: 'on'`**

Run: `uv run signalbench backtest run --setup pullback --jev filter`, then `uv run signalbench backtest run --setup breakout --jev filter`
Expected: two reports, `reports/backtests/<date>-pullback-filter.md` and `…-breakout-filter.md`, each with `**Result:** PASS (information only)` or `FAIL (information only)`, the **Information only — cannot change the v1 result.** banner, and `Filter theta_block` equal to the committed θ. Whatever they show, the spec 02 result stands: both setups failed. A filtered PASS is not evidence on its own: in a dry run with random fake readings, Breakout's Sharpe moved from 0.949 to 0.998 against QQQ's 0.996.

- [ ] **Step 3: Commit (ON only)**

```bash
git add reports/backtests/<date>-pullback-filter.md reports/backtests/<date>-breakout-filter.md
git commit -m "docs: information-only Jev-filtered runs of pullback and breakout

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 20: ⛔ Calibration report

**Gated:** Task 16 complete (it does not depend on Tasks 17–19).

- [ ] **Step 1: Write it**

Run: `uv run signalbench jev calibration`
Expected: `labeled … · not labeled …` (the last few days of documents cannot be labeled yet), one line per section with both ECEs and the accuracy against the baseline, and `report: …/reports/jev/<date>-calibration.md`. Nothing is adjusted: changing θ or the 0.70 threshold after reading it is an owner decision recorded in the spec 03 changelog.

- [ ] **Step 2: Commit**

```bash
git add reports/jev/<date>-calibration.md
git commit -m "docs: Jev calibration report (spec 03)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 21: ⛔ Gate record and the owner's decision

**Files:**
- Modify: `docs/superpowers/specs/2026-09-22-swing-assistant-03-jev-reader-design.md` (changelog), `docs/superpowers/specs/2026-09-22-swing-assistant-00-overview.md` (changelog)

- [ ] **Step 1: Record the gate facts in spec 03**

Append one changelog line to spec 03 with the live facts, e.g. `- <date>: gate record. jev test: build …, latency … ms, cost $…. Backfill: … filings and … news read (… skipped: …; … news over the cap), total $…, build(s) …. Filter: ON at θ … / information only (<reason>). Sentiment: PASS / FAIL / information only (N trades, mean R …, Sharpe … vs QQQ …; out of sample: N trades). Calibration: ECE positive …, negative …; accuracy … vs baseline ….`

- [ ] **Step 2: ⛔ Owner decision**

Send the owner the filter decision, the Sentiment report, and the calibration report paths with a two-line summary each. The owner decides whether Sentiment goes live, and so whether specs 04–05 are unlocked (overview: they wait unless the Sentiment setup passes). Write their decision, in their words, as a new line in the overview's Changelog, e.g. `- <date>: spec 03 gate — Jev filter ON at θ … / information only; Sentiment PASS / FAIL / information only; specs 04–05: unlocked / still waiting; decision: "…"`.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-09-22-swing-assistant-03-jev-reader-design.md docs/superpowers/specs/2026-09-22-swing-assistant-00-overview.md
git commit -m "docs: record the spec 03 gate and the owner's decision

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Ask the owner before pushing the branch or opening a PR (PR #5 for spec 02 is still open; this branch sits on top of it).

---

## Spec coverage check

| Spec 03 requirement | Task |
| --- | --- |
| OpenRouter endpoint, headers, body `{model, state, questions}`, model `typesafe/jev-1.13`, resolved build recorded | 3, 5, 6 |
| `choice` / `noul` parsing; `confidence` never used | 4 |
| Cleaned text with no added dates; no numeric questions | 3, 13 (fixture) |
| Question set `q1` verbatim and versioned | 3 |
| State `Company: … (SYMBOL)` / `Source: SEC 8-K, items …` or `News` | 3, 6 |
| `jev_readings` columns, cascade, unique key (per ticker; see Decisions), migration `0011` | 2 |
| httpx, 10 s timeout, 3 retries with exponential backoff on 429/5xx, concurrency 4 | 5, 6 |
| `JevClient` protocol and `FakeJevClient` | 5 |
| Budget guard `--max-cost-usd` (default 10.00), clean stop | 6, 13 |
| Privacy: only public documents are sent | 3, 6, 13 |
| News cap: 20 per symbol per New York day, most recent first, logged | 6, 13 |
| `ReadingsView` sees only documents with legal close ≤ as_of | 1, 7 |
| positive / catalyst / sentiment trigger / negative / block definitions | 7 |
| A document with no reading is neither positive nor negative | 7, 8 |
| Filter step 1: max `p_negative` over the 10 sessions up to the signal date, from the Jev-off v1 trade logs | 7, 8, 9 |
| Filter step 2: fit window, θ grid, 10-trade eligibility, largest kept − blocked | 8 |
| Filter step 3: confirmation window, ON rule | 8 |
| Filter step 4: v1 result stands; information-only filtered runs when ON; refused otherwise | 11, 12, 19 |
| Filter step 5: `data/jev_filter_v1.yaml` committed with the report; `strategy_v1.yaml` untouched | 9, 13, 17 |
| Sentiment: spec 02 rules, 2016 → end, calendar-midpoint halves, same pass bar | 12, 18 |
| Sentiment: signals ≥ 2026-09-15 reported separately | 11, 12 |
| Sentiment: fewer than 30 trades → information only | 11, 12 |
| Calibration labels (excess over QQQ, legal close → 5 sessions, ±2%) | 10 |
| Reliability deciles, ECE, argmax accuracy vs majority baseline, split at 2026-09-15 | 10 |
| Calibration report at `reports/jev/<date>-calibration.md`; nothing adjusted automatically | 10, 13, 20 |
| CLI `jev test`, `jev backfill`, `jev fit-filter [--write]`, `jev calibration` | 13 |
| Test: request payload shape against the documented schema | 3, 5 |
| Test: choice and noul parsing; rejecting sums outside 1 ± 0.01 | 4 |
| Test: retries on 429/5xx; the budget guard stops at the limit | 5, 6, 13 |
| Test: idempotent backfill (a second run makes no calls) | 6, 13 |
| Test: a document accepted at 16:05 ET is invisible at that close and visible the next session | 1, 7 |
| Test: filter decision with no eligible θ, a fit that does not confirm, a fit that confirms | 8 |
| Test: calibration buckets and ECE against a hand-computed example | 10 |
| Gate: CI green | every task (pytest, ruff, mypy), CI on push |
| Gate: `jev test` live; latency and cost recorded | 15, 21 |
| Gate: backfill complete; documents read, total cost, resolved build recorded | 16, 21 |
| Gate: filter decision and Sentiment report committed under `reports/` | 17, 18 |
| Gate: calibration report committed | 20 |
| Overview gate: owner decides whether Sentiment unlocks specs 04–05 | 21 |
