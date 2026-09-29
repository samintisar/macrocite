# Spec 06 — Breakout v2: idle cash in QQQ and holding time

**Parent:** [Overview](2026-09-22-swing-assistant-00-overview.md) · **Depends on:** spec 02 (simulator, pre-registration guard) and spec 03 (branch `feat/swing-03-jev-reader`, not merged yet).
**Goal:** Test two ideas on Breakout, all six combinations pre-registered and reported: (1) idle cash held in QQQ instead of cash, and (2) a longer or no time limit. The results are **post-hoc** (2012–2026 has been seen) and decide nothing on their own: every variant, pass or fail, then goes to forward paper trading (the next spec).

## Why these two

From `docs/research-log.md`:

- **Idle cash.** Breakout v1 had good trades (mean R +0.311, both halves positive) but missed QQQ's Sharpe (0.949 vs 0.996). It held cash 33% of the time while QQQ compounded. A rough overlay that let idle cash earn QQQ's return gave Sharpe 0.991 at 0.2% per switch and 1.103 at 0.05% (information only, positions not resized).
- **Holding time.** Breakout's 139 trades that hit the 30-session limit averaged +1.86 R, against −0.80 R for the 214 stopped out: the clock closed the best trades. Trends often last months.

Both ideas have a reason of their own besides the numbers. The variants are fixed here, before any run, and all of them are reported.

## Variants (pre-registered)

Each variant is its own config, a copy of `data/strategy_v1.yaml` with only the fields below changed, committed in one commit before any real run. The existing guard applies unchanged: the config must be `data/strategy_<version>.yaml`, committed and unchanged, with `cost_per_side` equal to the survey's (0.2%).

| Config | `version` | Breakout `time_limit` | `cash_vehicle` |
| --- | --- | --- | --- |
| `data/strategy_v2-t30-cash.yaml` | `v2-t30-cash` | 30 | none |
| `data/strategy_v2-t30-qqq.yaml` | `v2-t30-qqq` | 30 | QQQ |
| `data/strategy_v2-t60-cash.yaml` | `v2-t60-cash` | 60 | none |
| `data/strategy_v2-t60-qqq.yaml` | `v2-t60-qqq` | 60 | QQQ |
| `data/strategy_v2-none-cash.yaml` | `v2-none-cash` | `null` | none |
| `data/strategy_v2-none-qqq.yaml` | `v2-none-qqq` | `null` | QQQ |

- Every other field is v1's: entry rules, stops, the 3-ATR trailing stop, 2% risk, 3 slots, 2 per sector, the 15% drawdown pause, the QQQ 200-day regime rule for new entries, the earnings blackout and exit, the 0.2% CDR cost, the backtest period and halves. The runs use `--setup breakout`, so Pullback and Sentiment never fire.
- `cash_vehicle` (QQQ variants only): `{symbol: QQQ, cost_per_side: 0.002}`. The 0.2% is the CDR cost rule, fixed before any v2 result (owner decision, 2026-09-28). QQQ's adjusted prices stand in for the fund actually used, a CAD-listed, CAD-hedged Nasdaq-100 ETF, the same way US prices stand in for the hedged CDRs.
- Idle cash is always in QQQ, including when QQQ is below its 200-day average (owner decision). A regime rule for the idle cash is a separate, later experiment.
- `v2-t30-cash` is v1 Breakout exactly. Its run must reproduce the stored v1 Breakout run (419 trades, total return 610.6%, Sharpe 0.949); any difference is a bug.
- `time_limit: null` is accepted for Breakout only; such trades exit only on the trailing stop, the earnings exit, or the end of the run (open at the end, as today).

## Simulator: the cash vehicle

For a config with `cash_vehicle`, each session:

1. **Open, exits:** exit orders fill at the open as today; proceeds go to cash.
2. **Open, fund entries:** entries are sized as today (below). If an entry's cost exceeds cash, QQQ is sold at QQQ's open, just enough to cover it, paying `cash_vehicle.cost_per_side` on the amount sold.
3. **Open, park the rest:** any cash left after the day's fills buys QQQ at its open, paying the same cost on the amount bought. Nothing is traded on a day with no exits or entries, so costs apply only to money that moves.
4. **Close:** the QQQ holding is valued at QQQ's close. Equity = cash + QQQ value + positions (as today).

- **Sizing** (2% risk, the equity/3 cap) uses total equity including QQQ. The cash cap counts cash plus QQQ at the open, net of the switching cost.
- **Drawdown pause** uses total equity, as v1's rule is written: a large QQQ fall can pause new Breakout entries.
- **Look-ahead:** QQQ trades only at the open or close of the session being simulated, the same rule as every stock. `MarketView` already serves QQQ bars (the regime symbol).
- **Events:** each QQQ buy or sell is logged as a `vehicle_buy` / `vehicle_sell` event with the amount and cost.
- Configs without `cash_vehicle` skip steps 2–3 entirely, so v1 and the `-cash` variants behave byte for byte as before.

