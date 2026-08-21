# Phase 4 — Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Models:** cheap implementers `composer-2.5`; mid-tier implementers, reviewers, and fix loops `cursor-grok-4.6-high`. Never `*-fast` models. See [docs/superpowers/README.md](../README.md).

**Goal:** Serve a read-only research console: watchlist, latest signals with rationale, last backtest summary, and eval score trend. No auth.

**Architecture:** FastAPI JSON read endpoints plus one Jinja2 HTML page (not a separate React app). Copy frames the product as a research tool, not investment advice. Data is whatever is in Postgres; tests use the SQLite session + TestClient with dependency override.

**Tech Stack:** FastAPI, Jinja2, TestClient, existing SQLModel tables.

**Depends on:** Phase 3 gate green.

**Out of scope:** auth, write APIs, live trading, React SPA.

---

## File map

- Modify: `src/signalbench/api/main.py` — routers + templates
- Create: `src/signalbench/api/deps.py` — `get_session` override
- Create: `src/signalbench/api/routes/watchlist.py`
- Create: `src/signalbench/api/routes/signals.py`
- Create: `src/signalbench/api/routes/eval_runs.py`
- Create: `src/signalbench/api/routes/backtests.py`
- Create: `src/signalbench/api/routes/ui.py`
- Create: `src/signalbench/api/templates/dashboard.html`
- Modify: `README.md` — research-tool disclaimer
- Create: `tests/test_api_watchlist.py`, `tests/test_api_signals.py`, `tests/test_api_eval.py`, `tests/test_api_backtests.py`, `tests/test_dashboard_html.py`, `tests/test_disclaimer.py`

---

### Task 1: Read APIs

**Files:**
- Create: `src/signalbench/api/deps.py`
- Create: route modules
- Modify: `src/signalbench/api/main.py`
- Create: API tests

Override `get_session` in tests with the SQLite fixture.

Response shapes (lock in tests):

- `GET /watchlist` → `[{"symbol": "AAPL", "company_name": "Apple Inc.", "active": true}]`
- `GET /signals?symbol=AAPL` → latest 20 by `RawDocument.published_at` desc: `symbol`, `sentiment`, `event_type`, `confidence`, `rationale`, `published_at`, `prompt_version`
- `GET /eval-runs` → `run_at` ascending, `sentiment_accuracy`, `passed_ci_gate`, `model_version`, `prompt_version`
- `GET /backtests` → latest run: `sharpe_ratio`, `max_drawdown`, `win_rate`, `total_return`, `benchmark_return`, `signal_set_fingerprint`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_api_watchlist.py
from fastapi.testclient import TestClient
from sqlmodel import Session

from signalbench.api.main import app
from signalbench.api.deps import get_db
from signalbench.db.models import Ticker


def test_watchlist_json(session: Session) -> None:
    session.add(Ticker(symbol="AAPL", company_name="Apple Inc.", active=True))
    session.commit()

    def override() -> Session:
        return session

    app.dependency_overrides[get_db] = override
    client = TestClient(app)
    response = client.get("/watchlist")
    assert response.status_code == 200
    body = response.json()
    assert body[0]["symbol"] == "AAPL"
    app.dependency_overrides.clear()
```

```python
# tests/test_api_signals.py
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlmodel import Session

from signalbench.api.deps import get_db
from signalbench.api.main import app
from signalbench.db.models import EventType, RawDocument, Signal, Ticker


