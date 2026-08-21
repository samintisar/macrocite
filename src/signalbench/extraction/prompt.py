from pathlib import Path

PROMPT_ROOT = Path(__file__).resolve().parents[3] / "prompts"


def load_prompt(prompt_version: str) -> str:
    path = PROMPT_ROOT / f"extract_{prompt_version}.txt"
    return path.read_text(encoding="utf-8")
