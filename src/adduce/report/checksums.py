"""checksums.txt: SHA-256 of the data, model, and result artifacts in the tree."""

from __future__ import annotations

import hashlib

from ..engine import CheckResult

_CHECKSUMMABLE_EXTS = frozenset(
    {".pt", ".pth", ".ckpt", ".safetensors", ".h5", ".hdf5", ".onnx", ".pb", ".joblib",
     ".pkl", ".pickle", ".npz", ".npy", ".csv", ".tsv", ".parquet", ".feather", ".zip",
     ".tar", ".gz", ".json", ".jsonl"}
)
_MAX_BYTES = 4 * 1024 * 1024 * 1024


def render(result: CheckResult) -> str:
    lines = ["# SHA-256 checksums of data/model/result artifacts", "# verify with: shasum -a 256 -c checksums.txt"]
    repo = result.repo
    counted = 0
    for entry in repo.files:
        if entry.suffix not in _CHECKSUMMABLE_EXTS or entry.size > _MAX_BYTES:
            continue
        parts_lower = {p.lower() for p in entry.path.parts[:-1]}
        if not (parts_lower & {"data", "datasets", "models", "checkpoints", "results", "outputs", "weights"}):
            continue
        digest = hashlib.sha256()
        try:
            with open(repo.root / entry.path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(chunk)
        except OSError:
            continue
        lines.append(f"{digest.hexdigest()}  {entry.path}")
        counted += 1
    if counted == 0:
        lines.append("# no data/model artifacts found in the tree (they may be downloaded at setup)")
    return "\n".join(lines)
