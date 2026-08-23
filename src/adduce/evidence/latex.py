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
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from itertools import zip_longest

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
#: Rules, delimiters and row colouring carry no cell content and are removed
#: before a row is split into columns. Each is written with the arguments it
#: takes, because the generic cleanup below erases a command name and its
#: braces while keeping what sits between them: measured, ``\cmidrule(lr){2-3}``
#: leaves BarlowTwins a row labelled ``(lr)2-3(lr)4-5`` and t5 2,440 row labels
#: carrying a trim specification, and ``\rowcolor[gray]{.95}`` prefixes every
#: ConvNeXt row label with ``[gray].95``.
#:
#: ``\Xhline`` is the ``makecell`` package's rule of a stated thickness, and it
#: is how three of the twenty development papers draw the rule above a header.
#: Its argument is that thickness, so the generic cleanup leaves the dimension
#: standing where the row's first cell begins: measured, Swin heads 24 cells
#: ``1.0pt (a) Various frameworks ...`` and ConvNeXt labels 14 rows ``0.3 Swin-T``
#: and ``0.3 Swin-B^`` -- the ``0.3`` of an ``\Xhline{0.3\arrayrulewidth}``, a
#: rule three-tenths as thick as the default read as part of a model's name.
_ROW_MARKUP_RE = re.compile(
    r"\\(?:hline|toprule|midrule|bottomrule|cline\{[^}]*\}"
    r"|Xhline\{[^}]*\}"
    r"|cmidrule(?:\[[^\]]*\])?(?:\([a-zA-Z]*\))?\{[^}]*\}"
    r"|morecmidrules|addlinespace(?:\[[^\]]*\])?"
    r"|(?:row|cell|column)color(?:\[[^\]]*\])?\{[^}]*\}"
    r"|begin\{(?:" + _ENVIRONMENTS_PATTERN + r")\}(?:\[[^\]]*\])?(?:\{[^}]*\})*"
    r"|end\{(?:" + _ENVIRONMENTS_PATTERN + r")\})"
)
#: What is left of a cell once its structural wrappers are dissolved: a command
#: name with its opening brace, and stray braces or math delimiters.
_CELL_CLEANUP_RE = re.compile(r"\\[a-zA-Z]+\{?|[{}$]")
#: A tabular's column separator. ``\&`` prints an ampersand and separates
#: nothing, so only a bare ``&`` divides cells: measured on DINO, whose DAVIS
#: header writes ``$ (\mathcal{J}$\&$\mathcal{F})_m$``, splitting on the escaped
#: one made seven headers over six columns, so every column after it was named
#: by the wrong header and 18 cells reported ``F)_m`` and ``J_m``.
_COLUMN_SEPARATOR_RE = re.compile(r"(?<!\\)&")
#: The one escape resolved in a cell rather than left alone, because the split
#: above is what makes it ambiguous. ``\%`` is deliberately *not* resolved:
#: :func:`_cell_number` strips it to read a percentage, and a resolved ``%``
#: would leave a value the number pattern refuses.
_CELL_ESCAPE_RE = re.compile(r"\\&")
#: TeX's non-letter spacing escapes, resolved to a space rather than erased so
#: the words either side stay separate: the metric vocabulary carries ``\b``
#: anchors and gluing two words together defeats them. ``\,`` is what ConvNeXt
#: writes ahead of every model name in a results table, and the generic cleanup
#: cannot reach it -- it requires a letter after the backslash. ``~`` is
#: deliberately absent; see :data:`_CITATION_RE`.
_CELL_SPACE_RE = re.compile(r"\\[,;:!]")
#: Commands that print nothing and whose argument is not cell content: the
#: skips a paper uses to tighten a table. The generic cleanup keeps what sits
#: between the braces, so each leaves its dimension glued to the visible text --
#: measured, CLIP's ``\hspace{-0.3em}Gender\hspace{-0.3em}`` arrives as
#: ``-0.3emGender-0.3em`` and it heads a column, where the name is what decides
#: whether the cell is a claim at all.
_DISCARDED_ARGUMENT_RE = re.compile(r"\\[hv]space\*?\s*(?![a-zA-Z])")
#: The same family, taking two arguments rather than one. ``\fontsize{7pt}{1em}``
#: sets a size and the baseline skip that goes with it; both are dimensions and
#: neither is printed, so both must go, and removing only the first would leave
#: the second in the cell. Measured on MoCo, which opens each header cell of its
#: transfer tables with one: 43 cells arrive headed ``7.5pt1em COCO keypoint
#: detection`` and ``7pt1em accuracy (\%)``.
#:
#: ``\rule{0pt}{6ex}``, a strut of the same two-dimension shape, is deliberately
#: absent: it is in the corpus (DINO and ELECTRA) and leaves no residue in any
#: label there, so admitting it would be a guess rather than a measurement.
_DISCARDED_TWO_ARGUMENT_RE = re.compile(r"\\fontsize\s*(?![a-zA-Z])")
#: The two wrappers whose arguments would otherwise be read as cell content.
#: Both take their text as the last of several arguments, so the cleanup above
#: cannot dissolve them: it keeps every argument and concatenates them.
_ROTATEBOX_RE = re.compile(r"\\rotatebox\s*(?:\[[^\]]*\])?\s*")
_MULTICOLUMN_RE = re.compile(r"\\multicolumn\s*")
#: Wrappers nest (a ``\multicolumn`` holding a ``\rotatebox``), but not deeply;
#: the bound keeps a malformed cell from looping.
_MAX_MARKUP_NESTING = 8
# A group row over a dataset row over a metric row is the deepest header any
# paper in the dev set writes; see _header_depth for what a fourth would cost.
_MAX_HEADER_ROWS = 3

