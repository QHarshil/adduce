#!/usr/bin/env python3
"""Compare two validated scans of the same corpus for deterministic output."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__:
    from .run_contract import (
        RunContractError,
        ensure_output_outside,
        load_json_object_bytes,
        require_current_harness_file,
        validate_run_evidence,
        write_json,
    )
else:
    from run_contract import (
        RunContractError,
        ensure_output_outside,
        load_json_object_bytes,
        require_current_harness_file,
        validate_run_evidence,
        write_json,
    )

IDENTITY_FIELDS = (
    "adduce_version",
    "adduce_source_tree_sha256",
    "adduce_source_commit",
    "adduce_source_dirty",
    "builtin_rule_ids",
    "corpus_harness_sha256",
    "configuration_mode",
    "dependency_versions",
    "python",
    "platform",
    "repos_file_sha256",
    "clone_manifest_sha256",
    "claim_ground_truth_sha256",
    "execution_mode",
    "analysis_scope",
    "timeout_seconds",
    "runtime_context",
)
IGNORED_COMBINED_FIELDS = frozenset({"runtime_seconds", "peak_rss_value"})


def _normalized_raw(data: bytes) -> dict:
    payload = load_json_object_bytes(data, "raw comparison artifact")
    # Repository roots are acquisition locations, not analyzer output. All
    # substantive repository identity remains pinned by commit and tree hash.
    repository = payload.get("repository")
    if isinstance(repository, dict):
        payload = {**payload, "repository": {**repository, "root": "<normalized>"}}
    execution = payload.get("corpus_execution")
    if isinstance(execution, dict) and isinstance(execution.get("peak_rss"), dict):
        peak_rss = {**execution["peak_rss"], "value": "<normalized>"}
        payload = {**payload, "corpus_execution": {**execution, "peak_rss": peak_rss}}
    return payload


def compare(run_a: Path, run_b: Path) -> dict:
    if run_a.resolve() == run_b.resolve():
        raise RunContractError("determinism comparison requires two distinct run directories")
    meta_a, artifacts_a, row_list_a = validate_run_evidence(run_a)
    meta_b, artifacts_b, row_list_b = validate_run_evidence(run_b)
    require_current_harness_file(meta_a, "scripts/compare_runs.py", Path(__file__))
    require_current_harness_file(meta_b, "scripts/compare_runs.py", Path(__file__))
    if meta_a["run_id"] == meta_b["run_id"]:
        raise RunContractError("determinism comparison requires distinct run IDs")

    identity_differences: list[dict[str, str]] = []
    output_differences: list[dict[str, str]] = []

    for field in IDENTITY_FIELDS:
        if meta_a.get(field) != meta_b.get(field):
            identity_differences.append(
                {
                    "scope": "run",
                    "field": field,
                    "detail": "runs are not comparable because identities differ",
                }
            )

    rows_a = {row["id"]: row for row in row_list_a}
    rows_b = {row["id"]: row for row in row_list_b}
    if set(rows_a) != set(rows_b):
        identity_differences.append(
            {
                "scope": "run",
                "field": "repository_ids",
                "detail": "runs are not comparable because repository sets differ",
            }
        )

    shared_ids = sorted(set(rows_a) & set(rows_b)) if not identity_differences else []
    for repo_id in shared_ids:
        row_a = {
            key: value
            for key, value in rows_a[repo_id].items()
            if key not in IGNORED_COMBINED_FIELDS
        }
        row_b = {
            key: value
            for key, value in rows_b[repo_id].items()
            if key not in IGNORED_COMBINED_FIELDS
        }
        if row_a != row_b:
            output_differences.append(
                {
                    "scope": repo_id,
                    "field": "combined",
                    "detail": "non-runtime combined fields differ",
                }
            )
        raw_path = f"raw_json/{repo_id}.json"
        raw_a = artifacts_a.get(raw_path)
        raw_b = artifacts_b.get(raw_path)
        if (raw_a is None) != (raw_b is None):
            output_differences.append(
                {
                    "scope": repo_id,
                    "field": "raw_json",
                    "detail": "raw output exists in only one run",
                }
            )
        elif raw_a is not None and raw_b is not None:
            payload_a = _normalized_raw(raw_a)
            payload_b = _normalized_raw(raw_b)
            if payload_a != payload_b:
                output_differences.append(
                    {
                        "scope": repo_id,
                        "field": "raw_json",
                        "detail": "scores, findings, claims, or other raw output differ",
                    }
                )

    comparable = not identity_differences
    return {
        "comparison_schema_version": 1,
        "run_a": meta_a["run_id"],
        "run_b": meta_b["run_id"],
        "comparable": comparable,
        "deterministic": (not output_differences) if comparable else None,
        "identity_differences": identity_differences,
        "output_differences": output_differences,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_a", type=Path)
    parser.add_argument("run_b", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        if args.out:
            ensure_output_outside(args.out, [args.run_a, args.run_b])
            if args.out.exists():
                raise RunContractError(
                    f"refusing to overwrite existing comparison report: {args.out}"
                )
        report = compare(args.run_a, args.run_b)
    except RunContractError as exc:
        sys.exit(f"invalid corpus run: {exc}")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.out, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    if not report["comparable"]:
        return 2
    return 0 if report["deterministic"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
