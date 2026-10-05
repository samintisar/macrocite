# Swing Assistant — Overview

**Date:** 2026-09-22
**Status:** Design approved in brainstorming; sub-specs 01–05 below. Since 2026-09-29 the live scope is one strategy, `data/strategy_v2-none-cash.yaml` (Breakout, no Jev, no LLM): see the changelog.
**Not investment advice.** SignalBench shows what pre-registered rules say. The owner decides and places every order on Wealthsimple by hand. No broker integration.

## What this is

A personal swing-trading assistant. Every evening after the US close it scans liquid US large caps that have a Canadian Depositary Receipt (CDR), finds rule-based swing setups in code, sizes the trade to a fixed risk, and sends signals to Telegram. Live, that is the single Breakout strategy v2-none-cash (changelog 2026-09-29); Jev and the other setups were tested and are not used. The owner trades CDRs in CAD in a personal non-registered Wealthsimple account and reports fills back to the bot with buttons, which keeps a ledger, P&L, and tax cost base.

## Decisions (locked in brainstorming)

| Topic | Decision |
| --- | --- |
| Signal source | Price setups computed in code. Live: Breakout only, from `data/strategy_v2-none-cash.yaml` (2026-09-29). Jev's filter and trigger roles failed their tests (spec 03) |
| Instrument | US-company CDRs on Cboe Canada (currently BMO CDRs, `Z`-prefixed, e.g. ZNVD), in CAD. $0 commission, no FX fee, hedge cost built into the price. Thinly traded: market makers quote them, so the bid/ask spread matters more than volume |
| Account | Personal non-registered. TFSA is out of scope |
| Risk profile | **B**: 2% of equity at risk per trade, max 3 open positions, max 2 per sector, pause new entries at −15% from equity peak |
| Capital | Start **$100 CAD**. After 10 closed trades, a scale-up check may suggest adding $900 |
| Setups | Tested: Pullback, Breakout, Sentiment+price-confirmation (spec 02/03), and six Breakout v2 variants (spec 06). Live: v2-none-cash only (Breakout, 3-ATR trailing stop, no target, no time limit, idle cash stays cash), an owner decision on a post-hoc result (2026-09-29) |
| Stops | Checked on the daily close; exit at next open. Matches the backtest |
| Universe | Every US-company CDR (~40 names as of 2026-09-22) whose US stock passes a liquidity filter and whose CDR has a price in the last 5 sessions |
| Jev | Tested as a filter, catalyst tag, and Sentiment trigger; information only, and not used live (spec 03, 2026-09-28) |
| Messaging | Telegram bot, long polling, buttons + slash commands |
| Hosting | Owner's Windows PC: Docker Postgres, bot process, Task Scheduler for the evening scan |
| Explanations | A fixed template line with the rule's numbers (spec 05). No LLM (changed 2026-09-29 from Claude Haiku 4.5) |
| Old work | Paper book v1.1 closed with a note. Together extraction, prompts, eval gate, and sentiment backtest removed (kept in git history) |

## Architecture

```text
EVENING SCAN (Task Scheduler, 17:00 New York; live since 2026-09-29: v2-none-cash only)
  ingest ──> prices (US OHLCV + QQQ/SPY, CDR CAD prices)
         ──> earnings dates (SEC 2.02 + Finnhub calendar); split check for held names
  strategy.decide(as_of) ──> Breakout entries, exits, trailing-stop raises
  risk ──> size, caps, pause state (from the ledger)
  explain (template) ──> one line per signal
  bot ──> Telegram: 🟢 entries, ⬆️ stop raises, 🔴 exits, evening summary
OWNER ──> Wealthsimple ──> taps ✅ / ⏭ or /buy /sell ──> ledger
```

One `decide()` function is used by both the backtest and the live scan. There is no separate backtest copy of the strategy.

## Sub-specs and build order

Build one at a time. Each sub-spec gets its own implementation plan, written only after the previous step's gate is green.

| # | Spec | Gate |
| --- | --- | --- |
| 01 | [Data foundation](2026-09-22-swing-assistant-01-data-foundation-design.md) — housekeeping, CDR universe, prices, 8-K upgrade, earnings dates, Finnhub news | Tests green in CI; real ingest run records counts; CDR prices stored for at least 30 CDRs |
| 02 | [Strategy and backtest](2026-09-22-swing-assistant-02-strategy-backtest-design.md) — setups, sizing, simulator, pass-bar report | **🚦 Go/no-go.** If neither Pullback nor Breakout passes, stop and rethink before building 04–05 unless 03's Sentiment setup passes |
| 03 | [Jev reader](2026-09-22-swing-assistant-03-jev-reader-design.md) — client, backfill, filter, Sentiment setup, calibration report | Filter role decided by pre-registered rule; Sentiment setup passes or is information-only |
| 04 | [Ledger](2026-09-22-swing-assistant-04-ledger-design.md) — fills, positions, equity, ACB, stops and splits, scale-up check (v2-none-cash only) | Hand-checked ACB and CDR-split examples pass; equity rebuilt from fills |
| 05 | [Bot and evening scan](2026-09-22-swing-assistant-05-bot-scan-design.md) — Telegram, template explain, scheduler, catch-up (v2-none-cash only) | End-to-end dry run on fixtures; one live evening scan delivered to Telegram; `/deposit 100` recorded |

Specs 06 (Breakout v2 variants) and 07 (forward paper trading: built, never started) came after 03. Specs 04–05 were unlocked on 2026-09-29 for v2-none-cash only (changelog). Go live with $100 after 05, trading only `data/strategy_v2-none-cash.yaml`.

