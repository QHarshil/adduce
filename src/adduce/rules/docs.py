"""Documentation: is the path from clone to reproduced result written down?"""

from __future__ import annotations

from ..evidence import Evidence
from .base import Category, Finding, Rule, Status

_REQUIRED_SECTIONS: tuple[tuple[str, str], ...] = (
    ("install", "installation"),
    ("usage", "usage / how to run"),
    ("hardware", "hardware & runtime"),
)


class ReadmeSectionsRule(Rule):
    id = "R-DOC-001"
    category = Category.DOCUMENTATION
    title = "README covers install, usage, and hardware/runtime"
    rationale = "The README is the front door; these sections are the minimum a reproducer needs."
    weight = 5
    fix_command = "adduce fix --scaffold readme"

    def evaluate(self, ev: Evidence) -> Finding:
        if not ev.docs.has_readme:
            return self.finding(
                Status.FAIL,
                confidence=0.95,
                message="No README found at the repository root.",
                remediation="Add a README.md; `adduce fix --scaffold readme` generates the skeleton.",
            )
        missing = [label for key, label in _REQUIRED_SECTIONS if not ev.docs.has_section(key)]
        if ev.docs.mentions_hardware_inline and "hardware & runtime" in missing:
            missing.remove("hardware & runtime")
        if not missing:
            return self.finding(Status.PASS, confidence=0.8, message="README covers installation, usage, and hardware/runtime.")
        if len(missing) < len(_REQUIRED_SECTIONS):
            return self.finding(
                Status.PARTIAL,
                confidence=0.75,
                message="README missing section(s): " + ", ".join(missing) + ".",
                remediation="Add the missing sections; `adduce fix --scaffold readme` appends stubs for them.",
            )
        return self.finding(
            Status.FAIL,
            confidence=0.8,
            message="README exists but none of the core sections (install, usage, hardware) were recognised.",
            remediation="Structure the README with installation, usage, and hardware/runtime sections.",
        )


class HyperparametersDocumentedRule(Rule):
    id = "R-DOC-002"
    category = Category.DOCUMENTATION
    title = "Hyperparameters recorded somewhere recoverable"
    rationale = (
        "Hyperparameters buried in code cannot be audited or swept; configs, CLI defaults, "
        "or a documented table make the exact setting recoverable."
    )
    weight = 4

    def evaluate(self, ev: Evidence) -> Finding:
        surfaces: list[str] = []
        if ev.config.files:
            surfaces.append(f"{len(ev.config.files)} config file(s)")
        if ev.config.uses_hydra:
            surfaces.append("Hydra config tree")
        if ev.py.cli_args:
            surfaces.append(f"{len(ev.py.cli_args)} CLI argument(s) with defaults")
        if ev.py.dataclass_defaults:
            surfaces.append("dataclass config defaults")
        if ev.runs.materialized:
            surfaces.append(f"{len(ev.runs.materialized)} materialised run config(s)")

        if ev.config.files or ev.runs.materialized:
            return self.finding(Status.PASS, confidence=0.75, message="Hyperparameters externalised: " + "; ".join(surfaces) + ".")
        if surfaces:
            return self.finding(
                Status.PARTIAL,
                confidence=0.65,
                message="Hyperparameters live only in CLI/dataclass defaults (" + "; ".join(surfaces) + "); "
                "defaults alone are weak evidence of what was actually run.",
                remediation="Commit the exact config used per reported result (configs/*.yaml) and reference it in the README.",
            )
        return self.finding(
            Status.FAIL,
            confidence=0.6,
            message="No config files, CLI arguments, or materialised run configs detected; hyperparameters are likely hardcoded.",
            remediation="Externalise hyperparameters into config files or CLI flags so the experimental setting is recorded.",
        )


class ExpectedResultsRule(Rule):
    id = "R-DOC-003"
    category = Category.DOCUMENTATION
    title = "Expected outputs and results stated"
    rationale = (
        "Reproduction needs a target: the numbers (and tolerance) a rerun should land on. "
        "Without them a reproducer cannot tell success from failure."
    )
    weight = 4

    def evaluate(self, ev: Evidence) -> Finding:
        if ev.manifest.claims and any(c.value is not None for c in ev.manifest.claims):
            return self.finding(
                Status.PASS,
                confidence=0.9,
                message=f"The manifest records {sum(1 for c in ev.manifest.claims if c.value is not None)} "
                "claimed value(s) with their producing artifacts.",
            )
        if not ev.docs.has_readme:
            return self.finding(Status.FAIL, confidence=0.9, message="No README to state expected results in.",
                                remediation="Add a README with an expected-results table.")
        if ev.docs.has_section("results") and ev.docs.has_results_table:
            return self.finding(Status.PASS, confidence=0.8, message="README has a results section with a results table.")
        if ev.docs.has_section("results") or ev.docs.has_results_table:
            return self.finding(
                Status.PARTIAL,
                confidence=0.7,
                message="README hints at results but lacks "
                + ("a results table." if ev.docs.has_section("results") else "a labelled results section."),
                remediation="Add an 'Expected results' table: metric, value, and the command that produces it.",
            )
        return self.finding(
            Status.FAIL,
            confidence=0.75,
            message="No expected results found in the README or manifest.",
            remediation="State the numbers a successful reproduction should obtain, with the command for each.",
        )
