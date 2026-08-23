"""The research-artifact collectors: config, LaTeX, notebooks, remotes,
precision, results, run history, portability."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adduce.evidence.latex import (
    _MAX_MACRO_BODY_CHARS,
    _ROW_MARKUP_RE,
    _STATE_COMMANDS,
    _dissolve_multicolumn,
    _expand_macros,
    _strip_definitions,
    _strip_state_commands,
    _zero_argument_macros,
    collect_latex,
)
from adduce.model import scan_repository
from adduce.rules.base import Status
from adduce.rules.remote import RawUrlRule

_TEX = r"""
\documentclass{article}
\title{CineMatch: Personalized Movie Recommendation}
\begin{document}
% a comment with a learning rate of 999 that must be ignored
We train with a learning rate of $1\times10^{-4}$ and a batch size of 256
for 50 epochs on CIFAR-10, using three seeds and reporting mean $\pm$ std.
Our model achieves an accuracy of 92.4 on the test set.
All experiments ran on a single NVIDIA A100 GPU for 3 hours in bf16.
We include an ablation over attention heads.
\begin{tabular}{lcc}
\toprule
Model & Accuracy & F1 \\
\midrule
Ours & 92.4 & 89.1 \\
Baseline & 90.2 & 87.0 \\
\bottomrule
\end{tabular}
\end{document}
"""


def test_latex_extraction(make_evidence):
    ev = make_evidence({"paper/main.tex": _TEX})
    latex = ev.latex
    assert latex.has_paper and latex.main_file == "paper/main.tex"
    assert latex.title == "CineMatch: Personalized Movie Recommendation"

    hp = latex.hyperparameter_values()
    assert any(abs(v.value - 1e-4) < 1e-12 for v in hp.get("learning_rate", []))
    assert any(v.value == 256 for v in hp.get("batch_size", []))
    assert any(v.value == 50 for v in hp.get("epochs", []))

    assert any(m.name == "accuracy" and abs(m.value - 92.4) < 1e-9 for m in latex.metrics)
    assert any(c.row_label == "Ours" and c.value == 92.4 for c in latex.table_cells)
    assert "cifar-10" in latex.datasets_mentioned
    assert latex.mentions_hardware and latex.mentions_runtime
    assert latex.mentions_multiseed and latex.mentions_precision
    assert latex.ablation_mentions
    # Comment-stripped: the bogus 999 never appears.
    assert not any(v.value == 999 for values in hp.values() for v in values)


def test_a_cutoff_glued_to_a_metric_name_is_not_a_value(make_evidence):
    r"""``Recall@1`` is the metric's name, and the 1 is the rank, not the recall.

    BLIP writes ten of these, and the guard rejecting a number glued to a word
    did not hold for ``@``: they were read as a recall of 1, a BLEU of 4 and an
    MRR of 1, at confidence 0.5. The cutoff sits either side of the keyword
    boundary -- the pattern ``\brecall\b`` leaves the ``@`` ahead of the number
    and the pattern ``recall@`` takes it into the match -- so both sides are
    refused, and the sentence's own +2.7 is not a result either.
    """
    tex = (
        "Our model improves image-text retrieval by +2.7\\% in average recall@1,\n"
        "and captioning by +2.8\\% in CIDEr.\n"
        "Method & MRR$\\uparrow$ & R@1$\\uparrow$ & R@5$\\uparrow$ \\\\\n"
        "C: CIDEr, S: SPICE, B@4: BLEU@4.\n"
    )
    metrics = make_evidence({"paper/main.tex": tex}).latex.metrics
    assert [(m.name, m.value) for m in metrics] == []


def test_the_number_after_a_cutoff_is_the_one_the_sentence_states(make_evidence):
    r"""A cutoff is passed over, not treated as the end of the search.

    "Recall@1 of 82.5" is how a retrieval paper states a result, and refusing
    the candidate outright rather than skipping the rank would lose the 82.5 --
    turning a false positive into a miss on the commonest shape in the class.

    Both of the vocabulary's patterns for this metric match the one phrase, so
    the collector reads it twice and clustering merges them; the assertion is
    over the pair read, not over how many times one pattern list matched it.
    """
    tex = "We reach a recall@1 of 82.5 on the COCO test split.\n"
    metrics = make_evidence({"paper/main.tex": tex}).latex.metrics
    assert {(m.name, m.value) for m in metrics} == {("recall", 82.5)}


def test_a_number_glued_to_a_name_by_a_hyphen_is_still_refused(make_evidence):
    """The characters the guard already rejected keep being rejected.

    ``CIFAR-10`` is a dataset and ``top-1`` a column: neither states a value,
    and the ``@`` case is added beside them rather than in place of them.
    """
    tex = "We report accuracy on CIFAR-10 and follow the top-1 protocol.\n"
    metrics = make_evidence({"paper/main.tex": tex}).latex.metrics
    assert [(m.name, m.value) for m in metrics] == []


#: Both shapes of undissolved-wrapper defect, measured on real papers. Whisper
#: rotates its dataset headers to fit narrow columns; Swin spans a header cell
#: across two body columns.
_WRAPPED_HEADERS_TEX = r"""
\begin{tabular}{lcc}
Model & \multicolumn{1}{c}{\rotatebox[origin=rc]{270}{LibriSpeech}} & \rotatebox{270}{TED-LIUM3} \\
Ours & 3.4 & 4.5 \\
\end{tabular}
"""

_SPANNING_HEADER_TEX = r"""
\begin{tabular}{lccc}
Model & \multicolumn{2}{c}{ImageNet} & Params \\
Ours & 84.5 & 97.3 & 88 \\
\end{tabular}
"""


def test_rotated_and_spanned_headers_are_dissolved_to_their_text(make_evidence):
    r"""A wrapper's arguments are not part of the column's name.

    ``\multicolumn`` and ``\rotatebox`` take their text as the last of several
    arguments, so stripping command names blindly concatenates the rest onto it
    and a column named ``LibriSpeech`` arrives as ``1c[origin=rc]270LibriSpeech``
    -- which names no metric, so the whole column is dropped downstream.
    """
    cells = make_evidence({"paper/main.tex": _WRAPPED_HEADERS_TEX}).latex.table_cells
    by_value = {c.value: c.column_label for c in cells}
    assert by_value == {3.4: "LibriSpeech", 4.5: "TED-LIUM3"}


def test_a_spanning_header_names_every_column_it_covers(make_evidence):
    """The span is kept, not just the text.

    Dropping it leaves the header row shorter than the body rows, so every
    column after the spanned one is read against the wrong header -- or off the
    end of it, and named positionally as ``col3``.
    """
    cells = make_evidence({"paper/main.tex": _SPANNING_HEADER_TEX}).latex.table_cells
    by_value = {c.value: c.column_label for c in cells}
    assert by_value == {84.5: "ImageNet", 97.3: "ImageNet", 88.0: "Params"}


def test_a_spanning_body_cell_states_one_number_not_several(make_evidence):
    """A body cell spanning two columns is one reported number, not two.

    The header repeats across its span because it names both columns; a value
    must not, or one measurement would be counted twice.
    """
    tex = r"""
\begin{tabular}{lcc}
Model & Top-1 & Top-5 \\
Ours & \multicolumn{2}{c}{91.2} \\
\end{tabular}
"""
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [(c.column_label, c.value) for c in cells] == [("Top-1", 91.2)]


@pytest.mark.parametrize(
    "field",
    [
        r"\multicolumn{2}{c}{Unclosed",  # no closing brace
        r"\multicolumn{x}{c}{A}",  # span is not a number
        r"\multicolumn{0}{c}{A}",  # span covers no column
    ],
)
def test_a_malformed_span_falls_back_rather_than_being_guessed(field):
    """A wrapper it cannot parse is left alone and spans one column.

    The alternative -- guessing a span -- would silently shift every later
    column onto the wrong header, which is the defect this dissolving exists to
    remove.
    """
    assert _dissolve_multicolumn(field) == (field, 1)


#: Every environment a results table is written in, with the arguments each
#: takes: a width for the two that size themselves, an optional placement for
#: ``longtable``, and a column spec whose own braces nest.
_TABLE_ENVIRONMENTS = [
    (r"\begin{tabular}{l@{\hskip 6pt}cc}", r"\end{tabular}"),
    (r"\begin{tabularx}{\textwidth}{l@{\hskip 6pt}XX}", r"\end{tabularx}"),
    (r"\begin{tabular*}{\linewidth}{@{\extracolsep{\fill}}lcc}", r"\end{tabular*}"),
    (r"\begin{longtable}[c]{l@{\hskip 6pt}cc}", r"\end{longtable}"),
]


@pytest.mark.parametrize(("opening", "closing"), _TABLE_ENVIRONMENTS)
def test_every_table_environment_yields_its_cells(make_evidence, opening, closing):
    """A results table is written in whichever of these the layout wanted.

    Matching the name ``tabular`` alone leaves a paper that sizes its tables to
    the text width with no tables at all: electra writes seven of its eight in
    ``tabularx``, and reported four cells for the whole paper.
    """
    tex = opening + "\nModel & Top-1 & F1 \\\\\nOurs & 92.4 & 89.1 \\\\\n" + closing + "\n"
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [(c.row_label, c.column_label, c.value) for c in cells] == [
        ("Ours", "Top-1", 92.4),
        ("Ours", "F1", 89.1),
    ]


@pytest.mark.parametrize(("opening", "closing"), _TABLE_ENVIRONMENTS)
def test_an_environments_own_arguments_are_not_table_content(make_evidence, opening, closing):
    """A width and a column spec state a layout, not a row.

    Read as the first row they are concatenated onto the header, so every value
    beneath a spanned header is attributed to a column named after an alignment
    string -- and a length in that spec is read as a value the paper never
    stated.
    """
    tex = (
        opening + "\n\\multicolumn{2}{c}{ImageNet} & F1 \\\\\n"
        "Ours & 92.4 & 89.1 \\\\\n" + closing + "\n"
    )
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [(c.column_label, c.value) for c in cells] == [("ImageNet", 92.4), ("F1", 89.1)]


def test_a_longtable_is_not_closed_by_a_stray_end_tabular(make_evidence):
    r"""The environment that closes a table must be the one that opened it.

    Closing on any ``\end`` ends the table at the first environment to finish
    inside it, and every row after that point is lost.
    """
    tex = (
        "\\begin{longtable}{lcc}\n"
        "Model & Top-1 & F1 \\\\\n"
        "Ours & 92.4 & 89.1 \\\\\n"
        "\\end{tabular}\n"
        "Baseline & 90.2 & 87.0 \\\\\n"
        "\\end{longtable}\n"
    )
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [(c.row_label, c.value) for c in cells] == [
        ("Ours", 92.4),
        ("Ours", 89.1),
        ("Baseline", 90.2),
        ("Baseline", 87.0),
    ]


#: Mamba's zero-shot table: the columns name datasets and the row beneath them
#: names what was measured. That one table is 45 of the paper's own results.
_SECOND_HEADER_TEX = r"""
\begin{tabular}{lccc}
Model & Pile & LAMBADA & HellaSwag \\
      & ppl $\downarrow$ & ppl $\downarrow$ & acc $\uparrow$ \\
