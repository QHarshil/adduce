#!/usr/bin/env python3
"""Validate the immutable inputs declared for an effectiveness candidate pair."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections import Counter
from typing import Any

PREREGISTRATION_SCHEMA_VERSION = 1
ANALYSIS_SCOPE = "effectiveness"
EXECUTION_MODE = "offline-builtins-only"
CONFIGURATION_MODE = "defaults-only-repository-config-disabled"
ADDUCE_CHECK_MODE = "reviewer"
ENVIRONMENT_POLICY = "minimal-no-host-credentials"
INPUT_POLICY = "clone-root-symlink-containment"
PREREGISTRATION_ANALYSIS_PLAN_PATHS = (
    "ANNOTATION_GUIDE.md",
    "PILOT_PROTOCOL.md",
    "README.md",
    "claim-ground-truth.schema.json",
    "claim-review.schema.json",
    "finding-review.schema.json",
    "generation-audit.schema.json",
    "preregistration.schema.json",
    "review-allocation.schema.json",
    "scripts/audit_sentinel_generation.py",
    "scripts/check_builtin.py",
    "scripts/claim_ground_truth.py",
    "scripts/claim_review.py",
    "scripts/clone_repos.py",
    "scripts/compare_runs.py",
    "scripts/label_findings.py",
    "scripts/preregistration.py",
    "scripts/review_allocation.py",
    "scripts/run_contract.py",
    "scripts/run_validation.py",
    "scripts/sample_findings.py",
    "scripts/summarize.py",
    "scripts/validate_run.py",
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_TOP_LEVEL_FIELDS = {
    "preregistration_schema_version",
    "schema_sha256",
    "protocol_id",
    "candidate_pair",
    "analysis_scope",
    "adduce",
    "analysis_plan",
    "execution_contract",
    "inputs",
}
_ADDUCE_FIELDS = {
    "version",
    "source_tree_sha256",
    "builtin_rule_count",
    "builtin_rule_ids_sha256",
    "dependency_versions_sha256",
}
_ANALYSIS_PLAN_FIELDS = {"sha256", "files"}
_EXECUTION_FIELDS = {
    "adduce_check_mode",
    "timeout_seconds",
    "execution_mode",
    "configuration_mode",
    "environment_policy",
    "input_policy",
    "plugins_enabled",
}
_INPUT_FIELDS = {
    "repos_file_sha256",
    "repository_count",
    "cohort_counts",
    "clone_manifest_sha256",
    "clone_snapshot_set_sha256",
    "claim_ground_truth_sha256",
    "claim_review_schema_sha256",
    "badged_provenance_sha256",
}
_CLONE_SNAPSHOT_FIELDS = (
    "id",
    "status",
    "acquisition_status",
    "requested_sha",
    "resolved_sha",
    "git_tree_sha",
    "worktree_sha256",
    "submodule_state",
    "git_lfs_state",
    "git_lfs_pointer_count",
)


class PreregistrationError(ValueError):
    """A preregistration is malformed or differs from its frozen candidate inputs."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PreregistrationError(f"duplicate JSON field in preregistration input: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise PreregistrationError(f"non-finite JSON number in preregistration input: {value}")


