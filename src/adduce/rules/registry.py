"""Rule discovery.

Built-in rules ship with the package; third-party rule packs register a
module under the ``adduce.rules`` entry-point group exposing a ``RULES``
iterable of :class:`Rule` subclasses. Installing such a package is all it
takes to add lab-specific checks — no forking.
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Iterable
from importlib.metadata import EntryPoint, entry_points

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

_UNSAFE_LABEL = re.compile(r"[^A-Za-z0-9_.:-]+")
_VALID_ENTRY_POINT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z")
_VALID_RULE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}\Z")


class RulePluginWarning(UserWarning):
    """A configured rule plugin could not be used safely."""


def safe_label(value: object, fallback: str = "unknown") -> str:
    """Return bounded printable metadata suitable for a diagnostic.

    Shared with the engine, which quotes the same kind of plugin-supplied text.
    A class name is only an identifier when it was declared as one -- built
    through ``type()`` it is arbitrary text of arbitrary length, so the
    newlines that break a Markdown table and the headings that forge one are
    stripped here rather than at each call site.
    """
    try:
        text = str(value)
    except Exception:
        return fallback
    text = _UNSAFE_LABEL.sub("?", text)[:80]
    return text or fallback


def _entry_point_field(entry_point: object, field: str) -> object:
    try:
        return getattr(entry_point, field)
    except Exception:
        return "unknown"


def _entry_point_label(entry_point: EntryPoint) -> str:
    name = safe_label(_entry_point_field(entry_point, "name"), "unnamed")
    value = safe_label(_entry_point_field(entry_point, "value"))
    return f"{name} ({value})"


def _warn_plugin(entry_point: EntryPoint, reason: str) -> None:
    warnings.warn(
        f"Skipped adduce.rules plugin {_entry_point_label(entry_point)}: {reason}.",
        RulePluginWarning,
        stacklevel=2,
    )


def _warn_discovery() -> None:
    warnings.warn(
        "Could not discover adduce.rules plugins; built-in rules remain available.",
        RulePluginWarning,
        stacklevel=2,
    )


def _entry_point_key(entry_point: EntryPoint) -> tuple[str, str, str]:
    """Return a stable load order independent of package discovery order."""
    distribution = _entry_point_field(entry_point, "dist")
    distribution_name = _entry_point_field(distribution, "name")
    return (
        safe_label(_entry_point_field(entry_point, "name")),
        safe_label(_entry_point_field(entry_point, "value")),
        safe_label(distribution_name),
    )


def _plugin_rule_classes(
    entries: Iterable[EntryPoint],
) -> list[tuple[EntryPoint, type[Rule]]]:
    """Load valid plugin rule classes while isolating each entry point."""
    classes: list[tuple[EntryPoint, type[Rule]]] = []
    try:
        ordered_entries = sorted(entries, key=_entry_point_key)
    except Exception:
        _warn_discovery()
        return classes

    for entry_point in ordered_entries:
        try:
            name = entry_point.name
            module_name = entry_point.module
        except Exception:
            _warn_plugin(entry_point, "entry-point metadata is unreadable")
            continue
        if not isinstance(name, str) or _VALID_ENTRY_POINT_NAME.fullmatch(name) is None:
            _warn_plugin(entry_point, "entry-point name is invalid")
            continue
        if module_name == "adduce.rules.builtin":
            continue

        try:
            module = entry_point.load()
        except Exception:
            _warn_plugin(entry_point, "entry-point loading failed")
            continue

        try:
            candidates = module.RULES
            iterator = iter(candidates)
        except Exception:
            _warn_plugin(entry_point, "RULES is missing or is not iterable")
            continue

        staged_classes: list[type[Rule]] = []
        found_invalid = False
        try:
            for candidate in iterator:
                if isinstance(candidate, type) and issubclass(candidate, Rule):
                    staged_classes.append(candidate)
                else:
                    found_invalid = True
        except Exception:
            _warn_plugin(entry_point, "RULES iteration failed")
            continue

        if found_invalid:
            _warn_plugin(entry_point, "RULES contains a non-Rule class")
        if not staged_classes:
            _warn_plugin(entry_point, "RULES contains no Rule subclasses")
        classes.extend((entry_point, candidate) for candidate in staged_classes)

    return classes


def discover_rules(include_plugins: bool = True) -> list[Rule]:
    """Instantiate all built-in rules plus any registered plugin rules."""
    rules = [rule_class() for rule_class in BUILTIN_RULES]
    seen = {rule.id for rule in rules if rule.id}

    if not include_plugins:
        return rules

    try:
        plugin_entries = entry_points(group="adduce.rules")
    except Exception:
        _warn_discovery()
        return rules

    for entry_point, rule_class in _plugin_rule_classes(plugin_entries):
        try:
            rule = rule_class()
            rule_id = rule.id
        except Exception:
            _warn_plugin(entry_point, "Rule construction failed")
            continue
        if not isinstance(rule, Rule):
            _warn_plugin(entry_point, "Rule construction returned an invalid object")
            continue
        if not isinstance(rule_id, str) or _VALID_RULE_ID.fullmatch(rule_id) is None:
            _warn_plugin(entry_point, "Rule id is invalid")
            continue
        if rule_id in seen:
            _warn_plugin(
                entry_point,
                f"Rule id {safe_label(rule_id)} conflicts with an existing rule",
            )
            continue
        seen.add(rule_id)
        rules.append(rule)
    return rules
