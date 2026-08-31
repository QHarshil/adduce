"""Methodology contracts for corpus sampling, review, and claim ground truth."""

from __future__ import annotations

import copy
import csv
import json
import random
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from corpus.scripts.claim_ground_truth import (
    TARGETS,
    ClaimGroundTruthError,
    _observed_resolution,
    validate_ground_truth,
)
from corpus.scripts.claim_review import (
    ClaimReviewError,
    initialize_review,
    merge_independent_reviews,
    validate_review_for_candidate_run,
    verify_independent_review_sources,
)
from corpus.scripts.claim_review import (
    validate_review as validate_claim_review,
)
from corpus.scripts.label_findings import (
    FindingReviewError,
    _collect_conflict_declaration,
    _conflict_scope,
    initialize_finding_review_source,
    load,
    merge_independent_finding_reviews,
    report,
    validate_against_run,
    validate_finding_review_calibration,
    validate_independent_finding_review,
    validate_merged_finding_review,
)
from corpus.scripts.label_findings import (
    validate as validate_labels,
)
from corpus.scripts.preregistration import (
    PREREGISTRATION_ANALYSIS_PLAN_PATHS,
    PreregistrationError,
    build_preregistration,
    validate_preregistration_bytes,
)
from corpus.scripts.review_allocation import (
    ReviewAllocationError,
    require_first_review_completion,
    require_review_completion,
)
from corpus.scripts.review_allocation import (
    build_manifest as build_review_allocation,
)
from corpus.scripts.review_allocation import (
    validate_manifest as validate_review_allocation,
)
from corpus.scripts.run_contract import sha256_file
from corpus.scripts.sample_findings import (
    _filter_repositories,
    _fingerprint_set_sha256,
    _pick_repos,
    _sample_findings,
    _sampler_python_identity,
)
from jsonschema import Draft202012Validator

from tests.test_corpus_tooling import _write_minimal_valid_run

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_SCRIPT = ROOT / "corpus" / "scripts" / "sample_findings.py"
LABEL_SCRIPT = ROOT / "corpus" / "scripts" / "label_findings.py"
CLAIM_REVIEW_SCRIPT = ROOT / "corpus" / "scripts" / "claim_review.py"

# Frozen by protocol amendment 8 for the duration of the unlocked development
# interval. They are the study's material, not its machinery, and move only
# under a further dated amendment made before the change.
FROZEN_REPOS_SHA256 = "859fded20ca432fdd02a135b690ecaac75e5c2d457a2b7b2ea62dfc738107fd9"
FROZEN_BADGED_PROVENANCE_SHA256 = (
    "7119028796ce55c5fe6a4024f1ed435b2cc859fd6cdd06a12df11d21e391dd5f"
)
FROZEN_CLONE_MANIFEST_SHA256 = (
    "2fcefb2503e60d4a04a0b4a343056a99ad00294ae3d5ee5c8f430d0b79435b94"
)
FROZEN_CLONE_SNAPSHOT_SET_SHA256 = (
    "9a171656825240a0b8371833f69c3b25b570e9bb74c4e6bd5f5cab618de06c31"
)
FROZEN_CLAIM_GROUND_TRUTH_SHA256 = (
    "9a26d06c59070173ad89f60bc221a395dd1a487132eeed7415d2cadeff63611e"
)


def _preregistration_fixture() -> tuple[bytes, dict[str, Any], dict[str, object]]:
    schema_data = (ROOT / "corpus" / "preregistration.schema.json").read_bytes()
    repos_data = (
        "id,cohort,repo_url,commit_sha\n"
        f"badged,badged_functional,https://example.invalid/badged,{'1' * 40}\n"
        f"labelled,unvetted,https://example.invalid/labelled,{'2' * 40}\n"
        f"load,stress,https://example.invalid/load,{'3' * 40}\n"
    ).encode()
    records = []
    for repo_id, commit in (
        ("badged", "1" * 40),
        ("labelled", "2" * 40),
        ("load", "3" * 40),
    ):
        records.append(
            {
                "id": repo_id,
                "status": "cloned",
                "acquisition_status": "complete",
                "requested_sha": commit,
                "resolved_sha": commit,
                "git_tree_sha": "4" * 40,
                "worktree_sha256": "5" * 64,
                "submodule_state": "not_configured",
                "git_lfs_state": "no_pointers",
                "git_lfs_pointer_count": 0,
            }
        )
    inputs = {
        "schema_data": schema_data,
        "repos_data": repos_data,
        "clone_manifest_data": json.dumps(
            {"clone_schema_version": 2, "records": records},
            sort_keys=True,
        ).encode(),
        "claim_ground_truth_data": b'{"claims":[]}\n',
        "claim_review_schema_data": b'{"title":"claim review"}\n',
        "badged_provenance_data": b"id,commit_sha\n",
        "analysis_plan_files": {
            name: (ROOT / "corpus" / name).read_bytes()
            for name in PREREGISTRATION_ANALYSIS_PLAN_PATHS
        },
    }
    identity: dict[str, object] = {
        "adduce_version": "0.test",
        "adduce_source_commit": "9" * 40,
        "adduce_source_tree_sha256": "a" * 64,
        "builtin_rule_ids": ["R-TEST-001", "R-TEST-002"],
        "dependency_versions": {"fixture": "1.0"},
        "corpus_harness_git_commit": "9" * 40,
    }
    payload = build_preregistration(
        protocol_id="fixture-r3",
        candidate_pair=["candidate-a", "candidate-b"],
        source_identity=identity,
        timeout_seconds=300,
        **inputs,
    )
    return (
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
        inputs,
        identity,
    )


def _validate_preregistration_fixture(
    data: bytes,
    inputs: dict[str, Any],
    identity: dict[str, object],
    **overrides: Any,
) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        **inputs,
        "source_identity": identity,
        "candidate_run_name": "candidate-a",
        "timeout_seconds": 300,
    }
    arguments.update(overrides)
    return validate_preregistration_bytes(data, **arguments)


def _probability(numerator: int, denominator: int) -> dict[str, int | float]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator,
    }


def _bind_sample_set(entries: list[dict]) -> None:
    repo_ids = sorted({str(entry["repo_id"]) for entry in entries})
    fingerprints = [str(entry["finding_fingerprint"]) for entry in entries]
    binding = {
        "binding_schema_version": 1,
        "sampler_sha256": sha256_file(SAMPLE_SCRIPT),
        "sampler_python": _sampler_python_identity(),
        "arguments": {
            "mode": "sample",
            "seed": 7,
            "statuses": sorted({str(entry["finding_status"]) for entry in entries}),
            "n_repos": len(repo_ids),
            "per_stratum": 1,
            "include_cohorts": [],
            "exclude_cohorts": [],
            "include_repos": [],
            "exclude_repos": [],
            "include_suppressed": True,
        },
        "eligible_repository_ids": repo_ids,
        "selected_repository_ids": repo_ids,
        "entry_count": len(entries),
        "finding_fingerprint_set_sha256": _fingerprint_set_sha256(fingerprints),
    }
    for entry in entries:
        entry["sample_set"] = copy.deepcopy(binding)


def _sample_entry() -> dict:
    entry = {
        "label_schema_version": 2,
        "run_id": "run-1",
        "repo_id": "labelled",
        "repo_commit": "a" * 40,
        "cohort": "unvetted",
        "adduce_version": "0.test",
        "rule_id": "R-TEST-001",
        "category": "Documentation",
        "title": "Test finding",
        "finding_status": "fail",
        "finding_confidence": 0.8,
        "severity": "medium",
        "message": "test",
        "locations": [{"path": "README.md", "line": 1}],
        "suppressed": False,
        "finding_fingerprint": "v1:" + "b" * 64,
        "run_evidence": {
            "binding_schema_version": 1,
            "run_schema_version": 1,
            "run_meta_sha256": "c" * 64,
            "combined_csv_sha256": "d" * 64,
            "raw_json_sha256": "e" * 64,
        },
        "sampling": {
            "design": "two-stage-stratified",
            "design_version": 1,
            "seed": 7,
            "repository_stratum": {
                "cohort": "unvetted",
                "population_size": 1,
                "sample_size": 1,
                "inclusion_probability": _probability(1, 1),
            },
            "finding_stratum": {
                "status": "fail",
                "category": "Documentation",
                "population_size": 1,
                "sample_size": 1,
                "conditional_inclusion_probability": _probability(1, 1),
            },
            "overall_inclusion_probability": _probability(1, 1),
        },
        "reviews": [],
        "adjudication": None,
    }
    _bind_sample_set([entry])
    return entry


def _review(reviewer_id: str, correctness: str = "correct") -> dict:
    return {
        "reviewer_id": reviewer_id,
        "reviewed_at": "2026-07-13T12:00:00+00:00",
        "correctness": correctness,
        "applicability": "applicable",
        "utility": "actionable",
        "root_cause": "none",
        "verification_mode": "manual_static",
        "label_confidence": 0.9,
        "notes": "",
        "evidence_links": ["README.md:1"],
    }


def _mixed_report_entries() -> list[dict]:
    effectiveness = _sample_entry()
    effectiveness["reviews"] = [_review("reviewer-a"), _review("reviewer-b")]

    stress = copy.deepcopy(effectiveness)
    stress.update(
        {
            "repo_id": "stress-case",
            "cohort": "stress",
            "rule_id": "R-STRESS-001",
            "title": "Stress diagnostic",
            "finding_fingerprint": "v1:" + "f" * 64,
        }
    )
    stress["sampling"]["repository_stratum"]["cohort"] = "stress"
    entries = [effectiveness, stress]
    _bind_sample_set(entries)
    return entries


