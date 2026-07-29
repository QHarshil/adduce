"""Release metadata consistency checks."""

from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

from adduce import __version__


def test_project_metadata_version_matches_runtime_version() -> None:
    project_root = Path(__file__).resolve().parents[1]
    with (project_root / "pyproject.toml").open("rb") as handle:
        metadata = tomllib.load(handle)

    assert metadata["project"]["version"] == __version__
