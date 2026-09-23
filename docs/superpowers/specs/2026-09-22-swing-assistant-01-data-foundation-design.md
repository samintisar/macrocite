# Spec 01 — Data Foundation

**Parent:** [Overview](2026-09-22-swing-assistant-00-overview.md)
**Goal:** Clean out the retired sentiment pipeline, then ingest everything the strategy, Jev, and bot need, stored and tested: the CDR universe, long price history, upgraded 8-Ks, earnings dates, and Finnhub news.

## Part A — Housekeeping

1. **Close paper book v1.1.** Append a `## Closed — 2026-09-22` section to `docs/personal/paper-trading-playbook.md`. Contents: closed before its freeze ended because the strategy was replaced by the swing assistant design; 0 trades logged; no results exist; closing is not a response to results. Do not edit any other part of the file.
2. **Remove the Together pipeline:** `src/signalbench/extraction/`, `src/signalbench/eval/`, `prompts/`, `evals/`, the `extract` and `eval` CLI commands, the `together` dependency, and the `together_api_key`, `model_version`, and `prompt_version` settings. Remove the tests that only cover removed code.
3. **Remove the sentiment backtest:** `backtest/strategy.py`, `backtest/run.py`, the `backtest` CLI command, and `data/backtest_config.yaml`. Keep `backtest/metrics.py` and `backtest/fingerprint.py` with their tests, since spec 02 reuses them.
4. **Migration `0007`:** drop the `signals`, `eval_runs`, `backtest_runs`, and `backtest_configs` tables. Drop the related Postgres enums with `checkfirst` (see `0005`/`a5dc08f` for the existing idempotency pattern).
5. **CI:** replace `.github/workflows/eval.yml` with `ci.yml`, which runs `uv run pytest`, `uv run ruff check .`, and `uv run mypy src` on every push and PR. Tests use in-memory SQLite and make no network calls.
6. **README:** rewrite Current capabilities, Architecture, and the CLI reference for the new direction. The working tree has an uncommitted README edit; merge with it, don't overwrite it.

## Part B — CDR universe

**Source:** the JSON behind Cboe Canada's listing directory, `GET https://www-api.cboe.com/ca/equities/listing-directory-data/`, which returns `{"data": [{"symbol", "name", "currency", "security", "security_sub_type", "marketcap", "last", "changepcnt", "volume"}, ...]}`. CDRs are rows with `security == "dr"`. A US-company CDR has its US ticker in parentheses in `name`, e.g. `"NVIDIA (NVDA) BMO CDR (CAD HEDGED)"` → `NVDA`. Rows without a parenthesised ticker (foreign issuers such as Toyota or ASML, which don't file 8-Ks) are excluded. Share-class slashes are normalised for yfinance and SEC (`BRK/B` → `BRK-B`). On 2026-09-22 this gave about 40 US names.

**Stored as a checked-in file,** `data/cdr_universe.yaml`, not scraped on every run. The list changes rarely, and reviewing changes in git is part of the discipline.

```yaml
- us_symbol: NVDA
  cdr_symbol: ZNVD         # as listed on Cboe Canada
  price_symbol: ZNVD.NE    # yfinance symbol; `.NE` verified 2026-09-22 (`.TO` returns nothing)
  company_name: NVIDIA
  sector: Information Technology
```

- `signalbench universe refresh` fetches the Cboe JSON, prints a diff against the YAML (added and removed names), and writes it only with `--write`.
- **Sector** uses GICS sector names from yfinance `info["sector"]`. yfinance uses its own names (e.g. "Technology"); map them to GICS in one dictionary in `ingest/cdr.py`, and fail the refresh loudly on any unmapped name.
- `company_name` is the text before the first ` (` in `name`, title-cased.
- CDR price history is short (ZNVD starts 2025-10-16; new listings have days). That is expected. The backtest uses US prices (spec 02).

**Tables (migration `0008`).** Extend `tickers` with `kind` (`us_stock` | `cdr` | `benchmark`), `sector`, and `us_ticker_id` (nullable FK, set on CDR rows). The `data/watchlist.yaml` seeding is replaced by seeding from `cdr_universe.yaml` plus benchmarks `QQQ` and `SPY`. Delete `data/watchlist.yaml` and `seed-watchlist`.

**Liquidity filter,** evaluated nightly and stored as `tickers.active`:
- US stock: 20-session median traded value (close × volume) ≥ US$50M
- CDR: has a stored price row with a non-null close in the last 5 sessions. There is deliberately no CDR volume threshold, because most CDRs trade a few thousand dollars a day or less and fills come from market-maker quotes. The spread limit is applied live (spec 05).
- Both must pass. A name that fails stays in the YAML but is inactive for new signals. Open positions in it are still managed.

## Part C — Prices

- US stocks and benchmarks: daily OHLCV from `2010-01-01` (enough for a 200-session warm-up before the 2012 backtest start). CDRs: all available history.
- Store raw `open/high/low/close/volume` plus `adj_close` (as today). Readers compute split/dividend-adjusted OHLC with `factor = adj_close / close` applied to open, high, and low. This lives in one helper, `adjusted_bars()`, with a test on a split day.
- **Incremental:** each run fetches from the last stored date minus 5 sessions (to pick up late adjustments) and upserts. Fix the current two-year window: the start date comes from config, not a hard-coded lookback.
- Reject rows with NaN or non-positive prices or `high < low`, and log them. yfinance returns a NaN close for the current, unfinished session.

