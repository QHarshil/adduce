#!/usr/bin/env python3
"""Compute descriptive claim-review agreement and candidate metrics from completed artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

if __package__:
    from .claim_ground_truth import (
        TARGETS,
        ClaimGroundTruthError,
        validate_ground_truth_structure,
    )
    from .claim_review import (
        DECISIONS,
        ClaimReviewError,
        validate_review,
        verify_independent_review_sources,
    )
    from .reviewer_feedback import ReviewerFeedbackError, validate_feedback
    from .run_contract import (
        RUN_META_NAME,
        RunContractError,
        load_json_object_bytes,
        sha256_file,
        validate_run_evidence,
    )
else:
    from claim_ground_truth import (
        TARGETS,
        ClaimGroundTruthError,
        validate_ground_truth_structure,
    )
    from claim_review import (
        DECISIONS,
        ClaimReviewError,
        validate_review,
        verify_independent_review_sources,
    )
    from reviewer_feedback import ReviewerFeedbackError, validate_feedback
    from run_contract import (
        RUN_META_NAME,
        RunContractError,
        load_json_object_bytes,
        sha256_file,
        validate_run_evidence,
    )

CLAIM_REVIEW_METRICS_SCHEMA_VERSION = 1
CLAIM_POSITION = "claim"
POSITIONS_PER_CLAIM = len(TARGETS) + 1
RESOLUTION_CLASSES = ("resolved", "unresolved", "not_applicable")
OTHER_RESOLUTION = "other"
DECLINING_EXPECTATIONS = ("unresolved", "not_applicable")
_OBSERVED_RESOLUTIONS = {
    "resolved": "resolved",
    "unresolved": "unresolved",
    "absent": "not_applicable",
}
AGREEMENT_FORMULAS = (
    "Reviewer completion = decisions recorded in one reviewer's file / (claims x 11), where 11 "
    "is one claim-level decision plus ten link-level decisions. Reported per reviewer; the 220 "
    "decisions across two files are never pooled into one figure.",
    "Raw agreement = comparison positions where both reviewers recorded the same value / "
    "(claims x 11) comparison positions. A position where either reviewer has recorded nothing "
    "is reported separately as not comparable.",
    "Claim-level agreement = claims where both claim-level decisions match / claims.",
    "Link-level agreement = links where both decisions match / (claims x 10).",
    "Agreement by target = claims where both reviewers agree at that target / claims, for each "
    "of the ten targets.",
    "Disagreement count = comparison positions minus the raw-agreement numerator, listed item "
    "by item with the claim identifier, the target and both values.",
    "Adjudication burden = claims with at least one disagreeing position / claims. Each "
    "adjudicated claim carries 11 decisions.",
    "revision_required and unclear counts = decisions recorded with that value / (claims x 11) "
    "per reviewer.",
    "Cohen's kappa = (p_o - p_e) / (1 - p_e), with p_o the raw agreement over compared "
    "positions and p_e the sum over decision values of the product of the two reviewers' "
    "marginal proportions. It is undefined when p_e = 1 and is then reported as null with its "
    "reason.",
    "Review duration = the sum of self-reported minutes / claims with a recorded time, which "
    "may be fewer than the reviewer's claims.",
    "Median minutes per claim = the middle value of the per-claim minutes, or the mean of the "
    "two middle values, reported with n, the minimum and the maximum.",
    "Validator failures and clarification requests = the reported count / that reviewer's claims.",
)
CANDIDATE_FORMULAS = (
    "Candidate confusion matrix: rows are the resolution the accepted record expects, columns "
    "are the resolution the candidate run reports. A candidate resolution outside resolved, "
    "unresolved and not_applicable is counted in the 'other' column, and an accepted link the "
    "candidate never evaluated is counted there too.",
    "Candidate accuracy = accepted links whose candidate resolution equals the expected "
    "resolution / accepted links.",
    "Per-class precision = TP / (TP + FP); recall = TP / (TP + FN); "
    "F1 = 2 x precision x recall / (precision + recall), over accepted links.",
    "Macro-F1 = the unweighted mean of the per-class F1 values over the classes with non-zero "
    "support in the accepted record; the classes averaged are listed. A class with zero "
    "support is excluded and reported with a null F1 and its reason. A supported class the "
    "candidate never predicts has precision 0, recall 0 and F1 0 by convention.",
    "Per-target candidate accuracy = accepted links at that target whose candidate resolution "
    "equals the expected resolution / accepted links at that target.",
    "Declining-when-expected = accepted links expecting unresolved or not_applicable where the "
    "candidate also declined / accepted links with a declining expectation. Over-declining = "
    "accepted links expecting resolved where the candidate declined / accepted links expecting "
    "resolved. The candidate declined whenever its reported resolution is not 'resolved'.",
    "Operational failures are counted by category over the repositories attempted in one run "
    "and are never summed into a single figure.",
)
LIMITATIONS = (
    "This pilot establishes no population false-positive rate. The corpus is a purposive "
    "selection of repositories, not a sample from a defined population, and every proportion "
    "here is an unweighted summary of the reviewed records.",
    "This pilot establishes no calibrated score threshold, no tier boundary and no badge "
    "prediction. No figure here supports a cut-off.",
    "No figure here generalizes beyond the reviewed records. Each count belongs to the "
    "specific claims, links and runs named in the inputs above.",
    "Static analysis is not execution. Nothing here reports that a repository ran, reproduced "
    "a result, or is reproducible; the candidate dimensions compare a static resolution with "
    "an accepted human record.",
    "Cohen's kappa is a descriptive diagnostic. It is reported beside the raw agreement and "
    "disagreement counts and never replaces them, and it is undefined in degenerate "
    "single-class cases.",
    "Every figure states its numerator and its denominator. A single record moves most of "
    "these figures visibly at this size, so a ratio is not reportable on its own.",
)


class ClaimReviewMetricsError(ValueError):
    """A metrics input is missing, unbound, or has not passed the gate it depends on."""


@dataclass(frozen=True)
class Ratio:
    """A count with the denominator it was measured against."""

    numerator: int
    denominator: int

    def to_json(self) -> dict[str, Any]:
        ratio = None if self.denominator == 0 else self.numerator / self.denominator
        return {"numerator": self.numerator, "denominator": self.denominator, "ratio": ratio}


@dataclass(frozen=True)
class ReviewerFile:
    path: Path
    sha256: str
    reviewer_id: str
    payload: dict[str, Any]
    decisions: dict[str, dict[str, str]]
    declarations: int


@dataclass(frozen=True)
class LoadedFile:
    path: Path
    sha256: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class Comparison:
    claim_id: str
    target: str
    decision_a: str
    decision_b: str

    @property
    def agrees(self) -> bool:
        return self.decision_a == self.decision_b


@dataclass(frozen=True)
class AcceptedLink:
    claim_id: str
    repo_id: str
    target: str
    expected: str


@dataclass(frozen=True)
class CandidateRun:
    path: Path
    run_id: str
    meta_sha256: str
    metadata: dict[str, Any]
    rows: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class ClaimLinkOutput:
    path: Path
    sha256: str
    run_id: str
    observed: dict[tuple[str, str], str]
    claim_status: dict[str, str]


def _load_object(path: Path, context: str) -> dict[str, Any]:
    try:
        payload = load_json_object_bytes(path.read_bytes(), f"{context} {path}")
    except (OSError, RunContractError) as exc:
        raise ClaimReviewMetricsError(f"cannot read {context} {path}: {exc}") from exc
    return cast(dict[str, Any], payload)


def _file_record(path: Path, sha256: str) -> dict[str, str]:
    return {"path": str(path), "sha256": sha256}


def load_truth(path: Path) -> tuple[dict[str, Any], str]:
    """Load and structurally validate the frozen candidate truth."""
    truth = _load_object(path, "candidate truth")
    try:
        validate_ground_truth_structure(truth)
    except ClaimGroundTruthError as exc:
        raise ClaimReviewMetricsError(f"candidate truth {path} is invalid: {exc}") from exc
    return truth, sha256_file(path)


def load_independent_review(
    path: Path, truth: dict[str, Any], truth_sha256: str, label: str
) -> ReviewerFile:
    """Load one single-reviewer file and index its decisions by claim and target."""
    payload = _load_object(path, label)
    try:
        validate_review(payload, truth, truth_sha256)
    except ClaimReviewError as exc:
        raise ClaimReviewMetricsError(f"{label} {path} is not a valid claim review: {exc}") from exc
    if payload["initial_review_sources"]:
        raise ClaimReviewMetricsError(
            f"{label} {path} is a merged claim review, not an independent reviewer file"
        )
    reviewer_ids: set[str] = set()
    decisions: dict[str, dict[str, str]] = {}
    declarations = 0
    for record in payload["claims"]:
        claim_id = str(record["claim_id"])
        if record["adjudication"] is not None:
            raise ClaimReviewMetricsError(
                f"{label} {path} carries an adjudication for {claim_id}; an independent "
                "reviewer file records one reviewer's own decisions only"
            )
        reviews = record["reviews"]
        if len(reviews) > 1:
            raise ClaimReviewMetricsError(
                f"{label} {path} carries {len(reviews)} reviews for {claim_id}; an independent "
                "reviewer file carries at most one"
            )
        if not reviews:
            continue
        review = reviews[0]
        reviewer_ids.add(str(review["reviewer_id"]))
        declarations += all(
            isinstance(review.get(name), dict)
            for name in ("blinding_declaration", "conflict_of_interest_declaration")
        )
        entry = {CLAIM_POSITION: str(review["claim_decision"])}
        for link in review["link_decisions"]:
            entry[str(link["target"])] = str(link["decision"])
        decisions[claim_id] = entry
    if len(reviewer_ids) != 1:
        raise ClaimReviewMetricsError(
            f"{label} {path} must record exactly one reviewer identity, found "
            f"{sorted(reviewer_ids)}"
        )
    return ReviewerFile(
        path=path,
        sha256=sha256_file(path),
        reviewer_id=next(iter(reviewer_ids)),
        payload=payload,
        decisions=decisions,
        declarations=declarations,
    )


def require_comparable_reviewers(review_a: ReviewerFile, review_b: ReviewerFile) -> None:
    """Require two distinct reviewers bound to the same truth, inventory and candidate pair."""
    for reviewer_field, description in (
        ("claim_ground_truth_sha256", "candidate truth"),
        ("corpus_inventory_sha256", "corpus inventory"),
        ("candidate_pair", "candidate pair"),
    ):
        left = review_a.payload[reviewer_field]
        right = review_b.payload[reviewer_field]
        if left != right:
            raise ClaimReviewMetricsError(
                f"the two reviewer files bind a different {description}: "
                f"{review_a.path} has {left!r} and {review_b.path} has {right!r}"
            )
    if review_a.reviewer_id == review_b.reviewer_id:
        raise ClaimReviewMetricsError(
            f"both reviewer files record reviewer {review_a.reviewer_id!r}; agreement requires "
            "two distinct reviewer identities"
        )
    if review_a.sha256 == review_b.sha256:
        raise ClaimReviewMetricsError("the same reviewer file was supplied twice")


def compare_reviewers(
    review_a: ReviewerFile, review_b: ReviewerFile, claim_ids: Sequence[str]
) -> tuple[list[Comparison], list[str]]:
    """Return one comparison per position both reviewers recorded, and the claims neither pair."""
    comparisons: list[Comparison] = []
    not_comparable: list[str] = []
    for claim_id in claim_ids:
        left = review_a.decisions.get(claim_id)
        right = review_b.decisions.get(claim_id)
        if left is None or right is None:
            not_comparable.append(claim_id)
            continue
        for position in (CLAIM_POSITION, *TARGETS):
            comparisons.append(Comparison(claim_id, position, left[position], right[position]))
    return comparisons, not_comparable


def cohens_kappa(comparisons: Sequence[Comparison]) -> dict[str, Any]:
    """Return descriptive Cohen's kappa, or null with the reason it is undefined."""
    total = len(comparisons)
    if total == 0:
        return {
            "value": None,
            "undefined_reason": "there are no comparison positions",
            "interpretation": "descriptive",
        }
    left = Counter(comparison.decision_a for comparison in comparisons)
    right = Counter(comparison.decision_b for comparison in comparisons)
    observed = sum(1 for comparison in comparisons if comparison.agrees) / total
    expected = sum(left[value] * right[value] for value in sorted(DECISIONS)) / (total * total)
    if expected >= 1.0:
        return {
            "value": None,
            "undefined_reason": (
                "one decision value was observed on both sides, so expected agreement is 1.0 "
                "and the kappa denominator is 0"
            ),
            "interpretation": "descriptive",
        }
    return {
        "value": (observed - expected) / (1.0 - expected),
        "undefined_reason": None,
        "interpretation": "descriptive",
    }


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return float(ordered[middle])
    return (float(ordered[middle - 1]) + float(ordered[middle])) / 2.0


