"""Security and attribution boundaries for the corpus execution harness."""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from pathlib import Path

import corpus.scripts.run_validation as run_validation
import pytest
from corpus.scripts.check_builtin import _allowed_git_command, _enforce_offline
from corpus.scripts.clone_repos import (
    CLONE_SCHEMA_VERSION,
    _submodule_state,
    clone_one,
    repository_tree_sha256,
)
from corpus.scripts.run_contract import (
    BADGED_PROVENANCE_FIELDS,
    RunContractError,
    sha256_file,
    validate_run,
    write_json,
)
from corpus.scripts.run_validation import (
    _checker_environment,
    _source_tree_sha256,
    _validate_symlink_containment,
    check_repo,
    load_clone_records,
)

import adduce

ROOT = Path(__file__).resolve().parent.parent
CHECKER = ROOT / "corpus" / "scripts" / "check_builtin.py"
RUNNER = ROOT / "corpus" / "scripts" / "run_validation.py"


def _git(*args: str, cwd: Path) -> str:
    completed = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return completed.stdout.strip()


def _make_repo(path: Path, *, lfs_pointer: bool = False) -> str:
    path.mkdir(parents=True)
    (path / "README.md").write_text("# Fixture\n", encoding="utf-8")
    (path / "train.py").write_text("print('fixture')\n", encoding="utf-8")
    (path / "adduce.toml").write_text('profile = "acm"\nignore = ["R-DOC-001"]\n', encoding="utf-8")
    if lfs_pointer:
        (path / "weights.bin").write_text(
            "version https://git-lfs.github.com/spec/v1\n"
            "oid sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\n"
            "size 100\n",
            encoding="utf-8",
        )
    _git("init", "-q", cwd=path)
    _git("config", "user.name", "Corpus Test", cwd=path)
    _git("config", "user.email", "corpus@example.invalid", cwd=path)
    _git("remote", "add", "origin", "https://example.invalid/fixture", cwd=path)
    _git("add", ".", cwd=path)
    _git("commit", "-qm", "fixture", cwd=path)
    return _git("rev-parse", "HEAD", cwd=path)


def _inventory_row(commit: str) -> dict[str, str]:
    return {
        "id": "fixture",
        "cohort": "unvetted",
        "repo_url": "https://example.invalid/fixture",
        "commit_sha": commit,
    }


def test_offline_audit_policy_allows_only_required_read_only_git(tmp_path: Path) -> None:
    repository = tmp_path.resolve()
    allowed = ["git", "-C", str(repository), "rev-parse", "HEAD"]

    assert _allowed_git_command("git", allowed, repository)
    assert not _allowed_git_command("git", [*allowed[:-2], "fetch", "origin"], repository)
    assert not _allowed_git_command("git", allowed, repository / "other")

    for event in (
        "socket.__new__",
        "socket.getaddrinfo",
        "socket.gethostbyname",
        "socket.getnameinfo",
        "socket.sendmsg",
        "socket.sendto",
    ):
        with pytest.raises(RuntimeError, match="network access is disabled"):
            _enforce_offline(event, ("example.invalid", 443), repository)
    with pytest.raises(RuntimeError, match="process execution is disabled"):
        _enforce_offline("subprocess.Popen", (sys.executable, [sys.executable, "-V"]), repository)
    with pytest.raises(RuntimeError, match="process execution is disabled"):
        _enforce_offline("os.system", (b"true",), repository)
    with pytest.raises(RuntimeError, match="process execution is disabled"):
        _enforce_offline("os.fork", (), repository)
    with pytest.raises(RuntimeError, match="filesystem writes are disabled"):
        _enforce_offline("open", (str(repository / "output"), "w", os.O_WRONLY), repository)


def test_checker_environment_does_not_inherit_host_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADDUCE_TEST_SENTINEL_SECRET", "must-not-cross-boundary")
    monkeypatch.setenv("GITHUB_TOKEN", "must-not-cross-boundary")

    environment = _checker_environment()

    assert "ADDUCE_TEST_SENTINEL_SECRET" not in environment
    assert "GITHUB_TOKEN" not in environment
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"


