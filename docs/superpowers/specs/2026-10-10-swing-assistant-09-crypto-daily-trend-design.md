# Spec 09 — Crypto daily trend: Breakout on BTC and ETH

**Parent:** [Overview](2026-09-22-swing-assistant-00-overview.md) · **Depends on:** spec 02 (simulator, metrics, pass bar, pre-registration guard) and spec 06 (`time_limit: null`, the `v2-none-cash` config).
**Goal:** Test, once and pre-registered, whether the live Breakout rules (`data/strategy_v2-none-cash.yaml`) carry over unchanged to BTC and ETH on Kraken daily candles. **Research only:** nothing here connects to Kraken's trading API. A pass makes going live a separate, later spec; a fail is logged like any other.

## Why

The owner wants to trade crypto. Of the strategies reviewed on 2026-10-08, daily trend-following on the largest coins has the most evidence after fees; ranking altcoins, scalping, and intraday mean reversion do not survive retail fees. Breakout is the one setup that has worked here, and none of its numbers were chosen on crypto data, so running it unchanged is a clean test. Intraday momentum is spec 10.

## Decisions (owner, 2026-10-08 to 2026-10-10)

- **Scope:** research only (data, a pre-registered backtest, a report, a research-log entry).
- **Exchange:** Kraken (Canadian restricted dealer; public API; free full candle history). USD pairs `XBTUSD` and `ETHUSD`: the longest history and the most volume, and R and Sharpe stay in USD. Canadian retail accounts get no margin or derivatives, so the strategy is long-only, which Breakout already is.
- **Universe:** BTC and ETH only. They have the longest history, and no coin was picked after it did well beyond these two obvious survivors (a caveat in the report).
- **Rules:** Breakout from `v2-none-cash`, unchanged except what crypto lacks (below).
- **Pass bar:** v1's, with BTC buy-and-hold in place of QQQ.
- **Cost:** 0.40% per side, the top of Kraken's published fee range (starting tier, taker), with no extra slippage.
- **Approach:** a separate crypto runner on the shared engine. The stock runner, the live scan, and the simulator are not changed.

## Data

**Tickers.** `XBTUSD` and `ETHUSD` in `tickers`, with a new `TickerKind.crypto` (one migration) and sector `crypto`.