def agreement_metrics(
    comparisons: Sequence[Comparison], not_comparable: Sequence[str], claim_count: int
) -> dict[str, Any]:
    """Return raw, claim-level, link-level and per-target agreement with every denominator."""
    agreements = sum(1 for comparison in comparisons if comparison.agrees)
    claim_level = sum(
        1 for comparison in comparisons if comparison.target == CLAIM_POSITION and comparison.agrees
    )
    link_level = sum(
        1 for comparison in comparisons if comparison.target != CLAIM_POSITION and comparison.agrees
    )
    by_target = []
    for target in TARGETS:
        matched = sum(
            1 for comparison in comparisons if comparison.target == target and comparison.agrees
        )
        by_target.append({"target": target, "agreement": Ratio(matched, claim_count).to_json()})
    return {
        "raw": Ratio(agreements, claim_count * POSITIONS_PER_CLAIM).to_json(),
        "claim_level": Ratio(claim_level, claim_count).to_json(),
        "link_level": Ratio(link_level, claim_count * len(TARGETS)).to_json(),
        "by_target": by_target,
        "positions_compared": len(comparisons),
        "positions_not_comparable": len(not_comparable),
        "claims_not_comparable": list(not_comparable),
        "disagreement_count": len(comparisons) - agreements,
        "cohens_kappa": cohens_kappa(comparisons),
    }