def test_malformed_scanner_output_is_a_contract_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    checker = tmp_path / "checker.py"
    checker.write_text("print('{not-json')\n", encoding="utf-8")
    monkeypatch.setattr(run_validation, "BUILTIN_CHECKER", checker)

    payload, error, status, _ = check_repo(repository, 10)

    assert payload is None
    assert status == "contract_failed"
    assert error is not None and "valid JSON" in error


def test_checker_resolves_relative_repository_before_changing_child_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    commit = _make_repo(repository)
    monkeypatch.chdir(tmp_path)

    payload, error, status, _ = check_repo(Path("repo"), 30)

    assert error is None
    assert status is None
    assert payload is not None
    assert payload["repository"]["commit"] == commit
    assert Path(payload["repository"]["root"]) == repository.resolve()


def test_repository_symlinks_must_resolve_inside_the_clone(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "target").write_text("inside\n", encoding="utf-8")
    internal = repository / "internal"
    try:
        internal.symlink_to("target")
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")
    _validate_symlink_containment(repository)

    internal.unlink()
    external = tmp_path / "external"
    external.write_text("outside\n", encoding="utf-8")
    internal.symlink_to(external)
    with pytest.raises(RunContractError, match="escapes its clone root"):
        _validate_symlink_containment(repository)


