"""Security and attribution boundaries for the corpus execution harness."""

from __future__ import annotations

import csv
import json
import os
import shutil
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
    _corpus_git_identity,
    _source_tree_sha256,
    _validate_symlink_containment,
    check_repo,
    load_clone_records,
    require_reconstructable_analyzer,
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
    (path / "adduce.toml").write_text(
        'profile = "acm"\n'
        'ignore = ["R-DOC-001"]\n'
        'exclude = ["**/*.py"]\n'
        "fail_under = 100\n",
        encoding="utf-8",
    )
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
    prefix = [
        "git",
        "--no-pager",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.quotePath=true",
        "-C",
        str(repository),
    ]
    allowed = [*prefix, "rev-parse", "HEAD"]

    assert _allowed_git_command("git", allowed, repository)
    assert _allowed_git_command(
        "git",
        [*prefix, "tag", "--points-at", "HEAD"],
        repository,
    )
    assert not _allowed_git_command("git", [*prefix, "tag", "--list"], repository)
    assert not _allowed_git_command("git", [*prefix, "tag"], repository)
    assert not _allowed_git_command(
        "git",
        [*prefix, "tag", "--points-at", "HEAD", "--contains"],
        repository,
    )
    assert not _allowed_git_command("git", [*allowed[:-2], "fetch", "origin"], repository)
    assert not _allowed_git_command("git", allowed, repository / "other")
    assert not _allowed_git_command(
        "git",
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        repository,
    )
    assert not _allowed_git_command(
        "git",
        [*prefix[:3], "core.fsmonitor=true", *prefix[4:], "rev-parse", "HEAD"],
        repository,
    )
    assert not _allowed_git_command(
        "git",
        [*prefix[:6], "-c", "protocol.file.allow=always", *prefix[6:], "rev-parse", "HEAD"],
        repository,
    )

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
    _enforce_offline("open", (os.devnull, None, os.O_RDWR), repository)


def test_checker_allows_git_audit_event_in_the_windows_popen_shape(tmp_path: Path) -> None:
    """Windows reports Popen with executable unset and argv already collapsed.

    ``subprocess._execute_child`` on Windows rewrites ``args`` with
    ``list2cmdline`` and never derives ``executable`` from ``args[0]``, so the
    audit event carries ``(None, "<one string>")`` where POSIX carries
    ``("git", [tokens])``. Gating on the POSIX shape alone refused every git
    query the scan makes, on every Windows host.
    """
    repository = tmp_path.resolve()
    prefix = [
        "git",
        "--no-pager",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.quotePath=true",
        "-C",
        str(repository),
    ]
    allowed = [*prefix, "rev-parse", "HEAD"]

    # The POSIX shape keeps working, byte for byte.
    assert _allowed_git_command("git", allowed, repository)

    # The Windows shape, built with the exact function Windows uses to build it.
    assert _allowed_git_command(None, subprocess.list2cmdline(allowed), repository)
    assert _allowed_git_command(
        None,
        subprocess.list2cmdline([*prefix, "ls-files"]),
        repository,
    )

    # Still fails closed in the Windows shape.
    assert not _allowed_git_command(
        None, subprocess.list2cmdline([*prefix, "fetch", "origin"]), repository
    )
    assert not _allowed_git_command(
        None, subprocess.list2cmdline([sys.executable, "-V"]), repository
    )
    assert not _allowed_git_command(
        None, subprocess.list2cmdline([*prefix, "rev-parse", "HEAD"]), repository / "other"
    )
    assert not _allowed_git_command(None, 'git --no-pager -c "unterminated', repository)
    assert not _allowed_git_command(None, "", repository)
    assert not _allowed_git_command(None, None, repository)

    # A non-canonical line that the CRT would split to a token named "git"
    # while CreateProcess resolves the program by its own rules and launches
    # something else. Splitting is not authorisation: only a line that
    # list2cmdline would itself have produced is accepted.
    tail = f"--no-pager -c core.fsmonitor=false -c core.quotePath=true -C {repository} ls-files"
    for crafted in (
        f'"C:\\evil\\g""it" {tail}',
        f'g""it {tail}',
        f'""git {tail}',
    ):
        assert not _allowed_git_command(None, crafted, repository), crafted

    # A repository path containing a space must survive the list2cmdline
    # round trip, because that is exactly the case naive whitespace splitting
    # gets wrong and Windows temp paths can contain one.
    spaced = tmp_path / "dir with space"
    spaced.mkdir()
    spaced_command = [*prefix[:7], str(spaced.resolve()), "rev-parse", "HEAD"]
    assert _allowed_git_command(
        None, subprocess.list2cmdline(spaced_command), spaced.resolve()
    )

    # And the hook itself admits the allowed shape while still refusing others.
    _enforce_offline("subprocess.Popen", (None, subprocess.list2cmdline(allowed)), repository)
    with pytest.raises(RuntimeError, match="process execution is disabled"):
        _enforce_offline(
            "subprocess.Popen",
            (None, subprocess.list2cmdline([sys.executable, "-V"])),
            repository,
        )


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