def _completion(review: ReviewerFile, claim_count: int) -> dict[str, Any]:
    recorded = sum(len(entry) for entry in review.decisions.values())
    return {
        "reviewer_id": review.reviewer_id,
        "decisions": Ratio(recorded, claim_count * POSITIONS_PER_CLAIM).to_json(),
        "finalized_claims": Ratio(len(review.decisions), claim_count).to_json(),
        "declarations": Ratio(review.declarations, claim_count).to_json(),
    }


def _decision_counts(review: ReviewerFile, claim_count: int) -> dict[str, Any]:
    counts = Counter(decision for entry in review.decisions.values() for decision in entry.values())
    denominator = claim_count * POSITIONS_PER_CLAIM
    return {
        "reviewer_id": review.reviewer_id,
        "revision_required": Ratio(counts["revision_required"], denominator).to_json(),
        "unclear": Ratio(counts["unclear"], denominator).to_json(),
        "verified": Ratio(counts["verified"], denominator).to_json(),
    }


def load_merged_review(
    path: Path,
    truth: dict[str, Any],
    truth_sha256: str,
    *,
    require_accepted: bool,
) -> LoadedFile:
    """Load the merged review, requiring completion and acceptance when the gate demands it."""
    payload = _load_object(path, "merged claim review")
    try:
        validate_review(
            payload,
            truth,
            truth_sha256,
            require_complete=require_accepted,
            require_accepted=require_accepted,
        )
    except ClaimReviewError as exc:
        raise ClaimReviewMetricsError(
            f"merged claim review {path} did not pass validation: {exc}"
        ) from exc
    return LoadedFile(path=path, sha256=sha256_file(path), payload=payload)


def verify_merge_sources(
    merged: LoadedFile,
    review_a: ReviewerFile,
    review_b: ReviewerFile,
    truth: dict[str, Any],
    truth_sha256: str,
) -> None:
    """Require the merged review to reconstruct exactly from the two supplied reviewer files."""
    try:
        verify_independent_review_sources(
            merged.payload,
            [review_a.payload, review_b.payload],
            [review_a.sha256, review_b.sha256],
            truth,
            truth_sha256,
        )
    except ClaimReviewError as exc:
        raise ClaimReviewMetricsError(
            f"merged claim review {merged.path} does not reconstruct from {review_a.path} and "
            f"{review_b.path}: {exc}"
        ) from exc


def _merged_block(merged: LoadedFile, truth: dict[str, Any], truth_sha256: str) -> dict[str, Any]:
    summary = validate_review(merged.payload, truth, truth_sha256)
    return {
        "path": str(merged.path),
        "sha256": merged.sha256,
        "claims": summary["claims"],
        "completed_claims": Ratio(summary["completed"], summary["claims"]).to_json(),
        "accepted_claims": Ratio(summary["accepted"], summary["claims"]).to_json(),
        "adjudicated_claims": Ratio(summary["adjudicated"], summary["claims"]).to_json(),
        "initial_review_sources": [
            {"reviewer_id": str(source["reviewer_id"]), "sha256": str(source["sha256"])}
            for source in merged.payload["initial_review_sources"]
        ],
    }


def load_feedback(
    path: Path,
    reviews: Sequence[ReviewerFile],
    claim_ids: Sequence[str],
) -> tuple[ReviewerFile, LoadedFile]:
    """Load one submitted feedback artifact and bind it to the reviewer file it describes."""
    payload = _load_object(path, "reviewer feedback")
    try:
        summary = validate_feedback(payload)
    except ReviewerFeedbackError as exc:
        raise ClaimReviewMetricsError(f"reviewer feedback {path} is invalid: {exc}") from exc
    if not summary["submitted"]:
        raise ClaimReviewMetricsError(
            f"reviewer feedback {path} has not been submitted; it carries no ratings and no "
            "counts to summarise"
        )
    bound = [review for review in reviews if review.sha256 == payload["review_artifact_sha256"]]
    if not bound:
        raise ClaimReviewMetricsError(
            f"reviewer feedback {path} is bound to review artifact "
            f"{payload['review_artifact_sha256']}, which is neither supplied reviewer file"
        )
    review = bound[0]
    if review.reviewer_id != payload["reviewer_id"]:
        raise ClaimReviewMetricsError(
            f"reviewer feedback {path} reports reviewer {payload['reviewer_id']!r} but "
            f"{review.path} records reviewer {review.reviewer_id!r}"
        )
    unknown = sorted(set(payload["minutes_by_claim"]) - set(claim_ids))
    if unknown:
        raise ClaimReviewMetricsError(
            f"reviewer feedback {path} records minutes for claims outside the frozen truth: "
            f"{unknown}"
        )
    return review, LoadedFile(path=path, sha256=sha256_file(path), payload=payload)


def _burden_block(review: ReviewerFile, feedback: LoadedFile, claim_count: int) -> dict[str, Any]:
    minutes = [float(value) for value in feedback.payload["minutes_by_claim"].values()]
    return {
        "reviewer_id": review.reviewer_id,
        "feedback_path": str(feedback.path),
        "feedback_sha256": feedback.sha256,
        "review_artifact_sha256": feedback.payload["review_artifact_sha256"],
        "duration": {
            "total_minutes": sum(minutes),
            "timed_claims": len(minutes),
            "claims": claim_count,
        },
        "median_minutes_per_claim": {
            "value": _median(minutes),
            "n": len(minutes),
            "minimum": min(minutes) if minutes else None,
            "maximum": max(minutes) if minutes else None,
        },
        "validator_failures": Ratio(
            int(feedback.payload["validator_failure_count"]), claim_count
        ).to_json(),
        "clarification_requests": Ratio(
            int(feedback.payload["clarification_request_count"]), claim_count
        ).to_json(),
        "ratings": dict(feedback.payload["ratings"]),
        "free_text_answered": {
            "most_confusing_instruction": bool(feedback.payload["most_confusing_instruction"]),
            "missing_tool_or_material": bool(feedback.payload["missing_tool_or_material"]),
        },
    }


