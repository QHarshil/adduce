"""Behaviour tests for the coordinator-only claim-review metrics tool."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from corpus.scripts import claim_review_entry, claim_review_metrics, reviewer_feedback
from corpus.scripts.claim_ground_truth import TARGETS
from corpus.scripts.claim_review import initialize_review, merge_independent_reviews
from corpus.scripts.run_contract import sha256_file, write_json

from tests import review_fixtures as rf
from tests.test_corpus_tooling import _write_minimal_valid_run

PRE_CANDIDATE_KEYS = {
    "claim_review_metrics_schema_version",
    "mode",
    "inputs",
    "scope",
    "completion",
    "agreement",
    "disagreements",
    "decision_counts",
    "adjudication",
    "merged_review",
    "process_burden",
    "formulas",
    "limitations",
}
POST_CANDIDATE_KEYS = {
    "claim_review_metrics_schema_version",
    "mode",
    "inputs",
    "scope",
    "gates",
    "accepted_reference",
    "runs",
    "formulas",
    "limitations",
}
RUN_KEYS = {
    "run_id",
    "run_path",
    "run_meta_sha256",
    "adduce_version",
    "analysis_scope",
    "claim_links_path",
    "claim_links_sha256",
    "confusion_matrix",
    "accuracy",
    "per_class",
    "macro_f1",
    "per_target",
    "per_repository",
    "abstention",
    "claim_status_counts",
    "operational_failures",
}
NEGATIONS = ("no ", "not ", "never", "cannot", "nothing")


class Inputs(NamedTuple):
    truth: dict[str, Any]
    truth_path: Path
    scaffold_path: Path
    review_a: Path
    review_b: Path


def build_reviewer_file(
    tmp_path: Path,
    truth: dict[str, Any],
    truth_path: Path,
    scaffold_path: Path,
    reviewer_id: str,
    overrides: dict[str, dict[str, str]] | None = None,
) -> Path:
    """Export one complete single-reviewer file, overriding named decisions per claim."""
    chosen = overrides or {}
    clock = rf.advancing_clock()
    workspace = rf.init_workspace(
        tmp_path, scaffold_path, truth_path, clock, reviewer_id=reviewer_id
    )
    for claim in truth["claims"]:
        claim_id = str(claim["claim_id"])
        claim_overrides = chosen.get(claim_id, {})
        rf.declare_claim(workspace, claim_id, clock)
        rf.record_claim_decision(
            workspace, claim_id, clock, decision=claim_overrides.get("claim", "verified")
        )
        for target in TARGETS:
            rf.record_link_decision(
                workspace,
                claim_id,
                target,
                clock,
                decision=claim_overrides.get(target, "verified"),
            )
        rf.finalize_claim(workspace, truth_path, claim_id, clock)
    review = tmp_path / f"review-{reviewer_id}.json"
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


def prepare(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    claim_count: int = 2,
    overrides_a: dict[str, dict[str, str]] | None = None,
    overrides_b: dict[str, dict[str, str]] | None = None,
) -> Inputs:
    monkeypatch.chdir(tmp_path)
    truth = rf.synthetic_truth(claim_count)
    truth_path = rf.write_truth(tmp_path, truth)
    scaffold_path = rf.write_scaffold(tmp_path, truth_path, truth)
    review_a = build_reviewer_file(
        tmp_path, truth, truth_path, scaffold_path, rf.REVIEWER_ID, overrides_a
    )
    review_b = build_reviewer_file(
        tmp_path, truth, truth_path, scaffold_path, rf.SECOND_REVIEWER_ID, overrides_b
    )
    return Inputs(truth, truth_path, scaffold_path, review_a, review_b)


def write_merged(tmp_path: Path, inputs: Inputs) -> Path:
    sources = [inputs.review_a, inputs.review_b]
    merged = merge_independent_reviews(
        [json.loads(path.read_text(encoding="utf-8")) for path in sources],
        [sha256_file(path) for path in sources],
        inputs.truth,
        sha256_file(inputs.truth_path),
    )
    path = tmp_path / "merged-claim-review.json"
    write_json(path, merged)
    return path


def claim_links_payload(
    inputs: Inputs,
    run_id: str,
    *,
    observations: dict[tuple[str, str], str] | None = None,
    unevaluated_claims: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Build a candidate claim-link evaluation output for the synthetic truth."""
    chosen = observations or {}
    results = []
    for claim in inputs.truth["claims"]:
        claim_id = str(claim["claim_id"])
        if claim_id in unevaluated_claims:
            results.append(
                {
                    "claim_id": claim_id,
                    "repo_id": str(claim["repo_id"]),
                    "status": "mismatch",
                    "reason": "expected one matching claim trail; found 0",
                    "claim_discovery_match": False,
                    "links": [],
                }
            )
            continue
        links = []
        for link in claim["expected_links"]:
            target = str(link["target"])
            expected = str(link["expected_resolution"])
            default = "absent" if expected == "not_applicable" else expected
            observed = chosen.get((claim_id, target), default)
            links.append(
                {
                    "target": target,
                    "expected_resolution": expected,
                    "observed_resolution": observed,
                    "resolution_match": observed == default,
                    "artifact_identity_match": True,
                    "artifact_comparisons": [],
                    "match": observed == default,
                    "observed_entries": [],
                }
            )
        results.append(
            {
                "claim_id": claim_id,
                "repo_id": str(claim["repo_id"]),
                "status": "match" if all(link["match"] for link in links) else "mismatch",
                "claim_discovery_match": True,
                "expected_trail_status": "supported",
                "observed_trail_status": "supported",
                "trail_status_match": True,
                "links": links,
            }
        )
    return {
        "claim_evaluation_schema_version": 1,
        "evaluated_at": "2026-07-21T10:00:00+00:00",
        "run_id": run_id,
        "adduce_version": "0.test",
        "ground_truth_sha256": sha256_file(inputs.truth_path),
        "corpus_inventory_sha256": inputs.truth["corpus_inventory_sha256"],
        "n_claims": len(results),
        "results": results,
    }