#: How a paper defines a command of its own, in the spellings that reach a
#: zero-argument definition: ``\newcommand{\tsep}{...}``, ``\newcommand\tsep{...}``,
#: ``\def\tsep{...}``, and the ``renew``/``provide`` variants. The body is not in
#: the pattern -- it is brace-matched from the end of the match, which is also
#: what excludes a parameterised definition: its ``[2]`` arity argument sits
#: where the body would be, so no group opens there.
#:
#: ``@`` is a letter in a command name, because ``\makeatletter`` makes it one
#: and class and style code lives inside that. Without it a definition such as
#: MoCo's ``\def\@fs@pre{\hrule height.8pt depth0pt \kern2pt}`` is not
#: recognised as a definition at all, so its body is left standing in the
#: document and read as prose: measured, MoCo and SimSiam each yielded a
#: confident ``num_layers = 0.0`` from the ``depth0pt`` in that one line.
_MACRO_DEFINITION_RE = re.compile(
    r"\\(?:(?:new|renew|provide)command\*?|def)\s*"
    r"(?:\{\s*\\([a-zA-Z@]+)\s*\}|\\([a-zA-Z@]+))\s*"
)
#: A command *use*, which deliberately does not admit ``@``. Recognising the
#: definition is what removes the body from the page; substituting an
#: ``@``-named command at its uses would put class-internal plumbing back into
#: the text, and no such command prints content a claim could be read from.
_MACRO_USE_RE = re.compile(r"\\([a-zA-Z]+)")
#: What may sit between a definition's name and its body: the arity and default
#: arguments LaTeX takes (``[1]``, ``[2][none]``) and the parameter text TeX
#: takes (``#1``, ``##1`` inside another definition). Bounded and newline-free,
#: so a definition written in a shape this does not recognise is left alone
#: rather than having an arbitrary run of the document read as its body.
_MACRO_PARAMETERS_RE = re.compile(r"(?:\s*\[[^\]\n]{0,60}\]|\s*#{1,2}\d)*\s*")
#: A verbatim-like environment prints its contents, so a command inside one is
#: text the paper shows rather than markup it uses, and expanding it would
#: rewrite the page.
_VERBATIM_RE = re.compile(
    r"\\begin\{(verbatim\*?|lstlisting|minted|alltt)\}.*?\\end\{\1\}", re.DOTALL
)
_ENVIRONMENT_RE = re.compile(r"\\(?:begin|end)\{")
#: A TeX dimension: a digit against a length unit.
_DIMENSION_RE = re.compile(r"\d\s*(?:pt|ex|em|in|mm|cm|pc|bp|dd|cc|sp)\b")
#: A body longer than this is a document fragment, not a name. The bound is one
#: of several: paper sources are untrusted content, and an expansion that can
#: grow without limit is a denial of service rather than a parsing mistake.
_MAX_MACRO_BODY_CHARS = 200

#: Commands that set a counter or a length, with the number of brace groups each
#: takes. A counter is not a measurement: its arguments name a piece of
#: typesetting state and the number it is set to, and neither is printed.
#: Measured on DETR, ``\setcounter{tocdepth}{2}`` was read as ``num_layers =
#: 2.0`` -- a table-of-contents depth reported as a layer count -- because the
#: ``depth`` alias matches inside ``tocdepth`` and the keyword scan then finds
#: the ``2`` two characters later. Definition-stripping cannot reach it: this is
#: a *use* of a command, not a definition of one.
_STATE_COMMANDS: dict[str, int] = {
    "setcounter": 2,
    "addtocounter": 2,
    "stepcounter": 1,
    "refstepcounter": 1,
    "setlength": 2,
    "addtolength": 2,
    "newcounter": 1,
    "newlength": 1,
}
_STATE_COMMAND_RE = re.compile(
    r"\\(" + "|".join(sorted(_STATE_COMMANDS, key=len, reverse=True)) + r")\s*(?![a-zA-Z])"
)

