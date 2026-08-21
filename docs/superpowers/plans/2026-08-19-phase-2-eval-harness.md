# Phase 2 — Eval Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Models:** cheap implementers `composer-2.5`; mid-tier implementers, reviewers, and fix loops `cursor-grok-4.6-high`. Never `*-fast` models. See [docs/superpowers/README.md](../README.md).

**Goal:** Score extraction quality against a git-versioned label file and fail CI when sentiment-direction accuracy drops more than 5 points versus a committed baseline.

**Architecture:** Labels are frozen `{id, raw_text, human_sentiment, human_event_type}` in `evals/labels.json` — not foreign keys to `raw_documents` (re-ingest must not destroy the portfolio artifact). Metrics run in-process on model outputs (or stored signals). `eval_runs` records history including failures. GitHub Actions runs the module when prompt or model config changes.

**Tech Stack:** Pydantic, pytest, JSON files, GitHub Actions, SQLModel `eval_runs`. Custom metrics (not DeepEval) for the gate; you can add DeepEval later without replacing this script.

**Depends on:** Phase 1 gate green. Reuse `ExtractionResult` / `ExtractedClaim` and `EventType` names.

**Out of scope:** dashboard charts, 150–200 human labels as a blocker (start with a 10-example fixture; expand later in the same file).

---

## File map

- Create: `evals/labels.json` — fixture labels with frozen text
- Create: `evals/baseline.json` — `{ "sentiment_direction_accuracy": 0.80 }`
- Create: `src/signalbench/eval/labels.py` — load/validate labels
- Create: `src/signalbench/eval/metrics.py` — accuracy, per-class P/R, calibration buckets
- Create: `src/signalbench/eval/gate.py` — compare to baseline, exit code
- Create: `src/signalbench/eval/__main__.py` — `python -m signalbench.eval`
- Modify: `src/signalbench/db/models.py` — `EvalRun`
- Create: `alembic/versions/0004_eval_runs.py`
- Create: `src/signalbench/eval/persist.py`
- Create: `.github/workflows/eval.yml`
- Create: `tests/test_eval_labels.py`, `tests/test_eval_metrics.py`, `tests/test_eval_gate.py`, `tests/test_eval_persist.py`, `tests/test_eval_workflow.py`
- Modify: `src/signalbench/cli.py` — optional `eval` command wrapping the module

---

### Task 1: Label file schema

**Files:**
- Create: `evals/labels.json`
- Create: `src/signalbench/eval/labels.py`
- Create: `tests/test_eval_labels.py`

Each label has frozen `raw_text`. No `document_id`.

```python
# src/signalbench/eval/labels.py
from pathlib import Path

from pydantic import BaseModel, Field

from signalbench.extraction.schema import EventTypeName


class Label(BaseModel):
    id: str
    raw_text: str = Field(min_length=1)
    human_sentiment: float = Field(ge=-1.0, le=1.0)
    human_event_type: EventTypeName
    notes: str | None = None


class LabelSet(BaseModel):
    labels: list[Label]


def load_labels(path: Path) -> LabelSet:
    return LabelSet.model_validate_json(path.read_text(encoding="utf-8"))
```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_eval_labels.py
from pathlib import Path

import pytest
from pydantic import ValidationError

from signalbench.eval.labels import LabelSet, load_labels

REPO = Path(__file__).resolve().parents[1]


def test_repo_labels_validate() -> None:
    loaded = load_labels(REPO / "evals" / "labels.json")
    assert len(loaded.labels) >= 10
    assert all(item.raw_text for item in loaded.labels)
    assert not hasattr(loaded.labels[0], "document_id") or "document_id" not in LabelSet.model_json_schema()["$defs"]["Label"]["properties"]


def test_rejects_missing_text() -> None:
    with pytest.raises(ValidationError):
        LabelSet.model_validate({"labels": [{"id": "x", "raw_text": "", "human_sentiment": 0.1, "human_event_type": "earnings"}]})
```

Fix the `document_id` assertion to: `assert "document_id" not in Label.model_fields`.

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_eval_labels.py -v
```

Verify: `uv run pytest tests/test_eval_labels.py -v`
Expect: FAIL missing `evals/labels.json` or module.

- [ ] **Step 3: Write 10 fixture labels and loader**

`evals/labels.json`:

