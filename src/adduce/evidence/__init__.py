"""Evidence collection: the single filesystem pass that rules read from.

Collectors run once per check; rules are pure functions over the resulting
:class:`Evidence` object and never touch the filesystem themselves. The
manifest, when present, rides along as the authoritative layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..content import scan_once
from ..manifest import Manifest, load_manifest
from ..model import FrameworkSet, Repo
from ..telemetry import Telemetry
from .config import ConfigEvidence, collect_config
from .data import DataEvidence, collect_data
from .dependencies import DependencyEvidence, collect_dependencies
from .docs import DocsEvidence, collect_docs
from .environment import EnvironmentEvidence, collect_environment
from .git import GitEvidence, collect_git
from .latex import LatexEvidence, collect_latex
from .notebook import NotebookEvidence, collect_notebooks
from .portability import PortabilityConsumer, PortabilityEvidence, collect_portability
from .precision import PrecisionEvidence, collect_precision
from .python_ast import PythonConsumer, PythonEvidence, collect_python
from .remote import RemoteEvidence, RemoteTextConsumer, collect_remote, complete_remote
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
    # Re-exported: each of these walks and reads on its own, which is what a
    # caller outside the shared pass wants. ``collect`` no longer calls them.
    "collect_portability",
    "collect_python",
    "collect_remote",
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


def collect(repo: Repo, *, telemetry: Telemetry | None = None) -> Evidence:
    """Run all collectors and fill in framework detection on the repo model.

    ``telemetry`` records per-collector durations when supplied. Collector
    order encodes real dependencies: the Python evidence feeds framework
    detection, data, precision, and remote; the docs evidence feeds git.
    """
    tel = Telemetry() if telemetry is None else telemetry

    # One read per file, shared by the three collectors that all want source
    # text. Run separately they opened and decoded every Python file three
    # times; the text is now handed to each of them as it is read, and released
    # before the next file, so nothing is retained to achieve it.
    python_consumer = PythonConsumer()
    remote_evidence = RemoteEvidence()
    remote_consumer = RemoteTextConsumer(remote_evidence)
    portability_consumer = PortabilityConsumer()
    with tel.stage("collect.shared_read"):
        scan_once(
            repo,
            [python_consumer, remote_consumer, portability_consumer],
            telemetry=tel,
        )
    with tel.stage("collect.python"):
        py = python_consumer.finish()
    portability = portability_consumer.evidence

    with tel.stage("collect.dependencies"):
        deps = collect_dependencies(repo)
    with tel.stage("collect.frameworks"):
        repo.frameworks = _detect_frameworks(repo, py, deps)
    with tel.stage("collect.environment"):
        env = collect_environment(repo)
    with tel.stage("collect.docs"):
        docs = collect_docs(repo)
    with tel.stage("collect.data"):
        data = collect_data(repo, python_imports=py.imports)
    with tel.stage("collect.git"):
        git = collect_git(repo, docs)
    with tel.stage("collect.config"):
        config = collect_config(repo)
    with tel.stage("collect.latex"):
        latex = collect_latex(repo)
    with tel.stage("collect.notebooks"):
        notebooks = collect_notebooks(repo)
    with tel.stage("collect.precision"):
        precision = collect_precision(py, config)
    with tel.stage("collect.remote"):
        # The text half already ran in the shared pass; this adds the
        # AST-derived references, which need the completed Python evidence.
        remote = complete_remote(py, remote_evidence)
    with tel.stage("collect.results"):
        results = collect_results(repo)
    with tel.stage("collect.run_history"):
        runs = collect_run_history(repo)
    with tel.stage("collect.manifest"):
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
