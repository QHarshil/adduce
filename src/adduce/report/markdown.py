"""Markdown report: shareable in PRs, issues, and lab wikis."""

from __future__ import annotations

from ..engine import CheckResult
from ..rules.base import Status
from ..scoring import top_fixes

_STATUS_LABEL = {
    Status.PASS: "pass",
    Status.PARTIAL: "partial",
    Status.FAIL: "fail",
    Status.NOT_APPLICABLE: "n/a",
    Status.UNKNOWN: "unknown",
}


def render(result: CheckResult) -> str:
    card = result.card
    repo_name = result.repo.root.name
    commit = (result.repo.git.head_commit or "")[:7]
    lines: list[str] = []
    lines.append(f"# Reproducibility report — {repo_name}")
    lines.append("")
    subtitle = f"Score **{card.total:.0f}/100** ({card.tier}) · profile `{card.profile_name}`"
    if commit:
        subtitle += f" · commit `{commit}`"
    lines.append(subtitle)
    lines.append("")
    lines.append("| Category | Score | |")
    lines.append("|---|---:|---|")
    for cat in card.categories:
        bar = "" if cat.possible == 0 else f"{cat.percentage:.0f}%"
        lines.append(f"| {cat.category.value} | {cat.earned:.0f}/{cat.possible:.0f} | {bar} |")
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
        status = "ignored" if finding.suppressed else _STATUS_LABEL[finding.status]
        detail = finding.message.replace("|", "\\|")
        if finding.locations:
            detail += " — " + ", ".join(f"`{loc}`" for loc in finding.locations[:3])
        lines.append(f"| {finding.rule_id} | {status} | {finding.confidence:.0%} | {detail} |")
    lines.append("")
    lines.append(
        "> Statuses are detected signals from static analysis, not a certification of reproducibility."
    )
    lines.append("")
    return "\n".join(lines)
