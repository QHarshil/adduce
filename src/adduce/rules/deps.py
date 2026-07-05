"""Dependencies: per-dependency hygiene beyond repo-level pinning posture.

R-ENV-001 judges the overall pinning posture; the rules here find specific
mismatches between what the code imports and what the manifests declare —
the "works on my machine because I happened to have it installed" class.
"""

from __future__ import annotations

from ..evidence import Evidence
from ..evidence.dependencies import PinLevel
from ..model import Repo
from ..naming import STDLIB_MODULES, dist_for_import
from .base import Category, Finding, Location, Rule, Status

#: Import roots that ship with the frameworks that import them (no separate declaration).
_BUNDLED_ROOTS = frozenset({"pkg_resources", "setuptools", "pip", "wheel", "distutils"})


def _declared_dists(ev: Evidence) -> set[str]:
    return {d.name.lower().replace("_", "-") for d in ev.deps.dependencies}


def _project_modules(ev: Evidence) -> set[str]:
    return {m.module_name.split(".")[0] for m in ev.py.modules if m.module_name}


class UnpinnedDependencyRule(Rule):
    id = "R-DEP-001"
    category = Category.DEPENDENCIES
    title = "Individual dependencies left floating"
    rationale = "Each floating dependency is one more way the rebuilt environment differs from the original."
    weight = 3

    def applies_to(self, repo: Repo) -> bool:
        return bool(repo.python_files())

    def evaluate(self, ev: Evidence) -> Finding:
        if not ev.deps.dependencies:
            return self.finding(Status.NOT_APPLICABLE, confidence=0.7, message="No parseable dependency entries.")
        if ev.deps.has_lockfile:
            return self.finding(Status.PASS, confidence=0.85, message="A lockfile pins the resolved environment.")
        floating = [d for d in ev.deps.dependencies if d.pin is PinLevel.UNBOUNDED]
        if not floating:
            return self.finding(Status.PASS, confidence=0.85, message="Every declared dependency carries a version constraint.")
        names = ", ".join(sorted(d.name for d in floating)[:8])
        return self.finding(
            Status.PARTIAL if len(floating) < len(ev.deps.dependencies) else Status.FAIL,
            confidence=0.85,
            message=f"{len(floating)} dependency declaration(s) have no version constraint: {names}.",
            remediation="Pin each to the version used for the reported results.",
        )


class LooseRangeRule(Rule):
    id = "R-DEP-002"
    category = Category.DEPENDENCIES
    title = "Broad version ranges on result-affecting libraries"
    rationale = (
        "A range like torch>=1.0 admits releases years apart; for numerics-bearing libraries "
        "the admitted spread is the reproducibility gap."
    )
    weight = 2

    _NUMERIC_DISTS = frozenset(
        {"torch", "tensorflow", "numpy", "scipy", "scikit-learn", "jax", "jaxlib", "transformers", "xgboost", "lightgbm", "pandas"}
    )

    def applies_to(self, repo: Repo) -> bool:
        return bool(repo.python_files())

    def evaluate(self, ev: Evidence) -> Finding:
        if not ev.deps.dependencies:
            return self.finding(Status.NOT_APPLICABLE, confidence=0.7, message="No parseable dependency entries.")
        if ev.deps.has_lockfile:
            return self.finding(Status.PASS, confidence=0.85, message="A lockfile fixes exact versions regardless of declared ranges.")
        loose = [
            d
            for d in ev.deps.dependencies
            if d.pin in (PinLevel.UNBOUNDED, PinLevel.BOUNDED)
            and d.name.lower().replace("_", "-") in self._NUMERIC_DISTS
        ]
        if not loose:
            return self.finding(Status.PASS, confidence=0.8, message="Numerics-bearing libraries are pinned exactly.")
        names = ", ".join(sorted(f"{d.name}{d.specifier}" for d in loose)[:6])
        return self.finding(
            Status.PARTIAL,
            confidence=0.8,
            message=f"Result-affecting libraries declared with ranges instead of exact versions: {names}.",
            remediation="Pin numerics-bearing libraries (torch, numpy, transformers, ...) to exact versions.",
        )


class GhostDependencyRule(Rule):
    id = "R-DEP-010"
    category = Category.DEPENDENCIES
    title = "Imported but undeclared (ghost) dependencies"
    rationale = (
        "Code that imports a package no manifest declares runs only on machines where it "
        "happens to be installed — the canonical 'works here, breaks there'."
    )
    weight = 4

    def applies_to(self, repo: Repo) -> bool:
        return bool(repo.python_files())

    def evaluate(self, ev: Evidence) -> Finding:
        if not ev.deps.declared:
            return self.finding(
                Status.NOT_APPLICABLE,
                confidence=0.7,
                message="No dependency manifest to compare imports against (see R-ENV-001).",
            )
        declared = _declared_dists(ev)
        project = _project_modules(ev)
        ghosts: dict[str, str] = {}
        for root in sorted(ev.py.imports):
            if root in STDLIB_MODULES or root in _BUNDLED_ROOTS or root in project:
                continue
            dist = dist_for_import(root)
            if dist.lower() not in declared and root.lower() not in declared:
                ghosts[root] = dist
        if not ghosts:
            return self.finding(Status.PASS, confidence=0.8, message="Every third-party import is declared in a manifest.")
        listing = ", ".join(f"{root} ({dist})" for root, dist in list(ghosts.items())[:8])
        return self.finding(
            Status.FAIL if len(ghosts) > 2 else Status.PARTIAL,
            confidence=0.75,
            message=f"{len(ghosts)} import(s) have no matching declared dependency: {listing}.",
            remediation="Declare each missing distribution in requirements.txt or pyproject.toml.",
        )


