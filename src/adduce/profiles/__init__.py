"""Scoring profiles: category weights tuned to a target venue or standard."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from ..rules.base import Category

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

_CATEGORY_KEYS = {
    "code_execution": Category.CODE_EXECUTION,
    "environment": Category.ENVIRONMENT,
    "dependencies": Category.DEPENDENCIES,
    "data": Category.DATA,
    "documentation": Category.DOCUMENTATION,
    "determinism": Category.DETERMINISM,
    "precision": Category.PRECISION,
    "drift": Category.DRIFT,
    "results": Category.RESULTS,
    "run": Category.RUN,
    "checkpoint": Category.CHECKPOINT,
    "notebook": Category.NOTEBOOK,
    "portability": Category.PORTABILITY,
    "remote": Category.REMOTE,
    "versioning": Category.VERSIONING,
    "access_legal": Category.ACCESS_LEGAL,
    "archival": Category.ARCHIVAL,
}


@dataclass
class Profile:
    name: str
    description: str = ""
    weights: dict[Category, float] = field(default_factory=dict)
    disabled_rules: frozenset[str] = frozenset()

    def category_weight(self, category: Category) -> float:
        return self.weights.get(category, 0.0)


def _from_toml(name: str, text: str) -> Profile:
    data = tomllib.loads(text)
    weights = {
        _CATEGORY_KEYS[key]: float(value)
        for key, value in data.get("weights", {}).items()
        if key in _CATEGORY_KEYS
    }
    return Profile(
        name=data.get("name", name),
        description=data.get("description", ""),
        weights=weights,
        disabled_rules=frozenset(data.get("disabled_rules", [])),
    )


def available_profiles() -> list[str]:
    names = []
    for entry in resources.files(__package__).iterdir():
        if entry.name.endswith(".toml"):
            names.append(entry.name[: -len(".toml")])
    return sorted(names)


def load_profile(name_or_path: str) -> Profile:
    """Load a bundled profile by name, or any profile from a TOML path."""
    path = Path(name_or_path)
    if path.suffix == ".toml" and path.is_file():
        return _from_toml(path.stem, path.read_text(encoding="utf-8"))
    resource = resources.files(__package__).joinpath(f"{name_or_path}.toml")
    if not resource.is_file():
        raise ValueError(
            f"Unknown profile '{name_or_path}'. Bundled profiles: {', '.join(available_profiles())}."
        )
    return _from_toml(name_or_path, resource.read_text(encoding="utf-8"))
