#!/usr/bin/env python3
"""Freeze deterministic calibration and second-review finding allowlists."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path
from typing import Any

if __package__:
    from .label_findings import (
        EFFECTIVENESS_COHORTS,
        JUDGEMENT_FIELDS,
        STRESS_COHORTS,
        load,
        validate,
        validate_against_run,
    )
    from .run_contract import (
        RUN_META_NAME,
        RunContractError,
        ensure_output_outside,
        require_current_harness_file,
        sha256_file,
        validate_run_evidence,
        write_json,
    )
else:
    from label_findings import (
        EFFECTIVENESS_COHORTS,
        JUDGEMENT_FIELDS,
        STRESS_COHORTS,
        load,
        validate,
        validate_against_run,
    )
    from run_contract import (
        RUN_META_NAME,
        RunContractError,
        ensure_output_outside,
        require_current_harness_file,
        sha256_file,
        validate_run_evidence,
        write_json,
    )

REVIEW_ALLOCATION_SCHEMA_VERSION = 1
ALGORITHM = "sha256-balanced-repository-status-v1"
CALIBRATION_COUNT = 40
SECOND_REVIEW_NUMERATOR = 1
SECOND_REVIEW_DENOMINATOR = 5
DECISION_GROUPS = ("emitted", "pass", "abstention")
SELECTION_KEYS = {
    "calibration": "calibration",
    "second-review": "second_review",
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ReviewAllocationError(ValueError):
    """A review allocation is incomplete, inconsistent, or no longer bound."""


def _canonical_sha256(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ReviewAllocationError(f"cannot canonicalize allocation evidence: {exc}") from exc
    return hashlib.sha256(encoded).hexdigest()


def _identity_projection(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return source records without mutable human-review fields."""
    projected: list[dict[str, Any]] = []
    for entry in entries:
        projected.append(
            {key: value for key, value in entry.items() if key not in {"reviews", "adjudication"}}
        )
    return projected


def _source_identity_sha256(entries: list[dict[str, Any]]) -> str:
    return _canonical_sha256(_identity_projection(entries))


def _status_group(status: object) -> str:
    if status in {"fail", "partial"}:
        return "emitted"
    if status == "pass":
        return "pass"
    if status in {"unknown", "not-applicable"}:
        return "abstention"
    raise ReviewAllocationError(f"unsupported finding status in review population: {status!r}")


def _rank(seed: int, purpose: str, *parts: str) -> str:
    material = "\0".join((ALGORITHM, str(seed), purpose, *parts)).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _balanced_quotas(
    capacities: dict[str, int], total: int, *, seed: int, purpose: str
) -> dict[str, int]:
    """Allocate seats as evenly as capacities allow, with hash-stable ties."""
    if total < 0 or total > sum(capacities.values()):
        raise ReviewAllocationError(f"cannot allocate {total} records for {purpose}")
    quotas = dict.fromkeys(capacities, 0)
    while sum(quotas.values()) < total:
        candidates = [key for key, capacity in capacities.items() if quotas[key] < capacity]
        if not candidates:  # pragma: no cover - guarded by the capacity check
            raise ReviewAllocationError(f"allocation capacity exhausted for {purpose}")
        minimum = min(quotas[key] for key in candidates)
        tied = [key for key in candidates if quotas[key] == minimum]
        selected = min(tied, key=lambda key: (_rank(seed, purpose, key), key))
        quotas[selected] += 1
    return quotas


