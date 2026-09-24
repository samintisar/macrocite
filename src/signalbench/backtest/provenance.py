"""Git facts recorded with every run: the code version and whether the config is committed."""

import subprocess  # runs the local git binary with fixed arguments
from pathlib import Path

CODE_PATHS = ("src", "data", "alembic", "pyproject.toml", "uv.lock")
CODE_FOLDERS = ("src", "data", "alembic")  # new (untracked, not ignored) files here count too


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def git_sha(repo: Path) -> str:
    """HEAD's SHA, with "-dirty" when tracked code or data files have uncommitted changes, or
    when src/, data/, or alembic/ hold untracked files that .gitignore does not exclude."""
    head = _git(repo, "rev-parse", "HEAD")
    if head.returncode != 0:
        raise RuntimeError(f"git rev-parse HEAD failed: {head.stderr.strip()}")
    tracked = _git(repo, "status", "--porcelain", "--untracked-files=no", "--", *CODE_PATHS)
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard", "--", *CODE_FOLDERS)
    dirty = tracked.stdout.strip() or untracked.stdout.strip()
    return head.stdout.strip() + ("-dirty" if dirty else "")


def committed_unchanged(repo: Path, path: Path) -> bool:
    """True when `path` is tracked and identical to HEAD (pre-registration check).

    A path outside `repo` is never committed there, so it is False.
    """
    try:
        relative = path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return False
    tracked = _git(repo, "ls-files", "--error-unmatch", "--", relative)
    if tracked.returncode != 0:
        return False
    return _git(repo, "diff", "--quiet", "HEAD", "--", relative).returncode == 0
