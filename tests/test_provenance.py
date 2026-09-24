import subprocess
from pathlib import Path

import pytest

from signalbench.backtest.provenance import committed_unchanged, git_sha


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
         "-c", "commit.gpgsign=false", *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "strategy_v1.yaml").write_text("version: v1\n", encoding="utf-8")
    _git(tmp_path, "add", "data/strategy_v1.yaml")
    _git(tmp_path, "commit", "-q", "-m", "config")
    return tmp_path


def test_git_sha_is_head_and_marks_dirty_code(repo: Path) -> None:
    head = _git(repo, "rev-parse", "HEAD")
    assert git_sha(repo) == head
    (repo / "notes.txt").write_text("untracked files do not count\n", encoding="utf-8")
    assert git_sha(repo) == head
    (repo / "data" / "strategy_v1.yaml").write_text("version: v2\n", encoding="utf-8")
    assert git_sha(repo) == f"{head}-dirty"


def test_committed_unchanged(repo: Path) -> None:
    config = repo / "data" / "strategy_v1.yaml"
    assert committed_unchanged(repo, config) is True
    config.write_text("version: v1\ncost_per_side: 0.003\n", encoding="utf-8")
    assert committed_unchanged(repo, config) is False
    untracked = repo / "data" / "strategy_v2.yaml"
    untracked.write_text("version: v2\n", encoding="utf-8")
    assert committed_unchanged(repo, untracked) is False


def test_git_sha_outside_a_repo_raises(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="rev-parse"):
        git_sha(tmp_path)


@pytest.mark.parametrize("folder", ["src", "data", "alembic"])
def test_untracked_files_under_code_folders_mark_dirty(repo: Path, folder: str) -> None:
    head = _git(repo, "rev-parse", "HEAD")
    (repo / folder / "new").mkdir(parents=True, exist_ok=True)
    (repo / folder / "new" / "module.py").write_text("x = 1\n", encoding="utf-8")
    assert git_sha(repo) == f"{head}-dirty"


def test_ignored_files_under_code_folders_do_not_mark_dirty(repo: Path) -> None:
    (repo / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "ignore")
    head = _git(repo, "rev-parse", "HEAD")
    (repo / "src" / "__pycache__").mkdir(parents=True)
    (repo / "src" / "__pycache__" / "cli.pyc").write_bytes(b"\x00")
    assert git_sha(repo) == head


def test_committed_unchanged_is_false_outside_the_repo(
    repo: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = tmp_path_factory.mktemp("elsewhere") / "strategy_v1.yaml"
    outside.write_text("version: v1\n", encoding="utf-8")
    assert committed_unchanged(repo, outside) is False

