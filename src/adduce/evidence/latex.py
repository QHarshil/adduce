"""LaTeX evidence: the numeric claims the paper actually makes.

Best-effort by design. Comment stripping, ``\\input`` following, scientific
and LaTeX math notation (``10^{-3}``, ``1\\times10^{-4}``), keyword-proximity
hyperparameter extraction, and ``tabular`` table parsing cover the common
shapes of ML papers; everything extracted here feeds probabilistic rules
(drift, reconciliation) that report with confidence and never block.
"""

from __future__ import annotations

import math
import posixpath
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from ..model import Repo
from ..naming import HYPERPARAM_SYNONYMS, METRIC_PATTERNS

_INPUT_RE = re.compile(r"\\(?:input|include)\{([^}]+)\}")

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
#: The two wrappers whose arguments would otherwise be read as cell content.
#: Both take their text as the last of several arguments, so the generic cell
#: cleanup cannot dissolve them: it keeps every argument and concatenates them.
_ROTATEBOX_RE = re.compile(r"\\rotatebox\s*(?:\[[^\]]*\])?\s*")
_MULTICOLUMN_RE = re.compile(r"\\multicolumn\s*")
#: How many wrappers deep a cell is unwrapped. Paper sources are untrusted
#: input, so the loop is bounded.
_MAX_MARKUP_NESTING = 8
_COMMENT_RE = re.compile(r"(?<!\\)%.*$", re.MULTILINE)

#: value patterns: 0.001 · 1e-4 · 3E-5 · $10^{-3}$ · 1\times10^{-4} · 5\cdot10^{-3} · 92.4\%
#:
#: A number followed by ``^`` is a base, so the power branches come first and
#: match a base and caret with or without an exponent they can read.
#: :func:`_parse_number` then decides whether it understands the power. A
#: plain branch tried first, or after a power branch that cannot match the
#: exponent, reads a fragment: ``10^{-4}`` was read as 10, and a paper
#: agreeing with its config was reported as disagreeing by orders of magnitude.
#:
#: The first branch is base 10 only, and lets math delimiters, braces and
#: spacing commands split the power, because ``10$^{-4}$``,
#: ``1$\times$10$^{-4}$`` and ``$5\,\times\,10^{-4}$`` were read as 10, 1 and 5.
#: The second takes any other base and joins nothing, so ``224$^2$`` is still a
#: plain 224: a resolution, not a power. A brace may close between a base and
#: its caret, for ``\mathbf{76.1}^{*}``.
_SPACING = r"\\[,!;: ]|~"
_JOIN = r"(?:[\s${}]|" + _SPACING + r")*"
_EXPONENT_TOKEN = r"\{(?:[^{}]|\{[^{}]*\})*\}|[-+\u2212]?\d+(?:\.\d+)?|\\[A-Za-z]+(?:\{[^{}]*\})?|[*A-Za-z]"
_NUMBER_PATTERN = (
    r"(?:(?P<tmant>\d+(?:\.\d+)?)" + _JOIN + r"(?:\\times|\\cdot|[xX*])" + _JOIN + r")?"
    r"(?P<ten>10)" + _JOIN + r"\^\s*(?P<texp>" + _EXPONENT_TOKEN + r")?"
    r"|(?:(?P<mant>\d+(?:\.\d+)?)\s*(?:\\times|\\cdot|[xX*])\s*)?"
    r"(?P<base>\d+(?:\.\d+)?)\s*\}?\s*\^\s*(?P<exp>" + _EXPONENT_TOKEN + r")?"
    r"|(?P<emant>\d+(?:\.\d+)?)[eE](?P<exp_e>[-+\u2212]?\d+(?:\.\d+)?)"
    r"|(?P<plain>\d+(?:\.\d+)?)"
)
_NUMBER_RE = re.compile(_NUMBER_PATTERN)
#: An exponent this parser reads as a power: a whole integer, signed or not.
#: ``-4.5`` is refused, never truncated to ``-4``.
_EXPONENT_RE = re.compile(r"(?P<sign>[-+\u2212]?)\s*(?P<digits>\d+)")
#: Spacing inside an exponent: ``10^{\,-4}`` is ``10^{-4}``.
_EXPONENT_SPACING_RE = re.compile(_SPACING)
#: Arithmetic in an exponent with no digit: ``10^{-k}`` is a power of a symbol,
#: not a marker on 10.
_EXPONENT_ARITHMETIC_RE = re.compile(r"[-+\u2212]")
#: An exponent stating an uncertainty: ``76.1^{\pm 0.2}``, ``28.4^{+0.3}``.
_UNCERTAINTY_RE = re.compile(r"\\pm|\\mp|[\u00b1\u2213]")
_SIGNED_DECIMAL_RE = re.compile(r"[-+\u2212]\d+\.\d+")
#: A subscript after the exponent makes the pair an interval: ``28.4^{+0.3}_{-0.2}``.
_SUBSCRIPT_RE = re.compile(r"\s*_")

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


