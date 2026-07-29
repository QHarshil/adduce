#!/usr/bin/env python3
"""Initialize and validate blinded human review of candidate claim ground truth."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

if __package__:
    from .claim_ground_truth import (
        TARGETS,
        ClaimGroundTruthError,
        validate_ground_truth,
        validate_ground_truth_structure,
    )
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
    from claim_ground_truth import (
        TARGETS,
        ClaimGroundTruthError,
        validate_ground_truth,
        validate_ground_truth_structure,
    )
    from run_contract import (
        RunContractError,
        ensure_output_outside,
        load_json_object_bytes,
        require_current_harness_file,
        sha256_file,
        validate_run_evidence,
        write_json,
    )

CLAIM_REVIEW_SCHEMA_VERSION = 1
DECISIONS = frozenset({"verified", "revision_required", "unclear"})
REVIEW_POLICY = {
    "required_independent_reviews_per_claim": 2,
    "blind_to_other_reviewer_decisions": True,
    "blind_to_adduce_claim_link_outputs": True,
    "independent_adjudication_of_decision_disagreements": True,
    "conflicted_assignments_reassigned": True,
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_CONFLICT_FIELDS = {
    "scope",
    "no_relevant_authorship_or_contribution",
    "no_close_collaboration_supervision_or_employment",
    "no_financial_conflict",
    "no_personal_conflict",
    "declared_at",
}
_NO_CONFLICT_FIELDS = (
    "no_relevant_authorship_or_contribution",
    "no_close_collaboration_supervision_or_employment",
    "no_financial_conflict",
    "no_personal_conflict",
)


class ClaimReviewError(ValueError):
    """A human claim-review artifact is malformed, incomplete, or unbound."""


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
        raise ClaimReviewError(f"cannot canonicalize claim-review evidence: {exc}") from exc
    return hashlib.sha256(encoded).hexdigest()


def _parse_timestamp(value: object, context: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ClaimReviewError(f"{context} must be a timezone-aware timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ClaimReviewError(f"{context} is not an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ClaimReviewError(f"{context} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _require_shape(
    value: object,
    *,
    required: set[str],
    allowed: set[str],
    context: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ClaimReviewError(f"{context} must be an object")
    missing = required - set(value)
    extra = set(value) - allowed
    if missing or extra:
        raise ClaimReviewError(
            f"{context} fields do not match the schema "
            f"(missing={sorted(missing)}, extra={sorted(extra)})"
        )
    return value


def _identity(value: object, context: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ClaimReviewError(f"{context} must be a stable non-personal identifier")
    return value


def _evidence(value: object, context: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise ClaimReviewError(f"{context} requires at least one non-empty evidence locator")
    return value


def _decision(value: object, context: str) -> str:
    if not isinstance(value, str) or value not in DECISIONS:
        raise ClaimReviewError(f"{context} has an invalid decision")
    return value


def _validate_link_decisions(value: object, context: str) -> dict[str, str]:
    if not isinstance(value, list) or len(value) != len(TARGETS):
        raise ClaimReviewError(f"{context} must contain one decision for every claim-link target")
    decisions: dict[str, str] = {}
    for number, raw in enumerate(value, 1):
        item = _require_shape(
            raw,
            required={"target", "decision", "rationale", "evidence"},
            allowed={"target", "decision", "rationale", "evidence"},
            context=f"{context} item {number}",
        )
        target = item.get("target")
        if not isinstance(target, str) or target not in TARGETS or target in decisions:
            raise ClaimReviewError(f"{context} has an invalid or duplicate target")
        decisions[target] = _decision(item.get("decision"), f"{context} {target}")
        if not isinstance(item.get("rationale"), str) or not item["rationale"].strip():
            raise ClaimReviewError(f"{context} {target} requires a rationale")
        _evidence(item.get("evidence"), f"{context} {target}")
    if set(decisions) != set(TARGETS):
        raise ClaimReviewError(f"{context} must cover every target exactly once")
    return decisions


def _validate_blinding(value: object, *, context: str, reviewed_at: datetime) -> None:
    declaration = _require_shape(
        value,
        required={
            "independent_review",
            "other_reviewer_decisions_not_seen",
            "adduce_claim_link_outputs_not_seen",
            "declared_at",
        },
        allowed={
            "independent_review",
            "other_reviewer_decisions_not_seen",
            "adduce_claim_link_outputs_not_seen",
            "declared_at",
        },
        context=context,
    )
    for field in (
        "independent_review",
        "other_reviewer_decisions_not_seen",
        "adduce_claim_link_outputs_not_seen",
    ):
        if declaration.get(field) is not True:
            raise ClaimReviewError(f"{context} must affirm {field}")
    declared_at = _parse_timestamp(declaration.get("declared_at"), f"{context} declared_at")
    if declared_at > reviewed_at:
        raise ClaimReviewError(f"{context} was declared after the review timestamp")


def _validate_conflict_declaration(
    value: object,
    *,
    context: str,
    repository_id: str,
    artifact_id: str,
    not_before: datetime,
    not_after: datetime,
) -> None:
    declaration = _require_shape(
        value,
        required=_CONFLICT_FIELDS,
        allowed=_CONFLICT_FIELDS,
        context=context,
    )
    scope = _require_shape(
        declaration.get("scope"),
        required={"repository_id", "artifact_id"},
        allowed={"repository_id", "artifact_id"},
        context=f"{context} scope",
    )
    if scope != {"repository_id": repository_id, "artifact_id": artifact_id}:
        raise ClaimReviewError(f"{context} does not match the assigned repository and artifact")
    for field in _NO_CONFLICT_FIELDS:
        if declaration.get(field) is not True:
            raise ClaimReviewError(
                f"{context} does not exclude every relevant conflict; "
                "the assignment must be reassigned"
            )
    declared_at = _parse_timestamp(declaration.get("declared_at"), f"{context} declared_at")
    if declared_at < not_before:
        raise ClaimReviewError(f"{context} predates the review assignment")
    if declared_at > not_after:
        raise ClaimReviewError(f"{context} was made after the review decision")


def _validate_initial_review(
    value: object,
    *,
    context: str,
    frozen_at: datetime,
    repository_id: str,
    artifact_id: str,
) -> tuple[str, datetime, str, dict[str, str]]:
    review = _require_shape(
        value,
        required={
            "reviewer_id",
            "domain_expertise",
            "reviewed_at",
            "blinding_declaration",
            "conflict_of_interest_declaration",
            "claim_decision",
            "claim_rationale",
            "claim_evidence",
            "link_decisions",
        },
        allowed={
            "reviewer_id",
            "domain_expertise",
            "reviewed_at",
            "blinding_declaration",
            "conflict_of_interest_declaration",
            "claim_decision",
            "claim_rationale",
            "claim_evidence",
            "link_decisions",
        },
        context=context,
    )
    reviewer_id = _identity(review.get("reviewer_id"), f"{context} reviewer_id")
    expertise = review.get("domain_expertise")
    if not isinstance(expertise, str) or not expertise.strip():
        raise ClaimReviewError(f"{context} requires a domain-expertise statement")
    reviewed_at = _parse_timestamp(review.get("reviewed_at"), f"{context} reviewed_at")
    if reviewed_at < frozen_at:
        raise ClaimReviewError(f"{context} predates the candidate truth freeze")
    _validate_blinding(
        review.get("blinding_declaration"),
        context=f"{context} blinding_declaration",
        reviewed_at=reviewed_at,
    )
    _validate_conflict_declaration(
        review.get("conflict_of_interest_declaration"),
        context=f"{context} conflict_of_interest_declaration",
        repository_id=repository_id,
        artifact_id=artifact_id,
        not_before=frozen_at,
        not_after=reviewed_at,
    )
    claim_decision = _decision(review.get("claim_decision"), f"{context} claim")
    if not isinstance(review.get("claim_rationale"), str) or not review["claim_rationale"].strip():
        raise ClaimReviewError(f"{context} requires a claim rationale")
    _evidence(review.get("claim_evidence"), f"{context} claim")
    links = _validate_link_decisions(review.get("link_decisions"), f"{context} links")
    return reviewer_id, reviewed_at, claim_decision, links


def _validate_adjudication(
    value: object,
    *,
    context: str,
    reviewer_ids: set[str],
    latest_reviewed_at: datetime,
    repository_id: str,
    artifact_id: str,
) -> tuple[str, dict[str, str]]:
    adjudication = _require_shape(
        value,
        required={
            "adjudicator_id",
            "domain_expertise",
            "adjudicated_at",
            "conflict_of_interest_declaration",
            "claim_decision",
            "claim_rationale",
            "claim_evidence",
            "link_decisions",
        },
        allowed={
            "adjudicator_id",
            "domain_expertise",
            "adjudicated_at",
            "conflict_of_interest_declaration",
            "claim_decision",
            "claim_rationale",
            "claim_evidence",
            "link_decisions",
        },
        context=context,
    )
    adjudicator_id = _identity(adjudication.get("adjudicator_id"), f"{context} adjudicator_id")
    if adjudicator_id in reviewer_ids:
        raise ClaimReviewError(f"{context} adjudicator must be independent")
    expertise = adjudication.get("domain_expertise")
    if not isinstance(expertise, str) or not expertise.strip():
        raise ClaimReviewError(f"{context} requires a domain-expertise statement")
    adjudicated_at = _parse_timestamp(
        adjudication.get("adjudicated_at"), f"{context} adjudicated_at"
    )
    if adjudicated_at < latest_reviewed_at:
        raise ClaimReviewError(f"{context} predates an initial review")
    _validate_conflict_declaration(
        adjudication.get("conflict_of_interest_declaration"),
        context=f"{context} conflict_of_interest_declaration",
        repository_id=repository_id,
        artifact_id=artifact_id,
        not_before=latest_reviewed_at,
        not_after=adjudicated_at,
    )
    claim_decision = _decision(adjudication.get("claim_decision"), f"{context} claim")
    if (
        not isinstance(adjudication.get("claim_rationale"), str)
        or not adjudication["claim_rationale"].strip()
    ):
        raise ClaimReviewError(f"{context} requires a claim rationale")
    _evidence(adjudication.get("claim_evidence"), f"{context} claim")
    links = _validate_link_decisions(adjudication.get("link_decisions"), f"{context} links")
    return claim_decision, links


def initialize_review(
    truth: dict[str, Any], truth_sha256: str, candidate_pair: list[str]
) -> dict[str, Any]:
    if (
        len(candidate_pair) != 2
        or len(set(candidate_pair)) != 2
        or any(not _ID_RE.fullmatch(value) for value in candidate_pair)
    ):
        raise ClaimReviewError("candidate pair requires two distinct stable run labels")
    claims = []
    for claim in truth["claims"]:
        claims.append(
            {
                "claim_id": claim["claim_id"],
                "repo_id": claim["repo_id"],
                "repo_commit": claim["repo_commit"],
                "claim_record_sha256": _canonical_sha256(claim),
                "links": [
                    {
                        "target": link["target"],
                        "link_record_sha256": _canonical_sha256(link),
                    }
                    for link in claim["expected_links"]
                ],
                "reviews": [],
                "adjudication": None,
            }
        )
    return {
        "claim_review_schema_version": CLAIM_REVIEW_SCHEMA_VERSION,
        "claim_ground_truth_sha256": truth_sha256,
        "corpus_inventory_sha256": truth["corpus_inventory_sha256"],
        "candidate_pair": candidate_pair,
        "review_policy": REVIEW_POLICY,
        "initial_review_sources": [],
        "claims": claims,
    }


def _load_object(path: Path, context: str) -> dict[str, Any]:
    try:
        value = load_json_object_bytes(path.read_bytes(), f"{context} {path}")
    except (OSError, RunContractError) as exc:
        raise ClaimReviewError(f"cannot read {context} {path}: {exc}") from exc
    return cast(dict[str, Any], value)


def _review_source_bindings(value: object) -> dict[str, str]:
    if not isinstance(value, list) or len(value) not in {0, 2}:
        raise ClaimReviewError("initial review sources must be empty or contain exactly two files")
    bindings: dict[str, str] = {}
    digests: set[str] = set()
    for number, raw in enumerate(value, 1):
        source = _require_shape(
            raw,
            required={"reviewer_id", "sha256"},
            allowed={"reviewer_id", "sha256"},
            context=f"initial review source {number}",
        )
        reviewer_id = _identity(source.get("reviewer_id"), f"initial review source {number}")
        digest = source.get("sha256")
        if (
            reviewer_id in bindings
            or not isinstance(digest, str)
            or not _SHA256_RE.fullmatch(digest)
            or digest in digests
        ):
            raise ClaimReviewError("initial review sources require distinct identities and digests")
        bindings[reviewer_id] = digest
        digests.add(digest)
    return bindings


def validate_review(
    payload: dict[str, Any],
    truth: dict[str, Any],
    truth_sha256: str,
    *,
    require_complete: bool = False,
    require_accepted: bool = False,
) -> dict[str, int]:
    """Validate exact truth binding and human-review decision contracts."""
    top_fields = {
        "claim_review_schema_version",
        "claim_ground_truth_sha256",
        "corpus_inventory_sha256",
        "candidate_pair",
        "review_policy",
        "initial_review_sources",
        "claims",
    }
    _require_shape(payload, required=top_fields, allowed=top_fields, context="claim review")
    if payload.get("claim_review_schema_version") != CLAIM_REVIEW_SCHEMA_VERSION:
        raise ClaimReviewError("unsupported claim-review schema")
    if payload.get("claim_ground_truth_sha256") != truth_sha256:
        raise ClaimReviewError("claim review targets a different candidate truth SHA-256")
    if payload.get("corpus_inventory_sha256") != truth.get("corpus_inventory_sha256"):
        raise ClaimReviewError("claim review targets a different corpus inventory")
    candidate_pair = payload.get("candidate_pair")
    if (
        not isinstance(candidate_pair, list)
        or len(candidate_pair) != 2
        or len({str(value) for value in candidate_pair}) != 2
        or any(
            not isinstance(value, str) or not _ID_RE.fullmatch(value) for value in candidate_pair
        )
    ):
        raise ClaimReviewError("claim review has an invalid candidate pair")
    if payload.get("review_policy") != REVIEW_POLICY:
        raise ClaimReviewError("claim review changes the frozen review policy")
    source_bindings = _review_source_bindings(payload.get("initial_review_sources"))

    records = payload.get("claims")
    truth_claims = truth.get("claims")
    if not isinstance(records, list) or not isinstance(truth_claims, list):
        raise ClaimReviewError("claim review has invalid claim records")
    if len(records) != len(truth_claims):
        raise ClaimReviewError("claim review does not cover every candidate truth claim")
    frozen_at = _parse_timestamp(truth.get("frozen_at"), "candidate truth frozen_at")
    completed = 0
    accepted = 0
    adjudicated = 0
    reviewer_sets: list[set[str]] = []
    for number, (record_raw, claim_raw) in enumerate(zip(records, truth_claims, strict=True), 1):
        context = f"claim review {number}"
        record = _require_shape(
            record_raw,
            required={
                "claim_id",
                "repo_id",
                "repo_commit",
                "claim_record_sha256",
                "links",
                "reviews",
                "adjudication",
            },
            allowed={
                "claim_id",
                "repo_id",
                "repo_commit",
                "claim_record_sha256",
                "links",
                "reviews",
                "adjudication",
            },
            context=context,
        )
        if not isinstance(claim_raw, dict):
            raise ClaimReviewError(f"candidate truth claim {number} is invalid")
        for field in ("claim_id", "repo_id", "repo_commit"):
            if record.get(field) != claim_raw.get(field):
                raise ClaimReviewError(f"{context} changes candidate truth {field}")
        if record.get("claim_record_sha256") != _canonical_sha256(claim_raw):
            raise ClaimReviewError(f"{context} has a stale claim-record digest")
        links = record.get("links")
        truth_links = claim_raw.get("expected_links")
        if (
            not isinstance(links, list)
            or not isinstance(truth_links, list)
            or len(links) != len(TARGETS)
            or len(truth_links) != len(TARGETS)
        ):
            raise ClaimReviewError(f"{context} does not bind every candidate truth link")
        expected_link_bindings = {
            str(link["target"]): _canonical_sha256(link)
            for link in truth_links
            if isinstance(link, dict) and isinstance(link.get("target"), str)
        }
        observed_link_bindings: dict[str, str] = {}
        for link_number, raw_link in enumerate(links, 1):
            link = _require_shape(
                raw_link,
                required={"target", "link_record_sha256"},
                allowed={"target", "link_record_sha256"},
                context=f"{context} link binding {link_number}",
            )
            target = link.get("target")
            digest = link.get("link_record_sha256")
            if (
                not isinstance(target, str)
                or target in observed_link_bindings
                or not isinstance(digest, str)
                or not _SHA256_RE.fullmatch(digest)
            ):
                raise ClaimReviewError(f"{context} has an invalid link binding")
            observed_link_bindings[target] = digest
        if observed_link_bindings != expected_link_bindings:
            raise ClaimReviewError(f"{context} changes a candidate truth link binding")

        reviews = record.get("reviews")
        if not isinstance(reviews, list) or len(reviews) > 2:
            raise ClaimReviewError(f"{context} must contain at most two initial reviews")
        review_ids: set[str] = set()
        review_times: list[datetime] = []
        claim_decisions: list[str] = []
        link_decisions: list[dict[str, str]] = []
        for review_number, review in enumerate(reviews, 1):
            reviewer_id, reviewed_at, claim_decision, decisions = _validate_initial_review(
                review,
                context=f"{context}, reviewer {review_number}",
                frozen_at=frozen_at,
                repository_id=str(record["repo_id"]),
                artifact_id=str(record["claim_id"]),
            )
            if reviewer_id in review_ids:
                raise ClaimReviewError(f"{context} repeats reviewer {reviewer_id!r}")
            review_ids.add(reviewer_id)
            review_times.append(reviewed_at)
            claim_decisions.append(claim_decision)
            link_decisions.append(decisions)
        reviewer_sets.append(review_ids)

        decision_disagreement = len(reviews) == 2 and (
            len(set(claim_decisions)) > 1
            or any(
                len({decisions[target] for decisions in link_decisions}) > 1 for target in TARGETS
            )
        )
        adjudication = record.get("adjudication")
        resolved_claim: str | None = None
        resolved_links: dict[str, str] = {}
        if adjudication is not None:
            if len(reviews) != 2 or not decision_disagreement:
                raise ClaimReviewError(
                    f"{context} adjudication requires two disagreeing initial decisions"
                )
            resolved_claim, resolved_links = _validate_adjudication(
                adjudication,
                context=f"{context}, adjudication",
                reviewer_ids=review_ids,
                latest_reviewed_at=max(review_times),
                repository_id=str(record["repo_id"]),
                artifact_id=str(record["claim_id"]),
            )
            adjudicated += 1
        elif decision_disagreement:
            resolved_claim = None
        elif len(reviews) == 2:
            resolved_claim = claim_decisions[0]
            resolved_links = link_decisions[0]

        is_complete = len(reviews) == 2 and (not decision_disagreement or adjudication is not None)
        is_accepted = (
            is_complete
            and resolved_claim == "verified"
            and all(resolved_links.get(target) == "verified" for target in TARGETS)
        )
        completed += is_complete
        accepted += is_accepted
        if require_complete and not is_complete:
            raise ClaimReviewError(
                f"{context} lacks two independent reviews or required adjudication"
            )
        if require_accepted and not is_accepted:
            raise ClaimReviewError(f"{context} is not accepted as candidate ground truth")

    source_reviewers = set(source_bindings)
    if source_bindings and any(reviewers != source_reviewers for reviewers in reviewer_sets):
        raise ClaimReviewError(
            "merged review decisions do not match the two bound independent-review sources"
        )
    if require_complete and len(source_bindings) != 2:
        raise ClaimReviewError(
            "completed claim review requires deterministic merge provenance from two "
            "independent review files"
        )

    return {
        "claims": len(records),
        "completed": completed,
        "accepted": accepted,
        "adjudicated": adjudicated,
    }


def _single_reviewer_id(payload: dict[str, Any], truth: dict[str, Any], truth_sha256: str) -> str:
    validate_review(payload, truth, truth_sha256)
    if payload["initial_review_sources"] != []:
        raise ClaimReviewError("an independent reviewer file cannot already be a merged review")
    reviewer_ids: set[str] = set()
    for number, claim in enumerate(payload["claims"], 1):
        reviews = claim["reviews"]
        if len(reviews) != 1 or claim["adjudication"] is not None:
            raise ClaimReviewError(
                f"independent reviewer file claim {number} must contain exactly one "
                "initial review and no adjudication"
            )
        reviewer_ids.add(str(reviews[0]["reviewer_id"]))
    if len(reviewer_ids) != 1:
        raise ClaimReviewError("one independent reviewer file must use one reviewer identity")
    return next(iter(reviewer_ids))


def merge_independent_reviews(
    reviews: list[dict[str, Any]],
    source_sha256: list[str],
    truth: dict[str, Any],
    truth_sha256: str,
) -> dict[str, Any]:
    """Merge two separately completed, blinded reviewer files deterministically."""
    if len(reviews) != 2 or len(source_sha256) != 2:
        raise ClaimReviewError("merge requires exactly two independent reviewer files")
    if len(set(source_sha256)) != 2 or any(
        not _SHA256_RE.fullmatch(digest) for digest in source_sha256
    ):
        raise ClaimReviewError("independent reviewer files require two distinct SHA-256 digests")
    reviewer_ids = [_single_reviewer_id(review, truth, truth_sha256) for review in reviews]
    if len(set(reviewer_ids)) != 2:
        raise ClaimReviewError("independent reviewer files must use distinct reviewer identities")
    if reviews[0]["candidate_pair"] != reviews[1]["candidate_pair"]:
        raise ClaimReviewError("independent reviewer files target different candidate pairs")

    indexed = sorted(zip(reviewer_ids, source_sha256, reviews, strict=True))
    merged = copy.deepcopy(indexed[0][2])
    merged["initial_review_sources"] = [
        {"reviewer_id": reviewer_id, "sha256": digest} for reviewer_id, digest, _ in indexed
    ]
    for claim_number, claim in enumerate(merged["claims"]):
        claim["reviews"] = [
            copy.deepcopy(review["claims"][claim_number]["reviews"][0]) for _, _, review in indexed
        ]
        claim["adjudication"] = None
    validate_review(merged, truth, truth_sha256)
    return merged


def verify_independent_review_sources(
    merged: dict[str, Any],
    reviews: list[dict[str, Any]],
    source_sha256: list[str],
    truth: dict[str, Any],
    truth_sha256: str,
) -> None:
    """Verify source bytes and initial decisions behind a merged review."""
    validate_review(merged, truth, truth_sha256)
    reconstructed = merge_independent_reviews(reviews, source_sha256, truth, truth_sha256)
    if reconstructed["initial_review_sources"] != merged.get("initial_review_sources"):
        raise ClaimReviewError("merged review source hashes do not match supplied reviewer files")
    for number, (expected, observed) in enumerate(
        zip(reconstructed["claims"], merged.get("claims", []), strict=True), 1
    ):
        if expected["reviews"] != observed.get("reviews"):
            raise ClaimReviewError(
                f"merged review claim {number} changes an independent initial decision"
            )


def _latest_decision_timestamp(payload: dict[str, Any]) -> datetime:
    latest: datetime | None = None
    for claim in payload["claims"]:
        for review in claim["reviews"]:
            reviewed_at = _parse_timestamp(review["reviewed_at"], "review timestamp")
            latest = max(latest, reviewed_at) if latest else reviewed_at
        if claim["adjudication"] is not None:
            adjudicated_at = _parse_timestamp(
                claim["adjudication"]["adjudicated_at"], "adjudication timestamp"
            )
            latest = max(latest, adjudicated_at) if latest else adjudicated_at
    if latest is None:
        raise ClaimReviewError("claim review has no completed human decisions")
    return latest


def validate_review_for_candidate_run(
    payload: dict[str, Any],
    truth: dict[str, Any],
    truth_sha256: str,
    candidate_run: str,
    started_at: object,
) -> dict[str, int]:
    """Require accepted pre-scan review for one pre-registered candidate label."""
    summary = validate_review(
        payload,
        truth,
        truth_sha256,
        require_complete=True,
        require_accepted=True,
    )
    candidate_pair = cast(list[str], payload["candidate_pair"])
    if candidate_run not in candidate_pair:
        raise ClaimReviewError(
            f"run label {candidate_run!r} is not in the pre-registered candidate pair"
        )
    run_started_at = _parse_timestamp(started_at, "candidate run started_at")
    if _latest_decision_timestamp(payload) >= run_started_at:
        raise ClaimReviewError("candidate run began before human review was complete")
    return summary


def validate_candidate_runs(
    payload: dict[str, Any],
    truth: dict[str, Any],
    truth_sha256: str,
    review_sha256: str,
    runs: list[Path],
) -> None:
    if len(runs) != 2:
        raise ClaimReviewError("candidate validation requires exactly two run directories")
    candidate_pair = payload["candidate_pair"]
    if [run.name for run in runs] != candidate_pair:
        raise ClaimReviewError("run directory names do not match the pre-registered pair")
    for run in runs:
        try:
            metadata, _, _ = validate_run_evidence(run)
            require_current_harness_file(metadata, "scripts/claim_review.py", Path(__file__))
        except RunContractError as exc:
            raise ClaimReviewError(f"invalid candidate run {run}: {exc}") from exc
        if metadata.get("claim_ground_truth_sha256") != truth_sha256:
            raise ClaimReviewError(f"candidate run {run} is bound to different claim truth")
        if metadata.get("claim_review_sha256") != review_sha256:
            raise ClaimReviewError(f"candidate run {run} is bound to a different claim review")
        if metadata.get("candidate_run_name") != run.name:
            raise ClaimReviewError(f"candidate run {run} has a mismatched candidate label")
        validate_review_for_candidate_run(
            payload,
            truth,
            truth_sha256,
            run.name,
            metadata.get("started_at"),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="create an empty, bound review scaffold")
    init_parser.add_argument("--claims", type=Path, required=True)
    init_parser.add_argument("--repos", type=Path, required=True)
    init_parser.add_argument("--clones", type=Path, required=True)
    init_parser.add_argument("--candidate-run", action="append", required=True)
    init_parser.add_argument("--out", type=Path, required=True)

    merge_parser = subparsers.add_parser(
        "merge", help="merge two separately completed blinded reviewer files"
    )
    merge_parser.add_argument("--review", type=Path, action="append", required=True)
    merge_parser.add_argument("--claims", type=Path, required=True)
    merge_parser.add_argument("--out", type=Path, required=True)

    validate_parser = subparsers.add_parser("validate", help="validate a claim-review artifact")
    validate_parser.add_argument("--review", type=Path, required=True)
    validate_parser.add_argument("--claims", type=Path, required=True)
    validate_parser.add_argument("--require-complete", action="store_true")
    validate_parser.add_argument("--require-accepted", action="store_true")
    validate_parser.add_argument("--initial-review", type=Path, action="append", default=[])
    validate_parser.add_argument("--run", type=Path, action="append", default=[])
    args = parser.parse_args()

    try:
        if args.command == "init":
            ensure_output_outside(args.out, [args.clones])
            if args.out.exists():
                raise ClaimReviewError(f"refusing to overwrite existing claim review: {args.out}")
            truth = validate_ground_truth(args.claims, args.repos, args.clones)
            payload = initialize_review(truth, sha256_file(args.claims), list(args.candidate_run))
            args.out.parent.mkdir(parents=True, exist_ok=True)
            write_json(args.out, payload)
            print(
                f"wrote empty claim-review scaffold {args.out} for "
                f"{len(payload['claims'])} claim(s); no human decisions recorded"
            )
            return 0

        truth = _load_object(args.claims, "candidate truth")
        validate_ground_truth_structure(truth)
        truth_digest = sha256_file(args.claims)
        if args.command == "merge":
            if len(args.review) != 2:
                raise ClaimReviewError("merge requires exactly two --review inputs")
            if args.out.exists() or args.out.is_symlink():
                raise ClaimReviewError(f"refusing to overwrite merged claim review: {args.out}")
            reviews = [_load_object(path, "independent review") for path in args.review]
            merged = merge_independent_reviews(
                reviews,
                [sha256_file(path) for path in args.review],
                truth,
                truth_digest,
            )
            args.out.parent.mkdir(parents=True, exist_ok=True)
            write_json(args.out, merged)
            print(
                f"merged two independent reviewer files into {args.out}; "
                "no adjudication decisions added"
            )
            return 0

        payload = _load_object(args.review, "claim review")
        source_verification_required = (
            args.require_complete or args.require_accepted or bool(args.run)
        )
        if source_verification_required and len(args.initial_review) != 2:
            raise ClaimReviewError(
                "completed validation requires exactly two --initial-review inputs"
            )
        summary = validate_review(
            payload,
            truth,
            truth_digest,
            require_complete=args.require_complete or args.require_accepted or bool(args.run),
            require_accepted=args.require_accepted or bool(args.run),
        )
        if args.initial_review:
            if len(args.initial_review) != 2:
                raise ClaimReviewError("provide exactly two --initial-review inputs")
            independent_reviews = [
                _load_object(path, "independent review") for path in args.initial_review
            ]
            verify_independent_review_sources(
                payload,
                independent_reviews,
                [sha256_file(path) for path in args.initial_review],
                truth,
                truth_digest,
            )
        if args.run:
            validate_candidate_runs(
                payload,
                truth,
                truth_digest,
                sha256_file(args.review),
                args.run,
            )
        print(
            "valid claim-review artifact: "
            f"completed={summary['completed']}/{summary['claims']} "
            f"accepted={summary['accepted']}/{summary['claims']} "
            f"adjudicated={summary['adjudicated']}"
        )
        return 0
    except (ClaimGroundTruthError, ClaimReviewError, RunContractError, OSError) as exc:
        sys.exit(f"invalid claim review: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