#: A citation command, in the natbib and biblatex spellings alike, with the
#: optional pre- and post-notes ``\cite[see][p.~4]{key}`` takes, up to and
#: including the brace opening its key. Read twice, for two different jobs: it
#: is matched against the raw row to decide whether the row is attributed to
#: somebody else, and it is what :func:`_dissolve_discarded` matches to remove
#: the key from the label. The two must stay separate -- the flag is set from
#: the raw row precisely so that dissolving the citation cannot erase the
#: signal it carries.
#:
#: The tie binding the citation to the word before it is part of the match.
#: ``Swin-T~\cite{Liu2021swin}`` is the standard idiom, so the ``~`` belongs to
#: the citation and not to the label, and leaving it behind would trade a
#: bibliography key for a trailing non-breaking space. It is taken here rather
#: than by resolving every ``~`` in a cell to a space, which was measured and
#: rejected: a header cell holding nothing but ``~`` is an empty cell, and
#: emptying it makes two ablation tables of SimSiam compose the identical header
#: ``acc. (%)``, whereupon ``claims.cluster`` -- which separates measurements by
#: row and column and cannot see which table they came from -- merges two
#: distinct 68.1 results into one claim and the pair loses a recall match.
_CITATION_RE = re.compile(r"~?\\[a-zA-Z]*cite[a-zA-Z*]*\s*(?:\[[^\]]*\]\s*){0,2}\{")
#: A cross-reference, whose argument is an internal key and whose rendered text
#: is a number assigned at typesetting time. The generic cleanup keeps the key,
#: so the key becomes the label: measured, t5's Table 16 leads every row with
#: ``\ref{tab:baseline}`` naming the main-body table that row restates, and 2,277
#: of its cells are labelled ``tab:baseline``, ``tab:architectures_results`` and
#: eleven more keys of the same kind.
#:
#: Removing it is *not* enough to give those rows a label, and this pattern is
#: not used to try: see :func:`_row_label`, which is what decides what a row is
#: called once a leading cross-reference has gone.
_CROSS_REFERENCE_RE = re.compile(r"~?\\(?:auto|name|page|eq|[cC])?ref\*?\s*(?:\[[^\]]*\]\s*)?\{")
#: A trailing parenthetical qualifies a section label rather than naming it:
#: BERT writes ``Top Leaderboard Systems (Dec 10th, 2018)``.
_SECTION_QUALIFIER_RE = re.compile(r"\s*\([^()]*\)\s*$")
#: Section labels that state the rows beneath them are somebody else's work.
#: Measured over the twenty development papers, a section row is far more often
#: a training regime, a setting or an architecture family than a statement of
#: ownership, so this is matched against the whole normalised label and not
#: searched within it. ``baselines`` is deliberately absent: MAE heads a section
#: ``our supervised training baselines``, which are the authors' own.
_PRIOR_WORK_SECTIONS = frozenset(
    {
        "published",
        "published results",
        "published methods",
        "published systems",
        "top leaderboard systems",
        "leaderboard systems",
        "prior work",
        "prior art",
        "prior methods",
        "previous work",
        "previous methods",
        "related work",
        "concurrent work",
        "existing methods",
        "other methods",
    }
)

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
    #: Whether the markup around this cell attributes its row to somebody else:
    #: the row label cites a paper, or the row sits under a section header
    #: naming prior work. One flag rather than one per signal, because the two
    #: are read identically and a field no reader distinguishes is a field that
    #: costs memory to record and nothing to drop.
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


def _is_cutoff(tail: str, number: re.Match[str], keyword: str) -> bool:
    r"""Whether *number* is the rank an ``@`` binds to a metric's name.

    ``Recall@1`` and ``B@4`` name the rank a retrieval metric was measured at,
    never a value it took, and the ``@`` falls either side of the keyword's own
    boundary: the pattern ``\brecall\b`` leaves it ahead of the number, and the
    pattern ``recall@`` takes it into the match, where nothing at all separates
    the keyword from the number. Both are read here, because a guard holding on
    one side only leaves the same sentence stating the same false number.

    Measured over the dev set, ten candidates of this shape, all of them
    BLIP's: ``recall@1`` read as a recall of 1 six times, ``BLEU@4`` as a BLEU
    of 4, and a header row's ``R@1`` as an MRR of 1, each at confidence 0.5.

    Only ``@`` binds a rank. The other characters sitting ahead of a candidate
    were measured too, and they carry numbers the paper does state: ``$H/64``
    gives ALBERT's head count and ``$\geq\!$~1024`` SimSiam's batch size.
    """
    if number.start() == 0:
        return keyword.endswith("@")
    return tail[number.start() - 1] == "@"


#: One argument of a command closing and the next opening. Adjacent up to
#: whitespace, because that is what two arguments of one command always are.
_SIBLING_GROUP_RE = re.compile(r"\}\s*\{")


def _crosses_group_boundary(gap: str) -> bool:
    r"""Whether *gap* leaves the keyword's argument for the next one.

    A keyword at the end of one brace group and a number at the start of the
    next are not a statement, they are two arguments of one command, and the
    number belongs to the argument the keyword is not in. The shape that
    matters is a fraction: BiT writes ``$\frac{\mbox{batch size}}{256}$`` to
    say the learning rate scales with the batch, and DeiT writes
    ``\frac{\mathrm{lr}}{\mathrm{batchsize}}{512}``. **Both papers state a batch
    size of 4096**, so adduce was reporting the denominator of a scaling rule as
    the hyperparameter -- a wrong number about the paper, not merely a missing
    one.

    **Adjacent up to whitespace, and the narrowness is the whole design.** Two
    arguments of one command are written against each other; a brace that closes
    and one that opens with anything between them is two different pieces of
    markup, and the number after it is routinely real. Measured over the 34
    pairs, this refuses **4** candidates and every one is wrong: BiT's 256,
    DeiT's 512, and two from DETR's ``\oldnew{old}{new}`` revision macro, where
    the keyword ends the old text and the number opens the new --
    ``schedule.}\n{for 500 epochs``, an epoch count read as a schedule, and
    ``two 3-layers}{a 3-layer``, an FFN's depth read as the model's.

    Allowing any text between the braces was measured and **rejected**: it
    refuses 13 and takes real values with it. fairseq states a BLEU of 28.6 as
    ``BLEU} & {\it 28.6``, a table header closing and an italic cell opening,
    which is not one command's two arguments at all. The three further
    candidates it would also refuse are wrong reads of a different shape --
    ``ppl)}&\multicolumn{1``, ``Accuracy} & \\\cmidrule{2``, ``mIoU at}
    \\\n{} & 1`` -- and none of them is a fraction, so refusing them here would
    be this guard taking credit for a defect it does not describe.

    Only the gap between the keyword and the number is examined. Searching the
    whole window instead was written first and was wrong in a way every test
    passed: a brace opening *after* the number set a boundary *before* it, so
    ``Batch size}: 16`` and ``learning rate:} 0.003`` were refused along with
    the fractions. Nothing caught it -- the all-pair inventory counts cells and
    claims, and a hyperparameter is neither.
    """
    return _SIBLING_GROUP_RE.search(gap) is not None


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
                    # A cutoff is passed over rather than refused, because the
                    # number the sentence states comes after it.
                    num_match = _NUMBER_RE.search(tail, search_from)
                    while num_match is not None and _is_cutoff(tail, num_match, kw_match.group(0)):
                        num_match = _NUMBER_RE.search(tail, num_match.end())
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