def _load_object(data: bytes, context: str) -> dict[str, Any]:
    try:
        text = data.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreregistrationError(f"{context} is not strict UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise PreregistrationError(f"{context} must be a JSON object")
    return value


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
        raise PreregistrationError(f"cannot canonicalize preregistration input: {exc}") from exc
    return hashlib.sha256(encoded).hexdigest()


def builtin_rule_ids_sha256(rule_ids: list[str]) -> str:
    """Hash the exact ordered built-in rule-ID inventory."""
    if (
        not rule_ids
        or any(not isinstance(rule_id, str) or not rule_id for rule_id in rule_ids)
        or len(rule_ids) != len(set(rule_ids))
    ):
        raise PreregistrationError("built-in rule IDs must be a unique, non-empty string list")
    return _canonical_sha256(rule_ids)


def analysis_plan_identity(analysis_plan_files: dict[str, bytes]) -> dict[str, object]:
    """Hash every prospective protocol, schema, and analysis implementation file."""
    expected = set(PREREGISTRATION_ANALYSIS_PLAN_PATHS)
    if set(analysis_plan_files) != expected:
        raise PreregistrationError(
            "analysis-plan file set differs from the preregistration contract "
            f"(missing={sorted(expected - set(analysis_plan_files))}, "
            f"extra={sorted(set(analysis_plan_files) - expected)})"
        )
    if any(not isinstance(data, bytes) for data in analysis_plan_files.values()):
        raise PreregistrationError("analysis-plan files must be immutable byte snapshots")
    digests = {
        name: hashlib.sha256(analysis_plan_files[name]).hexdigest()
        for name in sorted(analysis_plan_files)
    }
    return {
        "sha256": _canonical_sha256(digests),
        "files": digests,
    }


def clone_snapshot_set_sha256(clone_manifest_data: bytes) -> str:
    """Hash the acquisition states and byte identities for every frozen clone."""
    manifest = _load_object(clone_manifest_data, "clone manifest")
    records = manifest.get("records")
    if not isinstance(records, list) or not records:
        raise PreregistrationError("clone manifest has no acquisition records")
    projection: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for number, record in enumerate(records, 1):
        if not isinstance(record, dict):
            raise PreregistrationError(f"clone manifest record {number} is not an object")
        repo_id = record.get("id")
        if not isinstance(repo_id, str) or not repo_id or repo_id in seen_ids:
            raise PreregistrationError("clone manifest has an invalid or duplicate repository ID")
        seen_ids.add(repo_id)
        projection.append({field: record.get(field) for field in _CLONE_SNAPSHOT_FIELDS})
    projection.sort(key=lambda record: str(record["id"]))
    return _canonical_sha256(projection)


def _inventory_summary(repos_data: bytes) -> tuple[int, dict[str, int]]:
    try:
        text = repos_data.decode("utf-8")
        reader = csv.DictReader(io.StringIO(text, newline=""))
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    except (UnicodeDecodeError, csv.Error) as exc:
        raise PreregistrationError(f"repository inventory is invalid: {exc}") from exc
    if (
        len(fields) != len(set(fields))
        or not {"id", "cohort", "repo_url", "commit_sha"}.issubset(fields)
        or not rows
        or any(None in row or any(value is None for value in row.values()) for row in rows)
    ):
        raise PreregistrationError("repository inventory has an invalid shape")
    ids = [str(row["id"]) for row in rows]
    if any(not repo_id for repo_id in ids) or len(ids) != len(set(ids)):
        raise PreregistrationError("repository inventory has an invalid or duplicate ID")
    cohorts = Counter(str(row["cohort"]) for row in rows)
    if any(not cohort for cohort in cohorts):
        raise PreregistrationError("repository inventory has an empty cohort")
    expected_cohorts = {"badged_functional", "unvetted", "stress"}
    if not set(cohorts).issubset(expected_cohorts):
        raise PreregistrationError("repository inventory has an unsupported cohort")
    return len(rows), {
        cohort: cohorts.get(cohort, 0) for cohort in sorted(expected_cohorts)
    }


def _require_exact_fields(value: object, expected: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        observed = set(value) if isinstance(value, dict) else set()
        raise PreregistrationError(
            f"{context} fields differ from the preregistration schema "
            f"(missing={sorted(expected - observed)}, extra={sorted(observed - expected)})"
        )
    return value


def _require_sha256(value: object, context: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise PreregistrationError(f"{context} must be a lowercase SHA-256")
    return value


def build_preregistration(
    *,
    protocol_id: str,
    candidate_pair: list[str],
    schema_data: bytes,
    repos_data: bytes,
    clone_manifest_data: bytes,
    claim_ground_truth_data: bytes,
    claim_review_schema_data: bytes,
    badged_provenance_data: bytes,
    analysis_plan_files: dict[str, bytes],
    source_identity: dict[str, object],
    timeout_seconds: int,
) -> dict[str, Any]:
    """Build the canonical lock payload for a not-yet-executed candidate pair."""
    if not isinstance(protocol_id, str) or not _SAFE_ID_RE.fullmatch(protocol_id):
        raise PreregistrationError("preregistration has an invalid protocol ID")
    if (
        not isinstance(candidate_pair, list)
        or len(candidate_pair) != 2
        or any(
            not isinstance(value, str) or not _SAFE_ID_RE.fullmatch(value)
            for value in candidate_pair
        )
        or len(set(candidate_pair)) != 2
    ):
        raise PreregistrationError("preregistration requires two distinct candidate run names")
    rule_ids = source_identity.get("builtin_rule_ids")
    if not isinstance(rule_ids, list):
        raise PreregistrationError("analyzer identity has no built-in rule inventory")
    version = source_identity.get("adduce_version")
    if not isinstance(version, str) or not version:
        raise PreregistrationError("analyzer identity has no version")
    source_tree_sha256 = _require_sha256(
        source_identity.get("adduce_source_tree_sha256"),
        "candidate analyzer source tree",
    )
    dependency_versions = source_identity.get("dependency_versions")
    if (
        not isinstance(dependency_versions, dict)
        or not dependency_versions
        or any(
            not isinstance(name, str)
            or not name
            or not isinstance(value, str)
            or not value
            for name, value in dependency_versions.items()
        )
    ):
        raise PreregistrationError("analyzer identity has no exact dependency versions")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or timeout_seconds <= 0
    ):
        raise PreregistrationError("candidate timeout must be a positive integer")
    repository_count, cohort_counts = _inventory_summary(repos_data)
    analysis_plan = analysis_plan_identity(analysis_plan_files)
    return {
        "preregistration_schema_version": PREREGISTRATION_SCHEMA_VERSION,
        "schema_sha256": hashlib.sha256(schema_data).hexdigest(),
        "protocol_id": protocol_id,
        "candidate_pair": list(candidate_pair),
        "analysis_scope": ANALYSIS_SCOPE,
        "adduce": {
            "version": version,
            "source_tree_sha256": source_tree_sha256,
            "builtin_rule_count": len(rule_ids),
            "builtin_rule_ids_sha256": builtin_rule_ids_sha256(rule_ids),
            "dependency_versions_sha256": _canonical_sha256(dependency_versions),
        },
        "analysis_plan": analysis_plan,
        "execution_contract": {
            "adduce_check_mode": ADDUCE_CHECK_MODE,
            "timeout_seconds": timeout_seconds,
            "execution_mode": EXECUTION_MODE,
            "configuration_mode": CONFIGURATION_MODE,
            "environment_policy": ENVIRONMENT_POLICY,
            "input_policy": INPUT_POLICY,
            "plugins_enabled": False,
        },
        "inputs": {
            "repos_file_sha256": hashlib.sha256(repos_data).hexdigest(),
            "repository_count": repository_count,
            "cohort_counts": cohort_counts,
            "clone_manifest_sha256": hashlib.sha256(clone_manifest_data).hexdigest(),
            "clone_snapshot_set_sha256": clone_snapshot_set_sha256(clone_manifest_data),
            "claim_ground_truth_sha256": hashlib.sha256(claim_ground_truth_data).hexdigest(),
            "claim_review_schema_sha256": hashlib.sha256(claim_review_schema_data).hexdigest(),
            "badged_provenance_sha256": hashlib.sha256(badged_provenance_data).hexdigest(),
        },
    }


def validate_preregistration_bytes(
    preregistration_data: bytes,
    *,
    schema_data: bytes,
    repos_data: bytes,
    clone_manifest_data: bytes,
    claim_ground_truth_data: bytes,
    claim_review_schema_data: bytes,
    badged_provenance_data: bytes,
    analysis_plan_files: dict[str, bytes],
    source_identity: dict[str, object],
    candidate_run_name: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Validate one candidate against every frozen preregistration input."""
    payload = _load_object(preregistration_data, "preregistration")
    _require_exact_fields(payload, _TOP_LEVEL_FIELDS, "preregistration")
    if payload.get("preregistration_schema_version") != PREREGISTRATION_SCHEMA_VERSION:
        raise PreregistrationError("unsupported preregistration schema version")
    if payload.get("schema_sha256") != hashlib.sha256(schema_data).hexdigest():
        raise PreregistrationError("preregistration schema SHA-256 differs from the frozen schema")
    protocol_id = payload.get("protocol_id")
    if not isinstance(protocol_id, str) or not _SAFE_ID_RE.fullmatch(protocol_id):
        raise PreregistrationError("preregistration has an invalid protocol ID")
    candidate_pair = payload.get("candidate_pair")
    if (
        not isinstance(candidate_pair, list)
        or len(candidate_pair) != 2
        or any(
            not isinstance(value, str) or not _SAFE_ID_RE.fullmatch(value)
            for value in candidate_pair
        )
        or len(set(candidate_pair)) != 2
    ):
        raise PreregistrationError("preregistration requires two distinct candidate run names")
    if not isinstance(candidate_run_name, str) or not _SAFE_ID_RE.fullmatch(
        candidate_run_name
    ):
        raise PreregistrationError("candidate run name is invalid")
    if candidate_run_name not in candidate_pair:
        raise PreregistrationError(
            f"run name {candidate_run_name!r} is absent from the preregistered candidate pair"
        )
    if payload.get("analysis_scope") != ANALYSIS_SCOPE:
        raise PreregistrationError("preregistration does not describe an effectiveness run")

    adduce = _require_exact_fields(payload.get("adduce"), _ADDUCE_FIELDS, "adduce lock")
    expected = build_preregistration(
        protocol_id=protocol_id,
        candidate_pair=candidate_pair,
        schema_data=schema_data,
        repos_data=repos_data,
        clone_manifest_data=clone_manifest_data,
        claim_ground_truth_data=claim_ground_truth_data,
        claim_review_schema_data=claim_review_schema_data,
        badged_provenance_data=badged_provenance_data,
        analysis_plan_files=analysis_plan_files,
        source_identity=source_identity,
        timeout_seconds=timeout_seconds,
    )
    expected_adduce = expected["adduce"]
    if adduce != expected_adduce:
        raise PreregistrationError("analyzer identity differs from the preregistered candidate")
    _require_sha256(adduce["source_tree_sha256"], "preregistered analyzer source tree")
    _require_sha256(adduce["builtin_rule_ids_sha256"], "preregistered built-in rule inventory")
    _require_sha256(
        adduce["dependency_versions_sha256"],
        "preregistered analyzer dependency versions",
    )

    analysis_plan = _require_exact_fields(
        payload.get("analysis_plan"),
        _ANALYSIS_PLAN_FIELDS,
        "analysis plan",
    )
    if analysis_plan != expected["analysis_plan"]:
        raise PreregistrationError("analysis plan differs from the preregistered candidate")
    _require_sha256(analysis_plan["sha256"], "preregistered analysis plan")
    files = analysis_plan.get("files")
    expected_files = set(PREREGISTRATION_ANALYSIS_PLAN_PATHS)
    if (
        not isinstance(files, dict)
        or set(files) != expected_files
        or any(
            not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest)
            for digest in files.values()
        )
    ):
        raise PreregistrationError("preregistered analysis-plan file map is invalid")

    execution = _require_exact_fields(
        payload.get("execution_contract"), _EXECUTION_FIELDS, "execution contract"
    )
    expected_execution = expected["execution_contract"]
    if execution != expected_execution:
        raise PreregistrationError("execution contract differs from the preregistered candidate")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int):
        raise PreregistrationError("candidate timeout must be an integer")

    inputs = _require_exact_fields(payload.get("inputs"), _INPUT_FIELDS, "input lock")
    expected_inputs = expected["inputs"]
    if inputs != expected_inputs:
        raise PreregistrationError("frozen inputs differ from the preregistered candidate")
    for field, value in inputs.items():
        if field.endswith("_sha256"):
            _require_sha256(value, f"preregistered input {field}")
    return payload