Mamba-2.8B & 6.22 & 4.23 & 66.1 \\
\end{tabular}
"""


def test_a_second_header_row_names_the_metric_its_columns_leave_unnamed(make_evidence):
    """Two header rows compose: the dataset from the first, the metric from the second.

    Reading the first row alone names every column after a dataset, so the cell
    is collected and can never match anything -- the recall gap on this paper
    was naming, not collection.
    """
    cells = make_evidence({"paper/main.tex": _SECOND_HEADER_TEX}).latex.table_cells
    assert [(c.column_label, c.value) for c in cells] == [
        ("Pile ppl", 6.22),
        ("LAMBADA ppl", 4.23),
        ("HellaSwag acc", 66.1),
    ]


def test_a_second_row_stating_numbers_is_data_and_stays_data(make_evidence):
    """A results table's first body row must never be read as a header.

    A transposed table is the case that decides this: its row labels are the
    metrics, so every test but "the row states numbers" says header. Consuming
    it drops the numbers it states and renames every column beneath it.
    """
    tex = r"""
\begin{tabular}{lcc}
Metric & Ours & Baseline \\
AP & 42.0 & 40.1 \\
AP50 & 62.4 & 60.6 \\
\end{tabular}
"""
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [(c.row_label, c.column_label, c.value) for c in cells] == [
        ("AP", "Ours", 42.0),
        ("AP", "Baseline", 40.1),
        ("AP50", "Ours", 62.4),
        ("AP50", "Baseline", 60.6),
    ]


def test_a_second_row_is_not_a_header_when_the_first_already_names_metrics(make_evidence):
    """A table whose columns are metrics already says what it measured.

    The second row here qualifies them rather than naming them, so composing
    the two would rename ``Accuracy`` after the qualifier.
    """
    tex = r"""
\begin{tabular}{lcc}
Model & Accuracy & F1 \\
      & top-1 & macro \\
Ours & 92.4 & 89.1 \\
\end{tabular}
"""
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [(c.column_label, c.value) for c in cells] == [("Accuracy", 92.4), ("F1", 89.1)]


#: ELECTRA's SQuAD table: one column of the first header row states a cost the
#: vocabulary knows and the rest name datasets whose metric sits underneath.
#: Asking that no cell of the first row name a metric refuses the whole table,
#: leaving all 331 of its cells named after a dataset and 0 canonical.
_MIXED_HEADER_TEX = r"""
\begin{tabular}{lcccc}
Model & Train FLOPs & \multicolumn{2}{c}{SQuAD 1.1 dev} & Params \\
 & & EM & F1 & \\
BERT & 1.9 & 84.1 & 90.9 & 335 \\
\end{tabular}
"""


def test_a_mixed_header_row_composes_the_columns_it_leaves_unnamed(make_evidence):
    """A header row naming a metric in one column still leaves the others unnamed.

    Composition is per column, so the cost column keeps the name it already
    has and the dataset columns take the metric written beneath them.
    """
    cells = make_evidence({"paper/main.tex": _MIXED_HEADER_TEX}).latex.table_cells
    assert [(c.column_label, c.value) for c in cells] == [
        ("Train FLOPs", 1.9),
        ("SQuAD 1.1 dev EM", 84.1),
        ("SQuAD 1.1 dev F1", 90.9),
        ("Params", 335.0),
    ]


def test_a_column_the_first_row_named_is_not_renamed_by_the_row_beneath(make_evidence):
    """The row-level refusal protected an already-named column; per column, this does.

    ``top-1`` qualifies ``Accuracy`` rather than naming it, and a column
    composed with its qualifier reports a metric the table never named.
    """
    tex = r"""
\begin{tabular}{lcc}
Model & Accuracy & SQuAD \\
      & top-1 & F1 \\
Ours & 92.4 & 88.5 \\
\end{tabular}
"""
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [(c.column_label, c.value) for c in cells] == [("Accuracy", 92.4), ("SQuAD F1", 88.5)]


def test_a_second_row_stating_a_number_is_data_even_beneath_a_mixed_header(make_evidence):
    """The number test is the whole protection now that the row-level one is gone.

    A transposed table names its metrics down the first column, so ``AP``
    under an unnamed ``Method`` is exactly the pairing that now composes.
    Consuming that row drops the numbers it states.
    """
    tex = r"""
\begin{tabular}{lcc}
Method & GFLOPs & Score \\
AP & 42.0 & 40.1 \\
AP50 & 62.4 & 60.6 \\
\end{tabular}
"""
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [(c.row_label, c.column_label, c.value) for c in cells] == [
        ("AP", "GFLOPs", 42.0),
        ("AP", "Score", 40.1),
        ("AP50", "GFLOPs", 62.4),
        ("AP50", "Score", 60.6),
    ]


#: Two floats, one placing its caption before the tabular and one after, since
#: both are ordinary. The wrong binding here is silent: every cell would be
#: named after the other table's metric.
_TWO_FLOATS_TEX = r"""
\begin{table}[t]
\caption{WER (\%) on MLS}
\begin{tabular}{lc}
Model & CORAAL \\
Whisper & 20.2 \\
\end{tabular}
\end{table}
\begin{table*}[t]
\begin{tabular}{lc}
Model & CoVoST2 \\
Whisper & 29.1 \\
\end{tabular}
\caption{BLEU scores on CoVoST2}
\end{table*}
"""


def test_a_caption_is_bound_to_the_tabular_in_its_own_float(make_evidence):
    r"""A float may write ``\caption`` before or after the table it captions.

    So the two are bound by containment, never by proximity: the nearest
    caption to the first table here is the second table's.
    """
    cells = make_evidence({"paper/main.tex": _TWO_FLOATS_TEX}).latex.table_cells
    assert [(c.value, c.caption) for c in cells] == [
        (20.2, "WER (%) on MLS"),
        (29.1, "BLEU scores on CoVoST2"),
    ]


def test_a_tabular_in_no_float_carries_no_caption(make_evidence):
    """A caption is recorded only for a table written inside one.

    A file states several; picking the nearest, or the first, would caption a
    bare tabular after another table's.
    """
    tex = _TWO_FLOATS_TEX + _WRAPPED_HEADERS_TEX
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [(c.value, c.caption) for c in cells if c.caption is None] == [
        (3.4, None),
        (4.5, None),
    ]


def test_a_caption_is_dissolved_of_markup_and_bounded(make_evidence):
    """Repository content, so what is recorded from it is bounded.

    A command's braces go and the text they wrap stays, with a space in their
    place: the vocabulary that reads a caption is anchored on word boundaries.
    """
    body = "Top-1 accuracy on \\textbf{ImageNet}. " + "Trained for many epochs. " * 40
    tex = (
        "\\begin{table}\n\\caption{" + body + "}\n"
        "\\begin{tabular}{lc}\nModel & ImageNet \\\\\nOurs & 92.4 \\\\\n"
        "\\end{tabular}\n\\end{table}\n"
    )
    (cell,) = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert cell.caption is not None
    assert cell.caption.startswith("Top-1 accuracy on ImageNet .")
    assert len(cell.caption) == 300


@pytest.mark.parametrize("setup", [r"\captionsetup{font=small}", r"\captionsetup {font=small}"])
def test_captionsetup_is_not_the_caption(make_evidence, setup):
    r"""It configures the caption and precedes it often enough to matter.

    LaTeX ignores space between a control word and its argument, so matching on
    the prefix alone reads ``font=small`` as what the table reports.
    """
    tex = (
        "\\begin{table}\n" + setup + "\n\\caption{WER (\\%) on MLS}\n"
        "\\begin{tabular}{lc}\nModel & CORAAL \\\\\nOurs & 20.2 \\\\\n"
        "\\end{tabular}\n\\end{table}\n"
    )
    (cell,) = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert cell.caption == "WER (%) on MLS"


@pytest.mark.parametrize(
    "markup",
    [
        r"\begin{tabular}{lcc}",
        r"\begin{tabularx}{\textwidth}{lXX}",
        r"\begin{tabular*}{\linewidth}{lcc}",
        r"\begin{longtable}[c]{lcc}",
        r"\end{tabular}",
        r"\end{tabularx}",
        r"\end{tabular*}",
        r"\end{longtable}",
    ],
)
def test_a_table_opened_inside_a_cell_contributes_no_cell_text(markup):
    """A nested table's own markup is structure wherever it appears."""
    assert _ROW_MARKUP_RE.sub("", markup) == ""


# --- rows the paper attributes to somebody else ---------------------------


@pytest.mark.parametrize(
    "citation",
    [
        r"\cite{he2016deep}",
        r"\citep{he2016deep}",
        r"\citet{he2016deep}",
        r"\shortcite{he2016deep}",
        r"\autocite{he2016deep}",
        r"\cite[see][p.~4]{he2016deep}",
    ],
)
def test_a_citation_in_the_row_label_marks_the_row_as_prior_work(make_evidence, citation):
    r"""The one piece of markup that says a number came from another paper.

    ConvNeXt cites 34 of its 170 numeric rows this way. It has to be read
    before the cell cleanup runs: the cleanup erases the command name and
    leaves the bibliography key against the label, where ``ResNet-50~he2016deep``
    is indistinguishable from a model whose name ends in a year.
    """
    tex = (
        "\\begin{tabular}{lc}\n"
        "Model & Accuracy \\\\\n"
        "ResNet-50~" + citation + " & 76.1 \\\\\n"
        "Ours & 92.4 \\\\\n"
        "\\end{tabular}\n"
    )
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [(c.row_label.split("~")[0], c.value, c.prior_work) for c in cells] == [
        ("ResNet-50", 76.1, True),
        ("Ours", 92.4, False),
    ]