def _dissolve_discarded(field: str) -> str:
    r"""Commands that print nothing, removed together with the argument they take.

    Four families, dissolved the same way because they fail the generic cleanup
    the same way: it erases the command name and the braces and keeps what was
    between them, so the argument survives glued to the visible text. Each is
    paired with the number of brace groups it takes, because a command whose
    arguments are all discarded must have *all* of them removed -- dissolving
    one of two leaves the other standing exactly where the first was.

    A citation's argument is a bibliography key. Measured, ``Swin-T~\\cite{Liu2021swin}``
    reaches a reader as ``Swin-T~Liu2021swin``, and that string is a claim's
    ``text`` and a cell label -- now part of the precision verdict key -- so
    nothing downstream can tell the key from part of a model's name.

    A cross-reference's argument is an internal key, and what it prints is a
    number this collector cannot compute. t5 leads all 2,277 body cells of its
    Table 16 with one, so a seventh of the pair's cells are labelled with a
    ``tab:`` key. Removing it leaves the cell empty, which is the honest reading
    of a reference whose target number is unknown; :func:`_row_label` is what
    then finds the row a name.

    A skip's or a font size's arguments are dimensions. CLIP heads its bias-audit
    columns ``\\hspace{-0.3em}Gender\\hspace{-0.3em}``, which arrives as
    ``-0.3emGender-0.3em`` on 44 of its cells, and MoCo opens each header cell of
    its transfer tables with ``\\fontsize{7.5pt}{1em}``, which arrives as
    ``7.5pt1em COCO keypoint detection`` on 43 of its cells; a column *name* is
    what decides whether a cell is read as a claim at all, so residue there is
    not cosmetic.

    A malformed instance -- a command one of whose groups does not brace-match --
    ends the pass for that family with *nothing* removed for that instance, and
    is left to the generic cleanup, which is what :func:`_dissolve_rotatebox`
    does for the same reason: guessing where an unbalanced group ends would
    remove text the paper prints.
    """
    for pattern, arity in (
        (_CITATION_RE, 1),
        (_CROSS_REFERENCE_RE, 1),
        (_DISCARDED_ARGUMENT_RE, 1),
        (_DISCARDED_TWO_ARGUMENT_RE, 2),
    ):
        parts: list[str] = []
        position = 0
        while (match := pattern.search(field, position)) is not None:
            # ``_CITATION_RE`` consumes the brace opening the key; the other
            # patterns stop in front of it.
            end = match.end() - 1 if match.group(0).endswith("{") else match.end()
            for _ in range(arity):
                group = _brace_group(field, end)
                if group is None:
                    break
                end = group[1]
            else:
                parts.append(field[position : match.start()])
                position = end
                continue
            break  # malformed: leave this instance whole for the cleanup
        parts.append(field[position:])
        field = "".join(parts)
    return field


def _clean_cell(content: str) -> str:
    r"""One cell's visible text: markup dissolved, white space normalised.

    Normalised rather than merely stripped. A row label spanning source lines
    kept its newlines and indentation -- DINO labelled rows ``Light\n\t    DINO``
    -- and a label is now read by people and by the precision key alike. It also
    reaches :func:`~adduce.naming.canonical_metric`, whose whole-name lookup is a
    dictionary hit, so a header carrying a stray newline could not canonicalise
    however well the vocabulary knew it.
    """
    text = _CELL_CLEANUP_RE.sub("", content)
    text = _CELL_ESCAPE_RE.sub("&", _CELL_SPACE_RE.sub(" ", text))
    return " ".join(text.split())


def _split_columns(row: str) -> list[tuple[str, int]]:
    """One tabular row as ``(cell text, column span)`` pairs."""
    columns: list[tuple[str, int]] = []
    for raw in _COLUMN_SEPARATOR_RE.split(row):
        content, span = _dissolve_multicolumn(_dissolve_rotatebox(_dissolve_discarded(raw)))
        columns.append((_clean_cell(content), span))
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


