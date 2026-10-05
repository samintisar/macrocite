"""A git runner for throwaway test repos, and New York evening timestamps."""

import subprocess
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
         "-c", "commit.gpgsign=false", *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def evening(day: date, hour: int = 18) -> datetime:
    """`hour`:00 New York time on `day`."""
    return datetime.combine(day, time(hour, 0), tzinfo=NEW_YORK)
