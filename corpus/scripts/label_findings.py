#!/usr/bin/env python3
"""Review sampled findings with blinded, independent, orthogonal judgements."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any

if __package__:
    from .run_contract import (
        HARNESS_DIRECTORY,
        RUN_META_NAME,
        RunContractError,
        finding_fingerprint,
        require_current_harness_file,
        sha256_file,
        validate_run_evidence,
    )
    from .sample_findings import (
        ALL_STATUSES,
        _completed_rows,
        _filter_repositories,
        _fingerprint_set_sha256,
        _is_within,
        _pick_repos,
        _sample_findings,
        _sampler_python_identity,
    )
else:
    from run_contract import (
        HARNESS_DIRECTORY,
        RUN_META_NAME,
        RunContractError,
        finding_fingerprint,
        require_current_harness_file,
        sha256_file,
        validate_run_evidence,
    )
    from sample_findings import (
        ALL_STATUSES,
        _completed_rows,
        _filter_repositories,
        _fingerprint_set_sha256,
        _is_within,
        _pick_repos,
        _sample_findings,
        _sampler_python_identity,
    )

LABEL_SCHEMA_VERSION = 2
SUPPORTED_LABEL_SCHEMA_VERSIONS = frozenset({1, LABEL_SCHEMA_VERSION})
CORRECTNESS = ("correct", "incorrect", "unclear")
APPLICABILITY = ("applicable", "not_applicable", "unclear")
UTILITY = ("actionable", "minor", "low_value", "not_applicable", "unclear")
VERIFICATION_MODES = ("manual_static", "manual_online", "author_confirmed")
ROOT_CAUSES = (
    "collector_miss",
    "semantic_equivalence",
    "abstraction_limit",
    "repository_context",
    "wording_problem",
    "weighting_problem",
    "real_repository_gap",
    "needs_dynamic_evidence",
    "needs_author_input",
    "suppression_policy",
    "none",
)
JUDGEMENT_FIELDS = ("correctness", "applicability", "utility")
REVIEW_FIELDS = (*JUDGEMENT_FIELDS, "root_cause", "verification_mode")
_FINGERPRINT_RE = re.compile(r"^v1:[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_RUN_EVIDENCE_FIELDS = frozenset(
    {
        "binding_schema_version",
        "run_schema_version",
        "run_meta_sha256",
        "combined_csv_sha256",
        "raw_json_sha256",
    }
)
_SAMPLE_SET_FIELDS = frozenset(
    {
        "binding_schema_version",
        "sampler_sha256",
        "sampler_python",
        "arguments",
        "eligible_repository_ids",
        "selected_repository_ids",
        "entry_count",
        "finding_fingerprint_set_sha256",
    }
)
_SAMPLER_PYTHON_FIELDS = frozenset({"implementation", "version"})
_PROBABILITY_FIELDS = frozenset({"numerator", "denominator", "value"})
_SAMPLING_FIELDS = frozenset(
    {
        "design",
        "design_version",
        "seed",
        "repository_stratum",
        "finding_stratum",
        "overall_inclusion_probability",
    }
)
_REPOSITORY_STRATUM_FIELDS = frozenset(
    {"cohort", "population_size", "sample_size", "inclusion_probability"}
)
_FINDING_STRATUM_FIELDS = frozenset(
    {
        "status",
        "category",
        "population_size",
        "sample_size",
        "conditional_inclusion_probability",
    }
)
_REVIEW_FIELDS = frozenset(
    {
        "reviewer_id",
        "reviewed_at",
        *REVIEW_FIELDS,
        "label_confidence",
        "notes",
        "evidence_links",
    }
)
_ADJUDICATION_FIELDS = (_REVIEW_FIELDS - {"reviewer_id"}) | {"adjudicator_id"}
_V2_ENTRY_FIELDS = frozenset(
    {
        "label_schema_version",
        "run_id",
        "repo_id",
        "repo_commit",
        "cohort",
        "adduce_version",
        "rule_id",
        "category",
        "title",
        "finding_status",
        "finding_confidence",
        "severity",
        "message",
        "locations",
        "suppressed",
        "finding_fingerprint",
        "run_evidence",
        "sampling",
        "sample_set",
        "reviews",
        "adjudication",
    }
)
SAMPLER_HARNESS_PATH = f"{HARNESS_DIRECTORY}/scripts/sample_findings.py"
_SAMPLE_ARGUMENT_FIELDS = frozenset(
    {
        "mode",
        "seed",
        "statuses",
        "n_repos",
        "per_stratum",
        "include_cohorts",
        "exclude_cohorts",
        "include_repos",
        "exclude_repos",
        "include_suppressed",
    }
)
_SELECTOR_ARGUMENTS = (
    "include_cohorts",
    "exclude_cohorts",
    "include_repos",
    "exclude_repos",
)


def load(path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {line_number} is not valid JSON: {exc}") from exc
        if not isinstance(entry, dict):
            raise ValueError(f"line {line_number} is not a JSON object")
        entries.append(entry)
    return entries


def _valid_choice(value: object, choices: tuple[str, ...], allow_empty: bool = False) -> bool:
    return (allow_empty and value == "") or value in choices


def _validate_timestamp(value: object, context: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context}: missing review timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{context}: invalid review timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{context}: review timestamp requires a timezone")
    return parsed


def _validate_probability(value: object, context: str) -> Fraction:
    if not isinstance(value, dict) or set(value) != _PROBABILITY_FIELDS:
        raise ValueError(f"{context}: inclusion probability fields do not match the schema")
    numerator = value.get("numerator")
    denominator = value.get("denominator")
    observed = value.get("value")
    if (
        isinstance(numerator, bool)
        or not isinstance(numerator, int)
        or isinstance(denominator, bool)
        or not isinstance(denominator, int)
        or denominator <= 0
        or not 0 <= numerator <= denominator
        or isinstance(observed, bool)
        or not isinstance(observed, (int, float))
        or abs(float(observed) - numerator / denominator) > 1e-12
    ):
        raise ValueError(f"{context}: invalid inclusion probability")
    return Fraction(numerator, denominator)


def _validate_sorted_strings(
    value: object, context: str, *, require_nonempty: bool = False
) -> list[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or value != sorted(set(value))
        or (require_nonempty and not value)
    ):
        raise ValueError(f"{context}: expected a sorted unique string list")
    return value


def _require_v2_entries(entries: list[dict[str, Any]]) -> None:
    schemas = {entry.get("label_schema_version") for entry in entries}
    if schemas != {LABEL_SCHEMA_VERSION}:
        raise ValueError("review and reporting require one v2-bound sample set")


def _validate_sample_set(entries: list[dict[str, Any]]) -> None:
    """Verify the immutable selection binding shared by every v2 record."""
    first = entries[0].get("sample_set")
    if not isinstance(first, dict) or set(first) != _SAMPLE_SET_FIELDS:
        raise ValueError("sample set has invalid binding fields")
    if any(entry.get("sample_set") != first for entry in entries[1:]):
        raise ValueError("sample entries have inconsistent sample-set bindings")
    if first.get("binding_schema_version") != 1:
        raise ValueError("sample set has an unsupported binding version")
    sampler_sha256 = first.get("sampler_sha256")
    fingerprint_digest = first.get("finding_fingerprint_set_sha256")
    if not isinstance(sampler_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sampler_sha256):
        raise ValueError("sample set has an invalid sampler SHA-256")
    sampler_python = first.get("sampler_python")
    if (
        not isinstance(sampler_python, dict)
        or set(sampler_python) != _SAMPLER_PYTHON_FIELDS
        or any(
            not isinstance(sampler_python.get(field), str) or not sampler_python[field]
            for field in _SAMPLER_PYTHON_FIELDS
        )
    ):
        raise ValueError("sample set has an invalid sampler Python identity")
    if sampler_python != _sampler_python_identity():
        raise ValueError("sample set requires a different sampler Python runtime")
    if not isinstance(fingerprint_digest, str) or not re.fullmatch(
        r"[0-9a-f]{64}", fingerprint_digest
    ):
        raise ValueError("sample set has an invalid fingerprint-set SHA-256")

    arguments = first.get("arguments")
    if not isinstance(arguments, dict) or set(arguments) != _SAMPLE_ARGUMENT_FIELDS:
        raise ValueError("sample set has invalid sampler arguments")
    mode = arguments.get("mode")
    if mode not in {"sample", "census"}:
        raise ValueError("sample set has an invalid selection mode")
    seed = arguments.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("sample set has an invalid seed")
    statuses = _validate_sorted_strings(
        arguments.get("statuses"), "sample set statuses", require_nonempty=True
    )
    if not set(statuses) <= ALL_STATUSES:
        raise ValueError("sample set has an invalid finding status")
    for field in _SELECTOR_ARGUMENTS:
        _validate_sorted_strings(arguments.get(field), f"sample set {field}")
    if not isinstance(arguments.get("include_suppressed"), bool):
        raise ValueError("sample set has an invalid suppression policy")
    n_repos = arguments.get("n_repos")
    per_stratum = arguments.get("per_stratum")
    if mode == "census":
        if n_repos is not None or per_stratum is not None:
            raise ValueError("census sample set cannot carry sampling limits")
    elif (
        isinstance(n_repos, bool)
        or not isinstance(n_repos, int)
        or n_repos <= 0
        or isinstance(per_stratum, bool)
        or not isinstance(per_stratum, int)
        or per_stratum <= 0
    ):
        raise ValueError("sample set has invalid sampling limits")

    eligible_ids = _validate_sorted_strings(
        first.get("eligible_repository_ids"),
        "sample set eligible repositories",
        require_nonempty=True,
    )
    selected_ids = _validate_sorted_strings(
        first.get("selected_repository_ids"),
        "sample set selected repositories",
        require_nonempty=True,
    )
    if not set(selected_ids) <= set(eligible_ids):
        raise ValueError("sample set selects an ineligible repository")
    entry_count = first.get("entry_count")
    if (
        isinstance(entry_count, bool)
        or not isinstance(entry_count, int)
        or entry_count <= 0
        or entry_count != len(entries)
    ):
        raise ValueError("sample-set entry count does not match the JSONL records")
    fingerprints = [str(entry.get("finding_fingerprint", "")) for entry in entries]
    if _fingerprint_set_sha256(fingerprints) != fingerprint_digest:
        raise ValueError("sample-set fingerprint digest does not match the JSONL records")

    for index, entry in enumerate(entries, 1):
        context = f"entry {index}"
        if entry.get("repo_id") not in selected_ids:
            raise ValueError(f"{context}: repository is absent from the selected sample set")
        if entry.get("finding_status") not in statuses:
            raise ValueError(f"{context}: status is absent from the sampler arguments")
        if not arguments["include_suppressed"] and entry.get("suppressed"):
            raise ValueError(f"{context}: suppressed finding violates the sampler arguments")
        sampling = entry.get("sampling")
        expected_design = "census" if mode == "census" else "two-stage-stratified"
        if not isinstance(sampling, dict) or sampling.get("design") != expected_design:
            raise ValueError(f"{context}: sampling design does not match the sample set")
        if sampling.get("seed") != seed:
            raise ValueError(f"{context}: sampling seed does not match the sample set")
        if mode == "census":
            repository_stratum = sampling.get("repository_stratum", {})
            finding_stratum = sampling.get("finding_stratum", {})
            if repository_stratum.get("population_size") != repository_stratum.get(
                "sample_size"
            ) or finding_stratum.get("population_size") != finding_stratum.get("sample_size"):
                raise ValueError(f"{context}: census records require complete strata")


def _validate_review(review: object, context: str, *, adjudication: bool = False) -> datetime:
    if not isinstance(review, dict):
        raise ValueError(f"{context}: review is not an object")
    expected_fields = _ADJUDICATION_FIELDS if adjudication else _REVIEW_FIELDS
    if set(review) != expected_fields:
        raise ValueError(f"{context}: review fields do not match the v2 schema")
    identity_field = "adjudicator_id" if adjudication else "reviewer_id"
    identity = review.get(identity_field)
    if (
        not isinstance(identity, str)
        or not identity
        or any(character.isspace() for character in identity)
    ):
        raise ValueError(f"{context}: invalid {identity_field}")
    reviewed_at = _validate_timestamp(review.get("reviewed_at"), context)
    if not _valid_choice(review.get("correctness"), CORRECTNESS):
        raise ValueError(f"{context}: invalid correctness label")
    if not _valid_choice(review.get("applicability"), APPLICABILITY):
        raise ValueError(f"{context}: invalid applicability label")
    if not _valid_choice(review.get("utility"), UTILITY):
        raise ValueError(f"{context}: invalid utility label")
    if not _valid_choice(review.get("verification_mode"), VERIFICATION_MODES):
        raise ValueError(f"{context}: invalid verification mode")
    if review.get("root_cause") not in ROOT_CAUSES:
        raise ValueError(f"{context}: invalid root cause")
    confidence = review.get("label_confidence")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0 <= confidence <= 1
    ):
        raise ValueError(f"{context}: label confidence must be between 0 and 1")
    evidence_links = review.get("evidence_links", [])
    if (
        not isinstance(evidence_links, list)
        or not evidence_links
        or any(not isinstance(link, str) or not link.strip() for link in evidence_links)
    ):
        raise ValueError(f"{context}: at least one non-empty evidence link is required")
    notes = review.get("notes")
    if not isinstance(notes, str):
        raise ValueError(f"{context}: notes must be a string")
    if (
        adjudication or any(review.get(field) == "unclear" for field in JUDGEMENT_FIELDS)
    ) and not notes.strip():
        reason = "adjudication" if adjudication else "unclear judgement"
        raise ValueError(f"{context}: {reason} requires explanatory notes")
    return reviewed_at


def _validate_legacy_entry(entry: dict[str, Any], context: str) -> None:
    complete = bool(entry.get("reviewed_at"))
    if not _valid_choice(entry.get("correctness"), CORRECTNESS, allow_empty=not complete):
        raise ValueError(f"{context}: invalid correctness label")
    if not _valid_choice(entry.get("applicability"), APPLICABILITY, allow_empty=not complete):
        raise ValueError(f"{context}: invalid applicability label")
    if not _valid_choice(entry.get("utility"), UTILITY, allow_empty=not complete):
        raise ValueError(f"{context}: invalid utility label")
    if not _valid_choice(
        entry.get("verification_mode"), VERIFICATION_MODES, allow_empty=not complete
    ):
        raise ValueError(f"{context}: invalid verification mode")
    if complete:
        _validate_review(
            {
                key: entry.get(key)
                for key in (
                    "reviewer_id",
                    "reviewed_at",
                    *REVIEW_FIELDS,
                    "label_confidence",
                    "notes",
                    "evidence_links",
                )
            },
            context,
        )


def _validate_v2_entry(entry: dict[str, Any], context: str) -> None:
    if set(entry) != _V2_ENTRY_FIELDS:
        raise ValueError(f"{context}: fields do not match the v2 entry schema")
    for field in (
        "run_id",
        "repo_id",
        "repo_commit",
        "cohort",
        "adduce_version",
        "rule_id",
        "finding_status",
    ):
        if not isinstance(entry.get(field), str) or not entry[field]:
            raise ValueError(f"{context}: missing {field}")
    if entry["finding_status"] not in {"pass", "partial", "fail", "unknown", "not-applicable"}:
        raise ValueError(f"{context}: invalid finding status")
    if not _COMMIT_RE.fullmatch(entry["repo_commit"]):
        raise ValueError(f"{context}: repository commit is not a full lowercase Git commit")
    if not isinstance(entry.get("suppressed"), bool):
        raise ValueError(f"{context}: suppressed must be a boolean")

    run_evidence = entry.get("run_evidence")
    if not isinstance(run_evidence, dict) or set(run_evidence) != _RUN_EVIDENCE_FIELDS:
        raise ValueError(f"{context}: invalid run evidence binding")
    if run_evidence.get("binding_schema_version") != 1:
        raise ValueError(f"{context}: unsupported run evidence binding")
    run_schema_version = run_evidence.get("run_schema_version")
    if (
        isinstance(run_schema_version, bool)
        or not isinstance(run_schema_version, int)
        or run_schema_version <= 0
    ):
        raise ValueError(f"{context}: invalid bound run schema version")
    for field in ("run_meta_sha256", "combined_csv_sha256", "raw_json_sha256"):
        if not isinstance(run_evidence.get(field), str) or not re.fullmatch(
            r"[0-9a-f]{64}", run_evidence[field]
        ):
            raise ValueError(f"{context}: invalid {field}")

    sampling = entry.get("sampling")
    if (
        not isinstance(sampling, dict)
        or set(sampling) != _SAMPLING_FIELDS
        or sampling.get("design")
        not in {
            "two-stage-stratified",
            "census",
        }
    ):
        raise ValueError(f"{context}: invalid sampling design")
    repository_stratum = sampling.get("repository_stratum")
    finding_stratum = sampling.get("finding_stratum")
    if not isinstance(repository_stratum, dict) or not isinstance(finding_stratum, dict):
        raise ValueError(f"{context}: sampling strata must be objects")
    if set(repository_stratum) != _REPOSITORY_STRATUM_FIELDS:
        raise ValueError(f"{context}: repository stratum fields do not match the schema")
    if set(finding_stratum) != _FINDING_STRATUM_FIELDS:
        raise ValueError(f"{context}: finding stratum fields do not match the schema")
    if (
        sampling.get("design_version") != 1
        or isinstance(sampling.get("seed"), bool)
        or not isinstance(sampling.get("seed"), int)
    ):
        raise ValueError(f"{context}: invalid sampling design version or seed")
    if repository_stratum.get("cohort") != entry["cohort"]:
        raise ValueError(f"{context}: repository stratum does not match cohort")
    if finding_stratum.get("status") != entry["finding_status"]:
        raise ValueError(f"{context}: finding stratum does not match status")
    if finding_stratum.get("category") != str(entry.get("category") or "?"):
        raise ValueError(f"{context}: finding stratum does not match category")
    for name, stratum in (
        ("repository stratum", repository_stratum),
        ("finding stratum", finding_stratum),
    ):
        population = stratum.get("population_size")
        sample = stratum.get("sample_size")
        if (
            isinstance(population, bool)
            or not isinstance(population, int)
            or population <= 0
            or isinstance(sample, bool)
            or not isinstance(sample, int)
            or not 0 < sample <= population
        ):
            raise ValueError(f"{context}: invalid {name} sizes")
    repository_probability = _validate_probability(
        repository_stratum.get("inclusion_probability"), f"{context}: repository stratum"
    )
    finding_probability = _validate_probability(
        finding_stratum.get("conditional_inclusion_probability"),
        f"{context}: finding stratum",
    )
    overall_probability = _validate_probability(
        sampling.get("overall_inclusion_probability"), f"{context}: overall"
    )
    expected_repository_probability = Fraction(
        repository_stratum["sample_size"], repository_stratum["population_size"]
    )
    expected_finding_probability = Fraction(
        finding_stratum["sample_size"], finding_stratum["population_size"]
    )
    if repository_probability != expected_repository_probability:
        raise ValueError(f"{context}: repository inclusion probability does not match its sizes")
    if finding_probability != expected_finding_probability:
        raise ValueError(f"{context}: finding inclusion probability does not match its sizes")
    if overall_probability != repository_probability * finding_probability:
        raise ValueError(f"{context}: overall inclusion probability does not match both stages")

    reviews = entry.get("reviews")
    if not isinstance(reviews, list):
        raise ValueError(f"{context}: reviews must be a list")
    reviewers: set[str] = set()
    review_timestamps: list[datetime] = []
    for review_number, review in enumerate(reviews, 1):
        review_context = f"{context}, review {review_number}"
        review_timestamps.append(_validate_review(review, review_context))
        reviewer_id = str(review["reviewer_id"])
        if reviewer_id in reviewers:
            raise ValueError(f"{context}: reviewer {reviewer_id!r} appears more than once")
        reviewers.add(reviewer_id)

    adjudication = entry.get("adjudication")
    if adjudication is not None:
        if len(reviews) < 2:
            raise ValueError(f"{context}: adjudication requires at least two reviews")
        if all(len({review[field] for review in reviews}) == 1 for field in JUDGEMENT_FIELDS):
            raise ValueError(f"{context}: adjudication recorded without a judgement disagreement")
        adjudicated_at = _validate_review(
            adjudication, f"{context}, adjudication", adjudication=True
        )
        if str(adjudication["adjudicator_id"]) in reviewers:
            raise ValueError(f"{context}: adjudicator must be independent of the reviewers")
        if adjudicated_at < max(review_timestamps):
            raise ValueError(f"{context}: adjudication timestamp precedes an initial review")


def validate(entries: list[dict[str, Any]]) -> None:
    if not entries:
        raise ValueError("label sample is empty")
    fingerprints: set[str] = set()
    for index, entry in enumerate(entries, 1):
        context = f"entry {index}"
        schema = entry.get("label_schema_version")
        if isinstance(schema, bool) or schema not in SUPPORTED_LABEL_SCHEMA_VERSIONS:
            raise ValueError(f"{context}: unsupported label schema")
        fingerprint = entry.get("finding_fingerprint")
        if not isinstance(fingerprint, str) or not _FINGERPRINT_RE.fullmatch(fingerprint):
            raise ValueError(f"{context}: invalid finding fingerprint")
        if fingerprint in fingerprints:
            raise ValueError(f"{context}: duplicate finding fingerprint")
        fingerprints.add(fingerprint)
        if schema == 1:
            _validate_legacy_entry(entry, context)
        else:
            _validate_v2_entry(entry, context)
    if all(entry.get("label_schema_version") == LABEL_SCHEMA_VERSION for entry in entries):
        _validate_sample_set(entries)


def _artifact_digests(metadata: dict[str, Any]) -> dict[str, str]:
    return {str(record["path"]): str(record["sha256"]) for record in metadata["artifacts"]}


def _exact_finding_fields(finding: dict[str, Any]) -> dict[str, Any]:
    return {
        "rule_id": finding.get("rule_id"),
        "category": finding.get("category"),
        "title": finding.get("title", ""),
        "finding_status": finding.get("status"),
        "finding_confidence": finding.get("confidence"),
        "severity": finding.get("severity"),
        "message": finding.get("message", ""),
        "locations": finding.get("locations", []),
        "suppressed": bool(finding.get("suppressed", False)),
    }


def _reconstruct_sample_fingerprints(
    sample_set: dict[str, Any],
    rows: list[dict[str, str]],
    artifacts: dict[str, bytes],
) -> set[str]:
    """Re-run the recorded deterministic selection against immutable raw evidence."""
    arguments = sample_set["arguments"]
    completed_rows = _completed_rows(rows, artifacts)
    include_repos = set(arguments["include_repos"])
    incomplete_requested = include_repos - {row["id"] for row in completed_rows}
    if incomplete_requested:
        raise ValueError(
            f"sample set selects incomplete repository scan(s): {sorted(incomplete_requested)}"
        )
    eligible = _filter_repositories(
        completed_rows,
        include_cohorts=set(arguments["include_cohorts"]),
        exclude_cohorts=set(arguments["exclude_cohorts"]),
        include_repos=include_repos,
        exclude_repos=set(arguments["exclude_repos"]),
        selector_universe=rows,
    )
    eligible_ids = sorted(row["id"] for row in eligible)
    if eligible_ids != sample_set["eligible_repository_ids"]:
        raise ValueError("sample-set eligibility does not match the immutable run")

    rng = random.Random(arguments["seed"])
    census = arguments["mode"] == "census"
    if census:
        picked = list(eligible)
    else:
        picked, _ = _pick_repos(eligible, int(arguments["n_repos"]), rng)
    selected_ids = sorted(row["id"] for row in picked)
    if selected_ids != sample_set["selected_repository_ids"]:
        raise ValueError("sample-set repository selection does not match the immutable run")

    fingerprints: set[str] = set()
    for repo in picked:
        raw_path = f"raw_json/{repo['id']}.json"
        try:
            payload = json.loads(artifacts[raw_path].decode("utf-8"))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:  # pragma: no cover
            raise ValueError(f"sample set cannot read raw evidence for {repo['id']}") from exc
        if not isinstance(payload, dict):  # pragma: no cover - run validation rejects this
            raise ValueError(f"sample set raw evidence for {repo['id']} is not an object")
        selected = _sample_findings(
            payload,
            frozenset(arguments["statuses"]),
            1 if census else int(arguments["per_stratum"]),
            rng,
            include_suppressed=bool(arguments["include_suppressed"]),
            census=census,
        )
        for finding, _ in selected:
            fingerprints.add(finding_fingerprint(repo["id"], repo["resolved_sha"], finding))
    return fingerprints


def validate_against_run(entries: list[dict[str, Any]], run: Path) -> None:
    """Bind every v2 sample entry to exact evidence in one validated run."""
    validate(entries)
    _require_v2_entries(entries)
    try:
        metadata, artifacts, rows = validate_run_evidence(run)
        require_current_harness_file(
            metadata, "scripts/label_findings.py", Path(__file__)
        )
        run_meta_sha256 = sha256_file(run / RUN_META_NAME)
    except RunContractError as exc:
        raise ValueError(f"corpus run is invalid: {exc}") from exc

    digests = _artifact_digests(metadata)
    rows_by_id = {row["id"]: row for row in rows}
    sample_set = entries[0]["sample_set"]
    sampler_path = Path(__file__).with_name("sample_findings.py")
    frozen_sampler_sha256 = hashlib.sha256(artifacts[SAMPLER_HARNESS_PATH]).hexdigest()
    if sample_set["sampler_sha256"] != frozen_sampler_sha256:
        raise ValueError(
            "sample set was produced by different sampler source than the immutable run harness"
        )
    if sample_set["sampler_sha256"] != sha256_file(sampler_path):
        raise ValueError("sample set was produced by different sampler source")
    run_completed_at = datetime.fromisoformat(str(metadata["completed_at"]).replace("Z", "+00:00"))
    expected_fingerprints = _reconstruct_sample_fingerprints(sample_set, rows, artifacts)
    observed_fingerprints = {str(entry["finding_fingerprint"]) for entry in entries}
    if expected_fingerprints != observed_fingerprints:
        raise ValueError("sample-set findings do not match deterministic selection from --run")
    payloads: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(entries, 1):
        context = f"entry {index}"
        review_records = [*entry["reviews"]]
        if isinstance(entry.get("adjudication"), dict):
            review_records.append(entry["adjudication"])
        if any(
            _validate_timestamp(review["reviewed_at"], context) < run_completed_at
            for review in review_records
        ):
            raise ValueError(f"{context}: review timestamp precedes run completion")
        if entry["run_id"] != metadata["run_id"]:
            raise ValueError(f"{context}: run ID does not match --run")
        if entry["adduce_version"] != metadata["adduce_version"]:
            raise ValueError(f"{context}: Adduce version does not match --run")

        repo_id = str(entry["repo_id"])
        row = rows_by_id.get(repo_id)
        if row is None:
            raise ValueError(f"{context}: repository is absent from --run")
        if entry["repo_commit"] != row["resolved_sha"]:
            raise ValueError(f"{context}: repository commit does not match --run")
        if entry["cohort"] != row["cohort"]:
            raise ValueError(f"{context}: cohort does not match --run")

        raw_path = f"raw_json/{repo_id}.json"
        raw_bytes = artifacts.get(raw_path)
        if raw_bytes is None:
            raise ValueError(f"{context}: repository has no successful raw evidence in --run")
        binding = entry["run_evidence"]
        expected_binding = {
            "binding_schema_version": 1,
            "run_schema_version": metadata["run_schema_version"],
            "run_meta_sha256": run_meta_sha256,
            "combined_csv_sha256": digests["combined.csv"],
            "raw_json_sha256": digests[raw_path],
        }
        if binding != expected_binding:
            raise ValueError(f"{context}: run evidence binding does not match --run")
        if hashlib.sha256(raw_bytes).hexdigest() != binding["raw_json_sha256"]:
            raise ValueError(f"{context}: raw evidence digest does not match --run")

        if repo_id not in payloads:
            try:
                payload = json.loads(raw_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:  # pragma: no cover
                raise ValueError(f"{context}: raw evidence is not valid JSON") from exc
            if not isinstance(payload, dict):  # pragma: no cover - run validation rejects this
                raise ValueError(f"{context}: raw evidence is not a JSON object")
            payloads[repo_id] = payload
        findings = payloads[repo_id].get("findings")
        if not isinstance(findings, list):  # pragma: no cover - run validation rejects this
            raise ValueError(f"{context}: raw evidence has no finding list")

        sampled_finding = {
            "rule_id": entry["rule_id"],
            "title": entry.get("title", ""),
            "locations": entry.get("locations", []),
        }
        observed_fingerprint = finding_fingerprint(
            repo_id, str(entry["repo_commit"]), sampled_finding
        )
        if entry["finding_fingerprint"] != observed_fingerprint:
            raise ValueError(f"{context}: finding fingerprint does not match sampled fields")

        fingerprint_matches = [
            finding
            for finding in findings
            if isinstance(finding, dict)
            and finding_fingerprint(repo_id, str(entry["repo_commit"]), finding)
            == entry["finding_fingerprint"]
        ]
        expected_fields = {
            field: entry.get(field)
            for field in (
                "rule_id",
                "category",
                "title",
                "finding_status",
                "finding_confidence",
                "severity",
                "message",
                "locations",
                "suppressed",
            )
        }
        exact_matches = [
            finding
            for finding in fingerprint_matches
            if _exact_finding_fields(finding) == expected_fields
        ]
        if len(exact_matches) != 1:
            detail = "not found" if not exact_matches else "not unique"
            raise ValueError(f"{context}: exact finding evidence is {detail} in immutable raw JSON")


def save(path: Path, entries: list[dict[str, Any]]) -> None:
    """Atomically persist progress after each reviewed finding."""
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _reviews(entry: dict[str, Any]) -> list[dict[str, Any]]:
    if entry.get("label_schema_version") == 1:
        return [entry] if entry.get("reviewed_at") else []
    return list(entry.get("reviews", []))


def _resolved_value(entry: dict[str, Any], field: str) -> str | None:
    adjudication = entry.get("adjudication")
    if isinstance(adjudication, dict):
        return str(adjudication[field])
    values = {str(review[field]) for review in _reviews(entry)}
    return values.pop() if len(values) == 1 else None


def _proportion(label: str, numerator: int, denominator: int) -> str:
    if not denominator:
        return f"{label}: not estimable (n=0)"
    return f"{label}: {numerator}/{denominator} ({numerator / denominator:.1%})"


def _agreement(entries: list[dict[str, Any]], field: str) -> tuple[int, int]:
    agreements = 0
    comparisons = 0
    for entry in entries:
        for first, second in itertools.combinations(_reviews(entry), 2):
            comparisons += 1
            agreements += first[field] == second[field]
    return agreements, comparisons


def _cohen_kappa(pairs: list[tuple[str, str]], choices: tuple[str, ...]) -> float | None:
    if not pairs:
        return None
    observed = sum(first == second for first, second in pairs) / len(pairs)
    first_counts = Counter(first for first, _ in pairs)
    second_counts = Counter(second for _, second in pairs)
    expected = sum(
        first_counts[choice] / len(pairs) * second_counts[choice] / len(pairs) for choice in choices
    )
    if expected == 1:
        return None
    return (observed - expected) / (1 - expected)


def report(entries: list[dict[str, Any]]) -> int:
    validate(entries)
    _require_v2_entries(entries)
    reviewed = [entry for entry in entries if _reviews(entry)]
    second_reviewed = [entry for entry in entries if len(_reviews(entry)) >= 2]
    adjudicated = [entry for entry in entries if isinstance(entry.get("adjudication"), dict)]
    unresolved = [
        entry
        for entry in second_reviewed
        if not entry.get("adjudication")
        and any(
            len({review[field] for review in _reviews(entry)}) > 1 for field in JUDGEMENT_FIELDS
        )
    ]
    review_records = sum(len(_reviews(entry)) for entry in entries)
    print(
        f"{review_records} review record(s) across {len(reviewed)} of "
        f"{len(entries)} sampled findings"
    )
    print(
        f"independent second review: {len(second_reviewed)} finding(s); "
        f"adjudicated: {len(adjudicated)}; unresolved disagreements: {len(unresolved)}"
    )

    print("pairwise reviewer agreement:")
    for field in JUDGEMENT_FIELDS:
        agreements, comparisons = _agreement(entries, field)
        print("  " + _proportion(field, agreements, comparisons))

    reviewer_pairs: dict[tuple[str, str], dict[str, list[tuple[str, str]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for entry in entries:
        reviews = sorted(_reviews(entry), key=lambda review: str(review.get("reviewer_id", "")))
        for first, second in itertools.combinations(reviews, 2):
            pair = (str(first.get("reviewer_id", "")), str(second.get("reviewer_id", "")))
            for field in JUDGEMENT_FIELDS:
                reviewer_pairs[pair][field].append((str(first[field]), str(second[field])))
    if reviewer_pairs:
        print("Cohen's kappa by reviewer pair:")
        choices_by_field = {
            "correctness": CORRECTNESS,
            "applicability": APPLICABILITY,
            "utility": UTILITY,
        }
        for pair, fields in sorted(reviewer_pairs.items()):
            values = []
            for field in JUDGEMENT_FIELDS:
                pairs = fields[field]
                kappa = _cohen_kappa(pairs, choices_by_field[field])
                rendered = "not estimable" if kappa is None else f"{kappa:.3f}"
                values.append(f"{field}={rendered} (n={len(pairs)})")
            print(f"  {pair[0]} / {pair[1]}: " + "; ".join(values))

    print("resolved judgement counts:")
    for field, choices in (
        ("correctness", CORRECTNESS),
        ("applicability", APPLICABILITY),
        ("utility", UTILITY),
    ):
        resolved_values = [_resolved_value(entry, field) for entry in reviewed]
        counts = Counter(value for value in resolved_values if value is not None)
        print(f"  {field} (resolved n={sum(counts.values())}):")
        for choice in choices:
            print(f"    {choice}: {counts.get(choice, 0)}")

    resolved = [
        {
            **entry,
            "_correctness": _resolved_value(entry, "correctness"),
            "_applicability": _resolved_value(entry, "applicability"),
            "_utility": _resolved_value(entry, "utility"),
        }
        for entry in reviewed
    ]
    determinate = [
        entry
        for entry in resolved
        if entry["_applicability"] == "applicable"
        and entry["_correctness"] in {"correct", "incorrect"}
    ]
    incorrect = sum(entry["_correctness"] == "incorrect" for entry in determinate)
    useful_denom = [entry for entry in resolved if entry["_utility"] is not None]
    useful = sum(entry["_utility"] in {"actionable", "minor"} for entry in useful_denom)
    emitted = [entry for entry in determinate if entry["finding_status"] in {"fail", "partial"}]
    emitted_incorrect = sum(entry["_correctness"] == "incorrect" for entry in emitted)
    passes = [entry for entry in determinate if entry["finding_status"] == "pass"]
    incorrect_passes = sum(entry["_correctness"] == "incorrect" for entry in passes)

    print("unweighted reviewed-sample proportions (descriptive; not corpus rates):")
    print(
        "  "
        + _proportion("incorrect among determinate applicable labels", incorrect, len(determinate))
    )
    print("  " + _proportion("actionable or minor utility", useful, len(useful_denom)))
    print("  " + _proportion("incorrect fail/partial labels", emitted_incorrect, len(emitted)))
    print("  " + _proportion("incorrect pass labels", incorrect_passes, len(passes)))

    print("per-rule resolved review summary (descriptive sample counts):")
    by_rule: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in reviewed:
        by_rule[str(entry["rule_id"])].append(entry)
    for rule_id, rule_entries in sorted(by_rule.items()):
        correctness = Counter(_resolved_value(entry, "correctness") for entry in rule_entries)
        utility = Counter(_resolved_value(entry, "utility") for entry in rule_entries)
        print(
            f"  {rule_id}: reviewed={len(rule_entries)}; "
            f"correct={correctness['correct']}; incorrect={correctness['incorrect']}; "
            f"unclear={correctness['unclear']}; unresolved={correctness[None]}; "
            f"actionable={utility['actionable']}; minor={utility['minor']}; "
            f"low_value={utility['low_value']}"
        )

    root_causes = Counter(_resolved_value(entry, "root_cause") for entry in reviewed)
    print("root-cause counts (resolved judgements):")
    for root_cause in ROOT_CAUSES:
        print(f"  {root_cause}: {root_causes[root_cause]}")
    print(f"  unresolved: {root_causes[None]}")
    if len(reviewed) < len(entries):
        print(f"note: {len(entries) - len(reviewed)} sampled findings have no review")
    return 0


def _prompt_choice(label: str, choices: tuple[str, ...]) -> str | None:
    for number, choice in enumerate(choices, 1):
        print(f"    [{number}] {choice}")
    while True:
        try:
            answer = input(f"{label}> ").strip()
        except EOFError:
            return None
        if answer == "q":
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(choices):
            return choices[int(answer) - 1]
        if answer in choices:
            return answer
        print(f"  enter 1-{len(choices)}, a displayed value, or q")


def _collect_judgement(identity_field: str, identity: str) -> dict[str, Any] | None:
    answers: dict[str, Any] = {}
    for field, choices in (
        ("correctness", CORRECTNESS),
        ("applicability", APPLICABILITY),
        ("utility", UTILITY),
        ("root_cause", ROOT_CAUSES),
        ("verification_mode", VERIFICATION_MODES),
    ):
        answer = _prompt_choice(field, choices)
        if answer is None:
            return None
        answers[field] = answer
    while True:
        try:
            raw_confidence = input("label_confidence [0..1]> ").strip()
        except EOFError:
            return None
        try:
            confidence = float(raw_confidence)
        except ValueError:
            print("  enter a number between 0 and 1")
            continue
        if 0 <= confidence <= 1:
            break
        print("  enter a number between 0 and 1")
    notes_required = identity_field == "adjudicator_id" or any(
        answers[field] == "unclear" for field in JUDGEMENT_FIELDS
    )
    while True:
        try:
            notes = input("notes (required)> " if notes_required else "notes (optional)> ").strip()
        except EOFError:
            return None
        if notes or not notes_required:
            break
        print("  explanatory notes are required for unclear or adjudicated judgements")
    while True:
        try:
            raw_links = input("evidence links (comma-separated, required)> ")
        except EOFError:
            return None
        links = [value.strip() for value in raw_links.split(",") if value.strip()]
        if links:
            break
        print("  enter at least one evidence link or repository path with a locator")
    return {
        **answers,
        identity_field: identity,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "label_confidence": confidence,
        "notes": notes,
        "evidence_links": links,
    }


def _print_finding(entry: dict[str, Any], *, show_cohort: bool) -> None:
    cohort = f" [{entry['cohort']}]" if show_cohort else ""
    print(f"--- {entry['repo_id']}{cohort} {entry['rule_id']} ({entry['finding_status']})")
    if entry.get("title"):
        print(f"    {entry['title']}")
    print(f"    {entry.get('message', '')}")
    for location in entry.get("locations", [])[:5]:
        if isinstance(location, dict):
            line = f":{location['line']}" if location.get("line") is not None else ""
            print(f"    at {location.get('path', '?')}{line}")
        else:
            print(f"    at {location}")


def label_loop(
    path: Path,
    entries: list[dict[str, Any]],
    reviewer_id: str,
    *,
    show_cohort: bool = False,
) -> int:
    if any(entry.get("label_schema_version") != LABEL_SCHEMA_VERSION for entry in entries):
        raise ValueError(
            "legacy v1 samples are report-only; draw a v2 sample for independent review"
        )
    pending = [
        entry
        for entry in entries
        if reviewer_id not in {str(review["reviewer_id"]) for review in _reviews(entry)}
    ]
    if not pending:
        print(f"reviewer {reviewer_id!r} has reviewed every finding")
        return 0
    print(
        f"{len(pending)} finding(s) pending for this reviewer; "
        "cohort and other reviewers' judgements are hidden; enter q to stop\n"
    )
    for entry in pending:
        _print_finding(entry, show_cohort=show_cohort)
        review = _collect_judgement("reviewer_id", reviewer_id)
        if review is None:
            print("stopped; completed reviews remain saved")
            return 0
        entry["reviews"].append(review)
        validate(entries)
        save(path, entries)
        print()
    print("all findings reviewed by this reviewer")
    return 0


def adjudication_loop(
    path: Path,
    entries: list[dict[str, Any]],
    adjudicator_id: str,
    *,
    show_cohort: bool = False,
) -> int:
    pending = [
        entry
        for entry in entries
        if len(_reviews(entry)) >= 2
        and entry.get("adjudication") is None
        and any(
            len({review[field] for review in _reviews(entry)}) > 1 for field in JUDGEMENT_FIELDS
        )
    ]
    if not pending:
        print("no unadjudicated judgement disagreements")
        return 0
    print(f"{len(pending)} disagreement(s) require adjudication; enter q to stop\n")
    for entry in pending:
        _print_finding(entry, show_cohort=show_cohort)
        for review in _reviews(entry):
            print(
                f"    reviewer {review['reviewer_id']}: "
                + ", ".join(f"{field}={review[field]}" for field in JUDGEMENT_FIELDS)
            )
        adjudication = _collect_judgement("adjudicator_id", adjudicator_id)
        if adjudication is None:
            print("stopped; completed adjudications remain saved")
            return 0
        entry["adjudication"] = adjudication
        validate(entries)
        save(path, entries)
        print()
    print("all disagreements adjudicated")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("jsonl", type=Path)
    parser.add_argument(
        "--run",
        type=Path,
        help="completed corpus run that produced the sample; required for v2 samples",
    )
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--adjudicate", action="store_true")
    parser.add_argument("--reviewer-id", help="stable non-personal reviewer identifier")
    parser.add_argument(
        "--show-cohort",
        action="store_true",
        help="show cohort during review; hidden by default to reduce expectation bias",
    )
    args = parser.parse_args()

    if args.report and args.adjudicate:
        sys.exit("--report and --adjudicate are mutually exclusive")
    if args.run is not None and _is_within(args.jsonl, args.run):
        sys.exit("label samples and review progress must remain outside the immutable run")
    if not args.jsonl.is_file():
        sys.exit(f"missing {args.jsonl}; run sample_findings.py first")
    try:
        entries = load(args.jsonl)
        _require_v2_entries(entries)
        validate(entries)
        if args.run is None:
            raise ValueError("v2 samples require --run for evidence validation")
        validate_against_run(entries, args.run)
    except ValueError as exc:
        sys.exit(f"invalid label sample: {exc}")
    if args.report:
        return report(entries)
    if not args.reviewer_id or any(character.isspace() for character in args.reviewer_id):
        sys.exit("--reviewer-id is required and may not contain whitespace")
    try:
        if args.adjudicate:
            return adjudication_loop(
                args.jsonl, entries, args.reviewer_id, show_cohort=args.show_cohort
            )
        return label_loop(args.jsonl, entries, args.reviewer_id, show_cohort=args.show_cohort)
    except ValueError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
