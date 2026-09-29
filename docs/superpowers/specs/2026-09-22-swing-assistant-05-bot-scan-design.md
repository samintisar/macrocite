# Spec 05 — Telegram Bot and Evening Scan

**Parent:** [Overview](2026-09-22-swing-assistant-00-overview.md) · **Depends on:** spec 01 (ingest), spec 02 (`decide()`), spec 04 (ledger), spec 06 (`data/strategy_v2-none-cash.yaml`), and spec 07 (code only: the safe price ingest, the earnings-calendar ingest, split detection, and the pinned-worktree script with its toast; paper trading was never started).
**Live strategy:** only `data/strategy_v2-none-cash.yaml`, Breakout only, produces entries. The file is frozen by spec 04's `live_config` row.
**Goal:** Run the evening scan on the owner's PC; deliver entries, stop raises, exits, and a summary to Telegram; and take fills back through buttons and commands.

## Evening scan (`signalbench scan`)

**Target session:** the latest complete NYSE session (`last_complete_session`, the 16:15 New York rule shared with the backtest and spec 07). **Catch-up** covers every session after the last successful scan's `as_of`, before the target.

**Steps, each recorded in `scan_runs`** (migration `0014_scan_runs`, on top of spec 04's `0013`):
- Columns: id, as_of, started_at, finished_at, status (`running`/`ok`/`failed`), failed_step, error, warnings, counts, git_sha, git_dirty, config_sha256.
- A `running` row older than two hours is marked failed, "abandoned (killed or crashed)", at the next scan's start.

| # | Step | Critical? |
| --- | --- | --- |
| 1 | Database check: connectivity, the `live_config` row exists, and the config file's sha256 matches it. Refuse uncommitted changes to tracked files under `src/`, `data/`, `alembic/`, `pyproject.toml`, or `uv.lock` | yes |
| 2 | `ingest prices` (spec 01, with spec 07's fix: fetch before write, and a rescale fetches and writes the whole history in one commit) and the liquidity flags. The target needs a bar for QQQ and for every symbol held or pending; any other universe name without one is not tradable tonight | yes |
| 3 | Earnings-calendar ingest (`ingest earnings`: the SEC 2.02 sync plus the Finnhub calendar for the next 30 days, queried from the last successful scan's `as_of` so that catch-up sessions keep their dates) | no. On failure, warn and use the stored dates |
| 4 | Split check for held positions (the CDR of every position, and the US symbol of managed ones) and `sent` signals (spec 04, Splits): record new splits, write `split` stop rows, run the scale check | no. A failed lookup or scale check holds that symbol's decisions for the night, with a ⚠️ line |
| 5 | Catch-up for each missed session before the target, in order: equity snapshot, pause state, then `decide()` for exits and stop raises only. Entries are dropped as stale. Exits and stop raises are sent marked **late**, and raises are recorded so that later sessions use them | yes |
| 6 | Tonight: equity snapshot, pause check, then `decide()` with the `PortfolioState` built from the ledger (spec 04: real positions and cash, with sent, unexpired signals holding slots) | yes |
| 7 | Live CDR sizing of each entry (below) | yes |
| 8 | The template "Why" line for each entry (below) | yes |
| 9 | Write `trade_signals`, `exit_alerts`, and `stop_updates`, then send entries, exit alerts, and stop-raise messages | yes |
| 10 | Expire old signals; run the scale-up check when due | yes |
| 11 | Evening summary | yes |

- Every `decide()` call uses the live config with `with_setups(("breakout",))` and `NullReadingsView` (spec 04).
- A critical failure stops the scan, marks it failed, and sends `⚠️ Scan failed at step N (<name>): <error>` **and** a Windows toast (below). The next scheduled run retries. If Telegram itself is unreachable, the toast still shows.
- **Idempotent:** a successful `as_of` isn't rescanned without `--force`.
  - Signals are unique on (`as_of`, `us_symbol`, `setup`) and trail stop rows on (`signal_id`, `session`), so a forced rescan never re-sends.
  - A row written without a `telegram_message_id` (the send failed) is sent by the next run.
  - A run whose target was already scanned exits 0 and sends nothing.
- **Single instance:** a Postgres advisory lock. A second concurrent scan exits immediately.
- `--dry-run --as-of DATE` prints every message to the console, then writes nothing and sends nothing.

## Live CDR sizing

For each `EntryOrder`:
- `cdr_close` = the CDR's latest close. If it's missing or the CDR is inactive, skip with `no_cdr_price`.
- `stop_pct = (us_signal_close − us_stop) / us_signal_close`, and `cdr_stop = cdr_close × (1 − stop_pct)`.
- `risk_amount_cad = risk_pct × equity`. `risk_pct` is the config's 0.02. There is no ramp, because the C$100 start is the ramp.
- `units_target = risk_amount_cad / (cdr_close − cdr_stop)`. Cap it so the value ≤ equity/3 (`max_positions`) and ≤ uncommitted cash.

**Whole-unit rule:**
- `w = floor(units_target)`. If `w ≥ 1` and `w ≥ 0.75 × units_target`, use `w` whole units with a **limit** order at `cdr_close × 1.01`.
- Otherwise try `c = ceil(units_target)`. Use it if `c × (cdr_close − cdr_stop) ≤ 0.025 × equity` and `c × cdr_close` fits the caps.
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
Skip if price > C$38.78 or bid/ask spread > <survey limit>%
Why: Closed at a 20-session high (US$181.52) on 2.3× its 50-session average volume, above its 50-session average.
[✅ I bought] [⏭ Skip]
```
- R is the risk planned at the signal (signal − stop, C$2.30 above), not fill − stop (spec 04).
- ✅ offers [Use suggested] or asks for a reply like `1 38.52` (units and price; the date defaults to today, and a third token can override it). That goes to `Ledger.record_fill`, which sends a confirmation.
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
- ✅ asks for units (default all) and a price. Ignore records `ignored`, which counts as a miss in the scale-up check.
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
| `/buy SYMBOL QTY PRICE [DATE]` | Manual or unsignalled buy |
| `/sell SYMBOL QTY\|all PRICE [DATE]` | Sell; linked to an open exit alert for that symbol if one exists |
| `/void FILL_ID REASON` | Void a fill |
| `/deposit AMOUNT`, `/withdraw AMOUNT` | Cash movements |
| `/resume` | Only while paused. Shows the review and a [Confirm] button |
| `/status` | Last scan result and time, next scheduled run, live config path and its sha256 (first 8 characters), code version (the last scan's git sha), pause state |
| `/tax YEAR` | ACB report summary (full CSV via the CLI) |
| `/help` | This list |

Symbols are CDR symbols. The bot resolves US symbols to their CDR when they're unambiguous.

## Telegram and processes

- `python-telegram-bot` (async), long polling. Confirm the current major version and API in the plan with ctx7.
- **Allowlist:** only `TELEGRAM_CHAT_ID`. Updates from any other chat are ignored with no reply, and logged.
- A `Messenger` protocol (`send(text, buttons) -> message_id`, `edit(message_id, text, buttons)`), with `TelegramMessenger` and `FakeMessenger`. The scan sends through the Bot API. Button presses and commands are handled only by the bot process.
- **Pinned worktree** (spec 07's pattern for `scripts/paper_nightly.ps1`):
  - Both scheduled tasks run a git worktree checked out at a tag (`live-v1`), so the code that trades changes only by a deliberate checkout of a new tag.
  - `.env` is not tracked, so the scripts load the main checkout's `.env` (`-RepoRoot`, `-EnvFile`). The environment wins, and values are never logged.
  - The scripts run `uv run --frozen signalbench scan` or `uv run --frozen signalbench bot run`, and append their output to `logs/scan-<yyyy-MM>.log` or `logs/bot-<yyyy-MM>.log` (git-ignored).
  - Each scan records its code version (`scan_runs.git_sha`, `git_dirty`). Step 1 refuses a dirty tree.
  - Migrations are run by hand, once, before the tasks are registered.
- **Scripts:** `scripts/live_scan.ps1` and `scripts/live_bot.ps1` share the `.env` loading and toast code with `paper_nightly.ps1`, and need Windows PowerShell 5.1 for the toast.
  - **Scan failure:** on a non-zero exit, `live_scan.ps1` shows a toast ("SignalBench scan failed: <first line of the error>"). The scan has already sent its ⚠️ Telegram message when it could.
  - **Stale scan:** before each run, the script checks `signalbench scan status --stale-after-days 3` (exit 3) and shows a toast when the last `ok` scan is more than 3 days old. The check never stops the run.
  - **Bot:** `live_bot.ps1` shows a toast when the bot exits with an error.
- **Windows Task Scheduler**, registered by `scripts/windows/register-tasks.ps1`, with docs in the README:
  - `live_bot.ps1`: at logon, restart every 1 minute on failure, no time limit.
  - `live_scan.ps1`: daily at 17:00 America/New_York, converted to the PC's local time by the script, plus at logon with a 5-minute delay for catch-up.
    - The conversion uses the earliest local time that is 17:00 New York or later on every day of the next 12 months, and the script prints it.
    - On this PC (British Columbia, which stops changing clocks in November 2026) that is 15:00 local: 18:00 New York in summer and 17:00 in winter.
    - A run before the session is complete would target the previous session and lose that night's entries.
  - The Docker Postgres container uses `restart: unless-stopped`.
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
