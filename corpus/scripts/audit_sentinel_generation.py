#!/usr/bin/env python3
"""Generate and validate evidence-ledger drafts for the three pilot sentinels.

The procedure is intentionally bounded: FRL, SimCSE, and Torchtune; the
bundled NeurIPS checklist and ACM appendix; strict evidence mode; built-in
rules only.  Repository code is never executed.  A child process re-runs the
exact source bound into an immutable corpus run under the corpus socket,
process, and write audit guard.  Generation is accepted only when that static
scan matches the run's raw JSON and both generated artifacts pass an
independent ledger audit.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

if __package__:
    from .clone_repos import repository_tree_sha256
    from .run_contract import (
        RunContractError,
        ensure_output_outside,
        require_current_harness_file,
        sha256_file,
        validate_run,
    )
else:
    from clone_repos import repository_tree_sha256
    from run_contract import (
        RunContractError,
        ensure_output_outside,
        require_current_harness_file,
        sha256_file,
        validate_run,
    )

SCHEMA_VERSION = 1
PROCEDURE = "adduce-sentinel-generation-audit"
SENTINELS = ("frl", "simcse", "torchtune")
PROFILE = "neurips"
MODE = "strict"
MANIFEST_NAME = "generation-audit.json"
SUCCESS_MARKER = "_GENERATION_AUDIT_SUCCESS"
FAILURE_MARKER = "_GENERATION_AUDIT_FAILED"
INCOMPLETE_MARKER = "_GENERATION_AUDIT_INCOMPLETE"
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "generation-audit.schema.json"
SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"
BUILTIN_CHECKER = Path(__file__).resolve().with_name("check_builtin.py")
SCRIPT_HARNESS_PATH = "scripts/audit_sentinel_generation.py"
SCHEMA_HARNESS_PATH = "generation-audit.schema.json"
CHECKER_HARNESS_PATH = "scripts/check_builtin.py"
ARTIFACT_NAMES = ("checklist-neurips.md", "artifact_appendix.md")
LEDGER_NAME = "evidence-ledger.json"
AFFIRMATIVE_ANSWERS = frozenset({"yes", "partial"})
ANSWER_TEXT = {
    "yes": "Yes (draft)",
    "partial": "Partial (draft)",
    "not_detected": "Not detected (draft)",
    "unknown": "Unknown (draft)",
    "author_input_required": (
        "[AUTHOR REVIEW REQUIRED] — depends on information outside the repository"
    ),
}
EVIDENCE_STRENGTHS = frozenset(
    {
        "direct",
        "inferred",
        "manifest_author_confirmed",
        "online_resolved",
        "dynamic_verified",
    }
)
OFFLINE_EVIDENCE_STRENGTHS = frozenset(
    {"direct", "inferred", "manifest_author_confirmed"}
)
ANSWER_LEVELS = frozenset(ANSWER_TEXT)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_EXECUTION_CLAIM_RE = re.compile(
    r"\b(?:results? (?:reproduce|were reproduced|have been reproduced|were verified)"
    r"|verified by execution|runs agree|successfully (?:ran|executed|reproduced)"
    r"|we (?:ran|executed|reproduced))\b",
    re.IGNORECASE,
)
_COUNTS_KEYS = frozenset(
    {
        "evidence_backed",
        "partial",
        "author_input_required",
        "not_detected",
        "unknown",
        "conflicts",
    }
)
_PROVENANCE_KEYS = frozenset(
    {
        "adduce_version",
        "command",
        "profile",
        "mode",
        "repo_commit",
        "generated_at",
        "corpus_run_id",
        "raw_json_sha256",
        "adduce_source_tree_sha256",
        "plugins_enabled",
        "generated_text_policy",
        "deterministic_generation",
        "generation_execution_mode",
    }
)
_GENERATION_POLICY = {
    "profile": PROFILE,
    "mode": MODE,
    "generated_text_policy": "evidence_only",
    "deterministic_generation": True,
    "plugins_enabled": False,
    "network_policy": "python-audit-socket-deny",
    "process_policy": "python-audit-read-only-git-metadata-only",
    "filesystem_policy": "python-audit-write-deny",
    "warning_policy": "ignore-syntaxwarning-only",
    "input_policy": "validated-clone-read-only-static-regeneration",
}


class GenerationAuditError(ValueError):
    """A sentinel generation bundle is unsupported, drifted, or unsafe."""


@dataclass(frozen=True)
class RepositoryInput:
    """Exact run and clone evidence for one predeclared sentinel."""

    repo_id: str
    clone: Path
    commit: str
    worktree_sha256: str
    raw_json: Path
    raw_json_sha256: str


@dataclass(frozen=True)
class GenerationContext:
    """Validated immutable inputs needed by generation and later validation."""

    run: Path
    clones: Path
    metadata: dict[str, Any]
    run_meta_sha256: str
    script_sha256: str
    schema_sha256: str
    checker_sha256: str
    repositories: dict[str, RepositoryInput]


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GenerationAuditError(f"JSON object contains duplicate key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise GenerationAuditError(f"JSON contains non-finite numeric constant {value}")


def _load_json_bytes(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise GenerationAuditError(f"{label} is not valid UTF-8: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise GenerationAuditError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise GenerationAuditError(f"{label} must contain a JSON object")
    return value


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return _load_json_bytes(path.read_bytes(), str(path))
    except OSError as exc:
        raise GenerationAuditError(f"cannot read {path}: {exc}") from exc


def _write_json(path: Path, value: Any) -> None:
    try:
        rendered = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
        path.write_text(rendered, encoding="utf-8", newline="\n")
    except (OSError, TypeError, ValueError) as exc:
        raise GenerationAuditError(f"cannot write strict JSON to {path}: {exc}") from exc


def _write_text(path: Path, value: str) -> None:
    try:
        path.write_text(value.rstrip("\n") + "\n", encoding="utf-8", newline="\n")
    except OSError as exc:
        raise GenerationAuditError(f"cannot write {path}: {exc}") from exc


def _exact_keys(value: dict[str, Any], expected: set[str] | frozenset[str], label: str) -> None:
    if set(value) != set(expected):
        raise GenerationAuditError(
            f"{label} fields are invalid "
            f"(missing={sorted(set(expected) - set(value))}, "
            f"extra={sorted(set(value) - set(expected))})"
        )


def _finite_number(value: object, label: str, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GenerationAuditError(f"{label} must be numeric")
    observed = float(value)
    if not math.isfinite(observed) or not minimum <= observed <= maximum:
        raise GenerationAuditError(
            f"{label} must be finite and between {minimum:g} and {maximum:g}"
        )
    return observed


def _timestamp(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise GenerationAuditError(f"{label} must be a non-empty timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GenerationAuditError(f"{label} is not an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GenerationAuditError(f"{label} lacks a UTC offset")
    return value


def _safe_relative(value: str) -> PurePosixPath:
    if not value or "\\" in value or "\x00" in value:
        raise GenerationAuditError(f"unsafe bundle path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise GenerationAuditError(f"unsafe bundle path: {value!r}")
    if path.as_posix() != value:
        raise GenerationAuditError(f"bundle path is not canonical POSIX: {value!r}")
    return path


def _source_tree_sha256(package_dir: Path) -> str:
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


def _validate_symlink_containment(root: Path) -> None:
    try:
        resolved_root = root.resolve(strict=True)
    except OSError as exc:
        raise GenerationAuditError(f"cannot resolve sentinel clone {root}: {exc}") from exc
    for current, directory_names, file_names in os.walk(root, followlinks=False):
        if Path(current) == root and ".git" in directory_names:
            directory_names.remove(".git")
        for name in [*directory_names, *file_names]:
            candidate = Path(current) / name
            if not candidate.is_symlink():
                continue
            try:
                candidate.resolve(strict=True).relative_to(resolved_root)
            except (OSError, ValueError) as exc:
                relative = candidate.relative_to(root).as_posix()
                raise GenerationAuditError(
                    f"sentinel symlink is broken or escapes its clone root: {relative}"
                ) from exc


def _git_head(repository: Path) -> str:
    environment = {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
        "PATH": os.defpath,
    }
    try:
        completed = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GenerationAuditError(f"cannot resolve sentinel commit for {repository}: {exc}") from exc
    commit = completed.stdout.strip().lower()
    if completed.returncode != 0 or not _COMMIT_RE.fullmatch(commit):
        raise GenerationAuditError(f"cannot resolve sentinel commit for {repository}")
    return commit


def _read_combined(path: Path) -> dict[str, dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
    except (OSError, csv.Error) as exc:
        raise GenerationAuditError(f"cannot read {path}: {exc}") from exc
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        repo_id = row.get("id", "")
        if not _SAFE_ID_RE.fullmatch(repo_id) or repo_id in result:
            raise GenerationAuditError("combined.csv contains an invalid or duplicate repository ID")
        result[repo_id] = row
    return result


def _current_dependency_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for distribution in ("typer", "rich", "jinja2", "pyyaml", "libcst", "tomli"):
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[distribution] = "not-installed"
    return versions


def _validate_context(run: Path, clones: Path) -> GenerationContext:
    schema = _load_json(SCHEMA_PATH)
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise GenerationAuditError("generation-audit schema is not JSON Schema 2020-12")
    try:
        metadata = validate_run(run)
    except RunContractError as exc:
        raise GenerationAuditError(f"invalid immutable corpus run: {exc}") from exc
    run = run.resolve(strict=True)
    clones = clones.resolve(strict=True)
    if metadata.get("analysis_scope") != "effectiveness":
        raise GenerationAuditError("sentinel generation requires an effectiveness corpus run")
    if metadata.get("execution_mode") != "offline-builtins-only":
        raise GenerationAuditError("sentinel generation requires an offline built-ins-only run")
    if metadata.get("python") != {
        "version": platform.python_version(),
        "implementation": platform.python_implementation(),
    }:
        raise GenerationAuditError("current Python identity differs from the immutable run")
    if metadata.get("dependency_versions") != _current_dependency_versions():
        raise GenerationAuditError("current dependency identity differs from the immutable run")

    try:
        script_sha = require_current_harness_file(metadata, SCRIPT_HARNESS_PATH, Path(__file__))
        schema_sha = require_current_harness_file(metadata, SCHEMA_HARNESS_PATH, SCHEMA_PATH)
        checker_sha = require_current_harness_file(metadata, CHECKER_HARNESS_PATH, BUILTIN_CHECKER)
    except RunContractError as exc:
        raise GenerationAuditError(str(exc)) from exc
    source_sha = _source_tree_sha256(SOURCE_ROOT / "adduce")
    if source_sha != metadata.get("adduce_source_tree_sha256"):
        raise GenerationAuditError("current Adduce source bytes differ from the immutable run")

    combined = _read_combined(run / "combined.csv")
    repositories: dict[str, RepositoryInput] = {}
    for repo_id in SENTINELS:
        row = combined.get(repo_id)
        if row is None:
            raise GenerationAuditError(f"immutable run does not contain sentinel {repo_id}")
        if row.get("run_status") not in {"succeeded", "succeeded_with_partial_acquisition"}:
            raise GenerationAuditError(
                f"sentinel {repo_id} has no successful raw result: {row.get('run_status', '')}"
            )
        clone = clones / repo_id
        _validate_symlink_containment(clone)
        commit = _git_head(clone)
        expected_commit = row.get("resolved_sha", "")
        if commit != expected_commit:
            raise GenerationAuditError(f"sentinel commit drift detected for {repo_id}")
        worktree_sha = repository_tree_sha256(clone)
        if worktree_sha != row.get("worktree_sha256") or worktree_sha != row.get(
            "repository_tree_sha256"
        ):
            raise GenerationAuditError(f"sentinel worktree drift detected for {repo_id}")
        raw_json = run / "raw_json" / f"{repo_id}.json"
        raw_sha = sha256_file(raw_json)
        repositories[repo_id] = RepositoryInput(
            repo_id=repo_id,
            clone=clone,
            commit=commit,
            worktree_sha256=worktree_sha,
            raw_json=raw_json,
            raw_json_sha256=raw_sha,
        )
    return GenerationContext(
        run=run,
        clones=clones,
        metadata=metadata,
        run_meta_sha256=sha256_file(run / "run_meta.json"),
        script_sha256=script_sha,
        schema_sha256=schema_sha,
        checker_sha256=checker_sha,
        repositories=repositories,
    )


def _deterministic_projection(payload: dict[str, Any]) -> dict[str, Any]:
    """Select analyzer evidence while excluding documented observations.

    ``repository.root`` is acquisition-location metadata. ``corpus_execution``
    contains the separately validated execution policy and may also contain
    platform resource observations. The remaining fields are the analyzer's
    deterministic evidence and result projection.
    """
    required = {
        "tool",
        "repository",
        "reviewer_time",
        "claims",
        "total",
        "tier",
        "profile",
        "categories",
        "findings",
        "corpus_execution",
    }
    if not required.issubset(payload):
        raise GenerationAuditError(
            "raw analyzer JSON lacks the deterministic projection fields"
        )
    repository = payload.get("repository")
    if not isinstance(repository, dict):
        raise GenerationAuditError("raw analyzer JSON has an invalid repository object")
    repository_fields = (
        "commit",
        "frameworks",
        "files_scanned",
        "input_file_count",
        "input_byte_count",
    )
    repository_projection = {
        key: repository[key] for key in repository_fields if key in repository
    }
    if set(repository_projection) != set(repository_fields):
        raise GenerationAuditError("raw analyzer JSON has incomplete repository identity")
    return {
        "tool": payload["tool"],
        "repository": repository_projection,
        "reviewer_time": payload["reviewer_time"],
        "claims": payload["claims"],
        "total": payload["total"],
        "tier": payload["tier"],
        "profile": payload["profile"],
        "categories": payload["categories"],
        "findings": payload["findings"],
    }


def _projection_sha256(payload: dict[str, Any]) -> str:
    try:
        canonical = json.dumps(
            _deterministic_projection(payload),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise GenerationAuditError(f"analyzer projection is not strict JSON: {exc}") from exc
    return _sha256_bytes(canonical)


def _worker_payload(
    repository: Path,
    source_root: Path,
    expected_source_sha256: str,
    generated_at: str,
    run_id: str,
    raw_json_sha256: str,
) -> dict[str, Any]:
    """Run the guarded static analyzer and render both drafts in memory."""
    import check_builtin as guarded_checker

    for key in list(os.environ):
        if key in guarded_checker._GIT_ENVIRONMENT_KEYS or key.startswith("GIT_CONFIG_"):
            os.environ.pop(key, None)
    os.environ.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "PATH": os.defpath,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
            "PYTHONWARNINGS": "ignore::SyntaxWarning",
        }
    )
    git = shutil.which("git")
    if git is None:
        raise GenerationAuditError("Git is required for attributable sentinel generation")
    os.environ["PATH"] = str(Path(git).resolve().parent)
    sys.addaudithook(
        lambda event, event_args: guarded_checker._enforce_offline(
            event, event_args, repository
        )
    )

    sys.path.insert(0, str(source_root))
    import adduce
    import adduce.engine as engine
    import adduce.report.appendix as appendix_report
    from adduce.checklists import load_checklist, render_markdown
    from adduce.report.json_report import render as render_json
    from adduce.rules import discover_rules

    package_dir = Path(adduce.__file__).resolve().parent
    if package_dir != source_root / "adduce":
        raise GenerationAuditError("loaded Adduce package is not the run-bound source tree")
    observed_source_sha256 = _source_tree_sha256(package_dir)
    if observed_source_sha256 != expected_source_sha256:
        raise GenerationAuditError("Adduce source bytes changed before sentinel generation")
    engine.load_config = guarded_checker._default_config
    rules = discover_rules(include_plugins=False)
    result = engine.run_check(repository, include_plugins=False, rules=rules)

    scan = json.loads(render_json(result))
    scan["repository"]["input_file_count"] = len(result.repo.files)
    scan["repository"]["input_byte_count"] = sum(entry.size for entry in result.repo.files)
    observed_rule_ids = {finding["rule_id"] for finding in scan["findings"]}
    for rule in rules:
        if rule.id in observed_rule_ids:
            continue
        if rule.applies_to(result.repo):
            raise GenerationAuditError(f"applicable built-in rule emitted no finding: {rule.id}")
        scan["findings"].append(
            {
                "rule_id": rule.id,
                "category": rule.category.value,
                "title": rule.title,
                "status": "not-applicable",
                "confidence": 1.0,
                "severity": rule.effective_severity,
                "message": "Rule not applicable to this repository snapshot.",
                "remediation": "",
                "weight": rule.weight,
                "locations": [],
                "fix_command": rule.fix_command,
                "suppressed": False,
            }
        )
    scan["corpus_execution"] = {
        "configuration_mode": "defaults-only-repository-config-disabled",
        "plugins_enabled": False,
        "network_policy": "python-audit-socket-deny",
        "process_policy": "python-audit-read-only-git-metadata-only",
        "enforcement_scope": "scanner-regression-guard-not-os-sandbox",
        "environment_policy": "minimal-no-host-credentials",
        "input_policy": "clone-root-symlink-containment",
        "adduce_source_tree_sha256": observed_source_sha256,
    }

    checklist_text, checklist_ledger = render_markdown(
        load_checklist(PROFILE), result, strict=True
    )
    appendix_text, appendix_ledger = appendix_report.render(result, strict=True)
    ledgers = {
        checklist_ledger.artifact_path: checklist_ledger,
        appendix_ledger.artifact_path: appendix_ledger,
    }
    if set(ledgers) != set(ARTIFACT_NAMES):
        raise GenerationAuditError("renderers produced an unsupported sentinel artifact set")
    for ledger in ledgers.values():
        ledger.provenance.update(
            {
                "generated_at": generated_at,
                "corpus_run_id": run_id,
                "raw_json_sha256": raw_json_sha256,
                "adduce_source_tree_sha256": observed_source_sha256,
                "plugins_enabled": False,
                "generated_text_policy": "evidence_only",
                "deterministic_generation": True,
                "generation_execution_mode": "offline-static-regeneration",
            }
        )
    return {
        "scan": scan,
        "artifacts": {
            "checklist-neurips.md": checklist_text,
            "artifact_appendix.md": appendix_text,
        },
        "ledgers": {name: ledger.to_dict() for name, ledger in ledgers.items()},
    }


def _minimal_worker_environment() -> dict[str, str]:
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
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
            "PYTHONWARNINGS": "ignore::SyntaxWarning",
        }
    )
    return retained


def _run_worker(
    repository: RepositoryInput,
    context: GenerationContext,
    generated_at: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "_worker",
        "--repository",
        str(repository.clone),
        "--source-root",
        str(SOURCE_ROOT),
        "--source-sha256",
        str(context.metadata["adduce_source_tree_sha256"]),
        "--generated-at",
        generated_at,
        "--run-id",
        str(context.metadata["run_id"]),
        "--raw-json-sha256",
        repository.raw_json_sha256,
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=repository.clone,
            capture_output=True,
            text=False,
            timeout=timeout_seconds,
            env=_minimal_worker_environment(),
        )
    except subprocess.TimeoutExpired as exc:
        raise GenerationAuditError(
            f"guarded sentinel generation timed out for {repository.repo_id}"
        ) from exc
    except OSError as exc:
        raise GenerationAuditError(
            f"cannot start guarded sentinel generation for {repository.repo_id}: {exc}"
        ) from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()[:500]
        raise GenerationAuditError(
            f"guarded sentinel generation failed for {repository.repo_id}: {detail}"
        )
    if completed.stderr.strip():
        detail = completed.stderr.decode("utf-8", errors="replace").strip()[:500]
        raise GenerationAuditError(
            f"guarded sentinel generation emitted stderr for {repository.repo_id}: {detail}"
        )
    return _load_json_bytes(completed.stdout, f"worker output for {repository.repo_id}")


def _validate_evidence_path(
    evidence: dict[str, Any], repository: Path, label: str
) -> None:
    value = evidence["path"]
    strength = evidence["strength"]
    line = evidence["line"]
    if not value:
        if strength != "inferred" or line is not None:
            raise GenerationAuditError(
                f"{label} may omit its path only for unlocated inferred evidence"
            )
        return
    relative = _safe_relative(value)
    candidate = repository / Path(*relative.parts)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(repository.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise GenerationAuditError(f"{label} points outside or beyond the frozen clone") from exc
    if not resolved.is_file():
        raise GenerationAuditError(f"{label} does not identify a regular file")
    if line is None:
        return
    if isinstance(line, bool) or not isinstance(line, int) or line <= 0:
        raise GenerationAuditError(f"{label} has an invalid source line")
    try:
        line_count = resolved.read_bytes().count(b"\n") + 1
    except OSError as exc:
        raise GenerationAuditError(f"cannot inspect {label}: {exc}") from exc
    if line > line_count:
        raise GenerationAuditError(f"{label} source line exceeds the frozen file")


def _expected_counts(entries: list[dict[str, Any]]) -> dict[str, int]:
    counts = dict.fromkeys(_COUNTS_KEYS, 0)
    answer_keys = {
        "yes": "evidence_backed",
        "partial": "partial",
        "author_input_required": "author_input_required",
        "not_detected": "not_detected",
        "unknown": "unknown",
    }
    for entry in entries:
        counts[answer_keys[entry["answer"]]] += 1
        if entry["conflicts"]:
            counts["conflicts"] += 1
    return counts


def _expected_provenance(
    *,
    context: GenerationContext,
    repository: RepositoryInput,
    generated_at: str,
    artifact_name: str,
) -> dict[str, Any]:
    return {
        "adduce_version": context.metadata["adduce_version"],
        "command": "checklist" if artifact_name == "checklist-neurips.md" else "appendix",
        "profile": PROFILE if artifact_name == "checklist-neurips.md" else None,
        "mode": MODE,
        "repo_commit": repository.commit,
        "generated_at": generated_at,
        "corpus_run_id": context.metadata["run_id"],
        "raw_json_sha256": repository.raw_json_sha256,
        "adduce_source_tree_sha256": context.metadata["adduce_source_tree_sha256"],
        "plugins_enabled": False,
        "generated_text_policy": "evidence_only",
        "deterministic_generation": True,
        "generation_execution_mode": "offline-static-regeneration",
    }


def _audit_ledger_bundle(
    *,
    repository: RepositoryInput,
    context: GenerationContext,
    generated_at: str,
    artifact_texts: dict[str, str],
    ledger_records: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate the complete ledger and audit each yes or partial entry."""
    if set(artifact_texts) != set(ARTIFACT_NAMES):
        raise GenerationAuditError("sentinel bundle has a missing or extra generated artifact")
    if set(ledger_records) != set(ARTIFACT_NAMES):
        raise GenerationAuditError("sentinel ledger has a missing or extra artifact record")

    audited: list[dict[str, Any]] = []
    failures: list[str] = []
    for artifact_name in ARTIFACT_NAMES:
        text = artifact_texts[artifact_name]
        record = ledger_records[artifact_name]
        if not isinstance(record, dict):
            raise GenerationAuditError(f"ledger record for {artifact_name} must be an object")
        _exact_keys(
            record,
            {
                "artifact_path",
                "artifact_sha256",
                "provenance",
                "generated_text_policy",
                "counts",
                "entries",
            },
            f"ledger record for {artifact_name}",
        )
        if record["artifact_path"] != artifact_name:
            raise GenerationAuditError(f"ledger artifact path mismatch for {artifact_name}")
        expected_artifact_sha = _sha256_bytes((text.rstrip("\n") + "\n").encode("utf-8"))
        if record["artifact_sha256"] != expected_artifact_sha:
            raise GenerationAuditError(f"ledger artifact hash mismatch for {artifact_name}")
        if record["generated_text_policy"] != "evidence_only":
            raise GenerationAuditError(f"unsupported generated-text policy for {artifact_name}")

        provenance = record["provenance"]
        if not isinstance(provenance, dict):
            raise GenerationAuditError(f"ledger provenance for {artifact_name} must be an object")
        _exact_keys(provenance, _PROVENANCE_KEYS, f"ledger provenance for {artifact_name}")
        if provenance != _expected_provenance(
            context=context,
            repository=repository,
            generated_at=generated_at,
            artifact_name=artifact_name,
        ):
            raise GenerationAuditError(f"ledger provenance mismatch for {artifact_name}")

        entries = record["entries"]
        if not isinstance(entries, list) or not entries:
            raise GenerationAuditError(f"ledger entries for {artifact_name} must be non-empty")
        seen_item_ids: set[str] = set()
        for index, entry in enumerate(entries):
            label = f"{repository.repo_id}/{artifact_name} ledger entry {index + 1}"
            if not isinstance(entry, dict):
                raise GenerationAuditError(f"{label} must be an object")
            _exact_keys(
                entry,
                {"item_id", "question", "answer", "evidence", "searched", "missing", "conflicts"},
                label,
            )
            item_id = entry["item_id"]
            if not isinstance(item_id, str) or not item_id or item_id in seen_item_ids:
                raise GenerationAuditError(f"{label} has an invalid or duplicate item ID")
            seen_item_ids.add(item_id)
            if not isinstance(entry["question"], str) or not entry["question"]:
                raise GenerationAuditError(f"{label} has no question")
            answer = entry["answer"]
            if answer not in ANSWER_LEVELS:
                raise GenerationAuditError(f"{label} has an unsupported answer")
            for field in ("searched", "missing", "conflicts"):
                values = entry[field]
                if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                    raise GenerationAuditError(f"{label} has invalid {field}")
            if len(entry["searched"]) != len(set(entry["searched"])):
                raise GenerationAuditError(f"{label} has duplicate searched surfaces")

            evidence_items = entry["evidence"]
            if not isinstance(evidence_items, list):
                raise GenerationAuditError(f"{label} evidence must be an array")
            entry_failure_count = len(failures)
            strengths: list[str] = []
            for evidence_index, evidence in enumerate(evidence_items):
                evidence_label = f"{label} evidence {evidence_index + 1}"
                if not isinstance(evidence, dict):
                    raise GenerationAuditError(f"{evidence_label} must be an object")
                _exact_keys(
                    evidence,
                    {"kind", "path", "line", "confidence", "strength"},
                    evidence_label,
                )
                if not isinstance(evidence["kind"], str) or not evidence["kind"]:
                    raise GenerationAuditError(f"{evidence_label} has no evidence kind")
                if not isinstance(evidence["path"], str):
                    raise GenerationAuditError(f"{evidence_label} path must be a string")
                _finite_number(
                    evidence["confidence"], evidence_label + " confidence", minimum=0.0, maximum=1.0
                )
                strength = evidence["strength"]
                if strength not in EVIDENCE_STRENGTHS:
                    raise GenerationAuditError(f"{evidence_label} has unsupported strength")
                strengths.append(strength)
                if strength not in OFFLINE_EVIDENCE_STRENGTHS:
                    failures.append(
                        f"{repository.repo_id}/{artifact_name}/{item_id}: "
                        f"offline generation recorded {strength} evidence"
                    )
                if evidence["kind"] != "manifest" and evidence["kind"] not in entry["searched"]:
                    raise GenerationAuditError(
                        f"{evidence_label} was not among the entry's searched surfaces"
                    )
                _validate_evidence_path(evidence, repository.clone, evidence_label)

            if answer in AFFIRMATIVE_ANSWERS:
                if not evidence_items:
                    failures.append(
                        f"{repository.repo_id}/{artifact_name}/{item_id}: "
                        f"{answer} answer has no evidence"
                    )
                if answer == "yes":
                    strong = any(
                        (
                            evidence["strength"] == "direct"
                            and float(evidence["confidence"]) >= 0.90
                            and bool(evidence["path"])
                        )
                        or (
                            evidence["strength"] == "manifest_author_confirmed"
                            and float(evidence["confidence"]) == 1.0
                            and evidence["kind"] == "manifest"
                        )
                        for evidence in evidence_items
                    )
                    if not strong:
                        failures.append(
                            f"{repository.repo_id}/{artifact_name}/{item_id}: "
                            "yes answer lacks strict direct or author-confirmed evidence"
                        )
                    if entry["conflicts"]:
                        failures.append(
                            f"{repository.repo_id}/{artifact_name}/{item_id}: "
                            "yes answer retains conflicting evidence"
                        )
                if evidence_items and len(failures) == entry_failure_count:
                    audited.append(
                        {
                            "artifact_path": artifact_name,
                            "item_id": item_id,
                            "answer": answer,
                            "evidence_count": len(evidence_items),
                            "strengths": sorted(set(strengths)),
                            "result": "pass",
                        }
                    )

        counts = record["counts"]
        if not isinstance(counts, dict):
            raise GenerationAuditError(f"ledger counts for {artifact_name} must be an object")
        _exact_keys(counts, _COUNTS_KEYS, f"ledger counts for {artifact_name}")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts.values()):
            raise GenerationAuditError(f"ledger counts for {artifact_name} are invalid")
        if counts != _expected_counts(entries):
            raise GenerationAuditError(f"ledger counts disagree with entries for {artifact_name}")

        if artifact_name == "checklist-neurips.md":
            observed_answers = re.findall(r"^\*\*Answer:\*\* (.+)$", text, flags=re.MULTILINE)
            expected_answers = [ANSWER_TEXT[entry["answer"]] for entry in entries]
            if observed_answers != expected_answers:
                raise GenerationAuditError("checklist answer text disagrees with its full ledger")

        execution_claim = _EXECUTION_CLAIM_RE.search(text)
        has_dynamic = any(
            evidence["strength"] == "dynamic_verified"
            for entry in entries
            for evidence in entry["evidence"]
        )
        if execution_claim and not has_dynamic:
            failures.append(
                f"{repository.repo_id}/{artifact_name}: static draft implies execution "
                f"without dynamic evidence ({execution_claim.group(0)!r})"
            )
    return audited, sorted(set(failures))


