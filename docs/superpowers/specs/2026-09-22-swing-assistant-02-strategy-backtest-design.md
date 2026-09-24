# Spec 02 — Strategy and Backtest

**Parent:** [Overview](2026-09-22-swing-assistant-00-overview.md) · **Depends on:** spec 01 gate green.
**Goal:** Implement the three setups, risk sizing, and one `decide()` function. Simulate them day by day over 2012–present and produce a pass-bar report that decides which setups go live. This spec runs Pullback and Breakout without Jev. The Sentiment setup and the Jev filter are wired in spec 03 through the `ReadingsView` port defined here.

## Pre-registration (the most important rule)

- **Prerequisite:** `data/cdr_spread_survey.yaml` (overview, "Spread survey") is committed first. It sets `cost_per_side` below.
- All parameters below go in `data/strategy_v1.yaml`, committed **before** the first backtest on real data.
- Each run records `config_sha256`, `git_sha`, and `data_fingerprint`.
- The first real v1 pass-bar report decides go/no-go. Parameters are not tuned to pass. A later `strategy_v2.yaml` may be tested, but every v2 report is labeled **post-hoc** and cannot overturn a v1 fail on its own. Going live on a post-hoc result is an explicit owner decision, written in the overview changelog.

## Setup rules (v1)

All values are computed on adjusted OHLC of the **US** stock (`adjusted_bars()` from spec 01), evaluated at the session close of `as_of`. All indicators are causal: they use only bars up to and including `as_of`.

**Every setup:**
- Regime: QQQ close > QQQ 200-session SMA, or there are no entries.
- Earnings blackout: no entry if an earnings event falls on any of the next 3 sessions (D+1 through D+3).
- No entry on a symbol that is already held or has a pending entry.
- If two setups fire on one symbol on the same day, one signal is kept, by config order `setup_priority: [pullback, breakout, sentiment]`.
- The symbol must be `active` under the liquidity filter as of that date (see Backtest universe for the backtest version).

| | Pullback | Breakout | Sentiment (spec 03) |
| --- | --- | --- | --- |
| Trend | close > SMA50 **and** SMA50 > SMA200 | close > SMA50 | close > SMA20 |
| Trigger | RSI(2) < 10 (Wilder) | close > max(close of prior 20 sessions) **and** volume ≥ 1.5 × mean(volume of prior 50 sessions) | Positive reading (spec 03) on a document whose legal close is within the last 3 sessions **and** close > prior session high |
| Stop level (set at signal) | min(low of last 3 sessions) − 0.5 × ATR | close − 2 × ATR | close − 2 × ATR |
| Trailing stop | none | max(current stop, highest close since entry − 3 × ATR(today)); only ratchets up | none |
| Target | entry + 2R | none | entry + 2R |
| Time limit | 10 sessions held | 30 sessions held | 10 sessions held |

**Execution** (identical in backtest and live):
- A signal on the close of day D is an entry order for the open of D+1 (the next session). Call this *next open*.
- **Skip entry** if next open > signal close × 1.01 (`gap_up`) or next open ≤ stop (`gap_below_stop`).
- **R is the risk planned at the signal:** per-unit `R = signal close − stop`. A trade's R = its P&L after costs ÷ (units × R), i.e. (exit fill − entry fill) ÷ (signal close − stop). The 2R target = entry fill + 2 × (signal close − stop): it is anchored on the actual fill but sized by the planned risk, so an open just above the stop cannot inflate R.
- Exits are evaluated on each close, in this order: stop (close ≤ stop), earnings (event on D+1 or D+2), target (close ≥ target), time (sessions held ≥ limit). The first match fills at the next open. Day of entry counts as session 1 held.
- **Cost:** `cost_per_side` = max(0.2%, median surveyed spread ÷ 2 + 0.1%). For example, a 0.4% median spread gives 0.3%. Buys fill at open × (1 + cost_per_side), sells at open × (1 − cost_per_side).

## Risk and sizing

