# Research log

One entry per experiment, formal or rough, newest last. Each entry says what was tested, whether the rules were fixed in advance, the result, and the takeaway. Record every test, including rough ones and ones that go nowhere: the count of what was tried is what keeps a lucky pass from looking like an edge.

**Kinds:**
- **Formal:** rules fixed and committed before the run; the result decides something.
- **Post-hoc:** run on data already seen; it can't overturn a formal result on its own.
- **Rough:** a quick estimate to decide whether something is worth building; information only.

Formal reports live in `reports/`; decisions in the owner's words live in the overview changelog (`docs/superpowers/specs/2026-09-22-swing-assistant-00-overview.md`). Stored runs and Jev readings are in the local database (backup: `C:\Users\samin\Documents\signalbench-backups\`).

---

## 2026-09-23 — Data foundation gate (spec 01)

- **Kind:** formal (gate check, not a strategy test).
- **Result:** 40 CDRs in the universe, all priced; 235,402 price rows; 5,082 8-Ks since 2016 (2,934 with exhibit text); 82,761 news rows; 1,702 earnings events. A second `ingest all` added nothing.
- **Takeaway:** the data is complete enough to backtest on.

## 2026-09-24 — CDR spread survey

- **Kind:** formal input (it sets the backtest cost).
- **Test:** Yahoo bid/ask for the CDRs every 30 minutes, 10:00–15:30 ET, on one session (329 readings, 38 CDRs).
- **Result:** median spread 0.106%, so `cost_per_side` is the 0.2% floor. After-close quotes were much wider (about 1.2%).
- **Takeaway:** trading costs are small during regular hours; place orders then, not after the close.

## 2026-09-24 — Pullback, v1, Jev off (spec 02)

- **Kind:** formal (pre-registered in `data/strategy_v1.yaml`, sha `98037e0b…`).
- **Test:** buy an RSI(2) < 10 dip in an uptrend, 2R target, 10-session limit; 2012–2026.
- **Result:** FAIL. 908 trades, mean R −0.071 (worse half −0.076), Sharpe −0.19 vs QQQ 1.00, total return −37.6%, exposure 67%.
- **Report:** `reports/backtests/2026-09-24-pullback-off.md`.
- **Takeaway:** buying short dips in these names lost money after costs.

## 2026-09-24 — Breakout, v1, Jev off (spec 02)

- **Kind:** formal (same pre-registration).
- **Test:** buy a 20-session closing high on 1.5× volume, 3-ATR trailing stop, 30-session limit; 2012–2026.
- **Result:** FAIL on Sharpe only. 419 trades, mean R +0.311 (worse half +0.265), Sharpe 0.949 vs QQQ 0.996, total return 610.6% (CAGR 14.2%) vs QQQ 1,376%, exposure 87%.
- **Report:** `reports/backtests/2026-09-24-breakout-off.md`.
- **Takeaway:** the closest thing to an edge found so far: good trades, but it still trailed buy-and-hold QQQ. Owner: "no-go on both".

## 2026-09-24 — Dry run with random Jev readings (rough, while planning spec 03)

- **Kind:** rough (on a throwaway database copy, fake random readings).
- **Result:** a Jev-filtered Breakout run moved Sharpe from 0.949 to 0.998, just over QQQ's 0.996, on pure noise.
- **Takeaway:** a near-miss criterion can flip by chance, which is why filtered runs are labeled information only.

## 2026-09-28 — Jev reading of filings and news (spec 03)

- **Kind:** formal data step.
- **Result:** one build, `typesafe/jev-1.13-20260917`. 80,208 news items read ($2.48); 28,295 more were over the 20-per-symbol-per-day cap.
- **Filings, first pass (superseded):** 50 filings failed as too long for Jev's 32K-token limit, including all 43 JPM earnings 8-Ks, because their financial tables are dense numbers. The owner chose to drop tables and dates from filing text. All filings were refetched and re-read: 5,110 of 5,115 read ($0.98); 5 PDF-style filings still too long.
- **Total spent:** $4.74.
- **Takeaway:** strip tables and dates from filings before sending them to a model; tables cost tokens and add nothing a tone reader can use.

## 2026-09-28 — Jev filter decision (spec 03)

- **Kind:** formal (pre-registered rule; `data/jev_filter_v1.yaml`, written once).
- **Test:** block a Pullback or Breakout entry after a document with `p_negative` ≥ θ in the last 10 sessions. Fit θ on 2016–2022 trades, confirm on 2023–2026.
- **Result:** information only; nothing is blocked. At every θ the trades that followed negative news did *better* than the rest (fit: blocked +0.14 to +0.38 R vs kept about −0.02 R). The rule picked θ 0.7; on confirmation the 80 blocked trades averaged +0.227 R vs −0.007 R kept.
- **Report:** `reports/jev/2026-09-28-filter-decision.md`.
- **Takeaway:** skipping trades after bad news would have cut the best trades. Likely reasons: the news is priced in before our entry, prices move on surprise rather than tone, and Pullback's dip-buys in strong names often bounce.

## 2026-09-28 — Sentiment, v1, Jev readings as trigger (spec 03)

- **Kind:** formal (committed v1 config; only the dates were set by spec 03's rules).
- **Test:** buy after a positive Jev reading in the last 3 sessions, with close above SMA20 and above the prior high; 2R target, 10-session limit; 2016–2026.
- **Result:** FAIL. 409 trades, mean R 0.098 vs the > 0.100 bar, halves 0.114 / 0.082, Sharpe 0.58 vs QQQ 0.95, total return 88.3% vs QQQ 630%, exposure 66%.
- **Exits:** 273 on time (+0.27 R), 82 stopped (−1.22 R), 30 earnings (+0.19 R), 24 at target (+2.48 R).
- **Report:** `reports/backtests/2026-09-28-sentiment-off.md`.
- **Takeaway:** small wins, full-size losses, and a third of the money idle. The edge that exists probably comes from short-term momentum, not from Jev (untested).

## 2026-09-28 — Jev calibration (spec 03)

- **Kind:** formal report (information only; nothing adjusted).
- **Test:** 82,367 readings labeled by the stock's return minus QQQ's over the 5 sessions after the legal close (above +2% up, below −2% down).
- **Result:** the rate of `up` outcomes is about 29% in every `p_positive` decile, and `down` about 35% in every `p_negative` decile. Argmax accuracy 34.1% vs 35.6% for always guessing "flat". ECE 0.31 positive, 0.33 negative. Only 1,559 readings fall after Jev's release, too few to judge.
- **Report:** `reports/jev/2026-09-28-calibration.md`.
- **Takeaway:** Jev's reading of public filings and news carries no information about the next week's move in these widely followed stocks.
- **Owner's decision:** "Jev filter information only; Sentiment FAIL, scrapped; specs 04–05 stay on hold."

## Open questions (not yet tested)

- **Idle cash:** does holding QQQ with idle cash close Breakout's Sharpe gap? All three setups sat partly in cash while QQQ compounded.
- **Slower momentum:** monthly rotation into the strongest names, holding weeks to months.
- **Fewer constraints on Breakout:** more slots, no regime pause.
- **Jev on other horizons or stocks:** longer holding periods, or less-followed stocks.
- **Caution:** every one of these reuses 2012–2026 data that has already been seen. A pass needs forward paper trading or unseen data before real money.
