"""Notebook evidence: execution order, hidden state, environment capture.

Parses ``.ipynb`` JSON directly (no nbformat dependency). Staleness and
hidden-state findings are heuristics and are reported as such.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..model import Repo

_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_][\w]*)", re.MULTILINE)
_PIP_RE = re.compile(r"^\s*[!%]\s*pip3?\s+install\b", re.MULTILINE)
_ABS_PATH_RE = re.compile(
    r"(/Users/\w|/home/(?!runner\b)\w|[A-Z]:\\\\?Users|~/(?:Desktop|Documents|Downloads)|/Volumes/)"
)
_SEED_RE = re.compile(r"\b(seed(?:_everything)?|manual_seed|set_seed|random_state)\s*[(=]")
_RANDOM_USE_RE = re.compile(
    r"\b(random\.|np\.random|torch\.rand|shuffle|sample\(|randint|randn|permutation|train_test_split)"
)


@dataclass
class NotebookInfo:
    path: str
    code_cells: int = 0
    executed_cells: int = 0
    execution_counts: list[int] = field(default_factory=list)
    monotonic: bool = True
    has_gaps: bool = False
    has_outputs: bool = False
    pip_install_cells: list[int] = field(default_factory=list)  # cell indices
    abs_path_cells: list[int] = field(default_factory=list)
    imports: set[str] = field(default_factory=set)
    uses_randomness: bool = False
    seed_before_randomness: bool | None = None  # None when no randomness used
    has_kernelspec: bool = False
    has_language_info: bool = False
    has_companion_script: bool = False
    parse_error: bool = False


@dataclass
class NotebookEvidence:
    notebooks: list[NotebookInfo] = field(default_factory=list)

    @property
    def present(self) -> bool:
        return any(not n.parse_error for n in self.notebooks)

    @property
    def all_imports(self) -> set[str]:
        return set().union(*(n.imports for n in self.notebooks)) if self.notebooks else set()


def _analyse_notebook(path: str, raw: str, companion_stems: set[str]) -> NotebookInfo:
    info = NotebookInfo(path=path)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        info.parse_error = True
        return info
    if not isinstance(data, dict):
        info.parse_error = True
        return info

    metadata = data.get("metadata") or {}
    info.has_kernelspec = bool(metadata.get("kernelspec"))
    info.has_language_info = bool(metadata.get("language_info"))

    stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    info.has_companion_script = stem in companion_stems

    seen_seed = False
    seen_random_before_seed = False
    for index, cell in enumerate(data.get("cells") or []):
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        info.code_cells += 1
        source = cell.get("source")
        text = "".join(source) if isinstance(source, list) else str(source or "")

        count = cell.get("execution_count")
        if isinstance(count, int):
            info.executed_cells += 1
            info.execution_counts.append(count)
        if cell.get("outputs"):
            info.has_outputs = True
        if _PIP_RE.search(text):
            info.pip_install_cells.append(index)
        if _ABS_PATH_RE.search(text):
            info.abs_path_cells.append(index)
        info.imports.update(_IMPORT_RE.findall(text))

        if _SEED_RE.search(text):
            seen_seed = True
        if _RANDOM_USE_RE.search(text):
            info.uses_randomness = True
            if not seen_seed:
                seen_random_before_seed = True

    counts = info.execution_counts
    info.monotonic = counts == sorted(counts)
    info.has_gaps = bool(counts) and (max(counts) - min(counts) + 1) != len(counts)
    if info.uses_randomness:
        info.seed_before_randomness = seen_seed and not seen_random_before_seed
    return info


def collect_notebooks(repo: Repo) -> NotebookEvidence:
    evidence = NotebookEvidence()
    notebooks = [f for f in repo.files if f.suffix == ".ipynb"]
    if not notebooks:
        return evidence
    # jupytext/papermill companions: any .py/.md sharing a notebook's stem.
    companion_stems = {
        f.path.stem for f in repo.files if f.suffix in {".py", ".md"} and f.path.stem
    }
    for entry in notebooks:
        raw = repo.read_text(entry.path)
        if raw is None:
            continue
        evidence.notebooks.append(_analyse_notebook(str(entry.path), raw, companion_stems))
    return evidence