```json
{
  "labels": [
    {"id": "ex01", "raw_text": "Company beats EPS estimates.", "human_sentiment": 0.8, "human_event_type": "earnings"},
    {"id": "ex02", "raw_text": "CEO resigns effective immediately.", "human_sentiment": -0.6, "human_event_type": "leadership"},
    {"id": "ex03", "raw_text": "Guidance raised for FY.", "human_sentiment": 0.7, "human_event_type": "guidance"},
    {"id": "ex04", "raw_text": "DOJ files antitrust suit.", "human_sentiment": -0.8, "human_event_type": "legal"},
    {"id": "ex05", "raw_text": "New product launch delayed.", "human_sentiment": -0.4, "human_event_type": "product"},
    {"id": "ex06", "raw_text": "Fed hike fears weigh on outlook.", "human_sentiment": -0.3, "human_event_type": "macro"},
    {"id": "ex07", "raw_text": "Routine 8-K announcing a conference.", "human_sentiment": 0.0, "human_event_type": "other"},
    {"id": "ex08", "raw_text": "Record revenue and margin expansion.", "human_sentiment": 0.9, "human_event_type": "earnings"},
    {"id": "ex09", "raw_text": "CFO appointed from internal bench.", "human_sentiment": 0.2, "human_event_type": "leadership"},
    {"id": "ex10", "raw_text": "Patent litigation settled with no admission.", "human_sentiment": 0.1, "human_event_type": "legal"}
  ]
}
```

Implement `labels.py` as specified.

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_eval_labels.py -v
```

Verify: `uv run pytest tests/test_eval_labels.py -v`
Expect: 10 labels validate; empty `raw_text` rejected.

- [ ] **Step 5: Commit**

```bash
git add evals/labels.json src/signalbench/eval/labels.py tests/test_eval_labels.py
git commit -m "feat: add git-versioned eval label fixtures"
```

---

### Task 2: Metrics (hand-computed fixture)

**Files:**
- Create: `src/signalbench/eval/metrics.py`
- Create: `tests/test_eval_metrics.py`

Sentiment **direction** match: `sign(pred) == sign(human)` treating `0` as its own class (both zero is a match).

Hand-computed for the test below: 8/10 = 0.8 direction accuracy.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_eval_metrics.py
from signalbench.eval.metrics import direction_accuracy, event_type_report, evaluate
from signalbench.extraction.schema import EventTypeName


def test_direction_accuracy_eight_of_ten() -> None:
    humans = [0.8, -0.6, 0.7, -0.8, -0.4, -0.3, 0.0, 0.9, 0.2, 0.1]
    preds = [0.5, -0.2, 0.1, -0.1, 0.2, -0.4, 0.0, 0.3, -0.1, 0.2]
    # mismatches: index 4 (human -0.4 vs pred +0.2), index 8 (human +0.2 vs pred -0.1)
    assert direction_accuracy(humans, preds) == 0.8


def test_event_type_report_perfect_on_two() -> None:
    y_true = [EventTypeName.earnings, EventTypeName.legal]
    y_pred = [EventTypeName.earnings, EventTypeName.legal]
    report = event_type_report(y_true, y_pred)
    assert report["earnings"]["precision"] == 1.0
    assert report["legal"]["recall"] == 1.0


def test_evaluate_returns_accuracy_and_report() -> None:
    humans_s = [1.0, -1.0]
    preds_s = [0.5, -0.2]
    humans_e = [EventTypeName.earnings, EventTypeName.legal]
    preds_e = [EventTypeName.earnings, EventTypeName.other]
    out = evaluate(
        human_sentiment=humans_s,
        pred_sentiment=preds_s,
        human_event=humans_e,
        pred_event=preds_e,
        pred_confidence=[0.9, 0.9],
        correct_direction=[True, True],
    )
    assert out.sentiment_direction_accuracy == 1.0
    assert "earnings" in out.event_type_metrics
    assert out.confidence_calibration is not None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_eval_metrics.py -v
```

Verify: `uv run pytest tests/test_eval_metrics.py -v`
Expect: FAIL import `direction_accuracy`.

- [ ] **Step 3: Implement metrics**