def pre_candidate_metrics(
    truth: LoadedFile,
    review_a: ReviewerFile,
    review_b: ReviewerFile,
    *,
    merged: dict[str, Any] | None = None,
    burden: Sequence[dict[str, Any]] = (),
    feedback_inputs: Sequence[dict[str, str]] = (),
) -> dict[str, Any]:
    """Build the pre-candidate metrics document from validated inputs."""
    claim_ids = [str(claim["claim_id"]) for claim in truth.payload["claims"]]
    comparisons, not_comparable = compare_reviewers(review_a, review_b, claim_ids)
    disagreements = [
        {
            "claim_id": comparison.claim_id,
            "target": comparison.target,
            review_a.reviewer_id: comparison.decision_a,
            review_b.reviewer_id: comparison.decision_b,
        }
        for comparison in comparisons
        if not comparison.agrees
    ]
    adjudication_needed = sorted(
        {comparison.claim_id for comparison in comparisons if not comparison.agrees}
    )
    claim_count = len(claim_ids)
    merged_input = None if merged is None else _file_record(Path(merged["path"]), merged["sha256"])
    return {
        "claim_review_metrics_schema_version": CLAIM_REVIEW_METRICS_SCHEMA_VERSION,
        "mode": "pre-candidate",
        "inputs": {
            "claims": _file_record(truth.path, truth.sha256),
            "review_a": _file_record(review_a.path, review_a.sha256),
            "review_b": _file_record(review_b.path, review_b.sha256),
            "merged": merged_input,
            "feedback": list(feedback_inputs),
        },
        "scope": {
            "claims": claim_count,
            "targets": list(TARGETS),
            "positions_per_claim": POSITIONS_PER_CLAIM,
            "comparison_positions": claim_count * POSITIONS_PER_CLAIM,
            "decision_values": sorted(DECISIONS),
            "reviewers": [review_a.reviewer_id, review_b.reviewer_id],
        },
        "completion": [
            _completion(review_a, claim_count),
            _completion(review_b, claim_count),
        ],
        "agreement": agreement_metrics(comparisons, not_comparable, claim_count),
        "disagreements": disagreements,
        "decision_counts": [
            _decision_counts(review_a, claim_count),
            _decision_counts(review_b, claim_count),
        ],
        "adjudication": {
            "claims_needing_adjudication": Ratio(len(adjudication_needed), claim_count).to_json(),
            "claim_ids": adjudication_needed,
            "decisions_per_adjudicated_claim": POSITIONS_PER_CLAIM,
            "adjudication_decisions": len(adjudication_needed) * POSITIONS_PER_CLAIM,
        },
        "merged_review": merged,
        "process_burden": list(burden),
        "formulas": list(AGREEMENT_FORMULAS),
        "limitations": list(LIMITATIONS),
    }


def accepted_reference(
    merged: LoadedFile, truth: dict[str, Any]
) -> tuple[list[AcceptedLink], dict[str, Any]]:
    """Return the accepted links and the exclusions the accepted record requires."""
    accepted: list[AcceptedLink] = []
    not_verified = 0
    outside_class_set = 0
    for record, claim in zip(merged.payload["claims"], truth["claims"], strict=True):
        claim_id = str(record["claim_id"])
        repo_id = str(record["repo_id"])
        expectations = {
            str(link["target"]): str(link["expected_resolution"])
            for link in claim["expected_links"]
        }
        adjudication = record["adjudication"]
        source = adjudication if adjudication is not None else record["reviews"][0]
        for link in source["link_decisions"]:
            target = str(link["target"])
            if str(link["decision"]) != "verified":
                not_verified += 1
                continue
            expected = expectations[target]
            if expected not in RESOLUTION_CLASSES:
                outside_class_set += 1
                continue
            accepted.append(AcceptedLink(claim_id, repo_id, target, expected))
    exclusions = {
        "links_bound": len(truth["claims"]) * len(TARGETS),
        "excluded_not_verified": not_verified,
        "excluded_expectation_outside_class_set": outside_class_set,
        "accepted_links": len(accepted),
    }
    return accepted, exclusions


def load_candidate_run(path: Path) -> CandidateRun:
    """Validate one candidate run against the run contract and return its evidence."""
    try:
        metadata, _, rows = validate_run_evidence(path)
    except RunContractError as exc:
        raise ClaimReviewMetricsError(
            f"candidate run {path} did not pass run-contract validation: {exc}"
        ) from exc
    return CandidateRun(
        path=path,
        run_id=str(metadata["run_id"]),
        meta_sha256=sha256_file(path / RUN_META_NAME),
        metadata=metadata,
        rows=rows,
    )


def load_claim_link_output(path: Path, truth: dict[str, Any], truth_sha256: str) -> ClaimLinkOutput:
    """Load one candidate claim-link output and bind it to the frozen truth."""
    payload = _load_object(path, "candidate claim-link output")
    if payload.get("claim_evaluation_schema_version") != 1:
        raise ClaimReviewMetricsError(f"{path} is not a supported candidate claim-link output")
    if payload.get("ground_truth_sha256") != truth_sha256:
        raise ClaimReviewMetricsError(
            f"candidate claim-link output {path} is bound to claim truth "
            f"{payload.get('ground_truth_sha256')!r} rather than {truth_sha256}"
        )
    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise ClaimReviewMetricsError(f"candidate claim-link output {path} names no run_id")
    expectations = {
        (str(claim["claim_id"]), str(link["target"])): str(link["expected_resolution"])
        for claim in truth["claims"]
        for link in claim["expected_links"]
    }
    results = payload.get("results")
    if not isinstance(results, list):
        raise ClaimReviewMetricsError(f"candidate claim-link output {path} has no results")
    observed: dict[tuple[str, str], str] = {}
    claim_status: dict[str, str] = {}
    for result in results:
        claim_id = str(result["claim_id"])
        claim_status[claim_id] = str(result["status"])
        for link in result["links"]:
            target = str(link["target"])
            key = (claim_id, target)
            if key not in expectations:
                raise ClaimReviewMetricsError(
                    f"candidate claim-link output {path} reports {claim_id} {target}, which is "
                    "not in the frozen truth"
                )
            if str(link["expected_resolution"]) != expectations[key]:
                raise ClaimReviewMetricsError(
                    f"candidate claim-link output {path} disagrees with the frozen truth "
                    f"expectation at {claim_id} {target}"
                )
            observed[key] = _OBSERVED_RESOLUTIONS.get(
                str(link["observed_resolution"]), OTHER_RESOLUTION
            )
    expected_claims = {str(claim["claim_id"]) for claim in truth["claims"]}
    if set(claim_status) != expected_claims:
        raise ClaimReviewMetricsError(
            f"candidate claim-link output {path} covers {sorted(claim_status)} rather than "
            f"the frozen truth claims {sorted(expected_claims)}"
        )
    return ClaimLinkOutput(
        path=path,
        sha256=sha256_file(path),
        run_id=run_id,
        observed=observed,
        claim_status=claim_status,
    )


