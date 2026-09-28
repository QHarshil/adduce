"""What two synthetic papers draft: the metric vocabulary and duplicate clustering.

No rule reads a drafted claim, so these cases' entries in
``corpus/synthetic/expectations.yaml`` can only assert that the rules abstain.
This module asserts what the cases exist for, from the claims package directly.
"""

from __future__ import annotations

from pathlib import Path

from adduce.aeg.schema import ResolutionMethod
from adduce.claims import ClaimCluster, cluster_candidates, extract_candidates
from adduce.evidence import collect
from adduce.model import scan_repository

CASES = Path(__file__).resolve().parents[1] / "corpus" / "synthetic"


def _clusters(case: str) -> list[ClaimCluster]:
    return cluster_candidates(extract_candidates(collect(scan_repository(CASES / case))))


def test_each_vocabulary_header_names_its_own_metric_with_certainty() -> None:
    clusters = _clusters("synthetic_metric_vocabulary")
    assert {(c.metric, c.value) for c in clusters} == {
        ("bleu", 70.4),
        ("meteor", 46.8),
        ("cider", 2.53),
        ("ter", 0.31),
        ("rouge_1", 43.52),
        ("rouge_2", 21.55),
        ("rouge_l", 40.69),
        ("spearman", 88.7),
        ("matthews", 62.1),
    }
    assert all(c.method is ResolutionMethod.DIRECT_PARSE for c in clusters)
    assert all(c.confidence == 1.0 for c in clusters)


def test_three_measurements_sharing_one_value_stay_three_claims() -> None:
    clusters = _clusters("synthetic_coincident_values")
    shared = [c for c in clusters if c.value == 84.1]
    assert sorted((c.members[0].row_label, c.members[0].column_label) for c in shared) == [
        ("Ours", "Dev EM"),
        ("Ours", "Test EM"),
        ("Prior work", "Dev EM"),
    ]
    assert len(clusters) == 7


def test_a_result_restated_in_a_second_table_is_one_claim_with_both_locations() -> None:
    clusters = _clusters("synthetic_coincident_values")
    (restated,) = [c for c in clusters if len(c.members) > 1]
    assert {(m.row_label, m.column_label, m.value) for m in restated.members} == {
        ("Ours", "Dev EM", 84.1)
    }
    assert restated.restated
    assert len({m.location.line for m in restated.members}) == 2


def test_a_wrapped_spanning_header_names_every_column_it_covers() -> None:
    clusters = _clusters("synthetic_wrapped_table_header")
    assert sorted((c.metric, c.value) for c in clusters) == [
        ("accuracy", 79.2),
        ("accuracy", 81.4),
        ("f1", 88.0),
    ]
