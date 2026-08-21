# Phase 1 — Signal Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Models:** cheap implementers `composer-2.5`; mid-tier implementers, reviewers, and fix loops `cursor-grok-4.6-high`. Never `*-fast` models. See [docs/superpowers/README.md](../README.md).

**Goal:** Extract structured signals from stored documents with a versioned prompt and mocked Claude client, one row per (document, ticker, model, prompt).

**Architecture:** A document is still the artifact. A signal is one LLM claim about one ticker in that document. Unique key `(document_id, ticker_id, model_version, prompt_version)` preserves history when the prompt or model changes. Extraction may name a ticker that ingest did not put on `document_tickers`. Tests never call Anthropic; inject a fake client.

**Tech Stack:** SQLModel, Pydantic v2, Anthropic SDK (tool-use / structured output), Typer, pytest. Prompt file `prompts/extract_v1.txt` with `prompt_version=v1` in config.

**Depends on:** Phase 0 gate green ([2026-08-19-phase-0-ingest.md](2026-08-19-phase-0-ingest.md)). Reuse `Ticker`, `RawDocument`, `tests/conftest.py` session fixture.

**Out of scope:** eval CI, backtests, dashboard, live API keys in CI.

---

## File map

- Modify: `src/signalbench/db/models.py` — add `EventType`, `Signal`
- Create: `alembic/versions/0002_signals.py`
- Create: `src/signalbench/extraction/schema.py` — `ExtractedSignal` Pydantic model
- Create: `src/signalbench/extraction/extract.py` — `extract_document`
- Create: `prompts/extract_v1.txt`
- Create: `src/signalbench/extraction/prompt.py` — load prompt + version string
- Modify: `src/signalbench/config.py` — `model_version`, `prompt_version`
- Modify: `src/signalbench/cli.py` — `extract` command
- Create: `tests/test_signal_schema.py`, `tests/test_extract_contract.py`, `tests/test_extract.py`, `tests/test_extract_cli.py`
- Create: `tests/fixtures/extract_doc.txt`

---

### Task 1: `signals` table

**Files:**
- Modify: `src/signalbench/db/models.py`
- Create: `tests/test_signal_schema.py`
- Create: `alembic/versions/0002_signals.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_signal_schema.py
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from signalbench.db.models import EventType, RawDocument, Signal, Ticker


def _doc_and_ticker(session: Session) -> tuple[RawDocument, Ticker]:
    ticker = Ticker(symbol="AAPL", company_name="Apple Inc.", active=True)
    session.add(ticker)
    session.commit()
    session.refresh(ticker)
    doc = RawDocument(
        source="sec_edgar",
        external_id="acc-1",
        doc_type="eight_k",
        raw_text="Apple reports earnings.",
        published_at=datetime(2024, 1, 15, tzinfo=timezone.utc),
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc, ticker


def test_unique_document_ticker_model_prompt(session: Session) -> None:
    doc, ticker = _doc_and_ticker(session)
    kwargs = dict(
        document_id=doc.id,
        ticker_id=ticker.id,
        model_version="claude-sonnet-4-6",
        prompt_version="v1",
        sentiment=0.4,
        event_type=EventType.earnings,
        confidence=0.8,
        rationale="Beat on EPS.",
        raw_llm_response={"ok": True},
    )
    session.add(Signal(**kwargs))
    session.commit()
    session.add(Signal(**kwargs))
    with pytest.raises(IntegrityError):
        session.commit()


def test_new_prompt_version_inserts_second_row(session: Session) -> None:
    doc, ticker = _doc_and_ticker(session)
    session.add(
        Signal(
            document_id=doc.id,
            ticker_id=ticker.id,
            model_version="claude-sonnet-4-6",
            prompt_version="v1",
            sentiment=0.4,
            event_type=EventType.earnings,
            confidence=0.8,
            rationale="v1",
        )
    )
    session.add(
        Signal(
            document_id=doc.id,
            ticker_id=ticker.id,
            model_version="claude-sonnet-4-6",
            prompt_version="v2",
            sentiment=0.5,
            event_type=EventType.earnings,
            confidence=0.7,
            rationale="v2",
        )
    )
    session.commit()
    rows = session.exec(select(Signal)).all()
    assert len(rows) == 2


def test_sentiment_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        Signal(
            document_id=__import__("uuid").uuid4(),
            ticker_id=__import__("uuid").uuid4(),
            model_version="m",
            prompt_version="v1",
            sentiment=2.0,
            event_type=EventType.other,
            confidence=0.5,
            rationale="bad",
        )
```

