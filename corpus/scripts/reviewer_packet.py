#!/usr/bin/env python3
"""Build, verify and inspect the derived packet handed to one blinded reviewer.

A packet is assembled from an explicit allowlist of roles, never by copying a
directory, so material that would unblind a reviewer cannot arrive by accident.
It carries the reviewer documents, the frozen claim record, the two schemas an
entry tool validates against, an empty review scaffold, and a wrapper that
re-verifies the packet from inside it.  This module is standard-library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

if __package__:
    from .claim_ground_truth import ClaimGroundTruthError, validate_ground_truth_structure
    from .claim_review import ClaimReviewError, validate_review
    from .clone_repos import read_repos
    from .run_contract import (
        RunContractError,
        ensure_output_outside,
        load_json_object_bytes,
        sha256_file,
        write_json,
    )
else:
    from claim_ground_truth import ClaimGroundTruthError, validate_ground_truth_structure
    from claim_review import ClaimReviewError, validate_review
    from clone_repos import read_repos
    from run_contract import (
        RunContractError,
        ensure_output_outside,
        load_json_object_bytes,
        sha256_file,
        write_json,
    )

PACKET_SCHEMA_VERSION = 1
MANIFEST_NAME = "PACKET_MANIFEST.json"
GUIDE_NAME = "REVIEWER_GUIDE.md"
CHECKLIST_NAME = "REVIEWER_CHECKLIST.md"
CLAIMS_NAME = "pilot-claims.json"
REVIEW_SCHEMA_NAME = "claim-review.schema.json"
WORKSPACE_SCHEMA_NAME = "reviewer-workspace.schema.json"
SCAFFOLD_NAME = "review-scaffold.json"
VERIFY_WRAPPER_NAME = "verify_packet.py"

PACKET_ROLES: tuple[tuple[str, str], ...] = (
    (GUIDE_NAME, "reviewer_guide"),
    (CHECKLIST_NAME, "reviewer_checklist"),
    (CLAIMS_NAME, "claim_ground_truth"),
    (REVIEW_SCHEMA_NAME, "claim_review_schema"),
    (WORKSPACE_SCHEMA_NAME, "reviewer_workspace_schema"),
    (SCAFFOLD_NAME, "review_scaffold"),
    (VERIFY_WRAPPER_NAME, "verify_wrapper"),
)
ROLE_BY_PACKET_PATH = dict(PACKET_ROLES)
COPIED_SOURCES: tuple[tuple[str, str], ...] = (
    (GUIDE_NAME, "guide"),
    (CHECKLIST_NAME, "checklist"),
    (CLAIMS_NAME, "claims"),
    (REVIEW_SCHEMA_NAME, "review-schema"),
    (WORKSPACE_SCHEMA_NAME, "workspace-schema"),
    (SCAFFOLD_NAME, "review"),
)
EXCLUDED_MATERIAL_CLASSES: tuple[str, ...] = (
    "peer_review",
    "merged_review",
    "adjudication",
    "adduce_claim_link_output",
    "candidate_aggregate_results",
    "coordinator_answer_summary",
)

# Filename denylist, applied to a source's own name and its parent directory
# name.  The allowlist of roles above is the primary control; this catches a
# withheld artifact being passed in under an explicit flag.
FORBIDDEN_PATH_MARKERS: tuple[tuple[str, str], ...] = (
    ("pilot-claim-links", "Adduce claim-link output over the truth under review"),
    ("claim-evaluation", "Adduce claim-link output over the truth under review"),
    ("effectiveness_metrics", "a coordinator-only effectiveness document"),
    ("coordinator", "a coordinator-only document"),
    ("adjudication", "adjudication output"),
    ("merged", "a merged review"),
    ("completed-review", "another reviewer's completed decisions"),
    ("answer-key", "an answer-key summary"),
    ("summary", "a generated candidate summary"),
    ("determinism", "a candidate report"),
    ("combined.csv", "candidate aggregate results"),
)
REFUSED_OUTPUT_ROOTS: tuple[str, ...] = (
    "corpus/labels",
    "corpus/outputs",
    "corpus/reports",
    "corpus/clones",
    "corpus/synthetic",
    "corpus/review",
    "corpus/scripts",
    "corpus",
)
# A reviewer identifier embedded in a scaffold filename must be this packet's
# reviewer.  These tokens name packet structure rather than a person and are
# therefore not identities.
STRUCTURAL_REVIEWER_TOKENS = frozenset(
    {
        "reviewer-workspace",
        "reviewer-packet",
        "reviewer-packets",
        "reviewer-guide",
        "reviewer-checklist",
        "reviewer-entry",
        "reviewer-feedback",
        "reviewer-id",
    }
)
# Content markers that must never reach a reviewer document, with the reason
# each unblinds or misdirects a reviewer.
FORBIDDEN_CONTENT_SENTINELS: tuple[tuple[str, str], ...] = (
    ("pilot-claim-links", "names the withheld Adduce claim-link output for this truth"),
    ("--require-accepted", "a coordinator acceptance gate over the reviewer's own answers"),
    ("--require-complete", "a coordinator completeness gate over the reviewer's own answers"),
    ("claim_review.py merge", "the coordinator step that joins both reviewers' files"),
    ("Facts appendix", "a derived answer-shaped digest of the record under review"),
    ("all claims are partial", "an aggregate over the expected resolutions"),
)
# Ten whitespace-separated R/U/N tokens are the compact expected-resolution map
# for one claim; it is the answer key in its shortest form.
EXPECTED_RESOLUTION_MAP_RE = re.compile(r"(?<![0-9A-Za-z])[RUN](?:\s+[RUN]){9}(?![0-9A-Za-z])")
EXPECTED_RESOLUTION_MAP_REASON = "a compact expected-resolution map for a claim under review"

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_PACKET_ID_RE = re.compile(r"^packet-[0-9a-f]{32}$")
_REVIEWER_TOKEN_RE = re.compile(r"reviewer-[a-z0-9]+(?:[-_][a-z0-9]+)*")

# Deterministic bytes for the in-packet verifier.  It re-checks the packet's
# own integrity and never carries the withheld-content sentinel table: shipping
# those strings would put the withheld phrasing, including an answer-shaped
# one, into the hands of the reviewer the packet is blinding.
VERIFY_WRAPPER_SOURCE = r'''#!/usr/bin/env python3
"""Verify this reviewer packet against the manifest it ships with.