def _zero_argument_macros(sources: Iterable[str]) -> dict[str, str]:
    """What each command the document defines expands to, where that is safe.

    A command the parser never resolves is a hole in the page it reads. ELECTRA
    ends its table rows with ``\\newcommand{\\tsep}{\\bstrut \\\\ \\thinline}``
    rather than ``\\\\``, so a body split on ``\\\\`` alone keeps most of a
    table as one row -- and the damage is not confined to the rows joined, since
    a second header row glued to the first body row states numbers and is no
    longer read as a header. BERT labels its own rows ``\\bertlarge (Single)``,
    where the cleanup strips the command and the model name with it, leaving
    ``(Single)``.

    Only a definition taking no arguments is read. The arity argument of a
    parameterised one sits where its body would be, so no group opens there, and
    a body still carrying a ``#`` parameter is refused outright.

    Three further bodies are refused, each because expanding it would state
    something the page does not. A body longer than
    :data:`_MAX_MACRO_BODY_CHARS` is a document fragment rather than a name. A
    body opening or closing an environment restructures the document and one
    textual pass cannot place it: MoCo defines row labels as whole nested
    ``tabular`` environments. And a body stating a TeX dimension is a spacing or
    rule command -- ``\\newcommand\\tstrut{\\rule{0pt}{2.6ex}}`` -- which the
    cell cleanup erases cleanly by name but cannot dissolve once expanded, so
    its lengths leak into the label beside them: measured on MoCo, every row
    label in the paper gained a ``1pt`` prefix. A body that ends a row is
    exempt, because failing to expand that one costs a whole table.

    Definitions are collected across the document because a command is defined
    once and used anywhere, and the last definition wins, as ``\\renewcommand``
    means it to.
    """
    macros: dict[str, str] = {}
    for text in sources:
        for match in _MACRO_DEFINITION_RE.finditer(text):
            group = _brace_group(text, match.end())
            if group is None:
                continue
            body = group[0]
            if len(body) > _MAX_MACRO_BODY_CHARS or "#" in body:
                continue
            if _ENVIRONMENT_RE.search(body):
                continue
            if "\\\\" not in body and _DIMENSION_RE.search(body):
                continue
            # A newline in a body is a space in TeX, and substituting it would
            # move every line after the first use: measured on DETR, a claim
            # recorded at line 135 was reported at 145. A locator is what sends
            # a reader to the number, so the expansion keeps the count.
            macros[match.group(1) or match.group(2)] = body.replace("\n", " ")
    return macros


def _expand_macros(text: str, macros: dict[str, str]) -> str:
    """Every command the document defines replaced by its body, once.

    One pass, and that is a bound rather than a simplification: :meth:`re.sub`
    never rescans what it substituted, so a command appearing in another's body
    stays as it is and no definition can expand into itself. Paper sources are
    untrusted content, where an expansion that recurses is a denial of service.

    Growth is bounded for the same reason. Expansion adds at most as many
    characters as the source holds, so a document can at most double however
    many commands it defines; past that the rest are left alone.
    """
    if not macros:
        return text
    budget = len(text)

    def replace(match: re.Match[str]) -> str:
        nonlocal budget
        body = macros.get(match.group(1))
        if body is None or len(body) > budget:
            return match.group(0)
        budget -= len(body)
        return body

    parts: list[str] = []
    position = 0
    for protected in _VERBATIM_RE.finditer(text):
        parts.append(_MACRO_USE_RE.sub(replace, text[position : protected.start()]))
        parts.append(protected.group(0))
        position = protected.end()
    parts.append(_MACRO_USE_RE.sub(replace, text[position:]))
    return "".join(parts)


def _definition_spans(text: str) -> list[tuple[int, int]]:
    """The source span of every command definition whose body prints nothing.

    A definition nests -- llncs defines ``\\authcount`` and ``\\lastand`` inside
    the body of its own ``\\tableofcontents`` -- so the spans a scan finds
    overlap. They are merged rather than reported separately, because a caller
    removing them cannot remove the same characters twice.

    A definition whose body opens or closes an environment is not reported at
    all, because that body is page content rather than a name; see
    :func:`_strip_definitions` for what that costs and why it is the gate.
    Nesting cannot defeat the test: a body holding such a definition holds its
    environment too, so the enclosing definition is kept for the same reason,
    and a markup definition written *inside* a kept body is still reported and
    still removed.

    A definition whose body cannot be brace-matched is not reported at all: the
    document is malformed there and guessing where the definition ends would
    remove text the paper prints.
    """
    spans: list[tuple[int, int]] = []
    for match in _MACRO_DEFINITION_RE.finditer(text):
        arguments = _MACRO_PARAMETERS_RE.match(text, match.end())
        group = _brace_group(text, arguments.end() if arguments else match.end())
        if group is None:
            continue
        if _ENVIRONMENT_RE.search(group[0]):
            continue
        start, end = match.start(), group[1]
        if spans and start <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
            continue
        spans.append((start, end))
    return spans


