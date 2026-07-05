"""Scoring: normalisation, exclusion of inapplicable findings, and fix ranking."""

from __future__ import annotations

from adduce.profiles import load_profile
from adduce.rules.base import Category, Finding, Status
from adduce.scoring import score, tier_for, top_fixes


def _finding(rule_id, category, status, weight, suppressed=False):
    return Finding(
        rule_id=rule_id,
        category=category,
        title=rule_id,
        status=status,
        confidence=0.8,
        message="",
        remediation="do the thing",
        weight=weight,
        suppressed=suppressed,
    )


def test_all_pass_scores_100():
    findings = [
        _finding("A", Category.CODE_EXECUTION, Status.PASS, 5),
        _finding("B", Category.DETERMINISM, Status.PASS, 8),
    ]
    card = score(findings, load_profile("default"))
    assert card.total == 100.0
    assert card.tier == "Gold"


def test_not_applicable_categories_are_renormalised():
    findings = [
        _finding("A", Category.CODE_EXECUTION, Status.PASS, 5),
        _finding("B", Category.DETERMINISM, Status.NOT_APPLICABLE, 8),
    ]
    card = score(findings, load_profile("default"))
    assert card.total == 100.0
    assert len(card.categories) == 1  # determinism excluded entirely


def test_partial_scores_half_weight():
    findings = [_finding("A", Category.CODE_EXECUTION, Status.PARTIAL, 5)]
    card = score(findings, load_profile("default"))
    assert card.total == 50.0


def test_suppressed_finding_scores_as_pass():
    findings = [
        _finding("A", Category.CODE_EXECUTION, Status.FAIL, 5, suppressed=True),
    ]
    card = score(findings, load_profile("default"))
    assert card.total == 100.0


def test_top_fixes_ranked_by_points_gained():
    findings = [
        _finding("BIG", Category.DETERMINISM, Status.FAIL, 8),
        _finding("SMALL", Category.ACCESS_LEGAL, Status.FAIL, 2),
        _finding("OK", Category.DETERMINISM, Status.PASS, 5),
    ]
    card = score(findings, load_profile("default"))
    fixes = top_fixes(card)
    assert [f.rule_id for f in fixes] == ["BIG", "SMALL"]


def test_top_fixes_exclude_suppressed():
    findings = [_finding("A", Category.CODE_EXECUTION, Status.FAIL, 5, suppressed=True)]
    card = score(findings, load_profile("default"))
    assert top_fixes(card) == []


def test_tiers():
    assert tier_for(90) == "Gold"
    assert tier_for(75) == "Silver"
    assert tier_for(55) == "Bronze"
    assert tier_for(20) == "Needs work"


def test_profiles_load_and_differ():
    default = load_profile("default")
    acm = load_profile("acm")
    assert default.weights != acm.weights
    assert sum(default.weights.values()) == 100