#: A verb or noun saying the quantity changes. Whole words only, so ``dropout``
#: is not ``drop``.
_CHANGE_RE = re.compile(
    r"\b(?:drop(?:s|ped|ping)?|decay(?:s|ed|ing)?|decreas(?:e|es|ed|ing)"
    r"|increas(?:e|es|ed|ing)|reduc(?:e|es|ed|ing|tion)|divid(?:e|es|ed|ing)"
    r"|multipl(?:y|ies|ied|ying)|halv(?:e|es|ed|ing)|anneal(?:s|ed|ing)?)\b",
    re.IGNORECASE,
)


def _describes_a_change(gap: str) -> bool:
    """Whether *gap* says the keyword's quantity changes before the number."""
    return _CHANGE_RE.search(gap) is not None


#: The word that introduces a number, at the end of the text before it. ``up
#: to`` is a bound, so it does not count as ``to``, however it is spaced.
_INTRODUCER_RE = re.compile(
    r"(?<![A-Za-z])(?P<word>by(?:\s+a\s+factor(?:\s+of)?)?|after|every|from|(?:up[\s~]+)?to)"
    r"[\s$~{(]*\Z",
    re.IGNORECASE,
)
#: Introducers whose number is a delta, a factor or a schedule point.
_SCHEDULE_WORDS = ("by", "after", "every")
#: ``by`` after a word placing one stage next to another introduces the next
#: stage, not a delta: ``followed by 100 epochs of fine-tuning``.
_SEQUENCE_BY_RE = re.compile(r"(?:followed|preceded|accompanied)\s+by[\s~]*\Z", re.IGNORECASE)
#: The ``to`` that ends a range, right after its first number, or after a
#: word or two naming the quantity when the range comes before its keyword:
#: ``from 100 epochs to 300 epochs``. Not across another number.
_RANGE_TO = r"(?:up[\s~]+)?to[\s$~]*"
_RANGE_TO_RE = re.compile(r"[\s$~]*" + _RANGE_TO, re.IGNORECASE)
_RANGE_MIDDLE_RE = re.compile(r"\D{0,30}?" + _RANGE_TO + r"\Z", re.IGNORECASE)
_RANGE_FROM_RE = re.compile(r"from[\s$~]*", re.IGNORECASE)
#: The end of a sentence. A point before a digit is a decimal point.
_SENTENCE_END_RE = re.compile(r"[.;](?!\d)|\n\s*\n")


def _introducer(lead: str) -> str | None:
    """The word introducing the number that follows *lead*, lowercased, or ``None``."""
    match = _INTRODUCER_RE.search(lead)
    if match is None:
        return None
    word = re.split(r"[\s~]+", match.group("word"))[0].lower()
    return None if word == "up" else word


def _range_end(text: str, start: int) -> re.Match[str] | None:
    """The number a ``to`` at *start* takes a range to, or ``None``."""
    to = _RANGE_TO_RE.match(text, start)
    return None if to is None else _NUMBER_RE.match(text, to.end())


def _ends_a_range(lead: str) -> bool:
    """Whether *lead* ends with ``from X to``, so the next number ends a range."""
    for start in _RANGE_FROM_RE.finditer(lead):
        first = _NUMBER_RE.match(lead, start.end())
        if first is not None and _RANGE_MIDDLE_RE.match(lead, first.end()) is not None:
            return True
    return False