If SQLModel CHECK constraints do not run on SQLite, also assert Pydantic field validators on `sentiment` and `confidence` (`ge=-1`, `le=1` / `ge=0`, `le=1`).

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_signal_schema.py -v
```

Verify: `uv run pytest tests/test_signal_schema.py -v`
Expect: FAIL — `EventType` / `Signal` not defined.

- [ ] **Step 3: Write models**

Add to `src/signalbench/db/models.py`:

```python
class EventType(str, Enum):
    earnings = "earnings"
    guidance = "guidance"
    leadership = "leadership"
    legal = "legal"
    product = "product"
    macro = "macro"
    other = "other"


class Signal(SQLModel, table=True):
    __tablename__ = "signals"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "ticker_id",
            "model_version",
            "prompt_version",
            name="uq_signals_doc_ticker_model_prompt",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    document_id: uuid.UUID = Field(foreign_key="raw_documents.id", ondelete="CASCADE")
    ticker_id: uuid.UUID = Field(foreign_key="tickers.id", ondelete="RESTRICT")
    model_version: str
    prompt_version: str
    sentiment: float = Field(ge=-1.0, le=1.0)
    event_type: EventType
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: Optional[str] = None
    raw_llm_response: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    extracted_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
```

Use `sqlalchemy.JSON` for `raw_llm_response`. Alembic `0002_signals` creates the table + unique constraint. Do not put `ticker_id` on `raw_documents`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_signal_schema.py -v
```

Verify: `uv run pytest tests/test_signal_schema.py -v`
Expect: unique key blocks dupes; `prompt_version=v2` inserts; `sentiment=2.0` raises `ValidationError`.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/db/models.py alembic/versions/0002_signals.py tests/test_signal_schema.py
git commit -m "feat: add versioned signals table"
```

---

### Task 2: Pydantic extraction contract (no DB)

**Files:**
- Create: `src/signalbench/extraction/schema.py`
- Create: `tests/test_extract_contract.py`

This is the PRD JSON shape. The LLM must return a **list** of claims so one document can yield multiple tickers.

```python
# src/signalbench/extraction/schema.py
from enum import Enum

from pydantic import BaseModel, Field


class EventTypeName(str, Enum):
    earnings = "earnings"
    guidance = "guidance"
    leadership = "leadership"
    legal = "legal"
    product = "product"
    macro = "macro"
    other = "other"


class ExtractedClaim(BaseModel):
    ticker: str = Field(min_length=1)
    sentiment: float = Field(ge=-1.0, le=1.0)
    event_type: EventTypeName
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)


class ExtractionResult(BaseModel):
    claims: list[ExtractedClaim]
```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_extract_contract.py
import pytest
from pydantic import ValidationError

from signalbench.extraction.schema import ExtractionResult


def test_rejects_sentiment_out_of_range() -> None:
    with pytest.raises(ValidationError):
        ExtractionResult.model_validate(
            {
                "claims": [
                    {
                        "ticker": "AAPL",
                        "sentiment": 2.0,
                        "event_type": "earnings",
                        "confidence": 0.5,
                        "rationale": "nope",
                    }
                ]
            }
        )


def test_rejects_unknown_event_type() -> None:
    with pytest.raises(ValidationError):
        ExtractionResult.model_validate(
            {
                "claims": [
                    {
                        "ticker": "AAPL",
                        "sentiment": 0.1,
                        "event_type": "merger",
                        "confidence": 0.5,
                        "rationale": "nope",
                    }
                ]
            }
        )


def test_accepts_two_claims() -> None:
    result = ExtractionResult.model_validate(
        {
            "claims": [
                {
                    "ticker": "AAPL",
                    "sentiment": 0.6,
                    "event_type": "earnings",
                    "confidence": 0.9,
                    "rationale": "Beat EPS.",
                },
                {
                    "ticker": "MSFT",
                    "sentiment": -0.2,
                    "event_type": "legal",
                    "confidence": 0.4,
                    "rationale": "Mentioned in passing.",
                },
            ]
        }
    )
    assert [c.ticker for c in result.claims] == ["AAPL", "MSFT"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_extract_contract.py -v
```

Verify: `uv run pytest tests/test_extract_contract.py -v`
Expect: FAIL import until `schema.py` exists; after empty file, FAIL validation tests.

- [ ] **Step 3: Write `schema.py` as above**

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_extract_contract.py -v
```

Verify: `uv run pytest tests/test_extract_contract.py -v`
Expect: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/extraction/schema.py tests/test_extract_contract.py
git commit -m "feat: add structured extraction Pydantic contract"
```

