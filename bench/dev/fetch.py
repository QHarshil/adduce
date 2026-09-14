#!/usr/bin/env python3
"""Fetch the dev-set paper+code pairs and record what was actually retrieved.

Each row in ``pairs.csv`` pins an official implementation repository at a
commit alongside the arXiv e-print of the paper it implements. Fetching
follows corpus/scripts/clone_repos.py's discipline: a shallow clone checked
out at the pinned commit (deepened only if the shallow history lacks it), an
isolated Git configuration, and a manifest recording what was actually
retrieved rather than what was requested. Ground truth for this benchmark is
labelled from the rendered PDF, never from the LaTeX source, so the PDF is
fetched alongside the e-print unconditionally rather than derived from it.

Nothing here is vendored into git: ``bench/dev/pairs/`` is gitignored, exactly
like ``corpus/clones/``. What ``pairs.csv`` tracks is the pin -- identity, and
after a fetch its digest -- never the fetched content itself.

Ten repositories are the locked evaluation holdout and must never enter the
dev set. A row naming one is refused outright, compared case-insensitively on
its ``owner/repo`` path so a URL variant (scheme, a trailing slash, a trailing
``.git``) cannot slip past the check.

The e-print source is untrusted third-party content: extraction refuses
absolute paths, ``..`` traversal, symlinks, hard links and device files, and
caps both the total extracted size and the member count. arXiv requests are
rate-limited to one every three seconds and carry a descriptive User-Agent.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import IO
from urllib.parse import urlparse

_DEV_DIR = Path(__file__).resolve().parent
_REPOSITORY_ROOT = _DEV_DIR.parent.parent
if str(_REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT / "src"))

from adduce.safe_write import replace_text_regular  # noqa: E402

DEFAULT_PAIRS_CSV = _DEV_DIR / "pairs.csv"
DEFAULT_PAIRS_ROOT = _DEV_DIR / "pairs"
MANIFEST_SCHEMA = "adduce-bench-dev-pairs/1"
MANIFEST_FILENAME = "manifest.json"

REQUIRED_COLUMNS: tuple[str, ...] = (
    "id",
    "repo_url",
    "commit_sha",
    "arxiv_id",
    "arxiv_version",
    "paper_title",
    "framework",
    "license",
    "notes",
)

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_FULL_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_ARXIV_ID_RE = re.compile(r"^\d{4}\.\d{4,5}$")

GIT_TIMEOUT_SECONDS = 600

MAX_EXTRACTED_BYTES = 200 * 1024 * 1024
MAX_EXTRACTED_MEMBERS = 20_000

ARXIV_EPRINT_BASE_URL = "https://arxiv.org/e-print"
ARXIV_PDF_BASE_URL = "https://arxiv.org/pdf"
ARXIV_USER_AGENT = "adduce-bench-dev-fetch/1 (+https://github.com/QHarshil/adduce)"
ARXIV_MIN_INTERVAL_SECONDS = 3.0
_VERSION_SUFFIX_RE = re.compile(r"v(\d+)(?:\.pdf)?/?$")

#: The locked evaluation set (bench/dev spec S7). None of these may ever be
#: fetched into the dev set; the fetcher refuses the row outright rather than
#: trusting the roster to be right.
HOLDOUT_REPOSITORIES: frozenset[str] = frozenset(
    {
        "monkbai/dnn-decompiler",
        "spin-umass/frl",
        "reds-lab/meta-sift",
        "yoruko-tang/modelguard",
        "nemoyuan2008/md-ml",
        "xingangpan/ibn-net",
        "ermongroup/ncsn",
        "princeton-nlp/simcse",
        "google-research/vision_transformer",
        "ashkamath/mdetr",
    }
)


@dataclass(frozen=True)
class PairRow:
    """One validated row of ``pairs.csv``."""

    id: str
    repo_url: str
    commit_sha: str
    arxiv_id: str
    arxiv_version: str
    paper_title: str
    framework: str
    license: str
    notes: str


class PairFetchError(RuntimeError):
    """A step in fetching one dev-set pair could not complete safely."""


class ArchiveSafetyError(PairFetchError):
    """An e-print archive member is not safe to extract."""


# -- parsing ------------------------------------------------------------------


def _parse_row(raw: dict[str, object]) -> PairRow:
    """Validate one CSV row, raising ``ValueError`` with the reason on any defect.

    A short row -- fewer fields than the header -- leaves ``csv.DictReader``'s
    missing columns as ``None``. Every column is required here, so that case
    surfaces as a clear message instead of an ``AttributeError`` on ``None``.
    """
    values: dict[str, str] = {}
    for column in REQUIRED_COLUMNS:
        value = raw.get(column)
        if not isinstance(value, str):
            raise ValueError(f"missing or short column {column!r}")
        values[column] = value.strip()

    row_id = values["id"]
    if not _SAFE_ID_RE.fullmatch(row_id):
        raise ValueError(f"id {row_id!r} is not a safe path component")

    repo_url = values["repo_url"]
    parsed_url = urlparse(repo_url)
    path_segments = [segment for segment in parsed_url.path.split("/") if segment]
    if (
        parsed_url.scheme != "https"
        or not parsed_url.hostname
        or parsed_url.username is not None
        or parsed_url.password is not None
        or bool(parsed_url.query)
        or bool(parsed_url.fragment)
        or len(path_segments) < 2
    ):
        raise ValueError(f"repo_url {repo_url!r} is not a well-formed HTTPS repository URL")

    commit_sha = values["commit_sha"].lower()
    if not _FULL_SHA_RE.fullmatch(commit_sha):
        raise ValueError(f"commit_sha {values['commit_sha']!r} is not a full 40-hex SHA")

    arxiv_id = values["arxiv_id"]
    if not _ARXIV_ID_RE.fullmatch(arxiv_id):
        raise ValueError(f"arxiv_id {arxiv_id!r} is not a recognised arXiv identifier")

    arxiv_version = values["arxiv_version"]
    if not (arxiv_version.isdigit() and int(arxiv_version) >= 1):
        raise ValueError(f"arxiv_version {arxiv_version!r} is not a positive integer")

    return PairRow(
        id=row_id,
        repo_url=repo_url,
        commit_sha=commit_sha,
        arxiv_id=arxiv_id,
        arxiv_version=arxiv_version,
        paper_title=values["paper_title"],
        framework=values["framework"],
        license=values["license"],
        notes=values["notes"],
    )


def _row_label(raw: dict[str, object], index: int) -> str:
    value = raw.get("id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return f"row-{index}"


def read_pairs(path: Path) -> tuple[list[PairRow], list[dict[str, object]]]:
    """Parse ``pairs.csv``, separating valid rows from ready-made manifest entries.

    A malformed row never raises or halts the run: it becomes its own record
    with status ``invalid_row`` and the reason, so one bad row cannot hide, or
    crash the processing of, the other rows.
    """
    valid: list[PairRow] = []
    invalid: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for index, raw in enumerate(reader, start=1):
            label = _row_label(raw, index)
            try:
                row = _parse_row(raw)
            except ValueError as exc:
                invalid.append(_invalid_record(label, str(exc)))
                continue
            if row.id in seen_ids:
                invalid.append(_invalid_record(row.id, f"duplicate id {row.id!r}"))
                continue
            seen_ids.add(row.id)
            valid.append(row)
    return valid, invalid


# -- holdout --------------------------------------------------------------


def _repository_key(repo_url: str) -> str:
    """The lower-cased ``owner/repo`` a URL names.

    Independent of scheme, case, a trailing slash, or a trailing ``.git``, so
    a URL variant cannot be mistaken for a different repository than the one
    it actually names.

    Git also accepts scp-like syntax, ``git@github.com:owner/repo.git``, which
    ``urlparse`` does not recognise as a URL: it yields no path, so the
    owner and repository would be read out of the host segment and the holdout
    check would silently pass. That form is normalised here rather than in the
    caller, because every caller must get the same answer.
    """
    candidate = repo_url.strip()
    if "://" not in candidate and ":" in candidate:
        candidate = candidate.split(":", 1)[1]
        segments = [segment for segment in candidate.split("/") if segment]
    else:
        segments = [segment for segment in urlparse(candidate).path.split("/") if segment]
    key = "/".join(segments[:2])
    if key.lower().endswith(".git"):
        key = key[: -len(".git")]
    return key.lower()


def is_holdout_repository(repo_url: str) -> bool:
    """Whether *repo_url* names one of the ten locked evaluation repositories."""
    return _repository_key(repo_url) in HOLDOUT_REPOSITORIES


# -- archive extraction --------------------------------------------------


def _validate_member(member: tarfile.TarInfo) -> PurePosixPath:
    """The member's normalised relative path, refusing anything unsafe to extract.

    An arXiv e-print is untrusted third-party content: a member is refused if
    it is an absolute path, escapes the extraction root through ``..``, or is
    anything other than a regular file or a directory -- no symlinks, hard
    links, device nodes, or FIFOs.
    """
    relative = PurePosixPath(member.name)
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise ArchiveSafetyError(f"archive member has an unsafe path: {member.name!r}")
    if member.issym() or member.islnk():
        raise ArchiveSafetyError(f"archive member is a link, refused: {member.name!r}")
    if member.isdev():
        raise ArchiveSafetyError(f"archive member is a device file, refused: {member.name!r}")
    if not (member.isreg() or member.isdir()):
        raise ArchiveSafetyError(f"archive member has an unsupported type: {member.name!r}")
    return relative


def _safe_target(dest: Path, resolved_dest: Path, relative: PurePosixPath) -> Path:
    """Join *relative* under *dest*, refusing any escape the host platform's own
    path semantics could otherwise permit.

    ``_validate_member`` already refuses an absolute POSIX path, but a name
    such as ``C:/evil`` is not absolute by POSIX rules and would still be
    treated as absolute -- and so replace *dest* entirely -- by a native
    ``WindowsPath`` join. Resolving the joined target and checking it is
    still inside *resolved_dest* catches that regardless of platform.
    """
    target = dest.joinpath(*relative.parts)
    try:
        target.resolve().relative_to(resolved_dest)
    except ValueError:
        raise ArchiveSafetyError(
            f"archive member escapes the extraction root: {relative}"
        ) from None
    return target


def _copy_stream(source: IO[bytes], handle: IO[bytes]) -> None:
    for chunk in iter(lambda: source.read(1024 * 1024), b""):
        handle.write(chunk)


def extract_source_archive(
    tarball: Path,
    dest: Path,
    *,
    max_bytes: int = MAX_EXTRACTED_BYTES,
    max_members: int = MAX_EXTRACTED_MEMBERS,
) -> int:
    """Extract *tarball* into *dest*, refusing anything unsafe. Returns bytes extracted.

    Members are validated and extracted one at a time rather than through
    ``TarFile.extractall``'s built-in filters, which only ship from Python
    3.12 -- this module supports 3.10. Both the running total of extracted
    bytes and the member count are capped, so a small download cannot
    decompress into an unbounded one.
    """
    extracted_bytes = 0
    dest.mkdir(parents=True, exist_ok=True)
    resolved_dest = dest.resolve()
    with tarfile.open(tarball, mode="r:*") as tar:
        for index, member in enumerate(tar, start=1):
            if index > max_members:
                raise ArchiveSafetyError(f"archive has more than {max_members} members")
            relative = _validate_member(member)
            target = _safe_target(dest, resolved_dest, relative)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            extracted_bytes += member.size
            if extracted_bytes > max_bytes:
                raise ArchiveSafetyError(f"archive extracts more than {max_bytes} bytes")
            source = tar.extractfile(member)
            if source is None:
                raise ArchiveSafetyError(f"archive member could not be read: {member.name!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with source, target.open("wb") as handle:
                _copy_stream(source, handle)
    return extracted_bytes


# -- pinned code clone ----------------------------------------------------

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


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run git in the same isolated configuration corpus/scripts/clone_repos.py uses.

    Duplicated rather than imported: bench/ and corpus/ are independent
    trees, and clone_repos.py sits inside the preregistration's hashed
    analysis plan, so nothing outside corpus/scripts/ should depend on it.
    """
    environment = os.environ.copy()
    for key in list(environment):
        if key in _GIT_ENVIRONMENT_KEYS or key.startswith("GIT_CONFIG_"):
            environment.pop(key, None)
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_LFS_SKIP_SMUDGE": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
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


