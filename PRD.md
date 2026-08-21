# SignalBench — PRD

**A signal-extraction and backtesting research console with an eval harness as its core differentiator.**

Status: Draft v1
Owner: Samin
Last updated: 2026-08-20

---

## 1. Problem Statement

LLM-generated trading/research signals are easy to produce and hard to trust. Most "AI trading bot" side projects skip the part that actually matters: **does the signal extraction hold up under scrutiny, and does it stay reliable as prompts/models change?**

SignalBench is a research tool that extracts structured signals (sentiment, event type, confidence) from financial news and SEC filings using an LLM, backtests simple strategies built on those signals, and — critically — evaluates the *extraction quality itself* against a hand-labeled dataset as a CI gate. This mirrors the eval-suite-as-CI-gate pattern already built for LLM Reliability Console, applied to a new domain.

## 2. Goals

- **Portfolio goal:** Produce a second concrete, rigor-first AI engineering artifact (alongside LLM Reliability Console) demonstrating structured extraction, backtesting, and eval-driven CI — not just "LLM wrapper" work.
- **Personal goal:** Build a decision-support tool Samin can actually use to inform (not automate) personal research and trade decisions — surfacing signals and their historical backtested behavior, not executing trades.

## 3. Non-Goals

- **Not** an automated execution/trading bot. No broker API integration, no order placement, no live capital at risk from agent decisions.
- **Not** intended to produce or claim a validated "alpha" strategy. Backtest results are for research/learning, not performance claims.
- Not multi-asset-class in v1 — equities only (US-listed, liquid tickers) to keep data sourcing simple.

*Note on framing:* Since this is meant for both a resume and personal trading use, the assumption here is "personal use" means Samin reviews signals and backtest output to inform manual decisions — not that the system executes trades autonomously. If autonomous execution is actually wanted later, that's a distinct and much higher-stakes project (needs paper-trading validation, risk controls, and probably its own PRD) — flagging this as a deliberate scope boundary, not an oversight.

## 4. Target Users

1. **Hiring managers / technical interviewers** reviewing Samin's portfolio — should be able to look at the eval harness and CI gate and immediately see production-grade ML engineering practice, not a notebook demo.
2. **Samin (personal use)** — wants a dashboard of recent signals per watchlist ticker, with backtested historical performance of similar past signals, to sanity-check research decisions.

## 5. Success Metrics

| Metric | Target |
|---|---|
| Signal extraction accuracy vs. hand-labeled eval set | ≥ 80% agreement (sentiment direction) at MVP |
| CI gate | Blocks merge if eval accuracy drops > 5pts vs. baseline |
| Backtest reproducibility | Same signal set + strategy params → identical output (deterministic) |
| Personal usage | Dashboard checked ≥ 3x/week during active research |
| Portfolio outcome | Referenced in ≥ 3 interview conversations as a technical deep-dive |

## 6. System Architecture

```
[Data Ingestion] → [Signal Extraction (LLM)] → [Postgres] → [Backtest Engine] → [Dashboard]
                                    ↑
                         [Eval Harness] ← [Hand-labeled dataset]
                                    ↓
                            [CI/CD Gate — GitHub Actions]
```

### 6.1 Data Ingestion
- **News:** Alpha Vantage News Sentiment endpoint (free tier) as primary source — already returns some sentiment metadata useful for eval comparison.
- **Filings:** SEC EDGAR full-text search API for 8-K (event-driven, highest signal density) as MVP scope; 10-K/10-Q as stretch.
- **Prices:** yfinance for OHLCV, daily granularity at MVP (intraday is a stretch goal).
- Ingestion runs on a scheduled job (cron or GitHub Actions scheduled workflow), writes raw docs to Postgres with dedup on (source, external_id); EDGAR uses accession number.

### 6.2 Signal Extraction
- LLM call (Together AI chat completions) per document, forced structured output via `response_format` `json_schema`:
  ```json
  {
    "ticker": "string",
    "sentiment": -1.0 to 1.0,
    "event_type": "earnings | guidance | leadership | legal | product | macro | other",
    "confidence": 0.0 to 1.0,
    "rationale": "one sentence, for human review"
  }
  ```
- Model choice is swappable via config — this matters for the eval harness (compare model versions against the same labeled set).
- Store raw LLM output + parsed structured fields + model/prompt version used (for later drift analysis).