def _class_metrics(matrix: dict[str, dict[str, int]]) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    for name in RESOLUTION_CLASSES:
        true_positive = matrix[name][name]
        false_negative = sum(count for column, count in matrix[name].items() if column != name)
        false_positive = sum(matrix[row][name] for row in RESOLUTION_CLASSES if row != name)
        support = true_positive + false_negative
        predicted = true_positive + false_positive
        precision: float | None
        recall: float | None
        f1: float | None
        reason: str | None = None
        if support == 0:
            recall = None
            precision = None if predicted == 0 else true_positive / predicted
            f1 = None
            reason = "the accepted record contains no link expecting this class"
        else:
            recall = true_positive / support
            precision = 0.0 if predicted == 0 else true_positive / predicted
            total = precision + recall
            f1 = 0.0 if total == 0 else 2 * precision * recall / total
        metrics.append(
            {
                "class": name,
                "support": support,
                "predicted": predicted,
                "true_positive": true_positive,
                "false_positive": false_positive,
                "false_negative": false_negative,
                "precision": precision,
                "recall": recall,
                "f1": f1,
                "undefined_reason": reason,
            }
        )
    return metrics


def _macro_f1(class_metrics: Sequence[dict[str, Any]]) -> dict[str, Any]:
    scored = [entry for entry in class_metrics if entry["f1"] is not None]
    if not scored:
        return {
            "value": None,
            "classes_averaged": [],
            "undefined_reason": "no resolution class has support in the accepted record",
        }
    return {
        "value": sum(float(entry["f1"]) for entry in scored) / len(scored),
        "classes_averaged": [str(entry["class"]) for entry in scored],
        "undefined_reason": None,
    }


def _operational_failures(run: CandidateRun) -> dict[str, Any]:
    attempted = int(run.metadata.get("n_repositories", 0))
    timeouts = sum(1 for row in run.rows if str(row.get("timeout", "")).strip().lower() == "true")
    return {
        "repositories_attempted": attempted,
        "acquisition_failures": Ratio(
            int(run.metadata.get("n_acquisition_failed", 0)), attempted
        ).to_json(),
        "scanner_crashes": Ratio(
            int(run.metadata.get("n_scanner_crashed", 0)), attempted
        ).to_json(),
        "timeouts": Ratio(timeouts, attempted).to_json(),
        "contract_failures": Ratio(
            int(run.metadata.get("n_contract_failed", 0)), attempted
        ).to_json(),
    }


def candidate_outcomes(
    accepted: Sequence[AcceptedLink], links: ClaimLinkOutput, run: CandidateRun
) -> dict[str, Any]:
    """Compare one candidate run's claim-link output with the accepted reference."""
    columns = (*RESOLUTION_CLASSES, OTHER_RESOLUTION)
    matrix = {row: dict.fromkeys(columns, 0) for row in RESOLUTION_CLASSES}
    matches = 0
    not_evaluated = 0
    per_target_matches: Counter[str] = Counter()
    per_target_total: Counter[str] = Counter()
    per_repository: dict[str, dict[str, Any]] = {}
    declining_total = 0
    declining_matched = 0
    resolved_expected = 0
    resolved_declined = 0
    for link in accepted:
        observed = links.observed.get((link.claim_id, link.target))
        if observed is None:
            observed = OTHER_RESOLUTION
            not_evaluated += 1
        matrix[link.expected][observed] += 1
        agreed = observed == link.expected
        matches += agreed
        per_target_total[link.target] += 1
        per_target_matches[link.target] += agreed
        repository = per_repository.setdefault(
            link.repo_id,
            {
                "repo_id": link.repo_id,
                "claim_id": link.claim_id,
                "accepted_links": 0,
                "matching_links": 0,
                "candidate_claim_status": links.claim_status.get(link.claim_id),
            },
        )
        repository["accepted_links"] += 1
        repository["matching_links"] += agreed
        if link.expected in DECLINING_EXPECTATIONS:
            declining_total += 1
            declining_matched += observed != "resolved"
        else:
            resolved_expected += 1
            resolved_declined += observed != "resolved"
    class_metrics = _class_metrics(matrix)
    return {
        "run_id": run.run_id,
        "run_path": str(run.path),
        "run_meta_sha256": run.meta_sha256,
        "adduce_version": run.metadata.get("adduce_version"),
        "analysis_scope": run.metadata.get("analysis_scope"),
        "claim_links_path": str(links.path),
        "claim_links_sha256": links.sha256,
        "confusion_matrix": {
            "rows": list(RESOLUTION_CLASSES),
            "columns": list(columns),
            "counts": matrix,
            "accepted_links": len(accepted),
            "links_without_candidate_observation": not_evaluated,
        },
        "accuracy": Ratio(matches, len(accepted)).to_json(),
        "per_class": class_metrics,
        "macro_f1": _macro_f1(class_metrics),
        "per_target": [
            {
                "target": target,
                "accuracy": Ratio(per_target_matches[target], per_target_total[target]).to_json(),
            }
            for target in TARGETS
        ],
        "per_repository": [per_repository[repo_id] for repo_id in sorted(per_repository)],
        "abstention": {
            "declining_when_expected": Ratio(declining_matched, declining_total).to_json(),
            "over_declining": Ratio(resolved_declined, resolved_expected).to_json(),
        },
        "claim_status_counts": dict(sorted(Counter(links.claim_status.values()).items())),
        "operational_failures": _operational_failures(run),
    }


def post_candidate_metrics(
    truth: LoadedFile,
    merged: LoadedFile,
    review_a: ReviewerFile,
    review_b: ReviewerFile,
    runs: Sequence[CandidateRun],
    outputs: Sequence[ClaimLinkOutput],
    accepted: Sequence[AcceptedLink],
    exclusions: dict[str, Any],
) -> dict[str, Any]:
    """Build the post-candidate metrics document from gated, validated inputs."""
    by_run = {output.run_id: output for output in outputs}
    claim_count = len(truth.payload["claims"])
    return {
        "claim_review_metrics_schema_version": CLAIM_REVIEW_METRICS_SCHEMA_VERSION,
        "mode": "post-candidate",
        "inputs": {
            "claims": _file_record(truth.path, truth.sha256),
            "merged": _file_record(merged.path, merged.sha256),
            "review_a": _file_record(review_a.path, review_a.sha256),
            "review_b": _file_record(review_b.path, review_b.sha256),
            "runs": [
                {"path": str(run.path), "run_id": run.run_id, "run_meta_sha256": run.meta_sha256}
                for run in runs
            ],
            "claim_links": [
                {"path": str(output.path), "sha256": output.sha256, "run_id": output.run_id}
                for output in outputs
            ],
        },
        "scope": {
            "claims": claim_count,
            "targets": list(TARGETS),
            "links_per_claim": len(TARGETS),
            "resolution_classes": list(RESOLUTION_CLASSES),
            "candidate_runs": len(runs),
            "reviewers": [review_a.reviewer_id, review_b.reviewer_id],
        },
        "gates": {
            "merged_review_complete_and_accepted": True,
            "independent_review_sources_verified": True,
            "candidate_runs_validated": len(runs),
            "claim_link_outputs_bound": len(outputs),
        },
        "accepted_reference": exclusions,
        "runs": [candidate_outcomes(accepted, by_run[run.run_id], run) for run in runs],
        "formulas": list(CANDIDATE_FORMULAS),
        "limitations": list(LIMITATIONS),
    }