def test_checker_environment_resolves_git_when_defpath_lacks_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """os.defpath is a POSIX-only fallback; git must resolve from inherited PATH."""
    monkeypatch.setattr(os, "defpath", "/nonexistent")

    environment = _checker_environment()

    assert shutil.which("git", path=environment["PATH"]) is not None


def test_effectiveness_runs_require_a_clean_committed_analyzer() -> None:
    clean = {
        "adduce_source_commit": "a" * 40,
        "adduce_source_dirty": False,
        "corpus_harness_git_commit": "a" * 40,
        "corpus_harness_git_dirty": False,
        "corpus_harness_git_tracked": True,
    }
    require_reconstructable_analyzer(clean, "effectiveness")
    require_reconstructable_analyzer({}, "operational-only")

    for identity in (
        {"adduce_source_commit": None, "adduce_source_dirty": None},
        {"adduce_source_commit": "a" * 40, "adduce_source_dirty": True},
        {"adduce_source_commit": "short", "adduce_source_dirty": False},
    ):
        with pytest.raises(RunContractError, match="clean analyzer at a full Git commit"):
            require_reconstructable_analyzer(identity, "effectiveness")

    for identity in (
        {**clean, "corpus_harness_git_commit": "b" * 40},
        {**clean, "corpus_harness_git_dirty": True},
        {**clean, "corpus_harness_git_tracked": False},
    ):
        with pytest.raises(RunContractError, match="complete corpus harness"):
            require_reconstructable_analyzer(identity, "effectiveness")


