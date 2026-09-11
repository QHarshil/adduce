"""LaTeX evidence: the numeric claims the paper actually makes.

Best-effort by design. Comment stripping, ``\\input`` following, scientific
and LaTeX math notation (``10^{-3}``, ``1\\times10^{-4}``), keyword-proximity
hyperparameter extraction, and ``tabular`` table parsing cover the common
shapes of ML papers; everything extracted here feeds probabilistic rules
(drift, reconciliation) that report with confidence and never block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..model import Repo
from ..naming import HYPERPARAM_SYNONYMS, METRIC_PATTERNS

_INPUT_RE = re.compile(r"\\(?:input|include)\{([^}]+)\}")
_COMMENT_RE = re.compile(r"(?<!\\)%.*$", re.MULTILINE)

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

#: The prose metric vocabulary, shared with claim extraction so a sentence, a
#: ``tabular`` header and a markdown column canonicalise to the same name. The
#: literal lives in ``naming``; this collector owns how it applies it.
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
#: The float a table's caption lives in. Closed by back-reference for the same
#: reason a starred environment needs one: a ``table*`` must not be closed by
#: ``\end{table}``.
_FLOAT_RE = re.compile(r"\\begin\{(table\*?)\}(.*?)\\end\{\1\}", re.DOTALL)
#: ``\caption``, ``\caption*``, and the optional short-title argument. The
#: negative lookahead keeps ``\captionsetup{...}`` -- which precedes the real
#: caption often enough to matter -- from being read as the caption itself.
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
#: A citation command, with its optional arguments. Matched against a row's
#: leading cell before markup is dissolved, because the dissolve turns
#: ``\cite{he2016}`` into the bibliography key and there is then nothing left
#: to recognise.
_CITATION_RE = re.compile(r"~?\\[a-zA-Z]*cite[a-zA-Z*]*\s*(?:\[[^\]]*\]\s*){0,2}\{")

#: Attribution written as prose rather than as a citation command. A related-work
#: table often names its rows "ResNet-50 (He et al.)" or "(Huang et al., 2017)"
#: with no ``\cite`` anywhere, and those rows carry someone else's numbers.
#:
#: This only ever demotes. A row matching here loses the certainty it would
#: otherwise get from a metric-named header; nothing is promoted and no claim is
#: removed, so a false match costs confidence rather than inventing or losing a
#: measurement. That asymmetry is why a deliberately loose pattern is safe here
#: and would not be in a detector whose positive result asserted something.
_PROSE_ATTRIBUTION_RE = re.compile(
    r"\bet\s+al\.?"          # "ResNet-50 (He et al.)"
    r"|\((?:19|20)\d{2}[a-z]?\)"  # "Kim and Park (2019)", "(2019a)"
    r"|\(\s*[A-Z][\w.'-]+[^)]*,\s*(?:19|20)\d{2}[a-z]?\s*\)",  # "(He et al., 2016)"
)


def _attributes_to_others(leading_cell: str) -> bool:
    """Whether a table row's leading cell credits work that is not this artifact's."""
    return (
        _CITATION_RE.search(leading_cell) is not None
        or _PROSE_ATTRIBUTION_RE.search(leading_cell) is not None
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
    #: How many digits the paper printed after the decimal point, or ``None``
    #: where that is not a meaningful question — see :func:`_printed_decimals`.
    decimals: int | None = None


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
    #: Whether the markup around this cell attributes its row to somebody else.
    #: One flag rather than one per signal, because a reader distinguishes none
    #: of them. Only the citation route sets it today: a row label citing a
    #: paper names the paper the whole row came from. A section header
    #: partitioning a table into published and own results is the other signal
    #: and needs a span-aware table parser this collector does not have, so a
    #: cell under one reads as ``False``, which is the conservative answer --
    #: the consumer demotes on ``True`` and never promotes on ``False``.
    prior_work: bool = False


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


_PRINTED_DECIMALS_RE = re.compile(r"\A\d+(?:\.(?P<fraction>\d+))?\Z")


def _printed_decimals(literal: str) -> int | None:
    """How many digits *literal* printed after the point, or ``None``.

    A paper that prints ``0.30`` has said something a paper printing ``0.3``
    has not: that the value is 0.3 to a hundredth. The comparison in
    :func:`~adduce.rules.drift.values_match` allows the code's value to differ
    by half of the last printed place, so the difference between those two is
    the difference between a tolerance of 0.005 and one of 0.05.

    **Inferring it from the parsed float instead is wrong in one direction, and
    that is the defect this exists to close.** A float cannot remember a
    trailing zero: ``f"{0.30:.10f}".rstrip("0")`` is ``"0.3"``, so a printed
    ``0.30`` was read as one decimal and given ten times the tolerance the
    paper stated. A paper stating 0.30 against code using 0.34 read as
    agreement. No amount of formatting recovers the digit; it has to come from
    the source text.

    ``None`` where the question is not meaningful, and the caller then infers
    what it always did. Scientific notation is the case: ``1e-4`` prints no
    fractional digits at all, yet states a value to a precision its decimal
    expansion is what expresses, so counting its printed digits would claim a
    tolerance of 0.5 on a number four orders of magnitude smaller. The same
    goes for anything this cannot parse as a plain decimal.
    """
    match = _PRINTED_DECIMALS_RE.match(literal.strip().lstrip("+-"))
    if match is None:
        return None
    fraction = match.group("fraction")
    return len(fraction) if fraction else 0


#: One argument of a command closing and the next opening. Adjacent up to
#: whitespace, because that is what two arguments of one command always are.
_SIBLING_GROUP_RE = re.compile(r"\}\s*\{")


def _crosses_group_boundary(gap: str) -> bool:
    r"""Whether *gap* leaves the keyword's argument for the next one.

    A keyword at the end of one brace group and a number at the start of the
    next are not a statement, they are two arguments of one command, and the
    number belongs to the argument the keyword is not in. The shape that
    matters is a fraction: a paper writes ``$\frac{\mbox{batch size}}{256}$``
    to say the learning rate scales with the batch, and the denominator was
    read as the batch size — a wrong number about the paper, not merely a
    missing one.

    **Adjacent up to whitespace, and the narrowness is the whole design.** Two
    arguments of one command are written against each other; a brace that
    closes and one that opens with anything between them is two different
    pieces of markup, and the number after it is routinely real. Allowing any
    text between the braces takes real values with it: a table header closing
    and an italic cell opening (``BLEU} & {\it 28.6``) is not one command's two
    arguments at all.

    Only the gap between the keyword and the number is examined. Searching the
    whole window instead is wrong in a way every test still passes: a brace
    opening *after* the number sets a boundary *before* it, so ``Batch size}:
    16`` and ``learning rate:} 0.003`` are refused along with the fractions.
    """
    return _SIBLING_GROUP_RE.search(gap) is not None


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
                literal: str | None = None
                if before and not plural:
                    value = float(before.group(1))
                    literal = before.group(1)
                    raw = (before.group(1) + " " + kw_match.group(0)).strip()
                if value is None:
                    # "learning rate of 1e-4": number shortly after the keyword.
                    tail = text[kw_match.end() : kw_match.end() + window]
                    connector = re.match(r"[\s\S]{0,24}?(?:of|is|was|to|=|at|:)?\s*\$?", tail)
                    search_from = connector.end() if connector else 0
                    num_match = _NUMBER_RE.search(tail, search_from)
                    if (
                        num_match
                        and num_match.start() <= search_from + 16
                        and not _crosses_group_boundary(tail[: num_match.start()])
                    ):
                        # Reject numbers glued to a word ("CIFAR-10") — those
                        # are names, not values.
                        preceding = tail[num_match.start() - 1 : num_match.start()]
                        if preceding == "" or not (preceding.isalpha() or preceding in "-_"):
                            value = _parse_number(num_match)
                            literal = num_match.group(0)
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
                        decimals=None if literal is None else _printed_decimals(literal),
                    )
                )
    return values


_HYPERPARAM_PATTERNS: dict[str, tuple[str, ...]] = {}
for _alias, _canonical in HYPERPARAM_SYNONYMS.items():
    if " " in _alias or len(_alias) >= 4:  # short aliases (lr, bs, k) are too noisy in prose
        _HYPERPARAM_PATTERNS.setdefault(_canonical, ())
        _HYPERPARAM_PATTERNS[_canonical] = (*_HYPERPARAM_PATTERNS[_canonical], re.escape(_alias).replace(r"\ ", r"[\s~-]+"))


def _brace_group(text: str, start: int) -> tuple[str, int] | None:
    r"""Contents of the ``{...}`` group at *start*, and the index just past it.

    Brace-matched rather than pattern-matched, so the group boundary is right
    even when the content is itself wrapped and a non-greedy ``\{[^}]*\}``
    would stop at the first inner close.
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