def _answer_totals(ledger_records: dict[str, Any]) -> tuple[int, int]:
    yes = 0
    partial = 0
    for record in ledger_records.values():
        for entry in record["entries"]:
            yes += entry["answer"] == "yes"
            partial += entry["answer"] == "partial"
    return yes, partial


def _artifact_record(root: Path, path: str) -> dict[str, str]:
    relative = _safe_relative(path)
    candidate = root / Path(*relative.parts)
    if candidate.is_symlink() or not candidate.is_file():
        raise GenerationAuditError(f"missing regular bundle artifact: {path}")
    return {"path": path, "sha256": sha256_file(candidate)}


def _validate_artifact_records(
    records: object, expected_paths: set[str], root: Path, label: str
) -> list[dict[str, str]]:
    if not isinstance(records, list):
        raise GenerationAuditError(f"{label} must be an array")
    normalized: list[dict[str, str]] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise GenerationAuditError(f"{label} record {index + 1} must be an object")
        _exact_keys(record, {"path", "sha256"}, f"{label} record {index + 1}")
        path = record["path"]
        digest = record["sha256"]
        if not isinstance(path, str) or not isinstance(digest, str) or not _SHA256_RE.fullmatch(
            digest
        ):
            raise GenerationAuditError(f"{label} record {index + 1} is invalid")
        normalized.append({"path": path, "sha256": digest})
    paths = [record["path"] for record in normalized]
    if paths != sorted(paths) or len(paths) != len(set(paths)) or set(paths) != expected_paths:
        raise GenerationAuditError(f"{label} path set is incomplete, extra, or non-canonical")
    for record in normalized:
        observed = _artifact_record(root, record["path"])
        if observed != record:
            raise GenerationAuditError(f"bundle artifact hash mismatch: {record['path']}")
    return normalized


