"""Methodology contracts for corpus sampling, review, and claim ground truth."""

from __future__ import annotations

import copy
import csv
import json
import random
import subprocess
import sys
from pathlib import Path

import pytest
from corpus.scripts.claim_ground_truth import (
    TARGETS,
    ClaimGroundTruthError,
    _observed_resolution,
    validate_ground_truth,
)
from corpus.scripts.label_findings import load, report, validate_against_run
from corpus.scripts.label_findings import validate as validate_labels
from corpus.scripts.run_contract import sha256_file
from corpus.scripts.sample_findings import (
    _filter_repositories,
    _fingerprint_set_sha256,
    _pick_repos,
    _sample_findings,
    _sampler_python_identity,
)

from tests.test_corpus_tooling import _write_minimal_valid_run

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_SCRIPT = ROOT / "corpus" / "scripts" / "sample_findings.py"
LABEL_SCRIPT = ROOT / "corpus" / "scripts" / "label_findings.py"


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
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert unbound.returncode != 0
    assert "v2 samples require --run" in unbound.stderr
    assert bound.returncode == 0, bound.stderr
    assert "sampled findings" in bound.stdout


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