@dataclass(frozen=True)
class CloneOutcome:
    resolved_sha: str | None
    error: str | None


def clone_pinned(repo_url: str, commit_sha: str, dest: Path) -> CloneOutcome:
    """Shallow-clone *repo_url* and check out *commit_sha*, deepening if needed.

    Mirrors clone_repos.py's ``clone_one``: a depth-1 clone, a checkout of the
    pinned commit, and -- only if the shallow history does not contain it --
    one additional fetch of exactly that object before retrying, rather than
    unshallowing all of history.
    """
    try:
        if (dest / ".git").exists():
            head = _git("rev-parse", "HEAD", cwd=dest)
            resolved = head.stdout.strip().lower() if head.returncode == 0 else ""
            if resolved == commit_sha:
                return CloneOutcome(resolved, None)
            return CloneOutcome(
                None, "existing clone is at a different commit; remove it and retry"
            )
        if dest.exists():
            return CloneOutcome(None, "destination exists but is not a git clone")

        dest.parent.mkdir(parents=True, exist_ok=True)
        cloned = _git("clone", "--quiet", "--depth", "1", repo_url, str(dest))
        if cloned.returncode != 0:
            return CloneOutcome(None, f"clone failed: {cloned.stderr.strip()[:300]}")

        checkout = _git("checkout", "--quiet", "--detach", commit_sha, cwd=dest)
        if checkout.returncode != 0:
            # Fetch exactly the requested object rather than unshallowing all history.
            _git("fetch", "--quiet", "--depth", "1", "origin", commit_sha, cwd=dest)
            checkout = _git("checkout", "--quiet", "--detach", commit_sha, cwd=dest)
        if checkout.returncode != 0:
            return CloneOutcome(None, f"checkout failed: {checkout.stderr.strip()[:300]}")

        head = _git("rev-parse", "HEAD", cwd=dest)
        resolved = head.stdout.strip().lower() if head.returncode == 0 else ""
        if not _FULL_SHA_RE.fullmatch(resolved):
            return CloneOutcome(None, "could not resolve HEAD after checkout")
        if not code_checkout_is_populated(dest):
            # An upstream repository whose contents were removed still has
            # commits, so the clone and the checkout both succeed and leave an
            # empty working tree. Reporting that as fetched hands on a pair
            # with no code side at all.
            return CloneOutcome(resolved, f"commit {resolved[:12]} checks out an empty tree")
        return CloneOutcome(resolved, None)
    except subprocess.TimeoutExpired:
        return CloneOutcome(None, f"git exceeded {GIT_TIMEOUT_SECONDS}s")
    except OSError as exc:
        return CloneOutcome(None, str(exc)[:300])


