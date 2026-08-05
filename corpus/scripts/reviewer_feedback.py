#!/usr/bin/env python3
"""Record one reviewer's report of review-process burden, separately from any decision."""

from __future__ import annotations

import argparse
import math
import re
import sys
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

if __package__:
    from .claim_review_entry import ReviewerWorkspaceError, atomic_write_json
    from .run_contract import (
        RunContractError,
        ensure_output_outside,
        load_json_object_bytes,
        sha256_file,
    )
else:
    from claim_review_entry import ReviewerWorkspaceError, atomic_write_json
    from run_contract import (
        RunContractError,
        ensure_output_outside,
        load_json_object_bytes,
        sha256_file,
    )

REVIEWER_FEEDBACK_SCHEMA_VERSION = 1
RATING_NAMES = (
    "decision_vocabulary_clear",
    "evidence_was_locatable",
    "tool_prevented_invalid_states",
    "felt_pressure_to_verify",
)
RATING_MINIMUM = 1
RATING_MAXIMUM = 5
STARTED_FIELDS = (
    "reviewer_feedback_schema_version",
    "reviewer_id",
    "review_artifact_sha256",
    "started_at",
    "minutes_by_claim",
)
SUBMISSION_FIELDS = (
    "completed_at",
    "validator_failure_count",
    "clarification_request_count",
    "ratings",
    "most_confusing_instruction",
    "missing_tool_or_material",
    "submitted_at",
)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class ReviewerFeedbackError(ValueError):
    """A reviewer feedback artifact is malformed, unbound, or already submitted."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _render_timestamp(moment: datetime) -> str:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ReviewerFeedbackError("the feedback clock must return a timezone-aware time")
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_timestamp(value: object, context: str) -> datetime:
    if not isinstance(value, str) or not _TIMESTAMP_RE.fullmatch(value):
        raise ReviewerFeedbackError(
            f"{context} must be an RFC3339 UTC timestamp of the form 2026-07-13T22:00:00Z, "
            f"found {value!r}"
        )
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _load_object(path: Path, context: str) -> dict[str, Any]:
    try:
        payload = load_json_object_bytes(path.read_bytes(), f"{context} {path}")
    except (OSError, RunContractError) as exc:
        raise ReviewerFeedbackError(f"cannot read {context} {path}: {exc}") from exc
    return cast(dict[str, Any], payload)


def review_claim_identifiers(path: Path) -> tuple[str, ...]:
    """Return the claim identifiers of a claim-review file without reading any decision."""
    # This function is the whole of this tool's contact with a review artifact. It reads the
    # claim identifiers and nothing else: a feedback tool that could see a decision could also
    # leak one into a burden report that the coordinator reads while the review gate is open.
    payload = _load_object(path, "claim review")
    if payload.get("claim_review_schema_version") != 1:
        raise ReviewerFeedbackError(f"{path} is not a supported claim-review artifact")
    records = payload.get("claims")
    if not isinstance(records, list) or not records:
        raise ReviewerFeedbackError(f"{path} contains no claim records")
    identifiers: list[str] = []
    for number, record in enumerate(records, 1):
        claim_id = record.get("claim_id") if isinstance(record, dict) else None
        if not isinstance(claim_id, str) or not _ID_RE.fullmatch(claim_id):
            raise ReviewerFeedbackError(f"{path} claim {number} has an invalid claim_id")
        if claim_id in identifiers:
            raise ReviewerFeedbackError(f"{path} repeats claim {claim_id!r}")
        identifiers.append(claim_id)
    return tuple(identifiers)


def _minutes(value: object, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReviewerFeedbackError(f"{context} must be a number of minutes")
    if not math.isfinite(value) or value < 0:
        raise ReviewerFeedbackError(f"{context} must be a finite non-negative number of minutes")
    return float(value)


def _count(value: object, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReviewerFeedbackError(f"{context} must be a non-negative whole number")
    return value


def _rating(value: object, context: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not RATING_MINIMUM <= value <= RATING_MAXIMUM
    ):
        raise ReviewerFeedbackError(
            f"{context} must be a whole number from {RATING_MINIMUM} to {RATING_MAXIMUM}"
        )
    return value


def _free_text(value: object, context: str) -> str:
    if not isinstance(value, str):
        raise ReviewerFeedbackError(f"{context} must be a string, which may be empty")
    return value


def validate_feedback(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate one feedback document in either its started or its submitted state."""
    observed = set(payload)
    missing = set(STARTED_FIELDS) - observed
    extra = observed - set(STARTED_FIELDS) - set(SUBMISSION_FIELDS)
    if missing or extra:
        raise ReviewerFeedbackError(
            f"reviewer feedback fields do not match the schema "
            f"(missing={sorted(missing)}, extra={sorted(extra)})"
        )
    submitted_present = observed & set(SUBMISSION_FIELDS)
    if submitted_present and submitted_present != set(SUBMISSION_FIELDS):
        raise ReviewerFeedbackError(
            "reviewer feedback carries part of a submission: "
            f"missing={sorted(set(SUBMISSION_FIELDS) - submitted_present)}"
        )
    if payload.get("reviewer_feedback_schema_version") != REVIEWER_FEEDBACK_SCHEMA_VERSION:
        raise ReviewerFeedbackError("unsupported reviewer-feedback schema")
    reviewer_id = payload.get("reviewer_id")
    if not isinstance(reviewer_id, str) or not _ID_RE.fullmatch(reviewer_id):
        raise ReviewerFeedbackError("reviewer feedback requires a stable non-personal reviewer_id")
    digest = payload.get("review_artifact_sha256")
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        raise ReviewerFeedbackError("reviewer feedback must bind one review artifact by SHA-256")
    started_at = _parse_timestamp(payload.get("started_at"), "started_at")
    minutes = payload.get("minutes_by_claim")
    if not isinstance(minutes, dict):
        raise ReviewerFeedbackError("minutes_by_claim must be an object keyed by claim identifier")
    for claim_id, value in minutes.items():
        if not _ID_RE.fullmatch(claim_id):
            raise ReviewerFeedbackError(f"minutes_by_claim key {claim_id!r} is not a claim id")
        _minutes(value, f"minutes_by_claim {claim_id}")

    summary = {
        "reviewer_id": reviewer_id,
        "review_artifact_sha256": digest,
        "timed_claims": len(minutes),
        "submitted": bool(submitted_present),
    }
    if not submitted_present:
        return summary

    completed_at = _parse_timestamp(payload.get("completed_at"), "completed_at")
    submitted_at = _parse_timestamp(payload.get("submitted_at"), "submitted_at")
    if completed_at < started_at:
        raise ReviewerFeedbackError("completed_at precedes started_at")
    if submitted_at < completed_at:
        raise ReviewerFeedbackError("submitted_at precedes completed_at")
    _count(payload.get("validator_failure_count"), "validator_failure_count")
    _count(payload.get("clarification_request_count"), "clarification_request_count")
    ratings = payload.get("ratings")
    if not isinstance(ratings, dict) or set(ratings) != set(RATING_NAMES):
        raise ReviewerFeedbackError(
            f"ratings must contain exactly {sorted(RATING_NAMES)}, "
            f"found {sorted(ratings) if isinstance(ratings, dict) else type(ratings).__name__}"
        )
    for name in RATING_NAMES:
        _rating(ratings[name], f"rating {name}")
    for name in ("most_confusing_instruction", "missing_tool_or_material"):
        _free_text(payload.get(name), name)
    return summary


