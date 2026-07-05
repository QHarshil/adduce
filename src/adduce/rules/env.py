"""Environment & Tooling: can the runtime environment be rebuilt?"""

from __future__ import annotations

from ..evidence import Evidence
from ..model import Repo
from .base import Category, Finding, Rule, Status


class DependencyPinningRule(Rule):
    id = "R-ENV-001"
    category = Category.ENVIRONMENT
    title = "Dependencies declared and pinned"
    rationale = (
        "Unpinned dependencies drift: the same install command produces a different "
        "environment a month later, and with it different numbers."
    )
    weight = 5

    def applies_to(self, repo: Repo) -> bool:
        return bool(repo.python_files())

    def evaluate(self, ev: Evidence) -> Finding:
        if not ev.deps.declared:
            return self.finding(
                Status.FAIL,
                confidence=0.9,
                message="No dependency manifest found (requirements.txt, pyproject.toml, environment.yml).",
                remediation="Declare dependencies, then pin them (pip freeze, pip-compile, uv lock, poetry lock).",
            )
        if not ev.deps.dependencies and not ev.deps.has_lockfile:
            return self.finding(
                Status.UNKNOWN,
                confidence=0.5,
                message="A manifest exists but no parseable dependency entries were found "
                "(dependencies may live in setup.py, which is not statically parsed).",
                remediation="Move dependencies to pyproject.toml or requirements.txt so versions are auditable.",
            )
        fraction = ev.deps.pinned_fraction
        total = len(ev.deps.dependencies)
        if fraction >= 0.9:
            detail = "a lockfile pins the full environment" if ev.deps.has_lockfile else f"{total} dependencies pinned"
            return self.finding(Status.PASS, confidence=0.85, message=f"Dependencies are pinned: {detail}.")
        if fraction >= 0.4:
            return self.finding(
                Status.PARTIAL,
                confidence=0.85,
                message=f"Dependencies partially pinned ({fraction:.0%} of {total} entries carry exact or bounded versions).",
                remediation="Pin the remaining entries to exact versions (pip-compile, uv lock, or poetry lock).",
            )
        return self.finding(
            Status.FAIL,
            confidence=0.85,
            message=f"Dependencies are largely unpinned ({fraction:.0%} of {total} entries constrained).",
            remediation="Generate a fully pinned set with pip-compile, uv lock, or pip freeze into requirements.txt.",
        )


class LockfileRule(Rule):
    id = "R-ENV-002"
    category = Category.ENVIRONMENT
    title = "Lockfile capturing the transitive environment"
    rationale = (
        "Direct pins still leave transitive dependencies floating; a lockfile freezes "
        "the entire resolved environment."
    )
    weight = 3

    def applies_to(self, repo: Repo) -> bool:
        return bool(repo.python_files())

    def evaluate(self, ev: Evidence) -> Finding:
        if ev.deps.has_lockfile:
            return self.finding(
                Status.PASS, confidence=0.9, message="Lockfile present: " + ", ".join(ev.deps.lockfiles) + "."
            )
        if ev.deps.dependencies and ev.deps.pinned_fraction >= 0.99:
            return self.finding(
                Status.PASS,
                confidence=0.7,
                message="No lockfile, but every declared dependency is pinned exactly (freeze-style).",
            )
        return self.finding(
            Status.FAIL,
            confidence=0.85,
            message="No lockfile found (poetry.lock, uv.lock, Pipfile.lock, conda-lock).",
            remediation="Commit a lockfile: `uv lock`, `poetry lock`, or `pip-compile requirements.in`.",
        )


class ContainerRule(Rule):
    id = "R-ENV-003"
    category = Category.ENVIRONMENT
    title = "Container or reproducible environment definition"
    rationale = (
        "A Dockerfile or devcontainer captures the system layer (CUDA, native libraries) "
        "that Python manifests cannot express."
    )
    weight = 4
    fix_command = "adduce fix --scaffold docker"

    def applies_to(self, repo: Repo) -> bool:
        return bool(repo.python_files())

    def evaluate(self, ev: Evidence) -> Finding:
        if ev.env.dockerfiles:
            return self.finding(
                Status.PASS, confidence=0.9, message="Container definition found: " + ", ".join(ev.env.dockerfiles) + "."
            )
        if ev.env.has_devcontainer:
            return self.finding(Status.PASS, confidence=0.85, message="Dev container configuration found.")
        if ev.env.has_conda_env:
            return self.finding(
                Status.PARTIAL,
                confidence=0.8,
                message="A conda environment file captures the Python layer, but no container "
                "captures the system layer (CUDA, native libraries).",
                remediation="Add a Dockerfile pinning the base image; `adduce fix --scaffold docker` drafts one.",
            )
        return self.finding(
            Status.FAIL,
            confidence=0.85,
            message="No Dockerfile, devcontainer, or conda environment file found.",
            remediation="Add a Dockerfile capturing the runtime environment; `adduce fix --scaffold docker` drafts one.",
        )


class PythonVersionRule(Rule):
    id = "R-ENV-004"
    category = Category.ENVIRONMENT
    title = "Python version specified"
    rationale = "Results and even installability differ across interpreter versions."
    weight = 3

    def applies_to(self, repo: Repo) -> bool:
        return bool(repo.python_files())

    def evaluate(self, ev: Evidence) -> Finding:
        if ev.deps.python_version:
            return self.finding(
                Status.PASS,
                confidence=0.9,
                message=f"Python version constraint found in {ev.deps.python_version_source}: "
                f"{ev.deps.python_version}.",
            )
        return self.finding(
            Status.FAIL,
            confidence=0.85,
            message="No Python version recorded (requires-python, .python-version, conda env, or Dockerfile base).",
            remediation='Add `requires-python = ">=3.X,<3.Y"` to pyproject.toml or commit a .python-version file.',
        )


class SystemLayerCapturedRule(Rule):
    id = "R-ENV-005"
    category = Category.ENVIRONMENT
    title = "System toolchain (CUDA, native libraries) captured or documented"
    rationale = (
        "CUDA and cuDNN versions are rarely visible in source; the honest check is whether "
        "anything records them — a container base image, a conda env, or the manifest/README."
    )
    weight = 3

    def applies_to(self, repo: Repo) -> bool:
        # Only meaningful where a GPU toolchain is plausibly involved.
        return repo.frameworks.uses_any({"torch", "tensorflow", "jax", "lightning"})

    def evaluate(self, ev: Evidence) -> Finding:
        captures: list[str] = []
        if ev.env.dockerfiles:
            captures.append(f"container base image ({ev.env.dockerfiles[0]})")
        if ev.env.has_conda_env:
            captures.append("conda environment file")
        if ev.manifest.environment.cuda:
            captures.append(f"manifest (cuda: {ev.manifest.environment.cuda})")
        if captures:
            return self.finding(
                Status.PASS,
                confidence=0.7,
                message="System layer captured via " + " and ".join(captures) + ".",
            )
        if ev.docs.mentions_hardware_inline or ev.docs.has_section("hardware"):
            return self.finding(
                Status.PARTIAL,
                confidence=0.6,
                message="Hardware is documented in prose, but nothing machine-recoverable captures the "
                "CUDA/toolchain versions.",
                remediation="Record the CUDA version in the manifest environment block, or pin a CUDA base image in a Dockerfile.",
            )
        return self.finding(
            Status.FAIL,
            confidence=0.7,
            message="Nothing captures or documents the system toolchain (CUDA, cuDNN, native libraries).",
            remediation=(
                "Add a Dockerfile with a pinned CUDA base image, or record cuda/cudnn versions in "
                ".adduce/manifest.yaml and the README."
            ),
        )
