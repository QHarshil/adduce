#!/usr/bin/env python3
"""Record one reviewer's own blinded claim-review decisions in a local sidecar workspace."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

if __package__:
    from .claim_ground_truth import (
        TARGETS,
        ClaimGroundTruthError,
        validate_ground_truth_structure,
    )
    from .claim_review import DECISIONS, ClaimReviewError, validate_review
    from .run_contract import (
        RunContractError,
        ensure_output_outside,
        load_json_object_bytes,
        sha256_file,
    )
else:
    from claim_ground_truth import (
        TARGETS,
        ClaimGroundTruthError,
        validate_ground_truth_structure,
    )
    from claim_review import DECISIONS, ClaimReviewError, validate_review
    from run_contract import (
        RunContractError,
        ensure_output_outside,
        load_json_object_bytes,
        sha256_file,
    )

REVIEWER_WORKSPACE_SCHEMA_VERSION = 1
WORKSPACE_KIND = "adduce-claim-review-workspace"
DECISIONS_PER_CLAIM = len(TARGETS) + 1
BLINDING_AFFIRMATIONS = (
    ("affirm_independent_review", "independent_review"),
    ("affirm_other_reviewer_decisions_not_seen", "other_reviewer_decisions_not_seen"),
    ("affirm_adduce_claim_link_outputs_not_seen", "adduce_claim_link_outputs_not_seen"),
)
CONFLICT_AFFIRMATIONS = (
    ("affirm_no_relevant_authorship_or_contribution", "no_relevant_authorship_or_contribution"),
    (
        "affirm_no_close_collaboration_supervision_or_employment",
        "no_close_collaboration_supervision_or_employment",
    ),
    ("affirm_no_financial_conflict", "no_financial_conflict"),
    ("affirm_no_personal_conflict", "no_personal_conflict"),
)
AFFIRMATIONS = BLINDING_AFFIRMATIONS + CONFLICT_AFFIRMATIONS
_REASSIGNMENT_NOTICE = (
    "consent is never inferred from invocation; if any affirmation cannot be made, the "
    "assignment must be reassigned to a different reviewer rather than proceeding with a "
    "disclosure"
)
_PLACEHOLDER_EVIDENCE = frozenset({"n/a", "na", "none", "-", "--", "tbd", "unknown"})
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_RELATIVE_PATH_RE = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._/-]*$")
_CLAIM_FIELDS = {
    "claim_id",
    "repo_id",
    "declarations",
    "claim_decision",
    "link_decisions",
    "finalized_at",
    "notes",
}
_WORKSPACE_FIELDS = {
    "reviewer_workspace_schema_version",
    "not_final",
    "workspace_kind",
    "reviewer_id",
    "domain_expertise",
    "scaffold_path",
    "scaffold_sha256",
    "claim_ground_truth_sha256",
    "corpus_inventory_sha256",
    "candidate_pair",
    "created_at",
    "updated_at",
    "claims",
}


class ReviewerWorkspaceError(ValueError):
    """A reviewer workspace is malformed, unbound, or not ready for the requested step."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _render_timestamp(moment: datetime) -> str:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ReviewerWorkspaceError("the workspace clock must return a timezone-aware time")
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _now(clock: Callable[[], datetime]) -> str:
    return _render_timestamp(clock())


