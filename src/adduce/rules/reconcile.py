"""Result reconciliation: do the paper's numbers exist in the logged results?

Matches metric statements and table cells from the paper against the metric
columns found in local result files. Rounding-level differences are low
severity; material differences and missing counterparts are what reviewers
need surfaced. All probabilistic, all confidence-carrying.
"""

from __future__ import annotations

from ..evidence import Evidence
from ..model import Repo
from .base import Category, Finding, Rule, Status
from .drift import values_match


def _paper_metric_statements(ev: Evidence) -> list[tuple[str, float, str | None]]:
    statements: list[tuple[str, float, str | None]] = []
    manifest_pairs: set[tuple[str, float]] = set()
    for claim in ev.manifest.claims:
        if claim.metric and claim.value is not None:
            is_draft = (claim.status or "").strip().lower() == "draft"
            statements.append(
                (claim.metric, claim.value, None if is_draft else claim.produced_by.log)
            )
            manifest_pairs.add((claim.metric.casefold(), claim.value))
    for metric in ev.latex.metrics:
        if (metric.name.casefold(), metric.value) not in manifest_pairs:
            statements.append((metric.name, metric.value, None))
    return statements


def _material_difference(paper: float, logged: float) -> bool:
    """Beyond rounding: a relative gap over 2% or an absolute gap over 0.02
    on percent-like scales."""
    if values_match(paper, logged):
        return False
    scale = max(abs(paper), abs(logged), 1e-9)
    return abs(paper - logged) / scale > 0.02


class _ReconcileBase(Rule):
    def applies_to(self, repo: Repo) -> bool:
        return True

    def _applicable(self, ev: Evidence) -> Finding | None:
        if not (ev.latex.has_paper or ev.manifest.claims):
            return self.finding(
                Status.NOT_APPLICABLE, confidence=0.7, message="No paper or manifest claims to reconcile against."
            )
        if not ev.results.present:
            return self.finding(
                Status.NOT_APPLICABLE,
                confidence=0.6,
                message="No local result files detected (results are often gitignored; nothing to reconcile).",
            )
        return None


class RoundingDifferenceRule(_ReconcileBase):
    id = "R-RES-001"
    category = Category.RESULTS
    title = "Reported metrics differ from logs only at rounding level"
    rationale = (
        "A rounded 0.814 backed by a logged 0.8137 is healthy; surfacing it confirms the "
        "trail rather than flagging an error."
    )
    weight = 1

    def evaluate(self, ev: Evidence) -> Finding:
        gate = self._applicable(ev)
        if gate:
            return gate
        rounded: list[str] = []
        for name, paper_value, log_path in _paper_metric_statements(ev):
            for source, values in ev.results.lookup_metric(name, path=log_path):
                if any(values_match(paper_value, v) and paper_value != v for v in values):
                    rounded.append(f"{name}={paper_value:g} ≈ {source}")
                    break
        if rounded:
            return self.finding(
                Status.PASS,
                confidence=0.6,
                message="Reported value(s) reconcile with logs at rounding level: " + "; ".join(rounded[:3]) + ".",
            )
        return self.finding(
            Status.NOT_APPLICABLE, confidence=0.5, message="No rounding-level correspondences detected."
        )


class MaterialDifferenceRule(_ReconcileBase):
    id = "R-RES-002"
    category = Category.RESULTS
    title = "Reported metric materially differs from the logged value"
    rationale = (
        "When the paper's number and the closest logged value disagree beyond rounding, "
        "either the log is from a different run or the paper is stale — both need resolving "
        "before a reviewer finds it."
    )
    weight = 4

    def evaluate(self, ev: Evidence) -> Finding:
        gate = self._applicable(ev)
        if gate:
            return gate
        material: list[str] = []
        compared = 0
        for name, paper_value, log_path in _paper_metric_statements(ev):
            matches = ev.results.lookup_metric(name, path=log_path)
            if not matches:
                continue
            compared += 1
            all_values = [v for _, values in matches for v in values]
            closest = min(all_values, key=lambda v: abs(v - paper_value))
            if _material_difference(paper_value, closest):
                material.append(f"{name}: paper {paper_value:g} vs closest logged {closest:g}")
        if compared == 0:
            return self.finding(
                Status.UNKNOWN,
                confidence=0.5,
                message="No paper metric could be matched to a logged metric column by name.",
            )
        if not material:
            return self.finding(
                Status.PASS,
                confidence=0.6,
                message=f"All {compared} reconcilable metric(s) agree with logged values within rounding.",
            )
        return self.finding(
            Status.PARTIAL,  # a different run may legitimately be logged; never an outright fail
            confidence=0.55,
            message="Reported metric(s) materially differ from the logged values: " + "; ".join(material[:3]) + ".",
            remediation="Confirm which run produced the paper's numbers and link its log in the manifest (produced_by.log).",
        )


