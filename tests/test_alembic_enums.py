from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_phase0_enum_create_is_idempotent() -> None:
    text = (REPO / "alembic" / "versions" / "0001_phase0.py").read_text(encoding="utf-8")
    assert "create_type=False" in text
    assert "checkfirst=True" in text


def test_signals_enum_create_is_idempotent() -> None:
    text = (REPO / "alembic" / "versions" / "0002_signals.py").read_text(encoding="utf-8")
    assert "create_type=False" in text
    assert "checkfirst=True" in text