- `equity` = cash + Σ(units × close) at the `as_of` close. `risk_amount = 0.02 × equity`.
- `units = risk_amount / (signal_close − stop)`. Then cap so `units × signal_close ≤ equity / 3` and ≤ uncommitted cash (cash minus the planned cost of entries already accepted that evening).
- Backtest units are fractional. Whole-unit rounding happens only live, on CDR prices (spec 05).
- **Slots:** at most 3 open-or-pending positions and at most 2 per sector. Candidates are ranked by:
  1. `catalyst` (spec 03; always false in this spec)
  2. higher 20-session median US traded value
  3. symbol A–Z

  Candidates that don't fit are recorded as skips with reason `no_slot` or `sector_cap`.
- **Pause:** if equity at close < 0.85 × peak, there are no new entries. Exits keep running.
  - Backtest stand-in for the owner's `/resume`: resume automatically after 10 sessions and reset `peak := equity` at resume.
  - Live, resume happens only on `/resume` (spec 05), which applies the same peak reset.

## Interfaces

```python
def decide(
    as_of: date,
    market: MarketView,        # bars/indicators ≤ as_of; earnings dates; active flags
    readings: ReadingsView,    # spec 03; NullReadingsView here (no blocks, no catalysts, no sentiment triggers)
    portfolio: PortfolioState, # cash, positions, pending entries, equity, peak, paused
    config: StrategyConfig,
) -> Decision: ...

@dataclass(frozen=True)
class Decision:
    entries: list[EntryOrder]    # symbol, setup, signal_close, stop, target_r, time_limit, units, risk_amount, catalyst, rank
    exits: list[ExitOrder]       # position_id, reason: stop|earnings|target|time
    stop_updates: list[StopUpdate]
    skips: list[Skip]            # symbol, setup, reason: gap_up|gap_below_stop|no_slot|sector_cap|blocked|paused|earnings_blackout|regime
```

- `decide()` is pure: no database, no clock, no network. Given the same inputs, it returns the same `Decision`.
- `MarketView` precomputes indicators once for the full history. `at(as_of)` then returns a view that raises `LookAheadError` on any access past `as_of`.
- The same `decide()` is called by the simulator (below) and by the live scan (spec 05).

## Simulator

For each NYSE session `t` from start to end:
1. At the open of `t`: fill the exits, then the entries, decided at `t−1`, applying the skip rules and costs.
2. At the close of `t`: mark to market, update the peak and the pause state, and call `decide(t)`.

Start equity is 100 (units are currency-agnostic). Every fill, skip, pause, and resume goes into the trade log.

**Backtest universe:** US tickers that have a CDR in `cdr_universe.yaml`. Liquidity is evaluated on US traded value only, because CDR history is short. Both simplifications are stated in every report.

## Metrics and pass bar

**Computed per run:**
- trades, win rate, mean and median R, mean R in each half, total return, CAGR, Sharpe (daily returns, √252, rf = 0), max drawdown, exposure %, average hold
- skips by reason, number of pauses
- the same stats for trades entered on or after 2026-09-15

**Benchmarks over the same dates:** QQQ buy-and-hold, and equal-weight buy-and-hold of the backtest universe. The latter holds names with data at the start date, is not rebalanced, and is labeled the "survivor benchmark".

**Pass bar (v1).** A setup run alone must meet all of these to go live:

| # | Criterion | Threshold |
| --- | --- | --- |
| 1 | Trades | ≥ 30 |
| 2 | Mean R after costs | > +0.10 |
| 3 | Mean R in each half (H1 2012-01-01 → 2018-12-31, H2 2019-01-01 → end) | > 0 in both |
| 4 | Sharpe | ≥ QQQ buy-and-hold Sharpe |

A **combined** run of all passing setups is reported for information only. It doesn't change pass or fail.

