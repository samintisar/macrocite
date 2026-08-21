# Phase 0 — Ingest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Models:** cheap implementers `composer-2.5`; mid-tier implementers, reviewers, and fix loops `cursor-grok-4.6-high`. Never `*-fast` models. See [docs/superpowers/README.md](../README.md).

**Goal:** Get a fixed 20-ticker watchlist, 8-K filings, and daily OHLCV (including `adj_close`) into Postgres via CLI, with mocked tests and a FastAPI `/health` stub.

**Architecture:** Documents are source artifacts with no `ticker_id`. `document_tickers` is ingest routing only (EDGAR issuer). Dedup is `UNIQUE (source, external_id)` where EDGAR `external_id` is the accession number. Prices upsert on `(ticker_id, date)`. No LLM, evals, backtests, news, or embeddings.

**Tech Stack:** Python 3.11, uv, FastAPI, SQLModel, Alembic, Postgres 16 (Docker Compose), Typer, httpx, yfinance, pytest, ruff, mypy.

**Depends on:** empty repo besides [PRD.md](../../../PRD.md) and [docs/superpowers/README.md](../README.md).

**Out of scope:** Together AI / LLM extraction, `signals` table, evals, vectorbt, dashboard, Alpha Vantage, pgvector.

---

## File map

- Create: `pyproject.toml` — project metadata, deps, ruff/mypy/pytest config
- Create: `docker-compose.yml` — Postgres 16 named `signalbench`
- Create: `.env.example` — `DATABASE_URL`, `SEC_USER_AGENT`
- Create: `.gitignore` — `.venv`, `.env`, `__pycache__`, `.mypy_cache`, `.ruff_cache`
- Create: `README.md` — replace leftover "Bank of Canada Macro Agent" with SignalBench runbook
- Create: `data/watchlist.yaml` — 20 liquid US names
- Create: `src/signalbench/__init__.py`
- Create: `src/signalbench/config.py` — pydantic-settings
- Create: `src/signalbench/db/models.py` — Ticker, RawDocument, DocumentTicker, Price
- Create: `src/signalbench/db/session.py` — engine + session factory
- Create: `src/signalbench/db/__init__.py`
- Create: `alembic.ini` + `alembic/env.py` + `alembic/versions/0001_phase0.py`
- Create: `src/signalbench/ingest/seed.py` — upsert watchlist
- Create: `src/signalbench/ingest/edgar.py` — CIK map + 8-K fetch (httpx)
- Create: `src/signalbench/ingest/prices.py` — yfinance daily bars
- Create: `src/signalbench/cli.py` — Typer app
- Create: `src/signalbench/api/main.py` — FastAPI `/health`
- Create: `tests/conftest.py` — SQLite test engine (uuid4 defaults; no pgcrypto)
- Create: `tests/test_health.py`, `tests/test_schema.py`, `tests/test_seed.py`, `tests/test_edgar.py`, `tests/test_prices.py`, `tests/test_cli.py`
- Create: `tests/fixtures/edgar_submissions.json`, `tests/fixtures/edgar_8k.html`, `tests/fixtures/company_tickers.json`
- Modify: [PRD.md](../../../PRD.md) §6.1 — dedup is `(source, external_id)`, not `(source, url, published_at)`

---

### Task 1: Scaffold uv project and Compose

**Files:**
- Create: `pyproject.toml`
- Create: `docker-compose.yml`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `src/signalbench/__init__.py`
- Create: `tests/test_smoke.py`
- Modify: `README.md`

Config files have no production behavior; the first test is a smoke import.

- [ ] **Step 1: Write the failing smoke test**

```python
# tests/test_smoke.py
def test_package_importable() -> None:
    import signalbench

    assert signalbench.__name__ == "signalbench"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_smoke.py -v
```

Verify: command fails (no `pyproject.toml` / package).
Expect: `ERROR` or `ModuleNotFoundError: No module named 'signalbench'`.

- [ ] **Step 3: Write minimal scaffold**

