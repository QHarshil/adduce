#!/usr/bin/env python3
"""Clone the corpus repositories and record what was actually checked out.

Shallow clones keep the corpus cheap to fetch; when a row pins a commit SHA
it is checked out (deepening the clone only if the shallow history does not
contain it). The resolved HEAD of every clone — pinned or floating — is
written to a clones-manifest JSON, because a validation run is only
attributable to exact code if the SHA that actually ran is recorded
somewhere, even for rows selected at HEAD. Failures are logged and recorded
in the manifest, never silently dropped.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

CORPUS_DIR = Path(__file__).resolve().parent.parent
COHORTS = frozenset({"badged_functional", "badged_available", "badged_venue", "unvetted", "stress"})
REQUIRED_COLUMNS = frozenset({"id", "cohort", "repo_url", "commit_sha"})
GIT_TIMEOUT_SECONDS = 600
MANIFEST_NAME = "clones_manifest.json"


def read_repos(path: Path) -> list[dict[str, str]]:
    """Load and validate repos.csv; exit loudly on schema drift rather than guessing."""
    if not path.is_file():
        sys.exit(f"missing {path}; see corpus/README.md for the expected schema.")
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        missing = REQUIRED_COLUMNS - set(row)
        if missing or not row["id"] or row["cohort"] not in COHORTS:
            sys.exit(
                f"malformed corpus row (need columns {sorted(REQUIRED_COLUMNS)}, "
                f"cohort one of {sorted(COHORTS)}): {row}"
            )
    return rows


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS
    )


def clone_one(row: dict[str, str], out_dir: Path) -> dict[str, str | None]:
    record: dict[str, str | None] = {
        "id": row["id"],
        "cohort": row["cohort"],
        "repo_url": row["repo_url"],
        "requested_sha": row["commit_sha"].strip() or None,
        "resolved_sha": None,
        "status": "cloned",
        "error": None,
    }
    dest = out_dir / row["id"]
    try:
        if (dest / ".git").exists():
            record["status"] = "already-cloned"
        else:
            proc = _git("clone", "--quiet", "--depth", "1", row["repo_url"], str(dest))
            if proc.returncode != 0:
                record.update(status="clone-failed", error=proc.stderr.strip()[:300])
                return record
        sha = row["commit_sha"].strip()
        if sha:
            checkout = _git("checkout", "--quiet", sha, cwd=dest)
            if checkout.returncode != 0:
                # Pinned commit outside the shallow history: deepen and retry.
                _git("fetch", "--quiet", "--unshallow", cwd=dest)
                checkout = _git("checkout", "--quiet", sha, cwd=dest)
            if checkout.returncode != 0:
                record.update(status="checkout-failed", error=checkout.stderr.strip()[:300])
                return record
        head = _git("rev-parse", "HEAD", cwd=dest)
        record["resolved_sha"] = head.stdout.strip() if head.returncode == 0 else None
    except subprocess.TimeoutExpired:
        record.update(status="timeout", error=f"git exceeded {GIT_TIMEOUT_SECONDS}s")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repos", type=Path, default=CORPUS_DIR / "repos.csv")
    parser.add_argument("--out", type=Path, default=CORPUS_DIR / "clones")
    args = parser.parse_args()

    rows = read_repos(args.repos)
    args.out.mkdir(parents=True, exist_ok=True)
    records = []
    failures = 0
    for row in rows:
        record = clone_one(row, args.out)
        records.append(record)
        if record["error"]:
            failures += 1
            print(f"{row['cohort']}: {row['id']} — {record['status']}: {record['error']}", file=sys.stderr)
        else:
            sha = record["resolved_sha"] or "?"
            print(f"{row['cohort']}: {row['id']} — {record['status']} @ {sha[:12]}")

    manifest_path = args.out / MANIFEST_NAME
    manifest_path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {manifest_path} ({len(records)} repositories, {failures} failure(s))")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
