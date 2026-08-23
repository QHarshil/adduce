"""Claim-extraction recall and precision: matching, refusals, and the --src swap.

Every fixture in the integration section runs the real measurement path
(``scan_repository`` -> ``evidence.collect`` -> ``collect_latex`` ->
``scaffold_manifest``) through a subprocess, exactly as ``bench/dev/recall.py``
does for a real pair. The unit tests further up construct
:class:`~bench.dev.recall.ExtractedClaim`/:class:`~bench.dev.recall.Label`
directly, because the property under test is the matching engine itself, not
the extractor.
"""

from __future__ import annotations

import io
import json
import subprocess
import tarfile
from pathlib import Path
from typing import Any

import pytest
from bench.dev import recall

# -- fixtures shared by the integration tests --------------------------------

_PAPER_TEX = r"""\documentclass{article}
\title{Fixture Paper}
\begin{document}
We report accuracy in the table below.

\begin{tabular}{lc}
Model & Accuracy \\
Ours & 92.4 \\
Baseline & 88.1 \\
Threshold & 79.5 \\
\end{tabular}

We report recall in the table below.

\begin{tabular}{lc}
Model & Recall \\
Ours & 81.0 \\
Baseline & 77.0 \\
\end{tabular}

\end{document}
"""


def _build_pair_tree(root: Path) -> tuple[Path, Path]:
    """A tiny code tree and a tiny paper tree with a real, parseable results table.

    Laid out as ``<root>/code`` and ``<root>/paper/src``, exactly the shape
    ``fetch.py`` gives one roster row -- so this doubles as a roster-layout
    fixture.

    The table yields five real claims: accuracy in {92.4 (Ours), 88.1
    (Baseline), 79.5 (Threshold)}, recall in {81.0 (Ours), 77.0 (Baseline)}.
    Each header is also picked up once by the keyword-proximity prose
    extractor, but it always names the same (metric, value) as that table's
    own first row, so it merges away in clustering rather than appearing as a
    sixth claim -- confirmed directly against this exact fixture before this
    file was written.
    """
    code = root / "code"
    code.mkdir(parents=True)
    (code / "README.md").write_text("# Fixture\n", encoding="utf-8")
    (code / "train.py").write_text("import torch\n", encoding="utf-8")

    paper = root / "paper" / "src"
    paper.mkdir(parents=True)
    (paper / "paper.tex").write_text(_PAPER_TEX, encoding="utf-8")
    return code, paper


def _labels_payload() -> dict[str, Any]:
    """Six labels against the fixture's five claims.

    L1/L2 are own results that the table states; L3 is the same accuracy
    metric quoted as a baseline; L4 relabels the Baseline recall cell as a
    hyperparameter (contrived, but it is a real extracted (metric, value) so
    it can actually be consumed as `wrong_kind`); L5 names a metric ("f1")
    nothing in the fixture states, so it can only be `missed`; L6 restates
    L3's value (88.1) under a metric ("bleu") nothing extracts, so it is
    `missed` under the metric+value rule but recoverable value-only.
    """
    return {
        "pair_id": "fixture-pair",
        "sampled": True,
        "sampling_seed": 42,
        "frame": {"Table 1": 3, "Table 2": 2},
        "labels": [
            {
                "id": "L1", "metric": "accuracy", "value": 92.4, "units": "%",
                "dataset": "Fixture", "split": "test", "role": "result",
                "is_own_result": True, "confident": True,
                "location": {"kind": "table", "label": "Table 1", "row": "Ours"}, "notes": "",
            },
            {
                "id": "L2", "metric": "recall", "value": 81.0, "units": "%",
                "dataset": "Fixture", "split": "test", "role": "result",
                "is_own_result": True, "confident": True,
                "location": {"kind": "table", "label": "Table 2", "row": "Ours"}, "notes": "",
            },
            {
                "id": "L3", "metric": "accuracy", "value": 88.1, "units": "%",
                "dataset": "Fixture", "split": "test", "role": "result",
                "is_own_result": False, "confident": True,
                "location": {"kind": "table", "label": "Table 1", "row": "Baseline"},
                "notes": "quoted from prior work",
            },
            {
                "id": "L4", "metric": "recall", "value": 77.0, "role": "hyperparameter",
                "is_own_result": False, "confident": True,
                "location": {"kind": "table", "label": "Table 2", "row": "Baseline"},
                "notes": "contrived for coverage: relabelled as a threshold hyperparameter",
            },
            {
                "id": "L5", "metric": "f1", "value": 95.0, "units": "%",
                "dataset": "Fixture", "split": "test", "role": "result",
                "is_own_result": True, "confident": True,
                "location": {"kind": "abstract"}, "notes": "not extracted -- exercises `missed`",
            },
            {
                "id": "L6", "metric": "bleu", "value": 88.1, "dataset": "Fixture",
                "split": "test", "role": "result", "is_own_result": True, "confident": True,
                "location": {"kind": "table", "label": "Table 1"},
                "notes": "same value as L3 under a different metric -- the value-only diagnostic",
            },
        ],
    }