**Every report prints these caveats:**
- survivorship (the universe is today's CDR list, compared with the survivor benchmark)
- US prices stand in for CDR prices
- realized 2.02 dates stand in for known-in-advance earnings dates
- liquidity is checked on US data only

## Persistence and output

- **New table `backtest_runs`** (migration `0009`): id, strategy_version, config_sha256, git_sha, setup (`pullback|breakout|sentiment|combined`), jev_mode (`off|filter`), start_date, end_date, data_fingerprint, metrics JSON, pass_bar JSON (`{criterion: {value, threshold, passed}}`), passed, trade_log JSON, run_at.
- `data_fingerprint` = SHA-256 of sorted `(symbol, first_date, last_date, row_count, round(sum(adj_close), 4))` over all input price series. It reuses `backtest/fingerprint.py` hashing helpers.
- **CLI:** `signalbench backtest run --setup {pullback,breakout,sentiment,combined} [--jev {off,filter}]` stores a run and writes `reports/backtests/<YYYY-MM-DD>-<setup>-<jev>.md`, which is committed to git as the record. `signalbench backtest show <run_id>` reprints a report.

## Testing

- Indicator unit tests against hand-computed values: SMA, Wilder RSI(2), Wilder ATR(14), 20-session high, volume mean.
- Synthetic price fixtures where each setup fires on a known date, with known stop, target, and exit dates. One fixture per exit reason, including a trailing stop that ratchets and never loosens.
- **Look-ahead test:** for random `as_of` dates, `decide()` on data truncated at `as_of` equals `decide()` on full data. `LookAheadError` is raised on any direct future access.
- Skip rules: gap_up, gap_below_stop, no_slot, sector_cap, paused, earnings_blackout, regime.
- Sizing: risk-based units, the equity/3 cap, the cash cap.
- Pause and auto-resume with the peak reset.
- Determinism: two runs on the same fixture give identical `metrics` and fingerprints.
- Pass-bar evaluation on crafted metrics at exactly each threshold.

## Gate

- CI green.
- `strategy_v1.yaml` committed before the first real run (checked in git history).
- Real v1 reports for `pullback` and `breakout` (jev off) are committed under `reports/backtests/`.
- The owner reads both and records go/no-go in the overview changelog. If both fail: stop before specs 04–05 unless the spec 03 Sentiment setup passes.

## Changelog

- 2026-09-22: created.
- 2026-09-22: cost per side now comes from the spread survey, not a fixed 0.2%.
- 2026-09-24: implementation choices (plan `2026-09-24-swing-02-strategy-backtest.md`):
  - `backtest_runs` is migration `0010`, because spec 01 used `0009` for `raw_documents.form`. Specs 03–05 shift to `0011`–`0013`.
  - Extra skip reasons: `held`, `no_cash`, and `no_bar`. Symbols that fail the liquidity filter are ignored without a skip record. Gate order: held, regime, paused, earnings_blackout, blocked.
  - `EntryOrder.risk_amount` is the risk after the caps. If the open plus cost would spend more than the cash left, the entry is trimmed to the cash and logged as `trimmed`.
  - Halves and the recent sample are split by entry date. `cost_per_side` is rounded to 6 decimals. `config_sha256` hashes the file with CRLF normalised to LF.
  - `backtest run` refuses a config that is not committed and unchanged in git. `git_sha` carries `-dirty` when tracked code or data files have uncommitted changes.
  - The trade log also records `stop_update` and `exit_deferred` events.
- 2026-09-24: owner decision: R is measured on the risk planned at the signal (signal close − stop), not on the fill (entry fill − stop). Trade R = P&L after costs ÷ (units × planned per-unit risk); the target is entry fill + target_r × planned per-unit risk. Gap rules, stops, and costs are unchanged. Reason: an open just above the stop made fill − stop tiny and inflated R (a normal 2R target read as +4.27R).
- 2026-09-24: `backtest run` only accepts a config at `data/strategy_<version>.yaml` whose `version` matches the file name (still committed and unchanged from HEAD), and refuses when stored runs of that version used a different `config_sha256`: a changed config must become a new version.
- 2026-09-24: `backtest run` also requires `data/cdr_spread_survey.yaml` to be committed and unchanged from HEAD, and refuses unless the config's `cost_per_side` equals the survey's (after its 6-decimal rounding).
- 2026-09-24: before simulating, the runner drops today's bars when the latest QQQ bar is dated today (America/New_York) and it is before 16:15 there (a partial bar), then refuses to run unless every universe series ends on the run's last session, listing each stale or empty series and pointing to `signalbench ingest prices`.
- 2026-09-24: the backtest's earnings dates (blackout and earnings exit) are every realized SEC Item 2.02 date (`sec_2.02` events only), unclustered. This is more conservative than `earnings_dates()`, which mixes in Finnhub calendar dates and collapses dates within 3 days to the first.