---

### Task 3: Extractor with injected LLM (persist + versioning)

**Files:**
- Create: `prompts/extract_v1.txt`
- Create: `src/signalbench/extraction/prompt.py`
- Create: `src/signalbench/extraction/extract.py`
- Create: `tests/test_extract.py`
- Create: `tests/fixtures/extract_doc.txt`
- Modify: `src/signalbench/config.py`

`extract_document(session, document, llm, model_version, prompt_version)`:

1. Load prompt text for `prompt_version`.
2. Call `llm.complete(prompt, document.raw_text) -> ExtractionResult` (protocol, not Anthropic type).
3. For each claim, resolve ticker by `symbol` (create inactive ticker if missing so we do not drop co-mentions).
4. Insert `Signal` rows; skip rows that violate unique key (already extracted).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_extract.py
from datetime import datetime, timezone

from sqlmodel import Session, select

from signalbench.db.models import RawDocument, Signal, Ticker
from signalbench.extraction.extract import extract_document
from signalbench.extraction.schema import ExtractedClaim, ExtractionResult


class FakeLLM:
    def __init__(self, result: ExtractionResult) -> None:
        self.result = result
        self.calls = 0

    def complete(self, prompt: str, raw_text: str) -> ExtractionResult:
        self.calls += 1
        assert "8-K" in raw_text or "earnings" in raw_text.lower() or len(raw_text) > 0
        assert len(prompt) > 0
        return self.result


