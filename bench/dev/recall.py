#!/usr/bin/env python3
"""Claim-extraction recall and precision, measured against a labelled dev pair.

Recall runs paper -> extraction: ``bench/dev/labels/<id>.json`` samples the
paper's own reported numbers (see ``bench/dev/README.md``), and this module
matches the shipped extractor's output against that sample. Precision runs
the other way, extraction -> paper, and cannot be answered from the same
sample -- under a sampled frame most extractions fall outside it, landing in
``unknown`` rather than ``false_positive``, so precision built from labels
alone is undefined for most of them. It is instead computed from
``bench/dev/verifications/<id>.json``, a per-extraction adjudication
(``real_own_result`` | ``baseline`` | ``hyperparameter`` | ``not_in_paper`` |
``unclear``), with ``unclear`` excluded from the denominator and reported
alongside. Recall and precision therefore have independent availability: a
pair may carry one and not the other. The six label-side classes below
(``matched``, ``missed``, ``baseline_extracted``, ``wrong_kind``, ``unknown``,
``false_positive``) remain as a diagnostic over the recall frame; none of
them is precision and none is presented as precision.

The measurement path is exactly ``check``'s: ``scan_repository`` the code
tree, ``evidence.collect`` it, replace ``ev.latex`` with ``collect_latex``
over the paper tree, then read ``scaffold_manifest(ev).claims`` -- so this
measures the shipped extractor, not a reimplementation of it. ``--src``
resolves that pipeline from an arbitrary source tree instead of this
repository's own, which is what lets a historical extractor be measured
retroactively against labels written long after it. That swap happens in a
fresh subprocess (the ``_extract`` subcommand below), never in this process:
this module's own matching functions need ``adduce.naming`` and
``adduce.rules.drift`` from the *current* tree regardless of which tree is
being measured, so nothing here imports ``adduce`` at module scope -- doing
so would fix the first import to whichever tree happened to load first. A
``--src`` tree with no importable ``adduce`` package is refused rather than
measured (:func:`src_refusal`), and every report states the directory the
extractor imported ``adduce`` from, because an arm that quietly fell through to
the installed package measures *this* repository and reads as "nothing changed".

Matching an extraction to a label requires both the value (rounding-aware,
via ``adduce.rules.drift.values_match``) and the metric (canonicalised via
``adduce.naming.canonical_metric``) to agree, and is one-to-one: a label is
satisfied by at most one extraction and an extraction satisfies at most one
label. Value-only agreement is reported separately, as a diagnostic, never
counted as a match -- matching on value alone reaches the right answer for
the wrong reason whenever every candidate happens to name the right metric.

Recall sees only the 20 pairs of the roster's 34 that carry labels, and
precision only the 4 that carry adjudications, so neither can observe an
extractor change that destroys an *unlabelled* paper: one did, deleting two
whole papers (624 table cells to 0, 66 to 0) with pooled recall unmoved.
``inventory`` is the gate that sees it -- table cells and drafted claims for
every roster row, ground truth or none -- and ``compare-inventory`` diffs two
such runs and names every pair whose counts moved in either direction.

Nothing here fabricates a number. A pair whose code tree, paper tree, label
file, or verification file is absent is reported ``unavailable`` for the
metric that input feeds, with the reason, and never contributes a zero to an
aggregate. Precision is held to the same standard against a *stale* input as
against a missing one: an adjudication describes the extractions of the tree
it was written against, so it is reported only while it still corresponds to
what the extractor produces now (see :func:`verification_coverage`).
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeGuard

_BENCH_DEV_ROOT = Path(__file__).resolve().parent
_REPOSITORY_ROOT = _BENCH_DEV_ROOT.parents[1]
_DEFAULT_SRC = _REPOSITORY_ROOT / "src"
if str(_DEFAULT_SRC) not in sys.path:
    sys.path.insert(0, str(_DEFAULT_SRC))

REPORT_SCHEMA = "adduce-bench-dev-recall/1"
INVENTORY_SCHEMA = "adduce-bench-dev-inventory/1"
INVENTORY_COMPARISON_SCHEMA = "adduce-bench-dev-inventory-comparison/1"
_EXTRACT_TIMEOUT_SECONDS = 900

_VALID_ROLES = frozenset({"result", "hyperparameter", "dataset_statistic"})
#: ``in_repo_not_paper`` is a claim the repository states -- a results table in
#: its README -- that the paper does not. It is not a false positive: the
#: artifact really does assert it, and surfacing claims the artifact makes is the
#: system's job. It is also not a hit against a paper-scoped label set. So it is
#: excluded from the precision denominator, exactly like ``unclear``, and
#: reported. Folding it into ``not_in_paper`` understated bert's precision by
#: calling six real README results fabrications.
_EXCLUDED_FROM_PRECISION = frozenset({"unclear", "in_repo_not_paper"})

_VALID_VERDICTS = frozenset(
    {
        "real_own_result",
        "baseline",
        "hyperparameter",
        "not_in_paper",
        "in_repo_not_paper",
        "unclear",
    }
)

#: fetch.py's layout (bench/dev/fetch.py: ``clone_pinned``/``fetch_paper``).
#: Duplicated rather than imported, the way fetch.py itself duplicates
#: clone_repos.py's git isolation: the two files are owned separately and
#: should not need to change in lockstep.
_ROSTER_CODE_SUBPATH = "code"
_ROSTER_PAPER_SUBPATH = ("paper", "src")


class RecallInputError(ValueError):
    """A label or verification file is present but malformed."""


# -- ground truth -------------------------------------------------------------


@dataclass(frozen=True)
class Label:
    """One reported number a human transcribed from the rendered PDF."""

    id: str
    metric: str
    value: float
    role: str  # result | hyperparameter | dataset_statistic
    is_own_result: bool
    confident: bool = True
    units: str | None = None
    dataset: str | None = None
    split: str | None = None
    location: dict[str, Any] = field(default_factory=dict)
    notes: str = ""


@dataclass(frozen=True)
class LabelFrame:
    """One pair's labels, plus the sampled frame they were drawn from."""

    pair_id: str
    sampled: bool
    sampling_seed: int | None
    frame: dict[str, int]
    labels: tuple[Label, ...]


@dataclass(frozen=True)
class Verification:
    """One human adjudication of one extraction, independent of any label."""

    metric: str
    value: float
    verdict: str  # real_own_result | baseline | hyperparameter | not_in_paper | unclear
    where: str | None = None
    row_label: str | None = None
    column_label: str | None = None
    notes: str = ""


