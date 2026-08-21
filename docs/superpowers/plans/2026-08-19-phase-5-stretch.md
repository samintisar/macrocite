# Phase 5 — Stretch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Models:** cheap implementers `composer-2.5`; mid-tier implementers, reviewers, and fix loops `cursor-grok-4.6-high`. Never `*-fast` models. See [docs/superpowers/README.md](../README.md).

**Goal:** Add news ingest, 10-K/10-Q filings, a parameter sweep, confidence calibration in eval output, and optional pgvector similar-signal search. Daily bars stay the default; intraday is an explicit extra table.

**Architecture:** Same document-as-artifact model. News articles become `raw_documents` with `doc_type=news` and `document_tickers` from the Alpha Vantage ticker list. 10-K/10-Q reuse EDGAR ingest with a form filter. Sweeps write many `BacktestRun` rows sharing `sweep_id`. Calibration extends Phase 2 metrics (already bucketed) and asserts non-empty JSON on a known fixture. pgvector is skipped in CI when the extension is absent.

**Tech Stack:** httpx mocks, existing SQLModel, Postgres `vector` extension (optional), HNSW index (not IVFFlat).

**Depends on:** Phase 4 gate green.

**Out of scope:** broker APIs, LangGraph, Azure, rewriting Phases 0–4.

---

## File map

- Create: `src/signalbench/ingest/news.py`
- Create: `tests/test_news.py`
- Create: `tests/fixtures/alpha_vantage_news.json`
- Modify: `src/signalbench/ingest/edgar.py` — `forms: tuple[str, ...] = ("8-K",)`
- Create: `tests/test_edgar_10q.py`
- Create: `src/signalbench/backtest/sweep.py`
- Create: `tests/test_sweep.py`
- Modify: `src/signalbench/db/models.py` — `BacktestRun.sweep_id: str | None`; optional `PriceIntraday`
- Create: `src/signalbench/ingest/intraday.py` (only if executing Task 4)
- Create: `tests/test_intraday.py` (only if executing Task 4)
- Modify: `src/signalbench/eval/metrics.py` — keep `confidence_calibration`
- Create: `tests/test_calibration.py`
- Create: `src/signalbench/search/similar.py`
- Create: `tests/test_similar.py`
- Modify: `README.md` — news history starts ~March 2022
- Modify: `src/signalbench/cli.py` — `ingest news`, `ingest filings --forms`, `backtest-sweep`
- Create: `alembic/versions/0005_stretch.py`

---

### Task 1: Alpha Vantage news ingest

**Files:**
- Create: `src/signalbench/ingest/news.py`
- Create: `tests/test_news.py`
- Create: `tests/fixtures/alpha_vantage_news.json`
- Modify: `src/signalbench/config.py` — `alpha_vantage_api_key: str = ""`

Fixture article: one `url`/`uuid`, tickers AAPL and MSFT.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_news.py
from pathlib import Path

import httpx
from sqlmodel import Session, select

from signalbench.db.models import DocumentTicker, RawDocument, Ticker
from signalbench.ingest.news import ingest_news

FIXTURES = Path(__file__).parent / "fixtures"


def test_one_article_two_tickers_and_dedup(session: Session) -> None:
    session.add(Ticker(symbol="AAPL", company_name="Apple Inc.", active=True))
    session.add(Ticker(symbol="MSFT", company_name="Microsoft Corporation", active=True))
    session.commit()

    payload = (FIXTURES / "alpha_vantage_news.json").read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    n1 = ingest_news(session, client=client, api_key="demo")
    n2 = ingest_news(session, client=client, api_key="demo")
    assert n1 == 1
    assert n2 == 0
    doc = session.exec(select(RawDocument)).one()
    assert doc.source == "alpha_vantage"
    assert doc.doc_type.value == "news"
    links = session.exec(select(DocumentTicker).where(DocumentTicker.document_id == doc.id)).all()
    assert len(links) == 2
```

`alpha_vantage_news.json` must include `feed[0].url` or `feed[0].uuid` as `external_id`, `title`, `time_published`, `summary` or `banner_image` text as `raw_text`, and `ticker_sentiment` with AAPL and MSFT.

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_news.py -v
```

Verify: `uv run pytest tests/test_news.py -v`
Expect: FAIL import `ingest_news`.