## Part D — 8-K upgrade

Extend `raw_documents` (also migration `0008`):
- `acceptance_at` (UTC, nullable only for non-SEC sources), from submissions `acceptanceDateTime`
- `items` (text, e.g. `"2.02,9.01"`), from the submissions `items` field
- `text` (cleaned plain text used by Jev); `raw_text` keeps the original

**Coverage:**
- Page through `filings.files` in the submissions JSON so filings older than the `recent` block are reached.
- Backfill 8-Ks accepted on or after `2016-01-01` for every US ticker in the universe.
- Also accept `8-K/A` amendments, stored with their own accession number.

**Exhibits:** fetch the filing index (`{accession}-index.json`) and download every `EX-99.*` document (these hold press releases and earnings results). `text` = cleaned primary document + `\n\n---\n\n` + each cleaned EX-99 in index order.

**Cleaning:**
- Parse HTML with BeautifulSoup + lxml. Drop `script`, `style`, and XBRL `ix:header` blocks. Collapse whitespace and keep paragraph breaks.
- Remove the signature block and the standard "forward-looking statements" safe-harbor paragraph when it can be detected by heading text.
- Cap `text` at 96,000 characters (about 24K tokens) and keep the beginning.

**Existing rows:** a one-off `signalbench ingest filings --backfill-text` fills `acceptance_at`, `items`, and `text` for documents already stored.

**Rate limit:** keep the existing 429 retry. Stay under SEC's 10 requests/second with a shared limiter set to 8/s.

## Part E — Earnings dates

- **History (for backtests):** the acceptance date of each 8-K whose `items` contains `2.02` (Results of Operations). No extra source is needed. Store in `earnings_events(ticker_id, event_date, source='sec_2.02')` (migration `0008`).
- **Upcoming (for live blackout and exits):** the Finnhub earnings calendar for the next 30 days, source `finnhub`. Upsert daily. When a `sec_2.02` row later lands within 3 days of a `finnhub` row, keep both. Readers take the earliest date in any 3-day cluster.
- **Known look-ahead, accepted:** earnings dates are announced weeks in advance, so the backtest using realized 2.02 dates is a close stand-in for knowing them ahead. Every backtest report states this.

## Part F — Finnhub news

- Endpoint: company news, per US symbol, `from`/`to` date range. The free tier covers about 1 year of history and 60 requests/minute.
- Store in `raw_documents` with `source='finnhub'` and `doc_type=news`. `external_id` = Finnhub article `id`. `published_at` = article datetime. `acceptance_at` = null. `text` = `headline + "\n\n" + summary`. `url` = article URL.
- Link to tickers through `document_tickers` using the queried symbol only. Finnhub's `related` field is ignored to avoid wrong links.
- **Backfill** the full available year once. Nightly runs fetch from the last stored `published_at` minus 2 days. Dedupe on `external_id`.
- **Rate limit:** a limiter at 50 requests/minute. On a 429, back off and retry up to 5 times.
- Copyright: store only the headline, summary, and URL that the API returns. No article scraping.

## Configuration

New `.env` keys: `FINNHUB_API_KEY`. Settings gain `price_history_start` (default `2010-01-01`) and `filings_backfill_start` (default `2016-01-01`).

## CLI after this spec

```text
signalbench universe refresh [--write]
signalbench seed                      # tickers from cdr_universe.yaml + benchmarks
signalbench ingest prices
signalbench ingest filings [--backfill-text]
signalbench ingest earnings
signalbench ingest news
signalbench ingest all                # in order: prices, filings, earnings, news
```

## Testing

- Fixtures (no network): the Cboe page HTML snippet, submissions JSON with `files` paging and `items`, a filing index JSON with two EX-99 exhibits, 8-K HTML with a signature and safe-harbor block, a Finnhub news page, a Finnhub earnings calendar page, a yfinance frame with a split.
- The cleaner keeps Item text and exhibit text, and drops scripts, XBRL headers, and signatures.
- Pagination reaches filings in `files`. Re-running ingest creates no duplicates.
- `adjusted_bars()` is correct across a split.
- Liquidity filter at exactly the thresholds.
- Earnings clustering takes the earliest date.
- Migrations `0007`/`0008` upgrade from `0006` on Postgres.

**Migration numbering across specs:** 01 → `0007`, `0008`; 02 → `0009`; 03 → `0010`; 04 → `0011`; 05 → `0012`.

 This is run locally; CI covers SQLite `create_all`.

## Gate

- CI green (pytest, ruff, mypy strict).
- A real local `signalbench ingest all` completes and prints per-table counts. Record them in the plan's completion notes: universe size, active names, price rows, 8-Ks since 2016, share with at least one EX-99, news rows, earnings events.
- `signalbench ingest prices` stores CDR rows for at least 30 CDRs.

## Changelog

- 2026-09-22: created.
- 2026-09-22: the universe source is the Cboe JSON endpoint (the page itself renders client-side). `.NE` is verified. The CDR traded-value filter was replaced by "priced in the last 5 sessions".