@dataclass(frozen=True)
class VerificationSet:
    pair_id: str
    verifications: tuple[Verification, ...]


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _is_number(value: Any) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _read_json(path: Path, *, label: str) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RecallInputError(f"no {label} at {path}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RecallInputError(f"{label} at {path} is not valid JSON: {exc}") from exc


def _parse_label(entry: Any, path: Path) -> Label:
    if not isinstance(entry, dict):
        raise RecallInputError(f"{path}: every label must be an object")
    label_id = entry.get("id")
    if not isinstance(label_id, str) or not label_id:
        raise RecallInputError(f"{path}: a label is missing its id")
    metric = entry.get("metric")
    if not isinstance(metric, str) or not metric:
        raise RecallInputError(f"{path}: {label_id}.metric is required")
    value = entry.get("value")
    if not _is_number(value):
        raise RecallInputError(f"{path}: {label_id}.value must be a number")
    role = entry.get("role")
    if role not in _VALID_ROLES:
        raise RecallInputError(f"{path}: {label_id}.role must be one of {sorted(_VALID_ROLES)}")
    is_own_result = entry.get("is_own_result")
    if not isinstance(is_own_result, bool):
        raise RecallInputError(f"{path}: {label_id}.is_own_result must be a boolean")
    confident = entry.get("confident", True)
    if not isinstance(confident, bool):
        raise RecallInputError(f"{path}: {label_id}.confident must be a boolean")
    location = entry.get("location") or {}
    if not isinstance(location, dict):
        raise RecallInputError(f"{path}: {label_id}.location must be an object")
    return Label(
        id=label_id,
        metric=metric,
        value=float(value),
        role=role,
        is_own_result=is_own_result,
        confident=confident,
        units=_optional_str(entry.get("units")),
        dataset=_optional_str(entry.get("dataset")),
        split=_optional_str(entry.get("split")),
        location=dict(location),
        notes=str(entry.get("notes", "")),
    )


def load_label_frame(path: Path) -> LabelFrame:
    """Parse one pair's label file, raising :class:`RecallInputError` on any defect."""
    raw = _read_json(path, label="label file")
    if not isinstance(raw, dict):
        raise RecallInputError(f"label file at {path} must contain a JSON object")
    pair_id = raw.get("pair_id")
    if not isinstance(pair_id, str) or not pair_id:
        raise RecallInputError(f"{path}: pair_id is required")
    sampled = raw.get("sampled")
    if not isinstance(sampled, bool):
        raise RecallInputError(f"{path}: sampled must be a boolean")
    seed = raw.get("sampling_seed")
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
        raise RecallInputError(f"{path}: sampling_seed must be an integer or null")
    frame = raw.get("frame")
    if not isinstance(frame, dict):
        raise RecallInputError(f"{path}: frame must be an object")
    for key, count in frame.items():
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise RecallInputError(f"{path}: frame[{key!r}] must be a non-negative integer")
    raw_labels = raw.get("labels")
    if not isinstance(raw_labels, list):
        raise RecallInputError(f"{path}: labels must be a list")
    labels = tuple(_parse_label(entry, path) for entry in raw_labels)
    return LabelFrame(
        pair_id=pair_id, sampled=sampled, sampling_seed=seed, frame=dict(frame), labels=labels
    )


def _parse_verification(entry: Any, path: Path) -> Verification:
    if not isinstance(entry, dict):
        raise RecallInputError(f"{path}: every verification must be an object")
    extraction = entry.get("extraction")
    if not isinstance(extraction, dict):
        raise RecallInputError(f"{path}: verification.extraction must be an object")
    metric = extraction.get("metric")
    if not isinstance(metric, str) or not metric:
        raise RecallInputError(f"{path}: verification.extraction.metric is required")
    value = extraction.get("value")
    if not _is_number(value):
        raise RecallInputError(f"{path}: verification.extraction.value must be a number")
    verdict = entry.get("verdict")
    if verdict not in _VALID_VERDICTS:
        raise RecallInputError(f"{path}: verdict must be one of {sorted(_VALID_VERDICTS)}")
    return Verification(
        metric=metric,
        value=float(value),
        verdict=verdict,
        where=_optional_str(extraction.get("where")),
        row_label=_optional_str(extraction.get("row_label")),
        column_label=_optional_str(extraction.get("column_label")),
        notes=str(entry.get("notes", "")),
    )


def load_verification_set(path: Path) -> VerificationSet:
    """Parse one pair's verification file, raising :class:`RecallInputError` on any defect."""
    raw = _read_json(path, label="verification file")
    if not isinstance(raw, dict):
        raise RecallInputError(f"verification file at {path} must contain a JSON object")
    pair_id = raw.get("pair_id")
    if not isinstance(pair_id, str) or not pair_id:
        raise RecallInputError(f"{path}: pair_id is required")
    raw_verifications = raw.get("verifications")
    if not isinstance(raw_verifications, list):
        raise RecallInputError(f"{path}: verifications must be a list")
    verifications = tuple(_parse_verification(entry, path) for entry in raw_verifications)
    return VerificationSet(pair_id=pair_id, verifications=verifications)


# -- the extractor's side ------------------------------------------------------


@dataclass(frozen=True)
class ExtractedClaim:
    """One ``manifest.Claim``, reduced to what matching needs.

    ``confidence`` and ``resolution_method`` are how the number was read, not
    how good it is. They are optional because a tree measured through ``--src``
    may predate the manifest fields that carry them, in which case they are
    absent rather than zero -- an unknown confidence is not a low one. The same
    holds of ``row_label`` and ``column_label``, which name the cell a table
    claim was read from, and are absent for a claim read from prose.
    """

    metric: str | None
    value: float | None
    where: str | None
    text: str
    confidence: float | None = None
    resolution_method: str | None = None
    row_label: str | None = None
    column_label: str | None = None


def _extracted_claim_from_json(entry: dict[str, Any]) -> ExtractedClaim:
    raw_value = entry.get("value")
    raw_confidence = entry.get("confidence")
    return ExtractedClaim(
        metric=_optional_str(entry.get("metric")),
        value=float(raw_value) if _is_number(raw_value) else None,
        where=_optional_str(entry.get("where")),
        text=_optional_str(entry.get("text")) or "",
        confidence=float(raw_confidence) if _is_number(raw_confidence) else None,
        resolution_method=_optional_str(entry.get("resolution_method")),
        row_label=_optional_str(entry.get("row_label")),
        column_label=_optional_str(entry.get("column_label")),
    )


# -- recall: matching extractions to labels -----------------------------------


@dataclass(frozen=True)
class RecallDiagnostic:
    """Six classes over the recall frame, plus the recall estimate.

    ``matched`` a label satisfied by an extraction on metric and value;
    ``missed`` a recall-denominator label with no extraction; ``baseline_extracted``
    a label matched but ``is_own_result: false``; ``wrong_kind`` a label matched but
    not ``role: result``; ``unknown`` a leftover extraction of an established
    result metric under a sampled (not exhaustive) frame; ``false_positive``
    any other leftover extraction. A diagnostic over the recall frame, not
    precision -- see the module docstring. ``recall`` is ``None`` when the
    denominator is zero (every label was a baseline, a hyperparameter, or
    excluded as unconfident): an undefined rate, never a fabricated zero.

    ``unnameable_labels`` counts denominator labels whose own metric does not
    canonicalise. Matching requires both sides to canonicalise, so such a label
    can never be satisfied by any extraction whatsoever, and reporting it as a
    plain miss states a vocabulary gap as an extractor failure. It stays in the
    denominator -- the paper really does report that number, and a metric adduce
    cannot name is a real limitation -- but it is surfaced separately so the two
    causes are never read as one.
    """

    matched: int
    missed: int
    baseline_extracted: int
    wrong_kind: int
    unknown: int
    false_positive: int
    value_only_matches: int
    recall_denominator: int
    excluded_unconfident: int
    unnameable_labels: int
    recall: float | None


@dataclass(frozen=True)
class _NumericClaim:
    """An :class:`ExtractedClaim` known to carry a value -- matching's own type.

    Isolated so ``value`` is ``float``, not ``float | None``, everywhere
    matching touches it: a claim with no value (the README-fallback
    placeholder) cannot be compared numerically and is filtered out once,
    here, rather than guarded at every comparison site. ``index`` is the
    claim's position in the sequence it was filtered out of, so an assignment
    made here names a row of the caller's own extraction.
    """

    index: int
    metric: str | None
    value: float
    where: str | None
    text: str
    confidence: float | None = None
    row_label: str | None = None
    column_label: str | None = None


def _numeric_claims(claims: Sequence[ExtractedClaim]) -> list[_NumericClaim]:
    result: list[_NumericClaim] = []
    for index, claim in enumerate(claims):
        if claim.value is None:
            continue
        result.append(
            _NumericClaim(
                index=index,
                metric=claim.metric,
                value=claim.value,
                where=claim.where,
                text=claim.text,
                confidence=claim.confidence,
                row_label=claim.row_label,
                column_label=claim.column_label,
            )
        )
    return result


def _claim_sort_key(claim: _NumericClaim) -> tuple[str, str, float, str]:
    return (claim.where or "", claim.metric or "", claim.value, claim.text)


def classify_recall(claims: Sequence[ExtractedClaim], frame: LabelFrame) -> RecallDiagnostic:
    """Match extractions to labels and report the six recall-frame classes.

    Matching is one-to-one on both sides: labels and claims are each sorted
    into a total order first (by id, and by location/metric/value/text
    respectively), so the result never depends on file or extraction order,
    and a label consumes at most one claim -- a duplicate extraction of an
    already-satisfied label cannot inflate ``matched``.

    Labels marked ``confident: false`` are excluded from matching entirely
    (counted in ``excluded_unconfident``), so an ambiguous reading can never
    silently become a miss.
    """
    from adduce.naming import canonical_metric
    from adduce.rules.drift import values_match

    def metric_matches(claim_metric: str | None, label_metric: str | None) -> bool:
        if not claim_metric or not label_metric:
            return False
        left = canonical_metric(claim_metric)
        right = canonical_metric(label_metric)
        return left is not None and left == right

    def is_denominator(label: Label) -> bool:
        return label.role == "result" and label.is_own_result

    numeric_claims = sorted(_numeric_claims(claims), key=_claim_sort_key)
    considered = [label for label in frame.labels if label.confident]
    excluded_unconfident = len(frame.labels) - len(considered)
    ordered_labels = sorted(considered, key=lambda label: label.id)

    pool = list(numeric_claims)
    matched = missed = baseline_extracted = wrong_kind = 0
    for label in ordered_labels:
        index = next(
            (
                i
                for i, claim in enumerate(pool)
                if metric_matches(claim.metric, label.metric)
                and values_match(label.value, claim.value)
            ),
            None,
        )
        if index is None:
            if is_denominator(label):
                missed += 1
            continue
        pool.pop(index)
        if label.role != "result":
            wrong_kind += 1
        elif not label.is_own_result:
            baseline_extracted += 1
        else:
            matched += 1

    # A metric-level proxy for "this extraction sits somewhere in the paper's
    # result frame". Individual extractions cannot be placed inside a named
    # table or section with today's evidence model (a Claim carries a
    # file:line, not a table label), so membership is approximated by metric:
    # a leftover extraction whose metric is one this pair genuinely reports as
    # an own result, under a sample that was not labelled exhaustively, is
    # `unknown` rather than a confident `false_positive`.
    known_result_metrics = {
        canonical_metric(label.metric)
        for label in ordered_labels
        if is_denominator(label) and canonical_metric(label.metric) is not None
    }
    unknown = false_positive = 0
    for claim in pool:
        canonical = canonical_metric(claim.metric) if claim.metric else None
        if frame.sampled and canonical is not None and canonical in known_result_metrics:
            unknown += 1
        else:
            false_positive += 1

    # An independent, hypothetical pass over the same recall-denominator
    # labels, ignoring metric agreement entirely -- not a residual of the pass
    # above -- so it answers "what would matching on value alone have
    # reached", making the cost of requiring the metric visible.
    value_pool = list(numeric_claims)
    value_only_matches = 0
    for label in ordered_labels:
        if not is_denominator(label):
            continue
        index = next(
            (i for i, claim in enumerate(value_pool) if values_match(label.value, claim.value)),
            None,
        )
        if index is not None:
            value_pool.pop(index)
            value_only_matches += 1

    denominator = sum(1 for label in ordered_labels if is_denominator(label))
    unnameable = sum(
        1
        for label in ordered_labels
        if is_denominator(label) and canonical_metric(label.metric) is None
    )
    recall = matched / denominator if denominator else None
    return RecallDiagnostic(
        matched=matched,
        missed=missed,
        baseline_extracted=baseline_extracted,
        wrong_kind=wrong_kind,
        unknown=unknown,
        false_positive=false_positive,
        value_only_matches=value_only_matches,
        recall_denominator=denominator,
        excluded_unconfident=excluded_unconfident,
        unnameable_labels=unnameable,
        recall=recall,
    )


# -- precision: adjudicating extractions --------------------------------------


@dataclass(frozen=True)
class PrecisionResult:
    """A tally over one pair's verification file, plus what it cost confidently.

    ``high_confidence_false_positives`` counts adjudicated extractions inside
    the precision denominator that are not ``real_own_result`` and that the
    extractor produced at ``confidence == 1.0``. It is the §17 acceptance
    criterion "zero high-confidence false positives", which was not computable
    while the manifest dropped a claim's resolution method on the floor.
    ``unjoined_false_positives`` counts the ones whose confidence could not be
    established -- see :func:`compute_precision`. Both are ``None`` when no
    extraction was supplied to join against: not measured, never zero.
    """

    real_own_result: int
    baseline: int
    hyperparameter: int
    not_in_paper: int
    in_repo_not_paper: int
    unclear: int
    adjudicated: int  # every verdict except those excluded from the denominator
    precision: float | None
    high_confidence_false_positives: int | None = None
    unjoined_false_positives: int | None = None


_LOCATOR_PATTERN = re.compile(r"^(?P<path>.+):(?P<line>\d+)$")

_ValueKey = tuple[str | None, float]
_LocatedKey = tuple[str | None, float, str, str]


def _split_locator(where: str | None) -> tuple[str, str] | None:
    """A ``path:line`` locator split into its parts, or ``None`` for anything else.

    ``manifest.Claim.where`` is a free-text field and the README-fallback
    claim really does put prose in it, so a locator is parsed, never assumed.
    """
    if where is None:
        return None
    match = _LOCATOR_PATTERN.match(where)
    if match is None:
        return None
    return match.group("path"), match.group("line")


def _is_path_suffix(candidate: str, path: str) -> bool:
    return path == candidate or path.endswith("/" + candidate)


def _normalise_label(value: str | None) -> str | None:
    """A row or column label reduced to what the two sides can agree on.

    Case and runs of whitespace are flattened and nothing else: a human records
    a label by reading the rendered table, the extractor records the cell text
    it parsed, and those two differ in spacing and capitalisation. Any further
    normalisation would be a guess, and one that need not be made: a label
    agreeing with no extraction beyond this degrades to the locator and is
    counted (:func:`_align`) rather than losing the match. An empty label is no
    label.
    """
    if value is None:
        return None
    return " ".join(value.split()).casefold() or None


def _recorded_labels(verification: Verification) -> tuple[str | None, str | None]:
    return (
        _normalise_label(verification.row_label),
        _normalise_label(verification.column_label),
    )


def _resolve_paths(extracted: Sequence[str], adjudicated: Iterable[str]) -> dict[str, str]:
    """Each adjudicated path mapped onto the extraction path naming the same file.

    The two sides are rooted differently, and by a depth that varies within
    one pair rather than by a constant prefix: a verification file records
    ``src/main.tex`` for a paper measured here from ``src`` itself, while a
    repository README keeps ``object_detection/README.md`` on both sides. So
    the root is recovered per file. A path resolves to itself when the
    extraction already states it, and otherwise to the single extraction path
    that is a ``/``-boundary suffix of it, or that it is a suffix of. Several
    candidates and none are both left unresolved rather than guessed.

    Measured over the four adjudicated pairs, against 674 verdicts: the raw
    locator resolves 26, the basename 660 but collapsing convnext's
    ``object_detection/README.md:18`` and ``semantic_segmentation/README.md:18``
    onto one key, dropping the first path component 601 with the same
    collapse, and this 660 with no two locators sharing a key.
    """
    known = set(extracted)
    resolved: dict[str, str] = {}
    for path in adjudicated:
        if path in resolved:
            continue
        if path in known:
            resolved[path] = path
            continue
        candidates = [
            candidate
            for candidate in extracted
            if _is_path_suffix(candidate, path) or _is_path_suffix(path, candidate)
        ]
        if len(candidates) == 1:
            resolved[path] = candidates[0]
    return resolved


@dataclass(frozen=True)
class _MatchIndex:
    """One pair's extraction under both keys the join may use.

    Built once and shared, so :func:`compute_precision` reads the confidence
    of the extraction a verdict was actually assigned rather than repeating
    the assignment under a weaker key.
    """

    claims: tuple[_NumericClaim, ...]
    by_location: dict[_LocatedKey, tuple[int, ...]]
    by_value: dict[_ValueKey, tuple[int, ...]]
    resolved_paths: dict[str, str]

    def value_key(self, verification: Verification) -> _ValueKey:
        return (verification.metric, verification.value)

    def located_key(self, verification: Verification) -> _LocatedKey | None:
        split = _split_locator(verification.where)
        if split is None:
            return None
        path = self.resolved_paths.get(split[0])
        if path is None:
            return None
        return (verification.metric, verification.value, path, split[1])

    def pool(
        self, verification: Verification, *, located: bool, narrowed: bool = True
    ) -> tuple[int, ...]:
        """The extractions one verdict may be assigned, at one strength of key.

        The locator groups; the labels narrow within that group. A verdict
        recording a label is offered only the extractions agreeing on it, so
        the narrowed pool is always a subset of the one the locator alone would
        give and the key can only get stronger. A verdict recording neither
        label is offered the whole group, which is what it was offered before
        labels existed. One label and not the other narrows by that one: a
        transposed table names its rows and leaves the column heading blank,
        and half a key is still more than none.

        ``narrowed=False`` drops the labels the verdict does record, which is
        the weakest form of each key. :func:`_align` reaches for it only after
        every verdict has been offered its narrowed pool, so a verdict whose
        label no longer agrees with any extraction degrades to the group rather
        than going stale, and cannot take an extraction another verdict names
        exactly.
        """
        if located:
            key = self.located_key(verification)
            group = self.by_location.get(key, ()) if key is not None else ()
        else:
            group = self.by_value.get(self.value_key(verification), ())
        if not narrowed:
            return group
        row, column = _recorded_labels(verification)
        if row is None and column is None:
            return group
        return tuple(
            position
            for position in group
            if (row is None or _normalise_label(self.claims[position].row_label) == row)
            and (column is None or _normalise_label(self.claims[position].column_label) == column)
        )


def _build_match_index(
    claims: Sequence[ExtractedClaim], verifications: VerificationSet
) -> _MatchIndex:
    numeric = _numeric_claims(claims)
    extracted_paths: list[str] = []
    located: dict[_LocatedKey, list[int]] = {}
    valued: dict[_ValueKey, list[int]] = {}
    for position, claim in enumerate(numeric):
        valued.setdefault((claim.metric, claim.value), []).append(position)
        split = _split_locator(claim.where)
        if split is None:
            continue
        extracted_paths.append(split[0])
        located.setdefault((claim.metric, claim.value, split[0], split[1]), []).append(position)
    adjudicated_paths = (
        split[0]
        for split in (_split_locator(v.where) for v in verifications.verifications)
        if split is not None
    )
    return _MatchIndex(
        claims=tuple(numeric),
        by_location={key: tuple(positions) for key, positions in located.items()},
        by_value={key: tuple(positions) for key, positions in valued.items()},
        resolved_paths=_resolve_paths(sorted(set(extracted_paths)), adjudicated_paths),
    )


@dataclass(frozen=True)
class VerdictAlignment:
    """Which live extraction each verdict adjudicates, and how that was decided.

    ``matched`` maps a verdict's position in the verification file to the
    position of its extraction in the sequence supplied, so a stale verdict
    and an unadjudicated extraction are identified rows rather than an excess
    counted per key. ``fallbacks`` is the subset matched on ``(metric, value)``
    alone -- the verdict carries no locator, or one no live extraction can be
    reconciled with, or one whose extraction has since moved.
    ``label_fallbacks`` is the subset matched without a row or column label,
    which is every match a file recording no labels can make.
    ``label_degradations`` is a different quantity and must not be read as that
    one: the subset that *did* record a label and was matched with it dropped,
    because no extraction under the verdict's key agrees on it any more. The
    first says the file never named a cell; the second says the cell it named
    has moved.
    """

    matched: dict[int, int]
    fallbacks: frozenset[int]
    label_fallbacks: frozenset[int]
    label_degradations: frozenset[int]
    unmatched_verdicts: tuple[int, ...]
    unmatched_claims: tuple[int, ...]


#: ``(located, narrowed)`` per pass, strongest key first. The locator is
#: dropped before the labels are: a locator reconciling with no live extraction
#: says only that the number moved in the file, which is routine, while a label
#: agreeing with none says the cell a human read is not there any more. Every
#: pass offers a superset of the first, and each is offered only what the passes
#: before it left, so no degraded match can take an extraction that some verdict
#: names exactly. A verdict recording no label is offered the same pool
#: narrowed or not, so the last two passes can never place one and never count
#: one as degraded.
_ALIGNMENT_STAGES: tuple[tuple[bool, bool], ...] = (
    (True, True),
    (False, True),
    (True, False),
    (False, False),
)


def _align(index: _MatchIndex, verifications: VerificationSet) -> VerdictAlignment:
    taken: set[int] = set()
    matched: dict[int, int] = {}
    fallbacks: set[int] = set()
    degradations: set[int] = set()

    def take(pool: tuple[int, ...]) -> int | None:
        return next((position for position in pool if position not in taken), None)

    # A verdict recording labels is offered a subset of what one without them
    # is offered, so it is placed first: otherwise an unlabelled verdict
    # earlier in the file can take the one extraction a labelled verdict names,
    # and a match that the stronger key identifies exactly is lost to a match
    # the weaker key made arbitrarily. The sort is stable, so file order still
    # decides within each group and nothing moves for a file recording none.
    pending = sorted(
        range(len(verifications.verifications)),
        key=lambda position: _recorded_labels(verifications.verifications[position])
        == (None, None),
    )
    for located, narrowed in _ALIGNMENT_STAGES:
        unplaced: list[int] = []
        for verdict in pending:
            verification = verifications.verifications[verdict]
            position = take(index.pool(verification, located=located, narrowed=narrowed))
            if position is None:
                unplaced.append(verdict)
                continue
            taken.add(position)
            matched[verdict] = index.claims[position].index
            if not located:
                fallbacks.add(verdict)
            if not narrowed:
                degradations.add(verdict)
        pending = unplaced

    return VerdictAlignment(
        matched=matched,
        fallbacks=frozenset(fallbacks),
        label_fallbacks=frozenset(
            verdict
            for verdict in matched
            if _recorded_labels(verifications.verifications[verdict]) == (None, None)
        ),
        label_degradations=frozenset(degradations),
        unmatched_verdicts=tuple(
            verdict for verdict in range(len(verifications.verifications))
            if verdict not in matched
        ),
        unmatched_claims=tuple(
            claim.index for position, claim in enumerate(index.claims) if position not in taken
        ),
    )


def align_verdicts(
    claims: Sequence[ExtractedClaim], verifications: VerificationSet
) -> VerdictAlignment:
    """Assign each verdict its extraction, on metric, value, locator and cell labels.

    Four passes, all one-to-one, over the keys in :data:`_ALIGNMENT_STAGES`.
    The first matches on the full key, with the verdict's path resolved into the
    extraction's own rooting by :func:`_resolve_paths` and the row and column
    labels the verdict records narrowing within that locator
    (:meth:`_MatchIndex.pool`). The second offers whatever is left to the
    verdicts the first could not place, on ``(metric, value)`` and the same
    labels -- the key this join used before locators were reconciled. The last
    two repeat both with the labels dropped, for the verdicts whose recorded
    label agrees with no live extraction.

    So a stronger key never loses a match the weaker key would have made: each
    field of the key falls back in turn rather than a mismatch on it ending the
    search, and the two degradations are counted separately (``fallbacks``,
    ``label_degradations``) so an identity resting on less than the full key is
    never read as one resting on all of it. Without the label fallback a verdict
    whose label differs from the extractor's by more than case and spacing went
    stale, losing a match the locator alone had made -- latent while no file
    recorded labels, live as soon as one did. Every pass walks the verification
    file and each key's extractions in order, so the assignment does not depend
    on iteration order anywhere.
    """
    return _align(_build_match_index(claims, verifications), verifications)


def compute_precision(
    verifications: VerificationSet, claims: Sequence[ExtractedClaim] | None = None
) -> PrecisionResult:
    """Precision from adjudications alone: real_own_result / adjudicated.

    ``unclear`` and ``in_repo_not_paper`` are excluded from the denominator and
    reported alongside, never silently dropped -- the first because it was not
    decided, the second because it is a claim the repository genuinely makes and
    a paper-scoped adjudication cannot credit or condemn it. ``precision`` is
    ``None`` when nothing was adjudicated -- undefined, never a fabricated zero.

    ``claims`` supplies the live extraction so each verdict can be joined to the
    confidence its extraction carried. Verification files record no confidence,
    so the join runs through :func:`align_verdicts`, exactly as
    :func:`verification_coverage` establishes correspondence, and reads the
    confidences of the extractions that shared the key the verdict was assigned
    under -- the located key where the locator reconciled, ``(metric, value)``
    where it fell back, narrowed by the labels the verdict records except where
    those degraded too. A verdict is counted as a high-confidence false positive
    only where every extraction under that key states ``1.0``; where they
    disagree, where the extraction states no confidence at all, or where no
    extraction was assigned, the answer is undecidable from the join and the
    verdict is counted as ``unjoined`` rather than guessed either way. The
    located key is the narrower group, and a recorded label narrows it again,
    so both can only reduce the number left unjoined. Omitting ``claims``
    leaves both counts ``None``.
    """
    tally = dict.fromkeys(_VALID_VERDICTS, 0)
    for verification in verifications.verifications:
        tally[verification.verdict] += 1
    adjudicated = sum(
        count for verdict, count in tally.items() if verdict not in _EXCLUDED_FROM_PRECISION
    )
    precision = tally["real_own_result"] / adjudicated if adjudicated else None

    high_confidence: int | None = None
    unjoined: int | None = None
    if claims is not None:
        index = _build_match_index(claims, verifications)
        alignment = _align(index, verifications)
        high_confidence = unjoined = 0
        for order, verification in enumerate(verifications.verifications):
            if verification.verdict in _EXCLUDED_FROM_PRECISION:
                continue
            if verification.verdict == "real_own_result":
                continue
            if order not in alignment.matched:
                unjoined += 1
                continue
            pool = index.pool(
                verification,
                located=order not in alignment.fallbacks,
                narrowed=order not in alignment.label_degradations,
            )
            stated = frozenset(index.claims[position].confidence for position in pool)
            if stated == frozenset({1.0}):
                high_confidence += 1
            elif len(stated) != 1 or None in stated:
                unjoined += 1

    return PrecisionResult(
        real_own_result=tally["real_own_result"],
        baseline=tally["baseline"],
        hyperparameter=tally["hyperparameter"],
        not_in_paper=tally["not_in_paper"],
        in_repo_not_paper=tally["in_repo_not_paper"],
        unclear=tally["unclear"],
        adjudicated=adjudicated,
        precision=precision,
        high_confidence_false_positives=high_confidence,
        unjoined_false_positives=unjoined,
    )


@dataclass(frozen=True)
class VerificationCoverage:
    """How far one verification file still describes what the extractor produces.

    ``unadjudicated`` is an extraction carrying no verdict; ``stale`` is a
    verdict matching no extraction. Both are reported whether or not precision
    is, so a reader can see *how far* a file has drifted rather than only that
    it has. ``location_fallbacks`` is how much of the key the file could not
    supply: verdicts placed on ``(metric, value)`` alone because their locator
    could not be reconciled with any live extraction. It does not stop a file
    corresponding -- those verdicts are matched, and refusing them would throw
    away an adjudication that is still exactly right -- but it states how far
    the identity rests on the weaker key. ``label_fallbacks`` says the same of
    the cell labels: verdicts matched without a row or column label, which are
    the ones a locator alone had to place. All four adjudicated files record
    labels on part of their verdicts, so it counts the remainder rather than
    every match.

    ``label_degradations`` is the other half of that, and a distinct quantity:
    verdicts that did record a label and were matched with it dropped, because
    no extraction under their key agrees on it any more. It is counted, and does
    not stop a file corresponding, on the same reasoning as
    ``location_fallbacks``: the alternative is reporting a whole file stale over
    a label a human transcribed differently. Non-zero here means an extractor
    change moved cell labels, and the assignment of those verdicts rests on
    their locator alone.
    """

    extractions: int
    verdicts: int
    unadjudicated: int
    stale: int
    location_fallbacks: int
    label_fallbacks: int
    label_degradations: int = 0

    @property
    def corresponds(self) -> bool:
        return self.unadjudicated == 0 and self.stale == 0


def verification_coverage(
    claims: Sequence[ExtractedClaim], verifications: VerificationSet
) -> VerificationCoverage:
    """Match adjudications to extractions, one to one, through :func:`align_verdicts`.

    An adjudication is a human reading of one extraction, so it describes the
    extractor that produced it and nothing else. Tallying the file alone
    therefore reports a precision for a set that may no longer exist:
    barlowtwins' file still tallies 27/58, measured, while the extractor it was
    written against has moved on and 63 of today's extractions carry no verdict
    at all.

    The identity is ``(metric, value, where, row_label, column_label)``, and
    ``where`` is load-bearing only because it is normalised and optional.
    Normalised, because the two sides are rooted differently: detr's verdicts
    were recorded from a paper root one level above the one measured here, and
    matching the locator raw reported all 144 of them as both unadjudicated and
    stale. Optional,
    because two locators for one number are routine -- convnext states twelve
    mIoU figures in both its paper and a task README, and which of the two
    survives clustering moved with an extractor change that left every number,
    and every verdict, untouched, all twelve still reading ``real_own_result``.
    A locator that cannot be reconciled therefore falls back rather than
    dropping the match, and the fallbacks are counted.

    What the stronger key buys is that a repeated ``(metric, value)`` stays
    decidable. Extractions are unique on ``(metric, value)`` today only
    because clustering de-duplicates globally on exactly that key; the moment
    that is repaired, a multiset difference over it stops naming which row is
    stale, and four adjudicated pairs have no way back to correspondence.

    The labels are what remains when the locator cannot decide either. Every
    cell of one ``tabular`` records the line its environment opens on, so two
    measurements a table states at one value under one metric share a locator
    exactly -- bert prints 88.5 as both a cited test F1 and its own dev F1.
    They are optional on the verdict side, so a file part-way through
    re-adjudication carries both kinds: a verdict recording labels is matched to
    an extraction agreeing on them where one exists, to its locator's group with
    the labels dropped where none does (counted in ``label_degradations``), and
    one recording no label at all is matched exactly as it was before, counted
    in ``label_fallbacks``.

    Claims carrying no value (the README-fallback placeholder) assert no
    number to adjudicate and are excluded here exactly as
    :func:`_numeric_claims` excludes them from matching.
    """
    alignment = align_verdicts(claims, verifications)
    return VerificationCoverage(
        extractions=len(_numeric_claims(claims)),
        verdicts=len(verifications.verifications),
        unadjudicated=len(alignment.unmatched_claims),
        stale=len(alignment.unmatched_verdicts),
        location_fallbacks=len(alignment.fallbacks),
        label_fallbacks=len(alignment.label_fallbacks),
        label_degradations=len(alignment.label_degradations),
    )


# -- the extraction worker: --src runs here, and only here --------------------


def src_refusal(src: Path | None) -> str | None:
    """Why this tree cannot be measured, or ``None``.

    A path with no ``adduce`` package under it is refused rather than measured.
    ``sys.path.insert`` of a directory that holds nothing does not fail: the
    import falls through to the editable install, so the worker would quietly
    measure this repository's own tree and a retroactive arm would read as a
    tree that changed nothing. Duplicated from
    ``bench/dev/manifest_identity.py`` rather than imported, the way this file
    duplicates fetch.py's layout: the two harnesses are owned separately.

    Only the extractor is swapped. This module's own matching functions import
    ``adduce.naming`` and ``adduce.rules.drift`` locally, from the current tree,
    whatever ``--src`` names -- so a historical extractor is scored by today's
    vocabulary rather than by its own, which is what makes two arms comparable.
    """
    if src is None:
        return None
    if not (src / "adduce" / "__init__.py").is_file():
        return f"no adduce package under {src}"
    return None


def _cmd_extract(arguments: argparse.Namespace) -> int:
    """Run the shipped measurement path for one pair and print its claims.

    Always executed in its own subprocess (see :func:`_run_extract_worker`),
    never called in-process: this is the only place ``--src`` is applied, and
    it must happen before the first ``adduce`` import in *this* process.
    """
    code: Path = arguments.code
    paper: Path = arguments.paper
    src: Path | None = arguments.src

    if not code.is_dir():
        json.dump({"available": False, "reason": f"code path is not a directory: {code}"}, sys.stdout)
        return 0
    if not paper.is_dir():
        json.dump(
            {"available": False, "reason": f"paper path is not a directory: {paper}"}, sys.stdout
        )
        return 0

    if refusal := src_refusal(src):
        json.dump({"available": False, "reason": refusal}, sys.stdout)
        return 0

    if src is not None:
        # Ahead of whatever is already on sys.path -- including this
        # repository's own <repo>/src, inserted by the module header above --
        # so this tree is the one "import adduce" resolves. Mirrors
        # bench/worker.py's own --src handling.
        sys.path.insert(0, str(src.resolve()))
    # Nothing above this point imports adduce, so there is normally nothing to
    # purge. Purged anyway, matching the measurement prototype this reuses, so
    # a future eager import elsewhere in this file cannot silently pin the
    # tree that happened to load first.
    for name in [m for m in sys.modules if m == "adduce" or m.startswith("adduce.")]:
        del sys.modules[name]

    from adduce import evidence as evidence_module
    from adduce import model
    from adduce.evidence.latex import collect_latex
    from adduce.manifest_builder import scaffold_manifest

    repo = model.scan_repository(code.resolve())
    ev = evidence_module.collect(repo)
    ev.latex = collect_latex(model.scan_repository(paper.resolve()))
    manifest = scaffold_manifest(ev)
    loaded_from = str(Path(sys.modules["adduce"].__file__ or "").parent)
    # getattr for the same reason the claim fields below use it: a tree reached
    # through --src may predate the field. Absent means unmeasured, reported as
    # null rather than as a zero -- a zero here is the very defect the
    # inventory exists to catch.
    cells = getattr(ev.latex, "table_cells", None)
    json.dump(
        {
            "available": True,
            "adduce_loaded_from": loaded_from,
            "table_cells": None if cells is None else len(cells),
            # getattr, not attribute access: a tree reached through --src may
            # predate the manifest fields carrying how a claim was resolved,
            # and a retroactive measurement must still run against it.
            "claims": [
                {
                    "metric": c.metric,
                    "value": c.value,
                    "where": c.where,
                    "text": c.text,
                    "confidence": getattr(c, "confidence", None),
                    "resolution_method": getattr(c, "resolution_method", None),
                    "row_label": getattr(c, "row_label", None),
                    "column_label": getattr(c, "column_label", None),
                }
                for c in manifest.claims
            ],
        },
        sys.stdout,
    )
    return 0


def _run_extract_worker(*, code: Path, paper: Path, src: Path | None) -> dict[str, Any]:
    """Shell out to this same file's ``_extract`` subcommand and parse its JSON.

    A fresh process per call, exactly as ``bench/runner.py`` shells out to
    ``bench/worker.py`` -- so a ``--src`` swap can never leak into this
    process's own ``sys.modules``, and two calls with different ``--src``
    values in a row (as the retroactive-measurement proof needs) cannot
    contaminate one another.
    """
    command = [
        sys.executable,
        "-B",
        "-W",
        "ignore",
        str(_BENCH_DEV_ROOT / "recall.py"),
        "_extract",
        "--code",
        str(code),
        "--paper",
        str(paper),
    ]
    if src is not None:
        command.extend(["--src", str(src)])
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=_EXTRACT_TIMEOUT_SECONDS,
            cwd=_REPOSITORY_ROOT,
        )
    except subprocess.TimeoutExpired:
        return {"available": False, "reason": f"extraction timed out after {_EXTRACT_TIMEOUT_SECONDS}s"}
    except OSError as exc:
        return {"available": False, "reason": f"could not start extraction worker: {exc}"}
    if result.returncode != 0:
        return {
            "available": False,
            "reason": f"extraction worker exited {result.returncode}",
            "stderr_tail": result.stderr[-2000:],
        }
    try:
        payload: dict[str, Any] = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {"available": False, "reason": f"extraction worker emitted invalid JSON: {exc}"}
    return payload