def _strip_definitions(text: str) -> str:
    """The document with every non-printing definition removed, line breaks kept.

    **What is removed.** A definition of a *name* states what the name means,
    and the page never shows that statement, so a number written inside one is
    not a number the paper reports. Measured on DETR,
    ``\\newcommand{\\iouloss}[1]{{\\cal L}_{\\rm iou}(#1)}`` is read as an IoU of
    1 and llncs' ``\\def\\authcount##1{\\setcounter{auco}{##1}}`` as an AUC of 1,
    where the value is the parameter placeholder ``#1``; BERT's
    ``\\def\\aclpaperid{1584}`` is read as an F1 of 584, because expansion
    substitutes the body at the definition's own site and leaves ``\\def1584``
    standing. Expansion cannot reach any of these in either direction:
    substituting a command at its uses leaves its definition where it was.

    **What is not removed: a body that opens an environment.** That body is not
    a name. CVPR and ICML papers routinely wrap a whole float in a macro and
    invoke it in the document body, and :func:`_expand_macros` cannot move such
    a body -- one textual pass cannot place an environment -- so removing the
    definition would delete printed content with nothing put back in its place.
    Measured: removing them took latent-diffusion from 624 table cells to 0 and
    StyleGAN2-ADA from 66 to 0, 20,616 of the 20,647 characters of one file and
    48,434 of 48,635 of another, destroying reported values including
    ``Batch Size 48``, ``Learning Rate 1.0e-4`` and ``Recall 0.261``.

    **The environment test is the whole gate, deliberately.** It is not the four
    conditions :func:`_zero_argument_macros` applies, because the false
    positives above are *parameterised* definitions, which is exactly what
    expansion refuses -- keeping whatever expansion would accept restores every
    one of them. It is not an allowlist of float and table environments either:
    such a list is a claim about which environments print, the set is open
    (packages define more), and every omission is a silent deletion of page
    content. Measured, StyleGAN2-ADA writes its main results tables in ``tabu``,
    which no list derived from :data:`_TABLE_ENVIRONMENTS` would contain. And it
    carries no length bound: whether a body prints does not vary with its size
    -- MoCo writes a printed ``tabular`` in 71 characters and latent-diffusion
    one in 3,150 -- and the bound expansion applies exists to stop substitution
    growing without limit, which removal never does.

    What is left is therefore removed without loss of printed text: what a
    non-environment body contributes to the page it contributes where the
    command is used, and :func:`_expand_macros` substitutes it there. A body
    expansion still declines for one of its other reasons -- one stating a
    dimension, one past the length bound -- loses what it states, and this makes
    no claim otherwise.

    Line breaks are kept in place of what is removed, because a locator is what
    sends a reader to a number and a body spanning lines would otherwise move
    every claim beneath it.

    A definition inside a verbatim environment is left alone. There the command
    is text the paper shows rather than markup it uses, which is the same reason
    expansion skips those regions.

    A kept body holding a ``tabular`` is parsed as a table in its own right,
    which numbers the real ones from further along. That cost is measured and
    accepted: on MoCo, which writes row labels that way, 12 such phantom tables
    over 19 real ones, and its 260 cells identical either way -- ``-0 +0`` on
    the multiset of ``(row, column, value, file, line)``. ``table_index`` is
    read by nothing outside this module: not by a claim, not by a report, not by
    the precision join key.
    """
    return _remove_spans(text, _definition_spans)


def _state_command_spans(text: str) -> list[tuple[int, int]]:
    r"""The source span of every counter- and length-setting command call.

    Arguments included, because the arguments are the whole problem: the number
    a counter is set to is what gets read as a measurement. Brace-matched for
    the reason :func:`_definition_spans` is -- a length is routinely set from
    another length, ``\setlength{\tabcolsep}{\dimexpr\columnsep/2}`` -- and a
    call whose groups do not match is left alone rather than guessed at.

    Every argument the command declares must be present for the call to be
    removed. One that takes two and is written with one is malformed, and
    removing the name alone would leave its arguments behind as text, which is
    the failure this exists to prevent rather than a lesser version of it.
    """
    spans: list[tuple[int, int]] = []
    for match in _STATE_COMMAND_RE.finditer(text):
        end = match.end()
        for _ in range(_STATE_COMMANDS[match.group(1)]):
            group = _brace_group(text, end)
            if group is None:
                end = -1
                break
            end = group[1]
        if end < 0:
            continue
        if spans and match.start() <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
            continue
        spans.append((match.start(), end))
    return spans


def _strip_state_commands(text: str) -> str:
    r"""The document with every counter and length assignment removed.

    A counter is not a measurement. ``\setcounter{tocdepth}{2}`` sets how deep a
    table of contents goes; ``\setlength{\tabcolsep}{6pt}`` sets how wide a
    column gutter is. Neither prints anything, so neither states a number the
    paper reports -- but both put a keyword and a number two characters apart,
    which is exactly the shape the keyword scan reads as a value. Measured on
    DETR, ``depth}{2`` was reported as ``num_layers = 2.0`` at full confidence.

    Removed rather than guarded against at the point of reading. A guard would
    have to recognise the shape from inside a sixteen-character window, while
    the command is unambiguous where it is written; and removal also keeps the
    assignment from leaking into a cell, since a paper sets ``\tabcolsep``
    beside the table it applies to.

    Line breaks are kept and verbatim regions are skipped, for the same two
    reasons :func:`_strip_definitions` keeps and skips them: a locator must not
    move, and a command a paper *prints* is text rather than markup.
    """
    return _remove_spans(text, _state_command_spans)