def _read_feedback(path: Path) -> dict[str, Any]:
    payload = _load_object(path, "reviewer feedback")
    validate_feedback(payload)
    return payload


def _require_bound_review(payload: dict[str, Any], review: Path) -> tuple[str, ...]:
    observed = sha256_file(review)
    if observed != payload["review_artifact_sha256"]:
        raise ReviewerFeedbackError(
            f"--review {review} hashes to {observed} but this feedback is bound to "
            f"{payload['review_artifact_sha256']}; nothing was written"
        )
    return review_claim_identifiers(review)


def _require_unsubmitted(payload: dict[str, Any], path: Path) -> None:
    if "submitted_at" in payload:
        raise ReviewerFeedbackError(
            f"reviewer feedback {path} was submitted at {payload['submitted_at']} and is "
            "immutable; record a correction as a newly created feedback file. Nothing was written"
        )


def _write(path: Path, payload: dict[str, Any]) -> None:
    validate_feedback(payload)
    try:
        atomic_write_json(path, payload)
    except ReviewerWorkspaceError as exc:
        raise ReviewerFeedbackError(str(exc)) from exc


def _command_init(args: argparse.Namespace, clock: Callable[[], datetime]) -> int:
    out = cast(Path, args.out)
    review = cast(Path, args.review)
    if out.exists() or out.is_symlink():
        raise ReviewerFeedbackError(
            f"refusing to overwrite existing reviewer feedback {out}; nothing was written"
        )
    ensure_output_outside(out, [review])
    reviewer_id = str(args.reviewer_id)
    if not _ID_RE.fullmatch(reviewer_id):
        raise ReviewerFeedbackError(
            f"--reviewer-id {reviewer_id!r} must be a stable non-personal identifier"
        )
    claim_ids = review_claim_identifiers(review)
    payload: dict[str, Any] = {
        "reviewer_feedback_schema_version": REVIEWER_FEEDBACK_SCHEMA_VERSION,
        "reviewer_id": reviewer_id,
        "review_artifact_sha256": sha256_file(review),
        "started_at": _render_timestamp(clock()),
        "minutes_by_claim": {},
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    _write(out, payload)
    print(
        f"started reviewer feedback {out} for {len(claim_ids)} claim(s) in {review}; "
        "no rating, no free text and no time recorded"
    )
    return 0


def _command_record_time(args: argparse.Namespace, clock: Callable[[], datetime]) -> int:
    path = cast(Path, args.feedback)
    payload = _read_feedback(path)
    _require_unsubmitted(payload, path)
    claim_ids = _require_bound_review(payload, cast(Path, args.review))
    claim_id = str(args.claim_id)
    if claim_id not in claim_ids:
        raise ReviewerFeedbackError(
            f"--claim-id {claim_id!r} is not one of the {len(claim_ids)} claims in the bound "
            "review; nothing was written"
        )
    minutes = _minutes(args.minutes, "--minutes")
    recorded: int | float = int(minutes) if minutes.is_integer() else minutes
    replaced = claim_id in payload["minutes_by_claim"]
    payload["minutes_by_claim"][claim_id] = recorded
    _write(path, payload)
    print(
        f"{'replaced' if replaced else 'recorded'} {recorded} minute(s) for {claim_id} in {path}: "
        f"timed claims {len(payload['minutes_by_claim'])}/{len(claim_ids)}"
    )
    return 0


def parse_ratings(values: Sequence[str]) -> dict[str, int]:
    """Parse ``--rating name=value`` pairs, requiring every rating exactly once."""
    ratings: dict[str, int] = {}
    for raw in values:
        name, separator, text = raw.partition("=")
        if not separator:
            raise ReviewerFeedbackError(f"--rating {raw!r} must be given as name=value")
        if name not in RATING_NAMES:
            raise ReviewerFeedbackError(f"--rating {name!r} is not one of {sorted(RATING_NAMES)}")
        if name in ratings:
            raise ReviewerFeedbackError(f"--rating {name} was given more than once")
        try:
            value = int(text)
        except ValueError as exc:
            raise ReviewerFeedbackError(
                f"--rating {name} must be a whole number from {RATING_MINIMUM} to "
                f"{RATING_MAXIMUM}, found {text!r}"
            ) from exc
        ratings[name] = _rating(value, f"--rating {name}")
    missing = set(RATING_NAMES) - set(ratings)
    if missing:
        raise ReviewerFeedbackError(
            f"submit requires every rating supplied explicitly; missing {sorted(missing)}. "
            "No rating has a default value"
        )
    return ratings


def _command_submit(args: argparse.Namespace, clock: Callable[[], datetime]) -> int:
    path = cast(Path, args.feedback)
    payload = _read_feedback(path)
    _require_unsubmitted(payload, path)
    ratings = parse_ratings(args.rating)
    completed_at = _render_timestamp(clock())
    submitted_at = _render_timestamp(clock())
    payload.update(
        {
            "completed_at": completed_at,
            "validator_failure_count": _count(args.validator_failures, "--validator-failures"),
            "clarification_request_count": _count(
                args.clarification_requests, "--clarification-requests"
            ),
            "ratings": ratings,
            "most_confusing_instruction": _free_text(
                args.most_confusing_instruction, "--most-confusing-instruction"
            ),
            "missing_tool_or_material": _free_text(
                args.missing_tool_or_material, "--missing-tool-or-material"
            ),
            "submitted_at": submitted_at,
        }
    )
    _write(path, payload)
    print(
        f"submitted reviewer feedback {path} at {submitted_at}: "
        f"timed claims {len(payload['minutes_by_claim'])} "
        f"validator failures {payload['validator_failure_count']} "
        f"clarification requests {payload['clarification_request_count']}"
    )
    return 0


def _command_validate(args: argparse.Namespace, clock: Callable[[], datetime]) -> int:
    path = cast(Path, args.feedback)
    summary = validate_feedback(_load_object(path, "reviewer feedback"))
    print(
        f"valid reviewer feedback {path}: reviewer={summary['reviewer_id']} "
        f"review_artifact_sha256={summary['review_artifact_sha256']} "
        f"timed_claims={summary['timed_claims']} "
        f"submitted={'yes' if summary['submitted'] else 'no'}"
    )
    return 0


_COMMANDS: dict[str, Callable[[argparse.Namespace, Callable[[], datetime]], int]] = {
    "init": _command_init,
    "record-time": _command_record_time,
    "submit": _command_submit,
    "validate": _command_validate,
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="start a feedback document for one review")
    init_parser.add_argument("--reviewer-id", required=True)
    init_parser.add_argument("--review", type=Path, required=True)
    init_parser.add_argument("--out", type=Path, required=True)

    time_parser = subparsers.add_parser("record-time", help="record minutes spent on one claim")
    time_parser.add_argument("--feedback", type=Path, required=True)
    time_parser.add_argument("--review", type=Path, required=True)
    time_parser.add_argument("--claim-id", required=True)
    time_parser.add_argument("--minutes", type=float, required=True)

    submit_parser = subparsers.add_parser("submit", help="submit the completed feedback")
    submit_parser.add_argument("--feedback", type=Path, required=True)
    submit_parser.add_argument(
        "--rating",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help=(
            f"one of {', '.join(RATING_NAMES)} scored {RATING_MINIMUM}-{RATING_MAXIMUM}; "
            "all four are required and none has a default"
        ),
    )
    submit_parser.add_argument("--validator-failures", type=int, required=True)
    submit_parser.add_argument("--clarification-requests", type=int, required=True)
    submit_parser.add_argument(
        "--most-confusing-instruction",
        required=True,
        help="free text; pass an empty string to decline to answer",
    )
    submit_parser.add_argument(
        "--missing-tool-or-material",
        required=True,
        help="free text; pass an empty string to decline to answer",
    )

    validate_parser = subparsers.add_parser("validate", help="validate a feedback document")
    validate_parser.add_argument("--feedback", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None, *, clock: Callable[[], datetime] = _utc_now) -> int:
    # The clock is injected for tests only and is deliberately not exposed as a flag: a
    # submission time a reviewer can choose is not evidence of when the work was done.
    args = _build_parser().parse_args(argv)
    try:
        return _COMMANDS[args.command](args, clock)
    except (ReviewerFeedbackError, ReviewerWorkspaceError, RunContractError, OSError) as exc:
        sys.exit(f"invalid reviewer feedback: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