# -- one pair -------------------------------------------------------------


@dataclass(frozen=True)
class RecallReport:
    available: bool
    reason: str | None = None
    claim_count: int | None = None
    diagnostic: RecallDiagnostic | None = None


@dataclass(frozen=True)
class PrecisionReport:
    available: bool
    reason: str | None = None
    result: PrecisionResult | None = None
    coverage: VerificationCoverage | None = None


@dataclass(frozen=True)
class PairReport:
    """One pair under both metrics, and the tree its extraction was taken from.

    ``adduce_loaded_from`` is the directory the worker imported ``adduce``
    from -- the only evidence that a ``--src`` arm measured the tree it was
    given rather than falling through to the installed package. It is ``None``
    for a pair whose extraction never ran.
    """

    pair_id: str
    recall: RecallReport
    precision: PrecisionReport
    adduce_loaded_from: str | None = None


@dataclass(frozen=True)
class PairSpec:
    id: str
    code: Path
    paper: Path
    labels: Path
    verifications: Path | None = None


def extract_claims(*, code: Path, paper: Path, src: Path | None = None) -> dict[str, Any]:
    """One pair's extraction, refusing an absent tree without spawning a worker."""
    if not code.is_dir():
        return {"available": False, "reason": f"code directory not found: {code}"}
    if not paper.is_dir():
        return {"available": False, "reason": f"paper directory not found: {paper}"}
    if refusal := src_refusal(src):
        return {"available": False, "reason": refusal}
    return _run_extract_worker(code=code, paper=paper, src=src)