def _ratio_text(value: dict[str, Any]) -> str:
    ratio = value["ratio"]
    rendered = "not defined (denominator 0)" if ratio is None else f"{ratio:.3f}"
    return f"{value['numerator']}/{value['denominator']} = {rendered}"


def _number_text(value: object) -> str:
    if value is None:
        return "not defined"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _input_rows(inputs: dict[str, Any]) -> list[str]:
    rows = []
    for name in ("claims", "merged", "review_a", "review_b"):
        record = inputs.get(name)
        if record is None:
            rows.append(f"| {name} | not supplied | not supplied |")
        else:
            rows.append(f"| {name} | {record['path']} | {record['sha256']} |")
    for record in inputs.get("feedback", []):
        rows.append(f"| feedback | {record['path']} | {record['sha256']} |")
    for record in inputs.get("runs", []):
        rows.append(f"| run {record['run_id']} | {record['path']} | {record['run_meta_sha256']} |")
    for record in inputs.get("claim_links", []):
        rows.append(f"| claim-links {record['run_id']} | {record['path']} | {record['sha256']} |")
    return rows


def _render_common(payload: dict[str, Any]) -> list[str]:
    return [
        "",
        "## Formulas",
        "",
        *[f"- {formula}" for formula in payload["formulas"]],
        "",
        "## Limitations",
        "",
        *[f"- {limitation}" for limitation in payload["limitations"]],
        "",
    ]


def _render_pre_candidate(payload: dict[str, Any]) -> str:
    scope = payload["scope"]
    agreement = payload["agreement"]
    kappa = agreement["cohens_kappa"]
    lines = [
        "# Claim-review metrics",
        "",
        f"Mode: {payload['mode']}",
        "",
        "Coordinator-only. Every figure below is a descriptive summary of the artifacts named "
        "here and of nothing else.",
        "",
        "## Inputs",
        "",
        "| artifact | path | sha256 |",
        "| --- | --- | --- |",
        *_input_rows(payload["inputs"]),
        "",
        "## Scope",
        "",
        f"- Claims: {scope['claims']}",
        f"- Positions per claim: {scope['positions_per_claim']} "
        f"(1 claim-level + {len(scope['targets'])} link-level)",
        f"- Comparison positions: {scope['comparison_positions']}",
        f"- Decision values: {', '.join(scope['decision_values'])}",
        f"- Reviewers: {', '.join(scope['reviewers'])}",
        "",
        "## Reviewer completion",
        "",
        "| reviewer | decisions | finalized claims | declarations |",
        "| --- | --- | --- | --- |",
    ]
    for entry in payload["completion"]:
        lines.append(
            f"| {entry['reviewer_id']} | {_ratio_text(entry['decisions'])} "
            f"| {_ratio_text(entry['finalized_claims'])} "
            f"| {_ratio_text(entry['declarations'])} |"
        )
    lines += [
        "",
        "## Agreement",
        "",
        f"- Raw agreement: {_ratio_text(agreement['raw'])}",
        f"- Claim-level agreement: {_ratio_text(agreement['claim_level'])}",
        f"- Link-level agreement: {_ratio_text(agreement['link_level'])}",
        f"- Positions compared: {agreement['positions_compared']}",
        f"- Positions not comparable: {agreement['positions_not_comparable']}",
        f"- Disagreement count: {agreement['disagreement_count']}",
        f"- Cohen's kappa ({kappa['interpretation']}): "
        + (
            f"{kappa['value']:.3f}"
            if kappa["value"] is not None
            else f"not defined, because {kappa['undefined_reason']}"
        ),
        "",
        "| target | agreement |",
        "| --- | --- |",
    ]
    for entry in agreement["by_target"]:
        lines.append(f"| {entry['target']} | {_ratio_text(entry['agreement'])} |")
    lines += [
        "",
        "## Disagreements",
        "",
        f"Items listed: {len(payload['disagreements'])}",
        "",
    ]
    if payload["disagreements"]:
        reviewers = scope["reviewers"]
        lines += [
            f"| claim | target | {reviewers[0]} | {reviewers[1]} |",
            "| --- | --- | --- | --- |",
        ]
        for item in payload["disagreements"]:
            lines.append(
                f"| {item['claim_id']} | {item['target']} | {item[reviewers[0]]} "
                f"| {item[reviewers[1]]} |"
            )
        lines.append("")
    lines += [
        "## Decision values recorded",
        "",
        "| reviewer | verified | revision_required | unclear |",
        "| --- | --- | --- | --- |",
    ]
    for entry in payload["decision_counts"]:
        lines.append(
            f"| {entry['reviewer_id']} | {_ratio_text(entry['verified'])} "
            f"| {_ratio_text(entry['revision_required'])} | {_ratio_text(entry['unclear'])} |"
        )
    adjudication = payload["adjudication"]
    lines += [
        "",
        "## Adjudication burden",
        "",
        "- Claims needing adjudication: "
        f"{_ratio_text(adjudication['claims_needing_adjudication'])}",
        f"- Decisions per adjudicated claim: {adjudication['decisions_per_adjudicated_claim']}",
        f"- Adjudication decisions implied: {adjudication['adjudication_decisions']}",
        "",
    ]
    merged = payload["merged_review"]
    lines += ["## Merged review", ""]
    if merged is None:
        lines += ["No merged review was supplied.", ""]
    else:
        lines += [
            f"- Path: {merged['path']}",
            f"- SHA-256: {merged['sha256']}",
            f"- Completed claims: {_ratio_text(merged['completed_claims'])}",
            f"- Accepted claims: {_ratio_text(merged['accepted_claims'])}",
            f"- Adjudicated claims: {_ratio_text(merged['adjudicated_claims'])}",
            "",
        ]
    lines += ["## Review process burden", ""]
    if not payload["process_burden"]:
        lines += ["No reviewer feedback was supplied.", ""]
    for entry in payload["process_burden"]:
        duration = entry["duration"]
        median = entry["median_minutes_per_claim"]
        lines += [
            f"### {entry['reviewer_id']}",
            "",
            f"- Feedback artifact: {entry['feedback_path']} ({entry['feedback_sha256']})",
            f"- Total self-reported minutes: {_number_text(duration['total_minutes'])} over "
            f"{duration['timed_claims']} timed claim(s) of {duration['claims']}",
            f"- Median minutes per claim: {_number_text(median['value'])} "
            f"(n={median['n']}, minimum={_number_text(median['minimum'])}, "
            f"maximum={_number_text(median['maximum'])})",
            f"- Validator failures: {_ratio_text(entry['validator_failures'])}",
            f"- Clarification requests: {_ratio_text(entry['clarification_requests'])}",
            "- Ratings (1-5, as recorded): "
            + ", ".join(f"{name}={value}" for name, value in sorted(entry["ratings"].items())),
            "- Free-text answers given: "
            + ", ".join(
                f"{name}={'yes' if answered else 'no'}"
                for name, answered in sorted(entry["free_text_answered"].items())
            ),
            "",
        ]
    lines += _render_common(payload)
    return "\n".join(lines)