# -- arXiv paper fetch ------------------------------------------------------


class ArxivThrottle:
    """Keeps arXiv requests at least ``minimum_interval_seconds`` apart."""

    def __init__(self, minimum_interval_seconds: float = ARXIV_MIN_INTERVAL_SECONDS) -> None:
        self._minimum_interval_seconds = minimum_interval_seconds
        self._last_request: float | None = None

    def wait(self) -> None:
        if self._last_request is not None:
            remaining = self._minimum_interval_seconds - (time.monotonic() - self._last_request)
            if remaining > 0:
                time.sleep(remaining)
        self._last_request = time.monotonic()


def _download(
    url: str, dest: Path, throttle: ArxivThrottle, *, timeout: float = 120.0
) -> tuple[str, int, str]:
    """Stream *url* to *dest*, rate-limited, returning its sha256, size, and final URL."""
    throttle.wait()
    request = urllib.request.Request(url, headers={"User-Agent": ARXIV_USER_AGENT})
    digest = hashlib.sha256()
    size = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        final_url = response.geturl()
        with dest.open("wb") as handle:
            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
                handle.write(chunk)
    return digest.hexdigest(), size, final_url


def _served_version(final_url: str, requested_version: str) -> str:
    """The arXiv version actually served, read from the response URL when possible.

    arXiv answers a versioned request with that exact version or a 404; it
    does not silently substitute another one. This still prefers the
    server's own URL over the request, so a change in that behaviour would
    be recorded rather than assumed away.
    """
    match = _VERSION_SUFFIX_RE.search(final_url)
    return match.group(1) if match else requested_version