def _claims_of(extraction: dict[str, Any]) -> list[ExtractedClaim]:
    return [_extracted_claim_from_json(entry) for entry in extraction.get("claims", [])]


def evaluate_recall(
    *,
    code: Path,
    paper: Path,
    labels_path: Path,
    src: Path | None = None,
    extraction: dict[str, Any] | None = None,
) -> RecallReport:
    """Recall for one pair. Refuses eagerly, cheapest check first.

    A missing code or paper directory, or a missing or malformed label file,
    is reported unavailable before any extraction runs; a failed extraction
    is reported unavailable with the worker's own reason. ``extraction``
    supplies an already-computed payload, so a caller wanting both metrics
    pays for one extraction rather than two.
    """
    if not code.is_dir():
        return RecallReport(available=False, reason=f"code directory not found: {code}")
    if not paper.is_dir():
        return RecallReport(available=False, reason=f"paper directory not found: {paper}")
    try:
        frame = load_label_frame(labels_path)
    except RecallInputError as exc:
        return RecallReport(available=False, reason=str(exc))

    if extraction is None:
        extraction = extract_claims(code=code, paper=paper, src=src)
    if not extraction.get("available"):
        reason = extraction.get("reason", "extraction unavailable")
        return RecallReport(available=False, reason=str(reason))

    claims = _claims_of(extraction)
    diagnostic = classify_recall(claims, frame)
    return RecallReport(available=True, claim_count=len(claims), diagnostic=diagnostic)