- [ ] **Step 3: Implement `ingest_news`. Unique `(source, external_id)`. Do not call the live API in tests.**

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_news.py -v
```

Verify: same command
Expect: one document, two `document_tickers`, retry no-op.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/ingest/news.py tests/test_news.py tests/fixtures/alpha_vantage_news.json src/signalbench/config.py
git commit -m "feat: ingest Alpha Vantage news with multi-ticker links"
```

---

### Task 2: 10-Q / 10-K ingest

**Files:**
- Modify: `src/signalbench/ingest/edgar.py`
- Create: `tests/test_edgar_10q.py`
- Create: `tests/fixtures/edgar_submissions_10q.json`
- Create: `tests/fixtures/edgar_10q.html`

Size policy: store full text if `len(raw) <= 500_000` characters; otherwise store the first 500_000 plus a trailing marker `\n\n[truncated]`.

- [ ] **Step 1: Write the failing test**

Reuse the Task 5 Phase 0 mock pattern. Submissions fixture `form: ["10-Q"]`, `primaryDocument` `aapl-10q.htm`.

```python
# tests/test_edgar_10q.py
from pathlib import Path

import httpx
from sqlmodel import Session, select

from signalbench.db.models import RawDocument, Ticker
from signalbench.ingest.edgar import ingest_filings_for_symbol

FIXTURES = Path(__file__).parent / "fixtures"


def test_10q_upserts_as_ten_q(session: Session) -> None:
    session.add(Ticker(symbol="AAPL", company_name="Apple Inc.", active=True))
    session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("company_tickers.json"):
            return httpx.Response(200, text=(FIXTURES / "company_tickers.json").read_text())
        if "submissions" in url:
            return httpx.Response(200, text=(FIXTURES / "edgar_submissions_10q.json").read_text())
        return httpx.Response(200, text=(FIXTURES / "edgar_10q.html").read_text())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    created = ingest_filings_for_symbol(
        session,
        symbol="AAPL",
        client=client,
        user_agent="SignalBench/0.1 (test@example.com)",
        forms=("10-Q",),
    )
    assert created == 1
    doc = session.exec(select(RawDocument)).one()
    assert doc.doc_type.value == "ten_q"


def test_eight_k_filter_still_works(session: Session) -> None:
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
    created = ingest_filings_for_symbol(
        session,
        symbol="AAPL",
        client=client,
        user_agent="SignalBench/0.1 (test@example.com)",
        forms=("8-K",),
    )
    assert created == 1
    assert session.exec(select(RawDocument)).one().doc_type.value == "eight_k"
```

Rename Phase 0 `ingest_eight_ks_for_symbol` to `ingest_filings_for_symbol` with `forms=("8-K",)` default, and keep `ingest_eight_ks_for_symbol` as a one-line wrapper calling that default so Phase 0 tests keep passing.

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_edgar_10q.py -v
```

Verify: `uv run pytest tests/test_edgar_10q.py -v`
Expect: FAIL until form filter maps `10-Q` → `DocType.ten_q`.

- [ ] **Step 3: Map `8-K`→`eight_k`, `10-Q`→`ten_q`, `10-K`→`ten_k`. Apply truncation policy.**

- [ ] **Step 4: Run tests including Phase 0 EDGAR**

```bash
uv run pytest tests/test_edgar.py tests/test_edgar_10q.py -v
```

Verify: that command
Expect: 10-Q stored as `ten_q`; 8-K path still green.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/ingest/edgar.py tests/test_edgar_10q.py tests/fixtures/edgar_submissions_10q.json tests/fixtures/edgar_10q.html
git commit -m "feat: ingest 10-Q and 10-K alongside 8-K"
```

---

### Task 3: Parameter sweep

**Files:**
- Modify: `src/signalbench/db/models.py` — `sweep_id: str | None = None` on `BacktestRun`
- Create: `src/signalbench/backtest/sweep.py`
- Create: `tests/test_sweep.py`
- Create: `alembic/versions/0005_stretch.py` (also used by later tasks)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sweep.py
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlmodel import Session, select

from signalbench.backtest.sweep import run_sweep
from signalbench.db.models import BacktestRun, EventType, Price, RawDocument, Signal, Ticker


