# SignalBench paper book — v1.1

**Frozen:** 2026-08-22  
**Status:** rules locked for the freeze window. Do not edit entry, exit, size, universe, or the go/no-go sentence after results exist.  
**Not investment advice.** Paper only. SignalBench does not place orders.

---

## Freeze

Do not change entry / exit / size / universe for **8 weeks or 20 closed taken trades**, whichever is **later**.

**Not in this run:** vol-adjusted sizing, trailing stops / take-profit, adding a 9th trade-list name. If sample size is short at week 8, **extend the calendar**. Do not widen the list.

## Trade list (frozen)

`NVDA`, `MSFT`, `AAPL`, `GOOGL`, `AMZN`, `META`, `AVGO`, `NFLX`

YAML watchlist stays at 20 for **reading**. A signal on any other name is skip-only (`skip_reason=not_on_trade_list`).

## Pre-registered edge (do not edit after you have results)

I'll consider the signal worth continuing if **taken-trade average return exceeds the random-entry baseline by ≥ 2 percentage points**, with **≥ 20 taken trades**.

- Return = position P&L in percent, entry `adj_close` → exit `adj_close`.
- Baseline = **`tag=control`** rows (random-entry), not skip-shadows.
- Skip-shadows (`tag=shadow`) are **secondary**: they tell you whether discretion helped. They do **not** decide go/no-go.
- If the primary test fails: keep using SignalBench as a research console. Do not loosen rules to “make it pass.”

## Account

- $1,000 paper notional. Cash. No margin, options, shorts, crypto, or leverage.
- **One** real (`taken`) position at a time. Shadows and controls do not occupy the slot.
- Size: **25% of paper cash** at entry (~$250 at start). Use that same notional on every shadow/control row logged that day (measurement only).

## Entry checklist (`taken`)

All must hold:

1. Signal on the **trade list**
2. `sentiment > 0.5`
3. `confidence ≥ 0.6`
4. `event_type` ∈ `earnings` | `guidance` | `leadership` | `legal` | `product` (not `other`, not `macro`)
5. You read the rationale / 8-K and still agree
6. No taken position already open
7. Not in a kill-switch pause

Otherwise **skip** and still log the shadow the same day.

## Same-session tie-break

Two or more eligible names, one slot: **higher `confidence`**, then **higher `sentiment`**, then **A–Z symbol**.

The loser is a skip (`skip_reason=tie_break`) and gets a `shadow` row. Do not improvise.

## Fill — T (no look-ahead)

A **session** = one regular US cash equity session (one official close). Weekends and market holidays do not count.

**Do not use `raw_documents.published_at` as the clock.** Ingest currently stores SEC `filingDate` at `00:00 UTC`, not acceptance time. After-hours 8-Ks can look knowable at that day’s close. Get **acceptance datetime** from EDGAR (`acceptanceDateTime` / acceptance on the filing).

**Earliest legal close** = the first session **close that occurs after** acceptance (America/New_York):

- Accepted **before** 16:00 ET on a session day → that day’s close is information-legal.
- Accepted **at or after** 16:00 ET, or when the market is shut → **next** session’s close.

**Human lag** (evening ingest): if that legal close has **already printed** when you log take/skip, **do not backfill it**. Slide T to the **next** session.

T = max(first close after acceptance, first close after you logged the decision).

**T+5** = the fifth **session** after T, not five calendar days. Settlement T+1 is unrelated.

## Exit (`taken`, `control`, and `shadow`)

Whichever hits first. Do not skip an exit.

| Exit | Rule |
| --- | --- |
| Time | Close of **T+5** |
| Stop | That session’s `adj_close` ≤ entry × **0.97** (−3%) |
| Target | **None** |

If price gaps through −3%, exit at that **close**. This book does not assume an intraday broker stop.

## Measurement layer

Every signal you look at produces **at least one row**. Taken trades produce **two**.

### A. Taken — `tag=taken`

Normal journal row. Occupies the one-position slot.

### B. Random-entry control — `tag=control` (only when you take)

Logged **the same minute** as the take, **before** later closes are known.

1. Write `k` = integer **1–10** immediately (`python -c "import random; print(random.randint(1,10))"`).
2. Same ticker as the take.
3. Control T = close of the **k-th following session** that has a price and **no** eligible signal on that ticker (`sentiment > 0.5` and the trade-list filters). If a session is a signal day, skip it and keep counting toward `k`.
4. Same 25% notional, same T+5 / −3% exits.

Only difference vs taken: **entry timing** (signal vs random no-signal session).

### C. Skip shadow — `tag=shadow` (every skip, as it happens)

Same T / exit math as if you had taken it. Includes: not on trade list, low confidence, `other` / `macro`, you disagreed, slot full, tie-break loser, kill-switch pause.

If you did not log it that day, it does not count in the skip analysis. Do not reconstruct at week 8.

## Kill switch + sector flag

- After a **taken** loss: no new **taken** entry that session (still log shadows / controls).
- **Three taken losses in a row:** no new takens for **one week**. Still manage rows already open.

The eight names are highly correlated mega-cap tech. A pause can be one bad sector week, not a dead extractor.

On every **taken** exit, log:

- `qqq_from_entry_pct` — QQQ total return over the same T → exit window (or yes/no: QQQ down over this hold?)
- `loss_with_tech_tape` — true if this was a loss **and** QQQ was down over that window

At review, split taken P&L by `loss_with_tech_tape`. Do not diversify mid-freeze to “fix” correlation.

## Daily ritual

1. `uv run signalbench ingest filings`
2. `uv run signalbench ingest prices`
3. `uv run signalbench extract --llm together`
4. For each new signal: checklist → take or skip.
5. If take: tie-break if needed → write **`k` immediately** → log `taken` + `control`.
6. If skip: log `shadow` the same day (same T rule).
7. Update open taken / control / shadow for stop and T+5.
8. On taken exits, fill QQQ / tech-tape fields.

## Journal columns

`date_seen | tag (taken/control/shadow) | signal_id | symbol | acceptance_at_et | published_at_pipeline | k | skip_reason | sentiment | event_type | confidence | T | entry_adj_close | size_$ | planned_T+5 | exit_date | exit_reason | exit_adj_close | pnl_pct | qqq_from_entry_pct | loss_with_tech_tape | notes`

- `k` required on `control`; empty on taken/shadow.
- `signal_id` empty only on `control`.
- Pipeline `published_at` is audit-only; **T always comes from acceptance + log time.**

## Parking list (recs)

Not tradable. After 30 days on YAML and two filings you have actually read, a name may **swap one** trade-list ticker **after** the freeze — not during, and not as a 9th name.

## Review (only after freeze)

1. Mean `pnl_pct` of `taken` vs mean `pnl_pct` of `control` (n ≥ 20 taken). **Pass** if taken ≥ control + 2pp.
2. Informational: taken vs `shadow`.
3. Informational: taken losses with `loss_with_tech_tape` vs without.

---

**Unchanged math:** 25% size, one slot, T+5 or −3%, these eight names.  
**Measurement:** random-entry control, live skip shadows, pre-registered 2pp test, tie-break, acceptance-time clock, tech-tape flag.