def test_finding_sampling_excludes_stress_unless_explicitly_selected() -> None:
    rows = [
        {"id": "evaluated", "cohort": "badged_functional"},
        {"id": "ordinary", "cohort": "unvetted"},
        {"id": "large", "cohort": "stress"},
    ]

    default = _filter_repositories(
        rows,
        include_cohorts=set(),
        exclude_cohorts=set(),
        include_repos=set(),
        exclude_repos=set(),
    )
    explicit = _filter_repositories(
        rows,
        include_cohorts={"stress"},
        exclude_cohorts=set(),
        include_repos=set(),
        exclude_repos=set(),
    )

    assert {row["id"] for row in default} == {"evaluated", "ordinary"}
    assert [row["id"] for row in explicit] == ["large"]


def test_repository_and_finding_sampling_record_population_and_probability() -> None:
    rows = [
        {"id": "a", "cohort": "evaluated"},
        {"id": "b", "cohort": "evaluated"},
        {"id": "c", "cohort": "unvetted"},
        {"id": "d", "cohort": "unvetted"},
    ]
    picked, repository_design = _pick_repos(rows, 2, random.Random(3))
    sampled = _sample_findings(
        {
            "findings": [
                {"rule_id": "R-1", "status": "fail", "category": "A"},
                {"rule_id": "R-2", "status": "fail", "category": "A"},
                {"rule_id": "R-3", "status": "partial", "category": "A"},
            ]
        },
        frozenset({"fail", "partial"}),
        1,
        random.Random(3),
    )

    assert len(picked) == 2
    assert repository_design == {
        "evaluated": {"population_size": 2, "sample_size": 1},
        "unvetted": {"population_size": 2, "sample_size": 1},
    }
    fail_design = next(design for finding, design in sampled if finding["status"] == "fail")
    assert fail_design["population_size"] == 2
    assert fail_design["sample_size"] == 1
    assert fail_design["conditional_inclusion_probability"] == _probability(1, 2)


def test_census_includes_suppressed_findings_by_default() -> None:
    payload = {
        "findings": [
            {"rule_id": "R-1", "status": "fail", "category": "A", "suppressed": False},
            {"rule_id": "R-2", "status": "pass", "category": "A", "suppressed": True},
        ]
    }

    census = _sample_findings(
        payload,
        frozenset({"fail", "pass"}),
        1,
        random.Random(0),
        census=True,
    )
    without_suppressed = _sample_findings(
        payload,
        frozenset({"fail", "pass"}),
        1,
        random.Random(0),
        census=True,
        include_suppressed=False,
    )

    assert {finding["rule_id"] for finding, _ in census} == {"R-1", "R-2"}
    assert [finding["rule_id"] for finding, _ in without_suppressed] == ["R-1"]
    assert all(design["sample_size"] == design["population_size"] for _, design in census)


def test_sample_set_binding_rejects_deleted_injected_and_inconsistent_records() -> None:
    first = _sample_entry()
    second = copy.deepcopy(first)
    second.update(
        {
            "rule_id": "R-TEST-002",
            "category": "Data",
            "title": "Second finding",
            "finding_fingerprint": "v1:" + "f" * 64,
        }
    )
    second["sampling"]["finding_stratum"]["category"] = "Data"
    entries = [first, second]
    _bind_sample_set(entries)
    validate_labels(entries)

    with pytest.raises(ValueError, match="entry count"):
        validate_labels(entries[:1])

    injected = [*copy.deepcopy(entries), copy.deepcopy(second)]
    injected[-1]["finding_fingerprint"] = "v1:" + "1" * 64
    with pytest.raises(ValueError, match="entry count|fingerprint digest"):
        validate_labels(injected)

    inconsistent = copy.deepcopy(entries)
    inconsistent[1]["sample_set"]["arguments"]["seed"] = 99
    with pytest.raises(ValueError, match="inconsistent sample-set"):
        validate_labels(inconsistent)


def test_v2_schema_rejects_injected_fields() -> None:
    entry = _sample_entry()
    entry["unexpected"] = "injected"
    with pytest.raises(ValueError, match="v2 entry schema"):
        validate_labels([entry])
    with pytest.raises(ValueError, match="v2 entry schema"):
        report([entry])

    entry = _sample_entry()
    entry["reviews"] = [_review("reviewer-a")]
    entry["reviews"][0]["unexpected"] = "injected"
    with pytest.raises(ValueError, match="review fields"):
        validate_labels([entry])

    entry = _sample_entry()
    entry["sampling"]["unexpected"] = "injected"
    with pytest.raises(ValueError, match="sampling design"):
        validate_labels([entry])

    entry = _sample_entry()
    probability = entry["sampling"]["overall_inclusion_probability"]
    probability["unexpected"] = "injected"
    with pytest.raises(ValueError, match="inclusion probability"):
        validate_labels([entry])

    entry = _sample_entry()
    entry["sampling"]["repository_stratum"]["unexpected"] = "injected"
    with pytest.raises(ValueError, match="repository stratum fields"):
        validate_labels([entry])

    entry = _sample_entry()
    entry["reviews"] = [_review("reviewer-a"), _review("reviewer-b", "incorrect")]
    entry["adjudication"] = {
        **_review("unused", "incorrect"),
        "adjudicator_id": "adjudicator-c",
        "notes": "Resolved against the pinned README evidence.",
        "unexpected": "injected",
    }
    del entry["adjudication"]["reviewer_id"]
    with pytest.raises(ValueError, match="review fields"):
        validate_labels([entry])


def test_review_schema_keeps_independent_records_and_requires_adjudication() -> None:
    entry = _sample_entry()
    entry["reviews"] = [_review("reviewer-a"), _review("reviewer-b", "incorrect")]
    validate_labels([entry])

    entry["adjudication"] = {
        **_review("unused", "incorrect"),
        "adjudicator_id": "adjudicator-c",
        "notes": "Resolved against the pinned README evidence.",
    }
    del entry["adjudication"]["reviewer_id"]
    validate_labels([entry])

    entry["adjudication"]["adjudicator_id"] = "reviewer-a"
    with pytest.raises(ValueError, match="adjudicator must be independent"):
        validate_labels([entry])
    entry["adjudication"]["adjudicator_id"] = "adjudicator-c"

    entry["adjudication"]["reviewed_at"] = "2026-07-13T11:59:00+00:00"
    with pytest.raises(ValueError, match="adjudication timestamp precedes"):
        validate_labels([entry])
    entry["adjudication"]["reviewed_at"] = "2026-07-13T12:00:00+00:00"

    entry["reviews"].append(_review("reviewer-a"))
    with pytest.raises(ValueError, match="appears more than once"):
        validate_labels([entry])

    entry["reviews"][-1] = _review("reviewer-c")
    with pytest.raises(ValueError, match="at most two"):
        validate_labels([entry])


def test_review_requires_evidence_and_uncertainty_rationale() -> None:
    entry = _sample_entry()
    entry["reviews"] = [_review("reviewer-a")]
    entry["reviews"][0]["evidence_links"] = []
    with pytest.raises(ValueError, match="evidence link"):
        validate_labels([entry])

    entry["reviews"][0] = _review("reviewer-a", "unclear")
    with pytest.raises(ValueError, match="unclear judgement requires explanatory notes"):
        validate_labels([entry])

    entry["reviews"][0]["notes"] = "The pinned source does not identify the run."
    validate_labels([entry])


def test_review_report_labels_aggregates_as_sample_proportions(
    capsys: pytest.CaptureFixture[str],
) -> None:
    entry = _sample_entry()
    entry["reviews"] = [_review("reviewer-a"), _review("reviewer-b")]
    validate_labels([entry])

    report([entry])

    output = capsys.readouterr().out
    assert "unweighted reviewed-sample proportions" in output
    assert "not corpus rates" in output
    assert "incorrect finding rate" not in output
    assert "independent second review: 1" in output
    assert "per-rule resolved review summary" in output
    assert "R-TEST-001: reviewed=1" in output
    assert "root-cause counts" in output
    assert "none: 1" in output


def test_mixed_report_requires_explicit_scope() -> None:
    entries = _mixed_report_entries()
    validate_labels(entries)

    with pytest.raises(ValueError, match=r"mixed effectiveness and stress.*--report-scope"):
        report(entries)


def test_report_scopes_effectiveness_and_stress_without_pooling(
    capsys: pytest.CaptureFixture[str],
) -> None:
    entries = _mixed_report_entries()

    report(entries, report_scope="effectiveness")
    effectiveness_output = capsys.readouterr().out
    assert "report scope: effectiveness" in effectiveness_output
    assert "scope selection: 1 of 2 validated bound findings" in effectiveness_output
    assert "R-TEST-001: reviewed=1" in effectiveness_output
    assert "R-STRESS-001" not in effectiveness_output
    assert "unweighted reviewed-sample proportions" in effectiveness_output

    report(entries, report_scope="stress")
    stress_output = capsys.readouterr().out
    assert "report scope: stress (diagnostic-only operational review)" in stress_output
    assert "scope selection: 1 of 2 validated bound findings" in stress_output
    assert "R-STRESS-001: reviewed=1" in stress_output
    assert "R-TEST-001" not in stress_output
    assert "unweighted reviewed-sample proportions" not in stress_output
    assert "incorrect fail/partial labels" not in stress_output
    assert "incorrect pass labels" not in stress_output


def test_report_validates_complete_bound_sample_before_scope_filtering() -> None:
    entries = _mixed_report_entries()
    entries[1]["reviews"][0]["evidence_links"] = []

    with pytest.raises(ValueError, match="evidence link"):
        report(entries, report_scope="effectiveness")


