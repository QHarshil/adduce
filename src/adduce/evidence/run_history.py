"""Run-history evidence: the commands actually executed and the configs
actually materialised.

In ML repositories these often live outside ``configs/``: shell and SLURM
scripts, Makefile targets, Hydra output directories, and local W&B/MLflow
run metadata. A materialised Hydra output config is the most authoritative
record of what actually ran and outranks a checked-in config in drift
resolution — with the standing caveat that run-output directories are often
gitignored, so a present one may not be the run behind the paper's numbers.
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from ..model import Repo
from .config import flatten

_PY_COMMAND_RE = re.compile(
    r"^\s*(?:srun\s+|uv\s+run\s+|poetry\s+run\s+)?"
    r"(python3?\s+(?:-m\s+)?\S+.*|torchrun\s+.*|accelerate\s+launch\s+.*|deepspeed\s+.*)$"
)
_SEED_RE = re.compile(r"--seed[= ](\d+)|\bseed=(\d+)")
_CONFIG_RE = re.compile(r"--config(?:[-_](?:file|path|name))?[= ](\S+)|--cfg[= ](\S+)")
_OVERRIDE_RE = re.compile(r"(?<!-)\b([\w.]+)=([^\s'\"]+)")
_SBATCH_RE = re.compile(r"^#SBATCH\s+(\S+.*)$")


@dataclass(frozen=True)
class RunCommand:
    command: str
    file: str
    line: int
    seeds: tuple[int, ...] = ()
    config_path: str | None = None
    overrides: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class SlurmScript:
    file: str
    directives: tuple[str, ...]
    gpu_request: str | None = None


@dataclass
class MaterializedConfig:
    """A config as it existed for an actual run (Hydra outputs, W&B, MLflow)."""

    path: str
    source: str  # hydra | wandb | mlflow
    values: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunHistoryEvidence:
    commands: list[RunCommand] = field(default_factory=list)
    slurm_scripts: list[SlurmScript] = field(default_factory=list)
    materialized: list[MaterializedConfig] = field(default_factory=list)

    @property
    def any_seeded_command(self) -> bool:
        return any(c.seeds for c in self.commands)

    def hyperparameters(self) -> dict[str, list[tuple[Any, str, str]]]:
        """canonical name -> [(value, path, original key)] from materialised configs."""
        from ..naming import canonical_hyperparameter

        found: dict[str, list[tuple[Any, str, str]]] = {}
        for config in self.materialized:
            for key, value in config.values.items():
                canonical = canonical_hyperparameter(key)
                if canonical and isinstance(value, (int, float, str, bool)):
                    found.setdefault(canonical, []).append((value, config.path, key))
        return found


def _parse_command(command: str, file: str, line: int) -> RunCommand:
    seeds = tuple(
        int(a or b) for a, b in _SEED_RE.findall(command) if (a or b)
    )
    config_match = _CONFIG_RE.search(command)
    config_path = next((g for g in (config_match.groups() if config_match else ()) if g), None)
    overrides = tuple(
        (key, value)
        for key, value in _OVERRIDE_RE.findall(command)
        if "." in key or key.islower()
    )
    return RunCommand(
        command=command.strip()[:300],
        file=file,
        line=line,
        seeds=seeds,
        config_path=config_path,
        overrides=overrides,
    )


def _scan_script(repo: Repo, rel: str, evidence: RunHistoryEvidence) -> None:
    text = repo.read_text(rel)
    if text is None:
        return
    directives: list[str] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        sbatch = _SBATCH_RE.match(raw)
        if sbatch:
            directives.append(sbatch.group(1).strip())
            continue
        command = _PY_COMMAND_RE.match(raw)
        if command:
            evidence.commands.append(_parse_command(command.group(1), rel, lineno))
    if directives:
        gpu = next((d for d in directives if "gpu" in d.lower() or "gres" in d.lower()), None)
        evidence.slurm_scripts.append(
            SlurmScript(file=rel, directives=tuple(directives), gpu_request=gpu)
        )


def _load_yaml_values(repo: Repo, rel: str) -> dict[str, Any]:
    text = repo.read_text(rel)
    if text is None:
        return {}
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return {}
    if not isinstance(data, dict):
        return {}
    flat = flatten(data)
    # W&B stores {key: {value: x, desc: ...}} — unwrap the .value layer.
    unwrapped = {}
    for key, value in flat.items():
        if key.endswith(".value"):
            unwrapped[key[: -len(".value")]] = value
        elif not key.endswith(".desc"):
            unwrapped[key] = value
    return unwrapped


def collect_run_history(repo: Repo) -> RunHistoryEvidence:
    evidence = RunHistoryEvidence()

    for entry in repo.files:
        rel = str(entry.path)
        name = entry.name

        if entry.suffix in {".sh", ".bash", ".slurm", ".sbatch"} or name in {"Makefile", "makefile", "justfile"}:
            _scan_script(repo, rel, evidence)
        elif name == "config.yaml" and ".hydra" in entry.path.parts:
            values = _load_yaml_values(repo, rel)
            if values:
                evidence.materialized.append(MaterializedConfig(path=rel, source="hydra", values=values))
        elif name == "config.yaml" and any(p.startswith("run-") for p in entry.path.parts) and "wandb" in entry.path.parts:
            values = _load_yaml_values(repo, rel)
            if values:
                evidence.materialized.append(MaterializedConfig(path=rel, source="wandb", values=values))
        elif "mlruns" in entry.path.parts and "params" in entry.path.parts:
            text = repo.read_text(rel)
            if text is not None and len(text) < 1000:
                key = entry.name
                value: Any = text.strip()
                with contextlib.suppress(ValueError):
                    value = float(value) if "." in value or "e" in value.lower() else int(value)
                run_dir = "/".join(entry.path.parts[:-2])
                existing = next(
                    (m for m in evidence.materialized if m.path == run_dir and m.source == "mlflow"),
                    None,
                )
                if existing is None:
                    existing = MaterializedConfig(path=run_dir, source="mlflow")
                    evidence.materialized.append(existing)
                existing.values[key] = value

    return evidence