def evaluate_precision(
    verifications_path: Path | None, extraction: dict[str, Any]
) -> PrecisionReport:
    """Precision for one pair, over the extractions the extractor produces now.

    The verification file is required to account for every current extraction
    and to describe no extraction that is gone: a file written against an older
    extractor otherwise keeps reporting a rate over a set that no longer
    exists. Where it does not, the coverage counts are reported in place of the
    rate -- a stale file is diagnosed, never guessed at, and never contributes
    to an aggregate. ``extraction`` is the worker payload, so an unavailable
    code or paper tree makes precision unavailable for the same reason recall
    is.
    """
    if verifications_path is None:
        return PrecisionReport(available=False, reason="no verification file configured for this pair")
    try:
        verifications = load_verification_set(verifications_path)
    except RecallInputError as exc:
        return PrecisionReport(available=False, reason=str(exc))
    if not extraction.get("available"):
        reason = extraction.get("reason", "extraction unavailable")
        return PrecisionReport(available=False, reason=f"cannot check coverage: {reason}")

    claims = _claims_of(extraction)
    coverage = verification_coverage(claims, verifications)
    if not coverage.corresponds:
        return PrecisionReport(
            available=False,
            reason=(
                f"verification file does not describe the current extractions: "
                f"{coverage.extractions} extractions, {coverage.verdicts} verdicts, "
                f"{coverage.unadjudicated} unadjudicated, {coverage.stale} stale, "
                f"{coverage.location_fallbacks} matched without a locator, "
                f"{coverage.label_fallbacks} matched without cell labels, "
                f"{coverage.label_degradations} matched with their labels dropped"
            ),
            coverage=coverage,
        )
    return PrecisionReport(
        available=True, result=compute_precision(verifications, claims), coverage=coverage
    )


