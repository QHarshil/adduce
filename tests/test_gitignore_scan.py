"""Honouring .gitignore during the walk.

A gitignored tree is not part of the artifact under review. Scanning it costs
time and, worse, lets one repository earn a passing status from another
repository's files. These tests pin the exclusion, its fallbacks, and the
guarantee that ambient git configuration cannot change what an audit examines.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from adduce.model import scan_repository

_GIT_AVAILABLE = True
try:
    subprocess.run(["git", "--version"], capture_output=True, check=True)
except (OSError, subprocess.CalledProcessError):  # pragma: no cover - git is a hard dependency
    _GIT_AVAILABLE = False

requires_git = pytest.mark.skipif(not _GIT_AVAILABLE, reason="git is not available")


def _git(root: Path, *args: str) -> None:
    """Run git hermetically, so ambient configuration cannot steer a fixture."""
    subprocess.run(
        ["git", "-c", "init.defaultBranch=main", "-C", str(root), *args],
        capture_output=True,
        check=True,
        env={
            # git on Windows needs its own directory and the system root on
            # PATH, so the ambient value is passed through rather than invented.
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        },
    )


def _write(root: Path, relative: str, content: str) -> None:
    """Write bytes, not text: text mode rewrites newlines on Windows."""
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content.encode("utf-8"))


def _paths(root: Path, *, honor: bool) -> set[str]:
    return {str(entry.path) for entry in scan_repository(root, honor_gitignore=honor).files}


@requires_git
def test_gitignored_file_is_scanned_by_default_and_skipped_when_honouring(tmp_path: Path) -> None:
    _write(tmp_path, ".gitignore", "secret.py\n")
    _write(tmp_path, "secret.py", "x = 1\n")
    _write(tmp_path, "kept.py", "y = 2\n")
    _git(tmp_path, "init")

    assert "secret.py" in _paths(tmp_path, honor=False)
    honoured = _paths(tmp_path, honor=True)
    assert "secret.py" not in honoured
    assert {"kept.py", ".gitignore"} <= honoured


@requires_git
def test_a_wholly_ignored_directory_is_excluded(tmp_path: Path) -> None:
    _write(tmp_path, ".gitignore", "data/\n")
    for index in range(5):
        _write(tmp_path, f"data/shard{index}.csv", "a,b\n1,2\n")
    _write(tmp_path, "train.py", "import torch\n")
    _git(tmp_path, "init")

    assert len([p for p in _paths(tmp_path, honor=False) if p.startswith("data/")]) == 5
    assert not [p for p in _paths(tmp_path, honor=True) if p.startswith("data/")]


@requires_git
def test_nested_gitignore_and_negation_follow_git(tmp_path: Path) -> None:
    _write(tmp_path, ".gitignore", "*.log\n")
    _write(tmp_path, "sub/.gitignore", "!keep.log\nlocal.txt\n")
    _write(tmp_path, "sub/keep.log", "kept\n")
    _write(tmp_path, "sub/drop.log", "dropped\n")
    _write(tmp_path, "sub/local.txt", "dropped\n")
    _write(tmp_path, "top.log", "dropped\n")
    _git(tmp_path, "init")

    honoured = _paths(tmp_path, honor=True)
    assert "sub/keep.log" in honoured
    assert "sub/drop.log" not in honoured
    assert "sub/local.txt" not in honoured
    assert "top.log" not in honoured


@requires_git
def test_a_tracked_file_is_kept_even_when_a_pattern_would_ignore_it(tmp_path: Path) -> None:
    """Tracking beats ignoring, exactly as git reports it."""
    _write(tmp_path, ".gitignore", "*.py\n")
    _write(tmp_path, "tracked.py", "x = 1\n")
    _write(tmp_path, "untracked.py", "y = 2\n")
    _git(tmp_path, "init")
    _git(tmp_path, "add", "-f", "tracked.py")

    honoured = _paths(tmp_path, honor=True)
    assert "tracked.py" in honoured
    assert "untracked.py" not in honoured


def test_a_directory_that_is_not_a_repository_scans_everything(tmp_path: Path) -> None:
    """No git answer means scan more, never silently scan less."""
    _write(tmp_path, ".gitignore", "ignored.py\n")
    _write(tmp_path, "ignored.py", "x = 1\n")
    assert _paths(tmp_path, honor=True) == _paths(tmp_path, honor=False)


@requires_git
def test_symlinks_are_still_refused_while_honouring_gitignore(tmp_path: Path) -> None:
    _write(tmp_path, ".gitignore", "ignored.py\n")
    _write(tmp_path, "real.py", "x = 1\n")
    _write(tmp_path, "ignored.py", "y = 2\n")
    try:
        (tmp_path / "link.py").symlink_to(tmp_path / "real.py")
    except (OSError, NotImplementedError):  # pragma: no cover - unprivileged Windows
        pytest.skip("symlink creation is not permitted here")
    _git(tmp_path, "init")

    honoured = _paths(tmp_path, honor=True)
    assert "real.py" in honoured
    assert "link.py" not in honoured
    assert "ignored.py" not in honoured


@requires_git
def test_ambient_global_git_config_cannot_change_what_is_scanned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A user's core.excludesFile must not silently shrink an audit."""
    excludes = tmp_path / "global-excludes"
    excludes.write_bytes(b"*.py\n")
    global_config = tmp_path / "global-config"
    # Forward slashes: a backslash is an escape character in git config.
    global_config.write_bytes(
        f"[core]\n\texcludesFile = {excludes.as_posix()}\n".encode()
    )

    repository = tmp_path / "repository"
    repository.mkdir()
    _write(repository, "train.py", "import torch\n")
    _git(repository, "init")

    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    assert "train.py" in _paths(repository, honor=True)


@requires_git
def test_scanning_a_gitignored_directory_still_finds_its_files(tmp_path: Path) -> None:
    """Every path below an ignored root is ignored; filtering would empty it.

    Auditing a vendored or otherwise gitignored working copy is legitimate, and
    reporting a clean repository containing no files would be the worst
    possible answer.
    """
    _write(tmp_path, ".gitignore", "vendor/\n")
    _write(tmp_path, "vendor/project/train.py", "import torch\n")
    _write(tmp_path, "vendor/project/README.md", "# vendored\n")
    _git(tmp_path, "init")

    inner = tmp_path / "vendor" / "project"
    honoured = _paths(inner, honor=True)
    assert honoured == _paths(inner, honor=False)
    assert {"train.py", "README.md"} <= honoured


@requires_git
def test_honouring_gitignore_only_removes_paths(tmp_path: Path) -> None:
    _write(tmp_path, ".gitignore", "build/\n*.log\n")
    _write(tmp_path, "train.py", "import torch\n")
    _write(tmp_path, "build/out.bin", "\x00\x01")
    _write(tmp_path, "run.log", "noise\n")
    _git(tmp_path, "init")

    assert _paths(tmp_path, honor=True) < _paths(tmp_path, honor=False)
