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
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

_NUMBER_LINE_RE = re.compile(r"([A-Za-z][\w@/ .-]{0,40}?)[:=]\s*(-?\d+(?:\.\d+)?(?:[eE]-?\d+)?)")


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

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "agree": self.agree,
            "disagreements": self.disagreements,
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
) -> ReproduceReport:
    """Run the command twice and compare fingerprints."""
    report = ReproduceReport(command=command)
    for _ in range(2):
        # Remove prior expected outputs so each run must regenerate them.
        for output in expected_outputs:
            target = root / output
            if target.is_file():
                target.unlink()
        report.runs.append(_run_once(command, root, seed, expected_outputs, timeout_minutes))

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
    shared_metrics = set(first.stdout_metrics) & set(second.stdout_metrics)
    for name in sorted(shared_metrics):
        if abs(first.stdout_metrics[name] - second.stdout_metrics[name]) > 1e-9:
            disagreements.append(
                f"stdout metric '{name}': {first.stdout_metrics[name]} vs {second.stdout_metrics[name]}"
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
