"""Report framings: the same findings, three audiences.

- author (default): friendly, fix-oriented.
- reviewer: skeptical; surfaces what could not be verified and what is ambiguous.
- ae-chair: badge eligibility, blocking issues, reviewer-burden headline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .rules.base import Finding, Status
from .scoring import ScoreCard


class Mode(str, Enum):
    AUTHOR = "author"
    REVIEWER = "reviewer"
    AE_CHAIR = "ae-chair"


@dataclass
class BadgeEligibility:
    label: str
    eligible: bool
    blocking: list[str] = field(default_factory=list)


def unverifiable_findings(card: ScoreCard) -> list[Finding]:
    """What a skeptical reviewer starts from: unknowns and low-confidence passes."""
    unknowns = [f for f in card.findings if f.status is Status.UNKNOWN and not f.suppressed]
    weak_passes = [
        f
        for f in card.findings
        if f.status is Status.PASS and f.confidence < 0.6 and not f.suppressed
    ]
    return unknowns + weak_passes


def _rule_status(card: ScoreCard, rule_id: str) -> Status | None:
    for finding in card.findings:
        if finding.rule_id == rule_id:
            return finding.status
    return None


def badge_eligibility(card: ScoreCard) -> list[BadgeEligibility]:
    """Likely eligibility for ACM artifact badges, from static evidence only.

    Results Reproduced/Replicated require independent execution and are
    deliberately never assessed here.
    """
    assessments: list[BadgeEligibility] = []

    # Artifacts Available: public, archived, persistent identifier.
    available_blockers = []
    if _rule_status(card, "R-ARC-001") in (Status.FAIL, Status.PARTIAL):
        available_blockers.append("no archival DOI/SWHID (R-ARC-001)")
    if _rule_status(card, "R-LIC-001") is Status.FAIL:
        available_blockers.append("no license (R-LIC-001)")
    assessments.append(
        BadgeEligibility("ACM Artifacts Available", eligible=not available_blockers, blocking=available_blockers)
    )

    # Artifacts Evaluated — Functional: documented, complete, exercisable.
    functional_requirements = {
        "R-EXEC-001": "no discoverable entrypoint",
        "R-EXEC-002": "no one-command execution path",
        "R-DOC-001": "README missing core sections",
        "R-ENV-001": "dependencies not declared/pinned",
        "R-DATA-002": "no data acquisition path",
    }
    functional_blockers = [
        f"{reason} ({rule_id})"
        for rule_id, reason in functional_requirements.items()
        if _rule_status(card, rule_id) is Status.FAIL
    ]
    assessments.append(
        BadgeEligibility(
            "ACM Artifacts Evaluated — Functional", eligible=not functional_blockers, blocking=functional_blockers
        )
    )

    # Reusable: Functional plus strong docs/env norms.
    reusable_blockers = list(functional_blockers)
    for rule_id, reason in (
        ("R-ENV-003", "no container/environment definition"),
        ("R-DOC-003", "expected results not stated"),
        ("R-LIC-002", "no citation metadata"),
    ):
        if _rule_status(card, rule_id) is Status.FAIL:
            reusable_blockers.append(f"{reason} ({rule_id})")
    assessments.append(
        BadgeEligibility(
            "ACM Artifacts Evaluated — Reusable", eligible=not reusable_blockers, blocking=reusable_blockers
        )
    )
    return assessments


def blocking_issues(card: ScoreCard) -> list[Finding]:
    """High-weight failures an AE chair would treat as gates."""
    return sorted(
        (
            f
            for f in card.findings
            if f.status is Status.FAIL and not f.suppressed and f.weight >= 3
        ),
        key=lambda f: -f.weight,
    )
