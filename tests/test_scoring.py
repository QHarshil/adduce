"""Scoring: normalisation, exclusion of inapplicable findings, and fix ranking."""

from __future__ import annotations

from adduce.profiles import load_profile
from adduce.rules.base import Category, Finding, Status
from adduce.scoring import (
    MINIMUM_ANALYSABLE_LINES,
    UNRATED_TIER,
    score,
    tier_for,
    top_fixes,
)


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


def test_suppressed_finding_retains_observed_score():
    findings = [
        _finding("A", Category.CODE_EXECUTION, Status.FAIL, 5, suppressed=True),
    ]
    card = score(findings, load_profile("default"))
    assert card.total == 0.0


def test_suppressed_partial_finding_retains_partial_score():
    findings = [
        _finding("A", Category.CODE_EXECUTION, Status.PARTIAL, 5, suppressed=True),
    ]
    card = score(findings, load_profile("default"))
    assert card.total == 50.0


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


def test_top_fix_gain_uses_same_denominator_as_score():
    findings = [
        _finding("SMALL-GAP", Category.CODE_EXECUTION, Status.FAIL, 1),
        _finding("SUPPRESSED", Category.CODE_EXECUTION, Status.PASS, 9, suppressed=True),
        _finding("LARGER-GAP", Category.DATA, Status.FAIL, 1),
    ]

    card = score(findings, load_profile("default"))

    assert [finding.rule_id for finding in top_fixes(card)] == [
        "LARGER-GAP",
        "SMALL-GAP",
    ]


def test_tiers():
    assert tier_for(90) == "Gold"
    assert tier_for(75) == "Silver"
    assert tier_for(55) == "Bronze"
    assert tier_for(20) == "Needs work"


# -- the evidence base a score rests on -------------------------------------
#
# A repository of plausible but empty files reached 72/100 and "Silver" on ten
# lines of source: rules that look for a problem find none and pass, rules that
# look for an artifact are satisfied by its bare presence, and the weighted
# average of those passes reads as a verdict. The score is still reported; the
# tier is not.


def _passing_findings() -> list[Finding]:
    return [
        _finding("A", Category.CODE_EXECUTION, Status.PASS, 5),
        _finding("B", Category.DATA, Status.PASS, 5),
    ]


def test_a_repository_with_too_little_source_gets_no_tier():
    card = score(
        _passing_findings(),
        load_profile("default"),
        analysable_lines=MINIMUM_ANALYSABLE_LINES - 1,
    )

    assert card.rated is False
    assert card.tier == UNRATED_TIER
    # The score itself is untouched: it is a real measurement of what ran.
    assert card.total == 100.0


def test_the_same_findings_over_enough_source_are_tiered_normally():
    card = score(
        _passing_findings(),
        load_profile("default"),
        analysable_lines=MINIMUM_ANALYSABLE_LINES,
    )

    assert card.rated is True
    assert card.tier == "Gold"
    assert card.total == 100.0


def test_a_caller_that_does_not_measure_source_still_gets_a_tier():
    """Plugins and tests score findings directly; they must not be penalised."""
    card = score(_passing_findings(), load_profile("default"))

    assert card.rated is True
    assert card.tier == "Gold"
    assert card.analysable_lines == 0


def test_coverage_counts_only_findings_that_reached_a_verdict():
    findings = [
        _finding("SCORED-PASS", Category.CODE_EXECUTION, Status.PASS, 5),
        _finding("SCORED-FAIL", Category.DATA, Status.FAIL, 5),
        _finding("EXCLUDED", Category.DETERMINISM, Status.NOT_APPLICABLE, 5),
        _finding("UNKNOWN", Category.DOCUMENTATION, Status.UNKNOWN, 5),
    ]

    card = score(findings, load_profile("default"), analysable_lines=1000)

    assert card.evaluated_rules == 2
    assert card.considered_rules == 4
    assert card.coverage == 50.0


