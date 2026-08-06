#!/usr/bin/env python3
"""One benchmark measurement, in its own process.

Peak resident memory is a process high-water mark, so measuring several
repositories in one interpreter reports the largest of them for all of them.
Each measurement therefore runs here, alone, and prints a single JSON object.

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


def measure(path: Path, *, honor_gitignore: bool, rule_statuses: bool = False) -> dict[str, Any]:
    from adduce import __version__
    from adduce.engine import run_check
    from adduce.report.json_report import render

    started = time.perf_counter()
    first = run_check(path, honor_gitignore=honor_gitignore)
    cold_seconds = time.perf_counter() - started

    started = time.perf_counter()
    second = run_check(path, honor_gitignore=honor_gitignore)
    repeat_seconds = time.perf_counter() - started

    telemetry = first.telemetry.snapshot()
    python_paths = [str(entry.path) for entry in first.repo.python_files()]
    file_count = len(first.repo.files)
    python_loc = _python_loc(first.repo.root, python_paths)

    outcome: dict[str, Any] = {
        "score": round(first.card.total, 4),
        "tier": first.card.tier,
        "findings": len(first.card.findings),
        "parser_failures": telemetry["counters"].get("parse.python.failed", 0),
    }
    if rule_statuses:
        # Opt-in: 78 entries per arm would triple the size of a report that
        # exists to be read by a human, and only the finding diff needs them.
        # A rule absent from this map produced no finding at all, which is a
        # different fact from any status it could carry.
        outcome["rule_statuses"] = {
            finding.rule_id: finding.status.value for finding in first.card.findings
        }

    return {
        "available": True,
        "adduce_version": __version__,
        "honor_gitignore": honor_gitignore,
        "inputs": {
            "files": file_count,
            "python_files": len(python_paths),
            "python_loc": python_loc,
            "bytes": sum(entry.size for entry in first.repo.files),
        },
        "performance": {
            "cold_runtime_seconds": round(cold_seconds, 4),
            "repeat_runtime_seconds": round(repeat_seconds, 4),
            # There is no analyzer cache yet, so a repeat run differs only by
            # operating-system page cache. It is not a warm path and must not
            # be reported as one.
            "warm_path_exists": False,
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
        "determinism": {
            "repeat_render_byte_identical": render(first) == render(second),
            "comparison": "two run_check calls in one process, default JSON report",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", required=True, type=Path)
    parser.add_argument("--gitignore", action="store_true")
    parser.add_argument(
        "--rule-statuses",
        action="store_true",
        help="include a rule-id to status map, which only the finding diff reads",
    )
    arguments = parser.parse_args(argv)

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
        ),
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