def _manifest_source(context: GenerationContext) -> dict[str, Any]:
    return {
        "run_id": context.metadata["run_id"],
        "run_meta_sha256": context.run_meta_sha256,
        "adduce_version": context.metadata["adduce_version"],
        "adduce_source_tree_sha256": context.metadata["adduce_source_tree_sha256"],
        "clone_manifest_sha256": context.metadata["clone_manifest_sha256"],
        "generation_script_sha256": context.script_sha256,
        "generation_schema_sha256": context.schema_sha256,
        "builtin_checker_sha256": context.checker_sha256,
    }


def _validate_manifest_shape(manifest: dict[str, Any]) -> None:
    _exact_keys(
        manifest,
        {
            "schema_version",
            "procedure",
            "generated_at",
            "result",
            "sentinels",
            "generation_policy",
            "source",
            "repositories",
            "artifacts",
            "summary",
        },
        "generation-audit manifest",
    )
    if manifest["schema_version"] != SCHEMA_VERSION or manifest["procedure"] != PROCEDURE:
        raise GenerationAuditError("unsupported generation-audit schema or procedure")
    _timestamp(manifest["generated_at"], "generation-audit generated_at")
    if manifest["result"] not in {"pass", "fail"}:
        raise GenerationAuditError("generation-audit result must be pass or fail")
    if manifest["sentinels"] != list(SENTINELS):
        raise GenerationAuditError("generation-audit sentinel set or order changed")
    if manifest["generation_policy"] != _GENERATION_POLICY:
        raise GenerationAuditError("generation-audit policy is unsupported")

    source = manifest["source"]
    if not isinstance(source, dict):
        raise GenerationAuditError("generation-audit source must be an object")
    _exact_keys(
        source,
        {
            "run_id",
            "run_meta_sha256",
            "adduce_version",
            "adduce_source_tree_sha256",
            "clone_manifest_sha256",
            "generation_script_sha256",
            "generation_schema_sha256",
            "builtin_checker_sha256",
        },
        "generation-audit source",
    )
    if not isinstance(source["run_id"], str) or not source["run_id"]:
        raise GenerationAuditError("generation-audit source has no run ID")
    if not isinstance(source["adduce_version"], str) or not source["adduce_version"]:
        raise GenerationAuditError("generation-audit source has no Adduce version")
    for field in (
        "run_meta_sha256",
        "adduce_source_tree_sha256",
        "clone_manifest_sha256",
        "generation_script_sha256",
        "generation_schema_sha256",
        "builtin_checker_sha256",
    ):
        if not isinstance(source[field], str) or not _SHA256_RE.fullmatch(source[field]):
            raise GenerationAuditError(f"generation-audit source has invalid {field}")

    repositories = manifest["repositories"]
    if not isinstance(repositories, list) or [
        repo.get("id") if isinstance(repo, dict) else None for repo in repositories
    ] != list(SENTINELS):
        raise GenerationAuditError("generation-audit repository set or order changed")
    summary = manifest["summary"]
    if not isinstance(summary, dict):
        raise GenerationAuditError("generation-audit summary must be an object")
    _exact_keys(
        summary,
        {
            "repositories",
            "generated_artifacts",
            "affirmative_entries",
            "yes_entries",
            "partial_entries",
            "failures",
        },
        "generation-audit summary",
    )
    for field in (
        "repositories",
        "generated_artifacts",
        "affirmative_entries",
        "yes_entries",
        "partial_entries",
    ):
        value = summary[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise GenerationAuditError(f"generation-audit summary has invalid {field}")
    if summary["repositories"] != len(SENTINELS) or summary["generated_artifacts"] != 6:
        raise GenerationAuditError("generation-audit summary scope changed")
    if not isinstance(summary["failures"], list) or any(
        not isinstance(value, str) or not value for value in summary["failures"]
    ):
        raise GenerationAuditError("generation-audit summary failures are invalid")


def _actual_bundle_files(bundle: Path) -> set[str]:
    files: set[str] = set()
    for root, directory_names, file_names in os.walk(bundle, followlinks=False):
        root_path = Path(root)
        for name in [*directory_names, *file_names]:
            candidate = root_path / name
            if candidate.is_symlink():
                raise GenerationAuditError("generation-audit bundles must not contain symlinks")
        for name in file_names:
            files.add((root_path / name).relative_to(bundle).as_posix())
    return files


def validate_bundle(bundle: Path, context: GenerationContext) -> dict[str, Any]:
    """Validate exact files, input bindings, full ledgers, and audit outcome."""
    try:
        bundle = bundle.resolve(strict=True)
    except OSError as exc:
        raise GenerationAuditError(f"cannot resolve generation-audit bundle: {exc}") from exc
    manifest = _load_json(bundle / MANIFEST_NAME)
    _validate_manifest_shape(manifest)
    if manifest["source"] != _manifest_source(context):
        raise GenerationAuditError("generation-audit source or run binding drifted")

    expected_artifact_paths = {
        f"{repo_id}/{name}"
        for repo_id in SENTINELS
        for name in (*ARTIFACT_NAMES, LEDGER_NAME)
    }
    marker = SUCCESS_MARKER if manifest["result"] == "pass" else FAILURE_MARKER
    expected_files = {MANIFEST_NAME, marker, *expected_artifact_paths}
    actual_files = _actual_bundle_files(bundle)
    if actual_files != expected_files:
        raise GenerationAuditError(
            "generation-audit file set mismatch "
            f"(missing={sorted(expected_files - actual_files)}, "
            f"extra={sorted(actual_files - expected_files)})"
        )
    expected_marker_text = (
        "generation audit passed\n" if marker == SUCCESS_MARKER else "generation audit failed\n"
    )
    if (bundle / marker).read_text(encoding="utf-8") != expected_marker_text:
        raise GenerationAuditError("generation-audit completion marker is invalid")
    artifacts = _validate_artifact_records(
        manifest["artifacts"], expected_artifact_paths, bundle, "generation-audit artifacts"
    )

    generated_at = manifest["generated_at"]
    expected_repositories: list[dict[str, Any]] = []
    all_failures: list[str] = []
    yes_total = 0
    partial_total = 0
    for repo_id in SENTINELS:
        repository = context.repositories[repo_id]
        if _git_head(repository.clone) != repository.commit:
            raise GenerationAuditError(f"sentinel commit drift detected for {repo_id}")
        if repository_tree_sha256(repository.clone) != repository.worktree_sha256:
            raise GenerationAuditError(f"sentinel worktree drift detected for {repo_id}")
        raw_data = repository.raw_json.read_bytes()
        if _sha256_bytes(raw_data) != repository.raw_json_sha256:
            raise GenerationAuditError(f"immutable raw JSON drift detected for {repo_id}")
        raw_payload = _load_json_bytes(raw_data, str(repository.raw_json))
        raw_projection_sha = _projection_sha256(raw_payload)

        repo_root = bundle / repo_id
        artifact_texts = {
            name: (repo_root / name).read_text(encoding="utf-8") for name in ARTIFACT_NAMES
        }
        ledger_records = _load_json(repo_root / LEDGER_NAME)
        audited, failures = _audit_ledger_bundle(
            repository=repository,
            context=context,
            generated_at=generated_at,
            artifact_texts=artifact_texts,
            ledger_records=ledger_records,
        )
        yes, partial = _answer_totals(ledger_records)
        yes_total += yes
        partial_total += partial
        all_failures.extend(failures)
        repo_paths = {f"{repo_id}/{name}" for name in (*ARTIFACT_NAMES, LEDGER_NAME)}
        repo_artifacts = [record for record in artifacts if record["path"] in repo_paths]
        recorded = manifest["repositories"][SENTINELS.index(repo_id)]
        rerun_projection_sha = recorded.get("rerun_projection_sha256")
        expected_repository = {
            "id": repo_id,
            "commit": repository.commit,
            "worktree_sha256": repository.worktree_sha256,
            "raw_json_sha256": repository.raw_json_sha256,
            "raw_projection_sha256": raw_projection_sha,
            "rerun_projection_sha256": rerun_projection_sha,
            "raw_scan_match": True,
            "artifacts": repo_artifacts,
            "affirmative_entries": audited,
            "failures": failures,
        }
        if not isinstance(rerun_projection_sha, str) or not _SHA256_RE.fullmatch(
            rerun_projection_sha
        ):
            raise GenerationAuditError(f"invalid rerun projection digest for {repo_id}")
        if rerun_projection_sha != raw_projection_sha:
            raise GenerationAuditError(f"rerun projection does not match raw scan for {repo_id}")
        if recorded != expected_repository:
            raise GenerationAuditError(f"generation-audit repository record drifted for {repo_id}")
        expected_repositories.append(expected_repository)

    all_failures = sorted(set(all_failures))
    expected_summary = {
        "repositories": len(SENTINELS),
        "generated_artifacts": len(SENTINELS) * len(ARTIFACT_NAMES),
        "affirmative_entries": yes_total + partial_total,
        "yes_entries": yes_total,
        "partial_entries": partial_total,
        "failures": all_failures,
    }
    if manifest["repositories"] != expected_repositories:
        raise GenerationAuditError("generation-audit repository records are inconsistent")
    if manifest["summary"] != expected_summary:
        raise GenerationAuditError("generation-audit summary disagrees with the full ledgers")
    expected_result = "pass" if not all_failures else "fail"
    if manifest["result"] != expected_result:
        raise GenerationAuditError("generation-audit result disagrees with its ledger audit")
    return manifest


def generate_bundle(
    *, run: Path, clones: Path, output: Path, timeout_seconds: int | None = None
) -> dict[str, Any]:
    """Generate a fresh, bounded sentinel bundle and validate it before return."""
    context = _validate_context(run, clones)
    ensure_output_outside(output, [context.run, context.clones])
    if output.exists() or output.is_symlink():
        raise GenerationAuditError(f"refusing to overwrite generation-audit output: {output}")
    output.mkdir(parents=True)
    _write_text(output / INCOMPLETE_MARKER, "incomplete generation audit")
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    timeout = timeout_seconds or int(context.metadata["timeout_seconds"])
    if timeout <= 0:
        raise GenerationAuditError("sentinel generation timeout must be positive")

    repository_records: list[dict[str, Any]] = []
    artifact_paths: list[str] = []
    all_failures: list[str] = []
    yes_total = 0
    partial_total = 0
    for repo_id in SENTINELS:
        repository = context.repositories[repo_id]
        before = repository_tree_sha256(repository.clone)
        worker = _run_worker(repository, context, generated_at, timeout)
        after = repository_tree_sha256(repository.clone)
        if before != repository.worktree_sha256 or after != before:
            raise GenerationAuditError(f"guarded generation modified sentinel {repo_id}")
        _exact_keys(worker, {"scan", "artifacts", "ledgers"}, f"worker result for {repo_id}")
        if not isinstance(worker["scan"], dict):
            raise GenerationAuditError(f"worker scan for {repo_id} must be an object")
        raw_payload = _load_json(repository.raw_json)
        raw_projection_sha = _projection_sha256(raw_payload)
        rerun_projection_sha = _projection_sha256(worker["scan"])
        if rerun_projection_sha != raw_projection_sha:
            raise GenerationAuditError(
                f"guarded generation scan differs from immutable raw result for {repo_id}"
            )
        artifact_texts = worker["artifacts"]
        ledger_records = worker["ledgers"]
        if not isinstance(artifact_texts, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in artifact_texts.items()
        ):
            raise GenerationAuditError(f"worker artifacts for {repo_id} are invalid")
        if not isinstance(ledger_records, dict):
            raise GenerationAuditError(f"worker ledgers for {repo_id} are invalid")
        audited, failures = _audit_ledger_bundle(
            repository=repository,
            context=context,
            generated_at=generated_at,
            artifact_texts=artifact_texts,
            ledger_records=ledger_records,
        )
        yes, partial = _answer_totals(ledger_records)
        yes_total += yes
        partial_total += partial
        all_failures.extend(failures)

        repo_root = output / repo_id
        repo_root.mkdir()
        for artifact_name in ARTIFACT_NAMES:
            _write_text(repo_root / artifact_name, artifact_texts[artifact_name])
            artifact_paths.append(f"{repo_id}/{artifact_name}")
        _write_json(repo_root / LEDGER_NAME, ledger_records)
        artifact_paths.append(f"{repo_id}/{LEDGER_NAME}")
        repo_artifacts = sorted(
            (_artifact_record(output, path) for path in artifact_paths if path.startswith(f"{repo_id}/")),
            key=lambda record: record["path"],
        )
        repository_records.append(
            {
                "id": repo_id,
                "commit": repository.commit,
                "worktree_sha256": repository.worktree_sha256,
                "raw_json_sha256": repository.raw_json_sha256,
                "raw_projection_sha256": raw_projection_sha,
                "rerun_projection_sha256": rerun_projection_sha,
                "raw_scan_match": True,
                "artifacts": repo_artifacts,
                "affirmative_entries": audited,
                "failures": failures,
            }
        )

    all_failures = sorted(set(all_failures))
    result = "pass" if not all_failures else "fail"
    artifacts = sorted(
        (_artifact_record(output, path) for path in artifact_paths),
        key=lambda record: record["path"],
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "procedure": PROCEDURE,
        "generated_at": generated_at,
        "result": result,
        "sentinels": list(SENTINELS),
        "generation_policy": _GENERATION_POLICY,
        "source": _manifest_source(context),
        "repositories": repository_records,
        "artifacts": artifacts,
        "summary": {
            "repositories": len(SENTINELS),
            "generated_artifacts": len(SENTINELS) * len(ARTIFACT_NAMES),
            "affirmative_entries": yes_total + partial_total,
            "yes_entries": yes_total,
            "partial_entries": partial_total,
            "failures": all_failures,
        },
    }
    _write_json(output / MANIFEST_NAME, manifest)
    (output / INCOMPLETE_MARKER).unlink()
    marker = SUCCESS_MARKER if result == "pass" else FAILURE_MARKER
    _write_text(
        output / marker,
        "generation audit passed" if result == "pass" else "generation audit failed",
    )
    return validate_bundle(output, context)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    generate = commands.add_parser(
        "generate", help="generate and audit a fresh bundle for the three sentinels"
    )
    generate.add_argument("--run", type=Path, required=True)
    generate.add_argument("--clones", type=Path, required=True)
    generate.add_argument("--out", type=Path, required=True)
    generate.add_argument("--timeout", type=int)

    validate = commands.add_parser(
        "validate", help="revalidate an existing bundle and its immutable inputs"
    )
    validate.add_argument("--bundle", type=Path, required=True)
    validate.add_argument("--run", type=Path, required=True)
    validate.add_argument("--clones", type=Path, required=True)

    worker = commands.add_parser("_worker", help=argparse.SUPPRESS)
    worker.add_argument("--repository", type=Path, required=True)
    worker.add_argument("--source-root", type=Path, required=True)
    worker.add_argument("--source-sha256", required=True)
    worker.add_argument("--generated-at", required=True)
    worker.add_argument("--run-id", required=True)
    worker.add_argument("--raw-json-sha256", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "_worker":
            payload = _worker_payload(
                args.repository.resolve(strict=True),
                args.source_root.resolve(strict=True),
                args.source_sha256,
                _timestamp(args.generated_at, "worker generated_at"),
                args.run_id,
                args.raw_json_sha256,
            )
            print(json.dumps(payload, sort_keys=True, allow_nan=False))
            return 0
        if args.command == "generate":
            manifest = generate_bundle(
                run=args.run,
                clones=args.clones,
                output=args.out,
                timeout_seconds=args.timeout,
            )
            print(
                f"generation audit {manifest['result']}: {len(SENTINELS)} repositories, "
                f"{manifest['summary']['affirmative_entries']} affirmative entries"
            )
            return 0 if manifest["result"] == "pass" else 1
        context = _validate_context(args.run, args.clones)
        manifest = validate_bundle(args.bundle, context)
        print(
            f"valid generation-audit bundle ({manifest['result']}): "
            f"{manifest['summary']['affirmative_entries']} affirmative entries"
        )
        return 0 if manifest["result"] == "pass" else 1
    except (GenerationAuditError, RunContractError, OSError, ValueError) as exc:
        print(f"generation audit error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
