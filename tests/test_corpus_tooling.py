"""Corpus runs are immutable, attributable evidence rather than mutable scratch output."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from fnmatch import fnmatch
from pathlib import Path
from typing import NamedTuple

import corpus.scripts.run_contract as run_contract
import corpus.scripts.run_validation as run_validation
import pytest
from corpus.scripts.claim_ground_truth import TARGETS
from corpus.scripts.claim_review import initialize_review, merge_independent_reviews
from corpus.scripts.clone_repos import (
    CLONE_SCHEMA_VERSION,
    read_repos,
    repository_tree_sha256,
)
from corpus.scripts.compare_runs import compare
from corpus.scripts.label_findings import validate as validate_labels
from corpus.scripts.preregistration import build_preregistration
from corpus.scripts.run_contract import (
    BADGED_PROVENANCE_FIELDS,
    COMPLETE_MARKER,
    REQUIRED_HARNESS_PATHS,
    RUNNING_MARKER,
    RunContractError,
    artifact_records,
    ensure_new_output_directory,
    ensure_output_outside,
    finalize_run,
    finding_fingerprint,
    load_json_object_bytes,
    reject_synthetic_protocol_in_recorded_outputs,
    sha256_file,
    validate_badged_provenance_bytes,
    validate_raw_payload,
    validate_run,
    write_json,
)

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "corpus" / "scripts" / "run_validation.py"
SUMMARIZE = ROOT / "corpus" / "scripts" / "summarize.py"


def _git(*args: str, cwd: Path) -> str:
    completed = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return completed.stdout.strip()


def _write(path: Path, text: str) -> None:
    """Write LF bytes into a fixture worktree on every platform.

    ``Path.write_text`` opens in text mode, so on Windows it translates every
    ``\\n`` to ``\\r\\n``. The worktree then holds CRLF while ``git add`` under
    an ambient ``core.autocrlf=true`` stores an LF blob, and the runner, which
    audits with that config suppressed, correctly reports the clone dirty and
    refuses to scan it.
    """
    path.write_bytes(text.encode("utf-8"))


def _make_git_repo(path: Path) -> str:
    path.mkdir(parents=True)
    _write(path / "README.md", "# Fixture\n\nRun with `python train.py`.\n")
    _write(path / "train.py", "print('fixture')\n")
    _git("init", "-q", cwd=path)
    _git("config", "user.name", "Corpus Test", cwd=path)
    _git("config", "user.email", "corpus@example.invalid", cwd=path)
    _git("add", ".", cwd=path)
    _git("commit", "-qm", "fixture", cwd=path)
    _git("remote", "add", "origin", "https://example.invalid/fixture", cwd=path)
    return _git("rev-parse", "HEAD", cwd=path)


def _write_repos(path: Path, commit: str) -> list[dict[str, str]]:
    fieldnames = [
        "id",
        "cohort",
        "repo_url",
        "commit_sha",
        "badge_type",
        "venue",
        "year",
        "framework",
        "has_tex",
        "notes",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(
            {
                "id": "fixture",
                "cohort": "unvetted",
                "repo_url": "https://example.invalid/fixture",
                "commit_sha": commit,
                "framework": "python",
                "has_tex": "false",
            }
        )
    (path.parent / "badged-provenance.csv").write_text(
        ",".join(BADGED_PROVENANCE_FIELDS) + "\n",
        encoding="utf-8",
    )
    return read_repos(path)


def _write_clone_manifest(clones: Path, repos: Path, commit: str) -> None:
    clone = clones / "fixture"
    manifest = {
        "clone_schema_version": CLONE_SCHEMA_VERSION,
        "created_at": "2026-01-01T00:00:00+00:00",
        "repos_file": str(repos),
        "repos_file_sha256": sha256_file(repos),
        "clone_tool_sha256": sha256_file(ROOT / "corpus" / "scripts" / "clone_repos.py"),
        "records": [
            {
                "id": "fixture",
                "cohort": "unvetted",
                "repo_url": "https://example.invalid/fixture",
                "requested_sha": commit,
                "resolved_sha": commit,
                "status": "cloned",
                "error": None,
                "origin_url": "https://example.invalid/fixture",
                "dirty": False,
                "git_tree_sha": _git("rev-parse", "HEAD^{tree}", cwd=clone),
                "worktree_sha256": repository_tree_sha256(clone),
                "submodule_status": [],
                "submodule_state": "not_configured",
                "git_lfs_state": "no_pointers",
                "git_lfs_pointer_count": 0,
                "git_lfs_paths_sample": [],
                "acquisition_status": "complete",
            }
        ],
    }
    write_json(clones / "clones_manifest.json", manifest)


def _write_accepted_claim_review(
    claims: Path,
    review: Path,
    repos: Path,
    clones: Path,
    commit: str,
    candidate_pair: list[str],
) -> list[Path]:
    readme = clones / "fixture" / "README.md"
    quote = readme.read_text(encoding="utf-8").splitlines()[2]
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
                "rationale": "The pinned source establishes this expected relationship.",
            }
        )
    truth = {
        "claim_ground_truth_schema_version": 1,
        "corpus_inventory_sha256": sha256_file(repos),
        "clone_manifest_sha256": sha256_file(clones / "clones_manifest.json"),
        "frozen_at": "2026-07-14T00:00:00+00:00",
        "claims": [
            {
                "claim_id": "fixture-command",
                "repo_id": "fixture",
                "repo_commit": commit,
                "source": {
                    "kind": "repository_file",
                    "path": "README.md",
                    "sha256": sha256_file(readme),
                    "line_start": 3,
                    "line_end": 3,
                    "quote": quote,
                },
                "claim": {
                    "text": "python train.py",
                    "metric": "documented command",
                    "value": "python train.py",
                },
                "adduce_match": {"headline_contains": "python train.py"},
                "expected_trail_status": "supported",
                "expected_links": links,
                "ground_truth_review": {
                    "prepared_by": "preparer-a",
                    "prepared_at": "2026-07-13T22:00:00+00:00",
                    "verified_by": "verifier-b",
                    "verified_at": "2026-07-13T23:00:00+00:00",
                },
            }
        ],
        "unavailable_repositories": [],
    }
    write_json(claims, truth)
    scaffold = initialize_review(truth, sha256_file(claims), candidate_pair)
    initial_reviews = []
    for reviewer_id, hour in (("domain-reviewer-a", 10), ("domain-reviewer-b", 11)):
        initial_reviews.append(
            {
                "reviewer_id": reviewer_id,
                "domain_expertise": "Research-artifact evaluation",
                "reviewed_at": f"2026-07-15T{hour:02d}:00:00+00:00",
                "blinding_declaration": {
                    "independent_review": True,
                    "other_reviewer_decisions_not_seen": True,
                    "adduce_claim_link_outputs_not_seen": True,
                    "declared_at": f"2026-07-15T{hour - 1:02d}:30:00+00:00",
                },
                "conflict_of_interest_declaration": {
                    "scope": {
                        "repository_id": "fixture",
                        "artifact_id": "fixture-command",
                    },
                    "no_relevant_authorship_or_contribution": True,
                    "no_close_collaboration_supervision_or_employment": True,
                    "no_financial_conflict": True,
                    "no_personal_conflict": True,
                    "declared_at": f"2026-07-15T{hour - 1:02d}:30:00+00:00",
                },
                "claim_decision": "verified",
                "claim_rationale": "The claim is present in the pinned source.",
                "claim_evidence": ["README.md:3"],
                "link_decisions": [
                    {
                        "target": target,
                        "decision": "verified",
                        "rationale": f"The pinned evidence supports the {target} mapping.",
                        "evidence": ["README.md:3"],
                    }
                    for target in TARGETS
                ],
            }
        )
    source_paths = [
        review.with_name("claim-review-reviewer-a.json"),
        review.with_name("claim-review-reviewer-b.json"),
    ]
    source_payloads = []
    for source_path, initial_review in zip(source_paths, initial_reviews, strict=True):
        source_payload = json.loads(json.dumps(scaffold))
        source_payload["claims"][0]["reviews"] = [initial_review]
        write_json(source_path, source_payload)
        source_payloads.append(source_payload)
    payload = merge_independent_reviews(
        source_payloads,
        [sha256_file(path) for path in source_paths],
        truth,
        sha256_file(claims),
    )
    write_json(review, payload)
    return source_paths


def _evidence_base(**overrides: object) -> dict[str, object]:
    """The minimal fixture's evidence base, with individual fields replaced.

    Rated with a numeric total, so the contract still recomputes the tier from
    the score. ``considered_rules`` must equal the number of findings in the
    payload, and the rule census must partition it exactly.
    """
    base: dict[str, object] = {
        "rated": True,
        "evaluated_rules": 1,
        "considered_rules": 1,
        "applicable_rules": 1,
        "coverage_percent": 100.0,
        "analysable_lines": 500,
        "rules": {
            "assessed": 1,
            "unknown": 0,
            "not_applicable": 0,
            "skipped_inapplicable": 0,
        },
    }
    base.update(overrides)
    return base


def _write_minimal_valid_run(path: Path, *, run_id: str = "test-run") -> None:
    ensure_new_output_directory(path)
    (path / "raw_json").mkdir()
    (path / "inputs").mkdir()
    (path / "harness").mkdir()
    payload = {
        "tool": {"name": "adduce", "version": "0.test"},
        "schema": {"name": "adduce-report", "version": 1},
        "repository": {
            "root": str(path / "checkout"),
            "commit": "a" * 40,
            "frameworks": ["python"],
            "files_scanned": 2,
            "input_file_count": 2,
            "input_byte_count": 42,
        },
        "configuration": {
            "source": None,
            "repository_policy_honored": False,
            "profile": "default",
            "ignored_rules": [],
            "excluded_paths": [],
        },
        "reviewer_time": {
            "low_minutes": 1,
            "high_minutes": 2,
            "bucket": "1-2 minutes",
            "unknown": False,
            "factors": [],
        },
        "claims": [],
        "total": 0.0,
        "tier": "Needs work",
        "profile": "default",
        "evidence_base": _evidence_base(),
        "categories": [
            {
                "category": "Documentation",
                "earned": 0.0,
                "possible": 1.0,
                "percentage": 0.0,
            }
        ],
        "findings": [
            {
                "rule_id": "R-TEST-001",
                "category": "Documentation",
                "title": "Test finding",
                "status": "fail",
                "confidence": 1.0,
                "severity": "medium",
                "message": "fixture",
                "remediation": "fixture",
                "weight": 1,
                "locations": [],
                "fix_command": None,
                "suppressed": False,
                "items": [],
            }
        ],
        "corpus_execution": {
            "adduce_check_mode": "reviewer",
            "configuration_mode": "defaults-only-repository-config-disabled",
            "plugins_enabled": False,
            "repository_policy_honored": False,
            "network_policy": "python-audit-socket-deny",
            "process_policy": "python-audit-read-only-git-metadata-only",
            "enforcement_scope": "scanner-regression-guard-not-os-sandbox",
            "environment_policy": "minimal-no-host-credentials",
            "input_policy": "clone-root-symlink-containment",
            "adduce_source_tree_sha256": "b" * 64,
            "peak_rss": {
                "available": False,
                "value": None,
                "unit": "unavailable",
                "source": "unavailable",
                "platform": "test",
            },
        },
    }
    write_json(path / "raw_json" / "repo.json", payload)
    (path / "inputs" / "repos.csv").write_text(
        f"id,cohort,repo_url,commit_sha\nrepo,unvetted,https://example.invalid/repo,{'a' * 40}\n",
        encoding="utf-8",
    )
    harness_bytes = {
        name: (
            (",".join(BADGED_PROVENANCE_FIELDS) + "\n").encode()
            if name == "badged-provenance.csv"
            else (ROOT / "corpus" / name).read_bytes()
        )
        for name in REQUIRED_HARNESS_PATHS
    }
    harness_files = {name: hashlib.sha256(data).hexdigest() for name, data in harness_bytes.items()}
    for name, data in harness_bytes.items():
        target = path / "harness" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    write_json(
        path / "inputs" / "clones_manifest.json",
        {
            "clone_schema_version": CLONE_SCHEMA_VERSION,
            "created_at": "2026-01-01T00:00:00+00:00",
            "repos_file": "repos.csv",
            "repos_file_sha256": sha256_file(path / "inputs" / "repos.csv"),
            "clone_tool_sha256": harness_files["scripts/clone_repos.py"],
            "records": [
                {
                    "id": "repo",
                    "cohort": "unvetted",
                    "repo_url": "https://example.invalid/repo",
                    "requested_sha": "a" * 40,
                    "status": "cloned",
                    "resolved_sha": "a" * 40,
                    "error": None,
                    "origin_url": "https://example.invalid/repo",
                    "dirty": False,
                    "git_tree_sha": "c" * 40,
                    "worktree_sha256": "d" * 64,
                    "submodule_status": [],
                    "submodule_state": "not_configured",
                    "git_lfs_state": "no_pointers",
                    "git_lfs_pointer_count": 0,
                    "git_lfs_paths_sample": [],
                    "acquisition_status": "complete",
                }
            ],
        },
    )
    (path / "combined.csv").write_text(
        "id,cohort,badge_type,repo_url,requested_sha,resolved_sha,worktree_sha256,"
        "repository_tree_sha256,clone_status,acquisition_status,submodule_state,"
        "input_file_count,input_byte_count,"
        "git_lfs_state,git_lfs_pointer_count,run_status,acquisition_failed,score,tier,"
        "reviewer_time_bucket,findings_fail,findings_partial,crash,timeout,runtime_seconds,"
        "peak_rss_value,peak_rss_unit,peak_rss_source,"
        "error,cat_documentation\n"
        f"repo,unvetted,,https://example.invalid/repo,{'a' * 40},{'a' * 40},"
        f"{'d' * 64},{'d' * 64},cloned,complete,not_configured,2,42,no_pointers,0,succeeded,"
        "False,0.0,Needs work,1-2 minutes,1,0,False,False,0.1,,unavailable,unavailable,,0.0\n",
        encoding="utf-8",
    )
    artifacts = artifact_records(
        path,
        [
            "combined.csv",
            "inputs/repos.csv",
            "inputs/clones_manifest.json",
            "raw_json/repo.json",
            *[f"harness/{name}" for name in REQUIRED_HARNESS_PATHS],
        ],
    )
    harness_digest = hashlib.sha256()
    for name, digest in sorted(harness_files.items()):
        harness_digest.update(name.encode())
        harness_digest.update(digest.encode())
    finalize_run(
        path,
        {
            "run_id": run_id,
            "adduce_version": "0.test",
            "adduce_source_tree_sha256": "b" * 64,
            "execution_mode": "offline-builtins-only",
            "environment_policy": "minimal-no-host-credentials",
            "input_policy": "clone-root-symlink-containment",
            "analysis_scope": "operational-only",
            "adduce_check_mode": "reviewer",
            "configuration_mode": "defaults-only-repository-config-disabled",
            "started_at": "2026-01-01T00:00:00+00:00",
            "completed_at": "2026-01-01T00:00:01+00:00",
            "repos_file_sha256": sha256_file(path / "inputs" / "repos.csv"),
            "clone_manifest_sha256": sha256_file(path / "inputs" / "clones_manifest.json"),
            "clone_tool_sha256": harness_files["scripts/clone_repos.py"],
            "clone_tool_sha256_matches_live": True,
            "builtin_rule_ids": ["R-TEST-001"],
            "builtin_rule_count": 1,
            "python": {"version": "3.test", "implementation": "test"},
            "platform": "test-platform",
            "runtime_context": {
                "logical_cpu": {
                    "available": True,
                    "value": 2,
                    "unit": "count",
                    "source": "os.cpu_count",
                },
                "physical_memory": {
                    "available": False,
                    "value": None,
                    "unit": "unavailable",
                    "source": "unavailable",
                },
                "cache_policy": {
                    "filesystem_cache": "not-cleared",
                    "scanner_process": "fresh-process-per-repository",
                    "adduce_application_cache": "disabled-default-offline-path",
                },
                "peak_rss_platform": "test",
                "input_measurement_policy": "adduce-scanned-regular-files-summed-by-reported-size",
            },
            "dependency_versions": {"fixture": "1.0"},
            "corpus_harness_files": harness_files,
            "corpus_harness_sha256": harness_digest.hexdigest(),
            "invocation": {"timeout_seconds": 1},
            "timeout_seconds": 1,
            "n_repositories": 1,
            "n_acquisition_failed": 0,
            "n_acquisition_partial": 0,
            "n_succeeded": 1,
            "n_crashed": 0,
            "n_scanner_crashed": 0,
            "n_contract_failed": 0,
            "artifacts": artifacts,
        },
    )


def _rehash_completed_run(run: Path, changed_artifact: str | None = None) -> None:
    """Update integrity hashes so a test can exercise semantic validation."""
    metadata_path = run / "run_meta.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if changed_artifact is not None:
        for record in metadata["artifacts"]:
            if record["path"] == changed_artifact:
                record["sha256"] = sha256_file(run / changed_artifact)
                break
        else:  # pragma: no cover - test helper misuse
            raise AssertionError(f"unrecorded fixture artifact: {changed_artifact}")
        if changed_artifact == "inputs/clones_manifest.json":
            metadata["clone_manifest_sha256"] = sha256_file(run / changed_artifact)
        elif changed_artifact == "inputs/repos.csv":
            metadata["repos_file_sha256"] = sha256_file(run / changed_artifact)
        elif changed_artifact == "inputs/claim_review.json":
            metadata["claim_review_sha256"] = sha256_file(run / changed_artifact)
        elif changed_artifact == "inputs/preregistration.json":
            metadata["preregistration_sha256"] = sha256_file(run / changed_artifact)
    write_json(metadata_path, metadata)
    (run / COMPLETE_MARKER).write_text(
        sha256_file(metadata_path) + "\n", encoding="utf-8", newline="\n"
    )


def test_completed_run_validates_and_is_explicitly_finalized(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_minimal_valid_run(run)

    metadata = validate_run(run)

    assert metadata["run_id"] == "test-run"
    assert (run / COMPLETE_MARKER).is_file()
    assert not (run / RUNNING_MARKER).exists()


def test_identical_valid_runs_compare_as_deterministic(tmp_path: Path) -> None:
    run_a = tmp_path / "run-a"
    run_b = tmp_path / "run-b"
    _write_minimal_valid_run(run_a, run_id="test-run-a")
    _write_minimal_valid_run(run_b, run_id="test-run-b")

    report = compare(run_a, run_b)

    assert report["comparable"] is True
    assert report["deterministic"] is True
    assert report["identity_differences"] == []
    assert report["output_differences"] == []


def test_run_validation_rejects_tampering(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_minimal_valid_run(run)
    with (run / "combined.csv").open("a", encoding="utf-8") as handle:
        handle.write("tampered,stress,,True,False\n")

    with pytest.raises(RunContractError, match="checksum mismatch"):
        validate_run(run)


def test_completion_marker_is_written_only_after_contract_validation(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_minimal_valid_run(run)
    metadata = json.loads((run / "run_meta.json").read_text(encoding="utf-8"))
    metadata.pop("complete")
    metadata.pop("run_schema_version")
    (run / COMPLETE_MARKER).replace(run / RUNNING_MARKER)
    (run / "run_meta.json").unlink()
    (run / "combined.csv").write_text("invalid\n", encoding="utf-8")

    with pytest.raises(RunContractError):
        finalize_run(run, metadata)

    assert (run / RUNNING_MARKER).is_file()
    assert not (run / COMPLETE_MARKER).exists()


def test_finalize_run_refuses_a_clone_tool_digest_disagreeing_with_the_copied_manifest(
    tmp_path: Path,
) -> None:
    """metadata["clone_tool_sha256"] and the copied manifest are independent records;
    a run whose two copies disagree must still be refused, even though neither side
    is compared against the live clone harness any more."""
    run = tmp_path / "run"
    _write_minimal_valid_run(run)
    metadata = json.loads((run / "run_meta.json").read_text(encoding="utf-8"))
    metadata.pop("complete")
    metadata.pop("run_schema_version")
    manifest = json.loads((run / "inputs" / "clones_manifest.json").read_text(encoding="utf-8"))
    assert metadata["clone_tool_sha256"] == manifest["clone_tool_sha256"]
    metadata["clone_tool_sha256"] = "f" * 64
    # Kept internally consistent with the mutation above (both sides genuinely
    # disagree with corpus_harness_files here) so this exercises the copied-manifest
    # check below, not the metadata self-consistency check that would otherwise
    # intercept an honestly-disclosed disagreement first.
    metadata["clone_tool_sha256_matches_live"] = False
    (run / COMPLETE_MARKER).replace(run / RUNNING_MARKER)
    (run / "run_meta.json").unlink()

    with pytest.raises(RunContractError, match="different clone harness"):
        finalize_run(run, metadata)


def test_finalize_run_refuses_a_malformed_clone_tool_digest_in_run_metadata(
    tmp_path: Path,
) -> None:
    run = tmp_path / "run"
    _write_minimal_valid_run(run)
    metadata = json.loads((run / "run_meta.json").read_text(encoding="utf-8"))
    metadata.pop("complete")
    metadata.pop("run_schema_version")
    metadata["clone_tool_sha256"] = "g" * 64  # present, but not valid hex
    # Keeps the pair present and its disclosure honest (the malformed digest
    # genuinely differs from the live harness snapshot too), so this exercises the
    # well-formedness check below rather than the metadata self-consistency check.
    metadata["clone_tool_sha256_matches_live"] = False
    (run / COMPLETE_MARKER).replace(run / RUNNING_MARKER)
    (run / "run_meta.json").unlink()

    with pytest.raises(RunContractError, match="well-formed clone-tool digest"):
        finalize_run(run, metadata)


def test_validate_run_still_refuses_a_missing_clone_tool_digest_in_the_copied_manifest(
    tmp_path: Path,
) -> None:
    """Preserved: _validate_clone_manifest's own check on the copied file is untouched."""
    run = tmp_path / "run"
    _write_minimal_valid_run(run)
    manifest_path = run / "inputs" / "clones_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["clone_tool_sha256"]
    write_json(manifest_path, manifest)
    _rehash_completed_run(run, "inputs/clones_manifest.json")

    with pytest.raises(RunContractError, match="different clone harness"):
        validate_run(run)