def test_a_citation_beside_a_number_does_not_mark_the_row(make_evidence):
    """A citation in a data cell annotates that number; it does not attribute it.

    A paper cites the source of a dataset, a metric definition or an evaluation
    protocol in the cell reporting its own result often enough that reading any
    citation in the row would demote whole tables of own results.
    """
    tex = (
        "\\begin{tabular}{lcc}\n"
        "Model & Accuracy & Protocol \\\\\n"
        "Ours & 92.4 & \\cite{russakovsky2015imagenet} \\\\\n"
        "\\end{tabular}\n"
    )
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [(c.value, c.prior_work) for c in cells] == [(92.4, False)]


_SECTION_TABLE_TEX = r"""
\begin{tabular}{lcc}
System & Accuracy & F1 \\
\multicolumn{3}{c}{Top Leaderboard Systems (Dec 10th, 2018)} \\
Human & 82.3 & 91.2 \\
\multicolumn{3}{c}{Published} \\
BiDAF+ELMo & 85.6 & 85.8 \\
\multicolumn{3}{c}{Ours} \\
TinyNet & 91.3 & 93.7 \\
\end{tabular}
"""


def test_a_full_width_section_row_marks_the_rows_beneath_it(make_evidence):
    """BERT's SQuAD shape: one table, partitioned into prior work and its own.

    The rows carry no citation at all, so the section header is the only thing
    on the page that says whose numbers these are. Both senses are written with
    identical markup, which is why an unrecognised label can default to
    neither. The trailing date qualifies the heading rather than naming it.
    """
    cells = make_evidence({"paper/main.tex": _SECTION_TABLE_TEX}).latex.table_cells
    assert [(c.row_label, c.value, c.prior_work) for c in cells] == [
        ("Human", 82.3, True),
        ("Human", 91.2, True),
        ("BiDAF+ELMo", 85.6, True),
        ("BiDAF+ELMo", 85.8, True),
        ("TinyNet", 91.3, False),
        ("TinyNet", 93.7, False),
    ]


@pytest.mark.parametrize(
    "label",
    [
        "ImageNet-22K pre-trained",  # ConvNeXt partitions by pre-training corpus
        "Self-supervised",  # DINO, by supervision regime
        "zero-shot",  # BLIP, by evaluation setting
        "our supervised training baselines",  # MAE: the authors' own baselines
    ],
)
def test_a_section_row_naming_no_owner_marks_nothing(make_evidence, label):
    """The common case by a wide margin, and the one that must abstain.

    Measured over twenty development papers, a full-width section row is far
    more often a corpus, a regime or a setting than a statement of ownership.
    Reading one as prior work would demote a paper's own results wholesale, so
    an unrecognised heading clears the sense rather than continuing or
    inventing one.
    """
    tex = (
        "\\begin{tabular}{lcc}\n"
        "System & Accuracy & F1 \\\\\n"
        "\\multicolumn{3}{c}{Published} \\\\\n"
        "BiDAF & 85.6 & 85.8 \\\\\n"
        "\\multicolumn{3}{c}{" + label + "} \\\\\n"
        "TinyNet & 91.3 & 93.7 \\\\\n"
        "\\end{tabular}\n"
    )
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [(c.value, c.prior_work) for c in cells] == [
        (85.6, True),
        (85.8, True),
        (91.3, False),
        (93.7, False),
    ]


def test_a_spanning_row_that_states_a_number_is_data_not_a_section(make_evidence):
    """The same bound the second-header test uses, for the same reason.

    A row stating no number yields no cell either way, so reading one as a
    section header can at worst mislabel the rows beneath it. Here the number
    row would otherwise read as a heading naming no owner and clear the sense
    ``Published`` set for the row that follows it.
    """
    tex = (
        "\\begin{tabular}{lcc}\n"
        "System & Accuracy & F1 \\\\\n"
        "\\multicolumn{3}{c}{Published} \\\\\n"
        "\\multicolumn{3}{c}{88.5} \\\\\n"
        "BiDAF & 85.6 & 85.8 \\\\\n"
        "\\end{tabular}\n"
    )
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [(c.value, c.prior_work) for c in cells] == [(85.6, True), (85.8, True)]


def test_a_partial_span_is_a_group_label_rather_than_a_section(make_evidence):
    """A heading that partitions a table spans it.

    One spanning two of five columns heads those two, and the rows below it in
    the other three are not below it at all, so it cannot say whose they are.
    """
    tex = (
        "\\begin{tabular}{lcccc}\n"
        "System & Dev & Dev & Test & Test \\\\\n"
        "\\multicolumn{2}{c}{Published} & & & \\\\\n"
        "BiDAF & 85.6 & 85.8 & 84.1 & 90.9 \\\\\n"
        "\\end{tabular}\n"
    )
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [(c.value, c.prior_work) for c in cells] == [
        (85.6, False),
        (85.8, False),
        (84.1, False),
        (90.9, False),
    ]


def test_a_spanning_label_beside_a_number_is_a_row_and_keeps_its_number(make_evidence):
    """A section row carries the row and nothing else; this one carries a value.

    The row is skipped once it is read as a heading, so admitting a row that
    also states a number would delete that number from the paper rather than
    merely mislabel it.
    """
    tex = (
        "\\begin{tabular}{lcc}\n"
        "System & Accuracy & F1 \\\\\n"
        "\\multicolumn{2}{c}{Published} & 88.5 \\\\\n"
        "\\end{tabular}\n"
    )
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [(c.value, c.prior_work) for c in cells] == [(88.5, False)]


#: ELECTRA's shape. Every row after the first header ends with the paper's own
#: ``\tsep`` rather than ``\\``, so a body split on ``\\`` alone reads the
#: second header row and the first body row as one row -- which states numbers,
#: so it is no longer a header, and the metric beneath each dataset is lost.
_MACRO_SEPARATOR_TEX = r"""
\newcommand{\tsep}	{\bstrut \\ \thinline}
\begin{tabular}{lcccc}
Model & \multicolumn{2}{c}{SQuAD} & \multicolumn{2}{c}{MNLI} \\
 & EM & F1 & EM & F1 \tsep
Ours & 84.1 & 90.9 & 80.5 & 88.1 \tsep
\end{tabular}
"""


def test_a_row_separator_the_paper_defined_itself_ends_a_row(make_evidence):
    """A paper may name its own row break, and a table read without it collapses."""
    cells = make_evidence({"paper/main.tex": _MACRO_SEPARATOR_TEX}).latex.table_cells
    assert [(c.column_label, c.value) for c in cells] == [
        ("SQuAD EM", 84.1),
        ("SQuAD F1", 90.9),
        ("MNLI EM", 80.5),
        ("MNLI F1", 88.1),
    ]


def test_a_row_label_written_as_a_macro_keeps_the_name_it_prints(make_evidence):
    r"""BERT labels its own rows ``\bertlarge (Single)``.

    Unexpanded, the cleanup strips the command and the model name with it, so
    the paper's own results read ``(Single)`` -- which loses the one signal on
    the page saying whose result the row is, and states a claim text no reader
    can check against the paper.
    """
    tex = (
        "\\newcommand\\bertlarge{BERT$_{\\textsc{LARGE}}$}\n"
        "\\begin{tabular}{lc}\n"
        "System & F1 \\\\\n"
        "\\bertlarge (Single) & 90.9 \\\\\n"
        "\\end{tabular}\n"
    )
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [(c.row_label, c.value) for c in cells] == [("BERT_LARGE (Single)", 90.9)]


def test_a_spacing_macro_is_not_expanded_into_the_label_beside_it(make_evidence):
    r"""``\newcommand\tstrut{\rule{0pt}{2.6ex}}`` prints nothing and must stay unread.

    The cell cleanup erases a command by name but cannot dissolve one taking
    several arguments, so expanding this one replaces a token that vanished
    cleanly with two lengths that do not. Measured on MoCo, whose ``\shline``
    is the same shape, expanding it prefixed every row label in the paper with
    ``1pt``.
    """
    tex = (
        "\\newcommand\\tstrut{\\rule{0pt}{2.6ex}}\n"
        "\\begin{tabular}{lc}\n"
        "Model & Accuracy \\tstrut \\\\\n"
        "Ours & 92.4 \\\\\n"
        "\\end{tabular}\n"
    )
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [(c.column_label, c.value) for c in cells] == [("Accuracy", 92.4)]


def test_a_macro_whose_body_is_an_environment_is_not_expanded(make_evidence):
    """MoCo writes row labels as whole nested ``tabular`` environments.

    One textual pass cannot place an environment: substituted into a cell it
    opens a table inside a table, and the enclosing parse loses the rows it was
    reading.
    """
    tex = (
        "\\newcommand{\\randinit}{\\begin{tabular}{cc} random init. & \\end{tabular}}\n"
        "\\begin{tabular}{lc}\n"
        "Model & Accuracy \\\\\n"
        "\\randinit & 61.8 \\\\\n"
        "\\end{tabular}\n"
    )
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [(c.row_label, c.column_label, c.value) for c in cells] == [("", "Accuracy", 61.8)]


def test_a_separator_carrying_a_dimension_is_still_a_separator(make_evidence):
    r"""``\newcommand{\rowgap}{\\[4pt]}`` is a row break that also states a length.

    Refusing it for the length would cost the whole table, where refusing a
    spacing command costs one label, so a body that ends a row is expanded
    whatever else it says.
    """
    tex = (
        "\\newcommand{\\rowgap}{\\\\[4pt]}\n"
        "\\begin{tabular}{lc}\n"
        "Model & Accuracy \\rowgap\n"
        "Ours & 92.4 \\rowgap\n"
        "\\end{tabular}\n"
    )
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [(c.column_label, c.value) for c in cells] == [("Accuracy", 92.4)]