def _parse_timestamp(value: str, context: str) -> datetime:
    if not _TIMESTAMP_RE.fullmatch(value):
        raise ReviewerWorkspaceError(
            f"{context} must be an RFC3339 UTC timestamp of the form 2026-07-13T22:00:00Z, "
            f"found {value!r}"
        )
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _require_shape(value: object, *, fields: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReviewerWorkspaceError(f"{context} must be a JSON object")
    missing = fields - set(value)
    extra = set(value) - fields
    if missing or extra:
        raise ReviewerWorkspaceError(
            f"{context} fields do not match the reviewer-workspace schema "
            f"(missing={sorted(missing)}, extra={sorted(extra)})"
        )
    return value


def _require_text(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewerWorkspaceError(f"{context} must be a non-empty string")
    return value


def _require_identifier(value: object, context: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ReviewerWorkspaceError(
            f"{context} must be a stable non-personal identifier, found {value!r}"
        )
    return value


def _require_digest(value: object, context: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ReviewerWorkspaceError(f"{context} must be a lowercase SHA-256, found {value!r}")
    return value


def _require_timestamp_field(value: object, context: str) -> str:
    if not isinstance(value, str):
        raise ReviewerWorkspaceError(f"{context} must be a timestamp string, found {value!r}")
    _parse_timestamp(value, context)
    return value


def parse_evidence(values: Sequence[str], context: str) -> tuple[str, ...]:
    """Validate reviewer-entered evidence locators, keeping their original text."""
    if not values:
        raise ReviewerWorkspaceError(f"{context} requires at least one evidence locator")
    seen: dict[str, str] = {}
    for value in values:
        if not isinstance(value, str):
            raise ReviewerWorkspaceError(f"{context} evidence locator must be a string")
        normalized = " ".join(value.split()).casefold()
        if not normalized:
            raise ReviewerWorkspaceError(
                f"{context} has a whitespace-only evidence locator; cite a file, line range, "
                "command, or artifact digest at the pinned commit"
            )
        if normalized in _PLACEHOLDER_EVIDENCE:
            raise ReviewerWorkspaceError(
                f"{context} evidence locator {value!r} is a placeholder rather than evidence; "
                "cite a file, line range, command, or artifact digest at the pinned commit"
            )
        if normalized in seen:
            raise ReviewerWorkspaceError(
                f"{context} repeats evidence locator {value!r}, already recorded as "
                f"{seen[normalized]!r}; each locator in one decision must be distinct"
            )
        seen[normalized] = value
    return tuple(values)


def _require_decision(value: object, context: str) -> str:
    if not isinstance(value, str) or value not in DECISIONS:
        raise ReviewerWorkspaceError(
            f"{context} decision {value!r} is not one of {sorted(DECISIONS)}"
        )
    return value


def _require_target(value: object, context: str) -> str:
    if not isinstance(value, str) or value not in TARGETS:
        raise ReviewerWorkspaceError(f"{context} target {value!r} is not one of {list(TARGETS)}")
    return value


@dataclass(frozen=True)
class ReviewerIdentity:
    """The single stable identity every decision in one workspace is attributed to."""

    reviewer_id: str
    domain_expertise: str

    def __post_init__(self) -> None:
        _require_identifier(self.reviewer_id, "reviewer_id")
        _require_text(self.domain_expertise, "domain_expertise")


@dataclass(frozen=True)
class DecisionEntry:
    """One decision a reviewer entered, with its rationale and evidence."""

    decision: str
    rationale: str
    evidence: tuple[str, ...]
    recorded_at: str
    target: str | None = None

    def __post_init__(self) -> None:
        context = f"{self.target} decision" if self.target else "claim decision"
        _require_decision(self.decision, context)
        _require_text(self.rationale, f"{context} rationale")
        parse_evidence(self.evidence, context)
        _require_timestamp_field(self.recorded_at, f"{context} recorded_at")
        if self.target is not None:
            _require_target(self.target, context)

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "decision": self.decision,
            "rationale": self.rationale,
            "evidence": list(self.evidence),
            "recorded_at": self.recorded_at,
        }
        if self.target is not None:
            payload["target"] = self.target
        return payload


@dataclass(frozen=True)
class ClaimDeclarations:
    """The blinding and conflict-of-interest declarations for one assigned claim."""

    blinding_declared_at: str
    conflict_declared_at: str
    repository_id: str
    artifact_id: str

    def __post_init__(self) -> None:
        _require_timestamp_field(self.blinding_declared_at, "blinding_declaration declared_at")
        _require_timestamp_field(
            self.conflict_declared_at, "conflict_of_interest_declaration declared_at"
        )
        _require_identifier(self.repository_id, "conflict declaration scope repository_id")
        _require_identifier(self.artifact_id, "conflict declaration scope artifact_id")

    def to_json(self) -> dict[str, Any]:
        return {
            "blinding_declaration": {
                **{field: True for _, field in BLINDING_AFFIRMATIONS},
                "declared_at": self.blinding_declared_at,
            },
            "conflict_of_interest_declaration": {
                "scope": {
                    "repository_id": self.repository_id,
                    "artifact_id": self.artifact_id,
                },
                **{field: True for _, field in CONFLICT_AFFIRMATIONS},
                "declared_at": self.conflict_declared_at,
            },
        }


@dataclass(frozen=True)
class ClaimWorkspace:
    """In-progress reviewer state for one claim; every field is entered by the reviewer."""

    claim_id: str
    repo_id: str
    declarations: ClaimDeclarations | None
    claim_decision: DecisionEntry | None
    link_decisions: tuple[DecisionEntry, ...]
    finalized_at: str | None
    notes: str

    def __post_init__(self) -> None:
        _require_identifier(self.claim_id, "claim_id")
        _require_identifier(self.repo_id, "repo_id")
        if self.claim_decision is not None and self.claim_decision.target is not None:
            raise ReviewerWorkspaceError(f"{self.claim_id} claim decision must not carry a target")
        targets = [entry.target for entry in self.link_decisions]
        if len(set(targets)) != len(targets):
            raise ReviewerWorkspaceError(f"{self.claim_id} repeats a link-decision target")
        if any(target is None for target in targets):
            raise ReviewerWorkspaceError(f"{self.claim_id} has a link decision without a target")
        if self.finalized_at is not None:
            _require_timestamp_field(self.finalized_at, f"{self.claim_id} finalized_at")
        if self.declarations is not None and (
            self.declarations.repository_id != self.repo_id
            or self.declarations.artifact_id != self.claim_id
        ):
            raise ReviewerWorkspaceError(
                f"{self.claim_id} conflict declaration is scoped to "
                f"{self.declarations.repository_id}/{self.declarations.artifact_id} rather than "
                f"the assigned {self.repo_id}/{self.claim_id}"
            )

    def decision_for(self, target: str) -> DecisionEntry | None:
        for entry in self.link_decisions:
            if entry.target == target:
                return entry
        return None

    def to_json(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "repo_id": self.repo_id,
            "declarations": None if self.declarations is None else self.declarations.to_json(),
            "claim_decision": (
                None if self.claim_decision is None else self.claim_decision.to_json()
            ),
            "link_decisions": [entry.to_json() for entry in self.link_decisions],
            "finalized_at": self.finalized_at,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class ReviewerWorkspace:
    """One reviewer's workspace, bound to exactly one scaffold and one candidate truth."""

    identity: ReviewerIdentity
    scaffold_path: str
    scaffold_sha256: str
    claim_ground_truth_sha256: str
    corpus_inventory_sha256: str
    candidate_pair: tuple[str, str]
    created_at: str
    updated_at: str
    claims: tuple[ClaimWorkspace, ...]

    def __post_init__(self) -> None:
        if not _RELATIVE_PATH_RE.fullmatch(self.scaffold_path) or ".." in self.scaffold_path.split(
            "/"
        ):
            raise ReviewerWorkspaceError(
                f"scaffold_path must be a relative POSIX path, found {self.scaffold_path!r}"
            )
        _require_digest(self.scaffold_sha256, "scaffold_sha256")
        _require_digest(self.claim_ground_truth_sha256, "claim_ground_truth_sha256")
        _require_digest(self.corpus_inventory_sha256, "corpus_inventory_sha256")
        if len(set(self.candidate_pair)) != 2:
            raise ReviewerWorkspaceError("candidate_pair requires two distinct run labels")
        for label in self.candidate_pair:
            _require_identifier(label, "candidate_pair entry")
        created_at = _parse_timestamp(self.created_at, "created_at")
        if _parse_timestamp(self.updated_at, "updated_at") < created_at:
            raise ReviewerWorkspaceError("updated_at precedes created_at")
        if not self.claims:
            raise ReviewerWorkspaceError("a reviewer workspace requires at least one claim")
        identifiers = [claim.claim_id for claim in self.claims]
        if len(set(identifiers)) != len(identifiers):
            raise ReviewerWorkspaceError("a reviewer workspace repeats a claim_id")

    def claim(self, claim_id: str) -> ClaimWorkspace:
        for claim in self.claims:
            if claim.claim_id == claim_id:
                return claim
        raise ReviewerWorkspaceError(
            f"claim {claim_id!r} is not in this workspace; assigned claims are "
            f"{[entry.claim_id for entry in self.claims]}"
        )

    def with_claim(self, claim: ClaimWorkspace, updated_at: str) -> ReviewerWorkspace:
        claims = tuple(
            claim if existing.claim_id == claim.claim_id else existing for existing in self.claims
        )
        return replace(self, claims=claims, updated_at=updated_at)

    def to_json(self) -> dict[str, Any]:
        return {
            "reviewer_workspace_schema_version": REVIEWER_WORKSPACE_SCHEMA_VERSION,
            "not_final": True,
            "workspace_kind": WORKSPACE_KIND,
            "reviewer_id": self.identity.reviewer_id,
            "domain_expertise": self.identity.domain_expertise,
            "scaffold_path": self.scaffold_path,
            "scaffold_sha256": self.scaffold_sha256,
            "claim_ground_truth_sha256": self.claim_ground_truth_sha256,
            "corpus_inventory_sha256": self.corpus_inventory_sha256,
            "candidate_pair": list(self.candidate_pair),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "claims": [claim.to_json() for claim in self.claims],
        }


@dataclass(frozen=True)
class ClaimProgress:
    """Derived completion state for one claim; never stored in the workspace."""

    claim_id: str
    declared: bool
    claim_decision_recorded: bool
    missing_targets: tuple[str, ...]
    finalized: bool

    @property
    def recorded_decisions(self) -> int:
        return int(self.claim_decision_recorded) + len(TARGETS) - len(self.missing_targets)

    @property
    def complete(self) -> bool:
        return self.declared and self.claim_decision_recorded and not self.missing_targets

    def missing_description(self) -> str:
        parts = []
        if not self.declared:
            parts.append("missing declarations")
        if not self.claim_decision_recorded:
            parts.append("missing claim decision")
        if self.missing_targets:
            parts.append(f"missing link decisions for {', '.join(self.missing_targets)}")
        return "; ".join(parts)


@dataclass(frozen=True)
class ReviewProgress:
    """Derived completion state for a whole workspace."""

    claims: tuple[ClaimProgress, ...]

    @property
    def completed(self) -> int:
        return sum(claim.complete for claim in self.claims)

    @property
    def finalized(self) -> int:
        return sum(claim.finalized for claim in self.claims)

    @property
    def declarations(self) -> int:
        return sum(claim.declared for claim in self.claims)

    @property
    def decisions(self) -> int:
        return sum(claim.recorded_decisions for claim in self.claims)

    @property
    def decisions_required(self) -> int:
        return len(self.claims) * DECISIONS_PER_CLAIM

    @property
    def export_ready(self) -> bool:
        return self.finalized == len(self.claims) and self.decisions == self.decisions_required

    @property
    def incomplete(self) -> tuple[ClaimProgress, ...]:
        return tuple(claim for claim in self.claims if not claim.complete)


def claim_progress(claim: ClaimWorkspace) -> ClaimProgress:
    return ClaimProgress(
        claim_id=claim.claim_id,
        declared=claim.declarations is not None,
        claim_decision_recorded=claim.claim_decision is not None,
        missing_targets=tuple(
            target for target in TARGETS if claim.decision_for(target) is None
        ),
        finalized=claim.finalized_at is not None,
    )


def review_progress(workspace: ReviewerWorkspace) -> ReviewProgress:
    return ReviewProgress(tuple(claim_progress(claim) for claim in workspace.claims))


def _parse_declarations(value: object, context: str) -> ClaimDeclarations | None:
    if value is None:
        return None
    declarations = _require_shape(
        value,
        fields={"blinding_declaration", "conflict_of_interest_declaration"},
        context=f"{context} declarations",
    )
    blinding = _require_shape(
        declarations["blinding_declaration"],
        fields={field for _, field in BLINDING_AFFIRMATIONS} | {"declared_at"},
        context=f"{context} blinding_declaration",
    )
    conflict = _require_shape(
        declarations["conflict_of_interest_declaration"],
        fields={field for _, field in CONFLICT_AFFIRMATIONS} | {"scope", "declared_at"},
        context=f"{context} conflict_of_interest_declaration",
    )
    for _, field in BLINDING_AFFIRMATIONS:
        if blinding[field] is not True:
            raise ReviewerWorkspaceError(f"{context} blinding_declaration does not affirm {field}")
    for _, field in CONFLICT_AFFIRMATIONS:
        if conflict[field] is not True:
            raise ReviewerWorkspaceError(
                f"{context} conflict_of_interest_declaration does not exclude {field}; "
                "the assignment must be reassigned"
            )
    scope = _require_shape(
        conflict["scope"],
        fields={"repository_id", "artifact_id"},
        context=f"{context} conflict_of_interest_declaration scope",
    )
    return ClaimDeclarations(
        blinding_declared_at=_require_timestamp_field(
            blinding["declared_at"], f"{context} blinding_declaration declared_at"
        ),
        conflict_declared_at=_require_timestamp_field(
            conflict["declared_at"], f"{context} conflict_of_interest_declaration declared_at"
        ),
        repository_id=_require_identifier(scope["repository_id"], f"{context} scope repository_id"),
        artifact_id=_require_identifier(scope["artifact_id"], f"{context} scope artifact_id"),
    )


def _parse_decision_entry(value: object, context: str, *, linked: bool) -> DecisionEntry:
    fields = {"decision", "rationale", "evidence", "recorded_at"}
    if linked:
        fields = fields | {"target"}
    entry = _require_shape(value, fields=fields, context=context)
    evidence = entry["evidence"]
    if not isinstance(evidence, list):
        raise ReviewerWorkspaceError(f"{context} evidence must be an array")
    return DecisionEntry(
        decision=_require_decision(entry["decision"], context),
        rationale=_require_text(entry["rationale"], f"{context} rationale"),
        evidence=parse_evidence(cast(Sequence[str], evidence), context),
        recorded_at=_require_timestamp_field(entry["recorded_at"], f"{context} recorded_at"),
        target=_require_target(entry["target"], context) if linked else None,
    )


def _parse_claim(value: object, context: str) -> ClaimWorkspace:
    record = _require_shape(value, fields=_CLAIM_FIELDS, context=context)
    claim_id = _require_identifier(record["claim_id"], f"{context} claim_id")
    link_decisions = record["link_decisions"]
    if not isinstance(link_decisions, list) or len(link_decisions) > len(TARGETS):
        raise ReviewerWorkspaceError(
            f"{claim_id} link_decisions must be an array of at most {len(TARGETS)} entries"
        )
    notes = record["notes"]
    if not isinstance(notes, str):
        raise ReviewerWorkspaceError(f"{claim_id} notes must be a string")
    finalized_at = record["finalized_at"]
    if finalized_at is not None:
        finalized_at = _require_timestamp_field(finalized_at, f"{claim_id} finalized_at")
    claim_decision = record["claim_decision"]
    return ClaimWorkspace(
        claim_id=claim_id,
        repo_id=_require_identifier(record["repo_id"], f"{context} repo_id"),
        declarations=_parse_declarations(record["declarations"], claim_id),
        claim_decision=(
            None
            if claim_decision is None
            else _parse_decision_entry(claim_decision, f"{claim_id} claim", linked=False)
        ),
        link_decisions=tuple(
            _parse_decision_entry(entry, f"{claim_id} link {number}", linked=True)
            for number, entry in enumerate(link_decisions, 1)
        ),
        finalized_at=finalized_at,
        notes=notes,
    )


def parse_workspace(payload: object) -> ReviewerWorkspace:
    """Validate a reviewer-workspace document and return it as value objects."""
    document = _require_shape(payload, fields=_WORKSPACE_FIELDS, context="reviewer workspace")
    if document["reviewer_workspace_schema_version"] != REVIEWER_WORKSPACE_SCHEMA_VERSION:
        raise ReviewerWorkspaceError(
            "unsupported reviewer-workspace schema version "
            f"{document['reviewer_workspace_schema_version']!r}"
        )
    if document["not_final"] is not True or document["workspace_kind"] != WORKSPACE_KIND:
        raise ReviewerWorkspaceError(
            "a reviewer workspace must declare not_final=true and "
            f"workspace_kind={WORKSPACE_KIND!r}"
        )
    candidate_pair = document["candidate_pair"]
    if not isinstance(candidate_pair, list) or len(candidate_pair) != 2:
        raise ReviewerWorkspaceError("candidate_pair must contain exactly two run labels")
    claims = document["claims"]
    if not isinstance(claims, list):
        raise ReviewerWorkspaceError("reviewer workspace claims must be an array")
    workspace = ReviewerWorkspace(
        identity=ReviewerIdentity(
            reviewer_id=_require_identifier(document["reviewer_id"], "reviewer_id"),
            domain_expertise=_require_text(document["domain_expertise"], "domain_expertise"),
        ),
        scaffold_path=_require_text(document["scaffold_path"], "scaffold_path"),
        scaffold_sha256=_require_digest(document["scaffold_sha256"], "scaffold_sha256"),
        claim_ground_truth_sha256=_require_digest(
            document["claim_ground_truth_sha256"], "claim_ground_truth_sha256"
        ),
        corpus_inventory_sha256=_require_digest(
            document["corpus_inventory_sha256"], "corpus_inventory_sha256"
        ),
        candidate_pair=(str(candidate_pair[0]), str(candidate_pair[1])),
        created_at=_require_timestamp_field(document["created_at"], "created_at"),
        updated_at=_require_timestamp_field(document["updated_at"], "updated_at"),
        claims=tuple(
            _parse_claim(claim, f"reviewer workspace claim {number}")
            for number, claim in enumerate(claims, 1)
        ),
    )
    _require_internal_ordering(workspace)
    return workspace


def _require_internal_ordering(workspace: ReviewerWorkspace) -> None:
    created_at = _parse_timestamp(workspace.created_at, "created_at")
    for claim in workspace.claims:
        entries: list[tuple[str, str]] = []
        if claim.declarations is not None:
            entries.append(
                (
                    f"{claim.claim_id} blinding_declaration.declared_at",
                    claim.declarations.blinding_declared_at,
                )
            )
            entries.append(
                (
                    f"{claim.claim_id} conflict_of_interest_declaration.declared_at",
                    claim.declarations.conflict_declared_at,
                )
            )
        if claim.claim_decision is not None:
            entries.append(
                (f"{claim.claim_id} claim decision recorded_at", claim.claim_decision.recorded_at)
            )
        entries.extend(
            (f"{claim.claim_id} {entry.target} decision recorded_at", entry.recorded_at)
            for entry in claim.link_decisions
        )
        for label, value in entries:
            if _parse_timestamp(value, label) < created_at:
                raise ReviewerWorkspaceError(f"{label} predates the workspace created_at")
        if claim.finalized_at is None:
            continue
        finalized_at = _parse_timestamp(claim.finalized_at, f"{claim.claim_id} finalized_at")
        if not claim_progress(claim).complete:
            raise ReviewerWorkspaceError(
                f"{claim.claim_id} records finalized_at while incomplete: "
                f"{claim_progress(claim).missing_description()}"
            )
        for label, value in entries:
            moment = _parse_timestamp(value, label)
            if label.endswith("declared_at") and moment >= finalized_at:
                raise ReviewerWorkspaceError(
                    f"{label} {value} is not strictly earlier than {claim.claim_id} "
                    f"finalized_at {claim.finalized_at}"
                )
            if moment > finalized_at:
                raise ReviewerWorkspaceError(
                    f"{label} {value} follows {claim.claim_id} finalized_at {claim.finalized_at}"
                )


def atomic_write_json(path: Path, payload: object) -> None:
    """Write deterministic JSON through a same-directory temporary file and ``os.replace``."""
    # run_contract.write_json is not reused here: it calls Path.write_text, which truncates the
    # target before writing, so an interrupted call would destroy recorded human decisions that
    # exist nowhere else. The rendering below is byte-identical to that helper's.
    try:
        rendered = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    except (TypeError, ValueError) as exc:
        raise ReviewerWorkspaceError(f"cannot render strict JSON for {path}: {exc}") from exc
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=f".{path.name}.", suffix=".partial"
        )
    except OSError as exc:
        raise ReviewerWorkspaceError(
            f"cannot create a temporary file beside {path}: {exc}"
        ) from exc
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise ReviewerWorkspaceError(f"cannot write {path} atomically: {exc}") from exc


def _load_json(path: Path, context: str) -> dict[str, Any]:
    try:
        return cast(dict[str, Any], load_json_object_bytes(path.read_bytes(), f"{context} {path}"))
    except (OSError, RunContractError) as exc:
        raise ReviewerWorkspaceError(f"cannot read {context} {path}: {exc}") from exc


def read_workspace(path: Path) -> ReviewerWorkspace:
    return parse_workspace(_load_json(path, "reviewer workspace"))


def _persist(path: Path, workspace: ReviewerWorkspace, previous_sha256: str) -> None:
    payload = workspace.to_json()
    parse_workspace(payload)
    try:
        atomic_write_json(path, payload)
    except ReviewerWorkspaceError as exc:
        raise ReviewerWorkspaceError(
            f"{exc}; workspace {path} is unchanged at sha256={previous_sha256}"
        ) from exc
    if read_workspace(path).to_json() != payload:
        raise ReviewerWorkspaceError(
            f"workspace {path} does not read back as written; the previous content hashed to "
            f"{previous_sha256}"
        )


def _load_truth(path: Path) -> tuple[dict[str, Any], str]:
    truth = _load_json(path, "candidate truth")
    validate_ground_truth_structure(truth)
    return truth, sha256_file(path)


def _require_truth_binding(
    workspace: ReviewerWorkspace, truth: dict[str, Any], truth_sha256: str, claims_path: Path
) -> None:
    if workspace.claim_ground_truth_sha256 != truth_sha256:
        raise ReviewerWorkspaceError(
            f"workspace is bound to candidate truth sha256={workspace.claim_ground_truth_sha256} "
            f"but {claims_path} hashes to {truth_sha256}; nothing was written"
        )
    if workspace.corpus_inventory_sha256 != truth.get("corpus_inventory_sha256"):
        raise ReviewerWorkspaceError(
            f"workspace corpus_inventory_sha256 {workspace.corpus_inventory_sha256} does not "
            f"match candidate truth {truth.get('corpus_inventory_sha256')}; nothing was written"
        )
    expected = [str(claim["claim_id"]) for claim in truth["claims"]]
    observed = [claim.claim_id for claim in workspace.claims]
    if observed != expected:
        raise ReviewerWorkspaceError(
            f"workspace claims {observed} do not match candidate truth claims {expected}; "
            "nothing was written"
        )


def _bound_scaffold(workspace: ReviewerWorkspace) -> tuple[Path, dict[str, Any]]:
    path = Path(workspace.scaffold_path)
    if not path.is_file():
        raise ReviewerWorkspaceError(
            f"bound review scaffold {workspace.scaffold_path} is not readable from "
            f"{Path.cwd()}; run this command from the directory used at init"
        )
    observed = sha256_file(path)
    if observed != workspace.scaffold_sha256:
        raise ReviewerWorkspaceError(
            f"bound review scaffold {workspace.scaffold_path} changed: expected sha256="
            f"{workspace.scaffold_sha256}, found {observed}; nothing was written"
        )
    scaffold = _load_json(path, "review scaffold")
    if scaffold.get("candidate_pair") != list(workspace.candidate_pair):
        raise ReviewerWorkspaceError(
            f"bound review scaffold {workspace.scaffold_path} targets candidate pair "
            f"{scaffold.get('candidate_pair')} rather than {list(workspace.candidate_pair)}"
        )
    return path, scaffold


def _initial_review(identity: ReviewerIdentity, claim: ClaimWorkspace) -> dict[str, Any]:
    if claim.declarations is None or claim.claim_decision is None or claim.finalized_at is None:
        raise ReviewerWorkspaceError(
            f"cannot export {claim.claim_id}: {claim_progress(claim).missing_description()}"
        )
    link_decisions = []
    for target in TARGETS:
        entry = claim.decision_for(target)
        if entry is None:
            raise ReviewerWorkspaceError(
                f"cannot export {claim.claim_id}: missing link decisions for "
                f"{', '.join(claim_progress(claim).missing_targets)}"
            )
        link_decisions.append(
            {
                "target": target,
                "decision": entry.decision,
                "rationale": entry.rationale,
                "evidence": list(entry.evidence),
            }
        )
    return {
        "reviewer_id": identity.reviewer_id,
        "domain_expertise": identity.domain_expertise,
        "reviewed_at": claim.finalized_at,
        **claim.declarations.to_json(),
        "claim_decision": claim.claim_decision.decision,
        "claim_rationale": claim.claim_decision.rationale,
        "claim_evidence": list(claim.claim_decision.evidence),
        "link_decisions": link_decisions,
    }


def build_final_review(
    workspace: ReviewerWorkspace, scaffold: dict[str, Any]
) -> dict[str, Any]:
    """Build a single-reviewer claim-review artifact from the scaffold and finalized claims."""
    payload = copy.deepcopy(scaffold)
    records = payload.get("claims")
    if not isinstance(records, list) or len(records) != len(workspace.claims):
        raise ReviewerWorkspaceError(
            "bound review scaffold does not cover the same claims as the workspace"
        )
    for record, claim in zip(records, workspace.claims, strict=True):
        if record.get("claim_id") != claim.claim_id or record.get("repo_id") != claim.repo_id:
            raise ReviewerWorkspaceError(
                f"bound review scaffold claim {record.get('claim_id')!r} does not match "
                f"workspace claim {claim.claim_id!r}"
            )
        record["reviews"] = (
            [] if claim.finalized_at is None else [_initial_review(workspace.identity, claim)]
        )
        record["adjudication"] = None
    return payload


def _relative_scaffold_path(path: Path) -> str:
    try:
        relative = path.resolve(strict=True).relative_to(Path.cwd().resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ReviewerWorkspaceError(
            f"--scaffold {path} must be inside the working directory {Path.cwd()} so the "
            "workspace can rebind it by relative path"
        ) from exc
    return relative.as_posix()


def _command_init(args: argparse.Namespace, clock: Callable[[], datetime]) -> int:
    workspace_path = cast(Path, args.workspace)
    if workspace_path.exists() or workspace_path.is_symlink():
        raise ReviewerWorkspaceError(
            f"refusing to overwrite existing reviewer workspace {workspace_path}; "
            "nothing was written"
        )
    ensure_output_outside(workspace_path, [args.scaffold, args.claims])
    if not args.domain_expertise.strip():
        raise ReviewerWorkspaceError(
            "init requires a non-empty --domain-expertise statement; nothing was written"
        )
    scaffold_path = _relative_scaffold_path(args.scaffold)
    truth, truth_sha256 = _load_truth(args.claims)
    scaffold = _load_json(args.scaffold, "review scaffold")
    validate_review(scaffold, truth, truth_sha256)
    # A workspace must never inherit a decision: starting from a scaffold that already carries
    # one would put another person's judgement under this reviewer's identity.
    if any(
        record.get("reviews") or record.get("adjudication") is not None
        for record in scaffold["claims"]
    ):
        raise ReviewerWorkspaceError(
            f"review scaffold {args.scaffold} already contains recorded decisions; a workspace "
            "must start from an empty scaffold. Nothing was written"
        )
    created_at = _now(clock)
    workspace = ReviewerWorkspace(
        identity=ReviewerIdentity(args.reviewer_id, args.domain_expertise),
        scaffold_path=scaffold_path,
        scaffold_sha256=sha256_file(args.scaffold),
        claim_ground_truth_sha256=truth_sha256,
        corpus_inventory_sha256=str(scaffold["corpus_inventory_sha256"]),
        candidate_pair=(
            str(scaffold["candidate_pair"][0]),
            str(scaffold["candidate_pair"][1]),
        ),
        created_at=created_at,
        updated_at=created_at,
        claims=tuple(
            ClaimWorkspace(
                claim_id=str(record["claim_id"]),
                repo_id=str(record["repo_id"]),
                declarations=None,
                claim_decision=None,
                link_decisions=(),
                finalized_at=None,
                notes="",
            )
            for record in scaffold["claims"]
        ),
    )
    workspace_path.parent.mkdir(parents=True, exist_ok=True)
    payload = workspace.to_json()
    parse_workspace(payload)
    atomic_write_json(workspace_path, payload)
    progress = review_progress(read_workspace(workspace_path))
    print(
        f"initialized reviewer workspace {workspace_path}: claims={len(progress.claims)} "
        f"decisions={progress.decisions}/{progress.decisions_required} "
        f"declarations={progress.declarations}/{len(progress.claims)}; "
        "no human decisions recorded"
    )
    return 0


def status_report(workspace: ReviewerWorkspace) -> dict[str, Any]:
    """Return the stable machine-readable workspace status."""
    progress = review_progress(workspace)
    return {
        "reviewer_workspace_schema_version": REVIEWER_WORKSPACE_SCHEMA_VERSION,
        "reviewer_id": workspace.identity.reviewer_id,
        "claims": len(progress.claims),
        "completed_claims": progress.completed,
        "finalized_claims": progress.finalized,
        "declarations_recorded": progress.declarations,
        "declarations_required": len(progress.claims),
        "decisions_recorded": progress.decisions,
        "decisions_required": progress.decisions_required,
        "final_export_ready": progress.export_ready,
        "incomplete_claims": [
            {
                "claim_id": claim.claim_id,
                "missing_declarations": not claim.declared,
                "missing_claim_decision": not claim.claim_decision_recorded,
                "missing_link_targets": list(claim.missing_targets),
            }
            for claim in progress.incomplete
        ],
    }


def _command_status(args: argparse.Namespace, clock: Callable[[], datetime]) -> int:
    workspace = read_workspace(args.workspace)
    if args.json:
        print(json.dumps(status_report(workspace), indent=2, sort_keys=True))
        return 0
    progress = review_progress(workspace)
    print(
        f"workspace valid: claims={len(progress.claims)} completed={progress.completed} "
        f"decisions={progress.decisions}/{progress.decisions_required} "
        f"declarations={progress.declarations}/{len(progress.claims)}"
    )
    for claim in progress.incomplete:
        print(f"cannot finalize {claim.claim_id}: {claim.missing_description()}")
    return 0


def _truth_claim(truth: dict[str, Any], claim_id: str) -> dict[str, Any]:
    for claim in truth["claims"]:
        if claim.get("claim_id") == claim_id:
            return cast(dict[str, Any], claim)
    raise ReviewerWorkspaceError(f"candidate truth has no claim {claim_id!r}")


def _command_show(args: argparse.Namespace, clock: Callable[[], datetime]) -> int:
    workspace = read_workspace(args.workspace)
    truth, truth_sha256 = _load_truth(args.claims)
    _require_truth_binding(workspace, truth, truth_sha256, args.claims)
    assigned = workspace.claim(args.claim_id)
    record = _truth_claim(truth, assigned.claim_id)
    source = record["source"]
    statement = record["claim"]
    print(f"claim_id: {record['claim_id']}")
    print(f"repo_id: {record['repo_id']}")
    print(f"repo_commit: {record['repo_commit']}")
    print(f"source.path: {source.get('path')}")
    print(f"source.line_start: {source.get('line_start')}")
    print(f"source.line_end: {source.get('line_end')}")
    print(f"source.sha256: {source.get('sha256')}")
    print(f"source.quote: {source.get('quote')}")
    for field in ("text", "metric", "value", "unit", "context"):
        print(f"claim.{field}: {statement.get(field)}")
    if args.target is None:
        return 0
    target = _require_target(args.target, "show")
    links = [entry for entry in record["expected_links"] if entry["target"] == target]
    if len(links) != 1:
        raise ReviewerWorkspaceError(
            f"candidate truth claim {assigned.claim_id} does not record exactly one {target} link"
        )
    link = links[0]
    print(f"link.target: {link['target']}")
    print(f"link.expected_resolution: {link['expected_resolution']}")
    print(f"link.rationale: {link['rationale']}")
    for number, artifact in enumerate(link["artifacts"], 1):
        print(f"link.artifact {number} kind: {artifact.get('kind')}")
        print(f"link.artifact {number} path: {artifact.get('path')}")
        print(f"link.artifact {number} sha256: {artifact.get('sha256')}")
        print(f"link.artifact {number} role: {artifact.get('role')}")
    return 0


def _command_declare(args: argparse.Namespace, clock: Callable[[], datetime]) -> int:
    workspace_path = cast(Path, args.workspace)
    workspace = read_workspace(workspace_path)
    previous_sha256 = sha256_file(workspace_path)
    missing = [
        f"--{attribute.replace('_', '-')}"
        for attribute, _ in AFFIRMATIONS
        if getattr(args, attribute) is not True
    ]
    if missing:
        raise ReviewerWorkspaceError(
            f"cannot declare for {args.claim_id}: missing {', '.join(missing)}; "
            f"{_REASSIGNMENT_NOTICE}. Nothing was written"
        )
    claim = workspace.claim(args.claim_id)
    if claim.declarations is not None:
        raise ReviewerWorkspaceError(
            f"{claim.claim_id} already carries declarations made at "
            f"{claim.declarations.blinding_declared_at}; clear them with "
            "'clear-field --field declarations --confirm' first. Nothing was written"
        )
    declared_at = _now(clock)
    updated = workspace.with_claim(
        replace(
            claim,
            declarations=ClaimDeclarations(
                blinding_declared_at=declared_at,
                conflict_declared_at=declared_at,
                repository_id=claim.repo_id,
                artifact_id=claim.claim_id,
            ),
        ),
        declared_at,
    )
    _persist(workspace_path, updated, previous_sha256)
    print(
        f"recorded blinding and conflict-of-interest declarations for {claim.claim_id} at "
        f"{declared_at}; scope={claim.repo_id}/{claim.claim_id}"
    )
    return 0


def _require_declared(claim: ClaimWorkspace) -> None:
    if claim.declarations is None:
        raise ReviewerWorkspaceError(
            f"cannot record a decision for {claim.claim_id} before its declarations: the "
            "blinding and conflict-of-interest declarations must precede every decision on "
            "the claim. Run 'declare' first. Nothing was written"
        )


def _withdraw_finalization(claim: ClaimWorkspace) -> tuple[ClaimWorkspace, str]:
    # Editing a finalized claim withdraws its finalization: reviewed_at in any export must be
    # the moment the reviewer finalized a complete, unchanged set of decisions.
    if claim.finalized_at is None:
        return claim, ""
    return replace(claim, finalized_at=None), "; finalization withdrawn"


def _command_record_claim(args: argparse.Namespace, clock: Callable[[], datetime]) -> int:
    workspace_path = cast(Path, args.workspace)
    workspace = read_workspace(workspace_path)
    previous_sha256 = sha256_file(workspace_path)
    claim = workspace.claim(args.claim_id)
    _require_declared(claim)
    context = f"{claim.claim_id} claim decision"
    entry = DecisionEntry(
        decision=_require_decision(args.decision, context),
        rationale=_require_text(args.rationale, f"{context} rationale"),
        evidence=parse_evidence(args.evidence, context),
        recorded_at=_now(clock),
    )
    # Revising one's own judgement before finalization is legitimate, so a re-record replaces
    # the entry and refreshes recorded_at rather than being refused.
    edited, withdrawal = _withdraw_finalization(replace(claim, claim_decision=entry))
    _persist(workspace_path, workspace.with_claim(edited, entry.recorded_at), previous_sha256)
    print(
        f"recorded claim decision for {claim.claim_id}: decision={entry.decision} "
        f"evidence={len(entry.evidence)} at {entry.recorded_at}{withdrawal}"
    )
    return 0


def _command_record_link(args: argparse.Namespace, clock: Callable[[], datetime]) -> int:
    workspace_path = cast(Path, args.workspace)
    workspace = read_workspace(workspace_path)
    previous_sha256 = sha256_file(workspace_path)
    claim = workspace.claim(args.claim_id)
    _require_declared(claim)
    target = _require_target(args.target, f"{claim.claim_id} link decision")
    context = f"{claim.claim_id} {target} link decision"
    entry = DecisionEntry(
        decision=_require_decision(args.decision, context),
        rationale=_require_text(args.rationale, f"{context} rationale"),
        evidence=parse_evidence(args.evidence, context),
        recorded_at=_now(clock),
        target=target,
    )
    replaced = claim.decision_for(target) is not None
    retained: dict[str, DecisionEntry] = {
        str(existing.target): existing for existing in claim.link_decisions
    }
    retained[target] = entry
    edited, withdrawal = _withdraw_finalization(
        replace(
            claim,
            link_decisions=tuple(retained[name] for name in TARGETS if name in retained),
        )
    )
    _persist(workspace_path, workspace.with_claim(edited, entry.recorded_at), previous_sha256)
    print(
        f"{'replaced' if replaced else 'recorded'} {target} link decision for "
        f"{claim.claim_id}: decision={entry.decision} evidence={len(entry.evidence)} "
        f"at {entry.recorded_at}{withdrawal}"
    )
    return 0


def _command_clear_field(args: argparse.Namespace, clock: Callable[[], datetime]) -> int:
    workspace_path = cast(Path, args.workspace)
    workspace = read_workspace(workspace_path)
    previous_sha256 = sha256_file(workspace_path)
    if not args.confirm:
        raise ReviewerWorkspaceError(
            f"cannot clear {args.field} for {args.claim_id} without --confirm; clearing "
            "discards recorded human input. Nothing was written"
        )
    if args.field != "link" and args.target is not None:
        raise ReviewerWorkspaceError(
            f"clear-field --field {args.field} does not take --target. Nothing was written"
        )
    claim = workspace.claim(args.claim_id)
    if args.field == "declarations":
        if claim.declarations is None:
            raise ReviewerWorkspaceError(
                f"{claim.claim_id} has no declarations to clear. Nothing was written"
            )
        edited = replace(claim, declarations=None)
        described = "declarations"
    elif args.field == "claim":
        if claim.claim_decision is None:
            raise ReviewerWorkspaceError(
                f"{claim.claim_id} has no claim decision to clear. Nothing was written"
            )
        edited = replace(claim, claim_decision=None)
        described = "claim decision"
    else:
        if args.target is None:
            raise ReviewerWorkspaceError(
                "clear-field --field link requires --target. Nothing was written"
            )
        target = _require_target(args.target, f"{claim.claim_id} clear-field")
        if claim.decision_for(target) is None:
            raise ReviewerWorkspaceError(
                f"{claim.claim_id} has no {target} link decision to clear. Nothing was written"
            )
        edited = replace(
            claim,
            link_decisions=tuple(
                entry for entry in claim.link_decisions if entry.target != target
            ),
        )
        described = f"{target} link decision"
    edited, withdrawal = _withdraw_finalization(edited)
    updated_at = _now(clock)
    _persist(workspace_path, workspace.with_claim(edited, updated_at), previous_sha256)
    print(f"cleared {described} for {claim.claim_id} at {updated_at}{withdrawal}")
    return 0


def _command_finalize_claim(args: argparse.Namespace, clock: Callable[[], datetime]) -> int:
    workspace_path = cast(Path, args.workspace)
    workspace = read_workspace(workspace_path)
    previous_sha256 = sha256_file(workspace_path)
    truth, truth_sha256 = _load_truth(args.claims)
    _require_truth_binding(workspace, truth, truth_sha256, args.claims)
    _, scaffold = _bound_scaffold(workspace)
    claim = workspace.claim(args.claim_id)
    progress = claim_progress(claim)
    if not progress.complete:
        raise ReviewerWorkspaceError(
            f"cannot finalize {claim.claim_id}: {progress.missing_description()}. "
            "Nothing was written"
        )
    finalized_at = _now(clock)
    declarations = claim.declarations
    if declarations is None:
        raise ReviewerWorkspaceError(
            f"cannot finalize {claim.claim_id}: missing declarations. Nothing was written"
        )
    for label, value in (
        ("blinding_declaration.declared_at", declarations.blinding_declared_at),
        ("conflict_of_interest_declaration.declared_at", declarations.conflict_declared_at),
    ):
        if _parse_timestamp(value, label) >= _parse_timestamp(finalized_at, "finalized_at"):
            raise ReviewerWorkspaceError(
                f"cannot finalize {claim.claim_id}: {label} {value} is not strictly earlier "
                f"than the finalization timestamp {finalized_at}. Nothing was written"
            )
    candidate = workspace.with_claim(replace(claim, finalized_at=finalized_at), finalized_at)
    validate_review(build_final_review(candidate, scaffold), truth, truth_sha256)
    _persist(workspace_path, candidate, previous_sha256)
    finalized = review_progress(candidate).finalized
    print(
        f"finalized {claim.claim_id} at {finalized_at}: "
        f"finalized claims {finalized}/{len(candidate.claims)}"
    )
    return 0


def _command_finalize_review(args: argparse.Namespace, clock: Callable[[], datetime]) -> int:
    workspace_path = cast(Path, args.workspace)
    out = cast(Path, args.out)
    workspace = read_workspace(workspace_path)
    truth, truth_sha256 = _load_truth(args.claims)
    _require_truth_binding(workspace, truth, truth_sha256, args.claims)
    scaffold_path, scaffold = _bound_scaffold(workspace)
    progress = review_progress(workspace)
    if not progress.export_ready:
        pending = [claim.claim_id for claim in progress.claims if not claim.finalized]
        raise ReviewerWorkspaceError(
            f"cannot export a final claim review: finalized={progress.finalized}/"
            f"{len(progress.claims)} decisions={progress.decisions}/"
            f"{progress.decisions_required}; claims awaiting finalization are {pending}. "
            "Nothing was written"
        )
    if out.exists() or out.is_symlink():
        raise ReviewerWorkspaceError(
            f"refusing to overwrite existing final claim review {out}; an exported review is "
            "immutable and a correction is a newly generated file. Nothing was written"
        )
    ensure_output_outside(out, [workspace_path, args.claims, scaffold_path])
    payload = build_final_review(workspace, scaffold)
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(out, payload)
    written = _load_json(out, "final claim review")
    summary = validate_review(written, truth, truth_sha256)
    reviews = sum(len(record["reviews"]) for record in written["claims"])
    decisions = sum(
        1 + len(review["link_decisions"])
        for record in written["claims"]
        for review in record["reviews"]
    )
    required = len(written["claims"]) * DECISIONS_PER_CLAIM
    if reviews != len(written["claims"]) or decisions != required:
        raise ReviewerWorkspaceError(
            f"exported claim review {out} carries {reviews} review(s) and {decisions} "
            f"decision(s) for {len(written['claims'])} claim(s); expected one review per claim "
            f"and {required} decisions"
        )
    print(
        f"wrote final claim review {out}: claims={len(written['claims'])} reviews={reviews} "
        f"decisions={decisions}/{required}; validated against {args.claims}"
    )
    print(f"claim_review.validate_review summary: {summary}")
    return 0


def _command_verify(args: argparse.Namespace, clock: Callable[[], datetime]) -> int:
    workspace_path = cast(Path, args.workspace)
    workspace = read_workspace(workspace_path)
    truth, truth_sha256 = _load_truth(args.claims)
    _require_truth_binding(workspace, truth, truth_sha256, args.claims)
    scaffold_path, scaffold = _bound_scaffold(workspace)
    build_final_review(workspace, scaffold)
    progress = review_progress(workspace)
    print(
        f"verified reviewer workspace {workspace_path}: "
        f"reviewer={workspace.identity.reviewer_id} claims={len(progress.claims)} "
        f"completed={progress.completed} finalized={progress.finalized} "
        f"decisions={progress.decisions}/{progress.decisions_required} "
        f"declarations={progress.declarations}/{len(progress.claims)}; "
        f"bound to scaffold {scaffold_path} and candidate truth sha256={truth_sha256}"
    )
    return 0


_COMMANDS: dict[str, Callable[[argparse.Namespace, Callable[[], datetime]], int]] = {
    "init": _command_init,
    "status": _command_status,
    "show": _command_show,
    "declare": _command_declare,
    "record-claim": _command_record_claim,
    "record-link": _command_record_link,
    "clear-field": _command_clear_field,
    "finalize-claim": _command_finalize_claim,
    "finalize-review": _command_finalize_review,
    "verify": _command_verify,
}


def _build_parser() -> argparse.ArgumentParser:
    decisions = ", ".join(sorted(DECISIONS))
    targets = ", ".join(TARGETS)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="create an empty reviewer workspace")
    init_parser.add_argument("--scaffold", type=Path, required=True)
    init_parser.add_argument("--claims", type=Path, required=True)
    init_parser.add_argument("--reviewer-id", required=True)
    init_parser.add_argument("--domain-expertise", required=True)
    init_parser.add_argument("--workspace", type=Path, required=True)

    status_parser = subparsers.add_parser("status", help="report entry progress")
    status_parser.add_argument("--workspace", type=Path, required=True)
    status_parser.add_argument("--json", action="store_true")

    show_parser = subparsers.add_parser("show", help="print one frozen claim record")
    show_parser.add_argument("--workspace", type=Path, required=True)
    show_parser.add_argument("--claims", type=Path, required=True)
    show_parser.add_argument("--claim-id", required=True)
    show_parser.add_argument("--target", help=f"one of: {targets}")

    declare_parser = subparsers.add_parser(
        "declare", help="record the blinding and conflict-of-interest declarations"
    )
    declare_parser.add_argument("--workspace", type=Path, required=True)
    declare_parser.add_argument("--claim-id", required=True)
    for attribute, _ in AFFIRMATIONS:
        declare_parser.add_argument(f"--{attribute.replace('_', '-')}", action="store_true")

    claim_parser = subparsers.add_parser("record-claim", help="record one claim decision")
    claim_parser.add_argument("--workspace", type=Path, required=True)
    claim_parser.add_argument("--claim-id", required=True)
    claim_parser.add_argument("--decision", required=True, help=f"one of: {decisions}")
    claim_parser.add_argument("--rationale", required=True)
    claim_parser.add_argument("--evidence", action="append", default=[])

    link_parser = subparsers.add_parser("record-link", help="record one claim-link decision")
    link_parser.add_argument("--workspace", type=Path, required=True)
    link_parser.add_argument("--claim-id", required=True)
    link_parser.add_argument("--target", required=True, help=f"one of: {targets}")
    link_parser.add_argument("--decision", required=True, help=f"one of: {decisions}")
    link_parser.add_argument("--rationale", required=True)
    link_parser.add_argument("--evidence", action="append", default=[])

    clear_parser = subparsers.add_parser("clear-field", help="discard one recorded entry")
    clear_parser.add_argument("--workspace", type=Path, required=True)
    clear_parser.add_argument("--claim-id", required=True)
    clear_parser.add_argument("--field", required=True, choices=["declarations", "claim", "link"])
    clear_parser.add_argument("--target", help=f"one of: {targets}")
    clear_parser.add_argument("--confirm", action="store_true")

    finalize_claim_parser = subparsers.add_parser(
        "finalize-claim", help="finalize one complete claim"
    )
    finalize_claim_parser.add_argument("--workspace", type=Path, required=True)
    finalize_claim_parser.add_argument("--claims", type=Path, required=True)
    finalize_claim_parser.add_argument("--claim-id", required=True)

    finalize_review_parser = subparsers.add_parser(
        "finalize-review", help="export the final single-reviewer claim review"
    )
    finalize_review_parser.add_argument("--workspace", type=Path, required=True)
    finalize_review_parser.add_argument("--claims", type=Path, required=True)
    finalize_review_parser.add_argument("--out", type=Path, required=True)

    verify_parser = subparsers.add_parser("verify", help="validate a workspace end to end")
    verify_parser.add_argument("--workspace", type=Path, required=True)
    verify_parser.add_argument("--claims", type=Path, required=True)
    return parser


def main(
    argv: list[str] | None = None, *, clock: Callable[[], datetime] = _utc_now
) -> int:
    # The clock is injected for tests only and is deliberately not exposed as a --now flag:
    # a reviewer must not be able to backdate a declaration, a decision, or a finalization.
    args = _build_parser().parse_args(argv)
    try:
        return _COMMANDS[args.command](args, clock)
    except (
        ClaimGroundTruthError,
        ClaimReviewError,
        ReviewerWorkspaceError,
        RunContractError,
        OSError,
    ) as exc:
        sys.exit(f"invalid reviewer workspace: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