```toml
# pyproject.toml
[project]
name = "signalbench"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.32",
  "sqlmodel>=0.0.22",
  "psycopg[binary]>=3.2",
  "alembic>=1.14",
  "httpx>=0.28",
  "typer>=0.15",
  "pyyaml>=6.0",
  "yfinance>=0.2",
  "pydantic-settings>=2.6",
]

[dependency-groups]
dev = [
  "pytest>=8.3",
  "ruff>=0.8",
  "mypy>=1.13",
  "types-pyyaml>=6.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/signalbench"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]

[tool.ruff]
target-version = "py311"
src = ["src", "tests"]

[tool.mypy]
python_version = "3.11"
strict = true
packages = ["signalbench"]
```

```yaml
# docker-compose.yml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: signalbench
      POSTGRES_PASSWORD: signalbench
      POSTGRES_DB: signalbench
    ports:
      - "5432:5432"
    volumes:
      - signalbench_pg:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U signalbench"]
      interval: 5s
      timeout: 5s
      retries: 10

volumes:
  signalbench_pg:
```

```
# .env.example
DATABASE_URL=postgresql+psycopg://signalbench:signalbench@localhost:5432/signalbench
SEC_USER_AGENT=SignalBench/0.1 (your-email@example.com)
```

```
# .gitignore
.venv/
.env
__pycache__/
.mypy_cache/
.ruff_cache/
*.pyc
dist/
```

```python
# src/signalbench/__init__.py
```

Rewrite `README.md` as SignalBench: research console, not a trading bot; how to `docker compose up -d`, `uv sync`, `uv run pytest`.

- [ ] **Step 4: Run test to verify it passes**

```bash
uv sync --group dev
uv run pytest tests/test_smoke.py -v
```

Verify: `uv run pytest tests/test_smoke.py -v`
Expect: `PASSED`

- [ ] **Step 5: Compose health**

```bash
docker compose up -d
docker compose ps
```

Verify: `docker compose ps`
Expect: `postgres` service `healthy` (or `running` then healthy within ~15s).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml docker-compose.yml .env.example .gitignore README.md src/signalbench/__init__.py tests/test_smoke.py uv.lock
git commit -m "feat: scaffold SignalBench uv project and Postgres Compose"
```

---

### Task 2: FastAPI `/health`

**Files:**
- Create: `src/signalbench/api/main.py`
- Create: `tests/test_health.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_health.py
from fastapi.testclient import TestClient

from signalbench.api.main import app

def test_health_returns_ok() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_health.py -v
```

Verify: `uv run pytest tests/test_health.py -v`
Expect: FAIL `ModuleNotFoundError` or `ImportError` for `signalbench.api.main`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/signalbench/api/main.py
from fastapi import FastAPI

app = FastAPI(title="SignalBench")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

Add `httpx` is already a dep; TestClient needs `fastapi` (included). If pytest complains, add `httpx` usage is fine.

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_health.py -v
```

Verify: `uv run pytest tests/test_health.py -v`
Expect: `PASSED`

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/api/main.py tests/test_health.py
git commit -m "feat: add FastAPI /health stub"
```

---

### Task 3: Phase 0 schema

**Files:**
- Create: `src/signalbench/db/models.py`
- Create: `src/signalbench/db/session.py`
- Create: `tests/conftest.py`
- Create: `tests/test_schema.py`

Document has **no** `ticker_id`. `DocumentTicker` is the M2M. `Price.adj_close` is required. FKs from documents/prices to tickers are `RESTRICT` (do not `CASCADE` ticker deletes).

- [ ] **Step 1: Write the failing tests**

```python
# tests/conftest.py
from collections.abc import Generator

import pytest
from sqlmodel import Session, SQLModel, create_engine

from signalbench.db import models as _models  # noqa: F401  # register tables


@pytest.fixture
def session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
```

```python
# tests/test_schema.py
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from signalbench.db.models import DocumentTicker, Price, RawDocument, Ticker


def test_document_has_no_ticker_id_column() -> None:
    assert "ticker_id" not in RawDocument.model_fields


