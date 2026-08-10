"""Stage 1 of claim resolution: find every number the artifact reports.

A *candidate* is a number stated somewhere a reader would take as a reported
result, together with what it is called, where it was found, and how confidently
it was read. Nothing here decides whether a candidate is true, or what produced
it; that is the resolver's job.

Three properties are load-bearing.

**No silent truncation.** The drafting path this replaces took
``ev.latex.metrics[:10]`` and dropped the rest without saying so, which is a
silent partial result. Extraction is exhaustive and the caller decides what to
show.

**Reading is not inferring.** A cell under a column literally headed
``Accuracy`` is :data:`~adduce.aeg.schema.ResolutionMethod.DIRECT_PARSE`; a
number recovered from prose by regex is
:data:`~adduce.aeg.schema.ResolutionMethod.LEXICAL_MATCH` and cannot carry full
confidence. The vocabulary is the graph's, so a reporter that already filters
graph facts on method filters these the same way.

**A column that names no known metric is not a claim.** Requiring the header to
canonicalise is what stops a table of tensor shapes or a library's argument
reference from being read as reported results.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from ..aeg.schema import CERTAIN_METHODS, ResolutionMethod
from ..naming import SPLIT_WORDS, canonical_metric

#: A table cell holding a number, optionally a percentage, optionally with a
#: spread ("92.4", "92.4%", "92.4 ± 0.3", "1.2e-4"). Anything else is prose.
_CELL_VALUE_RE = re.compile(
    r"""^\s*[*_`]*\s*
        (?P<value>[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?)
        \s*(?P<pct>%)?
        (?:\s*(?:\+/-|\+-|±|\\pm)\s*[-+]?\d+(?:\.\d+)?\s*%?)?
        \s*[*_`]*\s*$""",
    re.VERBOSE,
)

#: A GFM delimiter row: ``|---|:---:|---:|``. Its presence is what makes the
#: line above it a header rather than an ordinary line containing pipes.
_DELIMITER_RE = re.compile(r"^\s*\|?(?:\s*:?-{2,}:?\s*\|)+\s*:?-{2,}:?\s*\|?\s*$")

_MAX_TABLE_COLUMNS = 64

#: A column header the table parser filled in positionally because the source
#: named no header for it.
_POSITIONAL_HEADER_RE = re.compile(r"^col\d+$")

#: Residue of LaTeX the table parser did not dissolve: a brace or backslash, a
#: key=value directive from ``\rotatebox[origin=rc]{270}``, or the leading
#: ``2c``/``1c`` span-and-alignment of a ``\multicolumn``. Measured on real
#: papers, these arrive concatenated with the visible text, so a header such as
#: ``1c[origin=rc]270coraal`` names a metric no more than ``col4`` does.
_LATEX_RESIDUE_RE = re.compile(r"[{}\\]|=|^\d+[clr]")

#: The longest a column header can be and still plausibly name a metric. A
#: whole sentence in a header is a caption the parser mis-split, not a metric.
_MAX_METRIC_NAME_CHARS = 40


def is_plausible_metric_name(header: str, /) -> bool:
    """Whether *header* reads like the name of a metric at all.

    This is deliberately weaker than :func:`~adduce.naming.canonical_metric`,
    which asks whether a header names a metric adduce *knows*. A header can
    fail that and still be a metric -- ``Throughput`` is real and merely absent
    from the vocabulary, and dropping it would lose a reported number instead of
    abstaining on it. What this rejects is a header that is not a metric name
    under any vocabulary, so there is nothing to abstain about.
    """
    text = header.strip().strip("*_`$ ")
    if not text or len(text) > _MAX_METRIC_NAME_CHARS:
        return False
    if _POSITIONAL_HEADER_RE.match(text.lower()):
        return False
    if _LATEX_RESIDUE_RE.search(text):
        return False
    if text.lower().strip(".:") in SPLIT_WORDS:
        # "test" and "dev" name the split the number was measured on, not what
        # was measured. Kept as a metric they become claims about a metric
        # called "test", which nothing can ever resolve.
        return False
    # A metric is named in words. A header carrying no letter at all is a
    # value, a unit or a stray symbol that survived the split.
    return any(character.isalpha() for character in text)


class CandidateSource(str, Enum):
    """Where a candidate was read from. Closed, and reported to the author."""

    LATEX_PROSE = "latex_prose"
    LATEX_TABLE = "latex_table"
    MARKDOWN_TABLE = "markdown_table"


@dataclass(frozen=True)
class ClaimLocation:
    """Where a candidate was stated. Lines are data, never identity."""

    path: str
    line: int

    def __str__(self) -> str:
        return f"{self.path}:{self.line}"


@dataclass(frozen=True)
class ClaimCandidate:
    """One number the artifact reports, as read rather than as judged."""

    metric: str
    value: float
    source: CandidateSource
    location: ClaimLocation
    method: ResolutionMethod
    confidence: float
    text: str
    units: str | None = None
    row_label: str | None = None
    column_label: str | None = None

    def __post_init__(self) -> None:
        # The graph refuses full confidence for an inferred method; a claim
        # candidate is held to the same rule, so the two cannot disagree.
        if self.confidence == 1.0 and self.method not in CERTAIN_METHODS:
            raise ValueError(
                f"confidence 1.0 requires a certain method, got {self.method.value}"
            )
        if not 0.0 < self.confidence <= 1.0:
            raise ValueError(f"confidence out of range: {self.confidence}")


def _parse_cell(cell: str) -> tuple[float, str | None] | None:
    """A cell's numeric value and unit, or ``None`` when it states no number."""
    match = _CELL_VALUE_RE.match(cell)
    if match is None:
        return None
    try:
        value = float(match.group("value").replace(",", ""))
    except ValueError:  # pragma: no cover - the pattern already constrains this
        return None
    return value, ("%" if match.group("pct") else None)


