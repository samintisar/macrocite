# Spec 11 — Crypto paper trading: crypto-v1 forward on botbox

**Parent:** [Overview](2026-09-22-swing-assistant-00-overview.md) · **Depends on:** spec 09 (the Kraken fetcher, `DailySessions`, the crypto runner's data checks, `data/strategy_crypto-v1.yaml`) and spec 07's step refactor (`simulator.step`, still in the code).
**Goal:** Run the pre-registered `crypto-v1` Breakout rules forward on BTC and ETH, one day at a time, on Kraken candles that did not exist when its orders were decided. **No money is involved**, and nothing connects to Kraken's trading API.

## Why

`crypto-v1` passed its one pre-registered backtest (spec 09), but it reused 2015–2026, which was already seen. Paper trading is the only test on new prices. At about 6 trades a year, one year cannot prove an edge; it can show that the system works on live data and whether results look like the backtest. The verdict below is limited to that on purpose.

## Decisions (owner, 2026-10-11)

- **What:** `crypto-v1` only, starting as soon as it is built. Spec 10 (intraday BTC) may become a second paper portfolio later, only if its own backtest passes.
- **No real money** for the foreseeable future. Going live stays a separate spec and an owner decision.
- **Where:** botbox, kept apart from the live bot: its own checkout, tag, database, and systemd units.
- **Messages:** Telegram through the existing bot (send only), on each paper order, fill, closed trade, and failure, plus a weekly summary. Every message starts with `PAPER`.
- **Result:** a fixed 12-month review with three checks (below), giving **on track** or **off track**.
- **Approach:** nightly replay with an append-only log, not spec 07's saved-state runner. Each night re-runs the backtest code from the start day and appends only new days; any disagreement with saved rows stops the run.

## Pre-registration: `data/paper_crypto-v1.yaml`

Committed on its own, after the code is merged and before `crypto paper start`:

```yaml
name: crypto-v1-paper
strategy: data/strategy_crypto-v1.yaml
strategy_sha256: <SHA-256 of the strategy file's bytes>
setup: breakout
start: <first full UTC day after setup on botbox>
review:
  months: 12
  max_late_days: 7
  max_drawdown: 0.35
```

- **Strategy:** the file and its fingerprint. If the strategy file's bytes stop matching, every run refuses. A new idea is a new paper file with a new name, never an edit.
- **Start:** a fixed date, written when the file is committed. The portfolio starts that day with the config's `start_equity` (100) in cash and nothing open. Older candles are used only for indicators (the 200-day average is the longest).
- **Fills:** as in the backtest. Orders are decided at a day's close (00:00 UTC) and filled at the next day's Kraken open, with `cost_per_side` 0.004.
- **Review date:** `start` + `months`. The review covers every day from `start` to the day before the review date.

### The 12-month review (fixed here)

1. **On time.** Every day in the review period has its `paper_days` row, and at most `max_late_days` were recorded more than 24 hours after their candle closed. Day D's candle closes at 00:00 UTC on D+1, so D is late if its row's `recorded_at` is after 00:00 UTC on D+2.
2. **Matches the backtest code.** No run in the period ended with a replay mismatch.
3. **Drawdown.** The worst peak-to-trough drop of the portfolio's daily equity stayed under `max_drawdown`.

All three pass → **on track**. Any one fails → **off track**, with the reasons. The verdict is logged in `docs/research-log.md`. It is not a decision to use real money. Profit, win rate, mean R, and BTC buy-and-hold are reported for information only.

## The nightly job: `signalbench crypto paper run`

A systemd user timer on botbox runs it at 00:30, 06:30, and 12:30 UTC (`Persistent=true`). The first run normally does the night's work; the later ones do nothing unless it failed.

1. **Lock.** `advisory_lock(engine, PAPER_LOCK)` from `db/lock.py`, with its own key. If another run holds it, exit 0 with a message and record nothing.
2. **Run row.** Insert a `paper_runs` row (`started_at`, git SHA).
3. **Rules unchanged.** The paper file and the strategy file are committed and unchanged (`committed_unchanged`), the strategy's SHA-256 matches `strategy_sha256`, and the stored portfolio's `strategy_sha256` matches too. The cost check from spec 09 (`check_cost_matches_kraken`) still applies.
4. **Fetch.** `fetch_kraken_daily` for `XBTUSD` and `ETHUSD` (finished days only), stored with `ingest_kraken_bars`. Revised candles overwrite stored prices, and step 6 then catches any effect on saved rows.
5. **Data checks.** The crypto runner's checks against `last = last_complete_day(now)`: each series ends on `last`, BTC's first bar is on or before `start`, and no gap inside the run is longer than `MAX_GAP_DAYS`.
6. **Replay.** Build the `MarketView` exactly as the crypto runner does (`DailySessions`, the setup restricted to `breakout`, `NullReadingsView`), start from `initial_state(config)`, and call `step()` for each day from `start` to `last`. For each day, keep its events, closed trades, equity point, and the orders in the resulting state (decided at that close).
7. **Compare.** For every day already in `paper_days`, the replay's equity, cash, open positions, events, and orders must equal the saved rows (floats compared after the same JSON round-trip the rows went through). Any difference: the run fails with `replay mismatch on <day>: <what differs>`, saves nothing, and alerts. Later runs fail the same way until the owner decides what to do; nothing is fixed silently.
8. **Append.** In one transaction, add the days after the last saved one: one `paper_days` row each, and their `paper_events` rows (simulator events plus `order_entry` and `order_exit` rows for orders decided at that close), all with `recorded_at = now` and this run's id.
9. **Messages.** For the days just added: one message per fill, closed trade, and new order. On the first successful run of each ISO week (UTC), the weekly summary. A Telegram failure does not undo step 8; the run counts it in `messages_failed`.
10. **Finish.** Set the run row's `finished_at`, `status` (`ok` or `failed`), `error`, and `days_added`. A failed run sends `PAPER ALERT: <error>` (best effort) and exits 1.

A run that finds nothing new (step 8 adds no days) is `ok` with `days_added = 0` and sends nothing.

## Database: `signalbench_paper` on botbox

A separate database in botbox's existing Postgres container. The live `signalbench` database is not touched. Migration `0018_paper_crypto` adds:

| Table | Columns | Notes |
| --- | --- | --- |
| `paper_portfolios` | id, name (unique), paper_file, strategy_sha256, start, created_at | One row for `crypto-v1-paper` |
| `paper_days` | portfolio_id, day, equity, cash, positions (JSON), recorded_at, run_id | Unique on (portfolio_id, day). Never updated |
| `paper_events` | id, portfolio_id, day, kind, payload (JSON), recorded_at, run_id | Append-only. Kinds are the simulator's (`entry`, `exit`, `skip`, `exit_deferred`, `stop_update`, `pause`, `resume`) plus `order_entry` and `order_exit` |
| `paper_runs` | id, started_at, finished_at, status, error, git_sha, days_added, messages_failed | One row per run that gets the lock, including refused and failed ones |

## Messages

Sent with `TelegramMessenger` using the existing `telegram_bot_token` and `telegram_chat_id`; the paper job never polls Telegram, so it cannot clash with the live bot.

- **Order:** `PAPER crypto-v1: BUY XBTUSD at the next open (breakout). Signal close 62,410.00, stop 58,900.00.` Exits: `PAPER crypto-v1: SELL ETHUSD at the next open (trail).`
- **Fill:** `PAPER filled: BUY XBTUSD 0.000527 at 62,661.65 (open 62,412.00 + 0.4%), 33.02 spent.`
- **Closed trade:** `PAPER closed: XBTUSD +2.31R (+8.4%), held 18 days.`
- **Weekly summary:** equity vs the start's 100, BTC buy-and-hold over the same days (bought at the start day's close), open positions, closed trades so far, worst drawdown so far vs `max_drawdown`, late days so far vs `max_late_days`, failed runs in the last 7 days, and days until the review.
- **Alert:** `PAPER ALERT: <error>` for a failed run, including a replay mismatch or a changed strategy file.