```python
# src/signalbench/eval/metrics.py
from dataclasses import dataclass
from typing import Sequence

from signalbench.extraction.schema import EventTypeName


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def direction_accuracy(human: Sequence[float], pred: Sequence[float]) -> float:
    if len(human) != len(pred) or not human:
        raise ValueError("human and pred must be same non-empty length")
    matches = sum(_sign(h) == _sign(p) for h, p in zip(human, pred, strict=True))
    return matches / len(human)


def event_type_report(
    y_true: Sequence[EventTypeName],
    y_pred: Sequence[EventTypeName],
) -> dict[str, dict[str, float]]:
    labels = [e.value for e in EventTypeName]
    report: dict[str, dict[str, float]] = {}
    for label in labels:
        tp = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t.value == label and p.value == label)
        fp = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t.value != label and p.value == label)
        fn = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t.value == label and p.value != label)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        report[label] = {"precision": precision, "recall": recall, "f1": f1, "support": tp + fn}
    return report


@dataclass
class EvalMetrics:
    sentiment_direction_accuracy: float
    event_type_metrics: dict[str, dict[str, float]]
    confidence_calibration: dict[str, float] | None


def evaluate(
    *,
    human_sentiment: Sequence[float],
    pred_sentiment: Sequence[float],
    human_event: Sequence[EventTypeName],
    pred_event: Sequence[EventTypeName],
    pred_confidence: Sequence[float],
    correct_direction: Sequence[bool] | None = None,
) -> EvalMetrics:
    acc = direction_accuracy(human_sentiment, pred_sentiment)
    report = event_type_report(human_event, pred_event)
    if correct_direction is None:
        correct_direction = [
            _sign(h) == _sign(p) for h, p in zip(human_sentiment, pred_sentiment, strict=True)
        ]
    buckets: dict[str, list[bool]] = {"0.0-0.5": [], "0.5-0.9": [], "0.9-1.0": []}
    for conf, ok in zip(pred_confidence, correct_direction, strict=True):
        if conf >= 0.9:
            buckets["0.9-1.0"].append(ok)
        elif conf >= 0.5:
            buckets["0.5-0.9"].append(ok)
        else:
            buckets["0.0-0.5"].append(ok)
    calibration = {
        name: (sum(vals) / len(vals) if vals else 0.0) for name, vals in buckets.items()
    }
    return EvalMetrics(acc, report, calibration)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_eval_metrics.py -v
```

Verify: `uv run pytest tests/test_eval_metrics.py -v`
Expect: `direction_accuracy` is exactly `0.8` on the 10-value fixture.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/eval/metrics.py tests/test_eval_metrics.py
git commit -m "feat: add eval direction accuracy and class metrics"
```

---

### Task 3: Baseline gate (5 point drop)

**Files:**
- Create: `evals/baseline.json`
- Create: `src/signalbench/eval/gate.py`
- Create: `tests/test_eval_gate.py`

```python
# src/signalbench/eval/gate.py
from pathlib import Path

from pydantic import BaseModel


class Baseline(BaseModel):
    sentiment_direction_accuracy: float
    max_drop: float = 0.05


def load_baseline(path: Path) -> Baseline:
    return Baseline.model_validate_json(path.read_text(encoding="utf-8"))


def passed_ci_gate(accuracy: float, baseline: Baseline) -> bool:
    return accuracy >= baseline.sentiment_direction_accuracy - baseline.max_drop
```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_eval_gate.py
from signalbench.eval.gate import Baseline, passed_ci_gate


def test_fails_when_drop_exceeds_five_points() -> None:
    baseline = Baseline(sentiment_direction_accuracy=0.80, max_drop=0.05)
    assert passed_ci_gate(0.75, baseline) is True
    assert passed_ci_gate(0.74, baseline) is False


def test_passes_at_or_above_baseline() -> None:
    baseline = Baseline(sentiment_direction_accuracy=0.80, max_drop=0.05)
    assert passed_ci_gate(0.80, baseline) is True
    assert passed_ci_gate(0.90, baseline) is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_eval_gate.py -v
```

Verify: `uv run pytest tests/test_eval_gate.py -v`
Expect: FAIL import.

- [ ] **Step 3: Write `gate.py` and `evals/baseline.json`**

```json
{"sentiment_direction_accuracy": 0.80, "max_drop": 0.05}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_eval_gate.py -v
```

Verify: `uv run pytest tests/test_eval_gate.py -v`
Expect: `0.74` fails; `0.75` passes.

- [ ] **Step 5: Commit**

```bash
git add evals/baseline.json src/signalbench/eval/gate.py tests/test_eval_gate.py
git commit -m "feat: fail eval gate when accuracy drops more than 5 points"
```

---

### Task 4: Persist `eval_runs` including failures

**Files:**
- Modify: `src/signalbench/db/models.py`
- Create: `src/signalbench/eval/persist.py`
- Create: `tests/test_eval_persist.py`
- Create: `alembic/versions/0004_eval_runs.py`