def write_claim_links(tmp_path: Path, inputs: Inputs, run_id: str, **kwargs: Any) -> Path:
    path = tmp_path / f"claim-links-{run_id}.json"
    write_json(path, claim_links_payload(inputs, run_id, **kwargs))
    return path


def write_runs(tmp_path: Path) -> list[Path]:
    runs = []
    for name in rf.CANDIDATE_PAIR:
        run = tmp_path / name
        _write_minimal_valid_run(run, run_id=name)
        runs.append(run)
    return runs


def post_candidate_arguments(
    inputs: Inputs, merged: Path, runs: list[Path], claim_links: list[Path]
) -> list[str]:
    arguments = [
        "post-candidate",
        "--merged",
        str(merged),
        "--review-a",
        str(inputs.review_a),
        "--review-b",
        str(inputs.review_b),
        "--claims",
        str(inputs.truth_path),
    ]
    for run in runs:
        arguments += ["--run", str(run)]
    for links in claim_links:
        arguments += ["--claim-links", str(links)]
    return arguments


def metrics_json(capsys: pytest.CaptureFixture[str], arguments: list[str]) -> dict[str, Any]:
    capsys.readouterr()
    assert claim_review_metrics.main([*arguments, "--format", "json"]) == 0
    return json.loads(capsys.readouterr().out)


def metrics_markdown(capsys: pytest.CaptureFixture[str], arguments: list[str]) -> str:
    capsys.readouterr()
    assert claim_review_metrics.main(arguments) == 0
    return capsys.readouterr().out


def pre_candidate_arguments(inputs: Inputs, *extra: str) -> list[str]:
    return [
        "pre-candidate",
        "--review-a",
        str(inputs.review_a),
        "--review-b",
        str(inputs.review_b),
        "--claims",
        str(inputs.truth_path),
        *extra,
    ]


def affirmative_lines(text: str, needle: str) -> list[str]:
    return [
        line
        for line in text.splitlines()
        if needle in line.lower() and not any(token in line.lower() for token in NEGATIONS)
    ]


