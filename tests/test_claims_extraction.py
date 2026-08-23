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
from adduce.claims.candidates import caption_metric
from adduce.evidence.latex import PaperValue, TableCell
from adduce.manifest_builder import _draft_claims
from adduce.naming import METRIC_PATTERNS, canonical_hyperparameter, canonical_metric

# --- the vocabulary move -------------------------------------------------


def test_a_hyperparameter_name_resolves_on_its_stripped_terminal_segment():
    """The terminal segment of a key is stripped exactly as the whole key is.

    Left unstripped, a separator followed by a space decided whether a name
    resolved at all: ``dec. depth`` is MAE's own column header and resolved to
    nothing where ``depth`` resolves to ``num_layers``. Measured over the dev
    set this changes no lookup at all -- 3,839 lookups over 1,090 distinct keys
    from twenty repositories, none of which writes a key of this shape -- so
    what pins it is this assertion and corpus/synthetic/synthetic_spaced_config_key.
    """
    assert canonical_hyperparameter("depth") == "num_layers"
    assert canonical_hyperparameter("dec. depth") == "num_layers"
    assert canonical_hyperparameter("model. learning rate") == "learning_rate"
    assert canonical_hyperparameter("trainer/ lr") == "learning_rate"
    # Unchanged: the shapes the split already resolved, and a key naming nothing.
    assert canonical_hyperparameter("optim.lr") == "learning_rate"
    assert canonical_hyperparameter("  batch_size  ") == "batch_size"
    assert canonical_hyperparameter("encoder") is None
    assert canonical_hyperparameter("dec. encoder") is None



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
    _canonical_metric_case(header, expected)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        # Error rate is the complement of accuracy, not a synonym: a reported
        # error rate of 15.5 and an accuracy of 15.5 are different claims.
        ("Error rate", "Accuracy"),
        ("Err.", "Acc."),
        # Word and character error rate are measured over different units and
        # are reported side by side.
        ("WER", "CER"),
        # AP50 and AP75 are the same average at one fixed IoU threshold each,
        # printed in the same row as different numbers.
        ("AP50", "AP75"),
        ("AP50", "AP"),
        # Recall at three ranks is three numbers in one row, and text-to-image
        # and image-to-text retrieval are two more beside them.
        ("R@1", "R@5"),
        ("R@5", "R@10"),
        ("TR@1", "IR@1"),
        ("TR@1", "R@1"),
        ("average recall@1", "R@1"),
        # AP at large scale, and AP75 for keypoint and dense-pose tasks.
        ("APL", "AP"),
        ("APkp75", "AP75"),
        ("APkp75", "APdp75"),
        # ROUGE-1, ROUGE-2 and ROUGE-L are printed side by side.
        ("R-1-F", "R-2-F"),
        ("R-2-F", "R-L-F"),
        # A rate of inference is reported three ways and a paper may print two
        # of them in one row; a linear probe and a fine-tune likewise.
        ("throughput", "FPS"),
        ("throughput", "inference latency"),
        ("lin", "Accuracy"),
        # Matthews and Spearman correlation are two correlations, and CoLA and
        # STS-B are reported in adjacent columns of one GLUE row.
        ("MCC", "SCC"),
    ],
)
def test_distinct_metrics_do_not_share_a_canonical_name(left, right):
    """Two metrics collapsed onto one name turn one row into a contradiction."""
    left_canonical = canonical_metric(left)
    right_canonical = canonical_metric(right)
    assert left_canonical is not None and right_canonical is not None
    assert left_canonical != right_canonical


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Error rate", "error_rate"),
        ("Error (%)", "error_rate"),
        ("CER", "cer"),
        ("AP50", "ap50"),
        ("AP@0.5", "ap50"),
        ("AP75", "ap75"),
        ("AP", "map"),
        ("mean average precision", "map"),
    ],
)
def test_canonical_metric_reads_the_separated_metrics(header, expected):
    _canonical_metric_case(header, expected)


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        # A split named before the metric.
        ("dev F1", "f1"),
        ("test EM", "exact_match"),
        ("dev accuracy", "accuracy"),
        # A dataset named before the metric.
        ("RACE accuracy", "accuracy"),
        ("SQuAD1.1 F1", "f1"),
        ("SQuAD2.0 EM", "exact_match"),
        ("MNLI acc", "accuracy"),
    ],
)
def test_a_qualifier_before_the_metric_does_not_hide_it(header, expected):
    """Headers qualify the metric with a split or a dataset; the metric survives."""
    _canonical_metric_case(header, expected)


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        # These read as "<qualifier> <metric>" but are deliberately their own
        # metrics, and the full-string lookup must win before any fallback.
        ("train loss", "train_loss"),
        ("val loss", "val_loss"),
        ("test loss", "test_loss"),
        ("word error rate", "wer"),
        ("character error rate", "cer"),
        ("mean average precision", "map"),
        # Its trailing word is "gpu", which names nothing; its trailing two are
        # "per gpu".
        ("peak memory per GPU", "peak_memory"),
        # And this one's trailing word is itself a metric: without the
        # whole-name lookup it would read as recall@1, which it is not.
        ("average recall@1", "average_recall_at_1"),
    ],
)
def test_a_compound_metric_is_never_flattened_onto_its_last_word(header, expected):
    _canonical_metric_case(header, expected)


