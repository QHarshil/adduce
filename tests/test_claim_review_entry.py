"""Reviewer workspaces capture explicitly entered human decisions and nothing else."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from corpus.scripts import claim_review_entry
from corpus.scripts.claim_ground_truth import TARGETS
from corpus.scripts.claim_review import DECISIONS, validate_review
from corpus.scripts.claim_review_entry import main
from corpus.scripts.run_contract import sha256_file, write_json
from jsonschema import Draft202012Validator

from tests import review_fixtures

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_SCHEMA = ROOT / "corpus" / "reviewer-workspace.schema.json"
CLAIM_REVIEW_SCHEMA = ROOT / "corpus" / "claim-review.schema.json"
STATUS_JSON_KEYS = frozenset(
    {
        "reviewer_workspace_schema_version",
        "reviewer_id",
        "claims",
        "completed_claims",
        "finalized_claims",
        "declarations_recorded",
        "declarations_required",
        "decisions_recorded",
        "decisions_required",
        "final_export_ready",
        "incomplete_claims",
    }
)
FIRST_CLAIM = review_fixtures.synthetic_claim_id(0)
SECOND_CLAIM = review_fixtures.synthetic_claim_id(1)

_MISSING_EVERYTHING = (
    "missing declarations (record them with `declare`); "
    "missing claim decision (record it with `record-claim`); "
    "missing link decisions for " + ", ".join(TARGETS)
    + " (record each with `record-link`)"
)


class Bench(NamedTuple):
    root: Path
    truth: dict[str, Any]
    truth_path: Path
    scaffold_path: Path
    workspace: Path
    clock: Any


@pytest.fixture
def bench(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Bench:
    monkeypatch.chdir(tmp_path)
    truth = review_fixtures.synthetic_truth(2)
    truth_path = review_fixtures.write_truth(tmp_path, truth)
    scaffold_path = review_fixtures.write_scaffold(tmp_path, truth_path, truth)
    clock = review_fixtures.advancing_clock()
    workspace = review_fixtures.init_workspace(tmp_path, scaffold_path, truth_path, clock)
    return Bench(tmp_path, truth, truth_path, scaffold_path, workspace, clock)


def _document(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _status(workspace: Path) -> dict[str, Any]:
    return claim_review_entry.status_report(claim_review_entry.read_workspace(workspace))


def _record_link_argv(claim_id: str, target: str, **overrides: Any) -> list[str]:
    return [
        "record-link",
        "--claim-id",
        claim_id,
        "--target",
        overrides.get("target", target),
        "--decision",
        overrides.get("decision", "verified"),
        "--rationale",
        overrides.get("rationale", review_fixtures.fixture_rationale(claim_id, target)),
    ]


def test_reviewer_workspace_schema_is_valid_and_accepts_a_workspace(bench: Bench) -> None:
    schema = json.loads(WORKSPACE_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(_document(bench.workspace))

    review_fixtures.fill_claim(bench.workspace, FIRST_CLAIM, bench.clock)
    review_fixtures.finalize_claim(bench.workspace, bench.truth_path, FIRST_CLAIM, bench.clock)
    Draft202012Validator(schema).validate(_document(bench.workspace))


def test_workspace_does_not_satisfy_the_claim_review_schema(bench: Bench) -> None:
    schema = json.loads(CLAIM_REVIEW_SCHEMA.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    assert not validator.is_valid(_document(bench.workspace))
    errors = {error.validator for error in validator.iter_errors(_document(bench.workspace))}
    assert "additionalProperties" in errors


def test_init_records_no_decisions_and_no_declarations(
    bench: Bench, capsys: pytest.CaptureFixture[str]
) -> None:
    document = _document(bench.workspace)
    assert document["not_final"] is True
    assert document["workspace_kind"] == "adduce-claim-review-workspace"
    assert [claim["claim_id"] for claim in document["claims"]] == [FIRST_CLAIM, SECOND_CLAIM]
    for claim in document["claims"]:
        assert claim["declarations"] is None
        assert claim["claim_decision"] is None
        assert claim["link_decisions"] == []
        assert claim["finalized_at"] is None
        assert claim["notes"] == ""

    raw = bench.workspace.read_bytes()
    for decision in DECISIONS:
        assert decision.encode() not in raw
    for probe in (b"rationale", b"evidence", b"declared_at", b"recorded_at"):
        assert probe not in raw

    main(["status", "--workspace", str(bench.workspace)])
    assert capsys.readouterr().out.splitlines() == [
        "workspace valid: claims=2 completed=0 decisions=0/22 declarations=0/2",
        f"cannot finalize {FIRST_CLAIM}: {_MISSING_EVERYTHING}",
        f"cannot finalize {SECOND_CLAIM}: {_MISSING_EVERYTHING}",
    ]


def test_init_refuses_an_existing_output(bench: Bench) -> None:
    with pytest.raises(SystemExit) as error:
        review_fixtures.init_workspace(
            bench.root, bench.scaffold_path, bench.truth_path, bench.clock
        )
    assert "refusing to overwrite existing reviewer workspace" in str(error.value)


def test_init_refuses_a_scaffold_that_fails_validate_review(bench: Bench) -> None:
    unbound = _document(bench.scaffold_path)
    unbound["claim_ground_truth_sha256"] = "0" * 64
    unbound_path = bench.root / "unbound-scaffold.json"
    write_json(unbound_path, unbound)

    with pytest.raises(SystemExit) as error:
        main(
            [
                "init",
                "--scaffold",
                str(unbound_path),
                "--claims",
                str(bench.truth_path),
                "--reviewer-id",
                "reviewer-test-b",
                "--domain-expertise",
                review_fixtures.DOMAIN_EXPERTISE,
                "--workspace",
                str(bench.root / "second.review-workspace.json"),
            ],
            clock=bench.clock,
        )
    assert "targets a different candidate truth" in str(error.value)
    assert not (bench.root / "second.review-workspace.json").exists()


def test_recording_before_declarations_is_refused(bench: Bench) -> None:
    before = bench.workspace.read_bytes()
    with pytest.raises(SystemExit) as error:
        review_fixtures.record_claim_decision(bench.workspace, FIRST_CLAIM, bench.clock)
    assert "before its declarations" in str(error.value)

    with pytest.raises(SystemExit) as link_error:
        review_fixtures.record_link_decision(bench.workspace, FIRST_CLAIM, "code", bench.clock)
    assert "before its declarations" in str(link_error.value)
    assert bench.workspace.read_bytes() == before


@pytest.mark.parametrize("omitted", list(review_fixtures.AFFIRMATION_FLAGS))
def test_incomplete_declaration_is_refused_and_demands_reassignment(
    bench: Bench, omitted: str
) -> None:
    flags = [flag for flag in review_fixtures.AFFIRMATION_FLAGS if flag != omitted]
    with pytest.raises(SystemExit) as error:
        main(
            ["declare", "--workspace", str(bench.workspace), "--claim-id", FIRST_CLAIM, *flags],
            clock=bench.clock,
        )
    message = str(error.value)
    assert omitted in message
    assert "must be reassigned to a different reviewer" in message
    assert "Nothing was written" in message
    assert _document(bench.workspace)["claims"][0]["declarations"] is None


def test_declare_refuses_an_unknown_claim_and_a_second_declaration(bench: Bench) -> None:
    with pytest.raises(SystemExit) as unknown:
        review_fixtures.declare_claim(bench.workspace, "synthetic-repo-nine.c1", bench.clock)
    assert "is not in this workspace" in str(unknown.value)

    review_fixtures.declare_claim(bench.workspace, FIRST_CLAIM, bench.clock)
    with pytest.raises(SystemExit) as repeated:
        review_fixtures.declare_claim(bench.workspace, FIRST_CLAIM, bench.clock)
    assert "already carries declarations" in str(repeated.value)

    declarations = _document(bench.workspace)["claims"][0]["declarations"]
    assert declarations["blinding_declaration"] == {
        "independent_review": True,
        "other_reviewer_decisions_not_seen": True,
        "adduce_claim_link_outputs_not_seen": True,
        "declared_at": "2026-07-20T09:01:00Z",
    }
    assert declarations["conflict_of_interest_declaration"]["scope"] == {
        "repository_id": review_fixtures.synthetic_repo_id(0),
        "artifact_id": FIRST_CLAIM,
    }


def test_records_a_valid_claim_decision(bench: Bench) -> None:
    review_fixtures.declare_claim(bench.workspace, FIRST_CLAIM, bench.clock)
    review_fixtures.record_claim_decision(bench.workspace, FIRST_CLAIM, bench.clock)

    recorded = _document(bench.workspace)["claims"][0]["claim_decision"]
    assert recorded == {
        "decision": "verified",
        "rationale": review_fixtures.fixture_rationale(FIRST_CLAIM),
        "evidence": review_fixtures.fixture_evidence(FIRST_CLAIM),
        "recorded_at": "2026-07-20T09:02:00Z",
    }
    assert _status(bench.workspace)["decisions_recorded"] == 1


def test_records_a_valid_link_decision(bench: Bench) -> None:
    review_fixtures.declare_claim(bench.workspace, FIRST_CLAIM, bench.clock)
    review_fixtures.record_link_decision(bench.workspace, FIRST_CLAIM, "seed", bench.clock)

    assert _document(bench.workspace)["claims"][0]["link_decisions"] == [
        {
            "target": "seed",
            "decision": "verified",
            "rationale": review_fixtures.fixture_rationale(FIRST_CLAIM, "seed"),
            "evidence": review_fixtures.fixture_evidence(FIRST_CLAIM, "seed"),
            "recorded_at": "2026-07-20T09:02:00Z",
        }
    ]


def test_re_recording_replaces_the_entry_and_refreshes_its_timestamp(bench: Bench) -> None:
    review_fixtures.declare_claim(bench.workspace, FIRST_CLAIM, bench.clock)
    review_fixtures.record_link_decision(bench.workspace, FIRST_CLAIM, "seed", bench.clock)
    review_fixtures.record_link_decision(
        bench.workspace, FIRST_CLAIM, "seed", bench.clock, decision="unclear"
    )

    entries = _document(bench.workspace)["claims"][0]["link_decisions"]
    assert len(entries) == 1
    assert entries[0]["decision"] == "unclear"
    assert entries[0]["recorded_at"] == "2026-07-20T09:03:00Z"


def test_an_invalid_decision_value_is_refused(bench: Bench) -> None:
    review_fixtures.declare_claim(bench.workspace, FIRST_CLAIM, bench.clock)
    with pytest.raises(SystemExit) as error:
        main(
            [
                *_record_link_argv(FIRST_CLAIM, "code", decision="reproducible"),
                "--workspace",
                str(bench.workspace),
                "--evidence",
                "synthetic-repo-one/fixture-code.md:1",
            ],
            clock=bench.clock,
        )
    assert "'reproducible' is not one of" in str(error.value)
    assert _document(bench.workspace)["claims"][0]["link_decisions"] == []


def test_an_unknown_target_is_refused(bench: Bench) -> None:
    review_fixtures.declare_claim(bench.workspace, FIRST_CLAIM, bench.clock)
    with pytest.raises(SystemExit) as error:
        review_fixtures.record_link_decision(
            bench.workspace, FIRST_CLAIM, "hyperparameters", bench.clock
        )
    assert "target 'hyperparameters' is not one of" in str(error.value)


@pytest.mark.parametrize("rationale", ["", "   ", "\t\n"])
def test_an_empty_rationale_is_refused(bench: Bench, rationale: str) -> None:
    review_fixtures.declare_claim(bench.workspace, FIRST_CLAIM, bench.clock)
    with pytest.raises(SystemExit) as error:
        main(
            [
                *_record_link_argv(FIRST_CLAIM, "code", rationale=rationale),
                "--workspace",
                str(bench.workspace),
                "--evidence",
                "synthetic-repo-one/fixture-code.md:1",
            ],
            clock=bench.clock,
        )
    assert "rationale must be a non-empty string" in str(error.value)


def _refuse_evidence(bench: Bench, locator: str) -> str:
    review_fixtures.declare_claim(bench.workspace, FIRST_CLAIM, bench.clock)
    with pytest.raises(SystemExit) as error:
        main(
            [
                *_record_link_argv(FIRST_CLAIM, "code"),
                "--workspace",
                str(bench.workspace),
                # "--evidence --" would be read as argparse's own end-of-options marker.
                f"--evidence={locator}",
            ],
            clock=bench.clock,
        )
    assert _document(bench.workspace)["claims"][0]["link_decisions"] == []
    return str(error.value)


@pytest.mark.parametrize(
    "locator", ["", "   ", "N/A", "n/a", "none", "None", "-", "TBD", "unknown"]
)
def test_placeholder_evidence_is_refused(bench: Bench, locator: str) -> None:
    message = _refuse_evidence(bench, locator)
    assert "whitespace-only evidence locator" in message or "is a placeholder" in message


def test_a_bare_double_dash_evidence_locator_is_refused(bench: Bench) -> None:
    """A literal ``--`` locator is refused on every supported interpreter.

    Which guard rejects it is not pinned: argparse hands a ``--`` option value to
    the command differently across 3.10 and 3.14, so on one it reaches the
    placeholder rule as the string "--" and on the other it arrives as a
    non-string and is refused a step earlier. Both refuse and neither records a
    decision, which is the property that matters; asserting one message made this
    pass locally and fail on 3.10.
    """
    message = _refuse_evidence(bench, "--")
    assert "evidence locator" in message


def test_missing_evidence_is_refused(bench: Bench) -> None:
    review_fixtures.declare_claim(bench.workspace, FIRST_CLAIM, bench.clock)
    with pytest.raises(SystemExit) as error:
        main(
            [*_record_link_argv(FIRST_CLAIM, "code"), "--workspace", str(bench.workspace)],
            clock=bench.clock,
        )
    assert "requires at least one evidence locator" in str(error.value)


def test_duplicate_evidence_after_normalization_is_refused(bench: Bench) -> None:
    review_fixtures.declare_claim(bench.workspace, FIRST_CLAIM, bench.clock)
    with pytest.raises(SystemExit) as error:
        main(
            [
                *_record_link_argv(FIRST_CLAIM, "code"),
                "--workspace",
                str(bench.workspace),
                "--evidence",
                "synthetic-repo-one/README.md:1",
                "--evidence",
                "  synthetic-repo-one/README.MD:1 ",
            ],
            clock=bench.clock,
        )
    assert "repeats evidence locator" in str(error.value)


def test_status_counts_track_fill_level_and_expose_a_stable_json_key_set(
    bench: Bench, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = _status(bench.workspace)
    assert frozenset(empty) == STATUS_JSON_KEYS
    assert empty["decisions_recorded"] == 0
    assert empty["declarations_recorded"] == 0
    assert empty["final_export_ready"] is False

    review_fixtures.fill_claim(
        bench.workspace,
        FIRST_CLAIM,
        bench.clock,
        skip_targets={"environment", "seed", "commit"},
    )
    partial = _status(bench.workspace)
    assert partial["decisions_recorded"] == 8
    assert partial["decisions_required"] == 22
    assert partial["declarations_recorded"] == 1
    assert partial["completed_claims"] == 0
    assert partial["incomplete_claims"][0] == {
        "claim_id": FIRST_CLAIM,
        "missing_declarations": False,
        "missing_claim_decision": False,
        "missing_link_targets": ["environment", "seed", "commit"],
    }

    capsys.readouterr()
    main(["status", "--workspace", str(bench.workspace)])
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "workspace valid: claims=2 completed=0 decisions=8/22 declarations=1/2"
    assert lines[1] == (
        f"cannot finalize {FIRST_CLAIM}: missing link decisions for environment, seed, "
        "commit (record each with `record-link`)"
    )

    for target in ("environment", "seed", "commit"):
        review_fixtures.record_link_decision(bench.workspace, FIRST_CLAIM, target, bench.clock)
    complete = _status(bench.workspace)
    assert complete["decisions_recorded"] == 11
    assert complete["completed_claims"] == 1
    assert complete["finalized_claims"] == 0
    assert [entry["claim_id"] for entry in complete["incomplete_claims"]] == [SECOND_CLAIM]

    capsys.readouterr()
    main(["status", "--workspace", str(bench.workspace), "--json"])
    reported = json.loads(capsys.readouterr().out)
    assert frozenset(reported) == STATUS_JSON_KEYS
    assert reported == complete


def test_clear_field_requires_confirmation_and_removes_only_its_target(bench: Bench) -> None:
    review_fixtures.fill_claim(bench.workspace, FIRST_CLAIM, bench.clock)
    before = _document(bench.workspace)

    with pytest.raises(SystemExit) as error:
        main(
            [
                "clear-field",
                "--workspace",
                str(bench.workspace),
                "--claim-id",
                FIRST_CLAIM,
                "--field",
                "link",
                "--target",
                "seed",
            ],
            clock=bench.clock,
        )
    assert "without --confirm" in str(error.value)
    assert _document(bench.workspace) == before

    main(
        [
            "clear-field",
            "--workspace",
            str(bench.workspace),
            "--claim-id",
            FIRST_CLAIM,
            "--field",
            "link",
            "--target",
            "seed",
            "--confirm",
        ],
        clock=bench.clock,
    )
    after = _document(bench.workspace)
    assert [entry["target"] for entry in after["claims"][0]["link_decisions"]] == [
        target for target in TARGETS if target != "seed"
    ]
    assert after["claims"][0]["claim_decision"] == before["claims"][0]["claim_decision"]
    assert after["claims"][0]["declarations"] == before["claims"][0]["declarations"]
    assert after["claims"][1] == before["claims"][1]


def test_clear_field_requires_a_target_for_a_link(bench: Bench) -> None:
    review_fixtures.fill_claim(bench.workspace, FIRST_CLAIM, bench.clock)
    with pytest.raises(SystemExit) as error:
        main(
            [
                "clear-field",
                "--workspace",
                str(bench.workspace),
                "--claim-id",
                FIRST_CLAIM,
                "--field",
                "link",
                "--confirm",
            ],
            clock=bench.clock,
        )
    assert "requires --target" in str(error.value)


def test_a_partial_claim_cannot_finalize_and_the_error_names_the_missing_targets(
    bench: Bench,
) -> None:
    review_fixtures.fill_claim(
        bench.workspace,
        FIRST_CLAIM,
        bench.clock,
        skip_targets={"environment", "seed", "commit"},
    )
    with pytest.raises(SystemExit) as error:
        review_fixtures.finalize_claim(
            bench.workspace, bench.truth_path, FIRST_CLAIM, bench.clock
        )
    assert (
        f"cannot finalize {FIRST_CLAIM}: missing link decisions for environment, seed, "
        "commit (record each with `record-link`)"
        in str(error.value)
    )
    assert _document(bench.workspace)["claims"][0]["finalized_at"] is None


def test_a_complete_claim_finalizes(bench: Bench) -> None:
    review_fixtures.fill_claim(bench.workspace, FIRST_CLAIM, bench.clock)
    review_fixtures.finalize_claim(bench.workspace, bench.truth_path, FIRST_CLAIM, bench.clock)

    document = _document(bench.workspace)
    assert document["claims"][0]["finalized_at"] == "2026-07-20T09:13:00Z"
    assert document["claims"][1]["finalized_at"] is None
    assert _status(bench.workspace)["finalized_claims"] == 1


def test_editing_a_finalized_claim_withdraws_its_finalization(bench: Bench) -> None:
    review_fixtures.fill_claim(bench.workspace, FIRST_CLAIM, bench.clock)
    review_fixtures.finalize_claim(bench.workspace, bench.truth_path, FIRST_CLAIM, bench.clock)
    review_fixtures.record_link_decision(
        bench.workspace, FIRST_CLAIM, "seed", bench.clock, decision="unclear"
    )
    assert _document(bench.workspace)["claims"][0]["finalized_at"] is None


def test_declaration_timestamps_must_strictly_precede_finalization(bench: Bench) -> None:
    frozen = datetime(2026, 7, 20, 9, 0, 0, tzinfo=timezone.utc)
    review_fixtures.fill_claim(bench.workspace, FIRST_CLAIM, lambda: frozen)
    with pytest.raises(SystemExit) as error:
        review_fixtures.finalize_claim(
            bench.workspace, bench.truth_path, FIRST_CLAIM, lambda: frozen
        )
    message = str(error.value)
    assert "blinding_declaration.declared_at" in message
    assert "is not strictly earlier than the finalization timestamp" in message


def test_an_incomplete_review_cannot_be_exported(bench: Bench) -> None:
    review_fixtures.fill_claim(bench.workspace, FIRST_CLAIM, bench.clock)
    review_fixtures.finalize_claim(bench.workspace, bench.truth_path, FIRST_CLAIM, bench.clock)
    out = bench.root / "final-claim-review.json"

    with pytest.raises(SystemExit) as error:
        main(
            [
                "finalize-review",
                "--workspace",
                str(bench.workspace),
                "--claims",
                str(bench.truth_path),
                "--out",
                str(out),
            ],
            clock=bench.clock,
        )
    assert f"claims awaiting finalization are ['{SECOND_CLAIM}']" in str(error.value)
    assert not out.exists()


def _export(bench: Bench, name: str = "final-claim-review.json") -> Path:
    review_fixtures.complete_workspace(
        bench.workspace, bench.truth, bench.truth_path, bench.clock
    )
    out = bench.root / name
    main(
        [
            "finalize-review",
            "--workspace",
            str(bench.workspace),
            "--claims",
            str(bench.truth_path),
            "--out",
            str(out),
        ],
        clock=bench.clock,
    )
    return out


def test_a_complete_review_exports_and_passes_claim_review_validation(
    bench: Bench, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _export(bench)
    printed = capsys.readouterr().out.splitlines()
    assert printed[-2] == (
        f"wrote final claim review {out}: claims=2 reviews=2 decisions=22/22; "
        f"validated against {bench.truth_path}"
    )
    assert printed[-1].startswith("claim_review.validate_review summary: ")

    exported = _document(out)
    summary = validate_review(exported, bench.truth, sha256_file(bench.truth_path))
    assert summary["claims"] == 2
    assert len(exported["claims"]) == 2
    for record in exported["claims"]:
        assert len(record["reviews"]) == 1
        review = record["reviews"][0]
        assert review["reviewer_id"] == review_fixtures.REVIEWER_ID
        assert [entry["target"] for entry in review["link_decisions"]] == list(TARGETS)
        assert record["adjudication"] is None
    decisions = sum(
        1 + len(review["link_decisions"])
        for record in exported["claims"]
        for review in record["reviews"]
    )
    assert decisions == 22

    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            str(ROOT / "corpus" / "scripts" / "claim_review.py"),
            "validate",
            "--review",
            str(out),
            "--claims",
            str(bench.truth_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "valid claim-review artifact" in completed.stdout


def test_the_final_artifact_carries_no_workspace_only_field(bench: Bench) -> None:
    out = _export(bench)
    raw = out.read_bytes()
    for probe in (b"notes", b"recorded_at", b"finalized_at", b"not_final", b"workspace_kind"):
        assert probe not in raw

    exported = _document(out)
    for record in exported["claims"]:
        review = record["reviews"][0]
        declared = review["blinding_declaration"]["declared_at"]
        assert declared < review["reviewed_at"]
        assert review["conflict_of_interest_declaration"]["declared_at"] < review["reviewed_at"]


def test_finalize_review_refuses_an_existing_out(bench: Bench) -> None:
    out = _export(bench)
    original = out.read_bytes()
    with pytest.raises(SystemExit) as error:
        main(
            [
                "finalize-review",
                "--workspace",
                str(bench.workspace),
                "--claims",
                str(bench.truth_path),
                "--out",
                str(out),
            ],
            clock=bench.clock,
        )
    assert "refusing to overwrite existing final claim review" in str(error.value)
    assert out.read_bytes() == original


def test_an_atomic_write_failure_leaves_the_workspace_byte_identical(
    bench: Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    review_fixtures.declare_claim(bench.workspace, FIRST_CLAIM, bench.clock)
    before = bench.workspace.read_bytes()

    def refuse(source: Any, destination: Any) -> None:
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(SystemExit) as error:
        review_fixtures.record_claim_decision(bench.workspace, FIRST_CLAIM, bench.clock)
    monkeypatch.undo()

    assert "synthetic replace failure" in str(error.value)
    assert "is unchanged at sha256=" in str(error.value)
    assert bench.workspace.read_bytes() == before
    assert [path.name for path in bench.root.glob(".*partial")] == []


def test_reviewer_identity_cannot_change_mid_workspace(bench: Bench) -> None:
    review_fixtures.fill_claim(bench.workspace, FIRST_CLAIM, bench.clock)
    before = bench.workspace.read_bytes()

    with pytest.raises(SystemExit) as reinit:
        main(
            [
                "init",
                "--scaffold",
                str(bench.scaffold_path),
                "--claims",
                str(bench.truth_path),
                "--reviewer-id",
                review_fixtures.SECOND_REVIEWER_ID,
                "--domain-expertise",
                review_fixtures.DOMAIN_EXPERTISE,
                "--workspace",
                str(bench.workspace),
            ],
            clock=bench.clock,
        )
    assert "refusing to overwrite existing reviewer workspace" in str(reinit.value)
    assert bench.workspace.read_bytes() == before

    for command in ("declare", "record-claim", "record-link", "finalize-review"):
        with pytest.raises(SystemExit) as rejected:
            main(
                [
                    command,
                    "--workspace",
                    str(bench.workspace),
                    "--reviewer-id",
                    review_fixtures.SECOND_REVIEWER_ID,
                ],
                clock=bench.clock,
            )
        assert rejected.value.code == 2
    assert bench.workspace.read_bytes() == before


def test_show_prints_the_whole_claim_record_and_no_aggregate(
    bench: Bench, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    main(
        [
            "show",
            "--workspace",
            str(bench.workspace),
            "--claims",
            str(bench.truth_path),
            "--claim-id",
            FIRST_CLAIM,
            "--target",
            "commit",
        ]
    )
    out = capsys.readouterr().out
    record = bench.truth["claims"][0]
    link = next(entry for entry in record["expected_links"] if entry["target"] == "commit")
    for expected in (
        f"claim_id: {FIRST_CLAIM}",
        f"repo_id: {record['repo_id']}",
        f"repo_commit: {record['repo_commit']}",
        f"source.path: {record['source']['path']}",
        f"source.sha256: {record['source']['sha256']}",
        f"source.quote: {record['source']['quote']}",
        f"claim.text: {record['claim']['text']}",
        f"claim.metric: {record['claim']['metric']}",
        f"claim.value: {record['claim']['value']}",
        f"claim.unit: {record['claim']['unit']}",
        f"claim.context: {record['claim']['context']}",
        "link.target: commit",
        f"link.expected_resolution: {link['expected_resolution']}",
        f"link.rationale: {link['rationale']}",
    ):
        assert expected in out
    assert SECOND_CLAIM not in out
    assert "completed=" not in out
    assert "decisions=" not in out
    assert "declarations=" not in out


def test_verify_reports_a_bound_workspace_and_rejects_a_moved_scaffold(
    bench: Bench, capsys: pytest.CaptureFixture[str]
) -> None:
    review_fixtures.fill_claim(bench.workspace, FIRST_CLAIM, bench.clock)
    capsys.readouterr()
    main(["verify", "--workspace", str(bench.workspace), "--claims", str(bench.truth_path)])
    assert "verified reviewer workspace" in capsys.readouterr().out

    scaffold = _document(bench.scaffold_path)
    scaffold["candidate_pair"] = list(reversed(scaffold["candidate_pair"]))
    write_json(bench.scaffold_path, scaffold)
    with pytest.raises(SystemExit) as error:
        main(["verify", "--workspace", str(bench.workspace), "--claims", str(bench.truth_path)])
    assert "changed: expected sha256=" in str(error.value)


def test_a_two_claim_review_walks_from_init_to_a_validated_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    truth = review_fixtures.synthetic_truth(2)
    truth_path = review_fixtures.write_truth(tmp_path, truth)
    scaffold_path = review_fixtures.write_scaffold(tmp_path, truth_path, truth)
    clock = review_fixtures.advancing_clock()
    workspace = review_fixtures.init_workspace(tmp_path, scaffold_path, truth_path, clock)

    for claim in truth["claims"]:
        claim_id = str(claim["claim_id"])
        review_fixtures.declare_claim(workspace, claim_id, clock)
        review_fixtures.record_claim_decision(workspace, claim_id, clock)
        for target in TARGETS:
            review_fixtures.record_link_decision(workspace, claim_id, target, clock)
        review_fixtures.finalize_claim(workspace, truth_path, claim_id, clock)

    status = _status(workspace)
    assert status["completed_claims"] == 2
    assert status["finalized_claims"] == 2
    assert status["decisions_recorded"] == 22
    assert status["final_export_ready"] is True
    assert status["incomplete_claims"] == []

    out = tmp_path / "walked-claim-review.json"
    assert (
        main(
            [
                "finalize-review",
                "--workspace",
                str(workspace),
                "--claims",
                str(truth_path),
                "--out",
                str(out),
            ],
            clock=clock,
        )
        == 0
    )
    exported = _document(out)
    assert validate_review(exported, truth, sha256_file(truth_path))["claims"] == 2
    assert [record["claim_id"] for record in exported["claims"]] == [FIRST_CLAIM, SECOND_CLAIM]
    assert all(len(record["reviews"]) == 1 for record in exported["claims"])
    assert main(["verify", "--workspace", str(workspace), "--claims", str(truth_path)]) == 0
