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

    def to_dict(self) -> dict:
        return {
            "total": round(self.total, 1),
            "tier": self.tier,
            "profile": self.profile_name,
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


def tier_for(total: float) -> str:
    for threshold, name in _TIERS:
        if total >= threshold:
            return name
    return "Needs work"


def score(findings: list[Finding], profile: Profile) -> ScoreCard:
    """Aggregate findings into an explainable scorecard.

    Suppressed findings still appear in the report (marked as suppressed)
    but score as full passes: the author has explicitly accepted the state.
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
            value = 1.0 if finding.suppressed else finding.status.score_value
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
    return ScoreCard(
        total=total,
        categories=categories,
        findings=findings,
        profile_name=profile.name,
        tier=tier_for(total),
    )


def top_fixes(card: ScoreCard, limit: int = 5) -> list[Finding]:
    """Findings ranked by the total-score points fixing them would buy."""
    total_possible = sum(c.possible for c in card.categories) or 1.0
    gains: list[tuple[float, Finding]] = []
    for cat in card.categories:
        applicable_weight = sum(
            f.weight for f in cat.findings if f.status.score_value is not None and not f.suppressed
        ) or 1.0
        for finding in cat.findings:
            value = finding.status.score_value
            if finding.suppressed or value is None or value >= 1.0:
                continue
            points = 100.0 * (1.0 - value) * finding.weight / applicable_weight * cat.possible / total_possible
            gains.append((points, finding))
    gains.sort(key=lambda pair: pair[0], reverse=True)
    return [finding for _, finding in gains[:limit]]