def test_a_parameterised_macro_is_not_expanded(make_evidence):
    r"""``\newcommand{\demph}[1]{...}`` takes its text from the call, not the body.

    Substituting the body alone would print the definition's own placeholder
    where the paper prints an argument.
    """
    tex = (
        "\\newcommand{\\demph}[1]{\\textcolor{gray}{#1}}\n"
        "\\begin{tabular}{lc}\n"
        "Model & Accuracy \\\\\n"
        "\\demph{Ours} & 92.4 \\\\\n"
        "\\end{tabular}\n"
    )
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [(c.row_label, c.value) for c in cells] == [("Ours", 92.4)]


def test_macro_expansion_does_not_recurse():
    """One pass, so no definition can expand through another or into itself.

    Paper sources are untrusted content: an expansion that recurses is a denial
    of service rather than a parsing mistake.
    """
    assert _expand_macros(r"\a", {"a": r"\b", "b": "reached"}) == r"\b"
    assert _expand_macros(r"\loop", {"loop": r"\loop"}) == r"\loop"


def test_macro_expansion_cannot_grow_a_document_without_bound():
    """Expansion adds at most as many characters as the source holds."""
    text = r"\wide " * 200
    expanded = _expand_macros(text, {"wide": "x" * 200})
    assert len(expanded) <= 2 * len(text)
    assert expanded.endswith(r"\wide ")


def test_a_macro_body_longer_than_the_bound_is_not_read():
    """A body that long is a document fragment, not a name."""
    body = "x" * _MAX_MACRO_BODY_CHARS
    assert _zero_argument_macros(["\\newcommand{\\essay}{" + body + "}"]) == {"essay": body}
    assert _zero_argument_macros(["\\newcommand{\\essay}{" + body + "x}"]) == {}


def test_expansion_does_not_move_the_line_a_claim_is_recorded_at(make_evidence):
    """A body spanning lines is one space in TeX, and must stay one line here.

    Substituting it verbatim moves every line after the first use: measured on
    DETR, a claim recorded at line 135 was reported at 145. The locator is what
    sends a reader to the number.
    """
    tex = (
        "\\newcommand{\\ours}{Tiny\n Net}\n"
        "\\ours reaches an accuracy of 92.4 on the test set.\n"
        "We also report an accuracy of 88.1 on the development set.\n"
    )
    metrics = make_evidence({"paper/main.tex": tex}).latex.metrics
    assert [(m.value, m.line) for m in metrics] == [(92.4, 3), (88.1, 4)]


def test_a_command_inside_a_verbatim_environment_is_not_expanded():
    """A verbatim block prints what it holds, so its commands are text."""
    text = "\\newcommand{\\sam}{SAM}\n\\begin{verbatim}\n\\sam\n\\end{verbatim}\n\\sam\n"
    expanded = _expand_macros(text, _zero_argument_macros([text]))
    assert "\\begin{verbatim}\n\\sam\n\\end{verbatim}" in expanded
    assert expanded.endswith("SAM\n")


#: DETR's shape, and the reason a definition has to be removed rather than
#: expanded. The body states a metric name against the parameter placeholder
#: the command will be given, so it reads as an IoU and a Dice of 1 -- and
#: expansion cannot reach either, in either direction, because substituting a
#: command at its uses leaves its definition exactly where it was.
_DEFINED_LOSS_TEX = r"""
\newcommand{\bloss}[1]{{\cal L}_{\rm box}(#1)}
\newcommand{\iouloss}[1]{{\cal L}_{\rm iou}(#1)}
\newcommand{\diceloss}[1]{{\cal L}_{\rm DICE}(#1)}
The model is trained with \bloss{b}, \iouloss{b} and \diceloss{m}.
"""


def test_a_number_inside_a_command_definition_is_not_a_number_the_paper_reports(make_evidence):
    r"""A definition states what a command means, and the page never shows it.

    Measured on DETR, these two lines are read as an IoU of 1 and a Dice of 1
    at confidence 0.5 -- four such claims in that paper, about numbers in no
    rendered document.
    """
    evidence = make_evidence({"paper/main.tex": _DEFINED_LOSS_TEX}).latex
    assert evidence.metrics == []


def test_a_definition_nested_inside_another_is_removed_with_it(make_evidence):
    r"""The llncs class defines ``\authcount`` inside its own ``\tableofcontents``.

    The spans a scan finds therefore overlap, and a caller removing them cannot
    remove the same characters twice. DETR carries this file, where the value
    read is the parameter placeholder ``##1`` of a counter assignment.
    """
    tex = (
        "\\def\\tableofcontents{\\section*{Contents}\n"
        "    \\def\\authcount##1{\\setcounter{auco}{##1}\\setcounter{@auth}{1}}\n"
        "    \\def\\lastand{\\ifnum\\value{auco}=2\\relax and\\fi}\n"
        "  }\n"
        "Our model reaches an accuracy of 92.4 on the test set.\n"
    )
    evidence = make_evidence({"paper/main.tex": tex}).latex
    assert [(m.name, m.value, m.line) for m in evidence.metrics] == [("accuracy", 92.4, 5)]


def test_a_definition_the_expansion_rewrote_states_nothing(make_evidence):
    r"""BERT's preamble holds ``\def\aclpaperid{1584}``, a submission number.

    Expansion substitutes the command at every use, and one of those uses is
    the definition's own name: the line becomes ``\def1584{1584}``, whose
    ``f1584`` is read as an F1 of 584. So the artifact is not merely left in
    place by expansion, it is manufactured there -- and a paper ID is not a
    measurement in any reading.
    """
    tex = "\\def\\aclpaperid{1584}\nOur model reaches an accuracy of 92.4 on the test set.\n"
    evidence = make_evidence({"paper/main.tex": tex}).latex
    assert [(m.name, m.value) for m in evidence.metrics] == [("accuracy", 92.4)]


def test_a_definitions_arity_is_not_a_hyperparameter(make_evidence):
    r"""LoRA defines ``\parheadsc``, whose ``heads`` is not a count of heads.

    The alias matches inside the command's own name and the value read is the
    ``[1]`` stating how many arguments it takes. Hyperparameters and metrics
    are read by the same pass, so both are answered by removing the definition.
    """
    tex = (
        "\\newcommand{\\parheadsc}[1]{\\medskip \\noindent \\textsc{#1}.}\n"
        "\\parheadsc{Setup} We train with 12 attention heads.\n"
    )
    evidence = make_evidence({"paper/main.tex": tex}).latex
    assert [(h.name, h.value) for h in evidence.hyperparameters] == [("num_heads", 12.0)]


def test_a_number_the_paper_prints_through_a_command_survives_at_its_use(make_evidence):
    """Removing a definition may not remove a number the page shows.

    What a body contributes is contributed where the command is used, and the
    expansion substitutes it there, so the number is read at the use -- which
    is also the line a reader would open to check it.
    """
    tex = (
        "\\newcommand{\\ourbleu}{70.4}\n"
        "\n"
        "Our model reaches a BLEU of \\ourbleu on the test split.\n"
    )
    evidence = make_evidence({"paper/main.tex": tex}).latex
    assert [(m.name, m.value, m.line) for m in evidence.metrics] == [("bleu", 70.4, 3)]


def test_removing_a_definition_does_not_move_the_line_a_claim_is_recorded_at(make_evidence):
    """A body spanning lines is replaced by its line breaks, not by nothing.

    A locator is what sends a reader to a number, and a definition standing
    above a result would otherwise pull every claim beneath it upwards.
    """
    tex = (
        "\\newcommand{\\ours}{Tiny\n"
        "    Net\n"
        "    Large}\n"
        "\\ours reaches an accuracy of 92.4 on the test set.\n"
    )
    evidence = make_evidence({"paper/main.tex": tex}).latex
    assert [(m.value, m.line) for m in evidence.metrics] == [(92.4, 4)]


#: A whole float wrapped in a macro and invoked in the body -- the CVPR/ICML
#: idiom that makes a definition's body page content rather than a name. The
#: real shapes: latent-diffusion's ``\firststagetablecomplete`` is defined in
#: ``ms_tables_supp.tex`` and invoked at ``ms.tex:1674``; StyleGAN2-ADA's
#: ``\figSmallDatasetImages`` is defined in ``figures.tex`` and invoked at
#: ``paper.tex:339``.
_MACRO_WRAPPED_FLOAT_TEX = r"""
\newcommand{\segmentationtable}{
\begin{table}
\begin{tabular}{lcc}
Model & IoU & Dice \\
Ours & 82.9 & 91.3 \\
\end{tabular}
\end{table}
}
The complete comparison is in Tab.~\ref{tab:seg}.
\segmentationtable
"""


def test_a_float_a_macro_wraps_is_still_read_as_a_table(make_evidence):
    r"""A body that opens an environment is page content, so it stays in place.

    The document invokes the command, so the float prints -- and
    :func:`_expand_macros` cannot put the body at that invocation, because one
    textual pass cannot place an environment. Removing the definition therefore
    deletes printed content with nothing put back. Measured: it took
    latent-diffusion from 624 table cells to 0 and StyleGAN2-ADA from 66 to 0.
    """
    cells = make_evidence({"paper/main.tex": _MACRO_WRAPPED_FLOAT_TEX}).latex.table_cells
    assert [(c.row_label, c.column_label, c.value) for c in cells] == [
        ("Ours", "IoU", 82.9),
        ("Ours", "Dice", 91.3),
    ]


def test_a_parameterised_macro_wrapping_a_float_keeps_its_page_content(make_evidence):
    r"""StyleGAN2-ADA's ``\figSmallDatasetImages`` takes an argument and prints.

    This is why the gate cannot be "whatever the expansion would accept": a
    parameterised body is exactly what :func:`_zero_argument_macros` refuses,
    so keeping only expandable bodies would strip this float -- and would also
    restore DETR's four parameterised false positives, which is the same test
    reaching the opposite answer on the same input.
    """
    tex = (
        "\\newcommand{\\figresults}[1]{\n"
        "\\begin{figure}\n"
        "\\begin{tabular}{lc}\n"
        "Model & Recall \\\\\n"
        "Ours & 0.43 \\\\\n"
        "\\end{tabular}\n"
        "\\end{figure}\n"
        "}\n"
        "\\figresults{fig:results}\n"
    )
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [(c.row_label, c.column_label, c.value) for c in cells] == [("Ours", "Recall", 0.43)]


