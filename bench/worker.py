#!/usr/bin/env python3
"""One benchmark measurement, in its own process.

Peak resident memory is a process high-water mark, so measuring several
repositories in one interpreter reports the largest of them for all of them.
Each measurement therefore runs here, alone, and prints a single JSON object.

A default invocation runs ``run_check`` exactly once. Peak RSS then covers one
analysis, which is what a user actually gets. Measured on ``transformers``:
353 MB for one ``run_check`` call, versus ~595 MB when a worker ran two and
held the first ``CheckResult`` alive for a byte-identity comparison against
the second. No user runs an audit twice and keeps both results resident; the
595 MB figure had been quoted as if it were product behaviour, and it is a
harness artifact instead. Determinism genuinely needs two calls in one process
to compare renders, so it is its own mode (``--determinism``), reported on its
own, never folded into a performance measurement.

Nothing in this file estimates. A quantity that cannot be measured on this
platform is reported absent, with the reason, rather than defaulted to zero.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT / "src"))


def peak_rss_observation() -> dict[str, Any]:
    """Peak RSS with the platform's documented unit, or an explicit absence.

    ``ru_maxrss`` is bytes on Darwin and kibibytes on Linux, and unavailable on
    Windows. The unit is reported rather than normalised so a reader can never
    silently compare two different scales. This mirrors the contract in
    ``corpus/scripts/check_builtin.py``; it is restated rather than imported so
    that the benchmark harness does not depend on a preregistration-hashed file.
    """
    platform_id = sys.platform
    try:
        import resource

        value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except (ImportError, OSError, ValueError):
        value = 0
    unit = (
        "bytes"
        if platform_id == "darwin"
        else "kibibytes"
        if platform_id.startswith("linux")
        else None
    )
    if value <= 0 or unit is None:
        return {
            "available": False,
            "value": None,
            "unit": "unavailable",
            "source": "unavailable",
            "platform": platform_id,
        }
    return {
        "available": True,
        "value": value,
        "unit": unit,
        "source": "resource.getrusage(RUSAGE_SELF)",
        "platform": platform_id,
    }


def _python_loc(root: Path, relative_paths: list[str]) -> int:
    """Physical lines across the Python files the scan actually inventoried."""
    total = 0
    for relative in relative_paths:
        try:
            total += (root / relative).read_bytes().count(b"\n")
        except OSError:
            continue
    return total


def _measure_determinism(path: Path, *, honor_gitignore: bool) -> dict[str, Any]:
    """Two ``run_check`` calls, compared, and nothing else retained.

    Only the second call is timed: the first run's cost is a performance
    question, already answered by the reps a separate worker invocation
    produces, and restating it here would invite two different numbers for the
    same thing.
    """
    from adduce import __version__
    from adduce.engine import run_check
    from adduce.report.json_report import render

    first = run_check(path, honor_gitignore=honor_gitignore)
    started = time.perf_counter()
    second = run_check(path, honor_gitignore=honor_gitignore)
    repeat_seconds = time.perf_counter() - started

    return {
        "available": True,
        "adduce_version": __version__,
        "honor_gitignore": honor_gitignore,
        "determinism": {
            "repeat_render_byte_identical": render(first) == render(second),
            "comparison": "two run_check calls in one process, default JSON report",
            "repeat_runtime_seconds": round(repeat_seconds, 4),
            # There is no analyzer cache yet, so a repeat run differs only by
            # operating-system page cache. It is not a warm path and must not
            # be reported as one.
            "warm_path_exists": False,
        },
    }


def _measure_performance(
    path: Path, *, honor_gitignore: bool, rule_statuses: bool
) -> dict[str, Any]:
    """One ``run_check`` call: inputs, performance, outcome. No determinism block."""
    from adduce import __version__
    from adduce.engine import run_check

    started = time.perf_counter()
    result = run_check(path, honor_gitignore=honor_gitignore)
    cold_seconds = time.perf_counter() - started

    telemetry = result.telemetry.snapshot()
    python_paths = [str(entry.path) for entry in result.repo.python_files()]
    file_count = len(result.repo.files)
    python_loc = _python_loc(result.repo.root, python_paths)

    outcome: dict[str, Any] = {
        "score": round(result.card.total, 4),
        "tier": result.card.tier,
        "findings": len(result.card.findings),
        "parser_failures": telemetry["counters"].get("parse.python.failed", 0),
    }
    if rule_statuses:
        # Opt-in: 78 entries per arm would triple the size of a report that
        # exists to be read by a human, and only the finding diff needs them.
        # A rule absent from this map produced no finding at all, which is a
        # different fact from any status it could carry.
        outcome["rule_statuses"] = {
            finding.rule_id: finding.status.value for finding in result.card.findings
        }

    return {
        "available": True,
        "adduce_version": __version__,
        "honor_gitignore": honor_gitignore,
        "inputs": {
            "files": file_count,
            "python_files": len(python_paths),
            "python_loc": python_loc,
            "bytes": sum(entry.size for entry in result.repo.files),
        },
        "performance": {
            "cold_runtime_seconds": round(cold_seconds, 4),
            "files_per_second": round(file_count / cold_seconds, 1) if cold_seconds else None,
            "python_loc_per_second": (
                round(python_loc / cold_seconds, 1) if cold_seconds and python_loc else None
            ),
            "peak_rss": peak_rss_observation(),
            "stage_milliseconds": telemetry["stage_milliseconds"],
            "counters": telemetry["counters"],
            "disk_reads_per_inventoried_file": (
                round(telemetry["counters"]["files.read_from_disk"] / file_count, 4)
                if file_count
                else None
            ),
        },
        "outcome": outcome,
    }


def measure(
    path: Path,
    *,
    honor_gitignore: bool,
    rule_statuses: bool = False,
    determinism: bool = False,
) -> dict[str, Any]:
    if determinism:
        return _measure_determinism(path, honor_gitignore=honor_gitignore)
    return _measure_performance(path, honor_gitignore=honor_gitignore, rule_statuses=rule_statuses)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", required=True, type=Path)
    parser.add_argument("--gitignore", action="store_true")
    parser.add_argument(
        "--rule-statuses",
        action="store_true",
        help="include a rule-id to status map, which only the finding diff reads",
    )
    parser.add_argument(
        "--src",
        type=Path,
        help="resolve adduce from this tree, ahead of <repo>/src, for an A/B comparison",
    )
    parser.add_argument(
        "--determinism",
        action="store_true",
        help="run twice and report only byte-identity, instead of one timed analysis",
    )
    arguments = parser.parse_args(argv)

    if arguments.src is not None:
        # Inserted ahead of whatever site initialisation already placed on
        # sys.path -- including the editable install's own <repo>/src -- so
        # this tree is the one ``import adduce`` resolves, without needing to
        # remove anything site.py already added. Must happen before the first
        # adduce import, which is why this runs ahead of measure().
        sys.path.insert(0, str(arguments.src.resolve()))

    path: Path = arguments.path
    if not path.is_dir():
        json.dump(
            {"available": False, "reason": f"path is not a directory: {path}"},
            sys.stdout,
        )
        return 0
    json.dump(
        measure(
            path,
            honor_gitignore=bool(arguments.gitignore),
            rule_statuses=bool(arguments.rule_statuses),
            determinism=bool(arguments.determinism),
        ),
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