@pytest.mark.parametrize("header", ["absolute accuracy improvement", "GLUE score", "Avg"])
def test_a_qualifier_fallback_does_not_invent_a_metric(header):
    """A delta and a composite score are not the metric their words contain."""
    assert canonical_metric(header) is None


@pytest.mark.parametrize(
    "header",
    [
        # A bare average names no unit. Registering these was measured and
        # rejected: a canonicalising header pre-empts the caption fallback, so
        # llama's "Average" under a caption reading "Five-shot accuracy" would
        # stop being an accuracy and whisper's would stop being a word error
        # rate. Eight headers across the dev pairs, 113 cells.
        "Average",
        "Avg",
        "AVG",
        "K700 AVG",
        # And a bare "score" is not an average of anything. These two are what
        # registering it would have claimed.
        "test score",
        "MT-Bench Score (GPT-4)",
        # The suite name stays out for the reason CoLA does: it says what the
        # number was measured on, not what was measured.
        "GLUE",
        "SuperGLUE",
        "points on GLUE",
    ],
)
def test_a_suite_name_and_a_bare_average_are_not_the_average_score(header):
    """``average_score`` is registered on its compound forms only.

    The metric exists because a suite's headline number is the mean of tasks
    scored in different units -- GLUE averages Matthews correlation, Spearman
    correlation, F1 and accuracy together -- so it is neither an accuracy nor
    any one of them. What makes it readable is that a composed header states it
    in full: t5's ``GLUE Score Average``, whose trailing two words are the
    metric and whose leading word is the suite.
    """
    assert canonical_metric(header) is None


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        # Cost, reported as a rate, a frame count, a duration or a footprint.
        ("throughput", "throughput"),
        ("image throughput", "throughput"),
        ("im/s", "throughput"),
        ("FPS", "fps"),
        ("inference latency", "latency"),
        ("Speedup", "speedup"),
        ("wall-clock speedup", "speedup"),
        ("hours", "training_time"),
        ("pre-training time", "training_time"),
        ("peak memory per GPU", "peak_memory"),
        ("#param.", "param_count"),
        ("#params", "param_count"),
        # Captioning, translation and retrieval.
        ("CIDEr", "cider"),
        ("SPICE", "spice"),
        ("METEOR", "meteor"),
        ("MET", "meteor"),
        ("B@4", "bleu"),
        ("TER", "ter"),
        ("VQA score", "vqa_score"),
        ("R@1", "recall_at_1"),
        ("R@5", "recall_at_5"),
        ("R@10", "recall_at_10"),
        ("TR@1", "text_recall_at_1"),
        ("IR@1", "image_recall_at_1"),
        ("average recall@1", "average_recall_at_1"),
        # Detection at one scale or one task, and the summarisation variants.
        ("APL", "ap_large"),
        ("APkp75", "keypoint_ap75"),
        ("APdp75", "densepose_ap75"),
        ("ROUGE-2-F", "rouge_2"),
        ("R-1-F", "rouge_1"),
        ("R-L-F", "rouge_l"),
        # Two correlations, and a protocol reported beside a fine-tune.
        ("MCC", "matthews"),
        ("SCC", "spearman"),
        ("lin", "linear_probe_accuracy"),
        # A suite's headline number, and the composed header t5 states it under.
        ("average score", "average_score"),
        ("GLUE Score Average", "average_score"),
        ("SuperGLUE Score Average", "average_score"),
    ],
)
def test_canonical_metric_reads_the_names_the_dev_set_prints(header, expected):
    """Each of these is a printed header or phrase from a labelled paper.

    Measured over the twenty labelled pairs, 112 of 296 eligible labels named a
    metric the vocabulary could not read at all, so their recall ceiling was
    zero by construction. These are the names that closed 58 of them.
    """
    _canonical_metric_case(header, expected)