def test_unique_source_external_id_rejects_duplicate(session: Session) -> None:
    doc = RawDocument(
        source="sec_edgar",
        external_id="0000320193-24-000001",
        doc_type="eight_k",
        url="https://www.sec.gov/example",
        title="Item 2.02",
        raw_text="<html>8-K</html>",
        published_at=datetime(2024, 1, 15, tzinfo=timezone.utc),
    )
    session.add(doc)
    session.commit()
    session.add(
        RawDocument(
            source="sec_edgar",
            external_id="0000320193-24-000001",
            doc_type="eight_k",
            url="https://www.sec.gov/example-2",
            title="dup",
            raw_text="x",
            published_at=datetime(2024, 1, 16, tzinfo=timezone.utc),
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_one_document_can_link_two_tickers(session: Session) -> None:
    aapl = Ticker(symbol="AAPL", company_name="Apple Inc.", active=True)
    msft = Ticker(symbol="MSFT", company_name="Microsoft Corporation", active=True)
    session.add(aapl)
    session.add(msft)
    session.commit()
    session.refresh(aapl)
    session.refresh(msft)
    doc = RawDocument(
        source="sec_edgar",
        external_id="0000320193-24-000002",
        doc_type="eight_k",
        raw_text="mentions both",
        published_at=datetime(2024, 2, 1, tzinfo=timezone.utc),
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    session.add(DocumentTicker(document_id=doc.id, ticker_id=aapl.id))
    session.add(DocumentTicker(document_id=doc.id, ticker_id=msft.id))
    session.commit()
    links = session.exec(select(DocumentTicker).where(DocumentTicker.document_id == doc.id)).all()
    assert {link.ticker_id for link in links} == {aapl.id, msft.id}


def test_price_requires_adj_close(session: Session) -> None:
    ticker = Ticker(symbol="NVDA", company_name="NVIDIA Corporation", active=True)
    session.add(ticker)
    session.commit()
    session.refresh(ticker)
    session.add(
        Price(
            ticker_id=ticker.id,
            date=date(2024, 1, 2),
            open=Decimal("100.0000"),
            high=Decimal("101.0000"),
            low=Decimal("99.0000"),
            close=Decimal("100.5000"),
            adj_close=Decimal("100.5000"),
            volume=1_000_000,
        )
    )
    session.commit()
    row = session.exec(select(Price)).one()
    assert row.adj_close == Decimal("100.5000")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_schema.py -v
```

Verify: `uv run pytest tests/test_schema.py -v`
Expect: FAIL importing `signalbench.db.models`.

- [ ] **Step 3: Write models and session**

```python
# src/signalbench/db/models.py
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional

from sqlalchemy import Column, DateTime, Numeric, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DocType(str, Enum):
    news = "news"
    eight_k = "eight_k"
    ten_k = "ten_k"
    ten_q = "ten_q"


class Ticker(SQLModel, table=True):
    __tablename__ = "tickers"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    symbol: str = Field(unique=True, index=True)
    company_name: str
    sector: Optional[str] = None
    active: bool = Field(default=True)
    added_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))


class RawDocument(SQLModel, table=True):
    __tablename__ = "raw_documents"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_raw_documents_source_external_id"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    source: str
    external_id: str
    doc_type: DocType
    url: Optional[str] = None
    title: Optional[str] = None
    raw_text: str = Field(sa_column=Column(Text, nullable=False))
    published_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    ingested_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))


class DocumentTicker(SQLModel, table=True):
    __tablename__ = "document_tickers"

    document_id: uuid.UUID = Field(foreign_key="raw_documents.id", primary_key=True, ondelete="CASCADE")
    ticker_id: uuid.UUID = Field(foreign_key="tickers.id", primary_key=True, ondelete="RESTRICT")