def test_extract_persists_rationale_and_raw_payload(session: Session) -> None:
    ticker = Ticker(symbol="AAPL", company_name="Apple Inc.", active=True)
    session.add(ticker)
    session.commit()
    doc = RawDocument(
        source="sec_edgar",
        external_id="acc-extract-1",
        doc_type="eight_k",
        raw_text="Apple reports earnings beat.",
        published_at=datetime(2024, 1, 15, tzinfo=timezone.utc),
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    result = ExtractionResult(
        claims=[
            ExtractedClaim(
                ticker="AAPL",
                sentiment=0.7,
                event_type="earnings",
                confidence=0.85,
                rationale="Beat on EPS.",
            )
        ]
    )
    llm = FakeLLM(result)
    created = extract_document(
        session,
        document=doc,
        llm=llm,
        model_version="claude-sonnet-4-6",
        prompt_version="v1",
    )
    assert created == 1
    row = session.exec(select(Signal)).one()
    assert row.rationale == "Beat on EPS."
    assert row.raw_llm_response is not None
    assert row.model_version == "claude-sonnet-4-6"
    assert row.prompt_version == "v1"
    assert row.ticker_id == ticker.id
    assert llm.calls == 1


def test_same_key_does_not_duplicate(session: Session) -> None:
    ticker = Ticker(symbol="AAPL", company_name="Apple Inc.", active=True)
    session.add(ticker)
    session.commit()
    doc = RawDocument(
        source="sec_edgar",
        external_id="acc-extract-2",
        doc_type="eight_k",
        raw_text="x",
        published_at=datetime(2024, 1, 15, tzinfo=timezone.utc),
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    result = ExtractionResult(
        claims=[
            ExtractedClaim(
                ticker="AAPL",
                sentiment=0.1,
                event_type="other",
                confidence=0.5,
                rationale="neutral.",
            )
        ]
    )
    extract_document(session, doc, FakeLLM(result), "claude-sonnet-4-6", "v1")
    extract_document(session, doc, FakeLLM(result), "claude-sonnet-4-6", "v1")
    assert len(session.exec(select(Signal)).all()) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_extract.py -v
```

Verify: `uv run pytest tests/test_extract.py -v`
Expect: FAIL `ImportError` for `extract_document`.

- [ ] **Step 3: Write prompt + extract**

`prompts/extract_v1.txt` — instruct the model to return only claims about named tickers, `event_type` enum, one-sentence rationale.

`prompt.py`:

```python
from pathlib import Path

PROMPT_ROOT = Path(__file__).resolve().parents[2] / "prompts"


def load_prompt(prompt_version: str) -> str:
    path = PROMPT_ROOT / f"extract_{prompt_version}.txt"
    return path.read_text(encoding="utf-8")
```

`extract.py` implements the protocol:

```python
class LLMClient(Protocol):
    def complete(self, prompt: str, raw_text: str) -> ExtractionResult: ...
```

Store `raw_llm_response=result.model_dump()`. Map `event_type` to `EventType`. Add `model_version: str = "claude-sonnet-4-6"` and `prompt_version: str = "v1"` to `Settings`.

A later `AnthropicLLM` class wraps the SDK; tests must not instantiate it.

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_extract.py -v
```

Verify: `uv run pytest tests/test_extract.py -v`
Expect: persist rationale; second extract does not duplicate.

- [ ] **Step 5: Commit**

```bash
git add prompts/extract_v1.txt src/signalbench/extraction src/signalbench/config.py tests/test_extract.py tests/fixtures/extract_doc.txt
git commit -m "feat: extract versioned signals via injected LLM"
```

---

### Task 4: Multi-ticker claims from one document

**Files:**
- Modify: `tests/test_extract.py` (add test; do not rely on “see Task 3”)

- [ ] **Step 1: Write the failing test**

```python
def test_one_document_two_ticker_signals(session: Session) -> None:
    aapl = Ticker(symbol="AAPL", company_name="Apple Inc.", active=True)
    msft = Ticker(symbol="MSFT", company_name="Microsoft Corporation", active=True)
    session.add(aapl)
    session.add(msft)
    session.commit()
    doc = RawDocument(
        source="sec_edgar",
        external_id="acc-multi",
        doc_type="eight_k",
        raw_text="Apple and Microsoft announced a partnership.",
        published_at=datetime(2024, 3, 1, tzinfo=timezone.utc),
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    result = ExtractionResult(
        claims=[
            ExtractedClaim(
                ticker="AAPL",
                sentiment=0.3,
                event_type="product",
                confidence=0.6,
                rationale="Partnership named.",
            ),
            ExtractedClaim(
                ticker="MSFT",
                sentiment=0.3,
                event_type="product",
                confidence=0.6,
                rationale="Partnership named.",
            ),
        ]
    )
    created = extract_document(session, doc, FakeLLM(result), "claude-sonnet-4-6", "v1")
    assert created == 2
    rows = session.exec(select(Signal).where(Signal.document_id == doc.id)).all()
    assert {row.ticker_id for row in rows} == {aapl.id, msft.id}
```

Add imports already used in `tests/test_extract.py`.

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_extract.py::test_one_document_two_ticker_signals -v
```

Verify: that node
Expect: FAIL if extractor only stores the first claim; PASS immediately if Task 3 already looped claims (then this test is the regression lock — still commit it).

- [ ] **Step 3: Loop all `result.claims` in `extract_document`**

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_extract.py::test_one_document_two_ticker_signals -v
```

Verify: same command
Expect: `PASSED`. Same `document_id`, two `ticker_id`s.

- [ ] **Step 5: Commit**

```bash
git add tests/test_extract.py src/signalbench/extraction/extract.py
git commit -m "test: lock multi-ticker extraction per document"
```

---

### Task 5: CLI `extract` with no network

**Files:**
- Modify: `src/signalbench/cli.py`
- Create: `tests/test_extract_cli.py`

`--llm fake` reads `tests/fixtures/extract_claims.json` or uses a built-in fake for CI. Default production path uses Anthropic only when `ANTHROPIC_API_KEY` is set; CI always uses fake.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_extract_cli.py
from typer.testing import CliRunner

from signalbench.cli import app

runner = CliRunner()


def test_extract_help() -> None:
    result = runner.invoke(app, ["extract", "--help"])
    assert result.exit_code == 0
    assert "--llm" in result.stdout or "fake" in result.stdout.lower() or "extract" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_extract_cli.py -v
```

Verify: `uv run pytest tests/test_extract_cli.py -v`
Expect: FAIL until `extract` command exists (Typer “No such command”).

- [ ] **Step 3: Add `extract` command**

`signalbench extract --llm fake` loads all `raw_documents` without a signal for current `model_version`+`prompt_version` and runs `extract_document` with `FakeLLM` in tests. Production: `AnthropicLLM`.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_extract_cli.py tests/test_extract.py tests/test_extract_contract.py tests/test_signal_schema.py -v
```

Verify: that pytest command
Expect: all passed; no Anthropic HTTP.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/cli.py tests/test_extract_cli.py
git commit -m "feat: add extract CLI with fake LLM for tests"
```

---

## Phase 1 gate

Phase 0 tests must still pass.

```bash
uv run pytest
uv run ruff check src tests
uv run mypy src tests
```

Verify:

- Fixture extract writes a `signals` row with `model_version` and `prompt_version`.
- Same `(document_id, ticker_id, model_version, prompt_version)` does not duplicate.
- New `prompt_version` inserts a second row (`test_new_prompt_version_inserts_second_row`).
- No test constructs `Anthropic()` / hits `api.anthropic.com`.
