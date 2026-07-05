"""Data provenance evidence: can someone else obtain the data this repo needs?

The honest limit, stated in the docs as well: this layer sees what is in the
tree, not what the code does with the data. It reports committed binaries,
LFS coverage, and whether a provenance path (download script, DVC, dataset
DOI) exists. It says nothing about leakage or contamination.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field

from ..model import Repo

#: Extensions that are model weights or serialized data regardless of size.
_WEIGHT_EXTS = frozenset({".pt", ".pth", ".ckpt", ".safetensors", ".h5", ".hdf5", ".onnx", ".pb", ".joblib", ".pkl", ".pickle", ".npz", ".npy"})
#: Data extensions flagged only above the size threshold.
_DATA_EXTS = frozenset({".csv", ".tsv", ".json", ".jsonl", ".parquet", ".feather", ".arrow", ".zip", ".tar", ".gz", ".bin"})
_SIZE_THRESHOLD = 10 * 1024 * 1024  # 10 MiB

_DOWNLOAD_SCRIPT_RE = re.compile(r"(download|fetch|get_data|prepare_data|make_dataset)", re.IGNORECASE)
_DATASET_URL_RE = re.compile(
    r"(zenodo\.org|doi\.org|huggingface\.co/datasets|kaggle\.com|osf\.io|figshare\.com|archive\.org|data\.mendeley\.com|physionet\.org|openneuro\.org)",
    re.IGNORECASE,
)
_CHECKSUM_FILE_RE = re.compile(r"(sha256|sha512|md5|checksum)", re.IGNORECASE)
_LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v1"


@dataclass(frozen=True)
class LargeBinary:
    path: str
    size: int
    lfs_tracked: bool


@dataclass
class DataEvidence:
    large_binaries: list[LargeBinary] = field(default_factory=list)
    lfs_patterns: list[str] = field(default_factory=list)
    uses_dvc: bool = False
    download_scripts: list[str] = field(default_factory=list)
    dataset_urls: bool = False
    checksum_files: list[str] = field(default_factory=list)
    uses_hash_verification: bool = False

    @property
    def untracked_binaries(self) -> list[LargeBinary]:
        return [b for b in self.large_binaries if not b.lfs_tracked]

    @property
    def has_provenance(self) -> bool:
        return bool(self.download_scripts) or self.uses_dvc or self.dataset_urls

    @property
    def has_integrity_checks(self) -> bool:
        return bool(self.checksum_files) or self.uses_hash_verification or self.uses_dvc


def _parse_lfs_patterns(repo: Repo) -> list[str]:
    content = repo.read_text(".gitattributes") if repo.exists(".gitattributes") else None
    if not content:
        return []
    patterns = []
    for line in content.splitlines():
        parts = line.split()
        if len(parts) >= 2 and any("filter=lfs" in p for p in parts[1:]):
            patterns.append(parts[0])
    return patterns


def _lfs_covered(path: str, patterns: list[str]) -> bool:
    name = path.rsplit("/", 1)[-1]
    for pattern in patterns:
        candidate = pattern.lstrip("/")
        if fnmatch.fnmatch(path, candidate) or fnmatch.fnmatch(name, candidate):
            return True
    return False


def collect_data(repo: Repo, python_imports: set[str] | None = None) -> DataEvidence:
    evidence = DataEvidence()
    evidence.lfs_patterns = _parse_lfs_patterns(repo)
    tracked = repo.git.tracked_files  # None when not a git repo

    for entry in repo.files:
        rel = str(entry.path)
        name = entry.name

        if name.endswith(".dvc") or name in {"dvc.yaml", "dvc.lock"} or rel.startswith(".dvc/"):
            evidence.uses_dvc = True
        if _CHECKSUM_FILE_RE.search(name) and entry.suffix in {".txt", ".sha256", ".md5", ".sha512", ""}:
            evidence.checksum_files.append(rel)
        if entry.suffix in {".py", ".sh"} and _DOWNLOAD_SCRIPT_RE.search(name):
            evidence.download_scripts.append(rel)

        is_weight = entry.suffix in _WEIGHT_EXTS
        is_big_data = entry.suffix in _DATA_EXTS and entry.size >= _SIZE_THRESHOLD
        if not (is_weight or is_big_data):
            continue
        # Only files git actually tracks are "committed"; fall back to all
        # files when the directory is not a repository yet.
        if tracked is not None and rel not in tracked:
            continue
        if entry.size < 1024:
            content = repo.read_text(rel) or ""
            if content.startswith(_LFS_POINTER_PREFIX):
                evidence.large_binaries.append(LargeBinary(path=rel, size=entry.size, lfs_tracked=True))
                continue
        evidence.large_binaries.append(
            LargeBinary(path=rel, size=entry.size, lfs_tracked=_lfs_covered(rel, evidence.lfs_patterns))
        )

    # A dataset host referenced anywhere in the README counts as a provenance path.
    for readme in repo.find_names("README.md", "README.rst", "README.txt", "README"):
        if len(readme.path.parts) == 1:
            content = repo.read_text(readme.path) or ""
            if _DATASET_URL_RE.search(content):
                evidence.dataset_urls = True

    if python_imports and "hashlib" in python_imports:
        evidence.uses_hash_verification = True

    evidence.download_scripts = list(dict.fromkeys(evidence.download_scripts))
    return evidence
