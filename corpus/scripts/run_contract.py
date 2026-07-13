"""Integrity contract for immutable validation-corpus runs.

The corpus is evidence about one Adduce build inspecting exact repository
snapshots.  A completed run is accepted only when its acquisition record,
combined rows, raw JSON, metadata, and byte-level artifact manifest agree.
This module is deliberately standard-library only.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

RUN_SCHEMA_VERSION = 1
RUN_META_NAME = "run_meta.json"
RUNNING_MARKER = "_RUNNING"
COMPLETE_MARKER = "_RUN_SUCCESS"
RAW_DIRECTORY = "raw_json"
HARNESS_DIRECTORY = "harness"

REQUIRED_HARNESS_PATHS = (
    "ANNOTATION_GUIDE.md",
    "PILOT_PROTOCOL.md",
    "badged-provenance.csv",
    "claim-ground-truth.schema.json",
    "generation-audit.schema.json",
    "scripts/audit_sentinel_generation.py",
    "scripts/check_builtin.py",
    "scripts/claim_ground_truth.py",
    "scripts/clone_repos.py",
    "scripts/compare_runs.py",
    "scripts/label_findings.py",
    "scripts/run_contract.py",
    "scripts/run_validation.py",
    "scripts/sample_findings.py",
    "scripts/summarize.py",
    "scripts/validate_run.py",
)
BADGED_PROVENANCE_FIELDS = (
    "id",
    "commit_sha",
    "paper_title",
    "artifact_result_id",
    "badge_set",
    "evaluation_results_url",
    "artifact_snapshot_url",
    "artifact_appendix_url",
    "artifact_ref_kind",
    "artifact_ref",
    "artifact_ref_url",
    "resolved_commit_sha",
    "resolved_commit_url",
    "retrieved_at_utc",
    "mapping_basis",
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_FINGERPRINT_RE = re.compile(r"^v1:[0-9a-f]{64}$")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_COHORTS = frozenset(
    {"badged_functional", "badged_available", "badged_venue", "unvetted", "stress"}
)
_SUCCESS_CLONE_STATES = frozenset({"cloned", "already-cloned"})
_ACQUISITION_STATES = frozenset({"complete", "partial", "failed"})
_SUCCESS_RUN_STATES = frozenset({"succeeded", "succeeded_with_partial_acquisition"})
_FAILED_RUN_STATES = frozenset({"scanner_crash", "scanner_timeout", "contract_failed"})
_FINDING_STATES = frozenset({"pass", "partial", "fail", "unknown", "not-applicable"})
_REQUIRED_COMBINED_COLUMNS = frozenset(
    {
        "id",
        "cohort",
        "badge_type",
        "repo_url",
        "requested_sha",
        "resolved_sha",
        "worktree_sha256",
        "repository_tree_sha256",
        "input_file_count",
        "input_byte_count",
        "clone_status",
        "acquisition_status",
        "submodule_state",
        "git_lfs_state",
        "git_lfs_pointer_count",
        "run_status",
        "acquisition_failed",
        "score",
        "tier",
        "reviewer_time_bucket",
        "findings_fail",
        "findings_partial",
        "crash",
        "timeout",
        "runtime_seconds",
        "peak_rss_value",
        "peak_rss_unit",
        "peak_rss_source",
        "error",
    }
)


class RunContractError(ValueError):
    """A corpus run is incomplete, inconsistent, or no longer immutable."""


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of *path* without loading it all into memory."""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise RunContractError(f"cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def require_current_harness_file(
    metadata: dict[str, Any], relative_path: str, current_path: Path
) -> str:
    """Require a post-run analysis script to match the run's bound harness copy."""
    harness_files = metadata.get("corpus_harness_files")
    expected = harness_files.get(relative_path) if isinstance(harness_files, dict) else None
    observed = sha256_file(current_path)
    if expected != observed:
        raise RunContractError(f"current {relative_path} differs from the immutable run harness")
    return observed


def write_json(path: Path, payload: Any) -> None:
    """Write deterministic, newline-terminated JSON."""
    try:
        rendered = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
        path.write_text(rendered, encoding="utf-8", newline="\n")
    except (OSError, TypeError, ValueError) as exc:
        raise RunContractError(f"cannot write strict JSON to {path}: {exc}") from exc


def ensure_output_outside(output: Path, protected_roots: list[Path]) -> None:
    """Reject an output path inside an immutable run or acquired clone tree."""
    try:
        resolved_output = output.resolve(strict=False)
    except OSError as exc:
        raise RunContractError(f"cannot resolve output path {output}: {exc}") from exc
    for protected in protected_roots:
        try:
            resolved_protected = protected.resolve(strict=True)
        except OSError as exc:
            raise RunContractError(f"cannot resolve protected input {protected}: {exc}") from exc
        if resolved_output == resolved_protected or resolved_protected in resolved_output.parents:
            raise RunContractError(
                f"output path must be outside immutable input {protected}: {output}"
            )


