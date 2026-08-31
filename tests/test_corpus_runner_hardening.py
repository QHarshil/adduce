"""Security and attribution boundaries for the corpus execution harness."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Any, cast

import corpus.scripts.audit_sentinel_generation as generation
import corpus.scripts.check_builtin as check_builtin
import corpus.scripts.run_validation as run_validation
import pytest
from corpus.scripts.check_builtin import _allowed_git_command, _enforce_offline
from corpus.scripts.clone_repos import (
    CLONE_SCHEMA_VERSION,
    _submodule_state,
    clone_one,
    repository_tree_sha256,
)
from corpus.scripts.preregistration import clone_snapshot_set_sha256
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
    load_inventory_snapshot,
    require_reconstructable_analyzer,
)

import adduce

ROOT = Path(__file__).resolve().parent.parent
CHECKER = ROOT / "corpus" / "scripts" / "check_builtin.py"
RUNNER = ROOT / "corpus" / "scripts" / "run_validation.py"
FROZEN_CLONES_DIR = ROOT / "corpus" / "clones" / "pilot-2026-07-13"
# Frozen by protocol amendment 8 for the duration of the unlocked development
# interval; they move only under a further dated amendment.
FROZEN_CLONE_MANIFEST_SHA256 = (
    "2fcefb2503e60d4a04a0b4a343056a99ad00294ae3d5ee5c8f430d0b79435b94"
)
FROZEN_CLONE_SNAPSHOT_SET_SHA256 = (
    "9a171656825240a0b8371833f69c3b25b570e9bb74c4e6bd5f5cab618de06c31"
)


def _git(*args: str, cwd: Path) -> str:
    # Deliberately run under the ambient Git configuration, unlike the harness
    # helpers of the same name in clone_repos.py and run_validation.py. A
    # fixture built the way an operator's Git would build it is what makes a
    # CRLF worktree observable at all: ambient core.autocrlf=true normalises the
    # blob to LF, so a fixture holding CRLF is genuinely dirty and the harness
    # says so. Suppressing the ambient config here would store CRLF in the blob
    # too, hide the mismatch, and mask the defect that
    # test_ambient_autocrlf_makes_a_crlf_worktree_genuinely_dirty pins.
    completed = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return completed.stdout.strip()


def _write(path: Path, text: str) -> None:
    """Write LF bytes into a fixture worktree on every platform.

    ``Path.write_text`` opens in text mode, so on Windows it translates every
    ``\\n`` to ``\\r\\n``. A tracked fixture file written that way holds CRLF
    while ``git add`` under an ambient ``core.autocrlf=true`` stores an LF blob;
    the harness then audits with that config suppressed, compares CRLF against
    LF, and correctly reports the worktree as modified. Writing bytes keeps the
    fixture byte-identical on every platform, so its digests are too.
    """
    path.write_bytes(text.encode("utf-8"))


def _make_repo(path: Path, *, lfs_pointer: bool = False) -> str:
    path.mkdir(parents=True)
    _write(path / "README.md", "# Fixture\n")
    _write(path / "train.py", "print('fixture')\n")
    _write(
        path / "adduce.toml",
        'profile = "acm"\n'
        'ignore = ["R-DOC-001"]\n'
        'exclude = ["**/*.py"]\n'
        "fail_under = 100\n",
    )
    if lfs_pointer:
        _write(
            path / "weights.bin",
            "version https://git-lfs.github.com/spec/v1\n"
            "oid sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\n"
            "size 100\n",
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


def _committed_corpus(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    """A one-file corpus harness and one lock, both tracked and clean at HEAD."""
    repository = tmp_path / "repository"
    corpus = repository / "corpus"
    corpus.mkdir(parents=True)
    _write(corpus / "protocol.txt", "frozen protocol\n")
    preregistration = corpus / "preregistration.json"
    _write(preregistration, "{}\n")
    _git("init", "-q", cwd=repository)
    _git("config", "user.name", "Corpus Test", cwd=repository)
    _git("config", "user.email", "corpus@example.invalid", cwd=repository)
    _git("add", ".", cwd=repository)
    _git("commit", "-qm", "freeze corpus contract", cwd=repository)
    return repository, corpus, preregistration, _git("rev-parse", "HEAD", cwd=repository)


def test_corpus_git_identity_requires_every_declared_file_tracked_and_clean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, corpus, preregistration, commit = _committed_corpus(tmp_path)
    monkeypatch.setattr(run_validation, "CORPUS_DIR", corpus)
    monkeypatch.setenv(run_validation.LIVE_PREREGISTRATION_ENV, str(preregistration))
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

    _write(corpus / "protocol.txt", "changed protocol\n")
    assert _corpus_git_identity()["corpus_harness_git_dirty"] is True

    _write(corpus / "untracked.txt", "not committed\n")
    monkeypatch.setattr(
        run_validation,
        "REQUIRED_HARNESS_PATHS",
        ("protocol.txt", "untracked.txt"),
    )
    assert _corpus_git_identity()["corpus_harness_git_tracked"] is False

    _write(corpus / "protocol.txt", "frozen protocol\n")
    _write(preregistration, '{"retired": true}\n')
    monkeypatch.setattr(run_validation, "REQUIRED_HARNESS_PATHS", ("protocol.txt",))
    monkeypatch.delenv(run_validation.LIVE_PREREGISTRATION_ENV)
    assert _corpus_git_identity() == {
        "corpus_harness_git_commit": commit,
        "corpus_harness_git_dirty": False,
        "corpus_harness_git_tracked": True,
    }


def test_corpus_git_identity_audits_the_live_preregistration_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A resolved lock is audited with the harness, so it cannot be unregistered.

    This is the only thing forcing a live lock to be tracked and clean at the
    analyzer commit. Without it any readable path is accepted as a lock, and the
    run records a clean harness while binding a lock registered nowhere.
    """
    repository, corpus, preregistration, commit = _committed_corpus(tmp_path)
    monkeypatch.setattr(run_validation, "CORPUS_DIR", corpus)
    monkeypatch.setattr(run_validation, "REQUIRED_HARNESS_PATHS", ("protocol.txt",))

    monkeypatch.setenv(run_validation.LIVE_PREREGISTRATION_ENV, str(preregistration))
    _write(preregistration, '{"edited": true}\n')
    assert _corpus_git_identity() == {
        "corpus_harness_git_commit": commit,
        "corpus_harness_git_dirty": True,
        "corpus_harness_git_tracked": True,
    }
    _git("checkout", "--", "corpus/preregistration.json", cwd=repository)

    untracked = corpus / "successor.json"
    _write(untracked, "{}\n")
    monkeypatch.setenv(run_validation.LIVE_PREREGISTRATION_ENV, str(untracked))
    assert _corpus_git_identity()["corpus_harness_git_tracked"] is False

    outside = tmp_path / "outside-lock.json"
    _write(outside, "{}\n")
    monkeypatch.setenv(run_validation.LIVE_PREREGISTRATION_ENV, str(outside))
    assert _corpus_git_identity() == {
        "corpus_harness_git_commit": None,
        "corpus_harness_git_dirty": None,
        "corpus_harness_git_tracked": False,
    }


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
    loaded, _, _, declared = load_clone_records(clones, repos_data, [row])
    assert loaded["fixture"]["worktree_sha256"] == record["worktree_sha256"]
    assert declared == "a" * 64

    (clone / "untracked-empty-directory").mkdir()
    with pytest.raises(RunContractError, match="clone bytes changed"):
        load_clone_records(clones, repos_data, [row])
    (clone / "untracked-empty-directory").rmdir()

    _git("remote", "set-url", "origin", "https://example.invalid/changed", cwd=clone)
    with pytest.raises(RunContractError, match="clone origin changed"):
        load_clone_records(clones, repos_data, [row])