def _verifications_payload() -> dict[str, Any]:
    """Adjudicates the fixture's five real extractions, and only those.

    2 real_own_result, 1 baseline, 2 hyperparameter -- adjudicated = 5,
    precision = 2/5. Every entry names an extraction the fixture really
    produces, which is what ``evaluate_precision`` now requires: an
    adjudication of something the extractor does not emit is a verdict about a
    set that no longer exists. ``not_in_paper``, ``in_repo_not_paper`` and
    ``unclear`` are exercised against ``compute_precision`` directly instead,
    where a constructed verdict has no live extraction to correspond to.
    """
    return {
        "pair_id": "fixture-pair",
        "verifications": [
            {
                "extraction": {"metric": "accuracy", "value": 92.4, "where": "paper.tex:6"},
                "verdict": "real_own_result", "notes": "",
            },
            {
                "extraction": {"metric": "recall", "value": 81.0, "where": "paper.tex:15"},
                "verdict": "real_own_result", "notes": "",
            },
            {
                "extraction": {"metric": "accuracy", "value": 88.1, "where": "paper.tex:6"},
                "verdict": "baseline", "notes": "quoted from prior work",
            },
            {
                "extraction": {"metric": "recall", "value": 77.0, "where": "paper.tex:15"},
                "verdict": "hyperparameter", "notes": "actually a threshold",
            },
            {
                "extraction": {"metric": "accuracy", "value": 79.5, "where": "paper.tex:6"},
                "verdict": "hyperparameter", "notes": "the decision threshold, not a result",
            },
        ],
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _label(**overrides: Any) -> recall.Label:
    defaults: dict[str, Any] = {
        "id": "L1", "metric": "accuracy", "value": 92.4, "role": "result", "is_own_result": True,
    }
    defaults.update(overrides)
    return recall.Label(**defaults)


def _frame(*labels: recall.Label, sampled: bool = False) -> recall.LabelFrame:
    return recall.LabelFrame(
        pair_id="p", sampled=sampled, sampling_seed=None, frame={}, labels=tuple(labels)
    )


def _claim(**overrides: Any) -> recall.ExtractedClaim:
    """One extraction. Full confidence by default, as the fixture tree really is.

    Every cell of ``_PAPER_TEX`` sits under a header the vocabulary names, so
    the real measurement path reports all five at ``direct_parse``/1.0 --
    verified against the worker, not assumed.
    """
    defaults: dict[str, Any] = {
        "metric": "accuracy",
        "value": 92.4,
        "where": "a.tex:1",
        "text": "",
        "confidence": 1.0,
        "resolution_method": "direct_parse",
    }
    defaults.update(overrides)
    return recall.ExtractedClaim(**defaults)


# -- classify_recall: a label the vocabulary cannot name ---------------------


def test_a_label_whose_metric_does_not_canonicalise_is_reported_separately():
    """It cannot match anything, so calling it a plain miss blames the extractor.

    Matching requires both sides to canonicalise. A label metric the vocabulary
    has no name for is unsatisfiable by construction -- the failure is the
    vocabulary's, not the extractor's, and the two must not read as one number.
    """
    # Deliberately fictional, so adding a real metric to the vocabulary later
    # cannot quietly turn this test green for the wrong reason.
    frame = _frame(_label(metric="widget-score", value=59.0), sampled=True)

    diagnostic = recall.classify_recall([_claim(metric="widget-score", value=59.0)], frame)

    assert diagnostic.recall_denominator == 1
    assert diagnostic.unnameable_labels == 1
    assert diagnostic.matched == 0


def test_a_nameable_label_is_not_counted_as_unnameable():
    frame = _frame(_label(metric="Top-1", value=92.4), sampled=True)

    diagnostic = recall.classify_recall([_claim(value=92.4)], frame)

    assert diagnostic.unnameable_labels == 0
    assert diagnostic.matched == 1


# -- classify_recall: one-to-one assignment ----------------------------------


def test_one_to_one_assignment_does_not_double_count_an_identical_extraction():
    """Two identical extractions against one label: matched=1, not 2."""
    claims = [_claim(where="a.tex:1"), _claim(where="a.tex:2")]
    frame = _frame(_label(), sampled=True)

    diagnostic = recall.classify_recall(claims, frame)

    assert diagnostic.matched == 1
    assert diagnostic.recall_denominator == 1
    assert diagnostic.recall == 1.0
    # The second, identical extraction is not silently dropped: with no other
    # label left to consume it, and "accuracy" established as a real result
    # metric by L1, it lands in `unknown`, not a fabricated second match.
    assert diagnostic.unknown == 1
    assert diagnostic.false_positive == 0


def test_classify_recall_does_not_depend_on_input_order():
    claims = [_claim(where="a.tex:1", value=92.4), _claim(where="a.tex:2", value=92.4)]
    frame = _frame(_label(), sampled=True)

    forward = recall.classify_recall(claims, frame)
    backward = recall.classify_recall(list(reversed(claims)), frame)

    assert forward == backward


# -- classify_recall: the metric-and-value trap ------------------------------


def test_right_value_wrong_metric_is_not_matched_but_is_counted_value_only():
    """The exact trap that made a mutation vacuous in task 7.11b.

    Where every candidate happens to name the right metric, matching on value
    alone reaches the same answer as matching on both, so a fixture built
    that way cannot tell whether the metric is checked at all. This states
    the right number (92.4) under the wrong metric ("loss" against an
    accuracy label), which only the value-only pass may recover.
    """
    claims = [_claim(metric="loss", value=92.4, where="results/loss.csv:1")]
    frame = _frame(_label(metric="accuracy", value=92.4))

    diagnostic = recall.classify_recall(claims, frame)

    assert diagnostic.matched == 0
    assert diagnostic.missed == 1
    assert diagnostic.value_only_matches == 1
    # "loss" was never established as a result metric by any label, so the
    # leftover extraction is a false positive, sampled or not.
    assert diagnostic.false_positive == 1
    assert diagnostic.unknown == 0


# -- classify_recall: baseline and wrong-kind labels -------------------------


def test_baseline_label_matched_by_an_extraction_is_baseline_extracted_not_matched():
    claims = [_claim(value=88.1)]
    frame = _frame(_label(value=88.1, is_own_result=False))

    diagnostic = recall.classify_recall(claims, frame)

    assert diagnostic.matched == 0
    assert diagnostic.baseline_extracted == 1
    # is_own_result=False labels never enter the denominator.
    assert diagnostic.recall_denominator == 0
    assert diagnostic.recall is None


def test_hyperparameter_label_matched_by_an_extraction_is_wrong_kind_not_matched():
    claims = [_claim(value=50.0)]
    frame = _frame(_label(value=50.0, role="hyperparameter", is_own_result=False))

    diagnostic = recall.classify_recall(claims, frame)

    assert diagnostic.matched == 0
    assert diagnostic.wrong_kind == 1
    assert diagnostic.recall_denominator == 0


# -- classify_recall: missed, unconfident, and the leftover classes ---------


def test_missed_label_has_no_matching_extraction():
    frame = _frame(_label(metric="f1", value=95.0))

    diagnostic = recall.classify_recall([], frame)

    assert diagnostic.missed == 1
    assert diagnostic.recall == 0.0


def test_unconfident_label_is_excluded_from_matching_and_the_denominator():
    """An ambiguous reading is retained but excluded, so it can never become a miss."""
    claims = [_claim(value=92.4)]
    frame = _frame(_label(value=92.4, confident=False), sampled=True)

    diagnostic = recall.classify_recall(claims, frame)

    assert diagnostic.matched == 0
    assert diagnostic.missed == 0
    assert diagnostic.recall_denominator == 0
    assert diagnostic.recall is None
    assert diagnostic.excluded_unconfident == 1
    # The claim itself is not silently dropped -- it falls to the leftover
    # classification instead of being credited to the excluded label.
    assert diagnostic.unknown + diagnostic.false_positive == 1


def test_leftover_extraction_is_false_positive_when_the_frame_is_labelled_completely():
    claims = [_claim(value=50.0)]  # no label anywhere names this value
    frame = _frame(_label(value=92.4), sampled=False)

    diagnostic = recall.classify_recall(claims, frame)

    assert diagnostic.false_positive == 1
    assert diagnostic.unknown == 0


def test_leftover_extraction_of_an_established_result_metric_is_unknown_when_sampled():
    claims = [_claim(value=50.0)]  # a different accuracy value than L1's
    frame = _frame(_label(value=92.4), sampled=True)

    diagnostic = recall.classify_recall(claims, frame)

    assert diagnostic.unknown == 1
    assert diagnostic.false_positive == 0


def test_a_claim_with_no_value_cannot_be_matched_or_counted_as_a_false_positive():
    """The README-fallback placeholder claim: no value, so nothing to compare."""
    claims = [_claim(metric=None, value=None, text="fill in the metric and value")]
    frame = _frame(_label())

    diagnostic = recall.classify_recall(claims, frame)

    assert diagnostic.missed == 1
    assert diagnostic.unknown == 0
    assert diagnostic.false_positive == 0


# -- compute_precision --------------------------------------------------------


def _verification(**overrides: Any) -> recall.Verification:
    defaults: dict[str, Any] = {"metric": "accuracy", "value": 92.4, "verdict": "real_own_result"}
    defaults.update(overrides)
    return recall.Verification(**defaults)


def test_precision_counts_real_own_result_over_adjudicated():
    verifications = recall.VerificationSet(
        pair_id="p",
        verifications=(
            _verification(),
            _verification(metric="recall", value=81.0),
            _verification(value=88.1, verdict="baseline"),
        ),
    )

    result = recall.compute_precision(verifications)

    assert result.real_own_result == 2
    assert result.adjudicated == 3
    assert result.precision == pytest.approx(2 / 3)


def test_a_claim_the_repository_states_but_the_paper_does_not_is_not_a_false_positive():
    """A README results table is a claim the artifact really makes.

    Counting it against precision calls a real repository claim a fabrication;
    counting it for precision credits a paper-scoped adjudication with something
    outside the paper. It is excluded from the denominator and reported, exactly
    like ``unclear``. Measured on bert, folding these six into ``not_in_paper``
    reported precision as 20/36 rather than 20/30.
    """
    verifications = recall.VerificationSet(
        pair_id="p",
        verifications=(
            _verification(),
            _verification(value=88.1, verdict="in_repo_not_paper"),
            _verification(value=79.5, verdict="in_repo_not_paper"),
        ),
    )

    result = recall.compute_precision(verifications)

    assert result.in_repo_not_paper == 2
    assert result.adjudicated == 1
    assert result.precision == pytest.approx(1.0)


def test_a_genuine_fabrication_still_counts_against_precision():
    """The new verdict must not become a way to excuse a real false positive."""
    verifications = recall.VerificationSet(
        pair_id="p",
        verifications=(_verification(), _verification(value=3.0, verdict="not_in_paper")),
    )

    result = recall.compute_precision(verifications)

    assert result.not_in_paper == 1
    assert result.adjudicated == 2
    assert result.precision == pytest.approx(0.5)


def test_unclear_verdict_is_excluded_from_the_denominator_and_reported():
    verifications = recall.VerificationSet(
        pair_id="p",
        verifications=(_verification(), _verification(metric="f1", value=50.0, verdict="unclear")),
    )

    result = recall.compute_precision(verifications)

    assert result.unclear == 1
    assert result.adjudicated == 1
    assert result.real_own_result == 1
    assert result.precision == 1.0


def test_not_in_paper_verdict_counts_against_precision():
    verifications = recall.VerificationSet(
        pair_id="p",
        verifications=(_verification(), _verification(metric="bleu", value=10.0, verdict="not_in_paper")),
    )

    result = recall.compute_precision(verifications)

    assert result.not_in_paper == 1
    assert result.adjudicated == 2
    assert result.precision == pytest.approx(0.5)


def test_precision_is_none_when_nothing_is_adjudicated():
    verifications = recall.VerificationSet(
        pair_id="p", verifications=(_verification(metric="f1", value=50.0, verdict="unclear"),)
    )

    result = recall.compute_precision(verifications)

    assert result.adjudicated == 0
    assert result.precision is None


# -- high-confidence false positives ------------------------------------------


def test_a_false_positive_extracted_at_full_confidence_is_counted():
    """The §17 criterion: not merely wrong, but confidently wrong.

    Both verdicts are false positives against a paper-scoped adjudication. Only
    the one the extractor produced at 1.0 is a high-confidence one; the other
    was already stated as an inference.
    """
    verifications = recall.VerificationSet(
        pair_id="p",
        verifications=(
            _verification(value=88.1, verdict="baseline"),
            _verification(value=79.5, verdict="not_in_paper"),
        ),
    )
    claims = [
        _claim(value=88.1, confidence=1.0, resolution_method="direct_parse"),
        _claim(value=79.5, confidence=0.5, resolution_method="lexical_match"),
    ]

    result = recall.compute_precision(verifications, claims)

    assert result.adjudicated == 2
    assert result.high_confidence_false_positives == 1
    assert result.unjoined_false_positives == 0


def test_a_correct_extraction_is_never_a_high_confidence_false_positive():
    """Confidence is not the thing being counted -- being wrong is."""
    verifications = recall.VerificationSet(
        pair_id="p", verifications=(_verification(verdict="real_own_result"),)
    )

    result = recall.compute_precision(verifications, [_claim(confidence=1.0)])

    assert result.precision == 1.0
    assert result.high_confidence_false_positives == 0


def test_a_verdict_outside_the_precision_denominator_is_not_a_false_positive():
    """``unclear`` was not decided and ``in_repo_not_paper`` is a real repository claim.

    Both are excluded from the denominator, so counting either here would
    contradict the rate reported beside it.
    """
    verifications = recall.VerificationSet(
        pair_id="p",
        verifications=(
            _verification(metric="f1", value=50.0, verdict="unclear"),
            _verification(value=88.1, verdict="in_repo_not_paper"),
        ),
    )
    claims = [_claim(metric="f1", value=50.0), _claim(value=88.1)]

    result = recall.compute_precision(verifications, claims)

    assert result.adjudicated == 0
    assert result.high_confidence_false_positives == 0


def test_a_false_positive_whose_confidence_disagrees_across_its_key_is_not_guessed():
    """Two extractions of one ``(metric, value)`` disagreeing cannot be assigned.

    Which extraction the human adjudicated is undecidable from a join the
    verification file carries no confidence for, and counting it either way
    states a guess as a measurement.
    """
    verifications = recall.VerificationSet(
        pair_id="p", verifications=(_verification(value=88.1, verdict="baseline"),)
    )
    claims = [
        _claim(value=88.1, where="a.tex:1", confidence=1.0),
        _claim(value=88.1, where="b.tex:2", confidence=0.5),
    ]

    result = recall.compute_precision(verifications, claims)

    assert result.high_confidence_false_positives == 0
    assert result.unjoined_false_positives == 1


def test_a_false_positive_from_a_tree_that_states_no_confidence_is_not_guessed():
    """An unknown confidence is not a low one -- the --src retroactive case."""
    verifications = recall.VerificationSet(
        pair_id="p", verifications=(_verification(value=88.1, verdict="baseline"),)
    )

    result = recall.compute_precision(
        verifications, [_claim(value=88.1, confidence=None, resolution_method=None)]
    )

    assert result.high_confidence_false_positives == 0
    assert result.unjoined_false_positives == 1


def test_omitting_the_extraction_leaves_the_counts_unmeasured_rather_than_zero():
    """No extraction to join against is undefined, exactly as an undefined rate is."""
    verifications = recall.VerificationSet(
        pair_id="p", verifications=(_verification(value=88.1, verdict="baseline"),)
    )

    result = recall.compute_precision(verifications)

    assert result.precision == 0.0
    assert result.high_confidence_false_positives is None
    assert result.unjoined_false_positives is None


# -- verification coverage: precision only over the extractions produced now --


def _extraction(*claims: recall.ExtractedClaim) -> dict[str, Any]:
    """An extraction worker payload, in the shape ``evaluate_precision`` reads."""
    return {
        "available": True,
        "claims": [
            {
                "metric": c.metric,
                "value": c.value,
                "where": c.where,
                "text": c.text,
                "confidence": c.confidence,
                "resolution_method": c.resolution_method,
                "row_label": c.row_label,
                "column_label": c.column_label,
            }
            for c in claims
        ],
    }


def _fixture_extraction(*extra: recall.ExtractedClaim) -> dict[str, Any]:
    """The five extractions ``_verifications_payload`` adjudicates, plus any extra.

    Locators included, and they are the ones the real measurement path emits
    for ``_PAPER_TEX`` -- verified against the worker, not assumed -- so these
    unit fixtures exercise the located key rather than falling back to
    ``(metric, value)`` on a placeholder that names no real line.
    """
    return _extraction(
        _claim(metric="accuracy", value=92.4, where="paper.tex:6"),
        _claim(metric="recall", value=81.0, where="paper.tex:15"),
        _claim(metric="accuracy", value=88.1, where="paper.tex:6"),
        _claim(metric="recall", value=77.0, where="paper.tex:15"),
        _claim(metric="accuracy", value=79.5, where="paper.tex:6"),
        *extra,
    )


def _verifications_file(tmp_path: Path, *, drop: int | None = None) -> Path:
    payload = _verifications_payload()
    if drop is not None:
        del payload["verifications"][drop]
    path = tmp_path / "verifications.json"
    _write_json(path, payload)
    return path


def test_precision_is_reported_when_every_extraction_carries_a_verdict(tmp_path):
    report = recall.evaluate_precision(_verifications_file(tmp_path), _fixture_extraction())

    assert report.available is True
    assert report.result is not None
    assert report.result.precision == pytest.approx(2 / 5)
    assert report.coverage == recall.VerificationCoverage(
        extractions=5, verdicts=5, unadjudicated=0, stale=0, location_fallbacks=0, label_fallbacks=5
    )


def test_the_high_confidence_false_positive_count_reaches_the_json_report(tmp_path):
    """A diagnostic no reader can see answers nothing.

    The fixture's five extractions are all ``direct_parse``/1.0, so its three
    false positives (one baseline, two hyperparameters) are all confident.
    """
    report = recall.evaluate_precision(_verifications_file(tmp_path), _fixture_extraction())
    pair = recall.PairReport(
        pair_id="p",
        recall=recall.RecallReport(available=False, reason="no labels"),
        precision=report,
    )

    payload = recall.build_report([pair])

    result = payload["results"][0]["precision"]["result"]
    assert result["precision"] == pytest.approx(2 / 5)
    assert result["high_confidence_false_positives"] == 3
    assert result["unjoined_false_positives"] == 0


def test_an_extraction_with_no_verdict_makes_precision_unavailable(tmp_path):
    """The barlowtwins failure: the extractor moved on and the file did not.

    Tallying the file alone kept reporting 27/58 while the extractor produced
    61 extractions -- a rate over a set that no longer existed. An extraction
    nobody adjudicated is never guessed at; the pair reports its counts and no
    number.
    """
    extraction = _fixture_extraction(_claim(metric="accuracy", value=65.3))

    report = recall.evaluate_precision(_verifications_file(tmp_path), extraction)

    assert report.available is False
    assert report.result is None
    assert report.coverage == recall.VerificationCoverage(
        extractions=6, verdicts=5, unadjudicated=1, stale=0, location_fallbacks=0, label_fallbacks=5
    )
    assert "6 extractions" in report.reason
    assert "5 verdicts" in report.reason
    assert "1 unadjudicated" in report.reason
    assert "0 stale" in report.reason
    assert "0 matched with their labels dropped" in report.reason


def test_a_verdict_matching_no_extraction_makes_precision_unavailable(tmp_path):
    """The other direction: an adjudication of something the extractor dropped."""
    extraction = _extraction(
        _claim(metric="accuracy", value=92.4, where="paper.tex:6"),
        _claim(metric="recall", value=81.0, where="paper.tex:15"),
        _claim(metric="accuracy", value=88.1, where="paper.tex:6"),
        _claim(metric="recall", value=77.0, where="paper.tex:15"),
    )

    report = recall.evaluate_precision(_verifications_file(tmp_path), extraction)

    assert report.available is False
    assert report.result is None
    assert report.coverage == recall.VerificationCoverage(
        extractions=4, verdicts=5, unadjudicated=0, stale=1, location_fallbacks=0, label_fallbacks=4
    )
    assert "4 extractions" in report.reason
    assert "1 stale" in report.reason


def test_a_verdict_recorded_under_another_metric_name_is_stale(tmp_path):
    """The vocabulary split is a real extractor change, not a rename to absorb.

    ``ap50`` and ``ap75`` used to canonicalise onto ``map``; separating them
    changed which metric each extraction reports. A verdict carrying the old
    name adjudicated a different extraction, so it is stale and its extraction
    is unadjudicated -- both, not neither.
    """
    _write_json(
        tmp_path / "verifications.json",
        {
            "pair_id": "p",
            "verifications": [
                {"extraction": {"metric": "map", "value": 42.0}, "verdict": "real_own_result"}
            ],
        },
    )

    report = recall.evaluate_precision(
        tmp_path / "verifications.json", _extraction(_claim(metric="ap50", value=42.0))
    )

    assert report.available is False
    assert report.coverage == recall.VerificationCoverage(
        extractions=1, verdicts=1, unadjudicated=1, stale=1, location_fallbacks=0, label_fallbacks=0
    )


def test_an_extraction_whose_locator_moved_is_still_the_adjudicated_extraction():
    """Measured on convnext: twelve mIoU figures printed in both paper and README.

    Which of the two locators survives clustering moved with an extractor
    change that left every number untouched, and all twelve verdicts read
    ``real_own_result`` noting that both sources state the value. Keying
    coverage on ``where`` would refuse a precision number over an adjudication
    that is still exactly right.
    """
    verifications = recall.VerificationSet(
        pair_id="p",
        verifications=(
            _verification(
                metric="iou", value=46.0, where="semantic_segmentation/README.md:18"
            ),
        ),
    )

    coverage = recall.verification_coverage(
        [_claim(metric="iou", value=46.0, where="main.tex:496")], verifications
    )

    assert coverage.corresponds is True
    assert coverage == recall.VerificationCoverage(
        extractions=1, verdicts=1, unadjudicated=0, stale=0, location_fallbacks=1, label_fallbacks=1
    )


def test_a_claim_with_no_value_is_not_an_extraction_awaiting_a_verdict():
    """The README-fallback placeholder asserts no number, so none can be adjudicated.

    ``Verification`` cannot represent it -- its value must be a number -- so
    counting it would leave precision permanently unavailable for any pair whose
    extraction includes one, with no adjudication able to fix it.
    """
    verifications = recall.VerificationSet(pair_id="p", verifications=(_verification(),))

    coverage = recall.verification_coverage(
        [_claim(), _claim(metric=None, value=None, text="fill in the metric and value")],
        verifications,
    )

    assert coverage.corresponds is True
    assert coverage.extractions == 1


# -- the matching key: a normalised locator, and what it decides --------------


def test_a_locator_rooted_above_the_measured_paper_still_matches_its_extraction():
    """The recorded trap, and why the root is recovered per file rather than assumed.

    detr's verdicts were recorded from a paper root one level above the one
    measured here, so matching the locator raw read all 144 of them as both
    unadjudicated and stale. A repository README carries the same path on
    both sides and must not be disturbed by the repair, so the two are
    reconciled in one pass and neither needs a fallback.
    """
    claims = [
        _claim(metric="map", value=42.0, where="experiments.tex:402"),
        _claim(metric="accuracy", value=91.2, where="object_detection/README.md:12"),
    ]
    verifications = recall.VerificationSet(
        pair_id="p",
        verifications=(
            _verification(metric="map", value=42.0, where="src/experiments.tex:402"),
            _verification(metric="accuracy", value=91.2, where="object_detection/README.md:12"),
        ),
    )

    alignment = recall.align_verdicts(claims, verifications)

    assert alignment.matched == {0: 0, 1: 1}
    assert alignment.fallbacks == frozenset()


def test_two_files_sharing_a_basename_at_one_line_are_not_collapsed_onto_it():
    """Measured on convnext: the basename is not injective over its own verdicts.

    ``object_detection/README.md:18`` and ``semantic_segmentation/README.md:18``
    state the same mIoU at the same line, so reducing a locator to its
    basename makes the two verdicts interchangeable. They are recorded here in
    the reverse order, so an assignment that dropped the directory would swap
    them and read each verdict against the other's extraction.
    """
    claims = [
        _claim(metric="iou", value=46.0, where="object_detection/README.md:18"),
        _claim(metric="iou", value=46.0, where="semantic_segmentation/README.md:18"),
    ]
    verifications = recall.VerificationSet(
        pair_id="p",
        verifications=(
            _verification(metric="iou", value=46.0, where="semantic_segmentation/README.md:18"),
            _verification(metric="iou", value=46.0, where="object_detection/README.md:18"),
        ),
    )

    alignment = recall.align_verdicts(claims, verifications)

    assert alignment.matched == {0: 1, 1: 0}
    assert alignment.fallbacks == frozenset()


def test_a_locator_two_extraction_paths_could_answer_is_not_resolved_to_either():
    """Ambiguity falls back to the weaker key rather than picking a path.

    ``guide/docs/README.md`` ends in both extraction paths, and both state the
    same number, so resolving it to either would name a row on nothing but the
    order the candidates happened to arrive in. The verdict is still matched --
    on ``(metric, value)``, which is what the fallback is for -- and the match
    is reported as one the locator did not decide.
    """
    claims = [
        _claim(metric="accuracy", value=90.0, where="README.md:3"),
        _claim(metric="accuracy", value=90.0, where="docs/README.md:3"),
    ]
    verifications = recall.VerificationSet(
        pair_id="p",
        verifications=(_verification(value=90.0, where="guide/docs/README.md:3"),),
    )

    alignment = recall.align_verdicts(claims, verifications)

    assert alignment.matched == {0: 0}
    assert alignment.fallbacks == frozenset({0})


def test_a_path_ending_in_another_is_reconciled_only_at_a_component_boundary():
    """``submain.tex`` is not ``main.tex`` under some other root."""
    claims = [_claim(metric="accuracy", value=90.0, where="main.tex:3")]
    verifications = recall.VerificationSet(
        pair_id="p", verifications=(_verification(value=90.0, where="submain.tex:3"),)
    )

    alignment = recall.align_verdicts(claims, verifications)

    assert alignment.fallbacks == frozenset({0})


def test_a_where_that_is_not_a_file_and_line_is_no_locator_at_all():
    """``where`` is free text and the README-fallback claim really does put prose in it."""
    claims = [_claim(metric="accuracy", value=90.0, where="README results table")]
    verifications = recall.VerificationSet(
        pair_id="p", verifications=(_verification(value=90.0, where="README results table"),)
    )

    alignment = recall.align_verdicts(claims, verifications)

    assert alignment.matched == {0: 0}
    assert alignment.fallbacks == frozenset({0})


def _repeated_value() -> tuple[list[recall.ExtractedClaim], recall.VerificationSet]:
    """One value printed in two tables, adjudicated differently in each.

    This is what repairing clustering's global de-duplication produces, and it
    is the reason the key had to be strengthened first: extractions are unique
    on ``(metric, value)`` today only because clustering merges on exactly
    that key. The verdicts are recorded in the reverse order of the
    extractions, so no positional accident can pass for an assignment, and the
    two extractions carry different confidences so a wrong assignment is
    visible in the precision counts as well as in the alignment.
    """
    claims = [
        _claim(metric="accuracy", value=82.9, where="main.tex:449", confidence=1.0),
        _claim(metric="accuracy", value=82.9, where="main.tex:683", confidence=0.5),
    ]
    verifications = recall.VerificationSet(
        pair_id="p",
        verifications=(
            _verification(value=82.9, where="src/main.tex:683", verdict="baseline"),
            _verification(value=82.9, where="src/main.tex:449", verdict="real_own_result"),
        ),
    )
    return claims, verifications


def test_a_repeated_metric_value_is_undecidable_on_the_old_key_and_decided_on_the_new():
    """Why this change exists, asserted rather than argued.

    Under ``(metric, value)`` both extractions and both verdicts collapse onto
    a single entry, so a multiset over that key holds nothing that could tell
    the two apart and any assignment built on it is arbitrary. The normalised
    locator separates them and gives each verdict its own extraction.
    """
    claims, verifications = _repeated_value()

    assert {(claim.metric, claim.value) for claim in claims} == {("accuracy", 82.9)}
    assert {(v.metric, v.value) for v in verifications.verifications} == {("accuracy", 82.9)}

    alignment = recall.align_verdicts(claims, verifications)

    assert alignment.matched == {0: 1, 1: 0}
    assert alignment.fallbacks == frozenset()
    assert alignment.unmatched_verdicts == ()
    assert alignment.unmatched_claims == ()


def test_staleness_over_a_repeated_metric_value_names_the_row_rather_than_a_count():
    """A multiset excess says "one stale"; it cannot say which one.

    With only the second extraction surviving, the verdict that adjudicated
    the first is identified by position, so the repair is a decision about a
    named row rather than arithmetic over a key.
    """
    claims, verifications = _repeated_value()
    surviving = [claims[1]]

    alignment = recall.align_verdicts(surviving, verifications)
    coverage = recall.verification_coverage(surviving, verifications)

    assert alignment.matched == {0: 0}
    assert alignment.unmatched_verdicts == (1,)
    assert verifications.verifications[1].where == "src/main.tex:449"
    assert coverage.stale == 1
    assert coverage.location_fallbacks == 0


def test_a_repeated_metric_value_no_longer_leaves_its_confidence_undecidable():
    """``unjoined`` is reachable in fewer cases, and the locator is what decides.

    Two extractions of one ``(metric, value)`` disagreeing on confidence were
    undecidable from the old join and counted as ``unjoined`` rather than
    guessed. The locator resolves which one the human read, and moving the
    baseline verdict onto the other locator -- changing nothing else -- flips
    the answer, so the count follows the locator and not the fixture's luck.
    """
    claims, verifications = _repeated_value()

    result = recall.compute_precision(verifications, claims)

    assert result.high_confidence_false_positives == 0
    assert result.unjoined_false_positives == 0

    mirrored = recall.VerificationSet(
        pair_id="p",
        verifications=(
            _verification(value=82.9, where="src/main.tex:449", verdict="baseline"),
            _verification(value=82.9, where="src/main.tex:683", verdict="real_own_result"),
        ),
    )

    flipped = recall.compute_precision(mirrored, claims)

    assert flipped.high_confidence_false_positives == 1
    assert flipped.unjoined_false_positives == 0


# -- the cell labels: two measurements at one value in one table ---------------


def _cell_pair() -> tuple[list[recall.ExtractedClaim], recall.VerificationSet]:
    """One value stated twice in one table, adjudicated differently per cell.

    Both of the collisions found while auditing the baseline demotion have this
    shape: bert prints 88.5 as both R.M. Reader's test F1 and BERT-BASE's dev
    F1, and convnext ties 15.01 GFLOPs between a cited ResNet-200 and its own
    enhanced recipe. Every cell of one ``tabular`` records the line the
    environment opens on, so both members carry the *same* locator -- the
    labels are the only thing that separates them, which is why the located key
    cannot and the audit had to be done by hand.

    The quoted figure is the one extracted at full confidence here, so it is a
    confident false positive and the labels are what finds it. The verdicts are
    recorded in the reverse order of the extractions, so no positional accident
    can pass for an assignment.
    """
    claims = [
        _claim(
            metric="f1",
            value=88.5,
            where="squad_tab.tex:6",
            row_label="R.M. Reader (Ensemble)",
            column_label="Test F1",
            confidence=1.0,
        ),
        _claim(
            metric="f1",
            value=88.5,
            where="squad_tab.tex:6",
            row_label="BERT_ BASE (Single)",
            column_label="Dev F1",
            confidence=0.5,
            resolution_method="lexical_match",
        ),
    ]
    verifications = recall.VerificationSet(
        pair_id="p",
        verifications=(
            _verification(
                metric="f1",
                value=88.5,
                where="src/squad_tab.tex:6",
                row_label="BERT_ BASE (Single)",
                column_label="Dev F1",
                verdict="real_own_result",
            ),
            _verification(
                metric="f1",
                value=88.5,
                where="src/squad_tab.tex:6",
                row_label="R.M. Reader (Ensemble)",
                column_label="Test F1",
                verdict="baseline",
            ),
        ),
    )
    return claims, verifications


def _unlabelled(verification: recall.Verification) -> recall.Verification:
    """The same verdict as a file written before labels were recorded holds it."""
    return recall.Verification(
        metric=verification.metric,
        value=verification.value,
        verdict=verification.verdict,
        where=verification.where,
        notes=verification.notes,
    )


def test_two_cells_of_one_table_are_undecidable_on_the_locator_and_decided_by_their_labels():
    """Why the labels joined the key, asserted rather than argued.

    ``(metric, value, where)`` holds one entry for both extractions and both
    verdicts, so nothing in it could tell the two apart and any assignment
    built on it is arbitrary. The labels separate them and give each verdict
    its own extraction, with no fallback of either kind.
    """
    claims, verifications = _cell_pair()

    assert {(c.metric, c.value, c.where) for c in claims} == {("f1", 88.5, "squad_tab.tex:6")}
    assert {(v.metric, v.value, v.where) for v in verifications.verifications} == {
        ("f1", 88.5, "src/squad_tab.tex:6")
    }

    alignment = recall.align_verdicts(claims, verifications)

    assert alignment.matched == {0: 1, 1: 0}
    assert alignment.fallbacks == frozenset()
    assert alignment.label_fallbacks == frozenset()
    assert alignment.unmatched_verdicts == ()
    assert alignment.unmatched_claims == ()


def test_a_confident_false_positive_in_a_cell_pair_is_found_by_its_labels():
    """The count follows the labels, not the order the verdicts were written in.

    Under the locator alone the two extractions disagree on confidence, so the
    answer was undecidable and counted as ``unjoined`` rather than guessed.
    Exchanging the two verdicts between the labels -- changing nothing else --
    moves the confident extraction from the quoted figure to the paper's own,
    and the count follows.
    """
    claims, verifications = _cell_pair()

    result = recall.compute_precision(verifications, claims)

    assert result.high_confidence_false_positives == 1
    assert result.unjoined_false_positives == 0

    exchanged = recall.VerificationSet(
        pair_id="p",
        verifications=(
            _verification(
                metric="f1", value=88.5, where="src/squad_tab.tex:6",
                row_label="R.M. Reader (Ensemble)", column_label="Test F1",
                verdict="real_own_result",
            ),
            _verification(
                metric="f1", value=88.5, where="src/squad_tab.tex:6",
                row_label="BERT_ BASE (Single)", column_label="Dev F1",
                verdict="baseline",
            ),
        ),
    )

    flipped = recall.compute_precision(exchanged, claims)

    assert flipped.high_confidence_false_positives == 0
    assert flipped.unjoined_false_positives == 0

    # The same two verdicts as a file written before labels were recorded holds
    # them: one entry, two confidences, no way to decide which was read.
    stripped = recall.VerificationSet(
        pair_id="p",
        verifications=tuple(_unlabelled(v) for v in verifications.verifications),
    )

    old_key = recall.compute_precision(stripped, claims)

    assert old_key.high_confidence_false_positives == 0
    assert old_key.unjoined_false_positives == 1


def test_a_verdict_recording_no_labels_matches_as_it_did_and_the_fallback_is_counted():
    """No verification file records a label yet, so this is every match they make.

    The verdict is still placed -- refusing it would throw away an adjudication
    for supplying no field that existed when it was written -- and how far the
    identity rests on the weaker key is stated rather than hidden.
    """
    claims, verifications = _cell_pair()
    stripped = recall.VerificationSet(
        pair_id="p", verifications=(_unlabelled(verifications.verifications[0]),)
    )

    alignment = recall.align_verdicts(claims, stripped)
    coverage = recall.verification_coverage(claims, stripped)

    assert alignment.matched == {0: 0}
    assert alignment.fallbacks == frozenset()
    assert alignment.label_fallbacks == frozenset({0})
    assert coverage.label_fallbacks == 1
    assert coverage.location_fallbacks == 0
    # It supplied no label, so it lost none: the two counts are separate
    # quantities and a file recording none must never register a degradation.
    assert alignment.label_degradations == frozenset()
    assert coverage.label_degradations == 0


def test_a_verdict_whose_label_no_extraction_states_degrades_instead_of_going_stale():
    """The label falls back the way the locator does, and the fall is counted.

    A recorded label that agrees with no live extraction used to end the search,
    so the verdict went stale and the file stopped corresponding -- losing a
    match the locator alone had made, over a label a human may simply have
    transcribed differently. It now degrades to its locator's group, and
    ``label_degradations`` says so: the identity of that one verdict rests on
    the locator, exactly as ``location_fallbacks`` says of a verdict whose file
    moved. What it buys is that one label the extractor renamed no longer costs
    a whole pair its precision figure.
    """
    claims, verifications = _cell_pair()
    renamed = recall.VerificationSet(
        pair_id="p",
        verifications=(
            _verification(
                metric="f1", value=88.5, where="src/squad_tab.tex:6",
                row_label="BERT_ LARGE (Single)", column_label="Dev F1",
                verdict="real_own_result",
            ),
        ),
    )

    alignment = recall.align_verdicts(claims, renamed)
    coverage = recall.verification_coverage(claims, renamed)

    assert alignment.matched == {0: 0}
    assert alignment.label_degradations == frozenset({0})
    assert alignment.fallbacks == frozenset()
    assert alignment.label_fallbacks == frozenset()
    assert coverage.stale == 0
    assert coverage.label_degradations == 1
    assert coverage.label_fallbacks == 0


def test_a_degraded_verdict_cannot_take_the_cell_another_verdict_names_exactly():
    """The objection to the fallback, answered by when it runs rather than by refusing.

    The labels are dropped only after every verdict has been offered its
    narrowed pool, so the one cell of the pair that a verdict still names
    exactly is already taken. The degraded verdict gets the remainder -- which
    is the assignment the locator alone would have made for it -- and only it is
    counted as degraded.
    """
    claims, verifications = _cell_pair()
    half_renamed = recall.VerificationSet(
        pair_id="p",
        verifications=(
            _verification(
                metric="f1", value=88.5, where="src/squad_tab.tex:6",
                row_label="BERT_ LARGE (Single)", column_label="Dev F1",
                verdict="not_in_paper",
            ),
            verifications.verifications[1],
        ),
    )

    alignment = recall.align_verdicts(claims, half_renamed)

    assert alignment.matched == {1: 0, 0: 1}
    assert alignment.label_degradations == frozenset({0})
    assert recall.verification_coverage(claims, half_renamed).corresponds is True


def test_a_moved_locator_is_given_up_before_a_recorded_label_is():
    """Which field of the key falls first, asserted rather than argued.

    A locator that reconciles with nothing says the number moved in the source,
    which is routine and says nothing about which cell a human read. A label
    agreeing with no extraction says the cell itself moved. So the locator is
    dropped first: here the verdict's own locator holds a cell with different
    labels, and another file holds one whose labels agree exactly, and the
    labelled one is the match.
    """
    claims = [
        _claim(metric="f1", value=88.5, where="squad_tab.tex:6", row_label="Another Row",
               column_label="Test F1"),
        _claim(metric="f1", value=88.5, where="elsewhere.tex:2", row_label="BERT_ BASE (Single)",
               column_label="Dev F1"),
    ]
    verifications = recall.VerificationSet(
        pair_id="p",
        verifications=(
            _verification(
                metric="f1", value=88.5, where="src/squad_tab.tex:6",
                row_label="BERT_ BASE (Single)", column_label="Dev F1",
                verdict="real_own_result",
            ),
        ),
    )

    alignment = recall.align_verdicts(claims, verifications)

    assert alignment.matched == {0: 1}
    assert alignment.fallbacks == frozenset({0})
    assert alignment.label_degradations == frozenset()


def test_a_degraded_verdict_reads_the_confidence_of_the_pool_it_was_placed_in():
    """The join and the confidence read must agree on which key placed a verdict.

    Reading the narrowed pool for a verdict placed on the unnarrowed one looks
    up a key that matched nothing, so the answer comes back undecidable and a
    confident false positive whose label was renamed quietly stops counting
    against §17's criterion.
    """
    renamed = recall.VerificationSet(
        pair_id="p",
        verifications=(
            _verification(
                metric="f1", value=88.5, where="src/squad_tab.tex:6",
                row_label="R.M. Reader (Ensemble, 2018)", column_label="Test F1",
                verdict="baseline",
            ),
        ),
    )
    agreeing = [
        _claim(metric="f1", value=88.5, where="squad_tab.tex:6",
               row_label="R.M. Reader (Ensemble)", column_label="Test F1", confidence=1.0),
        _claim(metric="f1", value=88.5, where="squad_tab.tex:6",
               row_label="BERT_ BASE (Single)", column_label="Dev F1", confidence=1.0),
    ]

    result = recall.compute_precision(renamed, agreeing)

    # Every extraction the degraded pool offers states 1.0, so the answer is
    # decidable even though the label is not.
    assert result.high_confidence_false_positives == 1
    assert result.unjoined_false_positives == 0

    # Where they disagree it stays undecidable, and is counted as such rather
    # than guessed either way.
    disagreeing, _ = _cell_pair()

    split = recall.compute_precision(renamed, disagreeing)

    assert split.high_confidence_false_positives == 0
    assert split.unjoined_false_positives == 1


def test_a_labelled_verdict_whose_locator_moved_is_still_held_to_its_cell():
    """The two fallbacks are independent, and the weaker one is not a reset.

    A locator that reconciles with no live extraction falls back to
    ``(metric, value)``, and the labels still narrow that group -- otherwise
    losing the locator would silently discard the cell the human named too.
    """
    claims, verifications = _cell_pair()
    moved = recall.VerificationSet(
        pair_id="p",
        verifications=(
            _verification(
                metric="f1", value=88.5, where="src/table_three.tex:2",
                row_label="BERT_ BASE (Single)", column_label="Dev F1",
                verdict="real_own_result",
            ),
        ),
    )

    alignment = recall.align_verdicts(claims, moved)
    coverage = recall.verification_coverage(claims, moved)

    assert alignment.matched == {0: 1}
    assert alignment.fallbacks == frozenset({0})
    assert coverage.location_fallbacks == 1
    assert coverage.label_fallbacks == 0


def test_a_verdict_recording_only_a_row_label_narrows_by_that_much():
    """A transposed table names its rows and leaves the column heading blank.

    Half the key is more than none: the row alone already separates the two
    cells, so the verdict is placed on the cell it names rather than falling
    back to the locator, and it is not counted as having supplied no label.
    """
    claims, _ = _cell_pair()
    row_only = recall.VerificationSet(
        pair_id="p",
        verifications=(
            _verification(
                metric="f1", value=88.5, where="src/squad_tab.tex:6",
                row_label="BERT_ BASE (Single)", verdict="real_own_result",
            ),
        ),
    )

    alignment = recall.align_verdicts(claims, row_only)

    assert alignment.matched == {0: 1}
    assert alignment.label_fallbacks == frozenset()


def test_labels_are_matched_across_a_difference_in_case_and_spacing():
    """A human reads the rendered table; the extractor records the cell it parsed.

    Those two differ in capitalisation and in runs of whitespace and in nothing
    else that can be established until a pair is re-adjudicated, so that is all
    that is flattened.
    """
    claims, _ = _cell_pair()
    respaced = recall.VerificationSet(
        pair_id="p",
        verifications=(
            _verification(
                metric="f1", value=88.5, where="src/squad_tab.tex:6",
                row_label="bert_  base   (single)", column_label="  dev f1 ",
                verdict="real_own_result",
            ),
        ),
    )

    alignment = recall.align_verdicts(claims, respaced)

    assert alignment.matched == {0: 1}
    assert alignment.label_fallbacks == frozenset()


def test_a_labelled_verdict_is_placed_before_an_unlabelled_one_can_take_its_extraction():
    """A file half re-adjudicated must not lose the half that names its cells.

    A verdict recording labels is offered a subset of what one recording none
    is offered, so it is placed first. Left in file order, the unlabelled
    verdict here would take the extraction the labelled one names exactly, and
    the assignment the stronger key had identified would be lost to one the
    weaker key made arbitrarily.
    """
    claims, verifications = _cell_pair()
    mixed = recall.VerificationSet(
        pair_id="p",
        verifications=(
            _unlabelled(verifications.verifications[1]),
            verifications.verifications[1],
        ),
    )

    alignment = recall.align_verdicts(claims, mixed)

    assert alignment.matched[1] == 0
    assert alignment.matched[0] == 1
    assert alignment.label_fallbacks == frozenset({0})
    assert alignment.unmatched_verdicts == ()


def test_the_measure_output_states_how_many_verdicts_named_no_cell(tmp_path):
    """The count belongs beside the rate, exactly as the locator's does.

    No verification file records a label yet, so on every adjudicated pair this
    is the whole matched set: barlowtwins 121, bert 160, convnext 237, detr 130,
    measured. Presenting the strengthening as if it were already in force would
    overstate what the four pairs establish.
    """
    report = recall.evaluate_precision(_verifications_file(tmp_path), _fixture_extraction())
    pair = recall.PairReport(
        pair_id="p", recall=recall.RecallReport(available=False, reason="no labels"), precision=report
    )

    assert report.coverage is not None
    assert report.coverage.label_fallbacks == 5

    rendered = recall.render_text(recall.build_report([pair]))

    assert "2/5 = 40.0% (unclear=0) [no labels: 5]" in rendered


def test_the_measurement_path_reports_the_cell_each_extraction_came_from(tmp_path):
    """The labels reach the matcher from the real extractor, not from a fixture.

    All five name a cell, including the two the keyword-proximity prose
    extractor also reads out of a header row: a cluster with a prose member
    speaks for that member for its *text*, which reads as a sentence, and takes
    its labels from the first member that sits in a cell. Drafting those two
    with no labels left a re-adjudicated verdict recording them stale rather
    than matched, which is the failure the labels exist to prevent.
    """
    code, paper = _build_pair_tree(tmp_path)

    extraction = recall.extract_claims(code=code, paper=paper)
    claims = recall._claims_of(extraction)

    assert extraction["available"] is True
    assert {(c.value, c.row_label, c.column_label) for c in claims} == {
        (79.5, "Threshold", "Accuracy"),
        (88.1, "Baseline", "Accuracy"),
        (92.4, "Ours", "Accuracy"),
        (77.0, "Baseline", "Recall"),
        (81.0, "Ours", "Recall"),
    }


def test_precision_is_unavailable_when_the_extraction_itself_is(tmp_path):
    """Coverage cannot be checked against a tree that is not there."""
    report = recall.evaluate_precision(
        _verifications_file(tmp_path),
        {"available": False, "reason": "code directory not found: nope"},
    )

    assert report.available is False
    assert report.result is None
    assert report.coverage is None
    assert "code directory not found: nope" in report.reason


def test_the_coverage_counts_reach_the_json_report_when_precision_is_unavailable(tmp_path):
    """A reader must be able to see how stale a file is, not only that it is."""
    stale = recall.evaluate_precision(
        _verifications_file(tmp_path), _fixture_extraction(_claim(metric="accuracy", value=65.3))
    )
    pair = recall.PairReport(
        pair_id="p", recall=recall.RecallReport(available=False, reason="no labels"), precision=stale
    )

    payload = recall.build_report([pair])

    assert payload["results"][0]["precision"]["coverage"] == {
        "extractions": 6, "verdicts": 5, "unadjudicated": 1, "stale": 0,
        "location_fallbacks": 0, "label_fallbacks": 5, "label_degradations": 0,
    }
    # A stale pair contributes nothing to the pool -- never a zero.
    assert payload["summary"]["precision"]["pairs_available"] == 0
    assert payload["summary"]["precision"]["pooled"] is None


def test_the_measure_output_states_how_many_verdicts_had_no_usable_locator(tmp_path):
    """How much of the key a pair could not supply belongs beside its rate.

    Measured on the four adjudicated pairs at the time of writing: convnext 1
    and detr 2 of 674 verdicts name a file no live extraction does, so their
    precision rests on ``(metric, value)`` for those rows. A reader of
    ``measure`` sees that. The count is a reading of one tree, not a property
    of the files -- it moves whenever clustering changes which of a number's
    two locators survives, which is what left an earlier figure of 12 here.
    """
    corresponding = recall.evaluate_precision(
        _verifications_file(tmp_path),
        _extraction(
            _claim(metric="accuracy", value=92.4, where="other.tex:6"),
            _claim(metric="recall", value=81.0, where="other.tex:15"),
            _claim(metric="accuracy", value=88.1, where="other.tex:6"),
            _claim(metric="recall", value=77.0, where="other.tex:15"),
            _claim(metric="accuracy", value=79.5, where="other.tex:6"),
        ),
    )
    pair = recall.PairReport(
        pair_id="p",
        recall=recall.RecallReport(available=False, reason="no labels"),
        precision=corresponding,
    )

    assert corresponding.available is True
    assert corresponding.coverage is not None
    assert corresponding.coverage.location_fallbacks == 5

    rendered = recall.render_text(recall.build_report([pair]))

    assert "2/5 = 40.0% (unclear=0) [no locator: 5]" in rendered


def test_the_measure_output_states_how_many_verdicts_lost_the_cell_they_named(tmp_path):
    """A degraded label belongs beside the rate for the same reason a locator does.

    The rate is still reported -- the alternative is refusing a whole pair over
    one renamed row -- so what it rests on has to be visible in the same line.
    """
    payload = _verifications_payload()
    payload["verifications"][0]["extraction"]["row_label"] = "a row this extraction never had"
    path = tmp_path / "verifications.json"
    _write_json(path, payload)

    corresponding = recall.evaluate_precision(path, _fixture_extraction())
    pair = recall.PairReport(
        pair_id="p",
        recall=recall.RecallReport(available=False, reason="no labels"),
        precision=corresponding,
    )

    assert corresponding.available is True
    assert corresponding.coverage is not None
    assert corresponding.coverage.label_degradations == 1
    assert corresponding.coverage.label_fallbacks == 4

    rendered = recall.render_text(recall.build_report([pair]))

    assert "[no labels: 4] [labels dropped: 1]" in rendered


# -- loading and validating ground truth --------------------------------------


def test_load_label_frame_parses_a_well_formed_file(tmp_path):
    path = tmp_path / "labels.json"
    _write_json(path, _labels_payload())

    frame = recall.load_label_frame(path)

    assert frame.pair_id == "fixture-pair"
    assert frame.sampled is True
    assert len(frame.labels) == 6
    assert frame.labels[0].id == "L1"


def test_load_label_frame_rejects_an_unknown_role(tmp_path):
    path = tmp_path / "labels.json"
    _write_json(
        path,
        {
            "pair_id": "p", "sampled": False, "sampling_seed": None, "frame": {},
            "labels": [{"id": "L1", "metric": "accuracy", "value": 1.0, "role": "bogus", "is_own_result": True}],
        },
    )

    with pytest.raises(recall.RecallInputError, match="role"):
        recall.load_label_frame(path)


def test_load_verification_set_rejects_an_unknown_verdict(tmp_path):
    path = tmp_path / "verifications.json"
    _write_json(
        path,
        {
            "pair_id": "p",
            "verifications": [{"extraction": {"metric": "accuracy", "value": 1.0}, "verdict": "bogus"}],
        },
    )

    with pytest.raises(recall.RecallInputError, match="verdict"):
        recall.load_verification_set(path)


def test_load_verification_set_reads_the_cell_a_verdict_names(tmp_path):
    """The labels arrive from the file, beside the locator, and stay optional.

    No file records them yet, so a verdict written without them parses to the
    same thing it always did.
    """
    path = tmp_path / "verifications.json"
    _write_json(
        path,
        {
            "pair_id": "p",
            "verifications": [
                {
                    "extraction": {
                        "metric": "f1", "value": 88.5, "where": "src/squad_tab.tex:6",
                        "row_label": "BERT_ BASE (Single)", "column_label": "Dev F1",
                    },
                    "verdict": "real_own_result",
                },
                {
                    "extraction": {"metric": "f1", "value": 91.0, "where": "src/squad_tab.tex:6"},
                    "verdict": "baseline",
                },
            ],
        },
    )

    loaded = recall.load_verification_set(path)

    assert loaded.verifications[0].row_label == "BERT_ BASE (Single)"
    assert loaded.verifications[0].column_label == "Dev F1"
    assert loaded.verifications[1].row_label is None
    assert loaded.verifications[1].column_label is None


# -- refusals: unavailable, never a zero -------------------------------------


def test_missing_code_directory_yields_unavailable_recall_not_a_zero(tmp_path):
    _, paper = _build_pair_tree(tmp_path)
    labels_path = tmp_path / "labels.json"
    _write_json(labels_path, _labels_payload())

    report = recall.evaluate_recall(code=tmp_path / "absent", paper=paper, labels_path=labels_path)

    assert report.available is False
    assert "absent" in report.reason
    assert report.diagnostic is None


def test_missing_label_file_yields_unavailable_recall_not_a_zero(tmp_path):
    code, paper = _build_pair_tree(tmp_path)

    report = recall.evaluate_recall(
        code=code, paper=paper, labels_path=tmp_path / "no-such-labels.json"
    )

    assert report.available is False
    assert "no-such-labels.json" in report.reason
    assert report.diagnostic is None
    assert report.claim_count is None


def test_precision_is_unavailable_without_a_verification_file():
    report = recall.evaluate_precision(None, _extraction())

    assert report.available is False
    assert report.result is None


def test_pair_with_labels_but_no_verification_file_has_recall_and_unavailable_precision(tmp_path):
    code, paper = _build_pair_tree(tmp_path)
    labels_path = tmp_path / "labels.json"
    _write_json(labels_path, _labels_payload())
    spec = recall.PairSpec(
        id="fixture-pair", code=code, paper=paper, labels=labels_path, verifications=None
    )

    report = recall.evaluate_pair(spec)

    assert report.recall.available is True
    assert report.recall.diagnostic is not None
    assert report.recall.diagnostic.recall == pytest.approx(0.5)
    assert report.precision.available is False
    assert "verification" in report.precision.reason


# -- the end-to-end proof -----------------------------------------------------


def test_end_to_end_fixture_reproduces_the_hand_computed_recall_and_precision(tmp_path):
    """Runs the real measurement path and checks it against the hand computation.

    The fixture's five real claims and six labels are laid out in
    ``_labels_payload``'s docstring. By hand: L1 and L2 match; L3 (a
    baseline) and L4 (relabelled a hyperparameter) are each satisfied by a
    real extraction but are not `matched`; L5 names a metric nothing
    extracts, and L6 restates L3's value under a metric nothing extracts, so
    both are `missed` -- giving matched=2, denominator=4, recall=0.5. The
    value-only pass additionally recovers L1, L2 and L6 (L6's value 88.1
    equals the Baseline accuracy cell) = 3. The one leftover claim
    (accuracy=79.5, "Threshold") is `unknown`, since the frame is sampled and
    accuracy is an established result metric.

    The verification file adjudicates each of those five extractions, 2 of
    them real_own_result: precision = 2/5, over a verification set that
    corresponds one-to-one with what the extractor produces.
    """
    code, paper = _build_pair_tree(tmp_path)
    labels_path = tmp_path / "labels.json"
    verifications_path = tmp_path / "verifications.json"
    _write_json(labels_path, _labels_payload())
    _write_json(verifications_path, _verifications_payload())
    spec = recall.PairSpec(
        id="fixture-pair",
        code=code,
        paper=paper,
        labels=labels_path,
        verifications=verifications_path,
    )

    report = recall.evaluate_pair(spec)

    assert report.recall.available is True
    assert report.recall.claim_count == 5
    diagnostic = report.recall.diagnostic
    assert diagnostic == recall.RecallDiagnostic(
        matched=2,
        missed=2,
        baseline_extracted=1,
        wrong_kind=1,
        unknown=1,
        false_positive=0,
        value_only_matches=3,
        recall_denominator=4,
        excluded_unconfident=0,
        # Every fixture label names a metric the vocabulary knows, so none is
        # unmatchable for want of a name.
        unnameable_labels=0,
        recall=0.5,
    )

    assert report.precision.available is True
    result = report.precision.result
    assert result is not None
    assert result.real_own_result == 2
    assert result.baseline == 1
    assert result.hyperparameter == 2
    assert result.adjudicated == 5
    assert result.precision == pytest.approx(2 / 5)
    # Every fixture cell sits under a header the vocabulary names, so all five
    # extractions are direct_parse at 1.0 and all three false positives are
    # confident ones.
    assert result.high_confidence_false_positives == 3
    assert result.unjoined_false_positives == 0
    assert report.precision.coverage == recall.VerificationCoverage(
        extractions=5, verdicts=5, unadjudicated=0, stale=0, location_fallbacks=0, label_fallbacks=5
    )

    rendered = recall.render_text(recall.build_report([report]))
    assert "2/4 = 50.0%" in rendered
    assert "2/5 = 40.0% (unclear=0)" in rendered


# -- the --src swap: proof, not assertion by convention ----------------------


def test_src_swaps_the_loaded_adduce_tree_and_the_claims_it_produces(tmp_path):
    """Retroactive measurement, proved directly against a real historical tree.

    6f00c8b predates both the table-cell reader (8a73b52) and the drafting
    rewrite (00653f4), so its ``_draft_claims`` still reads
    ``ev.latex.metrics[:10]`` directly, with no table cells and no
    clustering. Against this fixture that recovers only the two
    keyword-proximity prose matches the metric collector finds next to each
    table's own header ("Accuracy ... 92.4", "Recall ... 81.0"); the three
    table-only values (88.1, 79.5, 77.0) are invisible to it. Measured this
    session via the identical subprocess call below: 5 claims now, 2 at
    6f00c8b.
    """
    archive = subprocess.run(
        ["git", "archive", "6f00c8b", "src"],
        cwd=recall._REPOSITORY_ROOT,
        capture_output=True,
        timeout=30,
    )
    if archive.returncode != 0:
        pytest.skip("commit 6f00c8b is not reachable in this checkout's history")

    archived_root = tmp_path / "archived-6f00c8b"
    archived_root.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(archived_root)  # trusted: this repository's own git history
    archived_src = archived_root / "src"

    code, paper = _build_pair_tree(tmp_path / "pair")
    current = recall._run_extract_worker(code=code, paper=paper, src=None)
    historical = recall._run_extract_worker(code=code, paper=paper, src=archived_src)

    assert current["available"] is True
    assert historical["available"] is True
    assert current["adduce_loaded_from"] != historical["adduce_loaded_from"]
    assert Path(historical["adduce_loaded_from"]) == archived_src / "adduce"

    assert len(current["claims"]) == 5
    assert len(historical["claims"]) == 2
    historical_values = {(c["metric"], c["value"]) for c in historical["claims"]}
    assert historical_values == {("accuracy", 92.4), ("recall", 81.0)}

    # 6f00c8b's Claim has no confidence and no resolution method to report, so
    # the worker must state their absence rather than fail against that tree.
    assert all(c["confidence"] == 1.0 for c in current["claims"])
    assert all(c["resolution_method"] == "direct_parse" for c in current["claims"])
    assert all(c["confidence"] is None for c in historical["claims"])
    assert all(c["resolution_method"] is None for c in historical["claims"])


# -- the --src refusal: a tree that is not measured is never measured silently -


def test_a_src_tree_with_no_adduce_package_is_refused_rather_than_measured(tmp_path, monkeypatch):
    """The recorded trap: the import falls through and the arm measures this repo.

    ``sys.path.insert`` of a directory holding nothing does not fail, so an arm
    pointed at the wrong tree produced this repository's own numbers and read
    as a tree that changed nothing. Refused at the library boundary, before a
    subprocess is paid for -- asserted by making the worker unusable, since a
    refusal from inside it would otherwise read the same from out here.
    """
    code, paper = _build_pair_tree(tmp_path / "pair")
    empty = tmp_path / "not-a-source-tree"
    empty.mkdir()

    assert recall.src_refusal(empty) == f"no adduce package under {empty}"
    assert recall.src_refusal(None) is None
    assert recall.src_refusal(recall._DEFAULT_SRC) is None

    def unusable(**_: object) -> dict[str, object]:
        raise AssertionError("a refused --src must not reach the worker")

    monkeypatch.setattr(recall, "_run_extract_worker", unusable)
    refused = recall.extract_claims(code=code, paper=paper, src=empty)
    assert refused["available"] is False
    assert refused["reason"] == f"no adduce package under {empty}"
    assert "claims" not in refused


def test_the_extraction_worker_refuses_the_swap_it_was_asked_to_make(tmp_path):
    """The refusal sits where the tree is put on ``sys.path``, not only above it.

    ``_extract`` is reachable on its own, and it is the only place the swap
    happens, so a direct invocation must not fall through to the installed
    package either.
    """
    code, paper = _build_pair_tree(tmp_path / "pair")
    empty = tmp_path / "not-a-source-tree"
    empty.mkdir()

    in_the_worker = recall._run_extract_worker(code=code, paper=paper, src=empty)

    assert in_the_worker["available"] is False
    assert in_the_worker["reason"] == f"no adduce package under {empty}"
    assert "claims" not in in_the_worker


def test_the_measure_cli_refuses_a_src_tree_with_no_adduce_package(tmp_path, capsys):
    """Refused before the roster is walked, so no pair is measured against it."""
    pairs_csv = tmp_path / "pairs.csv"
    pairs_csv.write_text("id\nfixture-pair\n", encoding="utf-8")
    empty = tmp_path / "not-a-source-tree"
    empty.mkdir()

    with pytest.raises(SystemExit) as exit_info:
        recall.main(
            [
                "measure",
                "--pairs-csv", str(pairs_csv),
                "--pairs-root", str(tmp_path / "pairs"),
                "--labels-dir", str(tmp_path / "labels"),
                "--verifications-dir", str(tmp_path / "verifications"),
                "--src", str(empty),
            ]
        )

    assert exit_info.value.code == 2
    assert f"no adduce package under {empty}" in capsys.readouterr().err


def test_the_report_states_the_directory_the_extractor_imported_adduce_from(tmp_path):
    """A vacuous arm cannot pass silently if the report names the tree it loaded."""
    code, paper = _build_pair_tree(tmp_path)
    labels_path = tmp_path / "labels.json"
    _write_json(labels_path, _labels_payload())

    pair = recall.evaluate_pair(
        recall.PairSpec(id="ok", code=code, paper=paper, labels=labels_path, verifications=None)
    )
    report = recall.build_report([pair])

    assert pair.adduce_loaded_from == str(recall._DEFAULT_SRC / "adduce")
    assert report["results"][0]["adduce_loaded_from"] == str(recall._DEFAULT_SRC / "adduce")
    measurement = report["measurement"]
    assert measurement["src"] is None
    assert measurement["adduce_loaded_from"] == str(recall._DEFAULT_SRC / "adduce")
    assert measurement["adduce_is_this_repository"] is True
    assert str(recall._DEFAULT_SRC / "adduce") in recall.render_text(report)


def test_a_src_arm_that_resolved_this_repositorys_own_tree_says_so(tmp_path):
    """A tree that holds an adduce package can still be the tree under test.

    The refusal cannot catch that one -- the package is importable -- so the
    report states it instead: every count such an arm produces is the
    unswapped run's own, and a comparison against it measures nothing.
    """
    code, paper = _build_pair_tree(tmp_path)
    labels_path = tmp_path / "labels.json"
    _write_json(labels_path, _labels_payload())

    pair = recall.evaluate_pair(
        recall.PairSpec(id="ok", code=code, paper=paper, labels=labels_path, verifications=None),
        src=recall._DEFAULT_SRC,
    )
    report = recall.build_report([pair], src=recall._DEFAULT_SRC)

    assert report["measurement"]["src"] == str(recall._DEFAULT_SRC)
    assert report["measurement"]["adduce_is_this_repository"] is True
    assert "not the tree it was given" in recall.render_text(report)


def test_the_measurement_block_reports_no_tree_when_nothing_was_extracted():
    """No extraction ran, so no tree was loaded -- stated, not read as this one."""
    report = recall.build_report(
        [
            recall.PairReport(
                pair_id="missing",
                recall=recall.RecallReport(available=False, reason="code directory not found"),
                precision=recall.PrecisionReport(available=False, reason="no verification file"),
            )
        ]
    )

    assert report["measurement"]["adduce_loaded_from"] is None
    assert report["measurement"]["adduce_is_this_repository"] is None


# -- the inventory: the 14 pairs no other instrument here can see --------------


def _inventory(pair_id: str, **counts: Any) -> recall.PairInventory:
    defaults: dict[str, Any] = {"table_cells": 5, "claims": 5, "numeric_claims": 5}
    defaults.update(counts)
    return recall.PairInventory(pair_id=pair_id, available=True, **defaults)


def _paper_without_its_tables(root: Path) -> tuple[Path, Path]:
    """The same pair with every ``tabular`` deleted, as the definition defect left it.

    latent-diffusion's tables were wrapped in a macro whose definition was
    stripped, so the body reached nothing that prints and 624 cells became 0.
    Deleting the environments reproduces that end state without reproducing the
    idiom, which belongs to the extractor's own tests.
    """
    code, paper = _build_pair_tree(root)
    stripped = "\n".join(
        line
        for line in _PAPER_TEX.splitlines()
        if "tabular" not in line and "&" not in line and "\\\\" not in line
    )
    (paper / "paper.tex").write_text(stripped + "\n", encoding="utf-8")
    return code, paper


def test_the_inventory_covers_a_pair_that_carries_no_ground_truth(tmp_path):
    """The blind spot, and its closure, in one assertion.

    Recall covers 20 of the roster's 34 pairs and precision 4. An extractor
    change that deleted two unlabelled papers moved neither rate, so the gate
    that catches it has to be taken over every pair -- including the ones
    ``evaluate_pair`` does not even extract, because no ground truth would read
    the result.
    """
    code, paper = _build_pair_tree(tmp_path / "pair")
    spec = recall.PairSpec(
        id="unlabelled",
        code=code,
        paper=paper,
        labels=tmp_path / "labels" / "unlabelled.json",
        verifications=tmp_path / "verifications" / "unlabelled.json",
    )

    measured = recall.evaluate_pair(spec)
    inventoried = recall.inventory_pair(spec)

    assert measured.recall.available is False
    assert measured.precision.available is False
    assert inventoried.available is True
    assert inventoried.table_cells == 5
    assert inventoried.claims == 5
    assert inventoried.numeric_claims == 5


def test_the_inventory_records_the_table_cells_the_claims_were_drafted_from(tmp_path):
    """Cells, not only claims: a paper can stop being read while claims survive.

    The two counts answer different questions -- one is what the collector
    read, the other what drafting made of it -- so a change that moves one and
    not the other is visible as such.
    """
    code, paper = _build_pair_tree(tmp_path / "pair")
    spec = recall.PairSpec(id="p", code=code, paper=paper, labels=tmp_path / "labels.json")

    entry = recall.inventory_pair(spec)

    assert entry.counts == {"table_cells": 5, "claims": 5, "numeric_claims": 5}
    assert entry.adduce_loaded_from == str(recall._DEFAULT_SRC / "adduce")


def test_a_pair_whose_paper_is_missing_is_unavailable_with_its_reason_and_no_zero(tmp_path):
    """An unmeasured pair must not read as a paper that lost all its cells."""
    code, _ = _build_pair_tree(tmp_path / "pair")
    spec = recall.PairSpec(
        id="p", code=code, paper=tmp_path / "pair" / "nowhere", labels=tmp_path / "labels.json"
    )

    entry = recall.inventory_pair(spec)

    assert entry.available is False
    assert "paper directory not found" in (entry.reason or "")
    assert entry.counts == {"table_cells": None, "claims": None, "numeric_claims": None}


def test_a_paper_that_stopped_being_read_is_a_mover_and_so_is_one_that_grew(tmp_path):
    """The comparison the deleted papers needed: both counts, both directions.

    ``624 -> 0`` is what a reviewer had to find by reading a diff. Reported as
    the numbers rather than as a flag, because the size of the move is what
    says whether a paper was damaged or an extractor improved.
    """
    before = [_inventory("latent-diffusion", table_cells=624, claims=511, numeric_claims=511),
              _inventory("swin", table_cells=340, claims=334, numeric_claims=334),
              _inventory("detr", table_cells=188, claims=138, numeric_claims=138)]
    after = [_inventory("latent-diffusion", table_cells=0, claims=0, numeric_claims=0),
             _inventory("swin", table_cells=340, claims=350, numeric_claims=350),
             _inventory("detr", table_cells=188, claims=138, numeric_claims=138)]

    # Ordered by pair id, not by the order either run listed them, so two runs
    # that walked the roster differently still compare row for row.
    unchanged, deleted, moved_up = recall.compare_inventories(before, after)

    assert deleted.pair_id == "latent-diffusion"
    assert deleted.moved == (
        ("table_cells", 624, 0),
        ("claims", 511, 0),
        ("numeric_claims", 511, 0),
    )
    assert "table_cells 624 -> 0" in deleted.summary()
    assert deleted.unchanged is False

    assert moved_up.pair_id == "swin"
    assert moved_up.moved == (("claims", 334, 350), ("numeric_claims", 334, 350))
    assert "claims 334 -> 350" in moved_up.summary()

    assert unchanged.pair_id == "detr"
    assert unchanged.unchanged is True
    assert unchanged.summary() == "unchanged"


def test_a_count_the_second_run_did_not_measure_is_not_read_as_a_fall_to_zero():
    """A tree that predates a field reports no count for it, and null is not 0.

    Reading it as zero would turn every retroactive arm into a report of two
    destroyed papers, which is the same failure as missing a real one.
    """
    payload = {"available": True, "claims": [], "adduce_loaded_from": "/somewhere/adduce"}

    entry = recall._inventory_from_extraction("historical", payload)

    assert entry.table_cells is None
    comparison, = recall.compare_inventories(
        [_inventory("historical", table_cells=624, claims=0, numeric_claims=0)], [entry]
    )
    assert comparison.moved == (("table_cells", 624, None),)
    assert "table_cells 624 -> not measured" in comparison.summary()


def test_a_pair_that_stopped_being_extractable_at_all_is_not_unchanged():
    """Both runs report no counts, so the counts agree; availability does not."""
    before = [_inventory("detr")]
    after = [recall.PairInventory(pair_id="detr", available=False, reason="paper directory not found")]

    comparison, = recall.compare_inventories(before, after)

    assert comparison.availability_moved is True
    assert comparison.unchanged is False
    assert comparison.summary() == "available -> unavailable: paper directory not found"


def test_a_pair_only_one_run_records_is_compared_rather_than_dropped():
    """A roster that stopped covering a pair is itself a change to the gate."""
    comparisons = recall.compare_inventories([_inventory("detr")], [_inventory("swin")])

    assert [c.pair_id for c in comparisons] == ["detr", "swin"]
    assert [c.availability_moved for c in comparisons] == [True, True]
    assert "not in the after run" in comparisons[0].summary()
    assert "not in the before run" in comparisons[1].summary()


def test_load_inventory_refuses_a_file_that_is_not_an_inventory(tmp_path):
    """``json.load`` validates nothing, and a bogus arm reads as movement."""
    path = tmp_path / "not-an-inventory.json"
    _write_json(path, {"schema": recall.REPORT_SCHEMA, "results": []})
    with pytest.raises(recall.RecallInputError, match="schema must be"):
        recall.load_inventory(path)

    _write_json(
        path,
        {
            "schema": recall.INVENTORY_SCHEMA,
            "pairs": [{"pair_id": "detr", "available": True, "table_cells": 188.5}],
        },
    )
    with pytest.raises(recall.RecallInputError, match="table_cells must be a non-negative"):
        recall.load_inventory(path)


def test_the_inventory_cli_gates_a_change_that_deletes_a_papers_tables(tmp_path, capsys):
    """End to end, on the shape of the defect: two runs, one comparison, exit 1.

    Two roster runs over the same pair id, the second against a paper whose
    tables are gone. This is what pooled recall could not see, and it needs no
    label file to see it.
    """
    pairs_csv = tmp_path / "pairs.csv"
    pairs_csv.write_text("id\nlatent-diffusion\n", encoding="utf-8")
    intact = tmp_path / "intact"
    _build_pair_tree(intact / "latent-diffusion")
    damaged = tmp_path / "damaged"
    _paper_without_its_tables(damaged / "latent-diffusion")

    def inventory(root: Path, output: Path) -> int:
        return recall.main(
            ["inventory", "--pairs-csv", str(pairs_csv), "--pairs-root", str(root),
             "--output", str(output)]
        )

    before, after = tmp_path / "before.json", tmp_path / "after.json"
    assert inventory(intact, before) == 0
    assert inventory(damaged, after) == 0

    # A measured zero, not an absent count: the paper is still read, and that
    # is exactly the distinction the damaged pairs needed.
    assert json.loads(before.read_text(encoding="utf-8"))["summary"]["totals"] == {
        "table_cells": 5, "claims": 5, "numeric_claims": 5
    }
    assert json.loads(after.read_text(encoding="utf-8"))["summary"]["totals"] == {
        "table_cells": 0, "claims": 0, "numeric_claims": 0
    }

    exit_code = recall.main(["compare-inventory", "--before", str(before), "--after", str(after)])

    assert exit_code == 1
    movers = capsys.readouterr().out
    assert "table_cells 5 -> 0" in movers
    assert "1 pair(s): 0 unchanged, 1 moved" in movers


def test_the_comparison_report_names_the_movers_and_flags_one_run_compared_to_itself(
    tmp_path, capsys
):
    """A comparison of one report with itself finds nothing, and says why."""
    pairs_csv = tmp_path / "pairs.csv"
    pairs_csv.write_text("id\nfixture-pair\n", encoding="utf-8")
    pairs_root = tmp_path / "pairs"
    _build_pair_tree(pairs_root / "fixture-pair")
    report = tmp_path / "inventory.json"

    assert recall.main(
        ["inventory", "--pairs-csv", str(pairs_csv), "--pairs-root", str(pairs_root),
         "--output", str(report)]
    ) == 0
    capsys.readouterr()

    exit_code = recall.main(
        ["compare-inventory", "--before", str(report), "--after", str(report)]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "1 pair(s): 1 unchanged, 0 moved" in output
    assert "both arms report the same provenance" in output


def test_an_unmeasured_pair_makes_the_inventory_exit_nonzero(tmp_path, capsys):
    """A pair the gate could not measure is a hole in the gate, not a pass."""
    pairs_csv = tmp_path / "pairs.csv"
    pairs_csv.write_text("id\nmissing\n", encoding="utf-8")

    exit_code = recall.main(
        ["inventory", "--pairs-csv", str(pairs_csv), "--pairs-root", str(tmp_path / "pairs")]
    )

    assert exit_code == 1
    assert "unavailable: code directory not found" in capsys.readouterr().out


# -- the roster and the aggregate ---------------------------------------------


def test_load_roster_builds_specs_at_fetch_pys_layout(tmp_path):
    pairs_csv = tmp_path / "pairs.csv"
    pairs_csv.write_text("id,repo_url\nalpha,https://example.invalid/a\n", encoding="utf-8")

    (spec,) = recall.load_roster(
        pairs_csv,
        pairs_root=tmp_path / "pairs",
        labels_dir=tmp_path / "labels",
        verifications_dir=tmp_path / "verifications",
    )

    assert spec.id == "alpha"
    assert spec.code == tmp_path / "pairs" / "alpha" / "code"
    assert spec.paper == tmp_path / "pairs" / "alpha" / "paper" / "src"
    assert spec.labels == tmp_path / "labels" / "alpha.json"
    assert spec.verifications == tmp_path / "verifications" / "alpha.json"


def test_aggregate_pools_across_pairs_and_excludes_the_unavailable_one(tmp_path):
    """An unavailable pair must not contribute a zero to the pooled rate."""
    code, paper = _build_pair_tree(tmp_path)
    labels_path = tmp_path / "labels.json"
    _write_json(labels_path, _labels_payload())
    available = recall.evaluate_pair(
        recall.PairSpec(id="ok", code=code, paper=paper, labels=labels_path, verifications=None)
    )
    unavailable = recall.PairReport(
        pair_id="missing",
        recall=recall.RecallReport(available=False, reason="code directory not found: nope"),
        precision=recall.PrecisionReport(
            available=False, reason="no verification file configured for this pair"
        ),
    )

    report = recall.build_report([available, unavailable])

    summary = report["summary"]
    assert summary["pairs"] == 2
    assert summary["recall"]["pairs_available"] == 1
    assert summary["recall"]["pairs_unavailable"] == 1
    assert summary["recall"]["pooled"] == pytest.approx(0.5)
    assert summary["precision"]["pairs_available"] == 0
    assert summary["precision"]["pairs_unavailable"] == 2
    assert summary["precision"]["pooled"] is None
    rendered = recall.render_text(report)
    assert "unavailable: code directory not found: nope" in rendered


def test_measure_cli_runs_the_roster_and_writes_a_json_report(tmp_path, capsys):
    pairs_root = tmp_path / "pairs"
    _build_pair_tree(pairs_root / "fixture-pair")
    labels_dir = tmp_path / "labels"
    _write_json(labels_dir / "fixture-pair.json", _labels_payload())
    pairs_csv = tmp_path / "pairs.csv"
    pairs_csv.write_text("id\nfixture-pair\n", encoding="utf-8")
    output = tmp_path / "report.json"

    exit_code = recall.main(
        [
            "measure",
            "--pairs-csv", str(pairs_csv),
            "--pairs-root", str(pairs_root),
            "--labels-dir", str(labels_dir),
            "--verifications-dir", str(tmp_path / "verifications"),
            "--output", str(output),
        ]
    )

    assert exit_code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema"] == recall.REPORT_SCHEMA
    (result,) = payload["results"]
    assert result["pair_id"] == "fixture-pair"
    assert result["recall"]["diagnostic"]["recall"] == pytest.approx(0.5)
    assert result["precision"]["available"] is False
    assert "fixture-pair" in capsys.readouterr().out
