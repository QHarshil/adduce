"""Claim extraction: stages 1 and 2.

Every failure mode asserted here was confirmed to bite before the test was
written — each one fails if the guard it covers is removed from the source.
"""

from __future__ import annotations

import pytest

from adduce.aeg.schema import CERTAIN_METHODS, ResolutionMethod
from adduce.claims import (
    CandidateSource,
    ClaimCandidate,
    ClaimLocation,
    certain,
    cluster_candidates,
    from_latex_prose,
    from_latex_tables,
    from_markdown_table,
)
from adduce.evidence.latex import PaperValue, TableCell
from adduce.naming import METRIC_PATTERNS, canonical_metric

# --- the vocabulary move -------------------------------------------------


def test_latex_metric_vocabulary_is_unchanged_by_the_move_to_naming():
    """Moving a vocabulary is exactly the edit that silently drops an alias.

    Pinned as a literal, including key order: dict order decides which pattern
    is tried first, so a reordering could change which metric a sentence
    matches even with identical content.
    """
    from adduce.evidence.latex import _METRIC_KEYWORDS

    expected = {
        "accuracy": ("accuracy", "acc\\.", "top-1", "top-5"),
        "f1": ("f1", "f1-score", "f-score", "macro-f1", "micro-f1"),
        "bleu": ("bleu",),
        "rouge": ("rouge", "rouge-l", "rouge-1", "rouge-2"),
        "ndcg": ("ndcg",),
        "map": ("\\bmap\\b", "mean average precision"),
        "mrr": ("mrr", "mean reciprocal rank"),
        "auc": ("auc", "auroc", "roc-auc"),
        "precision": ("precision@", "\\bprecision\\b"),
        "recall": ("recall@", "\\brecall\\b"),
        "perplexity": ("perplexity", "\\bppl\\b"),
        "wer": ("\\bwer\\b", "word error rate"),
        "mse": ("\\bmse\\b", "mean squared error"),
        "rmse": ("\\brmse\\b",),
        "mae": ("\\bmae\\b", "mean absolute error"),
        "iou": ("\\biou\\b", "\\bmiou\\b"),
        "dice": ("dice",),
        "exact_match": ("exact match", "\\bem\\b"),
    }
    moved = _METRIC_KEYWORDS
    assert moved == expected
    assert list(moved) == list(expected)
    assert moved is METRIC_PATTERNS


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Accuracy", "accuracy"),
        ("Top-1", "accuracy"),
        ("Acc.", "accuracy"),
        ("Accuracy (%)", "accuracy"),
        ("F1 ↑", "f1"),
        ("**BLEU**", "bleu"),
        ("val loss", "val_loss"),
        ("train loss", "train_loss"),
        ("model", None),
        ("params", None),
        ("", None),
    ],
)
def test_canonical_metric_reads_real_table_headers(header, expected):
    assert canonical_metric(header) == expected


def test_train_and_validation_loss_stay_distinct_metrics():
    """A training loss and a validation loss are two claims, not one restated."""
    assert canonical_metric("train loss") != canonical_metric("val loss")


# --- candidate honesty ---------------------------------------------------


def test_confidence_one_requires_a_certain_method():
    with pytest.raises(ValueError, match="certain method"):
        ClaimCandidate(
            metric="accuracy",
            value=92.4,
            source=CandidateSource.LATEX_PROSE,
            location=ClaimLocation("paper.tex", 1),
            method=ResolutionMethod.LEXICAL_MATCH,
            confidence=1.0,
            text="92.4",
        )


def test_prose_candidates_are_never_certain():
    """A regex over a sentence is an inference, whatever the number says."""
    values = [PaperValue("metric", "accuracy", 92.4, "accuracy of 92.4", "main.tex", 12)]
    (candidate,) = from_latex_prose(values)
    assert candidate.method is ResolutionMethod.LEXICAL_MATCH
    assert candidate.method not in CERTAIN_METHODS
    assert candidate.confidence < 1.0


def test_latex_table_cells_become_candidates_and_are_not_discarded():
    """These are parsed today and read by nothing; that is the defect."""
    cells = [
        TableCell(0, "Ours", "Accuracy", 92.4, "main.tex", 40),
        TableCell(0, "Baseline", "Accuracy", 88.1, "main.tex", 41),
    ]
    candidates = from_latex_tables(cells)
    assert [c.value for c in candidates] == [92.4, 88.1]
    assert all(c.metric == "accuracy" for c in candidates)
    assert all(c.method is ResolutionMethod.DIRECT_PARSE for c in candidates)
    assert [c.row_label for c in candidates] == ["Ours", "Baseline"]