def test_two_by_two_grid_four_runs(session: Session) -> None:
    ticker = Ticker(symbol="AAPL", company_name="Apple Inc.", active=True)
    session.add(ticker)
    session.commit()
    session.refresh(ticker)
    doc = RawDocument(
        source="sec_edgar",
        external_id="sweep-doc",
        doc_type="eight_k",
        raw_text="x",
        published_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    session.add(
        Signal(
            document_id=doc.id,
            ticker_id=ticker.id,
            model_version="m",
            prompt_version="v1",
            sentiment=0.8,
            event_type=EventType.earnings,
            confidence=0.9,
            rationale="x",
        )
    )
    for d, px in [(date(2024, 1, 2), "100"), (date(2024, 1, 3), "101"), (date(2024, 1, 4), "102"), (date(2024, 1, 5), "103")]:
        session.add(
            Price(
                ticker_id=ticker.id,
                date=d,
                open=Decimal(px),
                high=Decimal(px),
                low=Decimal(px),
                close=Decimal(px),
                adj_close=Decimal(px),
                volume=1,
            )
        )
    session.commit()
    sweep_id = run_sweep(
        session,
        ticker_ids=[ticker.id],
        thresholds=(0.3, 0.9),
        holding_days=(1, 3),
        model_version="m",
        prompt_version="v1",
    )
    runs = session.exec(select(BacktestRun).where(BacktestRun.sweep_id == sweep_id)).all()
    assert len(runs) == 4
    metrics = {(r.sharpe_ratio, r.total_return) for r in runs}
    assert len(metrics) >= 2
```

Threshold 0.9 may produce no trades while 0.3 does — that is enough for “metrics differ when params differ”.

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_sweep.py -v
```

Verify: `uv run pytest tests/test_sweep.py -v`
Expect: FAIL missing `run_sweep` / `sweep_id`.

- [ ] **Step 3: `run_sweep` nested loops, new `BacktestConfig` per param pair or one config with params JSON, four `BacktestRun` rows, shared `sweep_id` UUID string.**

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_sweep.py -v
```

Verify: same command
Expect: 4 runs; not all metric tuples identical.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/backtest/sweep.py src/signalbench/db/models.py alembic/versions/0005_stretch.py tests/test_sweep.py
git commit -m "feat: parameter sweep writes multiple backtest runs"
```

---

### Task 4: Intraday bars (in scope)

Daily `prices` remain the default for Phase 3 backtests. Add `prices_intraday` rather than overloading `prices`.

```python
class PriceIntraday(SQLModel, table=True):
    __tablename__ = "prices_intraday"
    __table_args__ = (UniqueConstraint("ticker_id", "ts", name="uq_intraday_ticker_ts"),)

    id: int | None = Field(default=None, primary_key=True)
    ticker_id: uuid.UUID = Field(foreign_key="tickers.id", ondelete="RESTRICT")
    ts: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    bar_size: str  # "5m"
    open: Decimal = Field(sa_column=Column(Numeric(12, 4), nullable=False))
    high: Decimal = Field(sa_column=Column(Numeric(12, 4), nullable=False))
    low: Decimal = Field(sa_column=Column(Numeric(12, 4), nullable=False))
    close: Decimal = Field(sa_column=Column(Numeric(12, 4), nullable=False))
    volume: int
```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_intraday.py
from datetime import datetime, timezone
from decimal import Decimal

from sqlmodel import Session, select

from signalbench.db.models import PriceIntraday, Ticker
from signalbench.ingest.intraday import ingest_intraday_bars, IntradayBar


def test_five_minute_bars_round_trip(session: Session) -> None:
    ticker = Ticker(symbol="AAPL", company_name="Apple Inc.", active=True)
    session.add(ticker)
    session.commit()
    session.refresh(ticker)
    bars = [
        IntradayBar(
            ts=datetime(2024, 1, 2, 14, 30, tzinfo=timezone.utc),
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100.5"),
            volume=1000,
        )
    ]
    n = ingest_intraday_bars(session, ticker, bar_size="5m", bars=bars)
    assert n == 1
    row = session.exec(select(PriceIntraday)).one()
    assert row.bar_size == "5m"
    assert row.close == Decimal("100.5")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_intraday.py -v
```

Verify: `uv run pytest tests/test_intraday.py -v`
Expect: FAIL missing table/function.

- [ ] **Step 3: Implement model + `ingest_intraday_bars` upsert on `(ticker_id, ts)`.**

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_intraday.py -v
```

Verify: same command
Expect: 5-minute bar round-trips.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/db/models.py src/signalbench/ingest/intraday.py tests/test_intraday.py alembic/versions/0005_stretch.py
git commit -m "feat: store optional 5-minute price bars"
```

---

### Task 5: Confidence calibration fixture

**Files:**
- Create: `tests/test_calibration.py`
- Modify: `src/signalbench/eval/metrics.py` only if buckets are empty-dict today

- [ ] **Step 1: Write the failing test**

```python
# tests/test_calibration.py
from signalbench.eval.metrics import evaluate
from signalbench.extraction.schema import EventTypeName


def test_high_confidence_wrong_shows_in_calibration() -> None:
    out = evaluate(
        human_sentiment=[1.0, 1.0, 1.0],
        pred_sentiment=[-1.0, 1.0, 1.0],
        human_event=[EventTypeName.other, EventTypeName.other, EventTypeName.other],
        pred_event=[EventTypeName.other, EventTypeName.other, EventTypeName.other],
        pred_confidence=[0.95, 0.95, 0.95],
    )
    assert out.confidence_calibration is not None
    assert out.confidence_calibration["0.9-1.0"] == 2 / 3
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_calibration.py -v
```

Verify: `uv run pytest tests/test_calibration.py -v`
Expect: FAIL if calibration is None or wrong bucket math.

- [ ] **Step 3: Ensure `evaluate` always returns non-empty `confidence_calibration` with the three bucket keys.**

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_calibration.py tests/test_eval_metrics.py -v
```

Verify: that command
Expect: 0.9-bucket is `2/3`; older metric tests still pass.

- [ ] **Step 5: Commit**

```bash
git add tests/test_calibration.py src/signalbench/eval/metrics.py
git commit -m "feat: lock confidence calibration buckets"
```

---

### Task 6: Similar signals (HNSW / pgvector)

**Files:**
- Modify: `src/signalbench/db/models.py` — `RawDocument.embedding` optional; skip on SQLite
- Create: `src/signalbench/search/similar.py`
- Create: `tests/test_similar.py`

SQLite tests cannot create `VECTOR`. Implement cosine similarity in Python on `list[float]` stored as JSON for unit tests. Alembic for Postgres: `CREATE EXTENSION IF NOT EXISTS vector;` and HNSW:

```sql
CREATE INDEX idx_raw_documents_embedding_hnsw
  ON raw_documents
  USING hnsw (embedding vector_cosine_ops);
```

Do not create IVFFlat.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_similar.py
from signalbench.search.similar import nearest_neighbor


def test_cosine_neighbor() -> None:
    corpus = {
        "a": [1.0, 0.0, 0.0],
        "b": [0.0, 1.0, 0.0],
        "c": [0.9, 0.1, 0.0],
    }
    match = nearest_neighbor([1.0, 0.0, 0.0], corpus, skip_id="a")
    assert match == "c"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_similar.py -v
```

Verify: `uv run pytest tests/test_similar.py -v`
Expect: FAIL import.

- [ ] **Step 3: Implement `nearest_neighbor` with cosine similarity. If `pytest` is marked `postgres` and the engine is Postgres without `vector`, skip with `pytest.skip("pgvector unavailable")`.**

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_similar.py -v
```

Verify: same command
Expect: neighbor `c`.

- [ ] **Step 5: README + CLI**

README: news-driven backtests start ~March 2022 on the free Alpha Vantage window. CLI: `ingest news`, `ingest filings --forms 8-K,10-Q`, `backtest-sweep`.

```bash
git add src/signalbench/search/similar.py tests/test_similar.py README.md src/signalbench/cli.py src/signalbench/db/models.py alembic/versions/0005_stretch.py
git commit -m "feat: similar-signal cosine search and stretch CLI"
```

---

## Phase 5 gate

```bash
uv run pytest
uv run ruff check src tests
uv run mypy src tests
```

Verify:

- News: one mock article → one document, two tickers; retry no-op. No live Alpha Vantage in CI.
- `ten_q` fixture ingest; `tests/test_edgar.py` still passes.
- 2×2 sweep → 4 `backtest_runs`.
- Intraday 5m round-trip.
- Calibration JSON non-empty on the 0.9-wrong fixture.
- Similar-signal unit test passes without Postgres extension.
- README mentions ~March 2022 news window.
- Phases 0–4 tests still pass.
