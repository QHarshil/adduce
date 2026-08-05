"""Repository model: the filesystem snapshot that collectors read from.

Everything downstream of ingestion works against this model rather than
touching the filesystem directly, so collectors and rules stay testable
against synthetic repositories.
"""

from __future__ import annotations

import fnmatch
import os
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path, PurePath, PurePosixPath
from urllib.parse import urlsplit, urlunsplit

#: Directories never worth scanning. Matched against any path segment.
DEFAULT_EXCLUDES: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        ".tox",
        ".nox",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "__pycache__",
        "node_modules",
        ".eggs",
        "build",
        "dist",
        "site-packages",
        ".ipynb_checkpoints",
        "adduce-submission",
    }
)

#: Import roots mapped to the framework label used by rule applicability gates.
_FRAMEWORK_IMPORTS: dict[str, str] = {
    "torch": "torch",
    "torchvision": "torch",
    "torchaudio": "torch",
    "pytorch_lightning": "lightning",
    "lightning": "lightning",
    "tensorflow": "tensorflow",
    "tf": "tensorflow",
    "keras": "tensorflow",
    "jax": "jax",
    "flax": "jax",
    "numpy": "numpy",
    "sklearn": "sklearn",
    "pandas": "pandas",
    "transformers": "transformers",
    "datasets": "transformers",
    "scipy": "scipy",
    "xgboost": "xgboost",
    "lightgbm": "lightgbm",
}

#: Distribution names (as they appear in dependency files) mapped to frameworks.
_FRAMEWORK_DISTS: dict[str, str] = {
    "torch": "torch",
    "torchvision": "torch",
    "pytorch-lightning": "lightning",
    "lightning": "lightning",
    "tensorflow": "tensorflow",
    "tensorflow-gpu": "tensorflow",
    "keras": "tensorflow",
    "jax": "jax",
    "flax": "jax",
    "numpy": "numpy",
    "scikit-learn": "sklearn",
    "sklearn": "sklearn",
    "pandas": "pandas",
    "transformers": "transformers",
    "scipy": "scipy",
    "xgboost": "xgboost",
    "lightgbm": "lightgbm",
}


def sanitized_remote_url(remote: str) -> str:
    """Remove credentials, query parameters, and fragments from an HTTP remote."""
    if not remote.startswith(("http://", "https://")):
        return remote
    parsed = urlsplit(remote)
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    try:
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError:
        port = ""
    return urlunsplit((parsed.scheme, hostname + port, parsed.path, "", ""))


@dataclass(frozen=True)
class FileEntry:
    """One file in the repository, path relative to the repo root.

    Stored as a PurePosixPath so path strings are /-separated on every
    platform: findings, suppressions, manifests, and reports must render
    and compare identically on Windows and POSIX.
    """

    path: PurePosixPath
    size: int

    @property
    def suffix(self) -> str:
        return self.path.suffix.lower()

    @property
    def name(self) -> str:
        return self.path.name


@dataclass(frozen=True)
class GitInfo:
    """Git metadata for the repository, if it is a repository at all."""

    is_repo: bool = False
    head_commit: str | None = None
    tags: tuple[str, ...] = ()
    tracked_files: frozenset[str] | None = None
    remotes: tuple[str, ...] = ()


@dataclass
class FrameworkSet:
    """Frameworks detected from imports and declared dependencies."""

    detected: set[str] = field(default_factory=set)

    def uses(self, framework: str) -> bool:
        return framework in self.detected

    def uses_any(self, frameworks: set[str] | frozenset[str]) -> bool:
        return bool(self.detected & set(frameworks))

    @staticmethod
    def framework_for_import(module_root: str) -> str | None:
        return _FRAMEWORK_IMPORTS.get(module_root)

    @staticmethod
    def framework_for_dist(dist_name: str) -> str | None:
        return _FRAMEWORK_DISTS.get(dist_name.lower())


@dataclass
class Repo:
    """A scanned repository: file inventory plus git metadata.

    File contents are read lazily and cached, since several collectors
    inspect the same files (README, pyproject.toml, ...).
    """

    root: Path
    files: list[FileEntry] = field(default_factory=list)
    git: GitInfo = field(default_factory=GitInfo)
    frameworks: FrameworkSet = field(default_factory=FrameworkSet)

    def __post_init__(self) -> None:
        self._by_path: dict[str, FileEntry] = {str(f.path): f for f in self.files}
        self._read_cached = lru_cache(maxsize=512)(self._read_uncached)

    # -- lookup -----------------------------------------------------------

    def exists(self, relative: str) -> bool:
        return relative in self._by_path

    def find(self, pattern: str) -> list[FileEntry]:
        """Files whose relative path matches a glob-style pattern."""
        return [f for f in self.files if fnmatch.fnmatch(str(f.path), pattern)]

    def find_names(self, *names: str) -> list[FileEntry]:
        """Files whose basename matches any of the given names (case-insensitive)."""
        wanted = {n.lower() for n in names}
        return [f for f in self.files if f.name.lower() in wanted]

    def python_files(self) -> list[FileEntry]:
        return [f for f in self.files if f.suffix == ".py"]

    # -- content ----------------------------------------------------------

    def read_text(self, relative: str | PurePath) -> str | None:
        """Read a file as UTF-8 text, or None when missing or undecodable."""
        return self._read_cached(str(relative))

    def read_cache_stats(self) -> tuple[int, int]:
        """Content-cache hits and disk reads so far, for telemetry.

        Several collectors inspect the same file, so the gap between these two
        numbers is how much decoding work the run repeated.
        """
        info = self._read_cached.cache_info()
        return info.hits, info.misses

    def _read_uncached(self, relative: str) -> str | None:
        target = self.root / relative
        try:
            return target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None