def test_a_float_body_past_the_expansion_length_bound_is_still_kept(make_evidence):
    r"""Whether a body prints does not vary with its size, so no bound applies.

    :data:`_MAX_MACRO_BODY_CHARS` bounds *substitution*, where an unbounded
    expansion of untrusted content is a denial of service. Nothing is
    substituted here, so the bound has no work to do -- and it would fall in the
    worst possible place: MoCo writes a printed ``tabular`` in 71 characters and
    latent-diffusion writes one in 3,150, with no fact separating them.
    """
    rows = "".join(f"Model{index} & {60 + index}.5 \\\\\n" for index in range(40))
    tex = (
        "\\newcommand{\\bigtable}{\n"
        "\\begin{tabular}{lc}\n"
        "Model & Accuracy \\\\\n" + rows + "\\end{tabular}\n"
        "}\n"
        "\\bigtable\n"
    )
    assert len(tex) > _MAX_MACRO_BODY_CHARS
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert len(cells) == 40
    assert (cells[0].row_label, cells[0].column_label, cells[0].value) == ("Model0", "Accuracy", 60.5)


def test_a_markup_definition_inside_a_kept_float_is_still_removed(make_evidence):
    r"""Keeping a body does not make what is written inside it page content.

    A float's body routinely sets up its own markup -- StyleGAN2-ADA's figure
    macros open with five ``\renewcommand`` lines -- and those inner
    definitions are names, not content. Here the inner one is BERT's
    ``\def\aclpaperid{1584}``, whose ``f1584`` reads as an F1 of 584 once
    expansion has rewritten it to ``\def1584{1584}``; the enclosing float must
    survive without it.
    """
    tex = (
        "\\newcommand{\\resultstable}{\n"
        "\\def\\aclpaperid{1584}\n"
        "\\begin{tabular}{lc}\n"
        "Model & Accuracy \\\\\n"
        "Ours & 92.4 \\\\\n"
        "\\end{tabular}\n"
        "}\n"
        "\\resultstable\n"
    )
    evidence = make_evidence({"paper/main.tex": tex}).latex
    assert [(c.row_label, c.column_label, c.value) for c in evidence.table_cells] == [
        ("Ours", "Accuracy", 92.4)
    ]
    # The keyword scan reads the column header beside the cell, which is the
    # number the page really states; what must not be here is an ``f1``.
    assert [(m.name, m.value) for m in evidence.metrics] == [("accuracy", 92.4)]


def test_a_table_inside_a_definition_costs_only_the_index_a_cell_records(make_evidence):
    r"""MoCo writes row labels as whole nested ``tabular`` environments.

    Those bodies print where the label is used, so the definition is left in
    place and the body is parsed as a table in its own right, numbering the real
    ones from further along. That is the whole cost: a row-label tabular states
    no number, so it yields no cell, and ``table_index`` is read by nothing
    outside the collector -- not by a claim, not by a report, not by the
    precision join key. Measured on MoCo, 12 such phantom tables over 19 real
    ones and its 260 cells identical either way, ``-0 +0`` on the multiset of
    ``(row, column, value, file, line)``.

    This test formerly asserted the opposite, that the definition was removed
    and the real table numbered 0. Tidying the index that way was measured to
    destroy whole floats on the papers that wrap one in a macro:
    latent-diffusion lost all 624 of its cells and StyleGAN2-ADA all 66.
    """
    tex = (
        "\\newcommand{\\randinit}{\\begin{tabular}{cc} random init. & \\end{tabular}}\n"
        "\\begin{tabular}{lc}\n"
        "Model & Accuracy \\\\\n"
        "\\randinit & 61.8 \\\\\n"
        "\\end{tabular}\n"
    )
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [(c.table_index, c.column_label, c.value) for c in cells] == [(1, "Accuracy", 61.8)]


def test_a_definition_whose_body_cannot_be_matched_is_left_alone():
    """An unbalanced body is malformed, and guessing where it ends removes text.

    The document is broken there in a way one pass cannot repair, so the
    definition is reported as no definition at all rather than as one running
    to the end of the file.
    """
    text = "\\newcommand{\\ours}{Tiny\nOur model reaches an accuracy of 92.4.\n"
    assert _strip_definitions(text) == text


def test_a_definition_inside_a_verbatim_environment_is_left_in_place():
    """A verbatim block prints what it holds, so a definition there is shown text.

    The same reason the expansion skips those regions: what the page displays
    is the definition itself, and removing it would rewrite the page.
    """
    text = (
        "\\newcommand{\\ours}{TinyNet}\n"
        "\\begin{verbatim}\n"
        "\\newcommand{\\ours}{TinyNet}\n"
        "\\end{verbatim}\n"
    )
    stripped = _strip_definitions(text)
    assert stripped == "\n\\begin{verbatim}\n\\newcommand{\\ours}{TinyNet}\n\\end{verbatim}\n"


def test_a_citation_key_is_not_part_of_a_row_label(make_evidence):
    r"""``Swin-T~\cite{Liu2021swin}`` labels a row ``Swin-T``.

    The generic cell cleanup erases a command name and its braces but keeps what
    was between them, so the bibliography key survived glued to the label, where
    nothing downstream can tell it from part of a model's name. Measured, ConvNeXt
    shipped ``RegNetY-16G~Radosavovic2020designing`` as a claim's text.
    """
    tex = (
        "\\begin{tabular}{lc}\n"
        "Model & Accuracy \\\\\n"
        "Swin-T~\\cite{Liu2021swin} & 88.1 \\\\\n"
        "\\end{tabular}\n"
    )
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [(c.row_label, c.column_label, c.value) for c in cells] == [("Swin-T", "Accuracy", 88.1)]


def test_a_row_whose_citation_was_dissolved_is_still_marked_prior_work(make_evidence):
    """Removing the citation from the label must not remove the signal it carried.

    The attribution flag is read from the raw row and the label is cleaned from a
    copy, so the two cannot interfere. Were they one pass, dissolving the citation
    would silently promote every quoted baseline to a confident own result.
    """
    tex = (
        "\\begin{tabular}{lc}\n"
        "Model & Accuracy \\\\\n"
        "Swin-T~\\cite{Liu2021swin} & 88.1 \\\\\n"
        "Ours & 91.4 \\\\\n"
        "\\end{tabular}\n"
    )
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [(c.row_label, c.prior_work) for c in cells] == [("Swin-T", True), ("Ours", False)]


def test_a_cmidrule_trim_specification_is_not_a_row_label(make_evidence):
    r"""``\cmidrule(lr){2-3}`` is a rule, and a rule labels nothing.

    Only ``\cline`` was recognised, so booktabs' spelling left its trim and column
    range in the label of the row beneath it: BarlowTwins drew a row labelled
    ``(lr)2-3(lr)4-5`` and t5 carried the residue on 2,440 cells.
    """
    tex = (
        "\\begin{tabular}{lcc}\n"
        "Model & Accuracy & F1 \\\\\n"
        "\\cmidrule(lr){2-3}\\cmidrule(lr){3-3}\n"
        "Ours & 91.4 & 84.2 \\\\\n"
        "\\end{tabular}\n"
    )
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert {c.row_label for c in cells} == {"Ours"}


def test_a_row_colour_is_not_part_of_a_row_label(make_evidence):
    r"""``\rowcolor[gray]{.95}`` colours a row; it does not name one.

    The colour model and the shade survived as a prefix on every label beneath:
    ConvNeXt's rows all read ``[gray].95...``, and DINO's read ``Light``, which is
    a colour name rather than a model. Reached through a macro here, which is how
    ConvNeXt writes it and why the row-markup pass must run after expansion.
    """
    tex = (
        "\\newcommand{\\gr}{\\rowcolor[gray]{.95}}\n"
        "\\begin{tabular}{lc}\n"
        "Model & Accuracy \\\\\n"
        "\\gr Ours & 91.4 \\\\\n"
        "\\rowcolor{Light}\n"
        "Theirs & 88.1 \\\\\n"
        "\\end{tabular}\n"
    )
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [(c.row_label, c.value) for c in cells] == [("Ours", 91.4), ("Theirs", 88.1)]


def test_a_horizontal_skip_is_neither_a_column_name_nor_a_value(make_evidence):
    r"""``\hspace{-0.3em}Gender\hspace{-0.3em}`` heads a column named ``Gender``.

    A skip has no brace-free spelling the residue pattern in ``claims`` would
    catch -- no brace, no backslash, no ``=``, no leading digit -- so
    ``-0.3emGender-0.3em`` passed as a plausible metric name. Worse, CLIP wraps its
    *values* the same way, and ``-0.9em91.4-0.4em`` states no number the parser can
    find: 2,025 of its result cells were dropped outright.
    """
    tex = (
        "\\begin{tabular}{lc}\n"
        "Model & \\hspace{-0.3em}Accuracy\\hspace{-0.3em} \\\\\n"
        "Ours & \\hspace{-0.9em}91.4\\hspace{-0.4em} \\\\\n"
        "\\end{tabular}\n"
    )
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [(c.column_label, c.value) for c in cells] == [("Accuracy", 91.4)]


def test_a_rule_thickness_is_not_part_of_the_label_beneath_it(make_evidence):
    r"""``\Xhline{1.0pt}`` draws a rule 1pt thick; it names nothing.

    Only the booktabs and plain spellings were recognised, so ``makecell``'s left
    its thickness standing where the next row's first cell begins. Measured, Swin
    heads 24 cells ``1.0pt (a) Various frameworks AP^box`` and ConvNeXt labels 14
    rows ``0.3 Swin-T`` -- the ``0.3`` of an ``\Xhline{0.3\arrayrulewidth}``, a
    rule three-tenths of the default thickness read as part of a model's name.

    Both positions are asserted, because the residue lands somewhere different in
    each. Ahead of a *body* row it prefixes that row's label; ahead of a spanned
    sub-caption it joins the first header row, and composition then copies it onto
    every column name beneath -- which is the shape Swin has, and the reason its
    count is 24 cells rather than one row's worth.
    """
    tex = (
        "\\begin{tabular}{lc}\n"
        "\\Xhline{1.0pt}\n"
        "\\multicolumn{2}{c}{(a) Various frameworks} \\\\\n"
        "Model & Accuracy \\\\\n"
        "\\Xhline{0.3\\arrayrulewidth}\n"
        "Swin-T & 88.1 \\\\\n"
        "\\end{tabular}\n"
    )
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [(c.row_label, c.column_label, c.value) for c in cells] == [
        ("Swin-T", "(a) Various frameworks Accuracy", 88.1)
    ]


