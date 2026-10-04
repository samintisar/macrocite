# SignalBench

SignalBench is a personal swing-trading assistant for US-company CDRs (Canadian Depositary Receipts) traded on Cboe Canada. It researches strategies against stored data, then runs the one strategy that was chosen: every evening it scans for signals, sends them to Telegram, and records what the owner actually did in a ledger.

The project is deliberately **not a trading bot**. It does not connect to a broker, place orders, or make autonomous investment decisions. Trading rules are code, pre-registered in git before they run, and the owner reviews every alert and places every trade by hand on Wealthsimple. Nothing here is investment advice.

## Why this project exists

Discretionary swing trading is hard to evaluate honestly. It's easy to remember the wins, forget the losses, and change the rules after seeing the outcome. This project keeps the trading logic in code and version control instead of in the owner's head:

- setups are defined and pre-registered before they're used, and every experiment is logged, including the ones that failed;
- every ingested data point, rule change, and trade is recorded and reviewable; and
- the owner still makes every decision and places every order by hand. This system informs, it does not act.

## How it went

1. **Data foundation (spec 01).** A CDR universe, prices since 2010, SEC 8-Ks, earnings dates, and news in PostgreSQL.
2. **Backtest (spec 02).** Two rule-based setups, Pullback and Breakout, were tested once against a pre-registered pass bar. Both failed.
3. **Jev reader (spec 03).** An LLM read about 85,000 filings and news items to see if it could filter or trigger trades. It showed no predictive signal, so it was dropped from the strategy.
4. **Breakout v2 (spec 06).** Six variants of Breakout (holding-time limit and where idle cash sits) were tested on data already seen. Five passed, which on its own decides nothing.
5. **Go-live (specs 04 and 05).** The owner chose one variant, `v2-none-cash`, and built the tooling to trade it: a ledger with a tax cost-base report, an evening scan, and a Telegram bot. Forward paper trading (spec 07) was built, never started, and then removed.

The Breakout v2 numbers come from the same 2012 to 2026 data the variants were picked on, so they are a hypothesis, not proof of an edge. Live results are the first out-of-sample evidence.

## Current capabilities

**Research**

- Builds the US-company CDR universe from Cboe Canada's listing directory, checked in at `data/cdr_universe.yaml`.
- Ingests daily OHLCV prices for US stocks, CDRs (`.NE`), and the QQQ/SPY benchmarks from 2010, with incremental upsert and split-safe refetch.
- Ingests SEC 8-K filings since 2016 (acceptance time, item codes, EX-99 exhibits, cleaned text), Finnhub company news, and earnings dates from SEC Item 2.02 filings plus the Finnhub calendar.
- Backtests rule-based setups against pre-registered configs (`data/strategy_*.yaml`) with costs from a measured CDR spread survey, a NYSE session calendar, and a pass bar.
- Has Jev read stored filings and news, decides by a pre-registered rule whether a negative reading may block entries, and writes a calibration report.

**Live (the one strategy, `data/strategy_v2-none-cash.yaml`)**

- **Evening scan.** After the NYSE close, ingests prices and earnings dates, checks for stock splits, and runs the same `decide()` function the backtest uses against the real portfolio. It sends exit alerts first, then stop raises, then new entries with a size, a limit price, and a template "why" line.
- **Telegram bot.** Long-polling bot that takes fills back through buttons and commands, allowlisted to the owner's chat.
- **Ledger.** Positions, cash, equity, peak, pause state, and each stop are derived from fills, cash movements, and corporate actions alone. Fills are never deleted, only voided.
- **Tax report.** CRA-style average cost (ACB), CDR split handling, and superficial-loss adjustments, as a record-keeping aid (not tax advice).
- **Safety rails.** A frozen config (the scan refuses to run if its sha256 changed), code pinned to a `live-v*` git tag, a Postgres advisory lock so only one scan runs at a time, an idempotent scan with catch-up for missed sessions, a scale-up check, and Windows toasts when a scan fails or goes stale.
- Provides a minimal FastAPI health endpoint for service checks.

## Architecture

```text
Cboe CDR directory ──> data/cdr_universe.yaml ──> tickers (US stock, CDR, benchmark)
yfinance ─────────────> prices ──> liquidity flags
SEC EDGAR ────────────> 8-Ks (+ EX-99, clean text) ──> earnings events (Item 2.02)
Finnhub ──────────────> news, upcoming earnings

prices + earnings ──> strategy decide() (pure, pre-registered) ──> simulator ──> pass-bar report
8-Ks + news ────────> Jev reader (OpenRouter) ──> jev_readings ──> filter decision, calibration

Evening scan (Task Scheduler, pinned to a live-v* tag)
  ingest ──> split check ──> decide() on the ledger's real portfolio ──> live CDR sizing
         ──> Telegram: exits, stop raises, entries, summary
Telegram bot (long polling) <──> ledger (fills, cash, splits) ──> equity, stops, ACB tax report
```

## Tech stack