def test_corpus_git_identity_requires_every_declared_file_tracked_and_clean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    corpus = repository / "corpus"
    corpus.mkdir(parents=True)
    (corpus / "protocol.txt").write_text("frozen protocol\n", encoding="utf-8")
    preregistration = corpus / "preregistration.json"
    preregistration.write_text("{}\n", encoding="utf-8")
    _git("init", "-q", cwd=repository)
    _git("config", "user.name", "Corpus Test", cwd=repository)
    _git("config", "user.email", "corpus@example.invalid", cwd=repository)
    _git("add", ".", cwd=repository)
    _git("commit", "-qm", "freeze corpus contract", cwd=repository)
    commit = _git("rev-parse", "HEAD", cwd=repository)
    monkeypatch.setattr(run_validation, "CORPUS_DIR", corpus)
    monkeypatch.setattr(run_validation, "PREREGISTRATION_PATH", preregistration)
    monkeypatch.setattr(run_validation, "REQUIRED_HARNESS_PATHS", ("protocol.txt",))

    identity = _corpus_git_identity()
    # The assertion message is only evaluated on failure, so these probes cost
    # nothing on a green run. They exist because this expectation has failed on
    # Windows and been attributed twice to a mechanism that later proved wrong;
    # the porcelain output and the effective config are the evidence that
    # distinguishes a real dirty worktree from a platform artefact. Every probe
    # is caught: a probe that raised would replace the AssertionError and
    # destroy exactly the evidence this exists to capture.
    def _probe(description: str, *arguments: str) -> str:
        try:
            completed = run_validation._git(*arguments, cwd=repository)
        except Exception as error:  # noqa: BLE001 - a probe must never mask the assertion
            return f"{description}=<probe failed: {error!r}>"
        return f"{description}={completed.stdout!r} (rc={completed.returncode})"

    assert identity == {
        "corpus_harness_git_commit": commit,
        "corpus_harness_git_dirty": False,
        "corpus_harness_git_tracked": True,
    }, "; ".join(
        [
            f"identity={identity}",
            _probe("isolated status", "status", "--porcelain=v1", "-uall"),
            _probe("effective config", "config", "-l", "--show-origin"),
        ]
    )

    (corpus / "protocol.txt").write_text("changed protocol\n", encoding="utf-8")
    assert _corpus_git_identity()["corpus_harness_git_dirty"] is True

    (corpus / "untracked.txt").write_text("not committed\n", encoding="utf-8")
    monkeypatch.setattr(
        run_validation,
        "REQUIRED_HARNESS_PATHS",
        ("protocol.txt", "untracked.txt"),
    )
    assert _corpus_git_identity()["corpus_harness_git_tracked"] is False


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
    source_tree_sha256 = _source_tree_sha256(package_dir)
    environment = os.environ.copy()
    environment["ADDUCE_CORPUS_SOURCE_ROOT"] = str(package_dir.parent)
    environment["ADDUCE_CORPUS_SOURCE_TREE_SHA256"] = source_tree_sha256

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
    assert payload["configuration"] == {
        "source": None,
        "repository_policy_honored": False,
        "profile": "default",
        "ignored_rules": [],
        "excluded_paths": [],
    }
    peak_rss = payload["corpus_execution"].pop("peak_rss")
    assert payload["corpus_execution"] == {
        "adduce_check_mode": "reviewer",
        "configuration_mode": "defaults-only-repository-config-disabled",
        "enforcement_scope": "scanner-regression-guard-not-os-sandbox",
        "network_policy": "python-audit-socket-deny",
        "plugins_enabled": False,
        "process_policy": "python-audit-read-only-git-metadata-only",
        "repository_policy_honored": False,
        "environment_policy": "minimal-no-host-credentials",
        "input_policy": "clone-root-symlink-containment",
        "adduce_source_tree_sha256": source_tree_sha256,
    }
    assert peak_rss["platform"] == sys.platform
    assert peak_rss["unit"] in {"bytes", "kibibytes", "unavailable"}
    assert payload["repository"]["input_file_count"] == payload["repository"]["files_scanned"]
    assert payload["repository"]["input_byte_count"] > 0


def test_checker_resolves_git_when_defpath_lacks_it(tmp_path: Path) -> None:
    """Reproduces the Windows condition (os.defpath has no git) on any platform."""
    commit = _make_repo(tmp_path / "repo")
    package_dir = Path(adduce.__file__).resolve().parent
    source_tree_sha256 = _source_tree_sha256(package_dir)
    environment = os.environ.copy()
    environment["ADDUCE_CORPUS_SOURCE_ROOT"] = str(package_dir.parent)
    environment["ADDUCE_CORPUS_SOURCE_TREE_SHA256"] = source_tree_sha256

    driver = tmp_path / "stub_defpath_driver.py"
    driver.write_text(
        "import os\n"
        "os.defpath = '/nonexistent'\n"
        "import runpy\n"
        "import sys\n"
        f"sys.argv = [{str(CHECKER)!r}, {str(tmp_path / 'repo')!r}]\n"
        f"runpy.run_path({str(CHECKER)!r}, run_name='__main__')\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, str(driver)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["repository"]["commit"] == commit


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


def test_clone_and_audit_agree_on_line_endings_under_ambient_autocrlf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """core.autocrlf=true reproduces on POSIX too; acquisition must ignore it."""
    origin = tmp_path / "origin"
    commit = _make_repo(origin)
    ambient_config = tmp_path / "ambient-gitconfig"
    ambient_config.write_text("[core]\n\tautocrlf = true\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(ambient_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    clones = tmp_path / "clones"
    row = {"id": "fixture", "cohort": "unvetted", "repo_url": str(origin), "commit_sha": ""}
    record = clone_one(row, clones)

    assert record["error"] is None, record["error"]
    assert record["resolved_sha"] == commit
    assert (clones / "fixture" / "README.md").read_bytes().count(b"\r\n") == 0

    repos = tmp_path / "repos.csv"
    repos.write_text(
        f"id,cohort,repo_url,commit_sha\nfixture,unvetted,{origin},\n",
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

    loaded, _, _ = load_clone_records(
        clones, repos.read_bytes(), [row], expected_clone_tool_sha256="a" * 64
    )
    assert loaded["fixture"]["worktree_sha256"] == record["worktree_sha256"]


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
