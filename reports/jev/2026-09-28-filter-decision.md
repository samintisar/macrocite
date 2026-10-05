# Jev filter decision (spec 03)

**Result:** information only (nothing is blocked)

**Why:** blocked mean R 0.227 is not below kept mean R -0.007 in the confirmation window.

| Field | Value |
| --- | --- |
| Decided on | 2026-09-28 |
| Question set / model requested | q1 / typesafe/jev-1.13 |
| Readings available | 85314 |
| Resolved builds | typesafe/jev-1.13-20260917 (85314) |
| Source run (pullback, v1, Jev off) | `e692b1c5-3e24-4306-aeba-4a189771ed38` |
| Source run (breakout, v1, Jev off) | `60245151-8be8-4914-9bca-4d9447ad06d7` |
| strategy_v1 config_sha256 | `98037e0b93e9695182524476c5a66ef773ee6455ee8e2e5c7c6ad83cab2b839e` |

## Rule (pre-registered)

Each trade gets the max `p_negative` among the symbol's documents whose legal close falls in the 10 sessions ending on its signal date (no reading: never blocked). A theta is eligible when it blocks at least 10 fit-window trades and keeps at least one. The eligible theta with the largest (kept mean R - blocked mean R) is chosen; ties go to the lower theta. The filter is ON only if, in the confirmation window, at least 10 trades are blocked and their mean R is below the kept mean R.

## Fit window: signals 2016-01-01 to 2022-12-31 (589 trades)

| theta | Blocked | Kept | Mean R blocked | Mean R kept | Kept - blocked | Eligible |
| --- | --- | --- | --- | --- | --- | --- |
| 0.5 | 18 | 571 | 0.384 | -0.025 | -0.409 | yes |
| 0.6 | 17 | 572 | 0.229 | -0.019 | -0.248 | yes |
| 0.7 | 13 | 576 | 0.139 | -0.016 | -0.155 | yes |
| 0.8 | 12 | 577 | 0.261 | -0.018 | -0.279 | yes |
| 0.9 | 11 | 578 | 0.332 | -0.019 | -0.351 | yes |

## Confirmation window: signals 2023-01-01 to 2026-09-24 (361 trades)

| theta | Blocked | Kept | Mean R blocked | Mean R kept |
| --- | --- | --- | --- | --- |
| 0.7 | 80 | 281 | 0.227 | -0.007 |

## Notes

- A run with `--jev filter` is information only: it cannot change the spec 02 v1 results (Pullback and Breakout both failed their pass bar).
- Finnhub news covers only about the last year, so the fit window is effectively filings-only.
- The theta grid, the windows, and the 10-trade minimum were fixed before this run. These results must not be used to change them.
