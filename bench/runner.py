#!/usr/bin/env python3
"""The permanent benchmark harness.

``measure`` runs every target in its own process and writes one report.
``compare`` checks a report against a committed baseline and fails on a
regression.

Two rules govern this file. It never invents a number: an absent target or a
failed measurement is recorded as such, and a report is still valid without it.
And it never claims a value it did not observe: the provenance block records the
git commit and whether the tree was dirty, so a report can always be tied back
to the analyzer that produced it.

This tree is deliberately outside ``corpus/``. The preregistration analysis plan
is an explicit tuple of paths, so a benchmark harness living here changes no
frozen digest and can evolve while human review is in progress.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_BENCH_ROOT = Path(__file__).resolve().parent
_REPOSITORY_ROOT = _BENCH_ROOT.parent
if str(_REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT / "src"))

from adduce import __version__  # noqa: E402
from adduce.safe_write import replace_text_regular  # noqa: E402

REPORT_SCHEMA = "adduce-bench/1"
_WORKER_TIMEOUT_SECONDS = 900

#: Fractional cold-runtime growth tolerated before ``compare`` fails. Wall clock
#: on a shared runner is noisy; a real regression in this analyzer is large.
_RUNTIME_TOLERANCE = 0.25


def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "--no-pager", "-C", str(_REPOSITORY_ROOT), *args],
            capture_output=True,
            text=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _provenance() -> dict[str, Any]:
    status = _git("status", "--porcelain")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "adduce_version": __version__,
        "git_commit": _git("rev-parse", "HEAD"),
        "git_dirty": None if status is None else bool(status),
        "python": platform.python_version(),
        "platform": sys.platform,
        "machine": platform.machine(),
        # Not the preregistration analyzer digest. That is computed by
        # corpus/scripts/preregistration.py and is deliberately not restated
        # here, so the two can never be confused for one another.
        "preregistration_digest": None,
    }


def _stratum(python_loc: int, strata: list[dict[str, Any]]) -> str:
    for band in strata:
        ceiling = band["max_python_loc"]
        if ceiling is None or python_loc <= ceiling:
            return str(band["id"])
    return "unclassified"


def _run_worker(path: Path, *, honor_gitignore: bool) -> dict[str, Any]:
    command = [sys.executable, "-W", "ignore", str(_BENCH_ROOT / "worker.py"), "--path", str(path)]
    if honor_gitignore:
        command.append("--gitignore")
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=_WORKER_TIMEOUT_SECONDS,
            cwd=_REPOSITORY_ROOT,
        )
    except subprocess.TimeoutExpired:
        return {"available": False, "reason": f"timed out after {_WORKER_TIMEOUT_SECONDS}s"}
    except OSError as exc:
        return {"available": False, "reason": f"could not start worker: {exc}"}
    if result.returncode != 0:
        return {
            "available": False,
            "reason": f"worker exited {result.returncode}",
            "stderr_tail": result.stderr[-2000:],
        }
    try:
        measurement: dict[str, Any] = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {"available": False, "reason": f"worker emitted invalid JSON: {exc}"}
    return measurement


def measure(strata_path: Path, only: str | None = None) -> dict[str, Any]:
    manifest = json.loads(strata_path.read_text(encoding="utf-8"))
    loc_strata: list[dict[str, Any]] = manifest["loc_strata"]
    results: list[dict[str, Any]] = []

    for target in manifest["targets"]:
        if only is not None and target["id"] != only:
            continue
        path = _REPOSITORY_ROOT / target["path"]
        record: dict[str, Any] = {
            "id": target["id"],
            "kind": target["kind"],
            "path": target["path"],
            "framework": target["framework"],
        }
        print(f"measuring {target['id']} ...", file=sys.stderr, flush=True)
        default = _run_worker(path, honor_gitignore=False)
        record["default"] = default
        if default.get("available"):
            record["stratum"] = _stratum(default["inputs"]["python_loc"], loc_strata)
        else:
            record["stratum"] = "unavailable"
        if target.get("measure_gitignore_delta"):
            record["gitignore"] = _run_worker(path, honor_gitignore=True)
        results.append(record)

    available = [r for r in results if r["default"].get("available")]
    return {
        "schema": REPORT_SCHEMA,
        "provenance": _provenance(),
        "strata_manifest": {
            "path": strata_path.relative_to(_REPOSITORY_ROOT).as_posix(),
            "gaps": manifest.get("gaps", []),
        },
        "summary": {
            "targets_declared": len(manifest["targets"]) if only is None else 1,
            "targets_measured": len(available),
            "targets_unavailable": len(results) - len(available),
            "determinism_holds": all(
                r["default"]["determinism"]["repeat_render_byte_identical"] for r in available
            )
            if available
            else None,
        },
        "results": results,
    }


def _regressions(baseline: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Every way the current report is worse than the baseline."""
    problems: list[str] = []
    baseline_by_id = {r["id"]: r for r in baseline["results"]}

    for record in current["results"]:
        identifier = record["id"]
        now = record["default"]
        if not now.get("available"):
            continue
        if not now["determinism"]["repeat_render_byte_identical"]:
            problems.append(f"{identifier}: repeated run is no longer byte-identical")

        previous = baseline_by_id.get(identifier, {}).get("default")
        if not previous or not previous.get("available"):
            continue
        if (
            now["inputs"]["files"] != previous["inputs"]["files"]
            or now["inputs"]["python_loc"] != previous["inputs"]["python_loc"]
        ):
            # A different input set is a different measurement. Comparing its
            # runtime would be meaningless, and this is the normal case for a
            # target whose gitignored content is present locally and absent in
            # CI. Determinism is still checked above, independently.
            continue

        if now["outcome"]["parser_failures"] > previous["outcome"]["parser_failures"]:
            problems.append(
                f"{identifier}: parser failures rose "
                f"{previous['outcome']['parser_failures']} -> {now['outcome']['parser_failures']}"
            )
        if record["kind"] == "synthetic" and now["outcome"]["score"] != previous["outcome"]["score"]:
            problems.append(
                f"{identifier}: synthetic-corpus score moved "
                f"{previous['outcome']['score']} -> {now['outcome']['score']}"
            )
        before = previous["performance"]["cold_runtime_seconds"]
        after = now["performance"]["cold_runtime_seconds"]
        if before > 0 and after > before * (1.0 + _RUNTIME_TOLERANCE):
            problems.append(
                f"{identifier}: cold runtime regressed {before:.3f}s -> {after:.3f}s "
                f"(tolerance {_RUNTIME_TOLERANCE:.0%})"
            )
        reads_before = previous["performance"]["disk_reads_per_inventoried_file"]
        reads_after = now["performance"]["disk_reads_per_inventoried_file"]
        if reads_before is not None and reads_after is not None and reads_after > reads_before:
            problems.append(
                f"{identifier}: disk reads per file rose {reads_before} -> {reads_after}"
            )
    return problems