def test_both_arguments_of_a_font_size_leave_the_cell(make_evidence):
    r"""``\fontsize{7.5pt}{1em}`` sets a size and a baseline skip, and prints neither.

    The generic cleanup keeps what sits between the braces, so a command whose
    arguments are *all* discarded needs all of them removed: dissolving one of the
    two would leave ``1em`` exactly where ``7.5pt1em`` was. Measured on MoCo, 43
    cells headed ``7.5pt1em COCO keypoint detection`` and ``7pt1em accuracy (\%)``.
    """
    tex = (
        "\\begin{tabular}{lc}\n"
        "\\fontsize{7.5pt}{1em}\\selectfont method"
        " & \\fontsize{7pt}{1em}\\selectfont COCO keypoint detection \\\\\n"
        "Ours & 91.4 \\\\\n"
        "\\end{tabular}\n"
    )
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [(c.row_label, c.column_label, c.value) for c in cells] == [
        ("Ours", "COCO keypoint detection", 91.4)
    ]


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        (r"\fontsize{7.5pt}{1em Accuracy", "7.5pt1em Accuracy"),  # second group unbalanced
        (r"\fontsize{7.5pt Accuracy", "7.5pt Accuracy"),  # first group unbalanced
    ],
)
def test_a_malformed_font_size_is_left_whole_for_the_cleanup(make_evidence, field, expected):
    """A command one of whose groups does not brace-match loses none of them.

    Removing the first argument of a two-argument command whose second is
    unbalanced would guess where the second ends, and guessing there removes text
    the paper prints. The generic cleanup still reduces what is left, so the cell
    degrades to the residue it had before rather than to something shorter.
    """
    tex = "\\begin{tabular}{lc}\nModel & " + field + " \\\\\nOurs & 91.4 \\\\\n\\end{tabular}\n"
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [c.column_label for c in cells] == [expected]


def test_a_cross_reference_key_is_not_a_row_label(make_evidence):
    r"""``\ref{tab:baseline}`` prints a number assigned at typesetting time.

    The generic cleanup keeps the key, so the key became the label. Measured, t5
    leads every body row of its Table 16 with one naming the main-body table that
    row restates, and 2,277 of its cells were labelled ``tab:baseline``,
    ``tab:architectures_results`` and eleven more keys of the same kind. What the
    reference prints is not knowable here, so the cell is emptied and the row is
    named by :func:`~adduce.evidence.latex._row_label` instead.
    """
    tex = (
        "\\begin{tabular}{llc}\n"
        "Table & Experiment & Accuracy \\\\\n"
        "\\ref{tab:baseline} & Baseline average & 83.28 \\\\\n"
        "\\autoref{tab:scaling} & 4x training steps & 85.33 \\\\\n"
        "\\end{tabular}\n"
    )
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [(c.row_label, c.value) for c in cells] == [
        ("Baseline average", 83.28),
        ("4x training steps", 85.33),
    ]


def test_a_row_whose_first_cell_is_empty_is_named_by_the_next_one(make_evidence):
    r"""A ``\multirow`` continuation row is named by the cell that follows the gap.

    The label was the first cell unconditionally, so every row whose first column
    was spent on something else -- a spanned label above it, a cross-reference --
    was called by the empty string. That is the condition ``claims.cluster`` cannot
    survive: it separates measurements by row and column and cannot see which table
    a cell came from, so identically named cells whose values round together become
    one claim. Measured, CLIP carried 1,775 such cells and t5 2,277.

    Swin's own markup, which is why the spanned label is written ``\multirow``. Its
    arguments are not dissolved -- that is a separate shape, and this test asserts
    only what the empty-cell search decides, so it does not depend on it.
    """
    tex = (
        "\\begin{tabular}{llc}\n"
        "Method & Backbone & FPS \\\\\n"
        "\\multirow{2}{*}{Cascade Mask R-CNN} & R-50 & 18.0 \\\\\n"
        " & Swin-T & 15.3 \\\\\n"
        "\\end{tabular}\n"
    )
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    continuation = [(c.row_label, c.value) for c in cells if c.value == 15.3]
    assert continuation == [("Swin-T", 15.3)]


def test_a_numeric_first_cell_is_still_the_row_label(make_evidence):
    """A number is a perfectly good name for a row, and stays the name.

    t5's Table 7 labels its rows by span length, and ``10`` is what one of them is
    called. Letting the search past an empty cell also override a *filled* numeric
    one cost bert 55 of its row labels and t5 that ``10``.
    """
    tex = (
        "\\begin{tabular}{lcc}\n"
        "Span length & EnFr & CNNDM \\\\\n"
        "10 & 39.49 & 19.24 \\\\\n"
        "\\end{tabular}\n"
    )
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [(c.row_label, c.column_label) for c in cells] == [("10", "EnFr"), ("10", "CNNDM")]


def test_a_row_whose_values_start_immediately_takes_no_name_from_them(make_evidence):
    """The search past an empty first cell stops at the first cell stating a number.

    That cell is already extracted as the row's first value, so reading it as the
    name as well would have one cell play both parts. An unnamed row is the honest
    answer, not a row named after one of its own measurements.
    """
    tex = (
        "\\begin{tabular}{lcc}\n"
        "Model & Accuracy & F1 \\\\\n"
        " & 91.4 & 84.2 \\\\\n"
        "\\end{tabular}\n"
    )
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [(c.row_label, c.column_label, c.value) for c in cells] == [
        ("", "Accuracy", 91.4),
        ("", "F1", 84.2),
    ]


def test_an_escaped_ampersand_does_not_separate_two_columns(make_evidence):
    r"""``$(\mathcal{J}$\&$\mathcal{F})_m$`` is one header, not two.

    ``\&`` prints an ampersand and separates nothing. Split as a separator it made
    DINO's DAVIS table seven headers over six columns, so every column after it was
    named by the wrong header -- 18 cells reported under ``F)_m`` and ``J_m``, and
    the header that should have carried them was never seen at all.
    """
    tex = (
        "\\begin{tabular}{lccc}\n"
        "Model & $ (\\mathcal{J}$\\&$\\mathcal{F})_m$ & $\\mathcal{J}_m$ & F1 \\\\\n"
        "Ours & 69.9 & 66.6 & 84.2 \\\\\n"
        "\\end{tabular}\n"
    )
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [(c.column_label, c.value) for c in cells] == [
        ("(J&F)_m", 69.9),
        ("J_m", 66.6),
        ("F1", 84.2),
    ]


def test_a_thin_space_is_not_part_of_a_label(make_evidence):
    r"""``\,`` is a thin space, and the generic cleanup cannot reach it.

    That pattern requires a letter after the backslash, so ``\,`` survived --
    ConvNeXt writes one ahead of every model name in a results table, 115 cells.
    Resolved to a space rather than erased, because the metric vocabulary carries
    ``\b`` anchors and gluing two words together defeats them.
    """
    tex = (
        "\\begin{tabular}{lc}\n"
        "Model & Accuracy \\\\\n"
        "\\,Ours\\, & 91.4 \\\\\n"
        "\\end{tabular}\n"
    )
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [c.row_label for c in cells] == ["Ours"]


def test_a_label_written_across_source_lines_is_read_as_one_line(make_evidence):
    """A header spanning two source lines kept its newline and its indentation.

    A label is what the page shows, and the page shows one line. It is also read
    by people, by the precision verdict key and by ``canonical_metric``, whose
    whole-name lookup is a dictionary hit that no amount of vocabulary can satisfy
    for a key carrying a stray newline.
    """
    tex = (
        "\\begin{tabular}{lcc}\n"
        "Model & \\multicolumn{2}{c}{Top-1\n"
        "    Accuracy} \\\\\n"
        "Ours & 91.4 & 88.1 \\\\\n"
        "\\end{tabular}\n"
    )
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [c.column_label for c in cells] == ["Top-1 Accuracy", "Top-1 Accuracy"]


def test_the_markup_residue_fixture_reads_as_eight_named_cells():
    """The synthetic case is what makes the corpus byte-identity checks see this.

    Asserted against the fixture itself rather than an inline copy, so the case
    cannot drift away from the behaviour it is there to pin. Without the fixes it
    yields six cells under three wrong column names; with them, eight cells whose
    row and column labels are what the page prints.
    """
    case = Path(__file__).resolve().parent.parent / "corpus" / "synthetic" / "synthetic_markup_residue"
    cells = collect_latex(scan_repository(case)).table_cells
    assert [(c.row_label, c.column_label, c.value) for c in cells] == [
        ("Swin-T", "Accuracy", 88.1),
        ("Swin-T", "(J&F)_m", 61.8),
        ("Swin-T", "J_m", 60.2),
        ("Swin-T", "F1", 79.5),
        ("Ours", "Accuracy", 91.4),
        ("Ours", "(J&F)_m", 69.9),
        ("Ours", "J_m", 66.6),
        ("Ours", "F1", 84.2),
    ]


def test_the_reference_row_label_fixture_names_every_row_the_page_names():
    """The second residue fixture, asserted against itself for the same reason.

    Every guard it plants is load-bearing here, which is what makes the case worth
    keeping: without the fixes the same eight cells read ``tab:baseline`` and
    ``0.3 tab:scaling`` for their rows and ``1.0pt (a) Ablations restated 7.5pt1em
    Accuracy`` for their columns. The last two rows are the controls -- a numeric
    label that must survive, and a row whose values start immediately, which must
    stay unnamed rather than be called after one of its own measurements.
    """
    case = (
        Path(__file__).resolve().parent.parent
        / "corpus"
        / "synthetic"
        / "synthetic_reference_row_label"
    )
    cells = collect_latex(scan_repository(case)).table_cells
    assert [(c.row_label, c.column_label, c.value) for c in cells] == [
        ("Baseline average", "(a) Ablations restated Accuracy", 88.1),
        ("Baseline average", "(a) Ablations restated F1", 79.5),
        ("Ours", "(a) Ablations restated Accuracy", 91.4),
        ("Ours", "(a) Ablations restated F1", 84.2),
        ("10", "(a) Ablations restated Accuracy", 89.3),
        ("10", "(a) Ablations restated F1", 81.0),
        ("", "(a) Ablations restated Accuracy", 90.0),
        ("", "(a) Ablations restated F1", 82.0),
    ]


