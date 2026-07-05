"""Rule discovery.

Built-in rules ship with the package; third-party rule packs register a
module under the ``adduce.rules`` entry-point group exposing a ``RULES``
iterable of :class:`Rule` subclasses. Installing such a package is all it
takes to add lab-specific checks — no forking.
"""

from __future__ import annotations

from importlib.metadata import entry_points

from .archival import ArchivableAsIsRule, ArchivalIdentifierRule, ArchivalMetadataRule
from .base import Rule
from .checkpoint import (
    OptimizerStateRule,
    ProgressStateRule,
    ProvenanceRule,
    RngStateRule,
    SchedulerStateRule,
)
from .data import (
    CommittedBinariesRule,
    DataFrictionRule,
    DataIntegrityRule,
    DataProvenanceRule,
    DownloadPathRule,
    RawProcessedRule,
)
from .deps import (
    GhostDependencyRule,
    LooseRangeRule,
    NotebookOnlyImportRule,
    SystemDependencyRule,
    UnpinnedDependencyRule,
    UnusedDependencyRule,
)
from .determinism import (
    CudnnFlagsRule,
    DataLoaderGeneratorRule,
    DataLoaderWorkerRule,
    SeedDeterminismRule,
    SklearnRandomStateRule,
    StrictDeterminismRule,
)
from .docs import ExpectedResultsRule, HyperparametersDocumentedRule, ReadmeSectionsRule
from .drift import (
    AblationTraceRule,
    AmbiguousConfigRule,
    DatasetDriftRule,
    HardwareClaimRule,
    HyperparameterDriftRule,
    MissingHyperparameterRule,
)
from .env import (
    ContainerRule,
    DependencyPinningRule,
    LockfileRule,
    PythonVersionRule,
    SystemLayerCapturedRule,
)
from .exec_ import EntrypointRule, ReproduceCommandRule, RunnerRule
from .licensing import CitationRule, LicenseRule, ThirdPartyLicensesRule
from .notebook import (
    ExecutionOrderRule,
    HiddenStateRule,
    KernelMetadataRule,
    NotebookPathsRule,
    NotebookScriptTwinRule,
    NotebookSeedRule,
    PipInstallCellRule,
    StaleOutputRule,
)
from .portability import AbsolutePathRule, LocalhostRule, PrivateDataSourceRule, SecretsRule
from .precision import (
    AmpRule,
    GpuHardwareBaselineRule,
    LowPrecisionCastRule,
    MatmulPrecisionRule,
    TF32Rule,
)
from .reconcile import (
    MaterialDifferenceRule,
    RoundingDifferenceRule,
    SingleRunRule,
    UnbackedMetricRule,
)
from .remote import (
    HFRevisionRule,
    MutableRevisionRule,
    RawUrlRule,
    RemoteResolutionRule,
    TorchHubRule,
)
from .run import ClaimCommandRule, MaterializedConfigDriftRule, SlurmRequirementsRule
from .versioning import CommitReferenceRule, GitRepositoryRule, TaggedReleaseRule

BUILTIN_RULES: tuple[type[Rule], ...] = (
    # Code & Execution
    EntrypointRule,
    RunnerRule,
    ReproduceCommandRule,
    # Environment & Tooling
    DependencyPinningRule,
    LockfileRule,
    ContainerRule,
    PythonVersionRule,
    SystemLayerCapturedRule,
    # Dependencies
    UnpinnedDependencyRule,
    LooseRangeRule,
    GhostDependencyRule,
    UnusedDependencyRule,
    NotebookOnlyImportRule,
    SystemDependencyRule,
    # Data
    DataProvenanceRule,
    DownloadPathRule,
    DataIntegrityRule,
    CommittedBinariesRule,
    DataFrictionRule,
    RawProcessedRule,
    # Documentation
    ReadmeSectionsRule,
    HyperparametersDocumentedRule,
    ExpectedResultsRule,
    # Determinism & Model
    SeedDeterminismRule,
    CudnnFlagsRule,
    StrictDeterminismRule,
    DataLoaderGeneratorRule,
    DataLoaderWorkerRule,
    SklearnRandomStateRule,
    # Numerical Precision & Hardware
    TF32Rule,
    AmpRule,
    LowPrecisionCastRule,
    MatmulPrecisionRule,
    GpuHardwareBaselineRule,
    # Paper & Artifact Consistency
    HyperparameterDriftRule,
    AmbiguousConfigRule,
    MissingHyperparameterRule,
    DatasetDriftRule,
    HardwareClaimRule,
    AblationTraceRule,
    # Result Reconciliation
    RoundingDifferenceRule,
    MaterialDifferenceRule,
    SingleRunRule,
    UnbackedMetricRule,
    # Run Traceability
    ClaimCommandRule,
    MaterializedConfigDriftRule,
    SlurmRequirementsRule,
    # Checkpoint & Experiment State
    OptimizerStateRule,
    SchedulerStateRule,
    ProgressStateRule,
    RngStateRule,
    ProvenanceRule,
    # Notebooks
    ExecutionOrderRule,
    StaleOutputRule,
    HiddenStateRule,
    PipInstallCellRule,
    NotebookPathsRule,
    NotebookSeedRule,
    KernelMetadataRule,
    NotebookScriptTwinRule,
    # Portability
    AbsolutePathRule,
    LocalhostRule,
    PrivateDataSourceRule,
    SecretsRule,
    # Remote Artifacts & Rot
    HFRevisionRule,
    MutableRevisionRule,
    TorchHubRule,
    RawUrlRule,
    RemoteResolutionRule,
    # Versioning
    GitRepositoryRule,
    TaggedReleaseRule,
    CommitReferenceRule,
    # Access & Legal
    LicenseRule,
    CitationRule,
    ThirdPartyLicensesRule,
    # Archival Readiness
    ArchivalIdentifierRule,
    ArchivableAsIsRule,
    ArchivalMetadataRule,
)


def discover_rules(include_plugins: bool = True) -> list[Rule]:
    """Instantiate all built-in rules plus any registered plugin rules."""
    classes: list[type[Rule]] = list(BUILTIN_RULES)
    if include_plugins:
        for ep in entry_points(group="adduce.rules"):
            if ep.module.startswith("adduce.rules"):
                continue  # the built-in entry point; already loaded
            try:
                module = ep.load()
            except Exception:
                continue  # a broken plugin must not break the check
            for cls in getattr(module, "RULES", []):
                if isinstance(cls, type) and issubclass(cls, Rule) and cls not in classes:
                    classes.append(cls)
    seen: set[str] = set()
    rules: list[Rule] = []
    for cls in classes:
        rule = cls()
        if rule.id and rule.id not in seen:
            seen.add(rule.id)
            rules.append(rule)
    return rules
