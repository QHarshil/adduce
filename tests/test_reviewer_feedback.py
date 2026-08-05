"""Behaviour tests for the reviewer process-feedback recorder."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from corpus.scripts import claim_review_entry, reviewer_feedback
from corpus.scripts.run_contract import sha256_file
from jsonschema import Draft202012Validator

from tests import review_fixtures as rf

Clock = Callable[[], datetime]
ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "corpus" / "reviewer-feedback.schema.json").read_text(encoding="utf-8"))
STARTED_KEYS = {
    "reviewer_feedback_schema_version",
    "reviewer_id",
    "review_artifact_sha256",
    "started_at",
    "minutes_by_claim",
}
SUBMITTED_KEYS = STARTED_KEYS | {
    "completed_at",
    "validator_failure_count",
    "clarification_request_count",
    "ratings",
    "most_confusing_instruction",
    "missing_tool_or_material",
    "submitted_at",
}
RATING_ARGUMENTS = [
    "--rating",
    "decision_vocabulary_clear=4",
    "--rating",
    "evidence_was_locatable=3",
    "--rating",
    "tool_prevented_invalid_states=5",
    "--rating",
    "felt_pressure_to_verify=1",
]


def completed_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, claim_count: int = 2) -> Path:
    """Export one completed single-reviewer claim review through the reviewer entry tool."""
    monkeypatch.chdir(tmp_path)
    truth = rf.synthetic_truth(claim_count)
    truth_path = rf.write_truth(tmp_path, truth)
    scaffold_path = rf.write_scaffold(tmp_path, truth_path, truth)
    clock = rf.advancing_clock()
    workspace = rf.init_workspace(tmp_path, scaffold_path, truth_path, clock)
    rf.complete_workspace(workspace, truth, truth_path, clock)
    review = tmp_path / "final-claim-review.json"
    claim_review_entry.main(
        [
            "finalize-review",
            "--workspace",
            str(workspace),
            "--claims",
            str(truth_path),
            "--out",
            str(review),
        ],
        clock=clock,
    )
    return review


def start_feedback(tmp_path: Path, review: Path, clock: Clock, name: str = "feedback.json") -> Path:
    feedback = tmp_path / name
    reviewer_feedback.main(
        [
            "init",
            "--reviewer-id",
            rf.REVIEWER_ID,
            "--review",
            str(review),
            "--out",
            str(feedback),
        ],
        clock=clock,
    )
    return feedback


def submit(feedback: Path, clock: Clock, *extra: str) -> int:
    return reviewer_feedback.main(
        [
            "submit",
            "--feedback",
            str(feedback),
            *(extra or RATING_ARGUMENTS),
            "--validator-failures",
            "2",
            "--clarification-requests",
            "1",
            "--most-confusing-instruction",
            "the difference between unclear and revision_required",
            "--missing-tool-or-material",
            "",
        ],
        clock=clock,
    )


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_init_binds_the_review_and_records_no_rating_and_no_free_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = completed_review(tmp_path, monkeypatch)

    clock = rf.advancing_clock()
    feedback = start_feedback(tmp_path, review, clock)

    payload = load(feedback)
    assert set(payload) == STARTED_KEYS
    assert payload["review_artifact_sha256"] == sha256_file(review)
    assert payload["reviewer_id"] == rf.REVIEWER_ID
    assert payload["minutes_by_claim"] == {}


def test_init_refuses_an_existing_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    review = completed_review(tmp_path, monkeypatch)
    clock = rf.advancing_clock()
    feedback = start_feedback(tmp_path, review, clock)

    with pytest.raises(SystemExit) as error:
        reviewer_feedback.main(
            [
                "init",
                "--reviewer-id",
                rf.REVIEWER_ID,
                "--review",
                str(review),
                "--out",
                str(feedback),
            ],
            clock=rf.advancing_clock(),
        )

    assert "refusing to overwrite" in str(error.value)


def test_record_time_replaces_a_previous_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = completed_review(tmp_path, monkeypatch)
    clock = rf.advancing_clock()
    feedback = start_feedback(tmp_path, review, clock)
    claim_id = rf.synthetic_claim_id(0)

    for minutes in ("42", "37.5"):
        reviewer_feedback.main(
            [
                "record-time",
                "--feedback",
                str(feedback),
                "--review",
                str(review),
                "--claim-id",
                claim_id,
                "--minutes",
                minutes,
            ],
            clock=rf.advancing_clock(),
        )

    assert load(feedback)["minutes_by_claim"] == {claim_id: 37.5}


def test_record_time_rejects_negative_minutes_and_an_unknown_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = completed_review(tmp_path, monkeypatch)
    clock = rf.advancing_clock()
    feedback = start_feedback(tmp_path, review, clock)
    before = feedback.read_bytes()

    with pytest.raises(SystemExit) as negative:
        reviewer_feedback.main(
            [
                "record-time",
                "--feedback",
                str(feedback),
                "--review",
                str(review),
                "--claim-id",
                rf.synthetic_claim_id(0),
                "--minutes",
                "-1",
            ],
            clock=rf.advancing_clock(),
        )
    with pytest.raises(SystemExit) as unknown:
        reviewer_feedback.main(
            [
                "record-time",
                "--feedback",
                str(feedback),
                "--review",
                str(review),
                "--claim-id",
                "synthetic-repo-nine.c1",
                "--minutes",
                "10",
            ],
            clock=rf.advancing_clock(),
        )

    assert "non-negative" in str(negative.value)
    assert "is not one of the 2 claims in the bound review" in str(unknown.value)
    assert feedback.read_bytes() == before


def test_record_time_requires_the_bound_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = completed_review(tmp_path, monkeypatch)
    clock = rf.advancing_clock()
    feedback = start_feedback(tmp_path, review, clock)
    other = tmp_path / "other-review.json"
    other.write_bytes(review.read_bytes().replace(b"fixture rationale", b"fixture  rationale"))

    with pytest.raises(SystemExit) as error:
        reviewer_feedback.main(
            [
                "record-time",
                "--feedback",
                str(feedback),
                "--review",
                str(other),
                "--claim-id",
                rf.synthetic_claim_id(0),
                "--minutes",
                "10",
            ],
            clock=rf.advancing_clock(),
        )

    assert "is bound to" in str(error.value)


def test_submit_requires_every_rating_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = completed_review(tmp_path, monkeypatch)
    clock = rf.advancing_clock()
    feedback = start_feedback(tmp_path, review, clock)

    with pytest.raises(SystemExit) as error:
        submit(
            feedback,
            clock,
            "--rating",
            "decision_vocabulary_clear=4",
            "--rating",
            "evidence_was_locatable=3",
            "--rating",
            "tool_prevented_invalid_states=5",
        )

    assert "missing ['felt_pressure_to_verify']" in str(error.value)
    assert "No rating has a default value" in str(error.value)
    assert set(load(feedback)) == STARTED_KEYS


@pytest.mark.parametrize("value", ["0", "6", "-1"])
def test_submit_rejects_a_rating_outside_one_to_five(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    review = completed_review(tmp_path, monkeypatch)
    clock = rf.advancing_clock()
    feedback = start_feedback(tmp_path, review, clock)

    with pytest.raises(SystemExit) as error:
        submit(
            feedback,
            clock,
            "--rating",
            f"decision_vocabulary_clear={value}",
            "--rating",
            "evidence_was_locatable=3",
            "--rating",
            "tool_prevented_invalid_states=5",
            "--rating",
            "felt_pressure_to_verify=1",
        )

    assert "must be a whole number from 1 to 5" in str(error.value)
    assert set(load(feedback)) == STARTED_KEYS


def test_submit_rejects_an_unknown_rating_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = completed_review(tmp_path, monkeypatch)
    clock = rf.advancing_clock()
    feedback = start_feedback(tmp_path, review, clock)

    with pytest.raises(SystemExit) as error:
        submit(
            feedback,
            clock,
            "--rating",
            "tool_was_pleasant=5",
            "--rating",
            "evidence_was_locatable=3",
            "--rating",
            "tool_prevented_invalid_states=5",
            "--rating",
            "felt_pressure_to_verify=1",
        )

    assert "'tool_was_pleasant' is not one of" in str(error.value)


def test_submit_preserves_empty_free_text_and_orders_its_timestamps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = completed_review(tmp_path, monkeypatch)
    clock = rf.advancing_clock()
    feedback = start_feedback(tmp_path, review, clock)

    assert submit(feedback, clock) == 0

    payload = load(feedback)
    assert set(payload) == SUBMITTED_KEYS
    assert payload["missing_tool_or_material"] == ""
    assert payload["most_confusing_instruction"].startswith("the difference between")
    assert payload["ratings"] == {
        "decision_vocabulary_clear": 4,
        "evidence_was_locatable": 3,
        "tool_prevented_invalid_states": 5,
        "felt_pressure_to_verify": 1,
    }
    assert payload["validator_failure_count"] == 2
    assert payload["clarification_request_count"] == 1
    assert payload["started_at"] <= payload["completed_at"] <= payload["submitted_at"]


def test_a_submitted_document_is_immutable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    review = completed_review(tmp_path, monkeypatch)
    clock = rf.advancing_clock()
    feedback = start_feedback(tmp_path, review, clock)
    submit(feedback, clock)
    before = feedback.read_bytes()

    with pytest.raises(SystemExit) as time_error:
        reviewer_feedback.main(
            [
                "record-time",
                "--feedback",
                str(feedback),
                "--review",
                str(review),
                "--claim-id",
                rf.synthetic_claim_id(0),
                "--minutes",
                "10",
            ],
            clock=rf.advancing_clock(),
        )
    with pytest.raises(SystemExit) as submit_error:
        submit(feedback, clock)

    assert "is immutable" in str(time_error.value)
    assert "is immutable" in str(submit_error.value)
    assert feedback.read_bytes() == before


def test_validate_accepts_a_submitted_document_and_enforces_timestamp_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    review = completed_review(tmp_path, monkeypatch)
    clock = rf.advancing_clock()
    feedback = start_feedback(tmp_path, review, clock)
    reviewer_feedback.main(
        [
            "record-time",
            "--feedback",
            str(feedback),
            "--review",
            str(review),
            "--claim-id",
            rf.synthetic_claim_id(0),
            "--minutes",
            "12",
        ],
        clock=rf.advancing_clock(),
    )
    submit(feedback, clock)

    assert reviewer_feedback.main(["validate", "--feedback", str(feedback)]) == 0
    assert "valid reviewer feedback" in capsys.readouterr().out

    payload = load(feedback)
    backdated = tmp_path / "backdated.json"
    backdated.write_text(
        json.dumps({**payload, "completed_at": "2020-01-01T00:00:00Z"}), encoding="utf-8"
    )
    out_of_order = tmp_path / "out-of-order.json"
    out_of_order.write_text(
        json.dumps({**payload, "submitted_at": payload["started_at"]}), encoding="utf-8"
    )

    with pytest.raises(SystemExit) as backdated_error:
        reviewer_feedback.main(["validate", "--feedback", str(backdated)])
    with pytest.raises(SystemExit) as order_error:
        reviewer_feedback.main(["validate", "--feedback", str(out_of_order)])

    assert "completed_at precedes started_at" in str(backdated_error.value)
    assert "submitted_at precedes completed_at" in str(order_error.value)


def test_validate_rejects_a_partial_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = completed_review(tmp_path, monkeypatch)
    clock = rf.advancing_clock()
    feedback = start_feedback(tmp_path, review, clock)
    submit(feedback, clock)
    payload = load(feedback)
    payload.pop("ratings")
    partial = tmp_path / "partial.json"
    partial.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SystemExit) as error:
        reviewer_feedback.main(["validate", "--feedback", str(partial)])

    assert "carries part of a submission" in str(error.value)


def test_the_schema_accepts_both_states_and_rejects_an_extra_property(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = completed_review(tmp_path, monkeypatch)
    clock = rf.advancing_clock()
    feedback = start_feedback(tmp_path, review, clock)
    started = load(feedback)
    submit(feedback, clock)
    submitted = load(feedback)
    validator = Draft202012Validator(SCHEMA)

    Draft202012Validator.check_schema(SCHEMA)
    validator.validate(started)
    validator.validate(submitted)

    for invalid in (
        {**submitted, "reviewer_name": "not permitted"},
        {**submitted, "ratings": {**submitted["ratings"], "decision_vocabulary_clear": 6}},
        {**submitted, "minutes_by_claim": {rf.synthetic_claim_id(0): -1}},
    ):
        assert not validator.is_valid(invalid)
    assert not validator.is_valid({key: submitted[key] for key in SUBMITTED_KEYS - {"ratings"}})


def test_no_command_writes_to_the_review_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = completed_review(tmp_path, monkeypatch)
    before = review.read_bytes()

    clock = rf.advancing_clock()
    feedback = start_feedback(tmp_path, review, clock)
    assert review.read_bytes() == before

    reviewer_feedback.main(
        [
            "record-time",
            "--feedback",
            str(feedback),
            "--review",
            str(review),
            "--claim-id",
            rf.synthetic_claim_id(1),
            "--minutes",
            "8",
        ],
        clock=rf.advancing_clock(),
    )
    assert review.read_bytes() == before

    submit(feedback, clock)
    assert review.read_bytes() == before

    reviewer_feedback.main(["validate", "--feedback", str(feedback)])
    assert review.read_bytes() == before


def test_review_claim_identifiers_reads_no_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = completed_review(tmp_path, monkeypatch)

    identifiers = reviewer_feedback.review_claim_identifiers(review)

    assert identifiers == (rf.synthetic_claim_id(0), rf.synthetic_claim_id(1))
    stripped = load(review)
    for record in stripped["claims"]:
        record["reviews"] = []
    without_decisions = tmp_path / "no-decisions.json"
    without_decisions.write_text(json.dumps(stripped), encoding="utf-8")
    assert reviewer_feedback.review_claim_identifiers(without_decisions) == identifiers
