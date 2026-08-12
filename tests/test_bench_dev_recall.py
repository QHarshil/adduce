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
    defaults: dict[str, Any] = {"metric": "accuracy", "value": 92.4, "where": "a.tex:1", "text": ""}
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


# -- verification coverage: precision only over the extractions produced now --


def _extraction(*claims: recall.ExtractedClaim) -> dict[str, Any]:
    """An extraction worker payload, in the shape ``evaluate_precision`` reads."""
    return {
        "available": True,
        "claims": [
            {"metric": c.metric, "value": c.value, "where": c.where, "text": c.text}
            for c in claims
        ],
    }


def _fixture_extraction(*extra: recall.ExtractedClaim) -> dict[str, Any]:
    """The five extractions ``_verifications_payload`` adjudicates, plus any extra."""
    return _extraction(
        _claim(metric="accuracy", value=92.4),
        _claim(metric="recall", value=81.0),
        _claim(metric="accuracy", value=88.1),
        _claim(metric="recall", value=77.0),
        _claim(metric="accuracy", value=79.5),
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
        extractions=5, verdicts=5, unadjudicated=0, stale=0
    )


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
        extractions=6, verdicts=5, unadjudicated=1, stale=0
    )
    assert "6 extractions" in report.reason
    assert "5 verdicts" in report.reason
    assert "1 unadjudicated" in report.reason
    assert "0 stale" in report.reason


def test_a_verdict_matching_no_extraction_makes_precision_unavailable(tmp_path):
    """The other direction: an adjudication of something the extractor dropped."""
    extraction = _extraction(
        _claim(metric="accuracy", value=92.4),
        _claim(metric="recall", value=81.0),
        _claim(metric="accuracy", value=88.1),
        _claim(metric="recall", value=77.0),
    )

    report = recall.evaluate_precision(_verifications_file(tmp_path), extraction)

    assert report.available is False
    assert report.result is None
    assert report.coverage == recall.VerificationCoverage(
        extractions=4, verdicts=5, unadjudicated=0, stale=1
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
        extractions=1, verdicts=1, unadjudicated=1, stale=1
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
        extractions=1, verdicts=1, unadjudicated=0, stale=0
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
    }
    # A stale pair contributes nothing to the pool -- never a zero.
    assert payload["summary"]["precision"]["pairs_available"] == 0
    assert payload["summary"]["precision"]["pooled"] is None


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
    assert report.precision.coverage == recall.VerificationCoverage(
        extractions=5, verdicts=5, unadjudicated=0, stale=0
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
