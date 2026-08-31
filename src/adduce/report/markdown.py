"""Markdown report: shareable in PRs, issues, and lab wikis."""

from __future__ import annotations

from ..engine import CheckResult
from ..rules.base import Finding, Status, summarize_items
from ..scoring import top_fixes
from ..text import safe_display_text

_STATUS_LABEL = {
    Status.PASS: "pass",
    Status.PARTIAL: "partial",
    Status.FAIL: "fail",
    Status.NOT_APPLICABLE: "n/a",
    Status.UNKNOWN: "unknown",
}


def _item_census(finding: Finding) -> str:
    """The complete child count and its per-status split, or nothing.

    A human report summarises rather than listing thousands of children, so it
    states how many there are: no child is listed here, so nothing can be read
    as the full set. ``json`` carries every item of every finding; ``sarif``
    carries every item of every finding it reports, and it reports only
    actionable (fail/partial) findings.
    """
    if not finding.items:
        return ""
    counts = summarize_items(finding.items)
    split = ", ".join(
        f"{count} {_STATUS_LABEL[status]}" for status, count in counts.items() if count
    )
    return f"{len(finding.items)} item(s) not listed here: {split}"


def render(result: CheckResult) -> str:
    card = result.card
    repo_name = result.repo.root.name
    commit = (result.repo.git.head_commit or "")[:7]
    lines: list[str] = []
    lines.append(f"# Reproducibility report — {repo_name}")
    lines.append("")
    headline = "**not assessed**" if card.total is None else f"**{card.total:.0f}/100**"
    subtitle = f"Score {headline} ({card.tier}) · profile `{card.profile_name}`"
    if commit:
        subtitle += f" · commit `{commit}`"
    lines.append(subtitle)
    lines.append("")
    if result.config.source and not result.config.repository_policy_honored:
        lines.append(
            f"> Repository policy in `{result.config.source}` was not applied in "
            "this reviewer-facing run."
        )
        lines.append("")
    elif result.config.source and (result.config.ignore or result.config.exclude):
        lines.append(
            f"> Repository policy from `{result.config.source}` applied "
            f"{len(result.config.ignore)} ignored rule(s) and "
            f"{len(result.config.exclude)} excluded path pattern(s). "
            "Ignored findings retain their observed score; excluded paths were not scanned."
        )
        lines.append("")
    lines.append("| Category | Score | |")
    lines.append("|---|---:|---|")
    for cat in card.categories:
        bar = "" if cat.possible == 0 else f"{cat.percentage:.0f}%"
        # ``:g`` after rounding, not ``:.0f``: a category that earned 0.2 of 1
        # rendered as "0/1" beside its own "20%", which reads as a contradiction.
        lines.append(
            f"| {cat.category.value} | {round(cat.earned, 1):g}/{cat.possible:.0f} | {bar} |"
        )
    lines.append("")

    fixes = top_fixes(card, limit=5)
    if fixes:
        lines.append("## Top fixes")
        lines.append("")
        for finding in fixes:
            fix_hint = f" (`{finding.fix_command}`)" if finding.fix_command else ""
            lines.append(f"1. **{finding.title}** — {finding.remediation or finding.message}{fix_hint}")
        lines.append("")

    lines.append("## All findings")
    lines.append("")
    lines.append("| Rule | Status | Confidence | Detail |")
    lines.append("|---|---|---:|---|")
    for finding in card.findings:
        status = _STATUS_LABEL[finding.status]
        if finding.suppressed:
            status += " (ignored)"
        detail = finding.message.replace("|", "\\|")
        if finding.locations:
            detail += " — " + ", ".join(f"`{safe_display_text(str(loc))}`" for loc in finding.locations[:3])
        census = _item_census(finding)
        if census:
            detail += f" — {census}"
        lines.append(f"| {finding.rule_id} | {status} | {finding.confidence:.0%} | {detail} |")
    lines.append("")
    lines.append(
        "> Statuses are detected signals from static analysis, not a certification of "
        "reproducibility. Suppression is a triage annotation and does not change scoring."
    )
    lines.append("")
    return "\n".join(lines)