def finding_fingerprint(repo_id: str, repo_commit: str, finding: dict[str, Any]) -> str:
    """Return the v1 identity for one rule occurrence on one repository commit."""
    locations = finding.get("locations") or []
    try:
        normalized_locations = sorted(
            {
                (
                    str(location.get("path", "")).replace("\\", "/"),
                    -1 if location.get("line") is None else int(location["line"]),
                )
                for location in locations
                if isinstance(location, dict) and location.get("path")
            }
        )
    except (TypeError, ValueError) as exc:
        raise RunContractError(f"finding has an invalid source location: {exc}") from exc
    identity = {
        "fingerprint_schema": 1,
        "repo_id": repo_id,
        "repo_commit": repo_commit,
        "rule_id": finding.get("rule_id"),
        "title": finding.get("title"),
        "locations": normalized_locations,
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return "v1:" + hashlib.sha256(canonical.encode()).hexdigest()


def ensure_new_output_directory(path: Path) -> None:
    """Create a fresh output directory, refusing even an empty existing path."""
    if path.exists() or path.is_symlink():
        raise RunContractError(f"refusing to overwrite existing run directory: {path}")
    path.mkdir(parents=True)
    (path / RUNNING_MARKER).write_text(
        "incomplete corpus run; do not analyze or import\n",
        encoding="utf-8",
        newline="\n",
    )


def _safe_relative_path(value: str) -> PurePosixPath:
    if (
        not value
        or value == "."
        or "\\" in value
        or "\x00" in value
        or _WINDOWS_DRIVE_RE.match(value)
    ):
        raise RunContractError(f"unsafe run artifact path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RunContractError(f"unsafe run artifact path: {value!r}")
    if path.as_posix() != value:
        raise RunContractError(f"run artifact path is not canonical POSIX: {value!r}")
    return path


def _regular_file_bytes(root: Path, relative: PurePosixPath) -> bytes:
    current = root
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            raise RunContractError(f"run artifacts must not be symlinks: {relative.as_posix()}")
    try:
        if not current.is_file():
            raise RunContractError(f"missing run artifact: {relative.as_posix()}")
        return current.read_bytes()
    except OSError as exc:
        raise RunContractError(f"cannot read run artifact {relative.as_posix()}: {exc}") from exc


def _decode_utf8(data: bytes, label: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RunContractError(f"{label} is not valid UTF-8: {exc}") from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RunContractError(f"JSON object contains duplicate key {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise RunContractError(f"JSON contains non-finite numeric constant {value}")


def load_json_object_bytes(data: bytes, label: str) -> dict[str, Any]:
    """Load one strict JSON object, rejecting duplicate keys and non-finite numbers."""
    try:
        payload = json.loads(
            _decode_utf8(data, label),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except RunContractError as exc:
        raise RunContractError(f"cannot read strict JSON from {label}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RunContractError(f"cannot read valid JSON from {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RunContractError(f"expected a JSON object in {label}")
    return payload


def _load_json_bytes(data: bytes, label: str) -> dict[str, Any]:
    return load_json_object_bytes(data, label)


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise RunContractError(f"cannot read {path}: {exc}") from exc
    return load_json_object_bytes(data, str(path))


def artifact_records(run_dir: Path, relative_paths: list[str]) -> list[dict[str, str]]:
    """Build sorted path/digest records for regular files generated by a run."""
    if len(relative_paths) != len(set(relative_paths)):
        raise RunContractError("duplicate paths supplied for the run artifact manifest")
    records: list[dict[str, str]] = []
    for value in sorted(relative_paths):
        relative = _safe_relative_path(value)
        data = _regular_file_bytes(run_dir, relative)
        records.append({"path": relative.as_posix(), "sha256": hashlib.sha256(data).hexdigest()})
    return records


def _parse_timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise RunContractError(f"run metadata requires non-empty {field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RunContractError(f"run metadata has invalid timestamp: {field}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RunContractError(f"run metadata timestamp lacks a UTC offset: {field}")
    return parsed


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RunContractError(f"run metadata requires positive integer {field}")
    return value


def _nonnegative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RunContractError(f"run metadata requires non-negative integer {field}")
    return value


def _validate_resource_observation(value: object, *, unit: str, source: str, context: str) -> None:
    if not isinstance(value, dict) or set(value) != {"available", "value", "unit", "source"}:
        raise RunContractError(f"{context} has an invalid observation schema")
    available = value.get("available")
    observed_value = value.get("value")
    if not isinstance(available, bool):
        raise RunContractError(f"{context} availability must be boolean")
    if available:
        if (
            isinstance(observed_value, bool)
            or not isinstance(observed_value, int)
            or observed_value <= 0
            or value.get("unit") != unit
            or value.get("source") != source
        ):
            raise RunContractError(f"{context} has an invalid available observation")
    elif (
        observed_value is not None
        or value.get("unit") != "unavailable"
        or value.get("source") != "unavailable"
    ):
        raise RunContractError(f"{context} has an invalid unavailable observation")


def _validate_runtime_context(value: object) -> None:
    if not isinstance(value, dict) or set(value) != {
        "logical_cpu",
        "physical_memory",
        "cache_policy",
        "peak_rss_platform",
        "input_measurement_policy",
    }:
        raise RunContractError("run metadata has an invalid runtime-context schema")
    _validate_resource_observation(
        value["logical_cpu"],
        unit="count",
        source="os.cpu_count",
        context="logical CPU context",
    )
    _validate_resource_observation(
        value["physical_memory"],
        unit="bytes",
        source="os.sysconf(SC_PAGE_SIZE*SC_PHYS_PAGES)",
        context="physical-memory context",
    )
    if value.get("cache_policy") != {
        "filesystem_cache": "not-cleared",
        "scanner_process": "fresh-process-per-repository",
        "adduce_application_cache": "disabled-default-offline-path",
    }:
        raise RunContractError("run metadata has an invalid cache policy")
    if not isinstance(value.get("peak_rss_platform"), str) or not value["peak_rss_platform"]:
        raise RunContractError("run metadata has an invalid peak-RSS platform identity")
    if (
        value.get("input_measurement_policy")
        != "adduce-scanned-regular-files-summed-by-reported-size"
    ):
        raise RunContractError("run metadata has an invalid input-measurement policy")


def _validate_metadata(metadata: dict[str, Any]) -> None:
    required_strings = (
        "run_id",
        "adduce_version",
        "adduce_source_tree_sha256",
        "execution_mode",
        "analysis_scope",
        "started_at",
        "completed_at",
        "repos_file_sha256",
        "clone_manifest_sha256",
        "platform",
        "corpus_harness_sha256",
    )
    for field in required_strings:
        if not isinstance(metadata.get(field), str) or not metadata[field]:
            raise RunContractError(f"run metadata requires non-empty {field}")
    for field in (
        "adduce_source_tree_sha256",
        "repos_file_sha256",
        "clone_manifest_sha256",
        "corpus_harness_sha256",
    ):
        if not _SHA256_RE.fullmatch(metadata[field]):
            raise RunContractError(f"run metadata has invalid digest: {field}")
    claim_ground_truth_sha256 = metadata.get("claim_ground_truth_sha256")
    if claim_ground_truth_sha256 is not None and (
        not isinstance(claim_ground_truth_sha256, str)
        or not _SHA256_RE.fullmatch(claim_ground_truth_sha256)
    ):
        raise RunContractError("run metadata has an invalid claim-ground-truth digest")
    analysis_scope = metadata["analysis_scope"]
    if analysis_scope not in {"effectiveness", "operational-only"}:
        raise RunContractError("run metadata has an invalid analysis scope")
    if (analysis_scope == "effectiveness") != (claim_ground_truth_sha256 is not None):
        raise RunContractError("run analysis scope and claim-ground-truth binding disagree")
    if metadata.get("execution_mode") != "offline-builtins-only":
        raise RunContractError("run did not use the offline built-ins-only corpus mode")
    if metadata.get("environment_policy") != "minimal-no-host-credentials":
        raise RunContractError("run did not use the minimal credential-free scanner environment")
    if metadata.get("input_policy") != "clone-root-symlink-containment":
        raise RunContractError("run did not enforce clone-root input containment")
    _validate_runtime_context(metadata.get("runtime_context"))

    started = _parse_timestamp(metadata["started_at"], "started_at")
    completed = _parse_timestamp(metadata["completed_at"], "completed_at")
    if completed < started:
        raise RunContractError("run completion timestamp precedes its start")

    timeout = _positive_int(metadata.get("timeout_seconds"), "timeout_seconds")
    if metadata.get("configuration_mode") != "defaults-only-repository-config-disabled":
        raise RunContractError("run did not use the uniform configuration-free corpus mode")
    rule_ids = metadata.get("builtin_rule_ids")
    if (
        not isinstance(rule_ids, list)
        or not rule_ids
        or any(not isinstance(rule_id, str) or not rule_id for rule_id in rule_ids)
        or len(rule_ids) != len(set(rule_ids))
        or metadata.get("builtin_rule_count") != len(rule_ids)
    ):
        raise RunContractError("run metadata has an invalid built-in rule identity")

    python = metadata.get("python")
    if not isinstance(python, dict) or any(
        not isinstance(python.get(field), str) or not python[field]
        for field in ("version", "implementation")
    ):
        raise RunContractError("run metadata lacks Python implementation identity")
    dependencies = metadata.get("dependency_versions")
    if (
        not isinstance(dependencies, dict)
        or not dependencies
        or any(
            not isinstance(key, str) or not key or not isinstance(value, str) or not value
            for key, value in dependencies.items()
        )
    ):
        raise RunContractError("run metadata lacks dependency identity")
    invocation = metadata.get("invocation")
    if not isinstance(invocation, dict) or not invocation:
        raise RunContractError("run metadata lacks invocation parameters")
    if invocation.get("timeout_seconds") != timeout:
        raise RunContractError("run timeout disagrees with invocation metadata")

    harness_digest = metadata.get("corpus_harness_sha256")
    harness_files = metadata.get("corpus_harness_files")
    if not isinstance(harness_digest, str) or not _SHA256_RE.fullmatch(harness_digest):
        raise RunContractError("run metadata lacks its corpus harness digest")
    if (
        not isinstance(harness_files, dict)
        or not harness_files
        or any(
            not isinstance(name, str)
            or not name
            or not isinstance(digest, str)
            or not _SHA256_RE.fullmatch(digest)
            for name, digest in harness_files.items()
        )
    ):
        raise RunContractError("run metadata lacks corpus harness file identities")
    if set(harness_files) != set(REQUIRED_HARNESS_PATHS):
        raise RunContractError(
            "run metadata corpus harness set is incomplete or unsupported "
            f"(missing={sorted(set(REQUIRED_HARNESS_PATHS) - set(harness_files))}, "
            f"extra={sorted(set(harness_files) - set(REQUIRED_HARNESS_PATHS))})"
        )
    observed_harness = hashlib.sha256()
    for name, digest in sorted(harness_files.items()):
        observed_harness.update(name.encode())
        observed_harness.update(digest.encode())
    if observed_harness.hexdigest() != harness_digest:
        raise RunContractError("corpus harness aggregate digest is inconsistent")

    n_repositories = _nonnegative_int(metadata.get("n_repositories"), "n_repositories")
    n_succeeded = _nonnegative_int(metadata.get("n_succeeded"), "n_succeeded")
    n_crashed = _nonnegative_int(metadata.get("n_crashed"), "n_crashed")
    n_acquisition_failed = _nonnegative_int(
        metadata.get("n_acquisition_failed"), "n_acquisition_failed"
    )
    _nonnegative_int(metadata.get("n_acquisition_partial"), "n_acquisition_partial")
    n_scanner_crashed = _nonnegative_int(metadata.get("n_scanner_crashed"), "n_scanner_crashed")
    n_contract_failed = _nonnegative_int(metadata.get("n_contract_failed"), "n_contract_failed")
    if n_succeeded + n_crashed + n_acquisition_failed != n_repositories:
        raise RunContractError("run metadata repository outcome counts do not add up")
    if n_scanner_crashed + n_contract_failed != n_crashed:
        raise RunContractError("run metadata scanner failure counts do not add up")


def _read_csv(data: bytes, label: str) -> tuple[list[str], list[dict[str, str]]]:
    try:
        reader = csv.DictReader(io.StringIO(_decode_utf8(data, label), newline=""))
        fieldnames = list(reader.fieldnames or [])
        raw_rows = list(reader)
    except csv.Error as exc:
        raise RunContractError(f"cannot parse {label}: {exc}") from exc
    if not fieldnames or any(not field for field in fieldnames):
        raise RunContractError(f"{label} has an empty or missing header")
    if len(fieldnames) != len(set(fieldnames)):
        raise RunContractError(f"{label} has duplicate columns")
    rows: list[dict[str, str]] = []
    for row in raw_rows:
        if None in row or any(value is None for value in row.values()):
            raise RunContractError(f"{label} has a short row or surplus cells")
        rows.append({str(key): str(value) for key, value in row.items()})
    return fieldnames, rows


def _validate_repository_url(value: str, context: str) -> None:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or bool(parsed.query)
        or bool(parsed.fragment)
    ):
        raise RunContractError(
            f"{context} must use credential-free HTTPS without query or fragment data"
        )


def validate_inventory_rows(rows: list[dict[str, str]]) -> None:
    """Validate the frozen repository identities used by acquisition and scans."""
    seen: set[str] = set()
    for index, row in enumerate(rows, 2):
        repo_id = row.get("id", "")
        cohort = row.get("cohort", "")
        commit = row.get("commit_sha", "")
        repo_url = row.get("repo_url", "")
        context = f"repository inventory line {index}"
        if not _SAFE_ID_RE.fullmatch(repo_id) or repo_id in seen:
            raise RunContractError(f"{context} has an unsafe or duplicate repository ID")
        if cohort not in _COHORTS:
            raise RunContractError(f"{context} has an unsupported cohort")
        if not _COMMIT_RE.fullmatch(commit):
            raise RunContractError(f"{context} requires a full lowercase commit pin")
        _validate_repository_url(repo_url, f"{context} URL")
        seen.add(repo_id)


def _parse_bool(value: str, label: str) -> bool:
    if value not in {"True", "False"}:
        raise RunContractError(f"{label} must be exactly True or False")
    return value == "True"


def _parse_nonnegative_number(value: str, label: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise RunContractError(f"{label} must be numeric") from exc
    if parsed < 0 or parsed != parsed or parsed in {float("inf"), float("-inf")}:
        raise RunContractError(f"{label} must be a finite non-negative number")
    return parsed


def _validate_peak_rss_columns(row: dict[str, str], repo_id: str) -> None:
    value = row["peak_rss_value"]
    unit = row["peak_rss_unit"]
    source = row["peak_rss_source"]
    if unit == "unavailable":
        if value or source != "unavailable":
            raise RunContractError(f"combined peak RSS is inconsistent for {repo_id}")
        return
    if unit not in {"bytes", "kibibytes"} or source != "resource.getrusage(RUSAGE_SELF)":
        raise RunContractError(f"combined peak RSS identity is invalid for {repo_id}")
    try:
        observed = int(value)
    except ValueError as exc:
        raise RunContractError(f"combined peak RSS value is invalid for {repo_id}") from exc
    if observed <= 0 or str(observed) != value:
        raise RunContractError(f"combined peak RSS value is invalid for {repo_id}")


def _category_key(name: str) -> str:
    return "cat_" + name.lower().replace(" & ", "_").replace(" ", "_")


def _finite_number(value: object, context: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RunContractError(f"{context} must be numeric")
    number = float(value)
    if not math.isfinite(number) or (minimum is not None and number < minimum):
        raise RunContractError(f"{context} must be a finite number")
    return number


def _exact_keys(value: dict[str, Any], expected: set[str], context: str) -> None:
    if set(value) != expected:
        raise RunContractError(
            f"{context} fields are invalid "
            f"(missing={sorted(expected - set(value))}, extra={sorted(set(value) - expected)})"
        )


def _validate_peak_rss(value: object, repo_id: str, expected_platform: str) -> dict[str, str]:
    context = f"raw JSON peak RSS for {repo_id}"
    if not isinstance(value, dict):
        raise RunContractError(f"{context} must be an object")
    _exact_keys(
        value,
        {"available", "value", "unit", "source", "platform"},
        context,
    )
    available = value.get("available")
    platform_id = value.get("platform")
    if (
        not isinstance(available, bool)
        or not isinstance(platform_id, str)
        or platform_id != expected_platform
    ):
        raise RunContractError(f"{context} has invalid availability or platform identity")
    if not available:
        if (
            value.get("value") is not None
            or value.get("unit") != "unavailable"
            or value.get("source") != "unavailable"
        ):
            raise RunContractError(f"{context} has an invalid unavailable observation")
        return {
            "peak_rss_value": "",
            "peak_rss_unit": "unavailable",
            "peak_rss_source": "unavailable",
        }
    observed = value.get("value")
    expected_unit = (
        "bytes"
        if platform_id == "darwin"
        else "kibibytes"
        if platform_id.startswith("linux")
        else None
    )
    if (
        isinstance(observed, bool)
        or not isinstance(observed, int)
        or observed <= 0
        or expected_unit is None
        or value.get("unit") != expected_unit
        or value.get("source") != "resource.getrusage(RUSAGE_SELF)"
    ):
        raise RunContractError(f"{context} has an invalid available observation")
    return {
        "peak_rss_value": str(observed),
        "peak_rss_unit": expected_unit,
        "peak_rss_source": "resource.getrusage(RUSAGE_SELF)",
    }


def validate_raw_payload(
    payload: dict[str, Any],
    repo_id: str,
    resolved_sha: str,
    version: str,
    source_tree_sha256: str,
    peak_rss_platform: str,
    builtin_rule_ids: set[str],
) -> dict[str, Any]:
    _exact_keys(
        payload,
        {
            "tool",
            "repository",
            "reviewer_time",
            "claims",
            "total",
            "tier",
            "profile",
            "categories",
            "findings",
            "corpus_execution",
        },
        f"raw JSON object for {repo_id}",
    )
    tool = payload.get("tool")
    repository = payload.get("repository")
    if not isinstance(tool, dict) or tool != {"name": "adduce", "version": version}:
        raise RunContractError(f"raw JSON tool identity mismatch for {repo_id}")
    if not isinstance(repository, dict) or repository.get("commit") != resolved_sha:
        raise RunContractError(f"raw JSON commit mismatch for {repo_id}")
    _exact_keys(
        repository,
        {
            "root",
            "commit",
            "frameworks",
            "files_scanned",
            "input_file_count",
            "input_byte_count",
        },
        f"raw JSON repository object for {repo_id}",
    )
    if not isinstance(repository.get("root"), str) or not repository["root"]:
        raise RunContractError(f"raw JSON repository root is invalid for {repo_id}")
    if (
        isinstance(repository.get("files_scanned"), bool)
        or not isinstance(repository.get("files_scanned"), int)
        or repository["files_scanned"] < 0
    ):
        raise RunContractError(f"raw JSON file count is invalid for {repo_id}")
    input_file_count = repository.get("input_file_count")
    input_byte_count = repository.get("input_byte_count")
    if (
        isinstance(input_file_count, bool)
        or not isinstance(input_file_count, int)
        or input_file_count < 0
        or input_file_count != repository["files_scanned"]
        or isinstance(input_byte_count, bool)
        or not isinstance(input_byte_count, int)
        or input_byte_count < 0
    ):
        raise RunContractError(f"raw JSON input-size census is invalid for {repo_id}")
    frameworks = repository.get("frameworks")
    if (
        not isinstance(frameworks, list)
        or any(not isinstance(item, str) or not item for item in frameworks)
        or len(frameworks) != len(set(frameworks))
    ):
        raise RunContractError(f"raw JSON frameworks are invalid for {repo_id}")

    total = _finite_number(payload.get("total"), f"raw JSON score for {repo_id}")
    if not 0 <= total <= 100:
        raise RunContractError(f"raw JSON score is invalid for {repo_id}")
    if not isinstance(payload.get("tier"), str) or not payload["tier"]:
        raise RunContractError(f"raw JSON tier is invalid for {repo_id}")
    if payload.get("profile") != "default":
        raise RunContractError(f"raw JSON profile is invalid for {repo_id}")

    reviewer = payload.get("reviewer_time")
    if not isinstance(reviewer, dict):
        raise RunContractError(f"raw JSON reviewer-time estimate is invalid for {repo_id}")
    _exact_keys(
        reviewer,
        {"low_minutes", "high_minutes", "bucket", "unknown", "factors"},
        f"raw JSON reviewer-time object for {repo_id}",
    )
    if not isinstance(reviewer.get("bucket"), str) or not reviewer["bucket"]:
        raise RunContractError(f"raw JSON reviewer-time estimate is invalid for {repo_id}")
    for field in ("low_minutes", "high_minutes"):
        value = reviewer.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RunContractError(f"raw JSON reviewer-time {field} is invalid for {repo_id}")
    if (
        reviewer["high_minutes"] < reviewer["low_minutes"]
        or not isinstance(reviewer.get("unknown"), bool)
        or not isinstance(reviewer.get("factors"), list)
        or any(not isinstance(factor, str) for factor in reviewer["factors"])
    ):
        raise RunContractError(f"raw JSON reviewer-time range is invalid for {repo_id}")

    findings = payload.get("findings")
    categories = payload.get("categories")
    claims = payload.get("claims")
    if (
        not isinstance(findings, list)
        or not isinstance(categories, list)
        or not isinstance(claims, list)
    ):
        raise RunContractError(f"raw JSON collections are invalid for {repo_id}")
    execution = payload.get("corpus_execution")
    if not isinstance(execution, dict):
        raise RunContractError(f"raw JSON execution policy is invalid for {repo_id}")
    _exact_keys(
        execution,
        {
            "configuration_mode",
            "plugins_enabled",
            "network_policy",
            "process_policy",
            "enforcement_scope",
            "environment_policy",
            "input_policy",
            "adduce_source_tree_sha256",
            "peak_rss",
        },
        f"raw JSON execution policy for {repo_id}",
    )
    expected_execution = {
        "configuration_mode": "defaults-only-repository-config-disabled",
        "plugins_enabled": False,
        "network_policy": "python-audit-socket-deny",
        "process_policy": "python-audit-read-only-git-metadata-only",
        "enforcement_scope": "scanner-regression-guard-not-os-sandbox",
        "environment_policy": "minimal-no-host-credentials",
        "input_policy": "clone-root-symlink-containment",
        "adduce_source_tree_sha256": source_tree_sha256,
    }
    if {key: execution.get(key) for key in expected_execution} != expected_execution:
        raise RunContractError(f"raw JSON execution policy is invalid for {repo_id}")
    peak_rss = _validate_peak_rss(execution.get("peak_rss"), repo_id, peak_rss_platform)
    seen_rules: set[str] = set()
    for finding in findings:
        if not isinstance(finding, dict):
            raise RunContractError(f"raw JSON finding is invalid for {repo_id}")
        _exact_keys(
            finding,
            {
                "rule_id",
                "category",
                "title",
                "status",
                "confidence",
                "severity",
                "message",
                "remediation",
                "weight",
                "locations",
                "fix_command",
                "suppressed",
            },
            f"raw JSON finding for {repo_id}",
        )
        rule_id = finding.get("rule_id")
        if not isinstance(rule_id, str) or not rule_id or rule_id in seen_rules:
            raise RunContractError(f"raw JSON rule identity is invalid for {repo_id}")
        seen_rules.add(rule_id)
        if rule_id not in builtin_rule_ids:
            raise RunContractError(f"raw JSON includes a non-built-in rule for {repo_id}")
        status = finding.get("status")
        if not isinstance(status, str) or status not in _FINDING_STATES:
            raise RunContractError(f"raw JSON finding status is invalid for {repo_id}")
        for field in ("category", "title", "message", "remediation"):
            if not isinstance(finding.get(field), str) or (
                field in {"category", "title"} and not finding[field]
            ):
                raise RunContractError(f"raw JSON finding {field} is invalid for {repo_id}")
        confidence = _finite_number(
            finding.get("confidence"), f"raw JSON finding confidence for {repo_id}"
        )
        if not 0 <= confidence <= 1:
            raise RunContractError(f"raw JSON finding confidence is invalid for {repo_id}")
        if finding.get("severity") not in {"low", "medium", "high"}:
            raise RunContractError(f"raw JSON finding severity is invalid for {repo_id}")
        weight = finding.get("weight")
        if isinstance(weight, bool) or not isinstance(weight, int) or weight <= 0:
            raise RunContractError(f"raw JSON finding weight is invalid for {repo_id}")
        fix_command = finding.get("fix_command")
        if fix_command is not None and not isinstance(fix_command, str):
            raise RunContractError(f"raw JSON finding fix command is invalid for {repo_id}")
        if not isinstance(finding.get("suppressed"), bool):
            raise RunContractError(f"raw JSON finding suppression is invalid for {repo_id}")
        locations = finding.get("locations")
        if not isinstance(locations, list):
            raise RunContractError(f"raw JSON finding locations are invalid for {repo_id}")
        for location in locations:
            if not isinstance(location, dict):
                raise RunContractError(f"raw JSON finding location is invalid for {repo_id}")
            _exact_keys(location, {"path", "line"}, f"raw JSON finding location for {repo_id}")
            path = location.get("path")
            if not isinstance(path, str):
                raise RunContractError(f"raw JSON finding location path is invalid for {repo_id}")
            _safe_relative_path(path)
            line = location.get("line")
            if line is not None and (
                isinstance(line, bool) or not isinstance(line, int) or line <= 0
            ):
                raise RunContractError(f"raw JSON finding location line is invalid for {repo_id}")

    if seen_rules != builtin_rule_ids:
        raise RunContractError(
            f"raw JSON built-in rule census is incomplete for {repo_id} "
            f"(missing={sorted(builtin_rule_ids - seen_rules)}, "
            f"extra={sorted(seen_rules - builtin_rule_ids)})"
        )

    status_values = {"pass": 1.0, "partial": 0.5, "fail": 0.0}
    applicable: dict[str, tuple[float, float]] = {}
    for finding in findings:
        value = 1.0 if finding["suppressed"] else status_values.get(finding["status"])
        if value is None:
            continue
        earned, possible = applicable.get(finding["category"], (0.0, 0.0))
        weight = float(finding["weight"])
        applicable[finding["category"]] = (earned + value * weight, possible + weight)

    category_percentages: dict[str, float] = {}
    observed_categories: set[str] = set()
    weighted_earned = 0.0
    weighted_possible = 0.0
    for category in categories:
        if not isinstance(category, dict):
            raise RunContractError(f"raw JSON category is invalid for {repo_id}")
        _exact_keys(
            category,
            {"category", "earned", "possible", "percentage"},
            f"raw JSON category for {repo_id}",
        )
        if not isinstance(category.get("category"), str) or not category["category"]:
            raise RunContractError(f"raw JSON category is invalid for {repo_id}")
        category_name = category["category"]
        key = _category_key(category_name)
        earned = _finite_number(
            category.get("earned"), f"raw JSON category earned value for {repo_id}", minimum=0
        )
        possible = _finite_number(
            category.get("possible"),
            f"raw JSON category possible value for {repo_id}",
            minimum=0,
        )
        percentage = _finite_number(
            category.get("percentage"), f"raw JSON category percentage for {repo_id}"
        )
        if (
            key in category_percentages
            or not 0 <= percentage <= 100
            or possible <= 0
            or earned > possible
        ):
            raise RunContractError(f"raw JSON category score is invalid for {repo_id}")
        if category_name not in applicable:
            raise RunContractError(
                f"raw JSON reports a category with no applicable rules for {repo_id}"
            )
        finding_earned, finding_possible = applicable[category_name]
        ratio = finding_earned / finding_possible
        expected_earned = round(ratio * possible, 2)
        expected_percentage = round(100.0 * ratio, 1)
        if earned != expected_earned or percentage != expected_percentage:
            raise RunContractError(
                f"raw JSON category score is not supported by its findings for {repo_id}"
            )
        weighted_earned += ratio * possible
        weighted_possible += possible
        observed_categories.add(category_name)
        category_percentages[key] = percentage

    if observed_categories != set(applicable):
        raise RunContractError(
            f"raw JSON category census is incomplete for {repo_id} "
            f"(missing={sorted(set(applicable) - observed_categories)})"
        )
    expected_total = round(
        100.0 * weighted_earned / weighted_possible if weighted_possible else 0.0,
        1,
    )
    if total != expected_total:
        raise RunContractError(
            f"raw JSON total score is not supported by its findings for {repo_id}"
        )
    expected_tier = next(
        name
        for threshold, name in (
            (85.0, "Gold"),
            (70.0, "Silver"),
            (50.0, "Bronze"),
            (0.0, "Needs work"),
        )
        if total >= threshold
    )
    if payload["tier"] != expected_tier:
        raise RunContractError(f"raw JSON tier is inconsistent with its score for {repo_id}")

    seen_claims: set[str] = set()
    if len(claims) > 10:
        raise RunContractError(f"raw JSON exceeds the claim-trail limit for {repo_id}")
    for claim in claims:
        if not isinstance(claim, dict):
            raise RunContractError(f"raw JSON claim trail is invalid for {repo_id}")
        _exact_keys(
            claim,
            {"id", "headline", "status", "inferred", "trail"},
            f"raw JSON claim trail for {repo_id}",
        )
        claim_id = claim.get("id")
        if not isinstance(claim_id, str) or not claim_id or claim_id in seen_claims:
            raise RunContractError(f"raw JSON claim identity is invalid for {repo_id}")
        seen_claims.add(claim_id)
        claim_status = claim.get("status")
        if (
            not isinstance(claim.get("headline"), str)
            or not claim["headline"]
            or not isinstance(claim_status, str)
            or claim_status not in {"supported", "partial", "unlinked"}
            or not isinstance(claim.get("inferred"), bool)
            or not isinstance(claim.get("trail"), list)
        ):
            raise RunContractError(f"raw JSON claim trail is invalid for {repo_id}")
        for entry in claim["trail"]:
            resolved = entry.get("resolved") if isinstance(entry, dict) else "invalid"
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("label"), str)
                or not isinstance(entry.get("value"), str)
                or not isinstance(entry.get("note"), str)
                or (resolved is not None and not isinstance(resolved, bool))
            ):
                raise RunContractError(f"raw JSON claim entry is invalid for {repo_id}")
            _exact_keys(
                entry,
                {"label", "value", "note", "resolved"},
                f"raw JSON claim entry for {repo_id}",
            )
            if not entry["label"] or not entry["value"]:
                raise RunContractError(f"raw JSON claim entry is empty for {repo_id}")

    return {
        "score": total,
        "tier": payload["tier"],
        "reviewer_time_bucket": reviewer["bucket"],
        "findings_fail": sum(f.get("status") == "fail" for f in findings),
        "findings_partial": sum(f.get("status") == "partial" for f in findings),
        "categories": category_percentages,
        "input_file_count": input_file_count,
        "input_byte_count": input_byte_count,
        **peak_rss,
    }


def _validate_raw_payload(
    payload: dict[str, Any],
    repo_id: str,
    resolved_sha: str,
    version: str,
    source_tree_sha256: str,
    peak_rss_platform: str,
    builtin_rule_ids: set[str],
) -> dict[str, Any]:
    """Backward-compatible internal alias for the public boundary validator."""
    return validate_raw_payload(
        payload,
        repo_id,
        resolved_sha,
        version,
        source_tree_sha256,
        peak_rss_platform,
        builtin_rule_ids,
    )


def _validate_clone_manifest(
    payload: dict[str, Any], inventory_sha: str, clone_tool_sha256: str
) -> dict[str, dict[str, Any]]:
    if payload.get("clone_schema_version") != 2:
        raise RunContractError("copied clone manifest has an unsupported schema")
    _parse_timestamp(payload.get("created_at"), "clone manifest created_at")
    if not isinstance(payload.get("repos_file"), str) or not payload["repos_file"]:
        raise RunContractError("copied clone manifest lacks its inventory path")
    if payload.get("repos_file_sha256") != inventory_sha:
        raise RunContractError("copied clone manifest inventory digest is inconsistent")
    if payload.get("clone_tool_sha256") != clone_tool_sha256:
        raise RunContractError("copied clone manifest was produced by a different clone harness")
    records = payload.get("records")
    if not isinstance(records, list):
        raise RunContractError("copied clone manifest records are invalid")
    clones: dict[str, dict[str, Any]] = {}
    for record in records:
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("id"), str)
            or not record["id"]
        ):
            raise RunContractError("copied clone manifest record is invalid")
        repo_id = record["id"]
        if repo_id in clones:
            raise RunContractError("copied clone manifest has duplicate IDs")
        for field in ("cohort", "repo_url", "status"):
            if not isinstance(record.get(field), str) or not record[field]:
                raise RunContractError(f"clone record {repo_id} lacks {field}")
        requested = record.get("requested_sha")
        if requested is not None and not isinstance(requested, str):
            raise RunContractError(f"clone record {repo_id} has invalid requested commit")
        if requested and not _COMMIT_RE.fullmatch(requested):
            raise RunContractError(f"clone record {repo_id} has invalid requested commit")
        error = record.get("error")
        if error is None:
            if record["status"] not in _SUCCESS_CLONE_STATES:
                raise RunContractError(f"clone record {repo_id} has incoherent success state")
            for field in ("resolved_sha", "git_tree_sha"):
                if not isinstance(record.get(field), str) or not _COMMIT_RE.fullmatch(
                    record[field]
                ):
                    raise RunContractError(f"clone record {repo_id} has invalid {field}")
            if not isinstance(record.get("origin_url"), str) or not record["origin_url"]:
                raise RunContractError(f"clone record {repo_id} lacks a resolved origin")
            if record.get("dirty") is not False:
                raise RunContractError(f"clone record {repo_id} is not clean")
            if not isinstance(record.get("worktree_sha256"), str) or not _SHA256_RE.fullmatch(
                record["worktree_sha256"]
            ):
                raise RunContractError(f"clone record {repo_id} lacks a worktree digest")
            if record.get("acquisition_status") not in {"complete", "partial"}:
                raise RunContractError(f"clone record {repo_id} has invalid acquisition state")
            if requested and record["resolved_sha"] != requested:
                raise RunContractError(f"clone record {repo_id} did not resolve its pinned commit")
            canonical_repo = record["repo_url"].rstrip("/").removesuffix(".git")
            canonical_origin = record["origin_url"].rstrip("/").removesuffix(".git")
            if canonical_origin != canonical_repo:
                raise RunContractError(f"clone record {repo_id} has a mismatched origin")
        elif not isinstance(error, str) or not error:
            raise RunContractError(f"clone record {repo_id} has invalid error state")
        elif record.get("acquisition_status") != "failed":
            raise RunContractError(f"clone record {repo_id} has incoherent failure state")
        if record.get("acquisition_status") not in _ACQUISITION_STATES:
            raise RunContractError(f"clone record {repo_id} has invalid acquisition state")
        if not isinstance(record.get("submodule_status"), list) or any(
            not isinstance(line, str) for line in record["submodule_status"]
        ):
            raise RunContractError(f"clone record {repo_id} lacks submodule state")
        if record.get("submodule_state") not in {
            "not_configured",
            "complete",
            "unavailable",
            "conflicted",
            "modified",
            "uninitialized",
        }:
            raise RunContractError(f"clone record {repo_id} has invalid submodule state")
        if record.get("git_lfs_state") not in {"no_pointers", "pointers_present"}:
            raise RunContractError(f"clone record {repo_id} has invalid Git LFS state")
        lfs_count = record.get("git_lfs_pointer_count")
        if lfs_count is not None and (
            isinstance(lfs_count, bool) or not isinstance(lfs_count, int) or lfs_count < 0
        ):
            raise RunContractError(f"clone record {repo_id} has invalid Git LFS state")
        lfs_sample = record.get("git_lfs_paths_sample")
        if not isinstance(lfs_sample, list) or any(
            not isinstance(path, str) for path in lfs_sample
        ):
            raise RunContractError(f"clone record {repo_id} lacks its Git LFS sample")
        if lfs_count is not None and len(lfs_sample) > lfs_count:
            raise RunContractError(f"clone record {repo_id} has an incoherent Git LFS sample")
        if record.get("git_lfs_state") == "pointers_present" and not lfs_count:
            raise RunContractError(f"clone record {repo_id} has an incoherent Git LFS state")
        if record.get("git_lfs_state") == "no_pointers" and lfs_count != 0:
            raise RunContractError(f"clone record {repo_id} has an incoherent Git LFS state")
        partial_expected = (
            record.get("submodule_state") not in {"not_configured", "complete"}
            or record.get("git_lfs_state") == "pointers_present"
        )
        if error is None and (record.get("acquisition_status") == "partial") != partial_expected:
            raise RunContractError(f"clone record {repo_id} has an incoherent partial state")
        clones[repo_id] = record
    return clones


def _validate_badged_provenance(data: bytes, inventory_rows: list[dict[str, str]]) -> None:
    fields, rows = _read_csv(data, f"{HARNESS_DIRECTORY}/badged-provenance.csv")
    if fields != list(BADGED_PROVENANCE_FIELDS):
        raise RunContractError("badged provenance has an unsupported header")
    expected = {row["id"]: row for row in inventory_rows if row["cohort"].startswith("badged_")}
    observed: dict[str, dict[str, str]] = {}
    result_ids: set[str] = set()
    for row in rows:
        repo_id = row["id"]
        if repo_id in observed or repo_id not in expected:
            raise RunContractError("badged provenance has an extra or duplicate repository ID")
        if row["commit_sha"] != expected[repo_id]["commit_sha"]:
            raise RunContractError(f"badged provenance commit mismatch for {repo_id}")
        if row["resolved_commit_sha"] != expected[repo_id]["commit_sha"]:
            raise RunContractError(f"badged provenance resolved commit mismatch for {repo_id}")
        if not row["paper_title"].strip() or not row["artifact_result_id"].strip():
            raise RunContractError(f"badged provenance lacks paper/result identity for {repo_id}")
        if row["artifact_result_id"] in result_ids:
            raise RunContractError("badged provenance artifact result IDs must be unique")
        result_ids.add(row["artifact_result_id"])
        if not row["badge_set"].strip() or row["badge_set"] != expected[repo_id].get(
            "badge_type", ""
        ):
            raise RunContractError(f"badged provenance badge set mismatch for {repo_id}")
        for field in (
            "evaluation_results_url",
            "artifact_snapshot_url",
            "artifact_appendix_url",
            "artifact_ref_url",
            "resolved_commit_url",
        ):
            _validate_repository_url(row[field], f"badged provenance {field} for {repo_id}")
        expected_commit_url = (
            expected[repo_id]["repo_url"].rstrip("/").removesuffix(".git")
            + "/commit/"
            + expected[repo_id]["commit_sha"]
        )
        if row["resolved_commit_url"] != expected_commit_url:
            raise RunContractError(f"badged provenance commit URL mismatch for {repo_id}")
        if (
            row["artifact_ref_kind"] not in {"commit", "tag", "release"}
            or not row["artifact_ref"].strip()
        ):
            raise RunContractError(f"badged provenance artifact ref is invalid for {repo_id}")
        if row["artifact_ref_kind"] == "commit" and row["artifact_ref"] != row["commit_sha"]:
            raise RunContractError(f"badged provenance commit ref mismatch for {repo_id}")
        retrieved_at = row["retrieved_at_utc"]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", retrieved_at):
            raise RunContractError(f"badged provenance retrieval time is not UTC for {repo_id}")
        _parse_timestamp(retrieved_at, f"badged provenance retrieved_at_utc for {repo_id}")
        if not row["mapping_basis"].strip():
            raise RunContractError(f"badged provenance lacks a mapping basis for {repo_id}")
        observed[repo_id] = row
    if set(observed) != set(expected):
        raise RunContractError(
            "badged provenance does not cover every badged inventory row "
            f"(missing={sorted(set(expected) - set(observed))})"
        )


def validate_badged_provenance_bytes(data: bytes, inventory_rows: list[dict[str, str]]) -> None:
    """Validate the frozen external badge mapping against the inventory."""
    _validate_badged_provenance(data, inventory_rows)


def _validate_claim_truth_copy(
    payload: dict[str, Any],
    metadata: dict[str, Any],
    inventory: dict[str, dict[str, str]],
) -> None:
    try:
        if __package__:
            from .claim_ground_truth import (  # noqa: PLC0415
                ClaimGroundTruthError,
                validate_ground_truth_structure,
            )
        else:
            from claim_ground_truth import (  # noqa: PLC0415
                ClaimGroundTruthError,
                validate_ground_truth_structure,
            )
        validate_ground_truth_structure(payload, require_verified=True)
    except ClaimGroundTruthError as exc:
        raise RunContractError(f"copied claim ground truth violates its schema: {exc}") from exc
    expected_fields = {
        "claim_ground_truth_schema_version",
        "corpus_inventory_sha256",
        "clone_manifest_sha256",
        "frozen_at",
        "claims",
        "unavailable_repositories",
    }
    _exact_keys(payload, expected_fields, "copied claim ground truth")
    if payload.get("claim_ground_truth_schema_version") != 1:
        raise RunContractError("copied claim ground truth has an unsupported schema")
    if payload.get("corpus_inventory_sha256") != metadata["repos_file_sha256"]:
        raise RunContractError("copied claim ground truth targets a different inventory")
    if payload.get("clone_manifest_sha256") != metadata["clone_manifest_sha256"]:
        raise RunContractError("copied claim ground truth targets a different acquisition manifest")
    frozen_at = _parse_timestamp(payload.get("frozen_at"), "claim ground truth frozen_at")
    started_at = _parse_timestamp(metadata["started_at"], "started_at")
    if frozen_at >= started_at:
        raise RunContractError("claim ground truth was not frozen before the run started")

    claims = payload.get("claims")
    unavailable = payload.get("unavailable_repositories")
    if not isinstance(claims, list) or not isinstance(unavailable, list):
        raise RunContractError("copied claim ground truth has invalid repository records")
    covered: set[str] = set()
    for claim in claims:
        if not isinstance(claim, dict):
            raise RunContractError("copied claim ground truth contains an invalid claim")
        repo_id = claim.get("repo_id")
        if not isinstance(repo_id, str) or repo_id not in inventory or repo_id in covered:
            raise RunContractError(
                "copied claim ground truth has an unknown or duplicate repository"
            )
        if inventory[repo_id]["cohort"] == "stress":
            raise RunContractError("copied claim ground truth includes a stress repository")
        if claim.get("repo_commit") != inventory[repo_id]["commit_sha"]:
            raise RunContractError(f"copied claim ground truth commit mismatch for {repo_id}")
        match = claim.get("adduce_match")
        if (
            not isinstance(match, dict)
            or not isinstance(match.get("headline_contains"), str)
            or not match["headline_contains"]
        ):
            raise RunContractError(
                f"copied claim ground truth lacks content identity for {repo_id}"
            )
        review = claim.get("ground_truth_review")
        if not isinstance(review, dict) or not all(
            isinstance(review.get(field), str) and review[field]
            for field in ("prepared_by", "prepared_at", "verified_by", "verified_at")
        ):
            raise RunContractError(
                f"copied claim ground truth lacks independent review for {repo_id}"
            )
        covered.add(repo_id)
    for entry in unavailable:
        if not isinstance(entry, dict):
            raise RunContractError(
                "copied claim ground truth contains an invalid unavailable record"
            )
        repo_id = entry.get("repo_id")
        if not isinstance(repo_id, str) or repo_id not in inventory or repo_id in covered:
            raise RunContractError(
                "copied claim ground truth has an unknown or duplicate repository"
            )
        if inventory[repo_id]["cohort"] == "stress":
            raise RunContractError("copied claim ground truth includes a stress repository")
        if entry.get("repo_commit") != inventory[repo_id]["commit_sha"]:
            raise RunContractError(f"copied claim ground truth commit mismatch for {repo_id}")
        if entry.get("acquisition_status") != "failed":
            raise RunContractError(
                f"copied unavailable record is not an acquisition failure: {repo_id}"
            )
        covered.add(repo_id)
    expected_coverage = {repo_id for repo_id, row in inventory.items() if row["cohort"] != "stress"}
    if covered != expected_coverage:
        raise RunContractError(
            "copied claim ground truth does not cover every labelled repository "
            f"(missing={sorted(expected_coverage - covered)}, "
            f"extra={sorted(covered - expected_coverage)})"
        )


def _validate_input_copies(
    metadata: dict[str, Any], combined_rows: list[dict[str, str]], artifacts: dict[str, bytes]
) -> None:
    repos_data = artifacts["inputs/repos.csv"]
    clones_data = artifacts["inputs/clones_manifest.json"]
    if hashlib.sha256(repos_data).hexdigest() != metadata["repos_file_sha256"]:
        raise RunContractError("copied repository inventory does not match run metadata")
    if hashlib.sha256(clones_data).hexdigest() != metadata["clone_manifest_sha256"]:
        raise RunContractError("copied clone manifest does not match run metadata")
    claims_digest = metadata.get("claim_ground_truth_sha256")
    claims_data = artifacts.get("inputs/claim_ground_truth.json")
    if claims_digest is None and claims_data is not None:
        raise RunContractError("run contains unbound claim ground truth")
    if claims_digest is not None and (
        claims_data is None or hashlib.sha256(claims_data).hexdigest() != claims_digest
    ):
        raise RunContractError("copied claim ground truth does not match run metadata")

    inventory_fields, inventory_rows = _read_csv(repos_data, "inputs/repos.csv")
    required = {"id", "cohort", "repo_url", "commit_sha"}
    if not required.issubset(inventory_fields):
        raise RunContractError("copied inventory lacks required columns")
    validate_inventory_rows(inventory_rows)
    inventory = {row["id"]: row for row in inventory_rows}
    combined = {row["id"]: row for row in combined_rows}
    if not inventory or len(inventory) != len(inventory_rows) or set(inventory) != set(combined):
        raise RunContractError("copied inventory IDs do not match combined.csv")

    clone_payload = _load_json_bytes(clones_data, "inputs/clones_manifest.json")
    clone_tool_digest = metadata["corpus_harness_files"]["scripts/clone_repos.py"]
    clones = _validate_clone_manifest(
        clone_payload, metadata["repos_file_sha256"], clone_tool_digest
    )
    if set(clones) != set(combined):
        raise RunContractError("copied clone manifest IDs do not match combined.csv")

    for repo_id, row in combined.items():
        source = inventory[repo_id]
        clone = clones[repo_id]
        if (
            clone["cohort"] != source["cohort"]
            or clone["repo_url"] != source["repo_url"]
            or (clone.get("requested_sha") or "") != source["commit_sha"]
        ):
            raise RunContractError(f"clone provenance mismatch for {repo_id}")
        expected = {
            "cohort": source["cohort"],
            "badge_type": source.get("badge_type", ""),
            "repo_url": source["repo_url"],
            "requested_sha": source["commit_sha"],
            "resolved_sha": clone.get("resolved_sha") or "",
            "clone_status": clone["status"],
            "worktree_sha256": clone.get("worktree_sha256") or "",
            "acquisition_status": clone["acquisition_status"],
            "submodule_state": clone["submodule_state"],
            "git_lfs_state": clone["git_lfs_state"],
            "git_lfs_pointer_count": (
                ""
                if clone.get("git_lfs_pointer_count") is None
                else str(clone["git_lfs_pointer_count"])
            ),
        }
        observed = {field: row[field] for field in expected}
        if observed != expected:
            raise RunContractError(f"input provenance mismatch for {repo_id}")

    provenance_data = artifacts[f"{HARNESS_DIRECTORY}/badged-provenance.csv"]
    _validate_badged_provenance(provenance_data, inventory_rows)
    if claims_data is not None:
        claim_payload = load_json_object_bytes(claims_data, "inputs/claim_ground_truth.json")
        _validate_claim_truth_copy(claim_payload, metadata, inventory)


def _validate_combined_rows(
    metadata: dict[str, Any], artifacts: dict[str, bytes]
) -> list[dict[str, str]]:
    fieldnames, rows = _read_csv(artifacts["combined.csv"], "combined.csv")
    if not _REQUIRED_COMBINED_COLUMNS.issubset(fieldnames):
        missing = sorted(_REQUIRED_COMBINED_COLUMNS - set(fieldnames))
        raise RunContractError(f"combined.csv lacks required columns: {missing}")
    unknown_columns = {
        field
        for field in fieldnames
        if field not in _REQUIRED_COMBINED_COLUMNS and not field.startswith("cat_")
    }
    if unknown_columns:
        raise RunContractError(f"combined.csv has unsupported columns: {sorted(unknown_columns)}")

    expected_count = metadata["n_repositories"]
    if expected_count != len(rows):
        raise RunContractError(
            f"combined row count {len(rows)} does not match metadata {expected_count!r}"
        )
    ids = [row["id"] for row in rows]
    if any(not value for value in ids) or len(ids) != len(set(ids)):
        raise RunContractError("combined.csv repository IDs must be non-empty and unique")

    successful_ids: set[str] = set()
    observed_category_columns: set[str] = set()
    crashed_count = 0
    acquisition_failed_count = 0
    acquisition_partial_count = 0
    scanner_crashed_count = 0
    contract_failed_count = 0
    for row in rows:
        repo_id = row["id"]
        crashed = _parse_bool(row["crash"], f"combined.csv crash for {repo_id}")
        timed_out = _parse_bool(row["timeout"], f"combined.csv timeout for {repo_id}")
        acquisition_failed = _parse_bool(
            row["acquisition_failed"], f"combined.csv acquisition_failed for {repo_id}"
        )
        acquisition_status = row["acquisition_status"]
        run_status = row["run_status"]
        _validate_peak_rss_columns(row, repo_id)
        if acquisition_status not in _ACQUISITION_STATES:
            raise RunContractError(f"invalid acquisition status for {repo_id}")
        if acquisition_status == "partial":
            acquisition_partial_count += 1
        if timed_out and not crashed:
            raise RunContractError(f"timeout row {repo_id} is not marked as a crash")
        resolved_sha = row["resolved_sha"]
        if resolved_sha and not _COMMIT_RE.fullmatch(resolved_sha):
            raise RunContractError(f"invalid resolved commit for {repo_id}: {resolved_sha!r}")

        category_fields = {field for field in fieldnames if field.startswith("cat_")}
        summary_fields = {
            "score",
            "tier",
            "reviewer_time_bucket",
            "findings_fail",
            "findings_partial",
            "input_file_count",
            "input_byte_count",
            *category_fields,
        }
        if acquisition_failed:
            acquisition_failed_count += 1
            if (
                acquisition_status != "failed"
                or run_status != "acquisition_failed"
                or crashed
                or timed_out
                or not row["error"]
            ):
                raise RunContractError(f"acquisition failure state is inconsistent for {repo_id}")
            if any(row[field] for field in summary_fields):
                raise RunContractError(f"acquisition failure row {repo_id} contains scan results")
            if row["runtime_seconds"] or row["repository_tree_sha256"]:
                raise RunContractError(
                    f"acquisition failure row {repo_id} claims scanner execution"
                )
            if row["peak_rss_unit"] != "unavailable":
                raise RunContractError(f"acquisition failure row {repo_id} claims peak RSS")
            continue

        if acquisition_status == "failed" or run_status == "acquisition_failed":
            raise RunContractError(f"acquisition state is inconsistent for {repo_id}")
        if not _SHA256_RE.fullmatch(row["worktree_sha256"]):
            raise RunContractError(f"acquired row {repo_id} lacks its acquisition byte digest")
        if not _SHA256_RE.fullmatch(row["repository_tree_sha256"]):
            raise RunContractError(f"acquired row {repo_id} lacks its scan-time byte digest")
        if row["worktree_sha256"] != row["repository_tree_sha256"]:
            raise RunContractError(f"acquisition and scan byte digests disagree for {repo_id}")
        _parse_nonnegative_number(row["runtime_seconds"], f"runtime for {repo_id}")

        if crashed:
            crashed_count += 1
            if run_status not in _FAILED_RUN_STATES or not row["error"]:
                raise RunContractError(f"crash row {repo_id} lacks an error")
            if timed_out != (run_status == "scanner_timeout"):
                raise RunContractError(f"timeout state is inconsistent for {repo_id}")
            if run_status in {"scanner_crash", "scanner_timeout"}:
                scanner_crashed_count += 1
            else:
                contract_failed_count += 1
            if any(row[field] for field in summary_fields):
                raise RunContractError(f"crash row {repo_id} contains successful scan results")
            if row["peak_rss_unit"] != "unavailable":
                raise RunContractError(f"crash row {repo_id} claims peak RSS")
            continue

        expected_success_status = (
            "succeeded_with_partial_acquisition" if acquisition_status == "partial" else "succeeded"
        )
        if timed_out or row["error"] or run_status != expected_success_status:
            raise RunContractError(f"successful row {repo_id} has failure state")
        if not _COMMIT_RE.fullmatch(resolved_sha):
            raise RunContractError(f"successful row {repo_id} has no full resolved commit")
        for field in ("input_file_count", "input_byte_count"):
            try:
                observed_input = int(row[field])
            except ValueError as exc:
                raise RunContractError(f"successful row {repo_id} has invalid {field}") from exc
            if observed_input < 0 or str(observed_input) != row[field]:
                raise RunContractError(f"successful row {repo_id} has invalid {field}")
        successful_ids.add(repo_id)

        relative = f"{RAW_DIRECTORY}/{repo_id}.json"
        if relative not in artifacts:
            raise RunContractError(f"successful row {repo_id} has no hashed raw JSON")
        payload = _load_json_bytes(artifacts[relative], relative)
        expected = _validate_raw_payload(
            payload,
            repo_id,
            resolved_sha,
            metadata["adduce_version"],
            metadata["adduce_source_tree_sha256"],
            metadata["runtime_context"]["peak_rss_platform"],
            set(metadata["builtin_rule_ids"]),
        )
        if _parse_nonnegative_number(row["score"], f"score for {repo_id}") != expected["score"]:
            raise RunContractError(f"combined score disagrees with raw JSON for {repo_id}")
        if (
            row["tier"] != expected["tier"]
            or row["reviewer_time_bucket"] != expected["reviewer_time_bucket"]
        ):
            raise RunContractError(f"combined summary disagrees with raw JSON for {repo_id}")
        for field in ("findings_fail", "findings_partial"):
            try:
                observed = int(row[field])
            except ValueError as exc:
                raise RunContractError(f"combined {field} is invalid for {repo_id}") from exc
            if str(observed) != row[field] or observed != expected[field]:
                raise RunContractError(f"combined {field} disagrees with raw JSON for {repo_id}")
        for field in (
            "input_file_count",
            "input_byte_count",
            "peak_rss_value",
            "peak_rss_unit",
            "peak_rss_source",
        ):
            if row[field] != str(expected[field]):
                raise RunContractError(f"combined {field} disagrees with raw JSON for {repo_id}")
        expected_categories = expected["categories"]
        observed_category_columns.update(expected_categories)
        for field in category_fields:
            value = row[field]
            if field in expected_categories:
                if (
                    _parse_nonnegative_number(value, f"{field} for {repo_id}")
                    != expected_categories[field]
                ):
                    raise RunContractError(
                        f"combined category disagrees with raw JSON for {repo_id}"
                    )
            elif value:
                raise RunContractError(
                    f"combined row has a category absent from raw JSON for {repo_id}"
                )

    category_columns = {field for field in fieldnames if field.startswith("cat_")}
    if category_columns != observed_category_columns:
        raise RunContractError("combined category columns do not match successful raw JSON")
    expected_raw = {f"{RAW_DIRECTORY}/{repo_id}.json" for repo_id in successful_ids}
    observed_raw = {path for path in artifacts if path.startswith(f"{RAW_DIRECTORY}/")}
    if observed_raw != expected_raw:
        raise RunContractError(
            "raw JSON set mismatch "
            f"(missing={sorted(expected_raw - observed_raw)}, extra={sorted(observed_raw - expected_raw)})"
        )
    if metadata["n_crashed"] != crashed_count:
        raise RunContractError("metadata crash count does not match combined.csv")
    if metadata["n_succeeded"] != len(successful_ids):
        raise RunContractError("metadata success count does not match combined.csv")
    if metadata["n_acquisition_failed"] != acquisition_failed_count:
        raise RunContractError("metadata acquisition-failure count does not match combined.csv")
    if metadata["n_acquisition_partial"] != acquisition_partial_count:
        raise RunContractError("metadata partial-acquisition count does not match combined.csv")
    if metadata["n_scanner_crashed"] != scanner_crashed_count:
        raise RunContractError("metadata scanner-crash count does not match combined.csv")
    if metadata["n_contract_failed"] != contract_failed_count:
        raise RunContractError("metadata contract-failure count does not match combined.csv")
    return rows


def _validate_artifacts(run_dir: Path, metadata: dict[str, Any]) -> dict[str, bytes]:
    raw_records = metadata.get("artifacts")
    if not isinstance(raw_records, list) or not raw_records:
        raise RunContractError("run metadata has no artifact hash records")
    artifacts: dict[str, bytes] = {}
    recorded_paths: list[str] = []
    for record in raw_records:
        if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
            raise RunContractError("run artifact record must contain only path and sha256")
        value = record["path"]
        expected = record["sha256"]
        if not isinstance(value, str) or not isinstance(expected, str):
            raise RunContractError("run artifact record requires string path and sha256")
        relative = _safe_relative_path(value)
        normalized = relative.as_posix()
        recorded_paths.append(normalized)
        if normalized in artifacts:
            raise RunContractError(f"duplicate run artifact record: {normalized}")
        if not _SHA256_RE.fullmatch(expected):
            raise RunContractError(f"invalid SHA-256 for run artifact: {normalized}")
        data = _regular_file_bytes(run_dir, relative)
        if hashlib.sha256(data).hexdigest() != expected:
            raise RunContractError(f"run artifact checksum mismatch: {normalized}")
        artifacts[normalized] = data
    if recorded_paths != sorted(recorded_paths):
        raise RunContractError("run artifact records are not in canonical path order")
    required = {"combined.csv", "inputs/repos.csv", "inputs/clones_manifest.json"}
    required.update(f"{HARNESS_DIRECTORY}/{path}" for path in REQUIRED_HARNESS_PATHS)
    if metadata.get("claim_ground_truth_sha256") is not None:
        required.add("inputs/claim_ground_truth.json")
    missing = required - set(artifacts)
    if missing:
        raise RunContractError(
            f"run integrity manifest lacks required artifacts: {sorted(missing)}"
        )
    raw_pattern = re.compile(rf"^{re.escape(RAW_DIRECTORY)}/[A-Za-z0-9][A-Za-z0-9._-]*\.json$")
    unsupported = {
        path for path in artifacts if path not in required and not raw_pattern.fullmatch(path)
    }
    if unsupported:
        raise RunContractError(
            f"run integrity manifest has unsupported artifacts: {sorted(unsupported)}"
        )
    for name, expected in metadata["corpus_harness_files"].items():
        artifact_name = f"{HARNESS_DIRECTORY}/{name}"
        observed = hashlib.sha256(artifacts[artifact_name]).hexdigest()
        if observed != expected:
            raise RunContractError(f"copied corpus harness digest mismatch: {name}")
    return artifacts


def _validate_exact_tree(run_dir: Path, artifact_paths: set[str], marker_name: str) -> None:
    if run_dir.is_symlink():
        raise RunContractError("run directory must not be a symlink")
    allowed_files = artifact_paths | {RUN_META_NAME, marker_name}
    allowed_directories = {"inputs", RAW_DIRECTORY, HARNESS_DIRECTORY}
    for value in artifact_paths:
        relative_path = PurePosixPath(value)
        for parent in relative_path.parents:
            if parent.as_posix() != ".":
                allowed_directories.add(parent.as_posix())

    observed_files: set[str] = set()
    observed_directories: set[str] = set()
    for root, directories, files in os.walk(run_dir, followlinks=False):
        root_path = Path(root)
        for name in directories:
            observed_path = root_path / name
            relative = observed_path.relative_to(run_dir).as_posix()
            if observed_path.is_symlink():
                raise RunContractError(f"run artifacts must not be symlinks: {relative}")
            observed_directories.add(relative)
        for name in files:
            observed_path = root_path / name
            relative = observed_path.relative_to(run_dir).as_posix()
            if observed_path.is_symlink():
                raise RunContractError(f"run artifacts must not be symlinks: {relative}")
            observed_files.add(relative)
    if observed_files != allowed_files:
        raise RunContractError(
            "run directory file set mismatch "
            f"(missing={sorted(allowed_files - observed_files)}, extra={sorted(observed_files - allowed_files)})"
        )
    extra_directories = observed_directories - allowed_directories
    if extra_directories:
        raise RunContractError(
            f"run directory has untracked directories: {sorted(extra_directories)}"
        )
    missing_directories = {"inputs", RAW_DIRECTORY, HARNESS_DIRECTORY} - observed_directories
    if missing_directories:
        raise RunContractError(
            f"run directory lacks structural directories: {sorted(missing_directories)}"
        )


def _validate_contents(
    run_dir: Path, metadata: dict[str, Any], marker_name: str
) -> tuple[dict[str, bytes], list[dict[str, str]]]:
    if metadata.get("run_schema_version") != RUN_SCHEMA_VERSION:
        raise RunContractError(f"unsupported run schema: {metadata.get('run_schema_version')!r}")
    if metadata.get("complete") is not True:
        raise RunContractError("run metadata is not marked complete")
    _validate_metadata(metadata)
    artifacts = _validate_artifacts(run_dir, metadata)
    _validate_exact_tree(run_dir, set(artifacts), marker_name)
    rows = _validate_combined_rows(metadata, artifacts)
    _validate_input_copies(metadata, rows, artifacts)
    return artifacts, rows


def finalize_run(run_dir: Path, metadata: dict[str, Any]) -> None:
    """Validate all evidence, then atomically replace the running marker."""
    running = run_dir / RUNNING_MARKER
    if not running.is_file() or running.is_symlink():
        raise RunContractError(f"missing regular {RUNNING_MARKER} marker in {run_dir}")
    if (run_dir / COMPLETE_MARKER).exists():
        raise RunContractError(f"run is already complete: {run_dir}")
    meta_path = run_dir / RUN_META_NAME
    if meta_path.exists() or meta_path.is_symlink():
        raise RunContractError(f"refusing to overwrite existing {RUN_META_NAME}")
    final_metadata = {
        **metadata,
        "complete": True,
        "run_schema_version": RUN_SCHEMA_VERSION,
    }
    write_json(meta_path, final_metadata)
    # The success marker is evidence that validation completed, not merely that
    # the producer reached the end of its loop. A failure deliberately leaves
    # _RUNNING and run_meta.json available for diagnosis.
    _validate_contents(run_dir, final_metadata, RUNNING_MARKER)
    meta_digest = sha256_file(meta_path)
    running.write_text(meta_digest + "\n", encoding="utf-8", newline="\n")
    running.replace(run_dir / COMPLETE_MARKER)


def validate_run_evidence_with_digest(
    run_dir: Path,
) -> tuple[dict[str, Any], dict[str, bytes], list[dict[str, str]], str]:
    """Return one internally consistent snapshot and its metadata digest."""
    if not run_dir.is_dir() or run_dir.is_symlink():
        raise RunContractError(f"run directory does not exist or is unsafe: {run_dir}")
    if (run_dir / RUNNING_MARKER).exists():
        raise RunContractError(f"run is incomplete: {RUNNING_MARKER} is present")
    complete_data = _regular_file_bytes(run_dir, PurePosixPath(COMPLETE_MARKER))
    meta_data = _regular_file_bytes(run_dir, PurePosixPath(RUN_META_NAME))
    metadata = _load_json_bytes(meta_data, RUN_META_NAME)
    marker_digest = _decode_utf8(complete_data, COMPLETE_MARKER)
    if marker_digest != hashlib.sha256(meta_data).hexdigest() + "\n":
        raise RunContractError("completion marker does not match run metadata")
    artifacts, rows = _validate_contents(run_dir, metadata, COMPLETE_MARKER)
    return metadata, artifacts, rows, hashlib.sha256(meta_data).hexdigest()


def validate_run_evidence(
    run_dir: Path,
) -> tuple[dict[str, Any], dict[str, bytes], list[dict[str, str]]]:
    """Return one internally consistent snapshot of a completed run."""
    metadata, artifacts, rows, _ = validate_run_evidence_with_digest(run_dir)
    return metadata, artifacts, rows


def validate_run(run_dir: Path) -> dict[str, Any]:
    """Validate a completed run and return its metadata."""
    metadata, _, _ = validate_run_evidence(run_dir)
    return metadata
