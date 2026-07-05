#!/usr/bin/env python3
"""Validation harness: run adduce over a labelled corpus of real repositories.

Reads ``corpus/repos.csv`` (columns: cohort,url,sha — cohort is ``badged`` or
``unvetted``), clones each repository at its pinned SHA, runs
``adduce check --format json`` with a timeout, and writes one JSON result per
repository plus ``corpus/results.csv`` for analysis.

Two measurements come out of this, and they must not be conflated:

1. Score separation (automated): compare the score distributions of the two
   cohorts. With ~50 repositories this is a distributional signal — report
   medians and spread, never invented significance statistics.
2. False-positive rate (manual): scores cannot tell you whether individual
   findings are correct. Sample repositories, hand-label each finding as
   correct or spurious, and report the rate with the sample size.

Clone failures and timeouts are logged and excluded, never silently dropped.
Record the adduce version with any number you publish, and freeze rule
weights before publishing it.
"""

from __future__ import annotations

import csv
import json
import statistics
import subprocess
import sys
from pathlib import Path

CORPUS_DIR = Path(__file__).parent
REPOS_FILE = CORPUS_DIR / "repos.csv"
CLONES_DIR = CORPUS_DIR / "clones"
RESULTS_DIR = CORPUS_DIR / "results"
SUMMARY_FILE = CORPUS_DIR / "results.csv"
PER_REPO_TIMEOUT_SECONDS = 300


def read_corpus() -> list[dict[str, str]]:
    if not REPOS_FILE.is_file():
        sys.exit(
            f"missing {REPOS_FILE}: create it with header 'cohort,url,sha' and one row per repository.\n"
            "cohorts: badged (ACM artifact-badged / equivalent) and unvetted (typical unreviewed repos)."
        )
    with REPOS_FILE.open() as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        missing = {"cohort", "url", "sha"} - set(row)
        if missing or row["cohort"] not in {"badged", "unvetted"}:
            sys.exit(f"malformed corpus row: {row}")
    return rows


def clone_at(url: str, sha: str, destination: Path) -> bool:
    if destination.exists():
        return True
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["git", "clone", "--quiet", url, str(destination)], check=True, timeout=600)
        subprocess.run(
            ["git", "-C", str(destination), "checkout", "--quiet", sha], check=True, timeout=60
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"  clone failed ({exc}); excluded", file=sys.stderr)
        return False


def run_adduce(repo_path: Path) -> dict | None:
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "adduce.cli", "check", str(repo_path), "--format", "json"],
            capture_output=True,
            text=True,
            timeout=PER_REPO_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        print("  timed out; excluded", file=sys.stderr)
        return None
    if completed.returncode not in (0, 1):
        print(f"  adduce failed (exit {completed.returncode}): {completed.stderr[:200]}", file=sys.stderr)
        return None
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        print("  unparseable output; excluded", file=sys.stderr)
        return None


def main() -> int:
    rows = read_corpus()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    adduce_version = subprocess.run(
        [sys.executable, "-m", "adduce.cli", "--version"], capture_output=True, text=True
    ).stdout.strip()

    summary_rows: list[dict] = []
    for row in rows:
        name = row["url"].rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        print(f"{row['cohort']}: {name} @ {row['sha'][:8]}")
        clone_path = CLONES_DIR / row["cohort"] / name
        if not clone_at(row["url"], row["sha"], clone_path):
            continue
        payload = run_adduce(clone_path)
        if payload is None:
            continue
        result_file = RESULTS_DIR / f"{row['cohort']}-{name}.json"
        result_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        summary: dict = {
            "cohort": row["cohort"],
            "repo": name,
            "sha": row["sha"],
            "adduce_version": adduce_version,
            "total": payload["total"],
            "tier": payload["tier"],
            "reviewer_time_bucket": payload["reviewer_time"]["bucket"],
            "findings_fail": sum(1 for f in payload["findings"] if f["status"] == "fail"),
            "findings_partial": sum(1 for f in payload["findings"] if f["status"] == "partial"),
        }
        for category in payload["categories"]:
            key = "cat_" + category["category"].lower().replace(" & ", "_").replace(" ", "_")
            summary[key] = category["percentage"]
        summary_rows.append(summary)
        print(f"  score {payload['total']}")

    if not summary_rows:
        sys.exit("no results produced")

    fieldnames = sorted({key for row in summary_rows for key in row}, key=lambda k: (k.startswith("cat_"), k))
    with SUMMARY_FILE.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summary_rows)

    print(f"\nwrote {SUMMARY_FILE} ({len(summary_rows)} repositories, adduce {adduce_version})")
    for cohort in ("badged", "unvetted"):
        scores = sorted(row["total"] for row in summary_rows if row["cohort"] == cohort)
        if scores:
            print(
                f"  {cohort}: n={len(scores)} median={statistics.median(scores):.1f} "
                f"range=[{scores[0]:.1f}, {scores[-1]:.1f}]"
            )
    print(
        "\nnext: inspect per-category columns for weight tuning, and hand-label a sample of findings "
        "for the false-positive rate — the score file cannot produce that number."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