def _changes_before(lead: str) -> bool:
    """Whether the sentence ahead of a keyword says its quantity changes.

    A comma does not end the verb's reach: ``we decay, with a cosine schedule,
    the learning rate to 1e-5``. A change word inside another quantity's name
    says nothing changes: ``a weight decay of 0.05 and a learning rate set to
    0.001``.
    """
    sentence = _SENTENCE_END_RE.split(lead)[-1]
    for _kind, _name, pattern in _QUANTITY_NAMES:
        sentence = pattern.sub(" ", sentence)
    return _describes_a_change(sentence)


def _names_another_quantity(gap: str, keyword: str, kind: str, canonical: str) -> bool:
    """Whether a keyword for some other quantity sits between *keyword* and its number.

    The nearer keyword owns the number. ``learning rate warmup of 1000 steps``
    states a warmup length, ``learning rate schedule for 300 epochs`` a training
    length and ``no dropout, weight decay 0.1`` a weight decay, and each was
    read as the first keyword's value. A word that only continues the
    keyword's own name is not another quantity: ``warmup steps of 4000`` is a
    warmup length.

    A leading boundary keeps ``auc`` out of ``because`` and ``temp`` out of
    ``attempt``. No trailing one, so ``scheduler`` still names a schedule.
    """
    for name_kind, name, pattern in _QUANTITY_NAMES:
        if (name_kind, name) == (kind, canonical):
            continue
        for match in pattern.finditer(gap):
            spelled = re.sub(r"[\s~-]+", "_", (keyword + gap[: match.end()]).strip().lower())
            if HYPERPARAM_SYNONYMS.get(spelled) == canonical:
                continue
            return True
    return False


def _refuses_the_number(gap: str, keyword: str, kind: str, canonical: str, lead: str = "") -> bool:
    """Whether the number after *gap* is not the value of *keyword*'s quantity.

    A keyword for another quantity in the gap takes the number first, for the
    reason :func:`_names_another_quantity` gives. Otherwise the word that
    introduces the number decides, because a schedule names its quantity and
    then how it moves, and the number after the movement was read as the
    value. ``learning rate drops by 2 every 50 epochs`` was a learning rate of
    2, and a paper agreeing with its config was reported as drifting from it.

    - ``by N``, ``after N``, ``every N``: a delta, a factor or a schedule
      point. Refused for every keyword.
    - ``from N``: decided by the caller, which sees what follows the number,
      for the reason :func:`_extract_keyword_values` gives.
    - ``to N`` with a change verb in the gap, or in the sentence *lead* ahead
      of the keyword: for a hyperparameter a final or peak value, which is not
      the configured one, so refused, because a false mismatch costs more than
      a miss. ``then decay the learning rate to $10^{-5}$`` states no rate.
      For a metric it is the stated result, so read: ``WER is reduced to 4.2``
      states 4.2.
    - Any other number after a change verb in the gap is refused: ``learning
      rate decay of 0.8`` states a decay factor.

    Nothing after the number is examined, for the reason
    :func:`_crosses_group_boundary` gives: ``learning rate of 0.001, dropped
    by 10x at epoch 30`` states its value before the verb.
    """
    if _names_another_quantity(gap, keyword, kind, canonical):
        return True
    word = _introducer(gap)
    if word in _SCHEDULE_WORDS:
        return True
    if word == "to" and kind == "hyperparameter" and _changes_before(lead):
        return True
    if word == "from" or not _describes_a_change(gap):
        return False
    return not (word == "to" and kind == "metric")


def _number_before_is_refused(lead: str, kind: str) -> bool:
    """Whether the number ahead of a keyword is not that keyword's value.

    ``after 400 epochs`` and ``every 50 epochs`` are schedule points and ``by
    10 epochs`` a delta, for the reason :func:`_refuses_the_number` gives, but
    ``followed by 100 epochs`` is the next stage, so it is read. A number after
    a caret is an exponent: ``10^4 steps`` does not state 4. For a
    hyperparameter, the end of a range is not the configured value:
    ``increase from 100 to 300 epochs`` states neither.
    """
    if lead.rstrip().endswith("^"):
        return True
    word = _introducer(lead)
    if word in _SCHEDULE_WORDS:
        return not (word == "by" and _SEQUENCE_BY_RE.search(lead) is not None)
    return kind == "hyperparameter" and _ends_a_range(lead)