@pytest.mark.parametrize(
    "header",
    [
        # A dataset the paper heads a column with, recorded in the label's
        # metric field because that is what was printed. Naming one here would
        # canonicalise a dataset into a metric and corrupt both sides of every
        # later match: CoLA is a dataset and its metric is Matthews correlation.
        "CoLA",
        "MNLI",
        "MultiNLI",
        "SST",
        "SST-2",
        "QNLI",
        "QQP",
        "MRPC",
        "RTE",
        "STS",
        "RACE",
        "GLUE",
        "points on GLUE",
        # A column of option names in a README argument table, which is why the
        # bare word is excluded while ``#params`` is not.
        "params",
    ],
)
def test_a_dataset_name_in_the_metric_field_stays_unnameable(header):
    assert canonical_metric(header) is None


@pytest.mark.parametrize(
    "header",
    [
        "nonlinear",  # contains "lin"
        "hours of pre-training",  # a leading qualifier is not the metric
        "Speedup vs. baseline",
        "MET Analysis",
    ],
)
def test_a_short_alias_is_not_matched_inside_an_unrelated_header(header):
    """The fallback reads trailing words, so a short name must not leak in.

    ``lin``, ``met`` and ``hours`` are short enough to appear inside a header
    that means something else. Only a whole header, or its trailing one or two
    words, may name the metric -- never a substring and never a leading word.
    """
    assert canonical_metric(header) is None


def test_no_alias_names_two_metrics():
    """A repeated alias would resolve by group order, silently and invisibly.

    :data:`~adduce.naming.METRIC_SYNONYMS` is built by walking the groups in
    order, so a second group claiming an alias the first already took would
    overwrite it with no error anywhere -- and the loser's metric would then be
    unreachable under the name its paper prints.
    """
    from adduce.naming import _METRIC_GROUPS

    owners: dict[str, str] = {}
    collisions = []
    for group in _METRIC_GROUPS:
        for alias in group:
            for key in (alias.lower(), alias.lower().replace(" ", "_")):
                if key in owners and owners[key] != group[0]:
                    collisions.append((key, owners[key], group[0]))
                owners[key] = group[0]
    assert collisions == []


def _canonical_metric_case(header, expected):
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
    cells = [TableCell(0, "Ours", "Mask quality rating", 1200.0, "main.tex", 9)]
    (candidate,) = from_latex_tables(cells)
    assert candidate.metric == "mask quality rating"
    assert candidate.method is ResolutionMethod.LEXICAL_MATCH
    assert candidate.confidence < 1.0


@pytest.mark.parametrize(
    "header",
    [
        "col4",  # positional placeholder the parser filled in
        "",  # no header at all
        "   ",
        "1c[origin=rc]270coraal",  # \\multicolumn + \\rotatebox residue
        "2ctop-1",  # \\multicolumn span fused to the visible text
        "270",  # a rotation angle that survived the split
        r"\multicolumn{2}{c}",
    ],
)
def test_latex_cell_under_a_header_that_is_not_a_metric_name_is_dropped(header):
    """Measured on ten real papers, these are what an unfiltered header admits.

    They are not metrics under any vocabulary, so there is nothing to abstain
    on -- unlike ``Mask quality rating`` above, which is kept at reduced
    confidence.
    """
    cells = [TableCell(0, "Ours", header, 92.4, "main.tex", 9)]
    assert from_latex_tables(cells) == []


