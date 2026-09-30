# Swing Assistant 05 — Evening Scan and Telegram Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the live strategy every evening on the owner's PC and talk to the owner through Telegram. `signalbench scan` runs the spec's eleven steps (the database and config check, prices, the earnings calendar, splits, catch-up, tonight's `decide()`, live CDR sizing, the template Why line, writing and sending, expiry and the scale-up check, and the summary), records each run in `scan_runs`, and is idempotent, single-instance, and printable with `--dry-run --as-of`. `signalbench bot run` long-polls Telegram, answers only the owner's chat, turns ✅/⏭/Sold/Ignore presses and the eleven commands into ledger writes, and writes a heartbeat. Two PowerShell scripts run them from Task Scheduler with toasts on failure, and a third registers the tasks, only on the owner's go-ahead.

**Architecture:** More of the `signalbench.live` package, on top of spec 04's `Ledger` and `review_session`. `sizing.py` (the entry window on the Cboe Canada calendar, and the whole-unit rule) and `why.py` (the template line) are pure. `messages.py` holds the text templates and button data, and `messenger.py` has the `Messenger` protocol with `TelegramMessenger` (python-telegram-bot's `Bot` on a private event loop), `FakeMessenger`, and `ConsoleMessenger` for the dry run. `outbox.py` sends every row that has no Telegram message id yet, so a failed send is retried by the next scan and nothing is sent twice. `summary.py` builds the evening summary, the pause review, `/portfolio`, and `/pnl` from the ledger. `scan.py` is the scan, `status.py` is `scan status` and `/status`, and `heartbeat.py` is the bot's heartbeat row. `bot.py` (`BotBrain`) turns each command, reply, or button press into replies and ledger writes without touching Telegram, and `telegram_bot.py` is the thin python-telegram-bot glue. Migration `0014_scan_runs` adds `scan_runs` and the single-row `bot_heartbeat`. The CLI gains `scan`, `scan status`, and `bot run`.

**Tech Stack:** Python 3.11+, SQLModel (in-memory SQLite in tests), Alembic, Typer, pytest, ruff 0.16, mypy strict, and one new dependency: **python-telegram-bot 22.8** (async, Bot API 10.0; pinned `>=22.8,<23`, its only dependency is httpx, already here). The API used was checked with ctx7 against the library's current docs (`/python-telegram-bot/python-telegram-bot`, v22.5 there; 22.8 from PyPI, released 2026-06-12): `ApplicationBuilder().token().post_init().post_shutdown().build()`, `run_polling(allowed_updates=...)`, `TypeHandler`, `CallbackQuery.answer()`, `Bot.send_message`/`edit_message_text` with `InlineKeyboardMarkup`, `Bot.initialize()`/`shutdown()` for standalone use, and a custom `BaseRequest` for tests. The Telegram Bot API limits the design relies on were checked the same way (`/websites/core_telegram_bots_api`): `callback_data` is 1-64 bytes, and a message is 1-4096 characters. Windows PowerShell 5.1 for the scripts.

**Spec:** [`docs/superpowers/specs/2026-09-22-swing-assistant-05-bot-scan-design.md`](../specs/2026-09-22-swing-assistant-05-bot-scan-design.md) (revised 2026-09-29 for v2-none-cash). **Depends on:** the spec 04 ledger ([plan](2026-09-30-swing-04-ledger.md), built on this branch) and spec 07's code (`paper/lock.py`, `paper/splits.py`, the safe price ingest, the Finnhub calendar ingest with `since`, `code_version`, and `scripts/paper_nightly.ps1`). Branch `feat/swing-08-live-v2`; nothing is merged to main.

## Conventions for every task

