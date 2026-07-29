"""ACM Artifact Appendix draft, filled from repository evidence.

Follows the standard artifact-appendix structure (abstract, checklist,
description, installation, experiment workflow, expected results). Items the
evidence cannot answer are marked for the author rather than guessed, and the
sections that state facts (A.2 check-list, A.6 expected results) are recorded
in an evidence ledger so those statements stay traceable and auditable.
"""

from __future__ import annotations

from ..engine import CheckResult
from ..ledger import Ledger, LedgerEntry, build_entry, build_provenance, sha256_text
from ..markdown_safety import (
    markdown_code_span,
    markdown_indented_lines,
    markdown_inline,
)
from ..rules.base import Finding

_TODO = "[AUTHOR REVIEW REQUIRED]"

# The A.2 meta-information block states facts about documentation, the
# environment, how to run, and licensing; A.6 states the expected numbers.
# These rule sets are the evidence surfaces those statements rest on.
_A2_RULES = ("R-DOC-001", "R-ENV-001", "R-EXEC-002", "R-LIC-001")
_A6_RULES = ("R-RES-001", "R-RES-002", "R-RES-003")


def _draft_inline(value: object) -> str:
    """Escape repository text while preserving the review marker verbatim."""
    return _TODO if value == _TODO else markdown_inline(value)


def _anchor_line(findings: list[Finding], cap: int = 4) -> str | None:
    anchors = [str(loc) for finding in findings for loc in finding.locations][:cap]
    return (
        "[EVIDENCE: "
        + ", ".join(markdown_code_span(anchor) for anchor in anchors)
        + "]"
        if anchors
        else None
    )