| Area | Technologies |
| --- | --- |
| Language and tooling | Python 3.11+, `uv`, Typer |
| API | FastAPI, Uvicorn |
| Data and persistence | PostgreSQL, SQLModel, SQLAlchemy, Alembic |
| Filing text | BeautifulSoup + lxml |
| Backtesting | NumPy, `exchange-calendars` (NYSE and Cboe sessions) |
| LLM reader | OpenRouter (Jev), with a fake client for tests |
| Market and filing data | SEC EDGAR, `yfinance`, Finnhub |
| Alerts | `python-telegram-bot` (long polling), Windows toast notifications |
| Scheduling | Windows Task Scheduler, PowerShell scripts, Docker Compose (Postgres) |
| Quality | pytest, Ruff, mypy, GitHub Actions |

## Quick start

### Prerequisites

- Python 3.11 or newer
- [`uv`](https://docs.astral.sh/uv/)
- Docker Compose v2
- A SEC contact email for filing ingestion
- A Finnhub API key for news and the earnings calendar ([free tier](https://finnhub.io/register))
- For the live scan and bot: a Telegram bot token from @BotFather and your chat id

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

For the live scan and bot (spec 05), add the Telegram settings. The bot only answers `TELEGRAM_CHAT_ID`, and the token is masked in all logs and stored errors:

```dotenv
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_CHAT_ID=your-chat-id
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

### 4. Go live (optional)

```bash
uv run signalbench live start
uv run signalbench scan --dry-run --as-of 2026-09-30
```

`live start` writes the frozen live config once. It refuses an uncommitted or edited config file, and refuses to run twice. `scan --dry-run` prints every message to the console and writes and sends nothing.

The scheduled scan and bot run from a git worktree pinned to a `live-v*` tag, so the code that trades changes only by a deliberate checkout of a new tag. `scripts/windows/register-tasks.ps1` prints the exact Task Scheduler commands and registers them only with `-Register`:

```powershell
.\scripts\windows\register-tasks.ps1 -Worktree <path-to-live-worktree>
```

## CLI reference

**Data and research**

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
| `uv run signalbench backtest run --setup {pullback,breakout,sentiment,combined} [--jev {off,filter}] [--config PATH]` | Simulate on stored data with a committed strategy config, store the run, and write `reports/backtests/<date>-<setup>-<jev>.md`. `--jev filter` is information only |
| `uv run signalbench backtest show RUN_ID` | Reprint a stored run's report |
| `uv run signalbench jev test` | One real Jev call on a small made-up 8-K; prints the answers, latency, cost, and resolved build |
| `uv run signalbench jev backfill [--source filings\|news\|all] [--since DATE] [--max-cost-usd N]` | Read every unread 8-K and news item once per universe ticker; resumable; stops cleanly at the budget (default $10) |
| `uv run signalbench jev fit-filter [--write]` | The pre-registered filter decision; `--write` records it once |
| `uv run signalbench jev calibration` | Write `reports/jev/<date>-calibration.md` (information only) |

**Live strategy**

| Command | Purpose |
| --- | --- |
| `uv run signalbench live start` | Record the frozen live config (path, sha256, start commit); once only |
| `uv run signalbench scan [--dry-run] [--as-of DATE] [--force]` | Run the evening scan for the latest complete session, with catch-up for missed ones; idempotent |
| `uv run signalbench scan status [--stale-after-days N]` | Last scan result; exits 3 when no scan has succeeded for too long |
| `uv run signalbench bot run` | Poll Telegram for the owner's commands and button presses until stopped |
| `uv run signalbench ledger tax YEAR [--csv PATH]` | The ACB, gain/loss, and superficial-loss report for a tax year |
| `uv run signalbench ledger split SYMBOL RATIO EX_DATE` | Record a split that yfinance missed |
| `uv run signalbench ledger void-action ID REASON` | Void a wrong corporate action; its effects are recomputed |

**Telegram bot commands:** `/signals`, `/portfolio`, `/pnl`, `/buy`, `/sell`, `/void`, `/deposit`, `/withdraw`, `/resume`, `/status`, `/tax YEAR`, and `/help`. A fill at a price more than 10% from the latest CDR mark asks for confirmation first, and each recorded update is stored once even if Telegram redelivers it.

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

Over 600 tests cover universe parsing, price ingestion and split-safe adjustment, the liquidity filter, filing ingestion and text cleaning, earnings-date clustering, news ingestion, the strategy rules, simulator (including look-ahead and money-conservation checks), metrics, and pass bar, the Jev client and backfill (against a fake client and mocked HTTP), the filter decision and calibration, the ledger (hand-checked ACB and CDR-split fixtures, voids, stops, equity, pause and resume), the scan's steps and idempotency, live sizing, the Telegram messages and bot (against a fake messenger), CLI wiring, migrations, and API health. All tests run against in-memory SQLite or fixtures and make no network calls.

## Project layout

```text
src/signalbench/
├── api/            FastAPI application and health endpoint
├── backtest/       Simulator, run metrics, benchmarks, pass bar, reports, and the backtest runner
├── strategy/       Pre-registered config, indicators, market view, and the pure decide()
├── db/             SQLModel models, database sessions, and the advisory lock
├── ingest/         CDR universe, prices, filings, earnings, news, and seeding
├── jev/            Jev client, backfill, filter decision, and calibration
├── live/           Ledger, ACB/tax, sizing, the evening scan, messages, outbox, and the Telegram bot
└── market/         Adjusted OHLC bars and the NYSE and Cboe session calendars
data/               CDR universe, spread survey, pre-registered strategy configs (v1 and six v2 variants), and the Jev filter decision
reports/            Committed backtest reports, plus Jev filter-decision and calibration reports
docs/               Specs 00-07, implementation plans, and the research log
scripts/            live_scan.ps1, live_bot.ps1, and the Task Scheduler registration
alembic/            Database migrations
tests/              Unit, integration, and regression tests
```

## Results so far

Every experiment is logged in [`docs/research-log.md`](docs/research-log.md), including the ones that went nowhere. Rules were fixed and committed before each formal run.

**Spec 02, v1 setups (formal, 2012 to 2026-09-24):**

| Setup | Trades | Mean R after costs | Sharpe | QQQ buy-and-hold Sharpe | Result |
| --- | --- | --- | --- | --- | --- |
| Pullback | 908 | -0.071 | -0.19 | 1.00 | Fail |
| Breakout | 419 | +0.311 | 0.95 | 1.00 | Fail (Sharpe only; both halves positive) |

**Spec 03, Jev (formal):**

- The negative-news filter stayed off. Trades that followed negative news did better than the rest (confirmation window: +0.227 R blocked vs -0.007 R kept), so blocking them would have cut the best trades.
- The Sentiment setup failed (409 trades, mean R 0.098 vs a 0.100 bar, Sharpe 0.58 vs QQQ 0.95).
- Calibration found no information: about 29% of readings were followed by an "up" week in every `p_positive` decile, so Jev's reading of public filings and news does not predict the next week's move in these widely followed stocks.

**Spec 06, Breakout v2 (post-hoc, 6 variants on data already seen, so it decides nothing on its own):**

| Variant | Trades | Total return | Sharpe | Max drawdown | Result |
| --- | --- | --- | --- | --- | --- |
| v1 Breakout | 419 | 610.6% | 0.95 | 24.8% | Fail |
| **v2-none-cash (live)** | 324 | 1,029.3% | 1.12 | 26.1% | Pass |
| v2-none-qqq | 322 | 2,031.1% | 1.04 | 36.2% | Pass |
| QQQ buy-and-hold | | 1,376.1% | 1.00 | 35.1% | |

Five of six variants passed. On 2026-09-29 the owner chose `v2-none-cash` (Breakout, no time limit, 3-ATR trailing stop, idle cash stays cash) to trade directly and skipped forward paper trading. Reports are in [`reports/backtests/`](reports/backtests/) and [`reports/jev/`](reports/jev/).

## Limitations and scope

SignalBench is a personal research and trading-assistant project, not an investment product or advice.

- It does not connect to a broker, place trades, manage capital, or provide financial advice. The owner places every trade by hand on Wealthsimple, and Wealthsimple is the source of truth for fills.
- The live strategy is a single frozen config chosen after a post-hoc study, with no out-of-sample test beforehand. Every backtest report prints its caveats: survivorship, US prices standing in for CDR prices, realized earnings dates, and US-only liquidity.
- CDRs trade thinly, so live orders use limit prices and are meant for regular hours, when the measured median spread is about 0.1%.
- The tax report follows the CRA's published average-cost method as a record-keeping aid. The owner verifies their own return.
- Jev is not part of the live strategy. The live scan uses no LLM and a template explanation.
- The interface is Telegram, the CLI, the database, and a minimal health API. There is no dashboard.

## Roadmap

Specs in [`docs/superpowers/specs/`](docs/superpowers/specs/):

- 01: data foundation (done)
- 02: strategy and backtest (done; no-go on both v1 setups)
- 03: Jev reader (done; filter stays off, Sentiment failed, calibration shows no signal)
- 04: ledger (done)
- 05: Telegram bot and evening scan (done; running as `live-v2`)
- 06: Breakout v2 (done; post-hoc, five of six variants passed; the owner chose `v2-none-cash`)
- 07: forward paper trading (retired; built, never started, and removed on 2026-09-30)

Open ideas, none tested yet: slower momentum rotation, fewer constraints on Breakout, and Jev on other horizons. Each reuses already-seen data, so a pass would need unseen data first.

## Contributing

1. Create a branch for your change.
2. Keep ingestion source changes (Cboe, SEC, yfinance, Finnhub) reviewable, noting rate limits and any new test fixtures.
3. Never edit a committed strategy config. A new idea is a new config file, and a change to the live strategy is a new owner decision recorded in the overview spec.
4. Add or update tests for behavior changes.
5. Run `uv run pytest`, `uv run ruff check .`, and `uv run mypy src` locally.

## License

No license has been selected for this repository yet. Until one is added, all rights are reserved by the copyright holder.