def _render_summary(report: dict[str, Any]) -> str:
    lines = [
        f"{'target':34s} {'stratum':8s} {'py LOC':>9s} {'cold s':>8s} {'reads/file':>11s} {'det':>4s}"
    ]
    for record in report["results"]:
        default = record["default"]
        if not default.get("available"):
            lines.append(f"{record['id']:34s} {'-':8s} {'unavailable: ' + str(default.get('reason'))}")
            continue
        performance = default["performance"]
        lines.append(
            f"{record['id']:34s} {record['stratum']:8s} "
            f"{default['inputs']['python_loc']:9d} "
            f"{performance['cold_runtime_seconds']:8.3f} "
            f"{str(performance['disk_reads_per_inventoried_file']):>11s} "
            f"{'ok' if default['determinism']['repeat_render_byte_identical'] else 'FAIL':>4s}"
        )
    for record in report["results"]:
        variant = record.get("gitignore")
        default = record["default"]
        if not (variant and variant.get("available") and default.get("available")):
            continue
        before = default["performance"]["cold_runtime_seconds"]
        after = variant["performance"]["cold_runtime_seconds"]
        if default["inputs"]["files"] == variant["inputs"]["files"]:
            continue
        lines.append(
            f"  gitignore delta {record['id']}: "
            f"{default['inputs']['files']} -> {variant['inputs']['files']} files, "
            f"{before:.2f}s -> {after:.2f}s, "
            f"score {default['outcome']['score']} -> {variant['outcome']['score']}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    measure_parser = subparsers.add_parser("measure", help="run every target and write a report")
    measure_parser.add_argument("--strata", type=Path, default=_BENCH_ROOT / "strata.json")
    measure_parser.add_argument("--output", type=Path, required=True)
    measure_parser.add_argument("--only", help="measure a single target by id")

    compare_parser = subparsers.add_parser("compare", help="fail on regression against a baseline")
    compare_parser.add_argument("--baseline", type=Path, required=True)
    compare_parser.add_argument("--current", type=Path, required=True)

    arguments = parser.parse_args(argv)

    if arguments.command == "measure":
        report = measure(arguments.strata, only=arguments.only)
        rendered = json.dumps(report, indent=2, sort_keys=False, allow_nan=False) + "\n"
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        replace_text_regular(
            arguments.output,
            rendered,
            label="benchmark report",
            parent_label="benchmark report directory",
        )
        print(_render_summary(report))
        summary = report["summary"]
        print(
            f"\n{summary['targets_measured']} measured, "
            f"{summary['targets_unavailable']} unavailable, "
            f"determinism_holds={summary['determinism_holds']}",
            file=sys.stderr,
        )
        print(f"written to {arguments.output}", file=sys.stderr)
        return 0

    baseline = json.loads(arguments.baseline.read_text(encoding="utf-8"))
    current = json.loads(arguments.current.read_text(encoding="utf-8"))
    problems = _regressions(baseline, current)
    if problems:
        print("benchmark regressions:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print("no benchmark regression against the baseline", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
