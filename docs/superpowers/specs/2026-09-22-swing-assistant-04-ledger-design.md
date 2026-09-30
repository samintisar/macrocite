# Spec 04 — Ledger

**Parent:** [Overview](2026-09-22-swing-assistant-00-overview.md) · **Depends on:** spec 01 (CDR tickers, CDR prices), spec 02 (`decide()`, `PortfolioState`), spec 06 (`data/strategy_v2-none-cash.yaml` and `time_limit: null`), and code first written for spec 07 (split detection and the earnings-calendar ingest; paper trading itself was never started and was removed on 2026-09-30).
**Live strategy:** `data/strategy_v2-none-cash.yaml`, Breakout only. It is the one strategy traded live (owner decision, overview changelog 2026-09-29).
**Goal:** Record what the owner actually did on Wealthsimple. From that alone, derive positions, cash, equity, peak, the pause state, each managed position's stop, the scale-up check, and a CRA-style cost base (ACB) report. Nothing here is typed in as a total. Every number is computed from fills, cash movements, and corporate actions.

**Not tax advice.** The ACB, superficial-loss, and split handling follow the CRA's published method as a record-keeping aid. The owner verifies their tax return independently.

## Live config

- The live config is `data/strategy_v2-none-cash.yaml`, committed and never edited. It runs with Breakout only (`config.with_setups(("breakout",))`) and no Jev readings (`NullReadingsView`: nothing is blocked, and nothing is a catalyst).
- What it means live:
  - **Entry:** the close is above the prior 20-session closing high and the 50-session average, on at least 1.5× the prior 50-session mean volume. The QQQ 200-day regime rule and the 3-session earnings blackout apply.
  - **Stops:** an initial stop 2 ATR below the signal close, then a 3-ATR trailing stop that only ratchets up.
  - **Exits:** the stop, or the earnings exit (2 sessions). There is no target and no time limit.
  - **Risk:** 2% risk, 3 slots, 2 per sector, and new entries pause at −15% from the peak.
  - **Cash:** idle cash stays cash (no `cash_vehicle`).
- `auto_resume_sessions` is the backtest's stand-in for `/resume`, and live ignores it: only `/resume` resumes (spec 02).
- **`live_config`** (single row): config_path, config_sha256, started_on, start_git_sha, created_at.
  - `signalbench live start` writes it once. It runs the backtest guard: the file is `data/strategy_<version>.yaml`, committed and unchanged, with `cost_per_side` equal to the survey's. It refuses if the row already exists.
  - The evening scan (spec 05) refuses to run when the file's sha256 differs from the stored one.
  - Trading a different config would be a new owner decision and is out of scope here.

## Tables (migration `0013_live_ledger`)

`0011` (Jev readings) is the last migration before this one; `0012` (paper trading) was removed on 2026-09-30 without ever being applied, so `0013` revises `0011`. Spec 05's `scan_runs` is migration `0014`.

All money is CAD, stored as `Numeric`, and computed with `Decimal`. US-equivalent levels are USD, stored as `Numeric(12, 4)`.

| Table | Columns |
| --- | --- |
| `live_config` | single row: config_path, config_sha256, started_on, start_git_sha, created_at (above) |
| `cash_movements` | id, amount_cad (+ deposit / − withdrawal), occurred_on, note |
| `trade_signals` | id, as_of, us_symbol, cdr_ticker_id, setup (always `breakout`; the column stays for clarity), us_signal_close, us_stop, stop_pct, cdr_signal_close, cdr_stop, suggested_units, order_type (`limit`/`market`), risk_amount_cad, explanation (template text, spec 05), status (`sent`/`taken`/`skipped`/`expired`/`withdrawn`), skip_reason (`disagree`/`no_time`/`price_moved`/`wide_spread`/`other`/null), telegram_message_id, expires_at, created_at. Unique (`as_of`, `us_symbol`, `setup`) |
| `exit_alerts` | id, signal_id (FK: the signal that opened the position), cdr_ticker_id, as_of, reason (`stop`/`earnings`), late (bool), status (`sent`/`done`/`ignored`), telegram_message_id, created_at |
| `stop_updates` | id, signal_id (FK: the signal that opened the managed position), session, reason (`trail`/`split`), old_us_stop, new_us_stop, old_cdr_stop, new_cdr_stop, late (bool), telegram_message_id, created_at. Unique (`signal_id`, `session`) among `trail` rows |
| `corporate_actions` | id, kind (`us_split`/`cdr_split`), us_symbol (US splits) or cdr_ticker_id (CDR splits), ex_date, ratio (new units per old unit, `Numeric(12, 6)`), source (`yfinance`/`owner`), voided (bool), void_reason, telegram_message_id, created_at. Unique (`kind`, symbol, `ex_date`) among non-voided rows |
| `fills` | id, cdr_ticker_id, side (`buy`/`sell`), quantity (18,6), price_cad (12,4), fee_cad (default 0), trade_date, signal_id (FK nullable), exit_alert_id (FK nullable), voided (bool), void_reason, created_at |
| `equity_snapshots` | date (PK), cash, positions_value, equity, peak |
| `risk_state` | single row: paused, paused_at, paused_reason, resumed_at, peak_reset_on |

