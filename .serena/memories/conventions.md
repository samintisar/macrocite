# Conventions

- `str | None`, not `Optional[str]`. `datetime.UTC`, not `timezone.utc`.
- Await all DB writes. Ingest helpers `session.commit()` themselves — `get_session()` does not auto-commit; closing without commit rolls back (Phase 0 CLI filings bug).
- Public Convex-style validators do not apply; this is SQLModel/Postgres. Still type-narrow; no `any`.
- Tests: TDD as specified in phase plans. SQLite in-memory; `SQLModel.metadata.create_all`. Alembic must still match model metadata (tests will not catch drift).
- `UTCDateTime` TypeDecorator wraps `DateTime(timezone=True)` and is **only** for `RawDocument.published_at`. Other tz columns (`added_at`, `ingested_at`) use `Column(DateTime(timezone=True))`.
- Dedup: `(source, external_id)`; EDGAR accession number. Not `(source, url, published_at)`.
- Watchlist path is repo-root `data/watchlist.yaml` (`Path(__file__).resolve().parents[2]` from `cli.py`).

Schema invariants: `mem:schema`.