def test_perfect_agreement_reports_every_denominator_and_an_undefined_kappa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    inputs = prepare(tmp_path, monkeypatch)

    payload = metrics_json(capsys, pre_candidate_arguments(inputs))

    agreement = payload["agreement"]
    assert agreement["raw"] == {"numerator": 22, "denominator": 22, "ratio": 1.0}
    assert agreement["claim_level"]["denominator"] == 2
    assert agreement["link_level"] == {"numerator": 20, "denominator": 20, "ratio": 1.0}
    assert agreement["disagreement_count"] == 0
    assert payload["disagreements"] == []
    assert agreement["cohens_kappa"]["value"] is None
    assert "expected agreement is 1.0" in agreement["cohens_kappa"]["undefined_reason"]
    assert agreement["cohens_kappa"]["interpretation"] == "descriptive"
    assert all(entry["agreement"]["numerator"] == 2 for entry in agreement["by_target"])
    assert payload["completion"] == [
        {
            "reviewer_id": rf.REVIEWER_ID,
            "decisions": {"numerator": 22, "denominator": 22, "ratio": 1.0},
            "finalized_claims": {"numerator": 2, "denominator": 2, "ratio": 1.0},
            "declarations": {"numerator": 2, "denominator": 2, "ratio": 1.0},
        },
        {
            "reviewer_id": rf.SECOND_REVIEWER_ID,
            "decisions": {"numerator": 22, "denominator": 22, "ratio": 1.0},
            "finalized_claims": {"numerator": 2, "denominator": 2, "ratio": 1.0},
            "declarations": {"numerator": 2, "denominator": 2, "ratio": 1.0},
        },
    ]
    assert payload["adjudication"]["claims_needing_adjudication"]["numerator"] == 0


def test_one_claim_level_disagreement_is_itemised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    claim_id = rf.synthetic_claim_id(0)
    inputs = prepare(tmp_path, monkeypatch, overrides_b={claim_id: {"claim": "revision_required"}})

    payload = metrics_json(capsys, pre_candidate_arguments(inputs))

    assert payload["disagreements"] == [
        {
            "claim_id": claim_id,
            "target": "claim",
            rf.REVIEWER_ID: "verified",
            rf.SECOND_REVIEWER_ID: "revision_required",
        }
    ]
    assert payload["agreement"]["raw"]["numerator"] == 21
    assert payload["agreement"]["claim_level"] == {
        "numerator": 1,
        "denominator": 2,
        "ratio": 0.5,
    }
    assert payload["agreement"]["link_level"]["numerator"] == 20
    assert payload["adjudication"] == {
        "claims_needing_adjudication": {"numerator": 1, "denominator": 2, "ratio": 0.5},
        "claim_ids": [claim_id],
        "decisions_per_adjudicated_claim": 11,
        "adjudication_decisions": 11,
    }
    counts = {entry["reviewer_id"]: entry for entry in payload["decision_counts"]}
    assert counts[rf.SECOND_REVIEWER_ID]["revision_required"] == {
        "numerator": 1,
        "denominator": 22,
        "ratio": 1 / 22,
    }
    assert counts[rf.REVIEWER_ID]["revision_required"]["numerator"] == 0


def test_one_link_level_disagreement_moves_only_that_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    claim_id = rf.synthetic_claim_id(0)
    inputs = prepare(tmp_path, monkeypatch, overrides_b={claim_id: {"code": "unclear"}})

    payload = metrics_json(capsys, pre_candidate_arguments(inputs))

    assert payload["agreement"]["raw"]["numerator"] == 21
    assert payload["agreement"]["claim_level"]["numerator"] == 2
    assert payload["agreement"]["link_level"] == {
        "numerator": 19,
        "denominator": 20,
        "ratio": 0.95,
    }
    by_target = {entry["target"]: entry["agreement"] for entry in payload["agreement"]["by_target"]}
    assert by_target["code"] == {"numerator": 1, "denominator": 2, "ratio": 0.5}
    assert by_target["data"] == {"numerator": 2, "denominator": 2, "ratio": 1.0}
    counts = {entry["reviewer_id"]: entry for entry in payload["decision_counts"]}
    assert counts[rf.SECOND_REVIEWER_ID]["unclear"]["numerator"] == 1