class UnusedDependencyRule(Rule):
    id = "R-DEP-011"
    category = Category.DEPENDENCIES
    title = "Declared but apparently unused dependencies"
    rationale = (
        "Unused declarations bloat the environment and slow the rebuild; heuristic, since "
        "plugins and CLI tools are used without being imported."
    )
    weight = 1

    _NEVER_FLAG = frozenset(
        {
            # Legitimately used without an import statement.
            "pytest", "pytest-cov", "ruff", "mypy", "black", "flake8", "isort", "pre-commit",
            "jupyter", "jupyterlab", "notebook", "ipykernel", "ipython", "tensorboard",
            "gunicorn", "uvicorn", "pip", "setuptools", "wheel", "build", "twine", "tox",
            "dvc", "nbconvert", "papermill", "jupytext", "sphinx",
        }
    )

    def applies_to(self, repo: Repo) -> bool:
        return bool(repo.python_files())

    def evaluate(self, ev: Evidence) -> Finding:
        if not ev.deps.dependencies:
            return self.finding(Status.NOT_APPLICABLE, confidence=0.7, message="No parseable dependency entries.")
        imported_dists = {dist_for_import(root).lower() for root in ev.py.imports}
        imported_dists |= {root.lower().replace("_", "-") for root in ev.py.imports}
        notebook_dists = {dist_for_import(root).lower() for root in ev.notebooks.all_imports}
        unused = [
            d.name
            for d in ev.deps.dependencies
            if d.name.lower() not in self._NEVER_FLAG
            and d.name.lower().replace("_", "-") not in imported_dists
            and d.name.lower().replace("_", "-") not in notebook_dists
            and not d.name.startswith(("git+", "http"))
        ]
        if not unused:
            return self.finding(Status.PASS, confidence=0.6, message="Every declared dependency appears to be imported somewhere.")
        return self.finding(
            Status.PARTIAL,
            confidence=0.4,  # deliberately low: plugins and extras are invisible to imports
            message=f"{len(unused)} declared dependenc(ies) never appear as imports: "
            + ", ".join(sorted(unused)[:8]) + " (heuristic; plugins and CLI tools are used without imports).",
            remediation="Remove genuinely unused declarations to shrink the environment reviewers must build.",
        )


class NotebookOnlyImportRule(Rule):
    id = "R-DEP-012"
    category = Category.DEPENDENCIES
    title = "Notebook imports missing from the dependency manifest"
    rationale = (
        "Notebook-only imports are the most common ghost dependencies: installed once with "
        "!pip install, never declared, gone on the reviewer's machine."
    )
    weight = 2

    def applies_to(self, repo: Repo) -> bool:
        return True

    def evaluate(self, ev: Evidence) -> Finding:
        if not ev.notebooks.present:
            return self.finding(Status.NOT_APPLICABLE, confidence=0.8, message="No notebooks in the repository.")
        if not ev.deps.declared:
            return self.finding(
                Status.NOT_APPLICABLE, confidence=0.7, message="No dependency manifest to compare against."
            )
        declared = _declared_dists(ev)
        project = _project_modules(ev)
        missing: list[str] = []
        for root in sorted(ev.notebooks.all_imports):
            if root in STDLIB_MODULES or root in project or root in _BUNDLED_ROOTS:
                continue
            dist = dist_for_import(root)
            if dist.lower() not in declared and root.lower() not in declared:
                missing.append(f"{root} ({dist})")
        if not missing:
            return self.finding(Status.PASS, confidence=0.75, message="All notebook imports are declared.")
        return self.finding(
            Status.PARTIAL,
            confidence=0.7,
            message=f"{len(missing)} notebook import(s) missing from the manifests: " + ", ".join(missing[:6]) + ".",
            remediation="Declare the notebook's dependencies alongside the project's.",
        )


class SystemDependencyRule(Rule):
    id = "R-DEP-013"
    category = Category.DEPENDENCIES
    title = "System/native dependencies used but undocumented"
    rationale = (
        "subprocess calls to external tools (ffmpeg, git, wget) fail on machines without them; "
        "the README or Dockerfile should say what to install. Heuristic."
    )
    weight = 1

    _KNOWN_TOOLS = frozenset(
        {"ffmpeg", "ffprobe", "convert", "sox", "wget", "curl", "unzip", "tar", "7z", "git",
         "dot", "latex", "pdflatex", "java", "Rscript", "blender", "melt"}
    )

    def applies_to(self, repo: Repo) -> bool:
        return bool(repo.python_files())

    def evaluate(self, ev: Evidence) -> Finding:
        invoked: dict[str, Location] = {}
        for qualname in ("subprocess.run", "subprocess.call", "subprocess.check_call", "subprocess.check_output", "subprocess.Popen", "os.system"):
            for site in ev.py.call_sites(qualname):
                if site.first_arg:
                    tool = site.first_arg.split()[0].rsplit("/", 1)[-1]
                    if tool in self._KNOWN_TOOLS:
                        invoked.setdefault(tool, Location(site.file, site.line))
        if not invoked:
            return self.finding(Status.NOT_APPLICABLE, confidence=0.6, message="No recognised external-tool invocations detected.")
        documented = ev.env.dockerfiles or ev.env.has_conda_env
        if documented:
            return self.finding(
                Status.PASS,
                confidence=0.6,
                message="External tools are invoked (" + ", ".join(sorted(invoked)) + ") and a container/conda env exists to provide them.",
            )
        return self.finding(
            Status.PARTIAL,
            confidence=0.6,
            message="External tools are invoked (" + ", ".join(sorted(invoked)) + ") but no container or conda env documents installing them.",
            remediation="Install these tools in the Dockerfile or document them in the README install section.",
            locations=list(invoked.values())[:5],
        )