Run it from inside the packet directory:

    python -B verify_packet.py

Standard library only and no arguments, so the packet stays verifiable wherever
it has been copied.  Ask the coordinator for a replacement rather than editing
anything by hand.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

MANIFEST_NAME = "PACKET_MANIFEST.json"
CLAIMS_NAME = "pilot-claims.json"
SCAFFOLD_NAME = "review-scaffold.json"
EXPECTED_ROLES = {
    "REVIEWER_GUIDE.md": "reviewer_guide",
    "REVIEWER_CHECKLIST.md": "reviewer_checklist",
    "pilot-claims.json": "claim_ground_truth",
    "claim-review.schema.json": "claim_review_schema",
    "reviewer-workspace.schema.json": "reviewer_workspace_schema",
    "review-scaffold.json": "review_scaffold",
    "verify_packet.py": "verify_wrapper",
}


class PacketError(ValueError):
    """This packet no longer matches the manifest it ships with."""


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise PacketError(f"cannot read {path.name}: {exc}") from exc
    if not isinstance(payload, dict):
        raise PacketError(f"{path.name} is not a JSON object")
    return payload


def check_scaffold(scaffold: dict[str, Any], manifest: dict[str, Any]) -> None:
    if scaffold.get("initial_review_sources") != []:
        raise PacketError("the review scaffold carries merged review provenance")
    claims = scaffold.get("claims")
    if not isinstance(claims, list) or not claims:
        raise PacketError("the review scaffold has no claim records")
    for claim in claims:
        claim_id = str(claim.get("claim_id"))
        if claim.get("reviews") != [] or claim.get("adjudication") is not None:
            raise PacketError(f"the review scaffold carries a decision for claim {claim_id}")
    if scaffold.get("claim_ground_truth_sha256") != manifest.get("claim_ground_truth_sha256"):
        raise PacketError("the review scaffold is bound to different claim ground truth")
    if scaffold.get("candidate_pair") != manifest.get("candidate_pair"):
        raise PacketError("the review scaffold is bound to a different candidate pair")


def verify(packet: Path) -> dict[str, Any]:
    manifest = load_json(packet / MANIFEST_NAME)
    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise PacketError("the manifest records no packet files")
    expected = {MANIFEST_NAME}
    for entry in entries:
        path = str(entry.get("path"))
        if EXPECTED_ROLES.get(path) != entry.get("role"):
            raise PacketError(f"manifest entry is not an allowed packet file and role: {path}")
        if path in expected:
            raise PacketError(f"manifest lists {path} more than once")
        expected.add(path)
        target = packet / path
        if target.is_symlink() or not target.is_file():
            raise PacketError(f"missing packet file: {path}")
        data = target.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry.get("sha256"):
            raise PacketError(f"packet file does not match its recorded digest: {path}")
        if len(data) != entry.get("size_bytes"):
            raise PacketError(f"packet file does not match its recorded size: {path}")
    if expected != {MANIFEST_NAME} | set(EXPECTED_ROLES):
        missing = sorted(({MANIFEST_NAME} | set(EXPECTED_ROLES)) - expected)
        raise PacketError(f"the manifest does not list every required packet file: {missing}")
    present = {entry.relative_to(packet).as_posix() for entry in packet.rglob("*")}
    if present != expected:
        raise PacketError(
            "packet contents differ from the manifest "
            f"(unexpected={sorted(present - expected)}, missing={sorted(expected - present)})"
        )
    check_scaffold(load_json(packet / SCAFFOLD_NAME), manifest)
    claims_digest = hashlib.sha256((packet / CLAIMS_NAME).read_bytes()).hexdigest()
    if claims_digest != manifest.get("claim_ground_truth_sha256"):
        raise PacketError("the packet claim record does not match its recorded digest")
    return manifest


