"""LaTeX evidence: the numeric claims the paper actually makes.

Best-effort by design. Comment stripping, scientific and LaTeX math notation
(``10^{-3}``, ``1\\times10^{-4}``), keyword-proximity hyperparameter
extraction, and ``tabular``-family table parsing cover the common shapes of ML
papers; everything extracted here feeds probabilistic rules (drift,
reconciliation) that report with confidence and never block.

Only the files the paper compiles are read. The include graph is resolved from
the ``\\documentclass`` roots, so a superseded draft left in a source tarball
is left out: its numbers reach no rendered document, and reporting them states
a claim the paper does not make. A tree the graph cannot explain -- fragments
with no root, or a root whose includes resolve to nothing -- falls back to
every ``.tex``, since no evidence at all is the worse failure.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field

from ..model import Repo
from ..naming import HYPERPARAM_SYNONYMS, METRIC_PATTERNS, canonical_metric

_INPUT_RE = re.compile(r"\\(?:input|include)\{([^}]+)\}")
_COMMENT_RE = re.compile(r"(?<!\\)%.*$", re.MULTILINE)

#: The row-bearing table environments, each with the number of brace groups it
#: takes before its body. ``tabularx`` and ``tabular*`` size themselves, so a
#: width precedes the column spec; ``longtable`` and ``tabular`` take the spec
#: alone. A paper that writes its results in ``tabularx`` has no tables at all
#: unless every name here is recognised.
_TABLE_ENVIRONMENTS: dict[str, int] = {
    "tabular": 1,
    "tabular*": 2,
    "tabularx": 2,
    "longtable": 1,
}
_ENVIRONMENTS_PATTERN = "|".join(re.escape(name) for name in _TABLE_ENVIRONMENTS)
#: The closing environment is a back-reference, so a stray ``\end{tabular}``
#: inside a ``longtable`` cannot end it and truncate the rows that follow.
_TABLE_RE = re.compile(
    r"\\begin\{(" + _ENVIRONMENTS_PATTERN + r")\}(\s*\[[^\]]*\])?(.*?)\\end\{\1\}",
    re.DOTALL,
)
#: Rules and delimiters carry no cell content and are removed before a row is
#: split into columns.
_ROW_MARKUP_RE = re.compile(
    r"\\(?:hline|toprule|midrule|bottomrule|cline\{[^}]*\}"
    r"|begin\{(?:" + _ENVIRONMENTS_PATTERN + r")\}(?:\[[^\]]*\])?(?:\{[^}]*\})*"
    r"|end\{(?:" + _ENVIRONMENTS_PATTERN + r")\})"
)
#: What is left of a cell once its structural wrappers are dissolved: a command
#: name with its opening brace, and stray braces or math delimiters.
_CELL_CLEANUP_RE = re.compile(r"\\[a-zA-Z]+\{?|[{}$]")
#: The two wrappers whose arguments would otherwise be read as cell content.
#: Both take their text as the last of several arguments, so the cleanup above
#: cannot dissolve them: it keeps every argument and concatenates them.
_ROTATEBOX_RE = re.compile(r"\\rotatebox\s*(?:\[[^\]]*\])?\s*")
_MULTICOLUMN_RE = re.compile(r"\\multicolumn\s*")
#: Wrappers nest (a ``\multicolumn`` holding a ``\rotatebox``), but not deeply;
#: the bound keeps a malformed cell from looping.
_MAX_MARKUP_NESTING = 8

#: The float a table's caption lives in. Closed by back-reference for the same
#: reason ``_TABLE_RE`` is: a ``table*`` must not be closed by ``\end{table}``.
_FLOAT_RE = re.compile(r"\\begin\{(table\*?)\}(.*?)\\end\{\1\}", re.DOTALL)
#: ``\caption``, ``\caption*``, and the optional short-title argument. The
#: negative lookahead keeps ``\captionsetup{...}`` -- which precedes the real
#: caption often enough to matter -- from being read as the caption itself. It
#: is one of two layers and not observable on its own: the search below skips a
#: match whose brace group does not parse, which reaches the same answer.
_CAPTION_RE = re.compile(r"\\caption\*?(?![a-zA-Z])\s*(?:\[[^\]]*\])?\s*")
#: An escape prints the character it escapes, so it is resolved rather than
#: dissolved: ``WER (\%)`` is what the page says.
_CAPTION_ESCAPE_RE = re.compile(r"\\([%&#_])")
#: Caption markup, dissolved the way a cell's is, with the substitution a space
#: rather than nothing so word boundaries survive: the vocabulary that reads a
#: caption carries ``\b`` anchors and gluing two words together defeats them.
_CAPTION_CLEANUP_RE = re.compile(r"\\[a-zA-Z]+\{?|[{}$\\~]")
#: A caption is repository content, so what is recorded from it is bounded. The
#: bound is generous enough for a caption's opening sentences, which is where a
#: paper names what its table reports.
_MAX_CAPTION_CHARS = 300

_CELL_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")

#: value patterns: 0.001 · 1e-4 · 3E-5 · $10^{-3}$ · 1\times10^{-4} · 5\cdot10^{-3} · 92.4\%
_NUMBER_PATTERN = r"""
    (?P<mant>\d+(?:\.\d+)?)
    (?:
        \s*(?:\\times|\\cdot|[xX*])\s*10\^\{?(?P<exp_times>-?\d+)\}?
        | [eE](?P<exp_e>-?\d+)
    )?
    | 10\^\{?(?P<exp_only>-?\d+)\}?
