# Spec 03 — Jev Reader

**Parent:** [Overview](2026-09-22-swing-assistant-00-overview.md) · **Depends on:** spec 01 (documents with `text`, `acceptance_at`) and spec 02 (`ReadingsView` port, simulator, pass bar).
**Goal:** Have Jev read every stored 8-K and news item once. Decide by a pre-registered rule whether Jev's negative reading is allowed to block entries. Backtest the Sentiment setup. Publish a calibration report.

## Jev facts this design relies on (docs checked 2026-09-22; API checked live 2026-09-24)

- Called through OpenRouter: `POST https://openrouter.ai/api/alpha/decisions` (not `/api/v1/…`), `Authorization: Bearer $OPENROUTER_API_KEY`, `Content-Type: application/json`, body `{model, state, questions}` (`session_id` and `user` are optional and not sent). Model `typesafe/jev-1.13`. The response `model` carries the dated build, e.g. `typesafe/jev-1.13-20260917`.
- Answer shapes: `choice` returns `choice`, `probabilities` (sums to 1), `confidence`. `noul` returns the probability of yes.
- `confidence` = `(3 × max probability − 1) / 2` for 3 options. TypeSafe does **not** claim it is calibrated. **This design never uses `confidence`**; it uses `probabilities` and the `noul` value.
- Documented weak spots: arithmetic, dates, long irrelevant context, prompt injection in the state. So we send cleaned, trimmed text with no dates and ask no numeric questions.
- Limits: 64K tokens per request, 32K of it for the state. Spec 01 caps `text` at about 24K tokens.
- Errors: 400, 401, 402 (insufficient credits), 403, 404, 413 (too large), 429, 500, 502, 503, 524, 529. Retry 429 and 5xx with exponential backoff; never retry the other 4xx. 401 and 402 stop the whole backfill with a clear message. 413 skips that document and records why.
- Price: $0.042 per 1M input tokens; output tokens are free. The full backfill (about 5,100 8-Ks averaging ~19K characters, and about 80,000 news items after the daily cap) is estimated at $3–4.

## Question set `q1`

State: `"Company: {company_name} ({us_symbol})\nSource: {'SEC 8-K, items ' + items | 'News'}\n\n{text}"`.

```json
{
  "impact": {
    "type": "choice",
    "instructions": "What does this document mean for the company's common shareholders?",
    "criteria": {
      "negative": "Clearly bad news: weak results or lowered guidance, lawsuits or investigations with real exposure, forced executive departures, impairments, restatements, liquidity problems, loss of a major customer or product.",
      "neutral": "Routine, procedural, or mixed news with no clear effect on the business: meeting and voting results, routine appointments, ordinary debt issuance, exhibit-only filings, commentary without new facts.",
      "positive": "Clearly good news: strong results or raised guidance, major contracts or approvals, new or larger buybacks or dividends, favourable legal outcomes, value-adding deals."
    }
  },
  "event_type": {
    "type": "choice",
    "instructions": "What kind of event is this document mainly about?",
    "criteria": {
      "earnings": "Reported financial results.",
      "guidance": "Forecasts or outlook changes.",
      "leadership": "Executive or board changes.",
      "legal": "Lawsuits, investigations, regulatory actions, settlements.",
      "product": "Products, contracts, customers, partnerships, approvals.",
      "macro": "Economy-wide or industry-wide conditions.",
      "other": "Anything else, including administrative filings."
    }
  },
  "routine": {
    "type": "noul",
    "instructions": "Is this a routine administrative document with no new business information?",
    "criteria": {
      "true": "Procedural or boilerplate: meeting results, standard exhibits, scheduled notices.",
      "false": "Contains new information about the business, its results, or its prospects."
    }
  }
}
```

The question set is versioned (`q1`). Changing any wording creates `q2`, and documents must be re-read under `q2` before any `q2` reading is used.

## Storage

