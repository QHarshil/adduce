from __future__ import annotations

import re
from pathlib import Path

import pytest

from adduce.evidence import Evidence, collect
from adduce.model import Repo, scan_repository

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def plain(text: str) -> str:
    """CLI output stripped of ANSI escapes, for content assertions."""
    return _ANSI_RE.sub("", text)


def build_repo(root: Path, files: dict[str, str]) -> Repo:
    """Materialise a synthetic repository and scan it."""
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return scan_repository(root)


def build_evidence(root: Path, files: dict[str, str]) -> Evidence:
    return collect(build_repo(root, files))


@pytest.fixture
def make_evidence(tmp_path):
    def _make(files: dict[str, str]) -> Evidence:
        return build_evidence(tmp_path, files)

    return _make


@pytest.fixture
def make_repo(tmp_path):
    def _make(files: dict[str, str]) -> Repo:
        return build_repo(tmp_path, files)

    return _make
