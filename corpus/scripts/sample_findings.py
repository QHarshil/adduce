#!/usr/bin/env python3
"""Draw a reproducible finding sample or census from a validated corpus run.

Finding-review sampling excludes the unlabelled ``stress`` cohort unless the
caller explicitly selects repositories or cohorts. Every sampled record
carries its repository- and finding-stratum denominators so later analysis can
distinguish an unweighted reviewed-sample proportion from a corpus estimate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import random
import sys
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path
from typing import Any

if __package__:
    from .run_contract import (
        HARNESS_DIRECTORY,
        RUN_META_NAME,
        RunContractError,
        finding_fingerprint,
        sha256_file,
        validate_run_evidence,
    )
else:
    from run_contract import (
        HARNESS_DIRECTORY,
        RUN_META_NAME,
        RunContractError,
        finding_fingerprint,
        sha256_file,
        validate_run_evidence,
    )

ALL_STATUSES = frozenset({"pass", "partial", "fail", "unknown", "not-applicable"})
DEFAULT_STATUSES = frozenset({"fail", "partial"})
DEFAULT_EXCLUDED_COHORTS = frozenset({"stress"})
LABEL_SCHEMA_VERSION = 2
SAMPLE_DESIGN_VERSION = 1
EVIDENCE_BINDING_VERSION = 1
SAMPLE_SET_BINDING_VERSION = 1
SAMPLER_HARNESS_PATH = f"{HARNESS_DIRECTORY}/scripts/sample_findings.py"


def _is_within(path: Path, directory: Path) -> bool:
    """Return whether *path* resolves inside *directory*, including itself."""
    candidate = path.resolve(strict=False)
    root = directory.resolve(strict=False)
    return candidate == root or root in candidate.parents


def _fingerprint_set_sha256(fingerprints: list[str] | set[str]) -> str:
    """Hash a canonical, order-independent finding-fingerprint set."""
    canonical = json.dumps(sorted(set(fingerprints)), ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _sampler_python_identity() -> dict[str, str]:
    """Return the Python identity that determines ``random`` sampling semantics."""
    return {
        "implementation": platform.python_implementation(),
        "version": platform.python_version(),
    }


def _probability(numerator: int, denominator: int) -> dict[str, int | float]:
    if denominator <= 0 or numerator < 0 or numerator > denominator:
        raise ValueError("invalid inclusion-probability fraction")
    fraction = Fraction(numerator, denominator)
    return {
        "numerator": fraction.numerator,
        "denominator": fraction.denominator,
        "value": float(fraction),
    }


def _pick_repos(
    rows: list[dict[str, str]], n_repos: int, rng: random.Random
) -> tuple[list[dict[str, str]], dict[str, dict[str, int]]]:
    """Select repositories uniformly within cohort using round-robin allocation."""
    by_cohort: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_cohort[row["cohort"]].append(row)
    if n_repos < len(by_cohort):
        raise ValueError("n_repos must be at least the number of eligible cohort strata")
    populations = {cohort: len(pool) for cohort, pool in by_cohort.items()}
    for pool in by_cohort.values():
        rng.shuffle(pool)

    picked: list[dict[str, str]] = []
    while len(picked) < min(n_repos, len(rows)) and any(by_cohort.values()):
        for cohort in sorted(by_cohort):
            if by_cohort[cohort] and len(picked) < n_repos:
                picked.append(by_cohort[cohort].pop())

    selected = Counter(row["cohort"] for row in picked)
    design = {
        cohort: {
            "population_size": population,
            "sample_size": selected.get(cohort, 0),
        }
        for cohort, population in sorted(populations.items())
    }
    return picked, design


def _sample_findings(
    payload: dict[str, Any],
    statuses: frozenset[str],
    per_stratum: int,
    rng: random.Random,
    *,
    include_suppressed: bool = True,
    census: bool = False,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    by_stratum: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for finding in payload.get("findings", []):
        if not isinstance(finding, dict):
            continue
        status = finding.get("status")
        if status in statuses and (include_suppressed or not finding.get("suppressed")):
            by_stratum[(str(status), str(finding.get("category", "?")))].append(finding)

    sampled: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for (status, category), pool in sorted(by_stratum.items()):
        sample_size = len(pool) if census else min(per_stratum, len(pool))
        selected = pool if census else rng.sample(pool, sample_size)
        for finding in selected:
            sampled.append(
                (
                    finding,
                    {
                        "status": status,
                        "category": category,
                        "population_size": len(pool),
                        "sample_size": sample_size,
                        "conditional_inclusion_probability": _probability(sample_size, len(pool)),
                    },
                )
            )
    return sampled


def _parse_statuses(value: str) -> frozenset[str]:
    statuses = frozenset(part.strip() for part in value.split(",") if part.strip())
    invalid = statuses - ALL_STATUSES
    if not statuses or invalid:
        choices = ",".join(sorted(ALL_STATUSES))
        raise argparse.ArgumentTypeError(
            f"statuses must be a comma-separated subset of {choices}; invalid={sorted(invalid)}"
        )
    return statuses


def _selector_values(values: list[str]) -> set[str]:
    return {part.strip() for value in values for part in value.split(",") if part.strip()}


def _filter_repositories(
    rows: list[dict[str, str]],
    *,
    include_cohorts: set[str],
    exclude_cohorts: set[str],
    include_repos: set[str],
    exclude_repos: set[str],
    selector_universe: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    universe = rows if selector_universe is None else selector_universe
    all_repos = {row["id"] for row in universe}
    all_cohorts = {row["cohort"] for row in universe}
    unknown_repos = (include_repos | exclude_repos) - all_repos
    unknown_cohorts = (include_cohorts | exclude_cohorts) - all_cohorts
    if unknown_repos:
        raise ValueError(f"unknown repository selector(s): {sorted(unknown_repos)}")
    if unknown_cohorts:
        raise ValueError(f"unknown cohort selector(s): {sorted(unknown_cohorts)}")

    explicit_allowlist = bool(include_cohorts or include_repos)
    selected: list[dict[str, str]] = []
    for row in rows:
        repo_id = row["id"]
        cohort = row["cohort"]
        if include_cohorts and cohort not in include_cohorts:
            continue
        if include_repos and repo_id not in include_repos:
            continue
        if not explicit_allowlist and cohort in DEFAULT_EXCLUDED_COHORTS:
            continue
        if cohort in exclude_cohorts or repo_id in exclude_repos:
            continue
        selected.append(row)
    return selected


def _completed_rows(
    rows: list[dict[str, str]], artifacts: dict[str, bytes]
) -> list[dict[str, str]]:
    completed = [
        row
        for row in rows
        if row.get("crash", "").lower() != "true"
        and f"raw_json/{row.get('id', '')}.json" in artifacts
    ]
    return completed


def _artifact_digests(metadata: dict[str, Any]) -> dict[str, str]:
    return {str(record["path"]): str(record["sha256"]) for record in metadata["artifacts"]}


def _evidence_binding(
    metadata: dict[str, Any], run_meta_sha256: str, raw_path: str
) -> dict[str, int | str]:
    digests = _artifact_digests(metadata)
    return {
        "binding_schema_version": EVIDENCE_BINDING_VERSION,
        "run_schema_version": int(metadata["run_schema_version"]),
        "run_meta_sha256": run_meta_sha256,
        "combined_csv_sha256": digests["combined.csv"],
        "raw_json_sha256": digests[raw_path],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument(
        "--census",
        action="store_true",
        help="select every matching finding from every eligible repository",
    )
    parser.add_argument("--n-repos", type=int, default=None)
    parser.add_argument(
        "--per-stratum",
        "--per-category",
        dest="per_stratum",
        type=int,
        default=None,
        help="maximum findings per status/category stratum per repository",
    )
    parser.add_argument(
        "--statuses",
        type=_parse_statuses,
        default=None,
        help="comma-separated finding statuses (sample default: fail,partial; census: all)",
    )
    parser.add_argument(
        "--include-cohort",
        action="append",
        default=[],
        metavar="COHORT",
        help="restrict sampling to named cohorts; repeat or use commas",
    )
    parser.add_argument(
        "--exclude-cohort",
        action="append",
        default=[],
        metavar="COHORT",
        help="exclude named cohorts; repeat or use commas",
    )
    parser.add_argument(
        "--include-repo",
        action="append",
        default=[],
        metavar="ID",
        help="restrict sampling to named repository IDs; repeat or use commas",
    )
    parser.add_argument(
        "--exclude-repo",
        action="append",
        default=[],
        metavar="ID",
        help="exclude named repository IDs; repeat or use commas",
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--exclude-suppressed",
        action="store_true",
        help="exclude suppressed findings; they are included by default",
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    if _is_within(args.out, args.run):
        sys.exit("--out must be outside the immutable corpus run directory")
    if args.census and (args.n_repos is not None or args.per_stratum is not None):
        sys.exit("--census cannot be combined with --n-repos or --per-stratum")
    n_repos = 10 if args.n_repos is None else args.n_repos
    per_stratum = 2 if args.per_stratum is None else args.per_stratum
    if args.statuses is not None:
        statuses = args.statuses
    elif args.census:
        statuses = ALL_STATUSES
    else:
        statuses = DEFAULT_STATUSES
    if n_repos <= 0 or per_stratum <= 0:
        sys.exit("--n-repos and --per-stratum must be positive")
    if args.out.exists():
        sys.exit(f"refusing to overwrite existing label sample: {args.out}")
    try:
        metadata, artifacts, all_rows = validate_run_evidence(args.run)
        current_sampler_sha256 = sha256_file(Path(__file__).resolve())
        frozen_sampler_sha256 = hashlib.sha256(artifacts[SAMPLER_HARNESS_PATH]).hexdigest()
        if current_sampler_sha256 != frozen_sampler_sha256:
            raise ValueError("current sampler source differs from immutable run harness")
        completed_rows = _completed_rows(all_rows, artifacts)
        include_cohorts = _selector_values(args.include_cohort)
        exclude_cohorts = _selector_values(args.exclude_cohort)
        include_repos = _selector_values(args.include_repo)
        exclude_repos = _selector_values(args.exclude_repo)
        incomplete_requested = include_repos - {row["id"] for row in completed_rows}
        if incomplete_requested:
            raise ValueError(
                f"selected repository scan(s) are incomplete: {sorted(incomplete_requested)}"
            )
        eligible = _filter_repositories(
            completed_rows,
            include_cohorts=include_cohorts,
            exclude_cohorts=exclude_cohorts,
            include_repos=include_repos,
            exclude_repos=exclude_repos,
            selector_universe=all_rows,
        )
    except (RunContractError, ValueError) as exc:
        sys.exit(f"cannot sample corpus run: {exc}")
    if not eligible:
        sys.exit("no completed repository scans match the sampling selectors")

    rng = random.Random(args.seed)
    if args.census:
        picked = list(eligible)
        populations = Counter(row["cohort"] for row in eligible)
        repo_design = {
            cohort: {"population_size": count, "sample_size": count}
            for cohort, count in sorted(populations.items())
        }
    else:
        try:
            picked, repo_design = _pick_repos(eligible, n_repos, rng)
        except ValueError as exc:
            sys.exit(f"cannot sample corpus run: {exc}")
    entries: list[dict[str, Any]] = []
    run_meta_sha256 = sha256_file(args.run / RUN_META_NAME)
    for repo in picked:
        raw_path = f"raw_json/{repo['id']}.json"
        raw_bytes = artifacts[raw_path]
        payload = json.loads(raw_bytes.decode("utf-8"))
        if hashlib.sha256(raw_bytes).hexdigest() != _artifact_digests(metadata)[raw_path]:
            sys.exit(f"cannot sample corpus run: raw evidence changed for {repo['id']}")
        repo_stratum = repo_design[repo["cohort"]]
        repo_probability = _probability(
            repo_stratum["sample_size"], repo_stratum["population_size"]
        )
        for finding, finding_stratum in _sample_findings(
            payload,
            statuses,
            per_stratum,
            rng,
            include_suppressed=not args.exclude_suppressed,
            census=args.census,
        ):
            conditional = finding_stratum["conditional_inclusion_probability"]
            overall = Fraction(
                int(repo_probability["numerator"]) * int(conditional["numerator"]),
                int(repo_probability["denominator"]) * int(conditional["denominator"]),
            )
            entries.append(
                {
                    "label_schema_version": LABEL_SCHEMA_VERSION,
                    "run_id": metadata["run_id"],
                    "repo_id": repo["id"],
                    "repo_commit": repo["resolved_sha"],
                    "cohort": repo["cohort"],
                    "adduce_version": metadata["adduce_version"],
                    "rule_id": finding["rule_id"],
                    "category": finding_stratum["category"],
                    "title": finding.get("title", ""),
                    "finding_status": finding["status"],
                    "finding_confidence": finding.get("confidence"),
                    "severity": finding.get("severity"),
                    "message": finding.get("message", ""),
                    "locations": finding.get("locations", []),
                    "suppressed": bool(finding.get("suppressed", False)),
                    "finding_fingerprint": finding_fingerprint(
                        repo["id"], repo["resolved_sha"], finding
                    ),
                    "run_evidence": _evidence_binding(metadata, run_meta_sha256, raw_path),
                    "sampling": {
                        "design": "census" if args.census else "two-stage-stratified",
                        "design_version": SAMPLE_DESIGN_VERSION,
                        "seed": args.seed,
                        "repository_stratum": {
                            "cohort": repo["cohort"],
                            **repo_stratum,
                            "inclusion_probability": repo_probability,
                        },
                        "finding_stratum": finding_stratum,
                        "overall_inclusion_probability": {
                            "numerator": overall.numerator,
                            "denominator": overall.denominator,
                            "value": float(overall),
                        },
                    },
                    "reviews": [],
                    "adjudication": None,
                }
            )

    if not entries:
        sys.exit("selection produced no findings")
    fingerprints = [str(entry["finding_fingerprint"]) for entry in entries]
    if len(fingerprints) != len(set(fingerprints)):
        sys.exit("selection produced duplicate finding fingerprints")
    sample_set = {
        "binding_schema_version": SAMPLE_SET_BINDING_VERSION,
        "sampler_sha256": current_sampler_sha256,
        "sampler_python": _sampler_python_identity(),
        "arguments": {
            "mode": "census" if args.census else "sample",
            "seed": args.seed,
            "statuses": sorted(statuses),
            "n_repos": None if args.census else n_repos,
            "per_stratum": None if args.census else per_stratum,
            "include_cohorts": sorted(include_cohorts),
            "exclude_cohorts": sorted(exclude_cohorts),
            "include_repos": sorted(include_repos),
            "exclude_repos": sorted(exclude_repos),
            "include_suppressed": not args.exclude_suppressed,
        },
        "eligible_repository_ids": sorted(row["id"] for row in eligible),
        "selected_repository_ids": sorted(row["id"] for row in picked),
        "entry_count": len(entries),
        "finding_fingerprint_set_sha256": _fingerprint_set_sha256(fingerprints),
    }
    for entry in entries:
        entry["sample_set"] = sample_set

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8", newline="\n") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
    repositories = len({entry["repo_id"] for entry in entries})
    cohorts = ", ".join(sorted({str(entry["cohort"]) for entry in entries})) or "none"
    print(
        f"wrote {args.out}: {len(entries)} findings from {repositories} repositories "
        f"(cohorts: {cohorts})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
