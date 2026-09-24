"""Git facts recorded with every run: the code version and whether the config is committed."""

import subprocess  # runs the local git binary with fixed arguments
from pathlib import Path

CODE_PATHS = ("src", "data", "alembic", "pyproject.toml", "uv.lock")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def git_sha(repo: Path) -> str:
    """HEAD's SHA, with "-dirty" when tracked code or data files have uncommitted changes."""
    head = _git(repo, "rev-parse", "HEAD")
    if head.returncode != 0:
        raise RuntimeError(f"git rev-parse HEAD failed: {head.stderr.strip()}")
    status = _git(repo, "status", "--porcelain", "--untracked-files=no", "--", *CODE_PATHS)
    return head.stdout.strip() + ("-dirty" if status.stdout.strip() else "")


def committed_unchanged(repo: Path, path: Path) -> bool:
    """True when `path` is tracked and identical to HEAD (pre-registration check)."""
    relative = str(path.resolve().relative_to(repo.resolve()))
    tracked = _git(repo, "ls-files", "--error-unmatch", "--", relative)
    if tracked.returncode != 0:
        return False
    return _git(repo, "diff", "--quiet", "HEAD", "--", relative).returncode == 0