## Commands (`signalbench crypto paper ...`)

- `start`: reads the paper file (committed and unchanged), checks the strategy fingerprint, creates the `paper_portfolios` row, creates the two crypto tickers if missing, and loads Kraken's last ~720 daily candles for both. Refuses if the portfolio already exists, or if `last_complete_day(now)` is on or after `start` (the start day must not have closed yet).
- `run`: the nightly job above.
- `status`: prints the weekly summary and the last successful run's time.
- `review`: refuses before the review date. On or after it, prints the three checks and **on track** or **off track** with reasons.

## On botbox

- Checkout at `~/paper/macrocite` on tag `paper-crypto-v1` (pushed to GitHub so botbox can fetch it), with its own uv venv. The live checkout `~/macrocite` on `live-v2` is not changed.
- `~/paper/config/.env`: `DATABASE_URL` pointing at `signalbench_paper`, plus the existing Telegram token and chat id.
- `CREATE DATABASE signalbench_paper` in `signalbench-postgres-1`, then `alembic upgrade head` from the paper checkout.
- Systemd user units: `signalbench-paper.service` + `signalbench-paper.timer` (00:30, 06:30, 12:30 UTC, `Persistent=true`), and `signalbench-paper-backup.service` + `.timer` (daily `pg_dump` of `signalbench_paper` to `~/paper/backups/`, 14 days kept). The live bot, scan, and backup units are not edited or restarted.
- Code updates: move the paper checkout to a new tag only after the change is merged; the next run's replay check proves the saved history still comes out the same.