def test_a_header_the_length_of_a_sentence_is_not_a_metric_name():
    """A caption the parser mis-split into the header row is not a metric.

    Separate from the empty-header case: this one has letters, no LaTeX
    residue and no positional placeholder, so only the length bound rejects it.
    """
    caption = "Results on ImageNet after pre-training for 1000 epochs with a batch size of 4096"
    cells = [TableCell(0, "Ours", caption, 92.4, "main.tex", 9)]
    assert from_latex_tables(cells) == []


def test_a_real_metric_missing_from_the_vocabulary_still_survives():
    """The filter must not become a second, stricter vocabulary check.

    ``Mask quality rating`` is segment-anything's own column header and the
    vocabulary does not name it, so it is the case that carries this test: the
    other three canonicalise, and a header that canonicalises would be kept
    whatever the filter did.
    """
    for header in ("FID", "GFLOPs", "AP^box_50", "Mask quality rating"):
        cells = [TableCell(0, "Ours", header, 3.6, "main.tex", 9)]
        assert from_latex_tables(cells), f"{header!r} should be kept"


# --- the caption names what the column does not ---------------------------


def test_a_caption_names_the_metric_a_dataset_column_leaves_unnamed():
    """whisper's columns are datasets and its metric is stated once, in the caption.

    Measured: all 121 distinct metric names it extracted were dataset names,
    every labelled cell was collected, and none could ever match.
    """
    cells = [TableCell(0, "Whisper large", "CORAAL", 20.2, "tables/x.tex", 4, "WER (%) on MLS")]
    (candidate,) = from_latex_tables(cells)
    assert candidate.metric == "wer"
    assert candidate.column_label == "CORAAL"
    assert "CORAAL" in candidate.text
    # The column did not say "WER"; the caption did. Never certain -- see below.
    assert candidate.method is ResolutionMethod.LEXICAL_MATCH
    assert candidate.confidence == 0.5


def test_a_caption_derived_metric_is_never_high_confidence():
    """A caption renames a column it cannot verify, so it must not assert certainty.

    Measured over the dev set, the caption rule renamed ~2,259 cells correctly
    and ~194 wrongly -- MAE's ``hours`` and ``speedup`` under a caption saying
    "our MAE training", LoRA's ``nist``/``meteor``/``cider`` collapsed to
    ``bleu``. The vocabulary has since learned every one of those but ``nist``,
    which is why ``nist`` is the case here. Nothing in a header separates a
    dataset column from a cost column, so the rest cannot be eliminated here;
    what they must not be is confident. ``zero high-confidence false positives``
    is a Phase 3 acceptance criterion, and only the header can satisfy it.
    """
    caption = "Accuracy of our method on ImageNet"
    from_caption = TableCell(0, "ViT-L", "nist", 34.5, "main.tex", 4, caption)
    (renamed,) = from_latex_tables([from_caption])
    assert renamed.metric == "accuracy"  # wrong, and known to be wrong
    assert renamed.method is ResolutionMethod.LEXICAL_MATCH
    assert renamed.confidence < 1.0

    from_header = TableCell(0, "ViT-L", "accuracy", 34.5, "main.tex", 4, caption)
    (parsed,) = from_latex_tables([from_header])
    assert parsed.method is ResolutionMethod.DIRECT_PARSE
    assert parsed.confidence == 1.0


def test_a_header_that_names_a_metric_is_not_overridden_by_its_caption():
    """The column is the more specific statement, so it wins.

    A caption naming one metric over a table reporting several would otherwise
    rename every column it does not mean.
    """
    cells = [TableCell(0, "Ours", "F1", 89.1, "main.tex", 4, "BLEU scores on CoVoST2")]
    (candidate,) = from_latex_tables(cells)
    assert candidate.metric == "f1"


def test_a_caption_naming_two_metrics_names_none():
    """It does not say which column reports which, so it says nothing.

    Guessing would state a confident wrong name where abstaining states none.
    """
    caption = "Accuracy and BLEU on the test split"
    assert caption_metric(caption) is None
    cells = [TableCell(0, "Ours", "CoVoST2", 20.2, "main.tex", 4, caption)]
    (candidate,) = from_latex_tables(cells)
    assert candidate.metric == "covost2"
    assert candidate.method is ResolutionMethod.LEXICAL_MATCH