def test_checker_ignores_repository_config_and_records_policy(tmp_path: Path) -> None:
    commit = _make_repo(tmp_path / "repo")
    package_dir = Path(adduce.__file__).resolve().parent
    environment = os.environ.copy()
    environment["ADDUCE_CORPUS_SOURCE_ROOT"] = str(package_dir.parent)
    environment["ADDUCE_CORPUS_SOURCE_TREE_SHA256"] = _source_tree_sha256(package_dir)

    completed = subprocess.run(
        [sys.executable, str(CHECKER), str(tmp_path / "repo")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["repository"]["commit"] == commit
    assert payload["profile"] == "default"
    peak_rss = payload["corpus_execution"].pop("peak_rss")
    assert payload["corpus_execution"] == {
        "configuration_mode": "defaults-only-repository-config-disabled",
        "enforcement_scope": "scanner-regression-guard-not-os-sandbox",
        "network_policy": "python-audit-socket-deny",
        "plugins_enabled": False,
        "process_policy": "python-audit-read-only-git-metadata-only",
        "environment_policy": "minimal-no-host-credentials",
        "input_policy": "clone-root-symlink-containment",
        "adduce_source_tree_sha256": _source_tree_sha256(package_dir),
    }
    assert peak_rss["platform"] == sys.platform
    assert peak_rss["unit"] in {"bytes", "kibibytes", "unavailable"}
    assert payload["repository"]["input_file_count"] == payload["repository"]["files_scanned"]
    assert payload["repository"]["input_byte_count"] > 0


def test_worktree_digest_tracks_empty_directories_and_symlink_targets(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / ".git").mkdir()
    (repository / "tracked.txt").write_text("content\n", encoding="utf-8")
    baseline = repository_tree_sha256(repository)

    (repository / ".git" / "ignored").write_text("metadata\n", encoding="utf-8")
    assert repository_tree_sha256(repository) == baseline

    empty = repository / "empty"
    empty.mkdir()
    with_empty = repository_tree_sha256(repository)
    assert with_empty != baseline

    target = repository / "target-a"
    target.mkdir()
    link = repository / "linked-directory"
    try:
        link.symlink_to("target-a", target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this platform")
    first_target = repository_tree_sha256(repository)
    link.unlink()
    link.symlink_to("empty", target_is_directory=True)
    assert repository_tree_sha256(repository) != first_target


def test_clone_record_surfaces_lfs_pointer_as_partial_acquisition(tmp_path: Path) -> None:
    clones = tmp_path / "clones"
    commit = _make_repo(clones / "fixture", lfs_pointer=True)

    record = clone_one(_inventory_row(commit), clones)

    assert record["error"] is None
    assert record["git_lfs_state"] == "pointers_present"
    assert record["git_lfs_pointer_count"] == 1
    assert record["acquisition_status"] == "partial"
    assert record["worktree_sha256"] == repository_tree_sha256(clones / "fixture")


def test_submodule_state_distinguishes_unavailable_and_incomplete_acquisition() -> None:
    assert _submodule_state([], configured=False) == "not_configured"
    assert _submodule_state(["-abc path"], configured=True) == "uninitialized"
    assert _submodule_state(["+abc path"], configured=True) == "modified"
    assert _submodule_state(["Uabc path"], configured=True) == "conflicted"
    assert _submodule_state([" abc path"], configured=True) == "complete"
    assert _submodule_state([], configured=True) == "unavailable"


def test_runner_rechecks_origin_and_acquisition_digest(tmp_path: Path) -> None:
    clones = tmp_path / "clones"
    clone = clones / "fixture"
    commit = _make_repo(clone)
    row = _inventory_row(commit)
    record = clone_one(row, clones)
    repos = tmp_path / "repos.csv"
    repos.write_text(
        "id,cohort,repo_url,commit_sha\n"
        f"fixture,unvetted,https://example.invalid/fixture,{commit}\n",
        encoding="utf-8",
    )
    write_json(
        clones / "clones_manifest.json",
        {
            "clone_schema_version": CLONE_SCHEMA_VERSION,
            "repos_file_sha256": sha256_file(repos),
            "clone_tool_sha256": "a" * 64,
            "records": [record],
        },
    )

    repos_data = repos.read_bytes()
    loaded, _, _ = load_clone_records(
        clones, repos_data, [row], expected_clone_tool_sha256="a" * 64
    )
    assert loaded["fixture"]["worktree_sha256"] == record["worktree_sha256"]

    (clone / "untracked-empty-directory").mkdir()
    with pytest.raises(RunContractError, match="clone bytes changed"):
        load_clone_records(clones, repos_data, [row], expected_clone_tool_sha256="a" * 64)
    (clone / "untracked-empty-directory").rmdir()

    _git("remote", "set-url", "origin", "https://example.invalid/changed", cwd=clone)
    with pytest.raises(RunContractError, match="clone origin changed"):
        load_clone_records(clones, repos_data, [row], expected_clone_tool_sha256="a" * 64)


def test_runner_keeps_acquisition_failure_separate_from_scanner_crash(tmp_path: Path) -> None:
    clones = tmp_path / "clones"
    commit = _make_repo(clones / "fixture")
    success_row = _inventory_row(commit)
    success = clone_one(success_row, clones)
    unavailable_sha = "b" * 40
    failure_row = {
        "id": "unavailable",
        "cohort": "stress",
        "repo_url": "https://example.invalid/unavailable",
        "commit_sha": unavailable_sha,
    }
    failure = {
        "id": "unavailable",
        "cohort": "stress",
        "repo_url": "https://example.invalid/unavailable",
        "requested_sha": unavailable_sha,
        "resolved_sha": None,
        "status": "clone-failed",
        "error": "fixture acquisition failure",
        "origin_url": None,
        "dirty": None,
        "git_tree_sha": None,
        "worktree_sha256": None,
        "submodule_status": [],
        "submodule_state": "not_configured",
        "git_lfs_state": "no_pointers",
        "git_lfs_pointer_count": 0,
        "git_lfs_paths_sample": [],
        "acquisition_status": "failed",
    }
    repos = tmp_path / "repos.csv"
    with repos.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", "cohort", "repo_url", "commit_sha"])
        writer.writeheader()
        writer.writerows([success_row, failure_row])
    provenance = tmp_path / "badged-provenance.csv"
    provenance.write_text(
        ",".join(BADGED_PROVENANCE_FIELDS) + "\n",
        encoding="utf-8",
    )
    write_json(
        clones / "clones_manifest.json",
        {
            "clone_schema_version": CLONE_SCHEMA_VERSION,
            "created_at": "2026-01-01T00:00:00+00:00",
            "repos_file": str(repos),
            "repos_file_sha256": sha256_file(repos),
            "clone_tool_sha256": sha256_file(ROOT / "corpus" / "scripts" / "clone_repos.py"),
            "records": [success, failure],
        },
    )
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
    metadata = validate_run(run)
    assert metadata["n_succeeded"] == 1
    assert metadata["n_acquisition_failed"] == 1
    assert metadata["n_crashed"] == 0
    with (run / "combined.csv").open(newline="", encoding="utf-8") as handle:
        rows = {row["id"]: row for row in csv.DictReader(handle)}
    assert rows["unavailable"]["run_status"] == "acquisition_failed"
    assert rows["unavailable"]["acquisition_failed"] == "True"
    assert rows["unavailable"]["crash"] == "False"