def test_disagreement_concentrated_at_one_target_gives_a_defined_kappa_near_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    overrides = {
        rf.synthetic_claim_id(0): {"data": "unclear"},
        rf.synthetic_claim_id(1): {"data": "unclear"},
    }
    inputs = prepare(tmp_path, monkeypatch, overrides_b=overrides)

    payload = metrics_json(capsys, pre_candidate_arguments(inputs))

    by_target = {entry["target"]: entry["agreement"] for entry in payload["agreement"]["by_target"]}
    assert by_target["data"] == {"numerator": 0, "denominator": 2, "ratio": 0.0}
    assert payload["agreement"]["raw"]["numerator"] == 20
    assert payload["agreement"]["disagreement_count"] == 2
    kappa = payload["agreement"]["cohens_kappa"]
    assert kappa["undefined_reason"] is None
    assert kappa["value"] == pytest.approx(0.0)
    assert len(payload["disagreements"]) == 2
    assert {item["target"] for item in payload["disagreements"]} == {"data"}


def test_a_claim_one_reviewer_has_not_recorded_is_reported_as_not_comparable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    inputs = prepare(tmp_path, monkeypatch)
    partial = json.loads(inputs.review_b.read_text(encoding="utf-8"))
    partial["claims"][1]["reviews"] = []
    partial_path = tmp_path / "review-partial.json"
    write_json(partial_path, partial)

    payload = metrics_json(
        capsys,
        [
            "pre-candidate",
            "--review-a",
            str(inputs.review_a),
            "--review-b",
            str(partial_path),
            "--claims",
            str(inputs.truth_path),
        ],
    )

    agreement = payload["agreement"]
    assert agreement["positions_compared"] == 11
    assert agreement["positions_not_comparable"] == 1
    assert agreement["claims_not_comparable"] == [rf.synthetic_claim_id(1)]
    assert agreement["raw"] == {"numerator": 11, "denominator": 22, "ratio": 0.5}
    completion = {entry["reviewer_id"]: entry for entry in payload["completion"]}
    assert completion[rf.SECOND_REVIEWER_ID]["decisions"] == {
        "numerator": 11,
        "denominator": 22,
        "ratio": 0.5,
    }
    assert completion[rf.SECOND_REVIEWER_ID]["finalized_claims"]["numerator"] == 1


def test_an_empty_comparison_set_and_a_zero_denominator_do_not_crash() -> None:
    empty = claim_review_metrics.agreement_metrics([], [], 0)

    assert empty["raw"] == {"numerator": 0, "denominator": 0, "ratio": None}
    assert empty["cohens_kappa"]["value"] is None
    assert empty["cohens_kappa"]["undefined_reason"] == "there are no comparison positions"
    assert claim_review_metrics.Ratio(0, 0).to_json()["ratio"] is None


def test_pre_candidate_refuses_unusable_source_bindings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = prepare(tmp_path, monkeypatch)
    other_truth = {**inputs.truth, "clone_manifest_sha256": "f" * 64}
    other_truth_path = tmp_path / "other-claims.json"
    write_json(other_truth_path, other_truth)
    other_scaffold = tmp_path / "other-scaffold.json"
    write_json(
        other_scaffold,
        initialize_review(other_truth, sha256_file(other_truth_path), list(rf.CANDIDATE_PAIR)),
    )
    other_truth_review = build_reviewer_file(
        tmp_path, other_truth, other_truth_path, other_scaffold, "reviewer-test-c"
    )
    pair_scaffold = tmp_path / "other-pair-scaffold.json"
    write_json(
        pair_scaffold,
        initialize_review(
            inputs.truth,
            sha256_file(inputs.truth_path),
            ["synthetic-candidate-c", "synthetic-candidate-d"],
        ),
    )
    other_pair_review = build_reviewer_file(
        tmp_path, inputs.truth, inputs.truth_path, pair_scaffold, "reviewer-test-d"
    )

    with pytest.raises(SystemExit) as truth_error:
        claim_review_metrics.main(
            pre_candidate_arguments(inputs._replace(review_b=other_truth_review))
        )
    with pytest.raises(SystemExit) as pair_error:
        claim_review_metrics.main(
            pre_candidate_arguments(inputs._replace(review_b=other_pair_review))
        )
    with pytest.raises(SystemExit) as identity_error:
        claim_review_metrics.main(
            pre_candidate_arguments(inputs._replace(review_b=inputs.review_a))
        )

    assert "different candidate truth SHA-256" in str(truth_error.value)
    assert "bind a different candidate pair" in str(pair_error.value)
    assert f"both reviewer files record reviewer {rf.REVIEWER_ID!r}" in str(identity_error.value)


