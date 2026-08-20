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

## Run tests

```bash
uv run pytest
```