def test_signals_include_rationale(session: Session) -> None:
    ticker = Ticker(symbol="AAPL", company_name="Apple Inc.", active=True)
    session.add(ticker)
    session.commit()
    session.refresh(ticker)
    doc = RawDocument(
        source="sec_edgar",
        external_id="d1",
        doc_type="eight_k",
        raw_text="x",
        published_at=datetime(2024, 1, 15, tzinfo=timezone.utc),
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    session.add(
        Signal(
            document_id=doc.id,
            ticker_id=ticker.id,
            model_version="claude-sonnet-4-6",
            prompt_version="v1",
            sentiment=0.4,
            event_type=EventType.earnings,
            confidence=0.8,
            rationale="Beat on EPS.",
        )
    )
    session.commit()
    app.dependency_overrides[get_db] = lambda: session
    client = TestClient(app)
    response = client.get("/signals", params={"symbol": "AAPL"})
    assert response.status_code == 200
    assert response.json()[0]["rationale"] == "Beat on EPS."
    app.dependency_overrides.clear()
```

```python
# tests/test_api_eval.py
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlmodel import Session

from signalbench.api.deps import get_db
from signalbench.api.main import app
from signalbench.db.models import EvalRun


def test_eval_runs_ordered_by_run_at(session: Session) -> None:
    session.add(
        EvalRun(
            model_version="m",
            prompt_version="v1",
            n_examples=10,
            sentiment_accuracy=0.70,
            event_type_metrics={},
            passed_ci_gate=True,
            run_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
    )
    session.add(
        EvalRun(
            model_version="m",
            prompt_version="v2",
            n_examples=10,
            sentiment_accuracy=0.82,
            event_type_metrics={},
            passed_ci_gate=True,
            run_at=datetime(2024, 2, 1, tzinfo=timezone.utc),
        )
    )
    session.commit()
    app.dependency_overrides[get_db] = lambda: session
    client = TestClient(app)
    body = client.get("/eval-runs").json()
    assert body[0]["sentiment_accuracy"] == 0.70
    assert body[1]["sentiment_accuracy"] == 0.82
    assert body[0]["run_at"] < body[1]["run_at"]
    app.dependency_overrides.clear()
```

```python
# tests/test_api_backtests.py
from datetime import date, datetime, timezone

from fastapi.testclient import TestClient
from sqlmodel import Session

from signalbench.api.deps import get_db
from signalbench.api.main import app
from signalbench.db.models import BacktestConfig, BacktestRun


def test_backtests_summary(session: Session) -> None:
    cfg = BacktestConfig(name="mvp", strategy_type="sentiment_threshold_long", params={})
    session.add(cfg)
    session.commit()
    session.refresh(cfg)
    session.add(
        BacktestRun(
            config_id=cfg.id,
            ticker_ids=[],
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            model_version="m",
            prompt_version="v1",
            signal_set_fingerprint="abc",
            sharpe_ratio=0.5,
            max_drawdown=-0.1,
            win_rate=0.55,
            total_return=0.12,
            benchmark_return=0.08,
            run_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
        )
    )
    session.commit()
    app.dependency_overrides[get_db] = lambda: session
    body = TestClient(app).get("/backtests").json()
    assert body[0]["sharpe_ratio"] == 0.5
    assert body[0]["signal_set_fingerprint"] == "abc"
    app.dependency_overrides.clear()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_api_watchlist.py tests/test_api_signals.py tests/test_api_eval.py tests/test_api_backtests.py -v
```

Verify: that command
Expect: FAIL 404 or missing `get_db`.

- [ ] **Step 3: Implement `get_db`, include routers on `app`, query helpers. Keep `GET /health`.**

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_api_watchlist.py tests/test_api_signals.py tests/test_api_eval.py tests/test_api_backtests.py tests/test_health.py -v
```

Verify: that command
Expect: JSON contracts match fixtures; `/health` still 200.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/api tests/test_api_watchlist.py tests/test_api_signals.py tests/test_api_eval.py tests/test_api_backtests.py
git commit -m "feat: add read-only watchlist signal eval and backtest APIs"
```

---

### Task 2: Server-rendered dashboard

**Files:**
- Create: `src/signalbench/api/templates/dashboard.html`
- Create: `src/signalbench/api/routes/ui.py`
- Create: `tests/test_dashboard_html.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dashboard_html.py
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlmodel import Session

from signalbench.api.deps import get_db
from signalbench.api.main import app
from signalbench.db.models import EventType, RawDocument, Signal, Ticker


def test_dashboard_shows_ticker_and_rationale(session: Session) -> None:
    ticker = Ticker(symbol="AAPL", company_name="Apple Inc.", active=True)
    session.add(ticker)
    session.commit()
    session.refresh(ticker)
    doc = RawDocument(
        source="sec_edgar",
        external_id="html-1",
        doc_type="eight_k",
        raw_text="x",
        published_at=datetime(2024, 1, 15, tzinfo=timezone.utc),
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
            sentiment=0.4,
            event_type=EventType.earnings,
            confidence=0.8,
            rationale="Beat on EPS.",
        )
    )
    session.commit()
    app.dependency_overrides[get_db] = lambda: session
    html = TestClient(app).get("/").text
    assert "AAPL" in html
    assert "Beat on EPS." in html
    app.dependency_overrides.clear()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_dashboard_html.py -v
```

Verify: `uv run pytest tests/test_dashboard_html.py -v`
Expect: FAIL `/` 404.

- [ ] **Step 3: Jinja2 `dashboard.html` lists watchlist, signals table (rationale column), last backtest stats, eval accuracies as a simple table (dates + accuracy). `GET /` renders it. Add `jinja2` to `pyproject.toml` if missing.**

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_dashboard_html.py -v
```

Verify: same command
Expect: HTML contains `AAPL` and `Beat on EPS.`

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/api/templates/dashboard.html src/signalbench/api/routes/ui.py src/signalbench/api/main.py pyproject.toml tests/test_dashboard_html.py
git commit -m "feat: add read-only Jinja dashboard"
```

---

### Task 3: Disclaimer copy

**Files:**
- Modify: `README.md`
- Modify: `src/signalbench/api/templates/dashboard.html`
- Create: `tests/test_disclaimer.py`

Required phrases (case-insensitive match in tests): `research tool` and `not investment advice`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_disclaimer.py
from pathlib import Path

from fastapi.testclient import TestClient

from signalbench.api.main import app


def test_readme_disclaimer() -> None:
    text = Path("README.md").read_text(encoding="utf-8").lower()
    assert "research tool" in text
    assert "not investment advice" in text


def test_footer_disclaimer() -> None:
    html = TestClient(app).get("/").text.lower()
    assert "research tool" in html
    assert "not investment advice" in html
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_disclaimer.py -v
```

Verify: `uv run pytest tests/test_disclaimer.py -v`
Expect: FAIL until copy exists.

- [ ] **Step 3: Add the two phrases to README and a `<footer>` on the dashboard.**

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_disclaimer.py -v
```

Verify: same command
Expect: both files contain the framing.

- [ ] **Step 5: Commit**

```bash
git add README.md src/signalbench/api/templates/dashboard.html tests/test_disclaimer.py
git commit -m "docs: frame SignalBench as research not advice"
```

---

### Task 4: Eval trend ordering (lock)

Already tested in `test_eval_runs_ordered_by_run_at`. If that test lives in Task 1, this task is: add a tiny SVG or HTML table so two accuracies appear on `/`.

- [ ] **Step 1: Extend `test_dashboard_html.py`**

```python
def test_dashboard_lists_eval_accuracies(session: Session) -> None:
    # insert two EvalRun rows as in test_eval_runs_ordered_by_run_at
    app.dependency_overrides[get_db] = lambda: session
    html = TestClient(app).get("/").text
    assert "0.7" in html or "0.70" in html
    assert "0.82" in html
    app.dependency_overrides.clear()
```

Insert the same two `EvalRun` objects as in `tests/test_api_eval.py` (copy the constructor kwargs; do not write “see Task 1”).

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_dashboard_html.py::test_dashboard_lists_eval_accuracies -v
```

Verify: that node
Expect: FAIL if dashboard omits eval numbers.

- [ ] **Step 3: Render eval runs on the page in `run_at` order.**

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_dashboard_html.py -v
```

Verify: same file
Expect: both accuracies visible.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/api/templates/dashboard.html tests/test_dashboard_html.py
git commit -m "feat: show eval accuracy trend on dashboard"
```

---

## Phase 4 gate

```bash
uv run pytest
uv run ruff check src tests
uv run mypy src tests
```

Verify:

- TestClient `/watchlist`, `/signals`, `/eval-runs`, `/backtests` match fixtures.
- `GET /` contains a ticker + rationale.
- README and footer include research-tool / not-investment-advice.
- `/eval-runs` increasing `run_at` with accuracies.
- No auth middleware.
- Phases 0–3 tests still pass.