def fetch_paper(row: PairRow, paper_dir: Path, throttle: ArxivThrottle) -> dict[str, object]:
    """Download the e-print source, extract it, and download the rendered PDF.

    Ground truth for this benchmark is labelled from the PDF, never from the
    LaTeX source, so the PDF is always fetched, not derived from the source.
    """
    result: dict[str, object] = {
        "served_arxiv_version": None,
        "source_tarball_sha256": None,
        "source_tarball_bytes": None,
        "pdf_sha256": None,
        "pdf_bytes": None,
        "error": None,
    }
    tarball_path = paper_dir / "source.tar.gz"
    src_dir = paper_dir / "src"
    pdf_path = paper_dir / "paper.pdf"
    try:
        source_url = f"{ARXIV_EPRINT_BASE_URL}/{row.arxiv_id}v{row.arxiv_version}"
        tarball_sha256, tarball_bytes, final_url = _download(source_url, tarball_path, throttle)
        result["source_tarball_sha256"] = tarball_sha256
        result["source_tarball_bytes"] = tarball_bytes
        result["served_arxiv_version"] = _served_version(final_url, row.arxiv_version)

        if src_dir.exists():
            shutil.rmtree(src_dir)
        extract_source_archive(tarball_path, src_dir)

        pdf_url = f"{ARXIV_PDF_BASE_URL}/{row.arxiv_id}v{row.arxiv_version}"
        pdf_sha256, pdf_bytes, _ = _download(pdf_url, pdf_path, throttle)
        with pdf_path.open("rb") as handle:
            if handle.read(5) != b"%PDF-":
                raise PairFetchError("downloaded PDF is missing its %PDF- signature")
        result["pdf_sha256"] = pdf_sha256
        result["pdf_bytes"] = pdf_bytes
    except (OSError, tarfile.TarError, PairFetchError) as exc:
        result["error"] = str(exc)[:500]
    return result