def _parse_tables(text: str, file: str) -> list[TableCell]:
    cells: list[TableCell] = []
    caption_spans = _float_captions(text)
    for table_index, tab_match in enumerate(
        re.finditer(r"\\begin\{tabular\}.*?\\end\{tabular\}", text, re.DOTALL)
    ):
        body = tab_match.group(0)
        caption = _caption_at(caption_spans, tab_match.start())
        base_line = _line_of(text, tab_match.start())
        rows: list[list[str]] = []
        cited: list[bool] = []
        for raw_row in body.split("\\\\"):
            cleaned = re.sub(r"\\(?:hline|toprule|midrule|bottomrule|cline\{[^}]*\}|begin\{tabular\}\{[^}]*\}|end\{tabular\})", "", raw_row)
            columns = [re.sub(r"\\[a-zA-Z]+\{?|[{}$]", "", c).strip() for c in cleaned.split("&")]
            if any(columns):
                rows.append(columns)
                # The label only, and before the cleanup: a citation beside a
                # number is a note on that number, while a citation in the row
                # label names the paper the whole row came from.
                cited.append(_attributes_to_others(cleaned.split("&")[0]))
        if len(rows) < 2:
            continue
        header = rows[0]
        for row_index, row in enumerate(rows[1:], start=1):
            if not row:
                continue
            row_label = row[0]
            for col_index, cell in enumerate(row[1:], start=1):
                num = re.fullmatch(r"-?\d+(?:\.\d+)?", cell.replace("\\%", "").strip())
                if not num:
                    continue
                # The header names this row's columns only when the two rows
                # have the same width. A spanning header cell collapses to one
                # cell here rather than repeating across the span, so a body
                # row wider than the header is offset against it and
                # ``header[col_index]`` would name some other column's metric
                # -- a confident wrong name, which is worse than none. Where
                # the widths disagree the columns are labelled positionally,
                # which is what the parser already reports for a column the
                # header does not reach.
                column_label = (
                    header[col_index] if len(row) == len(header) else f"col{col_index}"
                )
                cells.append(
                    TableCell(
                        table_index=table_index,
                        row_label=row_label,
                        column_label=column_label,
                        value=float(num.group(0)),
                        file=file,
                        line=base_line,
                        caption=caption,
                        prior_work=cited[row_index],
                    )
                )
    return cells


def collect_latex(repo: Repo) -> LatexEvidence:
    evidence = LatexEvidence()
    tex_entries = [f for f in repo.files if f.suffix == ".tex"]
    if not tex_entries:
        return evidence
    evidence.tex_files = [str(f.path) for f in tex_entries]

    for entry in tex_entries:
        text = repo.read_text(entry.path)
        if text is None:
            continue
        clean = strip_comments(text)
        rel = str(entry.path)
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