def main() -> int:
    packet = Path(__file__).resolve().parent
    try:
        manifest = verify(packet)
    except PacketError as exc:
        print(f"reviewer packet verification failed: {exc}", file=sys.stderr)
        return 2
    truth = str(manifest.get("claim_ground_truth_sha256"))[:8]
    print(
        f"review packet verified: files={len(manifest['files'])} "
        f"repositories={len(manifest['repositories'])} "
        f"repository_bindings=unchecked truth={truth}..."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


class ReviewerPacketError(ValueError):
    """A reviewer packet is unsafe to build, or no longer matches its manifest."""


@dataclass(frozen=True)
class PacketEntry:
    """One file inside a packet, measured after it was written."""

    path: str
    sha256: str
    size_bytes: int
    role: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "role": self.role,
        }


@dataclass(frozen=True)
class RepositoryBinding:
    """One pinned repository the packet expects to find in the shared clone root."""

    repository_id: str
    expected_commit: str
    resolved_commit: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "repository_id": self.repository_id,
            "expected_commit": self.expected_commit,
            "resolved_commit": self.resolved_commit,
            "read_only_expected": True,
        }


@dataclass(frozen=True)
class PacketManifest:
    """The validated manifest of one reviewer packet."""

    reviewer_id: str
    created_at: str
    source_commit: str | None
    claim_ground_truth_sha256: str
    corpus_inventory_sha256: str
    candidate_pair: tuple[str, str]
    review_scaffold_sha256: str
    clone_root: str
    repositories: tuple[RepositoryBinding, ...]
    files: tuple[PacketEntry, ...]

    @property
    def packet_id(self) -> str:
        return packet_id(self.body())

    def to_payload(self) -> dict[str, Any]:
        payload = self.body()
        payload["packet_id"] = self.packet_id
        return payload

    def body(self) -> dict[str, Any]:
        return {
            "packet_schema_version": PACKET_SCHEMA_VERSION,
            "reviewer_id": self.reviewer_id,
            "created_at": self.created_at,
            "source_commit": self.source_commit,
            "claim_ground_truth_sha256": self.claim_ground_truth_sha256,
            "corpus_inventory_sha256": self.corpus_inventory_sha256,
            "candidate_pair": list(self.candidate_pair),
            "review_scaffold_sha256": self.review_scaffold_sha256,
            "clone_root": self.clone_root,
            "repositories": [binding.to_payload() for binding in self.repositories],
            "files": [entry.to_payload() for entry in self.files],
            "excluded_material_classes": list(EXCLUDED_MATERIAL_CLASSES),
        }


@dataclass(frozen=True)
class PacketVerification:
    """Outcome of an independent re-check of a materialised packet."""

    manifest: PacketManifest
    repositories_verified: bool


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _canonical(payload: Any) -> bytes:
    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ReviewerPacketError(f"cannot canonicalize packet manifest: {exc}") from exc


def packet_id(body: dict[str, Any]) -> str:
    """Derive the packet identity from the manifest body, which carries created_at."""
    return "packet-" + hashlib.sha256(_canonical(body)).hexdigest()[:32]


def _git(arguments: list[str], cwd: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(cwd), *arguments],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def source_commit(root: Path) -> str | None:
    """Return the commit of *root* itself, or None when it is not a git repository."""
    output = _git(["rev-parse", "--show-toplevel", "HEAD"], root)
    if output is None:
        return None
    lines = output.splitlines()
    if len(lines) != 2:
        return None
    try:
        same_tree = Path(lines[0]).resolve() == root.resolve()
    except OSError:
        return None
    commit = lines[1].strip().lower()
    if not same_tree or not _COMMIT_RE.fullmatch(commit):
        return None
    return commit