def _calibration_records(
    records: list[dict[str, Any]], count: int, seed: int
) -> list[dict[str, Any]]:
    repositories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        repositories[str(record["repo_id"])].append(record)
    if count < len(repositories):
        raise ReviewAllocationError(
            "calibration count is too small to cover every Layer B repository"
        )
    repo_quotas = _balanced_quotas(
        {repo_id: len(values) for repo_id, values in repositories.items()},
        count,
        seed=seed,
        purpose="calibration-repository",
    )

    selected: list[dict[str, Any]] = []
    for repo_id in sorted(repositories):
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in repositories[repo_id]:
            grouped[str(record["decision_group"])].append(record)
        group_quotas = _balanced_quotas(
            {group: len(values) for group, values in grouped.items()},
            repo_quotas[repo_id],
            seed=seed,
            purpose=f"calibration-status:{repo_id}",
        )
        for group, values in sorted(grouped.items()):
            ranked = sorted(
                values,
                key=lambda value: (
                    _rank(seed, "calibration-finding", str(value["finding_fingerprint"])),
                    str(value["finding_fingerprint"]),
                ),
            )
            selected.extend(ranked[: group_quotas[group]])
    if len(selected) != count:  # pragma: no cover - allocation invariant
        raise ReviewAllocationError("calibration allocation did not reach its declared count")
    return selected


def _second_review_records(
    records: list[dict[str, Any]],
    calibration: list[dict[str, Any]],
    count: int,
    seed: int,
) -> list[dict[str, Any]]:
    by_stratum: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_stratum[(str(record["repo_id"]), str(record["decision_group"]))].append(record)
    calibration_fingerprints = {str(record["finding_fingerprint"]) for record in calibration}
    quotas = {
        stratum: sum(
            str(record["finding_fingerprint"]) in calibration_fingerprints for record in values
        )
        for stratum, values in by_stratum.items()
    }
    if count < sum(quotas.values()):
        raise ReviewAllocationError("second-review quota cannot exclude calibration records")
    population_count = len(records)
    ideals = {
        stratum: Fraction(count * len(values), population_count)
        for stratum, values in by_stratum.items()
    }
    while sum(quotas.values()) < count:
        candidates = [
            stratum for stratum, values in by_stratum.items() if quotas[stratum] < len(values)
        ]
        if not candidates:  # pragma: no cover - guarded by caller
            raise ReviewAllocationError("second-review allocation exhausted its population")
        largest_deficit = max(ideals[stratum] - quotas[stratum] for stratum in candidates)
        tied = [
            stratum
            for stratum in candidates
            if ideals[stratum] - quotas[stratum] == largest_deficit
        ]
        selected_stratum = min(
            tied,
            key=lambda value: (
                _rank(seed, "second-review-stratum", value[0], value[1]),
                value,
            ),
        )
        quotas[selected_stratum] += 1

    selected = list(calibration)
    for stratum, values in sorted(by_stratum.items()):
        already = [
            record
            for record in values
            if str(record["finding_fingerprint"]) in calibration_fingerprints
        ]
        needed = quotas[stratum] - len(already)
        remaining = [
            record
            for record in values
            if str(record["finding_fingerprint"]) not in calibration_fingerprints
        ]
        remaining.sort(
            key=lambda value: (
                _rank(seed, "second-review-finding", str(value["finding_fingerprint"])),
                str(value["finding_fingerprint"]),
            )
        )
        selected.extend(remaining[:needed])
    if len(selected) != count:  # pragma: no cover - allocation invariant
        raise ReviewAllocationError("second-review allocation did not reach its quota")
    return selected


def _reference(record: dict[str, Any], seed: int, purpose: str) -> dict[str, Any]:
    return {
        "source_id": record["source_id"],
        "source_entry_number": record["source_entry_number"],
        "finding_fingerprint": record["finding_fingerprint"],
        "repo_id": record["repo_id"],
        "cohort": record["cohort"],
        "rule_id": record["rule_id"],
        "category": record["category"],
        "finding_status": record["finding_status"],
        "decision_group": record["decision_group"],
        "rank_sha256": _rank(seed, purpose, str(record["finding_fingerprint"])),
    }


def _selection_sort_key(reference: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(reference["repo_id"]),
        str(reference["decision_group"]),
        str(reference["rank_sha256"]),
        str(reference["finding_fingerprint"]),
    )


