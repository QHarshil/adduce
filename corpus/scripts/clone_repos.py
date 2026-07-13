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
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

CORPUS_DIR = Path(__file__).resolve().parent.parent
COHORTS = frozenset({"badged_functional", "badged_available", "badged_venue", "unvetted", "stress"})
REQUIRED_COLUMNS = frozenset({"id", "cohort", "repo_url", "commit_sha"})
GIT_TIMEOUT_SECONDS = 600
MANIFEST_NAME = "clones_manifest.json"
CLONE_SCHEMA_VERSION = 2
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_FULL_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")


class _Digest(Protocol):
    def update(self, data: bytes, /) -> None: ...


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_path(digest: _Digest, repository: Path, path: Path, kind: bytes) -> None:
    """Add one typed worktree entry to ``digest`` without following symlinks."""
    relative = path.relative_to(repository).as_posix().encode()
    digest.update(kind)
    digest.update(len(relative).to_bytes(4, "big"))
    digest.update(relative)
    if kind == b"L":
        target = os.fsencode(os.readlink(path))
        digest.update(len(target).to_bytes(4, "big"))
        digest.update(target)
    elif kind == b"F":
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)


def repository_tree_sha256(repository: Path) -> str:
    """Hash every worktree entry except the repository's root Git database.

    Directory entries are included, so adding or removing an empty directory
    changes the digest. Directory symlinks are hashed as links and are never
    traversed.
    """
    digest = hashlib.sha256()
    for root, directory_names, file_names in os.walk(repository, followlinks=False):
        root_path = Path(root)
        traversable: list[str] = []
        for name in sorted(directory_names):
            path = root_path / name
            relative = path.relative_to(repository)
            if relative == Path(".git"):
                continue
            if path.is_symlink():
                _hash_path(digest, repository, path, b"L")
            else:
                _hash_path(digest, repository, path, b"D")
                traversable.append(name)
        directory_names[:] = traversable
        for name in sorted(file_names):
            path = root_path / name
            _hash_path(digest, repository, path, b"L" if path.is_symlink() else b"F")
    return digest.hexdigest()


def _lfs_pointers(repository: Path) -> list[str]:
    """Return worktree files that still contain Git LFS pointer metadata."""
    marker = b"version https://git-lfs.github.com/spec/v1"
    pointers: list[str] = []
    for root, directory_names, file_names in os.walk(repository, followlinks=False):
        root_path = Path(root)
        directory_names[:] = sorted(
            name
            for name in directory_names
            if name != ".git" and not (root_path / name).is_symlink()
        )
        for name in sorted(file_names):
            path = root_path / name
            if path.is_symlink():
                continue
            try:
                with path.open("rb") as handle:
                    prefix = handle.read(len(marker) + 1)
            except OSError:
                continue
            if prefix.rstrip(b"\r\n") == marker:
                pointers.append(path.relative_to(repository).as_posix())
    return pointers


def _submodule_state(lines: list[str], configured: bool) -> str:
    if not configured:
        return "not_configured"
    if not lines or any(line == "unable to inspect submodule status" for line in lines):
        return "unavailable"
    prefixes = {line[0] for line in lines if line}
    if "U" in prefixes:
        return "conflicted"
    if "+" in prefixes:
        return "modified"
    if "-" in prefixes:
        return "uninitialized"
    return "complete"


def read_repos(path: Path) -> list[dict[str, str]]:
    """Load and validate repos.csv; exit loudly on schema drift rather than guessing."""
    if not path.is_file():
        sys.exit(f"missing {path}; see corpus/README.md for the expected schema.")
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or ())
        missing_columns = REQUIRED_COLUMNS - columns
        if missing_columns:
            sys.exit(f"malformed corpus header; missing columns: {sorted(missing_columns)}")
        rows = list(reader)
    seen_ids: set[str] = set()
    for row in rows:
        repo_id = row["id"].strip()
        repo_url = row["repo_url"].strip()
        requested_sha = row["commit_sha"].strip()
        parsed_url = urlparse(repo_url)
        if (
            not _SAFE_ID_RE.fullmatch(repo_id)
            or repo_id in seen_ids
            or row["cohort"] not in COHORTS
            or parsed_url.scheme != "https"
            or not parsed_url.hostname
            or parsed_url.username is not None
            or parsed_url.password is not None
            or bool(parsed_url.query)
            or bool(parsed_url.fragment)
            or (requested_sha and not _FULL_SHA_RE.fullmatch(requested_sha))
        ):
            sys.exit(
                "malformed corpus row (IDs must be unique/path-safe, repository URLs "
                "must use credential-free HTTPS without query/fragment data, commit pins "
                "must be full 40-hex SHAs, and cohort must "
                f"be one of {sorted(COHORTS)}): {row}"
            )
        seen_ids.add(repo_id)
        row["id"] = repo_id
        row["repo_url"] = repo_url
        row["commit_sha"] = requested_sha.lower()
    return rows


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_LFS_SKIP_SMUDGE": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECONDS,
        env=environment,
    )