def test_pre_candidate_refuses_a_merged_file_as_an_independent_reviewer_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = prepare(tmp_path, monkeypatch)
    merged = write_merged(tmp_path, inputs)

    with pytest.raises(SystemExit) as error:
        claim_review_metrics.main(pre_candidate_arguments(inputs._replace(review_b=merged)))

    assert "is a merged claim review, not an independent reviewer file" in str(error.value)


def test_pre_candidate_reports_the_merged_review_and_reviewer_feedback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    inputs = prepare(tmp_path, monkeypatch)
    merged = write_merged(tmp_path, inputs)
    feedback = tmp_path / "feedback-a.json"
    clock = rf.advancing_clock()
    reviewer_feedback.main(
        [
            "init",
            "--reviewer-id",
            rf.REVIEWER_ID,
            "--review",
            str(inputs.review_a),
            "--out",
            str(feedback),
        ],
        clock=clock,
    )
    for claim_index, minutes in ((0, "40"), (1, "20")):
        reviewer_feedback.main(
            [
                "record-time",
                "--feedback",
                str(feedback),
                "--review",
                str(inputs.review_a),
                "--claim-id",
                rf.synthetic_claim_id(claim_index),
                "--minutes",
                minutes,
            ],
            clock=clock,
        )
    reviewer_feedback.main(
        [
            "submit",
            "--feedback",
            str(feedback),
            "--rating",
            "decision_vocabulary_clear=4",
            "--rating",
            "evidence_was_locatable=2",
            "--rating",
            "tool_prevented_invalid_states=5",
            "--rating",
            "felt_pressure_to_verify=1",
            "--validator-failures",
            "3",
            "--clarification-requests",
            "1",
            "--most-confusing-instruction",
            "",
            "--missing-tool-or-material",
            "a worked example",
        ],
        clock=clock,
    )

    payload = metrics_json(
        capsys,
        pre_candidate_arguments(inputs, "--merged", str(merged), "--feedback", str(feedback)),
    )

    assert payload["merged_review"]["accepted_claims"] == {
        "numerator": 2,
        "denominator": 2,
        "ratio": 1.0,
    }
    assert payload["merged_review"]["adjudicated_claims"]["numerator"] == 0
    assert [
        source["reviewer_id"] for source in payload["merged_review"]["initial_review_sources"]
    ] == [
        rf.REVIEWER_ID,
        rf.SECOND_REVIEWER_ID,
    ]
    burden = payload["process_burden"]
    assert len(burden) == 1
    assert burden[0]["reviewer_id"] == rf.REVIEWER_ID
    assert burden[0]["duration"] == {"total_minutes": 60.0, "timed_claims": 2, "claims": 2}
    assert burden[0]["median_minutes_per_claim"] == {
        "value": 30.0,
        "n": 2,
        "minimum": 20.0,
        "maximum": 40.0,
    }
    assert burden[0]["validator_failures"] == {"numerator": 3, "denominator": 2, "ratio": 1.5}
    assert burden[0]["clarification_requests"]["numerator"] == 1
    assert burden[0]["ratings"]["evidence_was_locatable"] == 2
    assert burden[0]["free_text_answered"] == {
        "most_confusing_instruction": False,
        "missing_tool_or_material": True,
    }
    assert payload["inputs"]["feedback"] == [
        {"path": str(feedback), "sha256": sha256_file(feedback)}
    ]


def test_pre_candidate_refuses_feedback_bound_to_another_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = prepare(tmp_path, monkeypatch)
    unrelated = tmp_path / "unrelated-review.json"
    write_json(unrelated, json.loads(inputs.review_a.read_text(encoding="utf-8")))
    feedback = tmp_path / "feedback-unbound.json"
    clock = rf.advancing_clock()
    reviewer_feedback.main(
        [
            "init",
            "--reviewer-id",
            rf.REVIEWER_ID,
            "--review",
            str(unrelated),
            "--out",
            str(feedback),
        ],
        clock=clock,
    )

    with pytest.raises(SystemExit) as error:
        claim_review_metrics.main(pre_candidate_arguments(inputs, "--feedback", str(feedback)))

    assert "has not been submitted" in str(error.value)