def _allocation_sources() -> list[tuple[str, str, list[dict]]]:
    sources: list[list[dict]] = [[], []]
    counter = 1
    statuses = ("fail", "pass", "unknown")
    for repo_number in range(10):
        cohort = "badged_functional" if repo_number < 5 else "unvetted"
        for status in statuses:
            for occurrence in range(10):
                entry = copy.deepcopy(_sample_entry())
                entry.update(
                    {
                        "repo_id": f"repo-{repo_number}",
                        "repo_commit": f"{repo_number + 1:x}" * 40,
                        "cohort": cohort,
                        "rule_id": f"R-ALLOC-{occurrence:03d}",
                        "finding_status": status,
                        "finding_fingerprint": f"v1:{counter:064x}",
                    }
                )
                entry["sampling"]["repository_stratum"]["cohort"] = cohort
                entry["sampling"]["finding_stratum"]["status"] = status
                entry["reviews"] = []
                entry["adjudication"] = None
                sources[0 if repo_number < 5 else 1].append(entry)
                counter += 1

    stress = copy.deepcopy(sources[0][0])
    stress.update(
        {
            "repo_id": "stress-repo",
            "repo_commit": "f" * 40,
            "cohort": "stress",
            "finding_fingerprint": f"v1:{counter:064x}",
        }
    )
    stress["sampling"]["repository_stratum"]["cohort"] = "stress"
    sources[0].append(stress)
    for entries in sources:
        _bind_sample_set(entries)
        validate_labels(entries)
    return [
        ("sentinels.jsonl", "1" * 64, sources[0]),
        ("sample.jsonl", "2" * 64, sources[1]),
    ]


def _allocation_run_binding() -> dict:
    return {
        "run_schema_version": 1,
        "run_id": "candidate-run",
        "adduce_version": "0.test",
        "run_meta_sha256": "3" * 64,
        "corpus_harness_sha256": "4" * 64,
        "claim_ground_truth_sha256": "5" * 64,
    }


def test_review_allocation_is_deterministic_stratified_and_excludes_stress() -> None:
    sources = _allocation_sources()
    manifest = build_review_allocation(sources, _allocation_run_binding(), seed=17)
    repeated = build_review_allocation(list(reversed(sources)), _allocation_run_binding(), seed=17)

    assert manifest == repeated
    assert manifest["population"]["entry_count"] == 300
    assert manifest["population"]["repository_count"] == 10
    assert manifest["calibration_count"] == 40
    assert manifest["second_review_count"] == 60
    calibration = {entry["finding_fingerprint"] for entry in manifest["calibration"]}
    second_review = {entry["finding_fingerprint"] for entry in manifest["second_review"]}
    assert calibration <= second_review
    assert {entry["repo_id"] for entry in manifest["calibration"]} == {
        f"repo-{number}" for number in range(10)
    }
    assert {entry["decision_group"] for entry in manifest["calibration"]} == {
        "emitted",
        "pass",
        "abstention",
    }
    assert all(entry["cohort"] != "stress" for entry in manifest["second_review"])
    bound_sources = {source["source_id"]: source for source in manifest["sources"]}
    assert bound_sources["sentinels.jsonl"]["excluded_stress_entry_count"] == 1
    assert bound_sources["sample.jsonl"]["initial_source_sha256"] == "2" * 64
    assert bound_sources["sentinels.jsonl"]["initial_source_sha256"] == "1" * 64


def test_review_allocation_remains_bound_when_only_review_fields_change() -> None:
    sources = _allocation_sources()
    run_binding = _allocation_run_binding()
    manifest = build_review_allocation(sources, run_binding, seed=4)
    sources[0][2][0]["reviews"] = [_review("reviewer-a")]

    validate_review_allocation(manifest, sources, run_binding)

    tampered = copy.deepcopy(manifest)
    tampered["calibration"][0]["finding_fingerprint"] = "v1:" + "f" * 64
    with pytest.raises(ReviewAllocationError, match="deterministic.*reconstruction"):
        validate_review_allocation(tampered, sources, run_binding)


def test_review_allocation_completion_enforces_calibration_and_full_quota() -> None:
    sources = _allocation_sources()
    manifest = build_review_allocation(sources, _allocation_run_binding(), seed=9)
    entries = {
        entry["finding_fingerprint"]: entry
        for _, _, source_entries in sources
        for entry in source_entries
    }
    for reference in manifest["calibration"]:
        entries[reference["finding_fingerprint"]]["reviews"] = [
            _review("reviewer-a"),
            _review("reviewer-b"),
        ]

    require_review_completion(manifest, sources, "calibration")
    with pytest.raises(ReviewAllocationError, match="first review is incomplete"):
        require_first_review_completion(sources)
    with pytest.raises(ReviewAllocationError, match="second-review review is incomplete"):
        require_review_completion(manifest, sources, "second-review")

    for reference in manifest["second_review"]:
        entry = entries[reference["finding_fingerprint"]]
        if not entry["reviews"]:
            entry["reviews"] = [_review("reviewer-a"), _review("reviewer-b")]
    require_review_completion(manifest, sources, "second-review")

    for _, _, source_entries in sources:
        for entry in source_entries:
            if entry["cohort"] != "stress" and not entry["reviews"]:
                entry["reviews"] = [_review("reviewer-a")]
    require_first_review_completion(sources)


def test_review_allocation_enforces_calibration_exact_agreement_floor() -> None:
    sources = _allocation_sources()
    manifest = build_review_allocation(sources, _allocation_run_binding(), seed=11)
    entries = {
        entry["finding_fingerprint"]: entry
        for _, _, source_entries in sources
        for entry in source_entries
    }
    for index, reference in enumerate(manifest["calibration"]):
        entry = entries[reference["finding_fingerprint"]]
        second = _review("reviewer-b", "incorrect" if index < 9 else "correct")
        entry["reviews"] = [_review("reviewer-a"), second]
        if index < 9:
            adjudication = {
                **_review("unused"),
                "adjudicator_id": "adjudicator-c",
                "notes": "Resolved against the pinned repository evidence.",
            }
            del adjudication["reviewer_id"]
            entry["adjudication"] = adjudication

    with pytest.raises(ReviewAllocationError, match="correctness.*below 80%: 31/40"):
        require_review_completion(manifest, sources, "calibration")


def _completed_finding_review_source(
    manifest: dict,
    sources: list[tuple[str, str, list[dict]]],
    *,
    role: str,
    reviewer_id: str,
) -> dict:
    payload = initialize_finding_review_source(
        manifest,
        sources,
        "6" * 64,
        review_role=role,
        reviewer_id=reviewer_id,
    )
    payload["domain_expertise"] = "Research-artifact evaluation and Python tooling."
    payload["blinding_declaration"] = {
        "independent_review": True,
        "other_reviewer_decisions_not_seen": True,
        "other_reviewer_source_not_accessed": True,
        "declared_at": "2026-07-13T11:00:00+00:00",
    }
    payload["conflict_of_interest_declaration"] = {
        "scope": _conflict_scope(payload["records"]),
        "no_relevant_authorship_or_contribution": True,
        "no_close_collaboration_supervision_or_employment": True,
        "no_financial_conflict": True,
        "no_personal_conflict": True,
        "declared_at": "2026-07-13T11:00:00+00:00",
    }
    for record in payload["records"]:
        record["review"] = _review(reviewer_id)
    return payload


def test_separate_finding_review_sources_bind_roles_and_exclude_stress() -> None:
    sources = _allocation_sources()
    manifest = build_review_allocation(sources, _allocation_run_binding(), seed=17)

    primary = initialize_finding_review_source(
        manifest,
        sources,
        "6" * 64,
        review_role="primary",
        reviewer_id="finding-reviewer-a",
    )
    secondary = initialize_finding_review_source(
        manifest,
        sources,
        "6" * 64,
        review_role="secondary",
        reviewer_id="finding-reviewer-b",
    )

    assert len(primary["records"]) == manifest["population"]["entry_count"] == 300
    assert len(secondary["records"]) == manifest["second_review_count"] == 60
    assert all("cohort" not in record for record in primary["records"])
    assert all("reviews" not in record for record in primary["records"])
    assert all(record["review"] is None for record in primary["records"])
    assert {
        record["finding_fingerprint"] for record in secondary["records"]
    } == {
        reference["finding_fingerprint"] for reference in manifest["second_review"]
    }
    stress_fingerprint = sources[0][2][-1]["finding_fingerprint"]
    assert stress_fingerprint not in {
        record["finding_fingerprint"] for record in primary["records"]
    }
    assert sum(
        binding["excluded_stress_entry_count"]
        for binding in primary["source_bindings"]
    ) == 1


