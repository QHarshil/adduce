"""Notebook hygiene: execution order, hidden state, environment capture.

Notebooks are where reproducibility goes to die quietly: out-of-order
execution, state from deleted cells, ``!pip install`` environments that
exist nowhere else. All heuristics; confidence stays moderate.
"""

from __future__ import annotations

from ..evidence import Evidence
from ..model import Repo
from .base import Category, Finding, Location, Rule, Status


class _NotebookBase(Rule):
    def applies_to(self, repo: Repo) -> bool:
        return any(f.suffix == ".ipynb" for f in repo.files)


class ExecutionOrderRule(_NotebookBase):
    id = "R-NB-001"
    category = Category.NOTEBOOK
    title = "Notebooks executed in linear order"
    rationale = (
        "Non-monotonic execution counts mean the saved outputs were produced in a different "
        "order than the code reads — rerunning top-to-bottom may not reproduce them."
    )
    weight = 3

    def evaluate(self, ev: Evidence) -> Finding:
        executed = [n for n in ev.notebooks.notebooks if n.execution_counts]
        if not executed:
            return self.finding(
                Status.NOT_APPLICABLE, confidence=0.7, message="No executed notebooks (no execution counts saved)."
            )
        disordered = [n for n in executed if not n.monotonic]
        if not disordered:
            return self.finding(
                Status.PASS, confidence=0.8, message=f"All {len(executed)} executed notebook(s) ran top-to-bottom."
            )
        return self.finding(
            Status.PARTIAL,
            confidence=0.8,
            message=f"{len(disordered)} notebook(s) show non-linear execution order.",
            remediation="Restart the kernel and 'Run All' before committing, so saved outputs match a clean run.",
            locations=[Location(n.path) for n in disordered[:5]],
        )


class StaleOutputRule(_NotebookBase):
    id = "R-NB-002"
    category = Category.NOTEBOOK
    title = "Committed outputs likely stale relative to the code"
    rationale = "Outputs from a different execution order are results the current code may not produce."
    weight = 2

    def evaluate(self, ev: Evidence) -> Finding:
        with_outputs = [n for n in ev.notebooks.notebooks if n.has_outputs]
        if not with_outputs:
            return self.finding(Status.NOT_APPLICABLE, confidence=0.7, message="No notebooks with committed outputs.")
        suspect = [n for n in with_outputs if not n.monotonic or n.has_gaps]
        if not suspect:
            return self.finding(
                Status.PASS,
                confidence=0.6,
                message="Committed outputs are consistent with a clean top-to-bottom run (heuristic).",
            )
        return self.finding(
            Status.PARTIAL,
            confidence=0.55,
            message=f"{len(suspect)} notebook(s) have outputs but disordered/gapped execution counts — "
            "the outputs may not correspond to the committed code.",
            remediation="Re-execute with 'Restart & Run All' and commit the clean state.",
            locations=[Location(n.path) for n in suspect[:5]],
        )


class HiddenStateRule(_NotebookBase):
    id = "R-NB-003"
    category = Category.NOTEBOOK
    title = "Hidden-state risk (gaps in execution counts)"
    rationale = (
        "Execution-count gaps can indicate out-of-order execution, deleted cells, or other "
        "hidden kernel state; they are a review signal rather than proof of one cause."
    )
    weight = 2

    def evaluate(self, ev: Evidence) -> Finding:
        executed = [n for n in ev.notebooks.notebooks if n.execution_counts]
        if not executed:
            return self.finding(Status.NOT_APPLICABLE, confidence=0.7, message="No executed notebooks.")
        gapped = [n for n in executed if n.has_gaps]
        if not gapped:
            return self.finding(Status.PASS, confidence=0.7, message="Execution counts are contiguous; low hidden-state risk.")
        return self.finding(
            Status.PARTIAL,
            confidence=0.6,
            message=f"{len(gapped)} notebook(s) have gaps in execution counts; committed outputs may depend on hidden state.",
            remediation="Restart the kernel and 'Run All' so committed state is self-contained.",
            locations=[Location(n.path) for n in gapped[:5]],
        )


class PipInstallCellRule(_NotebookBase):
    id = "R-NB-004"
    category = Category.NOTEBOOK
    title = "No !pip install inside notebook cells"
    rationale = (
        "In-cell installs build an environment that exists only in that kernel session and "
        "silently diverges from the declared dependencies."
    )
    weight = 2

    def evaluate(self, ev: Evidence) -> Finding:
        offenders = [n for n in ev.notebooks.notebooks if n.pip_install_cells]
        if not offenders:
            return self.finding(Status.PASS, confidence=0.85, message="No in-cell pip installs detected.")
        return self.finding(
            Status.PARTIAL,
            confidence=0.85,
            message=f"{len(offenders)} notebook(s) install packages inside cells.",
            remediation="Move the packages into the project's dependency manifest and delete the install cells.",
            locations=[Location(n.path) for n in offenders[:5]],
        )