def test_pre_candidate_json_and_markdown_are_stable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    inputs = prepare(
        tmp_path, monkeypatch, overrides_b={rf.synthetic_claim_id(0): {"seed": "unclear"}}
    )

    payload = metrics_json(capsys, pre_candidate_arguments(inputs))
    markdown = metrics_markdown(capsys, pre_candidate_arguments(inputs))

    assert set(payload) == PRE_CANDIDATE_KEYS
    assert payload["claim_review_metrics_schema_version"] == 1
    assert payload["mode"] == "pre-candidate"
    assert set(payload["inputs"]) == {"claims", "review_a", "review_b", "merged", "feedback"}
    assert set(payload["scope"]) == {
        "claims",
        "targets",
        "positions_per_claim",
        "comparison_positions",
        "decision_values",
        "reviewers",
    }
    assert set(payload["agreement"]) == {
        "raw",
        "claim_level",
        "link_level",
        "by_target",
        "positions_compared",
        "positions_not_comparable",
        "claims_not_comparable",
        "disagreement_count",
        "cohens_kappa",
    }
    assert set(payload["completion"][0]) == {
        "reviewer_id",
        "decisions",
        "finalized_claims",
        "declarations",
    }
    assert set(payload["decision_counts"][0]) == {
        "reviewer_id",
        "revision_required",
        "unclear",
        "verified",
    }
    assert set(payload["adjudication"]) == {
        "claims_needing_adjudication",
        "claim_ids",
        "decisions_per_adjudicated_claim",
        "adjudication_decisions",
    }
    assert payload["merged_review"] is None
    assert payload["process_burden"] == []

    assert "Mode: pre-candidate" in markdown
    assert sha256_file(inputs.truth_path) in markdown
    assert sha256_file(inputs.review_a) in markdown
    assert sha256_file(inputs.review_b) in markdown
    assert "## Limitations" in markdown
    assert "## Formulas" in markdown
    assert "21/22" in markdown
    assert "Disagreement count: 1" in markdown
    for limitation in claim_review_metrics.LIMITATIONS:
        assert limitation in markdown


def test_markdown_never_claims_a_rate_a_cut_off_or_generalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    inputs = prepare(tmp_path, monkeypatch)

    markdown = metrics_markdown(capsys, pre_candidate_arguments(inputs))

    assert "This pilot establishes no population false-positive rate." in markdown
    assert "This pilot establishes no calibrated score threshold" in markdown
    assert "false positive rate" not in markdown.lower()
    for needle in ("false-positive rate", "threshold", "generalize", "generalise"):
        assert affirmative_lines(markdown, needle) == []


def test_post_candidate_refuses_every_missing_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = prepare(tmp_path, monkeypatch)
    merged = write_merged(tmp_path, inputs)
    runs = write_runs(tmp_path)
    links = [write_claim_links(tmp_path, inputs, run.name) for run in runs]
    complete = post_candidate_arguments(inputs, merged, runs, links)

    def without(flag: str, count: int = 2) -> list[str]:
        trimmed: list[str] = []
        index = 0
        while index < len(complete):
            if complete[index] == flag and count > 0:
                count -= 1
                index += 2
                continue
            trimmed.append(complete[index])
            index += 1
        return trimmed

    with pytest.raises(SystemExit) as merged_error:
        claim_review_metrics.main(without("--merged"))
    with pytest.raises(SystemExit) as review_error:
        claim_review_metrics.main(without("--review-a"))
    with pytest.raises(SystemExit) as run_error:
        claim_review_metrics.main(without("--run", 1))
    with pytest.raises(SystemExit) as links_error:
        claim_review_metrics.main(without("--claim-links"))

    assert "require the accepted merged claim review (--merged)" in str(merged_error.value)
    assert "cannot be computed before the claim-review gate has passed" in str(merged_error.value)
    assert "require both independent source reviews" in str(review_error.value)
    assert "--review-a" in str(review_error.value)
    assert "require the two pre-registered candidate run directories (--run); found 1" in str(
        run_error.value
    )
    assert "require the candidate claim-link outputs (--claim-links); found 0" in str(
        links_error.value
    )