@pytest.mark.skipif(
    not FROZEN_CLONES_DIR.is_dir(),
    reason="local-only frozen corpus acquisition is not present in this checkout",
)
def test_runner_accepts_the_real_frozen_corpus_despite_its_stale_clone_tool_digest() -> None:
    """The 2026-07-13 acquisition predates 8799e09's clone_repos.py fix; must not refuse.

    A later patch to the clone harness cannot retroactively alter bytes an
    earlier version already acquired, so a disagreement here is evidence
    about tool history, never grounds to refuse the run.
    """
    repos_data, rows = load_inventory_snapshot(ROOT / "corpus" / "repos.csv")

    loaded, _, manifest_data, declared = load_clone_records(FROZEN_CLONES_DIR, repos_data, rows)

    manifest = json.loads(manifest_data)
    assert declared == manifest["clone_tool_sha256"]
    live_clone_tool_sha256 = sha256_file(ROOT / "corpus" / "scripts" / "clone_repos.py")
    # This is the real, historical mismatch the defect was about, not a
    # coincidental one: assert it rather than assume it.
    assert declared != live_clone_tool_sha256
    assert set(loaded) == {row["id"] for row in rows}

    # The gitignored half of protocol amendment 8's frozen set: verified
    # wherever the local corpus is present, and pinned in any checkout against
    # the retired r6 record by tests/test_corpus_methodology.py.
    assert hashlib.sha256(manifest_data).hexdigest() == FROZEN_CLONE_MANIFEST_SHA256
    assert clone_snapshot_set_sha256(manifest_data) == FROZEN_CLONE_SNAPSHOT_SET_SHA256


_MISSING_CLONE_TOOL_DIGEST = object()


