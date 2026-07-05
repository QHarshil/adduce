"""Environment capture and execution surfaces: containers, runners, entrypoints."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..model import Repo


@dataclass
class EnvironmentEvidence:
    dockerfiles: list[str] = field(default_factory=list)
    has_devcontainer: bool = False
    has_conda_env: bool = False
    makefile: str | None = None
    makefile_targets: list[str] = field(default_factory=list)
    run_scripts: list[str] = field(default_factory=list)
    entrypoint_files: list[str] = field(default_factory=list)
    console_scripts: bool = False
    has_ci: bool = False

    @property
    def has_container(self) -> bool:
        return bool(self.dockerfiles) or self.has_devcontainer

    @property
    def has_runner(self) -> bool:
        """A one-command way to run the project: script, Makefile target, or CLI."""
        return bool(self.run_scripts) or bool(self.makefile_targets) or self.console_scripts


_ENTRYPOINT_NAMES = frozenset(
    {"main.py", "train.py", "run.py", "cli.py", "app.py", "experiment.py", "evaluate.py", "eval.py"}
)
_RUN_SCRIPT_RE = re.compile(r"^(run|reproduce|train|launch|repro)[\w.-]*\.(sh|bash|bat|ps1)$", re.IGNORECASE)
_MAKE_TARGET_RE = re.compile(r"^([A-Za-z0-9][\w.-]*)\s*:(?!=)", re.MULTILINE)


def collect_environment(repo: Repo) -> EnvironmentEvidence:
    evidence = EnvironmentEvidence()

    for entry in repo.files:
        rel = str(entry.path)
        depth = len(entry.path.parts)
        name = entry.name
        if name == "Dockerfile" or name.startswith("Dockerfile."):
            evidence.dockerfiles.append(rel)
        elif rel.startswith(".devcontainer/") or name == ".devcontainer.json":
            evidence.has_devcontainer = True
        elif name.lower() in {"environment.yml", "environment.yaml"} and depth == 1:
            evidence.has_conda_env = True
        elif name in {"Makefile", "makefile", "justfile", "Justfile"} and depth == 1:
            evidence.makefile = rel
        elif _RUN_SCRIPT_RE.match(name) and depth <= 2:
            evidence.run_scripts.append(rel)
        elif name in _ENTRYPOINT_NAMES and depth <= 3 and entry.suffix == ".py":
            evidence.entrypoint_files.append(rel)
        elif rel.startswith((".github/workflows/", ".gitlab-ci")) or name == ".gitlab-ci.yml":
            evidence.has_ci = True

    if evidence.makefile:
        content = repo.read_text(evidence.makefile) or ""
        evidence.makefile_targets = [
            t for t in _MAKE_TARGET_RE.findall(content) if t not in {".PHONY", ".DEFAULT"}
        ]

    pyproject = repo.read_text("pyproject.toml") if repo.exists("pyproject.toml") else None
    if pyproject and re.search(r"^\[project\.scripts\]", pyproject, re.MULTILINE):
        evidence.console_scripts = True
    setup_cfg = repo.read_text("setup.cfg") if repo.exists("setup.cfg") else None
    if setup_cfg and "console_scripts" in setup_cfg:
        evidence.console_scripts = True

    return evidence
