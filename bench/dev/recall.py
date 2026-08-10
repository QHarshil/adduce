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
so would fix the first import to whichever tree happened to load first.

Matching an extraction to a label requires both the value (rounding-aware,
via ``adduce.rules.drift.values_match``) and the metric (canonicalised via
``adduce.naming.canonical_metric``) to agree, and is one-to-one: a label is
satisfied by at most one extraction and an extraction satisfies at most one
label. Value-only agreement is reported separately, as a diagnostic, never
counted as a match -- matching on value alone reaches the right answer for
the wrong reason whenever every candidate happens to name the right metric.

Nothing here fabricates a number. A pair whose code tree, paper tree, label
file, or verification file is absent is reported ``unavailable`` for the
metric that input feeds, with the reason, and never contributes a zero to an
aggregate.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeGuard

_BENCH_DEV_ROOT = Path(__file__).resolve().parent
_REPOSITORY_ROOT = _BENCH_DEV_ROOT.parents[1]
if str(_REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT / "src"))

REPORT_SCHEMA = "adduce-bench-dev-recall/1"
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
    """One ``manifest.Claim``, reduced to what matching needs."""

    metric: str | None
    value: float | None
    where: str | None
    text: str


def _extracted_claim_from_json(entry: dict[str, Any]) -> ExtractedClaim:
    raw_value = entry.get("value")
    return ExtractedClaim(
        metric=_optional_str(entry.get("metric")),
        value=float(raw_value) if _is_number(raw_value) else None,
        where=_optional_str(entry.get("where")),
        text=_optional_str(entry.get("text")) or "",
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
    here, rather than guarded at every comparison site.
    """

    metric: str | None
    value: float
    where: str | None
    text: str


def _numeric_claims(claims: Sequence[ExtractedClaim]) -> list[_NumericClaim]:
    result: list[_NumericClaim] = []
    for claim in claims:
        if claim.value is None:
            continue
        result.append(_NumericClaim(metric=claim.metric, value=claim.value, where=claim.where, text=claim.text))
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
    """A tally over one pair's verification file. Independent of any label."""

    real_own_result: int
    baseline: int
    hyperparameter: int
    not_in_paper: int
    in_repo_not_paper: int
    unclear: int
    adjudicated: int  # every verdict except those excluded from the denominator
    precision: float | None


def compute_precision(verifications: VerificationSet) -> PrecisionResult:
    """Precision from adjudications alone: real_own_result / adjudicated.

    ``unclear`` and ``in_repo_not_paper`` are excluded from the denominator and
    reported alongside, never silently dropped -- the first because it was not
    decided, the second because it is a claim the repository genuinely makes and
    a paper-scoped adjudication cannot credit or condemn it. ``precision`` is
    ``None`` when nothing was adjudicated -- undefined, never a fabricated zero.
    """
    tally = dict.fromkeys(_VALID_VERDICTS, 0)
    for verification in verifications.verifications:
        tally[verification.verdict] += 1
    adjudicated = sum(
        count for verdict, count in tally.items() if verdict not in _EXCLUDED_FROM_PRECISION
    )
    precision = tally["real_own_result"] / adjudicated if adjudicated else None
    return PrecisionResult(
        real_own_result=tally["real_own_result"],
        baseline=tally["baseline"],
        hyperparameter=tally["hyperparameter"],
        not_in_paper=tally["not_in_paper"],
        in_repo_not_paper=tally["in_repo_not_paper"],
        unclear=tally["unclear"],
        adjudicated=adjudicated,
        precision=precision,
    )


# -- the extraction worker: --src runs here, and only here --------------------


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
    json.dump(
        {
            "available": True,
            "adduce_loaded_from": loaded_from,
            "claims": [
                {"metric": c.metric, "value": c.value, "where": c.where, "text": c.text}
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


@dataclass(frozen=True)
class PairReport:
    pair_id: str
    recall: RecallReport
    precision: PrecisionReport


@dataclass(frozen=True)
class PairSpec:
    id: str
    code: Path
    paper: Path
    labels: Path
    verifications: Path | None = None


def evaluate_recall(
    *, code: Path, paper: Path, labels_path: Path, src: Path | None = None
) -> RecallReport:
    """Recall for one pair. Refuses eagerly, cheapest check first.

    A missing code or paper directory, or a missing or malformed label file,
    is reported unavailable before any extraction runs; a failed extraction
    is reported unavailable with the worker's own reason.
    """
    if not code.is_dir():
        return RecallReport(available=False, reason=f"code directory not found: {code}")
    if not paper.is_dir():
        return RecallReport(available=False, reason=f"paper directory not found: {paper}")
    try:
        frame = load_label_frame(labels_path)
    except RecallInputError as exc:
        return RecallReport(available=False, reason=str(exc))

    extraction = _run_extract_worker(code=code, paper=paper, src=src)
    if not extraction.get("available"):
        reason = extraction.get("reason", "extraction unavailable")
        return RecallReport(available=False, reason=str(reason))

    claims = [_extracted_claim_from_json(entry) for entry in extraction.get("claims", [])]
    diagnostic = classify_recall(claims, frame)
    return RecallReport(available=True, claim_count=len(claims), diagnostic=diagnostic)


def evaluate_precision(verifications_path: Path | None) -> PrecisionReport:
    """Precision for one pair. Independent of the code/paper trees and of recall."""
    if verifications_path is None:
        return PrecisionReport(available=False, reason="no verification file configured for this pair")
    try:
        verifications = load_verification_set(verifications_path)
    except RecallInputError as exc:
        return PrecisionReport(available=False, reason=str(exc))
    return PrecisionReport(available=True, result=compute_precision(verifications))


def evaluate_pair(spec: PairSpec, *, src: Path | None = None) -> PairReport:
    """Recall and precision for one pair, each with its own availability."""
    recall = evaluate_recall(code=spec.code, paper=spec.paper, labels_path=spec.labels, src=src)
    precision = evaluate_precision(spec.verifications)
    return PairReport(pair_id=spec.id, recall=recall, precision=precision)


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
        "recall": asdict(report.recall),
        "precision": asdict(report.precision),
    }


def build_report(pair_reports: Sequence[PairReport]) -> dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "provenance": _provenance(),
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
    return (
        f"{result['real_own_result']}/{result['adjudicated']} = "
        f"{result['precision']:.1%} (unclear={result['unclear']})"
    )


def render_text(report: dict[str, Any]) -> str:
    lines = [f"{'pair':30s} {'claims':>7s}  {'recall':<24s} {'precision':<36s}"]
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


# -- CLI ------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract_parser = subparsers.add_parser(
        "_extract", help="internal worker: run the measurement path for one pair"
    )
    extract_parser.add_argument("--code", type=Path, required=True)
    extract_parser.add_argument("--paper", type=Path, required=True)
    extract_parser.add_argument(
        "--src", type=Path, help="resolve adduce from this tree instead of <repo>/src"
    )

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
    measure_parser.add_argument(
        "--src", type=Path, help="resolve adduce from this tree instead of <repo>/src"
    )
    measure_parser.add_argument("--output", type=Path, help="also write the JSON report here")

    arguments = parser.parse_args(argv)

    if arguments.command == "_extract":
        return _cmd_extract(arguments)

    specs = load_roster(
        arguments.pairs_csv,
        pairs_root=arguments.pairs_root,
        labels_dir=arguments.labels_dir,
        verifications_dir=arguments.verifications_dir,
    )
    if arguments.only is not None:
        specs = [spec for spec in specs if spec.id == arguments.only]
        if not specs:
            measure_parser.error(f"no pair with id {arguments.only!r} in {arguments.pairs_csv}")

    reports = [evaluate_pair(spec, src=arguments.src) for spec in specs]
    report = build_report(reports)
    print(render_text(report))
    if arguments.output is not None:
        from adduce.safe_write import replace_text_regular

        rendered = json.dumps(report, indent=2, sort_keys=False, allow_nan=False) + "\n"
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        replace_text_regular(
            arguments.output,
            rendered,
            label="dev-set recall report",
            parent_label="dev-set recall report directory",
        )
        print(f"written to {arguments.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
