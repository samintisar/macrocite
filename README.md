# SignalBench

SignalBench is a signal-extraction research console for macro and market data. It is **not** a trading bot.

## Prerequisites

- [uv](https://docs.astral.sh/uv/)
- [Docker Compose](https://docs.docker.com/compose/) (v2)

## Setup

Start Postgres:

```bash
docker compose up -d
```

Install Python dependencies:

```bash
uv sync
```

For development (includes pytest, ruff, mypy):

```bash
uv sync --group dev
```

Copy environment variables:

```bash
cp .env.example .env
```

SEC EDGAR requires a contact email in the User-Agent. Edit `.env` and replace the placeholder:

```
SEC_USER_AGENT=SignalBench/0.1 (you@example.com)
```

## Ingest data

These commands load research data into Postgres. They are not a trading bot.

```bash
uv run alembic upgrade head
uv run signalbench seed-watchlist
uv run signalbench ingest filings
uv run signalbench ingest prices
```

`seed-watchlist` resolves `data/watchlist.yaml` from the repo root, so it works from any working directory.

## Run tests

```bash
uv run pytest
```