def _is_excluded(path: PurePosixPath, extra_excludes: frozenset[str]) -> bool:
    parts = set(path.parts)
    return bool(parts & DEFAULT_EXCLUDES) or bool(parts & extra_excludes)


def _git_environment() -> dict[str, str]:
    """A git environment that cannot be steered by ambient configuration.

    System and global config are suppressed so two machines auditing the same
    tree see the same repository, and so a user's ``core.excludesFile`` cannot
    silently change which files an audit examines.
    """
    environment = os.environ.copy()
    for key in list(environment):
        if key.startswith("GIT_"):
            environment.pop(key, None)
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return environment


def _git_query(root: Path, *operation: str) -> list[str]:
    """A hardened, read-only git command vector.

    Every git call the scan makes is built here, so none can omit a guard.
    ``core.fsmonitor=false`` is the load-bearing one: a repository may configure
    a filesystem-monitor hook, and git would otherwise execute it — letting a
    scanned repository run code inside an audit whose whole promise is that it
    never runs repository code. ``core.quotePath=true`` keeps path encoding
    identical across call sites.
    """
    return [
        "git",
        "--no-pager",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.quotePath=true",
        "-C",
        str(root),
        *operation,
    ]


def _is_ignored_directory(root: Path) -> bool:
    """Whether git ignores the scan root itself."""
    try:
        result = subprocess.run(
            _git_query(root, "check-ignore", "-q", "."),
            capture_output=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
            env=_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _collect_ignored(root: Path) -> tuple[frozenset[str], tuple[str, ...]] | None:
    """Paths git ignores, as an exact file set plus directory prefixes.

    Git's own matcher is used rather than a reimplementation: nested
    ``.gitignore`` files, negation, anchoring, and directory-only patterns are
    subtle enough that agreeing with git by construction is worth one
    subprocess. Returns None when the answer is unavailable — not a repository,
    no git, or a failed call — so the caller can fall back to scanning
    everything rather than silently examining less than it reports.

    ``--directory`` collapses a wholly ignored directory to its prefix, so a
    500,000-file ignored dataset costs one entry instead of half a million.
    """
    if _is_ignored_directory(root):
        # Auditing a gitignored working copy is legitimate, and every path
        # below an ignored root is ignored. Filtering here would empty the
        # inventory and report a clean repository with nothing in it. Current
        # git happens to fail this query outright; not relying on that.
        return None
    try:
        result = subprocess.run(
            # ``-z`` makes paths NUL-delimited, which git never quotes, so
            # core.quotePath cannot affect this output either way.
            _git_query(
                root,
                "ls-files",
                "-z",
                "--others",
                "--ignored",
                "--exclude-standard",
                "--directory",
            ),
            capture_output=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
            env=_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None

    files: set[str] = set()
    directories: list[str] = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        try:
            entry = raw.decode("utf-8")
        except UnicodeDecodeError:
            # A path this process cannot name is a path it cannot match
            # against the inventory; leaving it out only scans more.
            continue
        if entry.endswith("/"):
            directories.append(entry)
        else:
            files.add(entry)
    return frozenset(files), tuple(sorted(directories))


def _collect_git_info(root: Path) -> GitInfo:
    git_environment = _git_environment()

    def run(*args: str) -> str | None:
        try:
            result = subprocess.run(
                _git_query(root, *args),
                capture_output=True,
                text=True,
                timeout=10,
                stdin=subprocess.DEVNULL,
                env=git_environment,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return result.stdout.strip() if result.returncode == 0 else None

    inside = run("rev-parse", "--is-inside-work-tree")
    if inside != "true":
        return GitInfo(is_repo=False)

    head = run("rev-parse", "HEAD")
    # A tag is evidence for this scanned state only when it points at HEAD.
    tags_out = run("tag", "--points-at", "HEAD")
    tags = tuple(t for t in (tags_out or "").splitlines() if t.strip())
    tracked_out = run("ls-files")
    tracked = frozenset((tracked_out or "").splitlines()) if tracked_out is not None else None
    remotes_out = run("remote", "-v")
    remotes = tuple(dict.fromkeys(line.split()[1] for line in (remotes_out or "").splitlines() if line.split()))
    return GitInfo(is_repo=True, head_commit=head, tags=tags, tracked_files=tracked, remotes=remotes)


def scan_repository(
    root: Path,
    exclude: tuple[str, ...] = (),
    honor_gitignore: bool = True,
) -> Repo:
    """Walk a directory tree and build the repository model.

    ``exclude`` adds directory names (path segments) to the default skip list.
    Framework detection is filled in later by the evidence collectors, which
    see both imports and declared dependencies.

    ``honor_gitignore`` drops paths git ignores, and is on by default. A
    gitignored ``data/`` or ``wandb/`` tree is not part of the artifact under
    review, so scanning it costs time and lets one repository earn a status
    from another repository's files. Pass ``False`` to examine the whole tree
    regardless. The filter is purely subtractive: everything else about the
    walk, including its refusal to follow symlinks, is unchanged.
    """
    root = root.resolve()
    extra = frozenset(exclude)
    ignored_files: frozenset[str] = frozenset()
    ignored_directories: tuple[str, ...] = ()
    if honor_gitignore and (ignored := _collect_ignored(root)) is not None:
        ignored_files, ignored_directories = ignored
    entries: list[FileEntry] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = PurePosixPath(path.relative_to(root).as_posix())
        if _is_excluded(rel, extra):
            continue
        text = str(rel)
        if text in ignored_files or any(text.startswith(prefix) for prefix in ignored_directories):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        entries.append(FileEntry(path=rel, size=size))
    return Repo(root=root, files=entries, git=_collect_git_info(root))