def _remove_spans(text: str, finder: Callable[[str], list[tuple[int, int]]]) -> str:
    """*text* with each span *finder* reports replaced by its own line breaks.

    Shared by the two strippers so that neither can lose the verbatim guard or
    the line-break preservation the other has.
    """
    parts: list[str] = []
    position = 0
    for protected in (*_VERBATIM_RE.finditer(text), None):
        end = protected.start() if protected is not None else len(text)
        segment = text[position:end]
        cursor = 0
        for start, stop in finder(segment):
            parts.append(segment[cursor:start])
            parts.append("\n" * segment.count("\n", start, stop))
            cursor = stop
        parts.append(segment[cursor:])
        if protected is not None:
            parts.append(protected.group(0))
            position = protected.end()
    return "".join(parts)


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


def _names_metric(cell: str) -> bool:
    return canonical_metric(cell) is not None


def _states_a_number(row: list[str]) -> bool:
    return any(_cell_number(cell) is not None for cell in row)


def _expand_spans(row: list[tuple[str, int]]) -> list[str]:
    """One entry per column: a spanning header names every column it covers."""
    return [cell for cell, span in row for _ in range(span)]


def _is_second_header(first: list[str], second: list[str]) -> bool:
    """Whether *second* is a header row naming a metric *first* leaves unnamed.

    Two conditions, both required. The second row states no number in any
    column, and some *column* pairs a first-row cell naming no metric with a
    second-row cell that names one. A body row of a results table fails the
    first; a table whose columns are already metrics, and a units or
    group-label row, both fail the second.

    The second condition is per column because a header row mixes: ELECTRA
    heads one column ``Train FLOPs`` and the rest with dataset names whose
    metric sits underneath, and asking that *no* cell of the first row name a
    metric refuses that whole table. Composition keeps a column the first row
    already named, so the mixed case costs the named column nothing.

    The first condition is what bounds the cost of a wrong answer, and it
    bounds it per column exactly as it bounded it per row: a row stating no
    number yields no cell either way, so reading one as a header can never
    drop a value -- at worst it renames the columns beneath it.

    Mamba's zero-shot table is the shape this exists for: ``LAMBADA`` and
    ``HellaSwag`` head the columns with ``ppl``/``acc`` underneath, so the
    column names a dataset and the row beneath it names what was measured.
    """
    if _states_a_number(second):
        return False
    return any(
        not _names_metric(dataset) and _names_metric(metric)
        for dataset, metric in zip_longest(first, second, fillvalue="")
    )


def _header_depth(rows: list[list[tuple[str, int]]]) -> int:
    r"""How many rows below the first belong to the header, at most two.

    A results table heads its columns with one row, or with two where the first
    names the dataset and the second what was measured on it. A third shape
    exists and was being read as data: a group row over a dataset row over a
    metric row. t5's Table 16 is it -- ``\multicolumn{13}{c}{GLUE}`` above
    ``CoLA SST-2 MRPC ...`` above ``MCC Acc F1 ...`` -- and so are BLIP's two
    retrieval tables, CLIP's, and Swin's system-level comparison. Five tables
    over the 34 dev pairs, and every one of them a genuine three-row header.

    **Iterating :func:`_is_second_header` from the top cannot find them, and
    that is the whole reason this exists.** The predicate asks whether a row
    names a metric the row above leaves unnamed, so on t5 it is asked of the
    group row against the dataset row -- neither names a metric -- and answers
    False at the first step, ending the search two rows above the answer.
    Measured over all 34 pairs, iterating it claims a third row for *no* table.

    So the run is searched from its deepest end instead: the last row absorbed
    must be a second header of the row above it, and every row between the
    first and it must state no number. The metric requirement on that last row
    is what bounds the cost, and it is not a detail -- dropping it for the
    tempting phrasing, "absorb while the next row states no number", claims
    **136** tables rather than 5. Those are gpt-neox's ``0.324 \pm 0.015``
    cells, which no more parse as numbers than a header does, BERT's ``(Acc)``
    units row, and hyperparameter tables across mae, lora and bit.

    Strictly additive, and measured so: the 54 tables that compose two rows
    today are the same 54 afterwards, by set identity and not only by count.
    No table gains a second header row here, and none loses one.
    """
    for depth in range(_MAX_HEADER_ROWS - 1, 0, -1):
        if depth >= len(rows):
            continue
        run = [_expand_spans(row) for row in rows[: depth + 1]]
        if not _is_second_header(run[-2], run[-1]):
            continue
        if not any(_states_a_number(row) for row in run[1:-1]):
            return depth
    return 0


def _compose_headers(first: list[str], second: list[str]) -> list[str]:
    """One header per column, dataset first and metric second: ``LAMBADA ppl``.

    Both are kept because both are read downstream: the metric is what the
    column reports and the dataset is what it reports it on, and a composed
    header canonicalises on its trailing metric word where the dataset alone
    canonicalises to nothing. A column whose first row already names a metric
    keeps it, so the row beneath cannot rename ``Accuracy`` after a qualifier.
    """
    composed: list[str] = []
    for dataset, metric in zip_longest(first, second, fillvalue=""):
        if _names_metric(dataset):
            composed.append(dataset)
            continue
        composed.append(" ".join(part for part in (dataset, metric) if part))
    return composed