def test_finding_review_sources_merge_deterministically_with_exact_provenance() -> None:
    sources = _allocation_sources()
    manifest = build_review_allocation(sources, _allocation_run_binding(), seed=17)
    primary = _completed_finding_review_source(
        manifest,
        sources,
        role="primary",
        reviewer_id="finding-reviewer-a",
    )
    secondary = _completed_finding_review_source(
        manifest,
        sources,
        role="secondary",
        reviewer_id="finding-reviewer-b",
    )

    validate_independent_finding_review(
        primary,
        manifest,
        sources,
        "6" * 64,
        require_complete=True,
    )
    validate_independent_finding_review(
        secondary,
        manifest,
        sources,
        "6" * 64,
        require_complete=True,
    )
    merged = merge_independent_finding_reviews(
        [primary, secondary],
        ["7" * 64, "8" * 64],
        manifest,
        sources,
        "6" * 64,
    )
    repeated = merge_independent_finding_reviews(
        [secondary, primary],
        ["8" * 64, "7" * 64],
        manifest,
        sources,
        "6" * 64,
    )

    assert merged == repeated
    assert merged["population"] == {
        "cohorts": ["badged_functional", "unvetted"],
        "excluded_cohorts": ["stress"],
        "entry_count": 300,
        "finding_fingerprint_set_sha256": manifest["population"][
            "finding_fingerprint_set_sha256"
        ],
        "primary_review_count": 300,
        "secondary_review_count": 60,
        "excluded_stress_entry_count": 1,
    }
    assert [
        (source["review_role"], source["reviewer_id"], source["sha256"])
        for source in merged["initial_review_sources"]
    ] == [
        ("primary", "finding-reviewer-a", "7" * 64),
        ("secondary", "finding-reviewer-b", "8" * 64),
    ]
    summary = validate_merged_finding_review(
        merged,
        [secondary, primary],
        ["8" * 64, "7" * 64],
        manifest,
        sources,
        "6" * 64,
        require_complete=True,
    )
    assert summary == {
        "population": 300,
        "second_reviewed": 60,
        "adjudicated": 0,
        "pending_adjudications": 0,
    }


def test_finding_review_calibration_requires_complete_pair_and_agreement_floor() -> None:
    sources = _allocation_sources()
    manifest = build_review_allocation(sources, _allocation_run_binding(), seed=19)
    primary = _completed_finding_review_source(
        manifest,
        sources,
        role="primary",
        reviewer_id="finding-reviewer-a",
    )
    secondary = _completed_finding_review_source(
        manifest,
        sources,
        role="secondary",
        reviewer_id="finding-reviewer-b",
    )

    agreements = validate_finding_review_calibration(
        [secondary, primary],
        manifest,
        sources,
        "6" * 64,
    )
    assert agreements["correctness"] == (40, 40)
    assert agreements["applicability"] == (40, 40)

    incomplete = copy.deepcopy(secondary)
    calibration_fingerprint = manifest["calibration"][0]["finding_fingerprint"]
    next(
        record
        for record in incomplete["records"]
        if record["finding_fingerprint"] == calibration_fingerprint
    )["review"] = None
    with pytest.raises(FindingReviewError, match="calibration review is incomplete"):
        validate_finding_review_calibration(
            [primary, incomplete],
            manifest,
            sources,
            "6" * 64,
        )

    low_agreement = copy.deepcopy(secondary)
    by_fingerprint = {
        record["finding_fingerprint"]: record for record in low_agreement["records"]
    }
    for reference in manifest["calibration"][:9]:
        by_fingerprint[reference["finding_fingerprint"]]["review"]["correctness"] = (
            "incorrect"
        )
    with pytest.raises(FindingReviewError, match="correctness.*below 80%: 31/40"):
        validate_finding_review_calibration(
            [primary, low_agreement],
            manifest,
            sources,
            "6" * 64,
        )


def test_finding_review_source_rejects_weak_blinding_and_role_drift() -> None:
    sources = _allocation_sources()
    manifest = build_review_allocation(sources, _allocation_run_binding(), seed=5)
    primary = _completed_finding_review_source(
        manifest,
        sources,
        role="primary",
        reviewer_id="finding-reviewer-a",
    )

    missing_declaration = copy.deepcopy(primary)
    missing_declaration["blinding_declaration"] = None
    with pytest.raises(FindingReviewError, match="blinding declaration fields"):
        validate_independent_finding_review(
            missing_declaration,
            manifest,
            sources,
            "6" * 64,
        )

    late_declaration = copy.deepcopy(primary)
    late_declaration["blinding_declaration"]["declared_at"] = (
        "2026-07-13T12:00:01+00:00"
    )
    with pytest.raises(FindingReviewError, match="after review began"):
        validate_independent_finding_review(
            late_declaration,
            manifest,
            sources,
            "6" * 64,
        )

    missing_conflict_declaration = copy.deepcopy(primary)
    missing_conflict_declaration["conflict_of_interest_declaration"] = None
    with pytest.raises(FindingReviewError, match="conflict-of-interest declaration fields"):
        validate_independent_finding_review(
            missing_conflict_declaration,
            manifest,
            sources,
            "6" * 64,
        )

    conflicted = copy.deepcopy(primary)
    conflicted["conflict_of_interest_declaration"]["no_financial_conflict"] = False
    with pytest.raises(FindingReviewError, match="assignment must be reassigned"):
        validate_independent_finding_review(
            conflicted,
            manifest,
            sources,
            "6" * 64,
        )

    wrong_scope = copy.deepcopy(primary)
    wrong_scope["conflict_of_interest_declaration"]["scope"][
        "finding_fingerprint_set_sha256"
    ] = "0" * 64
    with pytest.raises(FindingReviewError, match="scope does not match"):
        validate_independent_finding_review(
            wrong_scope,
            manifest,
            sources,
            "6" * 64,
        )

    late_conflict_declaration = copy.deepcopy(primary)
    late_conflict_declaration["conflict_of_interest_declaration"]["declared_at"] = (
        "2026-07-13T12:00:01+00:00"
    )
    with pytest.raises(FindingReviewError, match="after review began"):
        validate_independent_finding_review(
            late_conflict_declaration,
            manifest,
            sources,
            "6" * 64,
        )

    changed_role = copy.deepcopy(primary)
    changed_role["review_role"] = "secondary"
    with pytest.raises(FindingReviewError, match="immutable field 'selection'"):
        validate_independent_finding_review(
            changed_role,
            manifest,
            sources,
            "6" * 64,
        )

    deleted_record = copy.deepcopy(primary)
    deleted_record["records"].pop()
    with pytest.raises(FindingReviewError, match="assigned records"):
        validate_independent_finding_review(
            deleted_record,
            manifest,
            sources,
            "6" * 64,
        )

    same_reviewer = _completed_finding_review_source(
        manifest,
        sources,
        role="secondary",
        reviewer_id="finding-reviewer-a",
    )
    with pytest.raises(FindingReviewError, match="distinct reviewer identities"):
        merge_independent_finding_reviews(
            [primary, same_reviewer],
            ["7" * 64, "8" * 64],
            manifest,
            sources,
            "6" * 64,
        )


def test_conflicted_finding_reviewer_is_recused_without_collecting_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("builtins.input", lambda _: "conflict")

    with pytest.raises(FindingReviewError, match="assignment must be reassigned"):
        _collect_conflict_declaration(
            {
                "repository_ids": ["labelled"],
                "finding_fingerprint_set_sha256": "1" * 64,
            },
            role="primary reviewer",
        )


def test_merged_finding_review_requires_adjudication_and_exact_source_hashes() -> None:
    sources = _allocation_sources()
    manifest = build_review_allocation(sources, _allocation_run_binding(), seed=13)
    primary = _completed_finding_review_source(
        manifest,
        sources,
        role="primary",
        reviewer_id="finding-reviewer-a",
    )
    secondary = _completed_finding_review_source(
        manifest,
        sources,
        role="secondary",
        reviewer_id="finding-reviewer-b",
    )
    merged = merge_independent_finding_reviews(
        [primary, secondary],
        ["7" * 64, "8" * 64],
        manifest,
        sources,
        "6" * 64,
    )
    calibration_fingerprint = manifest["calibration"][0]["finding_fingerprint"]
    disputed = next(
        entry
        for source in merged["sources"]
        for entry in source["entries"]
        if entry["finding_fingerprint"] == calibration_fingerprint
    )
    disputed["reviews"][1]["correctness"] = "incorrect"

    changed_secondary = copy.deepcopy(secondary)
    changed_record = next(
        record
        for record in changed_secondary["records"]
        if record["finding_fingerprint"] == calibration_fingerprint
    )
    changed_record["review"]["correctness"] = "incorrect"
    changed_merged = merge_independent_finding_reviews(
        [primary, changed_secondary],
        ["7" * 64, "9" * 64],
        manifest,
        sources,
        "6" * 64,
    )
    with pytest.raises(FindingReviewError, match="unadjudicated"):
        validate_merged_finding_review(
            changed_merged,
            [primary, changed_secondary],
            ["7" * 64, "9" * 64],
            manifest,
            sources,
            "6" * 64,
            require_complete=True,
        )

    changed_disputed = next(
        entry
        for source in changed_merged["sources"]
        for entry in source["entries"]
        if entry["finding_fingerprint"] == calibration_fingerprint
    )
    adjudication = {
        **_review("unused", "correct"),
        "adjudicator_id": "finding-adjudicator-c",
        "notes": "Resolved against the commit-pinned repository evidence.",
        "conflict_of_interest_declaration": {
            "scope": _conflict_scope([changed_disputed]),
            "no_relevant_authorship_or_contribution": True,
            "no_close_collaboration_supervision_or_employment": True,
            "no_financial_conflict": True,
            "no_personal_conflict": True,
            "declared_at": "2026-07-13T12:00:00+00:00",
        },
    }
    del adjudication["reviewer_id"]
    changed_disputed["adjudication"] = adjudication
    validate_merged_finding_review(
        changed_merged,
        [primary, changed_secondary],
        ["7" * 64, "9" * 64],
        manifest,
        sources,
        "6" * 64,
        require_complete=True,
    )

    missing_adjudicator_declaration = copy.deepcopy(changed_merged)
    changed_adjudication = next(
        entry
        for source in missing_adjudicator_declaration["sources"]
        for entry in source["entries"]
        if entry["finding_fingerprint"] == calibration_fingerprint
    )["adjudication"]
    del changed_adjudication["conflict_of_interest_declaration"]
    with pytest.raises(FindingReviewError, match="requires a conflict-of-interest declaration"):
        validate_merged_finding_review(
            missing_adjudicator_declaration,
            [primary, changed_secondary],
            ["7" * 64, "9" * 64],
            manifest,
            sources,
            "6" * 64,
        )

    conflicted_adjudicator = copy.deepcopy(changed_merged)
    conflicted_adjudication = next(
        entry
        for source in conflicted_adjudicator["sources"]
        for entry in source["entries"]
        if entry["finding_fingerprint"] == calibration_fingerprint
    )["adjudication"]
    conflicted_adjudication["conflict_of_interest_declaration"][
        "no_close_collaboration_supervision_or_employment"
    ] = False
    with pytest.raises(FindingReviewError, match="assignment must be reassigned"):
        validate_merged_finding_review(
            conflicted_adjudicator,
            [primary, changed_secondary],
            ["7" * 64, "9" * 64],
            manifest,
            sources,
            "6" * 64,
        )

    with pytest.raises(FindingReviewError, match="bound field 'initial_review_sources'"):
        validate_merged_finding_review(
            changed_merged,
            [primary, changed_secondary],
            ["7" * 64, "a" * 64],
            manifest,
            sources,
            "6" * 64,
        )


