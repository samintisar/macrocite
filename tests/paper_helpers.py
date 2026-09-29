"""A throwaway git repo with test configs and a paper file, shared by the paper tests."""

import subprocess
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from strategy_helpers import FIXTURE_CONFIG

NEW_YORK = ZoneInfo("America/New_York")
PAPER_FILE = "data/paper_test.yaml"
PORTFOLIOS = {  # name: (config, setup)
    "p-plain": ("data/strategy_test.yaml", "pullback"),
    "p-twin": ("data/strategy_test.yaml", "pullback"),
    "p-qqq": ("data/strategy_test-qqq.yaml", "pullback"),
}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
         "-c", "commit.gpgsign=false", *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def make_repo(root: Path) -> Path:
    """A repo with the test config, a QQQ copy of it, and a paper file, all committed."""
    data = root / "data"
    data.mkdir(parents=True)
    plain = FIXTURE_CONFIG.read_text(encoding="utf-8")
    (data / "strategy_test.yaml").write_text(plain, encoding="utf-8")
    qqq = plain.replace("version: test\n", "version: test-qqq\n")
    qqq += "cash_vehicle:\n  symbol: QQQ\n  cost_per_side: 0.002\n"
    (data / "strategy_test-qqq.yaml").write_text(qqq, encoding="utf-8")
    lines = ["portfolios:"]
    for name, (config, setup) in PORTFOLIOS.items():
        lines += [f"  - name: {name}", f"    config: {config}", f"    setup: {setup}"]
    (root / PAPER_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", "data")
    git(root, "commit", "-q", "-m", "configs")
    return root


def evening(day: date, hour: int = 18) -> datetime:
    """`hour`:00 New York time on `day`."""
    return datetime.combine(day, time(hour, 0), tzinfo=NEW_YORK)