@pytest.mark.parametrize(
    "clone_tool_sha256",
    [_MISSING_CLONE_TOOL_DIGEST, 12345, "a" * 63, "a" * 65, "g" * 64, "A" * 64, ""],
    ids=["missing", "non-string", "too-short", "too-long", "non-hex", "uppercase", "empty"],
)
def test_load_clone_records_refuses_a_malformed_clone_tool_digest(
    tmp_path: Path, clone_tool_sha256: object
) -> None:
    clones = tmp_path / "clones"
    commit = _make_repo(clones / "fixture")
    row = _inventory_row(commit)
    record = clone_one(row, clones)
    repos = tmp_path / "repos.csv"
    repos.write_text(
        "id,cohort,repo_url,commit_sha\n"
        f"fixture,unvetted,https://example.invalid/fixture,{commit}\n",
        encoding="utf-8",
    )
    manifest: dict[str, object] = {
        "clone_schema_version": CLONE_SCHEMA_VERSION,
        "repos_file_sha256": sha256_file(repos),
        "records": [record],
    }
    if clone_tool_sha256 is not _MISSING_CLONE_TOOL_DIGEST:
        manifest["clone_tool_sha256"] = clone_tool_sha256
    write_json(clones / "clones_manifest.json", manifest)

    with pytest.raises(RunContractError, match="well-formed clone-tool digest"):
        load_clone_records(clones, repos.read_bytes(), [row])


def test_ambient_autocrlf_makes_a_crlf_worktree_genuinely_dirty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A CRLF worktree over an LF blob is dirty, and acquisition is right to say so.

    This is the mechanism behind the Windows-only corpus-harness failures:
    fixtures written with ``Path.write_text`` hold CRLF there, ``git add``
    under the runner's ambient ``core.autocrlf=true`` stores LF, and the audit,
    which suppresses that config on purpose, sees the two disagree. The
    fixtures now write bytes, so this test plants the CRLF explicitly to keep
    the mechanism pinned on every platform. It also guards the ambient ``_git``
    above: making that helper hermetic would store CRLF in the blob, hide the
    mismatch, and turn this assertion green for the wrong reason.
    """
    ambient_config = tmp_path / "ambient-gitconfig"
    _write(ambient_config, "[core]\n\tautocrlf = true\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(ambient_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    clones = tmp_path / "clones"
    clone = clones / "fixture"
    clone.mkdir(parents=True)
    crlf = b"# Fixture\r\n"
    # The CRLF has to predate the commit, exactly as it does on Windows: Git
    # records the worktree size in the index, and a size that disagrees is
    # reported modified without the clean filter ever running. Planting the
    # CRLF afterwards would therefore look dirty to ambient Git too and would
    # test a different, easier thing.
    (clone / "README.md").write_bytes(crlf)
    _git("init", "-q", cwd=clone)
    _git("config", "user.name", "Corpus Test", cwd=clone)
    _git("config", "user.email", "corpus@example.invalid", cwd=clone)
    _git("remote", "add", "origin", "https://example.invalid/fixture", cwd=clone)
    _git("add", ".", cwd=clone)
    _git("commit", "-qm", "fixture", cwd=clone)
    commit = _git("rev-parse", "HEAD", cwd=clone)
    # Rewriting the identical bytes moves the mtime while leaving the size
    # alone, so Git compares content rather than trusting the cached stat.
    # Without it the result depends on residual racy-clean state left by the
    # commit, measured to hold 392 runs in 400; the residual 2% is the case
    # where the plant and the commit's index write straddle a second.
    (clone / "README.md").write_bytes(crlf)

    assert _git("cat-file", "-s", "HEAD:README.md", cwd=clone) == str(len(b"# Fixture\n"))
    assert (clone / "README.md").read_bytes() == crlf
    # Ambient Git applies the clean filter and so sees nothing wrong, which is
    # why the mismatch stays invisible until something audits without it.
    # --no-optional-locks is load-bearing rather than tidiness: a plain status
    # rewrites .git/index with refreshed stat data, and an entry Git no longer
    # treats as racily clean is short-circuited by the isolated audit below,
    # which then reports a clean clone and fails the assertion that follows.
    # Measured flaky at roughly 1 run in 80 without it, and always failing once
    # a second elapses between the write above and this line.
    assert _git("--no-optional-locks", "status", "--porcelain", cwd=clone) == ""

    record = clone_one(_inventory_row(commit), clones)

    assert record["dirty"] is True
    assert record["error"] == "clone has tracked or untracked changes"


def test_clone_and_audit_agree_on_line_endings_under_ambient_autocrlf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """core.autocrlf=true reproduces on POSIX too; acquisition must ignore it."""
    origin = tmp_path / "origin"
    commit = _make_repo(origin)
    ambient_config = tmp_path / "ambient-gitconfig"
    _write(ambient_config, "[core]\n\tautocrlf = true\n")
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

    loaded, _, _, _ = load_clone_records(clones, repos.read_bytes(), [row])
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


def _write_flavour_sensitive_tree(package_dir: Path) -> None:
    """Lay out names that separate the three candidate ordering rules.

    The uppercase-leading names divide the POSIX and Windows flavours, which
    casefold differently. ``sub.py`` sits beside the ``sub/`` directory, which
    divides segment order from whole-string order: ``.`` precedes ``/``, so the
    whole string interleaves the two while the segments keep them apart.
    """
    (package_dir / "sub").mkdir(parents=True)
    for relative, body in (
        ("README.md", "readme\n"),
        ("adapters.py", "adapters\n"),
        ("sub/Widget.py", "widget\n"),
        ("sub/parser.py", "parser\n"),
        ("sub.py", "sibling\n"),
    ):
        (package_dir / relative).write_text(body, encoding="utf-8")


def _relative_posix_paths(package_dir: Path) -> list[str]:
    return [
        path.relative_to(package_dir).as_posix()
        for path in package_dir.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    ]


def _digest_in_string_order(package_dir: Path) -> str:
    """Rebuild the digest ordering by the whole relative path instead of its segments."""
    digest = hashlib.sha256()
    for relative_posix in sorted(_relative_posix_paths(package_dir)):
        encoded = relative_posix.encode()
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update((package_dir / relative_posix).read_bytes())
    return digest.hexdigest()


def _digest_in_flavour_order(package_dir: Path, flavour: type[PurePath]) -> str:
    """Rebuild the source-tree digest using one path flavour's segment ordering."""
    digest = hashlib.sha256()
    for relative_posix in sorted(_relative_posix_paths(package_dir), key=flavour):
        encoded = relative_posix.encode()
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update((package_dir / relative_posix).read_bytes())
    return digest.hexdigest()


