"""Corpus runs are immutable, attributable evidence rather than mutable scratch output."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from corpus.scripts.clone_repos import (
    CLONE_SCHEMA_VERSION,
    read_repos,
    repository_tree_sha256,
)
from corpus.scripts.compare_runs import compare
from corpus.scripts.label_findings import validate as validate_labels
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
    sha256_file,
    validate_badged_provenance_bytes,
    validate_run,
    write_json,
)

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "corpus" / "scripts" / "run_validation.py"


def _git(*args: str, cwd: Path) -> str:
    completed = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return completed.stdout.strip()


def _make_git_repo(path: Path) -> str:
    path.mkdir(parents=True)
    (path / "README.md").write_text("# Fixture\n\nRun with `python train.py`.\n", encoding="utf-8")
    (path / "train.py").write_text("print('fixture')\n", encoding="utf-8")
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


def _write_minimal_valid_run(path: Path, *, run_id: str = "test-run") -> None:
    ensure_new_output_directory(path)
    (path / "raw_json").mkdir()
    (path / "inputs").mkdir()
    (path / "harness").mkdir()
    payload = {
        "tool": {"name": "adduce", "version": "0.test"},
        "repository": {
            "root": str(path / "checkout"),
            "commit": "a" * 40,
            "frameworks": ["python"],
            "files_scanned": 2,
            "input_file_count": 2,
            "input_byte_count": 42,
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
            }
        ],
        "corpus_execution": {
            "configuration_mode": "defaults-only-repository-config-disabled",
            "plugins_enabled": False,
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
            "configuration_mode": "defaults-only-repository-config-disabled",
            "started_at": "2026-01-01T00:00:00+00:00",
            "completed_at": "2026-01-01T00:00:01+00:00",
            "repos_file_sha256": sha256_file(path / "inputs" / "repos.csv"),
            "clone_manifest_sha256": sha256_file(path / "inputs" / "clones_manifest.json"),
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


def test_output_containment_rejects_descendants_of_immutable_inputs(tmp_path: Path) -> None:
    immutable = tmp_path / "immutable"
    immutable.mkdir()
    with pytest.raises(RunContractError, match="outside immutable input"):
        ensure_output_outside(immutable / "report.json", [immutable])


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


def test_runner_rejects_clone_changed_after_manifest(tmp_path: Path) -> None:
    clones = tmp_path / "clones"
    clone = clones / "fixture"
    commit = _make_git_repo(clone)
    repos = tmp_path / "repos.csv"
    _write_repos(repos, commit)
    _write_clone_manifest(clones, repos, commit)
    (clone / "untracked.txt").write_text("changed\n", encoding="utf-8")
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