### 6.3 Eval Harness (the differentiator)
- Hand-labeled dataset: 150–200 examples (Samin's own judgment on sentiment direction + event type), stored as JSON/CSV, versioned in the repo.
- Eval script computes accuracy, precision/recall per event_type class, and confidence calibration (does confidence=0.9 actually correlate with correctness?).
- Runs as a GitHub Actions job on every PR that touches the extraction prompt or model config. Fails the build if accuracy regresses beyond threshold.
- Dashboard tracks eval score over time (per prompt/model version) — this chart alone is a strong interview talking point.

### 6.4 Backtest Engine
- `vectorbt` for speed and clean numpy-based API.
- MVP strategy: long entry when sentiment > threshold, exit after N trading days or on a stop-loss/take-profit, one ticker at a time.
- Output metrics: Sharpe ratio, max drawdown, win rate, total return vs. buy-and-hold benchmark.
- Strategy parameters (threshold, holding period) are config-driven, not hardcoded — enables parameter sweep as a stretch feature.

### 6.5 Dashboard
- Simple read-only view: watchlist tickers, latest signals with rationale, backtest summary stats per strategy config, eval score trend chart.
- v1: server-rendered or lightweight React page reading from Postgres. No auth needed (single user, local/private deployment).

## 7. Tech Stack

Design principle: complement LLM Reliability Console, don't duplicate it. That project already covers Azure/AKS/Terraform/infra depth — SignalBench should hit a different slice of the AI engineer skill set (structured LLM outputs, data pipelines, evals, vector search) so the two projects together cover more ground than either alone.

| Component | Choice | Rationale / resume signal |
|---|---|---|
| Backend | Python 3.11 / FastAPI | Standard for AI service backends; reuses familiar patterns |
| Package/env mgmt | uv | Fast-emerging standard; signals current tooling awareness over pip/poetry |
| Structured LLM output | Pydantic v2 + Together `json_schema` (`ExtractionResult.model_json_schema()`) | Industry-standard structured extraction without Anthropic cost; schema-constrained JSON shows the contract is enforced at the API, not just in the prompt |
| ORM / DB models | SQLModel | Pydantic + SQLAlchemy combined, built by FastAPI's author; trending in the ecosystem |
| Database | PostgreSQL (+ pgvector extension) | Time-series + relational joins (signals ↔ prices ↔ tickers); pgvector enables a "similar historical signals" semantic search feature, reusing SolomindLM's embeddings/RAG experience in a new domain |
| Signal LLM | Together AI (`deepseek-ai/DeepSeek-V4-Flash-0731` default; `model_version` in config) | Swap-testable for eval harness; serverless chat is cheaper than Claude for per-document extraction |
| Eval framework | DeepEval or promptfoo + custom metrics on top | Recognized eval tooling is increasingly asked about by name in interviews; custom metrics on top show raw engineering isn't outsourced entirely |
| Backtesting | vectorbt | Fast, numpy-vectorized, good docs |
| Data sources | Alpha Vantage, SEC EDGAR, yfinance | Free tiers sufficient at MVP scale |
| Containerization | Docker | Near-universal ask; cheap to add, wasn't explicit in LLM Reliability Console's story beyond AKS |
| Testing / lint | pytest + ruff + mypy | Baseline engineering hygiene; do from day one, not bolted on later |
| Orchestration | Cron / GitHub Actions scheduled workflow at MVP; consider Prefect or Dagster once Phase 0/1 are stable | Real resume signal for orchestration tooling, but the pipeline is currently linear — don't add setup overhead before something is running |
| CI/CD | GitHub Actions | Reuse existing eval-gate pattern from LLM Reliability Console |
| Deployment | Local/personal VM or same Azure setup as LLM Reliability Console | Keep infra costs low; this isn't the infra-showcase project |

**Deliberately skipped:** LangGraph / multi-agent orchestration. LLM Reliability Console already demonstrates orchestration skill via its CI/CD gate logic, and this pipeline (ingest → extract → validate → store) is genuinely linear. Forcing a graph framework onto a linear flow reads as keyword-stuffing to anyone who inspects the code — which cuts against the story in a technical interview rather than helping it.

## 8. Build Order (MVP-first)

1. **Phase 0 — Data plumbing:** Ingest 8-Ks + daily prices for a fixed watchlist (~15–20 tickers). Get raw data into Postgres.
2. **Phase 1 — Signal extraction:** One prompt, structured output, stored per document. Manually spot-check quality.
3. **Phase 2 — Eval harness:** Hand-label 150–200 examples. Build eval script. Wire into GitHub Actions as a CI gate.
4. **Phase 3 — Backtest engine:** One strategy, one chart (Sharpe/drawdown/win rate vs. buy-and-hold).
5. **Phase 4 — Dashboard:** Read-only view tying signals + backtest + eval trend together.
6. **Phase 5 (stretch):** News source added, 10-K/10-Q support, parameter sweep, intraday data, confidence calibration analysis.

## 9. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Backtest overfitting to a small ticker set | Keep strategy params simple at MVP; note limitation explicitly in README |
| Eval label bias (Samin labeling own eval set) | Acceptable for portfolio/personal use; note as a known limitation, not hidden |
| Free-tier API rate limits | Batch ingestion jobs, cache aggressively, fall back to daily granularity |
| Scope creep toward "real trading bot" | Explicit non-goal in this doc; execution/automation is a separate future decision, not default direction |
| Presenting backtest results as investment advice | Dashboard and README explicitly frame this as a research tool, not a recommendation engine |
| News-source historical coverage is limited (Alpha Vantage News Sentiment only covers ~March 2022 onward on the free tier) | Backtest window is capped at ~2022–present for news-driven signals; filings-based signals (SEC EDGAR) and price data (yfinance) have no such limit and can extend further back if needed. Note this explicitly in the README rather than presenting backtest results as covering a longer history than the data supports. A paid news provider is a possible future upgrade if a longer window becomes necessary. |

## 10. Out of Scope (v1)

- Live order execution / broker integration
- Multi-asset classes (options, crypto, futures)
- Real-time/streaming data
- Multi-user auth or hosted SaaS version

## 11. Open Questions

- Fixed watchlist vs. configurable? (Leaning fixed ~20 tickers for MVP simplicity.)
- Should the eval dataset be public in the repo (portfolio transparency) or kept private (avoids "teaching to the test" concerns if ever expanded)? Leaning public — transparency is part of the pitch to hiring managers.
- Reuse Azure/AKS infra from LLM Reliability Console, or keep this one deliberately simpler/cheaper to run? Leaning simpler — this project's story is about eval rigor, not infra, so don't dilute the narrative.