def evaluate_pair(spec: PairSpec, *, src: Path | None = None) -> PairReport:
    """Recall and precision for one pair, each with its own availability.

    One extraction serves both metrics. It is skipped entirely for a pair
    carrying neither ground-truth file, so an unlabelled, unadjudicated roster
    row still costs a stat rather than a scan.
    """
    wanted = spec.labels.is_file() or (
        spec.verifications is not None and spec.verifications.is_file()
    )
    extraction: dict[str, Any] = (
        extract_claims(code=spec.code, paper=spec.paper, src=src)
        if wanted
        else {"available": False, "reason": "no label or verification file for this pair"}
    )
    recall = evaluate_recall(
        code=spec.code,
        paper=spec.paper,
        labels_path=spec.labels,
        src=src,
        extraction=extraction,
    )
    precision = evaluate_precision(spec.verifications, extraction)
    return PairReport(
        pair_id=spec.id,
        recall=recall,
        precision=precision,
        adduce_loaded_from=_optional_str(extraction.get("adduce_loaded_from")),
    )


# -- the roster -------------------------------------------------------------


def load_roster(
    csv_path: Path, *, pairs_root: Path, labels_dir: Path, verifications_dir: Path
) -> list[PairSpec]:
    """One :class:`PairSpec` per row of ``pairs.csv``, at fetch.py's own layout."""
    specs: list[PairSpec] = []
    with csv_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            pair_id = row["id"]
            pair_dir = pairs_root / pair_id
            specs.append(
                PairSpec(
                    id=pair_id,
                    code=pair_dir / _ROSTER_CODE_SUBPATH,
                    paper=pair_dir.joinpath(*_ROSTER_PAPER_SUBPATH),
                    labels=labels_dir / f"{pair_id}.json",
                    verifications=verifications_dir / f"{pair_id}.json",
                )
            )
    return specs


# -- reporting ----------------------------------------------------------------


def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "--no-pager", "-C", str(_REPOSITORY_ROOT), *args],
            capture_output=True,
            text=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _provenance() -> dict[str, Any]:
    status = _git("status", "--porcelain")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": _git("rev-parse", "HEAD"),
        "git_dirty": None if status is None else bool(status),
    }


def _measurement(src: Path | None, loaded_from: str | None) -> dict[str, Any]:
    """Which tree was measured, and which one the extractor actually imported.

    Stated rather than left for the reader to infer: a ``--src`` arm that
    resolved this repository's own ``src`` measures the tree under test, so
    every count it reports is the same one the unswapped run gives and a
    comparison built on it says nothing. ``adduce_is_this_repository`` is
    ``None`` when no extraction ran, because then no tree was loaded at all.
    """
    return {
        "src": str(src) if src is not None else None,
        "adduce_loaded_from": loaded_from,
        "adduce_is_this_repository": (
            None if loaded_from is None else loaded_from == str(_DEFAULT_SRC / "adduce")
        ),
    }


def _render_measurement(measurement: dict[str, Any]) -> list[str]:
    source = measurement["src"] or "this repository"
    lines = [f"src: {source} (adduce loaded from {measurement['adduce_loaded_from'] or '-'})"]
    if measurement["src"] is not None and measurement["adduce_is_this_repository"]:
        lines.append(
            "--src resolved to this repository's own src: this run measures this "
            "repository, not the tree it was given"
        )
    return lines


def _summarize(pair_reports: Sequence[PairReport]) -> dict[str, Any]:
    """Pooled (micro-averaged) counts over pairs that carry the input.

    Pooling, not a mean of per-pair ratios: a paper with two own results
    should not weigh the same as one with two hundred, and a pair lacking the
    relevant input contributes nothing -- never a zero -- to either pool.
    """
    matched_total = denominator_total = recall_available = 0
    for report in pair_reports:
        diagnostic = report.recall.diagnostic
        if not report.recall.available or diagnostic is None or diagnostic.recall is None:
            continue
        recall_available += 1
        matched_total += diagnostic.matched
        denominator_total += diagnostic.recall_denominator

    real_total = adjudicated_total = precision_available = 0
    for report in pair_reports:
        result = report.precision.result
        if not report.precision.available or result is None or result.precision is None:
            continue
        precision_available += 1
        real_total += result.real_own_result
        adjudicated_total += result.adjudicated

    return {
        "pairs": len(pair_reports),
        "recall": {
            "pairs_available": recall_available,
            "pairs_unavailable": len(pair_reports) - recall_available,
            "pooled": round(matched_total / denominator_total, 4) if denominator_total else None,
        },
        "precision": {
            "pairs_available": precision_available,
            "pairs_unavailable": len(pair_reports) - precision_available,
            "pooled": round(real_total / adjudicated_total, 4) if adjudicated_total else None,
        },
    }


def _pair_report_payload(report: PairReport) -> dict[str, Any]:
    return {
        "pair_id": report.pair_id,
        "adduce_loaded_from": report.adduce_loaded_from,
        "recall": asdict(report.recall),
        "precision": asdict(report.precision),
    }


def build_report(
    pair_reports: Sequence[PairReport], *, src: Path | None = None
) -> dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "provenance": _provenance(),
        "measurement": _measurement(
            src,
            next(
                (
                    report.adduce_loaded_from
                    for report in pair_reports
                    if report.adduce_loaded_from is not None
                ),
                None,
            ),
        ),
        "results": [_pair_report_payload(report) for report in pair_reports],
        "summary": _summarize(pair_reports),
    }


