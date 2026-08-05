"""Synthetic claim-review fixtures shared by the reviewer-entry tests.

Every identifier, rationale and evidence locator produced here is unmistakably
synthetic so that a fixture artifact can never be mistaken for, or merged with,
a real pilot decision about one of the ten corpus repositories.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from corpus.scripts import claim_review_entry
from corpus.scripts.claim_ground_truth import TARGETS
from corpus.scripts.claim_review import initialize_review
from corpus.scripts.run_contract import sha256_file, write_json

CANDIDATE_PAIR = ["synthetic-candidate-a", "synthetic-candidate-b"]
REVIEWER_ID = "reviewer-test-a"
SECOND_REVIEWER_ID = "reviewer-test-b"
ADJUDICATOR_ID = "adjudicator-test-c"
DOMAIN_EXPERTISE = "Synthetic fixture reviewer for reviewer-workspace tests"
FROZEN_AT = "2026-07-13T22:00:00Z"
CLOCK_START = datetime(2026, 7, 20, 9, 0, 0, tzinfo=timezone.utc)
AFFIRMATION_FLAGS = tuple(
    f"--{attribute.replace('_', '-')}" for attribute, _ in claim_review_entry.AFFIRMATIONS
)
_ORDINALS = (
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
)


def synthetic_repo_id(index: int) -> str:
    return f"synthetic-repo-{_ORDINALS[index]}"


def synthetic_claim_id(index: int) -> str:
    return f"{synthetic_repo_id(index)}.c1"


def fixture_rationale(claim_id: str, target: str | None = None) -> str:
    if target is None:
        return f"fixture rationale for {claim_id}"
    return f"fixture rationale for {claim_id} {target}"


def fixture_evidence(claim_id: str, target: str | None = None) -> list[str]:
    repo_id = claim_id.split(".", 1)[0]
    if target is None:
        return [f"{repo_id}/README.md:1", f"{repo_id}/fixture-support.md:2"]
    return [f"{repo_id}/fixture-{target}.md:1"]


def _digest(label: str) -> str:
    return hashlib.sha256(f"synthetic-{label}".encode()).hexdigest()


def _synthetic_claim(index: int) -> dict[str, Any]:
    repo_id = synthetic_repo_id(index)
    claim_id = synthetic_claim_id(index)
    commit = f"{index + 1:040x}"
    text = f"fixture metric for {claim_id} reaches 42.0 on the synthetic split"
    quote = f"The {text}."
    links = []
    for target in TARGETS:
        if target == "reported_result":
            resolution, artifacts = "resolved", [{"kind": "claim_source"}]
        elif target == "commit":
            resolution, artifacts = "resolved", [{"kind": "literal", "value": commit}]
        else:
            resolution, artifacts = "not_applicable", []
        links.append(
            {
                "target": target,
                "expected_resolution": resolution,
                "artifacts": artifacts,
                "rationale": f"fixture expectation for {target} on {claim_id}",
            }
        )
    return {
        "claim_id": claim_id,
        "repo_id": repo_id,
        "repo_commit": commit,
        "source": {
            "kind": "repository_file",
            "path": "README.md",
            "sha256": _digest(f"source-{claim_id}"),
            "line_start": 1,
            "line_end": 1,
            "quote": quote,
        },
        "claim": {
            "text": text,
            "metric": "fixture-metric",
            "value": 42.0,
            "unit": "points",
            "context": "synthetic split",
        },
        "adduce_match": {"headline_contains": f"fixture metric for {claim_id}"},
        "expected_trail_status": "supported",
        "expected_links": links,
        "ground_truth_review": {
            "prepared_by": "preparer-test-a",
            "prepared_at": "2026-07-13T20:00:00Z",
            "verified_by": "verifier-test-b",
            "verified_at": "2026-07-13T21:00:00Z",
        },
    }


def synthetic_truth(claim_count: int = 2) -> dict[str, Any]:
    """Return a structurally valid, entirely synthetic claim ground truth."""
    if not 1 <= claim_count <= len(_ORDINALS):
        raise ValueError(f"claim_count must be between 1 and {len(_ORDINALS)}")
    return {
        "claim_ground_truth_schema_version": 1,
        "corpus_inventory_sha256": _digest("corpus-inventory"),
        "clone_manifest_sha256": _digest("clone-manifest"),
        "frozen_at": FROZEN_AT,
        "claims": [_synthetic_claim(index) for index in range(claim_count)],
        "unavailable_repositories": [],
    }


def write_truth(tmp_path: Path, truth: dict[str, Any]) -> Path:
    path = tmp_path / "synthetic-claims.json"
    write_json(path, truth)
    return path


def scaffold_for(truth_path: Path, truth: dict[str, Any]) -> dict[str, Any]:
    return initialize_review(truth, sha256_file(truth_path), list(CANDIDATE_PAIR))


def write_scaffold(tmp_path: Path, truth_path: Path, truth: dict[str, Any]) -> Path:
    path = tmp_path / "synthetic-claim-review-scaffold.json"
    write_json(path, scaffold_for(truth_path, truth))
    return path


def advancing_clock(
    start: datetime = CLOCK_START, step: timedelta = timedelta(minutes=1)
) -> Callable[[], datetime]:
    """Return a deterministic clock that advances one step per call."""
    state = {"current": start - step}

    def clock() -> datetime:
        state["current"] = state["current"] + step
        return state["current"]

    return clock


def init_workspace(
    tmp_path: Path,
    scaffold_path: Path,
    truth_path: Path,
    clock: Callable[[], datetime],
    *,
    reviewer_id: str = REVIEWER_ID,
    domain_expertise: str = DOMAIN_EXPERTISE,
) -> Path:
    workspace = tmp_path / f"{reviewer_id}.review-workspace.json"
    claim_review_entry.main(
        [
            "init",
            "--scaffold",
            str(scaffold_path),
            "--claims",
            str(truth_path),
            "--reviewer-id",
            reviewer_id,
            "--domain-expertise",
            domain_expertise,
            "--workspace",
            str(workspace),
        ],
        clock=clock,
    )
    return workspace


def declare_claim(workspace: Path, claim_id: str, clock: Callable[[], datetime]) -> None:
    claim_review_entry.main(
        [
            "declare",
            "--workspace",
            str(workspace),
            "--claim-id",
            claim_id,
            *AFFIRMATION_FLAGS,
        ],
        clock=clock,
    )


def record_claim_decision(
    workspace: Path,
    claim_id: str,
    clock: Callable[[], datetime],
    *,
    decision: str = "verified",
) -> None:
    evidence: list[str] = []
    for locator in fixture_evidence(claim_id):
        evidence.extend(["--evidence", locator])
    claim_review_entry.main(
        [
            "record-claim",
            "--workspace",
            str(workspace),
            "--claim-id",
            claim_id,
            "--decision",
            decision,
            "--rationale",
            fixture_rationale(claim_id),
            *evidence,
        ],
        clock=clock,
    )


def record_link_decision(
    workspace: Path,
    claim_id: str,
    target: str,
    clock: Callable[[], datetime],
    *,
    decision: str = "verified",
) -> None:
    evidence: list[str] = []
    for locator in fixture_evidence(claim_id, target):
        evidence.extend(["--evidence", locator])
    claim_review_entry.main(
        [
            "record-link",
            "--workspace",
            str(workspace),
            "--claim-id",
            claim_id,
            "--target",
            target,
            "--decision",
            decision,
            "--rationale",
            fixture_rationale(claim_id, target),
            *evidence,
        ],
        clock=clock,
    )


def fill_claim(
    workspace: Path,
    claim_id: str,
    clock: Callable[[], datetime],
    *,
    skip_targets: Iterable[str] = (),
    decision: str = "verified",
) -> None:
    """Record declarations and every decision a claim needs, minus `skip_targets`."""
    skipped = set(skip_targets)
    declare_claim(workspace, claim_id, clock)
    record_claim_decision(workspace, claim_id, clock, decision=decision)
    for target in TARGETS:
        if target not in skipped:
            record_link_decision(workspace, claim_id, target, clock, decision=decision)


def finalize_claim(
    workspace: Path, truth_path: Path, claim_id: str, clock: Callable[[], datetime]
) -> None:
    claim_review_entry.main(
        [
            "finalize-claim",
            "--workspace",
            str(workspace),
            "--claims",
            str(truth_path),
            "--claim-id",
            claim_id,
        ],
        clock=clock,
    )


def complete_workspace(
    workspace: Path,
    truth: dict[str, Any],
    truth_path: Path,
    clock: Callable[[], datetime],
) -> None:
    """Fill and finalize every claim in `truth` with synthetic decisions."""
    for claim in truth["claims"]:
        claim_id = str(claim["claim_id"])
        fill_claim(workspace, claim_id, clock)
        finalize_claim(workspace, truth_path, claim_id, clock)