def _row_label(row: list[str]) -> str:
    r"""What a body row is called: the first cell of its leading label block.

    A row's name is its first cell whenever that cell is filled, and this returns
    it unchanged -- including when it is itself a number, because a number is a
    perfectly good name for a row: t5's Table 7 labels its rows by span length,
    and ``10`` is what one of them is called. Only an *empty* first cell starts a
    search, and only along the leading run of empty cells: the first filled cell
    ends it, naming the row if it states no number and leaving the row unnamed if
    it does. A filled numeric cell is already extracted as that row's first
    value, so reading it as the name too would have one cell play both parts.

    Widening the search past a filled cell was measured and rejected. Letting a
    numeric first cell be overridden cost bert 55 of its row labels and t5 the
    ``10`` its span-length rows are named by.

    This exists because a table may spend its first column on something that is
    not the row's name. t5's Table 16 leads with a ``Table`` column of
    cross-references to the main-body table each row restates and an
    ``Experiment`` column holding the name; once the reference is dissolved (see
    :data:`_CROSS_REFERENCE_RE`) the first cell is empty, and reading it as the
    label would call 2,277 cells by the same empty name. That is not a cosmetic
    matter: ``claims.cluster`` separates measurements by row and column and
    cannot see which table -- or which row -- a cell came from, so identically
    named cells whose values round together become one claim, and measured, the
    empty name alone collapsed t5's 2,339 claims to 1,689. Naming the row from
    the ``Experiment`` column instead keeps them distinct *and* gives a reader
    the label the paper prints.
    """
    if row and row[0]:
        return row[0]
    for cell in row[1:]:
        if not cell:
            continue
        return "" if _cell_number(cell) is not None else cell
    return ""


def _section_label(row: list[tuple[str, int]], width: int) -> str | None:
    """The label of a full-width section row, or ``None`` when this is not one.

    A results table is often partitioned by a row that spans it and states a
    heading rather than a measurement -- BERT splits its SQuAD tables into
    ``Top Leaderboard Systems``, ``Published`` and ``Ours``. Three conditions
    identify one: a single cell carries the row, it states no number, and it
    spans all the table's columns or all but one. The number test is what
    bounds a wrong answer, exactly as it does for a second header row: a row
    stating no number yields no cell either way.
    """
    filled = [(cell, span) for cell, span in row if cell]
    if len(filled) != 1:
        return None
    label, span = filled[0]
    if span < 2 or span + 1 < width or _cell_number(label) is not None:
        return None
    return label


def _section_marks_prior_work(label: str, /) -> bool:
    """Whether a section label says the rows beneath it are somebody else's.

    False where the label says nothing about ownership, which is the common
    case and the conservative one: ConvNeXt partitions a table by pre-training
    corpus, DINO by supervision regime and BLIP by evaluation setting, and none
    of those states who produced anything. Reading an unrecognised heading as
    prior work would demote a paper's own results across whole tables.

    An unrecognised heading therefore *clears* the sense the heading above it
    set, which is why the opposite statement -- BERT's ``Ours`` beneath its
    ``Published`` -- needs no vocabulary of its own. Not naming prior work is
    already the whole of what it has to do, and a second list would assert a
    distinction nothing downstream could observe.
    """
    normalised = _SECTION_QUALIFIER_RE.sub("", label.strip())
    return " ".join(normalised.lower().split()).strip(" .:,;-*") in _PRIOR_WORK_SECTIONS


def _parse_tables(text: str, file: str) -> list[TableCell]:
    cells: list[TableCell] = []
    caption_spans = _float_captions(text)
    for table_index, tab_match in enumerate(_TABLE_RE.finditer(text)):
        caption = _caption_at(caption_spans, tab_match.start())
        body = _table_body(tab_match.group(1), tab_match.group(3))
        base_line = _line_of(text, tab_match.start())
        rows: list[list[tuple[str, int]]] = []
        cited: list[bool] = []
        for raw_row in body.split("\\\\"):
            columns = _split_columns(_ROW_MARKUP_RE.sub("", raw_row))
            if any(cell for cell, _ in columns):
                rows.append(columns)
                # The label only, and before the cleanup: a citation beside a
                # number is a note on that number, while a citation in the row
                # label names the paper the whole row came from.
                first = _COLUMN_SEPARATOR_RE.split(raw_row)[0]
                cited.append(_CITATION_RE.search(first) is not None)
        if len(rows) < 2:
            continue
        # A spanning header names every column it covers, so it is repeated
        # across the span. A spanning body cell states one number, so only its
        # first column carries it and the rest are placeholders holding the row
        # in step with the header.
        header = _expand_spans(rows[0])
        first_body = _header_depth(rows) + 1
        for index in range(1, first_body):
            header = _compose_headers(header, _expand_spans(rows[index]))
        # Rows inherit the sense of the last section header above them, and a
        # heading naming no owner clears it rather than continuing one that no
        # longer applies.
        section = False
        for index in range(first_body, len(rows)):
            spanned_row = rows[index]
            label = _section_label(spanned_row, len(header))
            if label is not None:
                section = _section_marks_prior_work(label)
                continue
            row: list[str] = []
            for cell, span in spanned_row:
                row.append(cell)
                row.extend([""] * (span - 1))
            if not row:
                continue
            row_label = _row_label(row)
            prior_work = section or cited[index]
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
                        prior_work=prior_work,
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
    macros = _zero_argument_macros(sources[rel] for rel in evidence.tex_files)

    for rel in evidence.tex_files:
        clean = _strip_state_commands(
            _expand_macros(_strip_definitions(sources[rel]), macros)
        )
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