class NotebookPathsRule(_NotebookBase):
    id = "R-NB-005"
    category = Category.NOTEBOOK
    title = "No absolute/local paths in notebook cells"
    rationale = (
        "Machine-specific absolute paths commonly fail under a different account, "
        "filesystem layout, or operating system."
    )
    weight = 2

    def evaluate(self, ev: Evidence) -> Finding:
        offenders = [n for n in ev.notebooks.notebooks if n.abs_path_cells]
        if not offenders:
            return self.finding(Status.PASS, confidence=0.8, message="No local absolute paths detected in notebooks.")
        return self.finding(
            Status.PARTIAL,
            confidence=0.8,
            message=f"{len(offenders)} notebook(s) reference local absolute paths.",
            remediation="Use repository-relative paths (pathlib.Path(__file__ )-style anchors or a data-root config).",
            locations=[Location(n.path) for n in offenders[:5]],
        )


class NotebookSeedRule(_NotebookBase):
    id = "R-NB-006"
    category = Category.NOTEBOOK
    title = "Seeding precedes randomness in notebooks"
    rationale = (
        "Drawing before deterministic seeding makes notebook results depend on prior RNG state "
        "and can cause reruns to differ."
    )
    weight = 2

    def evaluate(self, ev: Evidence) -> Finding:
        random_users = [n for n in ev.notebooks.notebooks if n.uses_randomness]
        if not random_users:
            return self.finding(Status.NOT_APPLICABLE, confidence=0.7, message="No notebook uses randomness.")
        unseeded = [n for n in random_users if n.seed_before_randomness is False]
        if not unseeded:
            return self.finding(
                Status.PASS, confidence=0.7, message="Every randomness-using notebook seeds before its first draw (heuristic)."
            )
        return self.finding(
            Status.PARTIAL,
            confidence=0.65,
            message=f"{len(unseeded)} notebook(s) draw randomness before (or without) any seed call.",
            remediation="Add a seed cell at the top of the notebook, before any data shuffling or model init.",
            locations=[Location(n.path) for n in unseeded[:5]],
        )


class KernelMetadataRule(_NotebookBase):
    id = "R-NB-007"
    category = Category.NOTEBOOK
    title = "Notebook kernel/environment metadata present"
    rationale = "Kernel metadata records at least the language and kernel the notebook expects."
    weight = 1

    def evaluate(self, ev: Evidence) -> Finding:
        notebooks = [n for n in ev.notebooks.notebooks if not n.parse_error]
        if not notebooks:
            return self.finding(Status.NOT_APPLICABLE, confidence=0.7, message="No parseable notebooks.")
        missing = [n for n in notebooks if not (n.has_kernelspec and n.has_language_info)]
        if not missing:
            return self.finding(Status.PASS, confidence=0.8, message="All notebooks carry kernel and language metadata.")
        return self.finding(
            Status.PARTIAL,
            confidence=0.7,
            message=f"{len(missing)} notebook(s) lack kernel/language metadata.",
            remediation="Save the notebooks from a configured kernel so kernelspec/language_info are recorded.",
            locations=[Location(n.path) for n in missing[:5]],
        )


class NotebookScriptTwinRule(_NotebookBase):
    id = "R-NB-008"
    category = Category.NOTEBOOK
    title = "Result-bearing notebooks have a script equivalent"
    rationale = (
        "A script, Jupytext source, or Papermill entry point can make a result-bearing notebook "
        "easier to run headlessly, review as text, and integrate into automation."
    )
    weight = 2

    def evaluate(self, ev: Evidence) -> Finding:
        result_bearing = [n for n in ev.notebooks.notebooks if n.has_outputs and not n.parse_error]
        if not result_bearing:
            return self.finding(Status.NOT_APPLICABLE, confidence=0.7, message="No result-bearing notebooks.")
        without_twin = [n for n in result_bearing if not n.has_companion_script]
        if not without_twin:
            return self.finding(
                Status.PASS, confidence=0.7, message="Every result-bearing notebook has a same-stem script/markdown twin."
            )
        return self.finding(
            Status.PARTIAL,
            confidence=0.6,
            message=f"{len(without_twin)} result-bearing notebook(s) have no script equivalent.",
            remediation="Pair each with jupytext (`jupytext --set-formats ipynb,py`) or export the pipeline to a script.",
            locations=[Location(n.path) for n in without_twin[:5]],
        )
