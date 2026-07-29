#!/usr/bin/env python3
"""Review sampled findings with blinded, independent, orthogonal judgements."""

from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import os
import random
import re
import stat
import sys
from collections import Counter, defaultdict
from contextlib import suppress
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
REPORT_SCOPES = ("effectiveness", "stress")
EFFECTIVENESS_COHORTS = frozenset({"badged_functional", "unvetted"})
STRESS_COHORTS = frozenset({"stress"})
JUDGEMENT_FIELDS = ("correctness", "applicability", "utility")
REVIEW_FIELDS = (*JUDGEMENT_FIELDS, "root_cause", "verification_mode")
FINDING_REVIEW_SCHEMA_VERSION = 1
FINDING_REVIEW_ROLES = ("primary", "secondary")
FINDING_REVIEW_POLICY = {
    "effectiveness_cohorts": sorted(EFFECTIVENESS_COHORTS),
    "excluded_cohorts": sorted(STRESS_COHORTS),
    "primary_assignment": "complete-layer-b-population",
    "secondary_assignment": "deterministic-second-review-allocation",
    "separate_independent_review_sources": True,
    "other_reviewer_decisions_absent_from_independent_sources": True,
    "cohort_assignment_absent_from_independent_sources": True,
    "independent_adjudication_of_judgement_disagreements": True,
    "conflicted_assignments_reassigned": True,
}
_FINGERPRINT_RE = re.compile(r"^v1:[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REVIEWER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
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
_CONFLICT_DECLARATION_FIELDS = frozenset(
    {
        "scope",
        "no_relevant_authorship_or_contribution",
        "no_close_collaboration_supervision_or_employment",
        "no_financial_conflict",
        "no_personal_conflict",
        "declared_at",
    }
)
_NO_CONFLICT_FIELDS = (
    "no_relevant_authorship_or_contribution",
    "no_close_collaboration_supervision_or_employment",
    "no_financial_conflict",
    "no_personal_conflict",
)
_CONFLICT_SCOPE_FIELDS = frozenset(
    {"repository_ids", "finding_fingerprint_set_sha256"}
)
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


class FindingReviewError(ValueError):
    """An independent or merged finding-review artifact is invalid or unbound."""


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
    observed_fields = set(review)
    conflict_fields = expected_fields | {"conflict_of_interest_declaration"}
    if observed_fields != expected_fields and (
        not adjudication or observed_fields != conflict_fields
    ):
        raise ValueError(f"{context}: review fields do not match the v2 schema")
    identity_field = "adjudicator_id" if adjudication else "reviewer_id"
    identity = review.get(identity_field)
    if not isinstance(identity, str) or not _REVIEWER_ID_RE.fullmatch(identity):
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


def _validate_v2_entry(
    entry: dict[str, Any],
    context: str,
    *,
    require_adjudication_conflict_declaration: bool = False,
) -> None:
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
    if len(reviews) > 2:
        raise ValueError(f"{context}: at most two independent reviews are permitted")

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
        declaration = adjudication.get("conflict_of_interest_declaration")
        if declaration is not None:
            _validate_conflict_declaration(
                declaration,
                context=f"{context}, adjudication",
                expected_scope=_conflict_scope([entry]),
                not_before=max(review_timestamps),
                not_after=adjudicated_at,
            )
        elif require_adjudication_conflict_declaration:
            raise ValueError(
                f"{context}: adjudication requires a conflict-of-interest declaration"
            )


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
        require_current_harness_file(metadata, "scripts/label_findings.py", Path(__file__))
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


_INDEPENDENT_REVIEW_FIELDS = frozenset(
    {
        "finding_review_schema_version",
        "artifact_type",
        "schema_sha256",
        "allocation_sha256",
        "run_binding",
        "review_policy",
        "source_bindings",
        "review_role",
        "reviewer_id",
        "domain_expertise",
        "blinding_declaration",
        "conflict_of_interest_declaration",
        "selection",
        "records",
    }
)
_INDEPENDENT_RECORD_FIELDS = frozenset(
    {
        "source_id",
        "source_entry_number",
        "finding_fingerprint",
        "finding_record_sha256",
        "repo_id",
        "repo_commit",
        "rule_id",
        "category",
        "title",
        "finding_status",
        "finding_confidence",
        "severity",
        "message",
        "locations",
        "suppressed",
        "review",
    }
)
_BLINDING_DECLARATION_FIELDS = frozenset(
    {
        "independent_review",
        "other_reviewer_decisions_not_seen",
        "other_reviewer_source_not_accessed",
        "declared_at",
    }
)
_MERGED_REVIEW_FIELDS = frozenset(
    {
        "finding_review_schema_version",
        "artifact_type",
        "schema_sha256",
        "allocation_sha256",
        "run_binding",
        "review_policy",
        "source_bindings",
        "initial_review_sources",
        "population",
        "sources",
    }
)
_MERGED_SOURCE_FIELDS = frozenset({"source_id", "entries"})
_INITIAL_REVIEW_SOURCE_FIELDS = frozenset(
    {
        "review_role",
        "reviewer_id",
        "sha256",
        "domain_expertise",
        "blinding_declaration",
        "conflict_of_interest_declaration",
    }
)
_ROLE_ORDER = {"primary": 0, "secondary": 1}


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
        raise FindingReviewError(f"cannot canonicalize finding-review evidence: {exc}") from exc
    return hashlib.sha256(encoded).hexdigest()


def _finding_identity(entry: dict[str, Any]) -> dict[str, Any]:
    """Return immutable sample evidence without human decisions."""
    return {
        key: copy.deepcopy(value)
        for key, value in entry.items()
        if key not in {"reviews", "adjudication"}
    }


def _source_identity_sha256(entries: list[dict[str, Any]]) -> str:
    return _canonical_sha256([_finding_identity(entry) for entry in entries])


def _finding_review_schema_sha256() -> str:
    schema = Path(__file__).resolve().parent.parent / "finding-review.schema.json"
    try:
        return str(sha256_file(schema))
    except RunContractError as exc:
        raise FindingReviewError(f"cannot bind finding-review schema: {exc}") from exc


def _conflict_scope(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "repository_ids": sorted({str(record["repo_id"]) for record in records}),
        "finding_fingerprint_set_sha256": _canonical_sha256(
            sorted(str(record["finding_fingerprint"]) for record in records)
        ),
    }


def _validate_conflict_declaration(
    value: object,
    *,
    context: str,
    expected_scope: dict[str, Any],
    not_before: datetime | None,
    not_after: datetime | None,
) -> None:
    if not isinstance(value, dict) or set(value) != _CONFLICT_DECLARATION_FIELDS:
        raise FindingReviewError(
            f"{context} conflict-of-interest declaration fields do not match the schema"
        )
    scope = value.get("scope")
    if not isinstance(scope, dict) or set(scope) != _CONFLICT_SCOPE_FIELDS:
        raise FindingReviewError(
            f"{context} conflict-of-interest scope fields do not match the schema"
        )
    repository_ids = scope.get("repository_ids")
    scope_digest = scope.get("finding_fingerprint_set_sha256")
    if (
        not isinstance(repository_ids, list)
        or not repository_ids
        or any(
            not isinstance(repository_id, str)
            or not _REVIEWER_ID_RE.fullmatch(repository_id)
            for repository_id in repository_ids
        )
        or repository_ids != sorted(set(repository_ids))
        or not isinstance(scope_digest, str)
        or not _SHA256_RE.fullmatch(scope_digest)
    ):
        raise FindingReviewError(f"{context} has an invalid conflict-of-interest scope")
    if scope != expected_scope:
        raise FindingReviewError(
            f"{context} conflict-of-interest scope does not match the assigned artifacts"
        )
    for field in _NO_CONFLICT_FIELDS:
        if value.get(field) is not True:
            raise FindingReviewError(
                f"{context} does not exclude every relevant conflict; "
                "the assignment must be reassigned"
            )
    try:
        declared_at = _validate_timestamp(value.get("declared_at"), context)
    except ValueError as exc:
        raise FindingReviewError(str(exc)) from exc
    if not_before is not None and declared_at < not_before:
        raise FindingReviewError(
            f"{context} conflict-of-interest declaration predates the assignment"
        )
    if not_after is not None and declared_at > not_after:
        raise FindingReviewError(
            f"{context} conflict-of-interest declaration was made after review began"
        )


def _strict_json_object(path: Path, context: str) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise FindingReviewError(f"{context} contains duplicate key {key!r}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise FindingReviewError(f"{context} contains non-finite number {value}")

    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_constant,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FindingReviewError(f"cannot read {context} {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise FindingReviewError(f"{context} must be a JSON object")
    return payload


def _save_review_object(path: Path, payload: dict[str, Any], *, replace: bool) -> None:
    """Atomically save a review object while rejecting unsafe output objects."""
    try:
        if replace:
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise FindingReviewError(
                    f"refusing to replace unsafe finding-review file: {path}"
                )
        elif path.exists() or path.is_symlink():
            raise FindingReviewError(f"refusing to overwrite finding-review file: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        rendered = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            with suppress(OSError):
                temporary.unlink()
            raise
        if not replace and (path.exists() or path.is_symlink()):
            temporary.unlink(missing_ok=True)
            raise FindingReviewError(f"refusing to overwrite finding-review file: {path}")
        os.replace(temporary, path)
    except FindingReviewError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise FindingReviewError(f"cannot save finding-review file {path}: {exc}") from exc


def _review_source_bindings(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    raw = manifest.get("sources")
    if not isinstance(raw, list) or not raw:
        raise FindingReviewError("review allocation has no bound sample sources")
    bindings: list[dict[str, Any]] = []
    for number, source in enumerate(raw, 1):
        if not isinstance(source, dict):
            raise FindingReviewError(f"review allocation source {number} is invalid")
        bindings.append(copy.deepcopy(source))
    return sorted(bindings, key=lambda value: str(value.get("source_id", "")))


def _review_population(
    manifest: dict[str, Any],
    sources: list[tuple[str, str, list[dict[str, Any]]]],
) -> tuple[
    list[tuple[str, int, dict[str, Any]]],
    dict[tuple[str, int, str], dict[str, Any]],
]:
    """Reconstruct the exact effectiveness population from pristine sample files."""
    manifest_sources = {
        str(source.get("source_id")): source
        for source in _review_source_bindings(manifest)
        if isinstance(source, dict)
    }
    if len(manifest_sources) != len(manifest.get("sources", [])):
        raise FindingReviewError("review allocation repeats a sample source identity")
    if {source_id for source_id, _, _ in sources} != set(manifest_sources):
        raise FindingReviewError(
            "review source set does not match the allocation's complete sample-source set"
        )

    population: list[tuple[str, int, dict[str, Any]]] = []
    indexed: dict[tuple[str, int, str], dict[str, Any]] = {}
    fingerprints: set[str] = set()
    for source_id, observed_sha256, entries in sorted(sources, key=lambda value: value[0]):
        try:
            validate(entries)
        except ValueError as exc:
            raise FindingReviewError(f"sample source {source_id} is invalid: {exc}") from exc
        if any(entry.get("reviews") or entry.get("adjudication") is not None for entry in entries):
            raise FindingReviewError(
                f"sample source {source_id} is not pristine; independent review decisions "
                "must live only in separate reviewer files"
            )
        bound = manifest_sources[source_id]
        sample_set = entries[0].get("sample_set")
        expected_source = {
            "initial_source_sha256": observed_sha256,
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
        for field, expected in expected_source.items():
            if bound.get(field) != expected:
                raise FindingReviewError(
                    f"sample source {source_id!r} differs from allocation field {field!r}"
                )
        for entry_number, entry in enumerate(entries, 1):
            cohort = str(entry.get("cohort"))
            if cohort in STRESS_COHORTS:
                continue
            if cohort not in EFFECTIVENESS_COHORTS:
                raise FindingReviewError(
                    f"sample source {source_id!r} has unsupported cohort {cohort!r}"
                )
            fingerprint = str(entry.get("finding_fingerprint"))
            if fingerprint in fingerprints:
                raise FindingReviewError(
                    f"Layer B finding appears in multiple sample sources: {fingerprint}"
                )
            fingerprints.add(fingerprint)
            key = (source_id, entry_number, fingerprint)
            indexed[key] = entry
            population.append((source_id, entry_number, entry))

    population.sort(key=lambda value: (value[0], value[1], str(value[2]["finding_fingerprint"])))
    population_binding = manifest.get("population")
    if not isinstance(population_binding, dict):
        raise FindingReviewError("review allocation has no population binding")
    if population_binding.get("cohorts") != sorted(EFFECTIVENESS_COHORTS):
        raise FindingReviewError("review allocation changes the Layer B effectiveness cohorts")
    if population_binding.get("entry_count") != len(population):
        raise FindingReviewError("review allocation Layer B population count does not match samples")
    expected_digest = _canonical_sha256(sorted(fingerprints))
    if population_binding.get("finding_fingerprint_set_sha256") != expected_digest:
        raise FindingReviewError(
            "review allocation Layer B fingerprint digest does not match samples"
        )

    selection_sets: dict[str, set[tuple[str, int, str]]] = {}
    for selection_name in ("calibration", "second_review"):
        references = manifest.get(selection_name)
        if not isinstance(references, list) or not references:
            raise FindingReviewError(f"review allocation has invalid {selection_name} selection")
        selected: set[tuple[str, int, str]] = set()
        for number, reference in enumerate(references, 1):
            if not isinstance(reference, dict):
                raise FindingReviewError(
                    f"review allocation {selection_name} reference {number} is invalid"
                )
            key = (
                str(reference.get("source_id")),
                int(reference.get("source_entry_number", 0)),
                str(reference.get("finding_fingerprint")),
            )
            selected_entry = indexed.get(key)
            if selected_entry is None or key in selected:
                raise FindingReviewError(
                    f"review allocation {selection_name} contains an absent or duplicate finding"
                )
            if reference.get("cohort") not in EFFECTIVENESS_COHORTS:
                raise FindingReviewError(
                    f"review allocation {selection_name} contains a stress finding"
                )
            expected_reference = {
                "repo_id": selected_entry["repo_id"],
                "cohort": selected_entry["cohort"],
                "rule_id": selected_entry["rule_id"],
                "category": selected_entry.get("category") or "?",
                "finding_status": selected_entry["finding_status"],
            }
            if any(reference.get(field) != value for field, value in expected_reference.items()):
                raise FindingReviewError(
                    f"review allocation {selection_name} reference changes sample evidence"
                )
            selected.add(key)
        selection_sets[selection_name] = selected
    if not selection_sets["calibration"] <= selection_sets["second_review"]:
        raise FindingReviewError(
            "review allocation calibration set is not contained in second review"
        )
    if manifest.get("second_review_count") != len(selection_sets["second_review"]):
        raise FindingReviewError("review allocation second-review count is inconsistent")
    if manifest.get("calibration_count") != len(selection_sets["calibration"]):
        raise FindingReviewError("review allocation calibration count is inconsistent")
    return population, indexed


def _independent_record(
    source_id: str,
    entry_number: int,
    entry: dict[str, Any],
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "source_entry_number": entry_number,
        "finding_fingerprint": entry["finding_fingerprint"],
        "finding_record_sha256": _canonical_sha256(_finding_identity(entry)),
        "repo_id": entry["repo_id"],
        "repo_commit": entry["repo_commit"],
        "rule_id": entry["rule_id"],
        "category": entry.get("category"),
        "title": entry.get("title", ""),
        "finding_status": entry["finding_status"],
        "finding_confidence": entry.get("finding_confidence"),
        "severity": entry.get("severity"),
        "message": entry.get("message", ""),
        "locations": copy.deepcopy(entry.get("locations", [])),
        "suppressed": bool(entry.get("suppressed", False)),
        "review": None,
    }


def initialize_finding_review_source(
    manifest: dict[str, Any],
    sources: list[tuple[str, str, list[dict[str, Any]]]],
    allocation_sha256: str,
    *,
    review_role: str,
    reviewer_id: str,
) -> dict[str, Any]:
    """Create one role-bound reviewer file with no other reviewer decisions."""
    if review_role not in FINDING_REVIEW_ROLES:
        raise FindingReviewError(
            "finding-review role must be one of: " + ", ".join(FINDING_REVIEW_ROLES)
        )
    if not _REVIEWER_ID_RE.fullmatch(reviewer_id):
        raise FindingReviewError("reviewer_id must be a stable non-personal identifier")
    if not _SHA256_RE.fullmatch(allocation_sha256):
        raise FindingReviewError("finding-review allocation SHA-256 is invalid")
    population, indexed = _review_population(manifest, sources)
    if review_role == "primary":
        selected = population
        purpose = "complete-layer-b-population"
    else:
        selected = []
        for reference in manifest["second_review"]:
            key = (
                str(reference["source_id"]),
                int(reference["source_entry_number"]),
                str(reference["finding_fingerprint"]),
            )
            selected.append((key[0], key[1], indexed[key]))
        selected.sort(
            key=lambda value: (value[0], value[1], str(value[2]["finding_fingerprint"]))
        )
        purpose = "deterministic-second-review-allocation"
    records = [
        _independent_record(source_id, entry_number, entry)
        for source_id, entry_number, entry in selected
    ]
    return {
        "finding_review_schema_version": FINDING_REVIEW_SCHEMA_VERSION,
        "artifact_type": "independent",
        "schema_sha256": _finding_review_schema_sha256(),
        "allocation_sha256": allocation_sha256,
        "run_binding": copy.deepcopy(manifest.get("run_binding")),
        "review_policy": copy.deepcopy(FINDING_REVIEW_POLICY),
        "source_bindings": _review_source_bindings(manifest),
        "review_role": review_role,
        "reviewer_id": reviewer_id,
        "domain_expertise": "",
        "blinding_declaration": None,
        "conflict_of_interest_declaration": None,
        "selection": {
            "purpose": purpose,
            "entry_count": len(records),
            "finding_fingerprint_set_sha256": _canonical_sha256(
                sorted(str(record["finding_fingerprint"]) for record in records)
            ),
        },
        "records": records,
    }


def _validate_blinding_declaration(
    value: object,
    *,
    context: str,
    review_times: list[datetime],
) -> None:
    if not isinstance(value, dict) or set(value) != _BLINDING_DECLARATION_FIELDS:
        raise FindingReviewError(f"{context} blinding declaration fields do not match the schema")
    for field in (
        "independent_review",
        "other_reviewer_decisions_not_seen",
        "other_reviewer_source_not_accessed",
    ):
        if value.get(field) is not True:
            raise FindingReviewError(f"{context} blinding declaration must affirm {field}")
    try:
        declared_at = _validate_timestamp(value.get("declared_at"), context)
    except ValueError as exc:
        raise FindingReviewError(str(exc)) from exc
    if review_times and declared_at > min(review_times):
        raise FindingReviewError(
            f"{context} blinding declaration was made after review began"
        )


def validate_independent_finding_review(
    payload: dict[str, Any],
    manifest: dict[str, Any],
    sources: list[tuple[str, str, list[dict[str, Any]]]],
    allocation_sha256: str,
    *,
    require_complete: bool = False,
    review_not_before: datetime | None = None,
) -> dict[str, int]:
    """Validate immutable assignment binding and one reviewer's decisions."""
    if set(payload) != _INDEPENDENT_REVIEW_FIELDS:
        raise FindingReviewError("independent finding-review fields do not match the schema")
    if payload.get("finding_review_schema_version") != FINDING_REVIEW_SCHEMA_VERSION:
        raise FindingReviewError("unsupported finding-review schema")
    if payload.get("artifact_type") != "independent":
        raise FindingReviewError("expected an independent finding-review source")
    role = payload.get("review_role")
    reviewer_id = payload.get("reviewer_id")
    if not isinstance(role, str) or role not in FINDING_REVIEW_ROLES:
        raise FindingReviewError("independent finding review has an invalid reviewer role")
    if not isinstance(reviewer_id, str) or not _REVIEWER_ID_RE.fullmatch(reviewer_id):
        raise FindingReviewError("independent finding review has an invalid reviewer identity")
    expected = initialize_finding_review_source(
        manifest,
        sources,
        allocation_sha256,
        review_role=role,
        reviewer_id=reviewer_id,
    )
    for field in (
        "finding_review_schema_version",
        "artifact_type",
        "schema_sha256",
        "allocation_sha256",
        "run_binding",
        "review_policy",
        "source_bindings",
        "review_role",
        "reviewer_id",
        "selection",
    ):
        if payload.get(field) != expected[field]:
            raise FindingReviewError(
                f"independent finding review changes immutable field {field!r}"
            )
    records = payload.get("records")
    expected_records = expected["records"]
    if not isinstance(records, list) or len(records) != len(expected_records):
        raise FindingReviewError("independent finding review changes its assigned records")
    review_times: list[datetime] = []
    completed = 0
    for number, (record, expected_record) in enumerate(
        zip(records, expected_records, strict=True), 1
    ):
        context = f"independent review record {number}"
        if not isinstance(record, dict) or set(record) != _INDEPENDENT_RECORD_FIELDS:
            raise FindingReviewError(f"{context} fields do not match the schema")
        for field, value in expected_record.items():
            if field != "review" and record.get(field) != value:
                raise FindingReviewError(f"{context} changes immutable finding evidence")
        review = record.get("review")
        if review is None:
            continue
        try:
            reviewed_at = _validate_review(review, context)
        except ValueError as exc:
            raise FindingReviewError(str(exc)) from exc
        if review.get("reviewer_id") != reviewer_id:
            raise FindingReviewError(f"{context} uses a different reviewer identity")
        if review_not_before is not None and reviewed_at < review_not_before:
            raise FindingReviewError(f"{context} predates the completed corpus run")
        review_times.append(reviewed_at)
        completed += 1

    expertise = payload.get("domain_expertise")
    if not isinstance(expertise, str):
        raise FindingReviewError("independent finding review expertise must be a string")
    declaration = payload.get("blinding_declaration")
    conflict_declaration = payload.get("conflict_of_interest_declaration")
    expected_conflict_scope = _conflict_scope(expected_records)
    if review_times:
        if not expertise.strip():
            raise FindingReviewError(
                "independent finding review requires a domain-expertise statement"
            )
        _validate_blinding_declaration(
            declaration,
            context="independent finding review",
            review_times=review_times,
        )
        _validate_conflict_declaration(
            conflict_declaration,
            context="independent finding review",
            expected_scope=expected_conflict_scope,
            not_before=review_not_before,
            not_after=min(review_times),
        )
    elif declaration is not None:
        _validate_blinding_declaration(
            declaration,
            context="independent finding review",
            review_times=[],
        )
    if not review_times and conflict_declaration is not None:
        _validate_conflict_declaration(
            conflict_declaration,
            context="independent finding review",
            expected_scope=expected_conflict_scope,
            not_before=review_not_before,
            not_after=None,
        )
    if require_complete and completed != len(records):
        raise FindingReviewError(
            f"{role} finding review is incomplete: {completed}/{len(records)} records reviewed"
        )
    if require_complete and (
        not expertise.strip() or declaration is None or conflict_declaration is None
    ):
        raise FindingReviewError(
            f"{role} finding review lacks expertise, blinding, or conflict provenance"
        )
    return {"assigned": len(records), "completed": completed}


def merge_independent_finding_reviews(
    independent_reviews: list[dict[str, Any]],
    source_sha256: list[str],
    manifest: dict[str, Any],
    sources: list[tuple[str, str, list[dict[str, Any]]]],
    allocation_sha256: str,
    *,
    review_not_before: datetime | None = None,
) -> dict[str, Any]:
    """Deterministically merge exact primary and secondary reviewer files."""
    if len(independent_reviews) != 2 or len(source_sha256) != 2:
        raise FindingReviewError(
            "finding-review merge requires exactly two independent reviewer files"
        )
    if len(set(source_sha256)) != 2 or any(
        not _SHA256_RE.fullmatch(digest) for digest in source_sha256
    ):
        raise FindingReviewError(
            "finding-review merge requires two distinct reviewer source SHA-256 digests"
        )
    reviewed: list[tuple[str, str, str, dict[str, Any]]] = []
    for payload, digest in zip(independent_reviews, source_sha256, strict=True):
        validate_independent_finding_review(
            payload,
            manifest,
            sources,
            allocation_sha256,
            require_complete=True,
            review_not_before=review_not_before,
        )
        reviewed.append(
            (
                str(payload["review_role"]),
                str(payload["reviewer_id"]),
                digest,
                payload,
            )
        )
    roles = {role for role, _, _, _ in reviewed}
    reviewer_ids = {reviewer_id for _, reviewer_id, _, _ in reviewed}
    if roles != set(FINDING_REVIEW_ROLES):
        raise FindingReviewError(
            "finding-review merge requires one primary and one secondary source"
        )
    if len(reviewer_ids) != 2:
        raise FindingReviewError(
            "primary and secondary finding reviews require distinct reviewer identities"
        )
    reviewed.sort(key=lambda value: (_ROLE_ORDER[value[0]], value[1], value[2]))
    decision_maps = {
        role: {
            str(record["finding_fingerprint"]): copy.deepcopy(record["review"])
            for record in payload["records"]
        }
        for role, _, _, payload in reviewed
    }
    population, _ = _review_population(manifest, sources)
    population_fingerprints = {
        str(entry["finding_fingerprint"]) for _, _, entry in population
    }
    if set(decision_maps["primary"]) != population_fingerprints:
        raise FindingReviewError("primary reviewer source does not cover the full Layer B population")
    secondary_expected = {
        str(reference["finding_fingerprint"]) for reference in manifest["second_review"]
    }
    if set(decision_maps["secondary"]) != secondary_expected:
        raise FindingReviewError(
            "secondary reviewer source does not match the deterministic second-review allocation"
        )

    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for source_id, _, entry in population:
        fingerprint = str(entry["finding_fingerprint"])
        merged_entry = copy.deepcopy(entry)
        merged_entry["reviews"] = [decision_maps["primary"][fingerprint]]
        if fingerprint in decision_maps["secondary"]:
            merged_entry["reviews"].append(decision_maps["secondary"][fingerprint])
        merged_entry["adjudication"] = None
        by_source[source_id].append(merged_entry)
    merged_sources = [
        {"source_id": binding["source_id"], "entries": by_source[binding["source_id"]]}
        for binding in _review_source_bindings(manifest)
    ]
    initial_sources = [
        {
            "review_role": role,
            "reviewer_id": reviewer_id,
            "sha256": digest,
            "domain_expertise": payload["domain_expertise"],
            "blinding_declaration": copy.deepcopy(payload["blinding_declaration"]),
            "conflict_of_interest_declaration": copy.deepcopy(
                payload["conflict_of_interest_declaration"]
            ),
        }
        for role, reviewer_id, digest, payload in reviewed
    ]
    excluded_stress_count = sum(
        int(binding["excluded_stress_entry_count"])
        for binding in _review_source_bindings(manifest)
    )
    return {
        "finding_review_schema_version": FINDING_REVIEW_SCHEMA_VERSION,
        "artifact_type": "merged",
        "schema_sha256": _finding_review_schema_sha256(),
        "allocation_sha256": allocation_sha256,
        "run_binding": copy.deepcopy(manifest.get("run_binding")),
        "review_policy": copy.deepcopy(FINDING_REVIEW_POLICY),
        "source_bindings": _review_source_bindings(manifest),
        "initial_review_sources": initial_sources,
        "population": {
            "cohorts": sorted(EFFECTIVENESS_COHORTS),
            "excluded_cohorts": sorted(STRESS_COHORTS),
            "entry_count": len(population),
            "finding_fingerprint_set_sha256": _canonical_sha256(
                sorted(population_fingerprints)
            ),
            "primary_review_count": len(decision_maps["primary"]),
            "secondary_review_count": len(decision_maps["secondary"]),
            "excluded_stress_entry_count": excluded_stress_count,
        },
        "sources": merged_sources,
    }


def _merged_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    raw_sources = payload.get("sources")
    if not isinstance(raw_sources, list):
        raise FindingReviewError("merged finding review has invalid sources")
    for number, source in enumerate(raw_sources, 1):
        if not isinstance(source, dict) or set(source) != _MERGED_SOURCE_FIELDS:
            raise FindingReviewError(
                f"merged finding-review source {number} fields do not match the schema"
            )
        source_entries = source.get("entries")
        if not isinstance(source_entries, list):
            raise FindingReviewError(
                f"merged finding-review source {number} entries must be a list"
            )
        for entry in source_entries:
            if not isinstance(entry, dict):
                raise FindingReviewError(
                    f"merged finding-review source {number} contains a non-object entry"
                )
            entries.append(entry)
    return entries


def _require_calibration_agreement(
    manifest: dict[str, Any],
    entries_by_fingerprint: dict[str, dict[str, Any]],
) -> None:
    calibration = manifest.get("calibration")
    if not isinstance(calibration, list) or not calibration:
        raise FindingReviewError("review allocation has no calibration selection")
    for field in ("correctness", "applicability"):
        agreed = 0
        compared = 0
        for reference in calibration:
            fingerprint = str(reference["finding_fingerprint"])
            reviews = entries_by_fingerprint[fingerprint]["reviews"]
            if len(reviews) != 2:
                raise FindingReviewError(
                    "calibration selection lacks two independent finding reviews"
                )
            compared += 1
            agreed += reviews[0][field] == reviews[1][field]
        if Fraction(agreed, compared) < Fraction(4, 5):
            raise FindingReviewError(
                f"calibration {field} exact agreement is below 80%: {agreed}/{compared}"
            )


def validate_merged_finding_review(
    payload: dict[str, Any],
    independent_reviews: list[dict[str, Any]],
    source_sha256: list[str],
    manifest: dict[str, Any],
    sources: list[tuple[str, str, list[dict[str, Any]]]],
    allocation_sha256: str,
    *,
    require_complete: bool = False,
    review_not_before: datetime | None = None,
) -> dict[str, int]:
    """Verify merged decisions against exact independent source bytes and role assignments."""
    if set(payload) != _MERGED_REVIEW_FIELDS:
        raise FindingReviewError("merged finding-review fields do not match the schema")
    if payload.get("finding_review_schema_version") != FINDING_REVIEW_SCHEMA_VERSION:
        raise FindingReviewError("unsupported finding-review schema")
    if payload.get("artifact_type") != "merged":
        raise FindingReviewError("expected a merged finding-review artifact")
    expected = merge_independent_finding_reviews(
        independent_reviews,
        source_sha256,
        manifest,
        sources,
        allocation_sha256,
        review_not_before=review_not_before,
    )
    for field in _MERGED_REVIEW_FIELDS - {"sources"}:
        if payload.get(field) != expected[field]:
            raise FindingReviewError(f"merged finding review changes bound field {field!r}")

    observed_sources = payload.get("sources")
    expected_sources = expected["sources"]
    if not isinstance(observed_sources, list) or len(observed_sources) != len(expected_sources):
        raise FindingReviewError("merged finding review changes its bound sample sources")
    entries_by_fingerprint: dict[str, dict[str, Any]] = {}
    pending_adjudications = 0
    adjudicated = 0
    for source_number, (observed_source, expected_source) in enumerate(
        zip(observed_sources, expected_sources, strict=True), 1
    ):
        context = f"merged finding-review source {source_number}"
        if (
            not isinstance(observed_source, dict)
            or set(observed_source) != _MERGED_SOURCE_FIELDS
            or observed_source.get("source_id") != expected_source["source_id"]
        ):
            raise FindingReviewError(f"{context} changes its sample-source binding")
        observed_entries = observed_source.get("entries")
        expected_entries = expected_source["entries"]
        if not isinstance(observed_entries, list) or len(observed_entries) != len(
            expected_entries
        ):
            raise FindingReviewError(f"{context} changes its Layer B entry population")
        for entry_number, (entry, expected_entry) in enumerate(
            zip(observed_entries, expected_entries, strict=True), 1
        ):
            entry_context = f"{context}, entry {entry_number}"
            if not isinstance(entry, dict) or set(entry) != set(expected_entry):
                raise FindingReviewError(f"{entry_context} fields do not match the schema")
            for field, value in expected_entry.items():
                if field != "adjudication" and entry.get(field) != value:
                    raise FindingReviewError(
                        f"{entry_context} changes independent source evidence or decisions"
                    )
            try:
                _validate_v2_entry(
                    entry,
                    entry_context,
                    require_adjudication_conflict_declaration=True,
                )
            except ValueError as exc:
                raise FindingReviewError(str(exc)) from exc
            fingerprint = str(entry["finding_fingerprint"])
            if fingerprint in entries_by_fingerprint:
                raise FindingReviewError(
                    f"merged finding review repeats fingerprint {fingerprint}"
                )
            entries_by_fingerprint[fingerprint] = entry
            reviews = entry["reviews"]
            disagrees = len(reviews) == 2 and any(
                len({review[field] for review in reviews}) > 1
                for field in JUDGEMENT_FIELDS
            )
            if disagrees and entry.get("adjudication") is None:
                pending_adjudications += 1
            if entry.get("adjudication") is not None:
                adjudicated += 1

    expected_population = {
        str(entry["finding_fingerprint"])
        for source in expected_sources
        for entry in source["entries"]
    }
    if set(entries_by_fingerprint) != expected_population:
        raise FindingReviewError("merged finding review does not cover the full Layer B population")
    if require_complete and pending_adjudications:
        raise FindingReviewError(
            f"merged finding review has {pending_adjudications} unadjudicated "
            "judgement disagreement(s)"
        )
    if require_complete:
        _require_calibration_agreement(manifest, entries_by_fingerprint)
    return {
        "population": len(entries_by_fingerprint),
        "second_reviewed": sum(
            len(entry["reviews"]) == 2 for entry in entries_by_fingerprint.values()
        ),
        "adjudicated": adjudicated,
        "pending_adjudications": pending_adjudications,
    }


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


def _report_entries(
    entries: list[dict[str, Any]], report_scope: str | None
) -> tuple[list[dict[str, Any]], str]:
    cohorts = {str(entry["cohort"]) for entry in entries}
    supported = EFFECTIVENESS_COHORTS | STRESS_COHORTS
    unsupported = cohorts - supported
    if unsupported:
        raise ValueError(f"report sample has unsupported cohort(s): {sorted(unsupported)}")
    if report_scope is not None and report_scope not in REPORT_SCOPES:
        raise ValueError("report scope must be one of: " + ", ".join(REPORT_SCOPES))

    has_effectiveness = bool(cohorts & EFFECTIVENESS_COHORTS)
    has_stress = bool(cohorts & STRESS_COHORTS)
    if report_scope is None:
        if has_effectiveness and has_stress:
            raise ValueError(
                "mixed effectiveness and stress sample requires "
                "--report-scope effectiveness or --report-scope stress"
            )
        report_scope = "stress" if has_stress else "effectiveness"

    selected_cohorts = EFFECTIVENESS_COHORTS if report_scope == "effectiveness" else STRESS_COHORTS
    selected = [entry for entry in entries if entry["cohort"] in selected_cohorts]
    if not selected:
        raise ValueError(f"report scope {report_scope!r} selects no findings")
    return selected, report_scope


def report(entries: list[dict[str, Any]], *, report_scope: str | None = None) -> int:
    """Report one cohort role after validating the complete bound sample."""
    validate(entries)
    _require_v2_entries(entries)
    return _report_validated_entries(entries, report_scope=report_scope)


def _report_validated_entries(
    entries: list[dict[str, Any]], *, report_scope: str | None = None
) -> int:
    """Report entries already validated through a stronger aggregate contract."""
    bound_entry_count = len(entries)
    entries, report_scope = _report_entries(entries, report_scope)
    if report_scope == "stress":
        print("report scope: stress (diagnostic-only operational review)")
    else:
        print("report scope: effectiveness (badged_functional + unvetted)")
    if len(entries) != bound_entry_count:
        print(f"scope selection: {len(entries)} of {bound_entry_count} validated bound findings")

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
    if report_scope == "effectiveness":
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
            + _proportion(
                "incorrect among determinate applicable labels", incorrect, len(determinate)
            )
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


def _collect_conflict_declaration(
    expected_scope: dict[str, Any],
    *,
    role: str,
) -> dict[str, Any] | None:
    print(
        "Conflict-of-interest attestation for this exact assignment: confirm no "
        "relevant authorship or contribution; close collaboration, supervision, "
        "or employment; financial conflict; or personal conflict. Do not record "
        "names, employers, relationship details, or a conflict reason here."
    )
    try:
        confirmation = input(
            "type 'yes' to attest no conflict, 'conflict' to require reassignment, "
            "or q to stop> "
        ).strip()
    except EOFError:
        return None
    if confirmation == "q":
        return None
    if confirmation != "yes":
        raise FindingReviewError(
            f"{role} did not attest absence of every relevant conflict; "
            "the assignment must be reassigned"
        )
    return {
        "scope": copy.deepcopy(expected_scope),
        "no_relevant_authorship_or_contribution": True,
        "no_close_collaboration_supervision_or_employment": True,
        "no_financial_conflict": True,
        "no_personal_conflict": True,
        "declared_at": datetime.now(timezone.utc).isoformat(),
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
    allowed_fingerprints: set[str] | None = None,
) -> int:
    if any(entry.get("label_schema_version") != LABEL_SCHEMA_VERSION for entry in entries):
        raise ValueError(
            "legacy v1 samples are report-only; draw a v2 sample for independent review"
        )
    pending = [
        entry
        for entry in entries
        if (
            allowed_fingerprints is None
            or str(entry["finding_fingerprint"]) in allowed_fingerprints
        )
        if reviewer_id not in {str(review["reviewer_id"]) for review in _reviews(entry)}
    ]
    if not pending:
        scope = "selected finding" if allowed_fingerprints is not None else "finding"
        print(f"reviewer {reviewer_id!r} has reviewed every {scope}")
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


def finding_review_source_loop(
    path: Path,
    payload: dict[str, Any],
    manifest: dict[str, Any],
    sources: list[tuple[str, str, list[dict[str, Any]]]],
    allocation_sha256: str,
    *,
    review_not_before: datetime,
    review_set: str = "all",
) -> int:
    """Collect decisions in one role-specific file that contains no peer decisions."""
    validate_independent_finding_review(
        payload,
        manifest,
        sources,
        allocation_sha256,
        review_not_before=review_not_before,
    )
    if not str(payload["domain_expertise"]).strip():
        try:
            expertise = input("domain expertise (required)> ").strip()
        except EOFError:
            print("stopped before review began")
            return 0
        if not expertise:
            raise FindingReviewError("domain expertise is required before review begins")
        payload["domain_expertise"] = expertise
    if payload["blinding_declaration"] is None:
        print(
            "Confirm that this is an independent review, no other reviewer decisions "
            "were seen, and the other reviewer source was not accessed."
        )
        try:
            confirmation = input("type 'yes' to affirm the blinding declaration> ").strip()
        except EOFError:
            print("stopped before review began")
            return 0
        if confirmation != "yes":
            raise FindingReviewError("blinding declaration was not affirmed")
        payload["blinding_declaration"] = {
            "independent_review": True,
            "other_reviewer_decisions_not_seen": True,
            "other_reviewer_source_not_accessed": True,
            "declared_at": datetime.now(timezone.utc).isoformat(),
        }
    if payload["conflict_of_interest_declaration"] is None:
        conflict_declaration = _collect_conflict_declaration(
            _conflict_scope(payload["records"]),
            role=f"{payload['review_role']} reviewer",
        )
        if conflict_declaration is None:
            print("stopped before review began")
            return 0
        payload["conflict_of_interest_declaration"] = conflict_declaration
    validate_independent_finding_review(
        payload,
        manifest,
        sources,
        allocation_sha256,
        review_not_before=review_not_before,
    )
    _save_review_object(path, payload, replace=True)

    if review_set not in {"calibration", "remaining", "all"}:
        raise FindingReviewError("review set must be calibration, remaining, or all")
    calibration_fingerprints = {
        str(reference["finding_fingerprint"]) for reference in manifest["calibration"]
    }
    if review_set == "calibration":
        selected = [
            record
            for record in payload["records"]
            if str(record["finding_fingerprint"]) in calibration_fingerprints
        ]
    elif review_set == "remaining":
        selected = [
            record
            for record in payload["records"]
            if str(record["finding_fingerprint"]) not in calibration_fingerprints
        ]
    else:
        selected = list(payload["records"])
    pending = [record for record in selected if record["review"] is None]
    if not pending:
        print(
            f"{payload['review_role']} reviewer {payload['reviewer_id']!r} "
            f"has completed every {review_set} finding"
        )
        return 0
    print(
        f"{len(pending)} finding(s) pending for the {payload['review_role']} reviewer; "
        "cohort and other reviewer decisions are absent; enter q to stop\n"
    )
    for record in pending:
        _print_finding(record, show_cohort=False)
        decision = _collect_judgement("reviewer_id", str(payload["reviewer_id"]))
        if decision is None:
            print("stopped; completed reviews remain saved")
            return 0
        record["review"] = decision
        validate_independent_finding_review(
            payload,
            manifest,
            sources,
            allocation_sha256,
            review_not_before=review_not_before,
        )
        _save_review_object(path, payload, replace=True)
        print()
    print("all assigned findings reviewed")
    return 0


def validate_finding_review_calibration(
    independent_reviews: list[dict[str, Any]],
    manifest: dict[str, Any],
    sources: list[tuple[str, str, list[dict[str, Any]]]],
    allocation_sha256: str,
    *,
    review_not_before: datetime | None = None,
) -> dict[str, tuple[int, int]]:
    """Require two blinded calibration decisions and the predeclared agreement floor."""
    if len(independent_reviews) != 2:
        raise FindingReviewError("calibration validation requires two reviewer files")
    roles: set[str] = set()
    reviewer_ids: set[str] = set()
    by_role: dict[str, dict[str, dict[str, Any]]] = {}
    for payload in independent_reviews:
        validate_independent_finding_review(
            payload,
            manifest,
            sources,
            allocation_sha256,
            review_not_before=review_not_before,
        )
        role = str(payload["review_role"])
        roles.add(role)
        reviewer_ids.add(str(payload["reviewer_id"]))
        by_role[role] = {
            str(record["finding_fingerprint"]): record
            for record in payload["records"]
        }
    if roles != set(FINDING_REVIEW_ROLES) or len(reviewer_ids) != 2:
        raise FindingReviewError(
            "calibration requires distinct primary and secondary reviewer identities"
        )
    calibration_fingerprints = [
        str(reference["finding_fingerprint"]) for reference in manifest["calibration"]
    ]
    agreements = {field: [0, 0] for field in JUDGEMENT_FIELDS}
    for fingerprint in calibration_fingerprints:
        try:
            primary = by_role["primary"][fingerprint]["review"]
            secondary = by_role["secondary"][fingerprint]["review"]
        except KeyError as exc:  # pragma: no cover - allocation binding validates membership
            raise FindingReviewError(
                f"calibration finding is absent from a reviewer assignment: {fingerprint}"
            ) from exc
        if not isinstance(primary, dict) or not isinstance(secondary, dict):
            raise FindingReviewError(
                "calibration review is incomplete: both reviewers must finish all "
                f"{len(calibration_fingerprints)} records"
            )
        for field in JUDGEMENT_FIELDS:
            agreements[field][1] += 1
            agreements[field][0] += primary[field] == secondary[field]
    for field in ("correctness", "applicability"):
        agreed, compared = agreements[field]
        if Fraction(agreed, compared) < Fraction(4, 5):
            raise FindingReviewError(
                f"calibration {field} exact agreement is below 80%: {agreed}/{compared}"
            )
    return {
        field: (values[0], values[1]) for field, values in agreements.items()
    }


def merged_finding_adjudication_loop(
    path: Path,
    payload: dict[str, Any],
    independent_reviews: list[dict[str, Any]],
    source_sha256: list[str],
    manifest: dict[str, Any],
    sources: list[tuple[str, str, list[dict[str, Any]]]],
    allocation_sha256: str,
    adjudicator_id: str,
    *,
    review_not_before: datetime,
) -> int:
    """Adjudicate only disagreements after exact independent-source verification."""
    if not _REVIEWER_ID_RE.fullmatch(adjudicator_id):
        raise FindingReviewError("adjudicator_id must be a stable non-personal identifier")
    reviewer_ids = {
        str(source["reviewer_id"])
        for source in payload.get("initial_review_sources", [])
        if isinstance(source, dict)
    }
    if adjudicator_id in reviewer_ids:
        raise FindingReviewError("adjudicator must be independent of both reviewers")
    validate_merged_finding_review(
        payload,
        independent_reviews,
        source_sha256,
        manifest,
        sources,
        allocation_sha256,
        review_not_before=review_not_before,
    )
    pending = [
        entry
        for entry in _merged_entries(payload)
        if len(entry["reviews"]) == 2
        and entry.get("adjudication") is None
        and any(
            len({review[field] for review in entry["reviews"]}) > 1
            for field in JUDGEMENT_FIELDS
        )
    ]
    if not pending:
        print("no unadjudicated judgement disagreements")
        return 0
    print(f"{len(pending)} disagreement(s) require independent adjudication; enter q to stop\n")
    for entry in pending:
        _print_finding(entry, show_cohort=False)
        for review in entry["reviews"]:
            print(
                f"    reviewer {review['reviewer_id']}: "
                + ", ".join(f"{field}={review[field]}" for field in JUDGEMENT_FIELDS)
            )
        conflict_declaration = _collect_conflict_declaration(
            _conflict_scope([entry]),
            role=f"adjudicator {adjudicator_id!r}",
        )
        if conflict_declaration is None:
            print("stopped before this adjudication began")
            return 0
        adjudication = _collect_judgement("adjudicator_id", adjudicator_id)
        if adjudication is None:
            print("stopped; completed adjudications remain saved")
            return 0
        adjudication["conflict_of_interest_declaration"] = conflict_declaration
        entry["adjudication"] = adjudication
        validate_merged_finding_review(
            payload,
            independent_reviews,
            source_sha256,
            manifest,
            sources,
            allocation_sha256,
            review_not_before=review_not_before,
        )
        _save_review_object(path, payload, replace=True)
        print()
    print("all finding-review disagreements adjudicated")
    return 0


def report_merged_finding_review(
    payload: dict[str, Any],
    independent_reviews: list[dict[str, Any]],
    source_sha256: list[str],
    manifest: dict[str, Any],
    sources: list[tuple[str, str, list[dict[str, Any]]]],
    allocation_sha256: str,
    *,
    review_not_before: datetime | None = None,
) -> int:
    """Report completed Layer B review without admitting stress records."""
    validate_merged_finding_review(
        payload,
        independent_reviews,
        source_sha256,
        manifest,
        sources,
        allocation_sha256,
        require_complete=True,
        review_not_before=review_not_before,
    )
    entries = _merged_entries(payload)
    if any(str(entry.get("cohort")) in STRESS_COHORTS for entry in entries):
        raise FindingReviewError("effectiveness review contains a stress finding")
    return _report_validated_entries(entries, report_scope="effectiveness")


def _finding_review_context(
    allocation_path: Path,
    sample_paths: list[Path],
    run: Path,
) -> tuple[
    dict[str, Any],
    list[tuple[str, str, list[dict[str, Any]]]],
    str,
    datetime,
]:
    if not sample_paths:
        raise FindingReviewError("the complete set of --sample files is required")
    if not allocation_path.is_file():
        raise FindingReviewError(f"missing review allocation: {allocation_path}")
    if any(_is_within(path, run) for path in [allocation_path, *sample_paths]):
        raise FindingReviewError("review allocation and samples must remain outside the run")
    if __package__:
        from .review_allocation import (
            _load_bound_sources,
            _load_manifest,
            validate_manifest,
        )
    else:
        from review_allocation import (
            _load_bound_sources,
            _load_manifest,
            validate_manifest,
        )

    try:
        manifest = _load_manifest(allocation_path)
        sources, run_binding = _load_bound_sources(
            sample_paths,
            run,
            require_unreviewed=True,
        )
        validate_manifest(manifest, sources, run_binding)
        metadata, _, _ = validate_run_evidence(run)
    except (RunContractError, ValueError) as exc:
        raise FindingReviewError(f"invalid finding-review context: {exc}") from exc
    harness_files = metadata.get("corpus_harness_files")
    frozen_schema_sha256 = (
        harness_files.get("finding-review.schema.json")
        if isinstance(harness_files, dict)
        else None
    )
    if frozen_schema_sha256 != _finding_review_schema_sha256():
        raise FindingReviewError(
            "corpus run does not freeze the current finding-review.schema.json"
        )
    try:
        completed_at = datetime.fromisoformat(
            str(metadata["completed_at"]).replace("Z", "+00:00")
        )
    except (KeyError, ValueError) as exc:  # pragma: no cover - run validation checks it
        raise FindingReviewError("corpus run has an invalid completion timestamp") from exc
    return manifest, sources, sha256_file(allocation_path), completed_at


def _review_artifact_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog=f"{Path(sys.argv[0]).name} {argv[0]}",
        description="Manage separate, blinded finding-review sources.",
    )
    command = argv[0]
    parser.add_argument("--allocation", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--sample", type=Path, action="append", required=True)
    if command == "init-review-source":
        parser.add_argument("--review-role", choices=FINDING_REVIEW_ROLES, required=True)
        parser.add_argument("--reviewer-id", required=True)
        parser.add_argument("--out", type=Path, required=True)
    elif command == "review-source":
        parser.add_argument("--review", type=Path, required=True)
        parser.add_argument(
            "--review-set",
            choices=("calibration", "remaining", "all"),
            default="all",
        )
    elif command == "merge-review-sources":
        parser.add_argument("--review", type=Path, action="append", required=True)
        parser.add_argument("--out", type=Path, required=True)
    elif command == "validate-calibration":
        parser.add_argument("--review", type=Path, action="append", required=True)
    elif command == "validate-review":
        parser.add_argument("--review", type=Path, required=True)
        parser.add_argument("--initial-review", type=Path, action="append", required=True)
        parser.add_argument("--require-complete", action="store_true")
    elif command == "adjudicate-review":
        parser.add_argument("--review", type=Path, required=True)
        parser.add_argument("--initial-review", type=Path, action="append", required=True)
        parser.add_argument("--adjudicator-id", required=True)
    elif command == "report-review":
        parser.add_argument("--review", type=Path, required=True)
        parser.add_argument("--initial-review", type=Path, action="append", required=True)
    else:  # pragma: no cover - dispatch only calls declared commands
        raise FindingReviewError(f"unsupported finding-review command: {command}")
    args = parser.parse_args(argv[1:])

    manifest, sources, allocation_sha256, completed_at = _finding_review_context(
        args.allocation,
        list(args.sample),
        args.run,
    )
    if command == "init-review-source":
        if _is_within(args.out, args.run):
            raise FindingReviewError("finding-review output must remain outside the run")
        payload = initialize_finding_review_source(
            manifest,
            sources,
            allocation_sha256,
            review_role=args.review_role,
            reviewer_id=args.reviewer_id,
        )
        _save_review_object(args.out, payload, replace=False)
        print(
            f"wrote {args.review_role} reviewer source {args.out} with "
            f"{len(payload['records'])} assigned Layer B finding(s); "
            "no human decisions recorded"
        )
        return 0
    if command == "review-source":
        if _is_within(args.review, args.run):
            raise FindingReviewError("finding-review progress must remain outside the run")
        payload = _strict_json_object(args.review, "independent finding review")
        return finding_review_source_loop(
            args.review,
            payload,
            manifest,
            sources,
            allocation_sha256,
            review_not_before=completed_at,
            review_set=args.review_set,
        )

    initial_paths: list[Path]
    if command in {"merge-review-sources", "validate-calibration"}:
        initial_paths = list(args.review)
    else:
        initial_paths = list(args.initial_review)
    if len(initial_paths) != 2:
        raise FindingReviewError("provide exactly two independent reviewer files")
    if any(_is_within(path, args.run) for path in initial_paths):
        raise FindingReviewError("independent reviewer files must remain outside the run")
    independent_reviews = [
        _strict_json_object(path, "independent finding review") for path in initial_paths
    ]
    source_sha256 = [sha256_file(path) for path in initial_paths]
    if command == "validate-calibration":
        agreements = validate_finding_review_calibration(
            independent_reviews,
            manifest,
            sources,
            allocation_sha256,
            review_not_before=completed_at,
        )
        print(
            "valid blinded calibration: "
            + "; ".join(
                f"{field}={agreed}/{compared}"
                for field, (agreed, compared) in agreements.items()
            )
        )
        print(
            "review source SHA-256: "
            + "; ".join(
                f"{path.name}={digest}"
                for path, digest in zip(initial_paths, source_sha256, strict=True)
            )
        )
        return 0
    if command == "merge-review-sources":
        if _is_within(args.out, args.run):
            raise FindingReviewError("merged finding review must remain outside the run")
        merged = merge_independent_finding_reviews(
            independent_reviews,
            source_sha256,
            manifest,
            sources,
            allocation_sha256,
            review_not_before=completed_at,
        )
        _save_review_object(args.out, merged, replace=False)
        print(
            f"merged exact primary and secondary reviewer sources into {args.out}; "
            "no adjudication decisions added"
        )
        return 0

    if _is_within(args.review, args.run):
        raise FindingReviewError("merged finding review must remain outside the run")
    merged = _strict_json_object(args.review, "merged finding review")
    if command == "validate-review":
        summary = validate_merged_finding_review(
            merged,
            independent_reviews,
            source_sha256,
            manifest,
            sources,
            allocation_sha256,
            require_complete=args.require_complete,
            review_not_before=completed_at,
        )
        print(
            "valid merged finding review: "
            f"population={summary['population']} "
            f"second-reviewed={summary['second_reviewed']} "
            f"adjudicated={summary['adjudicated']} "
            f"pending-adjudications={summary['pending_adjudications']}"
        )
        return 0
    if command == "adjudicate-review":
        return merged_finding_adjudication_loop(
            args.review,
            merged,
            independent_reviews,
            source_sha256,
            manifest,
            sources,
            allocation_sha256,
            args.adjudicator_id,
            review_not_before=completed_at,
        )
    return report_merged_finding_review(
        merged,
        independent_reviews,
        source_sha256,
        manifest,
        sources,
        allocation_sha256,
        review_not_before=completed_at,
    )


def main() -> int:
    artifact_commands = {
        "init-review-source",
        "review-source",
        "merge-review-sources",
        "validate-calibration",
        "validate-review",
        "adjudicate-review",
        "report-review",
    }
    if len(sys.argv) > 1 and sys.argv[1] in artifact_commands:
        try:
            return _review_artifact_main(sys.argv[1:])
        except FindingReviewError as exc:
            sys.exit(f"invalid finding review: {exc}")

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("jsonl", type=Path)
    parser.add_argument(
        "--run",
        type=Path,
        help="completed corpus run that produced the sample; required for v2 samples",
    )
    parser.add_argument("--report", action="store_true")
    parser.add_argument(
        "--report-scope",
        choices=REPORT_SCOPES,
        help="report-only cohort role; required when a sample mixes effectiveness and stress",
    )
    parser.add_argument("--adjudicate", action="store_true")
    parser.add_argument("--reviewer-id", help="stable non-personal reviewer identifier")
    parser.add_argument(
        "--show-cohort",
        action="store_true",
        help="show cohort during review; hidden by default to reduce expectation bias",
    )
    parser.add_argument(
        "--allocation",
        type=Path,
        help="run/sample-bound review-allocation manifest used to restrict this review",
    )
    parser.add_argument(
        "--review-set",
        choices=("calibration", "second-review"),
        help="allowlist within --allocation; calibration is included in second-review",
    )
    parser.add_argument(
        "--allocation-source",
        type=Path,
        action="append",
        default=[],
        help="every review file bound by --allocation; repeat for the complete source set",
    )
    args = parser.parse_args()

    if args.report and args.adjudicate:
        sys.exit("--report and --adjudicate are mutually exclusive")
    if args.report_scope and not args.report:
        sys.exit("--report-scope requires --report")
    if (args.allocation is None) != (args.review_set is None):
        sys.exit("--allocation and --review-set must be supplied together")
    if args.allocation is not None and not args.allocation_source:
        sys.exit("--allocation requires every bound file via --allocation-source")
    if args.allocation is None and args.allocation_source:
        sys.exit("--allocation-source requires --allocation")
    if args.allocation is not None and (args.report or args.adjudicate):
        sys.exit("--allocation is only valid for an initial independent review")
    if args.allocation is not None:
        sys.exit(
            "allocation-bound review must use separate reviewer sources; "
            "run init-review-source, review-source, and merge-review-sources"
        )
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
        try:
            return report(entries, report_scope=args.report_scope)
        except ValueError as exc:
            sys.exit(f"cannot report label sample: {exc}")
    if not args.reviewer_id or any(character.isspace() for character in args.reviewer_id):
        sys.exit("--reviewer-id is required and may not contain whitespace")
    try:
        allowed_fingerprints: set[str] | None = None
        if args.allocation is not None:
            if __package__:
                from .review_allocation import allowed_fingerprints_for_source
            else:
                from review_allocation import allowed_fingerprints_for_source

            allowed_fingerprints = allowed_fingerprints_for_source(
                args.allocation,
                args.jsonl,
                entries,
                args.run,
                args.review_set,
                args.allocation_source,
            )
        if args.adjudicate:
            return adjudication_loop(
                args.jsonl, entries, args.reviewer_id, show_cohort=args.show_cohort
            )
        return label_loop(
            args.jsonl,
            entries,
            args.reviewer_id,
            show_cohort=args.show_cohort,
            allowed_fingerprints=allowed_fingerprints,
        )
    except ValueError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
