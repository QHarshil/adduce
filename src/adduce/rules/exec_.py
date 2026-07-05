"""Code & Execution: can someone actually run this repository?"""

from __future__ import annotations

from ..evidence import Evidence
from ..model import Repo
from .base import Category, Finding, Location, Rule, Status


class EntrypointRule(Rule):
    id = "R-EXEC-001"
    category = Category.CODE_EXECUTION
    title = "Discoverable entrypoint"
    rationale = (
        "Without an obvious entrypoint, reproduction starts with reverse-engineering "
        "which of the scripts is the one that produced the results."
    )
    weight = 5

    def applies_to(self, repo: Repo) -> bool:
        return bool(repo.python_files())

    def evaluate(self, ev: Evidence) -> Finding:
        signals: list[str] = []
        if ev.env.entrypoint_files:
            signals.append(f"conventional entrypoint file(s): {', '.join(sorted(ev.env.entrypoint_files)[:3])}")
        if ev.env.console_scripts:
            signals.append("console script declared in packaging metadata")
        main_guards = ev.py.main_guard_files

        if ev.env.entrypoint_files or ev.env.console_scripts:
            return self.finding(Status.PASS, confidence=0.85, message="Entrypoint detected: " + "; ".join(signals) + ".")
        if main_guards:
            return self.finding(
                Status.PARTIAL,
                confidence=0.7,
                message=f"No conventional entrypoint (main.py/train.py/CLI), but {len(main_guards)} "
                "file(s) have a __main__ guard.",
                remediation="Name the primary entrypoint conventionally (train.py, main.py) or declare a console script.",
                locations=[Location(p) for p in main_guards[:5]],
            )
        return self.finding(
            Status.FAIL,
            confidence=0.8,
            message="No entrypoint detected: no main.py/train.py/run.py, no console script, no __main__ guard.",
            remediation="Add a clearly named entrypoint script and document the exact command that reproduces the results.",
        )


class RunnerRule(Rule):
    id = "R-EXEC-002"
    category = Category.CODE_EXECUTION
    title = "One-command execution path"
    rationale = (
        "A run.sh, Makefile target, or documented command removes the guesswork between "
        "cloning and reproducing."
    )
    weight = 4
    fix_command = "adduce fix --scaffold runner"

    def applies_to(self, repo: Repo) -> bool:
        return bool(repo.python_files())

    def evaluate(self, ev: Evidence) -> Finding:
        surfaces: list[str] = []
        if ev.env.run_scripts:
            surfaces.append(f"run script(s): {', '.join(sorted(ev.env.run_scripts)[:3])}")
        if ev.env.makefile_targets:
            surfaces.append(f"Makefile with {len(ev.env.makefile_targets)} target(s)")
        if ev.docs.run_commands:
            surfaces.append("run command(s) documented in the README")

        if ev.env.run_scripts or ev.env.makefile_targets:
            return self.finding(Status.PASS, confidence=0.85, message="One-command execution path found: " + "; ".join(surfaces) + ".")
        if surfaces:
            return self.finding(
                Status.PARTIAL,
                confidence=0.75,
                message="Commands are documented, but there is no run script or Makefile target to execute them.",
                remediation="Wrap the documented commands in a run.sh or `make reproduce` target.",
            )
        return self.finding(
            Status.FAIL,
            confidence=0.8,
            message="No run script, Makefile, or documented run command found.",
            remediation="Add a run.sh or Makefile `reproduce` target; `adduce fix --scaffold runner` drafts one.",
        )


class ReproduceCommandRule(Rule):
    id = "R-EXEC-003"
    category = Category.CODE_EXECUTION
    title = "Exact reproduce command recorded"
    rationale = (
        "Distinct from having *a* runner: the specific command that regenerates the reported "
        "results must be written down, in the README or the manifest, or reproduction starts "
        "with guessing flags."
    )
    weight = 3

    def applies_to(self, repo: Repo) -> bool:
        return bool(repo.python_files())

    def evaluate(self, ev: Evidence) -> Finding:
        manifest_commands = [
            claim.produced_by.command
            for claim in ev.manifest.claims
            if claim.produced_by.command
        ]
        if ev.manifest.exists and manifest_commands:
            return self.finding(
                Status.PASS,
                confidence=0.9,
                message=f"The manifest records the producing command for {len(manifest_commands)} claim(s).",
            )
        if ev.docs.run_commands:
            return self.finding(
                Status.PARTIAL if not ev.manifest.exists else Status.PARTIAL,
                confidence=0.7,
                message="The README shows run command(s) (e.g. "
                f"`{ev.docs.run_commands[0][:60]}`), but they are not tied to specific reported results.",
                remediation=(
                    "Record per-claim commands in .adduce/manifest.yaml (`adduce manifest` scaffolds it), or state "
                    "in the README which command produces which table/figure."
                ),
            )
        if ev.runs.commands:
            return self.finding(
                Status.PARTIAL,
                confidence=0.65,
                message=f"Run commands exist in scripts ({ev.runs.commands[0].file}), but neither the README nor a "
                "manifest says which one reproduces the reported results.",
                remediation="Document the exact reproduce command in the README or the manifest.",
            )
        return self.finding(
            Status.FAIL,
            confidence=0.8,
            message="No reproduce command recorded anywhere (README, manifest, or scripts).",
            remediation="Write the exact command that regenerates the main results into the README and the manifest.",
        )
