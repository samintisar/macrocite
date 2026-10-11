# Backtest: breakout (Jev off)

**Strategy:** crypto-v1 · **Result:** PASS

**First test on crypto.** Rules carried over unchanged from v2-none-cash; the owner has seen crypto price charts, but no rule was chosen on them (spec 09).

| Field | Value |
| --- | --- |
| Run id | `aa650f01-da1a-4f23-9381-ca8edc17b93d` |
| Sessions | 2015-01-01 to 2026-10-10 |
| Run at (UTC) | 2026-10-11 04:35 |
| config_sha256 | `8a1069b92ec7b279716ec67311a6f8265d9ed24eafa3e074f4d537e49b3bf82c` |
| git_sha | `b88f0bede5dbcc7c8f7471921b54f2b9fcd8ea32` |
| data_fingerprint | `83c09c63e2a866f7bed9601abb8e570c290d3a141d2078e112aaf983545e95b2` |

## Pass bar

| # | Criterion | Value | Threshold | Passed |
| --- | --- | --- | --- | --- |
| 1 | Trades | 63.000 | >= 30.000 | yes |
| 2 | Mean R after costs | 3.581 | > 0.100 | yes |
| 3 | Mean R in both halves (worse half shown) | 1.325 | > 0.000 | yes |
| 4 | Sharpe vs BTC buy-and-hold | 1.234 | >= 0.862 | yes |

## Metrics

| Metric | Value |
| --- | --- |
| Trades | 63 |
| Win rate | 60.3% |
| Mean R | 3.581 |
| Median R | 0.806 |
| Mean R, H1 (entries to 2020-12-31) | 5.386 (35) |
| Mean R, H2 (entries from 2021-01-01) | 1.325 (28) |
| Total return | 3883.9% |
| CAGR | 36.8% |
| Sharpe (daily, sqrt 252, rf 0) | 1.23 |
| Max drawdown | 21.0% |
| Exposure | 37.4% |
| Average hold (sessions) | 37.7 |
| Pauses | 7 |
| Open positions at the end | 1 |

## Trades entered on or after 2026-07-01

Trades 1 · win rate 100.0% · mean R 1.383 · median R 1.383

## Benchmarks (same sessions)

| Series | Total return | CAGR | Sharpe | Max drawdown |
| --- | --- | --- | --- | --- |
| This run | 3883.9% | 36.8% | 1.23 | 21.0% |
| BTC buy-and-hold | 25411.8% | 60.1% | 0.86 | 83.6% |

## Missing days

Days inside the run with no candle (no trades on Kraken): nothing filled, and held coins waited.

| Symbol | Days | Dates |
| --- | --- | --- |
| ETHUSD | 6 | 2015-08-09, 2015-08-10, 2015-09-04, 2015-09-21, 2015-10-10, 2018-01-12 |
| XBTUSD | 0 | none |

## Skips by reason

| Reason | Count |
| --- | --- |
| gap_up | 4 |
| held | 304 |
| paused | 2 |
| regime | 38 |

## Caveats

- Kraken USD candles (XBT/USD, ETH/USD); a day runs from 00:00 to 00:00 UTC.
- Orders fill at the next day's 00:00 UTC open. The 0.40% fee per side is the only cost; no slippage beyond it is modeled.
- BTC and ETH were chosen partly because they survived; a universe picked today flatters the past.
- Sharpe annualises with sqrt(252) over every calendar day, so this run's and BTC's are both understated by about sqrt(365/252), 1.2x; the comparison is unaffected.
- Every calendar day is a session, so each lookback covers less time than on stocks: the 200-day average is about 6.6 months, the 20-day high about 3 weeks, and the 10-session auto-resume 10 days.
- Taxes are not modeled; frequent trading can be business income in Canada. Fractional coins are assumed.

## Trades