# -- manifest records -------------------------------------------------------


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _base_record(row: PairRow) -> dict[str, object]:
    return {
        "id": row.id,
        "repo_url": row.repo_url,
        "requested_sha": row.commit_sha,
        "resolved_sha": None,
        "arxiv_id": row.arxiv_id,
        "requested_arxiv_version": row.arxiv_version,
        "served_arxiv_version": None,
        "source_tarball_sha256": None,
        "source_tarball_bytes": None,
        "pdf_sha256": None,
        "pdf_bytes": None,
        "fetched_at": None,
        "status": "pending",
        "error": None,
    }


def _invalid_record(row_id: str, error: str) -> dict[str, object]:
    return {
        "id": row_id,
        "repo_url": None,
        "requested_sha": None,
        "resolved_sha": None,
        "arxiv_id": None,
        "requested_arxiv_version": None,
        "served_arxiv_version": None,
        "source_tarball_sha256": None,
        "source_tarball_bytes": None,
        "pdf_sha256": None,
        "pdf_bytes": None,
        "fetched_at": _utc_now(),
        "status": "invalid_row",
        "error": error,
    }


def _holdout_record(row: PairRow) -> dict[str, object]:
    record = _base_record(row)
    record["status"] = "refused_holdout"
    record["error"] = f"{row.repo_url} is in the locked evaluation holdout set"
    record["fetched_at"] = _utc_now()
    return record


def fetch_pair(row: PairRow, pair_dir: Path, throttle: ArxivThrottle) -> dict[str, object]:
    """Fetch one pair's code and paper, recording whatever each step produced.

    The two steps are independent, so a clone failure does not stop the
    paper from being fetched, and either failure is recorded rather than
    raised.
    """
    record = _base_record(row)

    clone_outcome = clone_pinned(row.repo_url, row.commit_sha, pair_dir / "code")
    record["resolved_sha"] = clone_outcome.resolved_sha

    paper_outcome = fetch_paper(row, pair_dir / "paper", throttle)
    paper_error = paper_outcome.pop("error", None)
    record.update(paper_outcome)

    errors = [message for message in (clone_outcome.error, paper_error) if message]
    record["fetched_at"] = _utc_now()
    record["status"] = "failed" if errors else "fetched"
    record["error"] = "; ".join(str(message) for message in errors) if errors else None
    return record


# -- idempotency -------------------------------------------------------------


def _matches_pinned_identity(row: PairRow, record: dict[str, object] | None) -> bool:
    if record is None or record.get("status") != "fetched":
        return False
    return (
        record.get("repo_url") == row.repo_url
        and record.get("requested_sha") == row.commit_sha
        and record.get("arxiv_id") == row.arxiv_id
        and record.get("requested_arxiv_version") == row.arxiv_version
    )


def code_checkout_is_populated(code_dir: Path) -> bool:
    """Whether the clone actually checked out files, not just a ``.git``.

    A pinned commit can legitimately resolve to an empty tree -- an upstream
    repository whose contents were removed still has commits, and cloning it
    succeeds. Checking only for ``.git`` would report such a pair as fetched
    and leave an empty code side to be discovered much later, by whatever
    tried to measure it.
    """
    if not (code_dir / ".git").exists():
        return False
    return any(entry.name != ".git" for entry in code_dir.iterdir())