class SingleRunRule(Rule):
    id = "R-RES-003"
    category = Category.RESULTS
    title = "Single-run results without variance reporting"
    rationale = (
        "Conference checklists ask explicitly for error bars; a single seed with no std/CI "
        "makes the reported difference uninterpretable."
    )
    weight = 3

    def applies_to(self, repo: Repo) -> bool:
        return repo.frameworks.uses_any({"torch", "tensorflow", "sklearn", "jax", "lightning", "transformers"})

    def evaluate(self, ev: Evidence) -> Finding:
        # Paper prose is a claim to be corroborated, not evidence in itself:
        # "averaged over 5 seeds" in the .tex must not pass this rule when
        # nothing in the repository actually sweeps seeds.
        corroborated: list[str] = []
        if any(len(c.seeds) > 1 for c in ev.manifest.claims):
            corroborated.append("the manifest records multiple seeds per claim")
        seeds_in_commands = {s for c in ev.runs.commands for s in c.seeds}
        if len(seeds_in_commands) > 1:
            corroborated.append(f"run scripts sweep {len(seeds_in_commands)} seeds")
        if corroborated:
            if ev.latex.mentions_multiseed:
                corroborated.append("matching multi-seed statistics in the paper")
            return self.finding(
                Status.PASS, confidence=0.65, message="Multi-run evidence: " + "; ".join(corroborated) + "."
            )
        if ev.latex.mentions_multiseed:
            return self.finding(
                Status.PARTIAL,
                confidence=0.6,
                message="The paper reports multi-seed statistics, but nothing in the repository "
                "corroborates it (no manifest seeds, no seed sweep in run scripts).",
                remediation="Commit the seed-sweep script or record the seeds per claim in the manifest so the paper's claim is checkable.",
            )
        if not (ev.latex.has_paper or ev.manifest.claims or ev.runs.commands):
            return self.finding(
                Status.UNKNOWN, confidence=0.5, message="No paper, manifest, or run scripts to judge seed coverage from."
            )
        return self.finding(
            Status.PARTIAL,
            confidence=0.55,
            message="No multi-seed or variance evidence found (paper statistics, manifest seeds, or seed sweeps in scripts).",
            remediation="Run the main experiments over several seeds and report mean ± std; record the seeds in the manifest.",
        )


class UnbackedMetricRule(_ReconcileBase):
    id = "R-RES-004"
    category = Category.RESULTS
    title = "Reported metric has no corresponding logged result"
    rationale = "A number with no log behind it is exactly what artifact reviewers probe first."
    weight = 3

    def evaluate(self, ev: Evidence) -> Finding:
        gate = self._applicable(ev)
        if gate:
            return gate
        statements = _paper_metric_statements(ev)
        if not statements:
            return self.finding(
                Status.NOT_APPLICABLE, confidence=0.6, message="No metric statements extracted from paper or manifest."
            )
        unbacked = [
            name
            for name, _, log_path in statements
            if not ev.results.lookup_metric(name, path=log_path)
        ]
        unbacked = sorted(set(unbacked))
        if not unbacked:
            return self.finding(
                Status.PASS,
                confidence=0.6,
                message=f"Every stated metric ({len(statements)}) has at least one matching logged column.",
            )
        return self.finding(
            Status.PARTIAL,
            confidence=0.55,
            message="Stated metric(s) with no matching logged column: " + ", ".join(unbacked[:5]) + " "
            "(logs may be gitignored — link them explicitly if they exist).",
            remediation="Commit or link the evaluation logs, and map each claim to its log in the manifest (produced_by.log).",
        )