def _exponent(text: str) -> int | None:
    """The whole-integer exponent *text* states, or ``None``."""
    match = _EXPONENT_RE.fullmatch(text)
    if match is None:
        return None
    digits = int(match.group("digits"))
    return -digits if match.group("sign") in ("-", "\u2212") else digits


def _reads_as_its_base(exponent: str, base: str, paired: bool) -> bool:
    r"""Whether a power with this *exponent* states its base as the value.

    An exponent with no digit and no arithmetic is a marker, a footnote or a
    significance mark, so ``76.1^{*}``, ``28.4^\dagger`` and ``10^\dagger``
    state their base. On a base other than 10, an uncertainty does too:
    ``76.1^{\pm 0.2}``, ``28.4^{+0.3}``, ``28.4^{+0.3}_{-0.2}``. Anything else
    with a digit is a power, and reading its base states a fragment: ``2^{11}``
    is not 2, and ``10^{-4.5}`` is not 10.
    """
    if not any(character.isdigit() for character in exponent):
        return _EXPONENT_ARITHMETIC_RE.search(exponent) is None
    if base == "10":
        return False
    return (
        paired
        or _UNCERTAINTY_RE.search(exponent) is not None
        or _SIGNED_DECIMAL_RE.fullmatch(exponent) is not None
    )


def _scaled(mantissa: str, exponent: int) -> float | None:
    """``mantissa`` times ten to ``exponent``, or ``None`` if a float cannot hold it.

    Every value is formed here, from a decimal string, because paper text is
    untrusted: ``10.0 ** 400`` raised ``OverflowError`` out of the collector
    and ``adduce check`` exited with a traceback. Parsing the string rounds to
    infinity or to zero instead of raising, and neither is the value the paper
    states.
    """
    value = float(f"{mantissa}e{exponent}")
    if not math.isfinite(value) or (value == 0 and float(mantissa) != 0):
        return None
    return value


def _parse_number(match: re.Match[str]) -> tuple[float, str] | None:
    r"""The value *match* states and the literal it printed, or ``None``.

    A power of ten with a whole-integer exponent reads as the power, with or
    without a mantissa. A power whose exponent is a marker or, on another
    base, an uncertainty reads as its base, for the reason
    :func:`_reads_as_its_base` gives, and the base is the literal, so its
    printed decimals still set the tolerance. Every other power yields
    nothing, because reading part of it states a truncated number as the
    paper's: ``2^{10}``, ``10^{-4.5}``, ``10^{\text{-}4}``, ``5\times10^{*}``.
    """
    try:
        if match.group("plain") is not None:
            return _with_literal(_scaled(match.group("plain"), 0), match.group("plain"))
        if match.group("emant") is not None:
            exponent = _exponent(match.group("exp_e"))
            return None if exponent is None else _with_literal(_scaled(match.group("emant"), exponent), match.group(0))
        if match.group("ten") is not None:
            mantissa, base, raised = match.group("tmant"), "10", match.group("texp")
        else:
            mantissa, base, raised = match.group("mant"), match.group("base"), match.group("exp")
        # A digit glued after the exponent leaves a token this does not
        # understand: ``10^{-1}0`` is not 0.1.
        if raised is None or match.string[match.end() : match.end() + 1].isdigit():
            return None
        if raised.startswith("{"):
            raised = raised[1:-1]
        raised = _EXPONENT_SPACING_RE.sub("", raised).strip()
        paired = _SUBSCRIPT_RE.match(match.string, match.end()) is not None
        exponent = _exponent(raised)
        if base == "10" and exponent is not None and not paired:
            return _with_literal(_scaled(mantissa or "1", exponent), match.group(0))
        if mantissa is None and _reads_as_its_base(raised, base, paired):
            return _with_literal(_scaled(base, 0), base)
        return None
    except (TypeError, ValueError):
        return None


