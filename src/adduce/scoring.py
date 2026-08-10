"""Explainable, category-weighted scoring.

Within each category, findings contribute ``status_value * rule_weight``
normalised by the total weight of applicable rules; category totals are then
combined using the profile's category weights. Not-applicable and unknown
findings are excluded entirely — a scikit-learn repository is never scored
against CUDA determinism, in either direction.

The result is never a mystery number: every category reports earned/possible
alongside the findings that moved it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .profiles import Profile
from .rules.base import Category, Finding


@dataclass
class CategoryScore:
    category: Category
    earned: float
    possible: float
    findings: list[Finding] = field(default_factory=list)

    @property
    def percentage(self) -> float:
        return 100.0 * self.earned / self.possible if self.possible else 0.0


@dataclass
class ScoreCard:
    total: float  # 0..100
    categories: list[CategoryScore]
    findings: list[Finding]
    profile_name: str
    tier: str
    #: Rules that reached a scoring verdict, against the rules considered.
    #: Not-applicable and unknown findings count only in the denominator.
    evaluated_rules: int = 0
    considered_rules: int = 0
    #: Physical lines across the source files the analyzer actually parsed.
    #: The substance the score rests on, and the input to ``rated``.
    analysable_lines: int = 0
    #: Whether there was enough parsed source for a tier to mean anything.
    rated: bool = True

    @property
    def coverage(self) -> float:
        """Percentage of considered rules that reached a scoring verdict."""
        if not self.considered_rules:
            return 0.0
        return 100.0 * self.evaluated_rules / self.considered_rules

    def to_dict(self) -> dict:
        return {
            "total": round(self.total, 1),
            "tier": self.tier,
            "profile": self.profile_name,
            # Additive. Existing consumers keep working; a reader that wants to
            # know how much the score rests on can now find out.
            "evidence_base": {
                "rated": self.rated,
                "evaluated_rules": self.evaluated_rules,
                "considered_rules": self.considered_rules,
                "coverage_percent": round(self.coverage, 1),
                "analysable_lines": self.analysable_lines,
            },
            "categories": [
                {
                    "category": c.category.value,
                    "earned": round(c.earned, 2),
                    "possible": round(c.possible, 2),
                    "percentage": round(c.percentage, 1),
                }
                for c in self.categories
            ],
            "findings": [f.to_dict() for f in self.findings],
        }


_TIERS: tuple[tuple[float, str], ...] = (
    (85.0, "Gold"),
    (70.0, "Silver"),
    (50.0, "Bronze"),
    (0.0, "Needs work"),
)

#: The tier reported when the repository carries too little parsed source for a
#: tier to be a statement about anything.
UNRATED_TIER = "Unrated (insufficient evidence)"

#: Physical lines of parsed source below which no tier is assigned.
#:
#: Most rules are assertions about code. Given no code, the ones that look for a
#: problem find none and pass, the ones that look for an artifact are satisfied
#: by its bare presence, and the weighted average of those passes reads as a
#: verdict. Measured on the corpus: a repository of plausible-looking but empty
#: files reached 72/100 and the "Silver" tier on 10 lines, while the smallest
#: real repository carries 1,220. Every value between 15 and 1,220 separates
#: every case measured, so the exact number here is not load-bearing; it is an
#: order of magnitude above the largest synthetic fixture and an order of
#: magnitude below the smallest real repository.
#:
#: This is a floor on whether anything can be said, not a defence against
#: deliberate gaming: padding a file defeats it, and is meant to.
MINIMUM_ANALYSABLE_LINES = 100


def tier_for(total: float) -> str:
    for threshold, name in _TIERS:
        if total >= threshold:
            return name
    return "Needs work"


def score(
    findings: list[Finding],
    profile: Profile,
    *,
    analysable_lines: int | None = None,
) -> ScoreCard:
    """Aggregate findings into an explainable scorecard.

    Suppressed findings still appear in the report (marked as suppressed)
    and retain their observed score. Suppression records an accepted exception;
    it does not turn absent evidence into evidence.

    ``analysable_lines`` is how much source the analyzer actually parsed. Below
    ``MINIMUM_ANALYSABLE_LINES`` the score is still computed and reported, but no
    tier is assigned: the number is real, and calling it Silver would be a claim
    about a repository that has not shown enough to support one. Passing ``None``
    leaves the card rated, so a caller that scores findings on their own — a
    plugin, or a test — is unaffected.
    """
    by_category: dict[Category, list[Finding]] = {}
    for finding in findings:
        by_category.setdefault(finding.category, []).append(finding)

    categories: list[CategoryScore] = []
    weighted_earned = 0.0
    weighted_possible = 0.0
    for category in Category:
        cat_findings = by_category.get(category, [])
        cat_weight = profile.category_weight(category)
        earned = 0.0
        possible = 0.0
        for finding in cat_findings:
            value = finding.status.score_value
            if value is None:  # not-applicable / unknown
                continue
            earned += value * finding.weight
            possible += finding.weight
        if possible == 0:
            continue  # nothing applicable in this category; exclude and renormalise
        categories.append(
            CategoryScore(
                category=category,
                earned=earned / possible * cat_weight,
                possible=cat_weight,
                findings=cat_findings,
            )
        )
        weighted_earned += earned / possible * cat_weight
        weighted_possible += cat_weight

    total = 100.0 * weighted_earned / weighted_possible if weighted_possible else 0.0
    rated = analysable_lines is None or analysable_lines >= MINIMUM_ANALYSABLE_LINES
    return ScoreCard(
        total=total,
        categories=categories,
        findings=findings,
        profile_name=profile.name,
        tier=tier_for(total) if rated else UNRATED_TIER,
        evaluated_rules=sum(1 for f in findings if f.status.score_value is not None),
        considered_rules=len(findings),
        analysable_lines=analysable_lines or 0,
        rated=rated,
    )


def top_fixes(card: ScoreCard, limit: int = 5) -> list[Finding]:
    """Findings ranked by the total-score points fixing them would buy."""
    total_possible = sum(c.possible for c in card.categories) or 1.0
    gains: list[tuple[float, Finding]] = []
    for cat in card.categories:
        applicable_weight = sum(
            f.weight for f in cat.findings if f.status.score_value is not None
        ) or 1.0
        for finding in cat.findings:
            value = finding.status.score_value
            if finding.suppressed or value is None or value >= 1.0:
                continue
            points = 100.0 * (1.0 - value) * finding.weight / applicable_weight * cat.possible / total_possible
            gains.append((points, finding))
    gains.sort(key=lambda pair: pair[0], reverse=True)
    return [finding for _, finding in gains[:limit]]