## Shared terms

- **Session**: one regular NYSE trading day. The backtest and signal timing use the NYSE calendar.
- **Legal close** for a document: the first NYSE session close strictly after the document's acceptance or publish time (America/New_York). A document accepted before 16:00 ET on a session day is visible at that day's close. Accepted at or after 16:00, or on a non-session day, it becomes visible at the next session's close.
- **As-of**: `decide(as_of)` may only see prices with date ≤ `as_of` and documents whose legal close ≤ `as_of`.
- **ATR**: 14-session Average True Range (Wilder smoothing) on adjusted OHLC.
- **R**: the per-unit risk planned at the signal, `signal close − stop`. A trade's R is its P&L after costs divided by units × R. A +2R exit gains twice what the planned stop would lose.
- **Equity peak**: the highest end-of-day equity seen so far.

## Spread survey (required before the first real backtest)

On 2026-09-22 the median CDR traded value was tiny (ZNVD about C$15k/day, ZMSF about C$1.5k, ZAAP zero volume on 20 of 20 sessions). Fills come from market-maker quotes, so cost depends on the bid/ask spread, and no free data source provides it. Before `strategy_v1.yaml` is committed (spec 02), the owner records bid/ask for at least 5 CDRs during market hours in `data/cdr_spread_survey.yaml`. That sets the backtest cost and the live spread limit. If the median spread is above 1%, stop and revisit the instrument choice before running any backtest.

## Out of scope

- Broker APIs or automated order placement
- Options, margin, shorting, crypto
- TFSA tracking
- Intraday data, intraday stops
- Plain-English trade entry via an LLM (possible later)
- Cloud hosting (possible later; everything is configured through `.env` and Docker so the move is small)
- Automatic threshold or parameter changes. Every rule change is a human decision recorded in git

## Supersedes

- `docs/personal/paper-trading-playbook.md` v1.1 (closed by spec 01)
- `docs/superpowers/plans/2026-08-19-phase-4-dashboard.md` and `2026-08-19-phase-5-stretch.md`. Not built; replaced by this design

## Changelog

- 2026-09-22: created.
- 2026-09-22: CDR findings. The universe is BMO `Z`-prefixed CDRs (~40 US names), and yfinance serves them as `<SYMBOL>.NE`. CDR volume is too thin for a traded-value filter, so a spread survey was added as a spec 02 prerequisite, and CDR liquidity became "priced in the last 5 sessions".
- 2026-09-24: R is the risk planned at the signal (`signal close − stop`), not `entry fill − stop` (owner decision, spec 02 changelog).
- 2026-09-24: spread survey done. Yahoo quotes for all 40 CDRs, sampled every 30 minutes from 10:00 to 15:30 ET on 2026-09-24, gave a median spread of 0.106%, so `cost_per_side` is the 0.2% floor (`data/cdr_spread_survey.yaml`). The owner didn't type readings from Wealthsimple. Quotes after the close are much wider (about 1.2%), so orders belong in regular hours.
- 2026-09-24: spec 02 go/no-go. Pullback FAIL (mean R −0.071, Sharpe −0.19). Breakout FAIL on Sharpe only (mean R +0.311, both halves positive, Sharpe 0.949 vs QQQ 0.996). Live setups: none. Owner's decision: "no-go on both". Per the spec 02 gate, specs 04–05 wait unless spec 03's Sentiment setup passes; spec 03 can also test Breakout with the Jev filter as a pre-registered run.
- 2026-09-28: spec 03 gate. Jev filter information only (theta_fit 0.7; blocked trades did better than kept ones). Sentiment FAIL (409 trades, mean R 0.098 vs > 0.100, Sharpe 0.58 vs QQQ 0.95). Calibration: Jev readings do not predict 5-session excess returns. Live setups: none. Owner's decision: "Jev filter information only; Sentiment FAIL, scrapped; specs 04–05 stay on hold."
- 2026-09-29: go-live decision on a post-hoc result (spec 02 rule: an explicit owner decision). Spec 06 Breakout v2 variants were backtested post-hoc on 2012–2026 (five of six passed; `docs/research-log.md`). The owner chose `data/strategy_v2-none-cash.yaml` (Breakout, no time limit, idle cash stays cash; Sharpe 1.12 vs QQQ 0.996, total return 1,029% vs 1,376%, worst drop 26%) and dropped forward paper trading (spec 07 built, not started). Owner's words: "You know what, I feel like scrapping the nightly experiments because I don't think they're gonna add any value" and "Okay, let's just do v2 non-cash".
- 2026-09-29: specs 04–05 unlocked and revised for v2-none-cash only: one frozen config (`live_config` sha check), Breakout with the trailing stop, no Jev and no LLM (a template Why line), a split-safe ledger, and the scan run from a pinned worktree. Decision-table rows Signal source, Setups, Jev, and Explanations, the architecture sketch, and the build-order note updated to match; the rest of the table stands.
- 2026-09-30: unused tables removed. Owner's words: "I guess you can also get rid of some of the tables that are not going to be used since we have uh, changed and updated the plans." The paper-trading code and its four tables (migration `0012`, never applied to the real database) are gone. The one-off `backup_eight_k_text_20260928` table (not from a migration) is for the owner to drop; its data is in the 2026-09-28 dump. Kept: `raw_documents` and `document_tickers` (the scan's earnings sync reads them), `jev_readings` and `backtest_runs` (the research log's evidence).
