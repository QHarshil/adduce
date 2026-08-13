"""The research-artifact collectors: config, LaTeX, notebooks, remotes,
precision, results, run history, portability."""

from __future__ import annotations

import json

import pytest

from adduce.evidence.latex import (
    _MAX_MACRO_BODY_CHARS,
    _ROW_MARKUP_RE,
    _dissolve_multicolumn,
    _expand_macros,
    _zero_argument_macros,
)
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