def _with_literal(value: float | None, literal: str) -> tuple[float, str] | None:
    return None if value is None else (value, literal)


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

    Only ``@`` binds a rank. Other characters ahead of a number carry values a
    paper does state: ``$H/64`` gives a head count and ``$\geq\!$~1024`` a
    batch size.
    """
    if number.start() == 0:
        return keyword.endswith("@")
    return tail[number.start() - 1] == "@"


def _extract_keyword_values(
    text: str, file: str, keywords: dict[str, tuple[str, ...]], kind: str, window: int = 80
) -> list[PaperValue]:
    r"""Numbers appearing shortly after a keyword ("a learning rate of 1e-4").

    A number introduced by ``from`` is the start of a change, and what follows
    it decides. ``from X to Y`` is a range: for a hyperparameter it yields
    nothing, because neither end need be the configured value and ``warm up
    the learning rate from 1e-6 to 1e-3`` was read as 1e-6. For a metric it
    yields Y, the stated result: ``WER is reduced from 5.1 to 4.2`` states
    4.2. A bare ``from X`` with no ``to`` after it still reads X: ``the
    learning rate is reduced from 0.01`` states 0.01. This holds whichever
    side of the keyword X is on.

    Where the keyword comes before Y, ``to`` makes a range only if whitespace,
    ``$`` and ``~`` are all that separate it from the X or keyword before it
    and from Y. ``up to`` counts as ``to``. Anything else between them leaves
    a bare ``from X``: ``WER is reduced from 5.1\% to 4.2`` states 5.1.
    """
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
                head_start = max(0, kw_match.start() - 16)
                head = text[head_start : kw_match.start()]
                before = re.search(r"(?<![\w.-])(\d+(?:\.\d+)?)\s*$", head)
                literal: str | None = None
                if before and not plural:
                    number_start = head_start + before.start(1)
                    lead = text[max(0, number_start - 40) : number_start]
                    # A refused number is the keyword's own, so the keyword
                    # states nothing. Searching on past it would read the next
                    # number in the sentence: "after 300 epochs on 8 GPUs" as a
                    # training length of 8. The 300 is refused though it is
                    # the run length here, because "after" also introduces
                    # schedule points: "drop after 400 epochs".
                    if _number_before_is_refused(lead, kind):
                        continue
                    end = _range_end(text, kw_match.end()) if _introducer(lead) == "from" else None
                    if end is None:
                        value = float(before.group(1))
                        literal = before.group(1)
                        raw = (before.group(1) + " " + kw_match.group(0)).strip()
                    else:
                        parsed = None if kind == "hyperparameter" else _parse_number(end)
                        if parsed is None:
                            continue
                        value, literal = parsed
                        raw = text[number_start : end.end()]
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
                        and not _refuses_the_number(
                            tail[: num_match.start()],
                            kw_match.group(0),
                            kind,
                            canonical,
                            text[max(0, kw_match.start() - window) : kw_match.start()],
                        )
                    ):
                        # Reject numbers glued to a word ("CIFAR-10") — those
                        # are names, not values.
                        preceding = tail[num_match.start() - 1 : num_match.start()]
                        if preceding == "" or not (preceding.isalpha() or preceding in "-_"):
                            stated: re.Match[str] | None = num_match
                            if _introducer(tail[: num_match.start()]) == "from":
                                end = _range_end(tail, num_match.end())
                                if end is not None:
                                    stated = None if kind == "hyperparameter" else end
                            parsed = None if stated is None else _parse_number(stated)
                            if stated is not None and parsed is not None:
                                value, literal = parsed
                                raw = (kw_match.group(0) + tail[: stated.end()]).strip()
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

#: Every quantity a keyword names, with its kind, for
#: :func:`_names_another_quantity`. Both vocabularies, because a metric's name
#: between a hyperparameter and a number takes the number as surely as another
#: hyperparameter's does.
_QUANTITY_NAMES: tuple[tuple[str, str, re.Pattern[str]], ...] = tuple(
    (_kind, _name, re.compile(r"(?<![A-Za-z])(?:" + _pattern + ")", re.IGNORECASE))
    for _kind, _vocabulary in (
        ("hyperparameter", _HYPERPARAM_PATTERNS),
        ("metric", _METRIC_KEYWORDS),
    )
    for _name, _patterns in _vocabulary.items()
    for _pattern in _patterns
)


#: A verbatim-like environment prints its contents, so a command inside one is
#: text the paper shows rather than markup it uses.
_VERBATIM_RE = re.compile(
    r"\\begin\{(verbatim\*?|lstlisting|minted|alltt)\}.*?\\end\{\1\}", re.DOTALL
)

#: Commands that set a counter or a length, with the number of brace groups each
#: takes. A counter is not a measurement: its arguments name a piece of
#: typesetting state and the number it is set to, and neither is printed. A
#: counter or length named ``layers`` or ``headsep`` carries a hyperparameter
#: keyword, and the keyword scan then reads the next number in the prose as its
#: value: ``\newlength{\headsep}`` ahead of a sentence mentioning 4 reads as
#: ``num_heads = 4``.
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


def _dissolve_rotatebox(field: str) -> str:
    """``\\rotatebox[origin=rc]{270}{TED-LIUM3}`` reads as ``TED-LIUM3``.

    The generic cleanup strips a command name and its opening brace without
    regard to argument structure, which glues the rotation arguments to the text
    they rotate: a column named ``TED-LIUM3`` arrives as
    ``[origin=rc]270TED-LIUM3``, names no metric, and is dropped.
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

    The span is returned rather than discarded, because dropping it leaves the
    header row shorter than the body rows, so every column after the spanned one
    is attributed to the wrong header. A wrapper that cannot be parsed is left
    alone and spans one column: guessing a span would shift every later column.
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