**Table `jev_readings`** (migration `0011`; `0010` is spec 02's `backtest_runs`):
- `id`, `document_id` (FK, cascade)
- `model_requested`, `model_resolved`, `question_set`
- `p_negative`, `p_neutral`, `p_positive`, `event_type`, `p_routine`, plus the full `answers` JSON
- `input_tokens`, `cost_usd`, `latency_ms`, `read_at`
- Unique on (`document_id`, `model_requested`, `question_set`).

**Client (`jev/client.py`):**
- httpx with a 10 s timeout, 3 retries with exponential backoff on 429 and 5xx, concurrency 4.
- A `JevClient` protocol with a `FakeJevClient` for tests.
- **Budget guard:** every CLI command takes `--max-cost-usd` (default 10.00) and stops cleanly once the cumulative `usage.cost` reaches it.

**Privacy:** only public documents (SEC filings, news headlines and summaries) are sent. Holdings, fills, and the ledger are never sent.

**News volume cap:** at most 20 news documents per symbol per calendar day are read, the most recent first. The cap is logged.

## Readings in the strategy

`ReadingsView.at(as_of)` sees only documents whose legal close ≤ `as_of`. For a symbol:

| Term | Definition |
| --- | --- |
| **negative** | a document in the last 10 sessions with `p_negative ≥ θ_block` |
| **positive** | a document with `p_positive ≥ 0.70` **and** `p_routine < 0.50` (fixed; not fitted, because it triggers a setup) |
| **catalyst** | a positive document in the last 10 sessions. Ranks the candidate first (spec 02) |
| **sentiment trigger** | a positive document in the last 3 sessions. Feeds the Sentiment setup (spec 02 table) |
| **block** | only when the filter is ON: a negative document means skip with reason `blocked` |

A document with no reading counts as neither positive nor negative. Live signals then show "⚠️ Jev check unavailable — read the filing yourself" (spec 05).

## Filter decision (pre-registered)

1. Take the jev-off trade logs of the Pullback and Breakout v1 runs (spec 02), including a setup that failed its pass bar. For each trade, record the max `p_negative` among the symbol's documents with legal close in the 10 sessions up to the signal date.
2. **Fit window**, signals 2016-01-01 → 2022-12-31. For θ in {0.5, 0.6, 0.7, 0.8, 0.9}, count θ as eligible if at least 10 trades have max `p_negative ≥ θ`. Choose the eligible θ with the largest (mean R of kept trades − mean R of blocked trades). If no θ is eligible, the filter is **information-only**.
3. **Confirm window**, signals 2023-01-01 → end, with the chosen θ. The filter is **ON** only if at least 10 trades are blocked **and** the blocked mean R < the kept mean R. Otherwise it is information-only.
4. Spec 02's v1 result stands: Pullback and Breakout both failed their pass bar (overview, 2026-09-24), and a filtered run can never overturn that. If the filter is ON, run Pullback and Breakout with `--jev filter` for information only; each report says "information only — cannot change the v1 result". If the filter is information-only, `--jev filter` runs are refused.
5. Write the question set, the model requested, θ, the ON/information-only outcome, both windows, the counts and mean Rs, and the report path to a new committed file, `data/jev_filter_v1.yaml`, in a commit that references the report. `data/strategy_v1.yaml` is never edited: its `config_sha256` is recorded in the stored v1 runs, and the pre-registration guard refuses a changed v1.

Known limit: Finnhub news only covers about the last year, so the fit window is effectively filings-only.

## Sentiment setup backtest

- Rules as in spec 02. Period 2016-01-01 → end. The halves split at the calendar midpoint of that period. Same pass bar otherwise.
- Trades with signal date ≥ 2026-09-15 (after Jev's release) are reported separately as the only fully out-of-sample sample. Jev's training cutoff is unpublished, so older results may be optimistic.
- If it has fewer than 30 trades, the setup is **information-only**: live messages may mention positive documents, but no Sentiment entries are sent.

## Calibration report (information only)

- **Outcome label** per document: the symbol's excess return over QQQ from its legal close to 5 sessions later, using adj_close. Above +2% is `up`, below −2% is `down`, otherwise `flat`.
- **Report contents:**
  - reliability tables for `p_positive` vs `up` and `p_negative` vs `down`, in deciles, with count, mean predicted, and observed rate
  - ECE for each
  - accuracy of the argmax `impact` vs the label, next to the majority-class baseline
  - all of the above split into before and after 2026-09-15
- Written to `reports/jev/<YYYY-MM-DD>-calibration.md`.
- Changing θ or the 0.70 positive threshold after reading this report is an owner decision, recorded in this spec's changelog. **Nothing is adjusted automatically.**

## CLI

```text
signalbench jev test                      # one real call on a fixture 8-K; prints answers, latency, cost
signalbench jev backfill [--source filings|news|all] [--since DATE] [--max-cost-usd N]
signalbench jev fit-filter                # steps 1–3 above; prints the decision; writes nothing without --write
signalbench jev calibration
```

## Testing

- Request payload shape against the documented schema: the fake client asserts the body.
- Response parsing for choice and noul. Rejecting probabilities that don't sum to 1 ± 0.01.
- Retries on 429/5xx. The budget guard stops at the limit.
- Idempotent backfill: a second run makes no calls.
- `ReadingsView` legal-close visibility: a document accepted at 16:05 ET is invisible at that day's close and visible the next session.
- Filter decision on crafted trade lists: no eligible θ, a fit that doesn't confirm, and a fit that confirms.
- Calibration buckets and ECE against a hand-computed small example.

## Gate

- CI green.
- `jev test` succeeds against the live API. Record latency and cost.
- Backfill complete. Record documents read, total cost, and the resolved model build.
- The filter decision and the Sentiment setup report are committed under `reports/`.
- The calibration report is committed.

## Changelog

- 2026-09-22: created.
- 2026-09-24: API facts checked live: the endpoint is `https://openrouter.ai/api/alpha/decisions`; the error codes and retry rules above; $0.042 per 1M input tokens, output free. `jev_readings` is migration `0011`. The filter decision is written to `data/jev_filter_v1.yaml` instead of `data/strategy_v1.yaml`, which must not change. Step 4 now follows the spec 02 go/no-go (both v1 setups failed): a filtered run is information only, and `--jev filter` is refused while the filter is information-only. Plan: `2026-09-24-swing-03-jev-reader.md`.
