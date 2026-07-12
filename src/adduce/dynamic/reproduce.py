"""``adduce reproduce``: the empirical definition of reproducibility.

Runs the manifest's smoke target (or a supplied command) twice with a pinned
seed environment, fingerprints each run — expected output files hashed,
numeric values parsed from stdout — and asserts the two runs agree.

This executes repository code. It is opt-in, requires explicit confirmation,
and is designed to run inside the repository's own container or CI where the
environment already exists. ``adduce check`` never reaches this module.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

_NUMBER_LINE_RE = re.compile(
    r"([A-Za-z][\w@/ .-]{0,40}?)[:=]\s*(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
)


@dataclass
class RunFingerprint:
    exit_code: int
    duration_seconds: float
    output_hashes: dict[str, str] = field(default_factory=dict)   # path -> sha256
    stdout_metrics: dict[str, float] = field(default_factory=dict)
    missing_outputs: list[str] = field(default_factory=list)


@dataclass
class ReproduceReport:
    command: str
    runs: list[RunFingerprint] = field(default_factory=list)
    agree: bool | None = None
    disagreements: list[str] = field(default_factory=list)
    comparable_fingerprints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "agree": self.agree,
            "disagreements": self.disagreements,
            "comparable_fingerprints": self.comparable_fingerprints,
            "runs": [
                {
                    "exit_code": run.exit_code,
                    "duration_seconds": round(run.duration_seconds, 1),
                    "output_hashes": run.output_hashes,
                    "stdout_metrics": run.stdout_metrics,
                    "missing_outputs": run.missing_outputs,
                }
                for run in self.runs
            ],
        }


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_stdout_metrics(stdout: str) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for line in stdout.splitlines()[-200:]:
        for name, value in _NUMBER_LINE_RE.findall(line):
            key = name.strip().lower()
            if any(word in key for word in ("loss", "acc", "f1", "score", "metric", "auc", "error", "ppl", "bleu", "rouge", "ndcg")):
                try:
                    metrics[key] = float(value)
                except ValueError:
                    continue
    return metrics


def _validate_expected_outputs(expected_outputs: list[str]) -> list[str]:
    """Return configuration errors for paths that must never escape a run workspace."""
    errors: list[str] = []
    for output in expected_outputs:
        path = Path(output)
        if not output.strip() or path.is_absolute() or path == Path(".") or ".." in path.parts:
            errors.append(
                f"{output or '<empty>'}: expected output must be a relative file path within the repository"
            )
    return errors


def _remove_copied_outputs(workspace: Path, expected_outputs: list[str]) -> None:
    """Remove only workspace copies so every attempt must generate fresh output."""
    for output in expected_outputs:
        target = workspace / output
        if target.is_symlink() or target.is_file():
            target.unlink()


def _copy_ignore(directory: str, names: list[str]) -> set[str]:
    """Skip local resolution cache and all symlinks in an isolated workspace.

    Dereferencing a repository symlink could copy data from outside the
    repository into the run workspace. Preserving it could let executed code
    mutate the original target. Neither is acceptable for the fenced runner.
    Other directories, including ``.git`` and repository-local environments,
    are copied because smoke commands may legitimately depend on them.
    """
    parent = Path(directory)
    ignored = {name for name in names if (parent / name).is_symlink()}
    if parent.name == ".adduce":
        ignored.add("cache")
    return ignored


def _metric_value(metrics: dict[str, float], expected_name: str) -> float | None:
    """Resolve an explicitly named metric without treating arbitrary stdout as evidence."""
    expected = expected_name.strip().lower()
    if expected in metrics:
        return metrics[expected]

    # Frameworks commonly prefix metrics (for example ``validation accuracy``).
    # Accept a suffix only when it identifies exactly one parsed metric.
    candidates = [
        value
        for name, value in metrics.items()
        if name.endswith(f" {expected}") or name.endswith(f"/{expected}")
    ]
    return candidates[0] if len(candidates) == 1 else None


def _run_once(
    command: str,
    root: Path,
    seed: int,
    expected_outputs: list[str],
    timeout_minutes: int,
) -> RunFingerprint:
    env_extra = {
        "PYTHONHASHSEED": str(seed),
        "ADDUCE_SEED": str(seed),
        "CUBLAS_WORKSPACE_CONFIG": ":4096:8",
    }
    import os

    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=root,
            env={**os.environ, **env_extra},
            capture_output=True,
            text=True,
            timeout=timeout_minutes * 60,
        )
        exit_code = completed.returncode
        stdout = completed.stdout + "\n" + completed.stderr
    except subprocess.TimeoutExpired as exc:
        exit_code = -1
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
    duration = time.monotonic() - started

    fingerprint = RunFingerprint(exit_code=exit_code, duration_seconds=duration)
    fingerprint.stdout_metrics = _parse_stdout_metrics(stdout)
    for output in expected_outputs:
        target = root / output
        if target.is_file():
            fingerprint.output_hashes[output] = _hash_file(target)
        else:
            fingerprint.missing_outputs.append(output)
    return fingerprint


def reproduce(
    root: Path,
    command: str,
    expected_outputs: list[str],
    seed: int = 0,
    timeout_minutes: int = 30,
    expected_metrics: list[str] | None = None,
) -> ReproduceReport:
    """Run the command twice in clean copies of the repository and compare evidence.

    A successful process exit is necessary but not sufficient. Agreement requires
    at least one expected output hash or explicitly named metric that can be
    compared across both attempts.
    """
    report = ReproduceReport(command=command)
    expected_metrics = expected_metrics or []
    path_errors = _validate_expected_outputs(expected_outputs)
    if path_errors:
        report.agree = False
        report.disagreements = path_errors
        return report

    root = root.resolve()
    with tempfile.TemporaryDirectory(prefix="adduce-reproduce-") as temporary:
        temporary_root = Path(temporary)
        for attempt in range(2):
            workspace = temporary_root / f"run-{attempt + 1}"
            # Skip repository symlinks while copying so commands cannot modify
            # original inputs through a link from the temporary workspace.
            shutil.copytree(
                root,
                workspace,
                symlinks=False,
                ignore_dangling_symlinks=True,
                ignore=_copy_ignore,
            )
            _remove_copied_outputs(workspace, expected_outputs)
            report.runs.append(
                _run_once(command, workspace, seed, expected_outputs, timeout_minutes)
            )

    first, second = report.runs
    disagreements: list[str] = []
    if first.exit_code != 0 or second.exit_code != 0:
        disagreements.append(
            f"non-zero exit codes (run 1: {first.exit_code}, run 2: {second.exit_code})"
        )
    for output in expected_outputs:
        hash_one = first.output_hashes.get(output)
        hash_two = second.output_hashes.get(output)
        if hash_one is None or hash_two is None:
            disagreements.append(f"{output}: not produced by both runs")
        elif hash_one != hash_two:
            disagreements.append(f"{output}: content differs between runs")
        else:
            report.comparable_fingerprints.append(f"output:{output}")
    for name in expected_metrics:
        value_one = _metric_value(first.stdout_metrics, name)
        value_two = _metric_value(second.stdout_metrics, name)
        if value_one is None or value_two is None:
            disagreements.append(f"expected stdout metric '{name}': not reported by both runs")
        elif abs(value_one - value_two) > 1e-9:
            disagreements.append(
                f"stdout metric '{name}': {value_one} vs {value_two}"
            )
        else:
            report.comparable_fingerprints.append(f"metric:{name}")
    if not report.comparable_fingerprints:
        disagreements.append(
            "no comparable fingerprints: declare an expected output or expected metric "
            "that both runs produce"
        )
    report.disagreements = disagreements
    report.agree = not disagreements
    return report


def save_report(root: Path, report: ReproduceReport) -> Path:
    directory = root / ".adduce"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "reproduce-report.json"
    target.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return target
