# Tech stack

Python ≥3.11, `uv`, FastAPI, SQLModel, Alembic, Postgres 16 (Compose), Typer, httpx, yfinance, pydantic-settings.

Pins that matter (`pyproject.toml`): `ruff==0.16.4`, `mypy==2.3.1`. mypy `strict` on package `signalbench`. yfinance `ignore_missing_imports`; numpy stubs `follow_imports = skip`.

CLI entry: `signalbench = signalbench.cli:app`.

Tests never call Together (or any live LLM). Inject fake LLM. No live API keys in CI.

Phase 1 adds Together SDK v2 (`together>=2.0.0`) for production extraction only: `TogetherLLM` uses serverless chat completions with `response_format` `json_schema`. Default `model_version` is `deepseek-ai/DeepSeek-V4-Flash-0731`. `TogetherLLM` parses `message.content` (not `reasoning`) and passes `reasoning={"enabled": False}` so extract does not bill thinking tokens. Env: `TOGETHER_API_KEY`. Keep tests on a protocol/`FakeLLM`. Not Anthropic. Not Together Batch API.

See also `mem:suggested_commands`, `mem:task_completion`.