def test_post_candidate_refuses_a_review_that_is_not_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unclear = {rf.synthetic_claim_id(0): {"commit": "unclear"}}
    inputs = prepare(tmp_path, monkeypatch, overrides_a=unclear, overrides_b=unclear)
    merged = write_merged(tmp_path, inputs)
    runs = write_runs(tmp_path)
    links = [write_claim_links(tmp_path, inputs, run.name) for run in runs]

    with pytest.raises(SystemExit) as error:
        claim_review_metrics.main(post_candidate_arguments(inputs, merged, runs, links))

    assert "is not accepted as candidate ground truth" in str(error.value)


def test_post_candidate_refuses_unbound_or_unpaired_candidate_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = prepare(tmp_path, monkeypatch)
    merged = write_merged(tmp_path, inputs)
    runs = write_runs(tmp_path)
    links = [write_claim_links(tmp_path, inputs, run.name) for run in runs]
    foreign = tmp_path / "claim-links-foreign.json"
    write_json(
        foreign,
        {**claim_links_payload(inputs, "unrelated-run"), "ground_truth_sha256": "a" * 64},
    )
    stray = write_claim_links(tmp_path, inputs, "unrelated-run")
    misnamed = tmp_path / "not-the-candidate-pair"
    _write_minimal_valid_run(misnamed, run_id="not-the-candidate-pair")

    with pytest.raises(SystemExit) as one_output:
        claim_review_metrics.main(post_candidate_arguments(inputs, merged, runs, links[:1]))
    with pytest.raises(SystemExit) as foreign_truth:
        claim_review_metrics.main(
            post_candidate_arguments(inputs, merged, runs, [links[0], foreign])
        )
    with pytest.raises(SystemExit) as unknown_run:
        claim_review_metrics.main(post_candidate_arguments(inputs, merged, runs, [links[0], stray]))
    with pytest.raises(SystemExit) as pair_error:
        claim_review_metrics.main(
            post_candidate_arguments(inputs, merged, [runs[0], misnamed], links)
        )

    assert "no --claim-links input covers run(s)" in str(one_output.value)
    assert "is bound to claim truth" in str(foreign_truth.value)
    assert "which is not one of the supplied candidate runs" in str(unknown_run.value)
    assert "must be the pre-registered candidate pair" in str(pair_error.value)


def test_post_candidate_scores_a_perfect_candidate_and_pins_its_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    inputs = prepare(tmp_path, monkeypatch)
    merged = write_merged(tmp_path, inputs)
    runs = write_runs(tmp_path)
    links = [write_claim_links(tmp_path, inputs, run.name) for run in runs]
    arguments = post_candidate_arguments(inputs, merged, runs, links)

    payload = metrics_json(capsys, arguments)
    markdown = metrics_markdown(capsys, arguments)

    assert set(payload) == POST_CANDIDATE_KEYS
    assert payload["mode"] == "post-candidate"
    assert set(payload["inputs"]) == {
        "claims",
        "merged",
        "review_a",
        "review_b",
        "runs",
        "claim_links",
    }
    assert payload["gates"] == {
        "merged_review_complete_and_accepted": True,
        "independent_review_sources_verified": True,
        "candidate_runs_validated": 2,
        "claim_link_outputs_bound": 2,
    }
    assert payload["accepted_reference"] == {
        "links_bound": 20,
        "excluded_not_verified": 0,
        "excluded_expectation_outside_class_set": 0,
        "accepted_links": 20,
    }
    assert [run["run_id"] for run in payload["runs"]] == list(rf.CANDIDATE_PAIR)
    run = payload["runs"][0]
    assert set(run) == RUN_KEYS
    assert run["accuracy"] == {"numerator": 20, "denominator": 20, "ratio": 1.0}
    assert run["confusion_matrix"]["counts"] == {
        "resolved": {"resolved": 4, "unresolved": 0, "not_applicable": 0, "other": 0},
        "unresolved": {"resolved": 0, "unresolved": 0, "not_applicable": 0, "other": 0},
        "not_applicable": {"resolved": 0, "unresolved": 0, "not_applicable": 16, "other": 0},
    }
    per_class = {entry["class"]: entry for entry in run["per_class"]}
    assert per_class["resolved"]["f1"] == 1.0
    assert per_class["unresolved"]["support"] == 0
    assert per_class["unresolved"]["f1"] is None
    assert (
        per_class["unresolved"]["undefined_reason"]
        == "the accepted record contains no link expecting this class"
    )
    assert run["macro_f1"] == {
        "value": 1.0,
        "classes_averaged": ["resolved", "not_applicable"],
        "undefined_reason": None,
    }
    assert run["abstention"] == {
        "declining_when_expected": {"numerator": 16, "denominator": 16, "ratio": 1.0},
        "over_declining": {"numerator": 0, "denominator": 4, "ratio": 0.0},
    }
    assert run["operational_failures"]["repositories_attempted"] == 1
    assert run["operational_failures"]["timeouts"] == {
        "numerator": 0,
        "denominator": 1,
        "ratio": 0.0,
    }
    assert run["per_repository"][0]["accepted_links"] == 10
    assert run["claim_status_counts"] == {"match": 2}

    assert "Mode: post-candidate" in markdown
    assert "## Limitations" in markdown
    assert "20/20" in markdown
    assert sha256_file(merged) in markdown
    assert sha256_file(links[0]) in markdown
    assert "expected \\ candidate" in markdown
    for needle in ("false-positive rate", "threshold", "generalize", "generalise"):
        assert affirmative_lines(markdown, needle) == []


