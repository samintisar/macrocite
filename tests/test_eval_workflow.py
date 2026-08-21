from pathlib import Path

import pytest
from typer.testing import CliRunner

from signalbench.cli import app

REPO = Path(__file__).resolve().parents[1]


def test_workflow_path_filters() -> None:
    text = (REPO / ".github" / "workflows" / "eval.yml").read_text(encoding="utf-8")
    assert "prompts/**" in text
    assert "uv run python -m signalbench.eval" in text


def test_main_returns_zero_on_fixture_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from signalbench.eval.__main__ import main

    assert main() == 0


def test_main_returns_one_when_gate_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from signalbench.eval import __main__ as eval_main

    monkeypatch.setattr(eval_main, "passed_ci_gate", lambda *args, **kwargs: False)
    assert eval_main.main() == 1


def test_main_skips_persist_when_database_url_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from signalbench.eval import __main__ as eval_main

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("must not persist without DATABASE_URL")

    monkeypatch.setattr(eval_main, "record_eval_run", boom)
    monkeypatch.setattr(eval_main, "get_session", boom)
    assert eval_main.main() == 0


def test_cli_help_lists_eval() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "eval" in result.stdout
