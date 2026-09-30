"""Git facts recorded with every run: the code version and whether the config is committed."""

import subprocess  # runs the local git binary with fixed arguments
from dataclasses import dataclass
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


@dataclass(frozen=True)
class CodeVersion:
    """HEAD, and the tracked code or data files (CODE_PATHS) that differ from it."""

    sha: str
    changed: tuple[str, ...]

    @property
    def dirty(self) -> bool:
        return bool(self.changed)


def uncommitted_code(version: CodeVersion) -> str:
    """Why a command refuses a working tree with uncommitted code or data changes."""
    return (
        f"uncommitted changes to tracked code or data ({', '.join(version.changed)}); "
        "commit them or check out a clean tag, then rerun"
    )


def _head(repo: Path) -> str:
    head = _git(repo, "rev-parse", "HEAD")
    if head.returncode != 0:
        raise RuntimeError(f"git rev-parse HEAD failed: {head.stderr.strip()}")
    return head.stdout.strip()


def code_version(repo: Path) -> CodeVersion:
    """HEAD's SHA and the tracked files under CODE_PATHS with uncommitted changes (untracked
    files are not code the run imports, so they do not count here)."""
    sha = _head(repo)
    tracked = _git(repo, "status", "--porcelain", "--untracked-files=no", "--", *CODE_PATHS)
    if tracked.returncode != 0:
        raise RuntimeError(f"git status failed: {tracked.stderr.strip()}")
    changed = tuple(line[3:] for line in tracked.stdout.splitlines() if line.strip())
    return CodeVersion(sha=sha, changed=changed)


def git_sha(repo: Path) -> str:
    """HEAD's SHA, with "-dirty" when tracked code or data files have uncommitted changes, or
    when src/, data/, or alembic/ hold untracked files that .gitignore does not exclude."""
    version = code_version(repo)
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard", "--", *CODE_FOLDERS)
    dirty = version.dirty or untracked.stdout.strip()
    return version.sha + ("-dirty" if dirty else "")


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
