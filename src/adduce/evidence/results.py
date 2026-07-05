"""Results evidence: the numbers the repository actually logged.

Reads local result artifacts (CSV/JSON/JSONL under results-like directories,
plus presence of TensorBoard, Weights & Biases, and MLflow output) so that
reported paper metrics can be reconciled against logged values.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field

from ..model import Repo

_RESULT_DIRS = frozenset({"results", "outputs", "logs", "metrics", "eval", "evaluation", "runs", "artifacts"})
_NAME_HINT_RE = re.compile(r"(result|metric|eval|score)", re.IGNORECASE)
_MAX_RESULT_BYTES = 5_000_000
_MAX_ROWS = 500


@dataclass
class ResultFile:
    path: str
    metrics: dict[str, list[float]] = field(default_factory=dict)  # column/key -> values


@dataclass
class ResultsEvidence:
    files: list[ResultFile] = field(default_factory=list)
    has_tensorboard: bool = False
    has_wandb: bool = False
    has_mlflow: bool = False

    @property
    def present(self) -> bool:
        return bool(self.files) or self.has_tensorboard or self.has_wandb or self.has_mlflow

    def lookup_metric(self, name: str) -> list[tuple[str, list[float]]]:
        """Result columns whose normalised name matches the metric name."""
        wanted = _normalise(name)
        matches = []
        for result in self.files:
            for column, values in result.metrics.items():
                normalised = _normalise(column)
                if wanted == normalised or wanted in normalised or normalised in wanted:
                    matches.append((f"{result.path}:{column}", values))
        return matches


def _normalise(name: str) -> str:
    return re.sub(r"[^a-z0-9@]", "", name.lower())


def _parse_csv(text: str, result: ResultFile) -> None:
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return
    columns: dict[int, str] = {i: h.strip() for i, h in enumerate(header) if h.strip()}
    for row_index, row in enumerate(reader):
        if row_index >= _MAX_ROWS:
            break
        for col_index, cell in enumerate(row):
            name = columns.get(col_index)
            if not name:
                continue
            try:
                value = float(cell)
            except ValueError:
                continue
            result.metrics.setdefault(name, []).append(value)


def _collect_scalars(data: object, result: ResultFile, prefix: str = "") -> None:
    if isinstance(data, dict):
        for key, value in data.items():
            dotted = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                result.metrics.setdefault(dotted, []).append(float(value))
            elif isinstance(value, dict):
                _collect_scalars(value, result, dotted)


def _parse_json(text: str, result: ResultFile) -> None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return
    _collect_scalars(data, result)


def _parse_jsonl(text: str, result: ResultFile) -> None:
    for line_index, line in enumerate(text.splitlines()):
        if line_index >= _MAX_ROWS:
            break
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        _collect_scalars(data, result)


def _looks_like_result_file(entry) -> bool:
    parts_lower = {p.lower() for p in entry.path.parts[:-1]}
    in_result_dir = bool(parts_lower & _RESULT_DIRS)
    name_hints = bool(_NAME_HINT_RE.search(entry.name))
    return in_result_dir or name_hints


def collect_results(repo: Repo) -> ResultsEvidence:
    evidence = ResultsEvidence()
    for entry in repo.files:
        rel = str(entry.path)
        if entry.name.startswith("events.out.tfevents"):
            evidence.has_tensorboard = True
            continue
        if rel.startswith("wandb/") or "/wandb/" in rel:
            evidence.has_wandb = True
            continue
        if rel.startswith("mlruns/") or "/mlruns/" in rel:
            evidence.has_mlflow = True
            continue
        if entry.suffix not in {".csv", ".json", ".jsonl"} or entry.size > _MAX_RESULT_BYTES:
            continue
        if not _looks_like_result_file(entry):
            continue
        text = repo.read_text(rel)
        if text is None:
            continue
        result = ResultFile(path=rel)
        if entry.suffix == ".csv":
            _parse_csv(text, result)
        elif entry.suffix == ".json":
            _parse_json(text, result)
        else:
            _parse_jsonl(text, result)
        if result.metrics:
            evidence.files.append(result)
    return evidence
