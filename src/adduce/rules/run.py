"""Run traceability: can each reported result be traced to a command?

Distinct from R-EXEC: those ask whether *any* run path exists; these are
claim-scoped and read the RunHistory evidence (scripts, SLURM, Hydra
outputs, W&B/MLflow). The standing caveat applies: run-output directories
are often gitignored, so a present one may not be the run behind the paper.
"""

from __future__ import annotations

from ..evidence import Evidence
from ..model import Repo
from .base import Category, Finding, Location, Rule, Status
from .drift import values_match


class ClaimCommandRule(Rule):
    id = "R-RUN-001"
    category = Category.RUN
    title = "Reported results have recoverable run commands"
    rationale = (
        "For each claim, some command must recoverably produce it — from the manifest, a "
        "script, or a documented invocation. R-EXEC-003 asks whether any command is "
        "documented; this asks per claim."
    )
    weight = 4

    def applies_to(self, repo: Repo) -> bool:
        return bool(repo.python_files())

    def evaluate(self, ev: Evidence) -> Finding:
        claims = ev.manifest.claims
        if claims:
            missing = [c.id for c in claims if not c.produced_by.command]
            if not missing:
                return self.finding(
                    Status.PASS, confidence=0.9, message=f"All {len(claims)} manifest claim(s) record a producing command."
                )
            return self.finding(
                Status.PARTIAL,
                confidence=0.85,
                message=f"{len(missing)} of {len(claims)} manifest claim(s) lack produced_by.command: "
                + ", ".join(missing[:5]) + ".",
                remediation="Fill produced_by.command for each claim in .adduce/manifest.yaml.",
            )
        # Without a manifest, judge from recoverable commands in the repo.
        if not (ev.latex.has_paper or ev.docs.has_results_table):
            return self.finding(
                Status.NOT_APPLICABLE, confidence=0.6, message="No claims (manifest, paper, or results table) to trace."
            )
        if ev.runs.commands or ev.docs.run_commands:
            return self.finding(
                Status.PARTIAL,
                confidence=0.6,
                message="Results are reported and run commands exist, but nothing maps commands to specific results.",
                remediation="Create the manifest (`adduce manifest`) and record which command produces each claim.",
            )
        return self.finding(
            Status.FAIL,
            confidence=0.7,
            message="Results are reported but no run command is recoverable from scripts, README, or a manifest.",
            remediation="Script the runs and record per-claim commands in the manifest.",
        )


class MaterializedConfigDriftRule(Rule):
    id = "R-RUN-002"
    category = Category.RUN
    title = "Materialised run config disagrees with checked-in configs"
    rationale = (
        "The Hydra output (or W&B/MLflow record) is what actually ran. When it disagrees with "
        "the committed config, the committed config is the stale one."
    )
    weight = 3

    def applies_to(self, repo: Repo) -> bool:
        return bool(repo.python_files())

    def evaluate(self, ev: Evidence) -> Finding:
        if not ev.runs.materialized:
            return self.finding(
                Status.NOT_APPLICABLE,
                confidence=0.7,
                message="No materialised run configs found (Hydra outputs, W&B, MLflow); they are often gitignored.",
            )
        run_values = ev.runs.hyperparameters()
        config_values = ev.config.hyperparameters()
        disagreements: list[str] = []
        for name, run_entries in run_values.items():
            static_entries = config_values.get(name)
            if not static_entries:
                continue
            for run_value, run_path, _ in run_entries:
                if not isinstance(run_value, (int, float)) or isinstance(run_value, bool):
                    continue
                statics = [v for v, _, _ in static_entries if isinstance(v, (int, float)) and not isinstance(v, bool)]
                if statics and not any(values_match(float(run_value), float(s)) for s in statics):
                    disagreements.append(f"{name}: ran with {run_value} ({run_path}), configs say {statics[0]:g}")
                    break
        if not disagreements:
            return self.finding(
                Status.PASS,
                confidence=0.65,
                message=f"{len(ev.runs.materialized)} materialised run config(s) agree with the checked-in configs "
                "(caveat: a present run directory may not be the paper's run).",
            )
        return self.finding(
            Status.PARTIAL,
            confidence=0.6,
            message="Materialised run configs disagree with checked-in configs: " + "; ".join(disagreements[:3]) + ".",
            remediation="The materialised config is authoritative for what ran; update the committed configs or state which run backs the paper.",
        )


class SlurmRequirementsRule(Rule):
    id = "R-RUN-003"
    category = Category.RUN
    title = "Batch-script resource requests undocumented for readers"
    rationale = (
        "SLURM directives encode the real hardware requirements (GPUs, memory, walltime); "
        "when only the batch script knows them, README readers plan with wrong expectations."
    )
    weight = 2

    def applies_to(self, repo: Repo) -> bool:
        return bool(repo.python_files())

    def evaluate(self, ev: Evidence) -> Finding:
        if not ev.runs.slurm_scripts:
            return self.finding(Status.NOT_APPLICABLE, confidence=0.7, message="No SLURM batch scripts detected.")
        gpu_scripts = [s for s in ev.runs.slurm_scripts if s.gpu_request]
        documented = (
            ev.docs.has_section("hardware")
            or ev.docs.mentions_hardware_inline
            or bool(ev.manifest.environment.hardware)
        )
        if documented:
            return self.finding(
                Status.PASS,
                confidence=0.7,
                message=f"{len(ev.runs.slurm_scripts)} batch script(s) present and hardware requirements are documented.",
            )
        detail = f" (e.g. {gpu_scripts[0].gpu_request} in {gpu_scripts[0].file})" if gpu_scripts else ""
        return self.finding(
            Status.PARTIAL,
            confidence=0.7,
            message=f"Batch scripts encode resource requests{detail}, but the README/manifest document no hardware requirements.",
            remediation="Mirror the batch scripts' resource requests (GPUs, memory, walltime) in the README hardware section.",
            locations=[Location(s.file) for s in ev.runs.slurm_scripts[:3]],
        )