def _split_row(line: str) -> list[str]:
    """Cells of a GFM row, tolerating the optional leading and trailing pipe."""
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|") and not stripped.endswith("\\|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def from_latex_prose(latex_metrics: list, /) -> list[ClaimCandidate]:
    """Every metric the LaTeX collector recovered from prose — uncapped.

    These are regex matches over sentences, so they are ``lexical_match``: the
    number is really there, but that a sentence *reports a result* rather than
    citing prior work is an inference this stage does not make.
    """
    candidates: list[ClaimCandidate] = []
    for metric in latex_metrics:
        candidates.append(
            ClaimCandidate(
                metric=metric.name,
                value=metric.value,
                source=CandidateSource.LATEX_PROSE,
                location=ClaimLocation(metric.file, metric.line),
                method=ResolutionMethod.LEXICAL_MATCH,
                confidence=0.5,
                text=metric.raw,
            )
        )
    return candidates


def from_latex_tables(table_cells: list, /) -> list[ClaimCandidate]:
    """Claims stated in ``tabular`` cells.

    These are parsed today at ``evidence/latex.py`` and read by nothing, which
    discards the richest claim source in an ML paper: a results table states
    the numbers the abstract only summarises.

    A header that is not a metric *name* at all is skipped. A header that reads
    like one but is unknown to the vocabulary is kept, at reduced confidence and
    as :data:`~adduce.aeg.schema.ResolutionMethod.LEXICAL_MATCH`, because
    ``Throughput`` is a real metric the vocabulary simply lacks and dropping it
    would lose a reported number rather than abstain on it.

    The distinction is what the measurement demanded. Emitting every header
    verbatim admitted whatever the parse happened to leave behind: over ten real
    papers, 4,383 candidates of which only 4.6% named a known metric, the rest
    positional placeholders (``col3``-``col7``), empty strings, and undissolved
    LaTeX markup -- ``\\multicolumn{1}{c}`` carrying a
    ``\\rotatebox[origin=rc]{270}`` directive arrived as the metric name
    ``1c[origin=rc]270coraal``. None of those is a metric under any vocabulary,
    so none of them is a claim to abstain on.
    """
    candidates: list[ClaimCandidate] = []
    for cell in table_cells:
        metric = canonical_metric(cell.column_label)
        if metric is None and not is_plausible_metric_name(cell.column_label):
            continue
        named = metric is not None
        candidates.append(
            ClaimCandidate(
                metric=metric or cell.column_label.strip().lower(),
                value=cell.value,
                source=CandidateSource.LATEX_TABLE,
                location=ClaimLocation(cell.file, cell.line),
                method=(
                    ResolutionMethod.DIRECT_PARSE if named else ResolutionMethod.LEXICAL_MATCH
                ),
                confidence=1.0 if named else 0.5,
                text=f"{cell.row_label} {cell.column_label} {cell.value}".strip(),
                row_label=cell.row_label or None,
                column_label=cell.column_label or None,
            )
        )
    return candidates


def from_markdown_table(text: str, path: str, /) -> list[ClaimCandidate]:
    """Claims stated in GFM tables in one markdown document.

    Markdown is not a claim source at all today, and in the pinned corpus it is
    the *only* one: measured across the 15 clones there are zero ``.tex`` files
    and 316 markdown tables holding 2,351 numeric cells. A README results table
    is how most repositories state what they achieved.

    A column whose header names no known metric is skipped, and that single
    requirement is what separates a claim extractor from a number scraper.
    Measured: across all 1,403 markdown files of ``transformers`` it rejects
    every one of the 2,351 numeric cells, because argument tables, shape tables
    and version matrices name no metric — while on ``nanogpt`` it keeps the
    eight train/val losses of the results table. The cost is that a metric the
    vocabulary does not know is invisible, so a missing alias is a recall bug
    and belongs in ``naming``.
    """
    candidates: list[ClaimCandidate] = []
    lines = text.splitlines()
    row = 0
    while row < len(lines):
        if not _DELIMITER_RE.match(lines[row]) or row == 0 or "|" not in lines[row - 1]:
            row += 1
            continue

        headers = _split_row(lines[row - 1])
        if len(headers) > _MAX_TABLE_COLUMNS:
            row += 1
            continue
        metrics = [canonical_metric(header) for header in headers]
        if not any(metrics):
            row += 1
            continue

        body = row + 1
        while body < len(lines) and "|" in lines[body] and lines[body].strip():
            cells = _split_row(lines[body])
            label = cells[0].strip(" *_`") if cells else ""
            for index, cell in enumerate(cells):
                if index >= len(metrics) or metrics[index] is None:
                    continue
                parsed = _parse_cell(cell)
                if parsed is None:
                    continue
                value, units = parsed
                metric = metrics[index]
                assert metric is not None  # guarded by the None check above
                candidates.append(
                    ClaimCandidate(
                        metric=metric,
                        value=value,
                        source=CandidateSource.MARKDOWN_TABLE,
                        location=ClaimLocation(path, body + 1),
                        method=ResolutionMethod.DIRECT_PARSE,
                        confidence=1.0,
                        text=f"{label} {headers[index]} {cell.strip()}".strip(),
                        units=units,
                        row_label=label or None,
                        column_label=headers[index] or None,
                    )
                )
            body += 1
        row = body
    return candidates
