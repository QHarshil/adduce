"""Scoring: normalisation, exclusion of inapplicable findings, and fix ranking."""

from __future__ import annotations

from adduce.profiles import load_profile
from adduce.rules.base import Category, Finding, Status
from adduce.scoring import (
    MINIMUM_ANALYSABLE_LINES,
    UNASSESSED_TIER,
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


def test_coverage_counts_assessed_findings_against_applicable_ones():
    """The denominator is applicability, not everything that returned a finding.

    This is a semantics change rather than a loosened assertion: the same
    fixture used to read 2/4, because a not-applicable check counted against
    coverage as though adduce had failed to answer it.
    """
    findings = [
        _finding("SCORED-PASS", Category.CODE_EXECUTION, Status.PASS, 5),
        _finding("SCORED-FAIL", Category.DATA, Status.FAIL, 5),
        _finding("EXCLUDED", Category.DETERMINISM, Status.NOT_APPLICABLE, 5),
        _finding("UNKNOWN", Category.DOCUMENTATION, Status.UNKNOWN, 5),
    ]

    card = score(findings, load_profile("default"), analysable_lines=1000)

    assert card.evaluated_rules == 2
    assert card.considered_rules == 4
    assert card.applicable_rules == 3
    assert card.unknown_rules == 1
    assert card.not_applicable_rules == 1
    assert card.coverage == 100.0 * 2 / 3
    assert card.to_dict()["evidence_base"]["coverage_percent"] == 66.7


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
        "applicable_rules": 2,
        "coverage_percent": 100.0,
        "analysable_lines": 10,
        "rules": {
            "assessed": 2,
            "unknown": 0,
            "not_applicable": 0,
            "skipped_inapplicable": 0,
        },
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
    # Every finding here is applicable, so the retained category is half the
    # coverage denominator and the unanswered half of it.
    assert card.evaluated_rules == 2
    assert card.considered_rules == 4
    assert card.applicable_rules == 4
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


def test_a_zero_weight_verdict_does_not_keep_its_category_as_unassessed():
    """Retention asks for applicable *and* unassessed, not applicable alone.

    Nothing constrains a rule's weight, so an out-of-tree pack can register one
    at 0. Its category reaches `possible == 0` while holding a verdict, and
    keeping it would render a FAIL as a question adduce never answered.
    """
    profile = load_profile("default")
    answered = score(
        [
            _finding("ZERO", Category.NOTEBOOK, Status.FAIL, 0),
            _finding("REAL", Category.DATA, Status.PASS, 3),
        ],
        profile,
    )
    assert [c.category for c in answered.categories] == [Category.DATA]

    # The control: an unanswered category is still kept, so the tightened
    # predicate has not disabled the retention path it guards.
    unanswered = score(
        [
            _finding("UNANSWERED", Category.NOTEBOOK, Status.UNKNOWN, 3),
            _finding("REAL", Category.DATA, Status.PASS, 3),
        ],
        profile,
    )
    assert [c.category for c in unanswered.categories] == [Category.DATA, Category.NOTEBOOK]

    # And a category holding both is kept, on the strength of the unanswered one.
    mixed = score(
        [
            _finding("ZERO", Category.NOTEBOOK, Status.FAIL, 0),
            _finding("UNANSWERED", Category.NOTEBOOK, Status.UNKNOWN, 3),
            _finding("REAL", Category.DATA, Status.PASS, 3),
        ],
        profile,
    )
    kept = [c for c in mixed.categories if c.category is Category.NOTEBOOK]
    assert len(kept) == 1
    assert kept[0].possible == 0


def test_top_fixes_ignores_a_retained_unassessed_category():
    findings = [
        _finding("A", Category.CODE_EXECUTION, Status.FAIL, 3),
        _finding("B", Category.NOTEBOOK, Status.UNKNOWN, 8),
    ]
    card = score(findings, load_profile("default"))
    assert [f.rule_id for f in top_fixes(card)] == ["A"]


# -- applicability against assessment ----------------------------------------
#
# UNKNOWN and NOT_APPLICABLE share a `None` quality value. Coverage depends on
# telling them apart, so the predicates are asserted over every member: a sixth
# status cannot be added without landing in this table.

_PREDICATES = [
    (Status.PASS, True, True),
    (Status.PARTIAL, True, True),
    (Status.FAIL, True, True),
    (Status.UNKNOWN, True, False),
    (Status.NOT_APPLICABLE, False, False),
]


def test_every_status_declares_applicability_and_assessment():
    assert {status for status, _, _ in _PREDICATES} == set(Status)
    for status, applicable, assessed in _PREDICATES:
        assert status.is_applicable is applicable, status
        assert status.is_assessed is assessed, status


def test_the_two_unvalued_statuses_are_distinguishable():
    """The point of the predicates: they differ where `score_value` cannot."""
    assert Status.UNKNOWN.score_value is None
    assert Status.NOT_APPLICABLE.score_value is None

    assert Status.UNKNOWN.is_applicable is True
    assert Status.UNKNOWN.is_assessed is False
    assert Status.NOT_APPLICABLE.is_applicable is False
    assert Status.NOT_APPLICABLE.is_assessed is False


def _four_outcomes() -> list[Finding]:
    return [
        _finding("ASSESSED", Category.CODE_EXECUTION, Status.PASS, 5),
        _finding("UNANSWERED", Category.DATA, Status.UNKNOWN, 5),
        _finding("OUT-OF-SCOPE", Category.DETERMINISM, Status.NOT_APPLICABLE, 5),
    ]


def test_the_rules_block_reports_all_four_outcomes():
    card = score(_four_outcomes(), load_profile("default"), skipped_inapplicable=9)

    assert card.to_dict()["evidence_base"]["rules"] == {
        "assessed": 1,
        "unknown": 1,
        "not_applicable": 1,
        "skipped_inapplicable": 9,
    }


def test_rules_skipped_before_evaluation_move_no_coverage_arithmetic():
    profile = load_profile("default")
    without = score(_four_outcomes(), profile)
    with_skips = score(_four_outcomes(), profile, skipped_inapplicable=9)

    assert with_skips.total == without.total
    assert with_skips.coverage == without.coverage
    assert with_skips.evaluated_rules == without.evaluated_rules
    assert with_skips.considered_rules == without.considered_rules
    assert with_skips.applicable_rules == without.applicable_rules

    base = without.to_dict()["evidence_base"]
    skipped = with_skips.to_dict()["evidence_base"]
    assert skipped.pop("rules")["skipped_inapplicable"] == 9
    assert base.pop("rules")["skipped_inapplicable"] == 0
    assert base == skipped


def test_a_card_that_assessed_nothing_has_no_score():
    for status in (Status.UNKNOWN, Status.NOT_APPLICABLE):
        card = score(
            [_finding("A", Category.CODE_EXECUTION, status, 5)],
            load_profile("default"),
            analysable_lines=1000,
        )

        assert card.total is None, status
        assert card.tier == UNASSESSED_TIER, status
        assert card.to_dict()["total"] is None, status


def test_a_card_that_failed_everything_scores_zero_rather_than_nothing():
    """A failing zero is a measurement; `None` is the absence of one."""
    card = score(
        [
            _finding("A", Category.CODE_EXECUTION, Status.FAIL, 5),
            _finding("B", Category.DATA, Status.FAIL, 5),
        ],
        load_profile("default"),
        analysable_lines=1000,
    )

    assert card.total == 0.0
    assert card.tier == "Needs work"
    assert card.to_dict()["total"] == 0.0