def test_latex_cell_under_an_unknown_column_is_kept_but_not_certain():
    cells = [TableCell(0, "Ours", "Throughput", 1200.0, "main.tex", 9)]
    (candidate,) = from_latex_tables(cells)
    assert candidate.metric == "throughput"
    assert candidate.method is ResolutionMethod.LEXICAL_MATCH
    assert candidate.confidence < 1.0


# --- markdown tables -----------------------------------------------------

_NANOGPT_TABLE = """\
and observe the following losses on train and val:

| model | params | train loss | val loss |
| ------| ------ | ---------- | -------- |
| gpt2 | 124M         | 3.11  | 3.12     |
| gpt2-medium | 350M  | 2.85  | 2.84     |
"""


def test_markdown_results_table_yields_one_candidate_per_metric_cell():
    candidates = from_markdown_table(_NANOGPT_TABLE, "README.md")
    assert len(candidates) == 4
    assert {c.metric for c in candidates} == {"train_loss", "val_loss"}
    assert [c.value for c in candidates] == [3.11, 3.12, 2.85, 2.84]
    assert all(c.method is ResolutionMethod.DIRECT_PARSE for c in candidates)
    assert [c.row_label for c in candidates] == ["gpt2", "gpt2", "gpt2-medium", "gpt2-medium"]


def test_markdown_candidate_locations_point_at_the_row_that_states_the_number():
    candidates = from_markdown_table(_NANOGPT_TABLE, "README.md")
    lines = _NANOGPT_TABLE.splitlines()
    for candidate in candidates:
        row = lines[candidate.location.line - 1]
        assert str(candidate.value) in row, f"{candidate.location} does not state {candidate.value}"


def test_a_table_naming_no_metric_yields_nothing():
    """The guard that separates an extractor from a number scraper."""
    table = """\
| argument | default | shape |
| -------- | ------- | ----- |
| hidden   | 768     | 512   |
"""
    assert from_markdown_table(table, "docs/api.md") == []


def test_pipes_without_a_delimiter_row_are_not_a_table():
    """Metric-named headers deliberately, so the delimiter is what is on trial.

    Two earlier versions of this test passed for the wrong reason. The first
    used non-metric headers, so the metric guard rejected the table before the
    delimiter rule was consulted. The second had only two rows, so dropping the
    rule made line 2 the delimiter and left no body to read — nothing was
    extracted either way. Three rows is the shape where the rule is the only
    thing standing between the input and a candidate.
    """
    text = "accuracy | f1\n92.4 | 88.1\n91.0 | 87.5\n"
    assert from_markdown_table(text, "README.md") == []

    delimited = "accuracy | f1\n--- | ---\n92.4 | 88.1\n91.0 | 87.5\n"
    assert len(from_markdown_table(delimited, "README.md")) == 4


@pytest.mark.parametrize(
    ("cell", "value", "units"),
    [
        ("92.4", 92.4, None),
        ("92.4%", 92.4, "%"),
        ("**92.4**", 92.4, None),
        ("92.4 ± 0.3", 92.4, None),
        ("1,234", 1234.0, None),
        ("1.2e-4", 0.00012, None),
        ("-0.5", -0.5, None),
    ],
)
def test_numeric_cell_shapes_that_really_occur(cell, value, units):
    table = f"| model | accuracy |\n| --- | --- |\n| ours | {cell} |\n"
    (candidate,) = from_markdown_table(table, "README.md")
    assert candidate.value == value
    assert candidate.units == units


@pytest.mark.parametrize("cell", ["n/a", "-", "TBD", "see below", "", "1.2.3"])
def test_non_numeric_cells_state_no_claim(cell):
    table = f"| model | accuracy |\n| --- | --- |\n| ours | {cell} |\n"
    assert from_markdown_table(table, "README.md") == []


def test_two_tables_in_one_document_are_both_read():
    text = _NANOGPT_TABLE + "\n" + "| model | bleu |\n| --- | --- |\n| ours | 41.2 |\n"
    candidates = from_markdown_table(text, "README.md")
    assert len(candidates) == 5
    assert candidates[-1].metric == "bleu"