def clone_one(row: dict[str, str], out_dir: Path) -> dict[str, object]:
    record: dict[str, object] = {
        "id": row["id"],
        "cohort": row["cohort"],
        "repo_url": row["repo_url"],
        "requested_sha": row["commit_sha"].strip() or None,
        "resolved_sha": None,
        "status": "cloned",
        "error": None,
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
    dest = out_dir / row["id"]
    try:
        created = False
        if (dest / ".git").exists():
            record["status"] = "already-cloned"
        elif dest.exists():
            record.update(
                status="destination-conflict", error="destination exists but is not a Git clone"
            )
            return record
        else:
            proc = _git("clone", "--quiet", "--depth", "1", row["repo_url"], str(dest))
            if proc.returncode != 0:
                record.update(status="clone-failed", error=proc.stderr.strip()[:300])
                return record
            created = True

        origin = _git("remote", "get-url", "origin", cwd=dest)
        origin_url = origin.stdout.strip() if origin.returncode == 0 else ""
        record["origin_url"] = origin_url or None
        if origin_url.rstrip("/").removesuffix(".git") != row["repo_url"].rstrip("/").removesuffix(
            ".git"
        ):
            record.update(status="origin-mismatch", error=f"origin is {origin_url or 'missing'}")
            return record

        sha = row["commit_sha"].strip()
        if sha:
            head_before = _git("rev-parse", "HEAD", cwd=dest)
            if not created and head_before.stdout.strip().lower() != sha.lower():
                record.update(
                    status="existing-commit-mismatch",
                    error="existing clone is not at the requested commit; use a fresh clone directory",
                )
                return record
            checkout = _git("checkout", "--quiet", "--detach", sha, cwd=dest)
            if created and checkout.returncode != 0:
                # Fetch exactly the requested object rather than unshallowing all history.
                _git("fetch", "--quiet", "--depth", "1", "origin", sha, cwd=dest)
                checkout = _git("checkout", "--quiet", sha, cwd=dest)
            if checkout.returncode != 0:
                record.update(status="checkout-failed", error=checkout.stderr.strip()[:300])
                return record
        head = _git("rev-parse", "HEAD", cwd=dest)
        resolved_sha = head.stdout.strip().lower() if head.returncode == 0 else ""
        if not _FULL_SHA_RE.fullmatch(resolved_sha):
            record.update(status="head-unresolved", error="could not resolve a full HEAD commit")
            return record
        record["resolved_sha"] = resolved_sha

        tree = _git("rev-parse", "HEAD^{tree}", cwd=dest)
        record["git_tree_sha"] = tree.stdout.strip().lower() if tree.returncode == 0 else None
        has_submodules = (dest / ".gitmodules").is_file()
        submodule_lines: list[str] = []
        if has_submodules:
            submodules = _git("submodule", "status", "--recursive", cwd=dest)
            submodule_lines = (
                [line for line in submodules.stdout.splitlines() if line.strip()]
                if submodules.returncode == 0
                else ["unable to inspect submodule status"]
            )
            record["submodule_status"] = submodule_lines
        record["submodule_state"] = _submodule_state(submodule_lines, has_submodules)
        lfs_paths = _lfs_pointers(dest)
        record["git_lfs_pointer_count"] = len(lfs_paths)
        record["git_lfs_paths_sample"] = lfs_paths[:20]
        record["git_lfs_state"] = "pointers_present" if lfs_paths else "no_pointers"

        dirty = _git("status", "--porcelain", "--untracked-files=all", cwd=dest)
        is_dirty = bool(dirty.stdout.strip()) if dirty.returncode == 0 else True
        record["dirty"] = is_dirty
        if is_dirty:
            record.update(status="dirty-clone", error="clone has tracked or untracked changes")
        else:
            record["worktree_sha256"] = repository_tree_sha256(dest)
            partial = (
                record["submodule_state"] not in {"not_configured", "complete"}
                or record["git_lfs_state"] == "pointers_present"
            )
            record["acquisition_status"] = "partial" if partial else "complete"
    except subprocess.TimeoutExpired:
        record.update(status="timeout", error=f"git exceeded {GIT_TIMEOUT_SECONDS}s")
    except OSError as exc:
        record.update(status="acquisition-inspection-failed", error=str(exc)[:300])
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repos", type=Path, default=CORPUS_DIR / "repos.csv")
    parser.add_argument("--out", type=Path, default=CORPUS_DIR / "clones")
    args = parser.parse_args()

    rows = read_repos(args.repos)
    args.out.mkdir(parents=True, exist_ok=True)
    manifest_path = args.out / MANIFEST_NAME
    if manifest_path.exists():
        sys.exit(f"refusing to overwrite existing clone manifest: {manifest_path}")
    records = []
    failures = 0
    for row in rows:
        record = clone_one(row, args.out)
        records.append(record)
        if record["error"]:
            failures += 1
            print(
                f"{row['cohort']}: {row['id']} — {record['status']}: {record['error']}",
                file=sys.stderr,
            )
        else:
            sha = str(record["resolved_sha"] or "?")
            acquisition = str(record["acquisition_status"])
            print(
                f"{row['cohort']}: {row['id']} — {record['status']} @ {sha[:12]} "
                f"({acquisition} acquisition)"
            )

    manifest = {
        "clone_schema_version": CLONE_SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "clone_tool_sha256": _sha256(Path(__file__).resolve()),
        "repos_file": str(args.repos),
        "repos_file_sha256": _sha256(args.repos),
        "records": records,
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"\nwrote {manifest_path} ({len(records)} repositories, {failures} failure(s))")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
