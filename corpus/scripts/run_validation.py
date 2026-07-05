#!/usr/bin/env python3
"""Run adduce over the cloned corpus; write per-repo JSON and combined.csv.

Every repository row produces exactly one output row: successful checks
carry the score, tier, reviewer-time bucket, per-category percentages, and
fail/partial counts; crashes and timeouts are recorded with crash=true
rather than dropped, because the crash rate is itself a robustness
measurement (especially on the stress cohort). The adduce version is
recorded with the run — a published number without the version that
produced it is not reproducible.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from clone_repos import CORPUS_DIR, read_repos

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

BASE_COLUMNS = [
    "id",
    "cohort",
    "badge_type",
    "score",
    "tier",
    "reviewer_time_bucket",
    "findings_fail",
    "findings_partial",
    "crash",
    "timeout",
    "runtime_seconds",
    "error",
]


def adduce_version() -> str:
    proc = subprocess.run(
        [sys.executable, "-m", "adduce.cli", "--version"], capture_output=True, text=True
    )
    cleaned = _ANSI_RE.sub("", proc.stdout).strip()  # rich colours the version output
    return cleaned.split()[-1] if cleaned else "unknown"


def check_repo(repo_path: Path, timeout: int) -> tuple[dict | None, str | None, bool, float]:
    """Return (payload, error, timed_out, runtime_seconds) for one repository."""
    started = time.monotonic()
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "adduce.cli", "check", str(repo_path), "--format", "json"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None, f"timed out after {timeout}s", True, time.monotonic() - started
    runtime = time.monotonic() - started
    if completed.returncode not in (0, 1):
        return None, f"exit {completed.returncode}: {completed.stderr.strip()[:200]}", False, runtime
    try:
        return json.loads(completed.stdout), None, False, runtime
    except json.JSONDecodeError:
        return None, "unparseable JSON output", False, runtime


def _category_key(name: str) -> str:
    return "cat_" + name.lower().replace(" & ", "_").replace(" ", "_")


def summarise_payload(payload: dict) -> dict:
    row = {
        "score": payload["total"],
        "tier": payload["tier"],
        "reviewer_time_bucket": payload["reviewer_time"]["bucket"],
        "findings_fail": sum(1 for f in payload["findings"] if f["status"] == "fail"),
        "findings_partial": sum(1 for f in payload["findings"] if f["status"] == "partial"),
    }
    for category in payload["categories"]:
        row[_category_key(category["category"])] = category["percentage"]
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repos", type=Path, default=CORPUS_DIR / "repos.csv")
    parser.add_argument("--clones", type=Path, default=CORPUS_DIR / "clones")
    parser.add_argument("--out", type=Path, default=None, help="defaults to corpus/outputs/<adduce-version>/")
    parser.add_argument("--timeout", type=int, default=120, help="per-repository timeout in seconds")
    args = parser.parse_args()

    version = adduce_version()
    out_dir = args.out or (CORPUS_DIR / "outputs" / version)
    raw_dir = out_dir / "raw_json"
    raw_dir.mkdir(parents=True, exist_ok=True)

    rows = read_repos(args.repos)
    output_rows: list[dict] = []
    for repo in rows:
        base = {
            "id": repo["id"],
            "cohort": repo["cohort"],
            "badge_type": repo.get("badge_type", ""),
            "crash": False,
            "timeout": False,
            "error": "",
        }
        clone_path = args.clones / repo["id"]
        if not clone_path.is_dir():
            base.update(crash=True, error="clone missing (run clone_repos.py first)")
            print(f"{repo['cohort']}: {repo['id']} — clone missing; recorded as crash", file=sys.stderr)
            output_rows.append(base)
            continue
        payload, error, timed_out, runtime = check_repo(clone_path, args.timeout)
        base["runtime_seconds"] = round(runtime, 1)
        if payload is None:
            base.update(crash=True, timeout=timed_out, error=error or "")
            print(f"{repo['cohort']}: {repo['id']} — {error}; recorded as crash", file=sys.stderr)
        else:
            (raw_dir / f"{repo['id']}.json").write_text(
                json.dumps(payload, indent=2) + "\n", encoding="utf-8"
            )
            base.update(summarise_payload(payload))
            print(f"{repo['cohort']}: {repo['id']} — score {payload['total']} ({runtime:.0f}s)")
        output_rows.append(base)

    if not output_rows:
        sys.exit("no repositories in the corpus file")

    cat_columns = sorted({key for row in output_rows for key in row if key.startswith("cat_")})
    combined = out_dir / "combined.csv"
    with combined.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[*BASE_COLUMNS, *cat_columns], restval="")
        writer.writeheader()
        writer.writerows(output_rows)

    meta = {
        "adduce_version": version,
        "timeout_seconds": args.timeout,
        "repos_file": str(args.repos),
        "n_repositories": len(output_rows),
        "n_crashed": sum(1 for r in output_rows if r["crash"]),
    }
    (out_dir / "run_meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {combined} ({len(output_rows)} rows, adduce {version})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
