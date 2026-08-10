#!/usr/bin/env python3
"""The permanent benchmark harness.

``measure`` runs every target ``--reps`` times, each rep its own process, and
writes a report carrying the median runtime, the spread across reps, and one
full observation (peak RSS, stage timings, counters) from the rep that
produced the median. ``compare`` checks a report against a committed baseline
and fails on a regression, scaled to the noise the two reports themselves
measured -- never to a fixed number alone. ``ab`` is the tool for an actual
runtime claim: it alternates two source trees within each rep, and which of
them leads flips every rep, so both arms see the same machine state and neither
carries the cost of going first. That pairing is what a single sample against a
stored baseline cannot do. It reports a delta as a result only when every rep
moved the same way, because on this hardware the drift *underneath* both arms
is routinely larger than the effect between them. See ``bench/README.md`` for
why a one-off ``measure`` vs. ``baseline.json`` is a coarse tripwire, not
evidence.

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
import os
import platform
import statistics
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
from adduce.rules.base import Status  # noqa: E402
from adduce.safe_write import replace_text_regular  # noqa: E402

REPORT_SCHEMA = "adduce-bench/1"
FINDING_DIFF_SCHEMA = "adduce-finding-diff/1"
AB_REPORT_SCHEMA = "adduce-bench-ab/1"
_WORKER_TIMEOUT_SECONDS = 900

#: Multiple of the baseline cold runtime at which ``compare`` calls a
#: catastrophe, and nothing finer.
#:
#: This is deliberately blunt, because wall clock across two *separate* reports
#: cannot be made precise. Reps measure the spread *within* one report, and that
#: statistic does not describe the drift *between* two of them: `transformers`
#: was measured at 14.5 s and at 39.6 s hours apart on one laptop -- 2.7x -- while
#: the reps inside the slower report agreed to 6.1%. A gate scaled to the 6.1%
#: would fire constantly; one scaled to the 2.7x detects almost nothing.
#:
#: So ``compare`` no longer pretends to gate runtime. It catches a gross failure
#: -- an accidental quadratic, a lost early exit -- and leaves every real runtime
#: claim to ``ab``, which pairs the arms and is immune to this drift by
#: construction. The gates that *are* exact (determinism, parser failures,
#: synthetic scores, reads per file) are unaffected and remain the useful ones.
_RUNTIME_CATASTROPHE_MULTIPLE = 4.0


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


def _load_average() -> dict[str, Any]:
    """What else the machine was doing, so a runtime can be read in context.

    A report taken on a busy interactive machine is not wrong, but it is not
    comparable to one taken on an idle host, and nothing else in the record
    says so. The same target was measured here at 1.38 s and at 1.82 s hours
    apart on one laptop, purely on background load. Absent rather than zeroed
    where the platform has no such concept.
    """
    try:
        one, five, fifteen = os.getloadavg()
    except (AttributeError, OSError):
        return {"available": False, "reason": f"unavailable on {sys.platform}"}
    return {
        "available": True,
        "one_minute": round(one, 2),
        "five_minute": round(five, 2),
        "fifteen_minute": round(fifteen, 2),
        "cpu_count": os.cpu_count(),
    }


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
        "load_average": _load_average(),
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


def _run_worker(
    path: Path,
    *,
    honor_gitignore: bool,
    rule_statuses: bool = False,
    src: Path | None = None,
    determinism: bool = False,
) -> dict[str, Any]:
    command = [sys.executable, "-W", "ignore", str(_BENCH_ROOT / "worker.py"), "--path", str(path)]
    if honor_gitignore:
        command.append("--gitignore")
    if rule_statuses:
        command.append("--rule-statuses")
    if src is not None:
        command.extend(["--src", str(src)])
    if determinism:
        command.append("--determinism")
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


def _runtime_statistics(samples: list[float]) -> tuple[float, float, int]:
    """Median runtime, the harness's own noise floor, and which rep is typical.

    ``spread`` is ``(max - min) / median``. The index identifies the rep whose
    other observations -- peak RSS, stage timings, counters -- should travel
    with the median: reusing one real rep's full record rather than inventing
    a value no process actually produced.
    """
    median = statistics.median(samples)
    spread = round((max(samples) - min(samples)) / median, 4) if median else 0.0
    median_index = sorted(range(len(samples)), key=lambda i: samples[i])[len(samples) // 2]
    return round(median, 4), spread, median_index


def _measure_arm(
    path: Path,
    *,
    honor_gitignore: bool,
    reps: int,
    rule_statuses: bool = False,
    src: Path | None = None,
) -> dict[str, Any]:
    """Everything one (target, ignore-setting) arm needs for a report.

    Performance is timed across ``reps`` independent processes -- peak RSS is
    a process high-water mark, so reps sharing one process would report the
    largest of them for all of them. Determinism needs two ``run_check`` calls
    in the same process to compare renders, so it is measured once, separately,
    keeping every performance rep at one analysis. Either kind of failure makes
    the whole arm unavailable, with its reason: a partial reps count would be
    a different, unstated measurement, not a smaller version of this one.
    """
    samples: list[dict[str, Any]] = []
    for _ in range(reps):
        sample = _run_worker(
            path, honor_gitignore=honor_gitignore, rule_statuses=rule_statuses, src=src
        )
        if not sample.get("available"):
            return sample
        samples.append(sample)

    determinism_sample = _run_worker(
        path, honor_gitignore=honor_gitignore, src=src, determinism=True
    )
    if not determinism_sample.get("available"):
        return determinism_sample

    runtimes = [float(sample["performance"]["cold_runtime_seconds"]) for sample in samples]
    median_runtime, spread, median_index = _runtime_statistics(runtimes)
    representative = samples[median_index]

    performance = dict(representative["performance"])
    performance["cold_runtime_seconds"] = median_runtime
    performance["cold_runtime_samples"] = runtimes
    performance["cold_runtime_spread"] = spread

    return {
        "available": True,
        "adduce_version": representative["adduce_version"],
        "honor_gitignore": honor_gitignore,
        "inputs": representative["inputs"],
        "performance": performance,
        "outcome": representative["outcome"],
        "determinism": determinism_sample["determinism"],
    }


def measure(strata_path: Path, only: str | None = None, *, reps: int = 3) -> dict[str, Any]:
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
        # Shipped behaviour: ``adduce check`` with no arguments honours
        # .gitignore. The six targets below also measure the whole tree, so
        # the cost and effect of that default stay visible.
        default = _measure_arm(path, honor_gitignore=True, reps=reps)
        record["default"] = default
        if default.get("available"):
            record["stratum"] = _stratum(default["inputs"]["python_loc"], loc_strata)
        else:
            record["stratum"] = "unavailable"
        if target.get("measure_gitignore_delta"):
            record["whole_tree"] = _measure_arm(path, honor_gitignore=False, reps=reps)
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


def _classify_move(before: str | None, after: str | None) -> str:
    """Name what happened to one rule when the ignore file was honoured.

    ``None`` means the rule produced no finding at all, which is a different
    fact from any status it could have carried: the rule stopped applying to
    the repository, rather than reaching a different conclusion about it.
    """
    if after is None:
        return "stopped_applying"
    if before is None:
        return "started_applying"
    if after == Status.NOT_APPLICABLE.value:
        return "became_not_applicable"
    before_value = Status(before).score_value
    after_value = Status(after).score_value
    if before_value is None or after_value is None:
        return "changed_scoring_eligibility"
    if after_value < before_value:
        return "dropped"
    return "improved"


def finding_diff(strata_path: Path, only: str | None = None) -> dict[str, Any]:
    """Every rule status that moves when the ignore file is honoured.

    This is the evidence for honouring ``.gitignore`` by default. A rule that
    passes on a gitignored file is passing on evidence that is not part of the
    artifact, and the only way to say so credibly is to enumerate the moves.
    """
    manifest = json.loads(strata_path.read_text(encoding="utf-8"))
    results: list[dict[str, Any]] = []

    for target in manifest["targets"]:
        if only is not None and target["id"] != only:
            continue
        path = _REPOSITORY_ROOT / target["path"]
        print(f"diffing {target['id']} ...", file=sys.stderr, flush=True)
        whole_tree = _run_worker(path, honor_gitignore=False, rule_statuses=True)
        honoured = _run_worker(path, honor_gitignore=True, rule_statuses=True)

        record: dict[str, Any] = {"id": target["id"], "kind": target["kind"]}
        if not (whole_tree.get("available") and honoured.get("available")):
            record["available"] = False
            record["reason"] = whole_tree.get("reason") or honoured.get("reason") or "unavailable"
            results.append(record)
            continue

        before: dict[str, str] = whole_tree["outcome"]["rule_statuses"]
        after: dict[str, str] = honoured["outcome"]["rule_statuses"]
        moves = [
            {
                "rule_id": rule_id,
                "from": before.get(rule_id),
                "to": after.get(rule_id),
                "classification": _classify_move(before.get(rule_id), after.get(rule_id)),
            }
            for rule_id in sorted(set(before) | set(after))
            if before.get(rule_id) != after.get(rule_id)
        ]
        tally: dict[str, int] = {}
        for move in moves:
            classification = str(move["classification"])
            tally[classification] = tally.get(classification, 0) + 1

        record.update(
            {
                "available": True,
                "files": {
                    "whole_tree": whole_tree["inputs"]["files"],
                    "honoured": honoured["inputs"]["files"],
                },
                "score": {
                    "whole_tree": whole_tree["outcome"]["score"],
                    "honoured": honoured["outcome"]["score"],
                },
                "cold_runtime_seconds": {
                    "whole_tree": whole_tree["performance"]["cold_runtime_seconds"],
                    "honoured": honoured["performance"]["cold_runtime_seconds"],
                },
                "rules_moved": len(moves),
                "classification_tally": dict(sorted(tally.items())),
                "moves": moves,
            }
        )
        results.append(record)

    measured = [r for r in results if r.get("available")]
    return {
        "schema": FINDING_DIFF_SCHEMA,
        "provenance": _provenance(),
        "summary": {
            "targets_measured": len(measured),
            "targets_unavailable": len(results) - len(measured),
            "targets_unchanged": sum(1 for r in measured if r["rules_moved"] == 0),
            "rules_moved_total": sum(int(r["rules_moved"]) for r in measured),
        },
        "results": results,
    }


def _render_finding_diff(report: dict[str, Any]) -> str:
    lines: list[str] = []
    for record in report["results"]:
        if not record.get("available"):
            lines.append(f"{record['id']:34s} unavailable: {record.get('reason')}")
            continue
        files = record["files"]
        score = record["score"]
        lines.append(
            f"{record['id']:34s} files {files['whole_tree']:>6d} -> {files['honoured']:<6d} "
            f"score {score['whole_tree']:>6.1f} -> {score['honoured']:<6.1f} "
            f"moved {record['rules_moved']:>3d} {record['classification_tally'] or ''}"
        )
    for record in report["results"]:
        for move in record.get("moves", []):
            lines.append(
                f"  {record['id']} {move['rule_id']:<14s} "
                f"{str(move['from']):>14s} -> {str(move['to']):<14s} {move['classification']}"
            )
    summary = report["summary"]
    lines.append(
        f"\n{summary['targets_measured']} measured, "
        f"{summary['targets_unchanged']} unchanged, "
        f"{summary['rules_moved_total']} rule statuses moved in total"
    )
    return "\n".join(lines)


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
        if before > 0 and after > before * _RUNTIME_CATASTROPHE_MULTIPLE:
            problems.append(
                f"{identifier}: cold runtime {before:.3f}s -> {after:.3f}s, past "
                f"{_RUNTIME_CATASTROPHE_MULTIPLE:.0f}x. This gate only catches a gross "
                f"failure; confirm with `ab` before treating it as a measured effect"
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
        whole_tree = record.get("whole_tree")
        default = record["default"]
        if not (whole_tree and whole_tree.get("available") and default.get("available")):
            continue
        before = whole_tree["performance"]["cold_runtime_seconds"]
        after = default["performance"]["cold_runtime_seconds"]
        if default["inputs"]["files"] == whole_tree["inputs"]["files"]:
            continue
        lines.append(
            f"  gitignore delta {record['id']}: "
            f"{whole_tree['inputs']['files']} -> {default['inputs']['files']} files, "
            f"{before:.2f}s -> {after:.2f}s, "
            f"score {whole_tree['outcome']['score']} -> {default['outcome']['score']}"
        )
    return "\n".join(lines)


def _ab_delta(
    baseline_samples: list[float], current_samples: list[float]
) -> tuple[float, bool, list[float]]:
    """Fractional runtime change per rep, its median, and whether it is a result.

    The comparison is *paired*, so it must be read pairwise. Each rep's two arms
    ran moments apart and therefore shared a machine state; the arms as a whole
    did not, because a run drifts -- this machine was measured drifting 6% across
    six reps of one target, and far more once thermally throttled. Comparing the
    two arms' aggregate spreads throws that pairing away and asks the effect to
    clear noise the pairing already cancelled.

    So the statistic is the per-rep difference, and the test is a sign test: an
    effect is resolvable when every rep moved the same way. Under the null that
    is a 2^(1-n) coincidence -- 6% at five reps. It assumes no distribution,
    which is the right bar for a handful of wall-clock samples, and it stays
    honest when the absolute level drifts underneath both arms.
    """
    deltas = [
        (current - baseline) / baseline
        for baseline, current in zip(baseline_samples, current_samples, strict=True)
        if baseline
    ]
    if not deltas:
        return 0.0, False, []
    resolvable = len(deltas) > 1 and (all(d > 0 for d in deltas) or all(d < 0 for d in deltas))
    return round(statistics.median(deltas), 4), resolvable, [round(d, 4) for d in deltas]


def ab(
    strata_path: Path,
    *,
    baseline_src: Path,
    current_src: Path,
    reps: int = 5,
    only: str | None = None,
) -> dict[str, Any]:
    """Paired, interleaved runtime comparison between two source trees.

    For each target, the two arms alternate within every rep -- baseline,
    current, baseline, current, ... -- so both see the same machine state. A
    failure partway through voids the whole target rather than reporting a
    shorter, unstated comparison.
    """
    manifest = json.loads(strata_path.read_text(encoding="utf-8"))
    results: list[dict[str, Any]] = []

    for target in manifest["targets"]:
        if only is not None and target["id"] != only:
            continue
        path = _REPOSITORY_ROOT / target["path"]
        print(f"a/b measuring {target['id']} ...", file=sys.stderr, flush=True)

        baseline_runtimes: list[float] = []
        current_runtimes: list[float] = []
        failure: dict[str, Any] | None = None
        for rep in range(reps):
            # Both arms run adjacent in time so they share a machine state, and
            # which of them goes first alternates. Fixing the order would let
            # any cost of running first -- page cache, CPU boost state -- land
            # on the same arm every rep and read as a real effect.
            order = (
                [("baseline", baseline_src), ("current", current_src)]
                if rep % 2 == 0
                else [("current", current_src), ("baseline", baseline_src)]
            )
            samples: dict[str, float] = {}
            for arm, source in order:
                sample = _run_worker(path, honor_gitignore=True, src=source)
                if not sample.get("available"):
                    failure = sample
                    break
                samples[arm] = float(sample["performance"]["cold_runtime_seconds"])
            if failure is not None:
                break
            baseline_runtimes.append(samples["baseline"])
            current_runtimes.append(samples["current"])

        record: dict[str, Any] = {"id": target["id"], "kind": target["kind"]}
        if failure is not None:
            record["available"] = False
            record["reason"] = failure.get("reason", "unavailable")
            results.append(record)
            continue

        baseline_median, baseline_spread, _ = _runtime_statistics(baseline_runtimes)
        current_median, current_spread, _ = _runtime_statistics(current_runtimes)
        delta, resolvable, paired_deltas = _ab_delta(baseline_runtimes, current_runtimes)

        record.update(
            {
                "available": True,
                "baseline": {
                    "median_runtime_seconds": baseline_median,
                    "spread": baseline_spread,
                    "samples": baseline_runtimes,
                },
                "current": {
                    "median_runtime_seconds": current_median,
                    "spread": current_spread,
                    "samples": current_runtimes,
                },
                "delta": delta,
                "paired_deltas": paired_deltas,
                "reps_agreeing_in_sign": (
                    max(
                        sum(1 for d in paired_deltas if d > 0),
                        sum(1 for d in paired_deltas if d < 0),
                    )
                ),
                "reps": len(paired_deltas),
                "resolvable": resolvable,
            }
        )
        results.append(record)

    measured = [r for r in results if r.get("available")]
    return {
        "schema": AB_REPORT_SCHEMA,
        "provenance": _provenance(),
        "baseline_src": str(baseline_src),
        "current_src": str(current_src),
        "reps": reps,
        "summary": {
            "targets_measured": len(measured),
            "targets_unavailable": len(results) - len(measured),
            "resolvable_deltas": sum(1 for r in measured if r["resolvable"]),
        },
        "results": results,
    }


def _render_ab(report: dict[str, Any]) -> str:
    lines: list[str] = []
    for record in report["results"]:
        if not record.get("available"):
            lines.append(f"{record['id']:34s} unavailable: {record.get('reason')}")
            continue
        baseline = record["baseline"]
        current = record["current"]
        agreeing = record["reps_agreeing_in_sign"]
        reps = record["reps"]
        verdict = (
            f"{record['delta']:+.2%} (resolvable, {agreeing}/{reps} reps agree)"
            if record["resolvable"]
            else f"not resolvable ({record['delta']:+.2%}, only {agreeing}/{reps} reps agree)"
        )
        lines.append(
            f"{record['id']:34s} "
            f"baseline {baseline['median_runtime_seconds']:.3f}s -> "
            f"current {current['median_runtime_seconds']:.3f}s "
            f"(arm drift {baseline['spread']:.0%}/{current['spread']:.0%}): {verdict}"
        )
    summary = report["summary"]
    lines.append(
        f"\n{summary['targets_measured']} measured, "
        f"{summary['resolvable_deltas']} resolvable, "
        f"{summary['targets_unavailable']} unavailable"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    measure_parser = subparsers.add_parser("measure", help="run every target and write a report")
    measure_parser.add_argument("--strata", type=Path, default=_BENCH_ROOT / "strata.json")
    measure_parser.add_argument("--output", type=Path, required=True)
    measure_parser.add_argument("--only", help="measure a single target by id")
    measure_parser.add_argument(
        "--reps",
        type=int,
        default=3,
        help="independent processes per arm; peak RSS is a process high-water mark",
    )

    compare_parser = subparsers.add_parser("compare", help="fail on regression against a baseline")
    compare_parser.add_argument("--baseline", type=Path, required=True)
    compare_parser.add_argument("--current", type=Path, required=True)

    diff_parser = subparsers.add_parser(
        "finding-diff", help="enumerate every rule status that honouring .gitignore moves"
    )
    diff_parser.add_argument("--strata", type=Path, default=_BENCH_ROOT / "strata.json")
    diff_parser.add_argument("--output", type=Path)
    diff_parser.add_argument("--only", help="diff a single target by id")

    ab_parser = subparsers.add_parser(
        "ab", help="the required tool for a runtime claim: paired, interleaved source trees"
    )
    ab_parser.add_argument("--strata", type=Path, default=_BENCH_ROOT / "strata.json")
    ab_parser.add_argument("--baseline-src", type=Path, required=True)
    ab_parser.add_argument("--current-src", type=Path, required=True)
    ab_parser.add_argument("--reps", type=int, default=5)
    ab_parser.add_argument("--only", help="compare a single target by id")
    ab_parser.add_argument("--output", type=Path)

    arguments = parser.parse_args(argv)

    if arguments.command == "finding-diff":
        report = finding_diff(arguments.strata, only=arguments.only)
        print(_render_finding_diff(report))
        if arguments.output is not None:
            rendered = json.dumps(report, indent=2, sort_keys=False, allow_nan=False) + "\n"
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
            replace_text_regular(
                arguments.output,
                rendered,
                label="finding diff report",
                parent_label="finding diff report directory",
            )
            print(f"written to {arguments.output}", file=sys.stderr)
        return 0

    if arguments.command == "measure":
        report = measure(arguments.strata, only=arguments.only, reps=arguments.reps)
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

    if arguments.command == "ab":
        report = ab(
            arguments.strata,
            baseline_src=arguments.baseline_src.resolve(),
            current_src=arguments.current_src.resolve(),
            reps=arguments.reps,
            only=arguments.only,
        )
        print(_render_ab(report))
        if arguments.output is not None:
            rendered = json.dumps(report, indent=2, sort_keys=False, allow_nan=False) + "\n"
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
            replace_text_regular(
                arguments.output,
                rendered,
                label="a/b report",
                parent_label="a/b report directory",
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