def build_manifest(
    sources: list[tuple[str, str, list[dict[str, Any]]]],
    run_binding: dict[str, Any],
    *,
    seed: int = 0,
    calibration_count: int = CALIBRATION_COUNT,
    initial_source_sha256: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a deterministic allocation from validated review sources."""
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ReviewAllocationError("allocation seed must be an integer")
    if (
        isinstance(calibration_count, bool)
        or not isinstance(calibration_count, int)
        or calibration_count <= 0
    ):
        raise ReviewAllocationError("calibration count must be a positive integer")
    if not sources:
        raise ReviewAllocationError("at least one review source is required")

    source_records: list[dict[str, Any]] = []
    population: list[dict[str, Any]] = []
    seen_source_ids: set[str] = set()
    seen_fingerprints: set[str] = set()
    for source_id, observed_sha256, entries in sorted(sources, key=lambda value: value[0]):
        if not source_id or source_id in seen_source_ids:
            raise ReviewAllocationError("review source basenames must be non-empty and unique")
        seen_source_ids.add(source_id)
        validate(entries)
        sample_set = entries[0].get("sample_set")
        if not isinstance(sample_set, dict):  # pragma: no cover - validate rejects this
            raise ReviewAllocationError(f"source {source_id} lacks a sample-set binding")
        layer_b_count = 0
        stress_count = 0
        for entry_number, entry in enumerate(entries, 1):
            cohort = str(entry["cohort"])
            if cohort in STRESS_COHORTS:
                stress_count += 1
                continue
            if cohort not in EFFECTIVENESS_COHORTS:
                raise ReviewAllocationError(f"source {source_id} has unsupported cohort {cohort!r}")
            fingerprint = str(entry["finding_fingerprint"])
            if fingerprint in seen_fingerprints:
                raise ReviewAllocationError(
                    f"finding fingerprint appears in more than one review source: {fingerprint}"
                )
            seen_fingerprints.add(fingerprint)
            layer_b_count += 1
            population.append(
                {
                    "source_id": source_id,
                    "source_entry_number": entry_number,
                    "finding_fingerprint": fingerprint,
                    "repo_id": str(entry["repo_id"]),
                    "cohort": cohort,
                    "rule_id": str(entry["rule_id"]),
                    "category": str(entry.get("category") or "?"),
                    "finding_status": str(entry["finding_status"]),
                    "decision_group": _status_group(entry["finding_status"]),
                }
            )
        recorded_sha256 = (
            initial_source_sha256[source_id]
            if initial_source_sha256 is not None and source_id in initial_source_sha256
            else observed_sha256
        )
        if not _SHA256_RE.fullmatch(recorded_sha256):
            raise ReviewAllocationError(f"source {source_id} has an invalid SHA-256")
        source_records.append(
            {
                "source_id": source_id,
                "initial_source_sha256": recorded_sha256,
                "source_identity_sha256": _source_identity_sha256(entries),
                "sample_set_sha256": _canonical_sha256(sample_set),
                "entry_count": len(entries),
                "layer_b_entry_count": layer_b_count,
                "excluded_stress_entry_count": stress_count,
            }
        )

    if calibration_count > len(population):
        raise ReviewAllocationError(
            f"calibration count {calibration_count} exceeds Layer B population {len(population)}"
        )
    present_groups = {str(record["decision_group"]) for record in population}
    missing_groups = set(DECISION_GROUPS) - present_groups
    if missing_groups:
        raise ReviewAllocationError(
            f"Layer B review population lacks decision group(s): {sorted(missing_groups)}"
        )
    calibration = _calibration_records(population, calibration_count, seed)
    second_review_count = max(
        calibration_count,
        (len(population) * SECOND_REVIEW_NUMERATOR + SECOND_REVIEW_DENOMINATOR - 1)
        // SECOND_REVIEW_DENOMINATOR,
    )
    second_review = _second_review_records(population, calibration, second_review_count, seed)
    calibration_refs = sorted(
        (_reference(record, seed, "calibration-finding") for record in calibration),
        key=_selection_sort_key,
    )
    second_refs = sorted(
        (_reference(record, seed, "second-review-finding") for record in second_review),
        key=_selection_sort_key,
    )

    calibration_set = {str(record["finding_fingerprint"]) for record in calibration_refs}
    second_set = {str(record["finding_fingerprint"]) for record in second_refs}
    if not calibration_set <= second_set:  # pragma: no cover - allocation invariant
        raise ReviewAllocationError("calibration allowlist is not contained in second review")

    stratum_population = Counter(
        (str(record["repo_id"]), str(record["decision_group"])) for record in population
    )
    stratum_calibration = Counter(
        (str(record["repo_id"]), str(record["decision_group"])) for record in calibration
    )
    stratum_second = Counter(
        (str(record["repo_id"]), str(record["decision_group"])) for record in second_review
    )
    strata = [
        {
            "repo_id": repo_id,
            "decision_group": group,
            "population_count": count,
            "calibration_count": stratum_calibration[(repo_id, group)],
            "second_review_count": stratum_second[(repo_id, group)],
        }
        for (repo_id, group), count in sorted(stratum_population.items())
    ]
    population_fingerprints = sorted(str(record["finding_fingerprint"]) for record in population)
    return {
        "review_allocation_schema_version": REVIEW_ALLOCATION_SCHEMA_VERSION,
        "algorithm": ALGORITHM,
        "selector_sha256": sha256_file(Path(__file__)),
        "schema_sha256": sha256_file(
            Path(__file__).resolve().parent.parent / "review-allocation.schema.json"
        ),
        "seed": seed,
        "run_binding": run_binding,
        "sources": source_records,
        "population": {
            "cohorts": sorted(EFFECTIVENESS_COHORTS),
            "entry_count": len(population),
            "repository_count": len({str(record["repo_id"]) for record in population}),
            "finding_fingerprint_set_sha256": _canonical_sha256(population_fingerprints),
            "strata": strata,
        },
        "calibration_count": calibration_count,
        "second_review_rate": {
            "numerator": SECOND_REVIEW_NUMERATOR,
            "denominator": SECOND_REVIEW_DENOMINATOR,
        },
        "second_review_count": second_review_count,
        "calibration": calibration_refs,
        "second_review": second_refs,
    }


def _load_bound_sources(
    paths: list[Path], run: Path, *, require_unreviewed: bool
) -> tuple[list[tuple[str, str, list[dict[str, Any]]]], dict[str, Any]]:
    try:
        metadata, _, _ = validate_run_evidence(run)
        require_current_harness_file(metadata, "scripts/review_allocation.py", Path(__file__))
    except RunContractError as exc:
        raise ReviewAllocationError(f"invalid corpus run: {exc}") from exc
    truth_digest = metadata.get("claim_ground_truth_sha256")
    if not isinstance(truth_digest, str) or not _SHA256_RE.fullmatch(truth_digest):
        raise ReviewAllocationError(
            "Layer B review allocation requires a run bound to frozen claim ground truth"
        )
    sources: list[tuple[str, str, list[dict[str, Any]]]] = []
    try:
        resolved_run = run.resolve(strict=True)
    except OSError as exc:  # pragma: no cover - run validation resolves it first
        raise ReviewAllocationError(f"cannot resolve corpus run {run}: {exc}") from exc
    for path in paths:
        if not path.is_file():
            raise ReviewAllocationError(f"missing review source: {path}")
        try:
            resolved_path = path.resolve(strict=True)
        except OSError as exc:
            raise ReviewAllocationError(f"cannot resolve review source {path}: {exc}") from exc
        if resolved_path == resolved_run or resolved_run in resolved_path.parents:
            raise ReviewAllocationError(
                f"review source must remain outside the immutable corpus run: {path}"
            )
        try:
            entries = load(path)
            validate(entries)
            validate_against_run(entries, run)
        except (OSError, ValueError) as exc:
            raise ReviewAllocationError(f"invalid review source {path}: {exc}") from exc
        if require_unreviewed and any(
            entry.get("reviews") or entry.get("adjudication") is not None for entry in entries
        ):
            raise ReviewAllocationError(
                f"review allocation must be frozen before annotation begins: {path}"
            )
        sources.append((path.name, sha256_file(path), entries))
    run_binding = {
        "run_schema_version": metadata["run_schema_version"],
        "run_id": metadata["run_id"],
        "adduce_version": metadata["adduce_version"],
        "run_meta_sha256": sha256_file(run / RUN_META_NAME),
        "corpus_harness_sha256": metadata["corpus_harness_sha256"],
        "claim_ground_truth_sha256": truth_digest,
    }
    return sources, run_binding


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewAllocationError(f"cannot read review allocation {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReviewAllocationError("review allocation must be a JSON object")
    return value


def _manifest_initial_hashes(manifest: dict[str, Any]) -> dict[str, str]:
    sources = manifest.get("sources")
    if not isinstance(sources, list):
        raise ReviewAllocationError("review allocation has invalid sources")
    initial: dict[str, str] = {}
    for source in sources:
        if not isinstance(source, dict):
            raise ReviewAllocationError("review allocation has an invalid source record")
        source_id = source.get("source_id")
        digest = source.get("initial_source_sha256")
        if (
            not isinstance(source_id, str)
            or not source_id
            or source_id in initial
            or not isinstance(digest, str)
            or not _SHA256_RE.fullmatch(digest)
        ):
            raise ReviewAllocationError("review allocation has invalid source identities")
        initial[source_id] = digest
    return initial


def validate_manifest(
    manifest: dict[str, Any],
    sources: list[tuple[str, str, list[dict[str, Any]]]],
    run_binding: dict[str, Any],
) -> None:
    """Reconstruct the allocation while allowing only review-field source changes."""
    if manifest.get("review_allocation_schema_version") != REVIEW_ALLOCATION_SCHEMA_VERSION:
        raise ReviewAllocationError("unsupported review-allocation schema")
    if manifest.get("algorithm") != ALGORITHM:
        raise ReviewAllocationError("unsupported review-allocation algorithm")
    seed = manifest.get("seed")
    calibration_count = manifest.get("calibration_count")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ReviewAllocationError("review allocation has an invalid seed")
    if (
        isinstance(calibration_count, bool)
        or not isinstance(calibration_count, int)
        or calibration_count <= 0
    ):
        raise ReviewAllocationError("review allocation has an invalid calibration count")
    expected = build_manifest(
        sources,
        run_binding,
        seed=seed,
        calibration_count=calibration_count,
        initial_source_sha256=_manifest_initial_hashes(manifest),
    )
    if manifest != expected:
        raise ReviewAllocationError(
            "review allocation differs from the deterministic run/sample-bound reconstruction"
        )


def _entries_by_fingerprint(
    sources: list[tuple[str, str, list[dict[str, Any]]]],
) -> dict[str, dict[str, Any]]:
    return {
        str(entry["finding_fingerprint"]): entry
        for _, _, entries in sources
        for entry in entries
        if str(entry.get("cohort")) in EFFECTIVENESS_COHORTS
    }


def _review_progress(
    manifest: dict[str, Any],
    sources: list[tuple[str, str, list[dict[str, Any]]]],
    selection: str,
) -> tuple[int, int, int, dict[str, tuple[int, int]]]:
    entries = _entries_by_fingerprint(sources)
    references = manifest[SELECTION_KEYS[selection]]
    complete = 0
    adjudication_pending = 0
    agreements = {field: [0, 0] for field in JUDGEMENT_FIELDS}
    for reference in references:
        entry = entries[str(reference["finding_fingerprint"])]
        reviews = entry.get("reviews", [])
        if len(reviews) > 2:  # pragma: no cover - label validation rejects this first
            raise ReviewAllocationError("a selected finding has more than two reviews")
        if len(reviews) != 2:
            continue
        complete += 1
        first, second = reviews[0], reviews[1]
        disagrees = False
        for field in JUDGEMENT_FIELDS:
            agreements[field][1] += 1
            if first[field] == second[field]:
                agreements[field][0] += 1
            else:
                disagrees = True
        if disagrees and entry.get("adjudication") is None:
            adjudication_pending += 1
    return (
        complete,
        len(references),
        adjudication_pending,
        {field: (values[0], values[1]) for field, values in agreements.items()},
    )


def require_review_completion(
    manifest: dict[str, Any],
    sources: list[tuple[str, str, list[dict[str, Any]]]],
    selection: str,
) -> None:
    complete, total, pending, agreements = _review_progress(manifest, sources, selection)
    if complete != total:
        raise ReviewAllocationError(
            f"{selection} review is incomplete: {complete}/{total} records have two reviews"
        )
    if pending:
        raise ReviewAllocationError(
            f"{selection} review has {pending} unadjudicated judgement disagreement(s)"
        )
    if selection == "calibration":
        for field in ("correctness", "applicability"):
            agreed, compared = agreements[field]
            if compared == 0 or Fraction(agreed, compared) < Fraction(4, 5):
                raise ReviewAllocationError(
                    f"calibration {field} exact agreement is below 80%: {agreed}/{compared}"
                )


def _first_review_progress(
    sources: list[tuple[str, str, list[dict[str, Any]]]],
) -> tuple[int, int]:
    population = [
        entry
        for _, _, entries in sources
        for entry in entries
        if str(entry.get("cohort")) in EFFECTIVENESS_COHORTS
    ]
    return sum(bool(entry.get("reviews")) for entry in population), len(population)


def require_first_review_completion(
    sources: list[tuple[str, str, list[dict[str, Any]]]],
) -> None:
    complete, total = _first_review_progress(sources)
    if complete != total:
        raise ReviewAllocationError(
            f"first review is incomplete: {complete}/{total} Layer B records reviewed"
        )


def allowed_fingerprints_for_source(
    allocation_path: Path,
    sample_path: Path,
    entries: list[dict[str, Any]],
    run: Path,
    selection: str,
    allocation_sources: list[Path],
) -> set[str]:
    """Validate one source binding and return its selected fingerprints."""
    if selection not in SELECTION_KEYS:
        raise ReviewAllocationError(f"unsupported review selection: {selection}")
    manifest = _load_manifest(allocation_path)
    if not allocation_sources:
        raise ReviewAllocationError(
            "all bound review sources are required when applying an allocation"
        )
    try:
        resolved_sample = sample_path.resolve(strict=True)
        resolved_sources = [path.resolve(strict=True) for path in allocation_sources]
    except OSError as exc:
        raise ReviewAllocationError(f"cannot resolve allocation source: {exc}") from exc
    if len(set(resolved_sources)) != len(resolved_sources):
        raise ReviewAllocationError("allocation source paths must be unique")
    if resolved_sample not in resolved_sources:
        raise ReviewAllocationError("the current review file is absent from allocation sources")
    bound_sources, run_binding = _load_bound_sources(
        allocation_sources, run, require_unreviewed=False
    )
    validate_manifest(manifest, bound_sources, run_binding)
    source_id = sample_path.name
    source_records = manifest.get("sources")
    if not isinstance(source_records, list):
        raise ReviewAllocationError("review allocation has invalid sources")
    matches = [
        source
        for source in source_records
        if isinstance(source, dict) and source.get("source_id") == source_id
    ]
    if len(matches) != 1:
        raise ReviewAllocationError(
            f"review allocation does not bind exactly one source named {source_id!r}"
        )
    source = matches[0]
    sample_set = entries[0].get("sample_set")
    expected = {
        "source_identity_sha256": _source_identity_sha256(entries),
        "sample_set_sha256": _canonical_sha256(sample_set),
        "entry_count": len(entries),
        "layer_b_entry_count": sum(
            str(entry.get("cohort")) in EFFECTIVENESS_COHORTS for entry in entries
        ),
        "excluded_stress_entry_count": sum(
            str(entry.get("cohort")) in STRESS_COHORTS for entry in entries
        ),
    }
    if any(source.get(field) != value for field, value in expected.items()):
        raise ReviewAllocationError(
            f"review source {source_id!r} differs from its persisted identity binding"
        )
    references = manifest.get(SELECTION_KEYS[selection])
    if not isinstance(references, list):
        raise ReviewAllocationError("review allocation has an invalid selection")
    allowed = {
        str(reference["finding_fingerprint"])
        for reference in references
        if isinstance(reference, dict) and reference.get("source_id") == source_id
    }
    available = {str(entry["finding_fingerprint"]) for entry in entries}
    if not allowed <= available:
        raise ReviewAllocationError(
            f"review selection references findings absent from {source_id!r}"
        )
    if any(
        str(entry["cohort"]) in STRESS_COHORTS
        for entry in entries
        if str(entry["finding_fingerprint"]) in allowed
    ):
        raise ReviewAllocationError("review selection contains a stress record")
    return allowed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create", help="freeze a new review allocation")
    create_parser.add_argument("--run", type=Path, required=True)
    create_parser.add_argument("--sample", type=Path, action="append", required=True)
    create_parser.add_argument("--seed", type=int, default=0)
    create_parser.add_argument("--calibration-count", type=int, default=CALIBRATION_COUNT)
    create_parser.add_argument("--out", type=Path, required=True)

    validate_parser = subparsers.add_parser(
        "validate", help="reconstruct and validate a persisted allocation"
    )
    validate_parser.add_argument("--allocation", type=Path, required=True)
    validate_parser.add_argument("--run", type=Path, required=True)
    validate_parser.add_argument("--sample", type=Path, action="append", required=True)
    validate_parser.add_argument("--require-first-review-complete", action="store_true")
    validate_parser.add_argument("--require-calibration-complete", action="store_true")
    validate_parser.add_argument("--require-second-review-complete", action="store_true")
    args = parser.parse_args()

    try:
        if args.command == "create":
            ensure_output_outside(args.out, [args.run])
            if args.out.exists():
                raise ReviewAllocationError(
                    f"refusing to overwrite existing review allocation: {args.out}"
                )
            sources, run_binding = _load_bound_sources(
                args.sample, args.run, require_unreviewed=True
            )
            manifest = build_manifest(
                sources,
                run_binding,
                seed=args.seed,
                calibration_count=args.calibration_count,
            )
            args.out.parent.mkdir(parents=True, exist_ok=True)
            write_json(args.out, manifest)
            print(
                f"wrote {args.out}: {manifest['calibration_count']} calibration and "
                f"{manifest['second_review_count']} second-review finding(s) from "
                f"{manifest['population']['entry_count']} Layer B targets; "
                "stress records excluded"
            )
            return 0

        manifest = _load_manifest(args.allocation)
        sources, run_binding = _load_bound_sources(args.sample, args.run, require_unreviewed=False)
        validate_manifest(manifest, sources, run_binding)
        if args.require_first_review_complete:
            require_first_review_completion(sources)
        if args.require_calibration_complete:
            require_review_completion(manifest, sources, "calibration")
        if args.require_second_review_complete:
            require_review_completion(manifest, sources, "second-review")
        calibration = _review_progress(manifest, sources, "calibration")
        second = _review_progress(manifest, sources, "second-review")
        first = _first_review_progress(sources)
        print(
            f"valid review allocation: first-review={first[0]}/{first[1]} "
            f"calibration={calibration[0]}/{calibration[1]} "
            f"second-review={second[0]}/{second[1]}; "
            f"pending adjudications={calibration[2]}/{second[2]}"
        )
        print(
            "calibration exact agreement: "
            + "; ".join(
                f"{field}={agreed}/{compared}"
                for field, (agreed, compared) in calibration[3].items()
            )
        )
        return 0
    except (OSError, ReviewAllocationError, RunContractError, ValueError) as exc:
        sys.exit(f"invalid review allocation: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
