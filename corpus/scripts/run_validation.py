#!/usr/bin/env python3
"""Run built-in Adduce rules over an attributable, immutable corpus clone set.

Every repository produces exactly one combined row. Successful scans also
produce raw JSON. Acquisition failures, partial acquisitions, scanner
crashes, malformed output, and timeouts remain distinct in the evidence. A
run is analyzable only after its integrity manifest and completion marker
have been written.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import io
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import adduce
from adduce.rules import discover_rules

if __package__:
    from .claim_ground_truth import ClaimGroundTruthError, validate_ground_truth_bytes
    from .clone_repos import (
        CLONE_SCHEMA_VERSION,
        CORPUS_DIR,
        MANIFEST_NAME,
        repository_tree_sha256,
    )
    from .run_contract import (
        REQUIRED_HARNESS_PATHS,
        RunContractError,
        artifact_records,
        ensure_new_output_directory,
        ensure_output_outside,
        finalize_run,
        load_json_object_bytes,
        validate_badged_provenance_bytes,
        validate_inventory_rows,
        validate_raw_payload,
        write_json,
    )
else:
    from claim_ground_truth import ClaimGroundTruthError, validate_ground_truth_bytes
    from clone_repos import (
        CLONE_SCHEMA_VERSION,
        CORPUS_DIR,
        MANIFEST_NAME,
        repository_tree_sha256,
    )
    from run_contract import (
        REQUIRED_HARNESS_PATHS,
        RunContractError,
        artifact_records,
        ensure_new_output_directory,
        ensure_output_outside,
        finalize_run,
        load_json_object_bytes,
        validate_badged_provenance_bytes,
        validate_inventory_rows,
        validate_raw_payload,
        write_json,
    )

BUILTIN_CHECKER = Path(__file__).with_name("check_builtin.py")
CONFIGURATION_MODE = "defaults-only-repository-config-disabled"
SUCCESS_STATUSES = frozenset({"succeeded", "succeeded_with_partial_acquisition"})

_GIT_ENVIRONMENT_KEYS = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_DIR",
        "GIT_INDEX_FILE",
        "GIT_NAMESPACE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_WORK_TREE",
    }
)

BASE_COLUMNS = [
    "id",
    "cohort",
    "badge_type",
    "repo_url",
    "requested_sha",
    "resolved_sha",
    "worktree_sha256",
    "repository_tree_sha256",
    "input_file_count",
    "input_byte_count",
    "clone_status",
    "acquisition_status",
    "submodule_state",
    "git_lfs_state",
    "git_lfs_pointer_count",
    "run_status",
    "acquisition_failed",
    "score",
    "tier",
    "reviewer_time_bucket",
    "findings_fail",
    "findings_partial",
    "crash",
    "timeout",
    "runtime_seconds",
    "peak_rss_value",
    "peak_rss_unit",
    "peak_rss_source",
    "error",
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def runtime_context() -> dict[str, object]:
    """Capture machine-local context without probing outside the standard library."""
    logical_cpu_count = os.cpu_count()
    logical_cpu = {
        "available": logical_cpu_count is not None and logical_cpu_count > 0,
        "value": logical_cpu_count
        if logical_cpu_count is not None and logical_cpu_count > 0
        else None,
        "unit": "count"
        if logical_cpu_count is not None and logical_cpu_count > 0
        else "unavailable",
        "source": "os.cpu_count"
        if logical_cpu_count is not None and logical_cpu_count > 0
        else "unavailable",
    }
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        page_count = os.sysconf("SC_PHYS_PAGES")
        memory_bytes = int(page_size) * int(page_count)
    except (AttributeError, OSError, TypeError, ValueError):
        memory_bytes = 0
    physical_memory = {
        "available": memory_bytes > 0,
        "value": memory_bytes if memory_bytes > 0 else None,
        "unit": "bytes" if memory_bytes > 0 else "unavailable",
        "source": ("os.sysconf(SC_PAGE_SIZE*SC_PHYS_PAGES)" if memory_bytes > 0 else "unavailable"),
    }
    return {
        "logical_cpu": logical_cpu,
        "physical_memory": physical_memory,
        "cache_policy": {
            "filesystem_cache": "not-cleared",
            "scanner_process": "fresh-process-per-repository",
            "adduce_application_cache": "disabled-default-offline-path",
        },
        "peak_rss_platform": sys.platform,
        "input_measurement_policy": "adduce-scanned-regular-files-summed-by-reported-size",
    }


def _git_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for key in list(environment):
        if key in _GIT_ENVIRONMENT_KEYS or key.startswith("GIT_CONFIG_"):
            environment.pop(key, None)
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
        }
    )
    return environment


def _checker_environment() -> dict[str, str]:
    """Build a minimal scanner environment without inherited credentials."""
    retained = {
        key: os.environ[key]
        for key in (
            "COMSPEC",
            "LANG",
            "LC_CTYPE",
            "PATHEXT",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
            "TMPDIR",
            "WINDIR",
        )
        if key in os.environ
    }
    retained.update(
        {
            "PATH": os.defpath,
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return retained


def _validate_symlink_containment(root: Path) -> None:
    """Reject broken links and links that resolve beyond the acquired worktree."""
    try:
        resolved_root = root.resolve(strict=True)
    except OSError as exc:
        raise RunContractError(f"cannot resolve acquired repository {root}: {exc}") from exc
    for current, directories, files in os.walk(root, followlinks=False):
        if Path(current) == root and ".git" in directories:
            directories.remove(".git")
        for name in [*directories, *files]:
            candidate = Path(current) / name
            if not candidate.is_symlink():
                continue
            try:
                candidate.resolve(strict=True).relative_to(resolved_root)
            except (OSError, ValueError) as exc:
                relative = candidate.relative_to(root).as_posix()
                raise RunContractError(
                    f"repository symlink is broken or escapes its clone root: {relative}"
                ) from exc


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
        env=_git_environment(),
    )


def _source_tree_sha256(package_dir: Path) -> str:
    """Hash installed Adduce package bytes, independent of Git cleanliness."""
    digest = hashlib.sha256()
    files = sorted(
        path
        for path in package_dir.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    )
    for path in files:
        relative = path.relative_to(package_dir).as_posix().encode()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def source_identity() -> dict[str, object]:
    """Describe the exact built-in analyzer used by this run."""
    package_dir = Path(adduce.__file__).resolve().parent
    head = _git("rev-parse", "HEAD", cwd=package_dir)
    status = _git("status", "--porcelain", "--untracked-files=all", "--", ".", cwd=package_dir)
    commit = head.stdout.strip().lower() if head.returncode == 0 else None
    dirty = bool(status.stdout.strip()) if status.returncode == 0 else None

    dependency_versions: dict[str, str] = {}
    for distribution in ("typer", "rich", "jinja2", "pyyaml", "libcst", "tomli"):
        try:
            dependency_versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            dependency_versions[distribution] = "not-installed"

    rule_ids = [rule.id for rule in discover_rules(include_plugins=False)]
    return {
        "adduce_version": adduce.__version__,
        "adduce_source_commit": commit,
        "adduce_source_dirty": dirty,
        "adduce_source_tree_sha256": _source_tree_sha256(package_dir),
        "builtin_rule_ids": rule_ids,
        "builtin_rule_count": len(rule_ids),
        "dependency_versions": dependency_versions,
    }


def harness_snapshot(
    *, badged_provenance: Path | None = None
) -> tuple[dict[str, object], dict[str, bytes]]:
    """Snapshot every script and protocol file that governs corpus interpretation."""
    snapshots: dict[str, bytes] = {}
    for name in REQUIRED_HARNESS_PATHS:
        path = (
            badged_provenance
            if name == "badged-provenance.csv" and badged_provenance is not None
            else CORPUS_DIR / Path(*name.split("/"))
        )
        try:
            snapshots[name] = path.read_bytes()
        except OSError as exc:
            raise RunContractError(f"cannot snapshot corpus harness file {path}: {exc}") from exc
    files = {name: hashlib.sha256(data).hexdigest() for name, data in snapshots.items()}
    digest = hashlib.sha256()
    for name, file_digest in sorted(files.items()):
        digest.update(name.encode())
        digest.update(file_digest.encode())
    identity: dict[str, object] = {
        "corpus_harness_sha256": digest.hexdigest(),
        "corpus_harness_files": files,
    }
    return identity, snapshots


def harness_identity(*, badged_provenance: Path | None = None) -> dict[str, object]:
    """Describe the corpus code whose behavior produces the run artifacts."""
    identity, _ = harness_snapshot(badged_provenance=badged_provenance)
    return identity


def load_inventory_snapshot(path: Path) -> tuple[bytes, list[dict[str, str]]]:
    """Read and parse one immutable inventory byte snapshot."""
    try:
        data = path.read_bytes()
        text = data.decode("utf-8")
        reader = csv.DictReader(io.StringIO(text, newline=""))
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise RunContractError(f"cannot read repository inventory {path}: {exc}") from exc
    required = {"id", "cohort", "repo_url", "commit_sha"}
    if len(fields) != len(set(fields)) or not required.issubset(fields):
        raise RunContractError("repository inventory has a missing or duplicate column")
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise RunContractError("repository inventory has a short row or surplus cells")
    normalized = [{str(key): str(value) for key, value in row.items()} for row in rows]
    validate_inventory_rows(normalized)
    return data, normalized


def _normalise_repo_url(value: str) -> str:
    return value.strip().rstrip("/").removesuffix(".git")


def load_clone_records(
    clones_dir: Path,
    repos_data: bytes,
    rows: list[dict[str, str]],
    *,
    expected_clone_tool_sha256: str,
) -> tuple[dict[str, dict], Path, bytes]:
    """Verify that clone metadata and current worktrees still agree."""
    manifest_path = clones_dir / MANIFEST_NAME
    try:
        manifest_data = manifest_path.read_bytes()
    except OSError as exc:
        raise RunContractError(f"cannot read clone manifest {manifest_path}: {exc}") from exc
    manifest = load_json_object_bytes(manifest_data, str(manifest_path))
    if manifest.get("clone_schema_version") != CLONE_SCHEMA_VERSION:
        raise RunContractError("unsupported or missing clone-manifest schema")
    if manifest.get("repos_file_sha256") != hashlib.sha256(repos_data).hexdigest():
        raise RunContractError("clone manifest was produced from different repository metadata")
    if manifest.get("clone_tool_sha256") != expected_clone_tool_sha256:
        raise RunContractError("clone manifest was produced by a different clone harness")
    records = manifest.get("records")
    if not isinstance(records, list):
        raise RunContractError("clone manifest records must be a list")

    by_id: dict[str, dict] = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("id"), str):
            raise RunContractError("invalid clone manifest record")
        if record["id"] in by_id:
            raise RunContractError(f"duplicate clone manifest ID: {record['id']}")
        by_id[record["id"]] = record
    expected_ids = {row["id"] for row in rows}
    if set(by_id) != expected_ids:
        raise RunContractError("clone manifest IDs do not match repos.csv")

    for row in rows:
        record = by_id[row["id"]]
        if (
            record.get("cohort") != row["cohort"]
            or record.get("repo_url") != row["repo_url"]
            or (record.get("requested_sha") or "") != row["commit_sha"]
        ):
            raise RunContractError(f"clone manifest metadata mismatch for {row['id']}")
        if record.get("error"):
            if record.get("acquisition_status") != "failed":
                raise RunContractError(
                    f"failed clone record has inconsistent acquisition status: {row['id']}"
                )
            continue
        clone_path = clones_dir / row["id"]
        if not (clone_path / ".git").is_dir():
            raise RunContractError(f"successful clone record has no Git worktree: {row['id']}")
        if record.get("acquisition_status") not in {"complete", "partial"}:
            raise RunContractError(f"invalid acquisition status for {row['id']}")
        expected_origin = _normalise_repo_url(row["repo_url"])
        recorded_origin = _normalise_repo_url(str(record.get("origin_url") or ""))
        origin = _git("remote", "get-url", "origin", cwd=clone_path)
        observed_origin = _normalise_repo_url(origin.stdout) if origin.returncode == 0 else ""
        if recorded_origin != expected_origin or observed_origin != expected_origin:
            raise RunContractError(f"clone origin changed after manifest creation: {row['id']}")
        head = _git("rev-parse", "HEAD", cwd=clone_path)
        observed_sha = head.stdout.strip().lower() if head.returncode == 0 else ""
        if observed_sha != record.get("resolved_sha"):
            raise RunContractError(f"clone commit changed after manifest creation: {row['id']}")
        tree = _git("rev-parse", "HEAD^{tree}", cwd=clone_path)
        observed_tree = tree.stdout.strip().lower() if tree.returncode == 0 else ""
        if observed_tree != record.get("git_tree_sha"):
            raise RunContractError(f"clone Git tree changed after manifest creation: {row['id']}")
        dirty = _git("status", "--porcelain", "--untracked-files=all", cwd=clone_path)
        if dirty.returncode != 0 or dirty.stdout.strip():
            raise RunContractError(f"clone is dirty after manifest creation: {row['id']}")
        recorded_worktree = record.get("worktree_sha256")
        if not isinstance(recorded_worktree, str) or len(recorded_worktree) != 64:
            raise RunContractError(f"clone manifest lacks worktree digest for {row['id']}")
        if repository_tree_sha256(clone_path) != recorded_worktree:
            raise RunContractError(f"clone bytes changed after manifest creation: {row['id']}")
        _validate_symlink_containment(clone_path)
    return by_id, manifest_path, manifest_data


def check_repo(repo_path: Path, timeout: int) -> tuple[dict | None, str | None, str | None, float]:
    """Return payload, error, typed failure state, and runtime for one repository."""
    started = time.monotonic()
    try:
        repository = repo_path.resolve(strict=True)
    except OSError as exc:
        return (
            None,
            f"cannot resolve repository: {exc}",
            "contract_failed",
            time.monotonic() - started,
        )
    try:
        environment = _checker_environment()
        package_dir = Path(adduce.__file__).resolve().parent
        environment["ADDUCE_CORPUS_SOURCE_ROOT"] = str(package_dir.parent)
        environment["ADDUCE_CORPUS_SOURCE_TREE_SHA256"] = _source_tree_sha256(package_dir)
        completed = subprocess.run(
            [sys.executable, str(BUILTIN_CHECKER), str(repository)],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=repository,
            env=environment,
        )
    except OSError as exc:
        return (
            None,
            f"cannot start scanner: {exc}",
            "scanner_crash",
            time.monotonic() - started,
        )
    except subprocess.TimeoutExpired:
        return (
            None,
            f"timed out after {timeout}s",
            "scanner_timeout",
            time.monotonic() - started,
        )
    runtime = time.monotonic() - started
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        return None, f"exit {completed.returncode}: {detail[:300]}", "scanner_crash", runtime
    try:
        payload = load_json_object_bytes(completed.stdout.encode(), "scanner output")
    except RunContractError as exc:
        return None, str(exc), "contract_failed", runtime
    return payload, None, None, runtime


def _category_key(name: str) -> str:
    return "cat_" + name.lower().replace(" & ", "_").replace(" ", "_")


def summarise_payload(payload: dict) -> dict:
    row = {
        "score": payload["total"],
        "tier": payload["tier"],
        "reviewer_time_bucket": payload["reviewer_time"]["bucket"],
        "findings_fail": sum(1 for finding in payload["findings"] if finding["status"] == "fail"),
        "findings_partial": sum(
            1 for finding in payload["findings"] if finding["status"] == "partial"
        ),
    }
    for category in payload["categories"]:
        row[_category_key(category["category"])] = category["percentage"]
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repos", type=Path, default=CORPUS_DIR / "repos.csv")
    parser.add_argument("--clones", type=Path, default=CORPUS_DIR / "clones")
    parser.add_argument(
        "--claims",
        type=Path,
        default=None,
        help="frozen claim-ground-truth JSON required for an effectiveness run",
    )
    parser.add_argument(
        "--badged-provenance",
        type=Path,
        default=CORPUS_DIR / "badged-provenance.csv",
        help="badge mapping CSV copied into and bound by the run",
    )
    parser.add_argument(
        "--operational-only",
        action="store_true",
        help="permit a run with no claim ground truth; results cannot support effectiveness claims",
    )
    parser.add_argument(
        "--out", type=Path, default=None, help="defaults to corpus/outputs/<adduce-version>/"
    )
    parser.add_argument(
        "--timeout", type=int, default=120, help="per-repository timeout in seconds"
    )
    args = parser.parse_args()

    if args.timeout <= 0:
        sys.exit("--timeout must be positive")
    if args.claims is not None and args.operational_only:
        sys.exit("--claims and --operational-only are mutually exclusive")
    if args.claims is None and not args.operational_only:
        sys.exit("provide --claims, or explicitly select --operational-only")
    claim_ground_truth_sha256: str | None = None
    claim_truth_data: bytes | None = None
    analysis_scope = "operational-only" if args.operational_only else "effectiveness"
    try:
        harness_meta, harness_files = harness_snapshot(badged_provenance=args.badged_provenance)
        identity = {**source_identity(), **harness_meta}
        version = str(identity["adduce_version"])
        out_dir = args.out or (CORPUS_DIR / "outputs" / version)
        ensure_output_outside(out_dir, [args.clones])
        repos_data, rows = load_inventory_snapshot(args.repos)
        if not rows:
            raise RunContractError("no repositories in the corpus file")
        validate_badged_provenance_bytes(harness_files["badged-provenance.csv"], rows)
        clone_records, clone_manifest_path, clone_manifest_data = load_clone_records(
            args.clones,
            repos_data,
            rows,
            expected_clone_tool_sha256=str(
                hashlib.sha256(harness_files["scripts/clone_repos.py"]).hexdigest()
            ),
        )
        if args.claims is not None:
            try:
                claim_truth_data = args.claims.read_bytes()
            except OSError as exc:
                raise ClaimGroundTruthError(
                    f"cannot read claim ground truth {args.claims}: {exc}"
                ) from exc
            claim_truth = validate_ground_truth_bytes(
                claim_truth_data,
                args.claims,
                args.repos,
                args.clones,
                inventory_data=repos_data,
                clone_manifest_data=clone_manifest_data,
            )
            frozen_at = datetime.fromisoformat(str(claim_truth["frozen_at"]).replace("Z", "+00:00"))
            if frozen_at >= datetime.now(timezone.utc):
                raise ClaimGroundTruthError(
                    "claim ground truth must be frozen before the corpus run starts"
                )
            claim_ground_truth_sha256 = hashlib.sha256(claim_truth_data).hexdigest()
        ensure_new_output_directory(out_dir)
    except (ClaimGroundTruthError, RunContractError) as exc:
        sys.exit(str(exc))

    started_at = _utc_now()
    run_stamp = started_at.replace("-", "").replace(":", "").replace(".", "").replace("+0000", "Z")
    run_id = f"{version}-{str(identity['adduce_source_tree_sha256'])[:12]}-{run_stamp}"
    raw_dir = out_dir / "raw_json"
    input_dir = out_dir / "inputs"
    harness_dir = out_dir / "harness"
    raw_dir.mkdir()
    input_dir.mkdir()
    harness_dir.mkdir()
    (input_dir / "repos.csv").write_bytes(repos_data)
    (input_dir / MANIFEST_NAME).write_bytes(clone_manifest_data)
    if claim_truth_data is not None:
        (input_dir / "claim_ground_truth.json").write_bytes(claim_truth_data)
    for relative, data in harness_files.items():
        target = harness_dir / Path(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    output_rows: list[dict] = []
    for repo in rows:
        clone_record = clone_records[repo["id"]]
        base = {
            "id": repo["id"],
            "cohort": repo["cohort"],
            "badge_type": repo.get("badge_type", ""),
            "repo_url": repo["repo_url"],
            "requested_sha": clone_record.get("requested_sha") or "",
            "resolved_sha": clone_record.get("resolved_sha") or "",
            "worktree_sha256": clone_record.get("worktree_sha256") or "",
            "repository_tree_sha256": "",
            "input_file_count": "",
            "input_byte_count": "",
            "clone_status": clone_record.get("status") or "",
            "acquisition_status": clone_record.get("acquisition_status") or "failed",
            "submodule_state": clone_record.get("submodule_state") or "unavailable",
            "git_lfs_state": clone_record.get("git_lfs_state") or "unavailable",
            "git_lfs_pointer_count": clone_record.get("git_lfs_pointer_count"),
            "run_status": "",
            "acquisition_failed": False,
            "crash": False,
            "timeout": False,
            "peak_rss_value": "",
            "peak_rss_unit": "unavailable",
            "peak_rss_source": "unavailable",
            "error": "",
        }
        clone_path = args.clones / repo["id"]
        if clone_record.get("error"):
            base.update(
                run_status="acquisition_failed",
                acquisition_failed=True,
                error=f"clone {clone_record.get('status')}: {clone_record['error']}",
            )
            print(
                f"{repo['cohort']}: {repo['id']} — clone unavailable; acquisition failed",
                file=sys.stderr,
            )
            output_rows.append(base)
            continue

        tree_before = repository_tree_sha256(clone_path)
        base["repository_tree_sha256"] = tree_before
        payload, error, failure_status, runtime = check_repo(clone_path, args.timeout)
        base["runtime_seconds"] = round(runtime, 3)
        tree_after = repository_tree_sha256(clone_path)
        if tree_after != tree_before:
            payload = None
            error = "offline check modified repository bytes"
            failure_status = "contract_failed"
        if payload is None:
            status = failure_status or "contract_failed"
            timed_out = status == "scanner_timeout"
            base.update(run_status=status, crash=True, timeout=timed_out, error=error or "")
            print(
                f"{repo['cohort']}: {repo['id']} — {error}; recorded as crash",
                file=sys.stderr,
            )
        else:
            try:
                summary = validate_raw_payload(
                    payload,
                    repo["id"],
                    str(base["resolved_sha"]),
                    version,
                    str(identity["adduce_source_tree_sha256"]),
                    sys.platform,
                    set(cast(list[str], identity["builtin_rule_ids"])),
                )
            except RunContractError as exc:
                base.update(run_status="contract_failed", crash=True, error=str(exc))
                print(
                    f"{repo['cohort']}: {repo['id']} — invalid scanner contract; "
                    "recorded as contract failure",
                    file=sys.stderr,
                )
            else:
                base.update({key: value for key, value in summary.items() if key != "categories"})
                base.update(summary["categories"])
                base["run_status"] = (
                    "succeeded_with_partial_acquisition"
                    if base["acquisition_status"] == "partial"
                    else "succeeded"
                )
                write_json(raw_dir / f"{repo['id']}.json", payload)
                print(f"{repo['cohort']}: {repo['id']} — score {payload['total']} ({runtime:.0f}s)")
        output_rows.append(base)

    ending_identity = {
        **source_identity(),
        **harness_identity(badged_provenance=args.badged_provenance),
    }
    identity_keys = (
        "adduce_version",
        "adduce_source_commit",
        "adduce_source_dirty",
        "adduce_source_tree_sha256",
        "builtin_rule_ids",
        "builtin_rule_count",
        "dependency_versions",
        "corpus_harness_sha256",
        "corpus_harness_files",
    )
    if any(ending_identity.get(key) != identity.get(key) for key in identity_keys):
        sys.exit("analyzer or corpus harness changed while the run was in progress")

    cat_columns = sorted({key for row in output_rows for key in row if key.startswith("cat_")})
    combined = out_dir / "combined.csv"
    with combined.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=[*BASE_COLUMNS, *cat_columns], restval="")
        writer.writeheader()
        writer.writerows(output_rows)

    relative_artifacts = [
        "combined.csv",
        "inputs/repos.csv",
        f"inputs/{MANIFEST_NAME}",
        *(["inputs/claim_ground_truth.json"] if args.claims is not None else []),
        *[f"harness/{name}" for name in REQUIRED_HARNESS_PATHS],
        *[
            f"raw_json/{row['id']}.json"
            for row in output_rows
            if row["run_status"] in SUCCESS_STATUSES
        ],
    ]
    metadata = {
        **identity,
        "run_id": run_id,
        "started_at": started_at,
        "completed_at": _utc_now(),
        "execution_mode": "offline-builtins-only",
        "environment_policy": "minimal-no-host-credentials",
        "input_policy": "clone-root-symlink-containment",
        "analysis_scope": analysis_scope,
        "configuration_mode": CONFIGURATION_MODE,
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
        },
        "platform": platform.platform(),
        "runtime_context": runtime_context(),
        "invocation": {
            "repos": str(args.repos),
            "clones": str(args.clones),
            "claims": str(args.claims) if args.claims is not None else None,
            "badged_provenance": str(args.badged_provenance),
            "operational_only": args.operational_only,
            "out": str(out_dir),
            "timeout_seconds": args.timeout,
            "checker": str(BUILTIN_CHECKER),
        },
        "timeout_seconds": args.timeout,
        "repos_file": str(args.repos),
        "repos_file_sha256": hashlib.sha256(repos_data).hexdigest(),
        "clone_manifest": str(clone_manifest_path),
        "clone_manifest_sha256": hashlib.sha256(clone_manifest_data).hexdigest(),
        "claim_ground_truth_sha256": claim_ground_truth_sha256,
        "n_repositories": len(output_rows),
        "n_acquisition_failed": sum(1 for row in output_rows if row["acquisition_failed"]),
        "n_acquisition_partial": sum(
            1 for row in output_rows if row["acquisition_status"] == "partial"
        ),
        "n_crashed": sum(1 for row in output_rows if row["crash"]),
        "n_scanner_crashed": sum(
            1 for row in output_rows if row["run_status"] in {"scanner_crash", "scanner_timeout"}
        ),
        "n_contract_failed": sum(
            1 for row in output_rows if row["run_status"] == "contract_failed"
        ),
        "n_succeeded": sum(1 for row in output_rows if row["run_status"] in SUCCESS_STATUSES),
        "artifacts": artifact_records(out_dir, relative_artifacts),
    }
    try:
        finalize_run(out_dir, metadata)
    except RunContractError as exc:
        sys.exit(str(exc))
    print(f"\nwrote {combined} ({len(output_rows)} rows, adduce {version})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