class _WindowsOrderedPath:
    """A real path that sorts the way it would on Windows, to model that host here."""

    def __init__(self, real: Path, root: Path) -> None:
        self._real = real
        self._root = root

    def __lt__(self, other: _WindowsOrderedPath) -> bool:
        return PureWindowsPath(self._real.relative_to(self._root)) < PureWindowsPath(
            other._real.relative_to(other._root)
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def relative_to(self, other: object) -> Path:
        return self._real.relative_to(self._root)

    def rglob(self, pattern: str) -> Iterator[_WindowsOrderedPath]:
        for path in self._real.rglob(pattern):
            yield _WindowsOrderedPath(path, self._root)


def test_source_tree_digest_orders_by_posix_path_not_by_host_flavour(tmp_path: Path) -> None:
    """Reproduces the Windows condition (Path comparison casefolds) on any platform."""
    package_dir = tmp_path / "adduce"
    _write_flavour_sensitive_tree(package_dir)

    posix_order = _digest_in_flavour_order(package_dir, PurePosixPath)
    windows_order = _digest_in_flavour_order(package_dir, PureWindowsPath)

    # Guards everything below against going vacuous: these assertions only say
    # something while the two flavours still order these names differently.
    # Python 3.10 through 3.14 compare paths part by part, so casefolding is
    # what separates them and a path separator never participates.
    assert posix_order != windows_order

    # A host whose paths sort the Windows way must still produce the POSIX
    # value. Sorting Path objects would return windows_order here instead, and
    # the digest names the analyzer a preregistration lock is bound to.
    windows_host = cast(Path, _WindowsOrderedPath(package_dir, package_dir))
    assert _source_tree_sha256(windows_host) == posix_order

    # Segments, not the whole relative string. Both are host-independent, but
    # they part company as soon as a file sits beside a directory of the same
    # stem, and only the segment order reproduces what the corpus recorded
    # before this helper was fixed.
    assert posix_order != _digest_in_string_order(package_dir)

    assert _source_tree_sha256(package_dir) == posix_order


def test_source_tree_digest_agrees_across_every_harness_copy(tmp_path: Path) -> None:
    """check_builtin runs sandboxed and keeps its own copy; the copies must not drift."""
    package_dir = tmp_path / "adduce"
    _write_flavour_sensitive_tree(package_dir)
    posix_order = _digest_in_flavour_order(package_dir, PurePosixPath)
    windows_host = cast(Path, _WindowsOrderedPath(package_dir, package_dir))

    copies = {
        "run_validation": _source_tree_sha256,
        "check_builtin": check_builtin._source_tree_sha256,
        "audit_sentinel_generation": generation._source_tree_sha256,
    }

    # Each copy is checked against the Windows-ordered tree as well, because a
    # copy that still sorts Path objects returns the same value as a fixed one
    # on a POSIX host and parts company only there.
    for name, digest_of in copies.items():
        assert digest_of(package_dir) == posix_order, name
        assert digest_of(windows_host) == posix_order, name