def _format_ratio(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _format_recall(recall: dict[str, Any]) -> str:
    if not recall["available"]:
        return f"unavailable: {recall['reason']}"
    diagnostic = recall["diagnostic"]
    if diagnostic is None or diagnostic["recall"] is None:
        return "undefined (denominator 0)"
    return f"{diagnostic['matched']}/{diagnostic['recall_denominator']} = {diagnostic['recall']:.1%}"


def _format_precision(precision: dict[str, Any]) -> str:
    if not precision["available"]:
        return f"unavailable: {precision['reason']}"
    result = precision["result"]
    if result is None or result["precision"] is None:
        return "undefined (adjudicated 0)"
    coverage = precision["coverage"] or {}
    fallbacks = coverage.get("location_fallbacks") or 0
    unlabelled = coverage.get("label_fallbacks") or 0
    degraded = coverage.get("label_degradations") or 0
    rendered = (
        f"{result['real_own_result']}/{result['adjudicated']} = "
        f"{result['precision']:.1%} (unclear={result['unclear']})"
    )
    # Stated rather than hidden: this many verdicts rest on the weaker key.
    if fallbacks:
        rendered = f"{rendered} [no locator: {fallbacks}]"
    if unlabelled:
        rendered = f"{rendered} [no labels: {unlabelled}]"
    if degraded:
        rendered = f"{rendered} [labels dropped: {degraded}]"
    return rendered


def render_text(report: dict[str, Any]) -> str:
    lines = [*_render_measurement(report["measurement"]), ""]
    lines.append(f"{'pair':30s} {'claims':>7s}  {'recall':<24s} {'precision':<36s}")
    for record in report["results"]:
        claims = record["recall"].get("claim_count")
        lines.append(
            f"{record['pair_id']:30s} "
            f"{'-' if claims is None else claims:>7} "
            f" {_format_recall(record['recall']):<24s} {_format_precision(record['precision']):<36s}"
        )
    summary = report["summary"]
    lines.append(
        f"\n{summary['pairs']} pair(s): "
        f"recall {summary['recall']['pairs_available']} available / "
        f"{summary['recall']['pairs_unavailable']} unavailable, pooled "
        f"{_format_ratio(summary['recall']['pooled'])}; "
        f"precision {summary['precision']['pairs_available']} available / "
        f"{summary['precision']['pairs_unavailable']} unavailable, pooled "
        f"{_format_ratio(summary['precision']['pooled'])}"
    )
    return "\n".join(lines)


# -- the inventory: every pair, labelled or not --------------------------------

#: Every count the inventory records, in the order it reports them.
_INVENTORY_COUNTS = ("table_cells", "claims", "numeric_claims")


@dataclass(frozen=True)
class PairInventory:
    """One pair's extraction reduced to counts, whether or not it carries labels.

    ``table_cells`` is how many table cells the LaTeX collector read, which is
    the cheapest signal that a paper stopped being read at all. ``claims`` is
    the drafted manifest's own claim count; ``numeric_claims`` is the subset
    carrying a value, which is what matching and adjudication actually see
    (:func:`_numeric_claims`) -- a README-fallback placeholder asserts no
    number and appears only in the first. Both are recorded because a change
    that moves one and not the other has moved what is measurable, not only
    what is drafted.

    Every count is ``None`` when it was not measured -- an absent clone or
    paper, a worker that failed, or a ``--src`` tree predating the field -- and
    never zero. A zero cell count is the defect this record exists to catch, so
    it must not also be how an unmeasured pair reads.
    """

    pair_id: str
    available: bool
    reason: str | None = None
    table_cells: int | None = None
    claims: int | None = None
    numeric_claims: int | None = None
    adduce_loaded_from: str | None = None

    @property
    def counts(self) -> dict[str, int | None]:
        return {
            "table_cells": self.table_cells,
            "claims": self.claims,
            "numeric_claims": self.numeric_claims,
        }


def _inventory_from_extraction(pair_id: str, extraction: dict[str, Any]) -> PairInventory:
    cells = extraction.get("table_cells")
    claims = _claims_of(extraction)
    return PairInventory(
        pair_id=pair_id,
        available=True,
        table_cells=cells if isinstance(cells, int) and not isinstance(cells, bool) else None,
        claims=len(claims),
        numeric_claims=len(_numeric_claims(claims)),
        adduce_loaded_from=_optional_str(extraction.get("adduce_loaded_from")),
    )


def inventory_pair(spec: PairSpec, *, src: Path | None = None) -> PairInventory:
    """One pair's counts, through the same subprocess ``measure`` extracts with.

    Deliberately not :func:`evaluate_pair`, which skips the extraction for a
    pair carrying neither ground-truth file: those pairs are exactly the ones
    no other instrument in this file can see, so skipping them here would
    rebuild the blind spot the inventory exists to remove. A pair whose clone
    or paper is missing is reported unavailable with the worker's own reason and
    contributes no count.
    """
    extraction = extract_claims(code=spec.code, paper=spec.paper, src=src)
    if not extraction.get("available"):
        return PairInventory(
            pair_id=spec.id,
            available=False,
            reason=str(extraction.get("reason", "extraction unavailable")),
        )
    return _inventory_from_extraction(spec.id, extraction)


def take_inventory(specs: Sequence[PairSpec], *, src: Path | None = None) -> list[PairInventory]:
    return [inventory_pair(spec, src=src) for spec in specs]


def _inventory_total(entries: Sequence[PairInventory], name: str) -> int | None:
    """The pooled count over the pairs that reported one, or ``None`` if none did.

    Pairs that were not measured contribute nothing, never a zero, exactly as
    :func:`_summarize` pools the two rates.
    """
    measured = [entry.counts[name] for entry in entries if entry.counts[name] is not None]
    return sum(count for count in measured if count is not None) if measured else None


def build_inventory(
    entries: Sequence[PairInventory], *, src: Path | None = None
) -> dict[str, Any]:
    """The inventory as a report: provenance, which tree it measured, and the counts.

    Written with ``--output`` rather than compared in one process against two
    ``--src`` arms, the way ``manifest_identity.py`` compares. Two reasons, both
    measured: extraction over the whole roster is minutes of subprocess work, so
    a two-arm run pays it twice for a "before" that was already computed; and
    the "before" an extractor change is gated against is usually the working
    tree as it stood, which is not a second source tree that can be pointed at.
    So this command produces the artifact and ``compare-inventory`` diffs two of
    them.
    """
    return {
        "schema": INVENTORY_SCHEMA,
        "provenance": _provenance(),
        "measurement": _measurement(
            src,
            next(
                (
                    entry.adduce_loaded_from
                    for entry in entries
                    if entry.adduce_loaded_from is not None
                ),
                None,
            ),
        ),
        "pairs": [asdict(entry) for entry in entries],
        "summary": {
            "pairs": len(entries),
            "available": sum(1 for entry in entries if entry.available),
            "unavailable": sum(1 for entry in entries if not entry.available),
            "totals": {name: _inventory_total(entries, name) for name in _INVENTORY_COUNTS},
        },
    }


def _inventory_count(entry: Any, path: Path, pair_id: str, name: str) -> int | None:
    if entry is None:
        return None
    if isinstance(entry, bool) or not isinstance(entry, int) or entry < 0:
        raise RecallInputError(f"{path}: {pair_id}.{name} must be a non-negative integer or null")
    return entry


def _parse_inventory_entry(entry: Any, path: Path) -> PairInventory:
    if not isinstance(entry, dict):
        raise RecallInputError(f"{path}: every pair must be an object")
    pair_id = entry.get("pair_id")
    if not isinstance(pair_id, str) or not pair_id:
        raise RecallInputError(f"{path}: a pair is missing its pair_id")
    available = entry.get("available")
    if not isinstance(available, bool):
        raise RecallInputError(f"{path}: {pair_id}.available must be a boolean")
    return PairInventory(
        pair_id=pair_id,
        available=available,
        reason=_optional_str(entry.get("reason")),
        adduce_loaded_from=_optional_str(entry.get("adduce_loaded_from")),
        **{
            name: _inventory_count(entry.get(name), path, pair_id, name)
            for name in _INVENTORY_COUNTS
        },
    )


@dataclass(frozen=True)
class InventoryRun:
    """One inventory report, loaded."""

    provenance: dict[str, Any]
    measurement: dict[str, Any]
    pairs: tuple[PairInventory, ...]


def load_inventory(path: Path) -> InventoryRun:
    """Parse one inventory report, raising :class:`RecallInputError` on any defect.

    Validated, not trusted: a comparison is only a measurement if both sides
    really are inventories of this shape, and a truncated or hand-edited file
    would otherwise read as a run in which every pair lost every count.
    """
    raw = _read_json(path, label="inventory report")
    if not isinstance(raw, dict):
        raise RecallInputError(f"inventory report at {path} must contain a JSON object")
    schema = raw.get("schema")
    if schema != INVENTORY_SCHEMA:
        raise RecallInputError(f"{path}: schema must be {INVENTORY_SCHEMA!r}, not {schema!r}")
    entries = raw.get("pairs")
    if not isinstance(entries, list):
        raise RecallInputError(f"{path}: pairs must be a list")
    provenance = raw.get("provenance")
    measurement = raw.get("measurement")
    return InventoryRun(
        provenance=provenance if isinstance(provenance, dict) else {},
        measurement=measurement if isinstance(measurement, dict) else {},
        pairs=tuple(_parse_inventory_entry(entry, path) for entry in entries),
    )


@dataclass(frozen=True)
class InventoryComparison:
    """One pair's counts under two runs, and which of them moved.

    ``moved`` names each count that differs and carries both values, so a
    deleted paper is reported as ``table_cells 624 -> 0`` rather than as a
    boolean. A count one run measured and the other did not moves to or from
    ``None``: unmeasured is reported as unmeasured and never read as zero.
    Availability is compared too, so a pair that stopped being extractable at
    all cannot pass for unchanged.
    """

    pair_id: str
    before: PairInventory
    after: PairInventory

    @property
    def availability_moved(self) -> bool:
        return self.before.available != self.after.available

    @property
    def moved(self) -> tuple[tuple[str, int | None, int | None], ...]:
        before, after = self.before.counts, self.after.counts
        return tuple(
            (name, before[name], after[name])
            for name in _INVENTORY_COUNTS
            if before[name] != after[name]
        )

    @property
    def unchanged(self) -> bool:
        return not self.availability_moved and not self.moved

    def summary(self) -> str:
        if self.availability_moved:
            if self.after.available:
                return f"unavailable -> available: {self.before.reason or 'no reason recorded'}"
            return f"available -> unavailable: {self.after.reason or 'no reason recorded'}"
        if not self.before.available:
            return f"unavailable in both runs: {self.before.reason or 'no reason recorded'}"
        if not self.moved:
            return "unchanged"
        return ", ".join(
            f"{name} {_format_count(before)} -> {_format_count(after)}"
            for name, before, after in self.moved
        )


def _format_count(count: int | None) -> str:
    return "not measured" if count is None else str(count)


def compare_inventories(
    before: Sequence[PairInventory], after: Sequence[PairInventory]
) -> list[InventoryComparison]:
    """The two runs paired by id, over the union of the ids either one records.

    A pair only one run records is compared against an explicit "not in this
    run" rather than dropped: the roster it was taken against may have changed,
    and a gate that silently stops covering a pair is the failure this whole
    section is here to prevent.
    """
    left = {entry.pair_id: entry for entry in before}
    right = {entry.pair_id: entry for entry in after}

    def side(entries: dict[str, PairInventory], pair_id: str, arm: str) -> PairInventory:
        return entries.get(
            pair_id,
            PairInventory(pair_id=pair_id, available=False, reason=f"not in the {arm} run"),
        )

    return [
        InventoryComparison(
            pair_id=pair_id,
            before=side(left, pair_id, "before"),
            after=side(right, pair_id, "after"),
        )
        for pair_id in sorted(set(left) | set(right))
    ]


def build_inventory_comparison(
    before: InventoryRun, after: InventoryRun, *, before_path: Path, after_path: Path
) -> dict[str, Any]:
    comparisons = compare_inventories(before.pairs, after.pairs)
    return {
        "schema": INVENTORY_COMPARISON_SCHEMA,
        "arms": {
            "before": str(before_path),
            "after": str(after_path),
            "before_provenance": before.provenance,
            "after_provenance": after.provenance,
            "before_measurement": before.measurement,
            "after_measurement": after.measurement,
            # Stated, not left to the reader: one report compared with itself,
            # or with a copy of itself, reports nothing moved however many pairs
            # it covers.
            "arms_share_provenance": bool(before.provenance)
            and before.provenance == after.provenance,
        },
        "results": [
            {
                "pair_id": comparison.pair_id,
                "unchanged": comparison.unchanged,
                "moved": [
                    {"count": name, "before": before_count, "after": after_count}
                    for name, before_count, after_count in comparison.moved
                ],
                "availability_moved": comparison.availability_moved,
                "summary": comparison.summary(),
            }
            for comparison in comparisons
        ],
        "summary": {
            "pairs": len(comparisons),
            "unchanged": sum(1 for comparison in comparisons if comparison.unchanged),
            "moved": sum(1 for comparison in comparisons if not comparison.unchanged),
            "unavailable": sum(
                1
                for comparison in comparisons
                if not (comparison.before.available and comparison.after.available)
            ),
        },
    }


def render_inventory_text(report: dict[str, Any]) -> str:
    lines = [*_render_measurement(report["measurement"]), ""]
    lines.append(f"{'pair':30s} {'cells':>8s} {'claims':>8s} {'numeric':>8s}")
    for entry in report["pairs"]:
        if not entry["available"]:
            lines.append(f"{entry['pair_id']:30s} unavailable: {entry['reason']}")
            continue
        lines.append(
            f"{entry['pair_id']:30s} "
            f"{_format_count(entry['table_cells']):>8s} "
            f"{_format_count(entry['claims']):>8s} "
            f"{_format_count(entry['numeric_claims']):>8s}"
        )
    summary = report["summary"]
    totals = summary["totals"]
    lines.append(
        f"\n{summary['pairs']} pair(s): {summary['available']} available, "
        f"{summary['unavailable']} unavailable; totals "
        f"{_format_count(totals['table_cells'])} cells, "
        f"{_format_count(totals['claims'])} claims, "
        f"{_format_count(totals['numeric_claims'])} numeric"
    )
    return "\n".join(lines)


def render_inventory_comparison(report: dict[str, Any]) -> str:
    arms = report["arms"]
    lines = [f"before: {arms['before']}", f"after:  {arms['after']}"]
    if arms["arms_share_provenance"]:
        lines.append("both arms report the same provenance: this comparison may be vacuous")
    lines.append("")
    for record in report["results"]:
        if record["unchanged"]:
            continue
        lines.append(f"{record['pair_id']:30s} {record['summary']}")
    summary = report["summary"]
    lines.append(
        f"\n{summary['pairs']} pair(s): {summary['unchanged']} unchanged, "
        f"{summary['moved']} moved, {summary['unavailable']} unavailable in either run"
    )
    return "\n".join(lines)


# -- CLI ------------------------------------------------------------------

_SRC_HELP = (
    "resolve the extractor's adduce from this tree instead of <repo>/src; a tree with no "
    "adduce package is refused, and matching always uses the current tree's adduce.naming"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract_parser = subparsers.add_parser(
        "_extract", help="internal worker: run the measurement path for one pair"
    )
    extract_parser.add_argument("--code", type=Path, required=True)
    extract_parser.add_argument("--paper", type=Path, required=True)
    extract_parser.add_argument("--src", type=Path, help=_SRC_HELP)

    measure_parser = subparsers.add_parser(
        "measure", help="recall and precision for the dev-set roster (or --only one pair)"
    )
    measure_parser.add_argument("--pairs-csv", type=Path, default=_BENCH_DEV_ROOT / "pairs.csv")
    measure_parser.add_argument("--pairs-root", type=Path, default=_BENCH_DEV_ROOT / "pairs")
    measure_parser.add_argument("--labels-dir", type=Path, default=_BENCH_DEV_ROOT / "labels")
    measure_parser.add_argument(
        "--verifications-dir", type=Path, default=_BENCH_DEV_ROOT / "verifications"
    )
    measure_parser.add_argument("--only", help="restrict the roster to a single pair id")
    measure_parser.add_argument("--src", type=Path, help=_SRC_HELP)
    measure_parser.add_argument("--output", type=Path, help="also write the JSON report here")

    inventory_parser = subparsers.add_parser(
        "inventory",
        help="count table cells and drafted claims for every pair, labelled or not",
    )
    inventory_parser.add_argument("--pairs-csv", type=Path, default=_BENCH_DEV_ROOT / "pairs.csv")
    inventory_parser.add_argument("--pairs-root", type=Path, default=_BENCH_DEV_ROOT / "pairs")
    inventory_parser.add_argument("--only", help="restrict the roster to a single pair id")
    inventory_parser.add_argument("--src", type=Path, help=_SRC_HELP)
    inventory_parser.add_argument("--output", type=Path, help="also write the JSON report here")

    compare_parser = subparsers.add_parser(
        "compare-inventory", help="name every pair whose counts moved between two inventories"
    )
    compare_parser.add_argument("--before", type=Path, required=True)
    compare_parser.add_argument("--after", type=Path, required=True)
    compare_parser.add_argument(
        "--json", action="store_true", help="print the JSON report instead of the movers"
    )

    arguments = parser.parse_args(argv)

    if arguments.command == "_extract":
        return _cmd_extract(arguments)

    if arguments.command == "compare-inventory":
        try:
            before = load_inventory(arguments.before)
            after = load_inventory(arguments.after)
        except RecallInputError as exc:
            compare_parser.error(str(exc))
        comparison = build_inventory_comparison(
            before, after, before_path=arguments.before, after_path=arguments.after
        )
        print(
            json.dumps(comparison, indent=2)
            if arguments.json
            else render_inventory_comparison(comparison)
        )
        # A mover is a change to review, not a defect on its own; nothing to
        # review is the only exit-0 condition, so this is usable as a gate.
        summary = comparison["summary"]
        return 1 if summary["moved"] or summary["unavailable"] else 0

    if refusal := src_refusal(arguments.src):
        parser.error(refusal)

    inventory = arguments.command == "inventory"
    specs = load_roster(
        arguments.pairs_csv,
        pairs_root=arguments.pairs_root,
        # The inventory reads no ground truth -- that is the point of it -- but
        # the roster is one type, so it is built the same way for both.
        labels_dir=_BENCH_DEV_ROOT / "labels" if inventory else arguments.labels_dir,
        verifications_dir=(
            _BENCH_DEV_ROOT / "verifications" if inventory else arguments.verifications_dir
        ),
    )
    if arguments.only is not None:
        specs = [spec for spec in specs if spec.id == arguments.only]
        if not specs:
            parser.error(f"no pair with id {arguments.only!r} in {arguments.pairs_csv}")

    if inventory:
        report = build_inventory(take_inventory(specs, src=arguments.src), src=arguments.src)
        rendered_text = render_inventory_text(report)
        label = "dev-set inventory report"
    else:
        report = build_report(
            [evaluate_pair(spec, src=arguments.src) for spec in specs], src=arguments.src
        )
        rendered_text = render_text(report)
        label = "dev-set recall report"
    print(rendered_text)
    if arguments.output is not None:
        from adduce.safe_write import replace_text_regular

        rendered = json.dumps(report, indent=2, sort_keys=False, allow_nan=False) + "\n"
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        replace_text_regular(
            arguments.output,
            rendered,
            label=label,
            parent_label=f"{label} directory",
        )
        print(f"written to {arguments.output}", file=sys.stderr)
    if inventory:
        # An unmeasured pair is a hole in the gate, exactly as it is in
        # manifest_identity.py's compare.
        return 1 if report["summary"]["unavailable"] else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
