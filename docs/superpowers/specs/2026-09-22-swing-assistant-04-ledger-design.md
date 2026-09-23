# Spec 04 — Ledger

**Parent:** [Overview](2026-09-22-swing-assistant-00-overview.md) · **Depends on:** spec 01 (CDR tickers, CDR prices) and spec 02 (`PortfolioState`).
**Goal:** Record what the owner actually did on Wealthsimple. From that alone, derive positions, cash, equity, peak, the pause state, the scale-up check, and a CRA-style cost base (ACB) report. Nothing here is typed in as a total. Every number is computed from fills and cash movements.

**Not tax advice.** The ACB and superficial-loss handling follow the CRA's published method as a record-keeping aid. The owner verifies their tax return independently.

## Tables (migration `0011`)

All money is CAD, stored as `Numeric`, and computed with `Decimal`.

| Table | Columns |
| --- | --- |
| `cash_movements` | id, amount_cad (+ deposit / − withdrawal), occurred_on, note |
| `trade_signals` | id, as_of, us_symbol, cdr_ticker_id, setup, us_signal_close, us_stop, stop_pct, time_limit, cdr_signal_close, cdr_stop, suggested_units, order_type (`limit`/`market`), risk_amount_cad, catalyst, jev_status (`ok`/`unavailable`), explanation, status (`sent`/`taken`/`skipped`/`expired`), skip_reason (`disagree`/`no_time`/`price_moved`/`other`/null), telegram_message_id, expires_at, created_at. Unique (`as_of`, `us_symbol`, `setup`) |
| `exit_alerts` | id, cdr_ticker_id, as_of, reason (`stop`/`earnings`/`target`/`time`), status (`sent`/`done`/`ignored`), telegram_message_id, created_at |
| `fills` | id, cdr_ticker_id, side (`buy`/`sell`), quantity (18,6), price_cad (12,4), fee_cad (default 0), trade_date, signal_id (FK nullable), exit_alert_id (FK nullable), voided (bool), void_reason, created_at |
| `equity_snapshots` | date (PK), cash, positions_value, equity, peak |
| `risk_state` | single row: paused, paused_at, paused_reason, resumed_at, peak_reset_on |