def test_post_candidate_counts_wrong_resolutions_and_unevaluated_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    inputs = prepare(tmp_path, monkeypatch)
    merged = write_merged(tmp_path, inputs)
    runs = write_runs(tmp_path)
    first, second = (str(claim["claim_id"]) for claim in inputs.truth["claims"])
    links = [
        write_claim_links(
            tmp_path,
            inputs,
            runs[0].name,
            observations={
                (first, "code"): "resolved",
                (first, "seed"): "unknown",
                (first, "commit"): "unresolved",
            },
        ),
        write_claim_links(tmp_path, inputs, runs[1].name, unevaluated_claims=(second,)),
    ]

    payload = metrics_json(capsys, post_candidate_arguments(inputs, merged, runs, links))

    imperfect = payload["runs"][0]
    assert imperfect["accuracy"] == {"numerator": 17, "denominator": 20, "ratio": 0.85}
    assert imperfect["confusion_matrix"]["counts"] == {
        "resolved": {"resolved": 3, "unresolved": 1, "not_applicable": 0, "other": 0},
        "unresolved": {"resolved": 0, "unresolved": 0, "not_applicable": 0, "other": 0},
        "not_applicable": {"resolved": 1, "unresolved": 0, "not_applicable": 14, "other": 1},
    }
    per_class = {entry["class"]: entry for entry in imperfect["per_class"]}
    assert per_class["resolved"] == {
        "class": "resolved",
        "support": 4,
        "predicted": 4,
        "true_positive": 3,
        "false_positive": 1,
        "false_negative": 1,
        "precision": 0.75,
        "recall": 0.75,
        "f1": 0.75,
        "undefined_reason": None,
    }
    assert per_class["unresolved"]["false_positive"] == 1
    assert per_class["unresolved"]["precision"] == 0.0
    assert per_class["unresolved"]["recall"] is None
    assert per_class["not_applicable"]["precision"] == 1.0
    assert per_class["not_applicable"]["recall"] == 0.875
    assert per_class["not_applicable"]["f1"] == pytest.approx(14 / 15)
    assert imperfect["macro_f1"]["classes_averaged"] == ["resolved", "not_applicable"]
    assert imperfect["macro_f1"]["value"] == pytest.approx((0.75 + 14 / 15) / 2)
    by_target = {entry["target"]: entry["accuracy"] for entry in imperfect["per_target"]}
    assert by_target["code"] == {"numerator": 1, "denominator": 2, "ratio": 0.5}
    assert by_target["data"] == {"numerator": 2, "denominator": 2, "ratio": 1.0}
    assert imperfect["abstention"]["over_declining"] == {
        "numerator": 1,
        "denominator": 4,
        "ratio": 0.25,
    }

    unevaluated = payload["runs"][1]
    assert unevaluated["confusion_matrix"]["links_without_candidate_observation"] == 10
    assert unevaluated["confusion_matrix"]["counts"]["not_applicable"]["other"] == 8
    assert unevaluated["accuracy"] == {"numerator": 10, "denominator": 20, "ratio": 0.5}
    assert unevaluated["claim_status_counts"] == {"match": 1, "mismatch": 1}
    assert unevaluated["per_repository"][1]["matching_links"] == 0
