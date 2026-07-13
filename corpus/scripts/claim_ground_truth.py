#!/usr/bin/env python3
"""Validate pre-scan claim ground truth and compare it with Adduce claim trails."""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

if __package__:
    from .run_contract import (
        RunContractError,
        ensure_output_outside,
        load_json_object_bytes,
        require_current_harness_file,
        sha256_file,
        validate_run_evidence,
        write_json,
    )
else:
    from run_contract import (
        RunContractError,
        ensure_output_outside,
        load_json_object_bytes,
        require_current_harness_file,
        sha256_file,
        validate_run_evidence,
        write_json,
    )

CLAIM_GROUND_TRUTH_SCHEMA_VERSION = 1
CLONE_MANIFEST_NAME = "clones_manifest.json"
TARGETS = (
    "code",
    "reported_result",
    "run",
    "output",
    "command",
    "configuration",
    "data",
    "environment",
    "seed",
    "commit",
)
RESOLUTIONS = frozenset({"resolved", "unresolved", "unknown", "not_applicable"})
TRAIL_STATUSES = frozenset({"supported", "partial", "unlinked", "unknown"})
TARGET_LABELS = {
    "code": ("code",),
    "reported_result": ("metric",),
    "run": ("run",),
    "output": ("log",),
    "command": ("command",),
    "configuration": ("config",),
    "data": ("data",),
    "environment": ("env",),
    "seed": ("seeds",),
    "commit": ("commit",),
}
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class ClaimGroundTruthError(ValueError):
    """Claim ground truth is incomplete, inconsistent, or not attributable."""


def _require_object_shape(
    value: dict[str, Any],
    *,
    required: set[str],
    allowed: set[str],
    context: str,
) -> None:
    missing = required - set(value)
    extra = set(value) - allowed
    if missing or extra:
        raise ClaimGroundTruthError(
            f"{context} fields do not match the published schema "
            f"(missing={sorted(missing)}, extra={sorted(extra)})"
        )


def _load_object_bytes(data: bytes, label: str) -> dict[str, Any]:
    try:
        return load_json_object_bytes(data, label)
    except RunContractError as exc:
        raise ClaimGroundTruthError(str(exc)) from exc


def _load_object(path: Path) -> dict[str, Any]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ClaimGroundTruthError(f"cannot read {path}: {exc}") from exc
    return _load_object_bytes(data, str(path))


