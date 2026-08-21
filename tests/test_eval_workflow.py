from pathlib import Path

import pytest
from typer.testing import CliRunner

from signalbench.cli import app
from signalbench.eval.labels import load_labels
from signalbench.extraction.schema import (
    EventTypeName,
    ExtractedClaim,
    ExtractionResult,
)

REPO = Path(__file__).resolve().parents[1]


class _OracleLLM:
    def complete(self, prompt: str, raw_text: str) -> ExtractionResult:
        assert prompt
        by_text = {item.raw_text: item for item in load_labels(REPO / "evals" / "labels.json").labels}
        item = by_text[raw_text]
        return ExtractionResult(
            claims=[
                ExtractedClaim(
                    ticker="TEST",
                    sentiment=item.human_sentiment,
                    event_type=item.human_event_type,
                    confidence=1.0,
                    rationale="oracle",
                )
            ]
        )


class _AlwaysNegativeLLM:
    def complete(self, prompt: str, raw_text: str) -> ExtractionResult:
        return ExtractionResult(
            claims=[
                ExtractedClaim(
                    ticker="TEST",
                    sentiment=-1.0,
                    event_type=EventTypeName.other,
                    confidence=0.9,
                    rationale="negative",
                )
            ]
        )


def test_workflow_path_filters() -> None:
    text = (REPO / ".github" / "workflows" / "eval.yml").read_text(encoding="utf-8")
    assert "prompts/**" in text
    assert "uv run python -m signalbench.eval" in text
    assert "secrets.TOGETHER_API_KEY" in text


def test_main_returns_zero_on_fixture_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from signalbench.eval import __main__ as eval_main

    monkeypatch.setattr(eval_main, "_default_llm", lambda: _OracleLLM())
    assert eval_main.main() == 0


def test_main_scores_llm_claims_not_copied_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from signalbench.eval import __main__ as eval_main

    monkeypatch.setattr(eval_main, "_default_llm", lambda: _AlwaysNegativeLLM())
    assert eval_main.main() == 1


def test_main_returns_one_when_gate_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from signalbench.eval import __main__ as eval_main

    monkeypatch.setattr(eval_main, "_default_llm", lambda: _OracleLLM())
    monkeypatch.setattr(eval_main, "passed_ci_gate", lambda *args, **kwargs: False)
    assert eval_main.main() == 1


def test_main_skips_persist_when_database_url_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from signalbench.eval import __main__ as eval_main

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("must not persist without DATABASE_URL")

    monkeypatch.setattr(eval_main, "_default_llm", lambda: _OracleLLM())
    monkeypatch.setattr("signalbench.eval.persist.record_eval_run", boom)
    assert eval_main.main() == 0


def test_cli_help_lists_eval() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "eval" in result.stdout


def test_default_llm_requires_together_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from signalbench.eval import __main__ as eval_main

    monkeypatch.setattr(eval_main.settings, "together_api_key", None)
    with pytest.raises(RuntimeError, match="TOGETHER_API_KEY"):
        eval_main._default_llm()


def test_main_with_injected_llm_does_not_import_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    monkeypatch.delenv("DATABASE_URL", raising=False)
    from signalbench.eval import __main__ as eval_main

    sys.modules.pop("signalbench.extraction.together_llm", None)
    sys.modules.pop("together", None)
    monkeypatch.setattr(eval_main, "_default_llm", lambda: _OracleLLM())
    assert eval_main.main() == 0
    assert "together" not in sys.modules
    assert "signalbench.extraction.together_llm" not in sys.modules