"""
_NUMBER_RE = re.compile(_NUMBER_PATTERN, re.VERBOSE)

#: The prose patterns now live in ``naming`` so a table header and a result
#: column canonicalise to the same metric name this collector uses. The
#: content is unchanged and a test pins it, because moving a vocabulary is
#: exactly the kind of edit that silently drops an alias.
_METRIC_KEYWORDS: dict[str, tuple[str, ...]] = METRIC_PATTERNS

_KNOWN_DATASETS = (
    "cifar-10", "cifar-100", "cifar10", "cifar100", "imagenet", "imagenet-1k", "mnist",
    "fashion-mnist", "svhn", "ml-25m", "ml-1m", "ml-20m", "movielens", "squad", "glue",
    "superglue", "sst-2", "imdb", "wikitext-2", "wikitext-103", "penn treebank", "ptb",
    "coco", "pascal voc", "ade20k", "cityscapes", "librispeech", "common voice", "wmt14",
    "wmt16", "iwslt", "ag news", "agnews", "yelp", "snli", "mnli", "boolq", "hellaswag",
    "mmlu", "gsm8k", "humaneval", "c4", "the pile", "laion", "celeba", "lsun", "kitti",
    "nuscenes", "shapenet", "modelnet", "qm9", "zinc", "ogbn", "cora", "citeseer", "pubmed",
)

_GPU_RE = re.compile(
    r"\b(a100|v100|h100|h200|a6000|rtx\s?\d{4}|titan\s?(x|xp|rtx|v)|t4|p100|k80|l4|l40s?|tpu(?:\s?v\d)?|mi\d{3})\b",
    re.IGNORECASE,
)
_RUNTIME_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(gpu[- ]hours?|hours?|days?|minutes?)\b", re.IGNORECASE)
_MULTISEED_RE = re.compile(
    r"(averaged?\s+over|mean\s+(?:and\s+std(?:\.|ev)?\s+)?(?:of|over|across)|across)\s+(\d+|three|five|ten)\s+(seeds?|runs?|trials?)"
    r"|(\d+|three|five|ten)\s+(?:random\s+)?(seeds?|runs?)|\\pm|\bstd(?:\.|ev)?\b|standard deviation|confidence interval",
    re.IGNORECASE,
)
_PRECISION_RE = re.compile(
    r"\b(fp16|bf16|bfloat16|float16|tf32|fp32|mixed[- ]precision|half[- ]precision|amp)\b", re.IGNORECASE
)


@dataclass(frozen=True)
class PaperValue:
    """A numeric value stated in the paper near a recognised keyword."""

    kind: str          # "hyperparameter" | "metric"
    name: str          # canonical hyperparameter or metric name
    value: float
    raw: str           # the matched source text
    file: str
    line: int


@dataclass(frozen=True)
class TableCell:
    table_index: int
    row_label: str
    column_label: str
    value: float
    file: str
    line: int
    #: The enclosing float's caption, cleaned of markup and length-bounded, or
    #: ``None`` when the tabular sits in no float or the float carries none.
    #: Recorded, not interpreted: whether a caption names this cell's metric is
    #: a vocabulary question and belongs to :mod:`adduce.claims`.
    caption: str | None = None


@dataclass
class LatexEvidence:
    tex_files: list[str] = field(default_factory=list)
    main_file: str | None = None
    title: str | None = None
    hyperparameters: list[PaperValue] = field(default_factory=list)
    metrics: list[PaperValue] = field(default_factory=list)
    table_cells: list[TableCell] = field(default_factory=list)
    datasets_mentioned: set[str] = field(default_factory=set)
    mentions_hardware: bool = False
    mentions_runtime: bool = False
    mentions_multiseed: bool = False
    mentions_precision: bool = False
    ablation_mentions: list[tuple[str, int]] = field(default_factory=list)  # (file, line)

    @property
    def has_paper(self) -> bool:
        return bool(self.tex_files)

    def hyperparameter_values(self) -> dict[str, list[PaperValue]]:
        grouped: dict[str, list[PaperValue]] = {}
        for pv in self.hyperparameters:
            grouped.setdefault(pv.name, []).append(pv)
        return grouped


def _parse_number(match: re.Match) -> float | None:
    try:
        if match.group("exp_only") is not None:
            return 10.0 ** int(match.group("exp_only"))
        mantissa = float(match.group("mant"))
        if match.group("exp_times") is not None:
            return mantissa * 10.0 ** int(match.group("exp_times"))
        if match.group("exp_e") is not None:
            return mantissa * 10.0 ** int(match.group("exp_e"))
        return mantissa
    except (TypeError, ValueError):
        return None


def strip_comments(text: str) -> str:
    return _COMMENT_RE.sub("", text)


def _line_of(text: str, position: int) -> int:
    return text.count("\n", 0, position) + 1


def _extract_keyword_values(
    text: str, file: str, keywords: dict[str, tuple[str, ...]], kind: str, window: int = 80
) -> list[PaperValue]:
    """Numbers appearing shortly after a keyword ("a learning rate of 1e-4")."""
    values: list[PaperValue] = []
    for canonical, patterns in keywords.items():
        for pattern in patterns:
            for kw_match in re.finditer(pattern, text, re.IGNORECASE):
                value: float | None = None
                raw = kw_match.group(0)
                # "50 epochs": a number immediately before the keyword wins,
                # since it is unambiguous. But a *pluralised* singular keyword
                # ("3 seeds", "8 layers" for the "layer" alias) is a count of
                # things, not the thing's value — skip those.
                plural = text[kw_match.end() : kw_match.end() + 1] == "s" and not kw_match.group(0).endswith("s")
                head = text[max(0, kw_match.start() - 16) : kw_match.start()]
                before = re.search(r"(?<![\w.-])(\d+(?:\.\d+)?)\s*$", head)
                if before and not plural:
                    value = float(before.group(1))
                    raw = (before.group(1) + " " + kw_match.group(0)).strip()
                if value is None:
                    # "learning rate of 1e-4": number shortly after the keyword.
                    tail = text[kw_match.end() : kw_match.end() + window]
                    connector = re.match(r"[\s\S]{0,24}?(?:of|is|was|to|=|at|:)?\s*\$?", tail)
                    search_from = connector.end() if connector else 0
                    num_match = _NUMBER_RE.search(tail, search_from)
                    if num_match and num_match.start() <= search_from + 16:
                        # Reject numbers glued to a word ("CIFAR-10") — those
                        # are names, not values.
                        preceding = tail[num_match.start() - 1 : num_match.start()]
                        if preceding == "" or not (preceding.isalpha() or preceding in "-_"):
                            value = _parse_number(num_match)
                            raw = (kw_match.group(0) + tail[: num_match.end()]).strip()
                if value is None:
                    continue
                values.append(
                    PaperValue(
                        kind=kind,
                        name=canonical,
                        value=value,
                        raw=raw[:120],
                        file=file,
                        line=_line_of(text, kw_match.start()),
                    )
                )
    return values


_HYPERPARAM_PATTERNS: dict[str, tuple[str, ...]] = {}
for _alias, _canonical in HYPERPARAM_SYNONYMS.items():
    if " " in _alias or len(_alias) >= 4:  # short aliases (lr, bs, k) are too noisy in prose
        _HYPERPARAM_PATTERNS.setdefault(_canonical, ())
        _HYPERPARAM_PATTERNS[_canonical] = (*_HYPERPARAM_PATTERNS[_canonical], re.escape(_alias).replace(r"\ ", r"[\s~-]+"))


def _brace_group(text: str, start: int) -> tuple[str, int] | None:
    """Contents of the ``{...}`` group at *start*, and the index just past it.

    Brace-matched rather than pattern-matched, so that the group boundary is
    right even when a cell's content is itself wrapped
    (``\\multicolumn{1}{c}{\\rotatebox{270}{WSJ}}``), where a non-greedy
    ``\\{[^}]*\\}`` would stop at the first inner close. The nesting and the
    escaped-brace skip are defensive rather than load-bearing: the callers
    splice the text following the group straight back on, and the cleanup below
    erases braces, so a matcher that mis-placed the boundary would currently
    reach the same cell text by a different route. Correct boundaries are still
    what the callers are entitled to assume.
    """
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    index = start
    while index < len(text):
        char = text[index]
        if char == "\\":  # an escaped brace is content, not structure
            index += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : index], index + 1
        index += 1
    return None


def _dissolve_rotatebox(field: str) -> str:
    """``\\rotatebox[origin=rc]{270}{TED-LIUM3}`` reads as ``TED-LIUM3``.

    The generic cleanup below strips a command name and its opening brace
    without regard to argument structure, which glues the rotation arguments to
    the text they rotate: a column named ``TED-LIUM3`` arrives as
    ``[origin=rc]270TED-LIUM3``. Rotated headers are how papers fit a dataset
    name over a narrow column, so this is a whole table's worth of names each
    time it happens.
    """
    for _ in range(_MAX_MARKUP_NESTING):
        match = _ROTATEBOX_RE.search(field)
        if match is None:
            break
        angle = _brace_group(field, match.end())
        content = _brace_group(field, angle[1]) if angle is not None else None
        if content is None:  # malformed; leave it to the generic cleanup
            break
        field = field[: match.start()] + content[0] + field[content[1] :]
    return field


def _dissolve_multicolumn(field: str) -> tuple[str, int]:
    """``\\multicolumn{2}{c}{ImageNet}`` reads as ``ImageNet`` spanning 2 columns.

    The span is returned rather than discarded because dropping it leaves the
    header row shorter than the body rows, so every column after the spanned one
    is attributed to the wrong header — or to none, and is named positionally.
    """
    match = _MULTICOLUMN_RE.search(field)
    if match is None:
        return field, 1
    span_group = _brace_group(field, match.end())
    alignment = _brace_group(field, span_group[1]) if span_group is not None else None
    content = _brace_group(field, alignment[1]) if alignment is not None else None
    if span_group is None or content is None:
        return field, 1
    try:
        span = int(span_group[0].strip())
    except ValueError:
        return field, 1
    if span < 1:
        return field, 1
    return field[: match.start()] + content[0] + field[content[1] :], span


def _split_columns(row: str) -> list[tuple[str, int]]:
    """One tabular row as ``(cell text, column span)`` pairs."""
    columns: list[tuple[str, int]] = []
    for raw in row.split("&"):
        content, span = _dissolve_multicolumn(_dissolve_rotatebox(raw))
        columns.append((_CELL_CLEANUP_RE.sub("", content).strip(), span))
    return columns


def _table_body(environment: str, body: str) -> str:
    """The rows of a table environment, without the arguments of its opening.

    A width and a column spec are not cell content: left in place they are
    split with the first row, where an alignment string or the thickness of a
    rule reads as the table's first value. The groups are brace-matched because
    a column spec nests (``p{3cm}``, ``@{\\extracolsep{\\fill}}``).
    """
    index = 0
    for _ in range(_TABLE_ENVIRONMENTS[environment]):
        group = _brace_group(body, index)
        if group is None:
            break
        index = group[1]
    return body[index:]


def _clean_caption(raw: str) -> str:
    text = _CAPTION_CLEANUP_RE.sub(" ", _CAPTION_ESCAPE_RE.sub(r"\1", raw))
    return " ".join(text.split())[:_MAX_CAPTION_CHARS]


def _float_captions(text: str) -> list[tuple[int, int, str]]:
    """``(start, end, caption)`` for every table float that carries a caption.

    A float may place its ``\\caption`` before or after the tabular it holds,
    so proximity cannot bind the two; containment can. Where a float carries
    several captions -- subtables -- the first is the float's own.
    """
    spans: list[tuple[int, int, str]] = []
    for match in _FLOAT_RE.finditer(text):
        body = match.group(2)
        for caption_match in _CAPTION_RE.finditer(body):
            group = _brace_group(body, caption_match.end())
            if group is None:
                continue
            caption = _clean_caption(group[0])
            if caption:
                spans.append((match.start(), match.end(), caption))
            break
    return spans


def _caption_at(spans: list[tuple[int, int, str]], position: int) -> str | None:
    """The caption of the innermost captioned float containing *position*.

    Containment, never nearest-neighbour: a caption belongs to the float it is
    written in, and one float's caption describes no other float's table.
    """
    best: tuple[int, str] | None = None
    for start, end, caption in spans:
        if start <= position < end and (best is None or end - start < best[0]):
            best = (end - start, caption)
    return None if best is None else best[1]


def _cell_number(cell: str) -> float | None:
    match = _CELL_NUMBER_RE.fullmatch(cell.replace("\\%", "").strip())
    return float(match.group(0)) if match else None


def _names_metric(cells: list[str]) -> bool:
    return any(canonical_metric(cell) is not None for cell in cells)


def _is_second_header(first: list[str], second: list[str]) -> bool:
    """Whether *second* is a header row naming the metric *first* leaves unnamed.

    Three conditions, all required. The second row states no number in any
    column; no cell of the first row names a metric; some cell of the second
    row does. A body row of a results table fails the first, a table whose
    columns are already metrics fails the second, and a units or group-label
    row fails the third.

    The first condition is also what bounds the cost of a wrong answer: a row
    stating no number yields no cell either way, so reading one as a header can
    never drop a value -- at worst it renames the columns beneath it.

    Mamba's zero-shot table is the shape this exists for: ``LAMBADA`` and
    ``HellaSwag`` head the columns with ``ppl``/``acc`` underneath, so the
    column names a dataset and the row beneath it names what was measured.
    """
    if any(_cell_number(cell) is not None for cell in second):
        return False
    return not _names_metric(first) and _names_metric(second)


def _compose_headers(first: list[str], second: list[str]) -> list[str]:
    """One header per column, dataset first and metric second: ``LAMBADA ppl``.

    Both are kept because both are read downstream: the metric is what the
    column reports and the dataset is what it reports it on, and a composed
    header canonicalises on its trailing metric word where the dataset alone
    canonicalises to nothing.
    """
    width = max(len(first), len(second))
    composed: list[str] = []
    for index in range(width):
        dataset = first[index] if index < len(first) else ""
        metric = second[index] if index < len(second) else ""
        composed.append(" ".join(part for part in (dataset, metric) if part))
    return composed


def _parse_tables(text: str, file: str) -> list[TableCell]:
    cells: list[TableCell] = []
    caption_spans = _float_captions(text)
    for table_index, tab_match in enumerate(_TABLE_RE.finditer(text)):
        caption = _caption_at(caption_spans, tab_match.start())
        body = _table_body(tab_match.group(1), tab_match.group(3))
        base_line = _line_of(text, tab_match.start())
        rows: list[list[tuple[str, int]]] = []
        for raw_row in body.split("\\\\"):
            columns = _split_columns(_ROW_MARKUP_RE.sub("", raw_row))
            if any(cell for cell, _ in columns):
                rows.append(columns)
        if len(rows) < 2:
            continue
        # A spanning header names every column it covers, so it is repeated
        # across the span. A spanning body cell states one number, so only its
        # first column carries it and the rest are placeholders holding the row
        # in step with the header.
        header = [cell for cell, span in rows[0] for _ in range(span)]
        second = [cell for cell, span in rows[1] for _ in range(span)]
        body_rows = rows[1:]
        if _is_second_header(header, second):
            header = _compose_headers(header, second)
            body_rows = rows[2:]
        for spanned_row in body_rows:
            row: list[str] = []
            for cell, span in spanned_row:
                row.append(cell)
                row.extend([""] * (span - 1))
            if not row:
                continue
            row_label = row[0]
            for col_index, cell in enumerate(row[1:], start=1):
                number = _cell_number(cell)
                if number is None:
                    continue
                column_label = header[col_index] if col_index < len(header) else f"col{col_index}"
                cells.append(
                    TableCell(
                        table_index=table_index,
                        row_label=row_label,
                        column_label=column_label,
                        value=number,
                        file=file,
                        line=base_line,
                        caption=caption,
                    )
                )
    return cells


def _resolve_include(target: str, including: str, known: set[str]) -> str | None:
    """The file an include names, resolved the way LaTeX resolves it.

    A missing ``.tex`` extension is implied, and the target is tried against
    the including file's directory before the tree root, which is how the
    sources of an e-print tarball reference each other.
    """
    if not target:
        return None
    for base in (posixpath.dirname(including), ""):
        for name in (target, f"{target}.tex"):
            candidate = posixpath.normpath(posixpath.join(base, name))
            if candidate in known:
                return candidate
    return None


def _compiled_sources(sources: dict[str, str]) -> set[str]:
    """The files reachable from a ``\\documentclass`` root, that root included.

    Repeated and circular includes are visited once. When the graph explains
    nothing -- no root, or a root that reaches no other file -- every source is
    returned instead, so a tree using an inclusion mechanism this does not
    follow keeps yielding evidence.
    """
    known = set(sources)
    roots = {path for path, text in sources.items() if "\\documentclass" in text}
    if not roots:
        return known
    reachable: set[str] = set()
    pending = sorted(roots)
    while pending:
        current = pending.pop()
        if current in reachable:
            continue
        reachable.add(current)
        for match in _INPUT_RE.finditer(sources[current]):
            resolved = _resolve_include(match.group(1).strip(), current, known)
            if resolved is not None:
                pending.append(resolved)
    return known if reachable == roots else reachable


def collect_latex(repo: Repo) -> LatexEvidence:
    evidence = LatexEvidence()
    tex_entries = [f for f in repo.files if f.suffix == ".tex"]
    if not tex_entries:
        return evidence
    sources: dict[str, str] = {}
    for entry in tex_entries:
        text = repo.read_text(entry.path)
        if text is not None:
            sources[str(entry.path)] = strip_comments(text)
    compiled = _compiled_sources(sources)
    evidence.tex_files = [str(f.path) for f in tex_entries if str(f.path) in compiled]

    for rel in evidence.tex_files:
        clean = sources[rel]
        if "\\documentclass" in clean and evidence.main_file is None:
            evidence.main_file = rel
        if evidence.title is None:
            title_match = re.search(r"\\title\{([^{}]+)\}", clean)
            if title_match:
                evidence.title = title_match.group(1).strip()

        evidence.hyperparameters.extend(
            _extract_keyword_values(clean, rel, _HYPERPARAM_PATTERNS, kind="hyperparameter")
        )
        evidence.metrics.extend(
            _extract_keyword_values(clean, rel, _METRIC_KEYWORDS, kind="metric")
        )
        evidence.table_cells.extend(_parse_tables(clean, rel))

        lowered = clean.lower()
        for dataset in _KNOWN_DATASETS:
            if dataset in lowered:
                evidence.datasets_mentioned.add(dataset)
        evidence.mentions_hardware = evidence.mentions_hardware or bool(_GPU_RE.search(clean))
        evidence.mentions_runtime = evidence.mentions_runtime or bool(_RUNTIME_RE.search(clean))
        evidence.mentions_multiseed = evidence.mentions_multiseed or bool(_MULTISEED_RE.search(clean))
        evidence.mentions_precision = evidence.mentions_precision or bool(_PRECISION_RE.search(clean))
        for ablation in re.finditer(r"\bablation", lowered):
            evidence.ablation_mentions.append((rel, _line_of(clean, ablation.start())))

    return evidence