**History, from Kraken's downloadable files.** The owner downloaded Kraken's full OHLCVT archive (`Kraken_OHLCVT_Full_2026Q2.zip`, checksum verified against Kraken's published SHA-256) and extracted `XBTUSD_1440.csv` and `ETHUSD_1440.csv` (plus the `_1.csv` minute files for spec 10) to `$HOME\kraken-ohlcvt`, outside the repo. The files cover each market's first trade to 2026-06-30. Each row has no header: `timestamp, open, high, low, close, volume, trades`, with `timestamp` the candle's start in Unix seconds UTC.

`signalbench crypto ingest-file PATH --symbol XBTUSD` loads one daily file into `prices`:

- Each candle is dated by its UTC day; `adj_close = close` (no splits or dividends).
- `volume` is in coins, rounded to the nearest whole coin to fit the `BigInteger` column.
- The same upsert and bad-bar rejection as stock prices (`validate_bar`: non-positive prices, high below low, negative volume).
- The ticker is created if missing. The file name must start with the pair (`XBTUSD_…`), so one pair's file cannot be loaded as the other's, and every candle must start at 00:00 UTC, so a 1-minute file is refused.

**Staying current, from Kraken's public API.** `signalbench crypto ingest-recent` calls the public `OHLC` endpoint (`interval=1440`, no key), which serves about the last 720 daily candles, and upserts every complete candle it returns through the same upsert (unchanged rows are not rewritten). **Today's candle (UTC) is never stored**: it is the day in progress. A pair with no stored history is refused: load its file first. A day's candle is final only after 00:00 UTC. Tests use a fake HTTP client.

**Missing days.** Crypto trades every day, but Kraken's files have no row for a day with no trades. Found in the real files: none for BTC since 2015; five for ETH (four in its first weeks in 2015, and 2018-01-12, a Kraken outage). A missing day is a day with no candle, like a halted stock: nothing fills, and a held coin waits. The simulator already does this (an exit with no bar is deferred, an entry with no bar is skipped as `no_bar`, a held position is marked at its last close). The report lists every missing day. A run is refused if any gap inside the run is longer than 3 days.

## The config: `data/strategy_crypto-v1.yaml`

A copy of `data/strategy_v2-none-cash.yaml`, committed on its own before any real run, with only these fields changed:

| Field | v2-none-cash | crypto-v1 | Why |
| --- | --- | --- | --- |
| `version` | `v2-none-cash` | `crypto-v1` | |
| `regime.symbol` | `QQQ` | `XBTUSD` | BTC above its 200-day average gates new entries |
| `cost_per_side` | `0.002` | `0.004` | Kraken's top published fee |
| `liquidity.min_median_traded_value_usd` | `50000000` | `0` | The floor exists for thin CDRs; it would block 2015–16 here |
| `backtest.start` | `2012-01-01` | `2015-01-01` | BTC needs 200 days for the regime gate; Kraken's 2013–14 data is thin |
| `backtest.h1_end` / `h2_start` | `2018-12-31` / `2019-01-01` | `2020-12-31` / `2021-01-01` | About half and half |
| `backtest.recent_since` | `2026-09-15` | `2026-07-01` | The part filled from the API, after Kraken's file ends |

Everything else is identical: Breakout (close above the 20-day high close, volume at least 1.5× its 50-day average, close above the 50-day average, a 2-ATR initial stop, a 3-ATR trailing stop, no time limit), 2% risk per trade, 3 slots and 2 per sector (with two coins, both in sector `crypto`, at most two positions), the 15% drawdown pause with a 10-session auto-resume, the 1% gap-up skip, and the pass bar (at least 30 trades, mean R after costs above 0.10, mean R above 0 in both halves, Sharpe at least BTC buy-and-hold's). The `earnings` section stays (the parser requires it), but crypto has no earnings dates, so it never fires. Every "session" is a calendar day. ETH starts trading once its indicators exist; `decide()` already skips a symbol until they do.

The run uses Breakout only, as the stock v2 runs did with `--setup breakout`.

## Pre-registration guard

Unchanged: the config must be at `data/strategy_<version>.yaml` (`check_config_path`), and every stored run of a version must share its `config_sha256` (`check_version_unchanged`). The CDR survey check is replaced, for crypto only, by **`cost_per_side` must equal `KRAKEN_TOP_FEE = 0.004`**, a constant in code citing Kraken's fee page (<https://support.kraken.com/ca/articles/201893638-How-trading-fees-work-on-Kraken>, read 2026-10-08: fees range from −0.02% to 0.40%).

## Calendar

`DailySessions` in `market/calendar.py`, following the `Sessions` protocol: every calendar day is a session, from 2010-01-01; `next_sessions` returns the following days; each session closes at 00:00 UTC the next day. `exchange_calendars` is not used.

## Runner: `backtest/crypto_runner.py`

`run_crypto_backtest(session, config, config_sha256, calendar, git_sha, run_date, reports_dir, now)`:

1. Breakout only (`config.with_setups(("breakout",))`); `check_cost_matches_kraken`; refuse a regime other than `XBTUSD` or any `cash_vehicle` (the rules gate on BTC and keep idle cash as cash); `check_version_unchanged`.
2. Loads `XBTUSD` and `ETHUSD` bars from `prices` (refuses if either is not seeded or has no bars). BTC is both a universe symbol and the regime/benchmark series.
3. Drops any candle dated on or after today in UTC (`now` must be timezone-aware). The last run day is yesterday (UTC). Refuses if either series does not end on it, if BTC has no bar on or before `backtest.start` (the gate and the benchmark need it from day one), or if a gap inside the run is longer than 3 days.
4. `MarketView` over `DailySessions` from `backtest.start` to the last day, plus the earnings look-ahead days (harmless: no earnings dates).
5. `simulate()` → `run_metrics()` → `evaluate_pass_bar()` against BTC buy-and-hold over the same days. No survivor benchmark: ETH has no bar on the first day, so it would just repeat BTC.
6. Stores a `BacktestRun` (setup `breakout`, Jev mode `off`), with `"market": "crypto"`, `"benchmark": "BTC buy-and-hold"`, and the missing days in the stored metrics; writes the report to `reports/backtests/`.

## Report

The shared report reads `metrics["market"]`. When it is `"crypto"`:

- The pass-bar Sharpe row reads "Sharpe vs BTC buy-and-hold".
- The POST-HOC line is replaced by: **"First test on crypto.** Rules carried over unchanged from v2-none-cash; the owner has seen crypto price charts, but no rule was chosen on them."
- A **Missing days** section lists each symbol's missing days inside the run.
- Caveats: Kraken USD candles; fills at the 00:00 UTC open of the next day; the 0.40% fee is the only cost (no slippage beyond it); BTC and ETH were chosen partly because they survived; Sharpe annualises with sqrt(252) over calendar days, which understates this run's and BTC's equally (about 1.2x) and leaves the comparison unchanged; taxes are not modeled (frequent trading can be business income in Canada); fractional coins are assumed.

Stock runs have no `market` key and render exactly as before.

## Commands

| Command | Purpose |
| --- | --- |
| `uv run signalbench crypto ingest-file PATH --symbol {XBTUSD,ETHUSD}` | Load a Kraken daily OHLCVT file; create the ticker if missing |
| `uv run signalbench crypto ingest-recent` | Upsert the complete daily candles from Kraken's public API (about 720 days; today's is never stored) |
| `uv run signalbench crypto backtest --config data/strategy_crypto-v1.yaml` | The run above |

## Testing

All tests use in-memory SQLite or fixtures and make no network calls.

- Kraken file parser: a fixture CSV gives rows dated by UTC day, `adj_close == close`, rounded volume, bad bars rejected, re-ingest idempotent.
- `ingest-recent`: a fake HTTP client; today's candle is never stored; a rerun writes nothing; a pair with no history is refused; an API error stops with a clear message.
- `DailySessions`: every day is a session; `next_sessions`; closes at 00:00 UTC the next day.
- Runner guards: refuses a cost other than 0.004, a regime other than XBTUSD, a cash vehicle, an edited config, BTC starting after the run's start, a gap over 3 days, a series that does not end yesterday, an unseeded symbol. A 1–3 day gap is allowed and listed; today's candle is never read.
- Runner end to end: synthetic BTC and ETH bars with one clear breakout give exactly one trade with a hand-checked R; the benchmark is BTC buy-and-hold.
- Report: a crypto run shows the crypto label, benchmark, caveats, and missing days; every existing report test still passes.
- Migration: `crypto` is added to the ticker kind.
- `tests/test_v1_regression.py` still passes: the stock path is untouched.

## Order of work

1. Build and test the code (no real runs).
2. Commit `data/strategy_crypto-v1.yaml` on its own, before any real run.
3. Load the real data: `crypto ingest-file` for both daily files, then `crypto ingest-recent`.
4. Run once; commit the report, PASS or FAIL.
5. Add one research-log entry (**one try**) and a line in this spec's changelog.

A changed config is a new version, reported as post-hoc.

## Out of scope

Kraken's trading API, order placement, live alerts, CAD pairs, coins other than BTC and ETH, and intraday data (spec 10).

## Changelog

- 2026-10-10: written after brainstorming with the owner.
- 2026-10-11: plan written ([plan](../plans/2026-10-11-swing-09-crypto-daily-trend.md)). `ingest-recent` upserts every complete candle the endpoint returns rather than only newer ones (the shared upsert skips unchanged rows) and refuses a pair with no history; `ingest-file` checks the file name and the 00:00 UTC start. The report notes that Sharpe's sqrt(252) understates both sides equally.
- 2026-10-11: built (tasks 1–9). Review fixes: the runner also refuses a non-BTC regime, a cash vehicle, and a BTC series starting after the run's start; `ingest-recent` fails one pair cleanly (rolls back, names the error type, continues) and refuses a ticker with no stored prices; Kraken parse and response-shape errors become `KrakenError` with the file line or the pair. `data/strategy_crypto-v1.yaml` committed on its own (`992fae0`).
- 2026-10-11: run once (`aa650f01`, git `b88f0be`, fresh local database): **PASS** on all four criteria (63 trades, mean R 3.581, worse half 1.325, Sharpe 1.23 vs BTC's 0.86). Report and research-log entry committed. Going live would be a separate spec.
