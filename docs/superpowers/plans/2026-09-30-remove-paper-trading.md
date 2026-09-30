# Remove paper trading — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the forward paper-trading code (spec 07) and its four tables, which were never used, without changing anything the live v2-none-cash system does.

**Architecture:** The live scan borrows five small pieces from `signalbench.paper` and one misnamed function. Task 1 moves them to neutral homes and repoints the live code (no behavior change). Task 2 deletes the paper package, its CLI commands, tests, script, models, and migration `0012` (never applied to the real database, which is at `0011`), and makes `0013` revise `0011`. Task 3 updates the docs.

**Tech Stack:** Python 3.11, SQLModel, Alembic, Typer, pytest, ruff, mypy. Run everything with `uv run`.

**Owner decision (2026-09-30):** "I guess you can also get rid of some of the tables that are not going to be used since we have uh, changed and updated the plans." Kept on purpose: `raw_documents` and `document_tickers` (every scan's `ingest earnings` runs `sync_sec_earnings_events`, which reads them and deletes SEC earnings dates it cannot find there), `jev_readings` and `backtest_runs` (the evidence behind `docs/research-log.md`; nothing live reads them).

**Rules for every task:** never edit `.env*`, `data/strategy_*.yaml`, `README.md` (the owner has uncommitted edits), or the untracked `data/paper_v1.yaml`. Stage explicit paths only (never `git add -A` or `git add .`). End each commit message with a blank line and `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. The full suite takes 2–5 minutes.

---

### Task 1: Move the shared pieces out of `signalbench.paper`

No behavior change: the live code and tests import from new homes. The paper package keeps working until Task 2 deletes it.

**Files:**
- Create: `src/signalbench/db/lock.py`
- Create: `tests/repo_helpers.py`
- Modify: `src/signalbench/ingest/prices.py`, `src/signalbench/backtest/provenance.py`, `src/signalbench/ingest/earnings.py`, `src/signalbench/backtest/runner.py`, `src/signalbench/live/outbox.py`, `src/signalbench/live/levels.py`, `src/signalbench/live/scan.py`, `src/signalbench/cli.py`
- Modify: `src/signalbench/paper/lock.py`, `src/signalbench/paper/splits.py`, `src/signalbench/paper/start.py`, `src/signalbench/paper/run.py` (only to import the moved pieces)
- Modify tests: `tests/paper_helpers.py`, `tests/live_helpers.py`, `tests/scan_helpers.py`, `tests/test_live_cli.py`, `tests/test_live_outbox.py`, `tests/test_live_scan.py`, `tests/test_live_start.py`, `tests/test_live_telegram_bot.py`, `tests/test_scan_cli.py`

- [ ] **Step 1: The advisory lock.** Create `src/signalbench/db/lock.py`:

```python
"""One run at a time: a Postgres session advisory lock."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, text


@contextmanager
def advisory_lock(engine: Engine, key: int) -> Iterator[bool]:
    """Yield True when this process holds the lock named `key`, False when another run does.

    The lock lives on its own connection, held open for the whole run, so the run's
    transactions do not release it; Postgres drops it if the process dies.
    """
    with engine.connect() as connection:
        held = bool(
            connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar()
        )
        connection.commit()
        try:
            yield held
        finally:
            if held:
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                connection.commit()
```

Replace the body of `src/signalbench/paper/lock.py` below its docstring with:

```python
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine

from signalbench.db import lock

PAPER_RUN_LOCK = 2026_0929_07  # an arbitrary bigint that names the paper-run lock


@contextmanager
def advisory_lock(engine: Engine, key: int = PAPER_RUN_LOCK) -> Iterator[bool]:
    with lock.advisory_lock(engine, key) as held:
        yield held
```

In `src/signalbench/cli.py`, the live scan's lock (`advisory_lock(engine, LIVE_SCAN_LOCK)`, near line 1014) must use the new module. Add `from signalbench.db.lock import advisory_lock` in the import block, and change the paper import to `from signalbench.paper.lock import advisory_lock as paper_lock`; the `paper run` command (near line 799) calls `paper_lock(engine)`. `tests/test_scan_cli.py` monkeypatches `cli.advisory_lock` with a two-argument lambda — that keeps working.

- [ ] **Step 2: `SplitFetcher`.** In `src/signalbench/ingest/prices.py`, directly after the `Split` dataclass, add:

```python
SplitFetcher = Callable[[str, date], list[Split]]  # symbol, since: splits with an ex-date after it
```

(`Callable` and `date` are already imported there.) In `src/signalbench/paper/splits.py`, delete its own `SplitFetcher = ...` line and its comment, and import it: change `from signalbench.ingest.prices import Split` to `from signalbench.ingest.prices import Split, SplitFetcher`. If ruff then reports `SplitFetcher` unused there, check with `grep -rn "splits import SplitFetcher\|splits.SplitFetcher" src tests` whether anything imports it from `paper.splits`; if something does, repoint that import to `signalbench.ingest.prices`; then drop the unused name from the import. In `src/signalbench/live/scan.py`, replace `from signalbench.paper.splits import SplitFetcher` with `from signalbench.ingest.prices import SplitFetcher` (merge into an existing `signalbench.ingest.prices` import if there is one; let `uv run ruff check --fix` sort it).

- [ ] **Step 3: `uncommitted_code`.** In `src/signalbench/backtest/provenance.py`, after the `CodeVersion` class, add:

```python
def uncommitted_code(version: CodeVersion) -> str:
    """Why a command refuses a working tree with uncommitted code or data changes."""
    return (
        f"uncommitted changes to tracked code or data ({', '.join(version.changed)}); "
        "commit them or check out a clean tag, then rerun"
    )
```

In `src/signalbench/paper/start.py`, delete its `uncommitted_code` function and add `uncommitted_code` to its existing `from signalbench.backtest.provenance import (...)` import. In `src/signalbench/live/scan.py`, replace `from signalbench.paper.start import uncommitted_code` with an import from `signalbench.backtest.provenance`. Check `grep -rn "uncommitted_code" src tests` and repoint any other import that goes through `paper.start` (e.g. `paper/run.py`).

- [ ] **Step 4: The split tolerance.** In `src/signalbench/live/levels.py`, delete `from signalbench.paper.splits import MARK_TOLERANCE` and replace the `TOLERANCE = ...` line with:

```python
TOLERANCE = Decimal("0.03")  # a larger gap than 3% between two closes is an unrecorded split
```

- [ ] **Step 5: The earnings-calendar names.** In `src/signalbench/ingest/earnings.py`, rename `paper_earnings_dates` to `calendar_earnings_dates` and replace its docstring with:

```python
    """Every SEC Item 2.02 date plus every stored Finnhub calendar date, upcoming ones included:
    the live scan's earnings dates. Forward, a date is known from the calendar weeks before its
    8-K is filed. Unclustered, like sec_earnings_dates()."""
```

In the same file, in the calendar-ingest docstring (near line 85), replace the sentence starting `` `since` (paper runs only: the oldest last session of a portfolio that is behind) `` with `` `since` (the live scan's catch-up: the first session it has not scanned) ``, keeping the rest of that sentence unchanged. Repoint every user of the old name (`grep -rn "paper_earnings_dates" src tests`): `src/signalbench/backtest/runner.py` (import and line 113), `src/signalbench/live/outbox.py` (import and line 122), and any test. In `runner.py`'s `load_market_inputs` docstring, change `` `calendar_earnings` (paper trading only) `` to `` `calendar_earnings` (the live scan only) ``.

In `src/signalbench/cli.py`, rename `_ingest_paper_earnings` to `_ingest_calendar_earnings` (definition and both call sites) and replace its docstring with:

```python
    """`ingest earnings` for the scan: the Finnhub calendar from `since` on, skipped with a
    warning when FINNHUB_API_KEY is not set."""
```

In `tests/test_scan_cli.py`, change `monkeypatch.setattr(cli, "_ingest_paper_earnings", ...)` to `"_ingest_calendar_earnings"`.

- [ ] **Step 6: Test helpers.** Create `tests/repo_helpers.py`:

```python
"""A git runner for throwaway test repos, and New York evening timestamps."""

import subprocess
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
         "-c", "commit.gpgsign=false", *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def evening(day: date, hour: int = 18) -> datetime:
    """`hour`:00 New York time on `day`."""
    return datetime.combine(day, time(hour, 0), tzinfo=NEW_YORK)
```

In `tests/paper_helpers.py`, delete its own `NEW_YORK`, `git`, and `evening` definitions and import them: `from repo_helpers import NEW_YORK, evening, git` (drop now-unused imports such as `subprocess`, `time`, `ZoneInfo`). In every test file that imports from `paper_helpers` but is not a `test_paper_*.py` file — `tests/live_helpers.py`, `tests/scan_helpers.py`, `tests/test_live_cli.py`, `tests/test_live_outbox.py`, `tests/test_live_scan.py`, `tests/test_live_start.py`, `tests/test_live_telegram_bot.py`, `tests/test_scan_cli.py` — change `from paper_helpers import` to `from repo_helpers import` (same names).

- [ ] **Step 7: Verify nothing outside the paper package imports it.**

Run: `grep -rnE "signalbench\.paper|paper_helpers|paper_earnings_dates|_ingest_paper_earnings" src tests --include=*.py | grep -vE "^src/signalbench/paper/|^tests/test_paper_|^tests/paper_helpers.py"`
Expected: exactly the four `from signalbench.paper...` imports in `src/signalbench/cli.py` (`lock` as `paper_lock`, `run`, `start`, `status`) and nothing else.

- [ ] **Step 8: Full checks.**

Run: `uv run pytest -q -p no:cacheprovider` (timeout 600000 ms), `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`
Expected: 821 passed; ruff and mypy clean. (If `ruff format --check` flags only files you touched, run `uv run ruff format <those files>`; do not reformat other files.)

- [ ] **Step 9: Commit.**

```bash
git add src/signalbench/db/lock.py tests/repo_helpers.py src/signalbench/ingest/prices.py src/signalbench/backtest/provenance.py src/signalbench/ingest/earnings.py src/signalbench/backtest/runner.py src/signalbench/live/outbox.py src/signalbench/live/levels.py src/signalbench/live/scan.py src/signalbench/cli.py src/signalbench/paper/lock.py src/signalbench/paper/splits.py src/signalbench/paper/start.py src/signalbench/paper/run.py tests/paper_helpers.py tests/live_helpers.py tests/scan_helpers.py tests/test_live_cli.py tests/test_live_outbox.py tests/test_live_scan.py tests/test_live_start.py tests/test_live_telegram_bot.py tests/test_scan_cli.py
git commit -m "refactor: move the pieces the live scan shares with paper trading out of paper/"
```

(Add any other file you had to touch; `git status` must show only ` M README.md` and `?? data/paper_v1.yaml` afterwards.)

---

### Task 2: Delete paper trading

**Files:**
- Delete: `src/signalbench/paper/` (whole package), `tests/paper_helpers.py`, every `tests/test_paper_*.py`, `scripts/paper_nightly.ps1`, `alembic/versions/0012_paper_trading.py`
- Modify: `src/signalbench/db/models.py`, `src/signalbench/cli.py`, `alembic/versions/0013_live_ledger.py`, `tests/test_migrations.py`, `src/signalbench/backtest/simulator.py`, `scripts/lib/SignalBench.ps1`, `scripts/live_scan.ps1`, `scripts/live_bot.ps1`, `tests/test_config.py`

- [ ] **Step 1: Delete the files.**

```bash
git rm -r -q src/signalbench/paper tests/paper_helpers.py scripts/paper_nightly.ps1 alembic/versions/0012_paper_trading.py
git rm -q tests/test_paper_*.py
```

Also delete any leftover `src/signalbench/paper/__pycache__` directory from disk.

- [ ] **Step 2: Models.** In `src/signalbench/db/models.py`, delete the four classes `PaperPortfolio`, `PaperEvent`, `PaperEquity`, `PaperRun` (with their docstrings), and any import that becomes unused (ruff will say).

- [ ] **Step 3: Migrations.** In `alembic/versions/0013_live_ledger.py`, change the docstring line `Revises: 0012_paper_trading` to `Revises: 0011_jev_readings` and `down_revision: str | None = "0012_paper_trading"` to `down_revision: str | None = "0011_jev_readings"`. In `tests/test_migrations.py`, delete `test_0012_creates_the_four_paper_tables`, and in `test_0013_creates_the_nine_ledger_tables` change the expected line to `'down_revision: str | None = "0011_jev_readings"'`.

- [ ] **Step 4: CLI.** In `src/signalbench/cli.py`, delete: the imports from `signalbench.paper.lock`, `signalbench.paper.run`, `signalbench.paper.start`, `signalbench.paper.status`; the constants `PAPER_V1_PATH` and `PAPER_REPORTS_DIR`; the `paper_app = typer.Typer(...)` and `app.add_typer(paper_app, name="paper")` lines; the three commands `paper_start`, `paper_run`, `paper_status` (from `@paper_app.command("start")` down to the line before `live_app = typer.Typer(`). Change the `STALE_EXIT` comment to `# \`scan status --stale-after-days\`: no ok run for too long`. Remove any import ruff reports unused (e.g. `yaml` if only `paper_start` used it — check first).

- [ ] **Step 5: Comments.**
  - `src/signalbench/backtest/simulator.py` module docstring: delete the sentence `Forward paper trading (spec 07) saves the \`SimState\` between nights.` If the live code saves a `SimState` (check `grep -rn "state_to_json\|state_from_json" src/signalbench/live`), replace it instead with `The live scan saves the \`SimState\` between nights.`
  - `scripts/lib/SignalBench.ps1` synopsis: `(paper_nightly.ps1, live_scan.ps1, live_bot.ps1)` becomes `(live_scan.ps1, live_bot.ps1)`.
  - `scripts/live_scan.ps1` and `scripts/live_bot.ps1`: `The log, toast, and .env loading are shared with paper_nightly.ps1 (lib\SignalBench.ps1).` becomes `The log, toast, and .env loading are shared (lib\SignalBench.ps1).`
  - `tests/test_config.py` docstring: `(none in the paper worktree)` becomes `(none in the live worktree)`.

- [ ] **Step 6: Nothing left.**

Run: `grep -rniE "paper" src tests scripts alembic --include=*.py --include=*.ps1`
Expected: no output. If a line remains, it must be unrelated to spec 07 (quote it in your report) or be fixed the same way as Step 5.

- [ ] **Step 7: Full checks.**

Run: `uv run pytest -q -p no:cacheprovider` (timeout 600000 ms), `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`, and `uv run alembic heads`
Expected: all pass (fewer tests than 821: the paper tests are gone; report the count); ruff and mypy clean; `alembic heads` prints `0016_bot_updates (head)`. Also run `uv run signalbench --help` and confirm `paper` is no longer listed.

- [ ] **Step 8: Commit.**

```bash
git add src/signalbench/db/models.py src/signalbench/cli.py alembic/versions/0013_live_ledger.py tests/test_migrations.py src/signalbench/backtest/simulator.py scripts/lib/SignalBench.ps1 scripts/live_scan.ps1 scripts/live_bot.ps1 tests/test_config.py
git commit -m "chore: remove paper trading, which was never started, and its four tables"
```

(The `git rm` deletions from Step 1 are already staged. `git status` afterwards: only ` M README.md` and `?? data/paper_v1.yaml`.)

---

### Task 3: Docs

**Files:**
- Modify: `docs/superpowers/specs/2026-09-29-swing-assistant-07-paper-trading-design.md`, `docs/superpowers/plans/2026-09-29-swing-07-paper-trading.md`, `docs/superpowers/specs/2026-09-22-swing-assistant-04-ledger-design.md`, `docs/superpowers/specs/2026-09-22-swing-assistant-05-bot-scan-design.md`, `docs/superpowers/specs/2026-09-22-swing-assistant-00-overview.md`, `docs/research-log.md`

- [ ] **Step 1: Retire spec 07 and plan 07.** Directly under each file's first `#` heading, add a blank line and:

```markdown
> **Retired 2026-09-30.** Paper trading was never started, and its code, CLI commands, nightly script, and tables were removed (plan `2026-09-30-remove-paper-trading.md`). This document is kept as history; the code is in git history (branch `feat/swing-07-paper-trading`). The pieces the live scan still uses moved: the advisory lock to `signalbench/db/lock.py`, `SplitFetcher` to `signalbench/ingest/prices.py`, `uncommitted_code` to `signalbench/backtest/provenance.py`, and `paper_earnings_dates` became `calendar_earnings_dates`.
```

- [ ] **Step 2: Spec 04.**
  - Line 3 (`**Depends on:** ...`): replace `and spec 07 (code only: split detection and the earnings-calendar ingest; paper trading was never started).` with `and code first written for spec 07 (split detection and the earnings-calendar ingest; paper trading itself was never started and was removed on 2026-09-30).`
  - Line 26: replace `` `0011` (Jev readings) and `0012` (paper trading) are already in the tree; `0013` sits on top of them. `` with `` `0011` (Jev readings) is the last migration before this one; `0012` (paper trading) was removed on 2026-09-30 without ever being applied, so `0013` revises `0011`. ``
  - Line 235: `like \`paper start\`` becomes `like \`paper start\` did`.
  - Add a changelog entry at the end of its `## Changelog` list, in that list's style: `- 2026-09-30: paper trading removed (plan \`2026-09-30-remove-paper-trading.md\`): migration \`0013\` now revises \`0011\`; the split tolerance (3%) is a constant in \`live/levels.py\`; \`paper_earnings_dates\` is now \`calendar_earnings_dates\`.`

- [ ] **Step 3: Spec 05.**
  - Line 3: replace `and spec 07 (code only: the safe price ingest, the earnings-calendar ingest, split detection, and the pinned-worktree script with its toast; paper trading was never started).` with `and code first written for spec 07 (the safe price ingest, the earnings-calendar ingest, split detection, and the pinned-worktree script with its toast; paper trading itself was never started and was removed on 2026-09-30).`
  - Line 135: `(spec 07's pattern for \`scripts/paper_nightly.ps1\`)` becomes `(first written for spec 07's nightly paper script, since removed)`.
  - Line 141: `share the \`.env\` loading and toast code with \`paper_nightly.ps1\`` becomes `share the \`.env\` loading and toast code in \`scripts/lib/SignalBench.ps1\``.
  - Line 219 (changelog): leave it (history).
  - Add a changelog entry at the end of its `## Changelog` list: `- 2026-09-30: paper trading removed (plan \`2026-09-30-remove-paper-trading.md\`): \`paper_nightly.ps1\` is gone; the scan's advisory lock is \`signalbench/db/lock.py\`, and its earnings-calendar ingest is \`_ingest_calendar_earnings\`.`

- [ ] **Step 4: Overview changelog.** Append to the end of the changelog list in `docs/superpowers/specs/2026-09-22-swing-assistant-00-overview.md`:

```markdown
- 2026-09-30: unused tables removed. Owner's words: "I guess you can also get rid of some of the tables that are not going to be used since we have uh, changed and updated the plans." The paper-trading code and its four tables (migration `0012`, never applied to the real database) are gone. The one-off `backup_eight_k_text_20260928` table (not from a migration) is for the owner to drop; its data is in the 2026-09-28 dump. Kept: `raw_documents` and `document_tickers` (the scan's earnings sync reads them), `jev_readings` and `backtest_runs` (the research log's evidence).
```

- [ ] **Step 5: Research log.** In `docs/research-log.md`, in `## Open questions`, the first bullet's sentence `Forward paper trading (spec 07) was built but not started:` becomes `Forward paper trading (spec 07) was built but never started, and its code was removed on 2026-09-30:` (rest unchanged).

- [ ] **Step 6: Commit.**

```bash
git add docs/superpowers/specs/2026-09-29-swing-assistant-07-paper-trading-design.md docs/superpowers/plans/2026-09-29-swing-07-paper-trading.md docs/superpowers/specs/2026-09-22-swing-assistant-04-ledger-design.md docs/superpowers/specs/2026-09-22-swing-assistant-05-bot-scan-design.md docs/superpowers/specs/2026-09-22-swing-assistant-00-overview.md docs/research-log.md docs/superpowers/plans/2026-09-30-remove-paper-trading.md
git commit -m "docs: retire spec 07 and record the removal of the unused tables"
```

---

### After the tasks (controller, not an implementer)

- Drop the one-off backup table on the real database (not created by any migration): `DROP TABLE backup_eight_k_text_20260928;` The same data is in `C:\Users\samin\Documents\signalbench-backups\signalbench-2026-09-28.dump`.
- The real database is still at `0011`; migrating it to `0016` is go-live step 1 and waits for the owner.