def test_review_allocation_rejects_missing_decision_group() -> None:
    sources = _allocation_sources()
    for _, _, entries in sources:
        entries[:] = [entry for entry in entries if entry["finding_status"] != "unknown"]
        _bind_sample_set(entries)

    with pytest.raises(ReviewAllocationError, match="lacks decision group"):
        build_review_allocation(sources, _allocation_run_binding(), seed=0)


def _draw_bound_sample(tmp_path: Path) -> tuple[Path, Path, list[dict]]:
    run = tmp_path / "run"
    sample = tmp_path / "sample.jsonl"
    _write_minimal_valid_run(run)
    command = [
        sys.executable,
        str(SAMPLE_SCRIPT),
        "--run",
        str(run),
        "--n-repos",
        "1",
        "--statuses",
        "fail",
        "--out",
        str(sample),
    ]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    repeated = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert repeated.returncode != 0
    assert "refusing to overwrite" in repeated.stderr
    return run, sample, load(sample)


def test_sample_is_bound_to_validated_run_artifacts(tmp_path: Path) -> None:
    run, _, entries = _draw_bound_sample(tmp_path)

    validate_labels(entries)
    validate_against_run(entries, run)

    binding = entries[0]["run_evidence"]
    assert binding["run_meta_sha256"] == sha256_file(run / "run_meta.json")
    assert binding["raw_json_sha256"] == sha256_file(run / "raw_json" / "repo.json")
    assert binding["combined_csv_sha256"] == sha256_file(run / "combined.csv")
    sample_set = entries[0]["sample_set"]
    assert sample_set["sampler_sha256"] == sha256_file(SAMPLE_SCRIPT)
    assert sample_set["sampler_sha256"] == sha256_file(
        run / "harness" / "scripts" / "sample_findings.py"
    )
    assert sample_set["sampler_python"] == _sampler_python_identity()
    assert sample_set["arguments"] == {
        "mode": "sample",
        "seed": 0,
        "statuses": ["fail"],
        "n_repos": 1,
        "per_stratum": 2,
        "include_cohorts": [],
        "exclude_cohorts": [],
        "include_repos": [],
        "exclude_repos": [],
        "include_suppressed": True,
    }
    assert sample_set["eligible_repository_ids"] == ["repo"]
    assert sample_set["selected_repository_ids"] == ["repo"]
    assert sample_set["entry_count"] == 1


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("finding_status", "pass"),
        ("category", "Changed category"),
        ("title", "Changed title"),
        ("message", "Changed message"),
        ("locations", [{"path": "README.md", "line": 99}]),
        ("suppressed", True),
    ],
)
def test_run_binding_rejects_sampled_finding_drift(
    tmp_path: Path, field: str, changed: object
) -> None:
    run, _, entries = _draw_bound_sample(tmp_path)
    entries[0][field] = changed

    with pytest.raises(
        ValueError,
        match=(
            "sample-set|finding fingerprint|exact finding evidence|status is absent|finding stratum"
        ),
    ):
        validate_against_run(entries, run)