def test_validate_run_accepts_a_pre_field_run_meta_missing_both_clone_tool_fields(
    tmp_path: Path,
) -> None:
    """Shaped like the real pilot-0.1.2dev0-ops-{a,b} runs: predates both fields entirely.

    clone_tool_sha256 and clone_tool_sha256_matches_live are required present-together,
    not each unconditionally required, precisely so an artifact produced before either
    field existed still validates as it did at that HEAD.
    """
    run = tmp_path / "run"
    _write_minimal_valid_run(run)
    metadata_path = run / "run_meta.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    del metadata["clone_tool_sha256"]
    del metadata["clone_tool_sha256_matches_live"]
    write_json(metadata_path, metadata)
    (run / COMPLETE_MARKER).write_text(
        sha256_file(metadata_path) + "\n", encoding="utf-8", newline="\n"
    )

    result = validate_run(run)

    assert "clone_tool_sha256" not in result
    assert "clone_tool_sha256_matches_live" not in result


def test_finalize_run_refuses_a_clone_tool_digest_without_its_live_agreement_disclosure(
    tmp_path: Path,
) -> None:
    run = tmp_path / "run"
    _write_minimal_valid_run(run)
    metadata = json.loads((run / "run_meta.json").read_text(encoding="utf-8"))
    metadata.pop("complete")
    metadata.pop("run_schema_version")
    del metadata["clone_tool_sha256_matches_live"]
    (run / COMPLETE_MARKER).replace(run / RUNNING_MARKER)
    (run / "run_meta.json").unlink()

    with pytest.raises(RunContractError, match="must both be present or both be absent"):
        finalize_run(run, metadata)