- `trade_signals` rows are written by the evening scan (spec 05) when a signal is sent.
  - The old `catalyst` and `jev_status` columns are gone.
  - `time_limit` is dropped too: the live config pins it to `null`, and nothing reads it. The config's sha is recorded in `live_config`.
- A signal expires at the close of the entry session (next open's session) if it hasn't been taken.
- **Fills are never deleted.** A mistake is corrected with `void` plus a new fill.
- `stop_updates` and `corporate_actions` are append-only. A wrong corporate action is voided, never deleted or edited.

## Derivations

**Cash** = Σ cash_movements + Σ sell proceeds − Σ buy costs − Σ fees, over non-voided fills. Corporate actions never change cash.

**Positions** are grouped by CDR:
- **Open quantity:** buys and sells are replayed in trade-date order. A non-voided `cdr_split` multiplies the units held at its ex-date by its ratio. A fill dated on or after the ex-date is already in post-split units.
- **ACB:** the CRA average cost (below).
- **Managed:** the buy that opened the position (quantity going from 0 to above 0) is linked to a `trade_signal`, and that signal identifies the position. Managed positions get strategy exits and stop raises.
- **Manual:** opened with `/buy` without a signal. Manual positions count toward equity, slots, and the sector cap, but get no stop, stop-raise, or earnings alerts. They are marked "manual" everywhere.

**Strategy levels for a managed position.** Exits and stops are evaluated on the US series, the same as the backtest (spec 02):
- **Entry session:** the first NYSE session on or after the opening buy's trade date. It is session 1 of "sessions held", which is shown but no longer triggers anything.
- **Initial stop:** the signal's `us_stop`.
- **Current stop:** `new_us_stop` of the latest `stop_updates` row for the position, or the initial stop if there is none. A `trail` row only ever raises it. A `split` row rescales it (below).
- **Highest close:** the highest stored US close from the entry session through the session being decided. It is recomputed from the stored bars every night and never saved, so it is always on the stored prices' scale.
- **Timing (the backtest's rule):** at the close of session D, check the exit against the stop in force during D first. If there's no exit, the trailing stop is `max(current stop, highest close through D − trail_atr_mult × ATR(D))`, with `trail_atr_mult` = 3.0.
  - A rise writes a `trail` row with `session = D`.
  - The new stop is in force from D+1's close.
  - A position with an exit on D gets no raise.
- **Exits, first match:**
  1. `stop`: the US close ≤ the stop in force.
  2. `earnings`: an earnings date falls within the next `exit_sessions` (2) sessions.
  - There is no target and no time exit.
  - Earnings dates are the union of the SEC 2.02 dates and the stored Finnhub calendar dates, upcoming ones included. This is spec 07's `calendar_earnings=True` path, refreshed each evening by the calendar ingest (spec 05). The entry blackout uses the same dates.
- **One open exit alert per position:** while an exit alert is `sent`, the position gets no new exit alert and no stop raise, because it is due to be sold at the next open, as in the backtest. The evening summary repeats the alert until it is `done` or `ignored`. After `ignored`, the position is managed again from the next session.
- **US-equivalent entry (display only):** `entry_us = us_signal_close × (cdr_fill_price / cdr_signal_close)`, using the opening buy's price.
- **CDR display levels:**
  - The initial `cdr_stop = cdr_signal_close × (1 − stop_pct)`.
  - For a raise on session D, `new_cdr_stop = new_us_stop × (CDR close ÷ US close)` on the latest date ≤ D when the CDR had volume > 0. This is the equity mark's ratio. If the CDR has never traded, the ratio uses the latest CDR close.
  - The CDR levels are display only; decisions use the US levels.
- **Trade R** (managed positions): the position's realized P&L in CAD, after fees, over its whole life ÷ the planned risk. The planned risk is Σ signal-linked buy units × (`cdr_signal_close` − the initial `cdr_stop`).
  - `cdr_stop` has the same `stop_pct` as the US stop, so this equals P&L ÷ (filled units × (`us_signal_close` − `us_stop`)) in US-equivalent terms. That is R on the planned risk, the backtest's definition (spec 02), so spec 05's `/pnl` compares live mean R with the backtest's.
  - Fills are never rescaled, and a split never changes total P&L, so splits never change R.

**Equity** = cash + Σ open quantity × CDR mark.
- The CDR mark is the latest US close × (CDR close ÷ US close) on the most recent date the CDR had volume > 0. If the CDR has never traded, the latest CDR close is used. This avoids stale prices on the many zero-volume days.
- Equity is written nightly to `equity_snapshots`. **Peak** = max equity since `risk_state.peak_reset_on`, each earlier snapshot adjusted by the cash movements after its date: deposits are added to it and withdrawals taken from it. Moving cash in or out is never a gain or a drawdown, so a withdrawal alone never pauses.

**Pause:** set when equity < 0.85 × peak (the peak adjusted for cash movements). It is cleared only by `/resume`, which sets `peak_reset_on = today`.

**`PortfolioState` for `decide()`** is built from the above:
- **Positions:** every open position, managed and manual.
  - A managed position is passed as `Position(id = signal id, symbol = US symbol, setup = breakout, sector, units = open CDR quantity, entry_price = entry_us, entry_date = entry session, stop = current stop, target = None, time_limit = None, sessions_held, highest_close)`.
  - A manual position is passed with `stop = 0` so that it holds a slot and counts toward its sector. Any exit or stop update `decide()` returns for it is discarded.
- **Pending:** sent, unexpired signals, each with `planned_cost = suggested_units × limit` (the limit is `cdr_signal_close × 1.01`, rounded down to the cent: the most the order can spend). They hold slots. A scan retrying a target counts that target's own signals too.
- **Cash, equity, peak, and pause:** from the ledger.

## Splits

Wealthsimple shows split-adjusted holdings. The tool never rescales the owner's fills or the total ACB. It rescales only its own levels (stops, signal prices), and records a CDR split as a fill-neutral adjustment to units.

- **Detection (spec 05, step 4):** the evening scan looks up splits with spec 07's `fetch_yfinance_splits`. It covers the US symbol of every open managed position and `sent` signal, since the signal's `as_of`. It also covers the CDR (`<SYMBOL>.NE`) of every open position, manual ones included, since the opening buy's trade date, and of every `sent` signal since its `as_of`.
  - Each new split becomes one `corporate_actions` row (`source = yfinance`), so a rerun never applies a split twice.
  - The stored price history is already on the new scale: `ingest prices` refetches the whole history when older closes change, per the spec 07 fix.
- **US split, ratio r:**
  - The scan writes a `split` row in `stop_updates` for each affected managed position: `new_us_stop = old_us_stop ÷ r`, with the CDR stop unchanged.
  - `us_signal_close`, `us_stop`, and `entry_us` are read ÷ the product of the recorded US split ratios after the signal's `as_of`. The `trade_signals` row itself is never rewritten.
  - The highest close needs nothing (it is recomputed).
- **CDR split, ratio r:**
  - In the ledger, the units held are multiplied by r and the ACB per unit is divided by r, so the total ACB is unchanged. The CRA does not treat a split as a disposition: the old units' total cost moves to the new units.
  - Fills, cash, and P&L are unchanged.
  - The scan writes a `split` row in `stop_updates`: `new_cdr_stop = old_cdr_stop ÷ r`, with the US stop unchanged. `cdr_signal_close` is read ÷ r for display.
  - After the split, `/sell` takes post-split units.
  - A `sent` signal whose CDR splits before it is taken becomes `withdrawn`, with a message saying its prices no longer apply.
  - *Not tax advice.*
- **Owner corrections:**
  - `signalbench ledger split CDR RATIO EX_DATE` records a split that yfinance missed (`source = owner`).
  - `signalbench ledger void-action ID REASON` voids a wrong row. The effects are recomputed, and any `split` stop rows written from it are superseded by a new `split` row that undoes them.
- **Scale check:** before any exit or stop decision on a position or pending signal, the scan compares each signal price with the stored close of the date it came from:
  - It compares the stored raw close (`close`: split-adjusted, not dividend-adjusted) with `us_signal_close` ÷ the recorded US splits, and the CDR mark of the signal's `as_of` (the basis of its CDR reference, spec 05) with `cdr_signal_close` ÷ the recorded CDR splits.
  - A gap above 3% (spec 07's `MARK_TOLERANCE`) means an unrecorded split or bad data. Dividends never move the raw close.
  - That position or signal then gets no decision that night, and the scan sends `⚠️ NVDA: prices moved in a way no recorded split explains; check it by hand`. The held session is reviewed for exits and raises, marked late, by the first scan after the check passes (spec 05). A failed split lookup is only a warning: the scale check still guards the symbol.
  - The rest of the scan goes on.
- **Dividends:** nothing is adjusted. The stop is a price level compared with each night's close. A later dividend lowers older adjusted closes a little, which can only make a recomputed highest close, and so a trail candidate, slightly lower. The ratchet means the stop never falls.

## ACB and superficial losses

**Average cost per CDR symbol:**
- On a buy: `acb += quantity × price + fee`, `units += quantity`.
- On a sell: `acb_sold = acb × quantity / units`, `gain = quantity × price − fee − acb_sold`, then `acb −= acb_sold`, `units −= quantity`.
- On a CDR split with ratio r: `units ×= r`, and `acb` (the total) is unchanged, so the ACB per unit is divided by r.

**Superficial loss** (applies when a sale produces a loss):
- Let `bought_window` be the units of the **same CDR symbol** bought from 30 days before to 30 days after the sale, and `held_day_30` the units held at the end of the 30th day after the sale. Units are counted on the sale's scale: a split inside the window divides later quantities by its ratio.
- `denied = loss × min(quantity_sold, bought_window, held_day_30) / quantity_sold`
- The denied amount is added to the ACB of the units still held. It is reported as "superficial loss added to ACB".
- Until day 30 has passed, a loss is shown as **provisional**.
- Only the same CDR symbol counts as identical property. The report notes that the owner should confirm whether a US listing of the same company held elsewhere changes this.

**Tax report for a year:**
- Each disposition with date, symbol, quantity, proceeds, ACB, fees, gain/loss, superficial-loss adjustment, and provisional flag, plus totals.
- The year's CDR splits, listed with their ratio and the ACB per unit before and after.
- Available as `/tax YEAR` and `signalbench ledger tax YEAR [--csv PATH]`.

## Scale-up check

Runs after each close of a managed position. Active once at least 10 managed positions are fully closed.

| # | Check | Pass |
| --- | --- | --- |
| 1 | Take rate = taken ÷ (taken + expired + skipped for `disagree`/`no_time`/`other`) | ≥ 0.80. `price_moved` and `wide_spread` skips are excluded because the rules require them. `withdrawn` signals are excluded too |
| 2 | Mean \|cdr_fill − cdr_signal_close\| ÷ cdr_signal_close over signal-linked buys | ≤ 0.5% |
| 3 | Every exit alert followed by a matching sell within 1 session (for a late alert, within 1 session after it was sent) | 0 misses |
| 4 | No signal-linked buy filled above cdr_signal_close × 1.01 | 0 violations |

When all four pass, the bot suggests the C$900 deposit (spec 05). **Profitability is not a criterion.** The result and its inputs are stored in `risk_state` history for the record. Stop-raise messages ask for no action and play no part in the check.

## Interface

```python
class Ledger:
    def record_fill(self, *, cdr_symbol: str, side: Side, quantity: Decimal, price_cad: Decimal,
                    trade_date: date, signal_id: UUID | None = None,
                    exit_alert_id: UUID | None = None, fee_cad: Decimal = Decimal(0),
                    force: bool = False) -> Fill: ...
    def void_fill(self, fill_id: UUID, reason: str) -> None: ...
    def record_cash(self, amount_cad: Decimal, occurred_on: date, note: str) -> None: ...
    def record_stop_update(self, *, signal_id: UUID, session: date, reason: StopReason,
                           new_us_stop: Decimal, new_cdr_stop: Decimal,
                           late: bool = False) -> StopUpdateRow: ...
    def record_split(self, *, kind: SplitKind, symbol: str, ex_date: date, ratio: Decimal,
                     source: SplitSource) -> CorporateAction: ...
    def void_corporate_action(self, action_id: UUID, reason: str) -> None: ...
    def current_stop(self, signal_id: UUID) -> StopLevels: ...  # US and CDR, after splits
    def positions(self, as_of: date) -> list[Position]: ...
    def equity(self, as_of: date) -> EquitySnapshot: ...
    def portfolio_state(self, as_of: date) -> PortfolioState: ...
    def tax_report(self, year: int) -> TaxReport: ...
    def scale_up_check(self) -> ScaleUpResult: ...
```

**Validation:**
- quantity > 0 with at most 6 decimals, and price > 0
- trade_date ≤ today and not before the first cash movement
- a sell can't exceed the open quantity (no shorts), counted in post-split units
- a buy can't exceed available cash (no margin), and a withdrawal can't take the running cash below 0 from its date on. The owner can override either with `force=True`, because Wealthsimple is the source of truth. An override is logged.
- a `trail` stop update must be above the current stop, and only for an open managed position
- a split ratio must be > 0, and a `cdr_split` only for a CDR with an open position or a `sent` signal on its ex-date

## Testing

- **Worked ACB example, checked by hand and committed as a fixture:** buy 3 @ 30.00, buy 2 @ 33.00, sell 4 @ 35.00, buy 1 @ 31.00, sell 2 @ 29.00 (a loss, with a re-buy inside the window). Assert every intermediate ACB, the gain, and the superficial-loss adjustment.
- **Worked CDR split example, checked by hand and committed as a fixture:** buy 3 @ 30.00, a 2-for-1 split, then sell 4 @ 16.00. Assert 6 units after the split, the ACB per unit halved with the total ACB unchanged, the gain, and unchanged cash. Voiding the split restores the pre-split units.
- Fractional quantities through buy/sell/ACB.
- A void reverses cash and position effects.
- Validation rejects oversells (including across a split), and rejects overspends unless forced.
- Managed vs manual classification. A manual position holds a slot, and any decision `decide()` returns for it is discarded.
- Stops:
  - The trailing stop only rises, with a hand-worked series.
  - Timing: D's exit uses the stop in force before D's raise, and a raise applies from D+1.
  - No raise on an exit day or while an exit alert is `sent`.
  - The CDR display stop uses the last traded ratio.
- Exits: `stop` before `earnings`; the earnings exit fires from a Finnhub calendar date (not only SEC 2.02); a position is never closed for time and has no target.
- US split: the stop, `us_signal_close`, and `entry_us` rescale, and R is unchanged. The scale check refuses a position with an unrecorded split and lets a dividend-only history through.
- Trade R: equal on the CAD and US-equivalent definitions for a fixture trade, and unchanged across a CDR split.
- Equity, peak, pause, and resume with the peak reset. `auto_resume_sessions` has no effect.
- Each scale-up criterion at its threshold, and a late exit alert's 1-session window.
- `portfolio_state()` counts pending signals toward slots.
- `live start` refuses an uncommitted or edited config, and refuses a second run.

## Gate

- CI green, and both hand-checked fixtures (ACB, CDR split) pass.
- On a copy of fixture data, rebuilding positions and cash from fills and corporate actions alone matches the expected snapshot exactly.

## Changelog

- 2026-09-22: created.
- 2026-09-22: CDR mark from US close × last traded ratio; `wide_spread` skip reason added.
- 2026-09-24: target is `entry_us + target_r × (us_signal_close − us_stop)` and live trade R is P&L ÷ (filled units × (us_signal_close − us_stop)) in US-equivalent terms: R on planned risk, matching the spec 02 owner decision of 2026-09-24.
- 2026-09-29: narrowed to the single live strategy v2-none-cash (owner decision, overview changelog):
  - **Config:** `data/strategy_v2-none-cash.yaml` is frozen by a `live_config` row (path, sha256, start) written by `signalbench live start`, and the scan refuses a changed file.
  - **Removed:** Breakout only, with no Jev (`catalyst` and `jev_status` columns dropped), no target, and no time exit (`target`/`time` alert reasons and the `time_limit` column dropped).
  - **Stops:** the trailing stop is recorded in a new `stop_updates` table, with the backtest's timing. The earnings exit uses SEC 2.02 plus Finnhub calendar dates.
  - **Exit alerts:** `exit_alerts` gains `signal_id` and `late`, and a position has one open exit alert at a time.
  - **Splits:** a new `corporate_actions` table records US splits (the tool's US levels are rescaled) and CDR splits (units × r, ACB per unit ÷ r, total ACB unchanged). The scale check reuses spec 07's split detection. Fills and the owner's ACB are never rescaled.
  - **R:** trade R is stated in CAD on the planned risk, equal to the US-equivalent definition.
  - **Migration:** `0013_live_ledger` (`0011` and `0012` are taken).
- 2026-09-30: implementation choices (plan [2026-09-30-swing-04-ledger.md](../plans/2026-09-30-swing-04-ledger.md)):
  - **Ids** are integers (`/void 12`; Telegram button data is capped at 64 bytes), and the interface takes `int` where it showed `UUID`.
  - **Columns added:** `fills.forced` (a buy recorded with `force=True`, also logged), `stop_updates.corporate_action_id` (the split a `split` row applies or undoes), and `risk_state.scale_up_history` (each scale-up result with its inputs).
  - **Splits:** `record_split` writes the `split` stop rows itself and a CDR split withdraws the CDR's `sent` signals, in one transaction; voiding writes the undoing rows. A split recorded before a position opened rescales its initial stop without a row. A split ratio of 1 is refused. `ledger split` also takes a US symbol.
  - **Stops:** `stop_in_force(signal, D)` replays the rows in the order written, leaving out raises decided at D's close or later, so a forced rescan sees the stop in force during D. `record_stop_update` computes the CDR display stop itself and takes no `reason` (split rows come from `record_split`).
  - **Pending signals** hold their slot through the close of their entry session (the owner may have bought and not reported it yet).
  - **Money:** prices, fees, and stops are stored at 4 decimals and quantities at 6; more decimals are refused, not rounded. Equity snapshots are rounded to 4 decimals before the peak and pause use them; the tax report rounds to the cent. A position with no stored CDR price is valued at its ACB.
  - **Fills:** on one trade date, fills replay in the order recorded, after a split with that ex-date. The no-margin check is the running cash from the buy on. A void that would leave a later sale oversold is refused (record the corrected fill first). A buy may link to a `sent` or `expired` signal (then `taken`), not to a skipped or withdrawn one.
  - **`live start`** also refuses uncommitted tracked code, like `paper start` did, so `start_git_sha` names the code that ran.
- 2026-09-30: review fixes before real money (owner-approved; spec 05 has the scan and bot side):
  - **Peak:** each earlier snapshot is adjusted by the cash movements after it (+ deposits, − withdrawals), so a withdrawal alone never looks like a drawdown or pauses; the pause review no longer lists withdrawals.
  - **Withdrawals:** `record_cash` refuses a withdrawal that takes the running cash below 0 from its date on, unless `force` (logged).
  - **Pending cash:** a pending signal's `planned_cost` is `suggested_units × limit`, not × the close.
  - **Scale check:** the CDR side compares the CDR mark of the signal's `as_of` (the basis of the new CDR reference), so a stale zero-volume close is not taken for a split; a failed split lookup no longer holds a symbol.
- 2026-09-30: paper trading removed (plan `2026-09-30-remove-paper-trading.md`): migration `0013` now revises `0011`; the split tolerance (3%) is a constant in `live/levels.py`; `paper_earnings_dates` is now `calendar_earnings_dates`.
