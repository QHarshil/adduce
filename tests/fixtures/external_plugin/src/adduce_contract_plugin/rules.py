"""The module the ``adduce.rules`` entry point resolves to."""

from __future__ import annotations

from adduce.evidence import Evidence
from adduce.rules import Category, Finding, Rule, Status


class ContractProbeRule(Rule):
    """A pure rule over evidence: no filesystem, subprocess or network access."""

    id = "X-CONTRACT-PLUGIN-001"
    category = Category.CODE_EXECUTION
    title = "External plugin contract probe"
    rationale = "Confirms an installed rule pack reaches evaluation with typed evidence."
    weight = 1
    severity = "low"

    def evaluate(self, ev: Evidence) -> Finding:
        sources = ev.repo.python_files()
        if sources:
            return self.finding(
                Status.PASS,
                0.9,
                f"Detected {len(sources)} Python source file(s) in the scanned inventory.",
            )
        return self.finding(
            Status.UNKNOWN,
            0.5,
            "The scanned inventory holds no Python source file to read.",
        )


RULES = [ContractProbeRule]
