"""ACM Artifact Appendix draft, filled from repository evidence.

Follows the standard artifact-appendix structure (abstract, checklist,
description, installation, experiment workflow, expected results). Items the
evidence cannot answer are marked for the author rather than guessed.
"""

from __future__ import annotations

from ..engine import CheckResult

_TODO = "_[author: complete]_"


def _yes_no(condition: bool, detail: str = "") -> str:
    return f"Yes{f' — {detail}' if detail else ''}" if condition else "No"


def render(result: CheckResult) -> str:
    ev = result.evidence
    lines: list[str] = []
    title = ev.manifest.paper.title or ev.latex.title or result.repo.root.name

    lines += [f"# Artifact Appendix — {title}", ""]
    lines += ["## A.1 Abstract", "", _TODO + " One paragraph: what the artifact contains and which claims it supports.", ""]

    lines += ["## A.2 Artifact check-list (meta-information)", ""]
    frameworks = ", ".join(sorted(ev.repo.frameworks.detected - {"python"})) or "Python"
    total_mb = sum(f.size for f in ev.repo.files) / (1024 * 1024)
    runner = (
        ev.env.run_scripts[0]
        if ev.env.run_scripts
        else (f"make {ev.env.makefile_targets[0]}" if ev.env.makefile_targets else _TODO)
    )
    hardware = ev.manifest.environment.hardware or (_TODO if not ev.docs.mentions_hardware_inline else "see README hardware section")
    metrics = sorted({c.metric for c in ev.manifest.claims if c.metric}) or sorted({m.name for m in ev.latex.metrics}) or [_TODO]
    lines += [
        f"- **Program:** {frameworks}",
        f"- **Run-time environment:** Python {ev.deps.python_version or _TODO}"
        + (f", container: {ev.env.dockerfiles[0]}" if ev.env.dockerfiles else ""),
        f"- **Hardware:** {hardware}",
        f"- **Metrics:** {', '.join(str(m) for m in metrics)}",
        f"- **How to run:** `{runner}`" if runner != _TODO else f"- **How to run:** {_TODO}",
        f"- **Disk space required (artifact):** ~{total_mb:.0f} MiB plus datasets",
        f"- **Approximate experiment time:** {_TODO}",
        f"- **Publicly available:** {_yes_no(ev.git.is_repo)}",
        f"- **Code license:** {_yes_no(bool(ev.docs.license_file), ev.docs.license_file or '')}",
        f"- **Archived (DOI):** {_yes_no(ev.git.has_archival_doi)}",
        "",
    ]

    lines += ["## A.3 Description", "", "### A.3.1 How the artifact is delivered", ""]
    delivery = "Public git repository"
    if ev.git.has_archival_doi:
        delivery += " with an archival deposit (DOI)"
    lines += [delivery + ".", "", "### A.3.2 Hardware dependencies", ""]
    lines += [hardware if hardware != _TODO else _TODO, "", "### A.3.3 Software dependencies", ""]
    deps_line = ", ".join(ev.deps.declaration_files) if ev.deps.declared else _TODO
    lines += [f"Declared in: {deps_line}."
              + (f" Lockfile: {ev.deps.lockfiles[0]}." if ev.deps.lockfiles else " No lockfile."), ""]
    lines += ["### A.3.4 Data sets", ""]
    if ev.manifest.datasets:
        for dataset in ev.manifest.datasets:
            source = f" — {dataset.source}" if dataset.source else ""
            lines.append(f"- {dataset.id}{source}")
    else:
        lines.append(_TODO + " List each dataset, its source (DOI where possible), and its license.")
    lines += [""]

    lines += ["## A.4 Installation", ""]
    if ev.docs.has_section("install"):
        lines += ["See the README installation section. Summary:", ""]
    lines += ["```bash", "# " + (_TODO if not ev.docs.has_section("install") else "verify against the README"),
              "pip install -r requirements.txt" if ev.repo.exists("requirements.txt") else "pip install -e .",
              "```", ""]

    lines += ["## A.5 Experiment workflow", ""]
    commands = [c.command for c in ev.runs.commands[:5]] or ev.docs.run_commands[:5]
    if commands:
        lines += ["```bash", *commands, "```", ""]
    else:
        lines += [_TODO + " The exact command sequence from clean checkout to reported numbers.", ""]

    lines += ["## A.6 Evaluation and expected results", ""]
    claims_with_values = [c for c in ev.manifest.claims if c.value is not None]
    if claims_with_values:
        lines += ["| Claim | Metric | Expected value | Produced by |", "|---|---|---|---|"]
        for claim in claims_with_values[:10]:
            lines.append(
                f"| {claim.id} | {claim.metric or ''} | {claim.value:g} | `{claim.produced_by.command or _TODO}` |"
            )
        lines += [""]
    elif ev.docs.has_results_table:
        lines += ["Expected results are tabulated in the README; a rerun should land within the stated tolerance.", ""]
    else:
        lines += [_TODO + " State the numbers a successful evaluation should obtain and the tolerance.", ""]

    lines += ["## A.7 Notes", "",
              "Generated as a draft from repository evidence; every field marked for the author must be completed "
              "and all others verified before submission.", ""]
    return "\n".join(lines)