def _split_spans(row: str) -> list[tuple[str, int]]:
    """One tabular row as ``(cell text, column span)`` pairs."""
    columns: list[tuple[str, int]] = []
    for raw in row.split("&"):
        content, span = _dissolve_multicolumn(_dissolve_rotatebox(raw))
        columns.append((re.sub(r"\\[a-zA-Z]+\{?|[{}$]", "", content).strip(), span))
    return columns


def _expand_spans(row: list[tuple[str, int]]) -> list[str]:
    """One entry per column: a spanning header names every column it covers."""
    return [cell for cell, span in row for _ in range(span)]


def _table_body(environment: str, body: str) -> str:
    """The rows of a table environment, without the arguments of its opening.

    A width and a column spec are not cell content: left in place they are
    split with the first row. The groups are brace-matched because a column
    spec nests (``p{3cm}``, ``@{\\extracolsep{\\fill}}``).
    """
    index = 0
    for _ in range(_TABLE_ENVIRONMENTS[environment]):
        group = _brace_group(body, index)
        if group is None:
            break
        index = group[1]
    return body[index:]


def _parse_tables(text: str, file: str) -> list[TableCell]:
    cells: list[TableCell] = []
    caption_spans = _float_captions(text)
    for table_index, tab_match in enumerate(_TABLE_RE.finditer(text)):
        body = _table_body(tab_match.group(1), tab_match.group(3))
        caption = _caption_at(caption_spans, tab_match.start())
        base_line = _line_of(text, tab_match.start())
        rows: list[list[tuple[str, int]]] = []
        cited: list[bool] = []
        for raw_row in body.split("\\\\"):
            cleaned = re.sub(r"\\(?:hline|toprule|midrule|bottomrule|cline\{[^}]*\}|begin\{tabular\}\{[^}]*\}|end\{tabular\})", "", raw_row)
            columns = _split_spans(cleaned)
            if any(cell for cell, _ in columns):
                rows.append(columns)
                # The label only, and before the cleanup: a citation beside a
                # number is a note on that number, while a citation in the row
                # label names the paper the whole row came from.
                cited.append(_attributes_to_others(cleaned.split("&")[0]))
        if len(rows) < 2:
            continue
        # A spanning header names every column it covers, so it repeats across
        # the span. A spanning body cell states one number, so only its first
        # column carries it and the rest are placeholders keeping the row in
        # step with the header.
        header = _expand_spans(rows[0])
        for row_index, spanned in enumerate(rows[1:], start=1):
            row: list[str] = []
            for cell, span in spanned:
                row.append(cell)
                row.extend([""] * (span - 1))
            if not row:
                continue
            row_label = row[0]
            for col_index, cell in enumerate(row[1:], start=1):
                num = re.fullmatch(r"-?\d+(?:\.\d+)?", cell.replace("\\%", "").strip())
                if not num:
                    continue
                # The header names this row's columns only when the two rows
                # have the same width once spans are expanded. A span this
                # parser cannot read (a malformed ``\\multicolumn``, or one
                # built by a macro) leaves the rows offset, and
                # ``header[col_index]`` would then name some other column's
                # metric: a confident wrong name, which is worse than none.
                # Where the widths disagree the columns are labelled
                # positionally, as for a column the header does not reach.
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