def test_an_at_sign_is_a_letter_in_a_command_name(make_evidence):
    r"""``\makeatletter`` makes ``@`` a letter, and class code is written that way.

    A name class of ``[a-zA-Z]+`` does not recognise
    ``\def\@fs@pre{\hrule height.8pt depth0pt \kern2pt}`` as a definition at all,
    so the body stays in the document and the prose scan reads ``depth0pt`` as a
    layer count. Measured, MoCo and SimSiam each yielded a confident
    ``num_layers = 0.0`` from that one line.
    """
    tex = (
        "\\documentclass{article}\n"
        "\\makeatletter\n"
        "\\def\\@fs@pre{\\hrule height.8pt depth0pt \\kern2pt}\n"
        "\\makeatother\n"
        "\\begin{document}\n"
        "Our model reaches an accuracy of 91.4 on the held-out split.\n"
        "\\end{document}\n"
    )
    latex = make_evidence({"paper/main.tex": tex}).latex
    assert latex.hyperparameters == []
    assert [(m.name, m.value) for m in latex.metrics] == [("accuracy", 91.4)]


def test_a_counter_is_not_a_measurement(make_evidence):
    r"""``\setcounter{tocdepth}{2}`` sets how deep a contents list goes.

    It prints nothing, so it states no number the paper reports -- but it puts a
    keyword and a number two characters apart, which is the shape the keyword scan
    reads as a value. Measured on DETR, ``depth}{2`` was reported as
    ``num_layers = 2.0``. This is a *use* of a command rather than a definition of
    one, so removing definitions cannot reach it.
    """
    tex = (
        "\\documentclass{article}\n"
        "\\setcounter{tocdepth}{2}\n"
        "\\addtocounter{secnumdepth}{1}\n"
        "\\newcounter{layers}\n"
        "\\setcounter{layers}{4}\n"
        "\\setlength{\\tabcolsep}{6pt}\n"
        "\\begin{document}\n"
        "Our model reaches an accuracy of 91.4 on the held-out split.\n"
        "\\end{document}\n"
    )
    latex = make_evidence({"paper/main.tex": tex}).latex
    assert latex.hyperparameters == []
    assert [(m.name, m.value) for m in latex.metrics] == [("accuracy", 91.4)]


#: One realistic call per command the state-command guard covers, each written so
#: that a keyword falls within the keyword scan's window of a number. Every one of
#: these was measured to yield a phantom hyperparameter without the guard --
#: including the two that assign nothing, because it is the *name* argument that
#: carries the keyword and removing the call is what stops the scan reaching a
#: number in the prose that follows: ``\newlength{\headsep}`` ahead of a sentence
#: mentioning 4 was read as ``num_heads = 4.0``.
_STATE_COMMAND_PROBES = {
    "setcounter": r"\setcounter{tocdepth}{2}",
    "addtocounter": r"\addtocounter{secnumdepth}{1}",
    "stepcounter": r"\stepcounter{layers}",
    "refstepcounter": r"\refstepcounter{layers}",
    "setlength": r"\setlength{\layersep}{4pt}",
    "addtolength": r"\addtolength{\layersep}{2pt}",
    "newcounter": r"\newcounter{layers}",
    "newlength": r"\newlength{\headsep}",
}


def test_every_covered_state_command_has_a_probe():
    """A command added to the guard without a probe would be an unexercised guard."""
    assert set(_STATE_COMMAND_PROBES) == set(_STATE_COMMANDS)


@pytest.mark.parametrize("command", sorted(_STATE_COMMAND_PROBES))
def test_a_typesetting_assignment_states_no_hyperparameter(command, make_evidence):
    """Each covered command, on its own, against a number close enough to be read."""
    tex = (
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        + _STATE_COMMAND_PROBES[command]
        + " We report 4 configurations.\n"
        "Our model reaches an accuracy of 91.4 on the held-out split.\n"
        "\\end{document}\n"
    )
    latex = make_evidence({"paper/main.tex": tex}).latex
    assert latex.hyperparameters == []
    assert [(m.name, m.value) for m in latex.metrics] == [("accuracy", 91.4)]


def test_removing_a_counter_assignment_does_not_move_the_line_beneath_it(make_evidence):
    """A locator is what sends a reader to a number, so the line count is kept."""
    tex = (
        "\\documentclass{article}\n"
        "\\setlength{\\tabcolsep}{6pt}\n"
        "\\setcounter{tocdepth}{2}\n"
        "\\begin{document}\n"
        "Our model reaches an accuracy of 91.4 on the held-out split.\n"
        "\\end{document}\n"
    )
    latex = make_evidence({"paper/main.tex": tex}).latex
    assert [(m.value, m.line) for m in latex.metrics] == [(91.4, 5)]


def test_a_malformed_counter_assignment_is_left_alone():
    r"""Every argument the command declares must be there for the call to go.

    ``\setcounter`` takes two groups. One written with one is malformed, and
    removing the name alone would leave its arguments standing as text -- which is
    the failure the removal exists to prevent, not a lesser version of it.
    """
    text = "\\setcounter{tocdepth 2} and the depth is 4 layers\n"
    assert _strip_state_commands(text) == text


def test_a_counter_assignment_a_paper_prints_is_left_in_place():
    """Inside a verbatim block the assignment is what the page displays."""
    text = (
        "\\setcounter{tocdepth}{2}\n"
        "\\begin{verbatim}\n"
        "\\setcounter{tocdepth}{2}\n"
        "\\end{verbatim}\n"
    )
    assert _strip_state_commands(text) == (
        "\n\\begin{verbatim}\n\\setcounter{tocdepth}{2}\n\\end{verbatim}\n"
    )


def _table(column: str, value: str) -> str:
    """A minimal two-row ``tabular``: one header, one numeric cell."""
    return (
        "\\begin{tabular}{lc}\n"
        "Model & " + column + " \\\\\n"
        "Ours & " + value + " \\\\\n"
        "\\end{tabular}\n"
    )


_ROOT_TEX = r"""
\documentclass{article}
\begin{document}
\input{sections/results}
\end{document}
"""

_NESTING_SECTION_TEX = "\\input{tables/main_table}\n" + _table("Accuracy", "92.4")
_NESTED_TABLE_TEX = _table("F1", "89.1")
#: A superseded draft, left in the tarball, reachable from no document.
_ORPHAN_TEX = _table("Accuracy", "71.2")


def test_a_tex_file_no_document_reaches_is_not_read(make_evidence):
    """A source tree keeps drafts the paper does not compile.

    Their numbers appear in no rendered document, so extracting them states a
    claim the paper never made -- with the same confidence as one it did.
    """
    latex = make_evidence(
        {
            "paper/main.tex": _ROOT_TEX,
            "paper/sections/results.tex": _table("Accuracy", "92.4"),
            "paper/ablations.tex": _ORPHAN_TEX,
        }
    ).latex
    assert latex.tex_files == ["paper/main.tex", "paper/sections/results.tex"]
    assert latex.main_file == "paper/main.tex"
    assert [c.value for c in latex.table_cells] == [92.4]


def test_an_include_is_followed_through_every_level(make_evidence):
    """Papers nest: a section inputs its tables, which is where the numbers are."""
    latex = make_evidence(
        {
            "paper/main.tex": _ROOT_TEX,
            "paper/sections/results.tex": _NESTING_SECTION_TEX,
            "paper/sections/tables/main_table.tex": _NESTED_TABLE_TEX,
            "paper/ablations.tex": _ORPHAN_TEX,
        }
    ).latex
    assert latex.tex_files == [
        "paper/main.tex",
        "paper/sections/results.tex",
        "paper/sections/tables/main_table.tex",
    ]
    assert [c.value for c in latex.table_cells] == [92.4, 89.1]


def test_a_commented_out_include_is_not_an_include(make_evidence):
    r"""``% \input{ablations}`` is how a draft is taken out of a paper."""
    latex = make_evidence(
        {
            "paper/main.tex": _ROOT_TEX.replace(
                "\\end{document}", "% \\input{ablations}\n\\end{document}"
            ),
            "paper/sections/results.tex": _table("Accuracy", "92.4"),
            "paper/ablations.tex": _ORPHAN_TEX,
        }
    ).latex
    assert "paper/ablations.tex" not in latex.tex_files
    assert not any(c.value == 71.2 for c in latex.table_cells)


def test_an_include_resolves_against_the_including_file_then_the_tree_root(make_evidence):
    """Both of LaTeX's search paths, and the implied ``.tex`` extension."""
    root = (
        "\\documentclass{article}\n"
        "\\input{tables/scores.tex}\n"
        "\\input{shared/appendix}\n"
    )
    latex = make_evidence(
        {
            "src/main.tex": root,
            "src/tables/scores.tex": _table("Accuracy", "88.1"),
            "shared/appendix.tex": _table("F1", "77.3"),
            "src/old.tex": _ORPHAN_TEX,
        }
    ).latex
    assert latex.tex_files == ["shared/appendix.tex", "src/main.tex", "src/tables/scores.tex"]
    assert sorted(c.value for c in latex.table_cells) == [77.3, 88.1]


def test_a_circular_include_terminates_and_reads_each_file_once(make_evidence):
    """Two sections inputting each other must not loop or double-count."""
    latex = make_evidence(
        {
            "paper/main.tex": "\\documentclass{article}\n\\input{a}\n",
            "paper/a.tex": "\\input{b}\n" + _table("Accuracy", "1.5"),
            "paper/b.tex": "\\input{a}\n\\input{b}\n" + _table("F1", "2.5"),
        }
    ).latex
    assert latex.tex_files == ["paper/a.tex", "paper/b.tex", "paper/main.tex"]
    assert [c.value for c in latex.table_cells] == [1.5, 2.5]


def test_a_paper_with_no_documentclass_is_read_whole(make_evidence):
    """A directory of fragments has no root to resolve, and must still report.

    Scoping to an include graph that cannot be found would turn every such
    paper into no evidence at all, which is the worse failure of the two.
    """
    latex = make_evidence(
        {
            "paper/results.tex": _table("Accuracy", "92.4"),
            "paper/ablations.tex": _ORPHAN_TEX,
        }
    ).latex
    assert latex.tex_files == ["paper/ablations.tex", "paper/results.tex"]
    assert latex.main_file is None
    assert sorted(c.value for c in latex.table_cells) == [71.2, 92.4]