# --- clustering ----------------------------------------------------------


def _candidate(metric, value, path="a.tex", line=1, source=CandidateSource.LATEX_TABLE):
    method = (
        ResolutionMethod.DIRECT_PARSE
        if source is not CandidateSource.LATEX_PROSE
        else ResolutionMethod.LEXICAL_MATCH
    )
    return ClaimCandidate(
        metric=metric,
        value=value,
        source=source,
        location=ClaimLocation(path, line),
        method=method,
        confidence=1.0 if method in CERTAIN_METHODS else 0.5,
        text=f"{metric} {value}",
    )


def test_the_same_number_stated_three_ways_is_one_claim():
    """Abstract, results table and README are three statements of one claim."""
    candidates = [
        _candidate("accuracy", 92.4, "main.tex", 10, CandidateSource.LATEX_PROSE),
        _candidate("accuracy", 92.4, "main.tex", 40, CandidateSource.LATEX_TABLE),
        _candidate("accuracy", 92.4, "README.md", 7, CandidateSource.MARKDOWN_TABLE),
    ]
    (cluster,) = cluster_candidates(candidates)
    assert len(cluster.members) == 3
    assert cluster.restated
    assert cluster.sources == (
        CandidateSource.LATEX_PROSE,
        CandidateSource.LATEX_TABLE,
        CandidateSource.MARKDOWN_TABLE,
    )


def test_a_cluster_reports_the_strongest_method_any_member_carries():
    candidates = [
        _candidate("accuracy", 92.4, "main.tex", 10, CandidateSource.LATEX_PROSE),
        _candidate("accuracy", 92.4, "main.tex", 40, CandidateSource.LATEX_TABLE),
    ]
    (cluster,) = cluster_candidates(candidates)
    assert cluster.method is ResolutionMethod.DIRECT_PARSE
    assert cluster.confidence == 1.0
    assert certain([cluster]) == [cluster]


def test_rounding_is_agreement_and_the_precise_value_represents_the_cluster():
    candidates = [_candidate("accuracy", 92.4), _candidate("accuracy", 92.41, line=2)]
    (cluster,) = cluster_candidates(candidates)
    assert len(cluster.members) == 2
    assert cluster.value == 92.41


def test_different_values_of_one_metric_stay_separate_claims():
    candidates = [_candidate("accuracy", 92.4), _candidate("accuracy", 88.1, line=2)]
    clusters = cluster_candidates(candidates)
    assert len(clusters) == 2
    assert [c.value for c in clusters] == [88.1, 92.4]


def test_percent_and_fraction_are_not_silently_reconciled():
    """0.924 against 92.4 is numeric reconciliation, a later stage with its
    own resolution method. Doing it here would let an inference pass as a parse."""
    clusters = cluster_candidates([_candidate("accuracy", 0.924), _candidate("accuracy", 92.4)])
    assert len(clusters) == 2


def test_clustering_is_independent_of_input_order():
    candidates = [
        _candidate("f1", 88.0, "a.tex", 3),
        _candidate("accuracy", 92.4, "b.tex", 1),
        _candidate("accuracy", 92.4, "c.md", 9, CandidateSource.MARKDOWN_TABLE),
        _candidate("bleu", 41.2, "d.tex", 2),
    ]
    forward = cluster_candidates(candidates)
    backward = cluster_candidates(list(reversed(candidates)))

    def shape(clusters):
        # Member order too, not just cluster order: the trailing sort makes the
        # cluster sequence deterministic on its own, so comparing only that
        # passes even with the input ordering removed.
        return [
            (c.logical_id, [str(m.location) for m in c.members]) for c in clusters
        ]

    assert shape(forward) == shape(backward)


def test_logical_id_carries_no_line_number():
    """Identity must survive a reformat; lines are data, not identity."""
    a = cluster_candidates([_candidate("accuracy", 92.4, "main.tex", 10)])[0]
    b = cluster_candidates([_candidate("accuracy", 92.4, "main.tex", 999)])[0]
    assert a.logical_id == b.logical_id
    assert "10" not in a.logical_id.replace("92.4", "")


def test_extraction_never_truncates():
    """The path this replaces dropped everything past the tenth claim in silence."""
    candidates = [_candidate("accuracy", float(i), line=i) for i in range(1, 26)]
    clusters = cluster_candidates(candidates)
    assert len(clusters) == 25
