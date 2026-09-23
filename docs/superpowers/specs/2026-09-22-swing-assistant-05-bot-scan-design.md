# Spec 05 — Telegram Bot and Evening Scan

**Parent:** [Overview](2026-09-22-swing-assistant-00-overview.md) · **Depends on:** specs 01–04. Only setups listed in `live_setups` in `data/strategy_v1.yaml` (set from the spec 02/03 results) produce entries.
**Goal:** Run the evening scan on the owner's PC, deliver signals, exits, and a summary to Telegram, and take fills back through buttons and commands.

## Evening scan (`signalbench scan`)

**Target session:** the latest NYSE session whose close was at least 30 minutes ago (America/New_York). **Catch-up** covers every session after the last successful scan's `as_of`, up to that target.

**Steps, each recorded in `scan_runs`** (migration `0012`; as_of, started_at, finished_at, status, failed_step, error, counts):

| # | Step | Critical? |
| --- | --- | --- |
| 1 | DB connectivity check | yes |
| 2 | `ingest prices` (spec 01) and liquidity flags | yes |
| 3 | `ingest filings`, `ingest news`, `ingest earnings` | no. On failure, warn, and set `jev_status=unavailable` on tonight's signals |
| 4 | `jev backfill` for new documents (`--max-cost-usd 1.00` per scan) | no. Same as step 3 |
| 5 | For each missed session before the target, in order: `decide()` for exits and stop updates only (entries dropped as stale). Exit alerts are sent marked **late** | yes |
| 6 | Target session: equity snapshot, pause check, full `decide()` | yes |
| 7 | Live CDR sizing (below), explanations, write `trade_signals`, send entries and exits | yes (explanations: no) |
| 8 | Expire old signals; run the scale-up check when due | yes |
| 9 | Evening summary | yes |

- A critical failure stops the scan, marks it failed, and sends `⚠️ Scan failed at step N (<name>): <error>`. The next scheduled run retries.
- **Idempotent:** a successful `as_of` isn't rescanned without `--force`. Signals are unique on (`as_of`, `us_symbol`, `setup`), so a forced rescan never re-sends.
- **Single instance:** a Postgres advisory lock. A second concurrent scan exits immediately.
- `--dry-run --as-of DATE` prints every message to the console and writes nothing.

## Live CDR sizing

For each `EntryOrder`:
- `cdr_close` = the CDR's latest close. If it's missing or the CDR is inactive, skip with `no_cdr_price`.
- `stop_pct = (us_signal_close − us_stop) / us_signal_close`, and `cdr_stop = cdr_close × (1 − stop_pct)`.
- `risk_amount_cad = risk_pct × equity`. `risk_pct` is 0.02; profile B has no ramp, because the $100 start is the ramp.
- `units_target = risk_amount_cad / (cdr_close − cdr_stop)`. Cap it so the value ≤ equity/3 and ≤ uncommitted cash.

**Whole-unit rule:**
- `w = floor(units_target)`. If `w ≥ 1` and `w ≥ 0.75 × units_target`, use `w` whole units with a **limit** order at `cdr_close × 1.01`.
- Otherwise try `c = ceil(units_target)`. Use it if `c × (cdr_close − cdr_stop) ≤ 0.025 × equity` and `c × cdr_close` fits the caps.
- Otherwise use a **fractional market** order for `units_target` (dollar amount shown), with "only place it if the price is ≤ C$X" (1% rule).

**Entry session** is the next Cboe Canada session, using the `XTSE` calendar as the proxy. If Cboe Canada is closed while NYSE is open, the message says so, and the signal expires at the close of the next Cboe Canada session instead.

## Messages

**Entry:**
```text
🟢 BUY NVDA (CDR) — Pullback · NVIDIA
Signal C$38.40 · Stop C$36.10 (−6.0%) · Target +2R after your fill
Size 1 unit (~C$38.40) · risk C$2.30 · LIMIT C$38.78
Skip if price > C$38.78 or bid/ask spread > <survey limit>%
Jev: no negative filing or news in last 10 sessions
Why: <one line from Claude>
[✅ I bought] [⏭ Skip]
```
- The Jev line is one of: no negative / ⭐ catalyst: `<event_type>` (`<source>`) / ⚠️ Jev check unavailable — read the filing yourself.
- Breakout shows "Trailing stop, no target".
- ✅ offers [Use suggested] or asks for a reply like `1 38.52` (units, price; date defaults to today, and a third token can override it) → `Ledger.record_fill` → confirmation.
- ⏭ asks for a reason: [Disagree] [No time] [Price moved >1%] [Spread too wide] [Other].
- A second tap on a handled signal replies "already logged".

**Exit:**
```text
🔴 SELL NVDA (CDR) — stop hit (close C$35.90 ≤ stop C$36.10)
Held 4 sessions · unrealized −C$2.60 (−6.8%)
Sell at the open.
[✅ Sold] [Ignore]
```
- ✅ asks for units (default all) and price. Ignore records `ignored`, which counts as a miss in the scale-up check.
- Late exits carry `(late — should have been sent after <date>)`.

