"""User configuration: adduce.toml or the [tool.adduce] table in pyproject.toml."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib


@dataclass
class Config:
    profile: str = "default"
    ignore: frozenset[str] = frozenset()
    exclude: tuple[str, ...] = ()
    fail_under: float | None = None
    source: str | None = None


def _parse_table(table: dict, source: str) -> Config:
    return Config(
        profile=str(table.get("profile", "default")),
        ignore=frozenset(str(r) for r in table.get("ignore", [])),
        exclude=tuple(str(e) for e in table.get("exclude", [])),
        fail_under=float(table["fail-under"]) if "fail-under" in table else (
            float(table["fail_under"]) if "fail_under" in table else None
        ),
        source=source,
    )


def load_config(root: Path) -> Config:
    """Read adduce.toml if present, otherwise [tool.adduce] from pyproject.toml."""
    standalone = root / "adduce.toml"
    if standalone.is_file():
        try:
            data = tomllib.loads(standalone.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError):
            return Config()
        return _parse_table(data, "adduce.toml")
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError):
            return Config()
        table = data.get("tool", {}).get("adduce")
        if isinstance(table, dict):
            return _parse_table(table, "pyproject.toml [tool.adduce]")
    return Config()