- Run commands from the repo root with the Bash tool (Git Bash syntax). Use `uv run …` for every Python tool. Stay on branch `feat/swing-08-live-v2`; never push without asking the owner.
- **Never edit `data/strategy_*.yaml`** (the live config's sha256 is frozen by `live start`) **or any `.env*` file** (`.env.example` included: the owner adds the Telegram lines to `.env` by hand). Tests copy `data/strategy_v2-none-cash.yaml` into throwaway repos under pytest's `tmp_path`; they never write it. Stage explicit paths only; never `git add -A` or `git add .`.
- **`README.md` has an uncommitted change that belongs to the owner, and `data/paper_v1.yaml` is untracked.** Do not edit, stage, stash, or check out either. Task 13 gives the owner README lines to add by hand.
- **No real database, no Telegram, and no network before the ⛔ tasks.** Tasks 1–13 use the in-memory SQLite `session` fixture, stored fixture prices, `FakeMessenger`, and a Bot API that answers from memory (`tests/telegram_helpers.py`). The only network in Tasks 1–13 is `uv add` (PyPI, Task 5). Do not run the migration, `signalbench scan`, `bot run`, `live …`, `ledger …`, `ingest …`, or `paper …` against the real database, and do not register a scheduled task, until the controller's go-ahead below.
- mypy runs strict on `src/` only. Test helpers are imported as `from scan_helpers import …` (and `live_helpers`, `paper_helpers`, `strategy_helpers`, `telegram_helpers`).
- ruff 0.16 is strict in this repo (its defaults include import order `I001`, `ISC004` for an implicit string concatenation inside a list or call, `FLY002`, and `RUF100` for an unused `noqa`). Run `uv run ruff check --fix .` only **after** the module a test imports exists: before that, ruff files the missing `signalbench.*` name as third-party and moves the import. Every block below is already in ruff's order.
- "Append to `…`" adds the block after the file's last line: after two blank lines in a `.py` file, on the next line in a `.md` file. "In `…`, replace: … with: …" changes text that occurs exactly once. "Replace the whole of `…`" rewrites the file.
- The PowerShell files are ASCII only: Windows PowerShell 5.1 reads a `.ps1` without a byte-order mark as ANSI.
- After each task: `uv run pytest -q`, `uv run ruff check .`, and `uv run mypy src` all pass before committing. The full suite takes about three minutes, most of it in `tests/test_paper_run.py`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Every code block in this plan was replayed, task by task, in a scratch clone of this branch at `7ba46d4` (with the untracked `data/paper_v1.yaml` copied in): each "Expected" failure and pass count below is from that replay. The suite starts at 704 passed and ends at 789 passed after Task 13, with ruff and mypy clean. If a number disagrees, look for a transcription slip before touching a test.

## The scan and the bot, as built

| Rule | Behaviour |
| --- | --- |
| Target | `last_complete_session` (16:15 New York). `--dry-run --as-of DATE` replays DATE as if at 18:00 New York that evening |
| Run row | `scan_runs`: `running`, then `ok` or `failed` with the step, the error, the warnings (a JSON list), the counts, the git sha, whether the tree was dirty, and the live config's sha256. A `running` row over two hours old is marked failed, "abandoned (killed or crashed)" |
| Already scanned | A target whose last `ok` row is on or after it runs step 1, records an `ok` row with no counts, prints `DATE was already scanned; nothing to send.`, and sends nothing. `--force` rescans; the dry run always does |
| Catch-up | The NYSE sessions after the last `ok` row's `as_of` and before the target, in order: equity snapshot, pause, exits and raises (sent marked late); their entries are dropped. With no `ok` row yet, the target only |
| Critical steps | Any error fails the run at its step and sends `⚠️ Scan failed at step N (<name>): <error>`, then prints `ERROR: Scan failed …` for the script's toast. If Telegram is down too, the scan says so and the toast still shows |
| Not critical | Step 3: a calendar failure is a warning and the stored dates are used. Step 4: a failed lookup, a split the ledger refuses, or prices no recorded split explains holds that US symbol (no exit or raise tonight) with a ⚠️ line; an unexpected error there holds every held and pending symbol |
| Sizing | The spec's whole-unit rule on the CDR: floor if ≥ 1 and ≥ 75% of the target; else ceil if its risk ≤ 2.5% of equity and it fits equity/3 and the cash no pending signal holds; else a fractional market order. Limit = CDR close × 1.01, rounded down to the cent |
| Entry window | The next Cboe Canada session (exchange_calendars' `XTSE`); the signal expires at its close. A Cboe holiday on the next NYSE session moves both and the message says so |
| Sending | Each row with a message keeps its Telegram id. Unsent rows go out in order: split notices, entries, exit alerts, raises; then any pause review and the summary. A send that fails fails step 9, and the next run sends what is left |
| Bot | Only `TELEGRAM_CHAT_ID` is answered; any other chat is logged and ignored. ✅ asks for `UNITS PRICE [DATE] [force]` (or `[Use suggested]`), ⏭ asks for a reason, Sold asks for `[UNITS\|all] PRICE [DATE]`, and Ignore records a miss. A handled message is edited to say what was done, and a second tap answers "Already logged" |
| Heartbeat | Every 5 minutes the bot calls `getMe` and then rewrites `bot_heartbeat`. A scan warns (and prints `BOT STALE: …` for a toast) when it is missing or over an hour old |
| Scripts | `live_scan.ps1` and `live_bot.ps1` share `scripts/lib/SignalBench.ps1` with `paper_nightly.ps1` (the log, the toast, the `.env` loading). Logs go to `logs/scan-<yyyy-MM>.log` and `logs/bot-<yyyy-MM>.log` |

## Decisions this plan makes where the spec is silent

Two depart from the spec's letter: the message header names the CDR ticker, and `scan status --stale-after-days 0` is allowed. One is marked **Confirm with the owner**; it does not block Tasks 1–13.

- **`bot_heartbeat` is part of migration `0014`** (the spec's "e.g."): one row with a fixed id of 1 and `beat_at`. `scan_runs.warnings` is a JSON list and `counts` a JSON object (`signals`, `exits`, `raises`, `splits`, `expired`, `sent`).
- **A run with nothing to do still leaves a row.** When the target was already scanned, the run still does step 1 and records an `ok` row with empty counts. The stale check and `/status` then see the scheduled task running on weekends and holidays; the catch-up start (the last `ok` `as_of`) is unchanged.
- **The outbox is the resend rule.** A row is sent when its `telegram_message_id` is empty, and each id is saved right after its message, so a failed send retries only what was not sent. An entry whose expiry passed before it could be sent goes out as `ℹ️ The NVDA (CDR ZNVD) entry of … was not sent in time and expired at …: do not place it.`, with no buttons. A failed send fails step 9 (the spec marks step 9 critical).
- **A late run** is one that runs after 09:30 New York on the session after the target (the PC was off at 15:00 and the log-on trigger ran it the next morning). Its exits and raises are marked late, the summary warns, and its entries are still sent (their entry session is that day).
- **Sizing inputs.** decide()'s US close and stop are rounded to 4 decimals before both the CDR sizing and `record_signal`, so the stored stop and the message agree. The CDR close is the latest stored close on or before the target. A CDR that the liquidity flags mark inactive is skipped as `no_cdr_price`, and no uncommitted cash is `no_cash`. Fractional units are rounded down to 6 decimals. The spread limit is the spec changelog's default 0.5%, because the survey file has no limit field.
- **Messages name the CDR ticker** (`🟢 BUY NVDA (CDR ZNVD) — Breakout · …`, where the spec shows `NVDA (CDR)`), because the owner types ZNVD on Wealthsimple. Money is shown to the cent (rounded half up) with U+2212 for minus, and percentages to one decimal. A fractional entry reads `Size C$33.33 (0.868055 units) · risk C$2.00 · MARKET (fractional): only place it if the price is ≤ C$38.78`.
- **Split notices.** Each `split` stop row sends `ℹ️ … split R (ex-date …): stop … → … · no action` (US$ for a US split, C$ for a CDR split), and each CDR split also sends the units and the ACB per unit before and after, with the signals it withdrew. Owner-recorded splits (`ledger split`) are sent by the next scan the same way. A split with an ex-date after the target is not recorded that night.
- **The dry run** is `run_scan` inside one database transaction that is rolled back at the end (SQLAlchemy savepoints: every commit in the ledger becomes a savepoint), with no fetching (the stored prices, calendar, and splits), no lock, and `ConsoleMessenger`. It needs Postgres's savepoints; the test uses SQLite configured the same way.
- **The backtest's mean R** is read from the latest stored Breakout run (Jev off) whose `config_sha256` is the live config's (`reports/backtests/2026-09-28-v2-none-cash-breakout-off.md`, run `4123c177`: 0.505). Without one, the texts say there is no stored run to compare.
- **The scale-up check is due** when the count of closed managed positions is above the one in the last recorded result. The summary line is `Scale-up check: N of 10 managed trades closed.` before it is active, the spec's pass sentence, or the failing checks.
- **The bot's state.** After ✅, the bot waits in memory for one reply. A restart forgets it, and the owner taps again. A reply or `/buy` may end with `force` (spec 04's no-margin override). `/sell SYMBOL all PRICE` sells every unit held and links the sale to the CDR's open exit alert. `/signals` lists the `sent` signals that have not expired. `/tax YEAR` shows the totals and splits, not every sale, and points to the CSV. `/status` reads the next run from Task Scheduler (`schtasks /Query`), and "unknown" elsewhere.
- **The heartbeat means that Telegram and the database both answer:** `getMe` succeeds, then the row is written, every 5 minutes (the spec asks for at least every 10).
- **The bot never logs its token.** httpx logs each request URL at INFO, and a Bot API URL contains the token, so `bot run` sets the httpx logger to WARNING. The CLI prints neither the token nor the chat id.
- **Scripts.** The shared PowerShell code moved out of `paper_nightly.ps1` into `scripts/lib/SignalBench.ps1`, and the paper script's behaviour is unchanged. `live_bot.ps1` starts the bot again 60 seconds after an error exit, with a toast each time, as well as Task Scheduler's restart setting. `live_scan.ps1 -CheckOnly` runs only the stale check, and **`scan status --stale-after-days 0`** (allowed, where paper's minimum is 1) always reports stale, so the stale toast can be checked by hand without waiting three days. `register-tasks.ps1` prints the commands and runs them only with `-Register`, with `-At HH:mm` to override the computed time.
- **Docker.** `docker-compose.yml` gains `restart: unless-stopped` (the spec says the container uses it; the file did not). Task 16 applies it.
- **Confirm with the owner: [Use suggested] records the signal's CDR close as the fill price.** It is a shortcut for a limit order that filled at about the signal price. The scale-up check's slippage criterion then reads 0 for that buy, so the owner should type the real price (`3 10.45`) whenever it differs.

## File map

| Path | Action | Responsibility |
| --- | --- | --- |
| `src/signalbench/db/models.py` | Modify | `ScanRun`, `BotHeartbeat` |
| `alembic/versions/0014_scan_runs.py` | Create | The two tables |
| `src/signalbench/market/calendar.py` | Modify | `CboeCanadaSessions` (XTSE) |
| `src/signalbench/live/sizing.py` | Create (Task 2), replace (Task 3) | `entry_window`, `size_cdr` |
| `src/signalbench/live/why.py` | Create | The template Why line |
| `src/signalbench/config.py` | Modify | `telegram_bot_token`, `telegram_chat_id` |
| `src/signalbench/live/messenger.py` | Create | `Button`, `Messenger`, `TelegramMessenger`, `FakeMessenger`, `ConsoleMessenger` |
| `src/signalbench/live/messages.py` | Create | Entry, raise, split, exit, and failure texts; button data |
| `src/signalbench/live/summary.py`, `outbox.py` | Create | The summary, pause review, `/portfolio`, `/pnl`; sending the unsent rows |
| `src/signalbench/live/heartbeat.py`, `scan.py` | Create | The heartbeat row; `run_scan` and `dry_run_session` |
| `src/signalbench/live/status.py`, `bot.py` | Create | `scan status` and `/status`; `BotBrain` |
| `src/signalbench/live/telegram_bot.py` | Create | The python-telegram-bot application and the heartbeat task |
| `src/signalbench/cli.py` | Modify | `scan`, `scan status`, `bot run` |
| `pyproject.toml`, `uv.lock` | Modify (`uv add`) | python-telegram-bot 22.8 |
| `scripts/lib/SignalBench.ps1` | Create | The log, toast, `.env` loading, and `uv run` shared by the scripts |
| `scripts/paper_nightly.ps1` | Replace | Uses the shared file; same behaviour |
| `scripts/live_scan.ps1`, `scripts/live_bot.ps1`, `scripts/windows/register-tasks.ps1` | Create | The two scheduled scripts and the registration |
| `docker-compose.yml` | Modify | `restart: unless-stopped` |
| `tests/scan_helpers.py`, `tests/telegram_helpers.py` | Create | The Breakout world; the Bot API from memory |
| `tests/test_scan_schema.py`, `tests/test_live_*.py` (10 files), `tests/test_scan_cli.py` | Create | Tests, including the end-to-end fixture test (`tests/test_live_e2e.py`) |
| `tests/test_migrations.py` | Modify | The new head and a 0014 check |
| `docs/superpowers/specs/2026-09-22-swing-assistant-05-bot-scan-design.md` | Modify | Implementation choices |

---

### Task 1: The `scan_runs` and `bot_heartbeat` tables and migration `0014_scan_runs`

**Files:**
- Modify: `src/signalbench/db/models.py`, `tests/test_migrations.py`
- Create: `alembic/versions/0014_scan_runs.py`, `tests/test_scan_schema.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scan_schema.py`:

```python
"""Spec 05 tables: `scan_runs` and `bot_heartbeat`, and migration 0014 that creates them."""

import importlib.util
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, col, select

from signalbench.db.models import BotHeartbeat, ScanRun

MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0014_scan_runs.py"
TABLES = ("scan_runs", "bot_heartbeat")
NOW = datetime(2026, 10, 2, 21, 5, tzinfo=UTC)


def test_a_scan_run_keeps_its_steps_warnings_and_counts(session: Session) -> None:
    session.add(
        ScanRun(as_of=date(2026, 10, 2), started_at=NOW, status="failed", failed_step=2,
                error="QQQ has no bar", warnings=["earnings calendar FAILED"],
                counts={"signals": 1}, git_sha="a" * 40, git_dirty=False,
                config_sha256="b" * 64)
    )
    session.add(ScanRun(as_of=date(2026, 10, 5), started_at=NOW, status="running"))
    session.commit()
    session.expire_all()
    failed, running = session.exec(select(ScanRun).order_by(col(ScanRun.id))).all()
    assert (failed.failed_step, failed.warnings, failed.counts) == (
        2, ["earnings calendar FAILED"], {"signals": 1}
    )
    assert failed.started_at == NOW
    assert (running.finished_at, running.warnings, running.counts, running.git_sha) == (
        None, [], {}, None
    )


def test_the_heartbeat_is_one_row(session: Session) -> None:
    session.add(BotHeartbeat(beat_at=NOW))
    session.commit()
    session.add(BotHeartbeat(beat_at=NOW))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    [beat] = session.exec(select(BotHeartbeat)).all()
    assert (beat.id, beat.beat_at) == (1, NOW)


def test_migration_0014_creates_the_model_columns_and_drops_them_again() -> None:
    spec = importlib.util.spec_from_file_location("migration_0014", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
        inspector = inspect(connection)
        created = {
            table: [(c["name"], bool(c["nullable"])) for c in inspector.get_columns(table)]
            for table in TABLES
        }
        with Operations.context(MigrationContext.configure(connection)):
            module.downgrade()
        left = inspect(connection).get_table_names()
    for table in TABLES:
        model = SQLModel.metadata.tables[table]
        assert created[table] == [(c.name, bool(c.nullable)) for c in model.columns], table
    assert left == []
```

In `tests/test_migrations.py`, replace:

```python
    assert _script().get_heads() == ["0013_live_ledger"]
```

with:

```python
    assert _script().get_heads() == ["0014_scan_runs"]
```

Append to `tests/test_migrations.py`:

```python
def test_0014_creates_scan_runs_and_the_bot_heartbeat() -> None:
    text = (VERSIONS / "0014_scan_runs.py").read_text(encoding="utf-8")
    assert 'down_revision: str | None = "0013_live_ledger"' in text
    for table in ("scan_runs", "bot_heartbeat"):
        assert f'op.create_table(\n        "{table}"' in text
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_migrations.py -q`
Expected: 2 failed, 6 passed: the head is still `0013_live_ledger`, and `FileNotFoundError` for `0014_scan_runs.py`.

Run: `uv run pytest tests/test_scan_schema.py -q`
Expected: collection error, `ImportError: cannot import name 'BotHeartbeat' from 'signalbench.db.models'`.

- [ ] **Step 3: Add the models**

Append to `src/signalbench/db/models.py`:

```python
class ScanRun(SQLModel, table=True):
    """One `signalbench scan` (spec 05). `running` until it ends; a scan killed or crashed
    leaves it `running`, and the next scan marks it failed once it is two hours old."""

    __tablename__ = "scan_runs"

    id: int | None = Field(default=None, primary_key=True)
    as_of: date  # the target session
    started_at: datetime = Field(sa_column=Column(UTCDateTime(), nullable=False))
    finished_at: datetime | None = Field(
        default=None, sa_column=Column(UTCDateTime(), nullable=True)
    )
    status: str  # running | ok | failed
    failed_step: int | None = None  # 1-11 (spec 05, Evening scan)
    error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    warnings: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    counts: dict[str, int] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    git_sha: str | None = None  # HEAD of the code that ran (None when git itself failed)
    git_dirty: bool | None = None  # tracked code had uncommitted changes: the scan was refused
    config_sha256: str | None = None  # the live config's, once step 1 has checked it


class BotHeartbeat(SQLModel, table=True):
    """The bot's last sign of life (spec 05): one row, rewritten at least every 10 minutes."""

    __tablename__ = "bot_heartbeat"

    id: int = Field(default=1, primary_key=True, sa_column_kwargs={"autoincrement": False})
    beat_at: datetime = Field(sa_column=Column(UTCDateTime(), nullable=False))
```

- [ ] **Step 4: Write the migration**

Create `alembic/versions/0014_scan_runs.py`:

```python
"""Spec 05 evening scan runs and the bot heartbeat

Revision ID: 0014_scan_runs
Revises: 0013_live_ledger
Create Date: 2026-09-30 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_scan_runs"
down_revision: str | None = "0013_live_ledger"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scan_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("failed_step", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("counts", sa.JSON(), nullable=False),
        sa.Column("git_sha", sa.String(), nullable=True),
        sa.Column("git_dirty", sa.Boolean(), nullable=True),
        sa.Column("config_sha256", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "bot_heartbeat",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("beat_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("bot_heartbeat")
    op.drop_table("scan_runs")
```

- [ ] **Step 5: Run the tests and checks**

Run: `uv run pytest tests/test_scan_schema.py tests/test_migrations.py -q && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 11 passed (3 new schema tests, and the 8 migration tests with the new one); the full suite 708 passed; ruff `All checks passed!`; mypy `Success: no issues found in 74 source files`.

(`test_migration_0014_creates_the_model_columns_and_drops_them_again` runs the migration's own `upgrade()` and `downgrade()` on SQLite and compares every column name and nullability with the models.)

- [ ] **Step 6: Commit**

```bash
git add src/signalbench/db/models.py alembic/versions/0014_scan_runs.py tests/test_scan_schema.py tests/test_migrations.py
git commit -m "feat: scan_runs and bot_heartbeat tables, migration 0014

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Cboe Canada sessions and the entry window

**Files:**
- Modify: `src/signalbench/market/calendar.py`
- Create: `src/signalbench/live/sizing.py`, `tests/test_live_entry.py`

The real `XNYS` and `XTSE` calendars from exchange_calendars (no network). 2026-10-12 is Canadian Thanksgiving (NYSE open, Toronto closed) and 2026-11-26 is US Thanksgiving (the reverse).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_live_entry.py`:

```python
"""Spec 05, Live CDR sizing: the entry session is the next Cboe Canada session (XTSE as the
proxy), and a signal expires at its close. Real exchange calendars, no network."""

from datetime import UTC, date, datetime

import pytest

from signalbench.live.sizing import entry_window
from signalbench.market.calendar import CboeCanadaSessions, NyseSessions


@pytest.fixture(scope="module")
def calendars() -> tuple[NyseSessions, CboeCanadaSessions]:
    return NyseSessions(start=date(2026, 1, 1)), CboeCanadaSessions(start=date(2026, 1, 1))


def test_cboe_canada_uses_the_toronto_calendar(
    calendars: tuple[NyseSessions, CboeCanadaSessions],
) -> None:
    nyse, cboe = calendars
    thanksgiving = date(2026, 10, 12)  # Canadian Thanksgiving: NYSE open, Toronto closed
    assert (nyse.is_session(thanksgiving), cboe.is_session(thanksgiving)) == (True, False)
    us_thanksgiving = date(2026, 11, 26)
    assert (nyse.is_session(us_thanksgiving), cboe.is_session(us_thanksgiving)) == (False, True)


def test_the_entry_session_is_the_next_cboe_session_and_expires_at_its_close(
    calendars: tuple[NyseSessions, CboeCanadaSessions],
) -> None:
    window = entry_window(date(2026, 10, 6), *calendars)
    assert (window.session, window.note) == (date(2026, 10, 7), None)
    assert window.expires_at == datetime(2026, 10, 7, 20, 0, tzinfo=UTC)  # 16:00 Toronto


def test_a_cboe_holiday_moves_the_entry_and_the_expiry_and_says_so(
    calendars: tuple[NyseSessions, CboeCanadaSessions],
) -> None:
    window = entry_window(date(2026, 10, 9), *calendars)  # Friday before Canadian Thanksgiving
    assert window.session == date(2026, 10, 13)
    assert window.expires_at == datetime(2026, 10, 13, 20, 0, tzinfo=UTC)
    assert window.note == "Cboe Canada is closed on Mon 12 Oct: place it on Tue 13 Oct."


def test_a_us_holiday_does_not_move_the_entry(
    calendars: tuple[NyseSessions, CboeCanadaSessions],
) -> None:
    window = entry_window(date(2026, 11, 25), *calendars)  # the eve of US Thanksgiving
    assert (window.session, window.note) == (date(2026, 11, 26), None)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_live_entry.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'signalbench.live.sizing'`.

- [ ] **Step 3: Add the Cboe Canada calendar**

In `src/signalbench/market/calendar.py`, replace:

```python
    """The XNYS calendar from exchange_calendars, exposed as `datetime.date` values."""

    def __init__(self, start: date = HISTORY_START) -> None:
        import exchange_calendars as xcals

        self._calendar: Any = xcals.get_calendar("XNYS", start=start.isoformat())
```

with:

```python
    """The XNYS calendar from exchange_calendars, exposed as `datetime.date` values."""

    CODE = "XNYS"
    NAME = "NYSE"

    def __init__(self, start: date = HISTORY_START) -> None:
        import exchange_calendars as xcals

        self._calendar: Any = xcals.get_calendar(self.CODE, start=start.isoformat())
```

In `src/signalbench/market/calendar.py`, replace:

```python
            raise ValueError(f"Only {len(found)} NYSE sessions known after {day}; need {count}")
```

with:

```python
            raise ValueError(
                f"Only {len(found)} {self.NAME} sessions known after {day}; need {count}"
            )
```

Append to `src/signalbench/market/calendar.py`:

```python
class CboeCanadaSessions(NyseSessions):
    """Cboe Canada, where the CDRs trade (spec 05). exchange_calendars has no Cboe Canada
    calendar, so Toronto's XTSE stands in for it: the same holidays and hours."""

    CODE = "XTSE"
    NAME = "Cboe Canada"
```

- [ ] **Step 4: Write the entry window**

Create `src/signalbench/live/sizing.py`:

```python
"""Where and how big an entry is placed on the CDR (spec 05, Live CDR sizing).

Pure: Decimal in, Decimal out, no database. The entry session is the next Cboe Canada session
after the signal (the XTSE calendar as the proxy), and the signal expires at its close.
"""

from dataclasses import dataclass
from datetime import date, datetime

from signalbench.market.calendar import Sessions


@dataclass(frozen=True)
class EntryWindow:
    session: date  # the Cboe Canada session to place the order in
    expires_at: datetime  # that session's close
    note: str | None  # set when Cboe Canada is closed on the next NYSE session


def _day(day: date) -> str:
    return f"{day:%a} {day.day:02d} {day:%b}"


def entry_window(as_of: date, nyse: Sessions, cboe: Sessions) -> EntryWindow:
    """The next Cboe Canada session after the signal session `as_of`. When Cboe Canada is
    closed on the next NYSE session, the entry and the expiry move to its next session."""
    session = cboe.next_sessions(as_of, 1)[0]
    [(_, close)] = cboe.session_closes(session, session)
    us_next = nyse.next_sessions(as_of, 1)[0]
    note = None
    if session > us_next:
        note = f"Cboe Canada is closed on {_day(us_next)}: place it on {_day(session)}."
    return EntryWindow(session=session, expires_at=close, note=note)
```

Task 3 replaces this file with the sizing added.

- [ ] **Step 5: Run the tests and checks**

Run: `uv run pytest tests/test_live_entry.py tests/test_calendar.py -q && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 8 passed (4 new, and the 4 calendar tests); the full suite 712 passed; ruff `All checks passed!`; mypy `Success: no issues found in 75 source files`.

- [ ] **Step 6: Commit**

```bash
git add src/signalbench/market/calendar.py src/signalbench/live/sizing.py tests/test_live_entry.py
git commit -m "feat: Cboe Canada sessions and the entry window

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Live CDR sizing: whole units, rounding up, or a fractional order

**Files:**
- Modify: `src/signalbench/live/sizing.py`
- Create: `tests/test_live_sizing.py`

Every case is exact Decimal arithmetic on `signal_stop()` (spec 04), checked by hand in the test comments. At C$100 of equity the risk budget is C$2.00 and a third of equity is C$33.33.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_live_sizing.py`:

```python
"""Spec 05, Live CDR sizing: the whole-unit rule's three branches, the caps, and the 2.5% risk
bound on rounding up. Every number is exact Decimal arithmetic, checked by hand."""

from decimal import Decimal

from signalbench.live.sizing import CdrSize, size_cdr

RISK = Decimal("0.02")


def _size(
    us_close: str, us_stop: str, cdr_close: str, equity: str = "100", cash: str | None = None
) -> CdrSize | None:
    return size_cdr(
        us_signal_close=Decimal(us_close), us_stop=Decimal(us_stop),
        cdr_close=Decimal(cdr_close), equity=Decimal(equity),
        uncommitted_cash=Decimal(equity if cash is None else cash), risk_pct=RISK,
        max_positions=3,
    )


def test_floor_whole_units_with_a_limit_one_percent_over() -> None:
    # stop 4%: cdr_stop 9.70, 0.40 a unit; 2.00 / 0.40 = 5 units, capped at 33.33 / 10.10 = 3.30
    size = _size("101", "97", "10.10")
    assert size is not None
    assert (size.rule, size.order_type, size.units) == ("floor", "limit", Decimal(3))
    assert (size.cdr_stop, size.limit) == (Decimal("9.7000"), Decimal("10.20"))  # 10.201 down
    assert (size.risk, size.cost) == (Decimal("1.2000"), Decimal("30.30"))


def test_the_spec_example_at_c120_is_one_unit() -> None:
    size = _size("200", "188", "38.40", equity="120")  # 1.04 units: floor 1 is >= 75% of it
    assert size is not None
    assert (size.rule, size.units, size.limit) == ("floor", Decimal(1), Decimal("38.78"))
    assert (size.cdr_stop, size.stop_pct, size.risk) == (
        Decimal("36.0960"), Decimal("0.06000000"), Decimal("2.3040")
    )


def test_ceil_when_the_floor_is_under_75_percent_and_the_risk_stays_within_2_5_percent() -> None:
    # stop 8%: 1.20 a unit, 2.00 / 1.20 = 1.67 units; floor 1 < 1.25, so ceil 2: risk 2.40
    size = _size("100", "92", "15")
    assert size is not None
    assert (size.rule, size.order_type, size.units) == ("ceil", "limit", Decimal(2))
    assert (size.risk, size.cost, size.limit) == (Decimal("2.4000"), Decimal(30), Decimal("15.15"))


def test_fractional_when_rounding_up_would_risk_more_than_2_5_percent() -> None:
    # stop 10%: 3.00 a unit, 0.67 units; 1 unit would risk 3.00 > 2.50
    size = _size("100", "90", "30")
    assert size is not None
    assert (size.rule, size.order_type, size.units) == ("fractional", "market", Decimal("0.666666"))
    assert size.limit == Decimal("30.30")


def test_fractional_when_one_unit_is_worth_more_than_a_third_of_equity() -> None:
    size = _size("200", "188", "38.40")  # the spec example at C$100: 1 unit is C$38.40 > 33.33
    assert size is not None
    assert (size.rule, size.units) == ("fractional", Decimal("0.868055"))  # 33.33 / 38.40
    assert size.cost < Decimal(100) / 3


def test_uncommitted_cash_caps_the_size_and_none_left_means_no_order() -> None:
    size = _size("101", "97", "10.10", cash="20")  # 20 / 10.10 = 1.98; 2 units cost 20.20
    assert size is not None
    assert (size.rule, size.units) == ("fractional", Decimal("1.980198"))
    assert _size("101", "97", "10.10", cash="0") is None
    assert _size("101", "97", "10.10", cash="-5") is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_live_sizing.py -q`
Expected: collection error, `ImportError: cannot import name 'CdrSize' from 'signalbench.live.sizing'`.

- [ ] **Step 3: Add the sizing**

Replace the whole of `src/signalbench/live/sizing.py` with:

```python
"""Where and how big an entry is placed on the CDR (spec 05, Live CDR sizing).

Pure: Decimal in, Decimal out, no database. The entry session is the next Cboe Canada session
after the signal (the XTSE calendar as the proxy), and the signal expires at its close.

The size risks `risk_pct` of equity between the CDR close and the CDR stop (the US stop's
distance, applied to the CDR close), capped at equity / max_positions and at the cash not
already promised to pending signals. Whole units are preferred:
- floor: the whole units below the target, when at least 1 and at least 75% of the target;
- ceil: else the whole units above it, when they risk at most 2.5% of equity and fit the caps;
- fractional: else the target itself, as a market order for a dollar amount.
Whole units are a limit order at the CDR close + 1%; a fractional order is placed only when
the price is at or below that same level.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, Decimal
from typing import Literal

from signalbench.live.book import OrderType, signal_stop
from signalbench.market.calendar import Sessions

SizeRule = Literal["floor", "ceil", "fractional"]
WHOLE_SHARE = Decimal("0.75")  # floor is used when it is at least 75% of the target
CEIL_RISK = Decimal("0.025")  # ceil is used when it risks at most 2.5% of equity
LIMIT_FACTOR = Decimal("1.01")  # the limit (and the fractional price bound): 1% over the close
CENT = Decimal("0.01")
UNIT_STEP = Decimal("0.000001")  # units are stored at 6 decimals
ZERO = Decimal(0)


@dataclass(frozen=True)
class EntryWindow:
    session: date  # the Cboe Canada session to place the order in
    expires_at: datetime  # that session's close
    note: str | None  # set when Cboe Canada is closed on the next NYSE session


@dataclass(frozen=True)
class CdrSize:
    rule: SizeRule
    order_type: OrderType  # limit for whole units, market for a fractional order
    units: Decimal
    cdr_close: Decimal
    cdr_stop: Decimal  # cdr_close x (1 - stop_pct)
    stop_pct: Decimal  # (us_signal_close - us_stop) / us_signal_close
    limit: Decimal  # cdr_close x 1.01, rounded down to the cent

    @property
    def risk(self) -> Decimal:
        """The planned risk: units x (CDR close - CDR stop)."""
        return self.units * (self.cdr_close - self.cdr_stop)

    @property
    def cost(self) -> Decimal:
        return self.units * self.cdr_close


def _day(day: date) -> str:
    return f"{day:%a} {day.day:02d} {day:%b}"


def entry_window(as_of: date, nyse: Sessions, cboe: Sessions) -> EntryWindow:
    """The next Cboe Canada session after the signal session `as_of`. When Cboe Canada is
    closed on the next NYSE session, the entry and the expiry move to its next session."""
    session = cboe.next_sessions(as_of, 1)[0]
    [(_, close)] = cboe.session_closes(session, session)
    us_next = nyse.next_sessions(as_of, 1)[0]
    note = None
    if session > us_next:
        note = f"Cboe Canada is closed on {_day(us_next)}: place it on {_day(session)}."
    return EntryWindow(session=session, expires_at=close, note=note)


def size_cdr(
    *,
    us_signal_close: Decimal,
    us_stop: Decimal,
    cdr_close: Decimal,
    equity: Decimal,
    uncommitted_cash: Decimal,
    risk_pct: Decimal,
    max_positions: int,
) -> CdrSize | None:
    """The entry's CDR order, or None when nothing fits (no uncommitted cash)."""
    stop_pct, cdr_stop = signal_stop(us_signal_close, us_stop, cdr_close)
    per_unit = cdr_close - cdr_stop
    cap = min(equity / max_positions, max(uncommitted_cash, ZERO))
    target = min(risk_pct * equity / per_unit, cap / cdr_close)
    if target <= 0:
        return None
    limit = (cdr_close * LIMIT_FACTOR).quantize(CENT, rounding=ROUND_DOWN)

    def order(rule: SizeRule, units: Decimal) -> CdrSize:
        return CdrSize(
            rule=rule, order_type="market" if rule == "fractional" else "limit", units=units,
            cdr_close=cdr_close, cdr_stop=cdr_stop, stop_pct=stop_pct, limit=limit,
        )

    whole = target.to_integral_value(rounding=ROUND_FLOOR)
    if whole >= 1 and whole >= WHOLE_SHARE * target:
        return order("floor", whole)
    up = target.to_integral_value(rounding=ROUND_CEILING)
    if up * per_unit <= CEIL_RISK * equity and up * cdr_close <= cap:
        return order("ceil", up)
    units = target.quantize(UNIT_STEP, rounding=ROUND_DOWN)
    return order("fractional", units) if units > 0 else None
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_live_sizing.py tests/test_live_entry.py -q && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 10 passed (6 new, and the 4 of Task 2); the full suite 718 passed; ruff `All checks passed!`; mypy `Success: no issues found in 75 source files`.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/live/sizing.py tests/test_live_sizing.py
git commit -m "feat: live CDR sizing with the whole-unit rule

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The template Why line

**Files:**
- Create: `src/signalbench/live/why.py`, `tests/test_live_why.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_live_why.py`:

```python
"""Spec 05, the "Why" line: a fixed template filled from the Snapshot that fired Breakout, with
the lookbacks from the config. Pure: no network, no model."""

from dataclasses import replace
from pathlib import Path

import pytest

from signalbench.live.why import why_line
from signalbench.strategy.config import load_strategy_config
from strategy_helpers import make_snapshot

CONFIG = load_strategy_config(Path(__file__).parents[1] / "data" / "strategy_v2-none-cash.yaml")[0]


def test_the_spec_example() -> None:
    snap = make_snapshot(close=181.515, volume=2_300_000.0, prior_mean_volume=1_000_000.0)
    assert why_line(snap, CONFIG.breakout) == (
        "Closed at a 20-session high (US$181.52) on 2.3× its 50-session average volume, "
        "above its 50-session average."
    )


def test_rounding_is_half_up_on_the_printed_value() -> None:
    snap = make_snapshot(close=1830.125, volume=2_250_000.0, prior_mean_volume=1_000_000.0)
    assert why_line(snap, CONFIG.breakout) == (
        "Closed at a 20-session high (US$1,830.13) on 2.3× its 50-session average volume, "
        "above its 50-session average."
    )
    snap = make_snapshot(close=99.994, volume=1_549_999.0, prior_mean_volume=1_000_000.0)
    assert "(US$99.99) on 1.5×" in why_line(snap, CONFIG.breakout)


def test_the_lookbacks_come_from_the_config() -> None:
    params = replace(CONFIG.breakout, lookback=55, volume_lookback=30, trend_sma=100)
    snap = make_snapshot(close=50.0, volume=3_000_000.0, prior_mean_volume=1_000_000.0)
    assert why_line(snap, params) == (
        "Closed at a 55-session high (US$50.00) on 3.0× its 30-session average volume, "
        "above its 100-session average."
    )


def test_a_snapshot_without_a_volume_average_is_refused() -> None:
    with pytest.raises(ValueError, match="volume average"):
        why_line(make_snapshot(prior_mean_volume=None), CONFIG.breakout)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_live_why.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'signalbench.live.why'`.

- [ ] **Step 3: Write the template**

Create `src/signalbench/live/why.py`:

```python
"""The "Why" line of an entry (spec 05): a fixed template, no LLM.

Filled from the same Snapshot values that fired the Breakout rule (the signal close, its
volume, and the prior mean volume), with the lookbacks from the config. Pure: no network and
no model. The line is stored in `trade_signals.explanation`.
"""

from decimal import ROUND_HALF_UP, Decimal

from signalbench.strategy.config import BreakoutParams
from signalbench.strategy.market_view import Snapshot


def _half_up(value: float, step: str) -> Decimal:
    """`value` rounded half up on its shortest decimal form (181.515 -> 181.52)."""
    return Decimal(repr(value)).quantize(Decimal(step), rounding=ROUND_HALF_UP)


def why_line(snapshot: Snapshot, breakout: BreakoutParams) -> str:
    if not snapshot.prior_mean_volume:
        raise ValueError(f"no prior volume average on {snapshot.date}: Breakout cannot have fired")
    close = _half_up(snapshot.close, "0.01")
    ratio = _half_up(snapshot.volume / snapshot.prior_mean_volume, "0.1")
    return (
        f"Closed at a {breakout.lookback}-session high (US${close:,}) on {ratio}× its "
        f"{breakout.volume_lookback}-session average volume, above its "
        f"{breakout.trend_sma}-session average."
    )
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_live_why.py -q && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 4 passed; the full suite 722 passed; ruff `All checks passed!`; mypy `Success: no issues found in 76 source files`.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/live/why.py tests/test_live_why.py
git commit -m "feat: the template Why line

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: python-telegram-bot and the `Messenger`

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (with `uv add`), `src/signalbench/config.py`
- Create: `src/signalbench/live/messenger.py`, `tests/telegram_helpers.py`, `tests/test_live_messenger.py`

- [ ] **Step 1: Add the dependency**

The one network step before the ⛔ tasks: `uv add` resolves the package from PyPI.

Run: `uv add "python-telegram-bot>=22.8,<23"`
Expected: exit 0, ending `+ python-telegram-bot==22.8`; `pyproject.toml` gains `"python-telegram-bot>=22.8,<23",` and `uv.lock` gains the package (its one dependency, httpx, is already locked).

Run: `uv run python -c "import telegram; print(telegram.__version__, telegram.__bot_api_version__)"`
Expected: `22.8 10.0`.

- [ ] **Step 2: Write the failing tests**

`tests/telegram_helpers.py` is a `BaseRequest` that answers `getMe`, `sendMessage`, `editMessageText`, and `answerCallbackQuery` from memory and records every call, so python-telegram-bot's real `Bot` runs with no network.

Create `tests/telegram_helpers.py`:

```python
"""A Bot API that answers from memory, for python-telegram-bot's Bot: no network, every call
recorded. Shared by the messenger and the bot tests."""

import json
from typing import Any

from telegram.request import BaseRequest, RequestData

TOKEN = "123456:TEST-TOKEN"  # the shape of a real token; never sent anywhere
CHAT = 4242  # the owner's chat in these tests


class FakeTelegram(BaseRequest):
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._next_id = 500

    async def initialize(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    @property
    def read_timeout(self) -> float | None:
        return 1.0

    async def do_request(
        self, url: str, method: str, request_data: RequestData | None = None, **_: Any
    ) -> tuple[int, bytes]:
        endpoint = url.rsplit("/", 1)[-1]
        params = dict(request_data.parameters) if request_data is not None else {}
        self.calls.append((endpoint, params))
        return 200, json.dumps({"ok": True, "result": self._result(endpoint, params)}).encode()

    def _result(self, endpoint: str, params: dict[str, Any]) -> object:
        if endpoint == "getMe":
            return {"id": 1, "is_bot": True, "first_name": "SignalBench",
                    "username": "signalbench_test_bot"}
        if endpoint == "sendMessage":
            self._next_id += 1
            return {"message_id": self._next_id, "date": 0, "text": params["text"],
                    "chat": {"id": params["chat_id"], "type": "private"}}
        if endpoint == "editMessageText":
            return {"message_id": params["message_id"], "date": 0, "text": params["text"],
                    "chat": {"id": params["chat_id"], "type": "private"}}
        return True  # answerCallbackQuery

    def sent(self, endpoint: str) -> list[dict[str, Any]]:
        return [params for name, params in self.calls if name == endpoint]
```

Create `tests/test_live_messenger.py`:

```python
"""Spec 05, Telegram and processes: the Messenger protocol, its fake and console versions, and
TelegramMessenger against a Bot API that answers from memory (no network)."""

from typing import Any

import pytest
from telegram.error import NetworkError

from signalbench.live.messenger import (
    MAX_TEXT,
    Button,
    ConsoleMessenger,
    FakeMessenger,
    Messenger,
    TelegramMessenger,
    chunks,
)
from telegram_helpers import CHAT, TOKEN, FakeTelegram

ROW = ((Button("✅ I bought", "b:12"), Button("⏭ Skip", "s:12")),)


def test_button_data_must_fit_telegrams_64_bytes() -> None:
    Button("ok", "x" * 64)
    with pytest.raises(ValueError, match="1-64 bytes"):
        Button("too long", "x" * 65)
    with pytest.raises(ValueError, match="1-64 bytes"):
        Button("multi-byte", "é" * 33)  # 66 bytes in UTF-8
    with pytest.raises(ValueError, match="1-64 bytes"):
        Button("empty", "")


def test_long_text_is_cut_on_line_breaks_into_telegram_sized_pieces() -> None:
    line = "x" * 1000
    text = "\n".join([line] * 9)  # 9008 characters
    pieces = chunks(text)
    assert [len(piece) for piece in pieces] == [4003, 4003, 1000]
    assert "\n".join(pieces) == text
    assert chunks("y" * (MAX_TEXT + 5)) == ["y" * MAX_TEXT, "y" * 5]


def test_the_fake_records_sends_and_edits_and_can_fail() -> None:
    fake = FakeMessenger()
    messenger: Messenger = fake
    first = messenger.send("hello", ROW)
    messenger.edit(first, "hello again")
    assert [(m.message_id, m.text, m.buttons) for m in fake.sent] == [(first, "hello", ROW)]
    assert fake.edits == [(first, "hello again", ())]
    fake.fail = True
    with pytest.raises(ConnectionError):
        messenger.send("lost")
    assert fake.texts() == ["hello"]


def test_the_console_messenger_prints_each_message_and_its_buttons() -> None:
    lines: list[str] = []
    console = ConsoleMessenger(lines.append)
    assert console.send("line one\nline two", ROW) == 1
    console.edit(1, "changed")
    assert lines == [
        "--- message 1 ---", "line one\nline two", "[✅ I bought] [⏭ Skip]",
        "--- edit of message 1 ---", "changed",
    ]


def test_telegram_messenger_sends_and_edits_through_the_bot_api() -> None:
    api = FakeTelegram()
    messenger = TelegramMessenger(TOKEN, CHAT, request=api)
    assert api.calls == []  # it connects on the first send, so a scan runs without Telegram
    message_id = messenger.send("🟢 BUY", ROW)
    messenger.edit(message_id, "🟢 BUY\n✅ taken")
    messenger.close()
    assert [name for name, _ in api.calls] == ["getMe", "sendMessage", "editMessageText"]
    [sent] = api.sent("sendMessage")
    assert sent == {
        "chat_id": CHAT, "text": "🟢 BUY",
        "reply_markup": {"inline_keyboard": [[
            {"text": "✅ I bought", "callback_data": "b:12"},
            {"text": "⏭ Skip", "callback_data": "s:12"},
        ]]},
    }
    [edited] = api.sent("editMessageText")
    assert (edited["message_id"], edited["text"], "reply_markup" in edited) == (
        message_id, "🟢 BUY\n✅ taken", False
    )


def test_telegram_messenger_splits_a_long_message_and_buttons_go_on_the_last_piece() -> None:
    api = FakeTelegram()
    messenger = TelegramMessenger(TOKEN, CHAT, request=api)
    last = messenger.send("\n".join(["z" * 3000] * 2), ROW)
    messenger.close()
    first, second = api.sent("sendMessage")
    assert ("reply_markup" in first, "reply_markup" in second) == (False, True)
    assert last == 502  # the second message's id: its buttons are the ones pressed later


def test_a_failed_connection_is_tried_again_by_the_next_send() -> None:
    class Offline(FakeTelegram):
        def _result(self, endpoint: str, params: dict[str, Any]) -> object:
            if endpoint == "getMe" and len(self.sent("getMe")) == 1:
                raise ConnectionError("no network")
            return super()._result(endpoint, params)

    api = Offline()
    messenger = TelegramMessenger(TOKEN, CHAT, request=api)
    with pytest.raises(NetworkError, match="no network"):
        messenger.send("lost")
    assert messenger.send("sent") == 501
    messenger.close()
    assert [name for name, _ in api.calls] == ["getMe", "getMe", "sendMessage"]
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest tests/test_live_messenger.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'signalbench.live.messenger'`.

- [ ] **Step 4: Add the Telegram settings**

In `src/signalbench/config.py`, replace:

```python
    openrouter_api_key: str | None = None
```

with:

```python
    openrouter_api_key: str | None = None
    telegram_bot_token: str | None = None  # from @BotFather (spec 05); never logged
    telegram_chat_id: int | None = None  # the owner's chat: the bot's allowlist
```

- [ ] **Step 5: Write the messengers**

Create `src/signalbench/live/messenger.py`:

```python
"""How the evening scan talks to Telegram (spec 05, Telegram and processes).

A `Messenger` sends a message with inline buttons and returns its id, and edits a message
later. `TelegramMessenger` sends through the Bot API with python-telegram-bot; `FakeMessenger`
records messages for tests; `ConsoleMessenger` prints them (`scan --dry-run`). Button presses
and commands are handled only by the bot process (live/telegram_bot.py).
"""

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Protocol, TypeVar

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.request import BaseRequest

MAX_TEXT = 4096  # Bot API: a message is 1-4096 characters
MAX_CALLBACK_BYTES = 64  # Bot API: callback_data is 1-64 bytes
T = TypeVar("T")


@dataclass(frozen=True)
class Button:
    """An inline button. `data` comes back to the bot when it is pressed."""

    text: str
    data: str

    def __post_init__(self) -> None:
        if not 1 <= len(self.data.encode()) <= MAX_CALLBACK_BYTES:
            raise ValueError(f"button data must be 1-64 bytes in UTF-8, not {self.data!r}")


Buttons = tuple[tuple[Button, ...], ...]  # rows of buttons


class Messenger(Protocol):
    def send(self, text: str, buttons: Buttons = ()) -> int:
        """Send a message; returns its Telegram message id."""
        ...

    def edit(self, message_id: int, text: str, buttons: Buttons = ()) -> None:
        """Replace a message's text and buttons (no buttons removes them)."""
        ...


def chunks(text: str, limit: int = MAX_TEXT) -> list[str]:
    """`text` cut on line breaks into pieces of at most `limit` characters (a longer line is
    cut where it must be)."""
    pieces: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(line[:limit])
            line = line[limit:]
        joined = f"{current}\n{line}" if current else line
        if current and len(joined) > limit:
            pieces.append(current)
            current = line
        else:
            current = joined
    return [*pieces, current] if current or not pieces else pieces


def keyboard(buttons: Buttons) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(b.text, callback_data=b.data) for b in row] for row in buttons]
    )


@dataclass(frozen=True)
class Sent:
    message_id: int
    text: str
    buttons: Buttons


@dataclass
class FakeMessenger:
    """Records messages; `fail` makes every send raise, as if Telegram were unreachable."""

    fail: bool = False
    sent: list[Sent] = field(default_factory=list)
    edits: list[tuple[int, str, Buttons]] = field(default_factory=list)
    next_id: int = 100

    def send(self, text: str, buttons: Buttons = ()) -> int:
        if self.fail:
            raise ConnectionError("Telegram is unreachable (FakeMessenger)")
        self.next_id += 1
        self.sent.append(Sent(self.next_id, text, buttons))
        return self.next_id

    def edit(self, message_id: int, text: str, buttons: Buttons = ()) -> None:
        if self.fail:
            raise ConnectionError("Telegram is unreachable (FakeMessenger)")
        self.edits.append((message_id, text, buttons))

    def texts(self) -> list[str]:
        return [message.text for message in self.sent]


class ConsoleMessenger:
    """Prints every message and its buttons (`scan --dry-run`); nothing is sent."""

    def __init__(self, echo: Callable[[str], None]) -> None:
        self._echo = echo
        self._count = 0

    def send(self, text: str, buttons: Buttons = ()) -> int:
        self._count += 1
        self._echo(f"--- message {self._count} ---")
        self._echo(text)
        for row in buttons:
            self._echo(" ".join(f"[{b.text}]" for b in row))
        return self._count

    def edit(self, message_id: int, text: str, buttons: Buttons = ()) -> None:
        self._echo(f"--- edit of message {message_id} ---")
        self._echo(text)
        for row in buttons:
            self._echo(" ".join(f"[{b.text}]" for b in row))


class TelegramMessenger:
    """The Bot API through python-telegram-bot's Bot, run on a private event loop so the
    synchronous scan can call it. It connects on the first send, so a scan still runs, and
    records its rows, when Telegram is down (the next scan sends what was not sent); a failed
    connection is tried again by the next call."""

    def __init__(self, token: str, chat_id: int, *, request: BaseRequest | None = None) -> None:
        self._bot = Bot(token, request=request)
        self._chat_id = chat_id
        self._loop = asyncio.new_event_loop()
        self._ready = False

    def _run(self, make: Callable[[], Coroutine[Any, Any, T]]) -> T:
        if not self._ready:
            self._loop.run_until_complete(self._bot.initialize())  # getMe checks the token
            self._ready = True
        return self._loop.run_until_complete(make())

    def send(self, text: str, buttons: Buttons = ()) -> int:
        pieces = chunks(text)
        message_id = 0
        for index, piece in enumerate(pieces):
            markup = keyboard(buttons) if index == len(pieces) - 1 else None
            message = self._run(
                partial(self._bot.send_message, self._chat_id, piece, reply_markup=markup)
            )
            message_id = message.message_id
        return message_id

    def edit(self, message_id: int, text: str, buttons: Buttons = ()) -> None:
        self._run(
            partial(
                self._bot.edit_message_text, chunks(text)[0], chat_id=self._chat_id,
                message_id=message_id, reply_markup=keyboard(buttons),
            )
        )

    def close(self) -> None:
        if self._ready:
            self._loop.run_until_complete(self._bot.shutdown())
            self._ready = False
        self._loop.close()
```

- [ ] **Step 6: Run the tests and checks**

Run: `uv run pytest tests/test_live_messenger.py -q && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 7 passed; the full suite 729 passed; ruff `All checks passed!`; mypy `Success: no issues found in 77 source files`.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/signalbench/config.py src/signalbench/live/messenger.py tests/telegram_helpers.py tests/test_live_messenger.py
git commit -m "feat: the Messenger protocol with Telegram, fake, and console messengers

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The message templates and button data

**Files:**
- Create: `src/signalbench/live/messages.py`, `tests/test_live_messages.py`

The spec's own examples (NVDA at C$38.40, stop C$36.10) are the expected texts.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_live_messages.py`:

```python
"""Spec 05, Messages: the entry, stop-raised, split, exit, and failure texts and their buttons,
checked against the spec's examples."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from signalbench.live.messages import (
    CdrSplitText,
    EntryText,
    ExitText,
    RaiseText,
    SplitStopText,
    cad,
    cdr_split_message,
    entry_buttons,
    entry_message,
    exit_buttons,
    exit_message,
    failure_message,
    pct,
    raise_message,
    ratio_text,
    skip_buttons,
    split_stop_message,
    usd,
)

WHY = (
    "Closed at a 20-session high (US$181.52) on 2.3× its 50-session average volume, above its "
    "50-session average."
)
ENTRY = EntryText(
    us_symbol="NVDA", cdr_symbol="ZNVD", company="NVIDIA", cdr_close=Decimal("38.40"),
    cdr_stop=Decimal("36.0960"), stop_pct=Decimal("0.06"), units=Decimal(1),
    order_type="limit", limit=Decimal("38.78"), risk=Decimal("2.3040"), why=WHY, note=None,
)
EXIT = ExitText(
    us_symbol="NVDA", cdr_symbol="ZNVD", reason="stop", us_close=Decimal("171.20"),
    us_stop=Decimal(172), cdr_mark=Decimal("35.90"), cdr_stop=Decimal("36.10"),
    earnings_on=None, sessions_held=4, unrealized=Decimal("-2.60"),
    unrealized_pct=Decimal("-0.068"), late_after=None,
)


def test_money_and_percent_formats() -> None:
    assert (cad(Decimal("38.4")), cad(Decimal("-2.604")), cad(Decimal("1234.5"))) == (
        "C$38.40", "−C$2.60", "C$1,234.50"
    )
    assert usd(Decimal(1830)) == "US$1,830.00"
    assert (pct(Decimal("-0.06")), pct(Decimal("0.0214")), pct(Decimal(0))) == (
        "−6.0%", "+2.1%", "0.0%"
    )
    assert [ratio_text(Decimal(r)) for r in ("10", "0.1", "1.5", "2")] == [
        "10-for-1", "1-for-10", "3-for-2", "2-for-1"
    ]


def test_the_entry_message_of_the_spec() -> None:
    assert entry_message(ENTRY) == (
        "🟢 BUY NVDA (CDR ZNVD) — Breakout · NVIDIA\n"
        "Signal C$38.40 · Stop C$36.10 (−6.0%) · Trailing stop, no target, no time limit\n"
        "Size 1 unit (~C$38.40) · risk C$2.30 · LIMIT C$38.78\n"
        "Skip if price > C$38.78, price ≤ the stop C$36.10, or bid/ask spread > 0.5%\n"
        f"Why: {WHY}"
    )


def test_a_fractional_entry_shows_the_dollar_amount_and_the_price_bound() -> None:
    text = entry_message(
        replace(ENTRY, units=Decimal("0.868055"), order_type="market", risk=Decimal(2))
    )
    assert text.splitlines()[2] == (
        "Size C$33.33 (0.868055 units) · risk C$2.00 · MARKET (fractional): only place it if "
        "the price is ≤ C$38.78"
    )


def test_an_entry_moved_by_a_cboe_holiday_says_so() -> None:
    note = "Cboe Canada is closed on Mon 12 Oct: place it on Tue 13 Oct."
    lines = entry_message(replace(ENTRY, units=Decimal(3), note=note)).splitlines()
    assert lines[2].startswith("Size 3 units (~C$115.20)")
    assert lines[4] == note


def test_buttons_carry_short_callback_data() -> None:
    assert [[(b.text, b.data) for b in row] for row in entry_buttons(12)] == [
        [("✅ I bought", "b:12"), ("⏭ Skip", "s:12")]
    ]
    assert [[(b.text, b.data) for b in row] for row in exit_buttons(7)] == [
        [("✅ Sold", "x:7"), ("Ignore", "i:7")]
    ]
    assert [b.data for row in skip_buttons(12) for b in row] == [
        "k:12:disagree", "k:12:no_time", "k:12:price_moved", "k:12:wide_spread", "k:12:other"
    ]
    assert [b.text for row in skip_buttons(12) for b in row] == [
        "Disagree", "No time", "Price moved >1%", "Spread too wide", "Other"
    ]


def test_the_stop_raised_message_in_both_currencies() -> None:
    text = RaiseText(us_symbol="NVDA", cdr_symbol="ZNVD", old_cdr=Decimal("36.10"),
                     new_cdr=Decimal("38.40"), old_us=Decimal(172), new_us=Decimal("183.10"),
                     late_for=None)
    assert raise_message(text) == (
        "⬆️ NVDA (CDR ZNVD) stop raised C$36.10 → C$38.40 (US$172.00 → US$183.10) · still "
        "holding, no action"
    )
    late = replace(text, late_for=date(2026, 10, 6))
    assert raise_message(late).endswith("still holding, no action (late — for 2026-10-06)")


def test_split_messages() -> None:
    us = SplitStopText(symbol="NVDA", ratio=Decimal(10), ex_date=date(2026, 6, 10),
                       old=Decimal(1830), new=Decimal(183), currency="US$")
    assert split_stop_message(us) == (
        "ℹ️ NVDA split 10-for-1 (ex-date 2026-06-10): stop US$1,830.00 → US$183.00 · no action"
    )
    cdr = CdrSplitText(cdr_symbol="ZNVD", ratio=Decimal(2), ex_date=date(2026, 10, 5),
                       units_before=Decimal(3), units_after=Decimal(6),
                       per_unit_before=Decimal(30), per_unit_after=Decimal(15), withdrawn=(12,))
    assert cdr_split_message(cdr) == (
        "ℹ️ ZNVD split 2-for-1 (ex-date 2026-10-05): 3 units → 6, ACB per unit C$30.00 → "
        "C$15.00 (the total ACB is unchanged) · check that Wealthsimple shows 6 units · no action\n"
        "Signal 12 withdrawn: its prices no longer apply."
    )
    none_held = replace(cdr, units_before=Decimal(0), units_after=Decimal(0),
                        per_unit_before=None, per_unit_after=None)
    assert cdr_split_message(none_held) == (
        "ℹ️ ZNVD split 2-for-1 (ex-date 2026-10-05) · no action\n"
        "Signal 12 withdrawn: its prices no longer apply."
    )


def test_the_exit_message_of_the_spec() -> None:
    assert exit_message(EXIT) == (
        "🔴 SELL NVDA (CDR ZNVD) — stop hit (US close US$171.20 ≤ stop US$172.00 · CDR ~C$35.90 "
        "vs stop C$36.10)\n"
        "Held 4 sessions · unrealized −C$2.60 (−6.8%)\n"
        "Sell at the open."
    )


def test_the_earnings_and_late_exit_variants() -> None:
    earnings = exit_message(
        replace(EXIT, reason="earnings", earnings_on=date(2026, 10, 28), sessions_held=1,
                unrealized=Decimal("1.2"), unrealized_pct=Decimal("0.031"))
    )
    assert earnings.splitlines()[:2] == [
        "🔴 SELL NVDA (CDR ZNVD) — earnings on 2026-10-28, sell before them",
        "Held 1 session · unrealized +C$1.20 (+3.1%)",
    ]
    late = exit_message(replace(EXIT, late_after=date(2026, 10, 6)))
    assert late.splitlines()[0].endswith("(late — should have been sent after 2026-10-06)")


def test_the_failure_message() -> None:
    assert failure_message(2, "prices", "QQQ has no bar for 2026-10-06") == (
        "⚠️ Scan failed at step 2 (prices): QQQ has no bar for 2026-10-06"
    )
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_live_messages.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'signalbench.live.messages'`.

- [ ] **Step 3: Write the templates**

Create `src/signalbench/live/messages.py`:

```python
"""The evening scan's Telegram messages (spec 05, Messages): pure text templates.

Money is shown to the cent, rounded half up: C$ for the CDR, US$ for the US levels that
decide. A minus sign is U+2212, as in the spec. Button data is short (`b:12`) because Telegram
caps it at 64 bytes; live/bot.py reads it back.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

from signalbench.live.book import AlertReason, OrderType
from signalbench.live.messenger import Button, Buttons

MINUS = "−"
CENT = Decimal("0.01")
TENTH = Decimal("0.1")
SPREAD_LIMIT = Decimal("0.005")  # the survey's live spread limit (spec 05 changelog: 0.5%)
SKIP_REASONS = (
    ("disagree", "Disagree"),
    ("no_time", "No time"),
    ("price_moved", "Price moved >1%"),
    ("wide_spread", "Spread too wide"),
    ("other", "Other"),
)


def _money(value: Decimal, currency: str, sign: bool = False) -> str:
    q = value.quantize(CENT, rounding=ROUND_HALF_UP)
    prefix = MINUS if q < 0 else ("+" if sign and q > 0 else "")
    return f"{prefix}{currency}{abs(q):,}"


def cad(value: Decimal) -> str:
    """C$38.40, −C$2.60."""
    return _money(value, "C$")


def signed_cad(value: Decimal) -> str:
    """+C$1.20, −C$2.60, C$0.00."""
    return _money(value, "C$", sign=True)


def usd(value: Decimal) -> str:
    """US$1,830.00."""
    return _money(value, "US$")


def pct(fraction: Decimal) -> str:
    """A signed percentage to one decimal: −6.0%, +2.1%, 0.0%."""
    q = (fraction * 100).quantize(TENTH, rounding=ROUND_HALF_UP)
    prefix = MINUS if q < 0 else ("+" if q > 0 else "")
    return f"{prefix}{abs(q)}%"


def units_text(units: Decimal) -> str:
    """1 unit, 3 units, 0.868055 units."""
    shown = f"{units.normalize():f}"
    return f"{shown} unit" if units == 1 else f"{shown} units"


def ratio_text(ratio: Decimal) -> str:
    """New units per old unit as a split is named: 10-for-1, 1-for-10, 3-for-2."""
    fraction = Fraction(ratio).limit_denominator(1000)
    return f"{fraction.numerator}-for-{fraction.denominator}"


@dataclass(frozen=True)
class EntryText:
    us_symbol: str
    cdr_symbol: str
    company: str
    cdr_close: Decimal
    cdr_stop: Decimal
    stop_pct: Decimal
    units: Decimal
    order_type: OrderType
    limit: Decimal
    risk: Decimal
    why: str
    note: str | None  # the entry moved by a Cboe Canada holiday


def entry_message(e: EntryText) -> str:
    stop = cad(e.cdr_stop)
    if e.order_type == "limit":
        size = (
            f"Size {units_text(e.units)} (~{cad(e.units * e.cdr_close)}) · risk {cad(e.risk)} · "
            f"LIMIT {cad(e.limit)}"
        )
    else:
        size = (
            f"Size {cad(e.units * e.cdr_close)} ({units_text(e.units)}) · risk {cad(e.risk)} · "
            f"MARKET (fractional): only place it if the price is ≤ {cad(e.limit)}"
        )
    spread = (SPREAD_LIMIT * 100).quantize(TENTH)
    lines = [
        f"🟢 BUY {e.us_symbol} (CDR {e.cdr_symbol}) — Breakout · {e.company}",
        (
            f"Signal {cad(e.cdr_close)} · Stop {stop} ({pct(-e.stop_pct)}) · Trailing stop, "
            "no target, no time limit"
        ),
        size,
        f"Skip if price > {cad(e.limit)}, price ≤ the stop {stop}, or bid/ask spread > {spread}%",
    ]
    if e.note is not None:
        lines.append(e.note)
    lines.append(f"Why: {e.why}")
    return "\n".join(lines)


def entry_buttons(signal_id: int) -> Buttons:
    return ((Button("✅ I bought", f"b:{signal_id}"), Button("⏭ Skip", f"s:{signal_id}")),)


def skip_buttons(signal_id: int) -> Buttons:
    """The skip reasons (spec 04's skip_reason values)."""
    buttons = [Button(text, f"k:{signal_id}:{reason}") for reason, text in SKIP_REASONS]
    return (tuple(buttons[:3]), tuple(buttons[3:]))


@dataclass(frozen=True)
class RaiseText:
    us_symbol: str
    cdr_symbol: str
    old_cdr: Decimal
    new_cdr: Decimal
    old_us: Decimal
    new_us: Decimal
    late_for: date | None  # the session a catch-up raise was decided at


def raise_message(r: RaiseText) -> str:
    text = (
        f"⬆️ {r.us_symbol} (CDR {r.cdr_symbol}) stop raised {cad(r.old_cdr)} → {cad(r.new_cdr)} "
        f"({usd(r.old_us)} → {usd(r.new_us)}) · still holding, no action"
    )
    return text if r.late_for is None else f"{text} (late — for {r.late_for})"


@dataclass(frozen=True)
class SplitStopText:
    symbol: str  # the US symbol for a US split, the CDR for a CDR split
    ratio: Decimal
    ex_date: date
    old: Decimal
    new: Decimal
    currency: str  # US$ or C$


def split_stop_message(s: SplitStopText) -> str:
    return (
        f"ℹ️ {s.symbol} split {ratio_text(s.ratio)} (ex-date {s.ex_date}): stop "
        f"{_money(s.old, s.currency)} → {_money(s.new, s.currency)} · no action"
    )


@dataclass(frozen=True)
class CdrSplitText:
    cdr_symbol: str
    ratio: Decimal
    ex_date: date
    units_before: Decimal
    units_after: Decimal
    per_unit_before: Decimal | None
    per_unit_after: Decimal | None
    withdrawn: tuple[int, ...]  # sent signals whose prices no longer apply


def cdr_split_message(s: CdrSplitText) -> str:
    head = f"ℹ️ {s.cdr_symbol} split {ratio_text(s.ratio)} (ex-date {s.ex_date})"
    if s.per_unit_before is not None and s.per_unit_after is not None:
        after = f"{s.units_after.normalize():f}"
        head += (
            f": {units_text(s.units_before)} → {after}, ACB per unit "
            f"{cad(s.per_unit_before)} → {cad(s.per_unit_after)} (the total ACB is unchanged) · "
            f"check that Wealthsimple shows {after} units"
        )
    lines = [f"{head} · no action"]
    lines += [f"Signal {i} withdrawn: its prices no longer apply." for i in s.withdrawn]
    return "\n".join(lines)


@dataclass(frozen=True)
class ExitText:
    us_symbol: str
    cdr_symbol: str
    reason: AlertReason
    us_close: Decimal
    us_stop: Decimal  # the stop in force during the session
    cdr_mark: Decimal | None
    cdr_stop: Decimal
    earnings_on: date | None
    sessions_held: int
    unrealized: Decimal
    unrealized_pct: Decimal
    late_after: date | None  # a catch-up or late alert: the session it was due after


def exit_message(x: ExitText) -> str:
    head = f"🔴 SELL {x.us_symbol} (CDR {x.cdr_symbol}) — "
    if x.reason == "stop":
        mark = "n/a" if x.cdr_mark is None else f"~{cad(x.cdr_mark)}"
        head += (
            f"stop hit (US close {usd(x.us_close)} ≤ stop {usd(x.us_stop)} · CDR {mark} vs "
            f"stop {cad(x.cdr_stop)})"
        )
    else:
        head += f"earnings on {x.earnings_on}, sell before them"
    if x.late_after is not None:
        head += f" (late — should have been sent after {x.late_after})"
    held = "1 session" if x.sessions_held == 1 else f"{x.sessions_held} sessions"
    return "\n".join([
        head,
        f"Held {held} · unrealized {signed_cad(x.unrealized)} ({pct(x.unrealized_pct)})",
        "Sell at the open.",
    ])


def exit_buttons(alert_id: int) -> Buttons:
    return ((Button("✅ Sold", f"x:{alert_id}"), Button("Ignore", f"i:{alert_id}")),)


def failure_message(step: int, name: str, error: str) -> str:
    return f"⚠️ Scan failed at step {step} ({name}): {error}"
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_live_messages.py -q && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 10 passed; the full suite 739 passed; ruff `All checks passed!`; mypy `Success: no issues found in 78 source files`.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/live/messages.py tests/test_live_messages.py
git commit -m "feat: the scan's message templates

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: The outbox and the texts built from the ledger

**Files:**
- Create: `src/signalbench/live/summary.py`, `src/signalbench/live/outbox.py`, `tests/test_live_outbox.py`

The fixture trade: a signal on 2026-06-01 (US 200 / stop 188, CDR 40 / 37.60), 2 units bought at C$40.20 the next day, a raise to US$192 at the third close, and a stop exit when the US close falls to 190. The CDR trades at a fifth of the US price, so 192 is C$38.40 and 190 is C$38.00.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_live_outbox.py`:

```python
"""Spec 05, step 9 and Messages: every unsent row goes out once, in order, rendered from the
ledger; and the texts built from the ledger (the summary, the pause review, /portfolio, /pnl)."""

from datetime import date
from decimal import Decimal

import pytest
from sqlmodel import Session, select

from live_helpers import add_pair, add_prices, make_ledger, send_signal
from paper_helpers import evening
from signalbench.db.models import ExitAlert, StopUpdateRow, TradeSignal
from signalbench.live.ledger import Ledger
from signalbench.live.messenger import Buttons, FakeMessenger
from signalbench.live.outbox import send_unsent
from signalbench.live.summary import (
    BacktestR,
    Tonight,
    evening_summary,
    pause_review,
    pnl_text,
    portfolio_text,
)
from strategy_helpers import WeekdaySessions, weekdays

DAYS = weekdays(date(2026, 6, 1), 8)  # Monday 2026-06-01 to Wednesday 2026-06-10
D0, D1, D2, D3, D4, D5 = DAYS[:6]
US = [200, 201, 204, 190, 190, 190, 190, 190]  # 190 on D3 is below the stop raised at D2
CALENDAR = WeekdaySessions()
BACKTEST = BacktestR(mean_r=Decimal("0.505"), version="v2-none-cash",
                     run_id="4123c177-1ee5-4366-9f31-993a80329e0e")


@pytest.fixture
def ledger(session: Session) -> Ledger:
    nvda, znvd = add_pair(session)
    add_prices(session, nvda, DAYS, US)
    add_prices(session, znvd, DAYS, [close / 5 for close in US], volume=100)  # ratio 0.2
    xom, zxom = add_pair(session, "XOM", "ZXOM", "Energy")
    add_prices(session, xom, DAYS, [50] * 8)
    add_prices(session, zxom, DAYS, [10] * 8, volume=100)
    ledger = make_ledger(session, today=DAYS[-1])
    ledger.record_cash(Decimal("100.00"), D0, "deposit")
    return ledger


def _trade(ledger: Ledger) -> tuple[int, int]:
    """Signal on D0 (stop 188, CDR 40 / 37.60), 2 units bought on D1, a raise to 192 at D2's
    close, and the stop exit at D3 (190 <= 192). Returns the signal and exit alert ids."""
    signal = send_signal(ledger, D0)
    assert signal.id is not None
    ledger.record_fill(cdr_symbol="ZNVD", side="buy", quantity=Decimal(2),
                       price_cad=Decimal("40.20"), trade_date=D1, signal_id=signal.id)
    for day in (D1, D2):
        ledger.record_equity(day, pause_drawdown=Decimal("0.15"))
    ledger.record_stop_update(signal_id=signal.id, session=D2, new_us_stop=Decimal(192))
    alert = ledger.record_exit_alert(signal_id=signal.id, as_of=D3, reason="stop")
    assert alert.id is not None
    return signal.id, alert.id


def _send(session: Session, ledger: Ledger, messenger: FakeMessenger, day: date) -> int:
    return send_unsent(session, ledger, messenger, nyse=CALENDAR, cboe=CALENDAR,
                       now=evening(day))


def test_unsent_rows_go_out_once_in_order_with_their_buttons(
    ledger: Ledger, session: Session
) -> None:
    signal_id, alert_id = _trade(ledger)
    messenger = FakeMessenger()
    assert _send(session, ledger, messenger, D0) == 3
    entry, exit_, raised = messenger.sent
    assert entry.text == (
        "🟢 BUY NVDA (CDR ZNVD) — Breakout · NVDA\n"
        "Signal C$40.00 · Stop C$37.60 (−6.0%) · Trailing stop, no target, no time limit\n"
        "Size 1 unit (~C$40.00) · risk C$2.40 · LIMIT C$40.40\n"
        "Skip if price > C$40.40, price ≤ the stop C$37.60, or bid/ask spread > 0.5%\n"
        "Why: Closed at a 20-session high."
    )
    assert [b.data for row in entry.buttons for b in row] == [f"b:{signal_id}", f"s:{signal_id}"]
    assert exit_.text == (
        "🔴 SELL NVDA (CDR ZNVD) — stop hit (US close US$190.00 ≤ stop US$192.00 · CDR ~C$38.00 "
        "vs stop C$38.40)\n"
        "Held 3 sessions · unrealized −C$4.40 (−5.5%)\n"
        "Sell at the open."
    )
    assert [b.data for row in exit_.buttons for b in row] == [f"x:{alert_id}", f"i:{alert_id}"]
    assert raised.text == (
        "⬆️ NVDA (CDR ZNVD) stop raised C$37.60 → C$38.40 (US$188.00 → US$192.00) · still "
        "holding, no action"
    )
    signal, alert = session.get(TradeSignal, signal_id), session.get(ExitAlert, alert_id)
    assert signal is not None and alert is not None
    assert (signal.telegram_message_id, alert.telegram_message_id) == (
        entry.message_id, exit_.message_id
    )
    assert _send(session, ledger, messenger, D0) == 0  # nothing is sent twice


def test_a_failed_send_leaves_the_rest_for_the_next_scan(ledger: Ledger, session: Session) -> None:
    _trade(ledger)

    class Flaky(FakeMessenger):
        def send(self, text: str, buttons: Buttons = ()) -> int:
            if len(self.sent) == 1:
                raise ConnectionError("Telegram is unreachable")
            return super().send(text, buttons)

    flaky = Flaky()
    with pytest.raises(ConnectionError):
        _send(session, ledger, flaky, D0)
    later = FakeMessenger()
    assert _send(session, ledger, later, D0) == 2  # the exit alert and the raise
    assert [m.text.split()[0] for m in later.sent] == ["🔴", "⬆️"]


def test_an_entry_sent_after_its_expiry_says_not_to_place_it(
    ledger: Ledger, session: Session
) -> None:
    send_signal(ledger, D0)  # expires at 16:00 New York on D1
    messenger = FakeMessenger()
    _send(session, ledger, messenger, D1)
    [late] = messenger.sent
    assert late.buttons == ()
    assert late.text == (
        "ℹ️ The NVDA (CDR ZNVD) entry of 2026-06-01 was not sent in time and expired at "
        "2026-06-02 16:00 New York time: do not place it."
    )


def test_an_unsent_entry_withdrawn_by_a_split_is_not_offered(
    ledger: Ledger, session: Session
) -> None:
    send_signal(ledger, D0)
    ledger.record_split(kind="cdr_split", symbol="ZNVD", ex_date=D1, ratio=Decimal(2),
                        source="owner")
    messenger = FakeMessenger()
    _send(session, ledger, messenger, D0)
    assert messenger.sent[-1].buttons == ()
    assert messenger.texts()[-1] == (
        "ℹ️ The NVDA (CDR ZNVD) entry of 2026-06-01 was not sent in time and is withdrawn: do not "
        "place it."
    )


def test_split_notices_for_a_us_split_and_a_cdr_split(ledger: Ledger, session: Session) -> None:
    signal_id, _ = _trade(ledger)
    ledger.record_fill(cdr_symbol="ZXOM", side="buy", quantity=Decimal(1),
                       price_cad=Decimal(10), trade_date=D1)
    ledger.record_split(kind="us_split", symbol="NVDA", ex_date=D4, ratio=Decimal(2),
                        source="yfinance")
    ledger.record_split(kind="cdr_split", symbol="ZXOM", ex_date=D5, ratio=Decimal(2),
                        source="yfinance")
    messenger = FakeMessenger()
    _send(session, ledger, messenger, D0)
    us, cdr = messenger.texts()[:2]
    assert us == (
        "ℹ️ NVDA split 2-for-1 (ex-date 2026-06-05): stop US$192.00 → US$96.00 · no action"
    )
    assert cdr == (
        "ℹ️ ZXOM split 2-for-1 (ex-date 2026-06-08): 1 unit → 2, ACB per unit C$10.00 → C$5.00 "
        "(the total ACB is unchanged) · check that Wealthsimple shows 2 units · no action"
    )
    [row] = session.exec(select(StopUpdateRow).where(StopUpdateRow.reason == "split")).all()
    assert (row.signal_id, row.telegram_message_id) == (signal_id, messenger.sent[0].message_id)


def test_the_portfolio_and_the_evening_summary(ledger: Ledger, session: Session) -> None:
    _trade(ledger)
    ledger.record_fill(cdr_symbol="ZXOM", side="buy", quantity=Decimal(1),
                       price_cad=Decimal(10), trade_date=D2)
    position = (
        "• ZNVD 2 units · ACB C$40.20/unit · last C$38.00 · −5.5% · stop C$38.40 / US$192.00 · "
        "3 sessions"
    )
    manual = "• ZXOM 1 unit · ACB C$10.00/unit · last C$10.00 · 0.0% · manual (no stop) · 2 sessions"
    money = (
        "Cash C$9.60 · equity C$95.60 · peak C$101.20 · drawdown −5.5% · new entries not paused"
    )
    assert portfolio_text(ledger, D3).splitlines() == [
        "💼 Portfolio at the close of 2026-06-04", position, manual, money
    ]
    tonight = Tonight(
        as_of=D3, regime="QQQ above its 200-session average: new entries allowed", signals=0,
        skips={"held": 1, "no_cdr_price": 1}, exits=1, raises=0, caught_up=(D2,),
        warnings=("earnings calendar FAILED (ConnectError: down); used the stored dates",),
        scale_up=None,
    )
    assert evening_summary(ledger, session, tonight).splitlines() == [
        "📊 Evening summary · 2026-06-04 (Thu)",
        "QQQ above its 200-session average: new entries allowed",
        "Signals sent 0 · skipped: held 1, no_cdr_price 1 · exits 1 · stop raises 0",
        "Caught up (exits and stop raises only, sent marked late): 2026-06-03",
        "Positions:", position, manual,
        "Open exit alerts:",
        "• ZNVD stop exit since 2026-06-04: sell, or tap Ignore",
        money,
        "⚠️ earnings calendar FAILED (ConnectError: down); used the stored dates",
    ]


def test_pnl_and_the_pause_review_compare_live_r_with_the_backtest(
    ledger: Ledger, session: Session
) -> None:
    _, alert_id = _trade(ledger)
    ledger.record_fill(cdr_symbol="ZNVD", side="sell", quantity=Decimal(2),
                       price_cad=Decimal("38.10"), trade_date=D4, exit_alert_id=alert_id)
    ledger.record_cash(Decimal("-10.00"), D4, "withdrawal")
    assert pnl_text(ledger, D5, BACKTEST).splitlines() == [
        "💰 P&L to 2026-06-08",
        "Realized: all time −C$4.20 · June 2026 −C$4.20",
        "Unrealized: C$0.00 on 0 open positions",
        "Closed managed trades: 1 · win rate 0%",
        (
            "Live mean R −0.88 over 1 closed managed trades vs the backtest's 0.505 "
            "(v2-none-cash, run 4123c177), both R on planned risk"
        ),
    ]
    risk = ledger.risk_state()
    risk.paused, risk.paused_at = True, D4
    risk.paused_reason = "equity C$85.99 is below 0.85 x the peak C$101.20"
    session.add(risk)
    session.commit()
    assert pause_review(ledger, session, None).splitlines() == [
        (
            "⏸ New entries are paused since the close of 2026-06-05: equity C$85.99 is below "
            "0.85 x the peak C$101.20."
        ),
        "Last 10 managed trades (R on planned risk):",
        "• ZNVD 2026-06-02 → 2026-06-05 · −C$4.20 · −0.88R",
        (
            "Live mean R −0.88 over 1 closed managed trades (no stored backtest run of the live "
            "config to compare)"
        ),
        "Your skips: none · missed exit alerts: 0",
        (
            "Withdrawals since the peak on 2026-06-03: C$10.00. A withdrawal lowers equity like "
            "a loss (spec 04), so part of this drawdown is your own cash."
        ),
        "/resume to continue (resets peak)",
    ]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_live_outbox.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'signalbench.live.outbox'`.

- [ ] **Step 3: Write the texts from the ledger**

Create `src/signalbench/live/summary.py`:

```python
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
```

- [ ] **Step 4: Write the outbox**

Create `src/signalbench/live/outbox.py`:

```python
"""Send what the scan wrote (spec 05, step 9).

Every message-bearing row keeps the id of its Telegram message, and a row without one has not
been sent yet: split notices (`split` stop rows and CDR splits), entry signals, exit alerts,
and trailing-stop raises. They go out oldest first, each id saved as soon as its message is
sent, so a failure part way leaves the rest for the next scan and nothing is sent twice.
"""

from datetime import date, datetime
from decimal import ROUND_DOWN

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
from signalbench.ingest.earnings import paper_earnings_dates
from signalbench.live.book import NEW_YORK, ZERO
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
from signalbench.live.sizing import CENT, LIMIT_FACTOR, entry_window
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
    entry = EntryText(
        us_symbol=signal.us_symbol, cdr_symbol=cdr.symbol, company=cdr.company_name,
        cdr_close=signal.cdr_signal_close, cdr_stop=signal.cdr_stop, stop_pct=signal.stop_pct,
        units=signal.suggested_units,
        order_type="limit" if signal.order_type == "limit" else "market",
        limit=(signal.cdr_signal_close * LIMIT_FACTOR).quantize(CENT, rounding=ROUND_DOWN),
        risk=signal.risk_amount_cad, why=signal.explanation,
        note=entry_window(signal.as_of, nyse, cboe).note,
    )
    assert signal.id is not None
    return entry_message(entry), entry_buttons(signal.id)


def _earnings_after(session: Session, us: Ticker, day: date) -> date | None:
    return next((d for d in paper_earnings_dates(session, us.id) if d > day), None)


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


def send_unsent(
    session: Session,
    ledger: Ledger,
    messenger: Messenger,
    *,
    nyse: Sessions,
    cboe: Sessions,
    now: datetime,
) -> int:
    """Send every unsent row: split notices, then entries, exit alerts, and stop raises.
    Returns how many messages were sent. A send that raises stops here; what is left is sent
    by the next scan."""
    sent = 0

    def mark(row: StopUpdateRow | CorporateAction | TradeSignal | ExitAlert, text: str,
             buttons: Buttons = ()) -> None:
        nonlocal sent
        row.telegram_message_id = messenger.send(text, buttons)
        session.add(row)
        session.commit()
        sent += 1

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
    return sent
```

- [ ] **Step 5: Run the tests and checks**

Run: `uv run pytest tests/test_live_outbox.py -q && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 7 passed; the full suite 746 passed; ruff `All checks passed!`; mypy `Success: no issues found in 80 source files`.

- [ ] **Step 6: Commit**

```bash
git add src/signalbench/live/summary.py src/signalbench/live/outbox.py tests/test_live_outbox.py
git commit -m "feat: the scan's outbox and the texts built from the ledger

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: `run_scan`: the eleven steps, catch-up, idempotency, the lock, and the dry run

**Files:**
- Create: `src/signalbench/live/heartbeat.py`, `src/signalbench/live/scan.py`, `tests/scan_helpers.py`, `tests/test_live_scan.py`

`tests/scan_helpers.py` builds the Breakout world every scan, bot, and end-to-end test uses: 230 weekdays of bars from 2025-12-01, the breakout on day `B` = 2026-10-05 (a Monday), the real v2-none-cash config committed in a throwaway repo and frozen by a `live_config` row, and C$100 deposited. With ATR exactly 2 the signal is US$101.00 with a stop of 97; on the CDR (a tenth of the US price) that is C$10.10 and C$9.70, 3 units by the floor rule, limit C$10.20.

- [ ] **Step 1: Write the failing tests**

Create `tests/scan_helpers.py`:

```python
"""A Breakout world for the scan tests (spec 05), on the weekday calendar (no holidays).

230 weekdays of stored bars. Every US stock is flat (high/low = close +/- 1, so ATR(14) is
exactly 2) on 1M shares, and QQQ rises all along (regime on). NVDA breaks out on day B:
101 on 2M shares, above the prior 20-session high of 100, so the signal close is US$101.00 and
the initial stop 101 - 2 x 2 = 97. It then rises by 1 a session to 104 on B+3 (the trailing
stop 104 - 3 x 2 = 98 > 97) and falls to 97.5 on B+4, below that raised stop. Every CDR trades
at a tenth of its US price on 100 units a day. The live config is the real v2-none-cash file,
committed in a throwaway repo and frozen by a live_config row.
"""

import shutil
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Engine, event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from live_helpers import add_benchmark, add_pair, add_prices, make_ledger, universe
from paper_helpers import evening, git
from signalbench.db.models import LiveConfig, Price, Ticker
from signalbench.ingest.cdr import CdrEntry
from signalbench.ingest.prices import Split
from signalbench.live.heartbeat import write_heartbeat
from signalbench.live.ledger import Ledger
from signalbench.live.messenger import FakeMessenger, Messenger
from signalbench.live.scan import ScanOutcome, run_scan
from signalbench.market.calendar import Sessions
from signalbench.strategy.config import load_strategy_config
from strategy_helpers import WeekdaySessions, weekdays

REPO = Path(__file__).resolve().parents[1]
LIVE = "data/strategy_v2-none-cash.yaml"
DAYS = weekdays(date(2025, 12, 1), 230)
B = 220  # the breakout session: 220 sessions of history before it
UNIVERSE: list[CdrEntry] = universe(
    ("AAPL", "ZAAP", "Information Technology"),
    ("NVDA", "ZNVD", "Information Technology"),
    ("XOM", "ZXOM", "Energy"),
)
NVDA = {B: 101.0, B + 1: 102.0, B + 2: 103.0, B + 3: 104.0, B + 4: 97.5}


def path(changes: dict[int, float], start: float = 100.0) -> list[float]:
    """Flat at `start`, then each change from its index on."""
    closes, close = [], start
    for index in range(len(DAYS)):
        close = changes.get(index, close)
        closes.append(close)
    return closes


def volumes(spikes: dict[int, int]) -> list[int]:
    return [spikes.get(index, 1_000_000) for index in range(len(DAYS))]


def make_repo(root: Path) -> Path:
    """A committed copy of the live config and one tracked source file."""
    (root / "data").mkdir(parents=True)
    shutil.copyfile(REPO / LIVE, root / LIVE)
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("VERSION = 1\n", encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", "data", "src")
    git(root, "commit", "-q", "-m", "live")
    return root


@dataclass
class World:
    session: Session
    repo: Path
    messenger: FakeMessenger = field(default_factory=FakeMessenger)
    splits: dict[str, list[Split]] = field(default_factory=dict)
    earnings_error: Exception | None = None
    bot_down: bool = False
    cboe: Sessions = field(default_factory=WeekdaySessions)
    echoed: list[str] = field(default_factory=list)

    def ledger(self, index: int) -> Ledger:
        return make_ledger(self.session, today=DAYS[index])

    def scan(
        self,
        index: int,
        *,
        messenger: Messenger | None = None,
        force: bool = False,
        held: bool = True,
        hour: int = 18,
    ) -> ScanOutcome:
        def earnings(_session: Session, _since: date) -> list[str]:
            if self.earnings_error is not None:
                raise self.earnings_error
            return []

        if not self.bot_down:  # the bot checked in five minutes ago
            write_heartbeat(self.session, evening(DAYS[index], hour) - timedelta(minutes=5))
        return run_scan(
            self.session, lock=nullcontext(held), repo=self.repo, universe=UNIVERSE,
            nyse=WeekdaySessions(), cboe=self.cboe,
            clock=lambda: evening(DAYS[index], hour), ingest=lambda _session: [],
            ingest_earnings=earnings, splits=lambda symbol, _since: self.splits.get(symbol, []),
            messenger=self.messenger if messenger is None else messenger,
            echo=self.echoed.append, force=force,
        )


def make_world(
    session: Session,
    root: Path,
    *,
    closes: dict[str, dict[int, float]] | None = None,
    spikes: dict[str, dict[int, int]] | None = None,
) -> World:
    """The stocks, their CDRs, QQQ, a C$100 deposit on the first day, and the frozen config."""
    closes = {"NVDA": NVDA} if closes is None else closes
    spikes = {"NVDA": {B: 2_000_000}} if spikes is None else spikes
    for entry in UNIVERSE:
        us, cdr = add_pair(session, entry.us_symbol, entry.cdr_symbol, entry.sector)
        us_closes = path(closes.get(entry.us_symbol, {}))
        add_prices(session, us, DAYS, us_closes, volumes=volumes(spikes.get(entry.us_symbol, {})))
        add_prices(session, cdr, DAYS, [round(c / 10, 4) for c in us_closes], volume=100)
    add_benchmark(session, DAYS, [300.0 + i for i in range(len(DAYS))])
    repo = make_repo(root)
    sha = load_strategy_config(repo / LIVE)[1]
    session.add(LiveConfig(config_path=LIVE, config_sha256=sha, started_on=DAYS[0],
                           start_git_sha=git(repo, "rev-parse", "HEAD")))
    session.commit()
    make_ledger(session, today=DAYS[0]).record_cash(Decimal("100.00"), DAYS[0], "deposit")
    return World(session, repo)


class Holiday(WeekdaySessions):
    """The weekday calendar with one day closed: a Cboe Canada holiday when NYSE is open."""

    def __init__(self, closed: date) -> None:
        self.closed = closed

    def sessions_between(self, start: date, end: date) -> list[date]:
        return [d for d in super().sessions_between(start, end) if d != self.closed]

    def next_sessions(self, day: date, count: int) -> list[date]:
        return [d for d in super().next_sessions(day, count + 1) if d != self.closed][:count]

    def is_session(self, day: date) -> bool:
        return day != self.closed and super().is_session(day)


def savepoint_engine() -> Engine:
    """In-memory SQLite that honours savepoints, as Postgres does for `dry_run_session`
    (pysqlite's own transaction handling does not)."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _no_autobegin(dbapi_connection: object, _record: object) -> None:
        dbapi_connection.isolation_level = None  # type: ignore[attr-defined]

    @event.listens_for(engine, "begin")
    def _begin(connection: object) -> None:
        connection.exec_driver_sql("BEGIN")  # type: ignore[attr-defined]

    SQLModel.metadata.create_all(engine)
    return engine


def rescale(session: Session, symbol: str, ratio: float) -> None:
    """The stored history after a `ratio`-for-1 split, as yfinance serves it."""
    ticker = session.exec(select(Ticker).where(Ticker.symbol == symbol)).one()
    for row in session.exec(select(Price).where(Price.ticker_id == ticker.id)).all():
        factor = Decimal(repr(ratio))
        row.open, row.high, row.low = row.open / factor, row.high / factor, row.low / factor
        row.close, row.adj_close = row.close / factor, row.adj_close / factor
        session.add(row)
    session.commit()
```

Create `tests/test_live_scan.py`:

```python
"""Spec 05, Evening scan: the steps and their run row, failures, idempotency, the lock, splits,
and the dry run, on the Breakout world of scan_helpers (FakeMessenger, no network)."""

from contextlib import nullcontext
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlmodel import Session, col, select

from paper_helpers import evening
from scan_helpers import (
    DAYS,
    LIVE,
    UNIVERSE,
    B,
    Holiday,
    World,
    make_world,
    rescale,
    savepoint_engine,
)
from signalbench.db.models import (
    LiveConfig,
    Price,
    ScanRun,
    StopUpdateRow,
    Ticker,
    TradeSignal,
)
from signalbench.ingest.prices import Split
from signalbench.live.messenger import ConsoleMessenger, FakeMessenger
from signalbench.live.scan import ABANDONED, dry_run_session, run_scan
from strategy_helpers import WeekdaySessions

ENTRY = (
    "🟢 BUY NVDA (CDR ZNVD) — Breakout · NVDA\n"
    "Signal C$10.10 · Stop C$9.70 (−4.0%) · Trailing stop, no target, no time limit\n"
    "Size 3 units (~C$30.30) · risk C$1.20 · LIMIT C$10.20\n"
    "Skip if price > C$10.20, price ≤ the stop C$9.70, or bid/ask spread > 0.5%\n"
    "Why: Closed at a 20-session high (US$101.00) on 2.0× its 50-session average volume, above "
    "its 50-session average."
)


@pytest.fixture
def world(session: Session, tmp_path: Path) -> World:
    return make_world(session, tmp_path)


def _runs(session: Session) -> list[ScanRun]:
    return list(session.exec(select(ScanRun).order_by(col(ScanRun.id))).all())


def _bought(world: World) -> int:
    """The owner bought 3 ZNVD at C$10.45 the day after the signal."""
    [signal] = world.session.exec(select(TradeSignal)).all()
    assert signal.id is not None
    world.ledger(B + 1).record_fill(cdr_symbol="ZNVD", side="buy", quantity=Decimal(3),
                                    price_cad=Decimal("10.45"), trade_date=DAYS[B + 1],
                                    signal_id=signal.id)
    return signal.id


def test_a_breakout_is_sized_explained_recorded_and_sent(world: World) -> None:
    outcome = world.scan(B)
    assert (outcome.status, outcome.as_of, outcome.counts) == (
        "ok", DAYS[B], {"signals": 1, "sent": 2}
    )
    entry, summary = world.messenger.sent
    assert entry.text == ENTRY
    assert summary.text.splitlines()[:4] == [
        "📊 Evening summary · 2026-10-05 (Mon)",
        "QQQ above its 200-session average: new entries allowed",
        "Signals sent 1 · skipped: none · exits 0 · stop raises 0",
        "Positions: none",
    ]
    [signal] = world.session.exec(select(TradeSignal)).all()
    assert (signal.us_signal_close, signal.us_stop, signal.cdr_signal_close, signal.cdr_stop) == (
        Decimal(101), Decimal(97), Decimal("10.10"), Decimal("9.70")
    )
    assert (signal.suggested_units, signal.order_type, signal.risk_amount_cad) == (
        Decimal(3), "limit", Decimal("1.20")
    )
    assert signal.explanation.startswith("Closed at a 20-session high (US$101.00)")
    assert signal.expires_at == evening(DAYS[B + 1], 16)  # the close of the next session
    assert signal.telegram_message_id == entry.message_id
    [run] = _runs(world.session)
    assert (run.status, run.as_of, run.failed_step, run.warnings, run.git_dirty) == (
        "ok", DAYS[B], None, [], False
    )
    assert run.config_sha256 is not None and len(run.git_sha or "") == 40


def test_a_scanned_target_sends_nothing_and_a_forced_rescan_never_resends_a_row(
    world: World,
) -> None:
    world.scan(B)
    again = world.scan(B, hour=19)
    assert (again.status, len(world.messenger.sent)) == ("nothing", 2)
    assert world.echoed[-1] == "2026-10-05 was already scanned; nothing to send."
    forced = world.scan(B, force=True, hour=20)
    assert forced.status == "ok"
    assert [m.text.split()[0] for m in world.messenger.sent[2:]] == ["📊"]  # the summary only
    assert "skipped: already_sent 1" in world.messenger.sent[-1].text
    assert [r.status for r in _runs(world.session)] == ["ok", "ok", "ok"]


def test_a_row_whose_send_failed_is_sent_by_the_next_run(world: World) -> None:
    down = FakeMessenger(fail=True)
    failed = world.scan(B, messenger=down)
    assert failed.status == "failed"
    unreachable = "ConnectionError: Telegram is unreachable (FakeMessenger)"
    assert world.echoed[-2:] == [
        f"could not send the failure message ({unreachable})",
        f"ERROR: Scan failed at step 9 (send): {unreachable}",
    ]
    [run] = _runs(world.session)
    assert (run.status, run.failed_step) == ("failed", 9)
    [signal] = world.session.exec(select(TradeSignal)).all()
    assert signal.telegram_message_id is None
    retried = world.scan(B, hour=19)
    assert retried.status == "ok"
    assert world.messenger.texts()[0] == ENTRY  # sent once, by the retry
    assert len(world.session.exec(select(TradeSignal)).all()) == 1


def test_a_critical_failure_sends_the_warning_and_records_the_step(world: World) -> None:
    qqq = world.session.exec(select(Ticker).where(Ticker.symbol == "QQQ")).one()
    bar = world.session.exec(
        select(Price).where(Price.ticker_id == qqq.id, Price.date == DAYS[B])
    ).one()
    world.session.delete(bar)
    world.session.commit()
    outcome = world.scan(B)
    assert outcome.status == "failed"
    assert world.messenger.texts() == [
        "⚠️ Scan failed at step 2 (prices): no 2026-10-05 bar for QQQ; the next run retries"
    ]
    [run] = _runs(world.session)
    assert (run.status, run.failed_step, run.error) == (
        "failed", 2, "no 2026-10-05 bar for QQQ; the next run retries"
    )


def test_a_calendar_ingest_failure_is_a_warning_and_the_stored_dates_are_used(
    world: World,
) -> None:
    world.earnings_error = ConnectionError("finnhub down")
    assert world.scan(B).status == "ok"
    warning = (
        "earnings calendar FAILED (ConnectionError: finnhub down); the stored dates were used"
    )
    assert f"⚠️ {warning}" in world.messenger.sent[-1].text.splitlines()
    assert _runs(world.session)[0].warnings == [warning]


def test_a_changed_config_a_missing_row_and_a_dirty_tree_are_refused(world: World) -> None:
    config = world.repo / LIVE
    original = config.read_bytes()
    config.write_bytes(original + b"# edited\n")
    assert world.scan(B).error is not None
    assert "changed: sha256" in world.messenger.texts()[-1]
    config.write_bytes(original)
    (world.repo / "src" / "app.py").write_text("VERSION = 2\n", encoding="utf-8")
    world.scan(B)
    assert world.messenger.texts()[-1] == (
        "⚠️ Scan failed at step 1 (database check): uncommitted changes to tracked code or data "
        "(src/app.py); commit them or check out a clean tag, then rerun"
    )
    assert _runs(world.session)[-1].git_dirty is True


def test_a_missing_live_config_row_is_refused(session: Session, tmp_path: Path) -> None:
    world = make_world(session, tmp_path)
    row = session.get(LiveConfig, 1)
    session.delete(row)
    session.commit()
    world.scan(B)
    [failure] = world.messenger.texts()
    assert failure == (
        "⚠️ Scan failed at step 1 (database check): No live config. Run `signalbench live start` "
        "first."
    )


def test_a_second_scan_exits_while_the_lock_is_held(world: World) -> None:
    assert world.scan(B, held=False).status == "locked"
    assert (_runs(world.session), world.messenger.sent) == ([], [])


def test_a_run_left_running_for_two_hours_is_marked_abandoned(world: World) -> None:
    stale = ScanRun(as_of=DAYS[B - 1], started_at=evening(DAYS[B]) - timedelta(hours=3),
                    status="running")
    world.session.add(stale)
    world.session.commit()
    world.scan(B)
    first = _runs(world.session)[0]
    assert (first.status, first.error) == ("failed", ABANDONED)


def test_a_split_in_a_held_stock_writes_a_split_row_and_a_notice(world: World) -> None:
    world.scan(B)
    signal_id = _bought(world)
    world.scan(B + 1)
    rescale(world.session, "NVDA", 2.0)  # yfinance's history after a 2-for-1 split on B+2
    world.splits = {"NVDA": [Split(ex_date=DAYS[B + 2], ratio=2.0)]}
    outcome = world.scan(B + 2)
    assert (outcome.status, outcome.counts["splits"]) == ("ok", 1)
    assert "ℹ️ NVDA split 2-for-1 (ex-date 2026-10-07): stop US$97.00 → US$48.50 · no action" in (
        world.messenger.texts()
    )
    [row] = world.session.exec(select(StopUpdateRow).where(StopUpdateRow.reason == "split")).all()
    assert (row.signal_id, row.new_us_stop, row.new_cdr_stop) == (
        signal_id, Decimal("48.50"), Decimal("9.70")
    )


def test_an_unrecorded_split_holds_that_symbol_and_the_rest_of_the_scan_goes_on(
    world: World,
) -> None:
    world.scan(B)
    _bought(world)
    world.scan(B + 1)
    rescale(world.session, "NVDA", 2.0)  # a split yfinance did not list
    outcome = world.scan(B + 2)
    assert outcome.status == "ok"
    summary = world.messenger.sent[-1].text
    assert (
        "⚠️ NVDA: prices moved in a way no recorded split explains; check it by hand (the "
        "stored US close 50.5000 on 2026-10-05 is 0.5000x the signal's 101.0000 after recorded "
        "splits)"
    ) in summary.splitlines()
    assert "stop raises 0" in summary


def test_a_cboe_holiday_moves_the_entry_and_its_expiry(world: World) -> None:
    world.cboe = Holiday(DAYS[B + 1])  # Cboe Canada closed on the next NYSE session
    world.scan(B)
    [signal] = world.session.exec(select(TradeSignal)).all()
    assert signal.expires_at == evening(DAYS[B + 2], 16)
    assert world.messenger.texts()[0].splitlines()[4] == (
        "Cboe Canada is closed on Tue 06 Oct: place it on Wed 07 Oct."
    )


def test_the_bot_heartbeat_is_checked(world: World) -> None:
    world.bot_down = True
    world.scan(B)
    assert world.echoed[-1] == (
        "BOT STALE: the bot has never checked in: is `signalbench bot run` running?"
    )
    assert "⚠️ the bot has never checked in: is `signalbench bot run` running?" in (
        world.messenger.sent[-1].text.splitlines()
    )


def test_a_drawdown_pauses_new_entries_and_sends_the_review(world: World) -> None:
    world.scan(B - 1)  # the peak: C$100 on 2026-10-02
    world.ledger(B).record_cash(Decimal("-20.00"), DAYS[B], "withdrawal")
    before = len(world.messenger.sent)
    world.scan(B)
    review, summary = world.messenger.texts()[before:]  # no entry: the breakout is skipped
    assert review.splitlines()[0] == (
        "⏸ New entries are paused since the close of 2026-10-05: equity C$80.00 is below 0.85 "
        "x the peak C$100.00."
    )
    assert "Withdrawals since the peak on 2026-10-02: C$20.00." in review
    assert "skipped: paused 1" in summary
    assert "new entries PAUSED since 2026-10-05 (/resume)" in summary


def test_a_run_after_the_next_open_is_late_and_says_so(world: World) -> None:
    world.scan(B + 1, hour=8)  # before B+1 is complete, the target is still B; before its open
    [run] = _runs(world.session)
    assert (run.as_of, run.warnings) == (DAYS[B], [])
    world.scan(B + 1, hour=10, force=True)  # after the 09:30 open of B+1
    assert _runs(world.session)[-1].warnings == [
        "late run: this scan ran after the 2026-10-06 open, so its orders are late"
    ]


def test_a_dry_run_prints_every_message_and_writes_nothing(tmp_path: Path) -> None:
    engine = savepoint_engine()
    with Session(engine) as seed:
        world = make_world(seed, tmp_path)
    lines: list[str] = []
    with dry_run_session(engine) as session:
        outcome = run_scan(
            session, lock=nullcontext(True), repo=world.repo, universe=UNIVERSE,
            nyse=WeekdaySessions(), cboe=WeekdaySessions(), clock=lambda: evening(DAYS[B]),
            ingest=lambda _s: [], ingest_earnings=lambda _s, _d: [],
            splits=lambda _symbol, _since: [], messenger=ConsoleMessenger(lines.append),
            echo=lambda _line: None, as_of=DAYS[B], force=True,
        )
    assert outcome.status == "ok"
    assert lines[:3] == ["--- message 1 ---", ENTRY, "[✅ I bought] [⏭ Skip]"]
    assert lines[3] == "--- message 2 ---"
    assert lines[4].startswith("📊 Evening summary · 2026-10-05 (Mon)")
    assert len(lines) == 5
    with Session(engine) as after:
        assert after.exec(select(ScanRun)).all() == []
        assert after.exec(select(TradeSignal)).all() == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_live_scan.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'signalbench.live.heartbeat'` (`tests/scan_helpers.py` imports it first).

- [ ] **Step 3: Write the heartbeat row**

Create `src/signalbench/live/heartbeat.py`:

```python
"""The bot's heartbeat (spec 05): the bot rewrites one row at least every 10 minutes while it
polls, and the evening scan warns when it is more than an hour old, so a bot that is down
without exiting is noticed."""

from datetime import datetime, timedelta

from sqlmodel import Session

from signalbench.db.models import BotHeartbeat
from signalbench.live.book import NEW_YORK

STALE_AFTER = timedelta(hours=1)


def write_heartbeat(session: Session, now: datetime) -> None:
    beat = session.get(BotHeartbeat, 1)
    if beat is None:
        beat = BotHeartbeat(beat_at=now)
    beat.beat_at = now
    session.add(beat)
    session.commit()


def heartbeat_problem(session: Session, now: datetime) -> str | None:
    """Why the bot looks down (never checked in, or not for over an hour), or None."""
    beat = session.get(BotHeartbeat, 1)
    if beat is None:
        return "the bot has never checked in: is `signalbench bot run` running?"
    age = now - beat.beat_at
    if age <= STALE_AFTER:
        return None
    hours = age.total_seconds() / 3600
    return (
        f"the bot last checked in {hours:.1f} hours ago "
        f"({beat.beat_at.astimezone(NEW_YORK):%Y-%m-%d %H:%M} New York time): is it running?"
    )
```

- [ ] **Step 4: Write the scan**

Create `src/signalbench/live/scan.py`:

```python
"""`signalbench scan` (spec 05, Evening scan): the live strategy's nightly job.

Eleven steps, recorded in the run's `scan_runs` row. A critical step that fails stops the
scan, marks the row failed with the step, and sends `⚠️ Scan failed at step N (<name>): ...`;
the next scheduled run retries. Steps 3 (earnings calendar) and 4 (splits) are not critical:
their failures are warnings in the summary, and a split problem holds only its symbol.

The target is the latest complete NYSE session (16:15 New York). Sessions missed since the last
successful scan are caught up first, in order, for exits and stop raises only (sent marked
late); entries come only from the target. A target already scanned is not scanned again without
`force`, and every row is unique (a signal per session and symbol, a raise per position and
session), so a forced rescan never sends a row twice. One scan runs at a time (`lock`).
"""

from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import Literal, TypeVar

from sqlalchemy import Engine
from sqlmodel import Session, col, func, select

from signalbench.backtest.provenance import code_version
from signalbench.backtest.runner import last_complete_session
from signalbench.db.models import (
    CorporateAction,
    Price,
    ScanRun,
    StopUpdateRow,
    Ticker,
    TickerKind,
    TradeSignal,
)
from signalbench.ingest.cdr import CdrEntry
from signalbench.live.book import (
    MARKET_CLOSE,
    NEW_YORK,
    ZERO,
    LedgerError,
    SplitKind,
    q4,
)
from signalbench.live.heartbeat import heartbeat_problem
from signalbench.live.ledger import Ledger
from signalbench.live.messages import failure_message
from signalbench.live.messenger import Messenger
from signalbench.live.outbox import send_unsent
from signalbench.live.review import SessionReview, live_market, review_session
from signalbench.live.scaleup import ScaleUpResult
from signalbench.live.sizing import CdrSize, entry_window, size_cdr
from signalbench.live.start import LiveRefusedError, verify_live_config
from signalbench.live.summary import (
    BacktestR,
    Tonight,
    backtest_r,
    evening_summary,
    pause_review,
)
from signalbench.live.why import why_line
from signalbench.market.calendar import Sessions
from signalbench.paper.splits import SplitFetcher
from signalbench.paper.start import uncommitted_code
from signalbench.strategy.config import StrategyConfig
from signalbench.strategy.decision import EntryOrder
from signalbench.strategy.market_view import MarketView

STEPS = {
    1: "database check",
    2: "prices",
    3: "earnings calendar",
    4: "splits",
    5: "catch-up",
    6: "tonight",
    7: "sizing",
    8: "why",
    9: "send",
    10: "expiry and scale-up",
    11: "summary",
}
LIVE_SCAN_LOCK = 2026_0930_05  # an arbitrary bigint that names the scan's advisory lock
ABANDONED_AFTER = timedelta(hours=2)  # a `running` row this old was killed or crashed
ABANDONED = "abandoned (killed or crashed)"
MARKET_OPEN = time(9, 30)  # New York: a run after the next session's open is a late run
ScanStatus = Literal["locked", "ok", "nothing", "failed"]
T = TypeVar("T")


class StepError(Exception):
    """A critical step failed: the scan stops and the run is marked failed."""

    def __init__(self, step: int, message: str) -> None:
        super().__init__(message)
        self.step = step
        self.message = message


@dataclass(frozen=True)
class ScanOutcome:
    status: ScanStatus  # nothing: the target was already scanned, so nothing was sent
    run_id: int | None = None
    as_of: date | None = None
    error: str | None = None
    counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Sized:
    entry: EntryOrder
    size: CdrSize
    cdr: Ticker
    why: str = ""


def _first_line(error: BaseException) -> str:
    lines = str(error).strip().splitlines()
    return f"{type(error).__name__}: {lines[0] if lines else ''}"


def scale_up_text(result: ScaleUpResult) -> str:
    """The scale-up result for the summary (spec 04, spec 05)."""
    if not result.active:
        return f"Scale-up check: {result.closed_managed} of 10 managed trades closed."
    if result.passed:
        return "All 4 checks passed. You can add C$900 when ready; record it with /deposit 900."
    failed = []
    if not result.checks["take_rate"]:
        failed.append(f"take rate {result.take_rate or ZERO:.2f} < 0.80")
    if not result.checks["slippage"]:
        failed.append(f"mean slippage {(result.mean_slippage or ZERO) * 100:.2f}% > 0.5%")
    if not result.checks["exit_misses"]:
        failed.append(f"{len(result.misses)} missed exit alerts")
    if not result.checks["limit_violations"]:
        failed.append(f"{len(result.violations)} buys above the 1% limit")
    return "Scale-up check: not yet (" + "; ".join(failed) + ")."


@contextmanager
def dry_run_session(engine: Engine) -> Iterator[Session]:
    """A session whose commits are savepoints inside one transaction that is rolled back at the
    end: `scan --dry-run` runs every step on the real data and writes nothing."""
    with engine.connect() as connection:
        outer = connection.begin()
        try:
            with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
                yield session
        finally:
            outer.rollback()


def run_scan(
    session: Session,
    *,
    lock: AbstractContextManager[bool],
    repo: Path,
    universe: list[CdrEntry],
    nyse: Sessions,
    cboe: Sessions,
    clock: Callable[[], datetime],
    ingest: Callable[[Session], list[str]],
    ingest_earnings: Callable[[Session, date], list[str]],
    splits: SplitFetcher,
    messenger: Messenger,
    echo: Callable[[str], None],
    as_of: date | None = None,
    force: bool = False,
) -> ScanOutcome:
    """The evening scan. `lock` yields False when another scan holds it: nothing is done.
    `ingest` refreshes prices and the liquidity flags and returns what failed; `ingest_earnings`
    refreshes the earnings calendar from a date; `splits` lists a symbol's splits after a date.
    `as_of` overrides the target (`--dry-run --as-of`); `force` rescans a scanned target.
    `clock` must return timezone-aware times."""
    with lock as held:
        if not held:
            echo("Another scan holds the lock; nothing to do.")
            return ScanOutcome("locked")
        now = clock()
        _abandon_stale_runs(session, now)
        target = as_of if as_of is not None else last_complete_session(nyse, now)
        run = ScanRun(as_of=target, started_at=now, status="running")
        session.add(run)
        session.commit()
        session.refresh(run)
        scan = _Scan(session, run, repo, universe, nyse, cboe, clock, messenger, echo)
        try:
            return scan.run(ingest, ingest_earnings, splits, force)
        except StepError as failure:
            session.rollback()
            text = failure_message(failure.step, STEPS[failure.step], failure.message)
            scan.finish("failed", failure.step, failure.message)
            try:
                messenger.send(text)
            except Exception as error:  # noqa: BLE001  # Telegram down: the script's toast shows
                echo(f"could not send the failure message ({_first_line(error)})")
            echo(f"ERROR: {text.removeprefix('⚠️ ')}")
            return ScanOutcome("failed", run.id, target, failure.message, scan.counts())


def _abandon_stale_runs(session: Session, now: datetime) -> None:
    """A scan killed or crashed mid-way leaves its row `running`. The lock is held here, so an
    old one is not in progress: it counts as failed."""
    rows = session.exec(select(ScanRun).where(ScanRun.status == "running")).all()
    for row in rows:
        if now - row.started_at > ABANDONED_AFTER:
            row.status = "failed"
            row.error = ABANDONED
            session.add(row)
    session.commit()


class _Scan:
    def __init__(
        self,
        session: Session,
        run: ScanRun,
        repo: Path,
        universe: list[CdrEntry],
        nyse: Sessions,
        cboe: Sessions,
        clock: Callable[[], datetime],
        messenger: Messenger,
        echo: Callable[[str], None],
    ) -> None:
        self.session = session
        self.run_row = run
        self.target = run.as_of
        self.repo = repo
        self.universe = universe
        self.nyse = nyse
        self.cboe = cboe
        self.clock = clock
        self.now = clock()
        self.messenger = messenger
        self.echo = echo
        self.ledger = Ledger(session, calendar=nyse, today=self.now.astimezone(NEW_YORK).date())
        self.warnings: list[str] = []
        self.tally: Counter[str] = Counter()
        self.skips: Counter[str] = Counter()
        self.hold: set[str] = set()
        self.pause_texts: list[str] = []
        self.caught_up: list[date] = []
        self.backtest: BacktestR | None = None

    # --- Bookkeeping -----------------------------------------------------------------------

    def counts(self) -> dict[str, int]:
        return dict(self.tally)

    def warn(self, text: str) -> None:
        self.warnings.append(text)
        self.echo(f"warning: {text}")

    def finish(self, status: str, step: int | None = None, error: str | None = None) -> None:
        run = self.run_row
        run.status = status
        run.finished_at = self.clock()
        run.failed_step = step
        run.error = error
        run.warnings = list(self.warnings)
        run.counts = self.counts()
        self.session.add(run)
        self.session.commit()

    def step(self, number: int, work: Callable[[], T]) -> T:
        try:
            return work()
        except StepError:
            raise
        except Exception as error:  # any error in a critical step fails the scan
            raise StepError(number, _first_line(error)) from error

    # --- The scan --------------------------------------------------------------------------

    def run(
        self,
        ingest: Callable[[Session], list[str]],
        ingest_earnings: Callable[[Session, date], list[str]],
        splits: SplitFetcher,
        force: bool,
    ) -> ScanOutcome:
        config = self.step(1, self.check)
        last_ok = self.session.exec(
            select(func.max(ScanRun.as_of)).where(ScanRun.status == "ok")
        ).one()
        if last_ok is not None and last_ok >= self.target and not force:
            self.echo(f"{self.target} was already scanned; nothing to send.")
            self.check_heartbeat()
            self.finish("ok")
            return ScanOutcome("nothing", self.run_row.id, self.target, None, self.counts())
        sessions = [self.target]
        if last_ok is not None and last_ok < self.target:
            sessions = self.nyse.sessions_between(last_ok + timedelta(days=1), self.target)
        self.step(2, lambda: self.prices(ingest, config))
        self.earnings(ingest_earnings, last_ok)
        self.splits(splits)
        market = self.step(
            5, lambda: live_market(self.session, self.universe, config, self.nyse, self.target)
        )
        for day in sessions[:-1]:
            self.step(5, partial(self.review, day, market, config, True))
            self.caught_up.append(day)
        tonight = self.step(6, lambda: self.review(self.target, market, config, self.late_run()))
        sized = self.step(7, lambda: self.size(tonight, config))
        explained = self.step(8, lambda: [self.explain(s, market, config) for s in sized])
        self.step(9, lambda: self.send(explained))
        scale_up = self.step(10, self.expire_and_scale_up)
        self.step(11, lambda: self.summary(market, config, scale_up))
        self.finish("ok")
        return ScanOutcome("ok", self.run_row.id, self.target, None, self.counts())

    def check(self) -> StrategyConfig:
        """Step 1: the live_config row, the config's sha256, and a clean tree."""
        try:
            row, config = verify_live_config(self.session, self.repo)
        except LiveRefusedError as error:
            raise StepError(1, str(error)) from None
        version = code_version(self.repo)
        run = self.run_row
        run.git_sha, run.git_dirty, run.config_sha256 = version.sha, version.dirty, row.config_sha256
        self.session.add(run)
        self.session.commit()
        if version.dirty:
            raise StepError(1, uncommitted_code(version))
        self.backtest = backtest_r(self.session, row.config_sha256)
        return config

    def late_run(self) -> bool:
        """After the next NYSE session's open: the target's orders were due at that open."""
        after = self.nyse.next_sessions(self.target, 1)[0]
        late = self.now >= datetime.combine(after, MARKET_OPEN, tzinfo=NEW_YORK)
        if late:
            self.warn(f"late run: this scan ran after the {after} open, so its orders are late")
        return late

    def _us_ticker(self, symbol: str) -> Ticker | None:
        return self.session.exec(
            select(Ticker).where(Ticker.symbol == symbol, Ticker.kind == TickerKind.us_stock)
        ).first()

    def _held_and_pending(self) -> set[str]:
        """The US symbols of every open position and sent signal."""
        symbols = {s.us_symbol for s in self._sent_signals()}
        for episode in self.ledger.open_episodes():
            cdr = self.session.get(Ticker, episode.cdr_ticker_id)
            us = None if cdr is None or cdr.us_ticker_id is None else self.session.get(
                Ticker, cdr.us_ticker_id
            )
            if us is not None:
                symbols.add(us.symbol)
        return symbols

    def _sent_signals(self) -> list[TradeSignal]:
        return list(
            self.session.exec(
                select(TradeSignal).where(TradeSignal.status == "sent").order_by(col(TradeSignal.id))
            ).all()
        )

    def prices(self, ingest: Callable[[Session], list[str]], config: StrategyConfig) -> None:
        """Step 2: ingest prices; the target needs a bar for QQQ and every held or pending
        symbol. Any other universe name without one is just not tradable tonight."""
        failed = ingest(self.session)
        if failed:
            self.warn(f"prices: {len(failed)} failed ({', '.join(failed)}); stored prices used")
        wanted = sorted({config.regime_symbol} | self._held_and_pending())
        missing = []
        for symbol in wanted:
            ticker = self.session.exec(select(Ticker).where(Ticker.symbol == symbol)).first()
            bar = None if ticker is None else self.session.exec(
                select(Price.id).where(Price.ticker_id == ticker.id, Price.date == self.target)
            ).first()
            if bar is None:
                missing.append(symbol)
        if missing:
            raise StepError(
                2, f"no {self.target} bar for {', '.join(missing)}; the next run retries"
            )

    def earnings(
        self, ingest_earnings: Callable[[Session, date], list[str]], last_ok: date | None
    ) -> None:
        """Step 3, not critical: the calendar from the last successful scan's as_of on, so the
        catch-up sessions keep their dates. On failure the stored dates are used."""
        since = self.target if last_ok is None else last_ok
        try:
            failed = ingest_earnings(self.session, since)
        except Exception as error:  # noqa: BLE001  # the stored dates still serve
            self.session.rollback()
            self.warn(f"earnings calendar FAILED ({_first_line(error)}); the stored dates were used")
            return
        if failed:
            self.warn(
                f"earnings calendar: {len(failed)} failed ({', '.join(failed)}); the stored dates "
                "were used for them"
            )

    def splits(self, fetch: SplitFetcher) -> None:
        """Step 4, not critical: record new splits of held and pending names, then the scale
        check. A failed lookup, a split that cannot be recorded, or prices no recorded split
        explains hold that symbol's decisions tonight, with a warning."""
        try:
            self._record_splits(fetch)
            self._scale_check()
        except Exception as error:  # noqa: BLE001  # hold everything held rather than stop
            self.session.rollback()
            held = self._held_and_pending()
            self.hold |= held
            self.warn(
                f"split check FAILED ({_first_line(error)}); no decisions tonight for "
                f"{', '.join(sorted(held)) or 'nothing'}"
            )

    def _lookups(self) -> dict[tuple[str, SplitKind, str], tuple[date, str]]:
        """(price symbol, kind, ledger symbol) -> (since, US symbol): the CDR of every open
        position since its opening buy, the US stock of every managed one since its signal, and
        both for every sent signal since its as_of."""
        found: dict[tuple[str, SplitKind, str], tuple[date, str]] = {}

        def add(ticker: Ticker, kind: SplitKind, since: date, us_symbol: str) -> None:
            key = (ticker.price_symbol or ticker.symbol, kind, ticker.symbol)
            earlier = found.get(key)
            found[key] = (since if earlier is None else min(since, earlier[0]), us_symbol)

        for episode in self.ledger.open_episodes():
            cdr = self.session.get(Ticker, episode.cdr_ticker_id)
            assert cdr is not None and cdr.us_ticker_id is not None
            us = self.session.get(Ticker, cdr.us_ticker_id)
            assert us is not None
            add(cdr, "cdr_split", episode.opening.trade_date, us.symbol)
            if episode.signal_id is not None:
                add(us, "us_split", self.ledger.signal(episode.signal_id).as_of, us.symbol)
        for signal in self._sent_signals():
            cdr = self.session.get(Ticker, signal.cdr_ticker_id)
            us = self._us_ticker(signal.us_symbol)
            assert cdr is not None and us is not None
            add(cdr, "cdr_split", signal.as_of, us.symbol)
            add(us, "us_split", signal.as_of, us.symbol)
        return found

    def _recorded(self, kind: SplitKind, symbol: str, ex_date: date) -> bool:
        query = select(CorporateAction.id).where(
            CorporateAction.kind == kind, CorporateAction.ex_date == ex_date,
            col(CorporateAction.voided).is_(False),
        )
        if kind == "us_split":
            query = query.where(CorporateAction.us_symbol == symbol)
        else:
            cdr = self.session.exec(select(Ticker).where(Ticker.symbol == symbol)).one()
            query = query.where(CorporateAction.cdr_ticker_id == cdr.id)
        return self.session.exec(query).first() is not None

    def _record_splits(self, fetch: SplitFetcher) -> None:
        for (price_symbol, kind, symbol), (since, us_symbol) in sorted(self._lookups().items()):
            try:
                found = fetch(price_symbol, since)
            except Exception as error:  # noqa: BLE001  # a lookup that failed is not "no split"
                self.hold.add(us_symbol)
                self.warn(
                    f"{us_symbol}: the split lookup for {price_symbol} failed "
                    f"({_first_line(error)}); no decisions for it tonight"
                )
                continue
            for split in sorted(found, key=lambda s: s.ex_date):
                if split.ex_date > self.target or self._recorded(kind, symbol, split.ex_date):
                    continue
                try:
                    self.ledger.record_split(
                        kind=kind, symbol=symbol, ex_date=split.ex_date,
                        ratio=Decimal(repr(split.ratio)), source="yfinance",
                    )
                except LedgerError as error:
                    self.hold.add(us_symbol)
                    self.warn(f"{us_symbol}: the {split.ex_date} split was not recorded ({error})")
                    continue
                self.tally["splits"] += 1

    def _scale_check(self) -> None:
        signal_ids = [s.id for s in self._sent_signals() if s.id is not None]
        signal_ids += [
            e.signal_id for e in self.ledger.open_episodes() if e.signal_id is not None
        ]
        for signal_id in signal_ids:
            problem = self.ledger.scale_check(signal_id)
            if problem is not None:
                symbol = self.ledger.signal(signal_id).us_symbol
                self.hold.add(symbol)
                self.warn(
                    f"{symbol}: prices moved in a way no recorded split explains; check it by "
                    f"hand ({problem})"
                )

    def review(
        self, day: date, market: MarketView, config: StrategyConfig, late: bool
    ) -> SessionReview:
        """Steps 5 and 6: the equity snapshot and the pause, then decide() for `day`, and the
        exit alerts and raises it calls for (a raise is recorded now, so later sessions use it)."""
        _, paused_now = self.ledger.record_equity(
            day, pause_drawdown=Decimal(repr(config.pause_drawdown))
        )
        if paused_now:
            self.pause_texts.append(pause_review(self.ledger, self.session, self.backtest))
        result = review_session(self.ledger, market, config, day, hold=self.hold)
        for call in result.exits:
            if self.ledger.open_exit_alert(call.signal_id) is None:
                self.ledger.record_exit_alert(
                    signal_id=call.signal_id, as_of=day, reason=call.reason, late=late
                )
                self.tally["exits"] += 1
        for up in result.raises:
            taken = self.session.exec(
                select(StopUpdateRow.id).where(
                    StopUpdateRow.signal_id == up.signal_id, StopUpdateRow.session == day,
                    StopUpdateRow.reason == "trail",
                )
            ).first()
            if taken is None:
                self.ledger.record_stop_update(
                    signal_id=up.signal_id, session=day, new_us_stop=up.new_us_stop, late=late
                )
                self.tally["raises"] += 1
        return result

    def size(self, tonight: SessionReview, config: StrategyConfig) -> list[Sized]:
        """Step 7: each entry on the CDR, in rank order, from the cash no pending signal holds."""
        self.skips.update(skip.reason for skip in tonight.decision.skips)
        snapshot = self.ledger.equity(self.target)
        cash = self.ledger.cash(self.target) - sum(
            (s.suggested_units * s.cdr_signal_close for s in self.ledger.pending_signals(self.target)),
            ZERO,
        )
        cdrs = {entry.us_symbol: entry.cdr_symbol for entry in self.universe}
        sized: list[Sized] = []
        for entry in tonight.decision.entries:
            recorded = self.session.exec(
                select(TradeSignal.id).where(
                    TradeSignal.as_of == self.target, TradeSignal.us_symbol == entry.symbol,
                    TradeSignal.setup == "breakout",
                )
            ).first()
            if recorded is not None:
                self.skips["already_sent"] += 1
                continue
            cdr = self.session.exec(
                select(Ticker).where(Ticker.symbol == cdrs.get(entry.symbol, ""))
            ).first()
            close = None if cdr is None or not cdr.active else self.session.exec(
                select(col(Price.close))
                .where(Price.ticker_id == cdr.id, col(Price.date) <= self.target)
                .order_by(col(Price.date).desc())
            ).first()
            if cdr is None or close is None:
                self.skips["no_cdr_price"] += 1
                continue
            size = size_cdr(
                us_signal_close=q4(Decimal(repr(entry.signal_close))),
                us_stop=q4(Decimal(repr(entry.stop))), cdr_close=close,
                equity=snapshot.equity, uncommitted_cash=cash,
                risk_pct=Decimal(repr(config.risk_pct)), max_positions=config.max_positions,
            )
            if size is None:
                self.skips["no_cash"] += 1
                continue
            cash -= size.cost
            sized.append(Sized(entry, size, cdr))
        return sized

    def explain(self, sized: Sized, market: MarketView, config: StrategyConfig) -> Sized:
        """Step 8: the template Why line, from the Snapshot that fired the rule."""
        snapshot = market.at(self.target).snapshot(sized.entry.symbol)
        assert snapshot is not None
        return Sized(sized.entry, sized.size, sized.cdr, why_line(snapshot, config.breakout))

    def send(self, explained: list[Sized]) -> None:
        """Step 9: write tonight's signals, then send every unsent row and any pause review."""
        window = entry_window(self.target, self.nyse, self.cboe)
        for item in explained:
            self.ledger.record_signal(
                as_of=self.target, us_symbol=item.entry.symbol, cdr_symbol=item.cdr.symbol,
                us_signal_close=q4(Decimal(repr(item.entry.signal_close))),
                us_stop=q4(Decimal(repr(item.entry.stop))), cdr_signal_close=item.size.cdr_close,
                suggested_units=item.size.units, order_type=item.size.order_type,
                risk_amount_cad=item.size.risk, explanation=item.why,
                expires_at=window.expires_at,
            )
            self.tally["signals"] += 1
        self.tally["sent"] += send_unsent(
            self.session, self.ledger, self.messenger, nyse=self.nyse, cboe=self.cboe,
            now=self.now,
        )
        for text in self.pause_texts:
            self.messenger.send(text)
            self.tally["sent"] += 1

    def expire_and_scale_up(self) -> str | None:
        """Step 10: signals whose entry session has closed expire; the scale-up check runs after
        each close of a managed position. Returns its line for the summary when it ran."""
        close = datetime.combine(self.target, MARKET_CLOSE, tzinfo=NEW_YORK)
        for signal in self._sent_signals():
            if signal.expires_at <= close and signal.id is not None:
                self.ledger.mark_signal(signal.id, "expired")
                self.tally["expired"] += 1
        closed = sum(1 for trade in self.ledger.closed_trades() if trade.episode.managed)
        history = self.ledger.risk_state().scale_up_history
        checked = int(history[-1]["closed_managed"]) if history else 0
        if closed <= checked:
            return None
        result = self.ledger.scale_up_check()
        self.ledger.record_scale_up(result)
        return scale_up_text(result)

    def check_heartbeat(self) -> None:
        problem = heartbeat_problem(self.session, self.now)
        if problem is not None:
            self.warnings.append(problem)
            self.echo(f"BOT STALE: {problem}")

    def summary(self, market: MarketView, config: StrategyConfig, scale_up: str | None) -> None:
        """Step 11: the evening summary."""
        view = market.at(self.target)
        allowed = view.regime_on()
        regime = (
            f"{config.regime_symbol} {'above' if allowed else 'below'} its {config.regime_sma}-"
            f"session average: new entries {'allowed' if allowed else 'off'}"
        )
        self.check_heartbeat()
        tonight = Tonight(
            as_of=self.target, regime=regime, signals=self.tally["signals"], skips=self.skips,
            exits=self.tally["exits"], raises=self.tally["raises"],
            caught_up=tuple(self.caught_up), warnings=tuple(self.warnings), scale_up=scale_up,
        )
        self.messenger.send(evening_summary(self.ledger, self.session, tonight))
        self.tally["sent"] += 1
```

- [ ] **Step 5: Run the tests and checks**

Run: `uv run pytest tests/test_live_scan.py -q && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 16 passed; the full suite 762 passed; ruff `All checks passed!`; mypy `Success: no issues found in 82 source files`. (The scan tests build the 230-session world several times: about 20 seconds.)

- [ ] **Step 6: Commit**

```bash
git add src/signalbench/live/heartbeat.py src/signalbench/live/scan.py tests/scan_helpers.py tests/test_live_scan.py
git commit -m "feat: signalbench scan, the eleven steps with catch-up, idempotency, and the lock

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: The bot's brain, `/status`, and the end-to-end fixture test

**Files:**
- Modify: `tests/scan_helpers.py`
- Create: `src/signalbench/live/status.py`, `src/signalbench/live/bot.py`, `tests/test_live_bot.py`, `tests/test_live_e2e.py`

The end-to-end test is the spec's: the Breakout fires on D, ✅ with `3 10.45` records the fill, the new high of D+3 raises the stop (a ⬆️ in both currencies), the close of D+4 falls to it (the exit alert), and ✅ Sold at C$9.70 closes the position: P&L −C$2.25 = 29.10 − 31.35, the ACB 31.35, and R = −2.25 / (3 × 0.40) = −1.875 on planned risk. The catch-up case skips D+3 and D+4 and runs on D+5.

- [ ] **Step 1: Write the failing tests**

In `tests/scan_helpers.py`, replace:

```python
from signalbench.live.heartbeat import write_heartbeat
```

with:

```python
from signalbench.live.bot import BotBrain
from signalbench.live.heartbeat import write_heartbeat
```

Append to `tests/scan_helpers.py`:

```python
OWNER = 4242  # the owner's chat: TELEGRAM_CHAT_ID


def bot_brain(world: World, index: int = B + 1, hour: int = 10) -> BotBrain:
    """The bot's brain on `index`'s New York date (by default the session after the signal)."""
    return BotBrain(
        chat_id=OWNER, sessions=lambda: Session(world.session.get_bind()),
        calendar=WeekdaySessions(), clock=lambda: evening(DAYS[index], hour),
        next_run=lambda: "2026-10-06 15:00 (local)",
    )
```

Create `tests/test_live_bot.py`:

```python
"""Spec 05, Commands and the buttons: the bot's brain on the Breakout world, without Telegram.
Every command's parsing and error replies, the allowlist, and each button."""

import logging
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlmodel import Session, select

from scan_helpers import DAYS, OWNER, B, World, bot_brain, make_world
from signalbench.db.models import ExitAlert, Fill, TradeSignal
from signalbench.live.bot import HELP, Reply
from signalbench.live.ledger import Ledger
from signalbench.live.messenger import Sent
from strategy_helpers import WeekdaySessions


@pytest.fixture
def world(session: Session, tmp_path: Path) -> World:
    world = make_world(session, tmp_path)
    world.scan(B)  # the NVDA entry: 3 ZNVD at C$10.10, stop C$9.70
    return world


def texts(replies: list[Reply]) -> list[str]:
    return [reply.text for reply in replies]


def entry(world: World) -> Sent:
    return world.messenger.sent[0]


def test_updates_from_any_other_chat_are_ignored_and_logged(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    bot = bot_brain(world)
    with caplog.at_level(logging.WARNING, logger="signalbench.live.bot"):
        assert bot.handle_text(999, "/deposit 100") == []
        assert bot.handle_callback(999, "b:1", entry(world).message_id, entry(world).text) == []
    assert caplog.messages == ["ignored an update from chat 999 (not TELEGRAM_CHAT_ID)"] * 2
    assert bot_brain(world).handle_text(OWNER, "/portfolio")[0].text.startswith("💼")


def test_help_and_unknown_input(world: World) -> None:
    bot = bot_brain(world)
    assert texts(bot.handle_text(OWNER, "/help")) == [HELP]
    assert texts(bot.handle_text(OWNER, "/start@signalbench_bot")) == [HELP]
    assert texts(bot.handle_text(OWNER, "/frobnicate")) == [
        "⚠️ unknown command /frobnicate. Send /help for the commands."
    ]
    assert texts(bot.handle_text(OWNER, "hello")) == ["Send /help for the commands."]
    for command in ("/signals", "/portfolio", "/pnl", "/buy", "/sell", "/void", "/deposit",
                    "/withdraw", "/resume", "/status", "/tax", "/help"):
        assert command in HELP


def test_i_bought_asks_for_units_and_price_then_records_the_linked_fill(world: World) -> None:
    bot = bot_brain(world)
    sent = entry(world)
    [ask] = bot.handle_callback(OWNER, "b:1", sent.message_id, sent.text)
    assert ask.text == (
        "How many units of ZNVD did you buy, and at what price? Reply like `3 10.1` (a third "
        "token sets the date, YYYY-MM-DD)."
    )
    assert [(b.text, b.data) for row in ask.buttons for b in row] == [
        ("Use suggested: 3 units @ C$10.10", "u:1")
    ]
    assert texts(bot.handle_text(OWNER, "3 ten")) == ["⚠️ the price must be a number, not 'ten'"]
    confirm, edit = bot.handle_text(OWNER, "3 10.45")
    assert confirm.text == (
        "✅ Bought 3 units ZNVD @ C$10.45 on 2026-10-06 (fill 1), for signal 1. Stop C$9.70 / "
        "US$97.00; the evening scan manages it from here."
    )
    assert (edit.edit, edit.buttons) == (sent.message_id, ())
    assert edit.text == f"{sent.text}\n✅ Bought 3 units @ C$10.45 (fill 1)"
    [fill] = world.session.exec(select(Fill)).all()
    assert (fill.signal_id, fill.quantity, fill.price_cad, fill.trade_date) == (
        1, Decimal(3), Decimal("10.45"), DAYS[B + 1]
    )
    assert bot.awaiting is None
    assert texts(bot.handle_callback(OWNER, "b:1", sent.message_id, sent.text)) == [
        "Already logged: signal 1 is taken."
    ]


def test_use_suggested_records_the_suggested_size_at_the_signal_close(world: World) -> None:
    sent = entry(world)
    replies = bot_brain(world).handle_callback(OWNER, "u:1", sent.message_id, sent.text)
    assert replies[0].text.startswith("✅ Bought 3 units ZNVD @ C$10.10 on 2026-10-06 (fill 1)")


def test_skip_asks_for_a_reason_and_records_it(world: World) -> None:
    bot = bot_brain(world)
    sent = entry(world)
    [reasons] = bot.handle_callback(OWNER, "s:1", sent.message_id, sent.text)
    assert (reasons.edit, reasons.text) == (sent.message_id, sent.text)
    assert reasons.buttons[0][0].data == "k:1:disagree"
    [skipped] = bot.handle_callback(OWNER, "k:1:wide_spread", sent.message_id, sent.text)
    assert skipped.edit == sent.message_id
    assert skipped.text == f"{sent.text}\n⏭ Skipped (wide spread)"
    signal = world.session.exec(select(TradeSignal)).one()
    world.session.refresh(signal)
    assert (signal.status, signal.skip_reason) == ("skipped", "wide_spread")
    assert texts(bot.handle_callback(OWNER, "k:1:other", sent.message_id, sent.text)) == [
        "Already logged: signal 1 is skipped."
    ]
    assert texts(bot.handle_callback(OWNER, "zz:1", 1, "")) == [
        "⚠️ That button is no longer valid."
    ]


def _exit_alert(world: World) -> Sent:
    """Bought on B+1, then the scans to the stop exit on B+4."""
    bot = bot_brain(world)
    sent = entry(world)
    bot.handle_callback(OWNER, "b:1", sent.message_id, sent.text)
    bot.handle_text(OWNER, "3 10.45")
    for index in range(B + 1, B + 5):
        world.scan(index)
    alert = next(m for m in world.messenger.sent if m.text.startswith("🔴"))
    return alert


def test_sold_asks_for_units_and_price_and_closes_the_position(world: World) -> None:
    alert = _exit_alert(world)
    bot = bot_brain(world, B + 5)
    [ask] = bot.handle_callback(OWNER, "x:1", alert.message_id, alert.text)
    assert ask.text.startswith("How many units did you sell, and at what price?")
    sold, edit = bot.handle_text(OWNER, "9.70")
    assert sold.text == (
        "✅ Sold 3 units ZNVD @ C$9.70 on 2026-10-12 (fill 2). Position closed: P&L −C$2.25 · "
        "−1.88R on planned risk."
    )
    assert edit.text == f"{alert.text}\n✅ Sold 3 units @ C$9.70 (fill 2)"
    exit_alert = world.session.exec(select(ExitAlert)).one()
    world.session.refresh(exit_alert)
    assert exit_alert.status == "done"
    assert texts(bot.handle_callback(OWNER, "x:1", alert.message_id, alert.text)) == [
        "Already logged: exit alert 1 is done."
    ]


def test_ignore_records_a_miss(world: World) -> None:
    alert = _exit_alert(world)
    [ignored] = bot_brain(world, B + 5).handle_callback(OWNER, "i:1", alert.message_id, alert.text)
    assert ignored.text.endswith(
        "Ignored: this counts as a miss in the scale-up check, and the position is managed "
        "again from the next session."
    )


def test_the_sell_command_is_linked_to_the_open_exit_alert(world: World) -> None:
    _exit_alert(world)
    replies = bot_brain(world, B + 5).handle_text(OWNER, "/sell nvda all 9.70")
    [sold] = texts(replies)
    assert sold == (
        "✅ Sold 3 units ZNVD @ C$9.70 on 2026-10-12 (fill 2). Position closed: P&L −C$2.25 · "
        "−1.88R on planned risk."
    )
    [fill] = world.session.exec(select(Fill).where(Fill.side == "sell")).all()
    assert fill.exit_alert_id == 1

def test_buy_and_sell_commands_and_their_errors(world: World) -> None:
    bot = bot_brain(world)
    assert texts(bot.handle_text(OWNER, "/buy ZNVD 2")) == [
        "⚠️ Usage: /buy SYMBOL QTY PRICE [YYYY-MM-DD] [force]"
    ]
    assert texts(bot.handle_text(OWNER, "/buy ZZZZ 1 10")) == [
        "⚠️ unknown symbol 'ZZZZ': use a CDR symbol, like ZNVD"
    ]
    assert texts(bot.handle_text(OWNER, "/buy xom one 5")) == [
        "⚠️ the units must be a number, not 'one'"
    ]
    assert texts(bot.handle_text(OWNER, "/buy xom 1 5 2026-13-01")) == [
        "⚠️ the date must be YYYY-MM-DD, not '2026-13-01'"
    ]
    [refused] = texts(bot.handle_text(OWNER, "/buy XOM 20 5.10"))  # a US symbol, one CDR
    assert refused.startswith("⚠️ a buy of C$102.00 on 2026-10-06 takes cash to C$-2.00")
    assert texts(bot.handle_text(OWNER, "/buy XOM 20 5.10 force")) == [
        "✅ Bought 20 units ZXOM @ C$5.10 on 2026-10-06 (fill 1), manual: no stop or exit alerts."
    ]
    assert texts(bot.handle_text(OWNER, "/sell ZXOM 25 5.20")) == [
        "⚠️ ZXOM: a sale of 25 on 2026-10-06 is more than the 20 units held"
    ]
    assert texts(bot.handle_text(OWNER, "/sell ZXOM 5 5.20")) == [
        "✅ Sold 5 units ZXOM @ C$5.20 on 2026-10-06 (fill 2). Still holding 15 units."
    ]
    assert texts(bot.handle_text(OWNER, "/sell ZXOM all 5.00 2026-10-06")) == [
        "✅ Sold 15 units ZXOM @ C$5.00 on 2026-10-06 (fill 3). Position closed: P&L −C$1.00."
    ]
    assert texts(bot.handle_text(OWNER, "/sell ZXOM all 5.00")) == [
        "⚠️ no ZXOM units are held"
    ]


def test_cash_void_and_the_read_only_commands(world: World) -> None:
    bot = bot_brain(world)
    assert texts(bot.handle_text(OWNER, "/deposit 100")) == [
        "Deposit of C$100.00 recorded on 2026-10-06. Cash is now C$200.00."
    ]
    assert texts(bot.handle_text(OWNER, "/withdraw 0")) == ["⚠️ the amount must be above 0"]
    assert texts(bot.handle_text(OWNER, "/withdraw 50.005")) == [
        "⚠️ amount -50.005 has more than 2 decimals"
    ]
    bot.handle_text(OWNER, "/buy ZXOM 2 5")
    assert texts(bot.handle_text(OWNER, "/void 1 typo")) == [
        "Fill 1 voided: typo. Cash is now C$200.00."
    ]
    assert texts(bot.handle_text(OWNER, "/void x")) == ["⚠️ Usage: /void FILL_ID REASON"]
    [signals] = texts(bot.handle_text(OWNER, "/signals"))
    assert signals == (
        "Open signals:\n• 1 ZNVD (NVDA) 3 units · LIMIT C$10.20 · stop C$9.70 · until Tue 06 Oct "
        "16:00 New York"
    )
    assert texts(bot_brain(world, B + 2).handle_text(OWNER, "/signals")) == ["No open signals."]
    [portfolio] = texts(bot.handle_text(OWNER, "/portfolio"))
    assert portfolio.splitlines()[1] == "No open positions."
    [pnl] = texts(bot.handle_text(OWNER, "/pnl"))
    assert pnl.splitlines()[-1] == (
        "No closed managed trades yet (no stored backtest run of the live config to compare)"
    )
    [tax] = texts(bot.handle_text(OWNER, "/tax 2026"))
    assert tax.splitlines()[0] == "ACB report 2026: 0 dispositions"
    assert tax.splitlines()[-1] == "Every sale: signalbench ledger tax 2026 --csv PATH"
    assert texts(bot.handle_text(OWNER, "/tax soon")) == ["⚠️ Usage: /tax YEAR"]


def test_status_shows_the_last_scan_the_config_sha_and_the_code_version(world: World) -> None:
    [status] = texts(bot_brain(world).handle_text(OWNER, "/status"))
    lines = status.splitlines()
    assert lines[0] == "Last scan: 2026-10-05 ok, started 2026-10-05 18:00 New York"
    assert lines[1] == "Next scheduled scan: 2026-10-06 15:00 (local)"
    assert lines[2].startswith("Live config: data/strategy_v2-none-cash.yaml · sha256 a9579593 · "
                               "code ")
    assert len(lines[2].rsplit(" ", 1)[1]) == 8
    assert lines[3] == "New entries: not paused"


def test_resume_shows_the_review_and_confirm_resumes(world: World) -> None:
    bot = bot_brain(world)
    assert texts(bot.handle_text(OWNER, "/resume")) == ["New entries are not paused."]
    with Session(world.session.get_bind()) as session:
        risk = Ledger(session, calendar=WeekdaySessions(), today=DAYS[B + 1]).risk_state()
        risk.paused, risk.paused_at, risk.paused_reason = True, DAYS[B], "a test pause"
        session.add(risk)
        session.commit()
    [review] = bot.handle_text(OWNER, "/resume")
    assert review.text.splitlines()[0] == (
        "⏸ New entries are paused since the close of 2026-10-05: a test pause."
    )
    assert review.text.splitlines()[-1] == "/resume to continue (resets peak)"
    assert [b.data for row in review.buttons for b in row] == ["rc"]
    assert texts(bot.handle_callback(OWNER, "rc", 7, review.text)) == [
        "▶️ Resumed: new entries are allowed again; the peak counts from 2026-10-06."
    ]
    assert texts(bot.handle_callback(OWNER, "rc", 7, review.text)) == [
        "⚠️ new entries are not paused"
    ]


def test_a_date_token_backdates_a_reply(world: World) -> None:
    bot = bot_brain(world, B + 2)
    sent = entry(world)
    bot.handle_callback(OWNER, "b:1", sent.message_id, sent.text)
    confirm, _ = bot.handle_text(OWNER, "3 10.45 2026-10-06")
    assert "on 2026-10-06 (fill 1)" in confirm.text
    assert world.session.exec(select(Fill)).one().trade_date == date(2026, 10, 6)
```

Create `tests/test_live_e2e.py`:

```python
"""Spec 05, Testing: the end-to-end fixture test and the catch-up case.

A Breakout fires, the owner taps ✅ and replies, a new high raises the trailing stop, the close
falls to the raised stop, and ✅ Sold closes the position with its P&L, ACB, and R on planned
risk. Then two missed evenings: exits and raises are caught up in order and sent marked late,
and only the target's entries are sent. FakeMessenger and stored prices: no network.
"""

from decimal import Decimal
from pathlib import Path

from sqlmodel import Session, col, select

from scan_helpers import DAYS, NVDA, OWNER, B, bot_brain, make_world
from signalbench.db.models import ExitAlert, Fill, StopUpdateRow, TradeSignal

WHY = (
    "Why: Closed at a 20-session high (US$101.00) on 2.0× its 50-session average volume, above "
    "its 50-session average."
)


def test_breakout_fill_raise_stop_hit_and_sale(session: Session, tmp_path: Path) -> None:
    world = make_world(session, tmp_path)

    # D: the Breakout fires. The entry says how the position is managed, and why.
    world.scan(B)
    entry = world.messenger.sent[0]
    assert "Trailing stop, no target, no time limit" in entry.text
    assert entry.text.splitlines()[-1] == WHY
    signal = session.exec(select(TradeSignal)).one()
    assert (signal.as_of, signal.us_symbol, signal.status) == (DAYS[B], "NVDA", "sent")

    # D+1: ✅ I bought, then "3 10.45": a fill linked to the signal.
    bot = bot_brain(world, B + 1)
    bot.handle_callback(OWNER, f"b:{signal.id}", entry.message_id, entry.text)
    bot.handle_text(OWNER, "3 10.45")
    fill = session.exec(select(Fill)).one()
    assert (fill.signal_id, fill.side, fill.quantity, fill.price_cad) == (
        signal.id, "buy", Decimal(3), Decimal("10.45")
    )

    # D+1 and D+2: 102 - 6 and 103 - 6 are not above the initial stop of 97.
    world.scan(B + 1)
    world.scan(B + 2)
    assert session.exec(select(StopUpdateRow)).all() == []

    # D+3: the new high of 104 raises the stop to 104 - 3 x 2 = 98, shown in both currencies.
    before = len(world.messenger.sent)
    world.scan(B + 3)
    [raised] = [m.text for m in world.messenger.sent[before:] if m.text.startswith("⬆️")]
    assert raised == (
        "⬆️ NVDA (CDR ZNVD) stop raised C$9.70 → C$9.80 (US$97.00 → US$98.00) · still holding, "
        "no action"
    )
    [row] = session.exec(select(StopUpdateRow)).all()
    assert (row.reason, row.session, row.old_us_stop, row.new_us_stop, row.late) == (
        "trail", DAYS[B + 3], Decimal(97), Decimal(98), False
    )

    # D+4: the close of 97.50 is at or below the raised stop: an exit alert.
    before = len(world.messenger.sent)
    world.scan(B + 4)
    alert_message = next(m for m in world.messenger.sent[before:] if m.text.startswith("🔴"))
    assert alert_message.text == (
        "🔴 SELL NVDA (CDR ZNVD) — stop hit (US close US$97.50 ≤ stop US$98.00 · CDR ~C$9.75 vs "
        "stop C$9.80)\n"
        "Held 4 sessions · unrealized −C$2.10 (−6.7%)\n"
        "Sell at the open."
    )
    alert = session.exec(select(ExitAlert)).one()
    assert (alert.as_of, alert.reason, alert.late) == (DAYS[B + 4], "stop", False)

    # D+5, not sold yet: one open exit alert per position, so no new alert and no raise, and
    # the summary repeats the open alert.
    before = len(world.messenger.sent)
    world.scan(B + 5)
    [summary] = world.messenger.sent[before:]
    assert "• ZNVD stop exit since 2026-10-09: sell, or tap Ignore" in summary.text.splitlines()
    assert len(session.exec(select(ExitAlert)).all()) == 1
    assert len(session.exec(select(StopUpdateRow)).all()) == 1

    # D+6: ✅ Sold, "9.70": the position closes with the right P&L, ACB, and R.
    bot = bot_brain(world, B + 6)
    bot.handle_callback(OWNER, f"x:{alert.id}", alert_message.message_id, alert_message.text)
    [sold, _] = bot.handle_text(OWNER, "9.70")
    assert sold.text == (
        "✅ Sold 3 units ZNVD @ C$9.70 on 2026-10-13 (fill 2). Position closed: P&L −C$2.25 · "
        "−1.88R on planned risk."
    )
    ledger = world.ledger(B + 6)
    [trade] = ledger.closed_trades()
    assert (trade.pnl, trade.planned_risk, trade.r) == (
        Decimal("-2.25"), Decimal("1.20"), Decimal("-1.875")
    )  # 29.10 - 31.35; 3 x (10.10 - 9.70)
    [sale] = ledger.books()["ZNVD"].dispositions
    assert (sale.proceeds, sale.acb, sale.gain) == (
        Decimal("29.10"), Decimal("31.35"), Decimal("-2.25")
    )
    session.refresh(alert)
    assert alert.status == "done"

    # The next scan runs the scale-up check after the close of a managed position.
    world.scan(B + 6)
    assert world.messenger.sent[-1].text.splitlines()[-1] == (
        "Scale-up check: 1 of 10 managed trades closed."
    )


def test_catch_up_after_two_missed_evenings(session: Session, tmp_path: Path) -> None:
    world = make_world(
        session, tmp_path,
        closes={"NVDA": NVDA, "XOM": {B + 3: 51.0}, "AAPL": {B + 5: 101.0}},
        spikes={"NVDA": {B: 2_000_000}, "XOM": {B + 3: 2_000_000}, "AAPL": {B + 5: 2_000_000}},
    )
    world.scan(B)
    [entry, _] = world.messenger.sent
    bot = bot_brain(world, B + 1)
    bot.handle_callback(OWNER, "b:1", entry.message_id, entry.text)
    bot.handle_text(OWNER, "3 10.45")
    world.scan(B + 1)
    world.scan(B + 2)

    # The PC was off on D+3 and D+4. The run on D+5 catches them up first, in order.
    before = len(world.messenger.sent)
    outcome = world.scan(B + 5)
    assert outcome.counts == {"exits": 1, "raises": 1, "signals": 1, "sent": 4}
    aapl, stop_exit, raised, summary = (m.text for m in world.messenger.sent[before:])
    # Entries come only from the target: AAPL on D+5, not XOM's stale breakout of D+3.
    assert aapl.startswith("🟢 BUY AAPL (CDR ZAAP) — Breakout")
    signals = session.exec(select(TradeSignal).order_by(col(TradeSignal.id))).all()
    assert [(s.as_of, s.us_symbol) for s in signals] == [(DAYS[B], "NVDA"), (DAYS[B + 5], "AAPL")]
    # D+3's new high raised the stop to 98, and that stop was in force for D+4's 97.50.
    assert raised == (
        "⬆️ NVDA (CDR ZNVD) stop raised C$9.70 → C$9.80 (US$97.00 → US$98.00) · still holding, "
        "no action (late — for 2026-10-08)"
    )
    assert stop_exit.splitlines()[0] == (
        "🔴 SELL NVDA (CDR ZNVD) — stop hit (US close US$97.50 ≤ stop US$98.00 · CDR ~C$9.75 vs "
        "stop C$9.80) (late — should have been sent after 2026-10-09)"
    )
    assert world.ledger(B + 5).stop_in_force(1, DAYS[B + 4]).us == Decimal(98)
    alert = session.exec(select(ExitAlert)).one()
    assert (alert.as_of, alert.late) == (DAYS[B + 4], True)
    assert (
        "Caught up (exits and stop raises only, sent marked late): 2026-10-08, 2026-10-09"
    ) in summary.splitlines()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_live_bot.py tests/test_live_e2e.py tests/test_live_scan.py -q`
Expected: 3 collection errors, `ModuleNotFoundError: No module named 'signalbench.live.bot'` (`tests/test_live_scan.py` too: the helper module now imports `BotBrain`).

- [ ] **Step 3: Write the status lines**

Create `src/signalbench/live/status.py`:

```python
"""The scan's status (spec 05): `scan status`, the stale check the scan script runs, and the
bot's /status."""

from collections.abc import Callable
from datetime import datetime, timedelta

from sqlmodel import Session, col, select

from signalbench.db.models import LiveConfig, LiveRiskState, ScanRun
from signalbench.live.book import NEW_YORK


def _when(moment: datetime) -> str:
    return f"{moment.astimezone(NEW_YORK):%Y-%m-%d %H:%M} New York"


def last_scan(session: Session, *, ok: bool = False) -> ScanRun | None:
    query = select(ScanRun)
    if ok:
        query = query.where(ScanRun.status == "ok")
    return session.exec(query.order_by(col(ScanRun.started_at).desc())).first()


def scan_stale_message(session: Session, now: datetime, days: int) -> str | None:
    """A warning when no scan has succeeded for more than `days` days (counted from `live
    start` when none has yet); None otherwise, or before `live start`."""
    last = last_scan(session, ok=True)
    live = session.get(LiveConfig, 1)
    since = last.started_at if last is not None else (None if live is None else live.created_at)
    if since is None or now - since <= timedelta(days=days):
        return None
    when = "never" if last is None else f"on {_when(last.started_at)}"
    return f"STALE: the last ok scan was {when}, more than {days} days ago."


def scan_status_lines(session: Session, next_run: Callable[[], str | None]) -> list[str]:
    """The last scan and its time, the next scheduled run, the live config and its sha256,
    the code version (the last scan's git sha), and the pause state."""
    last = last_scan(session)
    if last is None:
        lines = ["Last scan: none yet"]
    else:
        result = last.status
        if last.status == "failed":
            result = f"FAILED at step {last.failed_step}: {last.error}"
        lines = [f"Last scan: {last.as_of} {result}, started {_when(last.started_at)}"]
        ok = last_scan(session, ok=True)
        if ok is not None and ok.id != last.id:
            lines.append(f"Last ok scan: {ok.as_of}, started {_when(ok.started_at)}")
    lines.append(f"Next scheduled scan: {next_run() or 'unknown (the task is not registered)'}")
    live = session.get(LiveConfig, 1)
    if live is None:
        lines.append("Live config: none (run `signalbench live start`)")
    else:
        code = "no scan yet" if last is None or last.git_sha is None else last.git_sha[:8]
        lines.append(
            f"Live config: {live.config_path} · sha256 {live.config_sha256[:8]} · code {code}"
        )
    risk = session.get(LiveRiskState, 1)
    paused = risk is not None and risk.paused
    lines.append(
        f"New entries: PAUSED since {risk.paused_at} (/resume)" if paused and risk is not None
        else "New entries: not paused"
    )
    return lines
```

- [ ] **Step 4: Write the bot's brain**

Create `src/signalbench/live/bot.py`:

```python
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
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Literal, cast, get_args

from sqlmodel import Session, col, select

from signalbench.db.models import ExitAlert, LiveConfig, Ticker, TickerKind, TradeSignal
from signalbench.live.book import NEW_YORK, LedgerError, SignalSkip
from signalbench.live.ledger import Ledger
from signalbench.live.messages import cad, signed_cad, skip_buttons, units_text, usd
from signalbench.live.messenger import Button, Buttons
from signalbench.live.sizing import CENT, LIMIT_FACTOR
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
            limit = (s.cdr_signal_close * LIMIT_FACTOR).quantize(CENT, rounding=ROUND_DOWN)
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
```

- [ ] **Step 5: Run the tests and checks**

Run: `uv run pytest tests/test_live_bot.py tests/test_live_e2e.py tests/test_live_scan.py -q && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 31 passed (13 bot, 2 end-to-end, and the 16 scan tests); the full suite 777 passed; ruff `All checks passed!`; mypy `Success: no issues found in 84 source files`.

- [ ] **Step 6: Commit**

```bash
git add src/signalbench/live/status.py src/signalbench/live/bot.py tests/scan_helpers.py tests/test_live_bot.py tests/test_live_e2e.py
git commit -m "feat: the bot's commands and buttons, and the end-to-end fixture test

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: The bot process on python-telegram-bot, and its heartbeat

**Files:**
- Create: `src/signalbench/live/telegram_bot.py`, `tests/test_live_telegram_bot.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_live_telegram_bot.py`:

```python
"""Spec 05, Telegram and processes: the python-telegram-bot glue and the heartbeat, against a
Bot API that answers from memory (no network)."""

import asyncio
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session
from telegram import Bot, Update
from telegram.ext import TypeHandler

from paper_helpers import evening
from scan_helpers import DAYS, OWNER, B, World, bot_brain, make_world
from signalbench.live.heartbeat import heartbeat_problem, write_heartbeat
from signalbench.live.telegram_bot import beat_once, build_application, handle_update
from telegram_helpers import TOKEN, FakeTelegram

USER = {"id": OWNER, "is_bot": False, "first_name": "Owner"}


@pytest.fixture
def world(session: Session, tmp_path: Path) -> World:
    world = make_world(session, tmp_path)
    world.scan(B)
    return world


def _run(api: FakeTelegram, updates: list[dict[str, Any]], world: World) -> None:
    async def main() -> None:
        bot = Bot(TOKEN, request=api, get_updates_request=api)
        await bot.initialize()
        brain = bot_brain(world)
        for data in updates:
            await handle_update(Update.de_json(data, bot), brain, bot)
        await bot.shutdown()

    asyncio.run(main())


def _message(chat: int, text: str) -> dict[str, Any]:
    return {"update_id": 1, "message": {
        "message_id": 9, "date": 0, "chat": {"id": chat, "type": "private"},
        "from": {**USER, "id": chat}, "text": text,
    }}


def test_a_command_is_answered_in_the_owners_chat(world: World) -> None:
    api = FakeTelegram()
    _run(api, [_message(OWNER, "/deposit 50")], world)
    [sent] = api.sent("sendMessage")
    assert (sent["chat_id"], sent["text"]) == (
        OWNER, "Deposit of C$50.00 recorded on 2026-10-06. Cash is now C$150.00."
    )


def test_a_button_press_is_answered_and_its_reply_asks_for_the_fill(world: World) -> None:
    entry = world.messenger.sent[0]
    press = {"update_id": 2, "callback_query": {
        "id": "cb-1", "from": USER, "chat_instance": "ci", "data": "b:1",
        "message": {"message_id": entry.message_id, "date": 0, "text": entry.text,
                    "chat": {"id": OWNER, "type": "private"}},
    }}
    api = FakeTelegram()
    _run(api, [press], world)
    assert [name for name, _ in api.calls] == ["getMe", "answerCallbackQuery", "sendMessage"]
    [ask] = api.sent("sendMessage")
    assert ask["text"].startswith("How many units of ZNVD did you buy")
    assert ask["reply_markup"] == {
        "inline_keyboard": [[{"text": "Use suggested: 3 units @ C$10.10", "callback_data": "u:1"}]]
    }


def test_a_foreign_chat_gets_no_answer_at_all(world: World) -> None:
    api = FakeTelegram()
    press = {"update_id": 3, "callback_query": {
        "id": "cb-2", "from": {**USER, "id": 999}, "chat_instance": "ci", "data": "b:1",
        "message": {"message_id": 5, "date": 0, "text": "x", "chat": {"id": 999, "type": "private"}},
    }}
    _run(api, [_message(999, "/portfolio"), press], world)
    assert [name for name, _ in api.calls] == ["getMe"]  # nothing sent, not even an answer


def test_the_application_takes_every_update_to_one_handler_without_the_network(
    world: World,
) -> None:
    api = FakeTelegram()
    app = build_application(TOKEN, bot_brain(world), lambda: None, request=api)
    [[handler]] = app.handlers.values()
    assert isinstance(handler, TypeHandler)
    assert api.calls == []


def test_a_heartbeat_needs_telegram_to_answer_and_the_scan_sees_its_age(world: World) -> None:
    session = world.session
    now = evening(DAYS[B + 1])

    def write() -> None:
        write_heartbeat(Session(session.get_bind()), now)

    async def beat(api: FakeTelegram) -> bool:
        bot = Bot(TOKEN, request=api, get_updates_request=api)
        await bot.initialize()
        return await beat_once(bot, write)

    assert asyncio.run(beat(FakeTelegram())) is True
    assert heartbeat_problem(session, now + timedelta(minutes=59)) is None
    assert heartbeat_problem(session, now + timedelta(hours=2)) == (
        "the bot last checked in 2.0 hours ago (2026-10-06 18:00 New York time): is it running?"
    )

    class Down(FakeTelegram):
        def _result(self, endpoint: str, params: dict[str, Any]) -> object:
            if endpoint == "getMe" and len(self.calls) > 1:
                raise ConnectionError("no network")
            return super()._result(endpoint, params)

    assert asyncio.run(beat(Down())) is False  # the first getMe initializes; the beat fails
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_live_telegram_bot.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'signalbench.live.telegram_bot'`.

- [ ] **Step 3: Write the glue**

Create `src/signalbench/live/telegram_bot.py`:

```python
"""The bot process (spec 05, Telegram and processes): python-telegram-bot with long polling.

One handler takes every update to `BotBrain` (live/bot.py) and carries its replies back:
new messages, or edits of the message whose button was pressed. Only the owner's chat is
answered. A background task checks that Telegram answers and then writes the heartbeat every
five minutes, so the evening scan notices a bot that is down without exiting.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from telegram import Bot, Message, Update
from telegram.error import BadRequest
from telegram.ext import Application, ApplicationBuilder, ContextTypes, TypeHandler
from telegram.request import BaseRequest

from signalbench.live.bot import BotBrain, Reply
from signalbench.live.messenger import chunks, keyboard

HEARTBEAT_SECONDS = 300.0  # at least every 10 minutes while polling (spec 05)
log = logging.getLogger(__name__)


async def send_replies(bot: Bot, chat_id: int, replies: list[Reply]) -> None:
    for reply in replies:
        if reply.edit is None:
            pieces = chunks(reply.text)
            for index, piece in enumerate(pieces):
                last = index == len(pieces) - 1
                markup = keyboard(reply.buttons) if last else None
                await bot.send_message(chat_id, piece, reply_markup=markup)
            continue
        try:
            await bot.edit_message_text(
                chunks(reply.text)[0], chat_id=chat_id, message_id=reply.edit,
                reply_markup=keyboard(reply.buttons),
            )
        except BadRequest as error:  # e.g. "message is not modified": the reply still stands
            log.warning("could not edit message %s: %s", reply.edit, error)


async def handle_update(update: Update, brain: BotBrain, bot: Bot) -> None:
    """A command, a reply, or a button press from the owner's chat. Anything else is logged
    by the brain and gets no answer at all."""
    chat = update.effective_chat
    if chat is None or not brain.allowed(chat.id):
        return
    query = update.callback_query
    if query is not None:
        await query.answer()
        message = query.message if isinstance(query.message, Message) else None
        replies = brain.handle_callback(
            chat.id, query.data or "", 0 if message is None else message.message_id,
            "" if message is None or message.text is None else message.text,
        )
    elif update.message is not None and update.message.text:
        replies = brain.handle_text(chat.id, update.message.text)
    else:
        return
    await send_replies(bot, chat.id, replies)


async def beat_once(bot: Bot, write: Callable[[], None]) -> bool:
    """Telegram answers (getMe), then the heartbeat row is written. False when either failed."""
    try:
        await bot.get_me()
        write()
    except Exception:  # logged; the next beat tries again
        log.exception("heartbeat failed")
        return False
    return True


async def beat_forever(bot: Bot, write: Callable[[], None], every: float) -> None:
    while True:
        await beat_once(bot, write)
        await asyncio.sleep(every)


def build_application(
    token: str,
    brain: BotBrain,
    write_heartbeat: Callable[[], None],
    *,
    request: BaseRequest | None = None,
    every: float = HEARTBEAT_SECONDS,
) -> Application[Any, Any, Any, Any, Any, Any]:
    """The Application `bot run` polls with. `request` replaces the network (tests)."""
    tasks: list[asyncio.Task[None]] = []

    async def start(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
        tasks.append(asyncio.create_task(beat_forever(app.bot, write_heartbeat, every)))

    async def stop(_app: Application[Any, Any, Any, Any, Any, Any]) -> None:
        for task in tasks:
            task.cancel()

    builder = ApplicationBuilder().token(token).post_init(start).post_shutdown(stop)
    if request is not None:
        builder = builder.request(request).get_updates_request(request)
    app = builder.build()

    async def on_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await handle_update(update, brain, context.bot)

    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        log.error("update %s failed", update, exc_info=context.error)

    app.add_handler(TypeHandler(Update, on_update))
    app.add_error_handler(on_error)
    return app
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_live_telegram_bot.py -q && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 5 passed; the full suite 782 passed; ruff `All checks passed!`; mypy `Success: no issues found in 85 source files`.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/live/telegram_bot.py tests/test_live_telegram_bot.py
git commit -m "feat: the bot process on python-telegram-bot, with the heartbeat

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: The CLI: `scan`, `scan status`, and `bot run`

**Files:**
- Modify: `src/signalbench/cli.py`
- Create: `tests/test_scan_cli.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scan_cli.py`:

```python
"""`signalbench scan`, `scan status`, and `bot run` wiring (spec 05), on the Breakout world:
no real database, Telegram, or network."""

import logging
from contextlib import nullcontext
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session, select
from typer.testing import CliRunner

from paper_helpers import evening
from scan_helpers import DAYS, UNIVERSE, B, World, make_world, savepoint_engine
from signalbench import cli
from signalbench.cli import app
from signalbench.db.models import ScanRun, TradeSignal
from signalbench.live.heartbeat import write_heartbeat
from signalbench.live.messenger import FakeMessenger
from strategy_helpers import WeekdaySessions

runner = CliRunner()


class ClosableFake(FakeMessenger):
    closed = False

    def close(self) -> None:
        self.closed = True


def _wire(monkeypatch: pytest.MonkeyPatch, world: World, now: datetime) -> ClosableFake:
    messenger = ClosableFake()
    monkeypatch.setattr(cli, "get_session", lambda: world.session)
    monkeypatch.setattr(cli, "advisory_lock", lambda _engine, _key: nullcontext(True))
    monkeypatch.setattr(cli, "REPO_ROOT", world.repo)
    monkeypatch.setattr(cli, "load_universe", lambda _path: list(UNIVERSE))
    monkeypatch.setattr(cli, "NyseSessions", WeekdaySessions)
    monkeypatch.setattr(cli, "CboeCanadaSessions", WeekdaySessions)
    monkeypatch.setattr(cli, "_now", lambda: now)
    monkeypatch.setattr(cli, "_ingest_prices", lambda _session: [])
    monkeypatch.setattr(cli, "_ingest_paper_earnings", lambda _session, _since: [])
    monkeypatch.setattr(cli, "fetch_yfinance_splits", lambda _symbol, _since: [])
    monkeypatch.setattr(cli, "_next_scan_run", lambda: "2026-10-06 15:00:00 (local time)")
    monkeypatch.setattr(cli, "TelegramMessenger", lambda _token, _chat: messenger)
    monkeypatch.setattr(cli.settings, "telegram_bot_token", "123456:TEST-TOKEN")
    monkeypatch.setattr(cli.settings, "telegram_chat_id", 4242)
    return messenger


@pytest.fixture
def world(session: Session, tmp_path: Path) -> World:
    world = make_world(session, tmp_path)
    write_heartbeat(session, evening(DAYS[B]))
    return world


def test_help_lists_scan_status_and_bot_run() -> None:
    assert "status" in runner.invoke(app, ["scan", "--help"]).stdout
    assert "--dry-run" in runner.invoke(app, ["scan", "--help"]).stdout
    assert "run" in runner.invoke(app, ["bot", "--help"]).stdout


def test_scan_sends_through_the_messenger_and_prints_one_line(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    messenger = _wire(monkeypatch, world, evening(DAYS[B]))
    result = runner.invoke(app, ["scan"])
    assert result.exit_code == 0, result.stderr
    assert result.stdout.splitlines()[-1] == "scan 1: ok for 2026-10-05 (sent 2, signals 1)"
    assert [m.text.split()[0] for m in messenger.sent] == ["🟢", "📊"]
    assert messenger.closed
    again = runner.invoke(app, ["scan"])
    assert again.stdout.splitlines()[-1] == "scan 2: nothing for 2026-10-05 (none)"


def test_a_failed_scan_exits_1_after_its_error_line(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    messenger = _wire(monkeypatch, world, evening(DAYS[B]))
    messenger.fail = True
    result = runner.invoke(app, ["scan"])
    assert result.exit_code == 1
    assert result.stdout.splitlines()[-1].startswith("ERROR: Scan failed at step 9 (send):")


def test_scan_refuses_without_telegram_settings_and_as_of_without_dry_run(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, world, evening(DAYS[B]))
    monkeypatch.setattr(cli.settings, "telegram_chat_id", None)
    result = runner.invoke(app, ["scan"])
    assert result.exit_code == 1
    assert result.stderr == (
        "ERROR: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set (the main checkout's .env)\n"
    )
    assert world.session.exec(select(ScanRun)).all() == []
    result = runner.invoke(app, ["scan", "--as-of", "2026-10-05"])
    assert (result.exit_code, result.stderr) == (2, "--as-of needs --dry-run\n")


def test_a_dry_run_prints_the_messages_and_leaves_the_database_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engine = savepoint_engine()
    with Session(engine) as seed:
        world = make_world(seed, tmp_path)
        _wire(monkeypatch, world, evening(DAYS[B + 3]))
    monkeypatch.setattr(cli, "engine", engine)
    monkeypatch.setattr(cli, "TelegramMessenger", None)  # a dry run never builds one
    result = runner.invoke(app, ["scan", "--dry-run", "--as-of", "2026-10-05"])
    assert result.exit_code == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[lines.index("--- message 1 ---") + 1] == "🟢 BUY NVDA (CDR ZNVD) — Breakout · NVDA"
    assert lines[-2:] == [
        "scan 1: ok for 2026-10-05 (sent 2, signals 1)", "(dry run: nothing was written or sent)"
    ]
    with Session(engine) as after:
        assert (after.exec(select(ScanRun)).all(), after.exec(select(TradeSignal)).all()) == (
            [], []
        )


def test_scan_status_and_the_stale_exit(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, world, evening(DAYS[B]))
    runner.invoke(app, ["scan"])
    result = runner.invoke(app, ["scan", "status", "--stale-after-days", "3"])
    assert result.exit_code == 0, result.stderr
    assert result.stdout.splitlines()[0] == (
        "Last scan: 2026-10-05 ok, started 2026-10-05 18:00 New York"
    )
    assert result.stdout.splitlines()[1] == "Next scheduled scan: 2026-10-06 15:00:00 (local time)"
    monkeypatch.setattr(cli, "_now", lambda: evening(DAYS[B]) + timedelta(days=4))
    stale = runner.invoke(app, ["scan", "status", "--stale-after-days", "3"])
    assert (stale.exit_code, stale.stderr) == (
        3, "STALE: the last ok scan was on 2026-10-05 18:00 New York, more than 3 days ago.\n"
    )
    monkeypatch.setattr(cli, "_now", lambda: evening(DAYS[B]) + timedelta(minutes=1))
    always = runner.invoke(app, ["scan", "status", "--stale-after-days", "0"])  # the toast check
    assert always.exit_code == 3


def test_bot_run_polls_with_one_handler_and_keeps_the_token_out_of_the_log(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, world, evening(DAYS[B]))
    polled: list[dict[str, Any]] = []

    class App:
        def run_polling(self, **kwargs: Any) -> None:
            polled.append(kwargs)

    built: list[tuple[str, int]] = []

    def build(token: str, brain: Any, beat: Any) -> App:
        built.append((token, brain.chat_id))
        return App()

    monkeypatch.setattr(cli, "build_application", build)
    result = runner.invoke(app, ["bot", "run"])
    assert result.exit_code == 0, result.stderr
    assert "TEST-TOKEN" not in result.stdout and "4242" not in result.stdout
    assert built == [("123456:TEST-TOKEN", 4242)]
    assert polled == [{"allowed_updates": ["message", "callback_query"]}]
    assert logging.getLogger("httpx").level == logging.WARNING
    monkeypatch.setattr(cli.settings, "telegram_bot_token", None)
    assert runner.invoke(app, ["bot", "run"]).exit_code == 1
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_scan_cli.py -q`
Expected: 7 failed: `test_help_lists_scan_status_and_bot_run` (exit 2: there is no `scan` command yet) and six `AttributeError: <module 'signalbench.cli' …> has no attribute 'CboeCanadaSessions'` from the wiring fixture.

- [ ] **Step 3: Wire the commands**

In `src/signalbench/cli.py`, replace:

```python
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
```

with:

```python
import logging
import subprocess  # schtasks, for the next scheduled scan in /status
import sys
import uuid
from collections.abc import Callable
from contextlib import nullcontext
from datetime import UTC, date, datetime, time
```

In `src/signalbench/cli.py`, replace:

```python
from signalbench.live.book import LedgerError, SplitKind
from signalbench.live.ledger import Ledger
from signalbench.live.start import LIVE_CONFIG, start_live
from signalbench.live.tax import tax_csv, tax_text
from signalbench.market.calendar import HISTORY_START, NyseSessions
```

with:

```python
from signalbench.live.book import LedgerError, SplitKind
from signalbench.live.bot import BotBrain
from signalbench.live.heartbeat import write_heartbeat
from signalbench.live.ledger import Ledger
from signalbench.live.messenger import ConsoleMessenger, TelegramMessenger
from signalbench.live.scan import LIVE_SCAN_LOCK, ScanOutcome, dry_run_session, run_scan
from signalbench.live.start import LIVE_CONFIG, start_live
from signalbench.live.status import scan_stale_message, scan_status_lines
from signalbench.live.tax import tax_csv, tax_text
from signalbench.live.telegram_bot import build_application
from signalbench.market.calendar import HISTORY_START, CboeCanadaSessions, NyseSessions
```

In `src/signalbench/cli.py`, replace:

```python
STALE_EXIT = 3  # `paper status --stale-after-days`: no ok paper run for too long
```

with:

```python
STALE_EXIT = 3  # `paper status` and `scan status --stale-after-days`: no ok run for too long
SCAN_TASK = "SignalBench live scan"  # the Task Scheduler task (scripts/windows/register-tasks.ps1)
```

Append to `src/signalbench/cli.py`:

```python
scan_app = typer.Typer(help="The evening scan of the live strategy (spec 05).")
app.add_typer(scan_app, name="scan")
bot_app = typer.Typer(help="The Telegram bot (spec 05).")
app.add_typer(bot_app, name="bot")


def _telegram() -> tuple[str, int]:
    """The bot token and the owner's chat id from the environment (.env). Never printed."""
    token, chat = settings.telegram_bot_token, settings.telegram_chat_id
    if not token or chat is None:
        typer.echo(
            "ERROR: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set (the main checkout's .env)",
            err=True,
        )
        raise typer.Exit(1)
    return token, chat


def _next_scan_run() -> str | None:
    """The next run time Task Scheduler shows for the scan task, or None (not Windows, or the
    task is not registered)."""
    if sys.platform != "win32":
        return None
    try:
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", SCAN_TASK, "/FO", "LIST"],
            capture_output=True, text=True, check=False, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in result.stdout.splitlines() if result.returncode == 0 else []:
        key, _, value = line.partition(":")
        if key.strip() == "Next Run Time":
            return f"{value.strip()} (local time)"
    return None


def _print_scan(outcome: ScanOutcome) -> None:
    counts = ", ".join(f"{key} {value}" for key, value in sorted(outcome.counts.items()))
    typer.echo(f"scan {outcome.run_id}: {outcome.status} for {outcome.as_of} ({counts or 'none'})")


@scan_app.callback(invoke_without_command=True)
def scan(
    ctx: typer.Context,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print every message; write and send nothing.")
    ] = False,
    as_of: Annotated[
        datetime | None,
        typer.Option("--as-of", formats=["%Y-%m-%d"], help="With --dry-run: the session to scan."),
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Rescan a session that was already scanned.")
    ] = False,
) -> None:
    """The evening scan: ingest, catch up missed sessions, decide, size, and send (spec 05)."""
    if ctx.invoked_subcommand is not None:
        return
    if as_of is not None and not dry_run:
        typer.echo("--as-of needs --dry-run", err=True)
        raise typer.Exit(2)
    if dry_run:
        _dry_run(as_of)
        return
    token, chat = _telegram()
    messenger = TelegramMessenger(token, chat)
    try:
        with get_session() as session:
            outcome = run_scan(
                session, lock=advisory_lock(engine, LIVE_SCAN_LOCK), repo=REPO_ROOT,
                universe=load_universe(UNIVERSE_PATH), nyse=NyseSessions(),
                cboe=CboeCanadaSessions(), clock=_now, ingest=_ingest_prices,
                ingest_earnings=_ingest_paper_earnings, splits=fetch_yfinance_splits,
                messenger=messenger, echo=typer.echo, force=force,
            )
    except Exception as error:  # noqa: BLE001  # e.g. the database is down: no run row to mark
        typer.echo(f"ERROR: {type(error).__name__}: {_first_line(error)}", err=True)
        raise typer.Exit(1) from None
    finally:
        messenger.close()
    if outcome.status == "failed":
        raise typer.Exit(1)  # run_scan printed the ERROR line
    if outcome.status != "locked":
        _print_scan(outcome)


def _dry_run(as_of: datetime | None) -> None:
    """The scan as it would run at 18:00 New York on the session, inside a transaction that is
    rolled back: stored prices, calendar, and splits only (nothing is fetched), every message
    printed, nothing written or sent."""
    nyse = NyseSessions()
    day = last_complete_session(nyse, _now()) if as_of is None else as_of.date()
    try:
        with dry_run_session(engine) as session:
            outcome = run_scan(
                session, lock=nullcontext(True), repo=REPO_ROOT,
                universe=load_universe(UNIVERSE_PATH), nyse=nyse, cboe=CboeCanadaSessions(),
                clock=lambda: datetime.combine(day, time(18, 0), tzinfo=NEW_YORK),
                ingest=lambda _session: [], ingest_earnings=lambda _session, _since: [],
                splits=lambda _symbol, _since: [], messenger=ConsoleMessenger(typer.echo),
                echo=typer.echo, as_of=day, force=True,
            )
    except Exception as error:  # noqa: BLE001  # one line, not a traceback
        typer.echo(f"ERROR: {type(error).__name__}: {_first_line(error)}", err=True)
        raise typer.Exit(1) from None
    _print_scan(outcome)
    typer.echo("(dry run: nothing was written or sent)")
    if outcome.status == "failed":
        raise typer.Exit(1)


@scan_app.command("status")
def scan_status(
    stale_after_days: Annotated[
        int | None,
        typer.Option(
            "--stale-after-days", min=0,
            help="Exit 3 when no scan has succeeded for more than this many days (0: always, "
            "to check the script's toast).",
        ),
    ] = None,
) -> None:
    """The last scan, the next one, the live config and code, and the pause state."""
    try:
        with get_session() as session:
            lines = scan_status_lines(session, _next_scan_run)
            stale = (
                None if stale_after_days is None
                else scan_stale_message(session, _now(), stale_after_days)
            )
    except Exception as error:  # noqa: BLE001  # one line for the scan log, not a traceback
        typer.echo(f"ERROR: {type(error).__name__}: {_first_line(error)}", err=True)
        raise typer.Exit(1) from None
    for line in lines:
        typer.echo(line)
    if stale is not None:
        typer.echo(stale, err=True)
        raise typer.Exit(STALE_EXIT)


@bot_app.command("run")
def bot_run() -> None:
    """Poll Telegram for the owner's commands and button presses until stopped."""
    token, chat = _telegram()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its request lines show the token

    def beat() -> None:
        with get_session() as session:
            write_heartbeat(session, datetime.now(UTC))

    brain = BotBrain(chat_id=chat, sessions=get_session, calendar=NyseSessions(), clock=_now,
                     next_run=_next_scan_run)
    typer.echo("bot: polling Telegram; only TELEGRAM_CHAT_ID is answered")
    build_application(token, brain, beat).run_polling(allowed_updates=["message", "callback_query"])
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_scan_cli.py tests/test_live_cli.py tests/test_paper_cli.py tests/test_cli.py -q && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 55 passed (7 new, and the 48 existing CLI, live CLI, and paper CLI tests); the full suite 789 passed; ruff `All checks passed!`; mypy `Success: no issues found in 85 source files`.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/cli.py tests/test_scan_cli.py
git commit -m "feat: signalbench scan, scan status, and bot run

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: The scripts: `live_scan.ps1`, `live_bot.ps1`, and `register-tasks.ps1`

**Files:**
- Create: `scripts/lib/SignalBench.ps1`, `scripts/live_scan.ps1`, `scripts/live_bot.ps1`, `scripts/windows/register-tasks.ps1`
- Replace: `scripts/paper_nightly.ps1` (the shared code moves to `scripts/lib/SignalBench.ps1`)

No test file: the scripts are checked by parsing them with Windows PowerShell 5.1 and by running them with `-NoToast` against a database address that refuses (`127.0.0.1:1`) and an empty env file, so they reach neither the real database nor Telegram. `logs/` is git-ignored.

- [ ] **Step 1: Write the shared file**

Create `scripts/lib/SignalBench.ps1`:

```powershell
<#
.SYNOPSIS
    Shared by the scheduled SignalBench scripts (paper_nightly.ps1, live_scan.ps1, live_bot.ps1):
    the log, the Windows toast, the .env loading, and `uv run --frozen signalbench`.

.DESCRIPTION
    Dot-source it from a script: . (Join-Path $PSScriptRoot 'lib\SignalBench.ps1')
    The script sets $script:log to a fallback log file first, and may have a -NoToast switch.

    The scheduled tasks run a git worktree checked out at a tag, so the code that runs changes
    only by a deliberate checkout. `.env` is not tracked, so the worktree has none: the
    variables in -EnvFile (the main checkout's `.env` by default) are loaded into the process.
    A variable already set in the environment wins, as it does for pydantic-settings. Values are
    never printed or logged.

    Needs Windows PowerShell 5.1 (powershell.exe): the toast uses the built-in WinRT notification
    API, which PowerShell 7 cannot load. No modules are installed.
#>

function Write-Log([string[]]$Lines) {
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz'
    $text = @($Lines | ForEach-Object { "$stamp  $_" })
    try {
        Add-Content -Path $script:log -Encoding UTF8 -Value $text
    }
    catch {
        $fallback = Join-Path $env:TEMP 'signalbench-scripts.log'
        Add-Content -Path $fallback -Encoding UTF8 -Value ($text + @("$stamp  (could not write $($script:log): $_)"))
    }
}

function Show-Toast([string]$Title, [string]$Message) {
    # Never throws: a toast that cannot be shown is logged, and the script goes on.
    try {
        if ($NoToast) {
            Write-Log @("toast (not shown): $Title | $Message")
            return
        }
        $null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
        $null = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]
        $title = [System.Security.SecurityElement]::Escape($Title)
        $body = [System.Security.SecurityElement]::Escape($Message)
        $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
        $xml.LoadXml("<toast><visual><binding template=`"ToastGeneric`"><text>$title</text><text>$body</text></binding></visual></toast>")
        $toast = New-Object Windows.UI.Notifications.ToastNotification $xml
        # Windows PowerShell's own app id: toasts need a registered app, and this one always is.
        $appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
    }
    catch {
        try { Write-Log @("toast FAILED ($Title | $Message): $_") } catch { }
    }
}

function Get-MainEnvFile([string]$Root) {
    # The shared .git directory of a worktree lives in the main checkout.
    $common = & git -C $Root rev-parse --path-format=absolute --git-common-dir
    if ($LASTEXITCODE -ne 0 -or -not $common) {
        throw "git rev-parse --git-common-dir failed in $Root"
    }
    Join-Path (Split-Path -Parent ([System.IO.Path]::GetFullPath($common.Trim()))) '.env'
}

function Import-EnvFile([string]$Path) {
    # Simple KEY=value lines; blank lines and # comments are skipped; one pair of surrounding
    # quotes is removed. A variable already set in this process is kept. Values are never logged.
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "env file not found: $Path"
    }
    $loaded = 0
    $kept = 0
    foreach ($raw in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $line = $raw.Trim()
        if (-not $line -or $line.StartsWith('#')) { continue }
        if ($line.StartsWith('export ')) { $line = $line.Substring(7).TrimStart() }
        $eq = $line.IndexOf('=')
        if ($eq -lt 1) { continue }
        $key = $line.Substring(0, $eq).Trim()
        $value = $line.Substring($eq + 1).Trim()
        if ($value.Length -ge 2 -and (($value[0] -eq '"' -and $value[-1] -eq '"') -or ($value[0] -eq "'" -and $value[-1] -eq "'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        if ([Environment]::GetEnvironmentVariable($key, 'Process')) {
            $kept++
            continue
        }
        [Environment]::SetEnvironmentVariable($key, $value, 'Process')
        $loaded++
    }
    Write-Log @("env: $loaded variables loaded from $Path, $kept already set in the environment kept")
}

function Start-SignalBenchRun([string]$RepoRoot, [string]$Name, [string]$EnvFile) {
    # Log to <RepoRoot>\logs\<Name>-<yyyy-MM>.log (git-ignored), run from RepoRoot, load the env
    # file, and find uv. Returns the resolved RepoRoot.
    $root = (Resolve-Path -LiteralPath $RepoRoot).Path
    $logDir = Join-Path $root 'logs'
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $script:log = Join-Path $logDir ('{0}-{1:yyyy-MM}.log' -f $Name, (Get-Date))
    Set-Location -LiteralPath $root
    $env:PYTHONUTF8 = '1'
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    if (-not $EnvFile) {
        $EnvFile = Get-MainEnvFile $root
    }
    Write-Log @("repo: $root")
    Import-EnvFile $EnvFile
    $script:uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
    if (-not $script:uv) {
        throw 'uv is not on PATH'
    }
    $root
}

function Invoke-SignalBench([string[]]$Arguments) {
    # Native stderr must not stop the script: take every line, stdout and stderr, as text.
    # --frozen: run the worktree's uv.lock as committed, never rewrite it.
    $ErrorActionPreference = 'Continue'
    $output = @(& $script:uv run --frozen signalbench @Arguments 2>&1 | ForEach-Object { "$_" })
    $code = $LASTEXITCODE
    Write-Log (@("> signalbench $($Arguments -join ' ')  (exit $code)") + $output)
    [pscustomobject]@{ Code = $code; Output = $output }
}

function Get-ErrorLine([string[]]$Output, [int]$Code) {
    # The first "ERROR: ..." line without its prefix, else the last line printed, else the code.
    $line = $Output | Where-Object { $_ -like 'ERROR:*' } | Select-Object -First 1
    if (-not $line) {
        $line = $Output | Where-Object { $_.Trim() } | Select-Object -Last 1
    }
    if (-not $line) {
        $line = "exit code $Code"
    }
    $line -replace '^ERROR:\s*', ''
}
```

- [ ] **Step 2: Move the paper script onto it**

Replace the whole of `scripts/paper_nightly.ps1` with:

```powershell
<#
.SYNOPSIS
    The nightly SignalBench paper run (spec 07), for Task Scheduler.

.DESCRIPTION
    Runs `uv run --frozen signalbench paper run` in -RepoRoot and appends everything to
    <RepoRoot>\logs\paper-<yyyy-MM>.log (git-ignored). Shows a Windows toast when this run fails,
    and another when no paper run has succeeded for more than -StaleAfterDays days (checked
    before this run, so it catches a task that has not been running; it never stops the run).

    The scheduled task runs this script from a dedicated git worktree checked out at a tag
    (plan Task 16), so the code that steps the portfolios changes only by a deliberate
    `git -C <worktree> checkout <new tag>`. The log, toast, and .env loading are shared with
    the live scripts in lib\SignalBench.ps1: the variables in -EnvFile are loaded into this
    process first, a variable already set in the environment wins, and values are never logged.

    Needs Windows PowerShell 5.1 (powershell.exe) for the toast. No modules are installed.

.PARAMETER RepoRoot
    The checkout to run: the paper worktree for the scheduled task. Default: this script's repo.

.PARAMETER EnvFile
    A `.env` file of KEY=value lines. Default: `.env` in the main checkout of -RepoRoot's
    repository (the folder that holds the shared .git directory).

.PARAMETER NoToast
    Write each toast's text to the log instead of showing it (for checking the script).
#>
param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$EnvFile = '',
    [int]$StaleAfterDays = 3,
    [switch]$NoToast
)

$ErrorActionPreference = 'Stop'
$script:log = Join-Path $env:TEMP 'signalbench-paper-nightly.log'  # until RepoRoot\logs exists
. (Join-Path $PSScriptRoot 'lib\SignalBench.ps1')

$exitCode = 1
try {
    $RepoRoot = Start-SignalBenchRun -RepoRoot $RepoRoot -Name 'paper' -EnvFile $EnvFile
    try {
        $status = Invoke-SignalBench @('paper', 'status', '--stale-after-days', "$StaleAfterDays")
        if ($status.Code -eq 3) {
            $stale = $status.Output | Where-Object { $_ -like 'STALE:*' } | Select-Object -First 1
            Show-Toast 'SignalBench paper runs are stale' "$stale"
        }
    }
    catch {
        Write-Log @("stale check error (the run goes on): $_")
    }
    $run = Invoke-SignalBench @('paper', 'run')
    $exitCode = $run.Code
    if ($run.Code -ne 0) {
        Show-Toast 'SignalBench paper run failed' (Get-ErrorLine $run.Output $run.Code)
    }
}
catch {
    $exitCode = 1
    Write-Log @("script error: $_")
    Show-Toast 'SignalBench paper run failed' "$_"
}
exit $exitCode
```

- [ ] **Step 3: Write the scan script**

Create `scripts/live_scan.ps1`:

```powershell
<#
.SYNOPSIS
    The evening SignalBench scan (spec 05), for Task Scheduler.

.DESCRIPTION
    Runs `uv run --frozen signalbench scan` in -RepoRoot (the live-v1 worktree) and appends
    everything to <RepoRoot>\logs\scan-<yyyy-MM>.log (git-ignored).

    - Before the scan, `scan status --stale-after-days` (exit 3) shows a toast when the last ok
      scan is more than -StaleAfterDays days old. The check never stops the scan.
    - A scan that exits non-zero shows a toast with the first line of its error. The scan has
      already sent its warning to Telegram when it could; the toast shows even when Telegram
      is unreachable.
    - A `BOT STALE:` line (the bot's heartbeat is over an hour old) shows a toast too.

    The log, toast, and .env loading are shared with paper_nightly.ps1 (lib\SignalBench.ps1).
    Needs Windows PowerShell 5.1 (powershell.exe) for the toast.

.PARAMETER RepoRoot
    The checkout to run: the live-v1 worktree for the scheduled task. Default: this script's repo.

.PARAMETER EnvFile
    A `.env` file of KEY=value lines. Default: the main checkout's `.env`.

.PARAMETER CheckOnly
    Run the stale check and stop, without scanning (with -StaleAfterDays 0, to see the toast).

.PARAMETER NoToast
    Write each toast's text to the log instead of showing it (for checking the script).
#>
param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$EnvFile = '',
    [int]$StaleAfterDays = 3,
    [switch]$CheckOnly,
    [switch]$NoToast
)

$ErrorActionPreference = 'Stop'
$script:log = Join-Path $env:TEMP 'signalbench-live-scan.log'  # until RepoRoot\logs exists
. (Join-Path $PSScriptRoot 'lib\SignalBench.ps1')

$exitCode = 1
try {
    $RepoRoot = Start-SignalBenchRun -RepoRoot $RepoRoot -Name 'scan' -EnvFile $EnvFile
    try {
        $status = Invoke-SignalBench @('scan', 'status', '--stale-after-days', "$StaleAfterDays")
        if ($status.Code -eq 3) {
            $stale = $status.Output | Where-Object { $_ -like 'STALE:*' } | Select-Object -First 1
            Show-Toast 'SignalBench scans are stale' "$stale"
        }
    }
    catch {
        Write-Log @("stale check error (the scan goes on): $_")
    }
    if ($CheckOnly) {
        exit 0
    }
    $run = Invoke-SignalBench @('scan')
    $exitCode = $run.Code
    $bot = $run.Output | Where-Object { $_ -like 'BOT STALE:*' } | Select-Object -First 1
    if ($bot) {
        Show-Toast 'SignalBench bot is down' ($bot -replace '^BOT STALE:\s*', '')
    }
    if ($run.Code -ne 0) {
        Show-Toast 'SignalBench scan failed' (Get-ErrorLine $run.Output $run.Code)
    }
}
catch {
    $exitCode = 1
    Write-Log @("script error: $_")
    Show-Toast 'SignalBench scan failed' "$_"
}
exit $exitCode
```

- [ ] **Step 4: Write the bot script**

Create `scripts/live_bot.ps1`:

```powershell
<#
.SYNOPSIS
    The SignalBench Telegram bot (spec 05), for Task Scheduler.

.DESCRIPTION
    Runs `uv run --frozen signalbench bot run` in -RepoRoot (the live-v1 worktree) and appends
    its output, line by line as it comes, to <RepoRoot>\logs\bot-<yyyy-MM>.log (git-ignored).
    When the bot exits with an error, a toast shows its last line and the bot is started again
    after -RestartSeconds (Task Scheduler's restart-on-failure is the second line of defence).
    A clean exit (0) ends the script.

    The log, toast, and .env loading are shared with paper_nightly.ps1 (lib\SignalBench.ps1).
    Needs Windows PowerShell 5.1 (powershell.exe) for the toast.

.PARAMETER RepoRoot
    The checkout to run: the live-v1 worktree for the scheduled task. Default: this script's repo.

.PARAMETER EnvFile
    A `.env` file of KEY=value lines. Default: the main checkout's `.env`.

.PARAMETER Once
    Do not restart after an error (for checking the script).

.PARAMETER NoToast
    Write each toast's text to the log instead of showing it (for checking the script).
#>
param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$EnvFile = '',
    [int]$RestartSeconds = 60,
    [switch]$Once,
    [switch]$NoToast
)

$ErrorActionPreference = 'Stop'
$script:log = Join-Path $env:TEMP 'signalbench-live-bot.log'  # until RepoRoot\logs exists
. (Join-Path $PSScriptRoot 'lib\SignalBench.ps1')

$exitCode = 1
try {
    $RepoRoot = Start-SignalBenchRun -RepoRoot $RepoRoot -Name 'bot' -EnvFile $EnvFile
    while ($true) {
        Write-Log @('> signalbench bot run')
        $last = ''
        $ErrorActionPreference = 'Continue'  # native stderr is log output, not a script error
        & $script:uv run --frozen signalbench bot run 2>&1 | ForEach-Object {
            $line = "$_"
            if ($line.Trim()) { $last = $line }
            Write-Log @($line)
        }
        $code = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'
        Write-Log @("bot exited (exit $code)")
        $exitCode = $code
        if ($code -eq 0) { break }
        $why = if ($last) { $last -replace '^ERROR:\s*', '' } else { "exit code $code" }
        Show-Toast 'SignalBench bot stopped' $why
        if ($Once) { break }
        Start-Sleep -Seconds $RestartSeconds
    }
}
catch {
    $exitCode = 1
    Write-Log @("script error: $_")
    Show-Toast 'SignalBench bot stopped' "$_"
}
exit $exitCode
```

- [ ] **Step 5: Write the registration script**

Create `scripts/windows/register-tasks.ps1`:

```powershell
<#
.SYNOPSIS
    The two Task Scheduler tasks of the live strategy (spec 05): prints the exact commands, and
    runs them only with -Register (the owner's go-ahead: a lasting change on this PC).

.DESCRIPTION
    - "SignalBench live bot": scripts\live_bot.ps1 at log-on, restarted every minute on
      failure, with no time limit.
    - "SignalBench live scan": scripts\live_scan.ps1 daily at the scan time, plus at log-on
      with a 5-minute delay for catch-up.

    The scan time is the earliest local time of day that is 17:00 New York or later on every
    day of the 12 months from -From, taken from Windows' time-zone data. On a PC in British
    Columbia (which stops changing clocks in November 2026) that is 15:00: 18:00 New York in
    summer, 17:00 in winter. A scan before 16:15 New York would target the previous session and
    lose that night's entries. -At overrides it. Both tasks run the worktree pinned to the
    live-v1 tag, as the owner, only while logged on (the toasts need the desktop).

.PARAMETER Worktree
    The live-v1 worktree the tasks run.

.PARAMETER EnvFile
    The main checkout's .env (the worktree has none).

.PARAMETER From
    The first day of the 12 months the scan time must cover. Default: today.

.PARAMETER At
    The scan time, HH:mm local, instead of the computed one.

.PARAMETER Register
    Run the printed commands. Without it, nothing is registered.
#>
param(
    [string]$Worktree = 'C:\Users\samin\Documents\GitHub\macrocite-live',
    [string]$EnvFile = 'C:\Users\samin\Documents\GitHub\macrocite\.env',
    [datetime]$From = (Get-Date),
    [string]$At = '',
    [switch]$Register
)

$ErrorActionPreference = 'Stop'

function Get-ScanTime([datetime]$Start) {
    # The latest local time of day at which 17:00 New York falls, over 366 days, rounded up
    # to the minute. (Assumes 17:00 New York is the same local day, true west of New York.)
    $newYork = [TimeZoneInfo]::FindSystemTimeZoneById('Eastern Standard Time')
    $latest = [TimeSpan]::Zero
    for ($i = 0; $i -lt 366; $i++) {
        $five = [datetime]::SpecifyKind($Start.Date.AddDays($i).AddHours(17), 'Unspecified')
        $utc = [TimeZoneInfo]::ConvertTimeToUtc($five, $newYork)
        $local = [TimeZoneInfo]::ConvertTimeFromUtc($utc, [TimeZoneInfo]::Local)
        if ($local.TimeOfDay -gt $latest) { $latest = $local.TimeOfDay }
    }
    [TimeSpan]::FromMinutes([math]::Ceiling($latest.TotalMinutes))
}

$time = if ($At) { [TimeSpan]::Parse($At) } else { Get-ScanTime $From }
$clock = '{0:hh\:mm}' -f $time
$user = "$env:USERDOMAIN\$env:USERNAME"
$common = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File'
$scanArgs = "$common `"$Worktree\scripts\live_scan.ps1`" -RepoRoot `"$Worktree`" -EnvFile `"$EnvFile`""
$botArgs = "$common `"$Worktree\scripts\live_bot.ps1`" -RepoRoot `"$Worktree`" -EnvFile `"$EnvFile`""

$commands = @"
`$scanAction = New-ScheduledTaskAction -Execute 'powershell.exe' -WorkingDirectory '$Worktree' -Argument '$scanArgs'
`$scanDaily = New-ScheduledTaskTrigger -Daily -At '$clock'
`$scanLogOn = New-ScheduledTaskTrigger -AtLogOn -User '$user'
`$scanLogOn.Delay = 'PT5M'
`$scanSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName 'SignalBench live scan' -Action `$scanAction -Trigger @(`$scanDaily, `$scanLogOn) -Settings `$scanSettings -Description 'Evening scan of v2-none-cash (spec 05): the live-v1 worktree, scripts\live_scan.ps1'
`$botAction = New-ScheduledTaskAction -Execute 'powershell.exe' -WorkingDirectory '$Worktree' -Argument '$botArgs'
`$botLogOn = New-ScheduledTaskTrigger -AtLogOn -User '$user'
`$botSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName 'SignalBench live bot' -Action `$botAction -Trigger `$botLogOn -Settings `$botSettings -Description 'Telegram bot (spec 05): the live-v1 worktree, scripts\live_bot.ps1'
"@

Write-Output "Scan time: $clock local, the earliest time that is 17:00 New York or later every day from $('{0:yyyy-MM-dd}' -f $From) for 12 months ($([TimeZoneInfo]::Local.Id))."
Write-Output 'Commands:'
Write-Output $commands
if (-not $Register) {
    Write-Output 'Nothing registered. Rerun with -Register to run the commands above.'
    exit 0
}
Invoke-Expression $commands
Write-Output 'Registered. Check: Get-ScheduledTask -TaskName "SignalBench live *" | Get-ScheduledTaskInfo'
```

- [ ] **Step 6: Parse every script in Windows PowerShell 5.1**

Run: `for f in scripts/paper_nightly.ps1 scripts/live_scan.ps1 scripts/live_bot.ps1 scripts/lib/SignalBench.ps1 scripts/windows/register-tasks.ps1; do powershell.exe -NoProfile -Command "\$e = \$null; [void][System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path '$f'), [ref]\$null, [ref]\$e); '$f parse errors: ' + \$e.Count"; done`
Expected: five lines, each ending `parse errors: 0`.

- [ ] **Step 7: Run the three scripts offline, without a toast**

The empty env file stands in for `.env`, and `DATABASE_URL` points at a port that refuses, so no command reaches a database or Telegram. Each log line is stamped with the local time; the `sed` drops the stamps and the log's byte-order mark.

Run: `rm -rf logs; : > empty.env; DATABASE_URL='postgresql+psycopg://nobody:nothing@127.0.0.1:1/none?connect_timeout=2' powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/live_scan.ps1 -EnvFile empty.env -NoToast; echo "exit $?"; sed -e 's/^\xEF\xBB\xBF//' -e 's/^[^ ]* [^ ]* [^ ]*  //' logs/scan-*.log`
Expected: `exit 1`, then (the first line shows this checkout's path):

```text
repo: <the repo root>
env: 0 variables loaded from empty.env, 0 already set in the environment kept
> signalbench scan status --stale-after-days 3  (exit 1)
ERROR: OperationalError: (psycopg.errors.ConnectionTimeout) connection timeout expired
> signalbench scan  (exit 1)
ERROR: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set (the main checkout's .env)
toast (not shown): SignalBench scan failed | TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set (the main checkout's .env)
```

Run: `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/live_bot.ps1 -EnvFile empty.env -Once -NoToast; echo "exit $?"; sed -e 's/^\xEF\xBB\xBF//' -e 's/^[^ ]* [^ ]* [^ ]*  //' logs/bot-*.log`
Expected: `exit 1` (`-Once`: no restart), then:

```text
repo: <the repo root>
env: 0 variables loaded from empty.env, 0 already set in the environment kept
> signalbench bot run
ERROR: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set (the main checkout's .env)
bot exited (exit 1)
toast (not shown): SignalBench bot stopped | TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set (the main checkout's .env)
```

Run: `DATABASE_URL='postgresql+psycopg://nobody:nothing@127.0.0.1:1/none?connect_timeout=2' powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/paper_nightly.ps1 -EnvFile empty.env -NoToast; echo "exit $?"; sed -e 's/^\xEF\xBB\xBF//' -e 's/^[^ ]* [^ ]* [^ ]*  //' logs/paper-*.log; rm -rf logs empty.env`
Expected: `exit 1`, and the same lines as before the move to the shared file:

```text
repo: <the repo root>
env: 0 variables loaded from empty.env, 0 already set in the environment kept
> signalbench paper status --stale-after-days 3  (exit 1)
ERROR: OperationalError: (psycopg.errors.ConnectionTimeout) connection timeout expired
> signalbench paper run  (exit 1)
ERROR: OperationalError: (psycopg.errors.ConnectionTimeout) connection timeout expired
toast (not shown): SignalBench paper run failed | OperationalError: (psycopg.errors.ConnectionTimeout) connection timeout expired
```

- [ ] **Step 8: Print the registration commands (nothing is registered)**

Run: `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/windows/register-tasks.ps1 -From 2026-10-01`
Expected: on this PC (`British Columbia Standard Time`), the first line and the last line are

```text
Scan time: 15:00 local, the earliest time that is 17:00 New York or later every day from 2026-10-01 for 12 months (British Columbia Standard Time).
Nothing registered. Rerun with -Register to run the commands above.
```

with the eleven commands between them: `-Daily -At '15:00'`, the log-on trigger with `.Delay = 'PT5M'` for the scan, `-RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)` for the bot, both running `C:\Users\samin\Documents\GitHub\macrocite-live\scripts\…` with `-EnvFile "C:\Users\samin\Documents\GitHub\macrocite\.env"`, and `-User '<DOMAIN>\samin'`. Nothing is registered.

- [ ] **Step 9: Run the checks**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: the full suite 789 passed (no Python changed); ruff `All checks passed!`; mypy `Success: no issues found in 85 source files`.

`git status --short` must not list `logs/` or `empty.env`.

- [ ] **Step 10: Commit**

```bash
git add scripts/lib/SignalBench.ps1 scripts/paper_nightly.ps1 scripts/live_scan.ps1 scripts/live_bot.ps1 scripts/windows/register-tasks.ps1
git commit -m "feat: live scan and bot scripts with toasts, and the Task Scheduler registration

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Docker's restart policy, the spec 05 implementation choices, and the owner's README lines

**Files:**
- Modify: `docker-compose.yml`, `docs/superpowers/specs/2026-09-22-swing-assistant-05-bot-scan-design.md`

- [ ] **Step 1: Restart Postgres with the PC**

In `docker-compose.yml`, replace:

```yaml
  postgres:
    image: postgres:16
```

with:

```yaml
  postgres:
    image: postgres:16
    restart: unless-stopped  # spec 05: back after a reboot, for the scheduled scan and the bot
```

The running container keeps its old policy until Task 16 recreates it.

- [ ] **Step 2: Record the implementation choices in the spec's changelog**

Append to `docs/superpowers/specs/2026-09-22-swing-assistant-05-bot-scan-design.md`:

```markdown
- 2026-09-30: implementation choices (plan [2026-09-30-swing-05-bot-scan.md](../plans/2026-09-30-swing-05-bot-scan.md)):
  - **Tables:** migration `0014_scan_runs` also creates the single-row `bot_heartbeat`. `scan_runs.warnings` is a JSON list and `counts` a JSON object.
  - **Runs:** a run whose target was already scanned still checks step 1 and records an `ok` row with no counts, and sends nothing, so the stale check sees the scheduled task running. Catch-up starts the day after the last `ok` row's `as_of`; with none yet, the target only. A run after the next NYSE session's 09:30 open is a late run: its exits and raises are marked late and the summary says so.
  - **Sending:** every row with a message keeps its Telegram id, and a row without one is sent by the next run: split notices first, then entries, exits, and raises. A failed send fails step 9. An entry whose expiry passed before it could be sent goes out as a "do not place it" line with no buttons.
  - **Sizing:** decide()'s US close and stop are rounded to 4 decimals before sizing and storing, so the two agree. The CDR close is the latest stored close on or before the target. The limit is rounded down to the cent. Fractional units are rounded down to 6 decimals. The spread limit is the survey's default of 0.5%.
  - **Messages:** the header names the CDR ticker (`BUY NVDA (CDR ZNVD)`). A handled message is edited to show what was done, and its buttons are removed. Button data is short (`b:12`; the longest, `k:12:wide_spread`, is 16 bytes), within Telegram's 64.
  - **Dry run:** `scan --dry-run --as-of DATE` runs every step as of 18:00 New York on DATE, inside one database transaction that is rolled back. It fetches nothing, prints every message, and needs no lock.
  - **Bot:** after ✅, the bot waits in memory for one reply, `UNITS PRICE [DATE] [force]`. A restart forgets it, so tap again. [Use suggested] records the suggested units at the signal's CDR close. `/sell ... all` sells every unit held. `/status` reads the next run from Task Scheduler. The heartbeat is written every 5 minutes after a successful `getMe`. The httpx logger is kept at WARNING, because its INFO lines contain the bot token.
  - **Scripts:** the shared PowerShell code moved to `scripts/lib/SignalBench.ps1`, which `paper_nightly.ps1` now uses too. `live_bot.ps1` restarts the bot 60 seconds after an error exit. `live_scan.ps1 -CheckOnly -StaleAfterDays 0` shows the stale toast without scanning. `scripts/windows/register-tasks.ps1` prints the commands and runs them only with `-Register`. Docker's Postgres uses `restart: unless-stopped`.
```

- [ ] **Step 3: Run the checks**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 789 passed; ruff `All checks passed!`; mypy `Success: no issues found in 85 source files`.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml docs/superpowers/specs/2026-09-22-swing-assistant-05-bot-scan-design.md
git commit -m "docs: spec 05 implementation choices

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 5: Give the owner the README lines**

`README.md` carries the owner's uncommitted edits, so do not touch it. Ask the owner to add these rows to the CLI table:

```markdown
| `uv run signalbench scan [--force]` | The evening scan of v2-none-cash (spec 05): ingest, catch up missed sessions, decide, size on the CDR, and send entries, stop raises, exits, and a summary to Telegram. Records each run in `scan_runs`; a scanned session is not scanned again without `--force`. `scripts/live_scan.ps1` runs it from Task Scheduler with a toast on failure |
| `uv run signalbench scan --dry-run [--as-of DATE]` | The same scan on the stored data, printed to the console; nothing is written, fetched, or sent |
| `uv run signalbench scan status [--stale-after-days N]` | The last scan, the next scheduled one, the live config's sha256, the code version, and the pause state; exits 3 when no scan has succeeded for more than N days |
| `uv run signalbench bot run` | The Telegram bot (long polling): the buttons and `/signals`, `/portfolio`, `/pnl`, `/buy`, `/sell`, `/void`, `/deposit`, `/withdraw`, `/resume`, `/status`, `/tax`, `/help`, for `TELEGRAM_CHAT_ID` only. `scripts/live_bot.ps1` runs it at log-on |
```

this line to the layout block, after the `jev/` line:

```text
├── live/           The live ledger (spec 04), the evening scan, and the Telegram bot (spec 05)
```

and these two lines to the `.env` block of "Configure the environment" (from @BotFather; Task 16 shows how to read the chat id):

```dotenv
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_CHAT_ID=your-chat-id
```

---

> ## ⛔ Controller: stop here until the owner says go
>
> Tasks 1–13 are buildable with no real database, no Telegram, and no network beyond `uv add`. Tasks 14–20 change the owner's Postgres, create the `live-v1` tag and worktree, freeze the live config, need the owner's bot token, register two scheduled tasks on the owner's PC, and lead to real money. Report the Task 1–13 results, the **Confirm with the owner** decision, and Task 13's README lines to the owner. Run each ⛔ task only on the owner's go-ahead in chat, in order, and stop at the first surprise. Once `live start` has run, the config never changes: trading a different config is a new owner decision (spec 04).

### Task 14: ⛔ Migrate the real database to head

**Files:** none (database only). Run from the main checkout, `C:\Users\samin\Documents\GitHub\macrocite`, which has `.env`.

- [ ] **Step 1: Database up, migrations pending**

Run: `docker compose up -d && uv run alembic current`
Expected: `0013_live_ledger` if spec 04's Task 14 ran, else `0012_paper_trading` or `0011_jev_readings` (spec 07's migration was never run, because paper trading was dropped first). Anything else: **stop and report**.

- [ ] **Step 2: Migrate**

Run: `uv run alembic upgrade head && uv run alembic current`
Expected: a `Running upgrade` line per pending migration, ending with `Running upgrade 0013_live_ledger -> 0014_scan_runs, Spec 05 evening scan runs and the bot heartbeat`, then `0014_scan_runs (head)`. From `0011`, `0012` also creates the four empty paper tables, which is harmless: nothing writes them.

### Task 15: ⛔ The `live-v1` tag, the worktree, and `live start`

**Files:** none.

From here on, the scan and the bot run in a git worktree checked out at the `live-v1` tag, never in the main checkout, so the code that trades changes only by a deliberate checkout of a new tag. `.env` is not tracked, so the worktree has none: manual commands pass the main checkout's with `uv run --frozen --env-file`, and the scripts load it (`-EnvFile`). Never edit files in the worktree: uncommitted changes to tracked code or data there refuse every scan (step 1).

- [ ] **Step 1: The code and data are committed and clean**

Run: `git status --short -- data src alembic scripts pyproject.toml uv.lock docker-compose.yml`
Expected: no output (`README.md` and the untracked `data/paper_v1.yaml` are the owner's and stay as they are).

- [ ] **Step 2: Tag the commit and create the worktree**

In the main checkout, on this plan's last commit:

```powershell
git tag live-v1
git worktree add C:\Users\samin\Documents\GitHub\macrocite-live live-v1
cd C:\Users\samin\Documents\GitHub\macrocite-live
uv sync --frozen
git status --short
```

Expected: the worktree is created on a detached HEAD at `live-v1`, `uv sync` creates its own `.venv`, and `git status --short` prints nothing. The steps below run in this folder: in Git Bash, `cd /c/Users/samin/Documents/GitHub/macrocite-live`, with the env file's path quoted and in forward slashes (Git Bash drops unquoted backslashes).

- [ ] **Step 3: Freeze the live config**

Run: `uv run --frozen --env-file 'C:/Users/samin/Documents/GitHub/macrocite/.env' signalbench live start`
Expected: `live config: data/strategy_v2-none-cash.yaml | config_sha256 a9579593cc7b | started <today, New York> | code <the first 12 characters of git rev-parse live-v1>`. `a9579593cc7b` is the `config_sha256` in the header of `reports/backtests/2026-09-28-v2-none-cash-breakout-off.md`; any other hash means the config changed: **stop and report**. A second run must refuse with `The live config is already recorded. …` and exit 1.

### Task 16: ⛔ The owner creates the bot; Docker's restart policy

**Files:** none. The token and the chat id are the owner's secrets: they go into `.env` by the owner's hand and are never pasted into a chat with Claude or printed by a command.

- [ ] **Step 1: The owner creates the bot**

1. In Telegram, message @BotFather, send `/newbot`, choose a name (e.g. SignalBench) and a username ending in `bot`, and copy the token it replies with.
2. Send the new bot any message (e.g. `hi`), so the next call has an update to read.
3. In a PowerShell window on the PC, read the chat id (the token is typed at the prompt, not stored or shown):

```powershell
$token = Read-Host 'Bot token'
(Invoke-RestMethod "https://api.telegram.org/bot$token/getUpdates").result | ForEach-Object { $_.message.chat.id } | Select-Object -Unique
Remove-Variable token
```

Expected: one number, the owner's chat id (an empty result means step 2's message has not arrived: send another and repeat).
4. Add two lines to the main checkout's `.env`: `TELEGRAM_BOT_TOKEN=<the token>` and `TELEGRAM_CHAT_ID=<the chat id>`.

- [ ] **Step 2: The settings load (without printing them)**

Run (in the worktree): `uv run --frozen --env-file 'C:/Users/samin/Documents/GitHub/macrocite/.env' python -c "from signalbench.config import settings; print(bool(settings.telegram_bot_token), settings.telegram_chat_id is not None)"`
Expected: `True True`.

- [ ] **Step 3: Postgres restarts with the PC**

Run (in the main checkout): `docker compose up -d && docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' $(docker compose ps -q postgres)`
Expected: the container is recreated with its data volume, then `unless-stopped`.

### Task 17: ⛔ The dry run on real data, for the owner's review

**Files:** none. In the worktree.

- [ ] **Step 1: Stored prices up to the last complete session**

The dry run fetches nothing, so the prices must already be stored: this is the ordinary `ingest prices` of spec 01.

Run: `uv run --frozen --env-file 'C:/Users/samin/Documents/GitHub/macrocite/.env' signalbench ingest prices`
Expected: one line per ticker and a `liquidity:` line, exit 0 (a few failed tickers are listed and do not stop the dry run unless one is QQQ).

- [ ] **Step 2: Print the scan of a recent session**

Pick `<session>`, the last complete NYSE session (for example yesterday's date on a weekday evening, or today's after 16:15 New York). `PYTHONUTF8=1` lets Python print the messages' emoji to a pipe: Git Bash's default is cp1252, which has no 🟢 (the scheduled scripts set it themselves).

Run: `PYTHONUTF8=1 uv run --frozen --env-file 'C:/Users/samin/Documents/GitHub/macrocite/.env' signalbench scan --dry-run --as-of <session>`
Expected: `--- message N ---` blocks: any 🟢 entries, then the 📊 summary, then `scan <id>: ok for <session> (…)` and `(dry run: nothing was written or sent)` (the rolled-back row still used an id from Postgres's sequence). Before `/deposit 100` the ledger holds C$0, so every Breakout that fired is skipped as `no_cash` in the summary's `skipped:` list and no 🟢 message is printed; the summary shows `Positions: none`, `Cash C$0.00`, the QQQ regime line, and a ⚠️ that the bot has never checked in. Show the output to the owner. To see entry messages as they will look, rerun this step after Task 19's `/deposit 100`.

- [ ] **Step 3: Nothing was written**

Run: `uv run --frozen --env-file 'C:/Users/samin/Documents/GitHub/macrocite/.env' signalbench scan status`
Expected: `Last scan: none yet` first (the dry run's rows were rolled back).

### Task 18: ⛔ The bot by hand, then the scheduled tasks on the owner's go-ahead

**Files:** none. Registering the tasks is a lasting change on the owner's PC: show the commands, and run them only on the owner's go-ahead in chat.

- [ ] **Step 1: The bot answers the owner's phone**

In a separate PowerShell window, run the bot script once, in the foreground:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\samin\Documents\GitHub\macrocite-live\scripts\live_bot.ps1 -RepoRoot C:\Users\samin\Documents\GitHub\macrocite-live -EnvFile C:\Users\samin\Documents\GitHub\macrocite\.env -Once
```

The owner sends `/help` and then `/status` from the phone. Expected: the command list, then `Last scan: none yet`, `Next scheduled scan: unknown (the task is not registered)`, `Live config: data/strategy_v2-none-cash.yaml · sha256 a9579593 · code no scan yet`, and `New entries: not paused`. `C:\Users\samin\Documents\GitHub\macrocite-live\logs\bot-<yyyy-MM>.log` shows `bot: polling Telegram; only TELEGRAM_CHAT_ID is answered` and no token. Stop the bot with Ctrl+C.

- [ ] **Step 2: Show the owner the registration commands**

Run (in the worktree, Windows PowerShell): `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\windows\register-tasks.ps1`
Expected: `Scan time: 15:00 local, …(British Columbia Standard Time).`, the commands for the two tasks with this worktree and `C:\Users\samin\Documents\GitHub\macrocite\.env` in them, and `Nothing registered. Rerun with -Register to run the commands above.` If the time is not 15:00, Windows' time-zone data differs from the spec's: ask the owner, and pass `-At HH:mm` for the time the owner chooses.

- [ ] **Step 3: Register, on the owner's go-ahead**

Run the same command with `-Register` (an ordinary, not elevated, PowerShell window for the owner's account; if Windows answers "Access is denied", an elevated window for the same user). Then:

```powershell
Get-ScheduledTask -TaskName 'SignalBench live *' | Get-ScheduledTaskInfo | Select-Object TaskName, NextRunTime, LastTaskResult
Start-ScheduledTask -TaskName 'SignalBench live bot'
```

Expected: both tasks, the scan's `NextRunTime` at the next 15:00 local, `LastTaskResult` 267011 (never run); then the bot runs in the background, and `/status` from the phone shows the next scheduled scan. To remove them later: `Unregister-ScheduledTask -TaskName 'SignalBench live scan' -Confirm:$false` (and the same for the bot).

- [ ] **Step 4: The toasts, checked by hand once**

A forced failure: in a PowerShell window (the variable lasts only for this window and wins over `.env`; the owner's `.env` is not touched):

```powershell
$env:DATABASE_URL = 'postgresql+psycopg://nobody:nothing@127.0.0.1:1/none?connect_timeout=2'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\samin\Documents\GitHub\macrocite-live\scripts\live_scan.ps1 -RepoRoot C:\Users\samin\Documents\GitHub\macrocite-live -EnvFile C:\Users\samin\Documents\GitHub\macrocite\.env
"exit $LASTEXITCODE"
Remove-Item Env:DATABASE_URL
```

Expected: a toast from "Windows PowerShell" titled **SignalBench scan failed** with the `OperationalError` line, and `exit 1`. Nothing reaches the real database or Telegram.

The stale toast, without scanning: `powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\samin\Documents\GitHub\macrocite-live\scripts\live_scan.ps1 -RepoRoot C:\Users\samin\Documents\GitHub\macrocite-live -EnvFile C:\Users\samin\Documents\GitHub\macrocite\.env -CheckOnly -StaleAfterDays 0`
Expected: a toast titled **SignalBench scans are stale** (`STALE: the last ok scan was never, more than 0 days ago.`); `scan status` only reads. If no toast appears, check Settings > System > Notifications (Windows PowerShell on, Do not disturb off).

- [ ] **Updating the code later: a deliberate checkout of a new tag**

A fix is committed and tested in the main checkout (a new migration is applied there first, Task 14's way), tagged, and checked out in the worktree between two evening scans: `git tag live-v2`, `git -C C:\Users\samin\Documents\GitHub\macrocite-live checkout live-v2`, `uv sync --frozen --directory C:\Users\samin\Documents\GitHub\macrocite-live`, then restart the bot task (`Stop-ScheduledTask` and `Start-ScheduledTask -TaskName 'SignalBench live bot'`). The config must not change in the new tag: the scan refuses a config whose sha256 differs from `live_config`'s.

### Task 19: ⛔ The first scheduled scan, `/portfolio` from the phone, and `/deposit 100`

**Files:** `docs/superpowers/specs/2026-09-22-swing-assistant-05-bot-scan-design.md`, `docs/research-log.md` (one line each, at the end).

- [ ] **Step 1: The first scheduled scan delivers**

After the first 15:00 run: the owner receives the 📊 summary on the phone. In the worktree, `logs\scan-<yyyy-MM>.log` ends with `scan <id>: ok for <session> (…)`, and `uv run --frozen --env-file 'C:/Users/samin/Documents/GitHub/macrocite/.env' signalbench scan status` starts with `Last scan: <session> ok`. A failure shows its toast and a ⚠️ message: fix the cause (Task 18's update steps for a code fix) and let the next run retry.

- [ ] **Step 2: `/portfolio` and `/deposit 100` from the phone**

The owner sends `/portfolio` (expected: `No open positions.` and `Cash C$0.00`), then `/deposit 100`. Expected: `Deposit of C$100.00 recorded on <today>. Cash is now C$100.00.` Then `/status` and `/pnl` answer. The next scan sizes entries against C$100.

- [ ] **Step 3: The start lines**

Append one line to the spec 05 changelog: `- <date>: started. Migrations to 0014 applied; live-v1 tagged (<sha>) and its worktree created; live start froze data/strategy_v2-none-cash.yaml (a9579593cc7b); Task Scheduler "SignalBench live scan" daily at <time> local and at log-on (5-minute delay), "SignalBench live bot" at log-on; the forced failure and the stale check showed their toasts; the first scheduled scan on <date> delivered its summary; /deposit 100 recorded on <date>.` Add a matching entry to `docs/research-log.md` in its style, marking the start of live trading of v2-none-cash with C$100 (not investment advice: the owner places every order by hand).

```bash
git add docs/superpowers/specs/2026-09-22-swing-assistant-05-bot-scan-design.md docs/research-log.md
git commit -m "docs: spec 05 started, live scan and bot running

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Ask before pushing the branch or opening a PR (`feat/swing-03-jev-reader`, `feat/swing-06-breakout-v2`, and `feat/swing-07-paper-trading` are not merged; this branch sits on them).

### Task 20: Go live (the owner)

**Files:** none. Nothing here is done by Claude.

- [ ] The owner places the first real trade on Wealthsimple from a 🟢 message, within its limit and skip rules, then taps ✅ and replies with the units and the price (or taps ⏭ with a reason). From then on the evening scan manages the position: ⬆️ stop raises, and a 🔴 exit to sell at the next open, answered with ✅ Sold or Ignore.

---

## Spec coverage check

| Spec 05 item | Where |
| --- | --- |
| Target `last_complete_session`; catch-up of every session after the last successful scan | Task 8 (`run_scan`), Task 9 (`test_catch_up_after_two_missed_evenings`) |
| `scan_runs` columns and migration `0014` (with `bot_heartbeat`); a `running` row over two hours old is abandoned | Tasks 1, 8 |
| Step 1: connectivity, the `live_config` row, the config's sha256, a clean tree | Task 8 (`check`, spec 04's `verify_live_config`, `code_version`) |
| Step 2: `ingest prices` with spec 07's fix and the liquidity flags; bars for QQQ and every held or pending symbol | Tasks 8, 11 (`_ingest_prices`) |
| Step 3: the earnings calendar from the last successful scan's `as_of`, not critical | Tasks 8, 11 (`_ingest_paper_earnings`) |
| Step 4: split detection for positions (CDR and US) and `sent` signals, `split` rows, the scale check holding a symbol with ⚠️ | Task 8 (`splits`), Task 7 (the ℹ️ notices) |
| Step 5: catch-up for exits and raises only, sent late, raises recorded for later sessions; stale entries dropped | Tasks 8, 9 |
| Step 6: equity snapshot, pause, `decide()` on the ledger's `PortfolioState`, Breakout only, `NullReadingsView` | Task 8 (spec 04's `review_session`) |
| Steps 7–8: live CDR sizing (whole unit, ceil with the 2.5% bound, fractional) and the template Why | Tasks 3, 4, 8 |
| Entry session on the Cboe Canada calendar (XTSE), the expiry moved by a Cboe holiday | Tasks 2, 8 |
| Step 9: write the rows, then send; a row without a message id is sent by the next run | Tasks 7, 8 |
| Step 10: expire old signals; the scale-up check when due | Task 8 |
| Step 11: the evening summary with every listed part | Tasks 7, 8 |
| A critical failure: ⚠️ message, failed row, toast; Telegram down still toasts | Tasks 8, 11, 12 |
| Idempotency (`--force`, unique rows, a scanned target sends nothing) and the advisory lock | Task 8 (`LIVE_SCAN_LOCK` with spec 07's `advisory_lock`), Task 11 |
| `--dry-run --as-of` prints every message and writes and sends nothing | Tasks 8, 11 |
| Messages: entry, stop raised (both currencies, late), split notices, exit (stop, earnings, late), summary, pause review | Tasks 6, 7 |
| Buttons: ✅ with a reply or [Use suggested], ⏭ with the five reasons, ✅ Sold with units and price, Ignore; "already logged" | Task 9 |
| Commands: `/signals`, `/portfolio`, `/pnl`, `/buy`, `/sell`, `/void`, `/deposit`, `/withdraw`, `/resume`, `/status`, `/tax`, `/help`; US symbols resolved to their CDR | Task 9 |
| python-telegram-bot, long polling, the allowlist, the `Messenger` protocol with `TelegramMessenger` and `FakeMessenger` | Tasks 5, 10 |
| The bot heartbeat, and the scan's warning and toast when it is over an hour old | Tasks 8, 10, 12 |
| Scripts: `live_scan.ps1`, `live_bot.ps1` sharing `paper_nightly.ps1`'s `.env` loading and toasts; the stale check | Task 12 |
| Task Scheduler: the bot at log-on with restarts and no time limit; the scan daily at the converted time and at log-on with a delay; registered only on the owner's go-ahead | Tasks 12, 18 |
| Docker's `restart: unless-stopped` | Tasks 13, 16 |
| Owner setup: @BotFather, the chat id from `getUpdates`, the two `.env` lines, the task commands reviewed | Tasks 16, 18 |
| Testing: the end-to-end fixture test (fire, fill, raise, stop hit, sale) and catch-up, with `FakeMessenger` and no network | Task 9 (`tests/test_live_e2e.py`) |
| Testing: failures, calendar failure, refusals, splits, Why, one open exit alert, idempotency and resending, the lock, the whole-unit rule, the Cboe holiday, the allowlist, every command's errors, `/status` | Tasks 2–11 |
| Testing: the scripts checked by hand once (a forced failure, a stale check) | Task 12 (offline), Task 18 (the toasts) |
| Gate: CI green and the end-to-end test; the dry run reviewed; migrations, tag, worktree, `live start`; tasks registered; the first scheduled scan; `/portfolio`; `/deposit 100`; the first trade | Tasks 1–13, 14–20 |
| Out of scope: Jev, any LLM, other strategies, automatic orders, Congress data | Not built |