def test_run_binding_rejects_identity_and_run_tampering(tmp_path: Path) -> None:
    run, _, entries = _draw_bound_sample(tmp_path)
    entries[0]["run_evidence"]["run_meta_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="run evidence binding"):
        validate_against_run(entries, run)

    entries[0]["run_evidence"]["run_meta_sha256"] = sha256_file(run / "run_meta.json")
    entries[0]["repo_commit"] = "f" * 40
    with pytest.raises(ValueError, match="repository commit"):
        validate_against_run(entries, run)

    entries[0]["repo_commit"] = "a" * 40
    raw = run / "raw_json" / "repo.json"
    raw.write_text(raw.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="corpus run is invalid"):
        validate_against_run(entries, run)


def test_run_binding_rejects_different_sampler_source(tmp_path: Path) -> None:
    run, _, entries = _draw_bound_sample(tmp_path)
    entries[0]["sample_set"]["sampler_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="different sampler source"):
        validate_against_run(entries, run)


def test_run_binding_rejects_pre_run_reviews(tmp_path: Path) -> None:
    run, _, entries = _draw_bound_sample(tmp_path)
    entries[0]["reviews"] = [_review("reviewer-a")]
    entries[0]["reviews"][0]["reviewed_at"] = "2025-12-31T23:59:59+00:00"

    with pytest.raises(ValueError, match="review timestamp precedes run completion"):
        validate_against_run(entries, run)


def test_initial_pilot_rejects_dynamic_review_mode() -> None:
    entry = _sample_entry()
    review = _review("reviewer-a")
    review["verification_mode"] = "dynamic"
    entry["reviews"] = [review]

    with pytest.raises(ValueError, match="verification mode"):
        validate_labels([entry])


def test_v2_review_and_reporting_require_matching_run(tmp_path: Path) -> None:
    run, sample, _ = _draw_bound_sample(tmp_path)

    unbound = subprocess.run(
        [sys.executable, str(LABEL_SCRIPT), str(sample), "--report"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    bound = subprocess.run(
        [
            sys.executable,
            str(LABEL_SCRIPT),
            str(sample),
            "--run",
            str(run),
            "--report",
            "--report-scope",
            "effectiveness",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert unbound.returncode != 0
    assert "v2 samples require --run" in unbound.stderr
    assert bound.returncode == 0, bound.stderr
    assert "sampled findings" in bound.stdout
    assert "report scope: effectiveness" in bound.stdout


def test_report_scope_is_report_only(tmp_path: Path) -> None:
    run, sample, _ = _draw_bound_sample(tmp_path)

    completed = subprocess.run(
        [
            sys.executable,
            str(LABEL_SCRIPT),
            str(sample),
            "--run",
            str(run),
            "--report-scope",
            "effectiveness",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "--report-scope requires --report" in completed.stderr


def test_review_allocation_cli_requires_complete_argument_set(tmp_path: Path) -> None:
    run, sample, _ = _draw_bound_sample(tmp_path)
    without_manifest = subprocess.run(
        [
            sys.executable,
            str(LABEL_SCRIPT),
            str(sample),
            "--run",
            str(run),
            "--review-set",
            "calibration",
            "--reviewer-id",
            "reviewer-a",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    without_sources = subprocess.run(
        [
            sys.executable,
            str(LABEL_SCRIPT),
            str(sample),
            "--run",
            str(run),
            "--allocation",
            str(tmp_path / "allocation.json"),
            "--review-set",
            "calibration",
            "--reviewer-id",
            "reviewer-a",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    shared_file_attempt = subprocess.run(
        [
            sys.executable,
            str(LABEL_SCRIPT),
            str(sample),
            "--run",
            str(run),
            "--allocation",
            str(tmp_path / "allocation.json"),
            "--review-set",
            "calibration",
            "--allocation-source",
            str(sample),
            "--reviewer-id",
            "reviewer-a",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert without_manifest.returncode != 0
    assert "--allocation and --review-set must be supplied together" in without_manifest.stderr
    assert without_sources.returncode != 0
    assert "--allocation requires every bound file" in without_sources.stderr
    assert shared_file_attempt.returncode != 0
    assert "must use separate reviewer sources" in shared_file_attempt.stderr


def test_census_cli_records_complete_selection_contract(tmp_path: Path) -> None:
    run = tmp_path / "run"
    sample = tmp_path / "census.jsonl"
    _write_minimal_valid_run(run)

    completed = subprocess.run(
        [
            sys.executable,
            str(SAMPLE_SCRIPT),
            "--run",
            str(run),
            "--census",
            "--include-repo",
            "repo",
            "--out",
            str(sample),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    entries = load(sample)
    validate_labels(entries)
    validate_against_run(entries, run)
    assert entries[0]["sampling"]["design"] == "census"
    assert entries[0]["sampling"]["overall_inclusion_probability"] == _probability(1, 1)
    assert entries[0]["sample_set"]["arguments"]["mode"] == "census"
    assert entries[0]["sample_set"]["arguments"]["statuses"] == [
        "fail",
        "not-applicable",
        "partial",
        "pass",
        "unknown",
    ]
    assert entries[0]["sample_set"]["arguments"]["n_repos"] is None
    assert entries[0]["sample_set"]["arguments"]["per_stratum"] is None


def test_sample_and_review_paths_must_be_outside_immutable_run(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_minimal_valid_run(run)

    sample_inside = subprocess.run(
        [
            sys.executable,
            str(SAMPLE_SCRIPT),
            "--run",
            str(run),
            "--out",
            str(run / "sample.jsonl"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    review_inside = subprocess.run(
        [
            sys.executable,
            str(LABEL_SCRIPT),
            str(run / "sample.jsonl"),
            "--run",
            str(run),
            "--report",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert sample_inside.returncode != 0
    assert "outside the immutable corpus run" in sample_inside.stderr
    assert review_inside.returncode != 0
    assert "outside the immutable run" in review_inside.stderr


def test_cli_rejects_legacy_and_mixed_review_records(tmp_path: Path) -> None:
    run, sample, entries = _draw_bound_sample(tmp_path)
    legacy = copy.deepcopy(entries[0])
    legacy["label_schema_version"] = 1
    legacy_path = tmp_path / "legacy.jsonl"
    legacy_path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")

    mixed_path = tmp_path / "mixed.jsonl"
    mixed_path.write_text(
        "\n".join(json.dumps(entry) for entry in [entries[0], legacy]) + "\n",
        encoding="utf-8",
    )
    for path in (legacy_path, mixed_path):
        completed = subprocess.run(
            [
                sys.executable,
                str(LABEL_SCRIPT),
                str(path),
                "--run",
                str(run),
                "--report",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert completed.returncode != 0
        assert "v2-bound sample set" in completed.stderr


def _write_inventory(path: Path, commit: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", "cohort", "repo_url", "commit_sha"])
        writer.writeheader()
        writer.writerow(
            {
                "id": "labelled",
                "cohort": "unvetted",
                "repo_url": "https://example.invalid/labelled",
                "commit_sha": commit,
            }
        )
        writer.writerow(
            {
                "id": "load-test",
                "cohort": "stress",
                "repo_url": "https://example.invalid/load-test",
                "commit_sha": "b" * 40,
            }
        )


def _make_claim_repo(path: Path) -> str:
    path.mkdir(parents=True)
    (path / "README.md").write_text(
        "# Results\nOur reported accuracy is 91.2% on the test set.\n",
        encoding="utf-8",
    )
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.name", "Corpus Test"],
        ["git", "config", "user.email", "corpus@example.invalid"],
        ["git", "add", "README.md"],
        ["git", "commit", "-qm", "claim fixture"],
    ):
        subprocess.run(command, cwd=path, check=True, capture_output=True, text=True)
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _claim_payload(repos: Path, clones: Path, commit: str) -> dict:
    clone_manifest = clones / "clones_manifest.json"
    clone_manifest.write_text(
        json.dumps(
            {
                "records": [
                    {"id": "labelled", "status": "cloned", "error": None},
                    {"id": "load-test", "status": "cloned", "error": None},
                ]
            }
        ),
        encoding="utf-8",
    )
    readme = clones / "labelled" / "README.md"
    quote = readme.read_text(encoding="utf-8").splitlines()[1]
    links = []
    for target in TARGETS:
        if target == "reported_result":
            resolution = "resolved"
            artifacts = [{"kind": "claim_source"}]
        elif target == "commit":
            resolution = "resolved"
            artifacts = [{"kind": "literal", "value": commit}]
        else:
            resolution = "not_applicable"
            artifacts = []
        links.append(
            {
                "target": target,
                "expected_resolution": resolution,
                "artifacts": artifacts,
                "rationale": "Pre-scan manual inspection.",
            }
        )
    return {
        "claim_ground_truth_schema_version": 1,
        "corpus_inventory_sha256": sha256_file(repos),
        "clone_manifest_sha256": sha256_file(clone_manifest),
        "frozen_at": "2026-07-13T12:00:00+00:00",
        "claims": [
            {
                "claim_id": "labelled-headline",
                "repo_id": "labelled",
                "repo_commit": commit,
                "source": {
                    "kind": "repository_file",
                    "path": "README.md",
                    "sha256": sha256_file(readme),
                    "line_start": 2,
                    "line_end": 2,
                    "quote": quote,
                },
                "claim": {
                    "text": "accuracy is 91.2%",
                    "metric": "accuracy",
                    "value": 91.2,
                    "unit": "percent",
                },
                "adduce_match": {
                    "claim_id": "C1",
                    "headline_contains": "accuracy is 91.2%",
                },
                "expected_trail_status": "supported",
                "expected_links": links,
                "ground_truth_review": {
                    "prepared_by": "reviewer-a",
                    "prepared_at": "2026-07-13T11:00:00+00:00",
                    "verified_by": "reviewer-b",
                    "verified_at": "2026-07-13T11:30:00+00:00",
                },
            }
        ],
        "unavailable_repositories": [],
    }


def _domain_review(reviewer_id: str, decision: str = "verified") -> dict:
    return {
        "reviewer_id": reviewer_id,
        "domain_expertise": "Machine-learning artifact evaluation",
        "reviewed_at": "2026-07-14T12:00:00+00:00",
        "blinding_declaration": {
            "independent_review": True,
            "other_reviewer_decisions_not_seen": True,
            "adduce_claim_link_outputs_not_seen": True,
            "declared_at": "2026-07-14T11:00:00+00:00",
        },
        "conflict_of_interest_declaration": {
            "scope": {
                "repository_id": "labelled",
                "artifact_id": "labelled-headline",
            },
            "no_relevant_authorship_or_contribution": True,
            "no_close_collaboration_supervision_or_employment": True,
            "no_financial_conflict": True,
            "no_personal_conflict": True,
            "declared_at": "2026-07-14T11:00:00+00:00",
        },
        "claim_decision": decision,
        "claim_rationale": "The pinned source supports the selected claim.",
        "claim_evidence": ["README.md:2"],
        "link_decisions": [
            {
                "target": target,
                "decision": decision,
                "rationale": f"The frozen mapping for {target} matches the pinned evidence.",
                "evidence": ["README.md:2"],
            }
            for target in TARGETS
        ],
    }


def _claim_adjudication(decision: str = "verified") -> dict:
    return {
        "adjudicator_id": "adjudicator-c",
        "domain_expertise": "Machine-learning artifact evaluation",
        "adjudicated_at": "2026-07-14T13:00:00+00:00",
        "conflict_of_interest_declaration": {
            "scope": {
                "repository_id": "labelled",
                "artifact_id": "labelled-headline",
            },
            "no_relevant_authorship_or_contribution": True,
            "no_close_collaboration_supervision_or_employment": True,
            "no_financial_conflict": True,
            "no_personal_conflict": True,
            "declared_at": "2026-07-14T12:30:00+00:00",
        },
        "claim_decision": decision,
        "claim_rationale": "The pinned source resolves the disagreement.",
        "claim_evidence": ["README.md:2"],
        "link_decisions": [
            {
                "target": target,
                "decision": decision,
                "rationale": f"The pinned evidence resolves {target}.",
                "evidence": ["README.md:2"],
            }
            for target in TARGETS
        ],
    }


def _bind_claim_review_sources(review: dict) -> None:
    reviewer_ids = sorted(
        {item["reviewer_id"] for claim in review["claims"] for item in claim["reviews"]}
    )
    review["initial_review_sources"] = [
        {"reviewer_id": reviewer_id, "sha256": f"{number:064x}"}
        for number, reviewer_id in enumerate(reviewer_ids, 1)
    ]


def test_claim_review_scaffold_binds_truth_without_fabricating_decisions(
    tmp_path: Path,
) -> None:
    clones = tmp_path / "clones"
    commit = _make_claim_repo(clones / "labelled")
    repos = tmp_path / "repos.csv"
    _write_inventory(repos, commit)
    (clones / "load-test").mkdir()
    truth = _claim_payload(repos, clones, commit)
    review = initialize_review(
        truth,
        "7" * 64,
        ["pilot-0.1.2dev0-r4-a", "pilot-0.1.2dev0-r4-b"],
    )

    summary = validate_claim_review(review, truth, "7" * 64)

    assert summary == {"claims": 1, "completed": 0, "accepted": 0, "adjudicated": 0}
    assert review["claims"][0]["reviews"] == []
    assert review["claims"][0]["adjudication"] is None
    with pytest.raises(ClaimReviewError, match="lacks two independent reviews"):
        validate_claim_review(review, truth, "7" * 64, require_complete=True)


def test_claim_review_requires_independent_blinded_link_level_evidence(
    tmp_path: Path,
) -> None:
    clones = tmp_path / "clones"
    commit = _make_claim_repo(clones / "labelled")
    repos = tmp_path / "repos.csv"
    _write_inventory(repos, commit)
    (clones / "load-test").mkdir()
    truth = _claim_payload(repos, clones, commit)
    review = initialize_review(truth, "8" * 64, ["candidate-a", "candidate-b"])
    review["claims"][0]["reviews"] = [
        _domain_review("reviewer-a"),
        _domain_review("reviewer-b"),
    ]
    _bind_claim_review_sources(review)

    summary = validate_claim_review(
        review, truth, "8" * 64, require_complete=True, require_accepted=True
    )
    assert summary["accepted"] == 1

    unblinded = copy.deepcopy(review)
    unblinded["claims"][0]["reviews"][1]["blinding_declaration"][
        "adduce_claim_link_outputs_not_seen"
    ] = False
    with pytest.raises(ClaimReviewError, match="must affirm"):
        validate_claim_review(unblinded, truth, "8" * 64)

    duplicate = copy.deepcopy(review)
    duplicate["claims"][0]["reviews"][1]["reviewer_id"] = "reviewer-a"
    with pytest.raises(ClaimReviewError, match="repeats reviewer"):
        validate_claim_review(duplicate, truth, "8" * 64)

    missing_evidence = copy.deepcopy(review)
    missing_evidence["claims"][0]["reviews"][1]["link_decisions"][0]["evidence"] = []
    with pytest.raises(ClaimReviewError, match="evidence locator"):
        validate_claim_review(missing_evidence, truth, "8" * 64)

    conflicted = copy.deepcopy(review)
    conflicted["claims"][0]["reviews"][1]["conflict_of_interest_declaration"][
        "no_personal_conflict"
    ] = False
    with pytest.raises(ClaimReviewError, match="assignment must be reassigned"):
        validate_claim_review(conflicted, truth, "8" * 64)

    wrong_scope = copy.deepcopy(review)
    wrong_scope["claims"][0]["reviews"][1]["conflict_of_interest_declaration"]["scope"][
        "artifact_id"
    ] = "different-claim"
    with pytest.raises(ClaimReviewError, match="assigned repository and artifact"):
        validate_claim_review(wrong_scope, truth, "8" * 64)

    late_conflict_declaration = copy.deepcopy(review)
    late_conflict_declaration["claims"][0]["reviews"][1][
        "conflict_of_interest_declaration"
    ]["declared_at"] = "2026-07-14T12:00:01+00:00"
    with pytest.raises(ClaimReviewError, match="after the review decision"):
        validate_claim_review(late_conflict_declaration, truth, "8" * 64)


def test_independent_claim_reviews_merge_deterministically_with_source_hashes(
    tmp_path: Path,
) -> None:
    clones = tmp_path / "clones"
    commit = _make_claim_repo(clones / "labelled")
    repos = tmp_path / "repos.csv"
    _write_inventory(repos, commit)
    (clones / "load-test").mkdir()
    truth = _claim_payload(repos, clones, commit)
    first = initialize_review(truth, "8" * 64, ["candidate-a", "candidate-b"])
    second = copy.deepcopy(first)
    first["claims"][0]["reviews"] = [_domain_review("reviewer-a")]
    second["claims"][0]["reviews"] = [_domain_review("reviewer-b")]

    merged = merge_independent_reviews([first, second], ["1" * 64, "2" * 64], truth, "8" * 64)
    reversed_merge = merge_independent_reviews(
        [second, first], ["2" * 64, "1" * 64], truth, "8" * 64
    )

    assert merged == reversed_merge
    assert merged["initial_review_sources"] == [
        {"reviewer_id": "reviewer-a", "sha256": "1" * 64},
        {"reviewer_id": "reviewer-b", "sha256": "2" * 64},
    ]
    validate_claim_review(merged, truth, "8" * 64, require_complete=True, require_accepted=True)
    verify_independent_review_sources(
        merged, [second, first], ["2" * 64, "1" * 64], truth, "8" * 64
    )

    no_provenance = copy.deepcopy(merged)
    no_provenance["initial_review_sources"] = []
    with pytest.raises(ClaimReviewError, match="merge provenance"):
        validate_claim_review(no_provenance, truth, "8" * 64, require_complete=True)


def test_claim_review_cli_requires_both_source_files_for_acceptance(tmp_path: Path) -> None:
    clones = tmp_path / "clones"
    commit = _make_claim_repo(clones / "labelled")
    repos = tmp_path / "repos.csv"
    _write_inventory(repos, commit)
    (clones / "load-test").mkdir()
    truth = _claim_payload(repos, clones, commit)
    claims = tmp_path / "claims.json"
    claims.write_text(json.dumps(truth), encoding="utf-8")
    truth_digest = sha256_file(claims)
    first = initialize_review(truth, truth_digest, ["candidate-a", "candidate-b"])
    second = copy.deepcopy(first)
    first["claims"][0]["reviews"] = [_domain_review("reviewer-a")]
    second["claims"][0]["reviews"] = [_domain_review("reviewer-b")]
    source_paths = [tmp_path / "review-a.json", tmp_path / "review-b.json"]
    for path, payload in zip(source_paths, [first, second], strict=True):
        path.write_text(json.dumps(payload), encoding="utf-8")
    merged = merge_independent_reviews(
        [first, second],
        [sha256_file(path) for path in source_paths],
        truth,
        truth_digest,
    )
    merged_path = tmp_path / "merged.json"
    merged_path.write_text(json.dumps(merged), encoding="utf-8")
    base_command = [
        sys.executable,
        str(CLAIM_REVIEW_SCRIPT),
        "validate",
        "--review",
        str(merged_path),
        "--claims",
        str(claims),
        "--require-accepted",
    ]

    missing = subprocess.run(base_command, cwd=ROOT, capture_output=True, text=True)
    complete = subprocess.run(
        [
            *base_command,
            "--initial-review",
            str(source_paths[0]),
            "--initial-review",
            str(source_paths[1]),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert missing.returncode != 0
    assert "requires exactly two --initial-review" in missing.stderr
    assert complete.returncode == 0, complete.stderr


def test_claim_review_must_precede_and_name_the_candidate_run(tmp_path: Path) -> None:
    clones = tmp_path / "clones"
    commit = _make_claim_repo(clones / "labelled")
    repos = tmp_path / "repos.csv"
    _write_inventory(repos, commit)
    (clones / "load-test").mkdir()
    truth = _claim_payload(repos, clones, commit)
    review = initialize_review(truth, "8" * 64, ["candidate-a", "candidate-b"])
    review["claims"][0]["reviews"] = [
        _domain_review("reviewer-a"),
        _domain_review("reviewer-b"),
    ]
    _bind_claim_review_sources(review)

    summary = validate_review_for_candidate_run(
        review,
        truth,
        "8" * 64,
        "candidate-a",
        "2026-07-15T00:00:00+00:00",
    )
    assert summary["accepted"] == 1

    with pytest.raises(ClaimReviewError, match="pre-registered candidate pair"):
        validate_review_for_candidate_run(
            review,
            truth,
            "8" * 64,
            "candidate-c",
            "2026-07-15T00:00:00+00:00",
        )
    with pytest.raises(ClaimReviewError, match="before human review was complete"):
        validate_review_for_candidate_run(
            review,
            truth,
            "8" * 64,
            "candidate-a",
            "2026-07-14T12:00:00+00:00",
        )


def test_claim_review_disagreement_requires_independent_adjudication(
    tmp_path: Path,
) -> None:
    clones = tmp_path / "clones"
    commit = _make_claim_repo(clones / "labelled")
    repos = tmp_path / "repos.csv"
    _write_inventory(repos, commit)
    (clones / "load-test").mkdir()
    truth = _claim_payload(repos, clones, commit)
    review = initialize_review(truth, "9" * 64, ["candidate-a", "candidate-b"])
    review["claims"][0]["reviews"] = [
        _domain_review("reviewer-a"),
        _domain_review("reviewer-b", "unclear"),
    ]
    _bind_claim_review_sources(review)

    with pytest.raises(ClaimReviewError, match="required adjudication"):
        validate_claim_review(review, truth, "9" * 64, require_complete=True)

    review["claims"][0]["adjudication"] = _claim_adjudication()
    summary = validate_claim_review(
        review, truth, "9" * 64, require_complete=True, require_accepted=True
    )
    assert summary == {"claims": 1, "completed": 1, "accepted": 1, "adjudicated": 1}

    review["claims"][0]["adjudication"]["adjudicator_id"] = "reviewer-a"
    with pytest.raises(ClaimReviewError, match="adjudicator must be independent"):
        validate_claim_review(review, truth, "9" * 64)

    review["claims"][0]["adjudication"]["adjudicator_id"] = "adjudicator-c"
    review["claims"][0]["adjudication"]["conflict_of_interest_declaration"][
        "no_relevant_authorship_or_contribution"
    ] = False
    with pytest.raises(ClaimReviewError, match="assignment must be reassigned"):
        validate_claim_review(review, truth, "9" * 64)


def test_claim_ground_truth_is_commit_pinned_exact_and_excludes_stress(tmp_path: Path) -> None:
    clones = tmp_path / "clones"
    commit = _make_claim_repo(clones / "labelled")
    repos = tmp_path / "repos.csv"
    _write_inventory(repos, commit)
    (clones / "load-test").mkdir()
    claims = tmp_path / "claims.json"
    payload = _claim_payload(repos, clones, commit)
    claims.write_text(json.dumps(payload), encoding="utf-8")

    validated = validate_ground_truth(claims, repos, clones)

    assert len(validated["claims"]) == 1
    assert {link["target"] for link in validated["claims"][0]["expected_links"]} == set(TARGETS)

    payload["claims"][0]["source"]["quote"] = "A different statement."
    claims.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ClaimGroundTruthError, match="text must occur|quote does not match"):
        validate_ground_truth(claims, repos, clones)


def test_claim_ground_truth_requires_every_link_target(tmp_path: Path) -> None:
    clones = tmp_path / "clones"
    commit = _make_claim_repo(clones / "labelled")
    repos = tmp_path / "repos.csv"
    _write_inventory(repos, commit)
    (clones / "load-test").mkdir()
    claims = tmp_path / "claims.json"
    payload = _claim_payload(repos, clones, commit)
    payload["claims"][0]["expected_links"].pop()
    claims.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ClaimGroundTruthError, match="every target exactly once"):
        validate_ground_truth(claims, repos, clones)


def test_claim_resolution_comparison_distinguishes_unknown_from_absent() -> None:
    trail = {
        "trail": [
            {"label": "metric", "value": "accuracy = 91.2", "resolved": True},
            {"label": "command", "value": "python train.py", "resolved": None},
        ]
    }

    assert _observed_resolution(trail, "reported_result")[0] == "resolved"
    assert _observed_resolution(trail, "command")[0] == "unknown"
    assert _observed_resolution(trail, "run")[0] == "absent"


def test_claim_schema_is_valid_json_and_covers_normative_targets() -> None:
    schema_path = (
        Path(__file__).resolve().parent.parent / "corpus" / "claim-ground-truth.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema_targets = schema["$defs"]["link"]["properties"]["target"]["enum"]

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert set(schema_targets) == set(TARGETS)


def test_preregistration_binds_candidate_pair_analyzer_execution_and_inputs() -> None:
    data, inputs, identity = _preregistration_fixture()

    payload = _validate_preregistration_fixture(data, inputs, identity)
    Draft202012Validator(json.loads(inputs["schema_data"])).validate(payload)

    assert payload["candidate_pair"] == ["candidate-a", "candidate-b"]
    assert payload["execution_contract"] == {
        "adduce_check_mode": "reviewer",
        "configuration_mode": "defaults-only-repository-config-disabled",
        "environment_policy": "minimal-no-host-credentials",
        "execution_mode": "offline-builtins-only",
        "input_policy": "clone-root-symlink-containment",
        "plugins_enabled": False,
        "timeout_seconds": 300,
    }
    assert payload["inputs"]["repository_count"] == 3
    assert payload["inputs"]["cohort_counts"] == {
        "badged_functional": 1,
        "stress": 1,
        "unvetted": 1,
    }


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"candidate_run_name": "candidate-c"}, "absent from"),
        ({"timeout_seconds": 301}, "execution contract differs"),
        ({"claim_ground_truth_data": b'{"claims":[{}]}\n'}, "frozen inputs differ"),
        ({"schema_data": b'{"type":"array"}\n'}, "schema SHA-256 differs"),
        (
            {
                "source_identity": {
                    "adduce_version": "0.test",
                    "adduce_source_commit": "9" * 40,
                    "adduce_source_tree_sha256": "b" * 64,
                    "builtin_rule_ids": ["R-TEST-001", "R-TEST-002"],
                    "dependency_versions": {"fixture": "1.0"},
                    "corpus_harness_git_commit": "9" * 40,
                }
            },
            "analyzer identity differs",
        ),
        (
            {
                "source_identity": {
                    "adduce_version": "0.test",
                    "adduce_source_commit": "9" * 40,
                    "adduce_source_tree_sha256": "a" * 64,
                    "builtin_rule_ids": ["R-TEST-001", "R-TEST-002"],
                    "dependency_versions": {"fixture": "2.0"},
                    "corpus_harness_git_commit": "9" * 40,
                }
            },
            "analyzer identity differs",
        ),
    ],
)
def test_preregistration_rejects_any_candidate_lock_drift(
    overrides: dict[str, Any],
    match: str,
) -> None:
    data, inputs, identity = _preregistration_fixture()

    with pytest.raises(PreregistrationError, match=match):
        _validate_preregistration_fixture(data, inputs, identity, **overrides)


def test_preregistration_rejects_analysis_plan_drift_and_extra_fields() -> None:
    data, inputs, identity = _preregistration_fixture()
    changed_inputs = dict(inputs)
    changed_plan = dict(inputs["analysis_plan_files"])
    changed_plan["PILOT_PROTOCOL.md"] += b"\nchanged after preregistration\n"
    changed_inputs["analysis_plan_files"] = changed_plan

    with pytest.raises(PreregistrationError, match="analysis plan differs"):
        _validate_preregistration_fixture(data, changed_inputs, identity)

    payload = json.loads(data)
    payload["unregistered_field"] = True
    changed_data = json.dumps(payload).encode()
    with pytest.raises(PreregistrationError, match="fields differ"):
        _validate_preregistration_fixture(changed_data, inputs, identity)


@pytest.mark.parametrize(
    "data",
    [
        b'{"field":1,"field":2}',
        b'{"field":NaN}',
        b"\xff",
    ],
)
def test_preregistration_parser_rejects_ambiguous_json(data: bytes) -> None:
    _, inputs, identity = _preregistration_fixture()

    with pytest.raises(PreregistrationError):
        _validate_preregistration_fixture(data, inputs, identity)


@pytest.mark.parametrize(
    "candidate_pair",
    [
        ["candidate-a", "candidate-a"],
        ["candidate-a", "../candidate-b"],
        ["candidate-a", ["candidate-b"]],
    ],
)
def test_preregistration_builder_rejects_unsafe_candidate_pairs(
    candidate_pair: list[Any],
) -> None:
    _, inputs, identity = _preregistration_fixture()

    with pytest.raises(PreregistrationError, match="two distinct candidate"):
        build_preregistration(
            protocol_id="fixture-r3",
            candidate_pair=candidate_pair,
            source_identity=identity,
            timeout_seconds=300,
            **inputs,
        )


def _load_retired_preregistration() -> dict[str, Any]:
    return json.loads(
        (ROOT / "corpus" / "pilot-r6-preregistration.json").read_text(encoding="utf-8")
    )


# Protocol amendment 8 retires the r6 lock and opens an unlocked development
# interval, so the assertions that pinned the analyzer digest, the analysis-plan
# file set, the schemas, the rule-ID digest, the candidate-pair names, the
# protocol ID, the analyzer version, the timeout and the cohort counts are gone
# with it. What replaces the lock is four executable assertions: the two tracked
# frozen input digests below, the gitignored study digests verified wherever the
# local corpus is present (tests/test_review_facts.py and
# tests/test_corpus_runner_hardening.py) and pinned here against the retired
# record, and the refusal of an effectiveness run that binds no live lock
# (test_effectiveness_refuses_without_a_live_preregistration_lock and its
# siblings in tests/test_corpus_tooling.py).
#
# Three of the deleted pins are enforcement rather than bookkeeping and must
# return when r7 is registered: protocol_id, adduce.version and candidate_pair.
# Pair names alone do not distinguish one lock from another, so those three are
# what would catch a lock regenerated in place, which the standing rule of
# amendment 6 forbids. They are safe to omit only while no lock exists.


def test_frozen_tracked_study_inputs_carry_their_registered_digests() -> None:
    """The two frozen inputs a fresh checkout contains, held to amendment 8."""
    assert sha256_file(ROOT / "corpus" / "repos.csv") == FROZEN_REPOS_SHA256
    assert sha256_file(ROOT / "corpus" / "badged-provenance.csv") == (
        FROZEN_BADGED_PROVENANCE_SHA256
    )


def test_the_retired_r6_record_witnesses_the_frozen_study_digests() -> None:
    """The r6 lock is retired and governs nothing.

    It is read only as a tracked witness of what the frozen digests are. Three
    of the five name gitignored study data that a fresh checkout does not
    contain, and this record is the only tracked file carrying them.
    """
    inputs = _load_retired_preregistration()["inputs"]
    assert inputs["repos_file_sha256"] == FROZEN_REPOS_SHA256
    assert inputs["badged_provenance_sha256"] == FROZEN_BADGED_PROVENANCE_SHA256
    assert inputs["claim_ground_truth_sha256"] == FROZEN_CLAIM_GROUND_TRUTH_SHA256
    assert inputs["clone_manifest_sha256"] == FROZEN_CLONE_MANIFEST_SHA256
    assert inputs["clone_snapshot_set_sha256"] == FROZEN_CLONE_SNAPSHOT_SET_SHA256


def test_review_schemas_are_valid_and_accept_generated_draft_artifacts(
    tmp_path: Path,
) -> None:
    allocation_schema = json.loads(
        (ROOT / "corpus" / "review-allocation.schema.json").read_text(encoding="utf-8")
    )
    claim_review_schema = json.loads(
        (ROOT / "corpus" / "claim-review.schema.json").read_text(encoding="utf-8")
    )
    finding_review_schema = json.loads(
        (ROOT / "corpus" / "finding-review.schema.json").read_text(encoding="utf-8")
    )
    preregistration_schema = json.loads(
        (ROOT / "corpus" / "preregistration.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(allocation_schema)
    Draft202012Validator.check_schema(claim_review_schema)
    Draft202012Validator.check_schema(finding_review_schema)
    Draft202012Validator.check_schema(preregistration_schema)
    # Against a freshly built preregistration, not the retired r6 record: the
    # schema is free to change during the unlocked interval, so validating a
    # record registered before it would turn a permitted change red.
    built_preregistration = json.loads(_preregistration_fixture()[0])
    Draft202012Validator(preregistration_schema).validate(built_preregistration)

    allocation_sources = _allocation_sources()
    allocation = build_review_allocation(
        allocation_sources,
        _allocation_run_binding(),
        seed=2,
    )
    Draft202012Validator(allocation_schema).validate(allocation)
    primary = initialize_finding_review_source(
        allocation,
        allocation_sources,
        "6" * 64,
        review_role="primary",
        reviewer_id="finding-reviewer-a",
    )
    Draft202012Validator(finding_review_schema).validate(primary)
    primary = _completed_finding_review_source(
        allocation,
        allocation_sources,
        role="primary",
        reviewer_id="finding-reviewer-a",
    )
    secondary = _completed_finding_review_source(
        allocation,
        allocation_sources,
        role="secondary",
        reviewer_id="finding-reviewer-b",
    )
    merged = merge_independent_finding_reviews(
        [primary, secondary],
        ["7" * 64, "8" * 64],
        allocation,
        allocation_sources,
        "6" * 64,
    )
    Draft202012Validator(finding_review_schema).validate(merged)

    clones = tmp_path / "clones"
    commit = _make_claim_repo(clones / "labelled")
    repos = tmp_path / "repos.csv"
    _write_inventory(repos, commit)
    (clones / "load-test").mkdir()
    truth = _claim_payload(repos, clones, commit)
    claim_review = initialize_review(truth, "a" * 64, ["candidate-a", "candidate-b"])
    Draft202012Validator(claim_review_schema).validate(claim_review)
