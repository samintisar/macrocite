from pathlib import Path

BOTBOX = Path(__file__).resolve().parents[1] / "scripts" / "botbox"


def test_the_paper_timer_runs_three_times_a_day_in_utc() -> None:
    text = (BOTBOX / "signalbench-paper.timer").read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* 00,06,12:30:00 UTC" in text
    assert "Persistent=true" in text


def test_the_paper_service_runs_from_the_paper_checkout() -> None:
    text = (BOTBOX / "signalbench-paper.service").read_text(encoding="utf-8")
    assert "WorkingDirectory=%h/paper/macrocite" in text
    assert "EnvironmentFile=%h/paper/config/paper.env" in text
    assert "ExecStart=%h/.local/bin/uv run --frozen signalbench crypto paper run" in text


def test_the_backup_dumps_only_the_paper_database() -> None:
    text = (BOTBOX / "paper_backup.sh").read_text(encoding="utf-8")
    assert "-d signalbench_paper" in text
    assert "-mtime +14 -delete" in text


def test_the_env_example_points_at_the_paper_database() -> None:
    text = (BOTBOX / "paper.env.example").read_text(encoding="utf-8")
    assert "/signalbench_paper" in text
