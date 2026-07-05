"""Configuration evidence: the hyperparameter values the repository declares.

Sources: YAML/JSON/TOML config trees (including Hydra conf/ layouts),
DeepSpeed configs, and — via the Python evidence — argparse and dataclass
defaults. Keys are flattened to dotted paths and additionally normalised
through the hyperparameter synonym map for drift comparison.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from typing import Any

import yaml

from ..model import Repo
from ..naming import canonical_hyperparameter

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

_CONFIG_DIRS = frozenset({"config", "configs", "conf", "hparams", "params", "experiments", "exp", "cfg", "cfgs"})
_SKIP_NAMES = frozenset(
    {
        "pyproject.toml",
        "environment.yml",
        "environment.yaml",
        "action.yml",
        "action.yaml",
        "mkdocs.yml",
        "mkdocs.yaml",
        ".pre-commit-config.yaml",
        ".pre-commit-hooks.yaml",
        "docker-compose.yml",
        "docker-compose.yaml",
        "dvc.yaml",
        "dvc.lock",
        "conda-lock.yml",
        "codemeta.json",
        "package.json",
        "manifest.yaml",
        "manifest.json",
        "ro-crate-metadata.json",
    }
)
_MAX_CONFIG_BYTES = 1_000_000
_NAME_HINT_RE = re.compile(r"(config|hparam|param|setting|args)", re.IGNORECASE)


@dataclass
class ConfigFile:
    path: str
    values: dict[str, Any] = field(default_factory=dict)  # dotted key -> scalar
    is_hydra: bool = False
    is_deepspeed: bool = False


@dataclass
class ConfigEvidence:
    files: list[ConfigFile] = field(default_factory=list)
    uses_hydra: bool = False

    def hyperparameters(self) -> dict[str, list[tuple[Any, str, str]]]:
        """canonical name -> [(value, file, original key), ...] across all configs."""
        found: dict[str, list[tuple[Any, str, str]]] = {}
        for config in self.files:
            for key, value in config.values.items():
                canonical = canonical_hyperparameter(key)
                if canonical and isinstance(value, (int, float, str, bool)):
                    found.setdefault(canonical, []).append((value, config.path, key))
        return found


def flatten(data: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten nested mappings to dotted keys, keeping scalars and short lists."""
    flat: dict[str, Any] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            dotted = f"{prefix}.{key}" if prefix else str(key)
            flat.update(flatten(value, dotted))
    elif isinstance(data, list):
        if len(data) <= 8 and all(isinstance(v, (int, float, str, bool)) for v in data):
            flat[prefix] = data
    elif (isinstance(data, (int, float, str, bool)) or data is None) and prefix:
        flat[prefix] = data
    return flat


def _in_config_context(parts: tuple[str, ...], name: str) -> bool:
    return any(part.lower() in _CONFIG_DIRS for part in parts[:-1]) or bool(_NAME_HINT_RE.search(name))


def _parse_any(text: str, suffix: str) -> Any | None:
    try:
        if suffix in {".yaml", ".yml"}:
            return yaml.safe_load(text)
        if suffix == ".json":
            return json.loads(text)
        if suffix == ".toml":
            return tomllib.loads(text)
    except (yaml.YAMLError, json.JSONDecodeError, tomllib.TOMLDecodeError, ValueError):
        return None
    return None


def collect_config(repo: Repo) -> ConfigEvidence:
    evidence = ConfigEvidence()
    for entry in repo.files:
        if entry.suffix not in {".yaml", ".yml", ".json", ".toml"}:
            continue
        if entry.name in _SKIP_NAMES or entry.size > _MAX_CONFIG_BYTES:
            continue
        rel = str(entry.path)
        if rel.startswith((".github/", ".adduce/", "outputs/", "multirun/", "wandb/", "mlruns/")):
            continue  # CI and run outputs are handled elsewhere
        if not _in_config_context(entry.path.parts, entry.name):
            continue
        text = repo.read_text(rel)
        if text is None:
            continue
        data = _parse_any(text, entry.suffix)
        if not isinstance(data, dict):
            continue
        is_hydra = "defaults" in data and any(p.lower() in {"conf", "config", "configs"} for p in entry.path.parts[:-1])
        is_deepspeed = entry.suffix == ".json" and (
            "zero_optimization" in data or ("fp16" in data and isinstance(data.get("fp16"), dict))
        )
        config = ConfigFile(
            path=rel,
            values=flatten(data),
            is_hydra=is_hydra,
            is_deepspeed=is_deepspeed,
        )
        evidence.files.append(config)
        evidence.uses_hydra = evidence.uses_hydra or is_hydra
    return evidence
