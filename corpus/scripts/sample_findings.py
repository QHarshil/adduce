#!/usr/bin/env python3
"""Draw a stratified sample of individual findings for hand-labelling.

Scores cannot say whether individual findings are correct; only a human
reading each finding against the repository can. This script stratifies
first across cohorts (so badged and unvetted repos are both represented)
and then across rule categories (so one chatty category cannot dominate the
sample), emitting one JSON object per line with an empty "label" field for
label_findings.py to fill. The sampler is seeded so a sample is
reproducible from the same run directory.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

SAMPLED_STATUSES = frozenset({"fail", "partial"})


def _pick_repos(rows: list[dict[str, str]], n_repos: int, rng: random.Random) -> list[dict[str, str]]:
    """Round-robin across cohorts until n_repos are selected."""
    by_cohort: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_cohort[row["cohort"]].append(row)
    for pool in by_cohort.values():
        rng.shuffle(pool)
    picked: list[dict[str, str]] = []
    while len(picked) < n_repos and any(by_cohort.values()):
        for cohort in sorted(by_cohort):
            if by_cohort[cohort] and len(picked) < n_repos:
                picked.append(by_cohort[cohort].pop())
    return picked


def _sample_findings(payload: dict, per_category: int, rng: random.Random) -> list[dict]:
    by_category: dict[str, list[dict]] = defaultdict(list)
    for finding in payload.get("findings", []):
        if finding.get("status") in SAMPLED_STATUSES and not finding.get("suppressed"):
            by_category[finding.get("category", "?")].append(finding)
    sampled: list[dict] = []
    for category in sorted(by_category):
        pool = by_category[category]
        sampled.extend(rng.sample(pool, min(per_category, len(pool))))
    return sampled


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", type=Path, required=True, help="a corpus/outputs/<version>/ directory")
    parser.add_argument("--n-repos", type=int, default=12)
    parser.add_argument("--per-category", type=int, default=2, help="max findings per rule category per repo")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    combined = args.run / "combined.csv"
    if not combined.is_file():
        sys.exit(f"missing {combined}; run run_validation.py first.")
    with combined.open(newline="") as handle:
        rows = [r for r in csv.DictReader(handle) if r.get("crash") != "True"]
    if not rows:
        sys.exit("no completed runs to sample from.")

    rng = random.Random(args.seed)
    entries: list[dict] = []
    for repo in _pick_repos(rows, args.n_repos, rng):
        raw = args.run / "raw_json" / f"{repo['id']}.json"
        if not raw.is_file():
            print(f"skipping {repo['id']}: no raw JSON recorded", file=sys.stderr)
            continue
        payload = json.loads(raw.read_text(encoding="utf-8"))
        for finding in _sample_findings(payload, args.per_category, rng):
            entries.append(
                {
                    "repo": repo["id"],
                    "cohort": repo["cohort"],
                    "rule_id": finding["rule_id"],
                    "category": finding.get("category"),
                    "status": finding["status"],
                    "message": finding.get("message", ""),
                    "locations": finding.get("locations", []),
                    "label": "",
                }
            )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + "\n")
    repos = len({e["repo"] for e in entries})
    print(f"wrote {args.out}: {len(entries)} findings from {repos} repositories")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