def _parse_timestamp(value: object, context: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ClaimGroundTruthError(f"{context} must be a timezone-aware timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ClaimGroundTruthError(f"{context} is not an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ClaimGroundTruthError(f"{context} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _safe_relative_path(value: object, context: str) -> Path:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or "\x00" in value
        or re.match(r"^[A-Za-z]:", value)
    ):
        raise ClaimGroundTruthError(f"{context} requires a relative path")
    portable = PurePosixPath(value)
    if (
        portable.is_absolute()
        or any(part in {"", ".", ".."} for part in portable.parts)
        or portable.as_posix() != value
    ):
        raise ClaimGroundTruthError(f"{context} has unsafe path {value!r}")
    return Path(*portable.parts)


def _load_inventory_bytes(
    data: bytes, label: str
) -> tuple[list[dict[str, str]], dict[str, dict[str, str]]]:
    try:
        text = data.decode("utf-8")
        rows = list(csv.DictReader(text.splitlines()))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise ClaimGroundTruthError(f"cannot read repository inventory {label}: {exc}") from exc
    if not rows:
        raise ClaimGroundTruthError("repository inventory is empty")
    by_id: dict[str, dict[str, str]] = {}
    for line_number, row in enumerate(rows, 2):
        repo_id = row.get("id", "")
        commit = row.get("commit_sha", "").lower()
        if not repo_id or repo_id in by_id:
            raise ClaimGroundTruthError(f"inventory line {line_number} has duplicate or empty ID")
        if not _COMMIT_RE.fullmatch(commit):
            raise ClaimGroundTruthError(f"inventory line {line_number} lacks a full commit")
        row["commit_sha"] = commit
        by_id[repo_id] = row
    return rows, by_id


def _load_inventory(path: Path) -> tuple[list[dict[str, str]], dict[str, dict[str, str]]]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ClaimGroundTruthError(f"cannot read repository inventory {path}: {exc}") from exc
    return _load_inventory_bytes(data, str(path))


def _validate_file_anchor(
    anchor: dict[str, Any],
    *,
    root: Path,
    context: str,
    verify_quote: bool,
) -> None:
    relative = _safe_relative_path(anchor.get("path"), context)
    target = root / relative
    if not target.is_file():
        raise ClaimGroundTruthError(f"{context} does not exist: {relative.as_posix()}")
    try:
        target.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ClaimGroundTruthError(f"{context} resolves outside its evidence root") from exc
    expected_sha = anchor.get("sha256")
    if not isinstance(expected_sha, str) or not _SHA256_RE.fullmatch(expected_sha):
        raise ClaimGroundTruthError(f"{context} requires a full SHA-256")
    if sha256_file(target) != expected_sha:
        raise ClaimGroundTruthError(f"{context} SHA-256 does not match {relative.as_posix()}")

    line_start = anchor.get("line_start")
    line_end = anchor.get("line_end")
    role = anchor.get("role")
    if role is not None and (not isinstance(role, str) or not role.strip()):
        raise ClaimGroundTruthError(f"{context} has an invalid evidence role")
    if (line_start is None) != (line_end is None):
        raise ClaimGroundTruthError(f"{context} must provide both line_start and line_end")
    if verify_quote and line_start is None:
        raise ClaimGroundTruthError(f"{context} requires an exact line range")
    if line_start is not None:
        if (
            isinstance(line_start, bool)
            or not isinstance(line_start, int)
            or isinstance(line_end, bool)
            or not isinstance(line_end, int)
            or line_start <= 0
            or line_end < line_start
        ):
            raise ClaimGroundTruthError(f"{context} has invalid line bounds")
        if verify_quote or line_start is not None:
            try:
                lines = target.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError as exc:
                raise ClaimGroundTruthError(f"{context} source is not UTF-8 text") from exc
            if line_end > len(lines):
                raise ClaimGroundTruthError(f"{context} line range exceeds the source file")
            if verify_quote:
                excerpt = "\n".join(lines[line_start - 1 : line_end]).strip()
                if anchor.get("quote") != excerpt:
                    raise ClaimGroundTruthError(
                        f"{context} quote does not match its exact line range"
                    )


def _validate_source(
    source: object,
    *,
    repo_root: Path,
    claims_root: Path,
    context: str,
) -> str:
    if not isinstance(source, dict):
        raise ClaimGroundTruthError(f"{context} must be an object")
    quote = source.get("quote")
    if not isinstance(quote, str) or not quote.strip():
        raise ClaimGroundTruthError(f"{context} requires a non-empty exact quote")
    kind = source.get("kind")
    if kind == "repository_file":
        _require_object_shape(
            source,
            required={"kind", "path", "sha256", "quote", "line_start", "line_end"},
            allowed={"kind", "path", "sha256", "quote", "line_start", "line_end"},
            context=context,
        )
        _validate_file_anchor(source, root=repo_root, context=context, verify_quote=True)
    elif kind == "paper_snapshot":
        _require_object_shape(
            source,
            required={"kind", "path", "sha256", "quote", "publication_url"},
            allowed={
                "kind",
                "path",
                "sha256",
                "quote",
                "publication_url",
                "page",
                "locator",
            },
            context=context,
        )
        _validate_file_anchor(source, root=claims_root, context=context, verify_quote=False)
        page = source.get("page")
        locator = source.get("locator")
        if (isinstance(page, bool) or not isinstance(page, int) or page <= 0) and (
            not isinstance(locator, str) or not locator.strip()
        ):
            raise ClaimGroundTruthError(f"{context} requires a positive page or exact locator")
        publication_url = source.get("publication_url")
        if not isinstance(publication_url, str) or not publication_url.startswith("https://"):
            raise ClaimGroundTruthError(f"{context} requires an HTTPS publication URL")
    else:
        raise ClaimGroundTruthError(f"{context} has unsupported source kind {kind!r}")
    return quote


def _validate_artifact(
    artifact: object,
    *,
    repo_root: Path,
    context: str,
) -> None:
    if not isinstance(artifact, dict):
        raise ClaimGroundTruthError(f"{context} must be an object")
    kind = artifact.get("kind")
    if kind == "repository_file":
        _require_object_shape(
            artifact,
            required={"kind", "path", "sha256"},
            allowed={"kind", "path", "sha256", "line_start", "line_end", "role"},
            context=context,
        )
        _validate_file_anchor(artifact, root=repo_root, context=context, verify_quote=False)
    elif kind == "claim_source":
        _require_object_shape(
            artifact,
            required={"kind"},
            allowed={"kind", "role"},
            context=context,
        )
    elif kind == "literal":
        _require_object_shape(
            artifact,
            required={"kind", "value"},
            allowed={"kind", "value", "role"},
            context=context,
        )
        value = artifact.get("value")
        if value is None or isinstance(value, (bool, dict, list)) or str(value).strip() == "":
            raise ClaimGroundTruthError(f"{context} literal requires a scalar value")
    elif kind == "external_reference":
        _require_object_shape(
            artifact,
            required={"kind"},
            allowed={"kind", "identifier", "url", "role"},
            context=context,
        )
        identifier = artifact.get("identifier")
        url = artifact.get("url")
        if not (isinstance(identifier, str) and identifier.strip()) and not (
            isinstance(url, str) and url.startswith("https://")
        ):
            raise ClaimGroundTruthError(
                f"{context} external reference requires an identifier or HTTPS URL"
            )
    else:
        raise ClaimGroundTruthError(f"{context} has unsupported artifact kind {kind!r}")


def _validate_claim(
    claim: object,
    *,
    inventory: dict[str, dict[str, str]],
    clones: Path,
    claims_root: Path,
    frozen_at: datetime,
    context: str,
) -> dict[str, Any]:
    if not isinstance(claim, dict):
        raise ClaimGroundTruthError(f"{context} must be an object")
    _require_object_shape(
        claim,
        required={
            "claim_id",
            "repo_id",
            "repo_commit",
            "source",
            "claim",
            "adduce_match",
            "expected_trail_status",
            "expected_links",
            "ground_truth_review",
        },
        allowed={
            "claim_id",
            "repo_id",
            "repo_commit",
            "source",
            "claim",
            "adduce_match",
            "expected_trail_status",
            "expected_links",
            "ground_truth_review",
        },
        context=context,
    )
    claim_id = claim.get("claim_id")
    if not isinstance(claim_id, str) or not _ID_RE.fullmatch(claim_id):
        raise ClaimGroundTruthError(f"{context} has invalid claim_id")
    repo_id = claim.get("repo_id")
    if not isinstance(repo_id, str) or repo_id not in inventory:
        raise ClaimGroundTruthError(f"{context} references an unknown repository")
    expected_commit = inventory[repo_id]["commit_sha"]
    if claim.get("repo_commit") != expected_commit:
        raise ClaimGroundTruthError(f"{context} commit does not match the frozen inventory")
    repo_root = clones / repo_id
    if not repo_root.is_dir():
        raise ClaimGroundTruthError(f"{context} clone is unavailable: {repo_root}")
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ClaimGroundTruthError(f"{context} cannot verify the clone commit") from exc
    if completed.returncode != 0 or completed.stdout.strip().lower() != expected_commit:
        raise ClaimGroundTruthError(f"{context} clone does not match the frozen commit")

    quote = _validate_source(
        claim.get("source"),
        repo_root=repo_root,
        claims_root=claims_root,
        context=f"{context} source",
    )
    statement = claim.get("claim")
    if not isinstance(statement, dict):
        raise ClaimGroundTruthError(f"{context} claim statement must be an object")
    _require_object_shape(
        statement,
        required={"text", "metric", "value"},
        allowed={"text", "metric", "value", "unit", "context"},
        context=f"{context} claim statement",
    )
    text = statement.get("text")
    if not isinstance(text, str) or not text.strip() or text not in quote:
        raise ClaimGroundTruthError(f"{context} claim text must occur verbatim in the source quote")
    value = statement.get("value")
    if isinstance(value, (dict, list, bool)):
        raise ClaimGroundTruthError(f"{context} claim value must be a scalar or null")
    metric = statement.get("metric")
    if metric is not None and (not isinstance(metric, str) or not metric.strip()):
        raise ClaimGroundTruthError(f"{context} claim metric must be a non-empty string or null")

    match = claim.get("adduce_match")
    if not isinstance(match, dict):
        raise ClaimGroundTruthError(f"{context} requires an adduce_match object")
    _require_object_shape(
        match,
        required={"headline_contains"},
        allowed={"claim_id", "headline_contains"},
        context=f"{context} adduce_match",
    )
    match_id = match.get("claim_id")
    headline = match.get("headline_contains")
    if match_id is not None and (not isinstance(match_id, str) or not match_id):
        raise ClaimGroundTruthError(f"{context} adduce_match has an invalid claim_id")
    if not isinstance(headline, str) or not headline:
        raise ClaimGroundTruthError(f"{context} adduce_match requires headline_contains")
    if claim.get("expected_trail_status") not in TRAIL_STATUSES:
        raise ClaimGroundTruthError(f"{context} has invalid expected trail status")

    links = claim.get("expected_links")
    if not isinstance(links, list):
        raise ClaimGroundTruthError(f"{context} expected_links must be a list")
    targets: list[str] = []
    for link_number, link in enumerate(links, 1):
        link_context = f"{context} link {link_number}"
        if not isinstance(link, dict):
            raise ClaimGroundTruthError(f"{link_context} must be an object")
        _require_object_shape(
            link,
            required={"target", "expected_resolution", "artifacts", "rationale"},
            allowed={"target", "expected_resolution", "artifacts", "rationale"},
            context=link_context,
        )
        target = link.get("target")
        resolution = link.get("expected_resolution")
        if target not in TARGETS:
            raise ClaimGroundTruthError(f"{link_context} has invalid target")
        if resolution not in RESOLUTIONS:
            raise ClaimGroundTruthError(f"{link_context} has invalid expected resolution")
        targets.append(str(target))
        rationale = link.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            raise ClaimGroundTruthError(f"{link_context} requires a rationale")
        artifacts = link.get("artifacts")
        if not isinstance(artifacts, list):
            raise ClaimGroundTruthError(f"{link_context} artifacts must be a list")
        if resolution == "resolved" and not artifacts:
            raise ClaimGroundTruthError(f"{link_context} resolved link requires evidence artifacts")
        if resolution == "not_applicable" and artifacts:
            raise ClaimGroundTruthError(
                f"{link_context} not-applicable link cannot identify evidence artifacts"
            )
        for artifact_number, artifact in enumerate(artifacts, 1):
            _validate_artifact(
                artifact,
                repo_root=repo_root,
                context=f"{link_context} artifact {artifact_number}",
            )
            if (
                isinstance(artifact, dict)
                and artifact.get("kind") == "claim_source"
                and (target != "reported_result")
            ):
                raise ClaimGroundTruthError(
                    f"{link_context} can use claim_source only for the reported result"
                )
    if Counter(targets) != Counter(TARGETS):
        missing = sorted(set(TARGETS) - set(targets))
        duplicate = sorted(target for target, count in Counter(targets).items() if count > 1)
        raise ClaimGroundTruthError(
            f"{context} must record every link target exactly once "
            f"(missing={missing}, duplicate={duplicate})"
        )
    commit_link = next(link for link in links if link["target"] == "commit")
    if commit_link["expected_resolution"] == "resolved" and not any(
        artifact.get("kind") == "literal" and str(artifact.get("value")) == expected_commit
        for artifact in commit_link["artifacts"]
        if isinstance(artifact, dict)
    ):
        raise ClaimGroundTruthError(
            f"{context} resolved commit link must cite the exact frozen commit"
        )

    review = claim.get("ground_truth_review")
    if not isinstance(review, dict):
        raise ClaimGroundTruthError(f"{context} requires ground_truth_review")
    _require_object_shape(
        review,
        required={"prepared_by", "prepared_at"},
        allowed={"prepared_by", "prepared_at", "verified_by", "verified_at", "notes"},
        context=f"{context} ground_truth_review",
    )
    prepared_by = review.get("prepared_by")
    if (
        not isinstance(prepared_by, str)
        or not prepared_by
        or any(character.isspace() for character in prepared_by)
    ):
        raise ClaimGroundTruthError(f"{context} has invalid prepared_by")
    prepared_at = _parse_timestamp(review.get("prepared_at"), f"{context} prepared_at")
    if prepared_at > frozen_at:
        raise ClaimGroundTruthError(f"{context} was prepared after the ground-truth freeze")
    verified_by = review.get("verified_by")
    verified_at = review.get("verified_at")
    if (verified_by is None) != (verified_at is None):
        raise ClaimGroundTruthError(
            f"{context} verification identity and time must appear together"
        )
    if verified_by is not None:
        if (
            not isinstance(verified_by, str)
            or not verified_by
            or any(character.isspace() for character in verified_by)
            or verified_by == prepared_by
        ):
            raise ClaimGroundTruthError(f"{context} requires an independent verifier identity")
        if _parse_timestamp(verified_at, f"{context} verified_at") > frozen_at:
            raise ClaimGroundTruthError(f"{context} was verified after the ground-truth freeze")
    return claim


def validate_ground_truth_structure(
    payload: dict[str, Any], *, require_verified: bool = True
) -> None:
    """Validate the self-contained schema contract without dereferencing evidence files."""
    top_fields = {
        "claim_ground_truth_schema_version",
        "corpus_inventory_sha256",
        "clone_manifest_sha256",
        "frozen_at",
        "claims",
        "unavailable_repositories",
    }
    _require_object_shape(payload, required=top_fields, allowed=top_fields, context="claim truth")
    if payload.get("claim_ground_truth_schema_version") != CLAIM_GROUND_TRUTH_SCHEMA_VERSION:
        raise ClaimGroundTruthError("unsupported claim-ground-truth schema")
    for field in ("corpus_inventory_sha256", "clone_manifest_sha256"):
        value = payload.get(field)
        if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
            raise ClaimGroundTruthError(f"claim truth has invalid {field}")
    frozen_at = _parse_timestamp(payload.get("frozen_at"), "frozen_at")
    claims = payload.get("claims")
    unavailable = payload.get("unavailable_repositories")
    if not isinstance(claims, list) or not isinstance(unavailable, list):
        raise ClaimGroundTruthError("claim truth repository records must be arrays")

    claim_ids: set[str] = set()
    repo_ids: set[str] = set()
    for number, claim in enumerate(claims, 1):
        context = f"claim {number}"
        if not isinstance(claim, dict):
            raise ClaimGroundTruthError(f"{context} must be an object")
        fields = {
            "claim_id",
            "repo_id",
            "repo_commit",
            "source",
            "claim",
            "adduce_match",
            "expected_trail_status",
            "expected_links",
            "ground_truth_review",
        }
        _require_object_shape(claim, required=fields, allowed=fields, context=context)
        claim_id = claim.get("claim_id")
        repo_id = claim.get("repo_id")
        commit = claim.get("repo_commit")
        if not isinstance(claim_id, str) or not _ID_RE.fullmatch(claim_id) or claim_id in claim_ids:
            raise ClaimGroundTruthError(f"{context} has an invalid or duplicate claim_id")
        if not isinstance(repo_id, str) or not _ID_RE.fullmatch(repo_id) or repo_id in repo_ids:
            raise ClaimGroundTruthError(f"{context} has an invalid or duplicate repo_id")
        if not isinstance(commit, str) or not _COMMIT_RE.fullmatch(commit):
            raise ClaimGroundTruthError(f"{context} has an invalid repo_commit")
        claim_ids.add(claim_id)
        repo_ids.add(repo_id)

        source = claim.get("source")
        if not isinstance(source, dict):
            raise ClaimGroundTruthError(f"{context} source must be an object")
        source_kind = source.get("kind")
        if source_kind == "repository_file":
            source_fields = {"kind", "path", "sha256", "quote", "line_start", "line_end"}
            _require_object_shape(
                source, required=source_fields, allowed=source_fields, context=f"{context} source"
            )
        elif source_kind == "paper_snapshot":
            required = {"kind", "path", "sha256", "quote", "publication_url"}
            allowed = {*required, "page", "locator"}
            _require_object_shape(
                source, required=required, allowed=allowed, context=f"{context} source"
            )
            publication_url = source.get("publication_url")
            if not isinstance(publication_url, str) or not publication_url.startswith("https://"):
                raise ClaimGroundTruthError(f"{context} paper source requires an HTTPS URL")
            page = source.get("page")
            locator = source.get("locator")
            if (isinstance(page, bool) or not isinstance(page, int) or page <= 0) and (
                not isinstance(locator, str) or not locator.strip()
            ):
                raise ClaimGroundTruthError(f"{context} paper source requires a page or locator")
        else:
            raise ClaimGroundTruthError(f"{context} has an unsupported source kind")
        _safe_relative_path(source.get("path"), f"{context} source")
        source_sha = source.get("sha256")
        quote = source.get("quote")
        if not isinstance(source_sha, str) or not _SHA256_RE.fullmatch(source_sha):
            raise ClaimGroundTruthError(f"{context} source requires a SHA-256")
        if not isinstance(quote, str) or not quote.strip():
            raise ClaimGroundTruthError(f"{context} source requires an exact quote")
        if source_kind == "repository_file":
            line_start = source.get("line_start")
            line_end = source.get("line_end")
            if (
                isinstance(line_start, bool)
                or not isinstance(line_start, int)
                or isinstance(line_end, bool)
                or not isinstance(line_end, int)
                or line_start <= 0
                or line_end < line_start
            ):
                raise ClaimGroundTruthError(f"{context} source has invalid line bounds")

        statement = claim.get("claim")
        if not isinstance(statement, dict):
            raise ClaimGroundTruthError(f"{context} statement must be an object")
        _require_object_shape(
            statement,
            required={"text", "metric", "value"},
            allowed={"text", "metric", "value", "unit", "context"},
            context=f"{context} statement",
        )
        text = statement.get("text")
        if not isinstance(text, str) or not text.strip() or text not in quote:
            raise ClaimGroundTruthError(f"{context} text must occur in its exact source quote")
        metric = statement.get("metric")
        if metric is not None and (not isinstance(metric, str) or not metric.strip()):
            raise ClaimGroundTruthError(f"{context} metric is invalid")
        for field in ("value", "unit", "context"):
            value = statement.get(field)
            if isinstance(value, (bool, dict, list)):
                raise ClaimGroundTruthError(f"{context} statement {field} is invalid")
            if field in {"unit", "context"} and value is not None and not isinstance(value, str):
                raise ClaimGroundTruthError(f"{context} statement {field} is invalid")

        match = claim.get("adduce_match")
        if not isinstance(match, dict):
            raise ClaimGroundTruthError(f"{context} adduce_match must be an object")
        _require_object_shape(
            match,
            required={"headline_contains"},
            allowed={"claim_id", "headline_contains"},
            context=f"{context} adduce_match",
        )
        if not isinstance(match.get("headline_contains"), str) or not match["headline_contains"]:
            raise ClaimGroundTruthError(f"{context} requires a content selector")
        selector = match["headline_contains"]
        if quote.count(selector) != 1:
            raise ClaimGroundTruthError(
                f"{context} content selector must occur exactly once in the frozen source quote"
            )
        if selector not in text[:90]:
            raise ClaimGroundTruthError(
                f"{context} content selector is outside the ClaimTrail headline prefix"
            )
        if match.get("claim_id") is not None and (
            not isinstance(match["claim_id"], str) or not match["claim_id"]
        ):
            raise ClaimGroundTruthError(f"{context} has an invalid Adduce claim ID")
        if claim.get("expected_trail_status") not in TRAIL_STATUSES:
            raise ClaimGroundTruthError(f"{context} has an invalid trail status")

        links = claim.get("expected_links")
        if not isinstance(links, list):
            raise ClaimGroundTruthError(f"{context} links must be an array")
        targets: list[str] = []
        for link_number, link in enumerate(links, 1):
            link_context = f"{context} link {link_number}"
            if not isinstance(link, dict):
                raise ClaimGroundTruthError(f"{link_context} must be an object")
            link_fields = {"target", "expected_resolution", "artifacts", "rationale"}
            _require_object_shape(
                link, required=link_fields, allowed=link_fields, context=link_context
            )
            target = link.get("target")
            resolution = link.get("expected_resolution")
            artifacts = link.get("artifacts")
            if target not in TARGETS or resolution not in RESOLUTIONS:
                raise ClaimGroundTruthError(f"{link_context} has an invalid target or resolution")
            if not isinstance(link.get("rationale"), str) or not link["rationale"].strip():
                raise ClaimGroundTruthError(f"{link_context} requires a rationale")
            if not isinstance(artifacts, list):
                raise ClaimGroundTruthError(f"{link_context} artifacts must be an array")
            if resolution == "resolved" and not artifacts:
                raise ClaimGroundTruthError(f"{link_context} resolved link requires artifacts")
            if resolution == "not_applicable" and artifacts:
                raise ClaimGroundTruthError(f"{link_context} not-applicable link has artifacts")
            targets.append(str(target))
            for artifact_number, artifact in enumerate(artifacts, 1):
                artifact_context = f"{link_context} artifact {artifact_number}"
                if not isinstance(artifact, dict):
                    raise ClaimGroundTruthError(f"{artifact_context} must be an object")
                kind = artifact.get("kind")
                if kind == "repository_file":
                    required = {"kind", "path", "sha256"}
                    allowed = {*required, "line_start", "line_end", "role"}
                    _require_object_shape(
                        artifact, required=required, allowed=allowed, context=artifact_context
                    )
                    _safe_relative_path(artifact.get("path"), artifact_context)
                    digest = artifact.get("sha256")
                    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
                        raise ClaimGroundTruthError(f"{artifact_context} requires a SHA-256")
                    start = artifact.get("line_start")
                    end = artifact.get("line_end")
                    if (start is None) != (end is None) or (
                        start is not None
                        and (
                            isinstance(start, bool)
                            or not isinstance(start, int)
                            or isinstance(end, bool)
                            or not isinstance(end, int)
                            or start <= 0
                            or end < start
                        )
                    ):
                        raise ClaimGroundTruthError(f"{artifact_context} has invalid line bounds")
                elif kind == "claim_source":
                    _require_object_shape(
                        artifact,
                        required={"kind"},
                        allowed={"kind", "role"},
                        context=artifact_context,
                    )
                    if target != "reported_result":
                        raise ClaimGroundTruthError(
                            f"{artifact_context} can only identify the reported result"
                        )
                elif kind == "literal":
                    _require_object_shape(
                        artifact,
                        required={"kind", "value"},
                        allowed={"kind", "value", "role"},
                        context=artifact_context,
                    )
                    value = artifact.get("value")
                    if (
                        value is None
                        or isinstance(value, (bool, dict, list))
                        or not str(value).strip()
                    ):
                        raise ClaimGroundTruthError(f"{artifact_context} has an invalid literal")
                elif kind == "external_reference":
                    _require_object_shape(
                        artifact,
                        required={"kind"},
                        allowed={"kind", "identifier", "url", "role"},
                        context=artifact_context,
                    )
                    identifier = artifact.get("identifier")
                    url = artifact.get("url")
                    if not (isinstance(identifier, str) and identifier.strip()) and not (
                        isinstance(url, str) and url.startswith("https://")
                    ):
                        raise ClaimGroundTruthError(
                            f"{artifact_context} requires an identifier or HTTPS URL"
                        )
                else:
                    raise ClaimGroundTruthError(f"{artifact_context} has an unsupported kind")
                role = artifact.get("role")
                if role is not None and (not isinstance(role, str) or not role.strip()):
                    raise ClaimGroundTruthError(f"{artifact_context} has an invalid role")
        if Counter(targets) != Counter(TARGETS):
            raise ClaimGroundTruthError(f"{context} must record every target exactly once")
        commit_link = next(link for link in links if link["target"] == "commit")
        if commit_link["expected_resolution"] == "resolved" and not any(
            artifact.get("kind") == "literal" and str(artifact.get("value")) == commit
            for artifact in commit_link["artifacts"]
            if isinstance(artifact, dict)
        ):
            raise ClaimGroundTruthError(f"{context} commit link does not identify repo_commit")

        review = claim.get("ground_truth_review")
        if not isinstance(review, dict):
            raise ClaimGroundTruthError(f"{context} review must be an object")
        required_review = {"prepared_by", "prepared_at"}
        if require_verified:
            required_review |= {"verified_by", "verified_at"}
        _require_object_shape(
            review,
            required=required_review,
            allowed={"prepared_by", "prepared_at", "verified_by", "verified_at", "notes"},
            context=f"{context} review",
        )
        prepared_by = review.get("prepared_by")
        verified_by = review.get("verified_by")
        if (
            not isinstance(prepared_by, str)
            or not prepared_by
            or any(char.isspace() for char in prepared_by)
        ):
            raise ClaimGroundTruthError(f"{context} has an invalid preparer")
        if _parse_timestamp(review.get("prepared_at"), f"{context} prepared_at") > frozen_at:
            raise ClaimGroundTruthError(f"{context} was prepared after the final freeze")
        if (verified_by is None) != (review.get("verified_at") is None):
            raise ClaimGroundTruthError(f"{context} verification fields must appear together")
        if verified_by is not None:
            if (
                not isinstance(verified_by, str)
                or not verified_by
                or any(char.isspace() for char in verified_by)
                or verified_by == prepared_by
            ):
                raise ClaimGroundTruthError(f"{context} requires an independent verifier")
            if _parse_timestamp(review.get("verified_at"), f"{context} verified_at") > frozen_at:
                raise ClaimGroundTruthError(f"{context} was verified after the final freeze")

    unavailable_ids: set[str] = set()
    for number, entry in enumerate(unavailable, 1):
        context = f"unavailable repository {number}"
        if not isinstance(entry, dict):
            raise ClaimGroundTruthError(f"{context} must be an object")
        fields = {"repo_id", "repo_commit", "acquisition_status", "clone_status", "error"}
        _require_object_shape(entry, required=fields, allowed=fields, context=context)
        repo_id = entry.get("repo_id")
        if (
            not isinstance(repo_id, str)
            or not _ID_RE.fullmatch(repo_id)
            or repo_id in repo_ids
            or repo_id in unavailable_ids
        ):
            raise ClaimGroundTruthError(f"{context} has an invalid or duplicate repo_id")
        if not isinstance(entry.get("repo_commit"), str) or not _COMMIT_RE.fullmatch(
            entry["repo_commit"]
        ):
            raise ClaimGroundTruthError(f"{context} has an invalid repo_commit")
        if entry.get("acquisition_status") != "failed" or any(
            not isinstance(entry.get(field), str) or not entry[field]
            for field in ("clone_status", "error")
        ):
            raise ClaimGroundTruthError(f"{context} has an invalid failure record")
        unavailable_ids.add(repo_id)


def _validate_ground_truth_payload(
    payload: dict[str, Any],
    claims_path: Path,
    repos_path: Path,
    clones: Path,
    *,
    allow_partial: bool = False,
    inventory_data: bytes | None = None,
    clone_manifest_data: bytes | None = None,
) -> dict[str, Any]:
    validate_ground_truth_structure(payload, require_verified=not allow_partial)
    _require_object_shape(
        payload,
        required={
            "claim_ground_truth_schema_version",
            "corpus_inventory_sha256",
            "clone_manifest_sha256",
            "frozen_at",
            "claims",
            "unavailable_repositories",
        },
        allowed={
            "claim_ground_truth_schema_version",
            "corpus_inventory_sha256",
            "clone_manifest_sha256",
            "frozen_at",
            "claims",
            "unavailable_repositories",
        },
        context="claim ground truth",
    )
    if payload.get("claim_ground_truth_schema_version") != CLAIM_GROUND_TRUTH_SCHEMA_VERSION:
        raise ClaimGroundTruthError("unsupported claim-ground-truth schema")
    if inventory_data is None:
        try:
            inventory_data = repos_path.read_bytes()
        except OSError as exc:
            raise ClaimGroundTruthError(
                f"cannot read repository inventory {repos_path}: {exc}"
            ) from exc
    inventory_digest = hashlib.sha256(inventory_data).hexdigest()
    if payload.get("corpus_inventory_sha256") != inventory_digest:
        raise ClaimGroundTruthError("claim ground truth targets a different corpus inventory")
    clone_manifest_path = clones / CLONE_MANIFEST_NAME
    if clone_manifest_data is None:
        try:
            clone_manifest_data = clone_manifest_path.read_bytes()
        except OSError as exc:
            raise ClaimGroundTruthError(
                f"cannot read acquisition manifest {clone_manifest_path}: {exc}"
            ) from exc
    if payload.get("clone_manifest_sha256") != hashlib.sha256(clone_manifest_data).hexdigest():
        raise ClaimGroundTruthError("claim ground truth targets a different acquisition manifest")
    clone_manifest = _load_object_bytes(clone_manifest_data, str(clone_manifest_path))
    clone_records_raw = clone_manifest.get("records")
    if not isinstance(clone_records_raw, list):
        raise ClaimGroundTruthError("acquisition manifest has invalid records")
    clone_records = {
        str(record.get("id")): record
        for record in clone_records_raw
        if isinstance(record, dict) and isinstance(record.get("id"), str)
    }
    if len(clone_records) != len(clone_records_raw):
        raise ClaimGroundTruthError("acquisition manifest has invalid or duplicate repository IDs")
    frozen_at = _parse_timestamp(payload.get("frozen_at"), "frozen_at")
    rows, inventory = _load_inventory_bytes(inventory_data, str(repos_path))
    claims = payload.get("claims")
    if not isinstance(claims, list):
        raise ClaimGroundTruthError("claim ground truth claims must be a list")

    validated: list[dict[str, Any]] = []
    claim_ids: set[str] = set()
    repo_ids: set[str] = set()
    for number, claim in enumerate(claims, 1):
        validated_claim = _validate_claim(
            claim,
            inventory=inventory,
            clones=clones,
            claims_root=claims_path.parent,
            frozen_at=frozen_at,
            context=f"claim {number}",
        )
        claim_id = str(validated_claim["claim_id"])
        repo_id = str(validated_claim["repo_id"])
        if claim_id in claim_ids:
            raise ClaimGroundTruthError(f"duplicate claim_id: {claim_id}")
        if repo_id in repo_ids:
            raise ClaimGroundTruthError(f"more than one headline claim for repository: {repo_id}")
        claim_ids.add(claim_id)
        repo_ids.add(repo_id)
        validated.append(validated_claim)

    if not allow_partial:
        unverified = [
            str(claim["claim_id"])
            for claim in validated
            if not claim["ground_truth_review"].get("verified_by")
        ]
        if unverified:
            raise ClaimGroundTruthError(
                "complete ground truth requires independent verification for every claim "
                f"(unverified={sorted(unverified)})"
            )

    unavailable = payload.get("unavailable_repositories")
    if not isinstance(unavailable, list):
        raise ClaimGroundTruthError("claim ground truth unavailable_repositories must be a list")
    unavailable_ids: set[str] = set()
    for number, entry in enumerate(unavailable, 1):
        context = f"unavailable repository {number}"
        if not isinstance(entry, dict):
            raise ClaimGroundTruthError(f"{context} must be an object")
        unavailable_repo_id = entry.get("repo_id")
        if not isinstance(unavailable_repo_id, str) or unavailable_repo_id not in inventory:
            raise ClaimGroundTruthError(f"{context} references an unknown repository")
        if unavailable_repo_id in unavailable_ids or unavailable_repo_id in repo_ids:
            raise ClaimGroundTruthError(f"{context} duplicates a repository record")
        record = clone_records.get(unavailable_repo_id)
        expected = {
            "repo_id": unavailable_repo_id,
            "repo_commit": inventory[unavailable_repo_id]["commit_sha"],
            "acquisition_status": "failed",
            "clone_status": record.get("status") if isinstance(record, dict) else None,
            "error": record.get("error") if isinstance(record, dict) else None,
        }
        if entry != expected or not isinstance(expected["error"], str) or not expected["error"]:
            raise ClaimGroundTruthError(
                f"{context} does not exactly match a failed acquisition record"
            )
        unavailable_ids.add(unavailable_repo_id)

    labelled_repos = {row["id"] for row in rows if row.get("cohort") != "stress"}
    covered_ids = repo_ids | unavailable_ids
    unexpected = covered_ids - labelled_repos
    if unexpected:
        raise ClaimGroundTruthError(
            f"effectiveness ground truth cannot include stress repositories: {sorted(unexpected)}"
        )
    if not allow_partial and covered_ids != labelled_repos:
        raise ClaimGroundTruthError(
            "complete ground truth requires a claim or failed-acquisition record for every "
            f"labelled repository (missing={sorted(labelled_repos - covered_ids)})"
        )
    return payload


def validate_ground_truth_bytes(
    data: bytes,
    claims_path: Path,
    repos_path: Path,
    clones: Path,
    *,
    allow_partial: bool = False,
    inventory_data: bytes | None = None,
    clone_manifest_data: bytes | None = None,
) -> dict[str, Any]:
    """Validate the exact claim bytes that will be copied into a run."""
    payload = _load_object_bytes(data, str(claims_path))
    return _validate_ground_truth_payload(
        payload,
        claims_path,
        repos_path,
        clones,
        allow_partial=allow_partial,
        inventory_data=inventory_data,
        clone_manifest_data=clone_manifest_data,
    )


def validate_ground_truth(
    claims_path: Path,
    repos_path: Path,
    clones: Path,
    *,
    allow_partial: bool = False,
) -> dict[str, Any]:
    try:
        data = claims_path.read_bytes()
    except OSError as exc:
        raise ClaimGroundTruthError(f"cannot read {claims_path}: {exc}") from exc
    return validate_ground_truth_bytes(
        data,
        claims_path,
        repos_path,
        clones,
        allow_partial=allow_partial,
    )


def _matching_trails(claim: dict[str, Any], trails: list[dict[str, Any]]) -> list[dict[str, Any]]:
    match = claim["adduce_match"]
    expected_id = match.get("claim_id")
    headline = match.get("headline_contains")
    return [
        trail
        for trail in trails
        if (not expected_id or trail.get("id") == expected_id)
        and (not headline or headline in str(trail.get("headline", "")))
    ]


def _observed_resolution(trail: dict[str, Any], target: str) -> tuple[str, list[dict[str, Any]]]:
    labels = set(TARGET_LABELS[target])
    entries = [
        entry
        for entry in trail.get("trail", [])
        if isinstance(entry, dict) and entry.get("label") in labels
    ]
    if not entries:
        return "absent", []
    resolved = {entry.get("resolved") for entry in entries}
    if resolved == {True}:
        return "resolved", entries
    if resolved == {False}:
        return "unresolved", entries
    if resolved == {None}:
        return "unknown", entries
    return "mixed", entries


def _artifact_identity_match(
    artifact: dict[str, Any],
    entries: list[dict[str, Any]],
    *,
    claim: dict[str, Any],
    trail: dict[str, Any],
    target: str,
) -> bool:
    """Check that a resolved edge points to the preregistered artifact identity."""
    kind = artifact.get("kind")
    values = [str(entry.get("value", "")).strip() for entry in entries]
    if kind == "claim_source":
        selector = str(claim["adduce_match"]["headline_contains"])
        headline = str(trail.get("headline", ""))
        if target != "reported_result" or not entries or selector not in headline:
            return False
        expected_value = claim["claim"].get("value")
        if expected_value is None:
            return True
        expected = str(expected_value).strip().casefold()
        observed = " ".join([headline, *values]).casefold()
        return bool(expected) and expected in observed
    if kind == "repository_file":
        expected = str(artifact.get("path", "")).replace("\\", "/")
        candidates = [
            component.strip()
            for value in values
            for component in value.split("  (", 1)[0].split(" + ")
        ]
        return expected in candidates
    if kind == "literal":
        expected = str(artifact.get("value", "")).strip()
        return expected in values
    if kind == "external_reference":
        identities = [
            str(artifact[field]).strip()
            for field in ("identifier", "url")
            if artifact.get(field) is not None
        ]
        return all(any(identity in value for value in values) for identity in identities)
    return False


def _artifact_comparisons(
    artifacts: list[dict[str, Any]],
    entries: list[dict[str, Any]],
    *,
    claim: dict[str, Any],
    trail: dict[str, Any],
    target: str,
) -> list[dict[str, Any]]:
    return [
        {
            "kind": artifact.get("kind"),
            "identity": {
                field: artifact[field]
                for field in ("path", "sha256", "value", "identifier", "url", "role")
                if field in artifact
            },
            "match": _artifact_identity_match(
                artifact,
                entries,
                claim=claim,
                trail=trail,
                target=target,
            ),
        }
        for artifact in artifacts
    ]


def evaluate(
    claims_path: Path,
    repos_path: Path,
    clones: Path,
    run: Path,
) -> dict[str, Any]:
    try:
        truth_data = claims_path.read_bytes()
        inventory_data = repos_path.read_bytes()
        clone_manifest_data = (clones / CLONE_MANIFEST_NAME).read_bytes()
    except OSError as exc:
        raise ClaimGroundTruthError(f"cannot snapshot evaluation input: {exc}") from exc
    truth = validate_ground_truth_bytes(
        truth_data,
        claims_path,
        repos_path,
        clones,
        inventory_data=inventory_data,
        clone_manifest_data=clone_manifest_data,
    )
    try:
        run_metadata, artifacts, _ = validate_run_evidence(run)
        require_current_harness_file(run_metadata, "scripts/claim_ground_truth.py", Path(__file__))
    except RunContractError as exc:
        raise ClaimGroundTruthError(f"invalid corpus run: {exc}") from exc
    inventory_digest = hashlib.sha256(inventory_data).hexdigest()
    if run_metadata.get("repos_file_sha256") != inventory_digest:
        raise ClaimGroundTruthError("corpus run and claim ground truth use different inventories")
    truth_digest = hashlib.sha256(truth_data).hexdigest()
    if run_metadata.get("claim_ground_truth_sha256") != truth_digest:
        raise ClaimGroundTruthError("corpus run is bound to different claim ground truth")
    frozen_at = _parse_timestamp(truth["frozen_at"], "frozen_at")
    started_at = _parse_timestamp(run_metadata.get("started_at"), "run started_at")
    if frozen_at >= started_at:
        raise ClaimGroundTruthError("claim ground truth was not frozen before the corpus run")

    results: list[dict[str, Any]] = []
    for claim in truth["claims"]:
        repo_id = claim["repo_id"]
        raw_data = artifacts.get(f"raw_json/{repo_id}.json")
        if raw_data is None:
            results.append(
                {
                    "claim_id": claim["claim_id"],
                    "repo_id": repo_id,
                    "status": "not_evaluable",
                    "reason": "repository scan has no raw JSON",
                    "claim_discovery_match": False,
                    "links": [],
                }
            )
            continue
        raw = _load_object_bytes(raw_data, f"raw_json/{repo_id}.json")
        trails = [trail for trail in raw.get("claims", []) if isinstance(trail, dict)]
        matches = _matching_trails(claim, trails)
        if len(matches) != 1:
            results.append(
                {
                    "claim_id": claim["claim_id"],
                    "repo_id": repo_id,
                    "status": "not_evaluable" if len(matches) > 1 else "mismatch",
                    "reason": f"expected one matching claim trail; found {len(matches)}",
                    "claim_discovery_match": False,
                    "links": [],
                }
            )
            continue
        trail = matches[0]
        link_results: list[dict[str, Any]] = []
        for expected in claim["expected_links"]:
            observed, entries = _observed_resolution(trail, expected["target"])
            expected_resolution = expected["expected_resolution"]
            resolution_match = observed == (
                "absent" if expected_resolution == "not_applicable" else expected_resolution
            )
            artifact_comparisons = _artifact_comparisons(
                expected["artifacts"],
                entries,
                claim=claim,
                trail=trail,
                target=expected["target"],
            )
            artifact_identity_match = expected_resolution != "resolved" or all(
                comparison["match"] for comparison in artifact_comparisons
            )
            link_results.append(
                {
                    "target": expected["target"],
                    "expected_resolution": expected_resolution,
                    "observed_resolution": observed,
                    "resolution_match": resolution_match,
                    "artifact_identity_match": artifact_identity_match,
                    "artifact_comparisons": artifact_comparisons,
                    "match": resolution_match and artifact_identity_match,
                    "observed_entries": entries,
                }
            )
        status_match = (
            claim["expected_trail_status"] == "unknown"
            or trail.get("status") == claim["expected_trail_status"]
        )
        results.append(
            {
                "claim_id": claim["claim_id"],
                "repo_id": repo_id,
                "status": "match"
                if status_match and all(link["match"] for link in link_results)
                else "mismatch",
                "claim_discovery_match": True,
                "expected_trail_status": claim["expected_trail_status"],
                "observed_trail_status": trail.get("status"),
                "trail_status_match": status_match,
                "links": link_results,
            }
        )

    counts = Counter(result["status"] for result in results)
    link_results = [link for result in results for link in result["links"]]
    target_summary = []
    for target in TARGETS:
        comparisons = [link for link in link_results if link["target"] == target]
        target_summary.append(
            {
                "target": target,
                "n_comparisons": len(comparisons),
                "n_matches": sum(bool(link["match"]) for link in comparisons),
                "n_mismatches": sum(not bool(link["match"]) for link in comparisons),
                "n_not_evaluable": len(results) - len(comparisons),
            }
        )
    return {
        "claim_evaluation_schema_version": 1,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_metadata["run_id"],
        "adduce_version": run_metadata["adduce_version"],
        "ground_truth_sha256": truth_digest,
        "corpus_inventory_sha256": inventory_digest,
        "n_claims": len(results),
        "n_claim_matches": counts["match"],
        "n_claim_mismatches": counts["mismatch"],
        "n_not_evaluable": counts["not_evaluable"],
        "n_unavailable_repositories": len(truth["unavailable_repositories"]),
        "unavailable_repositories": truth["unavailable_repositories"],
        "n_link_comparisons": len(link_results),
        "n_link_matches": sum(bool(link["match"]) for link in link_results),
        "target_summary": target_summary,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="validate pre-scan ground truth")
    validate_parser.add_argument("--claims", type=Path, required=True)
    validate_parser.add_argument("--repos", type=Path, required=True)
    validate_parser.add_argument("--clones", type=Path, required=True)
    validate_parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="allow fewer than one claim per labelled repository while drafting",
    )

    evaluate_parser = subparsers.add_parser("evaluate", help="compare a run with frozen truth")
    evaluate_parser.add_argument("--claims", type=Path, required=True)
    evaluate_parser.add_argument("--repos", type=Path, required=True)
    evaluate_parser.add_argument("--clones", type=Path, required=True)
    evaluate_parser.add_argument("--run", type=Path, required=True)
    evaluate_parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    try:
        if args.command == "validate":
            payload = validate_ground_truth(
                args.claims, args.repos, args.clones, allow_partial=args.allow_partial
            )
            print(
                f"valid claim ground truth: {len(payload['claims'])} claim(s); "
                f"sha256={sha256_file(args.claims)}"
            )
            return 0
        ensure_output_outside(args.out, [args.run, args.clones])
        if args.out.exists():
            sys.exit(f"refusing to overwrite existing claim evaluation: {args.out}")
        result = evaluate(args.claims, args.repos, args.clones, args.run)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.out, result)
        print(
            f"wrote {args.out}: {result['n_claim_matches']} matched, "
            f"{result['n_claim_mismatches']} mismatched, "
            f"{result['n_not_evaluable']} not evaluable"
        )
        return 0
    except (ClaimGroundTruthError, RunContractError) as exc:
        sys.exit(f"invalid claim ground truth: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
