#!/usr/bin/env python3
"""Recompute coordinator-only claim-review provenance from the checked-out tree.

Every fact reported here is derived from the working tree rather than copied
from a note, so a stale record is visible instead of silent. The resolution and
trail-status distributions aggregate the frozen answer key, so no output of this
module may be placed in a reviewer packet.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

if __package__:
    from .run_contract import RunContractError, load_json_object_bytes, sha256_file
    from .run_validation import _git, _source_tree_sha256
else:
    from run_contract import RunContractError, load_json_object_bytes, sha256_file
    from run_validation import _git, _source_tree_sha256

REVIEW_FACTS_SCHEMA_VERSION = 1
COORDINATOR_ONLY_NOTICE = (
    "Coordinator-only. These facts aggregate the frozen answer key and must never "
    "be placed in a reviewer packet."
)
DEFAULT_TRUTH = "corpus/labels/pilot-claims.json"
DEFAULT_REPOS = "corpus/repos.csv"
DEFAULT_CLONES = "corpus/clones/pilot-2026-07-13"
DEFAULT_PREREGISTRATION = "corpus/pilot-r6-preregistration.json"
DEFAULT_SCAFFOLDS = (
    "corpus/labels/pilot-claim-review-r6-reviewer-a.json",
    "corpus/labels/pilot-claim-review-r6-reviewer-b.json",
)
RUNBOOK_BLOCK_KEY = "review_runbook"
RUNBOOK_SCALAR_FIELDS = (
    "source_commit",
    "truth_sha256",
    "corpus_inventory_sha256",
    "preregistration_sha256",
    "derived_at",
)
RUNBOOK_FIELDS = (*RUNBOOK_SCALAR_FIELDS, "candidate_pair")

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_FENCE_RE = re.compile(r"^```([A-Za-z0-9_+-]*)\s*$")
_SCALAR_RE = re.compile(r"^ {2}([a-z][a-z0-9_]*): (\S.*?)\s*$")
_MAPPING_RE = re.compile(r"^ {2}([a-z][a-z0-9_]*):\s*$")
_ITEM_RE = re.compile(r"^ {4}- (\S.*?)\s*$")


class ReviewFactsError(ValueError):
    """Coordinator provenance inputs are missing, malformed, or contradictory."""


def _display_path(path: Path, root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return resolved.as_posix()


def _digest_and_size(path: Path) -> tuple[str, int]:
    try:
        return sha256_file(path), path.stat().st_size
    except (RunContractError, OSError) as exc:
        raise ReviewFactsError(f"cannot measure {path}: {exc}") from exc


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ReviewFactsError(f"cannot read {label} {path}: {exc}") from exc
    try:
        return cast(dict[str, Any], load_json_object_bytes(data, str(path)))
    except RunContractError as exc:
        raise ReviewFactsError(f"cannot read {label}: {exc}") from exc


def _text(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReviewFactsError(f"{context} must be a non-empty string")
    return value


def _candidate_pair(value: object, context: str) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(not isinstance(item, str) or not _ID_RE.fullmatch(item) for item in value)
        or value[0] == value[1]
    ):
        raise ReviewFactsError(f"{context} must be two distinct candidate run identifiers")
    return [str(item) for item in value]


def git_facts(root: Path) -> dict[str, Any]:
    """Describe the commit, branch, and cleanliness of the checked-out tree."""
    unavailable: dict[str, Any] = {
        "available": False,
        "commit": None,
        "branch": None,
        "dirty": None,
    }
    try:
        head = _git("rev-parse", "HEAD", cwd=root)
        branch = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=root)
        status = _git("status", "--porcelain=v1", "--untracked-files=all", cwd=root)
    except (OSError, subprocess.SubprocessError):
        return unavailable
    if head.returncode != 0 or branch.returncode != 0 or status.returncode != 0:
        return unavailable
    commit = head.stdout.strip().lower()
    if not _COMMIT_RE.fullmatch(commit):
        return unavailable
    name = branch.stdout.strip()
    return {
        "available": True,
        "commit": commit,
        "branch": None if name in {"", "HEAD"} else name,
        "dirty": bool(status.stdout.strip()),
    }


def package_version(root: Path) -> str | None:
    """Read the declared package version from source, never from an installation."""
    path = root / "src" / "adduce" / "__init__.py"
    if not path.is_file():
        return None
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise ReviewFactsError(f"cannot read package version from {path}: {exc}") from exc
    values = [
        node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in node.targets
        )
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ]
    if len(values) != 1:
        raise ReviewFactsError(f"{path} must define exactly one string __version__")
    return str(values[0])


def analyzer_source_tree_sha256(root: Path) -> str | None:
    """Hash the analyzer byte tree with the same helper the run harness uses."""
    package_dir = root / "src" / "adduce"
    if not package_dir.is_dir():
        return None
    try:
        digest = _source_tree_sha256(package_dir)
    except OSError as exc:
        raise ReviewFactsError(f"cannot hash analyzer source tree {package_dir}: {exc}") from exc
    return str(digest)


def truth_facts(path: Path, root: Path) -> dict[str, Any]:
    """Summarize the frozen claim ground truth, including answer-key aggregates."""
    facts: dict[str, Any] = {
        "path": _display_path(path, root),
        "available": False,
        "sha256": None,
        "bytes": None,
        "frozen_at": None,
        "corpus_inventory_sha256": None,
        "claim_count": None,
        "link_count": None,
        "expected_resolution_counts": None,
        "expected_trail_status_counts": None,
        "unavailable_repositories": None,
    }
    if not path.is_file():
        return facts
    payload = _load_object(path, "claim ground truth")
    claims = payload.get("claims")
    if not isinstance(claims, list):
        raise ReviewFactsError(f"{path} has no claim list")
    resolutions: Counter[str] = Counter()
    trail_statuses: Counter[str] = Counter()
    link_count = 0
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            raise ReviewFactsError(f"{path} claim {index} is not an object")
        trail_statuses[
            _text(claim.get("expected_trail_status"), f"{path} claim {index} expected_trail_status")
        ] += 1
        links = claim.get("expected_links")
        if not isinstance(links, list):
            raise ReviewFactsError(f"{path} claim {index} has no expected_links list")
        link_count += len(links)
        for position, link in enumerate(links):
            if not isinstance(link, dict):
                raise ReviewFactsError(f"{path} claim {index} link {position} is not an object")
            resolutions[
                _text(
                    link.get("expected_resolution"),
                    f"{path} claim {index} link {position} expected_resolution",
                )
            ] += 1
    unavailable = payload.get("unavailable_repositories")
    if not isinstance(unavailable, list) or any(
        not isinstance(item, str) for item in unavailable
    ):
        raise ReviewFactsError(f"{path} must declare unavailable_repositories as a string list")
    digest, size = _digest_and_size(path)
    facts.update(
        {
            "available": True,
            "sha256": digest,
            "bytes": size,
            "frozen_at": _text(payload.get("frozen_at"), f"{path} frozen_at"),
            "corpus_inventory_sha256": _text(
                payload.get("corpus_inventory_sha256"), f"{path} corpus_inventory_sha256"
            ),
            "claim_count": len(claims),
            "link_count": link_count,
            "expected_resolution_counts": dict(sorted(resolutions.items())),
            "expected_trail_status_counts": dict(sorted(trail_statuses.items())),
            "unavailable_repositories": sorted(str(item) for item in unavailable),
        }
    )
    return facts


def corpus_facts(repos: Path, clones: Path, root: Path) -> dict[str, Any]:
    """Digest the repository inventory and report which clones are not on disk."""
    facts: dict[str, Any] = {
        "inventory_path": _display_path(repos, root),
        "inventory_available": False,
        "inventory_sha256": None,
        "repository_count": None,
        "clones_path": _display_path(clones, root),
        "clones_available": clones.is_dir(),
        "unavailable_repositories": None,
    }
    if not repos.is_file():
        return facts
    digest, _ = _digest_and_size(repos)
    try:
        text = repos.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ReviewFactsError(f"cannot read repository inventory {repos}: {exc}") from exc
    rows = [line for line in text.splitlines() if line.strip()]
    if len(rows) < 2 or not rows[0].startswith("id,"):
        raise ReviewFactsError(f"{repos} is not a repository inventory with an id column")
    identifiers = [row.split(",", 1)[0] for row in rows[1:]]
    facts.update(
        {
            "inventory_available": True,
            "inventory_sha256": digest,
            "repository_count": len(identifiers),
        }
    )
    if facts["clones_available"]:
        facts["unavailable_repositories"] = sorted(
            identifier for identifier in identifiers if not (clones / identifier).is_dir()
        )
    return facts


def scaffold_facts(paths: tuple[Path, ...], root: Path) -> list[dict[str, Any]]:
    """Measure each issued review scaffold and read the candidate pair it binds."""
    records: list[dict[str, Any]] = []
    for path in paths:
        record: dict[str, Any] = {
            "path": _display_path(path, root),
            "available": False,
            "sha256": None,
            "bytes": None,
            "candidate_pair": None,
        }
        if path.is_file():
            payload = _load_object(path, "claim-review scaffold")
            digest, size = _digest_and_size(path)
            record.update(
                {
                    "available": True,
                    "sha256": digest,
                    "bytes": size,
                    "candidate_pair": _candidate_pair(
                        payload.get("candidate_pair"), f"{path} candidate_pair"
                    ),
                }
            )
        records.append(record)
    return records


def resolve_candidate_pair(scaffolds: list[dict[str, Any]]) -> list[str] | None:
    """Return the single candidate pair the scaffolds agree on, or refuse to pick."""
    observed: dict[tuple[str, ...], list[str]] = {}
    for record in scaffolds:
        pair = record["candidate_pair"]
        if pair is None:
            continue
        observed.setdefault(tuple(pair), []).append(str(record["path"]))
    if not observed:
        return None
    if len(observed) > 1:
        rendered = "; ".join(
            f"{' + '.join(pair)} in {', '.join(sorted(sources))}"
            for pair, sources in sorted(observed.items())
        )
        raise ReviewFactsError(f"review scaffolds bind disagreeing candidate pairs: {rendered}")
    return list(next(iter(observed)))


def preregistration_facts(path: Path, root: Path) -> dict[str, Any]:
    """Digest the preregistration lock and read the pair it registers."""
    facts: dict[str, Any] = {
        "path": _display_path(path, root),
        "available": False,
        "sha256": None,
        "bytes": None,
        "protocol_id": None,
        "candidate_pair": None,
    }
    if not path.is_file():
        return facts
    payload = _load_object(path, "preregistration lock")
    digest, size = _digest_and_size(path)
    facts.update(
        {
            "available": True,
            "sha256": digest,
            "bytes": size,
            "protocol_id": _text(payload.get("protocol_id"), f"{path} protocol_id"),
            "candidate_pair": _candidate_pair(
                payload.get("candidate_pair"), f"{path} candidate_pair"
            ),
        }
    )
    return facts


def collect_facts(
    *,
    root: Path,
    truth: Path,
    repos: Path,
    clones: Path,
    scaffolds: tuple[Path, ...],
    preregistration: Path,
) -> dict[str, Any]:
    """Recompute every coordinator provenance fact from the checked-out tree."""
    resolved_root = root.resolve()
    if not resolved_root.is_dir():
        raise ReviewFactsError(f"repository root is not a directory: {root}")
    truth_record = truth_facts(truth, resolved_root)
    corpus_record = corpus_facts(repos, clones, resolved_root)
    scaffold_records = scaffold_facts(scaffolds, resolved_root)
    preregistration_record = preregistration_facts(preregistration, resolved_root)
    digests = [record["sha256"] for record in scaffold_records if record["available"]]
    unavailable_inputs = [truth_record["path"]] if not truth_record["available"] else []
    if not corpus_record["inventory_available"]:
        unavailable_inputs.append(corpus_record["inventory_path"])
    if not corpus_record["clones_available"]:
        unavailable_inputs.append(corpus_record["clones_path"])
    unavailable_inputs.extend(
        record["path"] for record in scaffold_records if not record["available"]
    )
    if not preregistration_record["available"]:
        unavailable_inputs.append(preregistration_record["path"])
    return {
        "review_facts_schema_version": REVIEW_FACTS_SCHEMA_VERSION,
        "coordinator_only": True,
        "notice": COORDINATOR_ONLY_NOTICE,
        "derived_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "root": resolved_root.as_posix(),
        "git": git_facts(resolved_root),
        "package_version": package_version(resolved_root),
        "analyzer_source_tree_sha256": analyzer_source_tree_sha256(resolved_root),
        "truth": truth_record,
        "corpus": corpus_record,
        "scaffolds": scaffold_records,
        "scaffolds_byte_equal": len(set(digests)) == 1 if len(digests) > 1 else None,
        "candidate_pair": resolve_candidate_pair(scaffold_records),
        "preregistration": preregistration_record,
        "unavailable_inputs": sorted(unavailable_inputs),
    }


def _value(value: object) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) if value else "none"
    if isinstance(value, dict):
        return ", ".join(f"{key}={item}" for key, item in value.items()) if value else "none"
    return str(value)


def render_text(facts: dict[str, Any]) -> str:
    """Render the human coordinator summary."""
    git = facts["git"]
    truth = facts["truth"]
    corpus = facts["corpus"]
    preregistration = facts["preregistration"]
    lines = [
        COORDINATOR_ONLY_NOTICE,
        "",
        f"root: {facts['root']}",
        f"derived at: {facts['derived_at']}",
        f"git commit: {_value(git['commit'])}",
        f"git branch: {'detached HEAD' if git['available'] and git['branch'] is None else _value(git['branch'])}",
        f"git worktree: {'unavailable' if git['dirty'] is None else ('dirty' if git['dirty'] else 'clean')}",
        f"package version: {_value(facts['package_version'])}",
        f"analyzer source tree sha256: {_value(facts['analyzer_source_tree_sha256'])}",
        f"truth: {truth['path']}{'' if truth['available'] else ' (unavailable)'}",
        f"  sha256: {_value(truth['sha256'])}",
        f"  frozen at: {_value(truth['frozen_at'])}",
        f"  declared corpus inventory sha256: {_value(truth['corpus_inventory_sha256'])}",
        f"  claims: {_value(truth['claim_count'])}",
        f"  links: {_value(truth['link_count'])}",
        f"  expected resolution: {_value(truth['expected_resolution_counts'])}",
        f"  expected trail status: {_value(truth['expected_trail_status_counts'])}",
        f"  repositories declared unavailable: {_value(truth['unavailable_repositories'])}",
        (
            f"corpus inventory: {corpus['inventory_path']}"
            f"{'' if corpus['inventory_available'] else ' (unavailable)'}"
        ),
        f"  sha256: {_value(corpus['inventory_sha256'])}",
        f"  repositories: {_value(corpus['repository_count'])}",
        (
            f"clone root: {corpus['clones_path']}"
            f"{'' if corpus['clones_available'] else ' (unavailable)'}"
        ),
        f"  repositories without a clone: {_value(corpus['unavailable_repositories'])}",
        "scaffolds:",
    ]
    for record in facts["scaffolds"]:
        if record["available"]:
            lines.append(
                f"  {record['path']} sha256={record['sha256']} bytes={record['bytes']} "
                f"candidate_pair={_value(record['candidate_pair'])}"
            )
        else:
            lines.append(f"  {record['path']} (unavailable)")
    lines.extend(
        [
            f"  byte-equal: {_value(facts['scaffolds_byte_equal'])}",
            f"candidate pair: {_value(facts['candidate_pair'])}",
            (
                f"preregistration: {preregistration['path']}"
                f"{'' if preregistration['available'] else ' (unavailable)'}"
            ),
            f"  sha256: {_value(preregistration['sha256'])}",
            f"  protocol id: {_value(preregistration['protocol_id'])}",
            f"  candidate pair: {_value(preregistration['candidate_pair'])}",
            f"unavailable inputs: {_value(facts['unavailable_inputs'])}",
        ]
    )
    return "\n".join(lines)


def render_markdown(facts: dict[str, Any]) -> str:
    """Render the coordinator provenance report."""
    git = facts["git"]
    truth = facts["truth"]
    corpus = facts["corpus"]
    preregistration = facts["preregistration"]
    lines = [
        "# Claim-review provenance facts",
        "",
        f"**{COORDINATOR_ONLY_NOTICE}**",
        "",
        f"Recomputed from `{facts['root']}` at {facts['derived_at']}.",
        "",
        "## Repository state",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Git commit | {_value(git['commit'])} |",
        f"| Git branch | {'detached HEAD' if git['available'] and git['branch'] is None else _value(git['branch'])} |",
        f"| Worktree | {'unavailable' if git['dirty'] is None else ('dirty' if git['dirty'] else 'clean')} |",
        f"| Package version | {_value(facts['package_version'])} |",
        f"| Analyzer source tree SHA-256 | {_value(facts['analyzer_source_tree_sha256'])} |",
        "",
        "## Input digests",
        "",
        "| Input | Path | SHA-256 | Bytes |",
        "| --- | --- | --- | --- |",
        f"| Claim ground truth | {truth['path']} | {_value(truth['sha256'])} | {_value(truth['bytes'])} |",
        (
            f"| Repository inventory | {corpus['inventory_path']} | "
            f"{_value(corpus['inventory_sha256'])} | - |"
        ),
        (
            f"| Preregistration lock | {preregistration['path']} | "
            f"{_value(preregistration['sha256'])} | {_value(preregistration['bytes'])} |"
        ),
    ]
    for record in facts["scaffolds"]:
        lines.append(
            f"| Review scaffold | {record['path']} | {_value(record['sha256'])} | "
            f"{_value(record['bytes'])} |"
        )
    lines.extend(
        [
            "",
            "## Candidate binding",
            "",
            "| Field | Value |",
            "| --- | --- |",
            f"| Candidate pair | {_value(facts['candidate_pair'])} |",
            f"| Scaffolds byte-equal | {_value(facts['scaffolds_byte_equal'])} |",
            f"| Preregistered protocol | {_value(preregistration['protocol_id'])} |",
            f"| Preregistered pair | {_value(preregistration['candidate_pair'])} |",
            "",
            "## Frozen answer key",
            "",
            "| Field | Value |",
            "| --- | --- |",
            f"| Frozen at | {_value(truth['frozen_at'])} |",
            f"| Claims | {_value(truth['claim_count'])} |",
            f"| Links | {_value(truth['link_count'])} |",
            f"| Expected resolution | {_value(truth['expected_resolution_counts'])} |",
            f"| Expected trail status | {_value(truth['expected_trail_status_counts'])} |",
            f"| Repositories declared unavailable | {_value(truth['unavailable_repositories'])} |",
            f"| Repositories without a clone | {_value(corpus['unavailable_repositories'])} |",
            "",
            "## Unavailable inputs",
            "",
            f"{_value(facts['unavailable_inputs'])}",
            "",
        ]
    )
    return "\n".join(lines)


def verify_expectations(
    facts: dict[str, Any],
    *,
    expect_truth_sha256: str | None,
    expect_candidate_pair: list[str],
) -> list[str]:
    """Compare recorded expectations against the recomputed facts."""
    mismatches: list[str] = []
    if expect_truth_sha256 is not None:
        if not _SHA256_RE.fullmatch(expect_truth_sha256):
            raise ReviewFactsError("expected truth digest is not a SHA-256 hex digest")
        if not facts["truth"]["available"]:
            raise ReviewFactsError(
                f"cannot verify the truth digest: {facts['truth']['path']} is unavailable"
            )
        live = facts["truth"]["sha256"]
        if live != expect_truth_sha256:
            mismatches.append(f"truth_sha256: expected {expect_truth_sha256}, live {live}")
    if expect_candidate_pair:
        if len(expect_candidate_pair) != 2:
            raise ReviewFactsError("--expect-candidate-pair must be given exactly twice")
        if facts["candidate_pair"] is None:
            raise ReviewFactsError(
                "cannot verify the candidate pair: no review scaffold is available"
            )
        live_pair = facts["candidate_pair"]
        if live_pair != expect_candidate_pair:
            mismatches.append(
                f"candidate_pair: expected {_value(expect_candidate_pair)}, "
                f"live {_value(live_pair)}"
            )
    return mismatches


def _fenced_blocks(text: str, info: str) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in text.splitlines():
        fence = _FENCE_RE.match(line)
        if current is None:
            if fence is not None and fence.group(1) == info:
                current = []
            continue
        if fence is not None and fence.group(1) == "":
            blocks.append(current)
            current = None
            continue
        current.append(line)
    if current is not None:
        raise ReviewFactsError(f"unterminated ```{info} block in the review runbook")
    return blocks


def parse_runbook_metadata(text: str) -> dict[str, Any]:
    """Parse the one fenced ``review_runbook`` block, rejecting any other shape."""
    candidates = [
        block
        for block in _fenced_blocks(text, "yaml")
        if block and block[0].rstrip() == f"{RUNBOOK_BLOCK_KEY}:"
    ]
    if not candidates:
        raise ReviewFactsError(
            f"review runbook has no fenced yaml {RUNBOOK_BLOCK_KEY} metadata block"
        )
    if len(candidates) > 1:
        raise ReviewFactsError(
            f"review runbook has {len(candidates)} {RUNBOOK_BLOCK_KEY} metadata blocks; expected one"
        )
    block = candidates[0]
    while block and not block[-1].strip():
        block.pop()
    parsed: dict[str, Any] = {}
    current_list: list[str] | None = None
    for line in block[1:]:
        item = _ITEM_RE.match(line)
        if item is not None:
            if current_list is None:
                raise ReviewFactsError(f"unexpected list item in runbook metadata: {line!r}")
            current_list.append(item.group(1))
            continue
        scalar = _SCALAR_RE.match(line)
        mapping = _MAPPING_RE.match(line)
        match = scalar or mapping
        if match is None:
            raise ReviewFactsError(f"unparsable line in runbook metadata: {line!r}")
        key = match.group(1)
        if key in parsed:
            raise ReviewFactsError(f"duplicate runbook metadata field: {key}")
        if key not in RUNBOOK_FIELDS:
            raise ReviewFactsError(f"unknown runbook metadata field: {key}")
        if scalar is not None:
            current_list = None
            parsed[key] = scalar.group(2)
        else:
            current_list = []
            parsed[key] = current_list
    missing = [field for field in RUNBOOK_FIELDS if field not in parsed]
    if missing:
        raise ReviewFactsError(f"runbook metadata is missing fields: {', '.join(missing)}")
    for field in RUNBOOK_SCALAR_FIELDS:
        if not isinstance(parsed[field], str):
            raise ReviewFactsError(f"runbook metadata field {field} must be a scalar")
    if not _COMMIT_RE.fullmatch(parsed["source_commit"]):
        raise ReviewFactsError("runbook metadata source_commit is not a 40-character commit SHA")
    for field in ("truth_sha256", "corpus_inventory_sha256", "preregistration_sha256"):
        if not _SHA256_RE.fullmatch(parsed[field]):
            raise ReviewFactsError(f"runbook metadata {field} is not a SHA-256 hex digest")
    parsed["candidate_pair"] = _candidate_pair(
        parsed["candidate_pair"], "runbook metadata candidate_pair"
    )
    parsed["derived_at"] = _runbook_timestamp(parsed["derived_at"])
    return parsed


def _runbook_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReviewFactsError(
            "runbook metadata derived_at is not an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReviewFactsError("runbook metadata derived_at must carry a UTC offset")
    return value


def compare_runbook(facts: dict[str, Any], metadata: dict[str, Any]) -> list[str]:
    """Report every runbook field that no longer matches the recomputed facts."""
    live_values: dict[str, Any] = {
        "source_commit": facts["git"]["commit"],
        "truth_sha256": facts["truth"]["sha256"],
        "corpus_inventory_sha256": facts["corpus"]["inventory_sha256"],
        "candidate_pair": facts["candidate_pair"],
        "preregistration_sha256": facts["preregistration"]["sha256"],
    }
    unavailable = sorted(field for field, live in live_values.items() if live is None)
    if unavailable:
        raise ReviewFactsError(
            "cannot check the runbook against unavailable facts: " + ", ".join(unavailable)
        )
    mismatches = [
        f"{field}: recorded {_value(metadata[field])}, live {_value(live)}"
        for field, live in live_values.items()
        if metadata[field] != live
    ]
    recorded_at = datetime.fromisoformat(metadata["derived_at"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    if recorded_at > now:
        mismatches.append(
            f"derived_at: recorded {metadata['derived_at']} is later than "
            f"{now.replace(microsecond=0).isoformat()}"
        )
    return mismatches


def _add_input_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root", type=Path, default=Path("."), help="repository root (default: .)"
    )
    parser.add_argument("--truth", type=Path, help=f"claim ground truth (default: {DEFAULT_TRUTH})")
    parser.add_argument("--repos", type=Path, help=f"repository inventory (default: {DEFAULT_REPOS})")
    parser.add_argument("--clones", type=Path, help=f"clone root (default: {DEFAULT_CLONES})")
    parser.add_argument(
        "--scaffold",
        type=Path,
        action="append",
        default=[],
        help="issued review scaffold; repeatable (default: the two r6 scaffolds)",
    )
    parser.add_argument(
        "--preregistration",
        type=Path,
        help=f"preregistration lock (default: {DEFAULT_PREREGISTRATION})",
    )


def _facts_from_arguments(args: argparse.Namespace) -> dict[str, Any]:
    root = args.root.resolve()
    scaffolds = (
        tuple(args.scaffold)
        if args.scaffold
        else tuple(root / name for name in DEFAULT_SCAFFOLDS)
    )
    return collect_facts(
        root=root,
        truth=args.truth if args.truth is not None else root / DEFAULT_TRUTH,
        repos=args.repos if args.repos is not None else root / DEFAULT_REPOS,
        clones=args.clones if args.clones is not None else root / DEFAULT_CLONES,
        scaffolds=scaffolds,
        preregistration=(
            args.preregistration
            if args.preregistration is not None
            else root / DEFAULT_PREREGISTRATION
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    subparsers = parser.add_subparsers(dest="command", required=True)

    show_parser = subparsers.add_parser("show", help="print the recomputed provenance facts")
    _add_input_arguments(show_parser)
    show_parser.add_argument("--json", action="store_true", help="emit the stable JSON object")

    verify_parser = subparsers.add_parser("verify", help="compare expectations against the tree")
    _add_input_arguments(verify_parser)
    verify_parser.add_argument("--expect-truth-sha256")
    verify_parser.add_argument("--expect-candidate-pair", action="append", default=[])

    markdown_parser = subparsers.add_parser("markdown", help="render the coordinator report")
    _add_input_arguments(markdown_parser)

    runbook_parser = subparsers.add_parser(
        "check-runbook", help="check a runbook metadata block against the tree"
    )
    _add_input_arguments(runbook_parser)
    runbook_parser.add_argument("--path", type=Path, required=True)

    args = parser.parse_args(argv)

    # Exit status carries meaning for a coordinator scripting this: 0 the runbook
    # is current, 1 it has drifted, 2 the check could not be made at all. A
    # missing runbook is the third case, not the second.
    if args.command == "check-runbook" and not args.path.is_file():
        print(f"review runbook not found: {args.path}", file=sys.stderr)
        return 2

    try:
        facts = _facts_from_arguments(args)
        if args.command == "show":
            print(json.dumps(facts, indent=2, sort_keys=True) if args.json else render_text(facts))
            return 0
        if args.command == "markdown":
            print(render_markdown(facts))
            return 0
        if args.command == "verify":
            if args.expect_truth_sha256 is None and not args.expect_candidate_pair:
                raise ReviewFactsError("verify requires at least one expectation")
            mismatches = verify_expectations(
                facts,
                expect_truth_sha256=args.expect_truth_sha256,
                expect_candidate_pair=list(args.expect_candidate_pair),
            )
        else:
            try:
                text = args.path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise ReviewFactsError(f"cannot read review runbook {args.path}: {exc}") from exc
            mismatches = compare_runbook(facts, parse_runbook_metadata(text))
    except ReviewFactsError as exc:
        print(f"cannot establish review facts: {exc}", file=sys.stderr)
        return 2

    if mismatches:
        for mismatch in mismatches:
            print(mismatch)
        return 1
    print(
        "review facts match the recorded expectations"
        if args.command == "verify"
        else f"review runbook {args.path} matches the recomputed facts"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