def test_the_evidence_base_is_reported_additively():
    card = score(_passing_findings(), load_profile("default"), analysable_lines=10)
    payload = card.to_dict()

    # Existing keys are untouched, so current consumers keep working.
    assert payload["total"] == 100.0
    assert set(payload) >= {"total", "tier", "profile", "categories", "findings"}
    assert payload["evidence_base"] == {
        "rated": False,
        "evaluated_rules": 2,
        "considered_rules": 2,
        "coverage_percent": 100.0,
        "analysable_lines": 10,
    }


def test_profiles_load_and_differ():
    default = load_profile("default")
    acm = load_profile("acm")
    assert default.weights != acm.weights
    assert sum(default.weights.values()) == 100

def test_an_all_unknown_category_is_kept_with_nothing_assessed():
    """Applicable and unanswered is not the same as inapplicable.

    The category stays on the card so a reader can see the question was asked
    and not answered, but it contributes no weight, so the total is unmoved.
    """
    findings = [
        _finding("A", Category.CODE_EXECUTION, Status.PASS, 3),
        _finding("B", Category.CODE_EXECUTION, Status.FAIL, 3),
        _finding("C", Category.NOTEBOOK, Status.UNKNOWN, 3),
        _finding("D", Category.NOTEBOOK, Status.UNKNOWN, 3),
    ]
    card = score(findings, load_profile("default"))

    kept = [c for c in card.categories if c.category is Category.NOTEBOOK]
    assert len(kept) == 1
    assert kept[0].possible == 0
    assert kept[0].earned == 0
    assert kept[0].percentage == 0.0
    assert [f.rule_id for f in kept[0].findings] == ["C", "D"]


def test_keeping_an_unassessed_category_moves_no_number():
    """The retained category must not reach the weight accumulation."""
    scored = [
        _finding("A", Category.CODE_EXECUTION, Status.PASS, 3),
        _finding("B", Category.CODE_EXECUTION, Status.FAIL, 3),
    ]
    with_unknown = scored + [
        _finding("C", Category.NOTEBOOK, Status.UNKNOWN, 3),
        _finding("D", Category.NOTEBOOK, Status.UNKNOWN, 3),
    ]
    profile = load_profile("default")
    base = score(scored, profile)
    card = score(with_unknown, profile)

    assert card.total == base.total
    assert card.tier == base.tier
    # Coverage keeps today's returned-findings denominator; PR 1 changes it.
    assert card.evaluated_rules == 2
    assert card.considered_rules == 4
    assert card.coverage == 50.0


def test_an_all_not_applicable_category_is_still_dropped():
    """The control: this fix must not impose the applicability semantics early."""
    findings = [
        _finding("A", Category.CODE_EXECUTION, Status.PASS, 3),
        _finding("B", Category.CHECKPOINT, Status.NOT_APPLICABLE, 3),
        _finding("C", Category.CHECKPOINT, Status.NOT_APPLICABLE, 3),
    ]
    card = score(findings, load_profile("default"))
    assert all(c.category is not Category.CHECKPOINT for c in card.categories)


def test_a_mixed_unknown_and_not_applicable_category_is_kept():
    """One unanswered check is enough to make the category worth showing."""
    findings = [
        _finding("A", Category.CODE_EXECUTION, Status.PASS, 3),
        _finding("B", Category.NOTEBOOK, Status.NOT_APPLICABLE, 3),
        _finding("C", Category.NOTEBOOK, Status.UNKNOWN, 3),
    ]
    card = score(findings, load_profile("default"))
    kept = [c for c in card.categories if c.category is Category.NOTEBOOK]
    assert len(kept) == 1
    assert kept[0].possible == 0


def test_top_fixes_ignores_a_retained_unassessed_category():
    findings = [
        _finding("A", Category.CODE_EXECUTION, Status.FAIL, 3),
        _finding("B", Category.NOTEBOOK, Status.UNKNOWN, 8),
    ]
    card = score(findings, load_profile("default"))
    assert [f.rule_id for f in top_fixes(card)] == ["A"]
