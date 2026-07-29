from __future__ import annotations

import os

_COLOR_ENV_VARS = ("FORCE_COLOR", "CLICOLOR_FORCE")

# adduce.cli builds module-level rich.Console singletons at import, and
# Console.__init__ caches the colour system from the environment right then.
# Strip the forcing vars before anything below can transitively import
# adduce.cli, not just per-test, so the ordering is structural, not incidental.
for _var in _COLOR_ENV_VARS:
    os.environ.pop(_var, None)

import re  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from adduce.evidence import Evidence, collect  # noqa: E402
from adduce.model import Repo, scan_repository  # noqa: E402

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


@pytest.fixture(autouse=True)
def _no_forced_color(monkeypatch):
    """Keep colour-forcing env vars out of each test's environment too."""
    for var in _COLOR_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
