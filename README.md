# SignalBench

SignalBench is a personal swing-trading research assistant for US-company CDRs (Canadian Depositary Receipts) traded on Cboe Canada.

The project is deliberately **not a trading bot**. It does not connect to a broker, place orders, or make autonomous investment decisions. Trading setups are rule-based and run as code, not model guesses; a reader called Jev (spec 03, TypeSafe's decision model through OpenRouter) reads SEC filings and news as a supporting signal. The owner reviews every candidate and places every trade by hand on Wealthsimple. Nothing here is investment advice.

## Why this project exists

Discretionary swing trading is hard to evaluate honestly — it's easy to remember the wins, forget the losses, and change the rules after seeing the outcome. This project keeps the trading logic in code and version control instead of in the owner's head:

- setups are defined and pre-registered before they're used live;
- every ingested data point and rule change is recorded and reviewable in git; and
- the owner still makes every decision and places every order by hand — this system informs, it does not act.

## Current capabilities

- Builds the US-company CDR universe from Cboe Canada's listing directory, checked in at `data/cdr_universe.yaml`.
- Ingests daily OHLCV prices for US stocks, CDRs (`.NE`), and the QQQ/SPY benchmarks from 2010, with incremental upsert.
- Flags tickers active or inactive with a liquidity filter (US-stock traded value; CDR price recency).
- Ingests SEC 8-K filings since 2016, with acceptance time, item codes, EX-99 exhibits, and cleaned filing text.
- Records earnings dates from SEC Item 2.02 filings and the Finnhub earnings calendar.
- Ingests Finnhub company news per ticker.
- Has Jev read each stored 8-K and news item once per ticker, decides by a pre-registered rule whether a negative reading may block entries, backtests the Sentiment setup, and writes a calibration report (spec 03).
- Stores tickers, prices, filings/news, and earnings events in PostgreSQL.
- Provides a minimal FastAPI health endpoint for service checks.

## Architecture

```text
Cboe CDR directory ──> data/cdr_universe.yaml ──> tickers (US stock, CDR, benchmark)
yfinance ─────────────> prices ──> liquidity flags
SEC EDGAR ────────────> 8-Ks (+ EX-99, clean text) ──> earnings events (Item 2.02)
Finnhub ──────────────> news, upcoming earnings
```

## Tech stack

| Area | Technologies |
| --- | --- |
| Language and tooling | Python 3.11+, `uv`, Typer |
| API | FastAPI, Uvicorn |
| Data and persistence | PostgreSQL, SQLModel, SQLAlchemy, Alembic |
| Filing text | BeautifulSoup + lxml |
| Market and filing data | SEC EDGAR, `yfinance`, Finnhub |
| Quality | pytest, Ruff, mypy, GitHub Actions |

## Quick start

### Prerequisites

- Python 3.11 or newer
- [`uv`](https://docs.astral.sh/uv/)
- Docker Compose v2
- A SEC contact email for filing ingestion
- A Finnhub API key for news and the earnings calendar ([free tier](https://finnhub.io/register))

### 1. Install dependencies and start PostgreSQL

```bash
uv sync --group dev
docker compose up -d
```

### 2. Configure the environment

```bash
cp .env.example .env
```

Update `.env` with a real SEC contact email and a Finnhub key:

```dotenv
SEC_USER_AGENT=SignalBench/0.1 (you@example.com)
FINNHUB_API_KEY=your-finnhub-api-key
```

For the Jev reader (spec 03), also add an OpenRouter key. `signalbench jev test` and `jev backfill` stop with a clear message without it:

```dotenv
OPENROUTER_API_KEY=your-openrouter-api-key
```

The default database URL matches `docker-compose.yml` (port 5432). If you run Postgres on 5433 via `docker-compose.port.yml`, update the port to match:

```dotenv
DATABASE_URL=postgresql+psycopg://signalbench:signalbench@localhost:5432/signalbench
```

### 3. Create the schema and load data

```bash
uv run alembic upgrade head
uv run signalbench seed
uv run signalbench ingest all
uv run signalbench ingest stats
```

`seed` loads tickers from the checked-in `data/cdr_universe.yaml` plus the QQQ/SPY benchmarks. `ingest all` runs prices, filings, earnings, and news in order. `ingest stats` prints per-table row counts so you can confirm data landed.

## CLI reference

| Command | Purpose |
| --- | --- |
| `uv run signalbench universe refresh [--write]` | Diff the Cboe CDR directory against `data/cdr_universe.yaml`; write only with `--write` |
| `uv run signalbench seed` | Load tickers from `data/cdr_universe.yaml` plus the QQQ/SPY benchmarks |
| `uv run signalbench ingest prices [--full]` | Fetch and upsert daily OHLCV for tickers and benchmarks; `--full` refetches from `PRICE_HISTORY_START` even for tickers with recent rows |
| `uv run signalbench ingest filings [--backfill-text]` | Fetch SEC 8-Ks since 2016; `--backfill-text` fills text/acceptance/items for rows stored before this spec |
| `uv run signalbench ingest earnings` | Record earnings dates from SEC Item 2.02 filings and the Finnhub calendar |
| `uv run signalbench ingest news` | Fetch Finnhub company news per ticker |
| `uv run signalbench ingest all` | Run prices, filings, earnings, and news in order |
| `uv run signalbench ingest stats` | Print per-table row counts |
| `uv run signalbench backtest cost [--survey PATH]` | Median CDR spread from `data/cdr_spread_survey.yaml` and the resulting cost per side |
| `uv run signalbench backtest run --setup {pullback,breakout,sentiment,combined} [--jev {off,filter}] [--config PATH]` | Simulate on stored data with the committed `data/strategy_v1.yaml`, store the run, and write `reports/backtests/<date>-<setup>-<jev>.md`. `sentiment` needs Jev readings and runs 2016 → end; `--jev filter` is information only and needs a committed `data/jev_filter_v1.yaml` with the filter ON |
| `uv run signalbench backtest show RUN_ID` | Reprint a stored run's report |
| `uv run signalbench jev test` | One real Jev call on a small made-up 8-K; prints the answers, latency, cost, and resolved build |
| `uv run signalbench jev backfill [--source filings\|news\|all] [--since DATE] [--max-cost-usd N]` | Read every unread 8-K and news item once per universe ticker (news capped at 20 per symbol per day); resumable; stops cleanly at the budget (default $10) |
| `uv run signalbench jev fit-filter [--write]` | The pre-registered filter decision; `--write` records `data/jev_filter_v1.yaml` and `reports/jev/<date>-filter-decision.md` once |
| `uv run signalbench jev calibration` | Write `reports/jev/<date>-calibration.md` (information only) |

## Run the API locally

The API currently exposes a health check intended for service and deployment verification:

```bash
uv run uvicorn signalbench.api.main:app --reload
```

Then open <http://127.0.0.1:8000/health> or query it with:

```bash
curl http://127.0.0.1:8000/health
```

Expected response:

```json
{"status":"ok"}
```

## Testing and static checks

Run the full test suite:

```bash
uv run pytest
```

Run the local quality checks:

```bash
uv run ruff check .
uv run mypy src
```

Tests cover CDR universe parsing, price ingestion and adjustment, the liquidity filter, filing ingestion and text cleaning, earnings-date clustering, Finnhub news ingestion, the Jev client and backfill (against a fake client and mocked HTTP), the filter decision, calibration, CLI wiring, migrations, and API health. All tests run against in-memory SQLite or fixtures and make no network calls.

## Project layout

```text
src/signalbench/
├── api/            FastAPI application and health endpoint
├── backtest/       Simulator, run metrics, benchmarks, pass bar, reports, and the backtest runner (spec 02)
├── strategy/       Pre-registered config, indicators, market view, and the pure decide() (spec 02)
├── db/             SQLModel models and database sessions
├── ingest/         CDR universe, prices, filings, earnings, news, and seeding
├── jev/            Jev client, backfill, filter decision, and calibration (spec 03)
└── market/         Adjusted OHLC bars and the NYSE session calendar
data/               Checked-in CDR universe, spread survey, strategy parameters, and the Jev filter decision
reports/            Committed backtest, filter-decision, and calibration reports
alembic/            Database migrations
tests/              Unit, integration, and regression tests
```

## Limitations and scope

SignalBench is a personal research and data-ingestion project, not an investment product or advice.

- The strategy and backtest exist (spec 02), but nothing sends signals yet; live signals arrive with the bot in spec 05. Every backtest report prints its caveats: survivorship, US prices standing in for CDR prices, realized earnings dates, and US-only liquidity.
- The project does not connect to a broker, place trades, manage capital, or provide financial advice. The owner places every trade by hand on Wealthsimple.
- The current data scope is prices, SEC 8-K filings since 2016, earnings dates, and Finnhub news for the checked-in CDR universe.
- The interface today is the CLI, database, and a minimal health API; there is no dashboard or bot yet (the bot lands in spec 05).

## Roadmap

Specs 02–05 in [`docs/superpowers/specs/`](docs/superpowers/specs/):

- 02 — strategy and backtest
- 03 — Jev reader
- 04 — ledger
- 05 — Telegram bot and evening scan

## Contributing

1. Create a branch for your change.
2. Keep ingestion source changes (Cboe, SEC, yfinance, Finnhub) reviewable, noting rate limits and any new test fixtures.
3. Add or update tests for behavior changes.
4. Run `uv run pytest`, `uv run ruff check .`, and `uv run mypy src` locally.

## License

No license has been selected for this repository yet. Until one is added, all rights are reserved by the copyright holder.
