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
from ..rules.base import Finding, Status, summarize_items
from ..scoring import top_fixes
from ..text import safe_display_text

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


def _item_census(finding: Finding) -> str:
    """The complete child count and its per-status split, or nothing.

    Summarised rather than listed: none of the children appear here, so the
    line cannot be mistaken for the whole set. `json` carries every item of
    every finding; `sarif` carries every item of every finding it reports, and
    it reports only actionable (fail/partial) findings.
    """
    if not finding.items:
        return ""
    counts = summarize_items(finding.items)
    split = ", ".join(
        f"{count} {_STATUS_STYLE[status][1]}" for status, count in counts.items() if count
    )
    return f"{len(finding.items)} item(s) not listed here: {split}"


def _status_text(finding: Finding) -> Text:
    style, label = _STATUS_STYLE[finding.status]
    if finding.suppressed:
        return Text(f"{label} (ignored)", style=style)
    return Text(label, style=style)


def _render_summary(result: CheckResult, console: Console) -> None:
    card = result.card
    commit = (result.repo.git.head_commit or "")[:7]
    header = f"[bold]adduce[/bold]  ·  {result.repo.root.name}" + (f"  ·  commit {commit}" if commit else "")
    score_cell = (
        ("no score", "bold dim")
        if card.total is None
        else (f"{card.total:.0f}/100", f"bold {_score_color(card.total)}")
    )
    summary = Text.assemble(
        ("Reproducibility  ", "bold"),
        score_cell,
        (f"   {card.tier}", "bold"),
        (f"   ·   profile: {card.profile_name}", "dim"),
    )
    console.print(Panel(summary, title=header, title_align="left", border_style="dim"))
    if card.total is None:
        # The tier reports nothing assessed, so the note has to as well: thin
        # source is a second fact here, never the stated cause.
        note = (
            "No tier assigned: no check reached an assessment, so there is nothing "
            "to score. Every check either did not apply to this repository or could "
            "not be answered from the evidence collected."
        )
        if not card.rated:
            note += (
                f" The analyzer parsed {card.analysable_lines} lines of source, "
                "itself below the floor for a rating."
            )
        console.print(Text(note, style="yellow"))
    elif not card.rated:
        # The score above is real; what it is a score *of* is the problem. Say
        # so next to it rather than letting a tier imply a judgement the
        # evidence does not support.
        console.print(
            Text(
                f"No tier assigned: only {card.analysable_lines} lines of source were "
                f"parsed, and {card.evaluated_rules} of {card.applicable_rules} "
                "applicable checks reached a verdict. Most checks are statements "
                "about code, so with this little of it they are answered by "
                "absence rather than evidence.",
                style="yellow",
            )
        )
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
    if result.config.source and not result.config.repository_policy_honored:
        console.print(
            Text(
                f"Repository policy in {result.config.source} was not applied in this "
                "reviewer-facing mode.",
                style="yellow",
            )
        )
    elif result.config.source and (result.config.ignore or result.config.exclude):
        details = []
        if result.config.ignore:
            details.append(f"{len(result.config.ignore)} ignored rule(s)")
        if result.config.exclude:
            details.append(f"{len(result.config.exclude)} excluded path pattern(s)")
        console.print(
            Text(
                f"Repository policy applied from {result.config.source}: "
                + ", ".join(details)
                + ". Ignored findings retain their observed score; excluded paths were not scanned.",
                style="yellow",
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
            + (" (ignored)" if finding.suppressed else "")
            for finding in cat.findings
            if finding.status in (Status.PARTIAL, Status.FAIL)
        ]
        joined = "; ".join(notes)
        if len(joined) > 180:
            joined = joined[:177].rsplit(" ", 1)[0] + " …"
        # `notes` collects only PARTIAL and FAIL, so a category holding PASS and
        # UNKNOWN together produces none and would otherwise claim everything was
        # satisfied. Count what went unanswered and say so instead.
        unknown = sum(1 for finding in cat.findings if finding.status is Status.UNKNOWN)
        if notes:
            note: str | Text = joined
        elif cat.possible == 0:
            # Nothing in this category reached an assessment at all.
            note = Text(f"{unknown} check(s) applied; none could be assessed", style="dim")
        elif unknown:
            note = Text(
                f"detected checks satisfied; {unknown} could not be assessed",
                style="dim",
            )
        else:
            note = Text("all detected checks satisfied", style="dim")
        score_cell = (
            Text("—", style="dim")
            if cat.possible == 0
            else Text(f"{cat.earned:.0f}/{cat.possible:.0f}", style=_score_color(cat.percentage))
        )
        table.add_row(cat.category.value, score_cell, note)
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
            # A path is repository-controlled, and this one is going to a
            # terminal. Unsanitised it can carry an escape sequence that clears
            # the findings printed above it, or an OSC 8 hyperlink pointing
            # anywhere. JSON and SARIF escape these; prose formats must strip them.
            "\n  at " + ", ".join(safe_display_text(str(loc)) for loc in finding.locations[:3])
            if finding.locations
            else ""
        )
        census = _item_census(finding)
        item_note = f"\n  {census}" if census else ""
        detail.add_row(
            finding.rule_id,
            _status_text(finding),
            f"{finding.confidence:.0%}",
            finding.message + location_note + item_note,
        )
    console.print(detail)
    console.print()


def _structured_observation_notice(result: CheckResult) -> str:
    """One line saying that child results exist, and where to read them.

    Counted, never rendered: one ``len()`` per finding, so the cost is fixed
    per finding rather than per child. Default output lists no child and, until
    this line, gave no sign that any existed -- which left the reader of a
    finding backed by thousands of observations unable to know they were there.
    """
    carrying = [finding for finding in result.card.findings if finding.items]
    if not carrying:
        return ""
    total = sum(len(finding.items) for finding in carrying)
    return (
        f"{total} structured observation(s) attached to {len(carrying)} finding(s), "
        "not listed here — use --verbose for per-finding counts, or --format json for the detail."
    )


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

    notice = _structured_observation_notice(result)
    if notice:
        console.print(Text(notice, style="dim"))

    console.print(
        Text(
            "Statuses are detected signals from static analysis, not a certification of reproducibility.",
            style="dim italic",
        )
    )
    console.print(
        Text("Next:  adduce manifest   ·   adduce checklist --profile neurips   ·   adduce check --verbose", style="dim")
    )