def render(result: CheckResult, strict: bool = False) -> tuple[str, Ledger]:
    ev = result.evidence
    findings_by_rule = {f.rule_id: f for f in result.card.findings}
    a2_findings = [findings_by_rule[r] for r in _A2_RULES if r in findings_by_rule]
    a6_findings = [findings_by_rule[r] for r in _A6_RULES if r in findings_by_rule]
    confirmed_claims = [
        claim
        for claim in ev.manifest.claims
        if (claim.status or "").strip().lower() == "confirmed"
    ]
    claims_with_values = [claim for claim in confirmed_claims if claim.value is not None]
    confirmed_metrics = sorted(
        {claim.metric for claim in confirmed_claims if claim.metric}
    )
    archival_doi = next(
        (
            doi
            for doi in ev.docs.dois
            if "zenodo" in doi.lower() or doi.startswith("10.5281/")
        ),
        None,
    )
    entries: list[LedgerEntry] = [
        build_entry(
            item_id="A.2",
            question="Artifact check-list (meta-information)",
            findings=a2_findings,
            rule_ids=_A2_RULES,
            manifest_backed=False,
            manifest_evidence=bool(
                confirmed_metrics or ev.manifest.environment.hardware
            ),
            strict=strict,
        ),
        build_entry(
            item_id="A.6",
            question="Evaluation and expected results",
            findings=a6_findings,
            rule_ids=_A6_RULES,
            manifest_backed=False,
            manifest_evidence=bool(claims_with_values),
            strict=strict,
        ),
    ]

    lines: list[str] = []
    title = ev.manifest.paper.title or ev.latex.title or result.repo.root.name

    lines += [f"# Artifact Appendix — {markdown_inline(title)}", ""]
    lines += ["## A.1 Abstract", "", _TODO + " One paragraph: what the artifact contains and which claims it supports.", ""]

    lines += ["## A.2 Artifact check-list (meta-information)", ""]
    a2_anchor = _anchor_line(a2_findings)
    if a2_anchor:
        lines += [a2_anchor, ""]
    frameworks = (
        ", ".join(
            markdown_inline(name)
            for name in sorted(ev.repo.frameworks.detected - {"python"})
        )
        or "Python"
    )
    total_mb = sum(f.size for f in ev.repo.files) / (1024 * 1024)
    runner = (
        ev.env.run_scripts[0]
        if ev.env.run_scripts
        else (f"make {ev.env.makefile_targets[0]}" if ev.env.makefile_targets else _TODO)
    )
    hardware = ev.manifest.environment.hardware or (
        _TODO
        if not ev.docs.mentions_hardware_inline
        else "see README hardware section"
    )
    metrics = confirmed_metrics or [_TODO]
    lines += [
        f"- **Program:** {frameworks}",
        f"- **Run-time environment:** Python {_draft_inline(ev.deps.python_version or _TODO)}"
        + (
            f", container: {markdown_inline(ev.env.dockerfiles[0])}"
            if ev.env.dockerfiles
            else ""
        ),
        f"- **Hardware:** {_draft_inline(hardware)}",
        f"- **Metrics:** {', '.join(_draft_inline(metric) for metric in metrics)}",
        (
            f"- **How to run:** {markdown_code_span(runner)}"
            if runner != _TODO
            else f"- **How to run:** {_TODO}"
        ),
        f"- **Disk space required (artifact):** ~{total_mb:.0f} MiB plus datasets",
        f"- **Approximate experiment time:** {_TODO}",
        f"- **Publicly available:** {_TODO} Confirm the artifact URL and its access permissions.",
        "- **Code license:** "
        + (
            f"Detected — {markdown_inline(ev.docs.license_file)}"
            if ev.docs.license_file
            else "Not detected"
        ),
        f"- **Archived (DOI):** Detected — {markdown_inline(archival_doi)}"
        if archival_doi
        else f"- **Archived (DOI):** {_TODO} Confirm the deposit and record its DOI.",
        "",
    ]

    lines += ["## A.3 Description", "", "### A.3.1 How the artifact is delivered", ""]
    lines += [
        _TODO + " Describe how reviewers receive the artifact and any access requirements.",
        "",
        "### A.3.2 Hardware dependencies",
        "",
    ]
    lines += [
        _draft_inline(hardware),
        "",
        "### A.3.3 Software dependencies",
        "",
    ]
    deps_line = (
        ", ".join(markdown_inline(path) for path in ev.deps.declaration_files)
        if ev.deps.declared
        else _TODO
    )
    lines += [
        f"Declared in: {deps_line}."
        + (
            f" Lockfile: {markdown_inline(ev.deps.lockfiles[0])}."
            if ev.deps.lockfiles
            else " No lockfile."
        ),
        "",
    ]
    lines += ["### A.3.4 Data sets", ""]
    if ev.manifest.datasets:
        for dataset in ev.manifest.datasets:
            source = f" — {dataset.source}" if dataset.source else ""
            lines.append(
                f"- {markdown_inline(dataset.id)}{markdown_inline(source)}"
            )
    else:
        lines.append(_TODO + " List each dataset, its source (DOI where possible), and its license.")
    lines += [""]

    lines += ["## A.4 Installation", ""]
    if ev.docs.has_section("install"):
        lines += [
            "Installation instructions were detected in the README; verify and reproduce the exact commands here before submission.",
            "",
            _TODO + " Paste the exact installation commands from a clean environment.",
            "",
        ]
    else:
        lines += [_TODO + " Provide exact installation commands from a clean environment.", ""]

    lines += ["## A.5 Experiment workflow", ""]
    commands = [c.command for c in ev.runs.commands[:5]] or ev.docs.run_commands[:5]
    if commands:
        lines += ["Commands detected in the repository:", ""]
        for command in commands:
            lines.extend(markdown_indented_lines(command))
            lines.append("")
    else:
        lines += [_TODO + " The exact command sequence from clean checkout to reported numbers.", ""]

    lines += ["## A.6 Evaluation and expected results", ""]
    a6_anchor = _anchor_line(a6_findings)
    if a6_anchor:
        lines += [a6_anchor, ""]
    if claims_with_values:
        lines += ["| Claim | Metric | Expected value | Produced by |", "|---|---|---|---|"]
        for claim in claims_with_values:
            lines.append(
                f"| {markdown_inline(claim.id)} | "
                f"{markdown_inline(claim.metric or '')} | {claim.value:g} | "
                f"{_draft_inline(claim.produced_by.command or _TODO)} |"
            )
        lines += [""]
    elif ev.docs.has_results_table:
        lines += [
            "Expected results are tabulated in the README.",
            "",
            _TODO + " State the acceptable tolerance and how reviewers should compare a run against those values.",
            "",
        ]
    else:
        lines += [_TODO + " State the numbers a successful evaluation should obtain and the tolerance.", ""]

    lines += ["## A.7 Notes", "",
              "Generated as a draft from repository evidence; every field marked for the author must be completed "
              "and all others checked before submission.", ""]
    markdown = "\n".join(lines)
    ledger = Ledger(
        artifact_path="artifact_appendix.md",
        artifact_sha256=sha256_text(markdown),
        provenance=build_provenance(
            command="appendix",
            profile=None,
            mode="strict" if strict else "default",
            repo_commit=result.repo.git.head_commit,
        ),
        entries=entries,
    )
    return markdown, ledger
