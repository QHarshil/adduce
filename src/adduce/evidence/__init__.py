"""Evidence collection: the single filesystem pass that rules read from.

Collectors run once per check; rules are pure functions over the resulting
:class:`Evidence` object and never touch the filesystem themselves. The
manifest, when present, rides along as the authoritative layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..manifest import Manifest, load_manifest
from ..model import FrameworkSet, Repo
from .config import ConfigEvidence, collect_config
from .data import DataEvidence, collect_data
from .dependencies import DependencyEvidence, collect_dependencies
from .docs import DocsEvidence, collect_docs
from .environment import EnvironmentEvidence, collect_environment
from .git import GitEvidence, collect_git
from .latex import LatexEvidence, collect_latex
from .notebook import NotebookEvidence, collect_notebooks
from .portability import PortabilityEvidence, collect_portability
from .precision import PrecisionEvidence, collect_precision
from .python_ast import PythonEvidence, collect_python
from .remote import RemoteEvidence, collect_remote
from .results import ResultsEvidence, collect_results
from .run_history import RunHistoryEvidence, collect_run_history

__all__ = [
    "Evidence",
    "collect",
    "PythonEvidence",
    "DependencyEvidence",
    "EnvironmentEvidence",
    "DocsEvidence",
    "DataEvidence",
    "GitEvidence",
    "ConfigEvidence",
    "LatexEvidence",
    "NotebookEvidence",
    "PortabilityEvidence",
    "PrecisionEvidence",
    "RemoteEvidence",
    "ResultsEvidence",
    "RunHistoryEvidence",
]


@dataclass
class Evidence:
    repo: Repo
    py: PythonEvidence
    deps: DependencyEvidence
    env: EnvironmentEvidence
    docs: DocsEvidence
    data: DataEvidence
    git: GitEvidence
    config: ConfigEvidence = field(default_factory=ConfigEvidence)
    latex: LatexEvidence = field(default_factory=LatexEvidence)
    notebooks: NotebookEvidence = field(default_factory=NotebookEvidence)
    portability: PortabilityEvidence = field(default_factory=PortabilityEvidence)
    precision: PrecisionEvidence = field(default_factory=PrecisionEvidence)
    remote: RemoteEvidence = field(default_factory=RemoteEvidence)
    results: ResultsEvidence = field(default_factory=ResultsEvidence)
    runs: RunHistoryEvidence = field(default_factory=RunHistoryEvidence)
    manifest: Manifest = field(default_factory=Manifest)


def _detect_frameworks(repo: Repo, py: PythonEvidence, deps: DependencyEvidence) -> FrameworkSet:
    frameworks = FrameworkSet()
    for module_root in py.imports:
        if framework := FrameworkSet.framework_for_import(module_root):
            frameworks.detected.add(framework)
    for dep in deps.dependencies:
        if framework := FrameworkSet.framework_for_dist(dep.name):
            frameworks.detected.add(framework)
    if repo.python_files():
        frameworks.detected.add("python")
    return frameworks


def collect(repo: Repo) -> Evidence:
    """Run all collectors and fill in framework detection on the repo model."""
    py = collect_python(repo)
    deps = collect_dependencies(repo)
    repo.frameworks = _detect_frameworks(repo, py, deps)
    env = collect_environment(repo)
    docs = collect_docs(repo)
    data = collect_data(repo, python_imports=py.imports)
    git = collect_git(repo, docs)
    config = collect_config(repo)
    latex = collect_latex(repo)
    notebooks = collect_notebooks(repo)
    portability = collect_portability(repo)
    precision = collect_precision(py, config)
    remote = collect_remote(repo, py, has_checksums=data.has_integrity_checks)
    results = collect_results(repo)
    runs = collect_run_history(repo)
    manifest = load_manifest(repo.root)
    return Evidence(
        repo=repo,
        py=py,
        deps=deps,
        env=env,
        docs=docs,
        data=data,
        git=git,
        config=config,
        latex=latex,
        notebooks=notebooks,
        portability=portability,
        precision=precision,
        remote=remote,
        results=results,
        runs=runs,
        manifest=manifest,
    )