**Evening summary:**
- date, QQQ regime (above or below the 200-day average), signals sent, skips by reason, exits
- positions: symbol, units, ACB/unit, last, P&L %, stop, sessions held
- cash, equity, peak, drawdown %, pause state
- warnings, Jev and Claude spend tonight
- the scale-up result when due. On a pass: "All 4 checks passed. You can add $900 when ready; record it with /deposit 900."

**Pause:** when triggered, the bot sends a review:
- the last 10 trades with R
- setups' live R vs their backtest mean R
- the owner's skips and misses
- "/resume to continue (resets peak)"

## Commands

| Command | Action |
| --- | --- |
| `/signals` | Today's open signals |
| `/portfolio` | Positions, cash, equity, drawdown |
| `/pnl` | Realized and unrealized P&L, all-time and this month; closed managed trades: count, win rate, mean R vs backtest |
| `/buy SYMBOL QTY PRICE [DATE]` | Manual or unsignalled buy |
| `/sell SYMBOL QTY\|all PRICE [DATE]` | Sell; linked to an open exit alert for that symbol if one exists |
| `/void FILL_ID REASON` | Void a fill |
| `/deposit AMOUNT`, `/withdraw AMOUNT` | Cash movements |
| `/resume` | Only while paused. Shows the review and a [Confirm] button |
| `/status` | Last scan result and time, next scheduled run, strategy version, live setups, Jev filter mode and θ |
| `/tax YEAR` | ACB report summary (full CSV via the CLI) |
| `/help` | This list |

Symbols are CDR symbols. The bot resolves US symbols to their CDR when they're unambiguous.

## Explanations (Claude)

- Anthropic Python SDK, model `claude-haiku-4-5-20251001` (confirm the current ID in the plan). `max_tokens` 80, timeout 10 s, 1 retry.
- **Input:** setup name, the trigger values (e.g. RSI(2) = 6.1, 20-session high), regime, and Jev's impact probabilities, event type, and headline or 8-K items. **Never holdings, cash, or fills.**
- **System prompt:** write one plain sentence of at most 30 words saying why the rules flagged this, with no advice, no predictions, and no price targets.
- On failure, the "Why:" line is left out and the summary shows a warning.

## Telegram and processes

- `python-telegram-bot` (async), long polling. Confirm the current major version and API in the plan with ctx7.
- **Allowlist:** updates from any chat other than `TELEGRAM_CHAT_ID` are ignored with no reply, and logged.
- A `Messenger` protocol (`send(text, buttons) -> message_id`, `edit(message_id, text, buttons)`), with `TelegramMessenger` and `FakeMessenger`. The scan sends through the Bot API. Button presses and commands are handled only by the bot process.
- **Windows Task Scheduler**, with a setup script `scripts/windows/register-tasks.ps1` and docs in the README:
  - `signalbench bot run`: at logon, restart every 1 minute on failure.
  - `signalbench scan`: daily at 17:00 America/New_York, converted to the PC's local time zone by the script, plus at logon with a 5-minute delay for catch-up.
  - The Docker Postgres container uses `restart: unless-stopped`.
- `.env` gains `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. `FINNHUB_API_KEY` comes from spec 01.

## Testing

- **End-to-end on fixtures with `FakeMessenger` and fake Jev and Claude:** seed data where a pullback fires on day D, then scan D. Expect an entry message and a `trade_signals` row. Tap ✅ with "1 38.52", and a fill is recorded. The stop is hit on D+3, so scan D+3 sends an exit alert. Tap ✅ Sold, and the position is closed with correct P&L and ACB.
- **Catch-up:** last scan D, run at D+3, so late exits are evaluated for D+1 and D+2, and entries only for D+3.
- A critical step failure sends the ⚠️ message and records `scan_runs.failed`. A non-critical failure sends signals with Jev unavailable.
- Idempotency and the advisory lock.
- Whole-unit rule: each branch (floor, ceil, fractional) and the 2.5% risk bound.
- Cboe Canada holiday: the expiry extends.
- The allowlist ignores a foreign chat.
- Every command's parsing and error replies (oversell, unknown symbol, bad number).
- The explanation request contains no ledger data (assert on the fake client's captured input).

## Gate

- CI green, and the end-to-end fixture test passes.
- `scan --dry-run --as-of <recent session>` on real data prints sensible messages. The owner reviews them.
- Tasks are registered. One real scheduled evening scan delivers a summary to the owner's Telegram. `/portfolio` works from the phone. `/deposit 100` is recorded.
- **Go live:** the owner trades the first real signal on Wealthsimple.

## Changelog

- 2026-09-22: created.
- 2026-09-22: the entry message includes the spread limit from the survey (default 0.5%); `wide_spread` skip button added.