class Price(SQLModel, table=True):
    __tablename__ = "prices"
    __table_args__ = (UniqueConstraint("ticker_id", "date", name="uq_prices_ticker_date"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    ticker_id: uuid.UUID = Field(foreign_key="tickers.id", ondelete="RESTRICT")
    date: date
    open: Decimal = Field(sa_column=Column(Numeric(12, 4), nullable=False))
    high: Decimal = Field(sa_column=Column(Numeric(12, 4), nullable=False))
    low: Decimal = Field(sa_column=Column(Numeric(12, 4), nullable=False))
    close: Decimal = Field(sa_column=Column(Numeric(12, 4), nullable=False))
    adj_close: Decimal = Field(sa_column=Column(Numeric(12, 4), nullable=False))
    volume: int
```

```python
# src/signalbench/db/session.py
from sqlmodel import Session, create_engine

from signalbench.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)


def get_session() -> Session:
    return Session(engine)
```

```python
# src/signalbench/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://signalbench:signalbench@localhost:5432/signalbench"
    sec_user_agent: str = "SignalBench/0.1 (dev@example.com)"


settings = Settings()
```

```python
# src/signalbench/db/__init__.py
```

Generate Alembic `0001_phase0` to match these tables for Compose Postgres (`sqlmodel` metadata). `alembic upgrade head` against Compose is required before live ingest; tests use `create_all` on SQLite.

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_schema.py -v
```

Verify: `uv run pytest tests/test_schema.py -v`
Expect: 4 passed. `test_document_has_no_ticker_id_column` passes.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/config.py src/signalbench/db tests/conftest.py tests/test_schema.py alembic.ini alembic
git commit -m "feat: add Phase 0 schema with document_tickers and adj_close"
```

---

### Task 4: Seed watchlist

**Files:**
- Create: `data/watchlist.yaml`
- Create: `src/signalbench/ingest/seed.py`
- Create: `tests/test_seed.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_seed.py
from pathlib import Path

from sqlmodel import Session, select

from signalbench.db.models import Ticker
from signalbench.ingest.seed import seed_watchlist

WATCHLIST = Path(__file__).resolve().parents[1] / "data" / "watchlist.yaml"


def test_seed_upserts_twenty_active_tickers(session: Session) -> None:
    count = seed_watchlist(session, WATCHLIST)
    assert count == 20
    rows = session.exec(select(Ticker)).all()
    assert len(rows) == 20
    assert all(row.active for row in rows)
    assert {row.symbol for row in rows} >= {"AAPL", "MSFT", "NVDA"}


def test_seed_is_idempotent(session: Session) -> None:
    seed_watchlist(session, WATCHLIST)
    seed_watchlist(session, WATCHLIST)
    rows = session.exec(select(Ticker)).all()
    assert len(rows) == 20
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_seed.py -v
```

Verify: `uv run pytest tests/test_seed.py -v`
Expect: FAIL `ImportError` for `seed_watchlist`.

- [ ] **Step 3: Write YAML and seeder**

```yaml
# data/watchlist.yaml
tickers:
  - {symbol: AAPL, company_name: Apple Inc.}
  - {symbol: MSFT, company_name: Microsoft Corporation}
  - {symbol: GOOGL, company_name: Alphabet Inc. Class A}
  - {symbol: AMZN, company_name: Amazon.com Inc.}
  - {symbol: NVDA, company_name: NVIDIA Corporation}
  - {symbol: META, company_name: Meta Platforms Inc.}
  - {symbol: TSLA, company_name: Tesla Inc.}
  - {symbol: JPM, company_name: JPMorgan Chase & Co.}
  - {symbol: V, company_name: Visa Inc.}
  - {symbol: UNH, company_name: UnitedHealth Group Inc.}
  - {symbol: XOM, company_name: Exxon Mobil Corporation}
  - {symbol: JNJ, company_name: Johnson & Johnson}
  - {symbol: WMT, company_name: Walmart Inc.}
  - {symbol: PG, company_name: Procter & Gamble Company}
  - {symbol: MA, company_name: Mastercard Inc.}
  - {symbol: HD, company_name: Home Depot Inc.}
  - {symbol: COST, company_name: Costco Wholesale Corporation}
  - {symbol: AVGO, company_name: Broadcom Inc.}
  - {symbol: LLY, company_name: Eli Lilly and Company}
  - {symbol: NFLX, company_name: Netflix Inc.}
```

```python
# src/signalbench/ingest/seed.py
from pathlib import Path

import yaml
from sqlmodel import Session, select

from signalbench.db.models import Ticker


def seed_watchlist(session: Session, path: Path) -> int:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    entries = payload["tickers"]
    for entry in entries:
        existing = session.exec(select(Ticker).where(Ticker.symbol == entry["symbol"])).first()
        if existing is None:
            session.add(
                Ticker(
                    symbol=entry["symbol"],
                    company_name=entry["company_name"],
                    active=True,
                )
            )
        else:
            existing.company_name = entry["company_name"]
            existing.active = True
            session.add(existing)
    session.commit()
    return len(entries)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_seed.py -v
```

Verify: `uv run pytest tests/test_seed.py -v`
Expect: 2 passed. Second run does not create 40 rows.

- [ ] **Step 5: Commit**

```bash
git add data/watchlist.yaml src/signalbench/ingest/seed.py tests/test_seed.py
git commit -m "feat: seed fixed 20-ticker watchlist"
```

---

### Task 5: EDGAR 8-K ingest (mocked)

**Files:**
- Create: `src/signalbench/ingest/edgar.py`
- Create: `tests/test_edgar.py`
- Create: `tests/fixtures/company_tickers.json`
- Create: `tests/fixtures/edgar_submissions.json`
- Create: `tests/fixtures/edgar_8k.html`

`external_id` = accession number (no dashes in EDGAR URLs; store the canonical dashed form from submissions JSON). `document_tickers` = issuer ticker only. Second ingest of the same accession is a no-op.

- [ ] **Step 1: Write fixtures and failing tests**

```json
{
  "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}
}
```

Save as `tests/fixtures/company_tickers.json` in the SEC `company_tickers.json` shape (`{ "0": { "cik_str", "ticker", "title" }, ... }`).

```json
{
  "cik": "0000320193",
  "filings": {
    "recent": {
      "accessionNumber": ["0000320193-24-000001"],
      "form": ["8-K"],
      "filingDate": ["2024-01-15"],
      "primaryDocument": ["aapl-20240115.htm"]
    }
  }
}
```

`tests/fixtures/edgar_8k.html`: `<html><body>Apple 8-K Item 2.02</body></html>`

```python
# tests/test_edgar.py
from datetime import timezone
from pathlib import Path

import httpx
from sqlmodel import Session, select

from signalbench.db.models import DocumentTicker, RawDocument, Ticker
from signalbench.ingest.edgar import ingest_eight_ks_for_symbol

FIXTURES = Path(__file__).parent / "fixtures"


def test_ingest_stores_one_8k_and_issuer_link(session: Session) -> None:
    ticker = Ticker(symbol="AAPL", company_name="Apple Inc.", active=True)
    session.add(ticker)
    session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("company_tickers.json"):
            return httpx.Response(200, text=(FIXTURES / "company_tickers.json").read_text())
        if "submissions/CIK0000320193.json" in url:
            return httpx.Response(200, text=(FIXTURES / "edgar_submissions.json").read_text())
        if url.endswith("aapl-20240115.htm"):
            return httpx.Response(200, text=(FIXTURES / "edgar_8k.html").read_text())
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    created = ingest_eight_ks_for_symbol(
        session,
        symbol="AAPL",
        client=client,
        user_agent="SignalBench/0.1 (test@example.com)",
    )
    assert created == 1
    doc = session.exec(select(RawDocument)).one()
    assert doc.source == "sec_edgar"
    assert doc.external_id == "0000320193-24-000001"
    assert doc.doc_type.value == "eight_k"
    assert doc.published_at.tzinfo is not None
    assert "Item 2.02" in doc.raw_text or "8-K" in doc.raw_text
    links = session.exec(select(DocumentTicker)).all()
    assert len(links) == 1
    assert links[0].ticker_id == ticker.id


def test_second_ingest_is_noop(session: Session) -> None:
    session.add(Ticker(symbol="AAPL", company_name="Apple Inc.", active=True))
    session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("company_tickers.json"):
            return httpx.Response(200, text=(FIXTURES / "company_tickers.json").read_text())
        if "submissions" in url:
            return httpx.Response(200, text=(FIXTURES / "edgar_submissions.json").read_text())
        return httpx.Response(200, text=(FIXTURES / "edgar_8k.html").read_text())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ingest_eight_ks_for_symbol(session, "AAPL", client, "SignalBench/0.1 (test@example.com)")
    ingest_eight_ks_for_symbol(session, "AAPL", client, "SignalBench/0.1 (test@example.com)")
    docs = session.exec(select(RawDocument)).all()
    assert len(docs) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_edgar.py -v
```

Verify: `uv run pytest tests/test_edgar.py -v`
Expect: FAIL `ImportError` for `ingest_eight_ks_for_symbol`.

- [ ] **Step 3: Write ingest**

Implement `ingest_eight_ks_for_symbol` in `src/signalbench/ingest/edgar.py`:

1. GET `https://www.sec.gov/files/company_tickers.json` with `User-Agent: {user_agent}`.
2. Find CIK for `symbol` (zero-pad to 10 digits).
3. GET `https://data.sec.gov/submissions/CIK{cik10}.json`.
4. For each recent filing where `form == "8-K"`, skip if a `RawDocument` exists with `source="sec_edgar"` and `external_id=accessionNumber`.
5. Download `https://www.sec.gov/Archives/edgar/data/{cik_no_zeros}/{accession_nodash}/{primaryDocument}`.
6. Insert `RawDocument` (`doc_type=eight_k`, `published_at` from `filingDate` at 00:00 UTC) and one `DocumentTicker` for the issuer.

Pass `httpx.Client` in so tests never hit the network.

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_edgar.py -v
```

Verify: `uv run pytest tests/test_edgar.py -v`
Expect: 2 passed. Second ingest does not insert a second row.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/ingest/edgar.py tests/test_edgar.py tests/fixtures
git commit -m "feat: ingest 8-Ks from EDGAR with accession dedup"
```

---

### Task 6: Daily prices with adj_close (mocked)

**Files:**
- Create: `src/signalbench/ingest/prices.py`
- Create: `tests/test_prices.py`

Do not call Yahoo in tests. Inject a fetch function.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_prices.py
from datetime import date
from decimal import Decimal

from sqlmodel import Session, select

from signalbench.db.models import Price, Ticker
from signalbench.ingest.prices import DailyBar, ingest_daily_prices


def test_ingest_writes_adj_close_and_is_idempotent(session: Session) -> None:
    ticker = Ticker(symbol="AAPL", company_name="Apple Inc.", active=True)
    session.add(ticker)
    session.commit()
    session.refresh(ticker)

    bars = [
        DailyBar(
            date=date(2024, 1, 2),
            open=Decimal("185.0000"),
            high=Decimal("186.0000"),
            low=Decimal("184.0000"),
            close=Decimal("185.5000"),
            adj_close=Decimal("185.2000"),
            volume=50_000_000,
        )
    ]

    def fetch(_symbol: str) -> list[DailyBar]:
        return bars

    n1 = ingest_daily_prices(session, ticker, fetch=fetch)
    n2 = ingest_daily_prices(session, ticker, fetch=fetch)
    assert n1 == 1
    assert n2 == 0
    row = session.exec(select(Price)).one()
    assert row.adj_close == Decimal("185.2000")
    assert row.close == Decimal("185.5000")
    assert row.ticker_id == ticker.id
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_prices.py -v
```

Verify: `uv run pytest tests/test_prices.py -v`
Expect: FAIL `ImportError` for `ingest_daily_prices`.

- [ ] **Step 3: Write implementation**

```python
# src/signalbench/ingest/prices.py
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlmodel import Session, select

from signalbench.db.models import Price, Ticker


@dataclass(frozen=True)
class DailyBar:
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    adj_close: Decimal
    volume: int


def ingest_daily_prices(
    session: Session,
    ticker: Ticker,
    fetch: Callable[[str], list[DailyBar]],
) -> int:
    created = 0
    for bar in fetch(ticker.symbol):
        existing = session.exec(
            select(Price).where(Price.ticker_id == ticker.id, Price.date == bar.date)
        ).first()
        if existing is not None:
            continue
        session.add(
            Price(
                ticker_id=ticker.id,
                date=bar.date,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                adj_close=bar.adj_close,
                volume=bar.volume,
            )
        )
        created += 1
    session.commit()
    return created


def fetch_yfinance_daily(symbol: str) -> list[DailyBar]:
    import yfinance as yf

    frame = yf.Ticker(symbol).history(period="max", auto_adjust=False)
    bars: list[DailyBar] = []
    for idx, row in frame.iterrows():
        adj = row["Adj Close"] if "Adj Close" in row.index else row["Close"]
        bars.append(
            DailyBar(
                date=idx.date(),
                open=Decimal(str(round(float(row["Open"]), 4))),
                high=Decimal(str(round(float(row["High"]), 4))),
                low=Decimal(str(round(float(row["Low"]), 4))),
                close=Decimal(str(round(float(row["Close"]), 4))),
                adj_close=Decimal(str(round(float(adj), 4))),
                volume=int(row["Volume"]),
            )
        )
    return bars
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_prices.py -v
```

Verify: `uv run pytest tests/test_prices.py -v`
Expect: `PASSED`. `adj_close` stored; second call `created == 0`.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/ingest/prices.py tests/test_prices.py
git commit -m "feat: ingest daily prices with required adj_close"
```

---

### Task 7: Typer CLI

**Files:**
- Create: `src/signalbench/cli.py`
- Create: `tests/test_cli.py`
- Modify: `pyproject.toml` — `[project.scripts] signalbench = "signalbench.cli:app"`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py
from typer.testing import CliRunner

from signalbench.cli import app

runner = CliRunner()


def test_help_lists_seed_and_ingest() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "seed-watchlist" in result.stdout
    assert "ingest" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_cli.py -v
```

Verify: `uv run pytest tests/test_cli.py -v`
Expect: FAIL `ImportError` for `signalbench.cli`.

- [ ] **Step 3: Write CLI**

Typer app with:

- `seed-watchlist` → `seed_watchlist` using `data/watchlist.yaml` and `get_session()`
- `ingest filings` → for each active ticker, `ingest_eight_ks_for_symbol` with a real `httpx.Client`
- `ingest prices` → for each active ticker, `ingest_daily_prices(..., fetch=fetch_yfinance_daily)`

Wire `[project.scripts]`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_cli.py -v
uv run signalbench --help
```

Verify: `uv run pytest tests/test_cli.py -v` and `uv run signalbench --help`
Expect: test PASSED; help text includes `seed-watchlist` and `ingest`.

- [ ] **Step 5: Update PRD dedup sentence**

In [PRD.md](../../../PRD.md) §6.1 replace “dedup on (source, url, published_at)” with “dedup on (source, external_id); EDGAR uses accession number”.

- [ ] **Step 6: Commit**

```bash
git add src/signalbench/cli.py tests/test_cli.py pyproject.toml PRD.md
git commit -m "feat: add ingest CLI and correct PRD dedup key"
```

---

## Phase 0 gate

Do not start Phase 1 until all of these pass:

```bash
docker compose up -d
uv sync --group dev
uv run pytest
uv run ruff check src tests
uv run mypy src tests
uv run pytest tests/test_health.py
```

Verify:

- `uv run pytest` — all Phase 0 tests pass (no live SEC/Yahoo).
- `uv run ruff check src tests` — exit 0.
- `uv run mypy src tests` — exit 0.
- TestClient `/health` still `{"status":"ok"}`.

Optional live smoke (not CI): `alembic upgrade head`, `uv run signalbench seed-watchlist`, then one ticker filings/prices with a real `SEC_USER_AGENT`.