## Pass bar and reports

- **Pass bar, unchanged from v1:** at least 30 trades; mean R after costs above 0.10; mean R above 0 in both halves (entries to 2018-12-31, from 2019-01-01); Sharpe of total equity (QQQ holding included) at least QQQ buy-and-hold's over the same sessions. R is still measured per Breakout trade on planned risk.
- **Every report is labeled POST-HOC** (the report already does this for any version other than v1). A PASS only means the variant did not fail on the past; nothing goes live from it.
- **Report additions:** average share of equity held in QQQ; stock exposure shown separately; number of QQQ switches and their total cost. For QQQ variants, a **"Sensitivity (information only): QQQ switching at 0.05%"** line with total return, CAGR, Sharpe, and max drawdown: the runner simulates the same config a second time with the sleeve cost at 0.0005. Only the 0.2% run is stored and judged.

## Order of work

1. Build and test the code (no real runs).
2. Commit the six config files together, on their own.
3. Run all six on the real database; commit the six reports.
4. Add one research-log entry with all six (plus v1 Breakout and QQQ) in one table, each PASS or FAIL, and one line in this spec's changelog. The six count as six tries.
5. Next: the paper-trading spec runs all six variants plus v1 Breakout forward, whatever these results are (owner decision). Judged after 12 months **and** at least 30 trades per variant, whichever is later, on the same pass bar over the paper period.

## Testing

- Config: `time_limit: null` parses for Breakout and is refused for Pullback and Sentiment; `cash_vehicle` parses and validates (known symbol, cost in [0, 0.05)); a missing `cash_vehicle` means none.
- Exits: a position with no time limit is never closed for time.
- Simulator, hand-worked fixtures: sell-to-fund covers an entry and charges cost only on the amount sold; leftover cash is parked at the open; no switch on a quiet day; sizing uses total equity; the drawdown pause sees the QQQ value; the equity curve adds the QQQ value.
- Look-ahead test gains a QQQ-variant case.
- Regression: a v1 config (no `cash_vehicle`) gives the same result and fingerprint as before; `v2-t30-cash` reproduces v1 Breakout on the real data (checked in step 3, recorded in the log).

## Caveats (written into each QQQ report)

- Post-hoc: the ideas came from looking at v1's results on the same data.
- QQQ's 2012–2026 run was exceptional; holding more QQQ helps less or hurts if the next decade differs.
- Taxes are not modeled. In a non-registered account each QQQ switch is a disposition, and selling at a loss and rebuying within 30 days can be a superficial loss. Fractional units of the ETF are assumed.

## Out of scope

Paper trading (next spec), other setups, a regime rule for the idle cash, and any change to Breakout's entry rules.

## Changelog

- 2026-09-28: created (owner decisions: all six variants, 0.2% switching with 0.05% reported alongside, idle cash always in QQQ, all seven portfolios paper-traded whatever the backtest shows).
- 2026-09-28: implementation choices (plan `2026-09-28-swing-06-breakout-v2.md`):
  - `cash_vehicle.symbol` must be the regime symbol (QQQ), the only series besides the universe that a run loads; its cost must be in [0, 0.05). `time_limit: null` must be written out (the key stays required) and only Breakout accepts it. The `-cash` configs have no `cash_vehicle` key.
  - The start equity is parked in QQQ at the first session's open (the spec is silent on the start). After that QQQ trades only on sessions with an exit or entry fill, at most once per session: the day's net need, at QQQ's open, filled like a stock (open × (1 − cost) when selling, open × (1 + cost) when buying), so the cost falls only on the amount that moves. On a session without a QQQ bar the sleeve does not trade.
  - `decide()` sizes on total equity, with a cash cap of cash plus QQQ at the close, net of the selling cost; the open-time cash cap uses QQQ at the open, net of the same cost.
  - Reports of versions other than v1 are named `<date>-<version>-<setup>-<jev>.md`, and their POST-HOC line adds that a PASS decides nothing. QQQ reports add an "Idle cash in QQQ" section (average shares of equity in QQQ, stocks, and cash; switches; total switching cost), the 0.05% sensitivity line from an unstored rerun, and four caveats: the spec's three plus the CAD-hedged ETF stand-in. A config without `cash_vehicle` stores exactly the v1 metrics keys, events, and fingerprint (pinned by `tests/test_v1_regression.py`).
- 2026-09-28: results. v2-t30-cash reproduced v1 Breakout (419 trades, 610.6%, Sharpe 0.949). v2-t30-qqq PASS (Sharpe 1.005 vs QQQ 0.996; mean R 0.331), v2-t60-cash PASS (0.997; 0.400), v2-t60-qqq PASS (1.020; 0.414), v2-none-cash PASS (1.120; 0.505), v2-none-qqq PASS (1.039; 0.486); v2-t30-cash FAIL (0.949; 0.311). Reports in reports/backtests/, logged in docs/research-log.md. Next: forward paper trading of all six plus v1 Breakout (owner decision).
