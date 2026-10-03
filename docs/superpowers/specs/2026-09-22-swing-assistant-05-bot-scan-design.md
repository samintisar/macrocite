# Spec 05 — Telegram Bot and Evening Scan

**Parent:** [Overview](2026-09-22-swing-assistant-00-overview.md) · **Depends on:** spec 01 (ingest), spec 02 (`decide()`), spec 04 (ledger), spec 06 (`data/strategy_v2-none-cash.yaml`), and code first written for spec 07 (the safe price ingest, the earnings-calendar ingest, split detection, and the pinned-worktree script with its toast; paper trading itself was never started and was removed on 2026-09-30).
**Live strategy:** only `data/strategy_v2-none-cash.yaml`, Breakout only, produces entries. The file is frozen by spec 04's `live_config` row.
**Goal:** Run the evening scan on the owner's PC; deliver entries, stop raises, exits, and a summary to Telegram; and take fills back through buttons and commands.

## Evening scan (`signalbench scan`)

**Target session:** the latest complete NYSE session (`last_complete_session`, the 16:15 New York rule shared with the backtest and spec 07). **Catch-up** covers every session after the last successful scan's `as_of`, before the target.

**Steps, each recorded in `scan_runs`** (migration `0014_scan_runs`, on top of spec 04's `0013`):
- Columns: id, as_of, started_at, finished_at, status (`running`/`ok`/`failed`), failed_step, error, warnings, counts, git_sha, git_dirty, config_sha256.
- A `running` row older than two hours is marked failed, "abandoned (killed or crashed)", at the next scan's start.

| # | Step | Critical? |
| --- | --- | --- |
| 1 | Database check: connectivity, the `live_config` row exists, and the config file's sha256 matches it. Refuse uncommitted changes to tracked files under `src/`, `data/`, `alembic/`, `pyproject.toml`, or `uv.lock`, and refuse a HEAD that is neither `live_config.start_git_sha` nor tagged `live-v*` (a deliberate update is a new tag; `--allow-any-commit` for development and dry runs elsewhere) | yes |
| 2 | `ingest prices` (spec 01, with spec 07's fix: fetch before write, and a rescale fetches and writes the whole history in one commit) and the liquidity flags. The target needs a bar for QQQ and for every symbol held or pending; any other universe name without one is not tradable tonight | yes |
| 3 | Earnings-calendar ingest (`ingest earnings`: the SEC 2.02 sync plus the Finnhub calendar for the next 30 days, queried from the last successful scan's `as_of` so that catch-up sessions keep their dates) | no. On failure, warn and use the stored dates |
| 4 | Split check for held positions (the CDR of every position, and the US symbol of managed ones) and `sent` signals (spec 04, Splits): record new splits (yfinance ratios rounded to 6 decimals), write `split` stop rows, run the scale check | no. A failed lookup is a ⚠️ warning only. A failed scale check holds that symbol's decisions for the night, with a ⚠️ line; each held session of a managed position is recorded (`scan_holds`) and reviewed for exits and raises, marked late, by the first scan after the hold clears |
| 5 | Catch-up for each missed session before the target, in order: equity snapshot, pause state, then `decide()` for exits and stop raises only. Entries are dropped as stale. Exits and stop raises are sent marked **late**, and raises are recorded so that later sessions use them | yes |
| 6 | Tonight: equity snapshot, pause check, then `decide()` with the `PortfolioState` built from the ledger (spec 04: real positions and cash, with sent, unexpired signals holding slots) | yes |
| 7 | Live CDR sizing of each entry (below) | yes |
| 8 | The template "Why" line for each entry (below) | yes |
| 9 | Send exit alerts, then stop raises, as soon as steps 5–6 have written them (before sizing, so a later failure cannot hold them back); then write `trade_signals` and send split notices and entries | yes |
| 10 | Expire old signals; run the scale-up check when due | yes |
| 11 | Evening summary | yes |

- Every `decide()` call uses the live config with `with_setups(("breakout",))` and `NullReadingsView` (spec 04).
- A critical failure stops the scan, marks it failed, tries once more to send any unsent exit alerts and raises, and sends `⚠️ Scan failed at step N (<name>): <error>` **and** a Windows toast (below). The next scheduled run retries (two retries a day are scheduled, below). If Telegram itself is unreachable, the toast still shows. The bot token is replaced by `***` in any error text stored, logged, or shown.
- **Idempotent:** a successful `as_of` isn't rescanned without `--force`.
  - Signals are unique on (`as_of`, `us_symbol`, `setup`) and trail stop rows on (`signal_id`, `session`), so a forced rescan never re-sends.
  - A row written without a `telegram_message_id` (the send failed) is sent by the next run: exits, raises, split notices, then entries.
  - A run whose target was already scanned exits 0 and sends only rows still unsent.
  - A retry of a target whose run failed counts that run's signals toward slots and committed cash, so it cannot add signals or over-commit.
- **Single instance:** a Postgres advisory lock. A second concurrent scan exits immediately.
- `--dry-run --as-of DATE` prints every message to the console, then writes nothing and sends nothing.

## Live CDR sizing

For each `EntryOrder`:
- `cdr_close` = the CDR reference: `us_signal_close × (CDR close ÷ US close)` on the most recent date the CDR traded (volume > 0), the equity mark's basis (spec 04), so a stale zero-volume close never sizes an order. If the CDR never traded or is inactive, skip with `no_cdr_price`. The message's close, limit, and stop use it.
- `stop_pct = (us_signal_close − us_stop) / us_signal_close`, and `cdr_stop = cdr_close × (1 − stop_pct)`.
- `risk_amount_cad = risk_pct × equity`. `risk_pct` is the config's 0.02. There is no ramp, because the C$100 start is the ramp.
- `units_target = risk_amount_cad / (cdr_close − cdr_stop)`. Cap it so the value ≤ equity/3 (`max_positions`) and ≤ uncommitted cash. A pending signal commits `suggested_units × limit` of the cash.

**Whole-unit rule:**
- `w = floor(units_target)`, reduced until `w × limit ≤ cap`. If `w ≥ 1` and `w ≥ 0.75 × units_target`, use `w` whole units with a **limit** order at `cdr_close × 1.01` (the limit).
- Otherwise try `c = ceil(units_target)`. Use it if `c × (cdr_close − cdr_stop) ≤ 0.025 × equity` and `c × limit` fits the caps.
- Otherwise use a **fractional market** order for `units_target` (dollar amount shown), with "only place it if the price is ≤ C$X" (1% rule).

**Entry session** is the next Cboe Canada session, using the `XTSE` calendar as the proxy. If Cboe Canada is closed while NYSE is open, the message says so, and the signal expires at the close of the next Cboe Canada session instead.

## The "Why" line (template, no LLM)

`Closed at a 20-session high (US$X) on N.N× its 50-session average volume, above its 50-session average.`

- X is the US signal close, to 2 decimals. N.N is the signal session's volume ÷ the prior 50-session mean volume, to 1 decimal. Both are the same `Snapshot` values that fired the Breakout rule.
- The 20 and 50s come from the config (`lookback`, `volume_lookback`, `trend_sma`).
- The line is a pure function: no network and no model. It is stored in `trade_signals.explanation`.

## Messages

**Entry:**
```text
🟢 BUY NVDA (CDR) — Breakout · NVIDIA
Signal C$38.40 · Stop C$36.10 (−6.0%) · Trailing stop, no target, no time limit
Size 1 unit (~C$38.40) · risk C$2.30 · LIMIT C$38.78
Skip if price > C$38.78, price ≤ the stop C$36.10, or bid/ask spread > <survey limit>%
Why: Closed at a 20-session high (US$181.52) on 2.3× its 50-session average volume, above its 50-session average.
[✅ I bought] [⏭ Skip]
```
- R is the risk planned at the signal (signal − stop, C$2.30 above), not fill − stop (spec 04).
- ✅ never assumes a fill. For a whole-unit (limit) order it pre-fills the suggested units and asks only for the average fill price from Wealthsimple's confirmation (`38.52`; `UNITS PRICE` overrides the units). A fractional order needs both (`0.868055 38.52`). The date defaults to the signal's entry session; a date outside (`as_of`, the expiry session] is refused unless the reply ends in `force`. That goes to `Ledger.record_fill`, which sends a confirmation.
- A price more than 10% from the signal's CDR reference, or more than 2× the suggested units, is shown back with [✅ Confirm] [Cancel] before anything is written.
- An entry sent after its session opened (a late run) is marked `(late — check the price before placing)` and keeps its buttons.
- ⏭ asks for a reason: [Disagree] [No time] [Price moved >1%] [Spread too wide] [Other].
- A second tap on a handled signal replies "already logged".

**Stop raised** (informational, no buttons):
```text
⬆️ NVDA (CDR) stop raised C$36.10 → C$38.40 (US$172.00 → US$183.10) · still holding, no action
```
- One message per `trail` row. Late raises from catch-up add `(late — for <date>)`.
- A `split` row sends `ℹ️ NVDA split 10-for-1 (ex-date <date>): stop US$1,830.00 → US$183.00 · no action`. A CDR split also shows the units and the ACB per unit before and after, and asks the owner to check that Wealthsimple shows the same units.

**Exit** (stop or earnings only):
```text
🔴 SELL NVDA (CDR) — stop hit (US close US$171.20 ≤ stop US$172.00 · CDR ~C$35.90 vs stop C$36.10)
Held 4 sessions · unrealized −C$2.60 (−6.8%)
Sell at the open.
[✅ Sold] [Ignore]
```
- The earnings variant reads `— earnings on <date>, sell before them`.
- ✅ asks for units (or `all`) and a price. A price alone is never silently "all": the bot asks "Sell ALL N units @ C$X?" with [✅ Confirm] [Cancel]. A price more than 10% from the latest CDR mark asks the same way. Ignore records `ignored`, which counts as a miss in the scale-up check.
- Late exits carry `(late — should have been sent after <date>)`.

**Evening summary:**
- date, QQQ regime (above or below the 200-day average), signals sent, skips by reason, exits, stop raises
- positions: symbol, units, ACB/unit, last, P&L %, current stop (C$ and US$), sessions held, and "manual" for manual positions (no stop)
- open exit alerts not yet done or ignored
- cash, equity, peak, drawdown %, pause state
- warnings (calendar ingest failed, a symbol held by the scale check, a late run)
- the scale-up result when due. On a pass: "All 4 checks passed. You can add C$900 when ready; record it with /deposit 900."

**Pause:** when triggered, the bot sends a review:
- the last 10 trades with R
- live mean R vs the backtest mean R of 0.505 (v2-none-cash's report, `reports/backtests/2026-09-28-v2-none-cash-breakout-off.md`, read from its stored run)
- the owner's skips and misses
- "/resume to continue (resets peak)"

## Commands

| Command | Action |
| --- | --- |
| `/signals` | Today's open signals |
| `/portfolio` | Positions with their current stops, cash, equity, drawdown |
| `/pnl` | Realized and unrealized P&L, all-time and this month; closed managed trades: count, win rate, and live mean R vs the backtest's 0.505 (both R on planned risk: spec 04, spec 02) |
| `/buy SYMBOL QTY PRICE [DATE] [force]` | Manual or unsignalled buy (`force`: beyond the cash on hand) |
| `/sell SYMBOL QTY\|all PRICE [DATE]` | Sell; linked to an open exit alert for that symbol if one exists |
| `/void FILL_ID REASON` | Void a fill |
| `/deposit AMOUNT`, `/withdraw AMOUNT [force]` | Cash movements. A withdrawal that takes cash below 0 needs `force` |
| `/resume` | Only while paused. Shows the review and a [Confirm] button |
| `/status` | Last scan result and time, next scheduled run, live config path and its sha256 (first 8 characters), code version (the last scan's git sha), pause state |
| `/tax YEAR` | ACB report summary (full CSV via the CLI) |
| `/help` | This list |

Symbols are CDR symbols. The bot resolves US symbols to their CDR when they're unambiguous. `/buy` and `/sell` at a price more than 10% from the latest CDR mark ask [✅ Confirm] [Cancel] first. Every write stores the Telegram `update_id` in the same transaction (`bot_updates`), so an update redelivered after a crash is not recorded twice.

## Telegram and processes

- `python-telegram-bot` (async), long polling. Confirm the current major version and API in the plan with ctx7.
- **Allowlist:** only `TELEGRAM_CHAT_ID`. Updates from any other chat are ignored with no reply, and logged.
- A `Messenger` protocol (`send(text, buttons) -> message_id`, `edit(message_id, text, buttons)`), with `TelegramMessenger` and `FakeMessenger`. The scan sends through the Bot API. Button presses and commands are handled only by the bot process.
- **Pinned worktree** (first written for spec 07's nightly paper script, since removed):
  - Both scheduled tasks run a git worktree checked out at a tag (`live-v1`), so the code that trades changes only by a deliberate checkout of a new tag.
  - `.env` is not tracked, so the scripts load the main checkout's `.env` (`-RepoRoot`, `-EnvFile`). The environment wins, and values are never logged.
  - The scripts run `uv run --frozen signalbench scan` or `uv run --frozen signalbench bot run`, and append their output to `logs/scan-<yyyy-MM>.log` or `logs/bot-<yyyy-MM>.log` (git-ignored).
  - Each scan records its code version (`scan_runs.git_sha`, `git_dirty`). Step 1 refuses a dirty tree.
  - Migrations are run by hand, once, before the tasks are registered.
- **Scripts:** `scripts/live_scan.ps1` and `scripts/live_bot.ps1` share the `.env` loading and toast code in `scripts/lib/SignalBench.ps1`, and need Windows PowerShell 5.1 for the toast.
  - **Scan failure:** on a non-zero exit, `live_scan.ps1` shows a toast ("SignalBench scan failed: <first line of the error>"). The scan has already sent its ⚠️ Telegram message when it could.
  - **Stale scan:** before each run, the script checks `signalbench scan status --stale-after-days 3` (exit 3) and shows a toast when the last `ok` scan is more than 3 days old. The check never stops the run.
  - **Bot:** `live_bot.ps1` shows a toast when the bot exits with an error.
  - **Bot heartbeat:** the bot writes a heartbeat time (e.g. a single-row `bot_heartbeat` table, created with `scan_runs`) at least every 10 minutes while polling. The evening scan warns in its summary and shows a toast when the last heartbeat is more than 1 hour old, so a bot that is down without exiting is noticed.
- **Windows Task Scheduler**, registered by `scripts/windows/register-tasks.ps1`, with docs in the README:
  - `live_bot.ps1`: at logon, restart every 1 minute on failure, no time limit.
  - `live_scan.ps1`: daily at 17:00 America/New_York, converted to the PC's local time by the script, retried daily 2 hours later and at about 07:30 New York (before the open, converted the same way), plus at logon with a 5-minute delay for catch-up. A retry of a scanned target only sends rows left unsent.
    - The conversion uses the earliest local time that is 17:00 New York or later on every day of the next 12 months, and the script prints it.
    - On this PC (British Columbia, which stops changing clocks in November 2026) that is 15:00 local: 18:00 New York in summer and 17:00 in winter.
    - A run before the session is complete would target the previous session and lose that night's entries.
  - The Docker Postgres container uses `restart: always`, so it comes back whenever Docker starts.
  - Registering the tasks is a lasting change on the owner's PC: the script prints the exact commands, and they run only with the owner's go-ahead.
- `.env` gains `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`, which the owner adds. `FINNHUB_API_KEY` already exists (spec 01). No LLM keys are needed.

**Owner setup:**
1. Create the bot with @BotFather and copy its token.
2. Send the bot a message, then read the chat id (the plan gives the exact `getUpdates` call).
3. Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` to the main checkout's `.env`.
4. Review the exact Task Scheduler commands, then approve registering the tasks.

## Testing

- **End-to-end on fixtures** with `FakeMessenger` and in-memory prices:
  - Seed data where a Breakout fires on day D, and scan D. Expect an entry message with "Trailing stop, no target, no time limit" and the exact template Why, plus a `trade_signals` row.
  - Tap ✅ with "1 38.52", and a fill is recorded.
  - A new high on D+k raises the stop: scan D+k and expect a `trail` row and a ⬆️ message with both currencies.
  - The close falls to the raised stop on D+m: scan D+m and expect an exit alert.
  - Tap ✅ Sold, and the position is closed with the correct P&L, ACB, and R on planned risk.
- **Catch-up:** last scan D, run at D+3.
  - Stop raises and exits are evaluated for D+1 and D+2 in order and sent marked late, and a raise on D+1 is the stop in force for D+2.
  - Entries come only for D+3. No stale entries are sent.
- A critical step failure sends the ⚠️ message and records a failed `scan_runs` row. A calendar-ingest failure warns and the scan uses the stored dates.
- The scan refuses a changed live config, a missing `live_config` row, and a dirty tree.
- A split in a held stock writes a `split` row and an ℹ️ message. An unrecorded split holds that symbol's decisions with a ⚠️ line, and the rest of the scan goes on.
- The Why template: exact text and rounding, with no network access.
- One open exit alert per position: no new alert or raise while one is `sent`.
- Idempotency (including resending a row whose send failed) and the advisory lock.
- Whole-unit rule: each branch (floor, ceil, fractional) and the 2.5% risk bound.
- Cboe Canada holiday: the expiry extends.
- The allowlist ignores a foreign chat.
- Every command's parsing and error replies (oversell, unknown symbol, bad number). `/status` shows the config sha and code version.
- The scripts are checked by hand once: a forced failure shows the toast, and a stale `scan_runs` shows the stale toast.

## Gate

- CI green, and the end-to-end fixture test passes.
- `scan --dry-run --as-of <recent session>` on real data prints sensible messages. The owner reviews them.
- ⛔ Migrations `0013` and `0014` run on the real database, the `live-v1` tag and worktree are created, and `signalbench live start` records the config sha.
- ⛔ Tasks are registered on the owner's go-ahead. One real scheduled evening scan delivers a summary to the owner's Telegram. `/portfolio` works from the phone. `/deposit 100` is recorded.
- **Go live:** the owner places the first real trade on Wealthsimple.

## Out of scope

Jev, Claude or any LLM explanation, other strategies or setups, automatic order placement, and Congress trading data (considered 2026-09-29 and declined).

## Changelog

- 2026-09-22: created.
- 2026-09-22: the entry message includes the spread limit from the survey (default 0.5%); `wide_spread` skip button added.
- 2026-09-24: R in messages, the pause review, and `/pnl` is R on planned risk (signal − stop), as in spec 02 and spec 04; the target is the fill + 2 × planned R.
- 2026-09-29: narrowed to the single live strategy v2-none-cash (owner decision, overview changelog):
  - **Scope:** `live_setups` is replaced by the frozen `live_config` (spec 04). Jev and Claude are removed: no filings/news or Jev steps, no `jev_status`, no Jev line, and no `OPENROUTER_API_KEY`/`ANTHROPIC_API_KEY`. The "Why" line is a fixed template.
  - **Scan:** now 11 steps. It adds the earnings-calendar ingest (non-critical), the split check, and stop raises (catch-up included). `scan_runs` moves to migration `0014`, with the code version, and the target uses `last_complete_session`.
  - **Messages:** a new ⬆️ stop-raised message. Exits are stop or earnings only. The summary shows each position's current stop.
  - **R comparison:** the pause review and `/pnl` compare live R with the backtest's 0.505. `/status` shows the config sha and code version.
  - **Running it:** the scan and bot run from a worktree pinned to the `live-v1` tag, with spec 07's `.env` loading. Failures show a ⚠️ message plus a Windows toast, and a stale-scan toast fires after 3 days.
  - **Registration:** the task time conversion handles British Columbia's clock change.
  - **Tests:** the end-to-end test is rewritten for Breakout (fire, raise, stop hit, catch-up).
- 2026-09-29: the entry message also says to skip when the price is at or below the stop (the backtest's gap-below-stop rule); a bot heartbeat checked by the evening scan. Split corrections stay CLI-only (`ledger split`, `ledger void-action`).
- 2026-09-30: implementation choices (plan [2026-09-30-swing-05-bot-scan.md](../plans/2026-09-30-swing-05-bot-scan.md)):
  - **Tables:** migration `0014_scan_runs` also creates the single-row `bot_heartbeat`. `scan_runs.warnings` is a JSON list and `counts` a JSON object.
  - **Runs:** a run whose target was already scanned still checks step 1 and records an `ok` row with no counts, and sends nothing, so the stale check sees the scheduled task running. Catch-up starts the day after the last `ok` row's `as_of`; with none yet, the target only. A run after the next NYSE session's 09:30 open is a late run: its exits and raises are marked late and the summary says so.
  - **Sending:** every row with a message keeps its Telegram id, and a row without one is sent by the next run: split notices first, then entries, exits, and raises. A failed send fails step 9. An entry whose expiry passed before it could be sent goes out as a "do not place it" line with no buttons.
  - **Sizing:** decide()'s US close and stop are rounded to 4 decimals before sizing and storing, so the two agree. The CDR close is the latest stored close on or before the target. The limit is rounded down to the cent. Fractional units are rounded down to 6 decimals. The spread limit is the survey's default of 0.5%.
  - **Messages:** the header names the CDR ticker (`BUY NVDA (CDR ZNVD)`). A handled message is edited to show what was done, and its buttons are removed. Button data is short (`b:12`; the longest, `k:12:wide_spread`, is 16 bytes), within Telegram's 64.
  - **Dry run:** `scan --dry-run --as-of DATE` runs every step as of 18:00 New York on DATE, inside one database transaction that is rolled back. It fetches nothing, prints every message, and needs no lock.
  - **Bot:** after ✅, the bot waits in memory for one reply, `UNITS PRICE [DATE] [force]`. A restart forgets it, so tap again. [Use suggested] records the suggested units at the signal's CDR close. `/sell ... all` sells every unit held. `/status` reads the next run from Task Scheduler. The heartbeat is written every 5 minutes after a successful `getMe`. The httpx logger is kept at WARNING, because its INFO lines contain the bot token.
  - **Scripts:** the shared PowerShell code moved to `scripts/lib/SignalBench.ps1`, which `paper_nightly.ps1` now uses too. `live_bot.ps1` restarts the bot 60 seconds after an error exit. `live_scan.ps1 -CheckOnly -StaleAfterDays 0` shows the stale toast without scanning. `scripts/windows/register-tasks.ps1` prints the commands and runs them only with `-Register`. Docker's Postgres uses `restart: unless-stopped`.
- 2026-09-30: review fixes before real money (owner-approved):
  - **Fills:** [Use suggested] is gone (it recorded a made-up fill at the signal close). ✅ asks for the actual average fill price of a whole-unit order (units pre-filled), or units and price for a fractional one; the date defaults to the entry session and must be in (`as_of`, expiry session] unless `force`.
  - **Sanity checks:** a price more than 10% from the reference (the signal's CDR reference for a buy, the latest CDR mark for a sale or a manual buy), a signal-linked buy over 2× the suggested units, and a price alone after ✅ Sold ask [✅ Confirm] [Cancel] first.
  - **Exits first:** exit alerts, then raises, are sent right after steps 5–6, before sizing; the outbox order is exits, raises, split notices, entries; a failed run tries them once more; two daily retries (+2 hours, 07:30 New York) and a run for a scanned target sends rows left unsent; a retry counts the failed run's signals toward slots and cash.
  - **Sizing:** whole units must fit the cap at the limit price, and a pending signal commits units × limit.
  - **CDR reference:** `us_signal_close × ` the last traded CDR/US ratio (never a stale zero-volume close); a CDR that never traded is `no_cdr_price`.
  - **Split holds:** only a failed scale check holds a symbol; a failed lookup is a warning; yfinance ratios are rounded to 6 decimals; a held session is reviewed, marked late, once the hold clears (`scan_holds`, migration `0015`). Catch-up exits and raises skip positions closed since.
  - **Token:** the bot token is replaced by `***` in `scan_runs.error`, the scan's and the bot's output and logs, and the scripts' log and toasts.
  - **Dedupe:** the bot stores each recorded update's `update_id` (`bot_updates`, migration `0016`), so a redelivered command or reply is recorded once.
  - **Withdrawals:** `/withdraw` refuses to take cash below 0 unless `force`; the peak moves with cash movements (spec 04), so a withdrawal alone never pauses.
  - **Pinned code:** step 1 also refuses a HEAD that is neither `live_config.start_git_sha` nor a `live-v*` tag (`--allow-any-commit` for development).
  - **Late runs:** entries sent after their session opened are marked "late — check the price before placing", with their buttons.
- 2026-09-30: paper trading removed (plan `2026-09-30-remove-paper-trading.md`): `paper_nightly.ps1` is gone; the scan's advisory lock is `signalbench/db/lock.py`, and its earnings-calendar ingest is `_ingest_calendar_earnings`.
- 2026-10-01: database connections time out after 10 seconds (`signalbench/db/session.py`). After a reboot the bot started while Docker's port was up and Postgres was not; its first heartbeat write hung forever on the connect, so it stopped polling and never checked in. Shipped as `live-v2`; the live config is unchanged.
- 2026-10-03: the Postgres container uses `restart: always`. With `unless-stopped` it did not come back after the owner shut the PC down (Docker Desktop restarted, the container stayed exited), so the scans failed until it was started by hand.