def test_a_cell_the_paper_attributes_to_prior_work_is_not_certain():
    """107 of the 109 confident false positives are numbers credited elsewhere.

    The reading is right -- the header names a metric, the cell states a value
    -- and what is wrong is offering a competitor's result as a claim about
    this artifact. So the cell is still a claim, at the same metric, value and
    location, and only how confidently it was read moves.
    """
    own = TableCell(0, "Ours", "Accuracy", 92.4, "main.tex", 40)
    quoted = TableCell(0, "ResNet-50", "Accuracy", 76.1, "main.tex", 41, prior_work=True)
    parsed, demoted = from_latex_tables([own, quoted])

    assert parsed.method is ResolutionMethod.DIRECT_PARSE
    assert parsed.confidence == 1.0

    assert demoted.metric == "accuracy"
    assert demoted.value == 76.1
    assert str(demoted.location) == "main.tex:41"
    assert demoted.method is ResolutionMethod.LEXICAL_MATCH
    assert demoted.method not in CERTAIN_METHODS
    assert demoted.confidence == 0.5


def test_prior_work_never_revives_a_cell_the_header_filter_dropped():
    """It lowers confidence; it does not admit a candidate.

    A positional or residue header means the parse lost the column, and whose
    number it is has no bearing on whether the column names a metric.
    """
    for header in ("col4", "", "1c[origin=rc]270coraal"):
        cells = [TableCell(0, "ResNet-50", header, 76.1, "main.tex", 9, prior_work=True)]
        assert from_latex_tables(cells) == [], f"{header!r} should stay dropped"


def test_prior_work_does_not_change_which_metric_is_chosen():
    """Attribution moves the method and the confidence and nothing else.

    A cell already at ``lexical_match`` for taking its metric from the caption
    stays exactly where it was: there is no lower reading to demote it to, and
    the caption still names the metric the column does not.
    """
    caption = "Accuracy of our method on ImageNet"
    cells = [TableCell(0, "ResNet-50", "nist", 34.5, "main.tex", 4, caption, prior_work=True)]
    (candidate,) = from_latex_tables(cells)
    assert candidate.metric == "accuracy"
    assert candidate.method is ResolutionMethod.LEXICAL_MATCH
    assert candidate.confidence == 0.5


def test_a_caption_never_revives_a_cell_the_header_filter_dropped():
    """A caption renames a candidate; it does not admit one.

    A positional or residue header means the parse lost the column, so there is
    no dataset to attribute the caption's metric to -- and admitting it would
    reinstate exactly the 2,954 non-metric candidates the header filter removed.
    """
    for header in ("col4", "", "1c[origin=rc]270coraal"):
        cells = [TableCell(0, "Ours", header, 20.2, "main.tex", 4, "WER (%) on MLS")]
        assert from_latex_tables(cells) == [], f"{header!r} should stay dropped"


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