def _render_post_candidate(payload: dict[str, Any]) -> str:
    scope = payload["scope"]
    reference = payload["accepted_reference"]
    gates = payload["gates"]
    lines = [
        "# Claim-review metrics",
        "",
        f"Mode: {payload['mode']}",
        "",
        "Coordinator-only. Every figure below is a descriptive summary of the artifacts named "
        "here and of nothing else.",
        "",
        "## Inputs",
        "",
        "| artifact | path | sha256 |",
        "| --- | --- | --- |",
        *_input_rows(payload["inputs"]),
        "",
        "## Gates",
        "",
        "- Merged claim review validated as complete and accepted: "
        f"{'yes' if gates['merged_review_complete_and_accepted'] else 'no'}",
        "- Independent review sources verified against the merged review: "
        f"{'yes' if gates['independent_review_sources_verified'] else 'no'}",
        f"- Candidate runs validated against the run contract: {gates['candidate_runs_validated']}",
        f"- Candidate claim-link outputs bound to a validated run: "
        f"{gates['claim_link_outputs_bound']}",
        "",
        "## Scope",
        "",
        f"- Claims: {scope['claims']}",
        f"- Links per claim: {scope['links_per_claim']}",
        f"- Resolution classes: {', '.join(scope['resolution_classes'])}",
        f"- Candidate runs: {scope['candidate_runs']}",
        "",
        "## Accepted reference",
        "",
        f"- Links bound by the frozen truth: {reference['links_bound']}",
        f"- Accepted links used as the reference: {reference['accepted_links']}",
        "- Excluded because the accepted record did not resolve them as verified: "
        f"{reference['excluded_not_verified']}",
        "- Excluded because the expectation lies outside the three resolution classes: "
        f"{reference['excluded_expectation_outside_class_set']}",
        "",
    ]
    for run in payload["runs"]:
        matrix = run["confusion_matrix"]
        macro = run["macro_f1"]
        lines += [
            f"## Candidate run {run['run_id']}",
            "",
            f"- Run directory: {run['run_path']} ({run['run_meta_sha256']})",
            f"- Claim-link output: {run['claim_links_path']} ({run['claim_links_sha256']})",
            f"- adduce version recorded by the run: {run['adduce_version']}",
            f"- Accepted links compared: {matrix['accepted_links']}",
            "- Accepted links the candidate never evaluated: "
            f"{matrix['links_without_candidate_observation']}",
            f"- Accuracy: {_ratio_text(run['accuracy'])}",
            "",
            "### Confusion matrix",
            "",
            "Rows are the expected resolution, columns the candidate resolution.",
            "",
            "| expected \\ candidate | " + " | ".join(matrix["columns"]) + " |",
            "| --- " * (len(matrix["columns"]) + 1) + "|",
        ]
        for row in matrix["rows"]:
            counts = matrix["counts"][row]
            lines.append(
                f"| {row} | "
                + " | ".join(str(counts[column]) for column in matrix["columns"])
                + " |"
            )
        lines += [
            "",
            "### Per class",
            "",
            "| class | support | predicted | TP | FP | FN | precision | recall | F1 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for entry in run["per_class"]:
            lines.append(
                f"| {entry['class']} | {entry['support']} | {entry['predicted']} "
                f"| {entry['true_positive']} | {entry['false_positive']} "
                f"| {entry['false_negative']} | {_number_text(entry['precision'])} "
                f"| {_number_text(entry['recall'])} | {_number_text(entry['f1'])} |"
            )
        lines.append("")
        for entry in run["per_class"]:
            if entry["undefined_reason"] is not None:
                lines.append(f"- {entry['class']}: {entry['undefined_reason']}")
        lines += [
            "- Macro-F1: "
            + (
                f"{macro['value']:.3f} over {', '.join(macro['classes_averaged'])}"
                if macro["value"] is not None
                else f"not defined, because {macro['undefined_reason']}"
            ),
            "",
            "### Per target",
            "",
            "| target | accuracy |",
            "| --- | --- |",
        ]
        for entry in run["per_target"]:
            lines.append(f"| {entry['target']} | {_ratio_text(entry['accuracy'])} |")
        lines += [
            "",
            "### Per repository",
            "",
            "| repository | claim | accepted links | matching links | candidate claim status |",
            "| --- | --- | --- | --- | --- |",
        ]
        for entry in run["per_repository"]:
            lines.append(
                f"| {entry['repo_id']} | {entry['claim_id']} | {entry['accepted_links']} "
                f"| {entry['matching_links']} | {entry['candidate_claim_status']} |"
            )
        abstention = run["abstention"]
        operational = run["operational_failures"]
        lines += [
            "",
            "### Abstention",
            "",
            "- Declining when a declining resolution was expected: "
            f"{_ratio_text(abstention['declining_when_expected'])}",
            f"- Over-declining: {_ratio_text(abstention['over_declining'])}",
            "",
            "### Operational failures",
            "",
            f"- Repositories attempted: {operational['repositories_attempted']}",
            f"- Acquisition failures: {_ratio_text(operational['acquisition_failures'])}",
            f"- Scanner crashes: {_ratio_text(operational['scanner_crashes'])}",
            f"- Timeouts: {_ratio_text(operational['timeouts'])}",
            f"- Run-contract failures: {_ratio_text(operational['contract_failures'])}",
            "",
        ]
    lines += _render_common(payload)
    return "\n".join(lines)


def render_markdown(payload: dict[str, Any]) -> str:
    """Render one metrics document as Markdown, with every denominator and every formula."""
    if payload["mode"] == "pre-candidate":
        return _render_pre_candidate(payload)
    return _render_post_candidate(payload)


def render(payload: dict[str, Any], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(payload, indent=2, sort_keys=True, allow_nan=False)
    return render_markdown(payload)


def _command_pre_candidate(args: argparse.Namespace) -> int:
    truth_payload, truth_sha256 = load_truth(args.claims)
    truth = LoadedFile(path=args.claims, sha256=truth_sha256, payload=truth_payload)
    review_a = load_independent_review(args.review_a, truth_payload, truth_sha256, "--review-a")
    review_b = load_independent_review(args.review_b, truth_payload, truth_sha256, "--review-b")
    require_comparable_reviewers(review_a, review_b)
    merged_block: dict[str, Any] | None = None
    if args.merged is not None:
        merged = load_merged_review(
            args.merged, truth_payload, truth_sha256, require_accepted=False
        )
        verify_merge_sources(merged, review_a, review_b, truth_payload, truth_sha256)
        merged_block = _merged_block(merged, truth_payload, truth_sha256)
    claim_ids = [str(claim["claim_id"]) for claim in truth_payload["claims"]]
    burden = []
    feedback_inputs = []
    for path in args.feedback:
        review, feedback = load_feedback(path, [review_a, review_b], claim_ids)
        burden.append(_burden_block(review, feedback, len(claim_ids)))
        feedback_inputs.append(_file_record(feedback.path, feedback.sha256))
    payload = pre_candidate_metrics(
        truth,
        review_a,
        review_b,
        merged=merged_block,
        burden=burden,
        feedback_inputs=feedback_inputs,
    )
    print(render(payload, args.format))
    return 0


def _require_post_candidate_inputs(args: argparse.Namespace) -> None:
    if args.merged is None:
        raise ClaimReviewMetricsError(
            "post-candidate metrics require the accepted merged claim review (--merged); "
            "candidate metrics cannot be computed before the claim-review gate has passed"
        )
    missing = [
        name
        for name, value in (("--review-a", args.review_a), ("--review-b", args.review_b))
        if value is None
    ]
    if missing:
        raise ClaimReviewMetricsError(
            f"post-candidate metrics require both independent source reviews; missing {missing}"
        )
    if len(args.run) != 2:
        raise ClaimReviewMetricsError(
            "post-candidate metrics require the two pre-registered candidate run directories "
            f"(--run); found {len(args.run)}"
        )
    if not args.claim_links:
        raise ClaimReviewMetricsError(
            "post-candidate metrics require the candidate claim-link outputs (--claim-links); "
            "found 0"
        )


def _pair_outputs(runs: Sequence[CandidateRun], outputs: Sequence[ClaimLinkOutput]) -> None:
    run_ids = [run.run_id for run in runs]
    if len(set(run_ids)) != len(run_ids):
        raise ClaimReviewMetricsError(
            f"the supplied candidate runs share a run_id: {sorted(run_ids)}"
        )
    output_ids = [output.run_id for output in outputs]
    if len(set(output_ids)) != len(output_ids):
        raise ClaimReviewMetricsError(
            f"two candidate claim-link outputs report the same run_id: {sorted(output_ids)}"
        )
    unknown = [output for output in outputs if output.run_id not in set(run_ids)]
    if unknown:
        raise ClaimReviewMetricsError(
            f"candidate claim-link output {unknown[0].path} reports run_id "
            f"{unknown[0].run_id!r}, which is not one of the supplied candidate runs {run_ids}"
        )
    uncovered = sorted(set(run_ids) - set(output_ids))
    if uncovered:
        raise ClaimReviewMetricsError(
            "post-candidate metrics require one candidate claim-link output per candidate run; "
            f"no --claim-links input covers run(s) {uncovered}"
        )


def _command_post_candidate(args: argparse.Namespace) -> int:
    _require_post_candidate_inputs(args)
    truth_payload, truth_sha256 = load_truth(args.claims)
    truth = LoadedFile(path=args.claims, sha256=truth_sha256, payload=truth_payload)
    merged = load_merged_review(args.merged, truth_payload, truth_sha256, require_accepted=True)
    review_a = load_independent_review(args.review_a, truth_payload, truth_sha256, "--review-a")
    review_b = load_independent_review(args.review_b, truth_payload, truth_sha256, "--review-b")
    require_comparable_reviewers(review_a, review_b)
    verify_merge_sources(merged, review_a, review_b, truth_payload, truth_sha256)
    runs = [load_candidate_run(path) for path in args.run]
    candidate_pair = [str(value) for value in merged.payload["candidate_pair"]]
    observed_pair = [run.path.name for run in runs]
    if observed_pair != candidate_pair:
        raise ClaimReviewMetricsError(
            f"the --run directories must be the pre-registered candidate pair {candidate_pair} "
            f"in that order; found {observed_pair}"
        )
    outputs = [
        load_claim_link_output(path, truth_payload, truth_sha256) for path in args.claim_links
    ]
    _pair_outputs(runs, outputs)
    accepted, exclusions = accepted_reference(merged, truth_payload)
    payload = post_candidate_metrics(
        truth, merged, review_a, review_b, runs, outputs, accepted, exclusions
    )
    print(render(payload, args.format))
    return 0


_COMMANDS = {
    "pre-candidate": _command_pre_candidate,
    "post-candidate": _command_post_candidate,
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    pre_parser = subparsers.add_parser(
        "pre-candidate", help="agreement and process metrics from two independent reviewer files"
    )
    pre_parser.add_argument("--review-a", type=Path, required=True)
    pre_parser.add_argument("--review-b", type=Path, required=True)
    pre_parser.add_argument("--claims", type=Path, required=True)
    pre_parser.add_argument("--merged", type=Path, default=None)
    pre_parser.add_argument("--feedback", type=Path, action="append", default=[])
    pre_parser.add_argument("--format", choices=["markdown", "json"], default="markdown")

    post_parser = subparsers.add_parser(
        "post-candidate",
        help="candidate claim-link metrics, computable only after the review gate has passed",
    )
    post_parser.add_argument("--merged", type=Path, default=None)
    post_parser.add_argument("--review-a", type=Path, default=None)
    post_parser.add_argument("--review-b", type=Path, default=None)
    post_parser.add_argument("--claims", type=Path, required=True)
    post_parser.add_argument("--run", type=Path, action="append", default=[])
    post_parser.add_argument("--claim-links", type=Path, action="append", default=[])
    post_parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return _COMMANDS[args.command](args)
    except (
        ClaimGroundTruthError,
        ClaimReviewError,
        ClaimReviewMetricsError,
        ReviewerFeedbackError,
        RunContractError,
        OSError,
    ) as exc:
        sys.exit(f"cannot compute claim-review metrics: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