def test_a_root_that_reaches_nothing_is_read_whole(make_evidence):
    """An inclusion mechanism this does not follow reads as no graph at all."""
    latex = make_evidence(
        {
            "paper/main.tex": "\\documentclass{article}\n\\subfile{sections/results}\n",
            "paper/sections/results.tex": _table("Accuracy", "92.4"),
        }
    ).latex
    assert latex.tex_files == ["paper/main.tex", "paper/sections/results.tex"]
    assert [c.value for c in latex.table_cells] == [92.4]


def test_config_collector_flattens_and_normalises(make_evidence):
    ev = make_evidence(
        {
            "configs/main.yaml": "optimizer:\n  lr: 0.0001\n  weight_decay: 0.01\ntrain:\n  batch_size: 256\n",
            "train.py": "import yaml\n",
        }
    )
    config = ev.config
    assert len(config.files) == 1
    assert config.files[0].values["optimizer.lr"] == 0.0001
    hp = config.hyperparameters()
    assert any(v == 0.0001 for v, _, _ in hp["learning_rate"])
    assert any(v == 256 for v, _, _ in hp["batch_size"])


def test_hydra_and_deepspeed_detection(make_evidence):
    ev = make_evidence(
        {
            "conf/config.yaml": "defaults:\n  - model: resnet\nlr: 0.001\n",
            "configs/ds_config.json": json.dumps({"zero_optimization": {"stage": 2}, "fp16": {"enabled": True}}),
            "train.py": "import hydra\n",
        }
    )
    assert ev.config.uses_hydra
    assert any(f.is_deepspeed for f in ev.config.files)


def _notebook(cells: list[dict], metadata: dict | None = None) -> str:
    return json.dumps(
        {"cells": cells, "metadata": metadata if metadata is not None else {"kernelspec": {"name": "python3"}, "language_info": {"name": "python"}}, "nbformat": 4, "nbformat_minor": 5}
    )


def _code_cell(source: str, count: int | None = None, outputs: bool = False) -> dict:
    return {
        "cell_type": "code",
        "source": [source],
        "execution_count": count,
        "outputs": [{"output_type": "stream", "text": "x"}] if outputs else [],
    }


def test_notebook_collector(make_evidence):
    disordered = _notebook(
        [
            _code_cell("import torch\n!pip install torch", count=5, outputs=True),
            _code_cell("df = pd.read_csv('/Users/alice/data.csv')", count=2),
            _code_cell("torch.rand(3)", count=9),
        ]
    )
    ev = make_evidence({"analysis.ipynb": disordered})
    nb = ev.notebooks.notebooks[0]
    assert not nb.monotonic and nb.has_gaps and nb.has_outputs
    assert nb.pip_install_cells and nb.abs_path_cells
    assert "torch" in nb.imports
    assert nb.uses_randomness and nb.seed_before_randomness is False


def test_notebook_companion_script_detected(make_evidence):
    clean = _notebook([_code_cell("print(1)", count=1, outputs=True)])
    ev = make_evidence({"analysis.ipynb": clean, "analysis.py": "print(1)\n"})
    assert ev.notebooks.notebooks[0].has_companion_script


def test_remote_collector_pins(make_evidence):
    sha = "8" * 40
    source = (
        "from transformers import AutoModel\n"
        "from datasets import load_dataset\n"
        "import torch\n"
        f"pinned = AutoModel.from_pretrained('bert-base-uncased', revision='{sha}')\n"
        "floating = AutoModel.from_pretrained('bert-base-uncased')\n"
        "tagged = AutoModel.from_pretrained('gpt2', revision='v1.0')\n"
        "ds = load_dataset('squad')\n"
        "hub = torch.hub.load('pytorch/vision:v0.10.0', 'resnet18')\n"
    )
    ev = make_evidence({"model.py": source, "get.sh": "wget https://example.org/weights.bin\n"})
    refs = ev.remote.references
    ordering = [(r.file, r.line, r.kind, r.spec, r.pin_detail) for r in refs]
    assert ordering == sorted(ordering)
    hf = [r for r in refs if r.kind == "hf"]
    assert sum(1 for r in hf if r.pinned) == 1
    assert any(r.pin_detail == "mutable-ref" for r in hf)
    assert any(r.kind == "torch_hub" and not r.pinned for r in refs)
    assert any(r.kind == "url" for r in refs)


def test_unrelated_checksum_file_does_not_cover_raw_download(make_evidence):
    ev = make_evidence(
        {
            "get.sh": "curl https://example.org/model.bin -o model.bin\n",
            "checksums.txt": "0" * 64 + "  different.bin\n",
        }
    )

    reference = next(r for r in ev.remote.references if r.kind == "url")

    assert reference.pin_detail == "none"
    assert RawUrlRule().evaluate(ev).status is Status.FAIL


def test_named_checksum_command_covers_its_raw_download(make_evidence):
    ev = make_evidence(
        {
            "get.sh": (
                "curl https://example.org/model.bin -o model.bin\n"
                "echo '"
                + "0" * 64
                + "  model.bin' | sha256sum -c -\n"
            )
        }
    )

    reference = next(r for r in ev.remote.references if r.kind == "url")

    assert reference.pin_detail == "checksum"
    assert RawUrlRule().evaluate(ev).status is Status.PASS


def test_checksum_probe_is_only_consulted_for_lines_carrying_a_reference(
    make_evidence, monkeypatch
):
    """The probe answers a question only the URL and bucket branches ask.

    It searches its own line for a download target and reads up to three more,
    so consulting it once per line made every line of every shell script and
    module pay for a question almost none of them raise.
    """
    from adduce.evidence import remote as remote_module

    original = remote_module._download_has_bound_checksum
    consulted: list[int] = []

    def counted(lines: list[str], index: int) -> bool:
        consulted.append(index)
        return original(lines, index)

    monkeypatch.setattr(remote_module, "_download_has_bound_checksum", counted)

    filler = "".join(f"value_{n}={n}\n" for n in range(200))
    ev = make_evidence(
        {"get.sh": filler + "curl https://example.org/model.bin -o model.bin\n"}
    )

    assert consulted == [200]
    assert [r.pin_detail for r in ev.remote.references if r.kind == "url"] == ["none"]


def test_precision_collector(make_evidence):
    source = (
        "import torch\n"
        "torch.backends.cuda.matmul.allow_tf32 = True\n"
        "torch.set_float32_matmul_precision('high')\n"
        "with torch.autocast('cuda', dtype=torch.bfloat16):\n"
        "    pass\n"
        "scaler = torch.cuda.amp.GradScaler()\n"
        "model = model.half()\n"
    )
    ev = make_evidence({"train.py": source})
    assert ev.precision.uses_tf32
    assert ev.precision.uses_amp
    assert ev.precision.uses_low_precision


def test_results_collector(make_evidence):
    ev = make_evidence(
        {
            "results/eval.csv": "epoch,accuracy,loss\n1,0.90,0.5\n2,0.921,0.4\n",
            "results/final.json": json.dumps({"ndcg@10": 0.8137, "runtime": 120}),
            "logs/events.out.tfevents.123.host": "binary",
        }
    )
    results = ev.results
    assert results.present and results.has_tensorboard
    accuracy = results.lookup_metric("accuracy")
    assert accuracy and 0.921 in accuracy[0][1]
    assert results.lookup_metric("ndcg@10")


def test_result_lookup_normalises_manifest_path_spelling(make_evidence):
    results = make_evidence(
        {"results/eval.csv": "epoch,accuracy\n1,0.9\n"}
    ).results

    assert results.lookup_metric("accuracy", path="./results/eval.csv")
    assert results.lookup_metric("accuracy", path=r"results\eval.csv")


def test_run_history_collector(make_evidence):
    ev = make_evidence(
        {
            "scripts/run_all.sh": (
                "#!/bin/bash\n"
                "#SBATCH --gres=gpu:2\n"
                "#SBATCH --time=12:00:00\n"
                "python train.py --config configs/main.yaml --seed 42 model.lr=0.0001\n"
            ),
            "outputs/2024-01-01/.hydra/config.yaml": "lr: 0.0002\nbatch_size: 128\n",
        }
    )
    runs = ev.runs
    assert runs.commands and runs.commands[0].seeds == (42,)
    assert runs.commands[0].config_path == "configs/main.yaml"
    assert ("model.lr", "0.0001") in runs.commands[0].overrides
    assert runs.slurm_scripts and runs.slurm_scripts[0].gpu_request
    assert runs.materialized and runs.materialized[0].source == "hydra"
    assert runs.materialized[0].values["lr"] == 0.0002


def test_portability_collector(make_evidence):
    ev = make_evidence(
        {
            "load.py": "path = '/Users/alice/data/train.csv'\nurl = 'http://localhost:8080/api'\n",
            "config.yaml": "key: AKIAIOSFODNN7EXAMPLE\n",
        }
    )
    kinds = {h.kind for h in ev.portability.hits}
    assert kinds == {"abs_path", "localhost", "secret"}
    secret = ev.portability.of_kind("secret")[0]
    assert "AKIA" not in secret.detail  # never echo the value


def test_secret_scanning_includes_documentation_and_manifest(make_evidence):
    ev = make_evidence(
        {
            "README.md": "Example accidentally contains ghp_" + "a" * 36 + "\n",
            ".adduce/manifest.yaml": "schema: adduce/1\ntoken: hf_" + "b" * 30 + "\n",
        }
    )

    hits = ev.portability.of_kind("secret")

    assert {hit.file for hit in hits} == {"README.md", ".adduce/manifest.yaml"}
    assert all("ghp_" not in hit.detail and "hf_" not in hit.detail for hit in hits)


def test_plural_keyword_is_a_count_not_a_value(make_evidence):
    tex = (
        "\\documentclass{article}\\begin{document}"
        "Results are averaged over 3 seeds with seed 42 as the base."
        "\\end{document}"
    )
    ev = make_evidence({"paper/main.tex": tex})
    seeds = ev.latex.hyperparameter_values().get("seed", [])
    # "3 seeds" is a count and must not be extracted; "seed 42" is a value.
    assert all(v.value != 3 for v in seeds)
    assert any(v.value == 42 for v in seeds)
