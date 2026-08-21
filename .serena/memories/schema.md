# Schema invariants (Phase 0)

`RawDocument` has **no** `ticker_id`. Link via `DocumentTicker` (M2M). FKs: document `CASCADE`, ticker `RESTRICT`.

`Price.adj_close` is required (split-adjusted close for backtests). Unique `(ticker_id, date)`.

`DocType` includes `news` for forward compat; Phase 0 does not ingest news.

Unique docs: `uq_raw_documents_source_external_id` on `(source, external_id)`.

Alembic `0001_phase0` is the Postgres source of truth for Phase 0 tables. Tests use metadata `create_all` on SQLite — keep models and migrations aligned.

Phase 1 adds `signals` unique `(document_id, ticker_id, model_version, prompt_version)`. Extraction may name a ticker not on `document_tickers`.

Phase 2 adds `eval_runs` (Alembic `0004_eval_runs`; `0003` already widened `prices.volume`). Labels live in `evals/labels.json` with frozen `raw_text` — not FKs to `raw_documents`. Failed CI gates still insert (`passed_ci_gate=false`).

See `mem:conventions`, `mem:core`.