def _state_command_spans(text: str) -> list[tuple[int, int]]:
    r"""The source span of every counter- and length-setting command call.

    Arguments included, because the arguments are the whole problem: the number
    a counter is set to, or the prose right after its name, is what gets read as
    a measurement. Brace-matched, because a length is routinely set from another
    length (``\setlength{\tabcolsep}{\dimexpr\columnsep/2}``), and a call whose
    groups do not match is left alone rather than guessed at.

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

    ``\setcounter{tocdepth}{2}`` sets how deep a table of contents goes;
    ``\setlength{\tabcolsep}{6pt}`` sets how wide a column gutter is. Neither
    prints anything, so neither states a number the paper reports, but a name
    such as ``layers`` or ``headsep`` puts a hyperparameter keyword in front of
    whatever number comes next.

    Removed rather than guarded against at the point of reading. A guard would
    have to recognise the shape from inside a short window, while the command is
    unambiguous where it is written. Line breaks are kept, so no locator moves,
    and verbatim regions are skipped, because a command a paper prints is text
    rather than markup.
    """
    return _remove_spans(text, _state_command_spans)


def _remove_spans(text: str, finder: Callable[[str], list[tuple[int, int]]]) -> str:
    """*text* with each span *finder* reports replaced by its own line breaks.

    Verbatim regions are passed through untouched and never searched.
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


def _resolve_include(target: str, including: str, root_dir: str, known: set[str]) -> str | None:
    """The file an include names, resolved the way LaTeX resolves it.

    LaTeX resolves ``\\input`` against the directory it runs in, which is the
    root document's, so a section file under ``sec/`` that inputs
    ``tables/x`` means ``<root dir>/tables/x``. The including file's own
    directory and the tree root are tried after it, because e-print tarballs
    also reference each other that way. A missing ``.tex`` extension is
    implied.
    """
    if not target:
        return None
    bases = dict.fromkeys((root_dir, posixpath.dirname(including), ""))
    for base in bases:
        for name in (target, f"{target}.tex"):
            candidate = posixpath.normpath(posixpath.join(base, name))
            if candidate in known:
                return candidate
    return None


def _compiled_sources(sources: dict[str, str]) -> set[str]:
    """The files reachable from a ``\\documentclass`` root, that root included.

    A ``.tex`` file no include reaches is not part of the paper: an e-print
    tarball often carries a superseded draft with different numbers, and
    reading it reports numbers that appear in no rendered document. Repeated
    and circular includes are visited once. When the graph explains nothing
    (no root, or a root that reaches no other file), every source is returned
    instead, so a tree using an inclusion mechanism this does not follow keeps
    yielding evidence.
    """
    known = set(sources)
    roots = {path for path, text in sources.items() if "\\documentclass" in text}
    if not roots:
        return known
    reachable: set[str] = set()
    pending = [(root, posixpath.dirname(root)) for root in sorted(roots)]
    while pending:
        current, root_dir = pending.pop()
        if current in reachable:
            continue
        reachable.add(current)
        for match in _INPUT_RE.finditer(sources[current]):
            resolved = _resolve_include(match.group(1).strip(), current, root_dir, known)
            if resolved is not None:
                pending.append((resolved, root_dir))
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
        clean = _strip_state_commands(sources[rel])
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