## Failure handling

- **Kraken down, a bad response, a missing candle, a gap:** the run fails, saves nothing, and alerts; the next timer slot retries. A day nobody records within 24 hours counts as late.
- **Replay mismatch, changed strategy or paper file:** fails, saves nothing, alerts, and keeps failing until the owner decides. The decision is recorded in this spec's changelog.
- **Telegram down:** data is saved; `messages_failed` records it and the weekly summary shows the count.
- **botbox or the timer down:** the job cannot report its own absence. A missing Monday summary is the signal; `status` shows the last successful run. The live bot is not changed to watch it.
- **Concurrent runs:** the second exits quietly at the lock.

## Testing

In-memory SQLite (the advisory lock is faked as in the live scan's tests), Kraken through `httpx.MockTransport`, Telegram through `FakeMessenger`.

- **Key invariant:** running `paper run` night by night over a stretch of fake candles stores exactly the trades, events, orders, and equity of one replay over the same days, and those match `simulate()` over the same days.
- A second run on the same night adds nothing and sends nothing.
- A saved row changed behind the job's back gives a replay mismatch, saves nothing, and alerts.
- A changed strategy file, a fingerprint mismatch, a missing candle, a gap over 3 days, and a Kraken error each refuse the run, save nothing, and alert.
- A Telegram failure still saves the days and counts `messages_failed`.
- `start` refuses a second portfolio and a start day that has already closed.
- Late-day counting, the drawdown, and the review verdict on hand-built examples; `review` refuses before the review date.
- Message text for an order, a fill, a closed trade, the weekly summary, and an alert.
- The weekly summary is sent once per ISO week.
- The migration test expects head `0018_paper_crypto`.

## Order of work

1. Build on branch `feat/swing-11-crypto-paper` (migration, paper file loader, replay and compare, append, messages, commands, tests). Merge to `main`.
2. Commit `data/paper_crypto-v1.yaml` on its own, with `start` set to the first full UTC day after setup will be done.
3. Tag `paper-crypto-v1`, push the tag, and set up botbox (database, checkout, env file, migration, units).
4. Run `crypto paper start` before the start day closes; enable the timers.
5. Check the first night's run, then log the start in `docs/research-log.md`.
6. At the review date, run `crypto paper review` and log the verdict.

## Out of scope

Real money and Kraken's trading API; spec 10; any change to the live bot, scan, or their database; monitoring that the job is alive; reviving spec 07's saved-state runner.

## Changelog

- 2026-10-11: written after the owner's decisions above.