```python
class EvalRun(SQLModel, table=True):
    __tablename__ = "eval_runs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    model_version: str
    prompt_version: str
    git_commit_sha: str | None = None
    label_set_git_sha: str | None = None
    n_examples: int
    sentiment_accuracy: float
    event_type_metrics: dict = Field(sa_column=Column(JSON, nullable=False))
    confidence_calibration: dict | None = Field(default=None, sa_column=Column(JSON))
    passed_ci_gate: bool
    run_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_eval_persist.py
from sqlmodel import Session, select

from signalbench.eval.persist import record_eval_run
from signalbench.db.models import EvalRun


def test_failed_run_still_inserts(session: Session) -> None:
    record_eval_run(
        session,
        model_version="deepseek-ai/DeepSeek-V4-Flash-0731",
        prompt_version="v1",
        git_commit_sha="abc",
        label_set_git_sha="def",
        n_examples=10,
        sentiment_accuracy=0.50,
        event_type_metrics={"earnings": {"f1": 0.0}},
        confidence_calibration={"0.9-1.0": 0.0},
        passed_ci_gate=False,
    )
    row = session.exec(select(EvalRun)).one()
    assert row.passed_ci_gate is False
    assert row.sentiment_accuracy == 0.50
    assert row.label_set_git_sha == "def"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_eval_persist.py -v
```

Verify: `uv run pytest tests/test_eval_persist.py -v`
Expect: FAIL missing `EvalRun` / `record_eval_run`.

- [ ] **Step 3: Implement model + `record_eval_run` (always `session.add` + commit, even when `passed_ci_gate` is False)**

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_eval_persist.py -v
```

Verify: `uv run pytest tests/test_eval_persist.py -v`
Expect: row inserted with `passed_ci_gate=false`.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/db/models.py src/signalbench/eval/persist.py alembic/versions/0004_eval_runs.py tests/test_eval_persist.py
git commit -m "feat: persist eval_runs including failed CI gates"
```

---

### Task 5: CLI module + GitHub Actions

**Files:**
- Create: `src/signalbench/eval/__main__.py`
- Create: `.github/workflows/eval.yml`
- Create: `tests/test_eval_workflow.py`

`python -m signalbench.eval`:

1. Load labels + baseline.
2. Get predictions (fake LLM in CI: map each label `id` through `FakeLLM` or a deterministic stub that scores the fixture at baseline).
3. `evaluate(...)`.
4. `record_eval_run` if `DATABASE_URL` is set; skip DB in GHA job unless service container is added. **GHA job must not require Postgres.** Persist is optional; **exit code** is required.
5. `sys.exit(0 if passed_ci_gate else 1)`.

Workflow:

```yaml
# .github/workflows/eval.yml
name: eval-gate
on:
  pull_request:
    paths:
      - "prompts/**"
      - "src/signalbench/extraction/**"
      - "src/signalbench/eval/**"
      - "evals/**"
      - "src/signalbench/config.py"
jobs:
  eval:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
      - run: uv sync --group dev
      - run: uv run python -m signalbench.eval
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_eval_workflow.py
from pathlib import Path

def test_workflow_path_filters() -> None:
    text = Path(".github/workflows/eval.yml").read_text(encoding="utf-8")
    assert "prompts/**" in text
    assert "uv run python -m signalbench.eval" in text
```

Also test `__main__` exit codes with monkeypatch of `evaluate` / `passed_ci_gate` if the module is structured with a `main() -> int`.

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_eval_workflow.py -v
```

Verify: `uv run pytest tests/test_eval_workflow.py -v`
Expect: FAIL missing workflow file.

- [ ] **Step 3: Write `__main__.py` and workflow**

Deterministic CI stub: predict the **human** labels so accuracy is 1.0 until real extraction is wired; then switch the runner to call the extractor on `raw_text` with FakeLLM/Together. For the gate test, include `tests/test_eval_main_exit.py`:

```python
from signalbench.eval.gate import Baseline, passed_ci_gate

def test_main_logic_fail():
    assert passed_ci_gate(0.70, Baseline(sentiment_direction_accuracy=0.80)) is False
```

(Already covered in Task 3.) `__main__.py` must call `passed_ci_gate` and exit 1 on False.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_eval_workflow.py tests/test_eval_gate.py -v
uv run python -m signalbench.eval
```

Verify: pytest passed; local `python -m signalbench.eval` exits 0 on the stub/fixture.
Expect: workflow file contains path filters; module is runnable without Postgres.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/eval/__main__.py .github/workflows/eval.yml tests/test_eval_workflow.py src/signalbench/cli.py
git commit -m "feat: run eval CI gate on prompt and model changes"
```

---

## Phase 2 gate

```bash
uv run pytest
uv run ruff check src tests
uv run mypy src tests
uv run python -m signalbench.eval; echo $?
```

Verify:

- Labels live in git with frozen `raw_text`.
- `direction_accuracy` 10-example fixture is 0.8.
- Accuracy 5+ points below baseline → `passed_ci_gate` False → process exit 1.
- Failed runs can insert `eval_runs.passed_ci_gate=false` when a session is provided.
- `.github/workflows/eval.yml` path-filters `prompts/**` and extraction/eval config.
- Phase 0–1 tests still pass.