def _identifier(value: object, context: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ReviewerPacketError(f"{context} must be a stable non-personal identifier")
    return value


def _digest(value: object, context: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ReviewerPacketError(f"{context} must be a full lowercase SHA-256")
    return value


def _timestamp(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReviewerPacketError(f"{context} must be a timezone-aware timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReviewerPacketError(f"{context} is not an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReviewerPacketError(f"{context} must include a UTC offset")
    return value


def reject_withheld_path(path: Path, label: str) -> None:
    """Refuse a source that names material a reviewer must not receive."""
    candidates = [path.name.lower(), path.parent.name.lower()]
    for marker, reason in FORBIDDEN_PATH_MARKERS:
        if any(marker in candidate for candidate in candidates):
            raise ReviewerPacketError(
                f"--{label} {path} names {reason} ({marker!r}); a packet never carries it"
            )


def reject_other_reviewer_scaffold(path: Path, reviewer_id: str) -> None:
    """Refuse a scaffold whose path names a reviewer other than this packet's."""
    tokens = {
        token
        for token in _REVIEWER_TOKEN_RE.findall(str(path).lower())
        if token not in STRUCTURAL_REVIEWER_TOKENS
    }
    foreign = sorted(token for token in tokens if token != reviewer_id.lower())
    if foreign:
        raise ReviewerPacketError(
            f"--review {path} belongs to {foreign[0]} rather than {reviewer_id}; "
            "a packet never carries another reviewer's artifact"
        )


def require_regular_source(path: Path, label: str, root: Path) -> Path:
    """Require a regular file reached without a symlink at or below *root*.

    Only components inside the supplied root are walked. An ancestor above the
    root is the operator's own filesystem layout, not a plantable redirect, and
    refusing one is a false positive: on macOS ``/tmp`` is a symlink.
    """
    absolute = path.absolute()
    try:
        walked = root.resolve()
        components = absolute.relative_to(walked).parts
    except (OSError, ValueError):
        walked = absolute.parent
        components = (absolute.name,)
    for component in components:
        walked = walked / component
        if walked.is_symlink():
            raise ReviewerPacketError(
                f"--{label} {path} passes through symlink {walked}; "
                "packet sources must be regular files on a real path"
            )
    if not absolute.is_file():
        raise ReviewerPacketError(f"--{label} is not a regular file: {path}")
    return absolute


def require_source_outside_evidence(path: Path, label: str, root: Path) -> None:
    """Refuse a source drawn from the immutable run or report trees."""
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return
    for refused in ("corpus/outputs", "corpus/reports"):
        if relative == refused or relative.startswith(f"{refused}/"):
            raise ReviewerPacketError(
                f"--{label} {path} is inside {refused}; a packet never carries "
                "candidate run output or a candidate report"
            )


def scan_reviewer_document(data: bytes, label: str) -> None:
    """Refuse a reviewer document carrying content that would unblind a reviewer."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReviewerPacketError(f"--{label} is not valid UTF-8: {exc}") from exc
    for sentinel, reason in FORBIDDEN_CONTENT_SENTINELS:
        if sentinel in text:
            raise ReviewerPacketError(
                f"--{label} contains {sentinel!r}, which is {reason}; refusing to ship it"
            )
    match = EXPECTED_RESOLUTION_MAP_RE.search(text)
    if match is not None:
        raise ReviewerPacketError(
            f"--{label} contains {EXPECTED_RESOLUTION_MAP_REASON}; refusing to ship it"
        )


def require_empty_scaffold(scaffold: dict[str, Any]) -> None:
    """Require a scaffold with no human decision of any kind."""
    if scaffold.get("initial_review_sources") != []:
        raise ReviewerPacketError(
            "review scaffold carries merged review provenance; a packet ships an empty scaffold"
        )
    claims = scaffold.get("claims")
    if not isinstance(claims, list) or not claims:
        raise ReviewerPacketError("review scaffold has no claim records")
    for number, claim in enumerate(claims, 1):
        if not isinstance(claim, dict):
            raise ReviewerPacketError(f"review scaffold claim record {number} is invalid")
        claim_id = str(claim.get("claim_id"))
        if claim.get("reviews") != []:
            raise ReviewerPacketError(
                f"review scaffold already carries a review decision for claim {claim_id}; "
                "a packet ships an empty scaffold"
            )
        if claim.get("adjudication") is not None:
            raise ReviewerPacketError(
                f"review scaffold already carries an adjudication for claim {claim_id}; "
                "a packet ships an empty scaffold"
            )


def candidate_pair(scaffold: dict[str, Any]) -> tuple[str, str]:
    """Return the scaffold's well-formed candidate pair."""
    value = scaffold.get("candidate_pair")
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(not isinstance(item, str) or not _ID_RE.fullmatch(item) for item in value)
        or value[0] == value[1]
    ):
        raise ReviewerPacketError("review scaffold has an invalid candidate pair")
    return (str(value[0]), str(value[1]))


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        return cast(dict[str, Any], load_json_object_bytes(path.read_bytes(), f"{label} {path}"))
    except (OSError, RunContractError) as exc:
        raise ReviewerPacketError(f"cannot read {label} {path}: {exc}") from exc


def claimed_repositories(truth: dict[str, Any], repos: Path) -> dict[str, str]:
    """Map each repository a claim names to the commit the inventory declares for it."""
    inventory = {row["id"]: row["commit_sha"] for row in read_repos(repos)}
    claims = truth.get("claims")
    if not isinstance(claims, list) or not claims:
        raise ReviewerPacketError("claim ground truth has no claim records")
    # A packet binds the repositories the frozen truth names, not every inventory
    # row: a repository carrying no claim enters no reviewer assignment.
    claimed: dict[str, str] = {}
    for number, claim in enumerate(claims, 1):
        if not isinstance(claim, dict):
            raise ReviewerPacketError(f"claim ground truth record {number} is invalid")
        repository_id = str(claim.get("repo_id"))
        declared = inventory.get(repository_id)
        if declared is None:
            raise ReviewerPacketError(
                f"claim {number} names repository {repository_id}, "
                f"which is absent from the inventory {repos}"
            )
        pinned = str(claim.get("repo_commit"))
        if pinned != declared:
            raise ReviewerPacketError(
                f"claim {number} pins repository {repository_id} at {pinned}, "
                f"inventory {repos} declares {declared}"
            )
        claimed[repository_id] = declared
    return claimed


def repository_bindings(
    truth: dict[str, Any], repos: Path, clones: Path
) -> tuple[RepositoryBinding, ...]:
    """Bind every claimed repository to the commit its clone actually resolves to."""
    if not clones.is_dir():
        raise ReviewerPacketError(
            f"clone root {clones} is absent; no repository commit can be resolved"
        )
    bindings: list[RepositoryBinding] = []
    for repository_id, expected in sorted(claimed_repositories(truth, repos).items()):
        clone = clones / repository_id
        if not clone.is_dir():
            raise ReviewerPacketError(f"repository {repository_id} has no clone under {clones}")
        resolved = _git(["rev-parse", "HEAD"], clone)
        if resolved is None or not _COMMIT_RE.fullmatch(resolved.lower()):
            raise ReviewerPacketError(
                f"repository {repository_id} clone {clone} has no resolvable git HEAD"
            )
        if resolved.lower() != expected:
            raise ReviewerPacketError(
                f"repository {repository_id} clone is at {resolved.lower()}, "
                f"expected {expected}"
            )
        bindings.append(RepositoryBinding(repository_id, expected, resolved.lower()))
    return tuple(bindings)


def clone_root_label(clones: Path, root: Path) -> str:
    resolved = clones.resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def resolve_clone_root(label: str, root: Path, override: Path | None) -> Path | None:
    if override is not None:
        return override
    candidate = Path(label)
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate if candidate.is_dir() else None


def require_safe_output(out: Path, clones: Path, root: Path) -> None:
    """Refuse an output that escapes upward, overwrites, or lands in corpus evidence."""
    if ".." in out.parts:
        raise ReviewerPacketError(
            f"--out {out} contains a parent-directory component; give an explicit path"
        )
    try:
        ensure_output_outside(out, [clones])
    except RunContractError as exc:
        raise ReviewerPacketError(str(exc)) from exc
    try:
        relative = out.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        relative = ""
    for refused in REFUSED_OUTPUT_ROOTS:
        if relative == refused or relative.startswith(f"{refused}/"):
            raise ReviewerPacketError(
                f"--out {out} is inside {refused}; build packets outside the corpus evidence tree"
            )
    if out.exists() or out.is_symlink():
        raise ReviewerPacketError(f"refusing to overwrite existing packet: {out}")


def _packet_entries(packet: Path) -> tuple[PacketEntry, ...]:
    entries = [
        PacketEntry(
            path=name,
            sha256=sha256_file(packet / name),
            size_bytes=(packet / name).stat().st_size,
            role=role,
        )
        for name, role in PACKET_ROLES
    ]
    return tuple(sorted(entries, key=lambda entry: entry.path))


def assert_exact_file_set(packet: Path) -> None:
    """Require the packet to hold every allowlisted file and nothing else."""
    expected = {MANIFEST_NAME, *ROLE_BY_PACKET_PATH}
    try:
        present = {entry.relative_to(packet).as_posix() for entry in packet.rglob("*")}
    except OSError as exc:
        raise ReviewerPacketError(f"cannot read packet {packet}: {exc}") from exc
    if present != expected:
        raise ReviewerPacketError(
            f"packet {packet} does not hold exactly the allowlisted files "
            f"(unexpected={sorted(present - expected)}, missing={sorted(expected - present)})"
        )
    for name in sorted(expected):
        if (packet / name).is_symlink() or not (packet / name).is_file():
            raise ReviewerPacketError(f"packet entry {name} is not a regular file")
        reject_withheld_path(Path(name), "packet")


def validate_manifest_payload(payload: dict[str, Any]) -> PacketManifest:
    """Validate a manifest against the published packet schema without trusting it."""
    expected_fields = {
        "packet_schema_version",
        "packet_id",
        "reviewer_id",
        "created_at",
        "source_commit",
        "claim_ground_truth_sha256",
        "corpus_inventory_sha256",
        "candidate_pair",
        "review_scaffold_sha256",
        "clone_root",
        "repositories",
        "files",
        "excluded_material_classes",
    }
    missing = expected_fields - set(payload)
    extra = set(payload) - expected_fields
    if missing or extra:
        raise ReviewerPacketError(
            f"packet manifest fields do not match the schema "
            f"(missing={sorted(missing)}, extra={sorted(extra)})"
        )
    if payload["packet_schema_version"] != PACKET_SCHEMA_VERSION:
        raise ReviewerPacketError("unsupported packet-manifest schema")
    identity = payload["packet_id"]
    if not isinstance(identity, str) or not _PACKET_ID_RE.fullmatch(identity):
        raise ReviewerPacketError("packet manifest has an invalid packet_id")
    commit = payload["source_commit"]
    if commit is not None and (not isinstance(commit, str) or not _COMMIT_RE.fullmatch(commit)):
        raise ReviewerPacketError("packet manifest has an invalid source_commit")
    clone_root = payload["clone_root"]
    if not isinstance(clone_root, str) or not clone_root:
        raise ReviewerPacketError("packet manifest has an invalid clone_root")
    if payload["excluded_material_classes"] != list(EXCLUDED_MATERIAL_CLASSES):
        raise ReviewerPacketError(
            "packet manifest does not declare the required excluded material classes"
        )

    raw_repositories = payload["repositories"]
    if not isinstance(raw_repositories, list) or not raw_repositories:
        raise ReviewerPacketError("packet manifest records no repository bindings")
    repositories: list[RepositoryBinding] = []
    for number, raw in enumerate(raw_repositories, 1):
        context = f"packet manifest repository {number}"
        if not isinstance(raw, dict) or set(raw) != {
            "repository_id",
            "expected_commit",
            "resolved_commit",
            "read_only_expected",
        }:
            raise ReviewerPacketError(f"{context} fields do not match the schema")
        if raw["read_only_expected"] is not True:
            raise ReviewerPacketError(f"{context} must declare the clone read-only")
        for field in ("expected_commit", "resolved_commit"):
            if not isinstance(raw[field], str) or not _COMMIT_RE.fullmatch(raw[field]):
                raise ReviewerPacketError(f"{context} has an invalid {field}")
        repositories.append(
            RepositoryBinding(
                repository_id=_identifier(raw["repository_id"], f"{context} repository_id"),
                expected_commit=raw["expected_commit"],
                resolved_commit=raw["resolved_commit"],
            )
        )
    ordered = sorted(repositories, key=lambda binding: binding.repository_id)
    if repositories != ordered:
        raise ReviewerPacketError("packet manifest repositories are not sorted by repository_id")
    if len({binding.repository_id for binding in repositories}) != len(repositories):
        raise ReviewerPacketError("packet manifest repeats a repository binding")

    raw_files = payload["files"]
    if not isinstance(raw_files, list):
        raise ReviewerPacketError("packet manifest records no files")
    files: list[PacketEntry] = []
    for number, raw in enumerate(raw_files, 1):
        context = f"packet manifest file {number}"
        if not isinstance(raw, dict) or set(raw) != {"path", "sha256", "size_bytes", "role"}:
            raise ReviewerPacketError(f"{context} fields do not match the schema")
        path = raw["path"]
        role = raw["role"]
        if not isinstance(path, str) or ROLE_BY_PACKET_PATH.get(path) != role:
            raise ReviewerPacketError(f"{context} is not an allowlisted packet path and role")
        size = raw["size_bytes"]
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ReviewerPacketError(f"{context} has an invalid size_bytes")
        files.append(
            PacketEntry(
                path=path,
                sha256=_digest(raw["sha256"], f"{context} sha256"),
                size_bytes=size,
                role=str(role),
            )
        )
    if [entry.path for entry in files] != sorted(ROLE_BY_PACKET_PATH):
        raise ReviewerPacketError(
            "packet manifest files are not the sorted allowlist of roles "
            f"(observed={[entry.path for entry in files]})"
        )
    if len({entry.role for entry in files}) != len(files):
        raise ReviewerPacketError("packet manifest repeats a logical role")

    manifest = PacketManifest(
        reviewer_id=_identifier(payload["reviewer_id"], "packet manifest reviewer_id"),
        created_at=_timestamp(payload["created_at"], "packet manifest created_at"),
        source_commit=commit,
        claim_ground_truth_sha256=_digest(
            payload["claim_ground_truth_sha256"], "packet manifest claim_ground_truth_sha256"
        ),
        corpus_inventory_sha256=_digest(
            payload["corpus_inventory_sha256"], "packet manifest corpus_inventory_sha256"
        ),
        candidate_pair=candidate_pair(payload),
        review_scaffold_sha256=_digest(
            payload["review_scaffold_sha256"], "packet manifest review_scaffold_sha256"
        ),
        clone_root=clone_root,
        repositories=tuple(repositories),
        files=tuple(files),
    )
    if manifest.packet_id != identity:
        raise ReviewerPacketError(
            f"packet_id {identity} does not match the manifest body it is derived from"
        )
    return manifest


def verify_packet(packet: Path, *, clones: Path | None = None, root: Path) -> PacketVerification:
    """Re-check a materialised packet without trusting the manifest's own claims."""
    if packet.is_symlink() or not packet.is_dir():
        raise ReviewerPacketError(f"packet {packet} is not a directory")
    manifest = validate_manifest_payload(
        load_json_object(packet / MANIFEST_NAME, "packet manifest")
    )
    assert_exact_file_set(packet)
    for entry in manifest.files:
        target = packet / entry.path
        observed = sha256_file(target)
        if observed != entry.sha256:
            raise ReviewerPacketError(
                f"packet file {entry.path} hashes to {observed}, manifest records {entry.sha256}"
            )
        if target.stat().st_size != entry.size_bytes:
            raise ReviewerPacketError(f"packet file {entry.path} does not match its recorded size")

    truth_digest = sha256_file(packet / CLAIMS_NAME)
    if truth_digest != manifest.claim_ground_truth_sha256:
        raise ReviewerPacketError("packet claim record does not match the bound truth digest")
    scaffold_digest = sha256_file(packet / SCAFFOLD_NAME)
    if scaffold_digest != manifest.review_scaffold_sha256:
        raise ReviewerPacketError("packet scaffold does not match the bound scaffold digest")

    truth = load_json_object(packet / CLAIMS_NAME, "claim ground truth")
    scaffold = load_json_object(packet / SCAFFOLD_NAME, "review scaffold")
    require_empty_scaffold(scaffold)
    try:
        validate_ground_truth_structure(truth)
        validate_review(scaffold, truth, truth_digest)
    except (ClaimGroundTruthError, ClaimReviewError) as exc:
        raise ReviewerPacketError(
            f"packet review scaffold is not bound to its claim truth: {exc}"
        ) from exc
    if candidate_pair(scaffold) != manifest.candidate_pair:
        raise ReviewerPacketError("packet manifest and scaffold name different candidate pairs")
    if scaffold.get("corpus_inventory_sha256") != manifest.corpus_inventory_sha256:
        raise ReviewerPacketError("packet manifest and scaffold name different corpus inventories")

    for name in (GUIDE_NAME, CHECKLIST_NAME):
        scan_reviewer_document((packet / name).read_bytes(), name)

    bound = {binding.repository_id: binding.expected_commit for binding in manifest.repositories}
    claimed = {str(claim["repo_id"]): str(claim["repo_commit"]) for claim in truth["claims"]}
    if bound != claimed:
        raise ReviewerPacketError(
            "packet manifest does not bind exactly the repositories its claim record names "
            f"(bound={sorted(bound)}, claimed={sorted(claimed)})"
        )

    clone_root = resolve_clone_root(manifest.clone_root, root, clones)
    if clone_root is None:
        return PacketVerification(manifest=manifest, repositories_verified=False)
    for binding in manifest.repositories:
        clone = clone_root / binding.repository_id
        resolved = _git(["rev-parse", "HEAD"], clone) if clone.is_dir() else None
        if resolved is None:
            raise ReviewerPacketError(
                f"repository {binding.repository_id} has no resolvable clone under {clone_root}"
            )
        if resolved.lower() != binding.expected_commit:
            raise ReviewerPacketError(
                f"repository {binding.repository_id} clone is at {resolved.lower()}, "
                f"manifest expects {binding.expected_commit}"
            )
    return PacketVerification(manifest=manifest, repositories_verified=True)


def build_packet(
    *,
    reviewer_id: str,
    claims: Path,
    review: Path,
    repos: Path,
    clones: Path,
    guide: Path,
    checklist: Path,
    workspace_schema: Path,
    review_schema: Path,
    out: Path,
    root: Path,
    clock: Callable[[], datetime],
) -> PacketManifest:
    """Validate every input, then materialise the packet atomically at *out*."""
    _identifier(reviewer_id, "--reviewer-id")
    sources: dict[str, Path] = {
        GUIDE_NAME: guide,
        CHECKLIST_NAME: checklist,
        CLAIMS_NAME: claims,
        REVIEW_SCHEMA_NAME: review_schema,
        WORKSPACE_SCHEMA_NAME: workspace_schema,
        SCAFFOLD_NAME: review,
    }
    labels = dict(COPIED_SOURCES)
    resolved_sources: dict[str, Path] = {}
    for name, source in sources.items():
        label = labels[name]
        reject_withheld_path(source, label)
        require_source_outside_evidence(source, label, root)
        resolved_sources[name] = require_regular_source(source, label, root)
    reject_other_reviewer_scaffold(review, reviewer_id)
    for name in (GUIDE_NAME, CHECKLIST_NAME):
        scan_reviewer_document(resolved_sources[name].read_bytes(), labels[name])

    truth = load_json_object(resolved_sources[CLAIMS_NAME], "claim ground truth")
    scaffold = load_json_object(resolved_sources[SCAFFOLD_NAME], "review scaffold")
    truth_digest = sha256_file(resolved_sources[CLAIMS_NAME])
    pair = candidate_pair(scaffold)
    # Emptiness is checked before schema validity so a scaffold carrying a
    # decision is named as such rather than as a malformed document.
    require_empty_scaffold(scaffold)
    try:
        validate_ground_truth_structure(truth)
        validate_review(scaffold, truth, truth_digest)
    except (ClaimGroundTruthError, ClaimReviewError) as exc:
        raise ReviewerPacketError(
            f"review scaffold is not bound to the claim truth: {exc}"
        ) from exc

    reject_withheld_path(repos, "repos")
    inventory_digest = sha256_file(require_regular_source(repos, "repos", root))
    if scaffold.get("corpus_inventory_sha256") != inventory_digest:
        raise ReviewerPacketError(
            f"review scaffold binds corpus inventory "
            f"{scaffold.get('corpus_inventory_sha256')}, --repos {repos} hashes to "
            f"{inventory_digest}"
        )
    if truth.get("corpus_inventory_sha256") != inventory_digest:
        raise ReviewerPacketError(
            f"claim ground truth binds a different corpus inventory than --repos {repos}"
        )
    bindings = repository_bindings(truth, repos, clones)

    require_safe_output(out, clones, root)
    created_at = clock()
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ReviewerPacketError("packet creation time must be timezone-aware")

    try:
        out.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ReviewerPacketError(f"cannot create packet parent {out.parent}: {exc}") from exc
    staging = Path(tempfile.mkdtemp(prefix=f".{out.name}.", suffix=".partial", dir=out.parent))
    try:
        for name, source in resolved_sources.items():
            (staging / name).write_bytes(source.read_bytes())
        (staging / VERIFY_WRAPPER_NAME).write_bytes(VERIFY_WRAPPER_SOURCE.encode("utf-8"))
        manifest = PacketManifest(
            reviewer_id=reviewer_id,
            created_at=created_at.astimezone(timezone.utc).isoformat(),
            source_commit=source_commit(root),
            claim_ground_truth_sha256=truth_digest,
            corpus_inventory_sha256=inventory_digest,
            candidate_pair=pair,
            review_scaffold_sha256=sha256_file(resolved_sources[SCAFFOLD_NAME]),
            clone_root=clone_root_label(clones, root),
            repositories=bindings,
            files=_packet_entries(staging),
        )
        try:
            write_json(staging / MANIFEST_NAME, manifest.to_payload())
        except RunContractError as exc:
            raise ReviewerPacketError(str(exc)) from exc
        assert_exact_file_set(staging)
        verify_packet(staging, clones=clones, root=root)
        os.replace(staging, out)
    finally:
        # os.replace consumes the staging directory, so this only fires on failure.
        shutil.rmtree(staging, ignore_errors=True)
    return manifest


def _status(verification: PacketVerification) -> str:
    return "checked" if verification.repositories_verified else "unchecked"


def _summary(verification: PacketVerification) -> str:
    manifest = verification.manifest
    return (
        f"files={len(manifest.files)} repositories={len(manifest.repositories)} "
        f"repository_bindings={_status(verification)} "
        f"truth={manifest.claim_ground_truth_sha256[:8]}..."
    )


def _inspect_lines(verification: PacketVerification) -> tuple[str, ...]:
    manifest = verification.manifest
    commit = manifest.source_commit or "unavailable (build tree is not a git repository)"
    return (
        f"packet_id: {manifest.packet_id}",
        f"reviewer_id: {manifest.reviewer_id}",
        f"source_commit: {commit}",
        f"claim_ground_truth_sha256: {manifest.claim_ground_truth_sha256}",
        f"candidate_pair: {', '.join(manifest.candidate_pair)}",
        f"repositories: {len(manifest.repositories)}",
        f"files: {len(manifest.files)}",
        f"repository_bindings: {_status(verification)}",
        "verification: manifest and packet contents agree",
    )


def main(argv: list[str] | None = None, *, clock: Callable[[], datetime] = _utc_now) -> int:
    # The clock is injected rather than exposed as a --frozen-at flag on purpose:
    # a packet timestamp is provenance, and a flag that sets it is a forgery
    # surface with no operational gain. Tests pass a clock through this argument.
    root = repository_root()
    corpus = root / "corpus"
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="build one reviewer's packet")
    build_parser.add_argument("--reviewer-id", required=True)
    build_parser.add_argument("--claims", type=Path, required=True)
    build_parser.add_argument("--review", type=Path, required=True)
    build_parser.add_argument("--out", type=Path, required=True)
    build_parser.add_argument("--repos", type=Path, default=corpus / "repos.csv")
    build_parser.add_argument("--clones", type=Path, default=corpus / "clones" / "pilot-2026-07-13")
    build_parser.add_argument("--guide", type=Path, default=corpus / "review" / GUIDE_NAME)
    build_parser.add_argument("--checklist", type=Path, default=corpus / "review" / CHECKLIST_NAME)
    build_parser.add_argument(
        "--workspace-schema", type=Path, default=corpus / WORKSPACE_SCHEMA_NAME
    )
    build_parser.add_argument("--review-schema", type=Path, default=corpus / REVIEW_SCHEMA_NAME)

    verify_parser = subparsers.add_parser("verify", help="re-check a materialised packet")
    verify_parser.add_argument("--packet", type=Path, required=True)
    verify_parser.add_argument("--clones", type=Path, default=None)

    inspect_parser = subparsers.add_parser("inspect", help="print a packet's bindings")
    inspect_parser.add_argument("--packet", type=Path, required=True)
    inspect_parser.add_argument("--clones", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        if args.command == "build":
            manifest = build_packet(
                reviewer_id=args.reviewer_id,
                claims=args.claims,
                review=args.review,
                repos=args.repos,
                clones=args.clones,
                guide=args.guide,
                checklist=args.checklist,
                workspace_schema=args.workspace_schema,
                review_schema=args.review_schema,
                out=args.out,
                root=root,
                clock=clock,
            )
            print(
                f"review packet built: {args.out} files={len(manifest.files)} "
                f"repositories={len(manifest.repositories)} "
                f"truth={manifest.claim_ground_truth_sha256[:8]}..."
            )
            return 0
        verification = verify_packet(args.packet, clones=args.clones, root=root)
        if args.command == "verify":
            print(f"review packet verified: {_summary(verification)}")
            return 0
        for line in _inspect_lines(verification):
            print(line)
        return 0
    except (ReviewerPacketError, RunContractError, OSError) as exc:
        written = "; no packet was written" if args.command == "build" else ""
        print(f"reviewer packet {args.command} failed: {exc}{written}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