- `trade_signals` rows are written by the evening scan (spec 05) when a signal is sent.
- A signal expires at the close of the entry session (next open's session) if it hasn't been taken.
- **Fills are never deleted.** A mistake is corrected with `void` plus a new fill.

## Derivations

**Cash** = Σ cash_movements + Σ sell proceeds − Σ buy costs − Σ fees, over non-voided fills.

**Positions** are grouped by CDR:
- open quantity = Σ buys − Σ sells
- `ACB` = CRA average cost (below)
- a position is **managed** if the buy that opened it is linked to a `trade_signal`. Managed positions get strategy exits.
- a position is **manual** if opened with `/buy` without a signal. Manual positions count toward equity, slots, and the sector cap, but get no stop/target/time alerts. They are marked "manual" everywhere.

**Strategy levels for a managed position.** Exits are evaluated on the US series, the same as the backtest:
- US-equivalent entry: `entry_us = us_signal_close × (cdr_fill_price / cdr_signal_close)`
- stop: `us_stop` from the signal
- target: `entry_us + 2 × (entry_us − us_stop)` for setups with a target
- the trailing stop (Breakout) is persisted on the position and updated from `Decision.stop_updates`
- sessions held count NYSE sessions from the buy's trade date (session 1)
- CDR display levels: `cdr_stop = cdr_signal_close × (1 − stop_pct)`; the target in CAD uses the same ratio

**Equity** = cash + Σ open quantity × latest CDR close. It is written nightly to `equity_snapshots`. **Peak** = max equity since `risk_state.peak_reset_on`.

**Pause:** set when equity < 0.85 × peak. It is cleared only by `/resume`, which sets `peak_reset_on = today`.

`PortfolioState` for `decide()` is built from the above plus pending (sent, unexpired) signals, which occupy slots.

## ACB and superficial losses

**Average cost per CDR symbol:**
- On a buy: `acb += quantity × price + fee`, `units += quantity`.
- On a sell: `acb_sold = acb × quantity / units`, `gain = quantity × price − fee − acb_sold`, then `acb −= acb_sold`, `units −= quantity`.

**Superficial loss** (applies when a sale produces a loss):
- Let `bought_window` be the units of the **same CDR symbol** bought from 30 days before to 30 days after the sale, and `held_day_30` the units held at the end of the 30th day after the sale.
- `denied = loss × min(quantity_sold, bought_window, held_day_30) / quantity_sold`
- The denied amount is added to the ACB of the units still held. It is reported as "superficial loss added to ACB".
- Until day 30 has passed, a loss is shown as **provisional**.
- Only the same CDR symbol counts as identical property. The report notes that the owner should confirm whether a US listing of the same company held elsewhere changes this.

**Tax report for a year:** each disposition with date, symbol, quantity, proceeds, ACB, fees, gain/loss, superficial-loss adjustment, and provisional flag, plus totals. Available as `/tax YEAR` and `signalbench ledger tax YEAR [--csv PATH]`.

## Scale-up check

Runs after each close of a managed position. Active once at least 10 managed positions are fully closed.

| # | Check | Pass |
| --- | --- | --- |
| 1 | Take rate = taken ÷ (taken + expired + skipped for `disagree`/`no_time`/`other`) | ≥ 0.80. `price_moved` skips are excluded because the rules require them |
| 2 | Mean \|cdr_fill − cdr_signal_close\| ÷ cdr_signal_close over signal-linked buys | ≤ 0.5% |
| 3 | Every exit alert followed by a matching sell within 1 session | 0 misses |
| 4 | No signal-linked buy filled above cdr_signal_close × 1.01 | 0 violations |

When all four pass, the bot suggests the $900 deposit (spec 05). **Profitability is not a criterion.** The result and its inputs are stored in `risk_state` history for the record.

## Interface

```python
class Ledger:
    def record_fill(self, *, cdr_symbol: str, side: Side, quantity: Decimal, price_cad: Decimal,
                    trade_date: date, signal_id: UUID | None = None,
                    exit_alert_id: UUID | None = None, fee_cad: Decimal = Decimal(0)) -> Fill: ...
    def void_fill(self, fill_id: UUID, reason: str) -> None: ...
    def record_cash(self, amount_cad: Decimal, occurred_on: date, note: str) -> None: ...
    def positions(self, as_of: date) -> list[Position]: ...
    def equity(self, as_of: date) -> EquitySnapshot: ...
    def portfolio_state(self, as_of: date) -> PortfolioState: ...
    def tax_report(self, year: int) -> TaxReport: ...
    def scale_up_check(self) -> ScaleUpResult: ...
```

**Validation:**
- quantity > 0 with at most 6 decimals, and price > 0
- trade_date ≤ today and not before the first cash movement
- a sell can't exceed the open quantity (no shorts)
- a buy can't exceed available cash (no margin). The owner can override with `force=True`, because Wealthsimple is the source of truth. An override is logged.

## Testing

- **Worked ACB example, checked by hand and committed as a fixture:** buy 3 @ 30.00, buy 2 @ 33.00, sell 4 @ 35.00, buy 1 @ 31.00, sell 2 @ 29.00 (loss, with a re-buy inside the window). Assert every intermediate ACB, the gain, and the superficial-loss adjustment.
- Fractional quantities through buy/sell/ACB.
- A void reverses cash and position effects.
- Validation rejects oversells, and rejects overspends unless forced.
- Managed vs manual classification, and US-equivalent entry/target math.
- Equity, peak, pause, and resume with the peak reset.
- Each scale-up criterion at its threshold.
- `portfolio_state()` counts pending signals toward slots.

## Gate

- CI green, and the hand-checked ACB fixture passes.
- On a copy of fixture data, rebuilding positions and cash from fills alone matches the expected snapshot exactly.

## Changelog

- 2026-09-22: created.