def test_finalize_run_refuses_a_live_agreement_disclosure_without_a_clone_tool_digest(
    tmp_path: Path,
) -> None:
    run = tmp_path / "run"
    _write_minimal_valid_run(run)
    metadata = json.loads((run / "run_meta.json").read_text(encoding="utf-8"))
    metadata.pop("complete")
    metadata.pop("run_schema_version")
    del metadata["clone_tool_sha256"]
    (run / COMPLETE_MARKER).replace(run / RUNNING_MARKER)
    (run / "run_meta.json").unlink()

    with pytest.raises(RunContractError, match="must both be present or both be absent"):
        finalize_run(run, metadata)


@pytest.mark.parametrize("bad_value", [1, 0, "false"], ids=["1-for-True", "0-for-False", "string"])
def test_finalize_run_refuses_a_non_bool_clone_tool_live_agreement_disclosure(
    tmp_path: Path, bad_value: object
) -> None:
    """Python's bool is an int subclass, so a naive truthiness check would accept 1 or 0 here."""
    run = tmp_path / "run"
    _write_minimal_valid_run(run)
    metadata = json.loads((run / "run_meta.json").read_text(encoding="utf-8"))
    metadata.pop("complete")
    metadata.pop("run_schema_version")
    metadata["clone_tool_sha256_matches_live"] = bad_value
    (run / COMPLETE_MARKER).replace(run / RUNNING_MARKER)
    (run / "run_meta.json").unlink()

    with pytest.raises(RunContractError, match="invalid clone-tool live-agreement evidence"):
        finalize_run(run, metadata)


def _disagree_clone_tool_digest(run: Path) -> None:
    """Make clone_tool_sha256 genuinely differ from corpus_harness_files, consistently."""
    manifest_path = run / "inputs" / "clones_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["clone_tool_sha256"] = "e" * 64
    write_json(manifest_path, manifest)
    metadata_path = run / "run_meta.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["corpus_harness_files"]["scripts/clone_repos.py"] != "e" * 64
    metadata["clone_tool_sha256"] = "e" * 64
    for record in metadata["artifacts"]:
        if record["path"] == "inputs/clones_manifest.json":
            record["sha256"] = sha256_file(manifest_path)
    metadata["clone_manifest_sha256"] = sha256_file(manifest_path)
    write_json(metadata_path, metadata)


def test_validate_run_refuses_a_clone_tool_live_agreement_disclosure_that_lies(
    tmp_path: Path,
) -> None:
    """QA's exact demonstration: a genuine disagreement, dishonestly disclosed as True."""
    run = tmp_path / "run"
    _write_minimal_valid_run(run)
    _disagree_clone_tool_digest(run)
    metadata_path = run / "run_meta.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["clone_tool_sha256_matches_live"] = True  # the lie
    write_json(metadata_path, metadata)
    (run / COMPLETE_MARKER).write_text(
        sha256_file(metadata_path) + "\n", encoding="utf-8", newline="\n"
    )

    with pytest.raises(RunContractError, match="disagrees with its own digests"):
        validate_run(run)


def test_validate_run_accepts_a_genuine_disagreement_honestly_disclosed_as_false(
    tmp_path: Path,
) -> None:
    """A real disagreement, correctly disclosed as False, is never refused on that account."""
    run = tmp_path / "run"
    _write_minimal_valid_run(run)
    _disagree_clone_tool_digest(run)
    metadata_path = run / "run_meta.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["clone_tool_sha256_matches_live"] = False  # honest
    write_json(metadata_path, metadata)
    (run / COMPLETE_MARKER).write_text(
        sha256_file(metadata_path) + "\n", encoding="utf-8", newline="\n"
    )

    result = validate_run(run)

    assert result["clone_tool_sha256_matches_live"] is False


@pytest.mark.parametrize(
    "replacement,match",
    [
        (
            ",false,False,0.1,,unavailable,unavailable,,0.0\n",
            "exactly True or False",
        ),
        (
            ",banana,False,0.1,,unavailable,unavailable,,0.0\n",
            "exactly True or False",
        ),
    ],
)
def test_combined_boolean_fields_are_strict(tmp_path: Path, replacement: str, match: str) -> None:
    run = tmp_path / "run"
    _write_minimal_valid_run(run)
    combined = run / "combined.csv"
    text = combined.read_text(encoding="utf-8")
    text = text.replace(",False,False,0.1,,unavailable,unavailable,,0.0\n", replacement)
    combined.write_text(text, encoding="utf-8")
    _rehash_completed_run(run, "combined.csv")

    with pytest.raises(RunContractError, match=match):
        validate_run(run)


