"""Rich terminal report: the default output of ``adduce check``.

Renders the score summary, per-category table, claim trails, ranked fixes,
and the reviewer-time headline, framed for the selected mode (author,
reviewer, or AE chair).
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..engine import CheckResult
from ..graph import TrailStatus
from ..modes import Mode, badge_eligibility, blocking_issues, unverifiable_findings
from ..rules.base import Finding, Status
from ..scoring import top_fixes

_STATUS_STYLE = {
    Status.PASS: ("green", "pass"),
    Status.PARTIAL: ("yellow", "partial"),
    Status.FAIL: ("red", "fail"),
    Status.NOT_APPLICABLE: ("dim", "n/a"),
    Status.UNKNOWN: ("dim", "unknown"),
}

_TRAIL_STYLE = {
    TrailStatus.SUPPORTED: ("green", "SUPPORTED"),
    TrailStatus.PARTIAL: ("yellow", "PARTIAL"),
    TrailStatus.UNLINKED: ("red", "UNLINKED"),
}


def _score_color(percentage: float) -> str:
    if percentage >= 85:
        return "green"
    if percentage >= 60:
        return "yellow"
    return "red"


def _status_text(finding: Finding) -> Text:
    if finding.suppressed:
        return Text("ignored", style="dim")
    style, label = _STATUS_STYLE[finding.status]
    return Text(label, style=style)


def _render_summary(result: CheckResult, console: Console) -> None:
    card = result.card
    commit = (result.repo.git.head_commit or "")[:7]
    header = f"[bold]adduce[/bold]  ·  {result.repo.root.name}" + (f"  ·  commit {commit}" if commit else "")
    color = _score_color(card.total)
    summary = Text.assemble(
        ("Reproducibility  ", "bold"),
        (f"{card.total:.0f}/100", f"bold {color}"),
        (f"   {card.tier}", "bold"),
        (f"   ·   profile: {card.profile_name}", "dim"),
    )
    console.print(Panel(summary, title=header, title_align="left", border_style="dim"))
    console.print(Text(result.reviewer_time.headline, style="bold"))
    for factor in result.reviewer_time.factors[:4]:
        console.print(Text(f"  - {factor}", style="dim"))
    if not result.evidence.latex.has_paper and not result.evidence.manifest.claims:
        console.print(
            Text(
                "No paper sources detected — repository-only audit "
                "(point adduce at the LaTeX sources with --paper to enable drift checks).",
                style="dim",
            )
        )
    console.print()


def _render_categories(result: CheckResult, console: Console) -> None:
    table = Table(box=None, pad_edge=False, show_header=True, header_style="bold dim")
    table.add_column("Category", min_width=24)
    table.add_column("Score", justify="right")
    table.add_column("Notes", overflow="fold")
    for cat in result.card.categories:
        notes = [
            finding.message.rstrip(".")
            for finding in cat.findings
            if finding.status in (Status.PARTIAL, Status.FAIL) and not finding.suppressed
        ]
        joined = "; ".join(notes)
        if len(joined) > 180:
            joined = joined[:177].rsplit(" ", 1)[0] + " …"
        table.add_row(
            cat.category.value,
            Text(f"{cat.earned:.0f}/{cat.possible:.0f}", style=_score_color(cat.percentage)),
            joined if notes else Text("all detected checks satisfied", style="dim"),
        )
    console.print(table)
    console.print()


def _render_trails(result: CheckResult, console: Console) -> None:
    trails = result.graph.trails
    if not trails:
        return
    if result.graph.from_manifest:
        source = "manifest; draft claims remain inferred until author-confirmed"
    else:
        source = "inferred from evidence — confirm via `adduce manifest`"
    console.print(f"[bold]Claim trails[/bold] [dim]({source})[/dim]")
    for trail in trails[:5]:
        style, label = _TRAIL_STYLE[trail.status]
        provenance = " [inferred draft]" if trail.inferred else ""
        console.print(Text.assemble("  ", (trail.headline, "bold"), (provenance, "dim")))
        for entry in trail.entries:
            marker = "" if entry.resolved is None else (" ✓" if entry.resolved else " ✗")
            line = Text(f"    {entry.label:<12}{entry.value}")
            if entry.note:
                line.append(f"   {entry.note}", style="yellow")
            if marker:
                line.append(marker, style="green" if entry.resolved else "red")
            console.print(line)
        console.print(Text.assemble("    status      ", (label, style)))
    if len(trails) > 5:
        console.print(Text(f"  … and {len(trails) - 5} more claim(s)", style="dim"))
    console.print()


def _render_fixes(result: CheckResult, console: Console) -> None:
    fixes = top_fixes(result.card, limit=5)
    if not fixes:
        return
    console.print("[bold]Top fixes[/bold] (largest score gains first)")
    for index, finding in enumerate(fixes, start=1):
        console.print(Text(f" {index}. ") + Text(finding.remediation or finding.title))
        if finding.fix_command:
            console.print(Text(f"     {finding.fix_command}", style="cyan"))
    console.print()


def _render_reviewer_mode(result: CheckResult, console: Console) -> None:
    console.print("[bold]Could not be verified[/bold] (a reviewer will probe these first)")
    unverifiable = unverifiable_findings(result.card)
    if not unverifiable:
        console.print(Text("  nothing flagged as unverifiable", style="dim"))
    for finding in unverifiable[:8]:
        console.print(Text(f"  {finding.rule_id}  ") + Text(finding.message, style="yellow"))
    console.print()
    ambiguous = [
        f
        for f in result.card.findings
        if f.status is Status.PARTIAL and not f.suppressed and f.confidence >= 0.6
    ]
    if ambiguous:
        console.print("[bold]Partially satisfied[/bold] (gaps a skeptical reader will notice)")
        for finding in ambiguous[:8]:
            console.print(Text(f"  {finding.rule_id}  ") + Text(finding.message))
        console.print()


def _render_chair_mode(result: CheckResult, console: Console) -> None:
    console.print("[bold]Badge prerequisites[/bold] (static signals only; never an award prediction)")
    for assessment in badge_eligibility(result.card):
        marker = (
            Text(" static prerequisites detected", style="green")
            if assessment.eligible
            else Text(" prerequisites incomplete", style="red")
        )
        console.print(Text(f"  {assessment.label}:") + marker)
        for blocker in assessment.blocking[:4]:
            console.print(Text(f"      - {blocker}", style="dim"))
        for item in assessment.manual_review:
            console.print(Text(f"      - author/reviewer check: {item}", style="dim"))
    console.print()
    gates = blocking_issues(result.card)
    if gates:
        console.print("[bold]Blocking issues[/bold]")
        for finding in gates[:6]:
            console.print(Text(f"  {finding.rule_id}  ") + Text(finding.message, style="red"))
        console.print()


def _render_findings_table(result: CheckResult, console: Console) -> None:
    console.print("[bold]All findings[/bold]")
    detail = Table(box=None, pad_edge=False, header_style="bold dim")
    detail.add_column("Rule")
    detail.add_column("Status")
    detail.add_column("Confidence", justify="right")
    detail.add_column("Detail", overflow="fold")
    for finding in result.card.findings:
        location_note = (
            "\n  at " + ", ".join(str(loc) for loc in finding.locations[:3]) if finding.locations else ""
        )
        detail.add_row(
            finding.rule_id,
            _status_text(finding),
            f"{finding.confidence:.0%}",
            finding.message + location_note,
        )
    console.print(detail)
    console.print()


def render(
    result: CheckResult,
    console: Console,
    verbose: bool = False,
    mode: Mode = Mode.AUTHOR,
) -> None:
    _render_summary(result, console)
    _render_categories(result, console)
    _render_trails(result, console)

    if mode is Mode.REVIEWER:
        _render_reviewer_mode(result, console)
    elif mode is Mode.AE_CHAIR:
        _render_chair_mode(result, console)
    else:
        _render_fixes(result, console)

    if verbose:
        _render_findings_table(result, console)

    console.print(
        Text(
            "Statuses are detected signals from static analysis, not a certification of reproducibility.",
            style="dim italic",
        )
    )
    console.print(
        Text("Next:  adduce manifest   ·   adduce checklist --profile neurips   ·   adduce check --verbose", style="dim")
    )