| # | Symbol | Setup | Signal | Entry | Exit | Reason | Entry price | Exit price | R |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | XBTUSD | breakout | 2015-06-29 | 2015-06-30 | 2015-07-23 | stop | 259.97 | 277.28 | 1.20 |
| 2 | ETHUSD | breakout | 2015-10-30 | 2015-10-31 | 2015-11-01 | stop | 0.95 | 0.97 | 0.05 |
| 3 | XBTUSD | breakout | 2015-10-27 | 2015-10-28 | 2015-11-12 | stop | 295.59 | 307.96 | 0.72 |
| 4 | ETHUSD | breakout | 2015-12-14 | 2015-12-15 | 2015-12-17 | stop | 0.98 | 0.97 | -0.12 |
| 5 | XBTUSD | breakout | 2015-12-07 | 2015-12-08 | 2016-01-16 | stop | 393.45 | 363.02 | -0.83 |
| 6 | ETHUSD | breakout | 2016-01-11 | 2016-01-12 | 2016-02-18 | stop | 1.05 | 3.60 | 19.84 |
| 7 | XBTUSD | breakout | 2016-02-14 | 2016-02-15 | 2016-03-06 | stop | 406.37 | 391.61 | -0.52 |
| 8 | ETHUSD | breakout | 2016-03-01 | 2016-03-02 | 2016-04-06 | stop | 7.62 | 10.38 | 1.60 |
| 9 | ETHUSD | breakout | 2016-05-16 | 2016-05-17 | 2016-05-28 | stop | 11.19 | 11.19 | -0.00 |
| 10 | ETHUSD | breakout | 2016-06-12 | 2016-06-13 | 2016-06-18 | stop | 15.70 | 15.28 | -0.23 |
| 11 | XBTUSD | breakout | 2016-05-27 | 2016-05-28 | 2016-06-23 | stop | 473.36 | 597.48 | 6.39 |
| 12 | ETHUSD | breakout | 2016-07-22 | 2016-07-23 | 2016-07-27 | stop | 14.73 | 11.91 | -1.15 |
| 13 | ETHUSD | breakout | 2016-09-20 | 2016-09-21 | 2016-09-25 | stop | 14.34 | 12.84 | -1.20 |
| 14 | XBTUSD | breakout | 2016-10-07 | 2016-10-08 | 2016-11-04 | stop | 620.98 | 690.13 | 4.07 |
| 15 | XBTUSD | breakout | 2016-11-18 | 2016-11-19 | 2017-01-06 | stop | 755.24 | 1000.39 | 5.86 |
| 16 | XBTUSD | breakout | 2017-02-24 | 2017-02-25 | 2017-03-11 | stop | 1179.27 | 1103.13 | -0.79 |
| 17 | ETHUSD | breakout | 2017-01-03 | 2017-01-04 | 2017-03-19 | stop | 9.67 | 33.37 | 20.57 |
| 18 | ETHUSD | breakout | 2017-03-24 | 2017-03-25 | 2017-06-26 | stop | 52.01 | 275.70 | 20.36 |
| 19 | XBTUSD | breakout | 2017-05-01 | 2017-05-02 | 2017-07-11 | stop | 1402.83 | 2326.97 | 9.90 |
| 20 | XBTUSD | breakout | 2017-08-05 | 2017-08-06 | 2017-09-13 | stop | 3269.34 | 4159.89 | 2.06 |
| 21 | XBTUSD | breakout | 2017-10-13 | 2017-10-14 | 2017-11-13 | stop | 5649.21 | 5816.64 | 0.31 |
| 22 | XBTUSD | breakout | 2017-11-16 | 2017-11-17 | 2017-12-22 | stop | 7786.72 | 15123.56 | 6.91 |
| 23 | ETHUSD | breakout | 2017-11-09 | 2017-11-10 | 2018-01-23 | stop | 320.78 | 996.12 | 20.66 |
| 24 | ETHUSD | breakout | 2019-04-02 | 2019-04-03 | 2019-04-26 | stop | 165.09 | 151.48 | -0.99 |
| 25 | XBTUSD | breakout | 2019-04-02 | 2019-04-03 | 2019-07-01 | stop | 4919.00 | 10706.40 | 18.96 |
| 26 | ETHUSD | breakout | 2019-05-11 | 2019-05-12 | 2019-07-12 | stop | 193.08 | 267.42 | 3.26 |
| 27 | ETHUSD | breakout | 2019-09-16 | 2019-09-17 | 2019-09-25 | stop | 198.67 | 165.61 | -1.79 |
| 28 | XBTUSD | breakout | 2019-10-26 | 2019-10-27 | 2019-11-19 | stop | 9298.75 | 8136.12 | -1.05 |
| 29 | XBTUSD | breakout | 2020-01-28 | 2020-01-29 | 2020-02-27 | stop | 9432.58 | 8748.17 | -0.97 |
| 30 | ETHUSD | breakout | 2020-01-30 | 2020-01-31 | 2020-02-27 | stop | 185.14 | 222.85 | 2.14 |
| 31 | XBTUSD | breakout | 2020-04-29 | 2020-04-30 | 2020-05-12 | stop | 8815.02 | 8536.52 | -0.33 |
| 32 | ETHUSD | breakout | 2020-07-23 | 2020-07-24 | 2020-09-04 | stop | 276.60 | 380.69 | 5.18 |
| 33 | XBTUSD | breakout | 2020-07-27 | 2020-07-28 | 2020-09-04 | stop | 11095.20 | 10139.28 | -1.50 |
| 34 | XBTUSD | breakout | 2020-10-21 | 2020-10-22 | 2021-01-22 | stop | 12864.25 | 30709.17 | 23.44 |
| 35 | ETHUSD | breakout | 2020-11-05 | 2020-11-06 | 2021-02-26 | stop | 418.71 | 1474.90 | 26.51 |
| 36 | ETHUSD | breakout | 2021-04-26 | 2021-04-27 | 2021-05-18 | stop | 2545.15 | 3269.43 | 1.81 |
| 37 | ETHUSD | breakout | 2021-08-09 | 2021-08-10 | 2021-09-11 | stop | 3177.16 | 3196.37 | 0.05 |
| 38 | XBTUSD | breakout | 2021-10-06 | 2021-10-07 | 2021-11-19 | stop | 55571.60 | 56683.16 | 0.20 |
| 39 | ETHUSD | breakout | 2021-10-06 | 2021-10-07 | 2021-11-19 | stop | 3590.84 | 3982.44 | 0.81 |
| 40 | XBTUSD | breakout | 2023-01-13 | 2023-01-14 | 2023-03-08 | stop | 20009.82 | 22106.02 | 2.35 |
| 41 | ETHUSD | breakout | 2023-01-13 | 2023-01-14 | 2023-03-10 | stop | 1456.92 | 1431.86 | -0.29 |
| 42 | ETHUSD | breakout | 2023-03-13 | 2023-03-14 | 2023-04-22 | stop | 1686.71 | 1841.87 | 1.01 |
| 43 | XBTUSD | breakout | 2023-03-14 | 2023-03-15 | 2023-04-22 | stop | 24852.41 | 27155.34 | 1.00 |
| 44 | XBTUSD | breakout | 2023-06-20 | 2023-06-21 | 2023-07-25 | stop | 28422.04 | 29063.18 | 0.35 |
| 45 | ETHUSD | breakout | 2023-06-30 | 2023-07-01 | 2023-07-31 | stop | 1941.76 | 1854.17 | -0.65 |
| 46 | XBTUSD | breakout | 2023-10-16 | 2023-10-17 | 2024-01-19 | stop | 28630.87 | 41099.74 | 8.43 |
| 47 | ETHUSD | breakout | 2023-10-22 | 2023-10-23 | 2024-01-24 | stop | 1669.90 | 2231.96 | 6.01 |
| 48 | XBTUSD | breakout | 2024-02-09 | 2024-02-10 | 2024-03-20 | stop | 47315.61 | 61657.68 | 4.79 |
| 49 | ETHUSD | breakout | 2024-02-09 | 2024-02-10 | 2024-03-20 | stop | 2497.16 | 3146.04 | 3.83 |
| 50 | ETHUSD | breakout | 2024-05-20 | 2024-05-21 | 2024-06-12 | stop | 3675.96 | 3483.03 | -0.56 |
| 51 | XBTUSD | breakout | 2024-05-15 | 2024-05-16 | 2024-06-19 | stop | 66498.03 | 64909.02 | -0.29 |
| 52 | ETHUSD | breakout | 2024-07-15 | 2024-07-16 | 2024-07-26 | stop | 3500.33 | 3160.66 | -1.18 |
| 53 | XBTUSD | breakout | 2024-07-15 | 2024-07-16 | 2024-08-04 | stop | 65023.36 | 60445.85 | -0.95 |
| 54 | ETHUSD | breakout | 2024-11-07 | 2024-11-08 | 2024-12-20 | stop | 2908.54 | 3400.95 | 1.95 |
| 55 | XBTUSD | breakout | 2024-10-15 | 2024-10-16 | 2024-12-28 | stop | 67324.22 | 93771.01 | 6.14 |
| 56 | XBTUSD | breakout | 2025-01-17 | 2025-01-18 | 2025-02-10 | stop | 104506.16 | 96087.11 | -1.11 |
| 57 | XBTUSD | breakout | 2025-05-08 | 2025-05-09 | 2025-06-06 | stop | 103640.91 | 101173.38 | -0.43 |
| 58 | ETHUSD | breakout | 2025-05-08 | 2025-05-09 | 2025-06-21 | stop | 2216.89 | 2397.08 | 0.86 |
| 59 | ETHUSD | breakout | 2025-07-09 | 2025-07-10 | 2025-08-20 | stop | 2781.41 | 4058.34 | 5.16 |
| 60 | XBTUSD | breakout | 2025-07-10 | 2025-07-11 | 2025-08-20 | stop | 116454.16 | 112379.08 | -0.75 |
| 61 | ETHUSD | breakout | 2025-08-22 | 2025-08-23 | 2025-09-02 | stop | 4849.33 | 4297.29 | -1.09 |
| 62 | XBTUSD | breakout | 2025-10-02 | 2025-10-03 | 2025-10-11 | stop | 121132.60 | 112548.00 | -1.72 |
| 63 | ETHUSD | breakout | 2026-08-19 | 2026-08-20 | 2026-10-09 | stop | 2260.51 | 2463.01 | 1.38 |

## Positions open at the end

Still held at the last close, marked at that close before exit costs. They are excluded from the trade stats (trades, win rate, mean and median R, halves); the equity metrics include them.

| Symbol | Setup | Entry | Entry price | Last close | Unrealized P&L |
| --- | --- | --- | --- | --- | --- |
| XBTUSD | breakout | 2026-08-20 | 69562.14 | 82912.70 | 234.35 |