def test_combined_summary_must_reconcile_with_raw_json(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_minimal_valid_run(run)
    combined = run / "combined.csv"
    combined.write_text(
        combined.read_text(encoding="utf-8").replace(",0.0,Needs work,", ",99.0,Needs work,"),
        encoding="utf-8",
    )
    _rehash_completed_run(run, "combined.csv")

    with pytest.raises(RunContractError, match="score disagrees"):
        validate_run(run)


def test_run_rejects_unmanifested_files_and_symlinked_artifacts(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_minimal_valid_run(run)
    (run / "notes.txt").write_text("not evidence\n", encoding="utf-8")
    with pytest.raises(RunContractError, match="file set mismatch"):
        validate_run(run)

    (run / "notes.txt").unlink()
    raw = run / "raw_json" / "repo.json"
    external = tmp_path / "external.json"
    raw.replace(external)
    raw.symlink_to(external)
    with pytest.raises(RunContractError, match="symlink"):
        validate_run(run)


def test_manifest_cannot_authorize_an_unsupported_artifact(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_minimal_valid_run(run)
    extra = run / "analysis-notes.json"
    extra.write_text("{}\n", encoding="utf-8")
    metadata_path = run / "run_meta.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["artifacts"].append({"path": extra.name, "sha256": sha256_file(extra)})
    metadata["artifacts"].sort(key=lambda record: record["path"])
    write_json(metadata_path, metadata)
    (run / COMPLETE_MARKER).write_text(
        sha256_file(metadata_path) + "\n", encoding="utf-8", newline="\n"
    )

    with pytest.raises(RunContractError, match="unsupported artifacts"):
        validate_run(run)


@pytest.mark.parametrize(
    "data",
    [b'{"value": 1, "value": 2}', b'{"value": NaN}', b'{"value": Infinity}'],
)
def test_strict_json_rejects_duplicate_keys_and_nonfinite_numbers(data: bytes) -> None:
    with pytest.raises(RunContractError):
        load_json_object_bytes(data, "fixture")


def test_raw_rule_census_cannot_silently_omit_a_builtin(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_minimal_valid_run(run)
    raw_path = run / "raw_json" / "repo.json"
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    payload["findings"] = []
    write_json(raw_path, payload)
    _rehash_completed_run(run, "raw_json/repo.json")

    with pytest.raises(RunContractError, match="rule census is incomplete"):
        validate_run(run)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("adduce_check_mode", "author"),
        ("repository_policy_honored", True),
    ],
)
def test_raw_corpus_scan_must_use_reviewer_policy_isolation(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    run = tmp_path / "run"
    _write_minimal_valid_run(run)
    raw_path = run / "raw_json" / "repo.json"
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    payload["corpus_execution"][field] = value
    write_json(raw_path, payload)
    _rehash_completed_run(run, "raw_json/repo.json")

    with pytest.raises(RunContractError, match="execution policy is invalid"):
        validate_run(run)


def test_raw_score_must_be_recomputable_from_findings(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_minimal_valid_run(run)
    raw_path = run / "raw_json" / "repo.json"
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    payload["total"] = 50.0
    payload["tier"] = "Bronze"
    write_json(raw_path, payload)
    _rehash_completed_run(run, "raw_json/repo.json")

    with pytest.raises(RunContractError, match="total score is not supported"):
        validate_run(run)


def _finding_item(**overrides: object) -> dict[str, object]:
    item: dict[str, object] = {
        "id": "citation:10.1234/a",
        "status": "fail",
        "message": "The cited work is not in the bibliography.",
        "confidence": 0.5,
        "locations": [{"path": "paper.tex", "line": 12}],
        "remediation": "Add the reference.",
        "kind": "citation",
        "attributes": {"doi": "10.1234/a", "page": 4, "quoted": True, "note": None},
    }
    item.update(overrides)
    return item


def _reseal_raw_payload(run: Path, payload: dict) -> None:
    """Rewrite the run's raw JSON and re-seal the integrity hashes over it."""
    write_json(run / "raw_json" / "repo.json", payload)
    _rehash_completed_run(run, "raw_json/repo.json")


def _read_raw_payload(run: Path) -> dict:
    return json.loads((run / "raw_json" / "repo.json").read_text(encoding="utf-8"))


def _validate_fixture_payload(payload: dict) -> None:
    """Validate a payload against the minimal fixture's declared run identity.

    Called directly where the strict JSON loader would reject the input before
    the payload validator ever saw it, so the payload validator's own guard is
    what the test exercises.
    """
    validate_raw_payload(payload, "repo", "a" * 40, "0.test", "b" * 64, "test", {"R-TEST-001"})


def test_raw_finding_items_are_accepted_and_never_reach_the_score(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_minimal_valid_run(run)
    payload = _read_raw_payload(run)
    payload["findings"][0]["items"] = [
        _finding_item(id=f"citation:10.1234/{index}", status="pass" if index else "fail")
        for index in range(64)
    ]
    _reseal_raw_payload(run, payload)

    validate_run(run)

    with (run / "combined.csv").open(newline="", encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))
    assert row["score"] == "0.0"


@pytest.mark.parametrize(
    ("items", "match"),
    [
        pytest.param(
            [_finding_item(kind="citation", extra="surprise")],
            r"finding item for repo fields are invalid \(missing=\[\], extra=\['extra'\]\)",
            id="unknown-key",
        ),
        pytest.param(
            [_finding_item(), _finding_item(status="pass")],
            "finding item id 'citation:10.1234/a' is duplicated",
            id="duplicate-id",
        ),
        pytest.param(
            [_finding_item(attributes={"trail": {"nested": 1}})],
            "attribute 'trail' is invalid",
            id="nested-attribute",
        ),
        pytest.param(
            [_finding_item(attributes={"pages": [1, 2]})],
            "attribute 'pages' is invalid",
            id="list-attribute",
        ),
        pytest.param(
            [_finding_item(attributes=[])],
            "attributes are invalid",
            id="attributes-not-an-object",
        ),
        pytest.param(
            [_finding_item(id="")],
            "finding item id is invalid",
            id="empty-id",
        ),
        pytest.param(
            [_finding_item(status="skipped")],
            "status is invalid",
            id="unknown-status",
        ),
        pytest.param(
            [_finding_item(confidence=1.5)],
            "confidence is invalid",
            id="confidence-out-of-range",
        ),
        pytest.param(
            [_finding_item(confidence="high")],
            "confidence for repo must be numeric",
            id="confidence-not-a-number",
        ),
        pytest.param(
            [_finding_item(message=None)],
            "message is invalid",
            id="message-not-a-string",
        ),
        pytest.param(
            [_finding_item(remediation=None)],
            "remediation is invalid",
            id="remediation-not-a-string",
        ),
        pytest.param(
            [_finding_item(kind=7)],
            "kind is invalid",
            id="kind-not-a-string",
        ),
        pytest.param(
            [_finding_item(locations=[{"path": "../escape.tex", "line": 1}])],
            "unsafe run artifact path",
            id="escaping-location",
        ),
        pytest.param(
            [_finding_item(locations=[{"path": "paper.tex", "line": 0}])],
            "location line is invalid",
            id="non-positive-location-line",
        ),
        pytest.param(
            [_finding_item(locations=[{"path": "paper.tex"}])],
            "location for repo fields are invalid",
            id="partial-location",
        ),
        pytest.param(["citation:10.1234/a"], "finding item is invalid", id="item-not-an-object"),
        pytest.param({}, "finding items are invalid", id="items-not-a-list"),
    ],
)
def test_raw_finding_items_are_content_checked_not_merely_admitted(
    tmp_path: Path, items: object, match: str
) -> None:
    """Every item field is validated, so admitting the key cannot fail open."""
    run = tmp_path / "run"
    _write_minimal_valid_run(run)
    payload = _read_raw_payload(run)
    payload["findings"][0]["items"] = items
    _reseal_raw_payload(run, payload)

    with pytest.raises(RunContractError, match=match):
        validate_run(run)


@pytest.mark.parametrize(
    ("locations", "match"),
    [
        pytest.param(
            [{"path": "../escape.py", "line": 1}], "unsafe run artifact path", id="escaping"
        ),
        pytest.param([{"path": "train.py", "line": 0}], "location line is invalid", id="zero-line"),
        pytest.param([{"path": "train.py"}], "location for repo fields", id="partial"),
        pytest.param({}, "finding locations are invalid", id="not-a-list"),
    ],
)
def test_a_findings_own_locations_are_still_validated(
    tmp_path: Path, locations: object, match: str
) -> None:
    """The finding and its items share one location validator; both call it."""
    run = tmp_path / "run"
    _write_minimal_valid_run(run)
    payload = _read_raw_payload(run)
    payload["findings"][0]["locations"] = locations
    _reseal_raw_payload(run, payload)

    with pytest.raises(RunContractError, match=match):
        validate_run(run)


def test_raw_finding_cannot_omit_its_items_array(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_minimal_valid_run(run)
    payload = _read_raw_payload(run)
    del payload["findings"][0]["items"]
    _reseal_raw_payload(run, payload)

    with pytest.raises(RunContractError, match=r"finding for repo fields are invalid.*'items'"):
        validate_run(run)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_raw_finding_item_attribute_must_be_a_finite_number(
    tmp_path: Path, value: float
) -> None:
    """Validated on the live payload, because no artifact can carry these bytes.

    ``json.dumps`` writes bare ``NaN`` and ``Infinity``, which is not JSON, so
    the strict writer refuses to record them and the strict loader refuses to
    read them back. The payload validator refuses them too, so the runner
    holding a scanner's dict in memory is not the only thing between a
    non-finite float and a recorded artifact.
    """
    run = tmp_path / "run"
    _write_minimal_valid_run(run)
    payload = _read_raw_payload(run)
    payload["findings"][0]["items"] = [_finding_item(attributes={"delta": value})]

    with pytest.raises(RunContractError, match="attribute 'delta' is invalid"):
        _validate_fixture_payload(payload)


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param({"name": "adduce-report", "version": 2}, id="future-version"),
        pytest.param({"name": "adduce-report", "version": "1"}, id="stringified-version"),
        pytest.param({"name": "adduce-report", "version": True}, id="boolean-version"),
        pytest.param({"name": "adduce", "version": 1}, id="wrong-name"),
        pytest.param({"version": 1}, id="missing-name"),
        pytest.param("adduce-report-1", id="not-an-object"),
    ],
)
def test_raw_report_schema_identity_is_pinned(tmp_path: Path, schema: object) -> None:
    run = tmp_path / "run"
    _write_minimal_valid_run(run)
    payload = _read_raw_payload(run)
    payload["schema"] = schema
    _reseal_raw_payload(run, payload)

    with pytest.raises(RunContractError, match="report schema mismatch"):
        validate_run(run)


def test_raw_payload_cannot_omit_the_report_schema(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_minimal_valid_run(run)
    payload = _read_raw_payload(run)
    del payload["schema"]
    _reseal_raw_payload(run, payload)

    with pytest.raises(RunContractError, match=r"object for repo fields are invalid.*'schema'"):
        validate_run(run)


def test_the_json_reporter_stamps_the_schema_the_contract_pins(tmp_path: Path) -> None:
    """The shape the contract requires is the shape a real report carries."""
    from adduce.engine import run_check
    from adduce.report import json_report

    _make_git_repo(tmp_path / "repo")
    payload = json.loads(json_report.render(run_check(tmp_path / "repo")))

    assert payload["schema"] == {"name": "adduce-report", "version": 1}
    assert payload["schema"]["version"] != payload["tool"]["version"]


def _write_unassessed_run(path: Path) -> None:
    """A valid run whose only rule applied and returned no answer.

    Nothing reached an assessment, so the raw JSON carries a null total and the
    tier that says so, its one category is reported with an empty score, and the
    combined row leaves both the score and that category empty rather than
    writing a zero.
    """
    _write_minimal_valid_run(path)
    raw_path = path / "raw_json" / "repo.json"
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    payload["total"] = None
    payload["tier"] = "Unrated (nothing assessed)"
    payload["findings"][0]["status"] = "unknown"
    payload["categories"] = [
        {"category": "Documentation", "earned": 0.0, "possible": 0.0, "percentage": 0.0}
    ]
    payload["evidence_base"] = _evidence_base(
        evaluated_rules=0,
        coverage_percent=0.0,
        rules={"assessed": 0, "unknown": 1, "not_applicable": 0, "skipped_inapplicable": 0},
    )
    write_json(raw_path, payload)
    combined = path / "combined.csv"
    text = combined.read_text(encoding="utf-8")
    text = text.replace(
        ",0.0,Needs work,1-2 minutes,1,0,",
        ",,Unrated (nothing assessed),1-2 minutes,0,0,",
    )
    text = text.replace("unavailable,unavailable,,0.0\n", "unavailable,unavailable,,\n")
    combined.write_text(text, encoding="utf-8")
    _rehash_completed_run(path, "raw_json/repo.json")
    _rehash_completed_run(path, "combined.csv")


def _write_partly_unassessed_run(path: Path) -> None:
    """A valid run that scored one category and could not assess another.

    The unassessed category is reported with an empty score rather than dropped,
    so the unanswered question survives into the artifact. Its weight stays out
    of the total, which is therefore unmoved, and its combined cell is empty
    beside a scored category that genuinely earned zero.
    """
    _write_minimal_valid_run(path)
    raw_path = path / "raw_json" / "repo.json"
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    payload["findings"].append(
        {
            **payload["findings"][0],
            "rule_id": "R-TEST-002",
            "category": "Determinism & Seeds",
            "status": "unknown",
        }
    )
    payload["categories"].append(
        {"category": "Determinism & Seeds", "earned": 0.0, "possible": 0.0, "percentage": 0.0}
    )
    payload["evidence_base"] = _evidence_base(
        considered_rules=2,
        applicable_rules=2,
        coverage_percent=50.0,
        rules={"assessed": 1, "unknown": 1, "not_applicable": 0, "skipped_inapplicable": 0},
    )
    write_json(raw_path, payload)
    combined = path / "combined.csv"
    text = combined.read_text(encoding="utf-8")
    text = text.replace(
        "error,cat_documentation\n", "error,cat_determinism_seeds,cat_documentation\n"
    )
    text = text.replace("unavailable,unavailable,,0.0\n", "unavailable,unavailable,,,0.0\n")
    combined.write_text(text, encoding="utf-8")
    metadata_path = path / "run_meta.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["builtin_rule_ids"] = ["R-TEST-001", "R-TEST-002"]
    metadata["builtin_rule_count"] = 2
    write_json(metadata_path, metadata)
    _rehash_completed_run(path, "raw_json/repo.json")
    _rehash_completed_run(path, "combined.csv")


def test_unassessed_run_records_a_null_score_that_is_not_a_zero(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_unassessed_run(run)

    validate_run(run)

    with (run / "combined.csv").open(newline="", encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))
    assert row["score"] == ""
    assert row["tier"] == "Unrated (nothing assessed)"


def test_unassessed_tier_outranks_the_unrated_one(tmp_path: Path) -> None:
    """Nothing assessed is reported ahead of too little source, as the analyzer does."""
    run = tmp_path / "run"
    _write_unassessed_run(run)
    raw_path = run / "raw_json" / "repo.json"
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    payload["evidence_base"]["rated"] = False
    payload["evidence_base"]["analysable_lines"] = 1
    write_json(raw_path, payload)
    _rehash_completed_run(run, "raw_json/repo.json")

    validate_run(run)


@pytest.mark.parametrize(
    ("total", "tier", "match"),
    [
        (0.0, "Unrated (nothing assessed)", "total score is not supported"),
        (0.0, "Needs work", "total score is not supported"),
        (None, "Needs work", "tier is inconsistent with its score"),
    ],
)
def test_raw_null_total_and_its_tier_must_agree_with_the_findings(
    tmp_path: Path,
    total: float | None,
    tier: str,
    match: str,
) -> None:
    run = tmp_path / "run"
    _write_unassessed_run(run)
    raw_path = run / "raw_json" / "repo.json"
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    payload["total"] = total
    payload["tier"] = tier
    write_json(raw_path, payload)
    _rehash_completed_run(run, "raw_json/repo.json")

    with pytest.raises(RunContractError, match=match):
        validate_run(run)


def test_combined_cannot_report_a_zero_for_a_null_score(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_unassessed_run(run)
    combined = run / "combined.csv"
    combined.write_text(
        combined.read_text(encoding="utf-8").replace(",,Unrated", ",0.0,Unrated"),
        encoding="utf-8",
    )
    _rehash_completed_run(run, "combined.csv")

    with pytest.raises(RunContractError, match="score disagrees"):
        validate_run(run)


def test_combined_cannot_omit_a_score_the_raw_json_reports(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_minimal_valid_run(run)
    combined = run / "combined.csv"
    combined.write_text(
        combined.read_text(encoding="utf-8").replace(",0.0,Needs work,", ",,Needs work,"),
        encoding="utf-8",
    )
    _rehash_completed_run(run, "combined.csv")

    with pytest.raises(RunContractError, match="score disagrees"):
        validate_run(run)


def test_a_category_with_nothing_assessed_is_reported_rather_than_dropped(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_partly_unassessed_run(run)

    validate_run(run)

    with (run / "combined.csv").open(newline="", encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))
    assert row["cat_determinism_seeds"] == ""
    assert row["cat_documentation"] == "0.0"


def test_combined_cannot_report_a_zero_for_an_unassessed_category(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_partly_unassessed_run(run)
    combined = run / "combined.csv"
    combined.write_text(
        combined.read_text(encoding="utf-8").replace("unavailable,,,0.0\n", "unavailable,,0.0,0.0\n"),
        encoding="utf-8",
    )
    _rehash_completed_run(run, "combined.csv")

    with pytest.raises(RunContractError, match="category disagrees"):
        validate_run(run)


def test_combined_cannot_omit_a_category_the_raw_json_scored(tmp_path: Path) -> None:
    """The absence convention cannot be met by leaving every category cell empty."""
    run = tmp_path / "run"
    _write_partly_unassessed_run(run)
    combined = run / "combined.csv"
    combined.write_text(
        combined.read_text(encoding="utf-8").replace("unavailable,,,0.0\n", "unavailable,,,\n"),
        encoding="utf-8",
    )
    _rehash_completed_run(run, "combined.csv")

    with pytest.raises(RunContractError, match="category disagrees"):
        validate_run(run)


def test_an_empty_category_score_must_be_backed_by_an_unanswered_rule(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_partly_unassessed_run(run)
    raw_path = run / "raw_json" / "repo.json"
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    payload["findings"][1]["status"] = "not-applicable"
    payload["evidence_base"] = _evidence_base(
        considered_rules=2,
        rules={"assessed": 1, "unknown": 0, "not_applicable": 1, "skipped_inapplicable": 0},
    )
    write_json(raw_path, payload)
    _rehash_completed_run(run, "raw_json/repo.json")

    with pytest.raises(RunContractError, match="empty category score is not supported"):
        validate_run(run)


@pytest.mark.parametrize(
    ("evidence_base", "match"),
    [
        (
            {
                "rated": True,
                "evaluated_rules": 1,
                "considered_rules": 1,
                "applicable_rules": 1,
                "coverage_percent": 100.0,
                "analysable_lines": 500,
            },
            r"evidence base for \w+ fields are invalid",
        ),
        (
            _evidence_base(
                rules={
                    "assessed": 1,
                    "unknown": 0,
                    "not_applicable": 0,
                    "skipped_inapplicable": 0,
                    "retired": 0,
                }
            ),
            r"rule census for \w+ fields are invalid",
        ),
        (
            _evidence_base(applicable_rules=True),
            "applicable-rule count is invalid",
        ),
        (
            _evidence_base(
                rules={
                    "assessed": True,
                    "unknown": 0,
                    "not_applicable": 0,
                    "skipped_inapplicable": 0,
                }
            ),
            "assessed-rule census is invalid",
        ),
        (
            _evidence_base(
                rules={"assessed": 1, "unknown": 0, "not_applicable": 0, "skipped_inapplicable": -1}
            ),
            "skipped_inapplicable-rule census is invalid",
        ),
        (
            _evidence_base(applicable_rules=0),
            "evaluated rules exceed those applicable",
        ),
        (
            _evidence_base(applicable_rules=2),
            "applicable rules exceed those considered",
        ),
        (
            _evidence_base(
                rules={"assessed": 0, "unknown": 1, "not_applicable": 0, "skipped_inapplicable": 0}
            ),
            "assessed-rule census disagrees with its evaluated count",
        ),
        (
            _evidence_base(
                rules={"assessed": 1, "unknown": 1, "not_applicable": 0, "skipped_inapplicable": 0}
            ),
            "does not partition the applicable rules",
        ),
        (
            _evidence_base(
                rules={"assessed": 1, "unknown": 0, "not_applicable": 1, "skipped_inapplicable": 0}
            ),
            "does not partition the considered rules",
        ),
        (
            _evidence_base(coverage_percent=73.9),
            "coverage is not supported",
        ),
        (
            _evidence_base(coverage_percent="100.0"),
            "coverage .* must be numeric",
        ),
    ],
)
def test_raw_evidence_base_must_partition_the_rules_it_counts(
    tmp_path: Path,
    evidence_base: dict[str, object],
    match: str,
) -> None:
    run = tmp_path / "run"
    _write_minimal_valid_run(run)
    raw_path = run / "raw_json" / "repo.json"
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    payload["evidence_base"] = evidence_base
    write_json(raw_path, payload)
    _rehash_completed_run(run, "raw_json/repo.json")

    with pytest.raises(RunContractError, match=match):
        validate_run(run)


def test_output_containment_rejects_descendants_of_immutable_inputs(tmp_path: Path) -> None:
    immutable = tmp_path / "immutable"
    immutable.mkdir()
    with pytest.raises(RunContractError, match="outside immutable input"):
        ensure_output_outside(immutable / "report.json", [immutable])


def test_a_synthetic_protocol_cannot_govern_a_run_under_corpus_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run_contract, "_CORPUS_DIR", tmp_path)
    outputs = tmp_path / "outputs"
    outputs.mkdir()

    for run_dir in (outputs / "candidate-a", outputs / "x" / "y"):
        with pytest.raises(RunContractError, match="is a fixture and cannot govern a run"):
            reject_synthetic_protocol_in_recorded_outputs("synthetic-fixture-r3", run_dir)


def test_a_synthetic_protocol_governs_a_run_outside_corpus_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run_contract, "_CORPUS_DIR", tmp_path)
    (tmp_path / "outputs").mkdir()

    reject_synthetic_protocol_in_recorded_outputs("synthetic-fixture-r3", tmp_path / "elsewhere")


def test_only_the_reserved_prefix_is_refused_under_corpus_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run_contract, "_CORPUS_DIR", tmp_path)
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    run_dir = outputs / "candidate-a"

    for protocol_id in ("pilot-0.1.2-r6", "synthetic", "not-synthetic-at-all", None, 123):
        reject_synthetic_protocol_in_recorded_outputs(protocol_id, run_dir)


def test_metadata_types_and_timestamps_are_strict(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_minimal_valid_run(run)
    metadata_path = run / "run_meta.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["n_repositories"] = True
    metadata["timeout_seconds"] = -1
    metadata["completed_at"] = "2025-01-01T00:00:00+00:00"
    write_json(metadata_path, metadata)
    (run / COMPLETE_MARKER).write_text(
        sha256_file(metadata_path) + "\n", encoding="utf-8", newline="\n"
    )

    with pytest.raises(RunContractError):
        validate_run(run)


def test_malformed_utf8_is_a_controlled_contract_error(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_minimal_valid_run(run)
    (run / "combined.csv").write_bytes(b"\xff")
    _rehash_completed_run(run, "combined.csv")

    with pytest.raises(RunContractError, match="UTF-8"):
        validate_run(run)


def test_compare_rejects_same_run_and_separates_incomparable_identity(tmp_path: Path) -> None:
    run_a = tmp_path / "run-a"
    run_b = tmp_path / "run-b"
    _write_minimal_valid_run(run_a, run_id="run-a")
    _write_minimal_valid_run(run_b, run_id="run-b")

    with pytest.raises(RunContractError, match="distinct run directories"):
        compare(run_a, run_a)

    metadata_path = run_b / "run_meta.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["platform"] = "different-platform"
    write_json(metadata_path, metadata)
    (run_b / COMPLETE_MARKER).write_text(
        sha256_file(metadata_path) + "\n", encoding="utf-8", newline="\n"
    )
    report = compare(run_a, run_b)
    assert report["comparable"] is False
    assert report["deterministic"] is None
    assert report["identity_differences"][0]["field"] == "platform"


def test_incomplete_and_existing_run_directories_are_rejected(tmp_path: Path) -> None:
    run = tmp_path / "run"
    ensure_new_output_directory(run)
    with pytest.raises(RunContractError, match="incomplete"):
        validate_run(run)
    with pytest.raises(RunContractError, match="overwrite"):
        ensure_new_output_directory(run)


def test_fingerprint_ignores_volatile_message_but_tracks_anchors() -> None:
    finding = {
        "rule_id": "R-TEST-001",
        "title": "Stable family",
        "message": "found 2 instances",
        "locations": [{"path": "train.py", "line": 5}],
    }
    first = finding_fingerprint("repo", "a" * 40, finding)
    finding["message"] = "found 3 instances"
    second = finding_fingerprint("repo", "a" * 40, finding)
    finding["locations"] = [{"path": "train.py", "line": 6}]
    third = finding_fingerprint("repo", "a" * 40, finding)

    assert first == second
    assert third != first


def test_repo_inventory_rejects_duplicate_or_unsafe_ids(tmp_path: Path) -> None:
    repos = tmp_path / "repos.csv"
    repos.write_text(
        "id,cohort,repo_url,commit_sha\n../escape,stress,https://example.invalid/a,\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="malformed corpus row"):
        read_repos(repos)


def test_badged_provenance_binds_full_badges_and_resolved_commits() -> None:
    rows = read_repos(ROOT / "corpus" / "repos.csv")
    provenance = (ROOT / "corpus" / "badged-provenance.csv").read_bytes()
    validate_badged_provenance_bytes(provenance, rows)

    changed = provenance.replace(
        b"available+functional+results_reproduced",
        b"available+functional",
        1,
    )
    with pytest.raises(RunContractError, match="badge set mismatch"):
        validate_badged_provenance_bytes(changed, rows)


def test_label_schema_rejects_duplicate_fingerprints() -> None:
    entry = {
        "label_schema_version": 1,
        "finding_fingerprint": "v1:" + "a" * 64,
        "correctness": "",
        "applicability": "",
        "utility": "",
        "verification_mode": "",
        "reviewed_at": "",
    }
    with pytest.raises(ValueError, match="duplicate"):
        validate_labels([entry, dict(entry)])


def test_runner_creates_a_valid_builtins_only_run_and_refuses_reuse(tmp_path: Path) -> None:
    clones = tmp_path / "clones"
    clone = clones / "fixture"
    commit = _make_git_repo(clone)
    repos = tmp_path / "repos.csv"
    _write_repos(repos, commit)
    _write_clone_manifest(clones, repos, commit)
    run = tmp_path / "run"

    command = [
        sys.executable,
        str(RUNNER),
        "--repos",
        str(repos),
        "--clones",
        str(clones),
        "--badged-provenance",
        str(repos.parent / "badged-provenance.csv"),
        "--out",
        str(run),
        "--timeout",
        "30",
        "--operational-only",
    ]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    repeated = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)

    assert completed.returncode == 0, completed.stderr
    metadata = validate_run(run)
    assert metadata["execution_mode"] == "offline-builtins-only"
    assert metadata["builtin_rule_count"] == 78
    assert metadata["n_succeeded"] == 1
    assert metadata["runtime_context"]["logical_cpu"]["unit"] in {
        "count",
        "unavailable",
    }
    assert metadata["runtime_context"]["cache_policy"]["filesystem_cache"] == "not-cleared"
    with (run / "combined.csv").open(newline="", encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))
    assert int(row["input_file_count"]) > 0
    assert int(row["input_byte_count"]) > 0
    assert row["peak_rss_unit"] in {"bytes", "kibibytes", "unavailable"}
    assert repeated.returncode != 0
    assert "refusing to overwrite" in repeated.stderr


def test_runner_records_clone_tool_digest_and_live_agreement(tmp_path: Path) -> None:
    """The declared acquisition digest and its live agreement are an observation, not a gate."""
    clones = tmp_path / "clones"
    clone = clones / "fixture"
    commit = _make_git_repo(clone)
    repos = tmp_path / "repos.csv"
    _write_repos(repos, commit)
    _write_clone_manifest(clones, repos, commit)
    run = tmp_path / "run"

    completed = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--repos",
            str(repos),
            "--clones",
            str(clones),
            "--badged-provenance",
            str(repos.parent / "badged-provenance.csv"),
            "--out",
            str(run),
            "--timeout",
            "30",
            "--operational-only",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    metadata = validate_run(run)
    manifest = json.loads((clones / "clones_manifest.json").read_bytes())
    assert metadata["clone_tool_sha256"] == manifest["clone_tool_sha256"]
    assert metadata["clone_tool_sha256_matches_live"] is True


def _make_reconciliation_repo(path: Path, url: str, *, runnable: bool) -> str:
    """A repository whose result-reconciliation rules apply, and may go unanswered.

    The framework import makes the category applicable. With no paper, manifest
    or run script nothing in it reaches an assessment, so the score card retains
    the category carrying no score; a run script gives the seed-coverage rule
    something to judge, and the same category is scored instead.
    """
    path.mkdir(parents=True)
    _write(path / "README.md", "# Fixture\n")
    _write(path / "train.py", "import torch\n\nprint('fixture')\n")
    if runnable:
        _write(path / "run.sh", "python train.py --lr 0.1\n")
    _git("init", "-q", cwd=path)
    _git("config", "user.name", "Corpus Test", cwd=path)
    _git("config", "user.email", "corpus@example.invalid", cwd=path)
    _git("add", ".", cwd=path)
    _git("commit", "-qm", "fixture", cwd=path)
    _git("remote", "add", "origin", url, cwd=path)
    return _git("rev-parse", "HEAD", cwd=path)


def _write_reconciliation_cohorts(tmp_path: Path) -> tuple[Path, Path, Path]:
    """One unvetted and one badged repository, differing only in what was assessed."""
    clones = tmp_path / "clones"
    repos = tmp_path / "repos.csv"
    provenance = tmp_path / "badged-provenance.csv"
    cohorts = {"fixture": "unvetted", "badged": "badged_functional"}
    urls = {repo_id: f"https://example.invalid/{repo_id}" for repo_id in cohorts}
    commits = {
        repo_id: _make_reconciliation_repo(
            clones / repo_id, urls[repo_id], runnable=repo_id == "badged"
        )
        for repo_id in cohorts
    }
    with repos.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["id", "cohort", "repo_url", "commit_sha", "badge_type"]
        )
        writer.writeheader()
        for repo_id, cohort in cohorts.items():
            writer.writerow(
                {
                    "id": repo_id,
                    "cohort": cohort,
                    "repo_url": urls[repo_id],
                    "commit_sha": commits[repo_id],
                    "badge_type": "available+functional" if repo_id == "badged" else "",
                }
            )
    with provenance.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(BADGED_PROVENANCE_FIELDS))
        writer.writeheader()
        writer.writerow(
            {
                "id": "badged",
                "commit_sha": commits["badged"],
                "paper_title": "Fixture artifact",
                "artifact_result_id": "fixture-result",
                "badge_set": "available+functional",
                "evaluation_results_url": "https://example.invalid/evaluation",
                "artifact_snapshot_url": f"{urls['badged']}/archive",
                "artifact_appendix_url": "https://example.invalid/appendix",
                "artifact_ref_kind": "commit",
                "artifact_ref": commits["badged"],
                "artifact_ref_url": f"{urls['badged']}/commit/{commits['badged']}",
                "resolved_commit_sha": commits["badged"],
                "resolved_commit_url": f"{urls['badged']}/commit/{commits['badged']}",
                "retrieved_at_utc": "2026-01-01T00:00:00Z",
                "mapping_basis": "fixture mapping",
            }
        )
    write_json(
        clones / "clones_manifest.json",
        {
            "clone_schema_version": CLONE_SCHEMA_VERSION,
            "created_at": "2026-01-01T00:00:00+00:00",
            "repos_file": str(repos),
            "repos_file_sha256": sha256_file(repos),
            "clone_tool_sha256": sha256_file(ROOT / "corpus" / "scripts" / "clone_repos.py"),
            "records": [
                {
                    "id": repo_id,
                    "cohort": cohort,
                    "repo_url": urls[repo_id],
                    "requested_sha": commits[repo_id],
                    "resolved_sha": commits[repo_id],
                    "status": "cloned",
                    "error": None,
                    "origin_url": urls[repo_id],
                    "dirty": False,
                    "git_tree_sha": _git("rev-parse", "HEAD^{tree}", cwd=clones / repo_id),
                    "worktree_sha256": repository_tree_sha256(clones / repo_id),
                    "submodule_status": [],
                    "submodule_state": "not_configured",
                    "git_lfs_state": "no_pointers",
                    "git_lfs_pointer_count": 0,
                    "git_lfs_paths_sample": [],
                    "acquisition_status": "complete",
                }
                for repo_id, cohort in cohorts.items()
            ],
        },
    )
    return repos, clones, provenance


def _run_reconciliation_cohorts(tmp_path: Path) -> Path:
    repos, clones, provenance = _write_reconciliation_cohorts(tmp_path)
    run = tmp_path / "run"
    completed = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--repos",
            str(repos),
            "--clones",
            str(clones),
            "--badged-provenance",
            str(provenance),
            "--out",
            str(run),
            "--timeout",
            "30",
            "--operational-only",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    validate_run(run)
    return run


def test_runner_records_an_unassessed_category_as_an_absent_combined_value(
    tmp_path: Path,
) -> None:
    """The raw JSON keeps its zero beside a zero ``possible``; the CSV cannot."""
    run = _run_reconciliation_cohorts(tmp_path)

    payload = json.loads((run / "raw_json" / "fixture.json").read_text(encoding="utf-8"))
    unassessed = [category for category in payload["categories"] if category["possible"] == 0]
    assert [category["category"] for category in unassessed] == ["Result Reconciliation"]
    assert unassessed[0]["percentage"] == 0.0
    with (run / "combined.csv").open(newline="", encoding="utf-8") as handle:
        rows = {row["id"]: row for row in csv.DictReader(handle)}
    assert rows["fixture"]["cat_result_reconciliation"] == ""
    assert rows["badged"]["cat_result_reconciliation"] == "50.0"
    assert rows["fixture"]["cat_documentation"] == "0.0"


def test_summary_excludes_an_unassessed_category_from_its_per_category_gaps(
    tmp_path: Path,
) -> None:
    """The gap that reads a zero for an unassessed category is the one this prevents."""
    run = _run_reconciliation_cohorts(tmp_path)
    summary = tmp_path / "summary.md"

    completed = subprocess.run(
        [sys.executable, str(SUMMARIZE), "--run", str(run), "--out", str(summary)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    text = summary.read_text(encoding="utf-8")
    assert "## Per-category median gaps" in text
    assert "\n- documentation: +0.0\n" in text
    assert "result_reconciliation" not in text


class _EffectivenessFixture(NamedTuple):
    run: Path
    repos: Path
    clones: Path
    claims: Path
    review: Path
    sources: list[Path]
    preregistration: Path
    clean_identity: Callable[[], dict[str, object]]


def _synthetic_effectiveness_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    run_name: str = "candidate-a",
) -> _EffectivenessFixture:
    """Bind a fixture lock to a clone set the effectiveness path can be driven over."""
    clones = tmp_path / "clones"
    commit = _make_git_repo(clones / "fixture")
    repos = tmp_path / "repos.csv"
    _write_repos(repos, commit)
    _write_clone_manifest(clones, repos, commit)
    run = tmp_path / run_name
    claims = tmp_path / "claims.json"
    review = tmp_path / "claim-review.json"
    source_paths = _write_accepted_claim_review(
        claims,
        review,
        repos,
        clones,
        commit,
        [run.name, "candidate-b"],
    )

    observed_identity = run_validation.source_identity

    def clean_committed_identity() -> dict[str, object]:
        identity = observed_identity()
        identity["adduce_source_commit"] = "a" * 40
        identity["adduce_source_dirty"] = False
        identity["corpus_harness_git_commit"] = "a" * 40
        identity["corpus_harness_git_dirty"] = False
        identity["corpus_harness_git_tracked"] = True
        return identity

    monkeypatch.setattr(run_validation, "source_identity", clean_committed_identity)
    badged_provenance = repos.parent / "badged-provenance.csv"
    _, harness_files = run_validation.harness_snapshot(
        badged_provenance=badged_provenance
    )
    preregistration = tmp_path / "pilot-r4-preregistration.json"
    preregistration_payload = build_preregistration(
        protocol_id="synthetic-fixture-r3",
        candidate_pair=[run.name, "candidate-b"],
        schema_data=harness_files["preregistration.schema.json"],
        repos_data=repos.read_bytes(),
        clone_manifest_data=(clones / "clones_manifest.json").read_bytes(),
        claim_ground_truth_data=claims.read_bytes(),
        claim_review_schema_data=harness_files["claim-review.schema.json"],
        badged_provenance_data=harness_files["badged-provenance.csv"],
        analysis_plan_files=run_validation.preregistration_analysis_plan(harness_files),
        source_identity=clean_committed_identity(),
        timeout_seconds=30,
    )
    write_json(preregistration, preregistration_payload)
    monkeypatch.setenv(run_validation.LIVE_PREREGISTRATION_ENV, str(preregistration))
    return _EffectivenessFixture(
        run=run,
        repos=repos,
        clones=clones,
        claims=claims,
        review=review,
        sources=source_paths,
        preregistration=preregistration,
        clean_identity=clean_committed_identity,
    )


def _effectiveness_argv(fixture: _EffectivenessFixture, run: Path) -> list[str]:
    return [
        str(RUNNER),
        "--repos",
        str(fixture.repos),
        "--clones",
        str(fixture.clones),
        "--badged-provenance",
        str(fixture.repos.parent / "badged-provenance.csv"),
        "--claims",
        str(fixture.claims),
        "--claim-review",
        str(fixture.review),
        "--claim-review-source",
        str(fixture.sources[0]),
        "--claim-review-source",
        str(fixture.sources[1]),
        "--out",
        str(run),
        "--timeout",
        "30",
    ]


def test_effectiveness_runner_requires_claim_review_before_creating_output(
    tmp_path: Path,
) -> None:
    run = tmp_path / "candidate-a"
    completed = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--claims",
            str(tmp_path / "claims.json"),
            "--out",
            str(run),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "effectiveness run requires --claim-review" in completed.stderr
    assert not run.exists()


def test_effectiveness_refuses_without_a_live_preregistration_lock(tmp_path: Path) -> None:
    run = tmp_path / "candidate-a"
    environment = {
        key: value
        for key, value in os.environ.items()
        if key != run_validation.LIVE_PREREGISTRATION_ENV
    }
    completed = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--claims",
            str(tmp_path / "claims.json"),
            "--claim-review",
            str(tmp_path / "claim-review.json"),
            "--claim-review-source",
            str(tmp_path / "reviewer-a.json"),
            "--claim-review-source",
            str(tmp_path / "reviewer-b.json"),
            "--out",
            str(run),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode != 0
    assert "live preregistration lock" in completed.stderr
    assert not run.exists()


def test_a_retired_lock_on_disk_is_not_the_live_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    retired = ROOT / "corpus" / "pilot-r6-preregistration.json"
    assert retired.is_file()

    monkeypatch.delenv(run_validation.LIVE_PREREGISTRATION_ENV, raising=False)
    assert run_validation.live_preregistration_path() is None

    monkeypatch.setenv(run_validation.LIVE_PREREGISTRATION_ENV, "")
    assert run_validation.live_preregistration_path() is None

    successor = tmp_path / "pilot-r7-preregistration.json"
    monkeypatch.setenv(run_validation.LIVE_PREREGISTRATION_ENV, str(successor))
    assert run_validation.live_preregistration_path() == successor

    defaults = sorted(
        name
        for name, value in vars(run_validation).items()
        if isinstance(value, Path) and fnmatch(value.name, "pilot-r*.json")
    )
    assert not defaults, (
        f"{defaults} names a module-level default lock. A default lock under any "
        "name is the regression this forbids, not just PREREGISTRATION_PATH: a "
        "retired lock would once again bind every effectiveness run that names "
        "no successor"
    )


def test_effectiveness_runner_binds_accepted_review_before_scanning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _synthetic_effectiveness_fixture(tmp_path, monkeypatch)
    run = fixture.run
    claims = fixture.claims
    review = fixture.review
    source_paths = fixture.sources
    preregistration = fixture.preregistration
    clean_committed_identity = fixture.clean_identity
    monkeypatch.setattr(sys, "argv", _effectiveness_argv(fixture, run))

    def dirty_harness_identity() -> dict[str, object]:
        identity = clean_committed_identity()
        identity["corpus_harness_git_dirty"] = True
        return identity

    monkeypatch.setattr(run_validation, "source_identity", dirty_harness_identity)
    with pytest.raises(SystemExit, match="complete corpus harness"):
        run_validation.main()
    assert not run.exists()
    monkeypatch.setattr(run_validation, "source_identity", clean_committed_identity)
    sys.argv[-1] = "31"
    with pytest.raises(SystemExit, match="execution contract differs"):
        run_validation.main()
    assert not run.exists()
    sys.argv[-1] = "30"
    assert run_validation.main() == 0
    metadata = validate_run(run)
    assert metadata["analysis_scope"] == "effectiveness"
    assert metadata["candidate_run_name"] == run.name
    assert metadata["claim_ground_truth_sha256"] == sha256_file(claims)
    assert metadata["claim_review_sha256"] == sha256_file(review)
    assert metadata["preregistration_sha256"] == sha256_file(preregistration)
    assert (run / "inputs" / "claim_ground_truth.json").read_bytes() == claims.read_bytes()
    assert (run / "inputs" / "claim_review.json").read_bytes() == review.read_bytes()
    assert (run / "inputs" / "preregistration.json").read_bytes() == preregistration.read_bytes()
    assert {
        (run / "inputs" / "claim_review_source_1.json").read_bytes(),
        (run / "inputs" / "claim_review_source_2.json").read_bytes(),
    } == {path.read_bytes() for path in source_paths}

    copied_review = json.loads((run / "inputs" / "claim_review.json").read_text(encoding="utf-8"))
    copied_review["claims"][0]["reviews"].pop()
    write_json(run / "inputs" / "claim_review.json", copied_review)
    _rehash_completed_run(run, "inputs/claim_review.json")
    with pytest.raises(RunContractError, match="lacks two independent reviews"):
        validate_run(run)

    (run / "inputs" / "claim_review.json").write_bytes(review.read_bytes())
    _rehash_completed_run(run, "inputs/claim_review.json")
    copied_preregistration = json.loads(
        (run / "inputs" / "preregistration.json").read_text(encoding="utf-8")
    )
    copied_preregistration["execution_contract"]["timeout_seconds"] = 31
    write_json(run / "inputs" / "preregistration.json", copied_preregistration)
    _rehash_completed_run(run, "inputs/preregistration.json")
    with pytest.raises(RunContractError, match="preregistration violates its contract"):
        validate_run(run)


def test_effectiveness_refuses_a_lock_whose_bound_value_was_altered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _synthetic_effectiveness_fixture(tmp_path, monkeypatch)
    locked = json.loads(fixture.preregistration.read_text(encoding="utf-8"))
    altered = "0" * 63 + "1"
    alterations = (
        (("inputs", "claim_ground_truth_sha256"), "frozen inputs differ"),
        (("adduce", "source_tree_sha256"), "analyzer identity differs"),
        (("analysis_plan", "sha256"), "analysis plan differs"),
    )

    for attempt, ((block, field), match) in enumerate(alterations, 1):
        payload = json.loads(json.dumps(locked))
        assert payload[block][field] != altered
        payload[block][field] = altered
        write_json(fixture.preregistration, payload)
        run = tmp_path / f"attempt-{attempt}" / fixture.run.name
        monkeypatch.setattr(sys, "argv", _effectiveness_argv(fixture, run))

        with pytest.raises(SystemExit, match=match):
            run_validation.main()
        assert not run.exists()


def test_effectiveness_refuses_to_record_a_fixture_run_under_corpus_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The runner will not produce the run at all, so there is nothing to file."""
    fixture = _synthetic_effectiveness_fixture(tmp_path, monkeypatch)
    recorded = tmp_path / "outputs" / fixture.run.name
    monkeypatch.setattr(run_contract, "_CORPUS_DIR", tmp_path)
    monkeypatch.setattr(sys, "argv", _effectiveness_argv(fixture, recorded))

    with pytest.raises(SystemExit, match="cannot govern a run recorded under corpus/outputs"):
        run_validation.main()

    assert not recorded.exists()
    assert not (tmp_path / "outputs").exists()


def test_run_contract_refuses_a_fixture_run_validated_from_corpus_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fixture run that reached the tracked tree by other means still fails."""
    fixture = _synthetic_effectiveness_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "argv", _effectiveness_argv(fixture, fixture.run))
    assert run_validation.main() == 0
    validate_run(fixture.run)

    recorded = tmp_path / "outputs" / fixture.run.name
    shutil.copytree(fixture.run, recorded)
    monkeypatch.setattr(run_contract, "_CORPUS_DIR", tmp_path)

    with pytest.raises(RunContractError) as refusal:
        validate_run(recorded)

    assert "cannot govern a run recorded under corpus/outputs" in str(refusal.value)
    assert "violates its contract" not in str(refusal.value)


def test_runner_rejects_clone_changed_after_manifest(tmp_path: Path) -> None:
    clones = tmp_path / "clones"
    clone = clones / "fixture"
    commit = _make_git_repo(clone)
    repos = tmp_path / "repos.csv"
    _write_repos(repos, commit)
    _write_clone_manifest(clones, repos, commit)
    _write(clone / "untracked.txt", "changed\n")
    run = tmp_path / "run"

    completed = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--repos",
            str(repos),
            "--clones",
            str(clones),
            "--badged-provenance",
            str(repos.parent / "badged-provenance.csv"),
            "--out",
            str(run),
            "--operational-only",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "clone is dirty" in completed.stderr
    assert not run.exists()