def _cell(metric, value, row, column, path="main.tex", line=1, text=None):
    return ClaimCandidate(
        metric=metric,
        value=value,
        source=CandidateSource.LATEX_TABLE,
        location=ClaimLocation(path, line),
        method=ResolutionMethod.DIRECT_PARSE,
        confidence=1.0,
        text=text if text is not None else f"{row} {column} {value}",
        row_label=row,
        column_label=column,
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


@pytest.mark.parametrize("neighbour", [54.1, 53.6, 54.4])
def test_a_value_printed_with_a_trailing_zero_does_not_swallow_its_neighbours(neighbour):
    """54.0 is stated to one decimal place. It is not the integer 54.

    ``repr(54.0)`` is ``'54.0'`` and stripping that trailing zero read it as
    zero decimal places, so every ``X.0`` agreed with everything within half a
    unit. Measured over five labelled dev papers this merged 48 pairs of
    distinct reported numbers, and every merge bound one table row's label to
    another row's value -- Barlow Twins' transfer table yielded
    ``BYOL: Places-205 = 54.1`` when BYOL's row states 54.0 and 54.1 is the
    authors' own row. Both a reported number and its locator were wrong.
    """
    clusters = cluster_candidates(
        [_candidate("accuracy", 54.0), _candidate("accuracy", neighbour, line=2)]
    )
    assert len(clusters) == 2
    assert sorted(c.value for c in clusters) == sorted([54.0, neighbour])


def test_a_coarser_statement_still_agrees_with_a_more_precise_one():
    """The rounding tolerance the module exists for must survive the fix above:
    54.0 and 54.04 are one claim stated at two precisions, not two claims."""
    clusters = cluster_candidates(
        [_candidate("accuracy", 54.0), _candidate("accuracy", 54.04, line=2)]
    )
    assert len(clusters) == 1
    assert clusters[0].value == 54.04


def test_two_cells_of_one_table_are_never_one_claim():
    """Clustering's premise is restatement, and one table stating two numbers is
    not restating one. ConvNeXt reports 81.3 for Swin-T and 81.33 for its own
    ablation in a single table; they agree at one decimal place, and merging
    them destroyed 81.3 and left 81.33 carrying Swin-T's row."""
    clusters = cluster_candidates(
        [
            _candidate("accuracy", 81.3, "main.tex", 805),
            _candidate("accuracy", 81.33, "main.tex", 805),
        ]
    )
    assert len(clusters) == 2
    assert sorted(c.value for c in clusters) == [81.3, 81.33]


def test_one_table_stating_a_number_twice_is_still_one_claim():
    """The rule above keys on a *different* number at the same location. A table
    repeating a value across two columns is a restatement, not two claims."""
    (cluster,) = cluster_candidates(
        [
            _candidate("accuracy", 81.3, "main.tex", 805),
            _candidate("accuracy", 81.3, "main.tex", 805),
        ]
    )
    assert len(cluster.members) == 2


def test_the_same_number_in_two_places_is_still_one_claim_after_the_table_rule():
    """The abstract and a results table stating 92.4 remain one claim."""
    (cluster,) = cluster_candidates(
        [
            _candidate("accuracy", 92.4, "abstract.tex", 4, CandidateSource.LATEX_PROSE),
            _candidate("accuracy", 92.41, "results.tex", 40),
        ]
    )
    assert len(cluster.members) == 2
    assert cluster.restated


@pytest.mark.parametrize("other_line", [6, 40])
def test_two_cells_reporting_one_value_stay_two_claims(other_line):
    """Two rows printing the same number are two measurements, not one restated.

    The locator answers neither case: every cell of a ``tabular`` records the
    line the environment opens on, so two cells of one table (``other_line``
    6) are indistinguishable by it, and cells of two tables (40) were compared
    on nothing at all. bert prints its own ``(Ens.+TriviaQA)`` F1 of 92.2 and an
    ELMo baseline of 92.2; merging them destroyed the own result and left the
    survivor carrying the baseline's row, and barlowtwins' Table 6 yielded
    three claims from eight cells the same way.
    """
    clusters = cluster_candidates(
        [
            _cell("f1", 92.2, "BERT (Ens.+TriviaQA)", "Test F1", line=6),
            _cell("f1", 92.2, "ELMo", "Test F1", line=other_line),
        ]
    )
    assert len(clusters) == 2
    assert sorted(member.row_label for c in clusters for member in c.members) == [
        "BERT (Ens.+TriviaQA)",
        "ELMo",
    ]


def test_one_measurement_restated_in_three_places_is_still_one_claim():
    """The rule above keys on what was measured, not on where it was printed.

    barlowtwins repeats its baseline row in every ablation table and states the
    number in prose as well: same row, same column, one measurement stated three
    times. Splitting those is the failure the module exists to prevent, and the
    cluster still reports the best method and the highest confidence any member
    carries.
    """
    (cluster,) = cluster_candidates(
        [
            _candidate("accuracy", 71.4, "4_ablations.tex", 5, CandidateSource.LATEX_PROSE),
            _cell("accuracy", 71.4, "Baseline", "Top-1", path="4_ablations.tex", line=25),
            _cell("accuracy", 71.4, "Baseline", "Top-1", path="4_ablations.tex", line=184),
        ]
    )
    assert len(cluster.members) == 3
    assert cluster.restated
    assert cluster.method is ResolutionMethod.DIRECT_PARSE
    assert cluster.confidence == 1.0


def test_the_order_candidates_cluster_in_covers_what_they_measure():
    """A field that decides membership cannot be left out of the total order.

    These two cells agree on every other field a candidate carries, so without
    the labels in the key their order -- and with it the member order of every
    run they take part in -- would follow the order they were extracted in.
    """
    first = _cell("accuracy", 89.0, "SimCLR", "Top-5", line=19, text="accuracy 89.0")
    second = _cell("accuracy", 89.0, "BYOL", "Top-5", line=19, text="accuracy 89.0")

    def shape(clusters):
        return [[(m.row_label, m.column_label) for m in c.members] for c in clusters]

    assert shape(cluster_candidates([first, second])) == [
        [("BYOL", "Top-5")],
        [("SimCLR", "Top-5")],
    ]
    assert shape(cluster_candidates([second, first])) == shape(cluster_candidates([first, second]))


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


# --- drafting integration ------------------------------------------------
#
# These drive the real pipeline rather than a constructed Evidence, because the
# defect being closed is in how drafting consumes evidence, and a hand-built
# Evidence would let the test agree with a wiring that does not exist.


def _repo(tmp_path, readme: str, results: dict[str, str] | None = None):
    (tmp_path / "README.md").write_text(readme, encoding="utf-8")
    (tmp_path / "train.py").write_text("import torch\n", encoding="utf-8")
    for name, body in (results or {}).items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    from adduce.engine import run_check

    return run_check(tmp_path).evidence


def test_drafting_no_longer_truncates_at_ten(tmp_path):
    """The path this replaces dropped claim eleven onward without saying so."""
    rows = "\n".join(f"| m{i} | {90 + i}.5 |" for i in range(25))
    evidence = _repo(tmp_path, f"| model | accuracy |\n| --- | --- |\n{rows}\n")
    claims = _draft_claims(evidence)
    assert len(claims) == 25
    assert claims[-1].id == "C25"


def test_draft_log_names_a_result_file_that_states_the_claim(tmp_path):
    evidence = _repo(
        tmp_path,
        "| model | accuracy |\n| --- | --- |\n| ours | 92.4 |\n",
        {"results/eval.csv": "accuracy\n92.4\n"},
    )
    (claim,) = _draft_claims(evidence)
    assert claim.produced_by.log == "results/eval.csv"


def test_draft_log_is_absent_when_no_result_file_states_the_claim(tmp_path):
    """The constant this replaces named the first result file regardless."""
    evidence = _repo(
        tmp_path,
        "| model | accuracy |\n| --- | --- |\n| ours | 92.4 |\n",
        {"results/eval.csv": "accuracy\n11.1\n"},
    )
    (claim,) = _draft_claims(evidence)
    assert evidence.results.files, "the result file must exist for this to mean anything"
    assert claim.produced_by.log is None


def test_two_claims_are_linked_to_the_files_that_actually_state_them(tmp_path):
    """The anti-constant property, stated directly: different claims, different logs."""
    evidence = _repo(
        tmp_path,
        "| model | accuracy |\n| --- | --- |\n| a | 92.4 |\n| b | 77.7 |\n",
        {
            "results/alpha.csv": "accuracy\n92.4\n",
            "results/beta.csv": "accuracy\n77.7\n",
        },
    )
    logs = {c.value: c.produced_by.log for c in _draft_claims(evidence)}
    assert logs == {92.4: "results/alpha.csv", 77.7: "results/beta.csv"}


def test_a_results_table_naming_no_metric_still_reports_something(tmp_path):
    """Unreadable is not the same state as absent, and must not look like it."""
    evidence = _repo(
        tmp_path, "| model | mask quality rating |\n| --- | --- |\n| ours | 1200 |\n"
    )
    claims = _draft_claims(evidence)
    if evidence.docs.has_results_table:
        assert len(claims) == 1
        assert claims[0].metric is None


def test_a_result_file_stating_the_same_number_under_another_metric_is_not_a_match(tmp_path):
    """Both halves of reconciliation are load-bearing, and only this shape shows it.

    Where every result file names the claim's metric, matching on value alone
    reaches the same answer, so a test built that way cannot tell whether the
    metric is being checked at all. Here the number is right and the metric is
    wrong, which is exactly the false link the check exists to refuse.
    """
    evidence = _repo(
        tmp_path,
        "| model | accuracy |\n| --- | --- |\n| ours | 92.4 |\n",
        {"results/loss.csv": "loss\n92.4\n"},
    )
    (claim,) = _draft_claims(evidence)
    assert evidence.results.files, "the result file must exist for this to mean anything"
    assert claim.produced_by.log is None