def _artifacts_present(pair_dir: Path) -> bool:
    paper_dir = pair_dir / "paper"
    return (
        code_checkout_is_populated(pair_dir / "code")
        and (paper_dir / "source.tar.gz").is_file()
        and (paper_dir / "paper.pdf").is_file()
        and any((paper_dir / "src").glob("*"))
    )


def _already_fetched(row: PairRow, record: dict[str, object] | None, pair_dir: Path) -> bool:
    """Whether *record* already reflects *row*'s pin, with the files still on disk."""
    return _matches_pinned_identity(row, record) and _artifacts_present(pair_dir)


# -- manifest I/O -------------------------------------------------------------


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_manifest(path: Path) -> dict[str, dict[str, object]]:
    if not path.is_file():
        return {}
    try:
        payload: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    records = payload.get("records")
    if not isinstance(records, list):
        return {}
    result: dict[str, dict[str, object]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        record_id = record.get("id")
        if isinstance(record_id, str):
            result[record_id] = record
    return result


def _write_manifest(path: Path, pairs_csv: Path, records: dict[str, dict[str, object]]) -> None:
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "generated_at": _utc_now(),
        "pairs_file_sha256": _sha256(pairs_csv),
        "records": [records[key] for key in sorted(records)],
    }
    rendered = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    replace_text_regular(
        path,
        rendered,
        label="dev-set fetch manifest",
        parent_label="dev-set pairs directory",
    )


# -- CLI ----------------------------------------------------------------------


def _dry_run_line(row: PairRow, pair_dir: Path, already_fetched: bool) -> str:
    if already_fetched:
        return (
            f"{row.id}: already fetched at {row.commit_sha[:12]} / "
            f"arXiv:{row.arxiv_id}v{row.arxiv_version}, would skip"
        )
    return (
        f"{row.id}: would clone {row.repo_url}@{row.commit_sha[:12]} -> {pair_dir / 'code'}; "
        f"would fetch arXiv:{row.arxiv_id}v{row.arxiv_version} -> {pair_dir / 'paper'}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", type=Path, default=DEFAULT_PAIRS_CSV)
    parser.add_argument("--out", type=Path, default=DEFAULT_PAIRS_ROOT)
    parser.add_argument("--only", help="operate on a single pair id")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be fetched, refused, or skipped, without touching the network",
    )
    arguments = parser.parse_args(argv)

    if not arguments.pairs.is_file():
        print(f"missing {arguments.pairs}", file=sys.stderr)
        return 2

    valid_rows, invalid_records = read_pairs(arguments.pairs)
    if arguments.only is not None:
        valid_rows = [row for row in valid_rows if row.id == arguments.only]
        invalid_records = [record for record in invalid_records if record["id"] == arguments.only]
        if not valid_rows and not invalid_records:
            print(f"no pair with id {arguments.only!r} in {arguments.pairs}", file=sys.stderr)
            return 2

    manifest = _load_manifest(arguments.out / MANIFEST_FILENAME)
    throttle = ArxivThrottle()
    failures = 0

    for record in invalid_records:
        failures += 1
        print(f"{record['id']}: invalid row - {record['error']}", file=sys.stderr)
        if not arguments.dry_run:
            manifest[str(record["id"])] = record

    for row in valid_rows:
        pair_dir = arguments.out / row.id
        if is_holdout_repository(row.repo_url):
            failures += 1
            print(
                f"{row.id}: REFUSED - {row.repo_url} is a locked evaluation holdout repository",
                file=sys.stderr,
            )
            if not arguments.dry_run:
                manifest[row.id] = _holdout_record(row)
            continue

        already = _already_fetched(row, manifest.get(row.id), pair_dir)
        if arguments.dry_run:
            print(_dry_run_line(row, pair_dir, already))
            continue
        if already:
            print(f"{row.id}: matches the pinned identity, skipping fetch")
            continue

        record = fetch_pair(row, pair_dir, throttle)
        manifest[row.id] = record
        if record["status"] == "fetched":
            print(f"{row.id}: fetched code@{str(record['resolved_sha'])[:12]}")
        else:
            failures += 1
            print(f"{row.id}: {record['status']} - {record['error']}", file=sys.stderr)

    if not arguments.dry_run:
        arguments.out.mkdir(parents=True, exist_ok=True)
        _write_manifest(arguments.out / MANIFEST_FILENAME, arguments.pairs, manifest)
        print(f"wrote {arguments.out / MANIFEST_FILENAME}", file=sys.stderr)

    total = len(valid_rows) + len(invalid_records)
    print(f"\n{total} pair(s) processed, {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
